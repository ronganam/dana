"""Calendar events for the catalog (addons/calendar/event_types.py): calendar.event.starting once per event start per user about 10 minutes ahead — the data, not for all-day / started / far events, once across restarts, again for a moved event, two users isolated, history from what was emitted, the prune.

Google's client is faked where it is built (``addons.calendar.client.service``):
each refresh token opens its own fake account (Ann's, Bob's); Carl has no
calendar. The job's function is called with explicit times; a tap collects
what is emitted. Every name, title and address is made up. No network.

Run: bin/test test_calendar_events
"""

import asyncio
import datetime
import os
import sys
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("calendar_events_test")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials
os.environ["TIMEZONE"] = "Asia/Jerusalem"

from core import clock, db, events, users  # noqa: E402
from core.addons import registry, store  # noqa: E402
from core.addons.addon import get_addon  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.users import User  # noqa: E402
from addons.calendar import client as cal_client, event_types  # noqa: E402

db.migrate()
users.bootstrap()

results: List[bool] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


NOW = clock.now().replace(second=0, microsecond=0)


def at(minutes: float) -> str:
    return (NOW + datetime.timedelta(minutes=minutes)).isoformat()


def _when(raw: str) -> datetime.datetime:
    value = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=clock.get_timezone())


# --- A fake Google Calendar, one account per refresh token ---------------------------------------

ACCOUNTS: Dict[str, Dict[str, Dict[str, Any]]] = {  # token -> event id -> Google event
    "ann-refresh": {
        "e1": {"id": "e1", "summary": "Deck review with Moti", "start": {"dateTime": at(8)},
               "end": {"dateTime": at(38)}, "location": "Room Example 2",
               "description": "<p>Bring   the deck</p>\n<br>and the numbers",
               "attendees": [{"email": "ann@example.com", "self": True},
                             {"email": "moti@example.com", "displayName": "Moti Example"},
                             {"email": "gal@example.com"}]},
        "e2": {"id": "e2", "summary": "Lunch", "start": {"dateTime": at(30)}, "end": {"dateTime": at(60)}},
        "e3": {"id": "e3", "summary": "Already started", "start": {"dateTime": at(-2)}, "end": {"dateTime": at(28)}},
        "e4": {"id": "e4", "summary": "Holiday", "start": {"date": NOW.date().isoformat()},
               "end": {"date": (NOW.date() + datetime.timedelta(days=1)).isoformat()}},
        "e5": {"id": "e5", "summary": "Declined", "start": {"dateTime": at(6)}, "end": {"dateTime": at(20)},
               "attendees": [{"email": "ann@example.com", "self": True, "responseStatus": "declined"}]},
    },
    "bob-refresh": {
        "b1": {"id": "b1", "summary": "Dentist", "start": {"dateTime": at(5)}, "end": {"dateTime": at(35)}},
    },
}
FAILING: set = set()
READS: List[str] = []


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeEvents:
    def __init__(self, token: str) -> None:
        self.token = token

    def list(self, calendarId: str, **kw: Any) -> _Call:
        def run():
            READS.append(self.token)
            if self.token in FAILING:
                raise RuntimeError("backend error")
            lo, hi = _when(kw["timeMin"]), _when(kw["timeMax"])

            def bound(ev, key):
                raw = ev[key].get("dateTime") or ev[key]["date"]
                return _when(raw)
            # As Google: events overlapping [timeMin, timeMax).
            return {"items": [dict(e) for e in ACCOUNTS[self.token].values() if bound(e, "end") > lo
                              and bound(e, "start") < hi]}
        return _Call(run)


class FakeService:
    def __init__(self, token: str) -> None:
        self.token = token

    def events(self) -> FakeEvents:
        return FakeEvents(self.token)


cal_client.service = lambda conn: FakeService(conn.credentials["refresh_token"])

ANN = users.create("Ann")
BOB = users.create("Bob")
CARL = users.create("Carl")
store.connect(ANN, "calendar", credentials={"refresh_token": "ann-refresh"})
store.connect(BOB, "calendar", credentials={"refresh_token": "bob-refresh"})
if store.is_connected(CARL, "calendar"):
    store.disconnect(CARL, "calendar")

SEEN: List[Tuple[str, str, str, Dict[str, Any]]] = []  # (event, user id, acting user id, data)


async def collect(name: str, user: User, data: Dict[str, Any]) -> None:
    if name.startswith("calendar."):
        SEEN.append((name, user.id, users.current().id, data))


async def run(user: User, minutes: float = 0) -> Tuple[int, List[Tuple[str, str, str, Dict[str, Any]]]]:
    SEEN.clear()
    n = await event_types.announce_starting(user, NOW + datetime.timedelta(minutes=minutes))
    await events.drain()
    return n, list(SEEN)


def local(minutes: float) -> str:
    return (NOW + datetime.timedelta(minutes=minutes)).isoformat(timespec="seconds")


async def test_starting() -> None:
    n, got = await run(ANN)
    check("Ann: one event, the one starting in 8 minutes (not later, started, all-day or declined), as Ann",
          n == 1 and [(e, u, acting) for e, u, acting, _ in got] == [("calendar.event.starting", ANN.id, ANN.id)],
          got)
    data = got[0][3] if got else {}
    check("data: the contract's fields (local ISO times, attendees joined without the user, a short plain description)",
          data == {"event_id": "e1", "title": "Deck review with Moti", "start": local(8), "end": local(38),
                   "location": "Room Example 2", "attendees": "Moti Example, gal@example.com", "minutes_before": 8,
                   "description": "Bring the deck and the numbers"}, data)

    n, got = await run(ANN, 1)
    check("a minute later: nothing again", n == 0 and got == [], got)
    event_types._cache.clear()  # as after a restart
    reads = len(READS)
    n, got = await run(ANN, 2)
    check("after a restart: read again, still not announced twice", n == 0 and got == [] and len(READS) > reads, got)

    reads = len(READS)
    n, got = await run(ANN, 21)
    check("lunch 9 minutes ahead: announced (from the cached read), minutes_before 9",
          n == 1 and got[0][3]["event_id"] == "e2" and got[0][3]["minutes_before"] == 9 and len(READS) == reads, got)

    ACCOUNTS["ann-refresh"]["e1"]["start"] = {"dateTime": at(45)}
    ACCOUNTS["ann-refresh"]["e1"]["end"] = {"dateTime": at(75)}
    event_types._cache.clear()
    n, got = await run(ANN, 35)
    check("a moved event: announced again for its new start",
          n == 1 and (got[0][3]["event_id"], got[0][3]["start"], got[0][3]["minutes_before"]) == ("e1", local(45), 10),
          got)

    n, got = await run(BOB)
    check("Bob: his own event only, as Bob", n == 1 and [(u, acting, d["event_id"]) for _, u, acting, d in got]
          == [(BOB.id, BOB.id, "b1")], got)
    reads = len(READS)
    n, got = await run(CARL)
    check("Carl (no calendar): nothing, nothing read", n == 0 and got == [] and len(READS) == reads)

    FAILING.add("bob-refresh")
    event_types._cache.clear()
    try:
        n, got = await run(BOB, 1)
    finally:
        FAILING.discard("bob-refresh")
    check("a failing read: no exception, nothing emitted", n == 0 and got == [], got)


def test_history_and_prune() -> None:
    since = NOW - datetime.timedelta(hours=1)
    ann = event_types.starting_history(ANN, since)
    check("history: Ann's announced starts as emitted, aware times",
          [(d["event_id"], d["start"]) for _, d in ann] == [("e1", local(8)), ("e2", local(30)), ("e1", local(45))]
          and all(t.tzinfo for t, _ in ann), ann)
    check("history: Bob's own only", [d["event_id"] for _, d in event_types.starting_history(BOB, since)] == ["b1"])
    pruned = event_types.prune(ANN, NOW + datetime.timedelta(days=31))
    with get_db_connection() as conn:
        left = {r["user_id"] for r in conn.execute("SELECT user_id FROM calendar_starting WHERE user_id IN (?, ?)",
                                                   (ANN.id, BOB.id))}
    check("prune: Ann's older than 30 days gone, Bob's kept", pruned == 3 and left == {BOB.id}, (pruned, left))


def test_catalog() -> None:
    ann = {e.name: e for e in registry.event_types(ANN)}
    carl = {e.name for e in registry.event_types(CARL)}
    et = ann.get("calendar.event.starting")
    check("catalog: calendar.event.starting for a user with the calendar, with history and its fields",
          et is not None and et.history is not None and set(et.fields) == {
              "event_id", "title", "start", "end", "location", "attendees", "minutes_before", "description"},
          list(ann))
    check("catalog: none for a user without the calendar", "calendar.event.starting" not in carl, carl)
    jobs = {j.name: j for j in get_addon("calendar").jobs}
    check("jobs: starting every minute, its prune daily",
          "starting" in jobs and jobs["starting"].every == datetime.timedelta(minutes=1)
          and jobs.get("prune_starting") is not None and jobs["prune_starting"].daily, list(jobs))


async def main() -> None:
    events.tap(collect)
    try:
        await test_starting()
        test_history_and_prune()
        test_catalog()
    finally:
        events.untap(collect)


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
if not all(results):
    sys.exit(1)
