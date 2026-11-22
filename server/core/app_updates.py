"""In-app update hosting for Dana's Android apps.

Serves a JSON manifest of the APKs this server offers and the APK files
themselves, so the phone app can check for updates and download them (the
phone app also offers the watch app's update).

APKs come from two sources, kept in one manifest; the newest ``versionCode``
of each artifact wins whichever source it came from:

- **GitHub Releases** (:func:`releases_loop`): every ``RELEASE_CHECK_SECONDS``
  and at start, the latest release of ``APP_RELEASES_REPO`` (default
  ``ronganam/dana``; empty disables) is read through the public GitHub API.
  Its ``versions.json`` asset says each artifact's version without
  downloading an APK; a newer one is downloaded and checked against the
  release's ``SHA256SUMS`` before it is offered.
- **Uploads** (``POST /api/app/upload``, Bearer ``APP_PUBLISH_TOKEN``): for
  people who build their own apps (``android/bin/deploy-app.sh``). An upload
  older than what is offered is refused (409).

Endpoints (registered by ``setup(app)``):
    GET  /api/app/version          -> manifest with latest phone/wear versions
    GET  /api/app/download/{file}  -> serves an offered APK
    POST /api/app/upload           -> publish an APK (Bearer APP_PUBLISH_TOKEN)

Manifest layout (<data dir>/app-updates/manifest.json):
    {
      "phone": {
        "versionName": "6.0.0", "versionCode": 6, "file": "phone-6.0.0.apk",
        "sha256": "...", "size": 12345, "release_notes": "...",
        "updated_at": "2026-01-01T00:00:00Z"
      },
      "wear": { ... }
    }
"""

import asyncio
import datetime
import hashlib
import hmac
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from aiohttp import web

from core import paths

logger = logging.getLogger(__name__)

APP_UPDATES_DIR = str(paths.APP_UPDATES_DIR)
MANIFEST_PATH = os.path.join(APP_UPDATES_DIR, "manifest.json")

# The artifacts offered: the apps that update through the phone (the phone app
# installs its own and the watch app's). The watch face, the tablet and the TV
# are installed by hand from the release (android/docs/release.md).
ARTIFACTS = {"phone", "wear"}

# Conservative filename allowlist — no path separators, no dot-dots.
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.apk$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# GitHub Releases source (module values: the tests point GITHUB_API at a fake).
DEFAULT_RELEASES_REPO = "ronganam/dana"
GITHUB_API = "https://api.github.com"
RELEASE_CHECK_SECONDS = 6 * 3600
VERSIONS_ASSET = "versions.json"
SUMS_ASSET = "SHA256SUMS"
MAX_APK_BYTES = 300 * 1024 * 1024
MAX_SMALL_ASSET_BYTES = 256 * 1024
MAX_RELEASE_NOTES_CHARS = 4000
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# Uploads and release installs change the manifest and the files one at a time.
_install_lock = asyncio.Lock()


class ReleaseError(Exception):
    """A release that cannot be used (missing asset, bad checksum…)."""


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


def offered_version_code(artifact: str) -> int:
    """The ``versionCode`` offered for ``artifact`` now (0: none)."""
    entry = _load_manifest().get(artifact)
    try:
        return int(entry.get("versionCode", 0)) if isinstance(entry, dict) else 0
    except (TypeError, ValueError):
        return 0


def _install(artifact: str, version_name: str, version_code: int, staged: Path,
             release_notes: str) -> Dict[str, Any]:
    """Offer the APK at ``staged`` (in the app-updates folder) as ``artifact``:
    store it as ``<artifact>-<versionName>.apk``, prune the artifact's older
    APKs and write its manifest entry, which is returned."""
    stored_name = f"{artifact}-{version_name}.apk"
    if not _FILENAME_RE.match(stored_name):
        staged.unlink(missing_ok=True)
        raise ValueError(f"version name {version_name!r} does not make a valid file name")
    os.replace(staged, os.path.join(APP_UPDATES_DIR, stored_name))
    # Only the latest APK of each artifact is kept: the data volume is often small.
    prefix = f"{artifact}-"
    for entry in os.listdir(APP_UPDATES_DIR):
        if entry.startswith(prefix) and entry.endswith(".apk") and entry != stored_name:
            try:
                os.remove(os.path.join(APP_UPDATES_DIR, entry))
                logger.info(f"Pruned superseded APK: {entry}")
            except OSError as e:
                logger.warning(f"Could not prune {entry}: {e}")
    path = Path(APP_UPDATES_DIR) / stored_name
    manifest = _load_manifest()
    manifest[artifact] = {
        "versionName": version_name,
        "versionCode": version_code,
        "file": stored_name,
        "sha256": _sha256_file(path),
        "size": path.stat().st_size,
        "release_notes": release_notes,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _save_manifest(manifest)
    return manifest[artifact]


# --- Uploads -------------------------------------------------------------------------------------

# Publishing is refused unless APP_PUBLISH_TOKEN is at least this long and not
# the example's placeholder (anyone who has it can ship an APK to every phone).
PUBLISH_TOKEN_MIN_LENGTH = 24
PUBLISH_TOKEN_PLACEHOLDERS = frozenset({"choose_a_random_publish_token"})


def publish_token_problem(token: str) -> Optional[str]:
    """Why ``token`` cannot guard publishing (None: it can)."""
    token = token.strip()
    if not token:
        return "APP_PUBLISH_TOKEN is not set"
    if token in PUBLISH_TOKEN_PLACEHOLDERS:
        return "APP_PUBLISH_TOKEN is the example's placeholder"
    if len(token) < PUBLISH_TOKEN_MIN_LENGTH:
        return f"APP_PUBLISH_TOKEN is shorter than {PUBLISH_TOKEN_MIN_LENGTH} characters"
    return None


def _check_publish_token(request: web.Request) -> bool:
    """Bearer-token check for the publish endpoint (a public path: the token
    is not a user's). Refused while :func:`publish_token_problem` finds one."""
    expected = os.getenv("APP_PUBLISH_TOKEN", "").strip()
    problem = publish_token_problem(expected)
    if problem:
        logger.warning(f"{problem}: app publishing is disabled, upload refused")
        return False
    auth = request.headers.get("Authorization", "")
    return hmac.compare_digest(auth, f"Bearer {expected}")


async def get_app_version_api(request: web.Request) -> web.Response:
    """GET /api/app/version — latest offered versions of the Android apps."""
    manifest = _load_manifest()
    if not manifest:
        return web.json_response(
            {"error": "No app updates published yet"}, status=404
        )
    return web.json_response(manifest)


async def download_app_apk_api(request: web.Request) -> web.Response:
    """GET /api/app/download/{filename} — serve an offered APK."""
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
        version_code   - integer, e.g. 6 (not older than the one offered: 409)
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
        if not isinstance(field, aiohttp.BodyPartReader):
            continue
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
    if not _FILENAME_RE.match(f"{artifact}-{version_name}.apk"):
        return web.json_response({"error": "version_name may hold only letters, digits, '.', '_' and '-'"},
                                 status=400)
    if not apk_bytes:
        return web.json_response({"error": "No APK data received"}, status=400)
    # Sanity check: APKs start with the ZIP magic "PK".
    if apk_bytes[:2] != b"PK":
        return web.json_response(
            {"error": f"{apk_filename or 'upload'} does not look like an APK (missing ZIP magic)"},
            status=400,
        )

    async with _install_lock:
        offered = offered_version_code(artifact)
        if version_code < offered:
            return web.json_response(
                {"error": "older", "message": f"{artifact} versionCode {offered} is offered already",
                 "offered_version_code": offered},
                status=409,
            )
        _ensure_dir()
        staged = Path(APP_UPDATES_DIR) / f".upload-{artifact}.part"
        staged.write_bytes(apk_bytes)
        entry = _install(artifact, version_name, version_code, staged, release_notes)

    logger.info(
        f"Published app update: {artifact} {version_name} (code {version_code}, "
        f"{entry['size']} bytes, sha256={entry['sha256'][:12]}...)"
    )
    return web.json_response({"status": "published", "artifact": artifact, **entry})


# --- GitHub Releases -----------------------------------------------------------------------------

def releases_repo() -> str:
    """The ``owner/name`` repository whose releases are offered ("": none)."""
    value = os.getenv("APP_RELEASES_REPO")
    return DEFAULT_RELEASES_REPO if value is None else value.strip()


def parse_sums(text: str) -> Dict[str, str]:
    """``{file name: sha256}`` from a ``sha256sum`` output (``<hex>  <name>``)."""
    sums: Dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and _SHA256_RE.match(parts[0].lower()):
            sums[parts[1].lstrip("*").strip()] = parts[0].lower()
    return sums


async def _get_json(session: aiohttp.ClientSession, url: str) -> Any:
    async with session.get(url, headers={"Accept": "application/vnd.github+json"}) as r:
        if r.status != 200:
            raise ReleaseError(f"GET {url}: HTTP {r.status}")
        return json.loads(await r.content.read(MAX_SMALL_ASSET_BYTES * 16))


async def _get_small(session: aiohttp.ClientSession, url: str) -> bytes:
    async with session.get(url) as r:
        if r.status != 200:
            raise ReleaseError(f"GET {url}: HTTP {r.status}")
        data = await r.content.read(MAX_SMALL_ASSET_BYTES + 1)
        if len(data) > MAX_SMALL_ASSET_BYTES:
            raise ReleaseError(f"{url} is larger than {MAX_SMALL_ASSET_BYTES} bytes")
        return data


async def _download(session: aiohttp.ClientSession, url: str, target: Path) -> str:
    """Stream ``url`` into ``target``; returns its sha256."""
    h = hashlib.sha256()
    written = 0
    async with session.get(url) as r:
        if r.status != 200:
            raise ReleaseError(f"GET {url}: HTTP {r.status}")
        with open(target, "wb") as f:
            async for chunk in r.content.iter_chunked(1024 * 1024):
                written += len(chunk)
                if written > MAX_APK_BYTES:
                    raise ReleaseError(f"{url} is larger than {MAX_APK_BYTES} bytes")
                h.update(chunk)
                f.write(chunk)
    return h.hexdigest()


async def sync_releases(repo: Optional[str] = None) -> List[str]:
    """Offer the latest release's APKs that are newer than the offered ones.

    Reads ``versions.json`` and ``SHA256SUMS`` from the latest release of
    ``repo`` (default :func:`releases_repo`); downloads each newer artifact,
    checks it against both, and installs it like an upload. Returns the
    artifacts installed. Raises :class:`ReleaseError` for an unusable release
    (nothing is installed from it then)."""
    repo = releases_repo() if repo is None else repo
    if not repo:
        return []
    if not _REPO_RE.match(repo):
        raise ReleaseError(f"APP_RELEASES_REPO {repo!r} is not owner/name")
    timeout = aiohttp.ClientTimeout(total=600, sock_connect=20, sock_read=60)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "dana-server"}) as session:
        release = await _get_json(session, f"{GITHUB_API}/repos/{repo}/releases/latest")
        if not isinstance(release, dict):
            raise ReleaseError("the latest release is not a JSON object")
        assets = {a.get("name"): a.get("browser_download_url") for a in release.get("assets") or []
                  if isinstance(a, dict) and a.get("name") and a.get("browser_download_url")}
        tag = str(release.get("tag_name") or "")
        if VERSIONS_ASSET not in assets or SUMS_ASSET not in assets:
            raise ReleaseError(f"release {tag} has no {VERSIONS_ASSET} or {SUMS_ASSET}")
        try:
            versions = json.loads(await _get_small(session, assets[VERSIONS_ASSET]))
        except ValueError as e:
            raise ReleaseError(f"release {tag}: {VERSIONS_ASSET} is not JSON: {e}") from e
        if not isinstance(versions, dict):
            raise ReleaseError(f"release {tag}: {VERSIONS_ASSET} is not an object")
        sums = parse_sums((await _get_small(session, assets[SUMS_ASSET])).decode("utf-8", "replace"))
        notes = str(release.get("body") or "")[:MAX_RELEASE_NOTES_CHARS]

        installed: List[str] = []
        for artifact in sorted(ARTIFACTS):
            entry = versions.get(artifact)
            if not isinstance(entry, dict):
                continue
            try:
                code = int(entry.get("versionCode", 0))
            except (TypeError, ValueError):
                raise ReleaseError(f"release {tag}: {artifact} has no integer versionCode")
            name = str(entry.get("versionName") or "")
            file = str(entry.get("file") or "")
            if code <= offered_version_code(artifact):
                continue
            if not name or not _FILENAME_RE.match(file) or file not in assets:
                raise ReleaseError(f"release {tag}: {artifact} names no APK asset of the release")
            expected = sums.get(file)
            if not expected:
                raise ReleaseError(f"release {tag}: {file} is not in {SUMS_ASSET}")
            if str(entry.get("sha256") or expected).lower() != expected:
                raise ReleaseError(f"release {tag}: {file}'s sha256 differs between {VERSIONS_ASSET} and {SUMS_ASSET}")
            _ensure_dir()
            staged = Path(APP_UPDATES_DIR) / f".release-{artifact}.part"
            try:
                digest = await _download(session, assets[file], staged)
                if digest != expected:
                    raise ReleaseError(f"release {tag}: {file} does not match its {SUMS_ASSET} checksum")
                async with _install_lock:
                    if code <= offered_version_code(artifact):
                        continue  # an upload got there first
                    await asyncio.to_thread(_install, artifact, name, code, staged, notes)
            finally:
                staged.unlink(missing_ok=True)
            logger.info(f"App update from release {tag}: {artifact} {name} (code {code})")
            installed.append(artifact)
        return installed


async def releases_loop() -> None:
    """Background task: :func:`sync_releases` at start and every
    ``RELEASE_CHECK_SECONDS``; nothing while ``APP_RELEASES_REPO`` is empty."""
    repo = releases_repo()
    if not repo:
        logger.info("APP_RELEASES_REPO is empty: app updates come from uploads only")
        return
    while True:
        try:
            await sync_releases(repo)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"App updates from {repo}'s releases: {e}")
        await asyncio.sleep(RELEASE_CHECK_SECONDS)


def setup(app: web.Application) -> None:
    """Register the app update routes."""
    app.router.add_get("/api/app/version", get_app_version_api)
    app.router.add_get("/api/app/download/{filename}", download_app_apk_api)
    app.router.add_post("/api/app/upload", upload_app_apk_api)
    problem = publish_token_problem(os.getenv("APP_PUBLISH_TOKEN", ""))
    if problem:
        logger.info(f"{problem}: Android app publishing (POST /api/app/upload) is disabled")
