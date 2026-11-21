"""In-app update hosting for Dana's Android app.

Serves a JSON manifest of published APKs and the APK files themselves so the
phone app (and deploy tooling) can check for updates and download them.

Endpoints (registered by ``setup(app)``):
    GET  /api/app/version          -> manifest with latest phone/wear versions
    GET  /api/app/download/{file}  -> serves a published APK
    POST /api/app/upload           -> publish an APK (Bearer APP_PUBLISH_TOKEN)

Manifest layout (APP_UPDATES_DIR/manifest.json):
    {
      "phone": {
        "versionName": "6.0.0", "versionCode": 6, "file": "phone-6.0.0.apk",
        "sha256": "...", "size": 12345, "release_notes": "...",
        "updated_at": "2026-01-01T00:00:00Z"
      },
      "wear": { ... }
    }
"""

import hashlib
import hmac
import json
import logging
import os
import re
import datetime
from pathlib import Path

from aiohttp import web

from core import paths

logger = logging.getLogger(__name__)

APP_UPDATES_DIR = str(paths.APP_UPDATES_DIR)
MANIFEST_PATH = os.path.join(APP_UPDATES_DIR, "manifest.json")

# Allowed artifact keys (phone app / wear app). The watchface is intentionally
# excluded: its Kotlin sources were never committed, so it cannot be rebuilt.
ARTIFACTS = {"phone", "wear"}

# Conservative filename allowlist — no path separators, no dot-dots.
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.apk$")


def _ensure_dir() -> None:
    os.makedirs(APP_UPDATES_DIR, exist_ok=True)


def _load_manifest() -> dict:
    try:
        with open(MANIFEST_PATH, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"Failed to read app-update manifest: {e}")
        return {}


def _save_manifest(manifest: dict) -> None:
    _ensure_dir()
    tmp = MANIFEST_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=2)
    os.replace(tmp, MANIFEST_PATH)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_publish_token(request: web.Request) -> bool:
    """Bearer-token check for the publish endpoint.

    The rest of the server has no auth (edge-network trust); publishing APKs is
    the first mutating, security-sensitive operation, so it gets a token.
    """
    expected = os.getenv("APP_PUBLISH_TOKEN", "")
    if not expected:
        logger.warning("APP_PUBLISH_TOKEN not set — rejecting app upload")
        return False
    auth = request.headers.get("Authorization", "")
    return hmac.compare_digest(auth, f"Bearer {expected}")


async def get_app_version_api(request: web.Request) -> web.Response:
    """GET /api/app/version — latest published versions of the Android apps."""
    manifest = _load_manifest()
    if not manifest:
        return web.json_response(
            {"error": "No app updates published yet"}, status=404
        )
    return web.json_response(manifest)


async def download_app_apk_api(request: web.Request) -> web.Response:
    """GET /api/app/download/{filename} — serve a published APK."""
    filename = request.match_info.get("filename", "")
    if not _FILENAME_RE.match(filename):
        return web.json_response({"error": "Invalid filename"}, status=400)
    path = os.path.join(APP_UPDATES_DIR, filename)
    # Defense in depth: resolved path must stay inside APP_UPDATES_DIR.
    resolved = os.path.realpath(path)
    if not resolved.startswith(os.path.realpath(APP_UPDATES_DIR) + os.sep):
        return web.json_response({"error": "Invalid path"}, status=400)
    if not os.path.isfile(resolved):
        return web.json_response({"error": "APK not found"}, status=404)
    return web.FileResponse(
        resolved,
        headers={"Content-Type": "application/vnd.android.package-archive"},
    )


async def upload_app_apk_api(request: web.Request) -> web.Response:
    """POST /api/app/upload — publish an APK for the Android app.

    Multipart form fields:
        artifact       - "phone" or "wear"
        version_name   - e.g. "6.0.0"
        version_code   - integer, e.g. 6
        release_notes  - optional free text
        apk            - the APK file
    """
    if not _check_publish_token(request):
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        reader = await request.multipart()
    except Exception:
        return web.json_response({"error": "Expected multipart form"}, status=400)

    artifact = ""
    version_name = ""
    version_code = 0
    release_notes = ""
    apk_bytes = bytearray()
    apk_filename = ""

    while True:
        field = await reader.next()
        if field is None:
            break
        if field.name in ("artifact", "version_name", "version_code", "release_notes"):
            value = (await field.read()).decode().strip()
            if field.name == "artifact":
                artifact = value
            elif field.name == "version_name":
                version_name = value
            elif field.name == "version_code":
                try:
                    version_code = int(value)
                except ValueError:
                    return web.json_response(
                        {"error": "version_code must be an integer"}, status=400
                    )
            elif field.name == "release_notes":
                release_notes = value
        elif field.name == "apk":
            apk_filename = field.filename or ""
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                apk_bytes.extend(chunk)

    if artifact not in ARTIFACTS:
        return web.json_response(
            {"error": f"artifact must be one of {sorted(ARTIFACTS)}"}, status=400
        )
    if not version_name or version_code <= 0:
        return web.json_response(
            {"error": "version_name and version_code are required"}, status=400
        )
    if not apk_bytes:
        return web.json_response({"error": "No APK data received"}, status=400)
    # Sanity check: APKs start with the ZIP magic "PK".
    if apk_bytes[:2] != b"PK":
        return web.json_response(
            {"error": f"{apk_filename or 'upload'} does not look like an APK (missing ZIP magic)"},
            status=400,
        )

    _ensure_dir()
    stored_name = f"{artifact}-{version_name}.apk"
    path = os.path.join(APP_UPDATES_DIR, stored_name)
    with open(path, "wb") as f:
        f.write(apk_bytes)

    # Prune older versions of this artifact — the PVC is small (128Mi, shared
    # with the DB) and local-path does not support live expansion, so storage
    # must stay bounded to the latest phone+wear APK pair.
    prefix = f"{artifact}-"
    for entry in os.listdir(APP_UPDATES_DIR):
        if entry.startswith(prefix) and entry.endswith(".apk") and entry != stored_name:
            try:
                os.remove(os.path.join(APP_UPDATES_DIR, entry))
                logger.info(f"Pruned superseded APK: {entry}")
            except OSError as e:
                logger.warning(f"Could not prune {entry}: {e}")

    sha256 = hashlib.sha256(apk_bytes).hexdigest()

    manifest = _load_manifest()
    manifest[artifact] = {
        "versionName": version_name,
        "versionCode": version_code,
        "file": stored_name,
        "sha256": sha256,
        "size": len(apk_bytes),
        "release_notes": release_notes,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }
    _save_manifest(manifest)

    logger.info(
        f"Published app update: {artifact} {version_name} (code {version_code}, "
        f"{len(apk_bytes)} bytes, sha256={sha256[:12]}...)"
    )
    return web.json_response({"status": "published", "artifact": artifact, **manifest[artifact]})


def setup(app: web.Application) -> None:
    """Register the app update routes."""
    app.router.add_get("/api/app/version", get_app_version_api)
    app.router.add_get("/api/app/download/{filename}", download_app_apk_api)
    app.router.add_post("/api/app/upload", upload_app_apk_api)
