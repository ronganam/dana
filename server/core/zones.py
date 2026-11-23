"""Zones: each user's own places (docs/zones.md).

A zone is a name, a centre (latitude, longitude) and a radius in metres, owned
by one user (table ``zones``, core migration ``m0006_zones_wizard``). The zone
with ``is_home`` is the user's Home (at most one). Names are unique per user,
in any case; location reminders (the tasks addon) name a zone, and the
reminder fires when that user arrives at or leaves it (the ``location``
signal: the phone's geofences, core/signals.py).

Every change (create, update, delete, set Home, an import that added any) is
emitted as ``core.zones.changed`` for the user (no data); the phone addon
sends their phone the ``zones_changed`` event so it registers its geofences
again.

Everything here is per user: another user's zone answers like a missing one.
"""

import datetime
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from core import events
from core.db import get_db_connection
from core.users import User

NAME_MAX = 60
RADIUS_MIN = 10
RADIUS_MAX = 50_000
DEFAULT_RADIUS = 100
HOME = "Home"


class Invalid(ValueError):
    """A zone field that is not valid (``field``: which one)."""

    def __init__(self, field: str, detail: str) -> None:
        super().__init__(detail)
        self.field = field
        self.detail = detail


@dataclass(frozen=True)
class Zone:
    id: str
    user_id: str
    name: str
    latitude: float
    longitude: float
    radius_m: int
    is_home: bool
    created_at: str

    @property
    def entity_id(self) -> str:
        """A Home Assistant style id (``zone.home`` for Home, ``zone.<name>`` otherwise)."""
        if self.is_home:
            return "zone.home"
        return "zone." + (re.sub(r"[^0-9a-z]+", "_", self.name.lower()).strip("_") or self.id)

    def to_dict(self) -> Dict[str, Any]:
        """What ``GET /api/zones`` lists: ``name`` and ``entity_id`` first (what the
        reminder editors read), then the zone's own fields."""
        data = asdict(self)
        data.pop("user_id")
        return {"name": self.name, "entity_id": self.entity_id, **data}


def _row(r: sqlite3.Row) -> Zone:
    return Zone(id=r["id"], user_id=r["user_id"], name=r["name"], latitude=float(r["latitude"]),
                longitude=float(r["longitude"]), radius_m=int(r["radius_m"]), is_home=bool(r["is_home"]),
                created_at=r["created_at"])


# --- Reads ---------------------------------------------------------------------------------

def list_zones(user: User) -> List[Zone]:
    """``user``'s zones, Home first, then by name."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM zones WHERE user_id = ? ORDER BY is_home DESC, name COLLATE NOCASE",
                            (user.id,)).fetchall()
    return [_row(r) for r in rows]


def get(user: User, zone_id: str) -> Optional[Zone]:
    """``user``'s zone ``zone_id`` (None for an unknown one or someone else's)."""
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM zones WHERE id = ? AND user_id = ?", (zone_id, user.id)).fetchone()
    return _row(r) if r else None


def find(user: User, name: str) -> Optional[Zone]:
    """``user``'s zone called ``name`` (any case, outer spaces ignored)."""
    wanted = (name or "").strip()
    if not wanted:
        return None
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM zones WHERE user_id = ? AND name = ? COLLATE NOCASE",
                         (user.id, wanted)).fetchone()
    return _row(r) if r else None


def home(user: Optional[User]) -> Optional[Zone]:
    """``user``'s Home zone (None without a user or without one)."""
    if user is None:
        return None
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM zones WHERE user_id = ? AND is_home = 1", (user.id,)).fetchone()
    return _row(r) if r else None


def home_name(user: Optional[User]) -> str:
    """The name of ``user``'s Home zone in lower case ("home" without one)."""
    z = home(user)
    return z.name.lower() if z else "home"


# --- Checks --------------------------------------------------------------------------------

def _name(value: Any) -> str:
    name = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    if not name:
        raise Invalid("name", "A zone needs a name.")
    if len(name) > NAME_MAX:
        raise Invalid("name", f"At most {NAME_MAX} characters.")
    return name


def _number(field: str, value: Any, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise Invalid(field, "A number is expected.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise Invalid(field, "A number is expected.") from None
    if not (low <= number <= high):
        raise Invalid(field, f"Between {low:g} and {high:g}.")
    return number


def _radius(value: Any) -> int:
    return int(round(_number("radius_m", value, RADIUS_MIN, RADIUS_MAX)))


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# --- Writes --------------------------------------------------------------------------------

def _taken(conn: sqlite3.Connection, user: User, name: str, other_than: str = "") -> bool:
    return conn.execute("SELECT 1 FROM zones WHERE user_id = ? AND name = ? COLLATE NOCASE AND id != ?",
                        (user.id, name, other_than)).fetchone() is not None


def _changed(user: User) -> None:
    events.emit("core.zones.changed", user)


def create(user: User, name: Any, latitude: Any, longitude: Any, radius_m: Any = DEFAULT_RADIUS,
           is_home: bool = False) -> Zone:
    """Add a zone of ``user``; ``is_home`` makes it their Home (the old Home stays a plain zone).

    Raises:
        Invalid: a bad field, or a name they already use (``field: name``).
    """
    zone = _insert(user, name, latitude, longitude, radius_m, is_home)
    _changed(user)
    return zone


def _insert(user: User, name: Any, latitude: Any, longitude: Any, radius_m: Any, is_home: bool) -> Zone:
    zone = Zone(id=uuid.uuid4().hex[:12], user_id=user.id, name=_name(name),
                latitude=_number("latitude", latitude, -90, 90), longitude=_number("longitude", longitude, -180, 180),
                radius_m=_radius(radius_m), is_home=bool(is_home), created_at=_now())
    with get_db_connection() as conn:
        if _taken(conn, user, zone.name):
            raise Invalid("name", f"You already have a zone called {zone.name}.")
        if zone.is_home:
            conn.execute("UPDATE zones SET is_home = 0 WHERE user_id = ?", (user.id,))
        conn.execute("INSERT INTO zones (id, user_id, name, latitude, longitude, radius_m, is_home, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (zone.id, user.id, zone.name, zone.latitude, zone.longitude, zone.radius_m, int(zone.is_home),
                      zone.created_at))
        conn.commit()
    return zone


def update(user: User, zone_id: str, changes: Mapping[str, Any]) -> Zone:
    """Change ``user``'s zone: any of ``name``, ``latitude``, ``longitude``,
    ``radius_m``, ``is_home`` (true makes it their Home; false leaves them without one).

    Raises:
        LookupError: no such zone of theirs.
        Invalid: a bad field or a name they already use.
    """
    zone = get(user, zone_id)
    if zone is None:
        raise LookupError("Zone not found")
    fields: Dict[str, Any] = {}
    if "name" in changes:
        fields["name"] = _name(changes["name"])
    if "latitude" in changes:
        fields["latitude"] = _number("latitude", changes["latitude"], -90, 90)
    if "longitude" in changes:
        fields["longitude"] = _number("longitude", changes["longitude"], -180, 180)
    if "radius_m" in changes:
        fields["radius_m"] = _radius(changes["radius_m"])
    if "is_home" in changes:
        fields["is_home"] = bool(changes["is_home"])
    with get_db_connection() as conn:
        if "name" in fields and _taken(conn, user, fields["name"], zone.id):
            raise Invalid("name", f"You already have a zone called {fields['name']}.")
        if fields.get("is_home"):
            conn.execute("UPDATE zones SET is_home = 0 WHERE user_id = ? AND id != ?", (user.id, zone.id))
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            values = [int(v) if isinstance(v, bool) else v for v in fields.values()]
            conn.execute(f"UPDATE zones SET {sets} WHERE id = ? AND user_id = ?", (*values, zone.id, user.id))
        conn.commit()
    updated = get(user, zone.id)
    assert updated is not None
    if fields:
        _changed(user)
    return updated


def delete(user: User, zone_id: str) -> bool:
    """Remove ``user``'s zone; False when they have none with that id."""
    with get_db_connection() as conn:
        cur = conn.execute("DELETE FROM zones WHERE id = ? AND user_id = ?", (zone_id, user.id))
        conn.commit()
    if cur.rowcount:
        _changed(user)
    return cur.rowcount > 0


def set_home(user: User, latitude: Any, longitude: Any, radius_m: Any = DEFAULT_RADIUS) -> Zone:
    """Put ``user``'s Home at a place: their Home zone moves there, or a zone
    called Home is made (and marked Home when they already have one by that name).

    Raises:
        Invalid: a bad coordinate or radius.
    """
    changes = {"latitude": latitude, "longitude": longitude, "radius_m": radius_m, "is_home": True}
    current = home(user) or find(user, HOME)
    if current is not None:
        return update(user, current.id, changes)
    return create(user, HOME, latitude, longitude, radius_m, is_home=True)


def import_zones(user: User, items: Iterable[Mapping[str, Any]]) -> Tuple[List[Zone], List[str]]:
    """Copy zones from elsewhere (Home Assistant) into ``user``'s: each item is
    ``{name, latitude, longitude, radius_m?, is_home?}``. A name they already
    have is skipped (their own zone stays as it is); an item marked home
    becomes their Home only while they have none. Returns (added, skipped names)."""
    added: List[Zone] = []
    skipped: List[str] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name or find(user, name) is not None:
            skipped.append(name)
            continue
        try:
            added.append(_insert(user, name, item.get("latitude"), item.get("longitude"),
                                 item.get("radius_m") or DEFAULT_RADIUS,
                                 is_home=bool(item.get("is_home")) and home(user) is None))
        except Invalid:
            skipped.append(name)
    if added:
        _changed(user)
    return added, skipped
