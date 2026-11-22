"""One user's Home Assistant, over that user's own connection: REST (states,
services, history, config, zones) and the WebSocket API (entity registry).

Every function takes the user's :class:`~core.addons.Connection`
(host and token from its credentials); nothing is shared between
connections, caches included."""

import datetime
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
import websockets  # type: ignore

from core import clock
from core.users import User
from core.addons import Connection, addresses, store
from addons.homeassistant.config import DEFAULT_HOST, ADDON

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection details
# ---------------------------------------------------------------------------

def host(conn: Connection) -> str:
    return (conn.credentials.get("host") or DEFAULT_HOST).strip().rstrip("/")


def token(conn: Connection) -> str:
    return conn.credentials.get("token") or ""


def url(conn: Connection, path: str) -> str:
    """The full URL of ``path`` (``/api/...``; ids in it through
    :func:`~core.addons.addresses.segment`) on the user's Home
    Assistant, built so it cannot leave that host (docs/access.md, "Security rules").

    Raises:
        ValueError: the stored host is not a valid address, or ``path`` is not a plain path.
    """
    return addresses.join(host(conn), path)


# What an entity id and a service's domain / name look like in Home Assistant;
# anything else is refused before it goes into a path (docs/access.md, "Security rules").
ENTITY_ID_RE = re.compile(r"^[a-z0-9_]{1,64}\.[a-z0-9_]{1,255}$")
NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def valid_entity_id(entity_id: str) -> bool:
    return bool(ENTITY_ID_RE.match(entity_id or ""))


def service_path(domain: str, service: str) -> str:
    """``/api/services/{domain}/{service}``.

    Raises:
        ValueError: ``domain`` or ``service`` is not a Home Assistant name.
    """
    if not (NAME_RE.match(domain or "") and NAME_RE.match(service or "")):
        raise ValueError(f"'{domain}.{service}' is not a Home Assistant service name.")
    return f"/api/services/{domain}/{service}"


def headers(conn: Connection) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token(conn)}", "Content-Type": "application/json"}


def ws_uri(conn: Connection) -> str:
    """The WebSocket API URL of the user's Home Assistant.

    Raises:
        ValueError: the stored host is not a valid address.
    """
    base = url(conn, "/api/websocket")
    return "wss://" + base[len("https://"):] if base.startswith("https://") else "ws://" + base[len("http://"):]


def signature(conn: Connection) -> Tuple[str, str, str]:
    """What identifies one connection's Home Assistant (caches key on it)."""
    return (conn.user.id, host(conn), token(conn))


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------

async def get(conn: Connection, path: str, *, timeout: float, params: Optional[Dict[str, Any]] = None
              ) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.get(url(conn, path), params=params, headers=headers(conn), timeout=timeout)


async def post(conn: Connection, path: str, *, json_body: Dict[str, Any], timeout: float,
               flag: str = "") -> httpx.Response:
    """POST ``path``; ``flag`` is a fixed query word added as is (``return_response``)."""
    target = url(conn, path) + (f"?{flag}" if flag else "")
    async with httpx.AsyncClient() as client:
        return await client.post(target, headers=headers(conn), json=json_body, timeout=timeout)


async def entity_state(conn: Connection, entity_id: str, timeout: float = 6.0) -> Optional[Dict[str, Any]]:
    """The state object of ``entity_id`` (None when it cannot be read)."""
    try:
        r = await get(conn, f"/api/states/{addresses.segment(entity_id)}", timeout=timeout)
    except Exception as e:
        logger.warning(f"[HA] state of {entity_id} failed: {e}")
        return None
    return r.json() if r.status_code == 200 else None


async def fetch_states(conn: Connection, timeout: float = 15.0) -> List[Dict[str, Any]]:
    """Every state object (``/api/states``).

    Raises:
        RuntimeError: Home Assistant answered with an error status.
        httpx.HTTPError: it could not be reached.
    """
    r = await get(conn, "/api/states", timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HA returned {r.status_code}")
    return r.json()


async def fetch_all_states(conn: Connection) -> Dict[str, Dict[str, Any]]:
    """``{entity_id: state object}`` for every entity."""
    return {s["entity_id"]: s for s in await fetch_states(conn)}


# Several clients polling /api/ha/states share one upstream call per connection.
STATES_TTL_SECONDS = 1.5
_states_cache: Dict[Tuple[str, str, str], Tuple[float, List[Dict[str, Any]]]] = {}


async def cached_states(conn: Connection) -> List[Dict[str, Any]]:
    """``/api/states`` of this connection, reused for :data:`STATES_TTL_SECONDS`."""
    key = signature(conn)
    hit = _states_cache.get(key)
    if hit and time.monotonic() - hit[0] < STATES_TTL_SECONDS:
        return hit[1]
    states = await fetch_states(conn, timeout=8.0)
    _states_cache[key] = (time.monotonic(), states)
    return states


def forget_cached_states(conn: Connection) -> None:
    """The next :func:`cached_states` asks Home Assistant again (after a service call)."""
    _states_cache.pop(signature(conn), None)


async def history(conn: Connection, entity_id: str, since_utc: datetime.datetime,
                  until_utc: datetime.datetime) -> Optional[List[Dict[str, Any]]]:
    """State changes of ``entity_id`` in [since, until] (minimal rows:
    ``state``, ``last_changed``); None when Home Assistant cannot be asked."""
    try:
        r = await get(conn, f"/api/history/period/{addresses.segment(since_utc.isoformat())}",
                      params={"filter_entity_id": entity_id, "end_time": until_utc.isoformat(),
                              "minimal_response": "", "no_attributes": ""}, timeout=10.0)
        if r.status_code != 200:
            logger.warning(f"[HA] history of {entity_id} returned {r.status_code}")
            return None
        body = r.json()
    except Exception as e:
        logger.warning(f"[HA] history of {entity_id} failed: {e}")
        return None
    return (body or [[]])[0] if body else []


async def forecast(conn: Connection, entity_id: str) -> Optional[Dict[str, Any]]:
    """Today's daily forecast of weather entity ``entity_id`` (``weather.get_forecasts``)."""
    try:
        r = await post(conn, "/api/services/weather/get_forecasts",
                       json_body={"entity_id": entity_id, "type": "daily"}, timeout=6.0, flag="return_response")
        if r.status_code != 200:
            return None
        body = r.json()
    except Exception as e:
        logger.debug(f"[HA] forecast of {entity_id} failed: {e}")
        return None
    resp = body.get("service_response", body) if isinstance(body, dict) else {}
    days = ((resp or {}).get(entity_id) or {}).get("forecast") or []
    return days[0] if days else None


# ---------------------------------------------------------------------------
# Zones (copied into Dana's own zones: POST /api/ha/zones/import)
# ---------------------------------------------------------------------------

async def zones(conn: Connection) -> List[Dict[str, Any]]:
    """The user's Home Assistant zones as ``{name, latitude, longitude,
    radius_m, is_home}`` (those with a name and a position; ``zone.home`` is
    Home).

    Raises:
        Exception: Home Assistant could not be reached or answered an error.
    """
    out: List[Dict[str, Any]] = []
    for s in await fetch_states(conn, timeout=10.0):
        entity_id = str(s.get("entity_id", ""))
        if not entity_id.startswith("zone."):
            continue
        attrs = s.get("attributes") or {}
        name = str(attrs.get("friendly_name") or "").strip()
        if not name or attrs.get("latitude") is None or attrs.get("longitude") is None:
            continue
        out.append({"name": name, "latitude": attrs.get("latitude"), "longitude": attrs.get("longitude"),
                    "radius_m": attrs.get("radius") or 100, "is_home": entity_id == "zone.home"})
    return out


# ---------------------------------------------------------------------------
# WebSocket: the exposed entities and their registry entries
# ---------------------------------------------------------------------------

async def authenticate(ws: Any, conn: Connection) -> None:
    """The WebSocket handshake (``auth_required`` → ``auth`` → ``auth_ok``).

    Raises:
        PermissionError: Home Assistant refused the token.
    """
    await ws.recv()  # auth_required
    await ws.send(json.dumps({"type": "auth", "access_token": token(conn)}))
    if json.loads(await ws.recv()).get("type") != "auth_ok":
        raise PermissionError("Home Assistant authentication failed. Check the connection's token.")


async def fetch_registry(conn: Connection) -> Tuple[Set[str], Dict[str, Dict[str, Any]]]:
    """Return (exposed_ids, registry_map) via WebSocket.

    registry_map: { entity_id -> { "name": str, "aliases": list[str] } }

    Note: config/entity_registry/list omits the aliases field. We use
    pipelined config/entity_registry/get calls (one per exposed entity)
    to get the full entry. All requests are sent before reading any
    responses, so this is a single batch round-trip regardless of count.
    """
    async with websockets.connect(ws_uri(conn)) as ws:
        await authenticate(ws, conn)

        # Step 1: get exposed entity IDs
        await ws.send(json.dumps({"id": 1, "type": "homeassistant/expose_entity/list"}))
        exposed_map: Dict[str, Any] = json.loads(await ws.recv())["result"]["exposed_entities"]
        exposed_ids: Set[str] = {
            eid for eid, data in exposed_map.items()
            if data.get("conversation") is True
        }

        if not exposed_ids:
            return set(), {}

        # Step 2: pipeline config/entity_registry/get for each exposed entity.
        # Send ALL requests first, then read ALL responses — one batch, no sequential waiting.
        id_to_eid: Dict[int, str] = {}
        for msg_id, eid in enumerate(sorted(exposed_ids), start=2):
            id_to_eid[msg_id] = eid
            await ws.send(json.dumps({
                "id": msg_id,
                "type": "config/entity_registry/get",
                "entity_id": eid,
            }))

        registry_map: Dict[str, Dict[str, Any]] = {}
        for _ in id_to_eid:
            resp = json.loads(await ws.recv())
            eid = id_to_eid.get(resp.get("id"), "")
            if not eid or not resp.get("success"):
                continue
            item = resp["result"]
            registry_map[eid] = {
                "name": item.get("name") or item.get("original_name") or eid,
                # aliases may contain null entries — filter them out
                "aliases": [a for a in (item.get("aliases") or []) if a],
            }

    return exposed_ids, registry_map


# ---------------------------------------------------------------------------
# The server's timezone (core/clock.py)
# ---------------------------------------------------------------------------

def timezone_of(user: User) -> Optional[str]:
    """The ``time_zone`` of ``user``'s Home Assistant (``/api/config``; None
    without the addon or when it cannot be read). Blocking: core/clock.py asks
    it once at startup, for the first admin."""
    conn = store.get(user, ADDON)
    if conn is None:
        return None
    try:
        r = httpx.get(url(conn, "/api/config"), headers=headers(conn), timeout=3.0)
        if r.status_code == 200:
            return r.json().get("time_zone")
    except Exception as e:
        logger.warning(f"Failed to fetch timezone from Home Assistant: {e}")
    return None


clock.set_home_assistant_zone(timezone_of)


# ---------------------------------------------------------------------------
# Connection check (Addon(check=): POST /api/addons/homeassistant/test)
# ---------------------------------------------------------------------------

async def check(conn: Connection) -> Optional[str]:
    """One cheap request (``/api/config``): None when Home Assistant answers
    with the token, else a short reason (never the token)."""
    try:
        r = await get(conn, "/api/config", timeout=8.0)
    except Exception as e:
        return f"Could not reach Home Assistant at {host(conn)} ({type(e).__name__})."
    if r.status_code in (401, 403):
        return f"Home Assistant refused the token (HTTP {r.status_code})."
    if r.status_code != 200:
        return f"Home Assistant answered HTTP {r.status_code}."
    return None
