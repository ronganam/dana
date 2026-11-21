"""The ``hello`` HTTP route: ``GET /api/hello`` → ``{"greeting": ..., "pending": [names]}`` for the caller.

Every addon's ``api.py`` is registered automatically (its ``setup(app)``),
behind the same authentication; 403 ``not_connected`` for a user without the addon.
"""

from aiohttp import web

from core.auth import request_user
from core.addons import store as addon_store
from addons.hello import store


async def get_greeting(request: web.Request) -> web.Response:
    user = request_user(request)
    conn = addon_store.require(user, "hello")  # NotConnected → 403 in the middleware
    return web.json_response({"greeting": conn.settings.get("greeting") or "Hello",
                              "pending": [g["name"] for g in store.pending(user)]})


def setup(app: web.Application) -> None:
    app.router.add_get("/api/hello", get_greeting)
