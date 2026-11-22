"""HTTP routes for people and the incoming-call card (docs/people.md).

    GET    /api/people?q=&sort=recent|name|drifting|pinned&hidden=1
    POST   /api/people                  {name, phones?, aliases?, relation?, notes?, birthday?, pinned?}
    GET    /api/people/lookup?phone=     → {found, card} (no model calls)
    GET    /api/people/merge-candidates  → {pairs}
    GET|PUT|DELETE /api/people/{id}
    POST   /api/people/{id}/merge       {other_id}
    POST   /api/people/{id}/brief       → {updated, person}
    GET    /api/people/{id}/card        → {found, card}

Every route answers for the request's user, who must have ``people``
connected (else 403 ``not_connected`` from the addon middleware);
another user's person id is 404 like an unknown one. Loaded by core/server.py
(``setup(app)``).
"""
from __future__ import annotations

import logging

from aiohttp import web

from core import budget
from core.auth import request_user
from core.users import User
from core.addons import store as connections
from addons.people import cadence, detail, merge, store
from addons.people.briefs import refresh_brief
from addons.people.values import ADDON

logger = logging.getLogger(__name__)


def people_user(request: web.Request) -> User:
    """The request's user, who must have ``people`` connected (``NotConnected``
    → 403 ``not_connected`` from the middleware)."""
    user = request_user(request)
    connections.require(user, ADDON)
    return user


async def _json(request: web.Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def list_people_api(request: web.Request) -> web.Response:
    """GET /api/people?q=&sort=recent|name|drifting|pinned&hidden=1"""
    user = people_user(request)
    q = request.query
    items = cadence.list_people(user, q=q.get("q", ""), sort=q.get("sort", "recent"),
                                include_hidden=q.get("hidden") in ("1", "true"))
    return web.json_response({"people": items})


async def create_person_api(request: web.Request) -> web.Response:
    """POST /api/people {name, phones?, aliases?, relation?, notes?, birthday?, pinned?}"""
    user = people_user(request)
    d = await _json(request)
    try:
        p = store.create_person(user, d.get("name") or "", phones=d.get("phones") or [],
                                aliases=d.get("aliases") or [], relation=d.get("relation") or "",
                                notes=d.get("notes") or "", birthday=d.get("birthday") or "",
                                pinned=bool(d.get("pinned")))
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response(p, status=201)


async def get_person_api(request: web.Request) -> web.Response:
    p = detail.get_person(people_user(request), request.match_info["id"])
    if not p:
        return web.json_response({"error": "Person not found"}, status=404)
    return web.json_response(p)


async def update_person_api(request: web.Request) -> web.Response:
    """PUT /api/people/{id} — name, aliases, add_alias, phones, add_phone, relation, notes, birthday, emails, pinned, hidden"""
    user = people_user(request)
    try:
        return web.json_response(store.update_person(user, request.match_info["id"], await _json(request)))
    except LookupError as e:
        return web.json_response({"error": str(e)}, status=404)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)


async def delete_person_api(request: web.Request) -> web.Response:
    if not store.delete_person(people_user(request), request.match_info["id"]):
        return web.json_response({"error": "Person not found"}, status=404)
    return web.json_response({"status": "deleted"})


async def merge_person_api(request: web.Request) -> web.Response:
    """POST /api/people/{id}/merge {other_id} — folds other into this person."""
    user = people_user(request)
    d = await _json(request)
    try:
        return web.json_response(merge.merge_people(user, request.match_info["id"], d.get("other_id") or ""))
    except LookupError as e:
        return web.json_response({"error": str(e)}, status=404)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)


async def merge_candidates_api(request: web.Request) -> web.Response:
    return web.json_response({"pairs": merge.merge_candidates(people_user(request))})


async def refresh_brief_api(request: web.Request) -> web.Response:
    """POST /api/people/{id}/brief — rewrite the brief now."""
    user = people_user(request)
    pid = request.match_info["id"]
    try:
        with budget.user_started():  # asked for now: the monthly budget does not hold it back
            updated = await refresh_brief(user, pid, force=True)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)
    p = detail.get_person(user, pid, schedule_brief=False)
    if not p:
        return web.json_response({"error": "Person not found"}, status=404)
    return web.json_response({"updated": updated, "person": p})


async def lookup_api(request: web.Request) -> web.Response:
    """GET /api/people/lookup?phone= — the incoming-call card. No model calls: answers in milliseconds."""
    return web.json_response(detail.lookup(people_user(request), request.query.get("phone", "")))


async def card_api(request: web.Request) -> web.Response:
    c = detail.card(people_user(request), request.match_info["id"])
    if not c:
        return web.json_response({"error": "Person not found"}, status=404)
    return web.json_response({"found": True, "card": c})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/people", list_people_api)
    r.add_post("/api/people", create_person_api)
    r.add_get("/api/people/lookup", lookup_api)
    r.add_get("/api/people/merge-candidates", merge_candidates_api)
    r.add_get("/api/people/{id}", get_person_api)
    r.add_put("/api/people/{id}", update_person_api)
    r.add_delete("/api/people/{id}", delete_person_api)
    r.add_post("/api/people/{id}/merge", merge_person_api)
    r.add_post("/api/people/{id}/brief", refresh_brief_api)
    r.add_get("/api/people/{id}/card", card_api)
