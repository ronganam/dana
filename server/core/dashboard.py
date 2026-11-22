"""Dashboards of tablets and TVs (docs/dashboard.md).

A tablet or TV shows a **dashboard**: widgets the addons contribute
(``@addon.widget``, core/addons/addon.py), in an order, each at a size with
its own settings. A dashboard is a **preset** (one per household role:
:data:`PRESETS`) plus the edits someone who may manage the device made
(added, removed, reordered, resized widgets, their settings), stored in
``dashboards`` (one row per device; ``widgets`` NULL = the preset's own).
A device that has none yet shows the preset of its role (no role: a tablet
the kitchen's, a TV the living room's).

**Whose data**: the device's owner's (``devices.user_id``: the user who
paired it, or for whom it was paired). That includes what others share with
them; a household device is simply a device with a role. Only the device
itself and its owner get the widgets' data; someone else who may manage the
device (``manage_users``) gets the layout to edit, the data withheld
(``private``).

**Who may read it** (:func:`readable`): the device itself, with its own
token, and nobody else's tablet or TV; any other credential of a user who
may manage the device. Any other id answers like a missing one.
"""

import asyncio
import copy
import inspect
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core import clock, devices, users
from core.addons import registry, state
from core.addons.addon import Field, all_addons
from core.addons.hooks import Hook, WidgetContext
from core.addons.store import NotConnected
from core.db import get_db_connection
from core.devices import Device
from core.users import User

logger = logging.getLogger(__name__)

# Widgets on one dashboard, the length of a setting value, a widget's data time.
MAX_WIDGETS = 24
MAX_VALUE = 300
WIDGET_TIMEOUT_SECONDS = 8.0
PLACED_ID = re.compile(r"^[a-z0-9_-]{1,40}$")


@dataclass(frozen=True)
class Preset:
    """A dashboard to start from: its ``key`` (a household role), its name and
    its widgets in order, each ``(widget key, size, settings)``."""
    key: str
    title: str
    widgets: Tuple[Tuple[str, str, Dict[str, str]], ...]


PRESETS: Dict[str, Preset] = {p.key: p for p in (
    Preset("kitchen", "Kitchen", (
        ("clock", "medium", {}), ("now", "medium", {}), ("shopping", "large", {}), ("calendar", "medium", {}),
        ("reminders", "medium", {}), ("ha_lights", "small", {}))),
    # The bedroom tablet: the camera with its events, the room's AC and the phone / watch batteries. Which
    # camera, climate entity and battery sensors are the user's own: settings, chosen in the editor.
    Preset("bedroom", "Bedroom", (
        ("clock", "medium", {}), ("cameras", "large", {"camera": ""}), ("reminders", "medium", {}),
        ("ha_climate", "medium", {"entity": ""}), ("ha_batteries", "small", {"sensors": ""}))),
    Preset("hall", "Hall", (
        ("clock", "medium", {}), ("now", "medium", {}), ("calendar", "medium", {}), ("reminders", "medium", {}),
        ("cameras", "medium", {"camera": ""}))),
    Preset("living_room", "Living room", (
        ("clock", "medium", {}), ("now", "medium", {}), ("calendar", "medium", {}), ("cameras", "medium", {"camera": ""}),
        ("ha_lights", "medium", {}), ("money", "medium", {}))),
)}
# A device without a role starts from this preset.
KIND_PRESETS = {"tablet": "kitchen", "tv": "living_room"}


class Invalid(ValueError):
    """A dashboard that cannot be saved: the API answers 400 ``invalid`` with this message."""


# --- Access ---------------------------------------------------------------------------------

def has_dashboard(device: Device) -> bool:
    """Only a tablet or a TV shows a dashboard."""
    return device.kind in devices.ROLE_KINDS


def readable(user: User, requester: Optional[Device], device_id: str) -> Optional[Device]:
    """The device whose dashboard ``user`` (with ``requester``, the device the
    request came from, or None) may read; None when it is missing, not a
    tablet / TV, or not theirs to read (all answered the same)."""
    device = devices.get(device_id) if device_id else None
    if device is None or not has_dashboard(device):
        return None
    if requester is not None and has_dashboard(requester):
        return device if requester.id == device.id else None
    return device if devices.may_manage(user, device) else None


def editable(user: User, requester: Optional[Device], device: Device) -> bool:
    """Whether the request may change ``device``'s dashboard: a user who may
    manage it, never from a tablet or TV's own token."""
    return (requester is None or not has_dashboard(requester)) and devices.may_manage(user, device)


def data_user(user: User, requester: Optional[Device], device: Device) -> Optional[User]:
    """Whose data the widgets show: the device's owner, when the request is the
    device itself or that owner; None (data withheld) otherwise."""
    if (requester is not None and requester.id == device.id) or user.id == device.user_id:
        return users.get(device.user_id)
    return None


# --- Widgets --------------------------------------------------------------------------------

def _declared(key: str) -> Optional[Hook]:
    """The widget ``key`` of an addon enabled on the server (whoever has it), or None."""
    off = state.disabled()
    return next((h for a in all_addons() if a.name not in off for h in a.hooks
                 if h.kind == "widget" and h.key == key), None)


def _fields(hook: Hook) -> Tuple[Field, ...]:
    return tuple(hook.option("settings", ()))


def _field_json(f: Field) -> Dict[str, Any]:
    return {"name": f.name, "label": f.title, "help": f.help, "placeholder": f.placeholder,
            "default": "" if f.default is None else str(f.default)}


def widget_info(hook: Hook) -> Dict[str, Any]:
    """A widget as the editor offers it."""
    return {"key": hook.key, "addon": hook.addon, "title": hook.option("title"),
            "description": hook.option("description", ""), "icon": hook.option("icon", ""),
            "sizes": list(hook.option("sizes")), "refresh": hook.option("refresh", 60),
            "settings": [_field_json(f) for f in _fields(hook)]}


def catalog(user: User) -> List[Dict[str, Any]]:
    """The widgets ``user`` can place: those of the addons active for them, in addon order."""
    return [widget_info(h) for h in registry.hooks("widget", user)]


def presets_payload() -> List[Dict[str, Any]]:
    """The presets, for the editor."""
    return [{"key": p.key, "title": p.title,
             "widgets": [{"widget": w, "size": size, "settings": dict(settings)} for w, size, settings in p.widgets]}
            for p in PRESETS.values()]


def effective_settings(hook: Optional[Hook], stored: Mapping[str, Any]) -> Dict[str, str]:
    """Every setting the widget declares: the stored value, else its default."""
    if hook is None:
        return {str(k): str(v) for k, v in stored.items()}
    out: Dict[str, str] = {}
    for f in _fields(hook):
        value = str(stored.get(f.name) or "").strip()
        out[f.name] = value or ("" if f.default is None else str(f.default))
    return out


# --- Layouts --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Layout:
    """A device's dashboard: its preset, whether it was edited, its widgets
    (``{id, widget, size, settings}`` each, settings as stored)."""
    preset: str
    edited: bool
    widgets: Tuple[Dict[str, Any], ...]


def default_preset(device: Device) -> str:
    """The preset a device starts from: its role's, else its kind's."""
    return device.role if device.role in PRESETS else KIND_PRESETS.get(device.kind, "kitchen")


def _with_ids(widgets: Sequence[Mapping[str, Any]]) -> Tuple[Dict[str, Any], ...]:
    """The widgets with an ``id`` each (kept when given and unused, else ``<widget>-<n>``)."""
    used: set = set()
    out = []
    for w in widgets:
        wid = str(w.get("id") or "")
        if not PLACED_ID.match(wid) or wid in used:
            n = 1
            while f"{w['widget']}-{n}" in used:
                n += 1
            wid = f"{w['widget']}-{n}"
        used.add(wid)
        out.append({"id": wid, "widget": w["widget"], "size": w["size"], "settings": dict(w.get("settings") or {})})
    return tuple(out)


def preset_widgets(key: str) -> Tuple[Dict[str, Any], ...]:
    """The widgets of preset ``key``, with ids."""
    return _with_ids([{"widget": w, "size": size, "settings": dict(settings)}
                      for w, size, settings in PRESETS[key].widgets])


def layout(device: Device) -> Layout:
    """``device``'s dashboard: its stored row, else its default preset."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT preset, widgets FROM dashboards WHERE device_id = ? AND user_id = ?",
                           (device.id, device.user_id)).fetchone()
    if row is None or row["preset"] not in PRESETS:
        return Layout(default_preset(device), False, preset_widgets(default_preset(device)))
    if row["widgets"] is None:
        return Layout(row["preset"], False, preset_widgets(row["preset"]))
    try:
        stored = json.loads(row["widgets"])
    except ValueError:
        stored = None
    if not isinstance(stored, list):
        logger.warning(f"Dashboard of device {device.id}: unreadable widgets; showing its preset")
        return Layout(row["preset"], False, preset_widgets(row["preset"]))
    return Layout(row["preset"], True, _with_ids([w for w in stored if isinstance(w, dict) and w.get("widget")]))


def clean_widgets(widgets: Any) -> List[Dict[str, Any]]:
    """The widgets a client sent, checked: a list of at most :data:`MAX_WIDGETS`
    ``{id?, widget, size?, settings?}``, each a widget an enabled addon declares,
    a size it has (default: its first), only settings it declares, text values.

    Raises:
        Invalid: what is wrong, in words.
    """
    if not isinstance(widgets, list):
        raise Invalid("widgets must be a list")
    if len(widgets) > MAX_WIDGETS:
        raise Invalid(f"a dashboard has at most {MAX_WIDGETS} widgets")
    out = []
    for i, w in enumerate(widgets):
        if not isinstance(w, dict):
            raise Invalid(f"widget {i + 1} must be an object")
        key = str(w.get("widget") or "")
        hook = _declared(key)
        if hook is None:
            raise Invalid(f"there is no widget {key!r}")
        sizes = list(hook.option("sizes"))
        size = str(w.get("size") or sizes[0])
        if size not in sizes:
            raise Invalid(f"the {hook.option('title')} widget comes in {', '.join(sizes)}")
        settings = w.get("settings") or {}
        if not isinstance(settings, dict):
            raise Invalid(f"the settings of widget {i + 1} must be an object")
        names = {f.name for f in _fields(hook)}
        unknown = sorted(set(settings) - names)
        if unknown:
            raise Invalid(f"the {hook.option('title')} widget has no setting {', '.join(unknown)}")
        clean: Dict[str, str] = {}
        for name, value in settings.items():
            if value is None:
                continue
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise Invalid(f"the {name} setting is text")
            text = str(value).strip()
            if len(text) > MAX_VALUE:
                raise Invalid(f"the {name} setting is at most {MAX_VALUE} characters")
            if text:
                clean[name] = text
        item: Dict[str, Any] = {"widget": key, "size": size, "settings": clean}
        if w.get("id") is not None:
            item["id"] = str(w["id"])
        out.append(item)
    return list(_with_ids(out))


def save(device: Device, by: User, preset: Any, widgets: Any) -> Layout:
    """Store ``device``'s dashboard: ``preset`` and its edited ``widgets``
    (None: the preset's own, so later changes to the preset reach it).

    Raises:
        Invalid: an unknown preset or a widget that cannot be saved (:func:`clean_widgets`).
    """
    if not isinstance(preset, str) or preset not in PRESETS:
        raise Invalid(f"preset must be one of {', '.join(PRESETS)}")
    stored = None if widgets is None else json.dumps(clean_widgets(widgets), ensure_ascii=False)
    with get_db_connection() as conn:
        conn.execute("INSERT INTO dashboards (device_id, user_id, preset, widgets, updated_by, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET preset = excluded.preset, "
                     "widgets = excluded.widgets, updated_by = excluded.updated_by, updated_at = excluded.updated_at "
                     "WHERE dashboards.user_id = excluded.user_id",
                     (device.id, device.user_id, preset, stored, by.id, clock.now().isoformat(timespec="seconds")))
        conn.commit()
    logger.info(f"Dashboard of device {device.id} saved by user {by.id}: preset {preset}"
                f"{', edited' if stored is not None else ''}")
    return layout(device)


# --- Data -----------------------------------------------------------------------------------

async def _call(hook: Hook, user: User, ctx: WidgetContext) -> Any:
    with users.acting_as(user):
        if inspect.iscoroutinefunction(hook.func):
            return await hook.func(user, ctx)
        return await asyncio.to_thread(hook.func, user, ctx)


async def widget_data(user: Optional[User], placed: Mapping[str, Any]) -> Dict[str, Any]:
    """``{data}`` of one placed widget for ``user`` (None: withheld, ``error``
    ``private``), or ``{data: None, error}``: ``not_connected`` (with ``addon``:
    an addon ``user`` does not have), ``unavailable`` (no enabled addon declares
    it), ``failed`` (it raised or took longer than :data:`WIDGET_TIMEOUT_SECONDS`)."""
    hook = _declared(placed["widget"])
    if hook is None:
        return {"data": None, "error": "unavailable"}
    if user is None:
        return {"data": None, "error": "private"}
    if hook.addon not in registry.active_addons(user):
        return {"data": None, "error": "not_connected", "addon": hook.addon}
    ctx = WidgetContext(settings=effective_settings(hook, placed.get("settings") or {}),
                        size=str(placed.get("size") or ""), now=clock.now())
    try:
        data = await asyncio.wait_for(_call(hook, user, ctx), WIDGET_TIMEOUT_SECONDS)
    except NotConnected as e:
        return {"data": None, "error": "not_connected", "addon": e.addon}
    except Exception as e:
        logger.warning(f"Widget {placed['widget']} failed for user {user.id}: {e!r}")
        return {"data": None, "error": "failed"}
    return {"data": copy.deepcopy(data)}


def require_widget(user: User, key: str) -> Hook:
    """The widget ``key`` when ``user`` has its addon.

    Raises:
        NotConnected: an enabled addon declares it, but ``user`` does not have that addon.
        LookupError: no enabled addon declares it.
    """
    hook = registry.hook_for("widget", key, user)
    if hook is None:
        raise LookupError(key)
    return hook


def _placed_json(placed: Mapping[str, Any], release: str) -> Dict[str, Any]:
    hook = _declared(placed["widget"])
    out = {"id": placed["id"], "widget": placed["widget"], "size": placed["size"],
           "settings": effective_settings(hook, placed.get("settings") or {}),
           "addon": hook.addon if hook else None, "title": hook.option("title") if hook else placed["widget"],
           "script": None, "refresh": hook.option("refresh", 60) if hook else 0}
    if hook is not None:
        out["script"] = f"/addons/{hook.addon}/web/{hook.option('script')}?v={release}"
    return out


async def payload(device: Device, user: Optional[User], release: str) -> Dict[str, Any]:
    """The dashboard of ``device`` as the renderer reads it, every widget's data
    for ``user`` (None: withheld)."""
    current = layout(device)
    placed = [_placed_json(w, release) for w in current.widgets]
    datas = await asyncio.gather(*(widget_data(user, w) for w in current.widgets))
    for item, data in zip(placed, datas):
        item.update(data)
    return {"device": {"id": device.id, "name": device.name, "kind": device.kind, "role": device.role,
                       "room": device.room},
            "preset": current.preset, "edited": current.edited, "release": release, "widgets": placed,
            "time": clock.now().isoformat(timespec="seconds")}


def layout_payload(device: Device, release: str) -> Dict[str, Any]:
    """The dashboard of ``device`` without data (what a save answers)."""
    current = layout(device)
    return {"device": {"id": device.id, "name": device.name, "kind": device.kind, "role": device.role,
                       "room": device.room},
            "preset": current.preset, "edited": current.edited, "release": release,
            "widgets": [_placed_json(w, release) for w in current.widgets]}


def placed(device: Device, widget_id: str) -> Optional[Dict[str, Any]]:
    """The placed widget ``widget_id`` of ``device``'s dashboard, or None."""
    return next((w for w in layout(device).widgets if w["id"] == widget_id), None)

