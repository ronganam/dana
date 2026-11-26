"""The event the ``calendar`` addon emits for the event catalog (../docs/addons.md,
"Events"): ``calendar.event.starting``, once per event start per user, about
:data:`MINUTES_BEFORE` minutes before it starts ("10 minutes before my meeting
with Moti remind me to bring the deck").

:func:`announce_starting` runs every minute for each user with the addon (the
``starting`` job in :mod:`.hooks`): it reads the next hour of the user's
selected calendars (cached per connection for :data:`REFRESH` seconds; one
Google request per calendar per refresh) and emits for each timed event that
starts within :data:`MINUTES_BEFORE` minutes. Each emit is claimed first in
``calendar_starting`` (user, event id, start), so it happens once across
restarts and a moved event is announced again for its new time; an event that
already started is never announced. All-day events are left out. The rows keep
the emitted data: they are the event's history (dry runs), pruned after
:data:`KEEP_DAYS` days.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core import clock, events
from core.addons import Connection, EventType, store
from core.addons.hooks import PastEvent
from core.db import get_db_connection
from core.users import User
from addons.calendar import client, events as calendar_events
from addons.calendar.config import ADDON

logger = logging.getLogger(__name__)

EVENT = "calendar.event.starting"
MINUTES_BEFORE = 10
LOOKAHEAD = datetime.timedelta(hours=1)  # read ahead per refresh (more than REFRESH + MINUTES_BEFORE)
REFRESH = 300  # seconds between reads of one connection's calendars
KEEP_DAYS = 30
DESCRIPTION_CHARS = 300


@dataclass
class _Upcoming:
    at: float = 0.0  # time.monotonic() of the last read (or failed read)
    until: Optional[datetime.datetime] = None  # the read covers starts up to here
    events: List[Dict[str, Any]] = field(default_factory=list)


_cache: Dict[Tuple[Any, Tuple[str, ...]], _Upcoming] = {}


def _parse(value: Any) -> Optional[datetime.datetime]:
    """A timed event's start / end as an aware local datetime (None for a date or garbage)."""
    text = str(value or "").strip()
    if len(text) <= 10:
        return None
    try:
        when = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    tz = clock.get_timezone()
    return (when if when.tzinfo is not None else when.replace(tzinfo=tz)).astimezone(tz)


def _short(text: Any) -> str:
    """A description in one short line: tags dropped, spaces collapsed, cut at :data:`DESCRIPTION_CHARS`."""
    plain = re.sub(r"<[^>]+>", " ", str(text or ""))
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain if len(plain) <= DESCRIPTION_CHARS else plain[:DESCRIPTION_CHARS - 1].rstrip() + "…"


def starting_data(event: Mapping[str, Any], start: datetime.datetime, minutes_before: int) -> Dict[str, Any]:
    """``calendar.event.starting``'s data of one event of :func:`calendar_events.list_events` (details on)."""
    end = _parse(event.get("end"))
    return {"event_id": event.get("id") or "", "title": event.get("title") or "",
            "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds") if end is not None else "",
            "location": event.get("location") or "",
            "attendees": ", ".join(a for a in (event.get("attendees") or []) if a),
            "minutes_before": minutes_before, "description": _short(event.get("description"))}


def _key(conn: Connection) -> Tuple[Any, Tuple[str, ...]]:
    return (client.signature(conn), tuple(client.selected_calendar_ids(conn)))


async def _upcoming(conn: Connection, now: datetime.datetime) -> List[Dict[str, Any]]:
    """The connection's events starting in the next hour, from the cache when it
    is fresh and covers ``now`` + :data:`MINUTES_BEFORE`; a failed read keeps
    the last events and waits :data:`REFRESH` seconds before trying again."""
    key = _key(conn)
    entry = _cache.get(key)
    if entry is None:
        for old in [k for k in _cache if k[0][0] == conn.user.id]:
            _cache.pop(old, None)
        entry = _cache[key] = _Upcoming()
    horizon = now + datetime.timedelta(minutes=MINUTES_BEFORE + 1)
    fresh = entry.until is not None and time.monotonic() - entry.at < REFRESH and horizon <= entry.until
    if fresh:
        return entry.events
    until = now + LOOKAHEAD
    try:
        entry.events = await asyncio.to_thread(calendar_events.list_events, conn, now, until, 50, True)
        entry.until = until
    except ValueError as e:  # no OAuth client / refresh token
        logger.debug(f"Calendar of {conn.user.id} not configured ({e})")
        entry.events, entry.until = [], until
    except Exception as e:
        logger.info(f"Upcoming events of {conn.user.id} not read: {e}")
        entry.until = entry.until or now
    entry.at = time.monotonic()
    return entry.events


def _claim(user: User, event_id: str, start: str, now: datetime.datetime, data: Dict[str, Any]) -> bool:
    """Record that this event start is announced; False when it already was."""
    at = now.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO calendar_starting (user_id, event_id, start, emitted_at, data) "
                           "VALUES (?, ?, ?, ?, ?)", (user.id, event_id, start, at,
                                                      json.dumps(data, ensure_ascii=False)))
        conn.commit()
        return cur.rowcount == 1


async def announce_starting(user: User, now: Optional[datetime.datetime] = None) -> int:
    """Emit ``calendar.event.starting`` for each of ``user``'s timed events that
    starts within :data:`MINUTES_BEFORE` minutes of ``now`` and was not
    announced yet; returns how many (0 without the addon)."""
    conn = store.get(user, ADDON)
    if conn is None:
        return 0
    now = now or clock.now()
    n = 0
    for ev in await _upcoming(conn, now):
        if ev.get("all_day") or not ev.get("id"):
            continue
        start = _parse(ev.get("start"))
        if start is None:
            continue
        lead = (start - now).total_seconds() / 60
        if not 0 < lead <= MINUTES_BEFORE:
            continue
        data = starting_data(ev, start, math.ceil(lead))
        if _claim(user, data["event_id"], data["start"], now, data):
            events.emit(EVENT, user, **data)
            n += 1
    return n


def prune(user: User, now: Optional[datetime.datetime] = None) -> int:
    """Forget ``user``'s announced starts older than :data:`KEEP_DAYS` days; returns how many."""
    cutoff = ((now or clock.now()) - datetime.timedelta(days=KEEP_DAYS)).astimezone(datetime.timezone.utc)
    with get_db_connection() as conn:
        cur = conn.execute("DELETE FROM calendar_starting WHERE user_id = ? AND emitted_at < ?",
                           (user.id, cutoff.isoformat(timespec="seconds")))
        conn.commit()
        return cur.rowcount


def starting_history(user: User, since: datetime.datetime) -> List[PastEvent]:
    """``user``'s announced event starts since ``since`` (as emitted), oldest first."""
    utc = since.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        rows = conn.execute("SELECT emitted_at, data FROM calendar_starting WHERE user_id = ? AND emitted_at >= ? "
                            "ORDER BY emitted_at", (user.id, utc)).fetchall()
    out: List[PastEvent] = []
    for row in rows:
        try:
            out.append((datetime.datetime.fromisoformat(row["emitted_at"]), json.loads(row["data"])))
        except (TypeError, ValueError):
            continue
    return out


CALENDAR_EVENTS = (
    EventType(EVENT, f"An event on the user's calendar starts in about {MINUTES_BEFORE} minutes "
                     "(once per event; not all-day events).",
              {"event_id": "the calendar event's id",
               "title": "the event's title (match a meeting by a word of it, e.g. a person's name)",
               "start": "when it starts (local ISO time)",
               "end": "when it ends (local ISO time)",
               "location": "where (may be empty)",
               "attendees": "the other attendees' names or emails, comma-joined (may be empty)",
               "minutes_before": f"minutes until it starts when emitted: {MINUTES_BEFORE}, fewer for an event "
                                 "added later or seen late after a restart",
               "description": f"the start of its description, plain text (up to {DESCRIPTION_CHARS} characters)"},
              history=starting_history),
)
