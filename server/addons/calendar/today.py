"""Today's events of one user, cached per connection (the Now screen's day
strip, the approval card's event names): refreshed in the background every
:data:`TTL` seconds, never awaited for long.

Each read records whether the user's calendar could be read in
``system_marks['calendar:<user id>']`` (core/checks.py reads it).
"""

import asyncio
import datetime
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core import clock
from core.db import get_db_connection
from core.users import User
from core.addons import Connection, store
from addons.calendar import client, events
from addons.calendar.config import ADDON

logger = logging.getLogger(__name__)

TTL = 300

CacheKey = Tuple[client.Signature, Tuple[str, ...]]


@dataclass
class _Today:
    date: Optional[str] = None
    events: Optional[List[Dict[str, Any]]] = None
    at: float = 0.0
    task: Optional["asyncio.Task[None]"] = field(default=None, repr=False)


# One entry per connection and calendar selection (a new token or selection starts over).
_cache: Dict[CacheKey, _Today] = {}


def mark_key(user_id: str) -> str:
    return f"calendar:{user_id}"


def _mark(user_id: str, ok: bool, detail: str = "") -> None:
    """Record a read of ``user_id``'s calendar (never raises)."""
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO system_marks (key, last_at, ok, last_ok_at, last_fail_at, detail) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET last_at = excluded.last_at, ok = excluded.ok, detail = excluded.detail, "
                "last_ok_at = COALESCE(excluded.last_ok_at, system_marks.last_ok_at), "
                "last_fail_at = COALESCE(excluded.last_fail_at, system_marks.last_fail_at)",
                (mark_key(user_id), now, 1 if ok else 0, now if ok else None, None if ok else now, (detail or "")[:300]))
            conn.commit()
    except Exception as e:
        logger.debug(f"Calendar mark for {user_id} not written: {e}")


def _key(conn: Connection) -> CacheKey:
    return (client.signature(conn), tuple(client.selected_calendar_ids(conn)))


def _entry(conn: Connection) -> _Today:
    key = _key(conn)
    entry = _cache.get(key)
    if entry is None:
        for old in [k for k in _cache if k[0][0] == conn.user.id]:
            _cache.pop(old, None)
        entry = _cache[key] = _Today()
    return entry


async def _fetch(conn: Connection, entry: _Today) -> None:
    now = clock.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        evs = await asyncio.to_thread(events.list_events, conn, start, start + datetime.timedelta(days=1))
        entry.date, entry.events = now.date().isoformat(), evs
        _mark(conn.user.id, True)
    except ValueError as e:  # no OAuth client / refresh token
        logger.debug(f"Calendar of {conn.user.id} not configured ({e})")
        entry.date, entry.events = now.date().isoformat(), []
    except Exception as e:
        logger.info(f"Calendar of {conn.user.id} unavailable ({e})")
        _mark(conn.user.id, False, str(e)[:200])
    finally:
        entry.at = time.monotonic()


def _refresh(conn: Connection, entry: _Today) -> Optional["asyncio.Task[None]"]:
    if entry.task is not None and not entry.task.done():
        return entry.task
    try:
        entry.task = asyncio.get_running_loop().create_task(_fetch(conn, entry))
    except RuntimeError:
        return None
    return entry.task


async def events_today(user: User, wait: float) -> Optional[List[Dict[str, Any]]]:
    """``user``'s events today from the cache (refreshed in the background
    every :data:`TTL` seconds). Waits up to ``wait`` seconds only when there is
    nothing for today yet. None: not read today (failing, or still loading),
    or ``user`` has no calendar."""
    conn = store.get(user, ADDON)
    if conn is None:
        return None
    entry = _entry(conn)
    today = clock.now().date().isoformat()
    usable = entry.date == today
    if not usable or time.monotonic() - entry.at > TTL:
        task = _refresh(conn, entry)
        if not usable and task is not None and wait > 0:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=wait)
            except (asyncio.TimeoutError, Exception):
                pass
    return entry.events if entry.date == today else None


async def refresh_now(user: User) -> None:
    """Read ``user``'s calendar for today now (no-op without the addon)."""
    conn = store.get(user, ADDON)
    if conn is not None:
        await _fetch(conn, _entry(conn))


def cached_events(user: Optional[User]) -> List[Dict[str, Any]]:
    """What the cache holds for ``user`` (no calendar call; [] without one)."""
    conn = store.get(user, ADDON) if user is not None else None
    if conn is None:
        return []
    entry = _cache.get(_key(conn))
    return list(entry.events or []) if entry is not None else []
