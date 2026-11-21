"""The web app's pages and the liveness probe.

- ``GET /``, ``GET /shopping`` — the web app (``/shopping`` opens it on the Shopping panel)
- ``GET /sw.js``, ``GET /manifest.json`` — the service worker and the PWA manifest
- ``GET /calendar`` — the full-screen calendar TV view
- ``GET /dashboard`` — the bedroom tablet dashboard (docs/dashboard.md), its CSS/JS versioned by the release
- ``GET /api/health`` — ``{ok, version, time}``; ``version`` is the ``VERSION`` file (CI writes the release name into it)
"""

import logging
import os
import time

from aiohttp import web

logger = logging.getLogger(__name__)


_SERVER_VERSION_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")


def _read_server_version() -> str:
    try:
        with open(_SERVER_VERSION_PATH) as f:
            return f.read().strip()
    except OSError:
        return "unknown"


_SERVER_VERSION = _read_server_version()


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


async def service_worker_handler(request: web.Request) -> web.StreamResponse:
    """Serve the service worker file."""
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    sw_path = os.path.join(static_dir, "sw.js")
    if not os.path.exists(sw_path):
        return web.Response(text="Service worker file not found.", status=404)
    return web.FileResponse(sw_path, headers={"Service-Worker-Allowed": "/"})


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


async def dashboard_fullscreen_handler(request: web.Request) -> web.StreamResponse:
    """Serve the full-screen bedroom dashboard view."""
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    dashboard_path = os.path.join(static_dir, "dashboard.html")
    if not os.path.exists(dashboard_path):
        return web.Response(text="Dashboard view file not found.", status=404)
    # Kiosk WebViews cache aggressively (no validators on /static), so a new release
    # would never reach the wall tablet. The page itself is always revalidated and
    # its CSS/JS are versioned by the release, so an update lands on the next load.
    with open(dashboard_path, encoding="utf-8") as f:
        html = f.read()
    for asset in ("/static/dashboard.css", "/static/dashboard.js"):
        html = html.replace(f'"{asset}"', f'"{asset}?v={_SERVER_VERSION}"')
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


def setup(app: web.Application) -> None:
    """Register the pages and ``/api/health``."""
    app.router.add_get("/", index_handler)
    app.router.add_get("/shopping", index_handler)  # the app, opened on the Shopping panel (static/shopping.js)
    app.router.add_get("/sw.js", service_worker_handler)
    app.router.add_get("/manifest.json", manifest_handler)
    app.router.add_get("/api/health", health_api)
    app.router.add_get("/calendar", calendar_fullscreen_handler)
    app.router.add_get("/dashboard", dashboard_fullscreen_handler)
