"""Zones over HTTP (docs/zones.md; core/zones.py).

    GET    /api/zones                → [{name, entity_id, id, latitude, longitude, radius_m, is_home, created_at}]
                                       (the caller's zones, Home first; ``name`` / ``entity_id`` are what
                                       the reminder editors read)
    POST   /api/zones {name, latitude, longitude, radius_m?, is_home?} → 201 {zone}
    PATCH  /api/zones/{id} {name?, latitude?, longitude?, radius_m?, is_home?} → {zone}
    DELETE /api/zones/{id}           → {ok}
    PUT    /api/zones/home {latitude, longitude, radius_m?} → {zone}: the caller's Home moves there
                                       (or is made)
    GET    /api/places?q=<name>      → {places: [{name, label, latitude, longitude, timezone,
                                       country_code, currency}]} (core/places.py; [] when the
                                       search is off or fails)

Every signed-in user, with or without Home Assistant; each sees and changes
only their own zones (someone else's id answers 404 like an unknown one). A
field that is not valid, or a name they already use: 400 ``{"error":
"invalid", "field", "detail"}``. Importing Home Assistant's zones is that
addon's route (``POST /api/ha/zones/import``).
"""

import json
from typing import Any, Dict, Union

from aiohttp import web

from core import places, user_locale, zones
from core.auth import request_user


def _invalid(field: str, detail: str) -> web.Response:
    return web.json_response({"error": "invalid", "field": field, "detail": detail}, status=400)


async def _body(request: web.Request) -> Union[Dict[str, Any], web.Response]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        body = None
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid", "detail": "a JSON object is expected"}, status=400)
    return body


async def list_zones_api(request: web.Request) -> web.Response:
    """GET /api/zones"""
    return web.json_response([z.to_dict() for z in zones.list_zones(request_user(request))])


async def create_zone_api(request: web.Request) -> web.Response:
    """POST /api/zones"""
    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    try:
        zone = zones.create(request_user(request), body.get("name"), body.get("latitude"), body.get("longitude"),
                            body.get("radius_m", zones.DEFAULT_RADIUS), bool(body.get("is_home")))
    except zones.Invalid as e:
        return _invalid(e.field, e.detail)
    return web.json_response({"zone": zone.to_dict()}, status=201)


async def update_zone_api(request: web.Request) -> web.Response:
    """PATCH /api/zones/{id}"""
    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    try:
        zone = zones.update(request_user(request), request.match_info["id"], body)
    except LookupError:
        return web.json_response({"error": "not_found"}, status=404)
    except zones.Invalid as e:
        return _invalid(e.field, e.detail)
    return web.json_response({"zone": zone.to_dict()})


async def delete_zone_api(request: web.Request) -> web.Response:
    """DELETE /api/zones/{id}"""
    if not zones.delete(request_user(request), request.match_info["id"]):
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response({"ok": True})


async def set_home_api(request: web.Request) -> web.Response:
    """PUT /api/zones/home"""
    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    try:
        zone = zones.set_home(request_user(request), body.get("latitude"), body.get("longitude"),
                              body.get("radius_m", zones.DEFAULT_RADIUS))
    except zones.Invalid as e:
        return _invalid(e.field, e.detail)
    return web.json_response({"zone": zone.to_dict()})


async def places_api(request: web.Request) -> web.Response:
    """GET /api/places?q="""
    user = request_user(request)
    language = user_locale.languages(user)[0].split("-")[0]
    return web.json_response({"places": await places.search(request.query.get("q", ""), language=language)})


def setup(app: web.Application) -> None:
    """Register the zones routes."""
    app.router.add_get("/api/zones", list_zones_api)
    app.router.add_post("/api/zones", create_zone_api)
    app.router.add_put("/api/zones/home", set_home_api)
    app.router.add_patch("/api/zones/{id}", update_zone_api)
    app.router.add_delete("/api/zones/{id}", delete_zone_api)
    app.router.add_get("/api/places", places_api)
