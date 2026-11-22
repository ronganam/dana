"""The Home Assistant dashboard widgets (docs/dashboard.md): a climate
device, lights, chosen entities and battery sensors, read from the user's
own Home Assistant (one shared ``/api/states`` read per connection). The
widgets' controls call ``POST /api/ha/service`` (addons/homeassistant/api.py).
Drawn by ``web/widget.js``."""

from typing import Any, Dict, List, Optional

from core.addons import Field, WidgetContext, store
from core.users import User
from addons.homeassistant import client
from addons.homeassistant.addon import addon
from addons.homeassistant.config import ADDON, BATTERY_SENSOR, setting

# Lights listed when none are chosen, and entities a widget lists at most.
MAX_ENTITIES = 12
# Domains whose entities the Entities widget can switch on and off.
TOGGLES = ("light", "switch", "fan", "input_boolean", "automation")


def _ids(text: str) -> List[str]:
    """Entity ids from a setting (separated by commas or spaces), each once, at most :data:`MAX_ENTITIES`."""
    seen: List[str] = []
    for part in (text or "").replace(",", " ").split():
        if "." in part and part not in seen:
            seen.append(part)
    return seen[:MAX_ENTITIES]


def _name(s: Dict[str, Any]) -> str:
    return str((s.get("attributes") or {}).get("friendly_name") or s.get("entity_id") or "")


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _states(user: User) -> Dict[str, Dict[str, Any]]:
    conn = store.require(user, ADDON)
    return {s["entity_id"]: s for s in await client.cached_states(conn) if s.get("entity_id")}


def _entities_field(label: str, help_text: str, placeholder: str) -> Field:
    return Field("entities", label, default="", required=False, label=label, help=help_text, placeholder=placeholder)


@addon.widget("ha_climate", title="Climate", script="widget.js", icon="thermometer", refresh=30,
              sizes=("medium", "large"),
              settings=(Field("entity", "The climate device (an AC or a thermostat)", default="", required=False,
                              label="Climate device", help="Its entity id; empty: the first climate device.",
                              placeholder="climate.bedroom"),),
              description="An AC or thermostat: the temperature, the target and its modes, with controls")
async def climate_widget(user: User, ctx: WidgetContext) -> Dict[str, Any]:
    """The climate device's state (``entity_id``, ``name``, ``mode``, ``modes``,
    ``fan_mode``, ``fan_modes``, ``current``, ``target``, ``humidity``, ``min``,
    ``max``, ``step``, ``unit``), or ``entity_id`` None when there is none."""
    states = await _states(user)
    wanted = (ctx.settings.get("entity") or "").strip()
    s = states.get(wanted) if wanted else next((v for k, v in sorted(states.items()) if k.startswith("climate.")),
                                               None)
    if s is None or not str(s.get("entity_id", "")).startswith("climate."):
        return {"entity_id": None, "wanted": wanted or None}
    a = s.get("attributes") or {}
    return {"entity_id": s["entity_id"], "name": _name(s), "mode": s.get("state"),
            "modes": list(a.get("hvac_modes") or []), "fan_mode": a.get("fan_mode"),
            "fan_modes": list(a.get("fan_modes") or []), "current": _number(a.get("current_temperature")),
            "target": _number(a.get("temperature")), "humidity": _number(a.get("current_humidity")),
            "min": _number(a.get("min_temp")), "max": _number(a.get("max_temp")),
            "step": _number(a.get("target_temp_step")) or 0.5, "unit": a.get("temperature_unit") or "°"}


@addon.widget("ha_lights", title="Lights", script="widget.js", icon="lightbulb", refresh=30,
              sizes=("medium", "small", "large"),
              settings=(_entities_field("Lights", "Their entity ids, separated by commas; empty: every light.",
                                        "light.kitchen, light.hall"),),
              description="Lights, on or off, with a tap to switch each")
async def lights_widget(user: User, ctx: WidgetContext) -> Dict[str, Any]:
    """``lights``: ``{entity_id, name, on, brightness}`` (brightness 0-100 or None)."""
    states = await _states(user)
    chosen = _ids(ctx.settings.get("entities") or "")
    ids = chosen or sorted(k for k in states if k.startswith("light."))[:MAX_ENTITIES]
    out = []
    for eid in ids:
        s = states.get(eid)
        if s is None:
            continue
        b = _number((s.get("attributes") or {}).get("brightness"))
        out.append({"entity_id": eid, "name": _name(s), "on": s.get("state") == "on",
                    "brightness": round(b * 100 / 255) if b is not None else None})
    return {"lights": out}


@addon.widget("ha_entities", title="Home Assistant", script="widget.js", icon="house", refresh=30,
              sizes=("small", "medium", "large"),
              settings=(_entities_field("Entities", "Their entity ids, separated by commas.",
                                        "sensor.outside_temperature, switch.boiler"),),
              description="The entities you choose: their state, with a tap to switch the ones that switch")
async def entities_widget(user: User, ctx: WidgetContext) -> Dict[str, Any]:
    """``entities``: ``{entity_id, name, state, unit, toggles}`` of the chosen ones (missing ones left out)."""
    states = await _states(user)
    out = []
    for eid in _ids(ctx.settings.get("entities") or ""):
        s = states.get(eid)
        if s is None:
            continue
        out.append({"entity_id": eid, "name": _name(s), "state": s.get("state"),
                    "unit": (s.get("attributes") or {}).get("unit_of_measurement") or "",
                    "toggles": eid.split(".", 1)[0] in TOGGLES})
    return {"entities": out}


@addon.widget("ha_batteries", title="Batteries", script="widget.js", icon="battery-medium", refresh=60,
              sizes=("small", "medium"),
              settings=(Field("sensors", "Battery level sensors", default="", required=False, label="Battery sensors",
                              help="Their entity ids, separated by commas; empty: your phone's battery sensor "
                                   "(Home Assistant settings).",
                              placeholder="sensor.phone_battery_level, sensor.watch_battery_level"),),
              description="Battery levels of your phone, watch or other devices")
async def batteries_widget(user: User, ctx: WidgetContext) -> Dict[str, Any]:
    """``batteries``: ``{entity_id, name, level}`` (level 0-100 or None when unknown)."""
    conn = store.require(user, ADDON)
    ids = _ids(ctx.settings.get("sensors") or "") or [x for x in (setting(conn, BATTERY_SENSOR),) if x]
    states = await _states(user) if ids else {}
    out = []
    for eid in ids:
        s = states.get(eid)
        level = _number(s.get("state")) if s else None
        out.append({"entity_id": eid, "name": _name(s) if s else eid,
                    "level": max(0, min(100, round(level))) if level is not None else None})
    return {"batteries": out}
