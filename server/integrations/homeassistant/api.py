"""HTTP routes of the homeassistant kind: proxies to the caller's own Home
Assistant for the web UI, the tablet and the TV (docs/dashboard.md).

- ``GET /api/zones`` — their zones (fallbacks when HA fails)
- ``GET /api/client-config`` — their phone / person entity names
- ``GET /api/ha/state/{entity_id}``, ``GET /api/ha/states?ids=|domains=`` (compact tiles)
- ``POST /api/ha/service {domain, service, entity_id, service_data?}``

403 ``not_connected`` without ``homeassistant``. Camera proxies are the cameras
kind's (integrations/cameras/api.py).
"""

import logging

from aiohttp import web

from core.auth import request_user
from integrations.framework import store
from integrations.homeassistant import client as ha_client, config as ha_config

logger = logging.getLogger(__name__)


def _ha_conn(request: web.Request) -> store.Connection:
    """The request's user's Home Assistant connection (else the middleware
    answers 403 ``not_connected``)."""
    return store.require(request_user(request), ha_config.KIND)


_FALLBACK_ZONES = [{"name": "Home", "entity_id": "zone.home"}, {"name": "Work", "entity_id": "zone.work"}]


async def get_zones_api(request: web.Request) -> web.Response:
    """GET /api/zones - the user's zones from their HA, or fallbacks when it fails."""
    conn = _ha_conn(request)
    try:
        r = await ha_client.get(conn, "/api/states", timeout=5.0)
        if r.status_code != 200:
            logger.warning(f"Failed to fetch zones from HA ({r.status_code}), returning fallbacks.")
            return web.json_response(_FALLBACK_ZONES)
        res = []
        for z in r.json():
            entity_id = z.get("entity_id", "")
            if not entity_id.startswith("zone."):
                continue
            friendly_name = z.get("attributes", {}).get("friendly_name")
            name = friendly_name or entity_id.replace("zone.", "").replace("_", " ").title()
            res.append({"name": name, "entity_id": entity_id})
        return web.json_response(res or _FALLBACK_ZONES)
    except Exception as e:
        logger.error(f"Error fetching zones from Home Assistant: {e}")
        return web.json_response(_FALLBACK_ZONES)


async def client_config_api(request: web.Request) -> web.Response:
    """GET /api/client-config — the user's phone / person entity names (their Home Assistant settings)."""
    return web.json_response(ha_config.client_config(_ha_conn(request)))


async def ha_entity_state_api(request: web.Request) -> web.Response:
    """GET /api/ha/state/{entity_id} — proxy entity state from HA REST API."""
    entity_id = request.match_info.get("entity_id")
    if not entity_id:
        return web.json_response({"error": "entity_id is required"}, status=400)

    conn = _ha_conn(request)
    if not ha_client.valid_entity_id(entity_id):
        return web.json_response({"error": "not an entity id"}, status=400)
    try:
        r = await ha_client.get(conn, f"/api/states/{entity_id}", timeout=5.0)
        if r.status_code != 200:
            return web.json_response({"error": f"HA returned {r.status_code}"}, status=502)
        return web.json_response(r.json())
    except Exception as e:
        logger.error(f"Error fetching HA state for {entity_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


# Attributes a client tile needs; everything else (entity pictures, long lists of
# effects, etc.) stays on the server so a TV polling every few seconds gets ~1 KB.
_HA_TILE_ATTRS = (
    "unit_of_measurement", "device_class", "icon",
    "current_temperature", "temperature", "target_temp_step", "min_temp", "max_temp",
    "hvac_modes", "hvac_action", "fan_mode",
    "brightness", "color_mode", "current_position", "battery_level",
    "media_title", "app_name", "source",
)


def compact_ha_states(states: list, ids: list[str] | None = None, domains: list[str] | None = None) -> list:
    """Filter HA /api/states to the requested ids (kept in request order) or domains,
    trimmed to what a control tile shows. Unknown ids come back as `unavailable`."""
    def compact(s: dict) -> dict:
        attrs = s.get("attributes") or {}
        entity_id = s.get("entity_id", "")
        return {
            "entity_id": entity_id,
            "domain": entity_id.split(".", 1)[0],
            "state": s.get("state"),
            "name": attrs.get("friendly_name") or entity_id,
            "attributes": {k: attrs[k] for k in _HA_TILE_ATTRS if k in attrs},
            "last_changed": s.get("last_changed"),
        }

    if ids:
        by_id = {s.get("entity_id"): s for s in states}
        out = []
        for entity_id in ids:
            s = by_id.get(entity_id)
            out.append(compact(s) if s else {
                "entity_id": entity_id, "domain": entity_id.split(".", 1)[0],
                "state": "unavailable", "name": entity_id, "attributes": {}, "last_changed": None,
            })
        return out
    wanted = set(domains or [])
    picked = [compact(s) for s in states if s.get("entity_id", "").split(".", 1)[0] in wanted]
    return sorted(picked, key=lambda s: (s["domain"], s["name"].lower()))


async def ha_states_api(request: web.Request) -> web.Response:
    """GET /api/ha/states?ids=a,b,c | ?domains=light,climate — compact entity states.

    `ids` returns exactly those entities in that order (control tiles); `domains`
    lists every entity of those domains (entity pickers)."""
    split = lambda v: [x.strip() for x in (v or "").split(",") if x.strip()]
    ids, domains = split(request.query.get("ids")), split(request.query.get("domains"))
    if not ids and not domains:
        return web.json_response({"error": "ids or domains is required"}, status=400)
    conn = _ha_conn(request)
    try:
        states = await ha_client.cached_states(conn)
    except Exception as e:
        logger.warning(f"HA states proxy failed: {e}")
        return web.json_response({"error": str(e)}, status=502)
    return web.json_response(compact_ha_states(states, ids=ids, domains=domains))


async def ha_call_service_api(request: web.Request) -> web.Response:
    """POST /api/ha/service — call a HA service (climate.set_temperature, etc.)."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    domain = (data.get("domain") or "").strip()
    service = (data.get("service") or "").strip()
    entity_id = (data.get("entity_id") or "").strip()
    service_data = data.get("service_data") or {}

    if not domain or not service or not entity_id:
        return web.json_response({"error": "domain, service, and entity_id are required"}, status=400)

    conn = _ha_conn(request)
    try:
        path = ha_client.service_path(domain, service)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    if not isinstance(service_data, dict):
        return web.json_response({"error": "service_data must be an object"}, status=400)
    payload = {"entity_id": entity_id}
    payload.update(service_data)

    logger.info(f"HA proxy: {domain}.{service} on {entity_id} — {service_data}")
    try:
        r = await ha_client.post(conn, path, json_body=payload, timeout=10.0)
        if r.status_code not in (200, 201):
            return web.json_response({"error": f"HA returned {r.status_code}: {r.text[:200]}"}, status=502)
        ha_client.forget_cached_states(conn)  # the next /api/ha/states poll shows the change
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.error(f"Error calling HA service {domain}.{service}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


def setup(app: web.Application) -> None:
    """Register the Home Assistant routes."""
    app.router.add_get("/api/zones", get_zones_api)
    app.router.add_get("/api/ha/state/{entity_id}", ha_entity_state_api)
    app.router.add_get("/api/ha/states", ha_states_api)
    app.router.add_get("/api/client-config", client_config_api)
    app.router.add_post("/api/ha/service", ha_call_service_api)
