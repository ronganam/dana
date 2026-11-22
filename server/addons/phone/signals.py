"""The phone as a source of the user's signals (docs/signals.md): what the app
posts to ``POST /api/signals`` and the screen sessions it keeps.

The app sends its events oldest first, at most :data:`MAX_EVENTS` per call,
and sends a whole batch again when a call fails, so every event that is
applied is remembered by a hash of its type, action, zone and times
(``phone_signal_keys``, :data:`STALE_HOURS`): the same event again is
accepted without effect. Each event's effect:

- ``unlock`` → the wake flow (``core.wake.unlock``);
- ``screen`` → a screen session (``phone_screen_sessions``, kept
  :data:`SCREEN_KEEP_DAYS` days), read back by the ``screen_sessions``
  provider (hooks.py) when the wake flow confirms a wake;
- ``car`` → the ``car`` signal (``core.signals.deliver``);
- ``zone`` → the ``location`` signal with the name of that zone of the user's;
- ``sleep`` → ``core.wake.sleep_ended`` (a sleep ending in the wake window
  today is the wake, at that time).

Per event an error: ``bad_event`` (a missing or invalid field, or a time more
than :data:`FUTURE_MINUTES` ahead), ``unknown_zone`` (not one of the caller's
zones: another user's zone id answers the same), ``stale`` (older than
:data:`STALE_HOURS`; dropped). The server never stores where the phone is:
only "arrived at Home at 18:02" as the user's last location signal.
"""

import datetime
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from core import devices, signals, wake, zones
from core.db import get_db_connection
from core.users import User

logger = logging.getLogger(__name__)

MAX_EVENTS = 200
STALE_HOURS = 48
FUTURE_MINUTES = 10
MAX_SPAN_HOURS = 24
SCREEN_KEEP_DAYS = 3
TYPES = ("unlock", "screen", "car", "zone", "sleep")
CAR_ACTIONS = ("enter", "leave")
CAR_VIA = ("bluetooth", "android_auto", "activity")
ZONE_ACTIONS = ("arrive", "leave")
SLEEP_VIA = ("health_connect",)
SOURCE = "phone"
LABEL = "Dana's phone app"


class Rejected(ValueError):
    """An event refused with ``error`` (``bad_event`` | ``unknown_zone`` | ``stale``)."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


def _utc(ms: int) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)


def _ms(event: Dict[str, Any], field: str) -> int:
    value = event.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value) or value <= 0:
        raise Rejected("bad_event")
    return int(value)


def _choice(event: Dict[str, Any], field: str, allowed: Tuple[str, ...]) -> str:
    value = event.get(field)
    if value not in allowed:
        raise Rejected("bad_event")
    return str(value)


def _check_time(ms: int, now: datetime.datetime) -> None:
    t = _utc(ms)
    if t > now + datetime.timedelta(minutes=FUTURE_MINUTES):
        raise Rejected("bad_event")
    if t < now - datetime.timedelta(hours=STALE_HOURS):
        raise Rejected("stale")


def _span(event: Dict[str, Any], now: datetime.datetime) -> Tuple[int, int]:
    start, end = _ms(event, "start"), _ms(event, "end")
    if end < start or end - start > MAX_SPAN_HOURS * 3600 * 1000:
        raise Rejected("bad_event")
    _check_time(end, now)
    return start, end


def parse(user: User, event: Any, now: datetime.datetime) -> Dict[str, Any]:
    """One event checked: its fields normalised (``zone``: the user's zone).

    Raises:
        Rejected: why it is refused.
    """
    if not isinstance(event, dict):
        raise Rejected("bad_event")
    kind = event.get("type")
    if kind not in TYPES:
        raise Rejected("bad_event")
    out: Dict[str, Any] = {"type": kind}
    if kind in ("unlock", "car", "zone"):
        out["at"] = _ms(event, "at")
        _check_time(out["at"], now)
    if kind == "screen":
        out["start"], out["end"] = _span(event, now)
    elif kind == "sleep":
        out["start"], out["end"] = _span(event, now)
        out["via"] = _choice(event, "via", SLEEP_VIA)
        if out["end"] == out["start"]:
            raise Rejected("bad_event")
    elif kind == "car":
        out["action"] = _choice(event, "action", CAR_ACTIONS)
        out["via"] = _choice(event, "via", CAR_VIA)
    elif kind == "zone":
        out["action"] = _choice(event, "action", ZONE_ACTIONS)
        zone_id = event.get("zone_id")
        if not isinstance(zone_id, str) or not zone_id:
            raise Rejected("bad_event")
        zone = zones.get(user, zone_id)
        if zone is None:
            raise Rejected("unknown_zone")
        out["zone_id"], out["zone"] = zone_id, zone
    return out


def event_key(event: Dict[str, Any]) -> str:
    """The hash that makes a repeated event the same one (type, action, zone, times)."""
    parts = [event.get(k) for k in ("type", "action", "zone_id", "at", "start", "end")]
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def _claim(user: User, key: str, now: datetime.datetime) -> bool:
    """Remember ``key`` for ``user``; False when it was already there (a repeat)."""
    with get_db_connection() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO phone_signal_keys (user_id, key, at) VALUES (?, ?, ?)",
                           (user.id, key, now.isoformat(timespec="seconds")))
        conn.commit()
    return cur.rowcount > 0


def _prune(now: datetime.datetime) -> None:
    with get_db_connection() as conn:
        # scoped: server-wide retention of event hashes older than the stale limit
        conn.execute("DELETE FROM phone_signal_keys WHERE at < ?",
                     ((now - datetime.timedelta(hours=STALE_HOURS + 1)).isoformat(timespec="seconds"),))
        # scoped: server-wide retention of screen sessions (SCREEN_KEEP_DAYS)
        conn.execute("DELETE FROM phone_screen_sessions WHERE end_ms < ?",
                     (int((now - datetime.timedelta(days=SCREEN_KEEP_DAYS)).timestamp() * 1000),))
        conn.commit()


def store_screen(user: User, start_ms: int, end_ms: int) -> None:
    """One screen-on stretch of ``user``'s phone (the same start again keeps the longer end)."""
    with get_db_connection() as conn:
        conn.execute("INSERT INTO phone_screen_sessions (user_id, start_ms, end_ms) VALUES (?, ?, ?) "
                     "ON CONFLICT(user_id, start_ms) DO UPDATE SET end_ms = MAX(end_ms, excluded.end_ms)",
                     (user.id, start_ms, end_ms))
        conn.commit()


def screen_seconds(user: User, since_utc: datetime.datetime,
                   until_utc: datetime.datetime) -> Optional[List[float]]:
    """Seconds of each of ``user``'s phone screen sessions inside [since,
    until]; None when the phone has sent none for ``signals.ACTIVE_HOURS``
    (then it is not a source of screen sessions and the next one is asked)."""
    since_ms, until_ms = int(since_utc.timestamp() * 1000), int(until_utc.timestamp() * 1000)
    recent_ms = int((datetime.datetime.now(datetime.timezone.utc)
                     - datetime.timedelta(hours=signals.ACTIVE_HOURS)).timestamp() * 1000)
    with get_db_connection() as conn:
        last = conn.execute("SELECT MAX(end_ms) AS e FROM phone_screen_sessions WHERE user_id = ?",
                            (user.id,)).fetchone()["e"]
        if last is None or last < recent_ms:
            return None
        rows = conn.execute("SELECT start_ms, end_ms FROM phone_screen_sessions WHERE user_id = ? "
                            "AND start_ms < ? AND end_ms > ? ORDER BY start_ms",
                            (user.id, until_ms, since_ms)).fetchall()
    return [(min(r["end_ms"], until_ms) - max(r["start_ms"], since_ms)) / 1000 for r in rows]


def missing(user: User) -> str:
    """What the phone needs before it can deliver ``user``'s signals ("" when nothing)."""
    if any(d.kind == "phone" and d.paired for d in devices.list_for(user.id)):
        return ""
    return "Pair Dana's phone app (Settings → Devices)"


async def _apply(user: User, e: Dict[str, Any]) -> None:
    kind = e["type"]
    if kind == "unlock":
        await wake.unlock(user, _utc(e["at"]), SOURCE)
    elif kind == "screen":
        store_screen(user, e["start"], e["end"])
        signals.record(user, "screen", SOURCE, f"{(e['end'] - e['start']) / 1000:.0f}s on", _utc(e["end"]))
    elif kind == "car":
        signals.deliver(user, "car", SOURCE, at=_utc(e["at"]), detail=f"{e['action']} ({e['via']})",
                        action=e["action"])
    elif kind == "zone":
        name = e["zone"].name
        signals.deliver(user, "location", SOURCE, at=_utc(e["at"]), detail=f"{e['action']} {name}",
                        action=e["action"], target=name)
    elif kind == "sleep":
        await wake.sleep_ended(user, _utc(e["start"]), _utc(e["end"]), SOURCE)


async def receive(user: User, events: List[Any]) -> Dict[str, Any]:
    """``POST /api/signals`` for ``user``: each event checked and applied in
    order (a repeat accepted without effect). Returns ``{accepted, rejected:
    [{index, error}]}``."""
    now = datetime.datetime.now(datetime.timezone.utc)
    _prune(now)
    accepted, rejected = 0, []
    for i, raw in enumerate(events):
        try:
            e = parse(user, raw, now)
        except Rejected as r:
            rejected.append({"index": i, "error": r.error})
            continue
        accepted += 1
        if not _claim(user, event_key(e), now):
            continue
        try:
            await _apply(user, e)
        except Exception:
            logger.exception(f"Signal {e['type']} of user {user.id} failed")
    return {"accepted": accepted, "rejected": rejected}
