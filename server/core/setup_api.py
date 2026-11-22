"""The setup page and its API (docs/devices.md, "Setup mode"; core/setup.py).

    GET  /setup              the setup page (static/setup.html). While the server
                             waits for its first admin it is open to anyone (the
                             code in its link is what counts); afterwards it is an
                             ordinary signed-in page.
    GET  /api/setup          → {setup_required, version}   (public)
    POST /api/setup/admin {code, name}                      (public, rate-limited)
         → 201 {user: {id, name, access}, device, token}, and the browser is
           signed in (the ``dana_session`` cookie of that token).
         409 ``already_set_up`` once a user exists, 403 ``invalid_code``,
         400 ``invalid`` (``field``: ``name``).
"""

import json
import logging
from pathlib import Path

from aiohttp import web

from core import devices, ratelimit
from core import setup as setup_mode
from core.auth import set_session_cookie
from core.pages_api import server_version

logger = logging.getLogger(__name__)

_PAGE = Path(__file__).resolve().parent.parent / "static" / "setup.html"


async def setup_page(request: web.Request) -> web.StreamResponse:
    """GET /setup"""
    return web.FileResponse(_PAGE, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


async def setup_state_api(request: web.Request) -> web.Response:
    """GET /api/setup"""
    return web.json_response({"setup_required": setup_mode.required(), "version": server_version()})


async def create_admin_api(request: web.Request) -> web.Response:
    """POST /api/setup/admin"""
    if not setup_mode.required():
        return web.json_response({"error": "already_set_up"}, status=409)
    key = ratelimit.client_key(request)
    wait = ratelimit.SETUP_FAILURES.retry_after(key)
    if wait:
        return ratelimit.too_many(wait)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        body = None
    if not isinstance(body, dict) or not all(isinstance(body.get(k, ""), str) for k in ("code", "name")):
        return web.json_response({"error": "invalid", "detail": "a JSON object with code and name is expected"},
                                 status=400)
    try:
        created = setup_mode.create_admin(body.get("code", ""), body.get("name", ""),
                                     devices.browser_name(request.headers.get("User-Agent", "")))
    except setup_mode.SetupError as e:
        if e.error == "already_set_up":
            return web.json_response({"error": e.error}, status=409)
        if e.error == "invalid_code":
            ratelimit.SETUP_FAILURES.fail(key)
            logger.warning(f"Setup refused from {key}: wrong setup code")
            return web.json_response({"error": e.error, "detail": str(e)}, status=403)
        return web.json_response({"error": e.error, "field": e.field, "detail": str(e)}, status=400)
    response = web.json_response({"user": {"id": created.user.id, "name": created.user.name,
                                           "access": created.user.access},
                                  "device": created.device.to_dict(), "token": created.token}, status=201)
    set_session_cookie(request, response, created.token)
    return response


def setup(app: web.Application) -> None:
    """Register the setup page and its API."""
    app.router.add_get("/setup", setup_page)
    app.router.add_get("/api/setup", setup_state_api)
    app.router.add_post("/api/setup/admin", create_admin_api)
