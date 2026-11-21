"""HTTP routes of the Telegram QR sign-in (docs/telegram-account.md). All are
the caller's own (their login cookie or token).

- ``GET /telegram/connect`` — the connect page (``static/telegram-connect.html``)
- ``GET /api/telegram/login`` → ``{status, configured, qr_svg?, expires_in?, account?, error?}``
  (``status``: none · starting · qr · password · connected · expired · failed)
- ``POST /api/telegram/login`` — start a new sign-in (a new QR code) → the same;
  503 ``telegram_not_configured`` without the server's Telegram app
- ``POST /api/telegram/login/password`` ``{password}`` — two-step verification → the same
  (``error: "bad_password"`` while it is wrong)
- ``DELETE /api/telegram/login`` — cancel → ``{status: "none"}``
"""

import logging
import os

from aiohttp import web

from core.auth import request_user
from integrations.telegram_account import client as tg, config, login
from integrations.telegram_account.kind import CONNECT_PATH

logger = logging.getLogger(__name__)

PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "static", "telegram-connect.html")


async def page(request: web.Request) -> web.StreamResponse:
    """GET /telegram/connect"""
    request_user(request)
    return web.FileResponse(PAGE, headers={"Cache-Control": "no-store"})


async def get_login(request: web.Request) -> web.Response:
    """GET /api/telegram/login"""
    return web.json_response(login.state(request_user(request)), headers={"Cache-Control": "no-store"})


async def start_login(request: web.Request) -> web.Response:
    """POST /api/telegram/login"""
    user = request_user(request)
    if not config.configured():
        return web.json_response({"status": "none", "configured": False, "error": "telegram_not_configured",
                                  "message": "The server has no Telegram app."}, status=503)
    try:
        return web.json_response(await login.start(user))
    except tg.TelegramUnavailable as e:
        return web.json_response({"status": "failed", "configured": True, "error": str(e)}, status=502)


async def password_login(request: web.Request) -> web.Response:
    """POST /api/telegram/login/password"""
    user = request_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    password = body.get("password") if isinstance(body, dict) else None
    if not isinstance(password, str) or not password:
        return web.json_response({"error": "bad_request", "message": "password is required"}, status=400)
    return web.json_response(await login.submit_password(user, password))


async def cancel_login(request: web.Request) -> web.Response:
    """DELETE /api/telegram/login"""
    await login.cancel(request_user(request))
    return web.json_response({"status": "none"})


def setup(app: web.Application) -> None:
    app.router.add_get(CONNECT_PATH, page)
    app.router.add_get("/api/telegram/login", get_login)
    app.router.add_post("/api/telegram/login", start_login)
    app.router.add_post("/api/telegram/login/password", password_login)
    app.router.add_delete("/api/telegram/login", cancel_login)
