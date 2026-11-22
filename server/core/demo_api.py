"""The demo household over HTTP (core/demo.py; docs/household.md).

    GET    /api/demo              → {loaded, users: [{id, name}], viewing: <demo user id> | null}
    POST   /api/demo              → 201 {loaded: true, users, rows: {addon: n}, viewing} (200, rows {}, when it
                                    was loaded already: nothing changes)
    DELETE /api/demo              → {loaded: false, removed: <number of demo users>}; stops viewing it
    POST   /api/demo/view {user_id?} → {viewing: <id>}: this browser shows the app as that demo user (the
                                    first by default) until DELETE; 409 ``not_loaded``, 404 an id that is
                                    not a demo user's
    DELETE /api/demo/view         → {viewing: null}

Every endpoint needs ``manage_users``. ``/api/demo*`` always runs as the
caller (never as the demo user being viewed: core/auth.py), so the banner can
always go back and remove it.
"""

import json
from typing import Any, Dict, Optional

from aiohttp import web

from core import demo, users
from core.auth import DEMO_VIEW_COOKIE, SESSION_MAX_AGE, requires


def _viewing(request: web.Request) -> Optional[str]:
    wanted = request.cookies.get(DEMO_VIEW_COOKIE, "")
    return wanted if wanted and demo.is_demo_user(wanted) else None


def _payload(st: demo.Status, request: web.Request, viewing: Optional[str] = None) -> Dict[str, Any]:
    out = st.to_json()
    out["viewing"] = viewing if viewing is not None else _viewing(request)
    return out


def _set_view(request: web.Request, response: web.StreamResponse, user_id: str) -> None:
    response.set_cookie(DEMO_VIEW_COOKIE, user_id, max_age=SESSION_MAX_AGE, httponly=True, samesite="Lax",
                        secure=request.secure or request.headers.get("X-Forwarded-Proto") == "https", path="/")


@requires("manage_users")
async def demo_status_api(request: web.Request) -> web.Response:
    """GET /api/demo"""
    return web.json_response(_payload(demo.status(), request))


@requires("manage_users")
async def load_demo_api(request: web.Request) -> web.Response:
    """POST /api/demo"""
    was_loaded = demo.status().loaded
    st = await demo.load()
    return web.json_response(_payload(st, request), status=200 if was_loaded else 201)


@requires("manage_users")
async def remove_demo_api(request: web.Request) -> web.Response:
    """DELETE /api/demo"""
    removed = await demo.remove()
    response = web.json_response({"loaded": False, "removed": len(removed), "users": [], "viewing": None})
    response.del_cookie(DEMO_VIEW_COOKIE, path="/")
    return response


@requires("manage_users")
async def view_demo_api(request: web.Request) -> web.Response:
    """POST /api/demo/view"""
    try:
        body = await request.json() if request.can_read_body else {}
    except (json.JSONDecodeError, ValueError):
        body = None
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid", "detail": "a JSON object is expected"}, status=400)
    found = users.demo_users()
    if not found:
        return web.json_response({"error": "not_loaded"}, status=409)
    wanted = body.get("user_id") or found[0].id
    if not isinstance(wanted, str) or not demo.is_demo_user(wanted):
        return web.json_response({"error": "not_found"}, status=404)
    response = web.json_response({"viewing": wanted})
    _set_view(request, response, wanted)
    return response


@requires("manage_users")
async def stop_viewing_api(request: web.Request) -> web.Response:
    """DELETE /api/demo/view"""
    response = web.json_response({"viewing": None})
    response.del_cookie(DEMO_VIEW_COOKIE, path="/")
    return response


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/demo", demo_status_api)
    r.add_post("/api/demo", load_demo_api)
    r.add_delete("/api/demo", remove_demo_api)
    r.add_post("/api/demo/view", view_demo_api)
    r.add_delete("/api/demo/view", stop_viewing_api)
