"""The ``hello`` HTTP route: ``GET /api/hello`` → ``{"greeting": ...}`` for the caller.

A plugin's ``api.py`` is registered after the built-in routes, behind the same
authentication; 403 ``not_connected`` for a user without the kind.
"""

from aiohttp import web

from core.auth import request_user
from integrations.framework import store


async def get_greeting(request: web.Request) -> web.Response:
    conn = store.require(request_user(request), "hello")  # NotConnected → 403 in the middleware
    return web.json_response({"greeting": conn.settings.get("greeting") or "Hello"})


def setup(app: web.Application) -> None:
    app.router.add_get("/api/hello", get_greeting)
