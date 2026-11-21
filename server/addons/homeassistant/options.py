"""Candidates for the Home Assistant addon's entity settings, read from the
user's own Home Assistant (``Addon(options=)``: ``GET
/api/addons/homeassistant/options``), so a client can offer a picker.

Each setting gets ``[{"value": entity_id, "label": friendly name}]`` sorted
by label: ``person`` the person entities, ``car_sensor`` the sensors with a
``connected_paired_devices`` attribute (``car_device`` the Bluetooth names
those sensors list now), ``phone_interactive_sensor`` the binary sensors,
``phone_battery_sensor`` the battery sensors, ``weather_entity`` the weather
entities, ``tv_entity`` the media players. ``phone_notify_service`` is a
service and ``tv_channel_command`` a command, not entities: no candidates.
"""

import ast
import json
from typing import Any, Dict, List

from core.addons import Connection
from addons.homeassistant import client
from addons.homeassistant.config import (BATTERY_SENSOR, CAR_DEVICE, CAR_SENSOR, PERSON, SCREEN_SENSOR, TV,
                                               WEATHER)

Option = Dict[str, Any]


def _devices(raw: Any) -> List[str]:
    """The entries of a ``connected_paired_devices`` attribute (a list, or a
    string holding one, as the Android companion app reports it)."""
    if isinstance(raw, list):
        return [str(d).strip() for d in raw if str(d).strip()]
    text = str(raw or "").strip()
    for parse in (json.loads, ast.literal_eval):
        try:
            value = parse(text)
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, (list, tuple)):
            return [str(d).strip() for d in value if str(d).strip()]
    inner = text.strip("[]")
    return [d.strip().strip("'\"") for d in inner.split(",") if d.strip().strip("'\"")]


def _option(state: Dict[str, Any]) -> Option:
    eid = state["entity_id"]
    return {"value": eid, "label": str((state.get("attributes") or {}).get("friendly_name") or eid)}


async def entity_options(conn: Connection) -> Dict[str, List[Option]]:
    """The candidates for each entity setting (see the module docstring).

    Raises:
        RuntimeError / httpx.HTTPError: Home Assistant could not be read.
    """
    states = await client.fetch_states(conn)
    out: Dict[str, List[Option]] = {PERSON: [], CAR_SENSOR: [], CAR_DEVICE: [], SCREEN_SENSOR: [],
                                    BATTERY_SENSOR: [], WEATHER: [], TV: []}
    devices: Dict[str, None] = {}
    for s in states:
        eid = str(s.get("entity_id") or "")
        attrs = s.get("attributes") or {}
        domain = eid.split(".", 1)[0]
        if domain == "person":
            out[PERSON].append(_option(s))
        elif domain == "binary_sensor":
            out[SCREEN_SENSOR].append(_option(s))
        elif domain == "weather":
            out[WEATHER].append(_option(s))
        elif domain == "media_player":
            out[TV].append(_option(s))
        elif domain == "sensor":
            if "connected_paired_devices" in attrs:
                out[CAR_SENSOR].append(_option(s))
                devices.update(dict.fromkeys(_devices(attrs["connected_paired_devices"])))
            if attrs.get("device_class") == "battery":
                out[BATTERY_SENSOR].append(_option(s))
    for name in out:
        out[name].sort(key=lambda o: (o["label"].lower(), o["value"]))
    out[CAR_DEVICE] = [{"value": d, "label": d} for d in sorted(devices, key=str.lower)]
    return out
