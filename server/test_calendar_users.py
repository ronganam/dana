"""Google Calendar per user (docs/access.md, "Kinds"; docs/now.md; docs/people.md).
Run: python test_calendar_users.py

Google's client is faked where it is built (``integrations.calendar.client.build``):
each refresh token opens its own fake account with its own calendars and
events, and every request is recorded with the token it came with. The first
admin's integration comes from the env import (an 8.1.0 database with a
``selected_calendars`` memory, migrated first: the import picks the selection
up); Bob connects his own account; Carl has no calendar. Checked: the import
and the other migration order (the integration already there); nothing else
reads the old env names or the memory; tools, ``/api/calendar/*``, the
selection, undo of added / changed events, accepting a suggestion (HTTP and
Now, with Now's undo), Now's events and approval card names, the briefing,
meeting cards, the weekly review and the self-check use only the acting
user's calendar with their own token; the credential cache per connection;
Carl: no tools, 403 on every endpoint, composers skip. No network.
"""

import asyncio
import datetime
import json
import os
import pathlib
import re
import sqlite3
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("calendar_users_test")
os.environ["API_TOKEN"] = "cal-users-test-env-token"  # a test value: with it set, DB tokens are honoured
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials
os.environ["GOOGLE_CLIENT_ID"] = "server-client-id"  # the server's OAuth app (fake)
os.environ["GOOGLE_CLIENT_SECRET"] = "server-client-secret"
# The first admin's env values, as production has them (fake).
os.environ["GOOGLE_REFRESH_TOKEN"] = "robin-refresh"
os.environ["GOOGLE_CALENDAR_ID"] = "robin-cal"
os.environ["TIMEZONE"] = "Asia/Jerusalem"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_fixtures", "db_8_1_0.sql")
ROBIN_SELECTION = ["robin-cal", "robin-work"]

# The production database before migrating: 8.1.0 with the calendar picker's memory.
_db = sqlite3.connect(DB_PATH)
with open(FIXTURE, encoding="utf-8") as f:
    _db.executescript(f.read())
_db.execute("INSERT INTO memories (key, value, category, updated_at) VALUES ('selected_calendars', ?, 'preferences', "
            "'2026-11-01 08:00:00')", (json.dumps(ROBIN_SELECTION),))
_db.commit()
_db.close()

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()
from integrations import bootstrap as integrations_bootstrap  # noqa: E402
IMPORTED = integrations_bootstrap.bootstrap()

from integrations.calendar import api as calendar_api  # noqa: E402
from integrations.phone import api as phone_api  # noqa: E402
from core import actions_api, approval_text, briefing, checks, clock, now as now_mod, now_api, undo  # noqa: E402
from core import weekly_review  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from integrations.calendar import client as cal_client, today as cal_today  # noqa: E402
from integrations.google_account import oauth as google_oauth  # noqa: E402
from integrations.framework import registry, store  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


# --- A fake Google Calendar, one account per refresh token ---------------------------------------

class HttpError(Exception):
    pass


class FakeAccount:
    def __init__(self, name: str, calendars: Dict[str, Dict[str, Any]]) -> None:
        self.name = name
        self.calendars = calendars  # id -> {"summary", "primary", "events": {id: event}}
        self.fail = False
        self.seq = 0

    def event(self, cal: str, eid: str) -> Dict[str, Any]:
        ev = self.calendars.get(cal, {}).get("events", {}).get(eid)
        if ev is None:
            raise HttpError(f"<HttpError 404 when requesting {eid}>")
        return ev


NOW = clock.now()
# Starting in a few minutes, but still today (Now / the briefing read today only).
SOON = min(NOW + datetime.timedelta(minutes=5), NOW.replace(hour=23, minute=59, second=59)).isoformat()


def _ev(eid: str, title: str, start: str, **extra: Any) -> Dict[str, Any]:
    return {"id": eid, "summary": title, "start": {"dateTime": start}, "end": {"dateTime": start},
            "updated": "u0", **extra}


ACCOUNTS = {
    "robin-refresh": FakeAccount("robin", {
        "robin-cal": {"summary": "Robin", "primary": True, "events": {"r1": _ev("r1", "Robin standup", SOON)}},
        "robin-work": {"summary": "Robin work", "events": {"r2": _ev("r2", "Robin review", SOON, location="Office")}},
        "robin-other": {"summary": "Robin other (not selected)", "events": {"r3": _ev("r3", "Robin hidden", SOON)}},
    }),
    "bob-refresh": FakeAccount("bob", {
        "primary": {"summary": "Bob", "primary": True, "events": {"b1": _ev("b1", "Bob dentist", SOON)}},
        "bob-family": {"summary": "Bob family", "events": {"b2": _ev("b2", "Bob dinner", SOON)}},
    }),
}
REQUESTS: List[Tuple[str, str, str]] = []  # (account, method, calendar id)
BUILT: List[Any] = []  # the credentials each service was built with


def _when(raw: Optional[str]):
    if not raw:
        return None
    value = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=clock.get_timezone())


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeEvents:
    def __init__(self, acct: FakeAccount) -> None:
        self.a = acct

    def _log(self, method: str, cal: str) -> None:
        REQUESTS.append((self.a.name, method, cal))
        if self.a.fail:
            raise HttpError("invalid_grant: Token has been expired or revoked.")

    def list(self, calendarId: str, **kw: Any) -> _Call:
        def run():
            self._log("events.list", calendarId)
            items = list(self.a.calendars.get(calendarId, {}).get("events", {}).values())
            q = kw.get("q")
            lo, hi = _when(kw.get("timeMin")), _when(kw.get("timeMax"))
            return {"items": [e for e in items if (not q or q.lower() in e["summary"].lower())
                              and (lo is None or _when(e["start"].get("dateTime")) >= lo)
                              and (hi is None or _when(e["start"].get("dateTime")) <= hi)]}
        return _Call(run)

    def get(self, calendarId: str, eventId: str) -> _Call:
        def run():
            self._log("events.get", calendarId)
            return dict(self.a.event(calendarId, eventId))
        return _Call(run)

    def insert(self, calendarId: str, body: Dict[str, Any], **kw: Any) -> _Call:
        def run():
            self._log("events.insert", calendarId)
            self.a.seq += 1
            eid = f"{self.a.name}-new{self.a.seq}"
            ev = {**body, "id": eid, "updated": f"u{self.a.seq}"}
            self.a.calendars.setdefault(calendarId, {"events": {}})["events"][eid] = ev
            return dict(ev)
        return _Call(run)

    def patch(self, calendarId: str, eventId: str, body: Dict[str, Any], **kw: Any) -> _Call:
        def run():
            self._log("events.patch", calendarId)
            ev = self.a.event(calendarId, eventId)
            self.a.seq += 1
            ev.update(body, updated=f"u{self.a.seq}")
            return dict(ev)
        return _Call(run)

    def delete(self, calendarId: str, eventId: str) -> _Call:
        def run():
            self._log("events.delete", calendarId)
            self.a.event(calendarId, eventId)
            del self.a.calendars[calendarId]["events"][eventId]
            return {}
        return _Call(run)


class FakeCalendarList:
    def __init__(self, acct: FakeAccount) -> None:
        self.a = acct

    def list(self) -> _Call:
        def run():
            REQUESTS.append((self.a.name, "calendarList.list", ""))
            if self.a.fail:
                raise HttpError("invalid_grant")
            return {"items": [{"id": cid, "summary": c["summary"], "description": c.get("description"),
                               **({"primary": True} if c.get("primary") else {})} for cid, c in self.a.calendars.items()]}
        return _Call(run)


class FakeService:
    def __init__(self, acct: FakeAccount) -> None:
        self.a = acct

    def events(self) -> FakeEvents:
        return FakeEvents(self.a)

    def calendarList(self) -> FakeCalendarList:
        return FakeCalendarList(self.a)


def fake_build(name: str, version: str, credentials: Any = None, **kw: Any) -> FakeService:
    assert (name, version) == ("calendar", "v3")
    BUILT.append(credentials)
    if ACCOUNTS[credentials.refresh_token].fail:
        raise HttpError("invalid_grant: Token has been expired or revoked.")
    if credentials.client_id != "server-client-id" or credentials.client_secret != "server-client-secret":
        raise AssertionError("not the server's OAuth client")
    return FakeService(ACCOUNTS[credentials.refresh_token])


cal_client.build = fake_build


def requests() -> List[Tuple[str, str, str]]:
    out = list(REQUESTS)
    REQUESTS.clear()
    return out


def accounts_hit() -> set:
    return {r[0] for r in requests()}


ADMIN = users.first_admin()
assert ADMIN is not None
BOB = users.create("Bob Levi", "member")
CARL = users.create("Carl Cohen", "member")
BOB_TOKEN, _ = users.issue_token(BOB.id, "test")
CARL_TOKEN, _ = users.issue_token(CARL.id, "test")
AUTH = {ADMIN.id: {"Authorization": f"Bearer {os.environ['API_TOKEN']}"},
        BOB.id: {"Authorization": f"Bearer {BOB_TOKEN}"},
        CARL.id: {"Authorization": f"Bearer {CARL_TOKEN}"}}
NOT_CONNECTED = {"error": "not_connected", "integration": "calendar"}


# --- 1. The env import, the selection's migration and the old names -------------------------------

def test_import() -> None:
    cal = store.require(ADMIN, "calendar")
    check("import: refresh token and default calendar from env, the selection from the migrated memory",
          "calendar" in IMPORTED and dict(cal.credentials) == {"refresh_token": "robin-refresh"}
          and dict(cal.settings) == {"calendar_id": "robin-cal", "selected_calendars": ROBIN_SELECTION},
          (IMPORTED, dict(cal.settings)))
    with get_db_connection() as conn:
        waiting = conn.execute("SELECT COUNT(*) FROM integration_import_settings").fetchone()[0]
        memory = conn.execute("SELECT 1 FROM memories WHERE key = 'selected_calendars'").fetchone()
    check("import: the waiting selection was consumed and the memory is gone", waiting == 0 and memory is None)

    # The other order: the integration exists when the migration moves the memory.
    store.update(ADMIN, "calendar", settings={"selected_calendars": None})
    with get_db_connection() as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version = 12")
        conn.execute("DROP TABLE integration_import_settings")
        conn.execute("INSERT INTO memories (key, value, category) VALUES ('selected_calendars', ?, 'preferences')",
                     (json.dumps(["robin-cal"]),))
        conn.commit()
    db.migrate()
    store.invalidate()
    check("migration: an existing integration gets the selection directly",
          store.require(ADMIN, "calendar").settings["selected_calendars"] == ["robin-cal"])
    store.update(ADMIN, "calendar", settings={"selected_calendars": ROBIN_SELECTION})

    root = pathlib.Path(__file__).parent
    old_names = ("GOOGLE_REFRESH_TOKEN", "GOOGLE_CALENDAR_ID")
    readers = []
    memory_readers = []
    for path in list((root / "core").rglob("*.py")) + list((root / "integrations").rglob("*.py")) + [root / "main.py"]:
        text = path.read_text(encoding="utf-8")
        if not (path.name == "bootstrap.py" and path.parent.name == "integrations"):
            readers += [f"{path.relative_to(root)}: {n}" for n in old_names if re.search(rf'["\']{n}["\']', text)]
        if path.parent.name != "migrations" and re.search(r"key\s*=\s*'selected_calendars'", text):
            memory_readers.append(str(path.relative_to(root)))
    check("old env names: read only by the env import", not readers, readers)
    check("the selected_calendars memory: read only by its migration", not memory_readers, memory_readers)


# --- 2. Tools ---------------------------------------------------------------------------------------

async def run_tool(user, name: str, **args: Any) -> Tuple[str, List[str]]:
    with users.acting_as(user), undo.collect() as ids:
        out = await registry.execute(user, name, args)
    return str(out), [i["id"] for i in ids]


async def undo_as(user, undo_id: str) -> None:
    with users.acting_as(user):
        await undo.run(undo_id)


async def test_tools() -> None:
    store.connect(BOB, "calendar", credentials={"refresh_token": "bob-refresh"})
    requests()
    out_a, _ = await run_tool(ADMIN, "list_calendars")
    hit_a = accounts_hit()
    out_b, _ = await run_tool(BOB, "list_calendars")
    hit_b = accounts_hit()
    check("list_calendars: each user's own calendars and selection",
          "Robin work" in out_a and "Bob" not in out_a and "ID: robin-other\n  Primary (personal): No\n  Searched by default: No"
          in out_a and "ID: robin-work\n  Primary (personal): No\n  Searched by default: Yes" in out_a
          and "Bob family" in out_b and "Robin" not in out_b and "ID: primary\n  Primary (personal): Yes\n  Searched by "
          "default: Yes" in out_b and "ID: bob-family\n  Primary (personal): No\n  Searched by default: No" in out_b,
          (out_a, out_b))
    check("list_calendars: only the user's own account was asked", hit_a == {"robin"} and hit_b == {"bob"}, (hit_a, hit_b))

    out_a, _ = await run_tool(ADMIN, "search_calendar")
    hit_a = requests()
    out_b, _ = await run_tool(BOB, "search_calendar")
    hit_b = requests()
    check("search_calendar: the admin's selected calendars only (not the unselected one, nothing of Bob's)",
          "Robin standup" in out_a and "Robin review" in out_a and "Robin hidden" not in out_a and "Bob" not in out_a, out_a)
    check("search_calendar: Bob's default calendar (no selection), nothing of Robin's",
          "Bob dentist" in out_b and "Bob dinner" not in out_b and "Robin" not in out_b, out_b)
    check("search_calendar: requests went to each user's own account",
          {r[0] for r in hit_a} == {"robin"} and {(r[1], r[2]) for r in hit_a if r[1] == "events.list"}
          == {("events.list", "robin-cal"), ("events.list", "robin-work")}
          and {r[0] for r in hit_b} == {"bob"}, (hit_a, hit_b))

    out, ids = await run_tool(BOB, "add_event", summary="Bob gym", start_time="2030-01-02T10:00:00",
                              end_time="2030-01-02T11:00:00")
    hit = requests()
    new_id = re.search(r"ID: (\S+?)\.$", out).group(1) if "ID:" in out else ""
    check("add_event: Bob's event in Bob's default calendar, with an undo",
          new_id in ACCOUNTS["bob-refresh"].calendars["primary"]["events"] and len(ids) == 1
          and hit == [("bob", "events.insert", "primary")], (out, hit))
    with users.acting_as(ADMIN):
        try:
            await undo.run(ids[0])
            refused = False
        except KeyError:
            refused = True
    check("undo: the admin cannot undo Bob's add_event", refused and new_id in ACCOUNTS["bob-refresh"].calendars["primary"]["events"])
    with users.acting_as(BOB):
        await undo.run(ids[0])
    hit = requests()
    check("undo: Bob's undo deletes it from Bob's calendar only",
          new_id not in ACCOUNTS["bob-refresh"].calendars["primary"]["events"] and {r[0] for r in hit} == {"bob"}, hit)

    out, ids = await run_tool(ADMIN, "add_event", summary="Robin lunch", start_time="2030-01-02T12:00:00",
                              end_time="2030-01-02T13:00:00")
    robin_new = re.search(r"ID: (\S+?)\.$", out).group(1)
    check("add_event: the admin's goes to the calendar of his setting (robin-cal), as before",
          robin_new in ACCOUNTS["robin-refresh"].calendars["robin-cal"]["events"] and requests() == [("robin", "events.insert", "robin-cal")])
    out, upd = await run_tool(ADMIN, "update_event", event_id=robin_new, summary="Robin long lunch")
    check("update_event: changed in the admin's calendar, with an undo",
          ACCOUNTS["robin-refresh"].calendars["robin-cal"]["events"][robin_new]["summary"] == "Robin long lunch" and len(upd) == 1)
    # The default calendar changes later: undo still targets the calendar the event was written to.
    store.update(ADMIN, "calendar", settings={"calendar_id": "robin-work"})
    requests()
    await undo_as(ADMIN, upd[0])
    hit = requests()
    check("undo: the change is reversed in the calendar it happened in",
          ACCOUNTS["robin-refresh"].calendars["robin-cal"]["events"][robin_new]["summary"] == "Robin lunch"
          and hit and all(r[:1] == ("robin",) and r[2] == "robin-cal" for r in hit), hit)
    store.update(ADMIN, "calendar", settings={"calendar_id": "robin-cal"})
    out, ids = await run_tool(ADMIN, "add_event", summary="Robin tea", start_time="2030-01-03T12:00:00",
                              end_time="2030-01-03T13:00:00")
    tea = re.search(r"ID: (\S+?)\.$", out).group(1)
    store.update(ADMIN, "calendar", settings={"calendar_id": "robin-work"})
    requests()
    await undo_as(ADMIN, ids[0])
    hit = requests()
    check("undo: the add is reversed in the calendar it happened in",
          tea not in ACCOUNTS["robin-refresh"].calendars["robin-cal"]["events"]
          and hit and all(r[:1] == ("robin",) and r[2] == "robin-cal" for r in hit), hit)
    store.update(ADMIN, "calendar", settings={"calendar_id": "robin-cal"})
    del ACCOUNTS["robin-refresh"].calendars["robin-cal"]["events"][robin_new]

    out, _ = await run_tool(BOB, "delete_event", event_id="r1")
    check("delete_event: Bob cannot reach the admin's event id (not found in his calendar)",
          out.startswith("Error") and "r1" in ACCOUNTS["robin-refresh"].calendars["robin-cal"]["events"]
          and {r[0] for r in requests()} == {"bob"}, out)

    names = {s["function"]["name"] for s in registry.tool_schemas(CARL)}
    check("Carl: no calendar tools offered", not names & {"list_calendars", "search_calendar", "add_event",
                                                           "update_event", "delete_event"}, names)
    try:
        await run_tool(CARL, "search_calendar")
        refused = False
    except registry.ToolNotAvailable:
        refused = True
    check("Carl: a calendar tool call is refused like an unknown tool, Google not asked", refused and not requests())


# --- 3. Token cache per connection ---------------------------------------------------------------

async def test_credentials_cache() -> None:
    sigs = {(s[0], s[2]) for s in google_oauth._credentials}
    check("credentials: one cache entry per connection, each with its own refresh token",
          sigs == {(ADMIN.id, "robin-refresh"), (BOB.id, "bob-refresh")}, sigs)
    a1 = google_oauth.credentials(store.require(ADMIN, "calendar"))
    a2 = google_oauth.credentials(store.require(ADMIN, "calendar"))
    b = google_oauth.credentials(store.require(BOB, "calendar"))
    check("credentials: reused for the same connection (access token kept), never shared",
          a1 is a2 and a1 is not b and a1.refresh_token == "robin-refresh" and b.refresh_token == "bob-refresh")
    check("credentials: every service so far was built with the acting user's own token",
          {c.refresh_token for c in BUILT} == {"robin-refresh", "bob-refresh"})
    ACCOUNTS["bob-refresh-2"] = ACCOUNTS["bob-refresh"]
    store.update(BOB, "calendar", credentials={"refresh_token": "bob-refresh-2"})
    b2 = google_oauth.credentials(store.require(BOB, "calendar"))
    check("credentials: a new refresh token starts over and drops the old entry",
          b2 is not b and b2.refresh_token == "bob-refresh-2" and (BOB.id, "calendar", "bob-refresh") not in google_oauth._credentials
          and (ADMIN.id, "calendar", "robin-refresh") in google_oauth._credentials)
    store.update(BOB, "calendar", credentials={"refresh_token": "bob-refresh"})


# --- 4. HTTP ------------------------------------------------------------------------------------

def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    now_api.setup(app)
    actions_api.setup(app)
    r = app.router
    r.add_get("/api/calendar/calendars", calendar_api.get_calendar_calendars_api)
    r.add_post("/api/calendar/selections", calendar_api.save_calendar_selections_api)
    r.add_get("/api/calendar/events", calendar_api.get_calendar_events_api)
    r.add_post("/api/suggestions/{id}/accept", phone_api.accept_suggestion_api)
    return app


def event_suggestion(title: str, owner) -> str:
    sid = str(uuid.uuid4())
    payload = {"title": title, "start": "2030-01-05 10:00", "end": "2030-01-05 11:00"}
    with get_db_connection() as conn:
        conn.execute("INSERT INTO call_suggestions (id, user_id, call_id, source_type, kind, payload, status) "
                     "VALUES (?, ?, 'call_x', 'call', 'event', ?, 'pending')", (sid, owner.id, json.dumps(payload)))
        conn.commit()
    return sid


def titles(account: str) -> List[str]:
    return sorted(e["summary"] for c in ACCOUNTS[account].calendars.values() for e in c["events"].values())


async def test_http() -> None:
    async with TestClient(TestServer(build_app())) as http:
        async def get(user, path):
            r = await http.get(path, headers=AUTH[user.id])
            return r.status, await r.json()

        requests()
        status, body = await get(ADMIN, "/api/calendar/calendars")
        check("GET /api/calendar/calendars: the admin's calendars, payload as before",
              status == 200 and body == [
                  {"id": "robin-cal", "summary": "Robin", "description": None, "primary": True, "selected": True},
                  {"id": "robin-work", "summary": "Robin work", "description": None, "primary": False, "selected": True},
                  {"id": "robin-other", "summary": "Robin other (not selected)", "description": None, "primary": False,
                   "selected": False}], body)
        status, body = await get(BOB, "/api/calendar/calendars")
        check("GET /api/calendar/calendars: Bob's, nothing chosen → his primary selected",
              status == 200 and [(c["id"], c["selected"]) for c in body] == [("primary", True), ("bob-family", False)],
              body)
        check("GET /api/calendar/calendars: each from their own account",
              [r[0] for r in requests()] == ["robin", "bob"])

        r = await http.post("/api/calendar/selections", json={"calendar_ids": ["bob-family"]}, headers=AUTH[BOB.id])
        body = await r.json()
        check("POST /api/calendar/selections: saved for Bob, payload as before",
              r.status == 200 and body == {"status": "saved", "calendar_ids": ["bob-family"]}
              and store.require(BOB, "calendar").settings["selected_calendars"] == ["bob-family"]
              and store.require(ADMIN, "calendar").settings["selected_calendars"] == ROBIN_SELECTION, body)
        r = await http.post("/api/calendar/selections", json={"calendar_ids": "x"}, headers=AUTH[BOB.id])
        check("POST /api/calendar/selections: not a list → 400 as before", r.status == 400)

        status, body = await get(ADMIN, "/api/calendar/events?start_date=2026-01-01T00:00:00&end_date=2031-01-01T00:00:00")
        check("GET /api/calendar/events: the admin's selected calendars, payload fields as before",
              status == 200 and sorted(e["summary"] for e in body) == ["Robin review", "Robin standup"]
              and body[0].keys() == {"id", "calendar_id", "summary", "start", "end", "all_day", "location",
                                     "description", "attendees"}
              and {(e["id"], e["calendar_id"], e["location"]) for e in body}
              == {("r1", "robin-cal", None), ("r2", "robin-work", "Office")}, body)
        status, body = await get(BOB, "/api/calendar/events")
        check("GET /api/calendar/events: Bob's new selection (family only), nothing of Robin's",
              status == 200 and [e["summary"] for e in body] == ["Bob dinner"], body)
        requests()
        status, body = await get(BOB, "/api/calendar/events?calendars=robin-cal")
        hit = requests()
        check("GET /api/calendar/events?calendars=<the admin's id>: asked of Bob's own account, nothing of Robin's",
              status == 200 and body == [] and hit == [("bob", "events.list", "robin-cal")], (body, hit))
        status, body = await get(ADMIN, "/api/calendar/events?start_date=nope")
        check("GET /api/calendar/events: a malformed date → 500 with the error, as before",
              status == 500 and "error" in body)

        for method, path in (("GET", "/api/calendar/calendars"), ("POST", "/api/calendar/selections"),
                             ("GET", "/api/calendar/events")):
            r = await http.request(method, path, headers=AUTH[CARL.id], json={"calendar_ids": []})
            check(f"Carl: {method} {path} → 403 not_connected", r.status == 403 and await r.json() == NOT_CONNECTED)
        check("Carl: Google never asked", not requests())

        # Accepting an event suggestion (the owner's, who has phone): the acting user's calendar.
        store.connect(BOB, "phone")
        store.connect(CARL, "phone")
        sid = event_suggestion("Bob call follow-up", BOB)
        before_robin = titles("robin-refresh")
        r = await http.post(f"/api/suggestions/{sid}/accept", json={}, headers=AUTH[BOB.id])
        body = await r.json()
        check("suggestion accept: Bob's → in Bob's default calendar, nothing in Robin's",
              r.status == 200 and body.get("kind") == "event" and "Bob call follow-up" in titles("bob-refresh")
              and titles("robin-refresh") == before_robin, body)
        sid = event_suggestion("Carl thing", CARL)
        r = await http.post(f"/api/suggestions/{sid}/accept", json={}, headers=AUTH[CARL.id])
        check("suggestion accept: Carl → 403 not_connected, still pending",
              r.status == 403 and await r.json() == NOT_CONNECTED)
        sid = event_suggestion("Bob now thing", BOB)

        r = await http.post("/api/now/act", json={"id": f"sug:{sid}", "action": "accept"}, headers=AUTH[BOB.id])
        body = await r.json()
        check("Now accept: Bob's → in Bob's calendar, with an undo",
              r.status == 200 and body.get("undo_id") and "Bob now thing" in titles("bob-refresh"), body)
        r = await http.post(f"/api/undo/{body['undo_id']}", headers=AUTH[ADMIN.id])
        check("Now accept undo: another user's undo id → 404, event kept",
              r.status == 404 and "Bob now thing" in titles("bob-refresh"))
        requests()
        r = await http.post(f"/api/undo/{body['undo_id']}", headers=AUTH[BOB.id])
        check("Now accept undo: Bob's undo removes it from Bob's calendar (his account only)",
              r.status == 200 and "Bob now thing" not in titles("bob-refresh")
              and {x[0] for x in requests()} == {"bob"}, await r.json())

        # Now: today's events of the caller.
        store.update(BOB, "calendar", settings={"selected_calendars": None})
        r = await http.get("/api/now", headers=AUTH[ADMIN.id])
        p_admin = await r.json()
        r = await http.get("/api/now", headers=AUTH[BOB.id])
        p_bob = await r.json()
        r = await http.get("/api/now", headers=AUTH[CARL.id])
        p_carl = await r.json()
        check("Now: the day strip has calendar data for the admin and Bob, none for Carl",
              p_admin["day"]["calendar"] is True and p_bob["day"]["calendar"] is True
              and p_carl["day"]["calendar"] is False, (p_admin["day"]["calendar"], p_carl["day"]["calendar"]))


# --- 5. Composers -------------------------------------------------------------------------------

async def test_composers() -> None:
    requests()
    with users.acting_as(ADMIN):
        ev_admin = await now_mod.calendar_today(ADMIN, wait=2)
        name_admin = approval_text.describe("delete_event", {"event_id": "r1"})["title"]
    with users.acting_as(BOB):
        ev_bob = await now_mod.calendar_today(BOB, wait=2)
        name_bob = approval_text.describe("delete_event", {"event_id": "r1"})["title"]
    with users.acting_as(CARL):
        ev_carl = await now_mod.calendar_today(CARL, wait=2)
    check("Now events: each user's own today (the admin's selection, Bob's default), Carl none",
          sorted(e["title"] for e in ev_admin or []) == ["Robin review", "Robin standup"]
          and [e["title"] for e in ev_bob or []] == ["Bob dentist"] and ev_carl is None, (ev_admin, ev_bob))
    check("approval card: an event name only from the user's own cache",
          name_admin == "Delete “Robin standup” from the calendar" and "Robin standup" not in name_bob, (name_admin, name_bob))
    keys = {k[0] for k in cal_today._cache}
    check("Now events cache: one entry per connection", keys == {(ADMIN.id, "robin-refresh"), (BOB.id, "bob-refresh")}, keys)

    requests()
    with users.acting_as(BOB):
        brief_bob = await briefing._events_today(BOB)
        week_bob = await weekly_review.next_week(BOB)
    hit = requests()
    with users.acting_as(CARL):
        brief_carl = await briefing._events_today(CARL)
        week_carl = await weekly_review.next_week(CARL)
    check("briefing / weekly review: Bob's own calendar only",
          [e["title"] for e in brief_bob or []] == ["Bob dentist"] and week_bob is not None
          and hit and all(r[0] == "bob" for r in hit), (brief_bob, hit))
    check("briefing / weekly review: skipped without a calendar (no Google call)",
          brief_carl is None and week_carl is None and not requests())

    with patch.object(briefing.phone_push, "send", AsyncMock()):
        with users.acting_as(BOB):
            await briefing.check_meetings(BOB)
        hit = requests()
        with users.acting_as(CARL):
            sent = await briefing.check_meetings(CARL)
        check("meeting cards: Bob's run reads only Bob's calendar; Carl's sends nothing and asks no one",
              hit and all(r[0] == "bob" for r in hit) and sent == 0 and not requests(), hit)

    # The self-check: per user.
    ACCOUNTS["bob-refresh"].fail = True
    await cal_today.refresh_now(BOB)
    await cal_today.refresh_now(ADMIN)
    ACCOUNTS["bob-refresh"].fail = False
    by = {u.id: {c["key"]: c for c in checks.compute(user=u)}["calendar"] for u in (ADMIN, BOB, CARL)}
    check("self-check: Bob's failing calendar is his, the admin's is fine, Carl not connected",
          by[ADMIN.id]["ok"] and not by[BOB.id]["ok"] and by[CARL.id]["ok"]
          and by[CARL.id]["detail"] == "not connected", by)


async def main() -> None:
    test_import()
    await test_tools()
    await test_credentials_cache()
    await test_http()
    await test_composers()


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
