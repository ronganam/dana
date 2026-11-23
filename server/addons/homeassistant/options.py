"""Candidates for the Home Assistant addon's entity settings, read from the
user's own Home Assistant (``Addon(options=)``: ``GET
/api/addons/homeassistant/options``), so a client can offer a picker.

Each setting gets ``[{"value": entity_id, "label": friendly name}]`` sorted
by label: ``phone_battery_sensor`` the battery sensors, ``tv_entity`` the
media players. ``tv_channel_command`` is a command, not an entity: no
candidates.
"""

from typing import Any, Dict, List

from core.addons import Connection
from addons.homeassistant import client
from addons.homeassistant.config import BATTERY_SENSOR, TV

Option = Dict[str, Any]


def _option(state: Dict[str, Any]) -> Option:
    eid = state["entity_id"]
    return {"value": eid, "label": str((state.get("attributes") or {}).get("friendly_name") or eid)}


async def entity_options(conn: Connection) -> Dict[str, List[Option]]:
    """The candidates for each entity setting (see the module docstring).

    Raises:
        RuntimeError / httpx.HTTPError: Home Assistant could not be read.
    """
    states = await client.fetch_states(conn)
    out: Dict[str, List[Option]] = {BATTERY_SENSOR: [], TV: []}
    for s in states:
        eid = str(s.get("entity_id") or "")
        attrs = s.get("attributes") or {}
        domain = eid.split(".", 1)[0]
        if domain == "media_player":
            out[TV].append(_option(s))
        elif domain == "sensor" and attrs.get("device_class") == "battery":
            out[BATTERY_SENSOR].append(_option(s))
    for name in out:
        out[name].sort(key=lambda o: (o["label"].lower(), o["value"]))
    return out
