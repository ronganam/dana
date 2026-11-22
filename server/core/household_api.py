"""The household: who uses this Dana, over HTTP (docs/household.md; the web
Settings → Household screen, static/household.js).

    GET    /api/household                    → {members: [<member>], can_manage, you}
    POST   /api/household/members            {name, pronoun?, languages?, level?} → 201 {member}
    PATCH  /api/household/members/{id}       {name?, pronoun?, languages?, level?} → {member}
    DELETE /api/household/members/{id}       → {ok, removed: <id>}

``<member>`` = ``{id, name, level, pronoun, languages: [..], devices,
last_seen_at, you}``: ``devices`` counts their devices, ``last_seen_at`` is
the newest use of any of their devices or tokens (ISO UTC, null: never).

Anyone signed in may list; without ``manage_users`` the list holds only
themselves (``can_manage: false``). Adding, changing and removing need
``manage_users``. A new member gets their default addons (as ``bin/dana users
add``) and no device: the screen's Invite starts their pairing codes
(``POST /api/devices/pairings`` with ``user_id``, docs/devices.md). The last
admin cannot lose ``admin`` or be removed (409 ``last_admin``), and an admin
cannot remove themselves here (409 ``self``: ``bin/dana users remove``).
The demo household's users are not members (404, core/demo.py). A bad field:
400 ``{"error": "invalid", "field", "detail"}``.
"""

import json
from typing import Any, Dict, List, Optional, Tuple, Union

from aiohttp import web

from core import access, user_locale, user_rows, users
from core.addons import store
from core.auth import request_user, requires
from core.db import get_db_connection
from core.users import User


def _invalid(field: str, detail: str) -> web.Response:
    return web.json_response({"error": "invalid", "field": field, "detail": detail}, status=400)


def _activity() -> Dict[str, Tuple[int, Optional[str]]]:
    """user id → (devices, newest use of a device or token)."""
    out: Dict[str, Tuple[int, Optional[str]]] = {}
    with get_db_connection() as conn:
        # scoped: every user's device count, only for the household list (manage_users, or filtered to the caller)
        for r in conn.execute("SELECT user_id, COUNT(*) AS n, MAX(last_seen_at) AS seen FROM devices "
                              "GROUP BY user_id"):
            out[r["user_id"]] = (int(r["n"]), r["seen"])
        # scoped: every user's newest token use, only for the household list (as above)
        for r in conn.execute("SELECT user_id, MAX(last_used_at) AS used FROM user_tokens GROUP BY user_id"):
            n, seen = out.get(r["user_id"], (0, None))
            out[r["user_id"]] = (n, max(filter(None, (seen, r["used"])), default=None))
    return out


def member_payload(user: User, caller: User, activity: Dict[str, Tuple[int, Optional[str]]]) -> Dict[str, Any]:
    """One member of the household as the API shows them."""
    n, seen = activity.get(user.id, (0, None))
    try:
        languages = list(user_locale.parse_languages(user.languages))
    except ValueError:
        languages = []
    return {"id": user.id, "name": user.name, "level": user.access, "pronoun": user.pronoun,
            "languages": languages, "devices": n, "last_seen_at": seen, "you": user.id == caller.id}


async def _body(request: web.Request) -> Union[Dict[str, Any], web.Response]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        body = None
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid", "detail": "a JSON object is expected"}, status=400)
    return body


def _fields(body: Dict[str, Any]) -> Union[Dict[str, str], web.Response]:
    """The member fields in ``body``, checked (only the ones given)."""
    out: Dict[str, str] = {}
    for key in ("name", "pronoun", "languages", "level"):
        if key not in body:
            continue
        value = body[key]
        if key == "languages" and isinstance(value, list):
            value = ",".join(str(v) for v in value)
        if not isinstance(value, str):
            return _invalid(key, f"{key} must be text")
        value = value.strip()
        try:
            if key == "name":
                if not value:
                    raise ValueError("A member needs a name.")
                if len(value) > 80:
                    raise ValueError("At most 80 characters.")
            elif key == "pronoun":
                value = user_rows.check_pronoun(value or "they")
            elif key == "languages":
                value = ",".join(user_locale.parse_languages(value))
            else:
                access.level(value)
        except ValueError as e:
            return _invalid(key, str(e))
        out[key] = value
    return out


def _member(request: web.Request) -> Union[User, web.Response]:
    found = users.get(request.match_info["id"])
    if found is None or found.demo:
        return web.json_response({"error": "not_found"}, status=404)
    return found


async def household_api(request: web.Request) -> web.Response:
    """GET /api/household"""
    caller = request_user(request)
    manage = caller.level.allows("manage_users")
    everyone: List[User] = users.list_users() if manage else [caller]
    activity = _activity()
    return web.json_response({"members": [member_payload(u, caller, activity) for u in everyone],
                              "can_manage": manage, "you": caller.id})


@requires("manage_users")
async def add_member_api(request: web.Request) -> web.Response:
    """POST /api/household/members"""
    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    fields = _fields(body)
    if isinstance(fields, web.Response):
        return fields
    if not fields.get("name"):
        return _invalid("name", "A member needs a name.")
    profile = user_rows.Profile(pronoun=fields.get("pronoun", "they"), languages=fields.get("languages", ""))
    user = users.create(fields["name"], fields.get("level", access.MEMBER), profile=profile)
    store.connect_defaults(user)
    return web.json_response({"member": member_payload(user, request_user(request), _activity())}, status=201)


@requires("manage_users")
async def update_member_api(request: web.Request) -> web.Response:
    """PATCH /api/household/members/{id}"""
    member = _member(request)
    if isinstance(member, web.Response):
        return member
    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    fields = _fields(body)
    if isinstance(fields, web.Response):
        return fields
    level = fields.pop("level", None)
    if level is not None and member.is_admin and level != access.ADMIN \
            and sum(1 for u in users.list_users() if u.is_admin) <= 1:
        return web.json_response({"error": "last_admin", "detail": f"{member.name} is the last admin; make "
                                                                   "someone else admin first."}, status=409)
    updated = users.update(member.id, access_level=level, **fields)
    return web.json_response({"member": member_payload(updated, request_user(request), _activity())})


@requires("manage_users")
async def remove_member_api(request: web.Request) -> web.Response:
    """DELETE /api/household/members/{id}"""
    member = _member(request)
    if isinstance(member, web.Response):
        return member
    if member.id == request_user(request).id:
        return web.json_response({"error": "self", "detail": "You cannot remove yourself here."}, status=409)
    try:
        users.remove(member.id)
    except ValueError as e:
        return web.json_response({"error": "last_admin", "detail": str(e)}, status=409)
    store.invalidate()
    return web.json_response({"ok": True, "removed": member.id})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/household", household_api)
    r.add_post("/api/household/members", add_member_api)
    r.add_patch("/api/household/members/{id}", update_member_api)
    r.add_delete("/api/household/members/{id}", remove_member_api)
