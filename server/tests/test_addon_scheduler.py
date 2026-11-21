"""The addon scheduler on a fake clock (core/addons/scheduler.py): every= counted from the last start kept across restarts, daily= once a day across restarts and never a day skipped, a per-user timeout, a raising pass that does not end the job's loop.

Run: bin/test test_addon_scheduler

One site addon (``sch``, through DANA_ADDON_DIRS) declares an every= job, a
daily= job and a job that hangs. ``scheduler._now`` / ``scheduler._sleep`` are
replaced by a fake clock: sleeping moves it on, and a test ends a job's loop
by raising from the fake sleep once it has seen enough.
"""

import asyncio
import datetime
import os
import sys
import textwrap
from pathlib import Path
from typing import List

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("addon_scheduler_test")

ROOT = Path(testenv.scratch_path("addon_dirs"))
(ROOT / "sch").mkdir(parents=True)
(ROOT / "sch" / "addon.py").write_text("from core.addons import Addon\naddon = Addon('sch', description='Scheduler test')\n")
(ROOT / "sch" / "hooks.py").write_text(textwrap.dedent('''\
    import asyncio
    from core.addons import minutes
    from addons.sch.addon import addon

    RUNS = []

    @addon.job(every=minutes(10))
    async def tick(user):
        RUNS.append("tick")

    @addon.job(daily="07:30")
    async def morning(user):
        RUNS.append("morning")

    @addon.job(every=minutes(1), timeout=0.2)
    async def hangs(user):
        await asyncio.sleep(3600)
    '''))
os.environ["DANA_ADDON_DIRS"] = str(ROOT)

from core import clock, db, users  # noqa: E402
from core.addons import registry, scheduler, store  # noqa: E402

db.migrate()
users.bootstrap()
from addons.sch import hooks as sch  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


ADMIN = users.first_admin()
assert ADMIN is not None
store.connect(ADMIN, "sch")
TZ = clock.get_timezone()
TICK = registry.job("sch.tick")
MORNING = registry.job("sch.morning")
HANGS = registry.job("sch.hangs")


class Stop(Exception):
    """Raised by the fake sleep to end a job's loop."""


class FakeClock:
    """``scheduler._now`` / ``_sleep``: sleeping moves the clock; ``stop_after`` ends the loop."""

    def __init__(self, start: datetime.datetime, stop_when) -> None:
        self.now = start
        self.sleeps: List[float] = []
        self.runs: List[tuple] = []
        self.stop_when = stop_when

    def time(self) -> datetime.datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.stop_when(self):
            raise Stop()
        self.now += datetime.timedelta(seconds=seconds)


def drive(job, start: datetime.datetime, stop_when) -> FakeClock:
    """Run ``job``'s loop on a fake clock from ``start`` until ``stop_when(clock)``; records (what, when) runs."""
    fake = FakeClock(start, stop_when)
    real_once = scheduler.run_once

    async def recording(j):
        fake.runs.append((j.name, fake.now))
        return await real_once(j)

    scheduler._now, scheduler._sleep, scheduler.run_once = fake.time, fake.sleep, recording
    try:
        asyncio.run(scheduler._run_job(job))
    except Stop:
        pass
    finally:
        scheduler._now, scheduler._sleep, scheduler.run_once = SAVED
    return fake


SAVED = (scheduler._now, scheduler._sleep, scheduler.run_once)
T0 = datetime.datetime(2026, 11, 16, 8, 0, tzinfo=TZ)


def test_every() -> None:
    fake = drive(TICK, T0, lambda f: len(f.runs) >= 2)
    check("every: the first start runs at once, then every interval",
          [w for _, w in fake.runs] == [T0, T0 + datetime.timedelta(minutes=10)], fake.runs)
    check("every: the start of the last pass is kept", scheduler.last_start(TICK) == T0 + datetime.timedelta(minutes=10),
          scheduler.last_start(TICK))
    # A restart 3 minutes after that start waits the 7 minutes left, then runs.
    restart = T0 + datetime.timedelta(minutes=13)
    fake = drive(TICK, restart, lambda f: len(f.runs) >= 1)
    check("every: a restart waits out what is left of the interval (no run at once)",
          fake.sleeps[:1] == [420.0] and [w for _, w in fake.runs] == [T0 + datetime.timedelta(minutes=20)],
          f"{fake.sleeps} {fake.runs}")
    # Down longer than the interval: runs at once.
    late = T0 + datetime.timedelta(hours=2)
    fake = drive(TICK, late, lambda f: len(f.runs) >= 1)
    check("every: after a downtime longer than the interval it runs at once", [w for _, w in fake.runs] == [late],
          fake.runs)


def test_daily() -> None:
    day = T0.replace(hour=8, minute=0)
    fake = drive(MORNING, day, lambda f: len(f.runs) >= 1 or len(f.sleeps) > 5000)
    check("daily: started after its time on a day it did not run: runs at once", [w for _, w in fake.runs] == [day],
          fake.runs)
    check("daily: the day of the pass is kept", scheduler.last_day(MORNING) == day.date())
    # Restarted the same day: no second run that day; the next one is the next day at 07:30.
    same = day.replace(hour=11)
    fake = drive(MORNING, same, lambda f: len(f.runs) >= 1 or len(f.sleeps) > 10000)
    when = [w for _, w in fake.runs]
    check("daily: a restart on the same day does not run it again; the next pass is the next day from 07:30",
          len(when) == 1 and when[0].date() == day.date() + datetime.timedelta(days=1)
          and (when[0].hour, when[0].minute) >= (7, 30) and when[0] - when[0].replace(hour=7, minute=30)
          < datetime.timedelta(minutes=1), when)
    # Down for two days, back before 07:30: waits for 07:30 that day (the missed days are not run twice).
    back = day.replace(hour=6) + datetime.timedelta(days=4)
    fake = drive(MORNING, back, lambda f: len(f.runs) >= 1 or len(f.sleeps) > 10000)
    when = [w for _, w in fake.runs]
    check("daily: back before its time: waits for it that day, runs once",
          len(when) == 1 and when[0].date() == back.date() and (when[0].hour, when[0].minute) >= (7, 30), when)


def test_timeout_and_raising_pass() -> None:
    sch.RUNS.clear()
    out = asyncio.run(scheduler.run_once(HANGS))
    check("timeout: a user's run longer than the job's timeout is cancelled and left out", out == {}, out)
    check("timeout: the heartbeat says a user failed", scheduler.last_run("sch", "hangs") is not None)

    calls = []
    fake_now = {"t": T0 + datetime.timedelta(days=30)}

    async def flaky(job):
        calls.append(fake_now["t"])
        if len(calls) == 1:
            raise RuntimeError("an unexpected bug in a pass")
        return {}

    async def sleep(seconds):
        fake_now["t"] += datetime.timedelta(seconds=seconds)
        if len(calls) >= 2:
            raise Stop()

    scheduler._now, scheduler._sleep, scheduler.run_once = (lambda: fake_now["t"]), sleep, flaky
    try:
        asyncio.run(scheduler._run_job(TICK))
    except Stop:
        pass
    finally:
        scheduler._now, scheduler._sleep, scheduler.run_once = SAVED
    check("a raising pass is logged and the job's loop goes on to its next pass", len(calls) == 2, calls)


test_every()
test_daily()
test_timeout_and_raising_pass()

passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
if passed != len(results):
    sys.exit(1)
