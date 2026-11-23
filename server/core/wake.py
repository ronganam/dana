"""The wake flow: one daily "the user is up" event per user → the event
``core.user.woke`` (``why``; the tasks addon fires the wake-trigger tasks) and
their morning briefing (docs/people.md, "Wake detection"; docs/signals.md).
A composer: a delivery channel plus a source of the ``wake`` signal
(core/signals.py: Dana's phone app). Anyone without both
is skipped.

Inside the user's wake window (core/wake_window.py), today:

- an **unlock** (:func:`unlock`, from the phone) starts a check: after
  :data:`WAKE_CONFIRM_SECONDS` the screen must have been on at least
  :data:`WAKE_MIN_SCREEN_SECONDS` since (the highest-ranked
  ``screen_sessions`` provider that answers: the phone's own screen
  sessions; none → the unlock is trusted);
- a **sleep** session (:func:`sleep_ended`, Health Connect through the
  phone) ending in the window is the wake, at that exact time;
- the **poll** (:func:`poll_once`) fires on enough screen use in the last
  30 minutes when no signal came.

Each goes through ``signals.deliver``: a lower-ranked source's wake is
ignored while a higher-ranked one is recent. Per user: the confirmed wakes
(``wake_log``, one row per user and date: the "once a day" record) and the
check in flight (in memory).
"""

import asyncio
import datetime
import logging
import os
from typing import Dict, List, Optional, Tuple

from core import briefing, clock, composers, events, signals, users, wake_window
from core.addons import registry
from core.db import get_db_connection
from core.users import User

logger = logging.getLogger(__name__)

WAKE_CONFIRM_SECONDS = int(os.getenv("WAKE_CONFIRM_SECONDS", "600"))
WAKE_MIN_SCREEN_SECONDS = int(os.getenv("WAKE_MIN_SCREEN_SECONDS", "90"))
WAKE_FALLBACK_SECONDS = int(os.getenv("WAKE_FALLBACK_SECONDS", "360"))
WAKE_FALLBACK_SESSIONS = int(os.getenv("WAKE_FALLBACK_SESSIONS", "3"))
WAKE_POLL_SECONDS = int(os.getenv("WAKE_POLL_SECONDS", "300"))

# user id → the local date a confirmation check is running for.
_pending: Dict[str, str] = {}


def available(user: Optional[User]) -> bool:
    """Whether the wake flow runs for ``user``: a delivery channel and a
    ``wake`` source that delivered recently or is set up."""
    return user is not None and bool(registry.channels(user)) and signals.usable(user, "wake")


def done_today(user: User, today: str) -> bool:
    """Whether ``user``'s wake was already confirmed on ``today``."""
    try:
        with get_db_connection() as conn:
            return conn.execute("SELECT 1 FROM wake_log WHERE user_id = ? AND date = ?",
                                (user.id, today)).fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking the wake date of user {user.id}: {e}")
        return False


async def screen_sessions(user: User, since_utc: datetime.datetime,
                          until_utc: datetime.datetime) -> Tuple[Optional[str], Optional[List[float]]]:
    """Seconds of each screen-on stretch of ``user`` in [since, until] from the
    highest-ranked ``screen_sessions`` provider that answers, and its source
    (recorded as the ``screen`` signal's); (None, None) when none answers."""
    hook, sessions = await registry.answer("screen_sessions", user, since_utc, until_utc)
    if hook is None:
        return None, None
    source = registry.source_of(hook)
    signals.record(user, "screen", source, f"{sum(sessions):.0f}s on")
    return source, list(sessions)


def _local(dt: datetime.datetime) -> datetime.datetime:
    return dt.astimezone(clock.get_timezone())


async def unlock(user: User, at_utc: Optional[datetime.datetime] = None, source: str = "phone") -> str:
    """``user``'s phone was unlocked at ``at_utc`` (default now). Returns
    "unavailable" | "ignored" (a higher-ranked source is active) | "outside_window" | "done" | "confirming"."""
    at = at_utc or datetime.datetime.now(datetime.timezone.utc)
    wake_window.note_unlock(user, _local(at))
    if not signals.deliver(user, "wake", source, at=at, detail="unlock"):
        return "unavailable" if not available(user) else "ignored"
    return await handle_wake(user, "unlock", at)


async def sleep_ended(user: User, start_utc: datetime.datetime, end_utc: datetime.datetime,
                      source: str = "phone") -> str:
    """A sleep session of ``user`` (Health Connect through the phone). Ending
    in today's wake window it is the wake, at ``end_utc``. Returns
    "unavailable" | "ignored" | "outside_window" | "done" | "woke"."""
    start, end = _local(start_utc), _local(end_utc)
    signals.deliver(user, "sleep", source, at=end_utc, detail=f"{start:%H:%M}–{end:%H:%M}")
    if not available(user):
        return "unavailable"
    today = clock.now().date()
    if end.date() != today or not wake_window.get(user).contains(end.time()):
        return "outside_window"
    if done_today(user, today.isoformat()):
        return "done"
    if not signals.deliver(user, "wake", source, at=end_utc, detail="sleep"):
        return "ignored"
    fired = await _fire_wake(user, today.isoformat(), f"sleep ended {end:%H:%M}", at=end)
    return "woke" if fired else "done"


async def handle_wake(user: User, source: str, signal_utc: datetime.datetime) -> str:
    """A wake signal of ``user`` at ``signal_utc`` that counts. Returns
    "unavailable" | "outside_window" | "done" | "confirming"."""
    if not available(user):
        logger.info(f"Wake signal ({source}) for user {user.id} without a channel or a wake source; skipped.")
        return "unavailable"
    local = _local(signal_utc)
    today = clock.now().date()
    if local.date() != today or not wake_window.get(user).contains(local.time()):
        logger.info(f"Wake signal ({source}) at {local:%H:%M} is outside user {user.id}'s wake window; ignored.")
        return "outside_window"
    if done_today(user, today.isoformat()):
        return "done"
    if _pending.get(user.id) == today.isoformat():
        return "confirming"
    _pending[user.id] = today.isoformat()
    with users.acting_as(user):
        asyncio.create_task(_confirm_wake(user, today.isoformat(), signal_utc, source))
    return "confirming"


async def _confirm_wake(user: User, today: str, since_utc: datetime.datetime, source: str) -> None:
    try:
        logger.info(f"Wake signal ({source}) for user {user.id}: checking in {WAKE_CONFIRM_SECONDS}s that they are up...")
        await asyncio.sleep(WAKE_CONFIRM_SECONDS)
        _, sessions = await screen_sessions(user, since_utc, datetime.datetime.now(datetime.timezone.utc))
        if sessions is None:
            logger.info("Wake check: no screen sessions from any source; trusting the signal.")
        elif sum(sessions) < WAKE_MIN_SCREEN_SECONDS:
            logger.info(f"Wake check: screen on {sum(sessions):.0f}s since the {source} signal "
                        f"(< {WAKE_MIN_SCREEN_SECONDS}s) — still asleep; waiting for the next one.")
            return
        await _fire_wake(user, today, f"{source}, screen {sum(sessions or []):.0f}s")
    except Exception as e:
        logger.error(f"Wake handling for user {user.id} failed: {e}", exc_info=True)
    finally:
        if _pending.get(user.id) == today:
            _pending.pop(user.id, None)


async def check_wake_fallback(user: User) -> bool:
    """The poll: no signal came, but ``user``'s screen sessions say they have been up a while."""
    if not available(user):
        return False
    now = clock.now()
    if not wake_window.get(user).contains(now.time()):
        return False
    today = now.date().isoformat()
    if done_today(user, today) or _pending.get(user.id) == today:
        return False
    until = datetime.datetime.now(datetime.timezone.utc)
    source, sessions = await screen_sessions(user, until - datetime.timedelta(minutes=30), until)
    if not sessions or source is None:
        return False
    total, count = sum(sessions), sum(1 for s in sessions if s >= 5)
    if total < WAKE_FALLBACK_SECONDS or count < WAKE_FALLBACK_SESSIONS:
        return False
    if not signals.deliver(user, "wake", source, detail="screen"):
        return False
    await _fire_wake(user, today, f"screen sessions: {total:.0f}s in {count}")
    return True


async def poll_once() -> Dict[str, bool]:
    """One fallback poll for every user with the wake flow."""
    return await composers.for_each("Wake poll", available, check_wake_fallback)


async def wake_watch_loop() -> None:
    """Poll the screen sessions of every user with the wake flow when no signal comes."""
    while True:
        await poll_once()
        await asyncio.sleep(WAKE_POLL_SECONDS)


async def _fire_wake(user: User, today: str, why: str, at: Optional[datetime.datetime] = None) -> bool:
    """Record ``user``'s wake of ``today`` (``at``: when, default now) and send
    ``core.user.woke`` and the briefing; False when it was already recorded."""
    when = _local(at) if at is not None else clock.now()
    with get_db_connection() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO wake_log (user_id, date, at, why) VALUES (?, ?, ?, ?)",
                           (user.id, today, when.isoformat(timespec="seconds"), (why or "")[:200]))
        conn.commit()
    if not cur.rowcount:
        return False  # already confirmed today
    logger.info(f"User {user.id} is up ({why}): core.user.woke and the briefing.")
    events.emit("core.user.woke", user, why=why)
    await briefing.on_wake(user)
    return True
