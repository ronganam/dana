"""HTTP routes for sharing an item (integrations/framework/sharing.py; docs/access.md, "Sharing").

    GET /api/sharing/{item_type}/{id}  → {item_type, id, title, owner: {id, name},
                                          shared_with: [{user_id, name, level}],
                                          users: [{id, name}]}
    PUT /api/sharing/{item_type}/{id}  {user_id, level: read|edit|none} → the same shape

Owner only. An item the caller cannot see answers 404 like a missing one; an
item shared with them (not theirs) answers 403 ``owner_only``. The caller
needs the item type's kind connected (403 ``not_connected``). ``users`` lists
the other users it can be shared with. Loaded by core/server.py (``setup(app)``).
"""

import logging
from typing import Any, Dict, Optional, Tuple

from aiohttp import web

from core import users
from core.auth import request_user
from core.users import User
from integrations.framework import sharing, store

logger = logging.getLogger(__name__)


def _payload(owner: User, t: sharing.ItemType, item_id: str) -> Dict[str, Any]:
    shared_with = []
    for s in sharing.shares(t.name, item_id):
        other = users.get(s.user_id)
        if other is not None:
            shared_with.append({"user_id": other.id, "name": other.name, "level": s.level})
    return {
        "item_type": t.name, "id": item_id, "title": t.title_of(item_id),
        "owner": {"id": owner.id, "name": owner.name},
        "shared_with": shared_with,
        "users": [{"id": u.id, "name": u.name} for u in users.list_users() if u.id != owner.id],
    }


def _resolve(request: web.Request) -> Tuple[Optional[Tuple[User, sharing.ItemType, str]], Optional[web.Response]]:
    """((user, item type, item id), None) or (None, an error response)."""
    user = request_user(request)
    try:
        t = sharing.item_type(request.match_info["item_type"])
    except KeyError:
        return None, web.json_response({"error": "Unknown item type"}, status=404)
    store.require(user, t.kind)
    item_id = request.match_info["id"]
    level = sharing.access(user, t.name, item_id)
    if level is None:
        return None, web.json_response({"error": f"{t.label.capitalize()} not found"}, status=404)
    if level != sharing.OWNER:
        return None, web.json_response({"error": "owner_only",
                                        "message": f"Only the owner of the {t.label} can share it."}, status=403)
    return (user, t, item_id), None


async def get_sharing_api(request: web.Request) -> web.Response:
    """GET /api/sharing/{item_type}/{id}"""
    found, error = _resolve(request)
    if error is not None:
        return error
    assert found is not None
    user, t, item_id = found
    return web.json_response(_payload(user, t, item_id))


async def put_sharing_api(request: web.Request) -> web.Response:
    """PUT /api/sharing/{item_type}/{id} {user_id, level}"""
    found, error = _resolve(request)
    if error is not None:
        return error
    assert found is not None
    user, t, item_id = found
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"error": "Expected a JSON object"}, status=400)
    try:
        sharing.set_level(user, t.name, item_id, str(data.get("user_id") or ""), str(data.get("level") or ""))
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response(_payload(user, t, item_id))


def setup(app: web.Application) -> None:
    app.router.add_get("/api/sharing/{item_type}/{id}", get_sharing_api)
    app.router.add_put("/api/sharing/{item_type}/{id}", put_sharing_api)
