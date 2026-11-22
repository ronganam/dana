"""Devices and pairing (docs/devices.md).

A **device** is one app instance of a user: a phone, a watch, a tablet, a TV,
a browser or another client. Each has its own API token (a row of
``user_tokens``, core/users.py), so it can be revoked alone; requests made
with it carry the device (``request["device"]``, core/auth.py) and refresh its
``last_seen_at``. A household tablet or TV has a ``role`` (the room it
serves); a personal device has none.

A device is made by **pairing**: a signed-in user (or an admin with
``manage_users``, for someone else) starts a pairing and gets a short code,
valid :data:`PAIRING_TTL_SECONDS`, used once and stored hashed; the device
sends the code to ``POST /api/pair`` and gets its token back
(:func:`redeem`). Server code starts one with :func:`start_pairing`.

Lookups by token run on every request, so they read an in-memory copy of the
table (refreshed after every change made here, and at most every
:data:`CACHE_TTL_SECONDS` for changes made by another process).
"""

import datetime
import hashlib
import hmac
import logging
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from core import users
from core.db import get_db_connection
from core.users import User

logger = logging.getLogger(__name__)

KINDS = ("phone", "watch", "tablet", "tv", "browser", "other")
# A household device's role: the room it serves (only a tablet or a TV has one).
ROLES = ("kitchen", "bedroom", "hall", "living_room")
ROLE_KINDS = ("tablet", "tv")
KIND_NAMES = {"phone": "Phone", "watch": "Watch", "tablet": "Tablet", "tv": "TV", "browser": "Browser",
              "other": "Device"}

PAIRING_TTL_SECONDS = 300
CODE_LENGTH = 8
# No 0 / O, 1 / I: a code read off a screen is typed without guessing.
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
# A user's pairings waiting at the same time (each new one beyond it is refused).
MAX_OPEN_PAIRINGS = 10
NAME_MAX = 60
ROOM_MAX = 40

CACHE_TTL_SECONDS = 10.0
# last_seen_at is written at most this often per device.
TOUCH_INTERVAL_SECONDS = 300.0


@dataclass(frozen=True)
class Device:
    """One app instance of a user (never its token)."""
    id: str
    user_id: str
    name: str
    kind: str
    room: str
    role: str
    token_id: Optional[str]
    created_at: str
    last_seen_at: Optional[str]

    @property
    def paired(self) -> bool:
        """It has its own token (a device moved from the phone's push
        registrations before pairing existed has none)."""
        return self.token_id is not None

    def to_dict(self) -> Dict[str, Any]:
        """The device as the API gives it."""
        return {"id": self.id, "user_id": self.user_id, "name": self.name, "kind": self.kind, "room": self.room,
                "role": self.role, "paired": self.paired, "created_at": self.created_at,
                "last_seen_at": self.last_seen_at}


@dataclass(frozen=True)
class Pairing:
    """A started pairing: ``code`` is the raw code, given only when it is made."""
    id: str
    code: str
    user_id: str
    created_by: str
    kind: str
    name: str
    room: str
    role: str
    created_at: str
    expires_at: str


class PairingError(Exception):
    """A pairing code could not be used. ``error``: ``invalid_code`` (unknown,
    expired or used: never says which) or ``wrong_kind`` (the code was made
    for another kind of device; it stays usable)."""

    def __init__(self, error: str, message: str, expected: str = "") -> None:
        super().__init__(message)
        self.error = error
        self.expected = expected


# --- Values --------------------------------------------------------------------------

def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_code(code: str) -> str:
    """A code as typed (any case, with spaces or dashes) in its stored form."""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def looks_like_code(text: str) -> bool:
    """Whether ``text`` has the shape of a pairing code (not a token)."""
    code = normalize_code(text)
    return len(code) == CODE_LENGTH and all(ch in CODE_ALPHABET for ch in code) and len(text.strip()) <= CODE_LENGTH + 2


def _new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def check_fields(kind: str, room: str = "", role: str = "") -> Tuple[str, str, str]:
    """``(kind, room, role)`` cleaned: a known kind, a room of at most
    :data:`ROOM_MAX` characters, a role only on a tablet or a TV.

    Raises:
        ValueError: ``args[0]`` names the field (``kind``, ``room``, ``role``), ``args[1]`` says why.
    """
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise ValueError("kind", f"kind must be one of {', '.join(KINDS)}")
    room = " ".join((room or "").split())
    if len(room) > ROOM_MAX:
        raise ValueError("room", f"room is at most {ROOM_MAX} characters")
    role = (role or "").strip().lower()
    if role and role not in ROLES:
        raise ValueError("role", f"role must be one of {', '.join(ROLES)} (or empty)")
    if role and kind not in ROLE_KINDS:
        raise ValueError("role", "only a tablet or a TV has a household role")
    return kind, room, role


def clean_name(name: str, kind: str = "") -> str:
    """A device name on one line, at most :data:`NAME_MAX` characters (empty:
    the kind's name)."""
    name = " ".join((name or "").split())[:NAME_MAX]
    return name or KIND_NAMES.get(kind, "Device")


def browser_name(user_agent: str) -> str:
    """A browser device's name from its ``User-Agent`` ("Firefox on Linux"; "Browser" when unknown)."""
    ua = user_agent or ""
    browser = next((name for key, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                                           ("Chrome/", "Chrome"), ("Safari/", "Safari")) if key in ua), "")
    system = next((name for key, name in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                                          ("Mac OS X", "macOS"), ("Windows", "Windows"), ("CrOS", "ChromeOS"),
                                          ("Linux", "Linux")) if key in ua), "")
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or "Browser"


def _device(row: sqlite3.Row) -> Device:
    return Device(id=row["id"], user_id=row["user_id"], name=row["name"], kind=row["kind"], room=row["room"],
                  role=row["role"], token_id=row["token_id"], created_at=row["created_at"],
                  last_seen_at=row["last_seen_at"])


# --- Cache -----------------------------------------------------------------------------

_by_token: Optional[Dict[str, Device]] = None
_loaded_at = 0.0
_touched: Dict[str, float] = {}


def invalidate() -> None:
    """Drop the in-memory copy; the next lookup reads the table again."""
    global _by_token
    _by_token = None


def by_token_id(token_id: Optional[str]) -> Optional[Device]:
    """The device whose own token is ``token_id`` (None: a token of no device)."""
    global _by_token, _loaded_at
    if not token_id:
        return None
    if _by_token is None or time.monotonic() - _loaded_at > CACHE_TTL_SECONDS:
        with get_db_connection() as conn:
            # scoped: every paired device, for token lookups (the token decides the user)
            rows = conn.execute("SELECT * FROM devices WHERE token_id IS NOT NULL").fetchall()
        _by_token, _loaded_at = {r["token_id"]: _device(r) for r in rows}, time.monotonic()
    return _by_token.get(token_id)


def touch(device: Device) -> None:
    """Record that ``device`` made a request (at most once per
    :data:`TOUCH_INTERVAL_SECONDS`). Never raises."""
    now = time.monotonic()
    if now - _touched.get(device.id, -TOUCH_INTERVAL_SECONDS) < TOUCH_INTERVAL_SECONDS:
        return
    _touched[device.id] = now
    try:
        with get_db_connection() as conn:
            conn.execute("UPDATE devices SET last_seen_at = ? WHERE id = ? AND user_id = ?",
                         (_iso(_now()), device.id, device.user_id))
            conn.commit()
    except Exception as e:
        logger.warning(f"Could not record device use: {e}")


# --- Devices ---------------------------------------------------------------------------

def get(device_id: str) -> Optional[Device]:
    """A device by id, whoever's (callers check whose it is)."""
    with get_db_connection() as conn:
        # scoped: by id; every caller checks the owner (may_manage)
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    return _device(row) if row else None


def list_for(user_id: str) -> List[Device]:
    """``user_id``'s devices, most recently seen first."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM devices WHERE user_id = ? "
                            "ORDER BY COALESCE(last_seen_at, created_at) DESC, created_at DESC",
                            (user_id,)).fetchall()
    return [_device(r) for r in rows]


def list_all() -> List[Device]:
    """Every user's devices (for a user with ``manage_users``)."""
    with get_db_connection() as conn:
        # scoped: everyone's devices, only for manage_users (core/devices_api.py)
        rows = conn.execute("SELECT * FROM devices ORDER BY user_id, COALESCE(last_seen_at, created_at) DESC").fetchall()
    return [_device(r) for r in rows]


def may_manage(by: User, device: Device) -> bool:
    """Whether ``by`` may see, change or revoke ``device``: their own, or anyone's with ``manage_users``."""
    return device.user_id == by.id or by.level.allows("manage_users")


def create(user: User, kind: str, name: str = "", room: str = "", role: str = "") -> Tuple[str, Device]:
    """A new device of ``user`` with its own token. Returns ``(token, device)``:
    the raw token is given once and never stored.

    Raises:
        ValueError: a field is not valid (:func:`check_fields`).
    """
    kind, room, role = check_fields(kind, room, role)
    with get_db_connection() as conn:
        token, device = _insert(conn, user.id, kind, clean_name(name, kind), room, role)
        conn.commit()
    _changed()
    return token, device


def _insert(conn: sqlite3.Connection, user_id: str, kind: str, name: str, room: str, role: str) -> Tuple[str, Device]:
    token, stored = users.insert_token(conn, user_id, name)
    now = _iso(_now())
    device = Device(id=uuid.uuid4().hex[:12], user_id=user_id, name=name, kind=kind, room=room, role=role,
                    token_id=stored.id, created_at=now, last_seen_at=now)
    conn.execute("INSERT INTO devices (id, user_id, name, kind, room, role, token_id, created_at, last_seen_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (device.id, user_id, name, kind, room, role, stored.id, now, now))
    return token, device


def update(device: Device, *, name: Optional[str] = None, room: Optional[str] = None,
           role: Optional[str] = None) -> Device:
    """Rename a device or change its room / role (left out: unchanged).

    Raises:
        ValueError: a field is not valid (:func:`check_fields`).
    """
    _, new_room, new_role = check_fields(device.kind, device.room if room is None else room,
                                         device.role if role is None else role)
    new_name = device.name if name is None else clean_name(name, device.kind)
    with get_db_connection() as conn:
        conn.execute("UPDATE devices SET name = ?, room = ?, role = ? WHERE id = ? AND user_id = ?",
                     (new_name, new_room, new_role, device.id, device.user_id))
        if device.token_id:
            conn.execute("UPDATE user_tokens SET label = ? WHERE id = ? AND user_id = ?",
                         (new_name, device.token_id, device.user_id))
        conn.commit()
    _changed()
    updated = get(device.id)
    assert updated is not None
    return updated


def remove(device: Device) -> None:
    """Delete a device and its token: the next request made with it is refused."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM devices WHERE id = ? AND user_id = ?", (device.id, device.user_id))
        if device.token_id:
            conn.execute("DELETE FROM user_tokens WHERE id = ? AND user_id = ?", (device.token_id, device.user_id))
        conn.commit()
    _touched.pop(device.id, None)
    _changed()
    logger.info(f"Device {device.id} ({device.kind}) of user {device.user_id} removed")


def _changed() -> None:
    invalidate()
    users.invalidate()


# --- Pairing ---------------------------------------------------------------------------

def start_pairing(user: User, kind: str, *, by: User, name: str = "", room: str = "", role: str = "") -> Pairing:
    """Start pairing a new device of ``user`` (started by ``by``: the user
    themselves, or someone with ``manage_users``). The code is valid
    :data:`PAIRING_TTL_SECONDS` and works once.

    Raises:
        PermissionError: ``by`` is someone else without ``manage_users``.
        ValueError: a field is not valid (:func:`check_fields`), or ``by``
            already has :data:`MAX_OPEN_PAIRINGS` waiting.
    """
    if by.id != user.id and not by.level.allows("manage_users"):
        raise PermissionError("manage_users")
    kind, room, role = check_fields(kind, room, role)
    name = " ".join((name or "").split())[:NAME_MAX]
    now = _now()
    expires = now + datetime.timedelta(seconds=PAIRING_TTL_SECONDS)
    with get_db_connection() as conn:
        # Spent and expired pairings are kept only while a device may still be polling for them.
        # scoped: created_by, the user who started them
        conn.execute("DELETE FROM device_pairings WHERE created_by = ? AND expires_at < ?",
                     (by.id, _iso(now - datetime.timedelta(hours=1))))
        # scoped: created_by, the user who started them
        open_count = conn.execute("SELECT COUNT(*) FROM device_pairings WHERE created_by = ? AND used_at IS NULL "
                                  "AND expires_at > ?", (by.id, _iso(now))).fetchone()[0]
        if open_count >= MAX_OPEN_PAIRINGS:
            raise ValueError("pairing", f"at most {MAX_OPEN_PAIRINGS} pairings can wait at once")
        while True:
            code = _new_code()
            try:
                pairing = Pairing(id=uuid.uuid4().hex[:12], code=code, user_id=user.id, created_by=by.id, kind=kind,
                                  name=name, room=room, role=role, created_at=_iso(now), expires_at=_iso(expires))
                conn.execute("INSERT INTO device_pairings (id, code_hash, user_id, created_by, kind, name, room, role, "
                             "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (pairing.id, _hash(code), user.id, by.id, kind, name, room, role, pairing.created_at,
                              pairing.expires_at))
                break
            except sqlite3.IntegrityError:
                continue  # the same code is waiting already (one in a trillion): draw again
        conn.commit()
    logger.info(f"Pairing {pairing.id} started for a {kind} of user {user.id} by {by.id}")
    return pairing


def pairing_status(pairing_id: str, by: User) -> Optional[Dict[str, Any]]:
    """``{id, status, expires_at, device?}`` of a pairing ``by`` started:
    ``pending``, ``paired`` (with the new device) or ``expired`` (also when
    cancelled). None: no such pairing of theirs."""
    with get_db_connection() as conn:
        # scoped: created_by, the user who started it
        row = conn.execute("SELECT p.id, p.expires_at, p.used_at, p.device_id FROM device_pairings p "
                           "WHERE p.id = ? AND p.created_by = ?", (pairing_id, by.id)).fetchone()
    if row is None:
        return None
    out: Dict[str, Any] = {"id": row["id"], "expires_at": row["expires_at"]}
    if row["used_at"] and row["device_id"]:
        device = get(row["device_id"])
        out["status"] = "paired" if device else "expired"
        if device:
            out["device"] = device.to_dict()
    elif row["used_at"] or row["expires_at"] <= _iso(_now()):
        out["status"] = "expired"
    else:
        out["status"] = "pending"
    return out


def cancel_pairing(pairing_id: str, by: User) -> bool:
    """End a waiting pairing ``by`` started (its code stops working). False: none waiting."""
    with get_db_connection() as conn:
        # scoped: created_by, the user who started it
        cur = conn.execute("UPDATE device_pairings SET used_at = ? WHERE id = ? AND created_by = ? AND used_at IS NULL",
                           (_iso(_now()), pairing_id, by.id))
        conn.commit()
    return cur.rowcount > 0


def redeem(code: str, *, kind: str, name: str = "") -> Tuple[str, Device, User]:
    """Use a pairing code: the device it makes (the pairing's kind, room and
    role; its name, else ``name``), its token, and whose it is. The code is
    spent in the same transaction, so it works once.

    Raises:
        PairingError: ``invalid_code`` or ``wrong_kind`` (see the class).
    """
    wanted = normalize_code(code)
    kind = (kind or "").strip().lower()
    invalid = PairingError("invalid_code", "That code is not valid. Start a new pairing and try again.")
    if len(wanted) != CODE_LENGTH:
        raise invalid
    digest = _hash(wanted)
    now = _iso(_now())
    with get_db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # scoped: the code is the credential; it names the user
            row = conn.execute("SELECT * FROM device_pairings WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?",
                               (digest, now)).fetchone()
            if row is None or not hmac.compare_digest(row["code_hash"], digest):
                raise invalid
            if kind and kind != row["kind"]:
                raise PairingError("wrong_kind", f"That code is for a {KIND_NAMES.get(row['kind'], row['kind'])}, "
                                   f"not a {KIND_NAMES.get(kind, kind)}.", expected=row["kind"])
            user = users.get(row["user_id"])
            if user is None:
                raise invalid
            token, device = _insert(conn, user.id, row["kind"], clean_name(row["name"] or name, row["kind"]),
                                    row["room"], row["role"])
            # scoped: the pairing found by its code above
            conn.execute("UPDATE device_pairings SET used_at = ?, device_id = ? WHERE id = ? AND used_at IS NULL",
                         (now, device.id, row["id"]))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    _changed()
    logger.info(f"Pairing {row['id']} used: device {device.id} ({device.kind}) of user {user.id}")
    return token, device, user
