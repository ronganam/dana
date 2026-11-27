"""The web app's pages and the liveness probe.

- ``GET /`` — the web app (an addon may serve it at its own path too: ``/shopping``)
- ``GET /sw.js``, ``GET /manifest.json`` — the service worker (its ``RELEASE``: :func:`web_release`) and the PWA manifest
- ``GET /calendar`` — the full-screen calendar TV view
- ``GET /api/health`` — ``{ok, version, time}``; ``version`` is the ``VERSION`` file (CI writes the release name into it)
"""

import functools
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any, List, Tuple

from aiohttp import web

from core.addons import loader

logger = logging.getLogger(__name__)


_SERVER_VERSION_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")


def _read_server_version() -> str:
    try:
        with open(_SERVER_VERSION_PATH) as f:
            return f.read().strip()
    except OSError:
        return "unknown"


_SERVER_VERSION = _read_server_version()


def server_version() -> str:
    """This server's version (the ``VERSION`` file; "unknown" without one)."""
    return _SERVER_VERSION


async def health_api(request: web.Request) -> web.Response:
    """GET /api/health — cheap liveness probe used by clients to show a
    "server unreachable" indicator. Does no I/O beyond building the response."""
    return web.json_response({"ok": True, "version": _SERVER_VERSION, "time": int(time.time())})


async def index_handler(request: web.Request) -> web.StreamResponse:
    """Serve the root dashboard HTML."""
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    index_path = os.path.join(static_dir, "index.html")
    if not os.path.exists(index_path):
        return web.Response(text="Dashboard frontend files are not yet created.", status=404)
    return web.FileResponse(index_path)


_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _digest_files(digest: Any, label: str, folder: Path) -> None:
    """Feeds every file under ``folder`` (its path under ``label``, then its bytes) to ``digest`` (a hashlib hash)."""
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        digest.update(f"{label}/{path.relative_to(folder).as_posix()}\0".encode())
        digest.update(path.read_bytes())


def _web_folders() -> List[Tuple[str, Path]]:
    folders = [("static", _STATIC_DIR)]
    for name in loader.loaded():
        web_dir = loader.folder(name).path / "web"
        if web_dir.is_dir():
            folders.append((f"addons/{name}", web_dir))
    return folders


def _stamp() -> Tuple[Tuple[str, int, int], ...]:
    """Every web file's path, size and mtime: cheap to take, and it changes whenever a file does."""
    out = []
    for label, folder in _web_folders():
        for path in folder.rglob("*"):
            if path.is_file():
                st = path.stat()
                out.append((f"{label}/{path.relative_to(folder).as_posix()}", st.st_size, st.st_mtime_ns))
    return tuple(sorted(out))


@functools.lru_cache(maxsize=4)
def _release_of(stamp: Tuple[Tuple[str, int, int], ...]) -> str:
    digest = hashlib.sha256()
    for label, folder in _web_folders():
        _digest_files(digest, label, folder)
    return digest.hexdigest()[:16]


def web_release() -> str:
    """What the web app's files are now: a digest of ``static/`` and of the ``web/`` folder of every loaded
    addon, recomputed when a file's size or mtime changes (a deploy, or bin/hot copying files into the
    running server). The service worker's cache is named after it, so any change to the shell or to an
    addon's page installs a new one (static/sw.js)."""
    return _release_of(_stamp())


async def service_worker_handler(request: web.Request) -> web.StreamResponse:
    """Serve the service worker, its ``RELEASE`` filled in (never cached: the browser compares it to find a new release)."""
    sw_path = _STATIC_DIR / "sw.js"
    if not sw_path.is_file():
        return web.Response(text="Service worker file not found.", status=404)
    text = sw_path.read_text(encoding="utf-8").replace("__DANA_RELEASE__", web_release())
    return web.Response(text=text, content_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


async def manifest_handler(request: web.Request) -> web.StreamResponse:
    """Serve the manifest.json file."""
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    manifest_path = os.path.join(static_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return web.Response(text="Manifest file not found.", status=404)
    return web.FileResponse(manifest_path)


async def calendar_fullscreen_handler(request: web.Request) -> web.StreamResponse:
    """Serve the full-screen calendar TV view."""
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    tv_path = os.path.join(static_dir, "calendar-tv.html")
    if not os.path.exists(tv_path):
        return web.Response(text="Calendar TV view file not found.", status=404)
    return web.FileResponse(tv_path)


def setup(app: web.Application) -> None:
    """Register the pages and ``/api/health``."""
    app.router.add_get("/", index_handler)
    app.router.add_get("/sw.js", service_worker_handler)
    app.router.add_get("/manifest.json", manifest_handler)
    app.router.add_get("/api/health", health_api)
    app.router.add_get("/calendar", calendar_fullscreen_handler)
