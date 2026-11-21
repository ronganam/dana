"""The wake flow: one daily "the user is up" event per user → their
wake-trigger tasks (``addons.tasks.triggers.fire(user, "wake")``) and
their morning briefing (docs/people.md, "Wake detection"). A composer: the
phone's unlock signal plus the phone's screen history from the user's Home
Assistant (their ``phone_interactive_sensor`` setting). Only for a user with
both ``phone`` and ``homeassistant``; anyone else is skipped.

Per user: the confirmed wakes (``wake_log``, one row per user and date: the
"once a day" record), the check in flight (in memory), the last raw signal
(``system_marks['wake_signal:<user id>']``).
"""

import asyncio
import datetime
import logging
import os
from typing import Dict, List, Optional

from core import briefing, clock, composers, users
from core.db import get_db_connection
from core.users import User
from core.addons import store
from addons.homeassistant import client as ha_client, config as ha_config
from addons.phone import config as phone_config
from addons.tasks import triggers

logger = logging.getLogger(__name__)

# Signals (docs/tasks-and-triggers.md):
# - the phone app reports an unlock (POST /api/phone/unlock) — the main one: an
#   alarm ringing or a glance at the clock never unlocks the phone;
# - a fallback poll of the phone's screen history in HA, strict enough that the
#   alarm screen (on for minutes while it rings) can't trip it.
# A signal is confirmed WAKE_CONFIRM_SECONDS later from HA's history: the screen
# was on for WAKE_MIN_SCREEN_SECONDS since the signal. Otherwise nothing is
# recorded and the next unlock tries again.

WAKE_CONFIRM_SECONDS = int(os.getenv("WAKE_CONFIRM_SECONDS", "600"))
WAKE_MIN_SCREEN_SECONDS = int(os.getenv("WAKE_MIN_SCREEN_SECONDS", "90"))
WAKE_WINDOW = (int(os.getenv("WAKE_WINDOW_START_HOUR", "5")), int(os.getenv("WAKE_WINDOW_END_HOUR", "12")))
# Fallback: screen on ≥ this much, in ≥ WAKE_FALLBACK_SESSIONS sessions, within the last 30 min.
WAKE_FALLBACK_SECONDS = int(os.getenv("WAKE_FALLBACK_SECONDS", "360"))
WAKE_FALLBACK_SESSIONS = int(os.getenv("WAKE_FALLBACK_SESSIONS", "3"))
WAKE_POLL_SECONDS = int(os.getenv("WAKE_POLL_SECONDS", "300"))
# The addons the wake flow needs: the phone (unlock signal, briefing push) and
# Home Assistant (the screen history).
ADDONS = (phone_config.ADDON, ha_config.ADDON)
SIGNAL_MARK = "wake_signal"

# user id → the local date a confirmation check is running for.
_pending: Dict[str, str] = {}


def available(user: Optional[User]) -> bool:
    """Whether the wake flow runs for ``user`` (``phone`` and ``homeassistant`` connected)."""
    return user is not None and composers.has_all(user, ADDONS)


def done_today(user: User, today: str) -> bool:
    """Whether ``user``'s wake was already confirmed on ``today``."""
    try:
        with get_db_connection() as conn:
            return conn.execute("SELECT 1 FROM wake_log WHERE user_id = ? AND date = ?",
                                (user.id, today)).fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking the wake date of user {user.id}: {e}")
        return False


async def _screen_sessions(user: User, since_utc: datetime.datetime,
                           until_utc: datetime.datetime) -> Optional[List[float]]:
    """Seconds of each screen-on stretch of ``user``'s phone screen sensor in
    [since, until], from their Home Assistant's history. None when it can't
    be asked (no Home Assistant, no screen sensor set, HA failing)."""
    conn = store.get(user, ha_config.ADDON)
    entity = ha_config.setting(conn, ha_config.SCREEN_SENSOR) if conn is not None else None
    if conn is None or not entity:
        return None
    rows = await ha_client.history(conn, entity, since_utc, until_utc)
    if rows is None:
        return None
    points = []
    for x in rows:
        try:
            t = datetime.datetime.fromisoformat(str(x.get("last_changed")).replace("Z", "+00:00"))
        except ValueError:
            continue
        points.append((t, x.get("state")))
    out: List[float] = []
    for i, (t, state) in enumerate(points):
        if state != "on":
            continue
        t_end = points[i + 1][0] if i + 1 < len(points) else until_utc
        a, b = max(t, since_utc), min(t_end, until_utc)
        if b > a:
            out.append((b - a).total_seconds())
    return out


def _in_window(now: datetime.datetime) -> bool:
    return WAKE_WINDOW[0] <= now.hour < WAKE_WINDOW[1]


async def handle_wake(user: User, source: str = "unlock", signal_utc: Optional[datetime.datetime] = None) -> str:
    """A wake signal for ``user``. Returns: "unavailable" (no ``phone`` or no
    Home Assistant: nothing recorded) | "outside_window" | "done" (already
    today) | "confirming" (a check is running or was just started)."""
    if not available(user):
        logger.info(f"Wake signal ({source}) for user {user.id} without the phone and Home Assistant; skipped.")
        return "unavailable"
    phone_config.mark(SIGNAL_MARK, user.id, True, source)
    now = clock.now()
    if not _in_window(now):
        logger.info(f"Wake signal ({source}) at {now:%H:%M} is outside the wake window; ignored.")
        return "outside_window"
    today = now.date().isoformat()
    if done_today(user, today):
        return "done"
    if _pending.get(user.id) == today:
        return "confirming"
    _pending[user.id] = today
    since = signal_utc or datetime.datetime.now(datetime.timezone.utc)
    with users.acting_as(user):
        asyncio.create_task(_confirm_wake(user, today, since, source))
    return "confirming"


async def _confirm_wake(user: User, today: str, since_utc: datetime.datetime, source: str) -> None:
    try:
        logger.info(f"Wake signal ({source}) for user {user.id}: checking in {WAKE_CONFIRM_SECONDS}s that they are up...")
        await asyncio.sleep(WAKE_CONFIRM_SECONDS)
        sessions = await _screen_sessions(user, since_utc, datetime.datetime.now(datetime.timezone.utc))
        if sessions is None:
            logger.warning("Wake check: no screen history from HA; trusting the signal.")
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
    """The poll: no signal came, but ``user``'s screen history says they have
    been up a while. False right away without ``phone`` and Home Assistant."""
    if not available(user):
        return False
    now = clock.now()
    if not _in_window(now):
        return False
    today = now.date().isoformat()
    if done_today(user, today) or _pending.get(user.id) == today:
        return False
    until = datetime.datetime.now(datetime.timezone.utc)
    sessions = await _screen_sessions(user, until - datetime.timedelta(minutes=30), until)
    if not sessions:
        return False
    total, count = sum(sessions), sum(1 for s in sessions if s >= 5)
    if total >= WAKE_FALLBACK_SECONDS and count >= WAKE_FALLBACK_SESSIONS:
        await _fire_wake(user, today, f"screen history: {total:.0f}s in {count} sessions")
        return True
    return False


async def poll_once() -> Dict[str, bool]:
    """One fallback poll for every user with the wake flow (each as that user,
    a failing one not stopping the next): user id → whether a wake fired."""
    return await composers.for_each("Wake poll", available, check_wake_fallback)


async def wake_watch_loop() -> None:
    """Poll the screen history of every user with the wake flow when no signal comes."""
    while True:
        await poll_once()
        await asyncio.sleep(WAKE_POLL_SECONDS)


async def _fire_wake(user: User, today: str, why: str) -> None:
    with get_db_connection() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO wake_log (user_id, date, at, why) VALUES (?, ?, ?, ?)",
                           (user.id, today, clock.now().isoformat(timespec="seconds"), (why or "")[:200]))
        conn.commit()
    if not cur.rowcount:
        return  # already confirmed today
    logger.info(f"User {user.id} is up ({why}): firing wake tasks and the briefing.")
    await triggers.fire(user, "wake")
    await briefing.on_wake(user)
