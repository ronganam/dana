"""App icons from the phone: each app's icon, saved by the phone from its
notifications and shown by the web next to that app's notifications (Now, the
Inbox; docs/notifications.md, "App icons").

Stored as files, one folder per user: ``<data dir>/app-icons/<user id>/<package>.png``
(``core.paths.APP_ICONS_DIR``; removed with the user by ``core.users.remove``).

- ``GET /api/phone/app-icons`` → ``{icons: {package: hash}}`` (the first 8 hex chars of the PNG's sha256)
- ``PUT /api/phone/app-icons/{package}`` (the PNG bytes) → 204
- ``GET /api/phone/app-icons/{package}.png`` → the PNG (ETag = the hash, 304 on If-None-Match)

Every route answers for the request's user, who must have ``phone`` (else 403
``not_connected``); another user's icons are never read or listed.
"""

import hashlib
import os
import re
import struct
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

from aiohttp import web

from core import paths
from core.users import User
from addons.phone.access import phone_user

PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
MAX_PACKAGE = 200
MAX_BYTES = 64 * 1024
MIN_SIDE, MAX_SIDE = 48, 192
MAX_ICONS = 500
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CACHE_CONTROL = "private, max-age=86400"

# (path, mtime_ns, size) → hash, so listing does not re-read every file.
_hashes: Dict[Tuple[str, int, int], str] = {}


class IconError(ValueError):
    """A refused upload: ``code`` is the API's error, ``status`` its HTTP status."""

    def __init__(self, code: str, status: int, detail: str) -> None:
        super().__init__(detail)
        self.code, self.status = code, status


def valid_package(package: str) -> bool:
    """Whether ``package`` is an Android package name this store accepts (also keeps paths inside the folder)."""
    return len(package) <= MAX_PACKAGE and bool(PACKAGE_RE.match(package))


def png_side(data: bytes) -> Optional[int]:
    """The side of a square PNG within the allowed sizes, read from its signature and IHDR; None otherwise."""
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        return None
    length, = struct.unpack(">I", data[8:12])
    width, height = struct.unpack(">II", data[16:24])
    if length != 13 or width != height or not MIN_SIDE <= width <= MAX_SIDE:
        return None
    return width


def short_hash(data: bytes) -> str:
    """The first 8 hex chars of ``data``'s sha256 (the icon's version)."""
    return hashlib.sha256(data).hexdigest()[:8]


def _folder(user: User) -> Path:
    return paths.APP_ICONS_DIR / user.id


def _path(user: User, package: str) -> Path:
    return _folder(user) / f"{package}.png"


def _hash_of(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    found = _hashes.get(key)
    if found is None:
        found = _hashes[key] = short_hash(path.read_bytes())
    return found


def list_icons(user: User) -> Dict[str, str]:
    """The user's stored icons: package → hash."""
    folder = _folder(user)
    if not folder.is_dir():
        return {}
    out = {}
    for p in sorted(folder.glob("*.png")):
        package = p.name[:-4]
        if valid_package(package):
            out[package] = _hash_of(p)
    return out


def save_icon(user: User, package: str, data: bytes) -> str:
    """Store (or replace) the user's icon of ``package``; returns its hash.

    Raises:
        IconError: ``bad_package`` / ``not_png`` (400), ``too_large`` (413), ``too_many`` (409).
    """
    if not valid_package(package):
        raise IconError("bad_package", 400, "Not an app package name.")
    if len(data) > MAX_BYTES:
        raise IconError("too_large", 413, f"An icon is at most {MAX_BYTES // 1024} KB.")
    if png_side(data) is None:
        raise IconError("not_png", 400, f"A square PNG of {MIN_SIDE}-{MAX_SIDE} px.")
    folder = _folder(user)
    target = _path(user, package)
    if not target.exists() and folder.is_dir() and sum(1 for _ in folder.glob("*.png")) >= MAX_ICONS:
        raise IconError("too_many", 409, f"At most {MAX_ICONS} app icons.")
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".up-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return short_hash(data)


def icon(user: User, package: str) -> Optional[Tuple[bytes, str]]:
    """The user's icon of ``package`` and its hash; None when they have none."""
    if not valid_package(package):
        return None
    p = _path(user, package)
    if not p.is_file():
        return None
    data = p.read_bytes()
    return data, short_hash(data)


def _etag_matches(header: str, etag: str) -> bool:
    tags = [t.strip() for t in header.split(",")]
    return any(t == "*" or t.removeprefix("W/").strip('"') == etag for t in tags)


async def _body(request: web.Request) -> bytes:
    """The request body, cut at ``MAX_BYTES + 1`` (enough to tell it is too large)."""
    data = b""
    while len(data) <= MAX_BYTES:
        chunk = await request.content.read(MAX_BYTES + 1 - len(data))
        if not chunk:
            break
        data += chunk
    return data


async def list_api(request: web.Request) -> web.Response:
    """GET /api/phone/app-icons → {icons: {package: hash}}"""
    return web.json_response({"icons": list_icons(phone_user(request))})


async def put_api(request: web.Request) -> web.Response:
    """PUT /api/phone/app-icons/{package} (body: the PNG) → 204"""
    user = phone_user(request)
    data = await _body(request)
    try:
        save_icon(user, request.match_info["package"], data)
    except IconError as e:
        return web.json_response({"error": e.code, "detail": str(e)}, status=e.status)
    return web.Response(status=204)


async def get_api(request: web.Request) -> web.Response:
    """GET /api/phone/app-icons/{package}.png → the PNG; 304 on a matching If-None-Match; 404 when none."""
    found = icon(phone_user(request), request.match_info["package"])
    if found is None:
        return web.json_response({"error": "not_found"}, status=404)
    data, digest = found
    headers = {"ETag": f'"{digest}"', "Cache-Control": CACHE_CONTROL}
    if _etag_matches(request.headers.get("If-None-Match", ""), digest):
        return web.Response(status=304, headers=headers)
    return web.Response(body=data, content_type="image/png", headers=headers)


def setup(app: web.Application) -> None:
    """Register the app icon routes."""
    app.router.add_get("/api/phone/app-icons", list_api)
    app.router.add_get("/api/phone/app-icons/{package}.png", get_api)
    app.router.add_put("/api/phone/app-icons/{package}", put_api)
