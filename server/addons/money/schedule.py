"""When sources sync (docs/money.md, "Schedule").

:func:`loop` (started with the app) looks every minute at every user with
``money``, as that user, and starts the sources whose ``next_sync_at`` has
come, unless they are syncing, waiting for a code or need attention. Regular
slots are ``MONEY_SYNC_TIMES`` (local) with a stable per-source jitter
(``slots.py``). :func:`request_soon` pulls a sync earlier (a phone payment
signal: at most :data:`config.SIGNAL_SYNCS_PER_DAY` a local day, never in the
quiet hours); :func:`sync_now` is "Sync now" (the page, the agent), at most
every :data:`config.MANUAL_MINUTES` per source."""

import asyncio
import datetime
import logging
import math
from typing import Any, Dict, List, Optional

from core import clock, users
from core.users import User
from core.addons import store as connections
from addons.money import classify, config, slots, store, sync

logger = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 15
INTERVAL_SECONDS = 60
# A source in one of these states is never started by the loop.
_WAITING = ("syncing", "needs_code", "needs_attention")
MANUAL_TRIGGERS = ("manual", "agent")

# Source id → the next_sync_at a signal set (the loop then starts it as a "signal" sync).
_signal_due: Dict[str, str] = {}


class TooSoon(Exception):
    """A manual sync of the source ran less than :data:`config.MANUAL_MINUTES` ago."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"synced moments ago; try again in {retry_after} s")
        self.retry_after = retry_after


class NeedsAttention(Exception):
    """The source's login must be fixed first (``reason``)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def request_soon(user: User, source_id: str, minutes: float = 30, reason: str = "signal") -> Optional[str]:
    """Pull the source's next sync to ``minutes`` from now (moved out of the
    quiet hours). Returns when it is now due (ISO UTC; an earlier time already
    set is kept), or None when refused: an unknown source, one that needs
    attention or is failing (its retries and slots stand: a payment never adds
    a sign-in to a login that fails), or :data:`config.SIGNAL_SYNCS_PER_DAY`
    pulls already today."""
    now = store.utc_now()
    target = slots.out_of_quiet_hours(source_id, now + datetime.timedelta(minutes=minutes))
    today = clock.now().date().isoformat()
    with store.db() as conn:
        row = conn.execute("SELECT state, next_sync_at, signal_syncs_day, signal_syncs FROM money_sources "
                           "WHERE id = ? AND user_id = ?", (source_id, user.id)).fetchone()
        if row is None or row["state"] in ("needs_attention", "failing"):
            return None
        current = store.parse(row["next_sync_at"])
        if current is not None and current <= target:
            return store.iso(current)
        count = row["signal_syncs"] if row["signal_syncs_day"] == today else 0
        if count >= config.SIGNAL_SYNCS_PER_DAY:
            return None
        due = store.iso(target)
        conn.execute("UPDATE money_sources SET next_sync_at = ?, signal_syncs_day = ?, signal_syncs = ?, "
                     "updated_at = ? WHERE id = ? AND user_id = ?",
                     (due, today, count + 1, store.iso(now), source_id, user.id))
    _signal_due[source_id] = due
    logger.info(f"Money source {source_id} of user {user.id}: sync pulled to {due} ({reason})")
    return due


def sync_now(user: User, source_id: str, trigger: str = "manual") -> str:
    """Start a sync of the source now; returns its id (the running one's when
    it is already syncing). ``manual`` / ``agent`` syncs are rate-limited.

    Raises:
        LookupError: no such source of the user.
        NeedsAttention: its login must be fixed first.
        TooSoon: a manual sync ran less than :data:`config.MANUAL_MINUTES` ago.
    """
    source = store.get_source(user, source_id)
    if source is None:
        raise LookupError(f"unknown money source {source_id!r}")
    running = sync.running(source_id)
    if running:
        return running
    # Some login problems may be retried by the user's own "Sync now" (store.HAND_RETRY_ERRORS); never by Dana.
    by_hand = trigger == "manual" and store.hand_sync_allowed(source)
    if source["state"] == "needs_attention" and not by_hand:
        raise NeedsAttention(source.get("state_reason") or "the login must be updated")
    if trigger in MANUAL_TRIGGERS:
        now = store.utc_now()
        last = store.parse(source.get("last_manual_at"))
        wait = config.MANUAL_MINUTES * 60 - ((now - last).total_seconds() if last else math.inf)
        if wait > 0:
            raise TooSoon(int(math.ceil(wait)))
        with store.db() as conn:
            conn.execute("UPDATE money_sources SET last_manual_at = ? WHERE id = ? AND user_id = ?",
                         (store.iso(now), source_id, user.id))
    return sync.start(user, source_id, trigger)


def due_sources(user: User, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """The user's sources the loop should start now."""
    at = store.iso(now or store.utc_now())
    marks = ",".join("?" * len(_WAITING))
    with store.db() as conn:
        rows = conn.execute(f"SELECT id, next_sync_at FROM money_sources WHERE user_id = ? AND next_sync_at IS NOT NULL "
                            f"AND next_sync_at <= ? AND state NOT IN ({marks}) ORDER BY next_sync_at",
                            (user.id, at, *_WAITING)).fetchall()
    return [dict(r) for r in rows if not sync.running(r["id"])]


def tick(now: Optional[datetime.datetime] = None) -> Dict[str, List[str]]:
    """Start every due source of every user with ``money`` (each as that user;
    one user's failure does not stop the next). Returns user id → started sync ids."""
    out: Dict[str, List[str]] = {}
    for uid in connections.users_with(config.ADDON):
        user = users.get(uid)
        if user is None:
            continue
        try:
            with users.acting_as(user):
                for source in due_sources(user, now):
                    trigger = "signal" if _signal_due.pop(source["id"], None) == source["next_sync_at"] else "schedule"
                    out.setdefault(user.id, []).append(sync.start(user, source["id"], trigger))
        except Exception as e:
            logger.error(f"Money schedule for user {uid} failed: {e}", exc_info=True)
    return out


def recover_all() -> None:
    """End what a previous process left mid-sync, for every user with ``money``."""
    for uid in connections.users_with(config.ADDON):
        user = users.get(uid)
        if user is None:
            continue
        try:
            with users.acting_as(user):
                sync.recover(user)
        except Exception as e:
            logger.error(f"Money recovery for user {uid} failed: {e}", exc_info=True)


async def loop(interval_seconds: float = INTERVAL_SECONDS) -> None:
    """Background loop started with the app (core/server.py)."""
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    recover_all()
    _catch_up = asyncio.get_running_loop().create_task(classify.catch_up())
    while True:
        try:
            tick()
        except Exception as e:
            logger.error(f"Money schedule loop error: {e}", exc_info=True)
        await asyncio.sleep(interval_seconds)
