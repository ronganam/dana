"""The ``showcase`` route its page reads: ``GET /api/showcase/plants`` → ``{"room", "plants": [...], "inbox": [...]}``.

Registered automatically (its ``setup(app)``), behind the same sign-in as every
route; 403 ``not_connected`` for a user who has not turned the addon on.
"""

from aiohttp import web

from core.auth import request_user
from core.addons import store as addon_store
from addons.showcase import store

# --- reference: route ---
async def get_plants(request: web.Request) -> web.Response:
    user = request_user(request)
    conn = addon_store.require(user, "showcase")  # NotConnected → 403 not_connected in the middleware
    return web.json_response({"room": conn.settings.get("room"),
                              "plants": [{"id": p["id"], "name": p["name"]} for p in store.plants(user)],
                              "inbox": store.inbox(user)})


def setup(app: web.Application) -> None:
    app.router.add_get("/api/showcase/plants", get_plants)
# --- end ---
