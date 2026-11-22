"""Running the addons' background work: ``@addon.job`` and ``@addon.service``
(../docs/addons.md, "The manifest").

:func:`start` (core/server.py, once the web server listens) starts one task
per job and per service of the loaded addons:

- a job runs ``func(user)`` for every user who has its addon active (oldest
  user first; ``registry.active_addons``), each inside ``users.acting_as(user)``
  and limited to the job's ``timeout`` (``asyncio.wait_for``); one user's
  failure or timeout is logged and the next user still runs, and a pass that
  raises is logged without ending the job's loop. ``every``: a pass every
  interval, counted from the start of the last pass, which is kept in
  ``system_marks['job-start:<addon>.<name>']`` so a restart waits out what is
  left (and runs at once when the interval passed while it was down);
  ``daily="HH:MM"``: one pass a day from that local time on (also on the day
  the server starts after it), the local date of the last pass kept in
  ``system_marks['job-day:<addon>.<name>']`` so a restart never runs it twice
  on one day; ``at_start``: one pass when the server starts (``delay``:
  seconds to wait before the first pass of any job). A pass is marked before
  it runs (a restart in the middle does not run it again). Every pass writes
  the job's heartbeat (``system_marks['job:<addon>.<name>']``), read with
  :func:`last_run` (the ``tasks`` addon's "Reminder checks" self-check).
- a service runs ``func(app)`` once; when it returns or raises it is started
  again after a backoff (1 s doubling to 5 min, back to 1 s after it ran for
  10 minutes).

An addon disabled on the server (core/addons/state.py) runs nothing: a job's
pass does nothing (no heartbeat, no mark), a service is not started, and a running one
is cancelled within :data:`STATE_POLL_SECONDS` and started again once the
addon is enabled.

Every task is cancelled by :func:`stop` (the app's cleanup).
"""

import asyncio
import datetime
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from core import budget, clock, users
from core.db import get_db_connection
from core.users import User
from core.addons import registry, state, store
from core.addons.hooks import Job, Service

logger = logging.getLogger(__name__)

SERVICE_BACKOFF_MIN = 1.0
SERVICE_BACKOFF_MAX = 300.0
SERVICE_HEALTHY_SECONDS = 600.0
DAILY_TICK_SECONDS = 30.0
# How often a service checks that its addon is still enabled.
STATE_POLL_SECONDS = 5.0
APP_KEY = "addon_tasks"


def _mark_key(job: Job) -> str:
    return f"job:{job.key}"


def _now() -> datetime.datetime:
    """The moment (aware, local); tests replace it with a fake clock."""
    return clock.now()


async def _sleep(seconds: float) -> None:
    """Wait ``seconds``; tests replace it to move a fake clock instead."""
    await asyncio.sleep(seconds)


def _heartbeat(job: Job, ok: bool, detail: str = "") -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO system_marks (key, last_at, ok, last_ok_at, last_fail_at, detail) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET last_at = excluded.last_at, ok = excluded.ok, detail = excluded.detail, "
                "last_ok_at = COALESCE(excluded.last_ok_at, system_marks.last_ok_at), "
                "last_fail_at = COALESCE(excluded.last_fail_at, system_marks.last_fail_at)",
                (_mark_key(job), now, 1 if ok else 0, now if ok else None, None if ok else now, detail[:300]))
            conn.commit()
    except Exception as e:
        logger.debug(f"Heartbeat of job {job.key} not written: {e}")


def _read_mark(key: str) -> Optional[str]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT last_at FROM system_marks WHERE key = ?", (key,)).fetchone()
    return row["last_at"] if row and row["last_at"] else None


def _write_mark(key: str, value: str) -> None:
    with get_db_connection() as conn:
        conn.execute("INSERT INTO system_marks (key, last_at, ok) VALUES (?, ?, 1) "
                     "ON CONFLICT(key) DO UPDATE SET last_at = excluded.last_at", (key, value))
        conn.commit()


def last_start(job: Job) -> Optional[datetime.datetime]:
    """When the last pass of an ``every=`` job started (aware; None: never)."""
    raw = _read_mark(f"job-start:{job.key}")
    try:
        return datetime.datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def last_day(job: Job) -> Optional[datetime.date]:
    """The local date of the last pass of a ``daily=`` job (None: never)."""
    raw = _read_mark(f"job-day:{job.key}")
    try:
        return datetime.date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def last_run(addon: str, name: str) -> Optional[datetime.datetime]:
    """When job ``<addon>.<name>`` last finished a pass (UTC; None: never)."""
    raw = _read_mark(f"job:{addon}.{name}")
    if not raw:
        return None
    try:
        dt = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


def users_for(addon: str) -> List[User]:
    """Every user who has ``addon`` active (oldest first)."""
    return [u for u in users.list_users() if addon in registry.active_addons(u)]


async def run_once(job: Job) -> Dict[str, Any]:
    """One pass of ``job``: ``func(user)`` for every user who has its addon,
    concurrently, each as that user and within ``job.timeout`` seconds (one
    user's slow service never holds up the others). Returns user id → result
    (a user whose run raised or timed out is logged and left out); nothing
    while the addon is disabled on the server."""
    if not state.is_enabled(job.addon):
        return {}

    async def one(user: User) -> Tuple[str, bool, Any]:
        try:
            with users.acting_as(user):
                return user.id, True, await asyncio.wait_for(job.func(user), timeout=job.timeout)
        except asyncio.TimeoutError:
            logger.error(f"Job {job.key} took longer than {job.timeout:.0f}s for user {user.id}; cancelled")
        except budget.Paused:
            # Background model work waits for the monthly budget (logged once by core/budget.py): not a failure.
            return user.id, True, None
        except Exception:
            logger.exception(f"Job {job.key} failed for user {user.id}")
        return user.id, False, None

    results = await asyncio.gather(*(one(u) for u in users_for(job.addon)))
    failed = sum(1 for _, ok, _ in results if not ok)
    _heartbeat(job, failed == 0, f"{failed} user(s) failed" if failed else "")
    return {uid: value for uid, ok, value in results if ok}


def _daily_time(job: Job) -> Tuple[int, int]:
    hh, mm = (int(x) for x in (job.daily or "00:00").split(":"))
    return hh, mm


def seconds_until_due(job: Job, now: datetime.datetime) -> float:
    """How long until the next pass of an ``every=`` / ``daily=`` job is due
    at ``now`` (0: due now), from its persisted marks."""
    if job.every is not None:
        started = last_start(job)
        if started is None:
            return 0.0
        return max(0.0, (started + job.every - now).total_seconds())
    if last_day(job) == now.date():
        tomorrow = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(1.0, (tomorrow - now).total_seconds())
    hh, mm = _daily_time(job)
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return max(0.0, (at - now).total_seconds())


async def _pass(job: Job) -> bool:
    """Mark and run one scheduled pass of ``job``; whatever it raises is logged.
    False when it was not marked (its addon is disabled, or the mark could not be written)."""
    try:
        if not state.is_enabled(job.addon):
            return False
        now = _now()
        if job.every is not None:
            _write_mark(f"job-start:{job.key}", now.isoformat(timespec="seconds"))
        elif job.daily is not None:
            _write_mark(f"job-day:{job.key}", now.date().isoformat())
    except Exception:
        logger.exception(f"Job {job.key}: its pass could not be marked; trying again later")
        return False
    try:
        await run_once(job)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(f"Job {job.key}: a pass failed; the job goes on")
    return True


async def _run_job(job: Job) -> None:
    if job.delay:
        await _sleep(job.delay)
    if job.at_start:
        await _pass(job)
        return
    while True:
        try:
            wait = seconds_until_due(job, _now())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f"Job {job.key}: its schedule could not be read; trying again")
            wait = DAILY_TICK_SECONDS
        if wait <= 0:
            if not await _pass(job):
                await _sleep(STATE_POLL_SECONDS)
            continue
        # Daily jobs look again every tick (the clock, the zone or a disable may change).
        await _sleep(wait if job.every is not None else min(wait, DAILY_TICK_SECONDS))


async def _until_disabled(service: Service, app: Any) -> bool:
    """Run ``service`` once; True when it was stopped because its addon was disabled."""
    inner = asyncio.ensure_future(service.func(app))
    try:
        while True:
            done, _ = await asyncio.wait({inner}, timeout=STATE_POLL_SECONDS)
            if done:
                inner.result()  # raises what the service raised
                return False
            if not state.is_enabled(service.addon):
                inner.cancel()
                await asyncio.gather(inner, return_exceptions=True)
                logger.info(f"Service {service.key} stopped: its addon is disabled on this server")
                return True
    except asyncio.CancelledError:
        inner.cancel()
        raise


async def _run_service(service: Service, app: Any) -> None:
    backoff = SERVICE_BACKOFF_MIN
    while True:
        if not state.is_enabled(service.addon):
            await asyncio.sleep(STATE_POLL_SECONDS)
            continue
        started = time.monotonic()
        try:
            if await _until_disabled(service, app):
                continue
            logger.warning(f"Service {service.key} returned; starting it again in {backoff:.0f}s")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f"Service {service.key} failed; starting it again in {backoff:.0f}s")
        if time.monotonic() - started >= SERVICE_HEALTHY_SECONDS:
            backoff = SERVICE_BACKOFF_MIN
        await asyncio.sleep(backoff)
        backoff = min(SERVICE_BACKOFF_MAX, backoff * 2)


def start(app: Any) -> List["asyncio.Task[None]"]:
    """Start every job and service of the loaded addons (kept in ``app[APP_KEY]``)."""
    loop = asyncio.get_running_loop()
    tasks = [loop.create_task(_run_job(j), name=f"job-{j.key}") for j in registry.jobs()]
    tasks += [loop.create_task(_run_service(s, app), name=f"service-{s.key}") for s in registry.services()]
    app[APP_KEY] = tasks
    logger.info(f"Addon background work: {len(registry.jobs())} job(s), {len(registry.services())} service(s)")
    return tasks


async def stop(app: Any) -> None:
    """Cancel the tasks :func:`start` started."""
    tasks = app.get(APP_KEY) or []
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
