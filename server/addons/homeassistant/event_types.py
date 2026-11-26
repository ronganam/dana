"""The event the ``homeassistant`` addon emits for the event catalog
(../docs/addons.md, "Events"): ``homeassistant.state.changed``, a change of
one entity's state (emitted by :mod:`.states`), with the user's real entity
ids as its known values and the changes Dana saw as its history.
"""

import re
from typing import Dict, List

from core import dashboard, devices
from core.addons import EventType, store
from core.users import User
from addons.homeassistant import home_map, states
from addons.homeassistant.config import ADDON, BATTERY_SENSOR, setting

_ENTITY = re.compile(r"\b[a-z0-9_]+\.[a-z0-9_]+\b")
# Domains whose state changes people usually mean, listed before the rest.
DOMAINS = ("binary_sensor", "sensor", "lock", "cover", "switch", "light", "climate", "media_player", "person",
           "device_tracker", "vacuum", "alarm_control_panel", "input_boolean", "fan", "water_heater", "event")


def dashboard_ids(user: User) -> List[str]:
    """Entity ids named in the settings of Home Assistant widgets on ``user``'s dashboards."""
    out: List[str] = []
    for device in devices.list_for(user.id):
        if not dashboard.has_dashboard(device):
            continue
        for w in dashboard.layout(device).widgets:
            if not str(w.get("widget") or "").startswith("ha_"):
                continue
            for value in (w.get("settings") or {}).values():
                out += [m for m in _ENTITY.findall(str(value)) if m not in out]
    return out


def known_values(user: User) -> Dict[str, List[str]]:
    """``entity_id``: every entity of ``user``'s Home Assistant as "id (name, device class)", the phone's
    battery sensor first, then the home map's entities and the dashboards', then by domain; {} before the
    first read of their Home Assistant (nothing is known then, so nothing is checked)."""
    ents = states.known_entities(user)
    if not ents:
        return {}
    conn = store.get(user, ADDON)
    battery = setting(conn, BATTERY_SENSOR) if conn is not None else None
    first = ([battery] if battery else []) + (home_map.exposed_ids(conn) if conn is not None else [])
    first += dashboard_ids(user)
    rank = {eid: i for i, eid in reversed(list(enumerate(first)))}

    def order(eid: str):
        e = ents[eid]
        domain = DOMAINS.index(e.domain) if e.domain in DOMAINS else len(DOMAINS)
        return (rank.get(eid, len(first)), domain, e.name.casefold(), eid)

    out = []
    for eid in sorted(ents, key=order):
        e = ents[eid]
        what = [e.name if e.name != eid else ""] + ([e.device_class] if e.device_class else [])
        if eid == battery:
            what.append("the user's phone battery")
        words = ", ".join(w for w in what if w)
        out.append(f"{eid} ({words})" if words else eid)
    return {"entity_id": out}


HOMEASSISTANT_EVENTS = (
    EventType(states.EVENT,
              "An entity of the user's Home Assistant changed state (a sensor's value, a door opened, a device "
              "turned on or off, an appliance finished; numeric sensors at most once a minute).",
              {"entity_id": "the entity id; must be one of the user's real entities (known_values): never guess one",
               "name": "the entity's friendly name (\"Kitchen dishwasher\")",
               "domain": "the part of the entity id before the dot: sensor | binary_sensor | switch | light | lock | "
                         "cover | climate | media_player | person | ...",
               "old_state": "the state before, as text (numbers stay text, numeric ops compare them as numbers). "
                            "A threshold crossing (\"drops below 50\", \"goes above 30\") checks both: old_state "
                            "gte 50 AND new_state lt 50, so it fires once per crossing, not on every reading below",
               "new_state": "the state now, as text: a number for sensors (\"49\"), on | off for binary_sensor / "
                            "switch / light (a door or window open = on), locked | unlocked, open | closed, home | "
                            "not_home for a person, an appliance's own words for its program state",
               "unit": "the unit of a numeric state (\"%\", \"°C\", \"W\"), else empty",
               "device_class": "what the entity measures or is (battery, door, window, motion, temperature, power, "
                               "...), else empty"},
              history=states.history, values=known_values),
)
