"""Running the addons' background work: ``@addon.job`` and ``@addon.service``
(../docs/addons.md, "The manifest").

:func:`start` (core/server.py, once the web server listens) starts one task
per job and per service of the loaded addons:

- a job runs ``func(user)`` for every user who has its addon (oldest user
  first), each inside ``users.acting_as(user)``; one user's failure is logged
  and the next user still runs. ``every``: a pass, then a sleep of the
  interval; ``daily="HH:MM"``: one pass a day
  from that local time on (also on the day the server starts after it);
  ``at_start``: one pass when the server starts (``delay``: seconds to wait
  before the first pass of any job). Every pass writes the job's
  heartbeat (``system_marks['job:<addon>.<name>']``), read with
  :func:`last_run` (the ``tasks`` addon's "Reminder checks" self-check).
- a service runs ``func(app)`` once; when it returns or raises it is started
  again after a backoff (1 s doubling to 5 min, back to 1 s after it ran for
  10 minutes).

Every task is cancelled by :func:`stop` (the app's cleanup).
"""

import asyncio
import datetime
import logging
import time
from typing import Any, Dict, List, Optional

from core import clock, users
from core.db import get_db_connection
from core.users import User
from core.addons import registry, store
from core.addons.hooks import Job, Service

logger = logging.getLogger(__name__)

SERVICE_BACKOFF_MIN = 1.0
SERVICE_BACKOFF_MAX = 300.0
SERVICE_HEALTHY_SECONDS = 600.0
DAILY_TICK_SECONDS = 30.0
APP_KEY = "addon_tasks"


def _mark_key(job: Job) -> str:
    return f"job:{job.key}"


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


def last_run(addon: str, name: str) -> Optional[datetime.datetime]:
    """When job ``<addon>.<name>`` last finished a pass (UTC; None: never)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT last_at FROM system_marks WHERE key = ?", (f"job:{addon}.{name}",)).fetchone()
    if not row or not row["last_at"]:
        return None
    try:
        dt = datetime.datetime.fromisoformat(row["last_at"])
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


def users_for(addon: str) -> List[User]:
    """Every user who has ``addon`` (oldest first)."""
    return [u for u in users.list_users() if store.is_connected(u, addon)]


async def run_once(job: Job) -> Dict[str, Any]:
    """One pass of ``job``: ``func(user)`` for every user who has its addon,
    each as that user. Returns user id → result (a user whose run raised is
    logged and left out)."""
    out: Dict[str, Any] = {}
    failed = 0
    for user in users_for(job.addon):
        try:
            with users.acting_as(user):
                out[user.id] = await job.func(user)
        except Exception:
            failed += 1
            logger.exception(f"Job {job.key} failed for user {user.id}")
    _heartbeat(job, failed == 0, f"{failed} user(s) failed" if failed else "")
    return out


def _daily_due(job: Job, now: datetime.datetime) -> bool:
    hh, mm = (int(x) for x in (job.daily or "00:00").split(":"))
    return (now.hour, now.minute) >= (hh, mm)


async def _run_job(job: Job) -> None:
    if job.delay:
        await asyncio.sleep(job.delay)
    if job.at_start:
        await run_once(job)
        return
    if job.every is not None:
        while True:
            await run_once(job)
            await asyncio.sleep(job.every.total_seconds())
    last_day: Optional[datetime.date] = None
    while True:
        now = clock.now()
        if last_day != now.date() and _daily_due(job, now):
            last_day = now.date()
            await run_once(job)
        await asyncio.sleep(DAILY_TICK_SECONDS)


async def _run_service(service: Service, app: Any) -> None:
    backoff = SERVICE_BACKOFF_MIN
    while True:
        started = time.monotonic()
        try:
            await service.func(app)
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
