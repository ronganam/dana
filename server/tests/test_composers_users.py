"""The composers per user: Now, the wake flow, the briefing, meeting cards,
the weekly review, search, the self-checks, context chats and the dashboard's
endpoints.

The first admin (converted from the 9.0 fixture) has every addon; Bob has
phone, tasks, people, knowledge, Home Assistant and calendar; Carl has tasks
and calendar and a Telegram chat; Eve has nothing. Each composer answers for
its user only, from the addons they have connected (a missing addon adds
nothing; an action that needs it is 403 ``not_connected``); state (hidden
cards, wake dates, briefings, reviews) is per user; every loop runs per
eligible user, as that user (usage rows theirs), and one failing user does
not stop the next; the first admin's prompts are byte for byte the old ones.

Home Assistant and Google Calendar are faked at their client functions (per
connection), FCM at httpx, Telegram at ``notify``'s channel; the model is
LiteLLM's offline ``mock_response`` (so the real usage callback records each
call).

Run: bin/test test_composers_users
"""

import asyncio
import datetime
import inspect
import json
import os
import sqlite3
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("composers_users_test")
ROOT = str(testenv.SERVER)
os.environ["API_TOKEN"] = "composers-users-test-env-token"  # a test value: with it set, DB tokens are honoured
os.environ["ANTHROPIC_API_KEY"] = "test-key"  # a test value: LiteLLM only ever runs its offline mock here
os.environ["FCM_SERVICE_ACCOUNT_JSON"] = '{"project_id": "dana-test"}'
os.environ["TIMEZONE"] = "Asia/Jerusalem"
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, ROOT)

# A 9.0 production database (converted at start): its briefing, review, wake day and hidden card are the first admin's.
_conn = sqlite3.connect(DB_PATH)
with open(os.path.join(testenv.FIXTURES, "db_9_0.sql"), encoding="utf-8") as _f:
    _conn.executescript(_f.read())
_conn.close()

import httpx
import litellm
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap

addons_bootstrap.bootstrap()

import core.server as server
from core import sessions_api
from addons.tasks import api as tasks_api
from core import (actions_api, briefing, briefing_api, checks, clock, composers, context_chats, now as now_mod, now_api,
                  notify as notify_mod, search, usage, user_rows, wake, weekly_review)
from core.auth import setup_auth
from core.db import get_db_connection
from core.session import get_session_state
from addons.calendar import events as calendar_events
from core.addons import sharing, store
from addons.homeassistant import client as ha_client
from addons.people import store as people_store
from addons.phone import push
from addons.shopping import lists as shopping_lists
from addons.tasks import groups as task_groups, titles as task_titles

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)


def run(coro):
    return LOOP.run_until_complete(coro)


ADMIN = users.first_admin()
assert ADMIN is not None


# --- 1. Migration ---------------------------------------------------------------------------------

def test_migration() -> None:
    with get_db_connection() as conn:
        owners = {t: {r[0] for r in conn.execute(f"SELECT DISTINCT user_id FROM {t}")}
                  for t in ("briefings", "weekly_reviews", "wake_log", "now_hidden")}
        wake_row = conn.execute("SELECT date, why FROM wake_log").fetchone()
        hidden = conn.execute("SELECT item_id FROM now_hidden").fetchone()
    check("migration: the briefing, review, wake day and hidden card are the first admin's",
          all(v == {ADMIN.id} for v in owners.values()), owners)
    check("migration: the wake day and the hidden card are kept as they were",
          tuple(wake_row) == ("2026-11-01", "phone alarm") and hidden[0] == "check:calls", (tuple(wake_row), hidden[0]))
    check("migration: the first admin's old briefing / review are theirs to read",
          weekly_review.get_review(ADMIN, "wr1") is not None)


test_migration()

# --- Users and their addons -------------------------------------------------------------------------

HA_HOSTS = {"admin": "http://ha-admin.invalid", "bob": "http://ha-bob.invalid"}
for _kind in ("phone", "tasks", "people", "knowledge", "shopping", "about", "money"):
    if not store.is_connected(ADMIN, _kind):
        store.connect(ADMIN, _kind)
store.connect(ADMIN, "homeassistant", credentials={"host": HA_HOSTS["admin"], "token": "tok-admin"},
              settings={"weather_entity": "weather.admin", "phone_interactive_sensor": "binary_sensor.admin_screen"})
store.connect(ADMIN, "calendar", credentials={"refresh_token": "admin-refresh"})

BOB = users.create("Bob Stone")
for _kind in ("phone", "tasks", "people", "knowledge"):
    store.connect(BOB, _kind)
store.connect(BOB, "homeassistant", credentials={"host": HA_HOSTS["bob"], "token": "tok-bob"},
              settings={"weather_entity": "weather.bob", "phone_interactive_sensor": "binary_sensor.bob_screen"})
store.connect(BOB, "calendar", credentials={"refresh_token": "bob-refresh"})
CARL = users.create("Carl Weiss", telegram_chat_id=7001)
store.connect(CARL, "tasks")
store.connect(CARL, "calendar", credentials={"refresh_token": "carl-refresh"})
EVE = users.create("Eve")
task_groups.ensure_default_groups_for_everyone()
shopping_lists.ensure_lists_for_everyone()
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ADMIN, BOB, CARL, EVE)}
NAMES = {ADMIN.id: "admin", BOB.id: "bob", CARL.id: "carl", EVE.id: "eve"}


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[user.id]}"}


# --- Fakes ----------------------------------------------------------------------------------------

NOW = clock.now()
TODAY = NOW.date()
SEEN: Dict[str, Any] = {"ha": [], "calendar": [], "pings": [], "model": [], "telegram": [],
                        "ha_fail": set(), "cal_fail": set(), "history": {}}


def _ha_who(conn) -> str:
    return next(k for k, v in HA_HOSTS.items() if v == ha_client.host(conn))


async def fake_entity_state(conn, entity_id, timeout=6.0):
    who = _ha_who(conn)
    SEEN["ha"].append((conn.user.id, who, "state", entity_id))
    return {"state": "rainy" if who == "admin" else "sunny",
            "attributes": {"temperature": 18 if who == "admin" else 27, "temperature_unit": "°C"}}


async def fake_forecast(conn, entity_id):
    who = _ha_who(conn)
    SEEN["ha"].append((conn.user.id, who, "forecast", entity_id))
    return {"temperature": 24 if who == "admin" else 31, "templow": 14, "precipitation_probability": 70 if who == "admin" else 0}


async def fake_history(conn, entity_id, since_utc, until_utc):
    who = _ha_who(conn)
    SEEN["ha"].append((conn.user.id, who, "history", entity_id))
    if who in SEEN["ha_fail"]:
        raise RuntimeError(f"{who}'s Home Assistant is down")
    now = datetime.datetime.now(datetime.timezone.utc)
    spec = SEEN["history"].get(who, [])
    return [{"state": st, "last_changed": (now - datetime.timedelta(seconds=ago)).isoformat()} for ago, st in spec]


async def fake_zones(conn):
    SEEN["ha"].append((conn.user.id, _ha_who(conn), "zones", ""))
    return [{"entity_id": "zone.home", "name": "Home"}]

ha_client.entity_state = fake_entity_state
ha_client.forecast = fake_forecast
ha_client.history = fake_history
ha_client.get_ha_zones_list = fake_zones

EVENTS: Dict[str, List[Dict[str, Any]]] = {"admin-refresh": [], "bob-refresh": [], "carl-refresh": []}


def ev(eid: str, title: str, start: datetime.datetime, attendees=()) -> Dict[str, Any]:
    return {"id": eid, "title": title, "start": start.isoformat(), "end": (start + datetime.timedelta(hours=1)).isoformat(),
            "all_day": False, "location": "", "attendees": list(attendees), "calendar_id": "primary"}


def fake_list_events(conn, time_min, time_max, limit=50, *args, **kwargs):
    token = conn.credentials["refresh_token"]
    SEEN["calendar"].append((conn.user.id, token))
    if token in SEEN["cal_fail"]:
        raise RuntimeError("calendar down")
    return [e for e in EVENTS[token] if time_min.isoformat() <= e["start"] <= time_max.isoformat()]

calendar_events.list_events = fake_list_events


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._payload


async def fake_post(self, url, *args, **kwargs):
    body = kwargs.get("json") or {}
    if "fcm.googleapis.com" in url:
        msg = body["message"]
        with get_db_connection() as conn:
            ev_row = conn.execute("SELECT user_id, type, data FROM phone_events WHERE seq = ?",
                                  (int(msg["data"]["seq"]),)).fetchone()
        if any(p["seq"] == int(msg["data"]["seq"]) for p in SEEN["pings"]):
            return FakeResponse({"name": "projects/dana-test/messages/1"})  # the same event to another of their devices
        SEEN["pings"].append({"seq": int(msg["data"]["seq"]), "token": msg["token"], "type": msg["data"]["type"],
                              "user": ev_row["user_id"] if ev_row else None,
                              "payload": json.loads(ev_row["data"]) if ev_row and ev_row["data"] else {}})
        return FakeResponse({"name": "projects/dana-test/messages/1"})
    raise AssertionError(f"unexpected POST {url}")


httpx.AsyncClient.post = fake_post


async def fake_fcm_token():
    return "fcm-test-token"

push._access_token = fake_fcm_token
push.register(ADMIN, "admin-phone", "tok-admin-phone", "Fold")
push.register(BOB, "bob-phone", "tok-bob-phone", "Pixel")


class FakeTelegram:
    async def send_text(self, chat_id, text, parse_mode=None):
        SEEN["telegram"].append((chat_id, text))

    async def notify_whatsapp_voice(self, user, chat_id, sender, transcript, message_id):
        return False

notify_mod.register_telegram(FakeTelegram())

_real_acompletion = litellm.acompletion


async def fake_acompletion(**kwargs):
    """LiteLLM's offline mock, so the real usage callback records the call."""
    props = set(kwargs["response_format"]["json_schema"]["schema"]["properties"])
    prompt = kwargs["messages"][0]["content"]
    uid = users.current_id() or ""
    if props == {"headline", "points"}:
        SEEN["model"].append({"user": uid, "job": "briefing", "prompt": prompt})
        kwargs["mock_response"] = json.dumps({"headline": f"Day of {NAMES.get(uid, '?')}",
                                              "points": [f"Point for {NAMES.get(uid, '?')}"]})
    elif props == {"headline", "summary", "items"}:
        SEEN["model"].append({"user": uid, "job": "review", "prompt": prompt})
        refs = json.loads(prompt.split("Items that need a decision:\n", 1)[1].split("\n\nFor every item", 1)[0])
        due = (TODAY + datetime.timedelta(days=3)).isoformat()
        kwargs["mock_response"] = json.dumps({
            "headline": f"Week of {NAMES.get(uid, '?')}", "summary": ["A fine week"],
            "items": [{"ref": i["ref"], "action": "reschedule" if i["type"] == "task" else "keep", "due_date": due,
                       "reason": "fresh date"} for i in refs]})
    else:
        SEEN["model"].append({"user": uid, "job": "other", "prompt": prompt})
        kwargs["mock_response"] = json.dumps({k: [] for k in props})
    return await _real_acompletion(**kwargs)


litellm.acompletion = fake_acompletion
litellm.suppress_debug_info = True


async def plain_title(t):
    return t


async def plain_group(user, group, title):
    return task_groups.default_group_id(user)

task_titles.sanitize_task_title = plain_title
task_titles.resolve_task_group = plain_group

wake.WAKE_WINDOW = (0, 24)  # the test may run at any hour
wake.WAKE_CONFIRM_SECONDS = 0


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    now_api.setup(app)
    briefing_api.setup(app)
    actions_api.setup(app)
    r = app.router
    r.add_post("/api/phone/unlock", briefing_api.phone_unlock_api)
    r.add_post("/api/chat/context", sessions_api.start_context_chat_api)
    r.add_get("/api/tasks", tasks_api.get_tasks_api)
    return app


# --- Data ---------------------------------------------------------------------------------------

def utc_sql(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def add_task(user, title: str, *, triggers=None, due: Optional[str] = None, fired_ago: Optional[int] = None,
             updated_days: float = 1, snooze_count: int = 0) -> str:
    tid = str(uuid.uuid4())
    with get_db_connection() as conn:
        conn.execute("INSERT INTO tasks (id, title, description, priority, status, triggers, due_date, group_id, "
                     "created_at, updated_at, snooze_count, user_id, created_by) "
                     "VALUES (?, ?, '', 'medium', 'needs_action', ?, ?, ?, ?, ?, ?, ?, ?)",
                     (tid, title, json.dumps(triggers or {}), due, task_groups.default_group_id(user),
                      utc_sql(NOW - datetime.timedelta(days=30)),
                      utc_sql(NOW - datetime.timedelta(days=updated_days)), snooze_count, user.id, user.id))
        if fired_ago is not None:
            conn.execute("INSERT OR REPLACE INTO task_trigger_marks (user_id, key, at) VALUES (?, ?, ?)",
                         (user.id, f"last_trigger_v2_time_{tid}",
                          (NOW - datetime.timedelta(minutes=fired_ago)).astimezone(datetime.timezone.utc).isoformat()))
        conn.commit()
    return tid


def add_suggestion(user, title: str, *, days_old: float = 0) -> str:
    sid = str(uuid.uuid4())
    with get_db_connection() as conn:
        conn.execute("INSERT INTO call_suggestions (id, user_id, call_id, source_type, kind, payload, status, created_at) "
                     "VALUES (?, ?, ?, 'call', 'task', ?, 'pending', ?)",
                     (sid, user.id, f"call-{sid[:8]}", json.dumps({"title": title, "triggers": {}, "confidence": 0.9}),
                      utc_sql(NOW - datetime.timedelta(days=days_old))))
        conn.commit()
    return sid


YESTERDAY = (TODAY - datetime.timedelta(days=1)).isoformat()
IDS: Dict[str, str] = {
    "admin_task": add_task(ADMIN, "Admin fired reminder", fired_ago=10, updated_days=10 / 1440),
    "admin_overdue": add_task(ADMIN, "Admin overdue papers", due=YESTERDAY),
    "admin_wake": add_task(ADMIN, "Admin wake stretch", triggers={"wake": True}),
    "admin_sug": add_suggestion(ADMIN, "Admin call back the plumber"),
    "bob_task": add_task(BOB, "Bob fired reminder", fired_ago=10, updated_days=10 / 1440),
    "bob_overdue": add_task(BOB, "Bob overdue zebra form", due=YESTERDAY),
    "bob_wake": add_task(BOB, "Bob wake vitamins", triggers={"wake": True}),
    "bob_sug": add_suggestion(BOB, "Bob book the vet"),
    "bob_old_sug": add_suggestion(BOB, "Bob old idea", days_old=10),
    "carl_task": add_task(CARL, "Carl fired reminder", fired_ago=10, updated_days=10 / 1440),
    "carl_overdue": add_task(CARL, "Carl overdue taxes", due=YESTERDAY),
}
BOB_PERSON = people_store.create_person(BOB, "Dafna Levi")["id"]
with get_db_connection() as _c:
    _c.execute("UPDATE people SET brief = ? WHERE id = ?",
               (json.dumps({"points": ["Talked about the move"], "open": []}), BOB_PERSON))
    _c.commit()
ADMIN_PERSON = people_store.create_person(ADMIN, "Dafna Levi")["id"]
with get_db_connection() as _c:
    _c.execute("UPDATE people SET brief = ? WHERE id = ?",
               (json.dumps({"points": ["Admin's notes on Dafna"], "open": []}), ADMIN_PERSON))
    _c.commit()
EVENTS["admin-refresh"] = [ev("a1", "Admin board meeting", NOW + datetime.timedelta(minutes=5))]
EVENTS["bob-refresh"] = [ev("b1", "Coffee with Dafna Levi", NOW + datetime.timedelta(minutes=5))]
EVENTS["carl-refresh"] = [ev("c1", "Carl dentist", NOW + datetime.timedelta(minutes=5))]


def unlock_body() -> Dict[str, int]:
    """An unlock 150 s ago: the screen has been on since (the check measures from the signal)."""
    return {"at": int((datetime.datetime.now(datetime.timezone.utc).timestamp() - 150) * 1000)}


def pings_of(user, type_: str) -> List[Dict[str, Any]]:
    return [p for p in SEEN["pings"] if p["user"] == user.id and p["type"] == type_]


async def drain() -> None:
    for _ in range(3):
        await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.wait(pending, timeout=5)


async def clear_usage() -> None:
    await drain()
    await usage.flush()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM usage_events")
        conn.commit()


async def usage_rows(purpose: str) -> List[str]:
    await drain()
    await usage.flush()
    with get_db_connection() as conn:
        return [r[0] for r in conn.execute("SELECT user_id FROM usage_events WHERE purpose = ? ORDER BY id", (purpose,))]


# --- 2. First-admin prompts byte for byte ---------------------------------------------------------

def old_briefing_prompt(d: Dict[str, Any]) -> str:
    """core/briefing.py's prompt before composers were per user (git 1a98af3)."""
    OWNER_NAME, OWNER_CONTEXT, LANGUAGE = testenv.OWNER_NAME, testenv.OWNER_CONTEXT, briefing.LANGUAGE
    return f"""Write {OWNER_NAME}'s morning briefing. He just woke up and will glance at it on his phone, watch and bedroom tablet.

About Robin:
{OWNER_CONTEXT}

Now: {clock.describe_now()}

Today's data (JSON):
{json.dumps({k: v for k, v in d.items() if k not in ('calendar_ok', briefing.SECTIONS)}, ensure_ascii=False, indent=1)}
{'' if d.get('calendar_ok', True) else '(The calendar could not be read today — say nothing about meetings.)'}

Return:
- `headline`: 3–8 words capturing the shape of the day ("Busy morning, free after 2", "Quiet Friday, two things due").
- `points`: 3–6 short fragments (4–12 words), most useful first: the first event and anything time-critical, what's due or overdue, what needs him in the Inbox, weather only if it matters (rain, heat, cold), birthdays, one person to reach out to. Skip empty categories; never pad.
Fragments, not sentences; no trailing periods; no emoji; no advice beyond the facts. Use 24h times. Write in {LANGUAGE}; keep names, event titles and task titles as written (Hebrew stays Hebrew)."""


def old_review_prompt(c: Dict[str, Any], nw: Optional[Dict[str, Any]], today: datetime.date) -> str:
    """core/weekly_review.py's prompt before composers were per user (git 1a98af3)."""
    OWNER_NAME, OWNER_CONTEXT = testenv.OWNER_NAME, testenv.OWNER_CONTEXT
    listing = [{"ref": i["ref"], "type": i["type"], "title": i["title"], "why": i["why"], "facts": i["facts"],
                "group": i.get("group"), "allowed": list(weekly_review.actions_for(i["type"]))} for i in c["items"]]
    return f"""You're helping {OWNER_NAME} with his weekly review (Saturday evening, before the Israeli work week starts Sunday).

About Robin:
{OWNER_CONTEXT}

Today: {clock.describe_now()}
This week: {c['stats']['done']} tasks done ({', '.join(c['stats']['done_titles']) or 'none'}), {c['stats']['created']} created, {c['stats']['open']} open.
Next week's calendar: {json.dumps(nw, ensure_ascii=False) if nw else 'unknown'}
Task groups: {', '.join(c['groups'])}

Items that need a decision:
{json.dumps(listing, ensure_ascii=False, indent=1)}

For every item return one entry in `items` with its `ref`, an `action` from its `allowed` list, and a short `reason` (≤ 10 words).
- reschedule → `due_date` (YYYY-MM-DD, after {today.isoformat()}); spread work away from next week's busiest day.
- move_group → `group` (one of the task groups).
- reach_out → `due_date` for a "call / message them" task.
- complete only when the title or facts suggest it's already done or no longer relevant; delete for clearly dead items; keep when unsure.
- Dismiss suggestions that are old and vague; accept ones still clearly useful.
Also return `headline` (3–8 words on the week) and `summary`: 1–3 short fragments about the week and the one coming (no advice-speak, no emoji).
Write in English; keep titles and names as written."""


def old_context_prompt(r: Dict[str, Any]) -> str:
    lines = [f"- [{i['type']} {i['ref']}] {i['title']} — {i['why']} ({'; '.join(i.get('facts') or [])}) → proposed "
             f"{i.get('action')}{' ' + str(i.get('due_date')) if i.get('due_date') and i.get('action') in ('reschedule', 'reach_out') else ''}"
             f"{' ' + str(i.get('group')) if i.get('action') == 'move_group' else ''}" for i in r["items"]]
    return (f"## Opened from the weekly review {r['week']} (status {r['status']})\n"
            f"{r['headline']}. {' '.join(r['summary'])}\n"
            "Items and the proposed actions:\n" + ("\n".join(lines) or "- none") +
            "\nHelp Robin decide. Make changes with the normal tools (update_task, complete_task, delete_task, "
            "accept_call_suggestion, dismiss_call_suggestion, add_task + link_task_to_person). Keep replies short.")


def test_prompts() -> None:
    real_describe = clock.describe_now
    clock.describe_now = lambda *a, **k: "Monday, 16 November 2026, 07:30 (Asia/Jerusalem)"
    try:
        for calendar_ok in (True, False):
            d = run(briefing.gather(ADMIN))
            d["calendar_ok"] = calendar_ok
            check(f"prompts: the first admin's briefing prompt is the old one byte for byte (calendar_ok={calendar_ok})",
                  briefing.briefing_prompt(d, ADMIN) == old_briefing_prompt(d))
        c = weekly_review.candidates(ADMIN)
        nw = run(weekly_review.next_week(ADMIN))
        check("prompts: the first admin's review prompt is the old one byte for byte",
              weekly_review.review_prompt(c, nw, TODAY, ADMIN) == old_review_prompt(c, nw, TODAY))
        bob_b = briefing.briefing_prompt(run(briefing.gather(BOB)), BOB)
        check("prompts: Bob's briefing prompt is written for Bob (name, they / their / them)",
              bob_b.startswith("Write Bob Stone's morning briefing. They just woke up and will glance at it on their phone")
              and "what needs them in the Inbox" in bob_b, bob_b[:200])
        bob_r = weekly_review.review_prompt(weekly_review.candidates(BOB), None, TODAY, BOB)
        check("prompts: Bob's review prompt is written for Bob",
              bob_r.startswith("You're helping Bob Stone with their weekly review"), bob_r[:120])
        leaks = [p for p in (bob_b, bob_r) if "Robin" in p or "About " in p or testenv.OWNER_CONTEXT in p]
        check("prompts: no first-admin name or context in Bob's prompts", not leaks)
        check("prompts: Bob's briefing data is only his (no admin task)",
              "Admin" not in bob_b and "Bob overdue zebra form" in bob_b)
    finally:
        clock.describe_now = real_describe


# --- 3. Briefing ----------------------------------------------------------------------------------

def test_briefing_content() -> None:
    a, b, c = run(briefing.gather(ADMIN)), run(briefing.gather(BOB)), run(briefing.gather(CARL))
    check("briefing: weather from each user's own Home Assistant",
          a["weather"]["condition"] == "rainy" and b["weather"]["condition"] == "sunny", (a["weather"], b["weather"]))
    check("briefing: no weather without Home Assistant (Carl)", c["weather"] is None)
    check("briefing: events from each user's own calendar",
          [e["title"] for e in a["events"]] == ["Admin board meeting"]
          and [e["title"] for e in b["events"]] == ["Coffee with Dafna Levi"]
          and [e["title"] for e in c["events"]] == ["Carl dentist"])
    check("briefing: tasks are each user's own",
          {t["title"] for t in a["tasks"]["due"]} >= {"Admin overdue papers"}
          and {t["title"] for t in b["tasks"]["due"]} == {"Bob overdue zebra form"}
          and {t["title"] for t in c["tasks"]["due"]} == {"Carl overdue taxes"},
          (a["tasks"]["due"], b["tasks"]["due"], c["tasks"]["due"]))
    check("briefing: suggestions are each user's own; none without the phone (Carl)",
          "Bob book the vet" in b["suggestions"]["top"] and "Admin call back the plumber" not in b["suggestions"]["top"]
          and c["suggestions"] == {"count": 0, "top": []}, (b["suggestions"], c["suggestions"]))
    check("briefing: no Inbox or people without the addons (Carl)",
          c["inbox"] == {"needs_you": 0, "active": 0, "points": []} and c["people"] == {"birthdays": [], "reach_out": []})
    check("briefing: who gets one — phone, or a Telegram chat with something to say; not Eve",
          briefing.available(ADMIN) and briefing.available(BOB) and briefing.available(CARL)
          and not briefing.available(EVE))
    check("briefing: a Telegram chat alone (no content addon) gets none",
          not briefing.available(users.create("Tom", telegram_chat_id=7002)))


# --- 4. Wake --------------------------------------------------------------------------------------

async def test_wake(client: TestClient) -> None:
    SEEN["history"] = {"admin": [(200, "on")], "bob": [(200, "on")]}
    SEEN["pings"].clear()
    r = await client.post("/api/phone/unlock", json=unlock_body(), headers=auth(BOB))
    check("wake: Bob's unlock starts his check", (r.status, (await r.json())["status"]) == (200, "confirming"))
    await drain()
    today = clock.now().date().isoformat()
    check("wake: Bob's wake confirmed (his wake_log row), not the first admin's",
          wake.done_today(BOB, today) and not wake.done_today(ADMIN, today))
    with get_db_connection() as conn:
        marks = {r[0] for r in conn.execute("SELECT key FROM task_trigger_marks WHERE key LIKE 'last_trigger_v2_wake_%'")}
        sig = conn.execute("SELECT detail FROM system_marks WHERE key = ?", (f"wake_signal:{BOB.id}",)).fetchone()
    check("wake: only Bob's wake-trigger task fired",
          f"last_trigger_v2_wake_{IDS['bob_wake']}" in marks and f"last_trigger_v2_wake_{IDS['admin_wake']}" not in marks,
          marks)
    check("wake: Bob's raw signal recorded under his id", sig is not None and sig[0] == "unlock")
    check("wake: the briefing pushed to Bob's phone only, built for him",
          len(pings_of(BOB, "briefing")) == 1 and not pings_of(ADMIN, "briefing")
          and pings_of(BOB, "briefing")[0]["payload"]["headline"] == "Day of bob",
          [(p["user"], p["type"]) for p in SEEN["pings"]])
    check("wake: Bob's wake reminder pushed to his phone",
          any(p["payload"].get("task_id") == IDS["bob_wake"] for p in pings_of(BOB, "reminder"))
          and not any(p["payload"].get("task_id") == IDS["admin_wake"] for p in SEEN["pings"]))
    b = briefing.get_today(BOB)
    check("wake: Bob's briefing of today is marked delivered (trigger wake)",
          b and b["trigger"] == "wake" and b["delivered_at"])
    r = await client.post("/api/phone/unlock", json=unlock_body(), headers=auth(BOB))
    check("wake: once a day per user — Bob's second unlock is done", (await r.json())["status"] == "done")
    n_before = len(pings_of(BOB, "briefing"))
    await drain()
    check("wake: no second briefing push for Bob", len(pings_of(BOB, "briefing")) == n_before)
    r = await client.post("/api/phone/unlock", json=unlock_body(), headers=auth(ADMIN))
    check("wake: the first admin's unlock is independent of Bob's", (await r.json())["status"] == "confirming")
    await drain()
    check("wake: the first admin's wake confirmed and their briefing pushed to their phone",
          wake.done_today(ADMIN, today) and len(pings_of(ADMIN, "briefing")) == 1)
    for u in (CARL, EVE):
        r = await client.post("/api/phone/unlock", json=unlock_body(), headers=auth(u))
        check(f"wake: {NAMES[u.id]} without the phone and Home Assistant → unavailable",
              (r.status, (await r.json())["status"]) == (200, "unavailable"))
    with get_db_connection() as conn:
        rows = {r[0] for r in conn.execute("SELECT user_id FROM wake_log WHERE date = ?", (today,))}
        carl_sig = conn.execute("SELECT 1 FROM system_marks WHERE key = ?", (f"wake_signal:{CARL.id}",)).fetchone()
    check("wake: nothing recorded for Carl / Eve", rows == {ADMIN.id, BOB.id} and carl_sig is None, rows)
    check("wake: the flow needs both phone and Home Assistant",
          wake.available(ADMIN) and wake.available(BOB) and not wake.available(CARL) and not wake.available(EVE))


async def test_wake_poll() -> None:
    """The fallback poll per user: a failing Home Assistant does not stop the next user."""
    today = clock.now().date().isoformat()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM wake_log WHERE date = ?", (today,))
        conn.commit()
    SEEN["ha_fail"] = {"admin"}
    SEEN["history"] = {"bob": [(1700, "on"), (1500, "off"), (900, "on"), (700, "off"), (300, "on"), (100, "off")]}
    SEEN["ha"].clear()
    out = await wake.poll_once()
    check("wake poll: Bob's wake fired although the first admin's Home Assistant failed first",
          out.get(BOB.id) is True and ADMIN.id not in out and wake.done_today(BOB, today), out)
    asked = {(u, who) for u, who, fn, _ in SEEN["ha"] if fn == "history"}
    check("wake poll: each user's own Home Assistant asked, only users with the flow",
          asked == {(ADMIN.id, "admin"), (BOB.id, "bob")}, asked)
    SEEN["ha_fail"] = set()


# --- 5. Now ---------------------------------------------------------------------------------------

def ids_of(payload: Dict[str, Any]) -> List[str]:
    return [i["id"] for i in payload["items"]]


async def get_now(client, user) -> Dict[str, Any]:
    r = await client.get("/api/now", headers=auth(user))
    assert r.status == 200, await r.text()
    return await r.json()


async def act(client, user, item_id: str, action: str, args=None):
    r = await client.post("/api/now/act", json={"id": item_id, "action": action, "args": args or {}}, headers=auth(user))
    return r.status, await r.json()


async def test_now(client: TestClient) -> None:
    a, b, c, e = (await get_now(client, ADMIN), await get_now(client, BOB), await get_now(client, CARL),
                  await get_now(client, EVE))
    check("now: the first admin sees their reminder and suggestion, not Bob's",
          f"task:{IDS['admin_task']}" in ids_of(a) and f"sug:{IDS['admin_sug']}" in ids_of(a)
          and f"task:{IDS['bob_task']}" not in ids_of(a) and f"sug:{IDS['bob_sug']}" not in ids_of(a), ids_of(a))
    check("now: Bob sees his reminder and suggestion, not the first admin's",
          f"task:{IDS['bob_task']}" in ids_of(b) and f"sug:{IDS['bob_sug']}" in ids_of(b)
          and f"task:{IDS['admin_task']}" not in ids_of(b) and f"sug:{IDS['admin_sug']}" not in ids_of(b), ids_of(b))
    check("now: Carl (no phone) sees his reminder and no suggestions / notifications",
          f"task:{IDS['carl_task']}" in ids_of(c) and not any(i.startswith(("sug:", "notif:")) for i in ids_of(c))
          and c["notifications"] == {"total": 0, "noise": 0, "needs_you": 0} and c["quiet"] == [], ids_of(c))
    check("now: Eve (no addons) has an empty screen", e["items"] == [] and e["quiet"] == [] and e["lede"] is None
          and e["day"]["calendar"] is False, e)
    slot_keys = {NAMES[u.id]: [s["key"] for s in p["day"]["slots"] if not s["key"].isdigit()]
                 for u, p in ((ADMIN, a), (BOB, b), (CARL, c), (EVE, e))}
    check("now: day slots — At home with Home Assistant, On waking with the wake flow; neither for Carl / Eve",
          slot_keys == {"admin": ["home", "wake"], "bob": ["home", "wake"], "carl": [], "eve": []}, slot_keys)
    wake_items = {NAMES[u.id]: [i["task_id"] for s in p["day"]["slots"] if s["key"] == "wake" for i in s["items"]]
                  for u, p in ((ADMIN, a), (BOB, b))}
    check("now: each user's wake slot holds only their own wake tasks",
          wake_items == {"admin": [IDS["admin_wake"]], "bob": [IDS["bob_wake"]]}, wake_items)
    check("now: each user's day strip knows their calendar", a["day"]["calendar"] and b["day"]["calendar"]
          and c["day"]["calendar"])
    sug = next(i for i in b["items"] if i["id"] == f"sug:{IDS['bob_sug']}")
    choice_text = json.dumps(sug, ensure_ascii=False)
    check("now: Bob's suggestion offers the home / wake choices (he has both)",
          "When I get home" in choice_text and "When I wake" in choice_text, choice_text[:300])
    labels = [o["label"] for o in now_mod._suggestion_choices(CARL, {}, clock.now())]
    check("now: no home / wake choices without Home Assistant / the wake flow",
          "When I get home" not in labels and "When I wake" not in labels, labels)
    # Lede: the briefing headline is per user.
    check("now: Bob's lede is his own briefing headline", b["lede"] == "Day of bob.", b["lede"])
    check("now: the first admin's lede is theirs", a["lede"] == "Day of admin.", a["lede"])
    check("now: Carl's lede is not someone else's briefing", c["lede"] in (None,) or "Day of" not in (c["lede"] or ""),
          c["lede"])

    # Hide / act / undo per user.
    st, res = await act(client, BOB, f"task:{IDS['bob_task']}", "snooze", {"minutes": 30})
    check("now: Bob snoozes his reminder", st == 200 and res.get("undo_id"), res)
    st2, _ = await act(client, ADMIN, f"task:{IDS['bob_task']}", "done")
    check("now: the first admin can't act on Bob's reminder (404)", st2 == 404, st2)
    r = await client.post(f"/api/undo/{res['undo_id']}", headers=auth(ADMIN))
    check("now: the first admin can't undo Bob's action", r.status == 404)
    r = await client.post(f"/api/undo/{res['undo_id']}", headers=auth(BOB))
    check("now: Bob undoes his snooze", r.status == 200)
    st, res = await act(client, BOB, f"sug:{IDS['bob_sug']}", "snooze", {"minutes": 60})
    b2, a2 = await get_now(client, BOB), await get_now(client, ADMIN)
    check("now: Bob's snoozed suggestion is hidden for him",
          st == 200 and f"sug:{IDS['bob_sug']}" not in ids_of(b2), (st, res))
    with get_db_connection() as conn:
        hidden = {(r[0], r[1]) for r in conn.execute("SELECT user_id, item_id FROM now_hidden")}
    check("now: hidden cards are stored per user",
          (BOB.id, f"sug:{IDS['bob_sug']}") in hidden and (ADMIN.id, f"sug:{IDS['bob_sug']}") not in hidden, hidden)
    r = await client.post(f"/api/undo/{res['undo_id']}", headers=auth(BOB))
    check("now: undo brings Bob's suggestion back", r.status == 200
          and f"sug:{IDS['bob_sug']}" in ids_of(await get_now(client, BOB)))
    check("now: the first admin's queue is unchanged by Bob's actions", ids_of(a2) == ids_of(a))
    st, res = await act(client, CARL, f"sug:{IDS['admin_sug']}", "dismiss")
    check("now: a suggestion action without the phone → 403 not_connected",
          st == 403 and res.get("error") == "not_connected" and res.get("addon") == "phone", (st, res))
    st, res = await act(client, CARL, "quiet:all", "dismiss")
    check("now: quiet:all without the phone → 403 not_connected", st == 403 and res.get("addon") == "phone")
    st, res = await act(client, EVE, f"task:{IDS['carl_task']}", "done")
    check("now: a reminder action without tasks → 403 not_connected",
          st == 403 and res.get("addon") == "tasks", (st, res))
    st, res = await act(client, EVE, "review:whatever", "skip")
    check("now: a review action without tasks → 403 not_connected", st == 403 and res.get("addon") == "tasks")
    st, res = await act(client, CARL, "check:push", "ignore", {"days": 7})
    check("now: a check that isn't the user's → 404", st == 404, (st, res))


# --- 6. Checks ------------------------------------------------------------------------------------

async def get_checks(client, user) -> Dict[str, Dict[str, Any]]:
    r = await client.get("/api/checks", headers=auth(user))
    assert r.status == 200, await r.text()
    return {c["key"]: c for c in (await r.json())["checks"]}


async def test_checks(client: TestClient) -> None:
    a, b, c, e = (await get_checks(client, ADMIN), await get_checks(client, BOB), await get_checks(client, CARL),
                  await get_checks(client, EVE))
    check("checks: the first admin (every addon) sees every check in the usual order",
          list(a) == list(checks.all_keys()), list(a))
    check("checks: the payload shape is unchanged",
          all(set(x) == {"key", "label", "ok", "detail", "since", "ignored_until"} for x in a.values()))
    check("checks: Bob sees only the checks of his addons (no server check)",
          set(b) == {"push", "notification_sync", "location", "car", "ha_entities", "ha_watcher", "wake", "triage",
                     "calls", "calendar", "briefing"}, list(b))
    check("checks: Carl sees calendar and briefing only", set(c) == {"calendar", "briefing"}, list(c))
    check("checks: Eve sees none", e == {}, e)
    check("checks: Bob's wake check reads his own wake", b["wake"]["detail"] == "confirmed today", b["wake"])
    r = await client.post("/api/checks/push/ignore", json={"days": 3}, headers=auth(BOB))
    body = await r.json()
    check("checks: Bob ignores his push check", r.status == 200 and body.get("until"), body)
    a2, b2 = await get_checks(client, ADMIN), await get_checks(client, BOB)
    check("checks: ignoring is per user", b2["push"]["ignored_until"] and not a2["push"]["ignored_until"],
          (a2["push"], b2["push"]))
    r = await client.post("/api/checks/push/ignore", json={"days": 3}, headers=auth(CARL))
    check("checks: Carl can't ignore a check he doesn't see (404)", r.status == 404)
    r = await client.post("/api/checks/reminders/ignore", json={"days": 3}, headers=auth(BOB))
    check("checks: a member can't ignore the server check (404)", r.status == 404)
    r = await client.post("/api/checks/reminders/ignore", json={"days": 0}, headers=auth(ADMIN))
    check("checks: the first admin can", r.status == 200)
    r = await client.post(f"/api/undo/{body['undo_id']}", headers=auth(ADMIN))
    check("checks: the first admin can't undo Bob's ignore", r.status == 404)
    r = await client.post(f"/api/undo/{body['undo_id']}", headers=auth(BOB))
    check("checks: Bob undoes his ignore", r.status == 200 and not (await get_checks(client, BOB))["push"]["ignored_until"])


# --- 7. Briefing / review routes, dashboard -------------------------------------------------------

async def test_routes(client: TestClient) -> None:
    r = await client.get("/api/briefing/today?generate=1", headers=auth(CARL))
    b = (await r.json())["briefing"]
    check("dashboard: Carl's briefing (Telegram + tasks) is made for him on demand",
          r.status == 200 and b and b["headline"] == "Day of carl" and b["trigger"] == "app_open", b)
    r = await client.get("/api/briefing/today?generate=1", headers=auth(EVE))
    check("dashboard: Eve gets a null briefing", r.status == 200 and (await r.json()) == {"briefing": None})
    r = await client.get("/api/briefing/today", headers=auth(BOB))
    check("dashboard: Bob reads his own briefing", (await r.json())["briefing"]["headline"] == "Day of bob")
    r = await client.post("/api/briefing/generate", json={}, headers=auth(EVE))
    check("briefing: rebuilding without the addons → 403 not_connected phone",
          r.status == 403 and (await r.json()).get("addon") == "phone")
    r = await client.get("/api/tasks", headers=auth(BOB))
    titles = {t["title"] for t in await r.json()} if r.status == 200 else set()
    check("dashboard: /api/tasks lists only Bob's tasks", "Bob overdue zebra form" in titles
          and not any(t.startswith(("Admin", "Carl")) for t in titles), titles)
    r = await client.get("/api/tasks", headers=auth(EVE))
    check("dashboard: /api/tasks without tasks → 403 not_connected", r.status == 403)
    r = await client.get("/api/review/latest", headers=auth(EVE))
    check("review: Eve's latest review is null", (await r.json()) == {"review": None})
    for path in ("/api/review/generate", "/api/review/x/apply", "/api/review/x/dismiss"):
        r = await client.post(path, json={}, headers=auth(EVE))
        check(f"review: {path} without tasks → 403 not_connected",
              r.status == 403 and (await r.json()).get("addon") == "tasks")


# --- 8. Weekly review -----------------------------------------------------------------------------

async def test_review(client: TestClient) -> None:
    cb, cc = weekly_review.candidates(BOB), weekly_review.candidates(CARL)
    refs_b = {i["ref"] for i in cb["items"]}
    check("review: Bob's candidates are his overdue task and old suggestion only",
          refs_b == {IDS["bob_overdue"], IDS["bob_old_sug"]}, [(i["title"], i["why"]) for i in cb["items"]])
    check("review: Carl's candidates have no suggestions (no phone)",
          {i["ref"] for i in cc["items"]} == {IDS["carl_overdue"]}, cc["items"])
    nw = await weekly_review.next_week(CARL)
    check("review: next week from each user's own calendar (none there for Carl)",
          nw is not None and nw["events"] == 0)
    r = await client.post("/api/review/generate", json={}, headers=auth(BOB))
    rev = (await r.json())["review"]
    check("review: Bob's review is generated from his items with his headline",
          r.status == 200 and rev["headline"] == "Week of bob" and {i["ref"] for i in rev["items"]} == refs_b, rev)
    r = await client.get(f"/api/review/{rev['id']}", headers=auth(ADMIN))
    check("review: the first admin can't read Bob's review (404)", r.status == 404)
    r = await client.post(f"/api/review/{rev['id']}/apply", json={}, headers=auth(CARL))
    check("review: Carl can't apply Bob's review (404)", r.status == 404)
    latest_a = weekly_review.latest(ADMIN)
    check("review: the first admin's latest is still their own", latest_a and latest_a["id"] != rev["id"])
    # Now card and context chat for Bob's review.
    b = await get_now(client, BOB)
    check("now: Bob's pending review is on his Now screen only",
          f"review:{rev['id']}" in ids_of(b) and f"review:{rev['id']}" not in ids_of(await get_now(client, ADMIN)))
    r = await client.post("/api/chat/context", json={"kind": "review", "id": rev["id"]}, headers=auth(BOB))
    chat = await r.json()
    check("context chat: Bob opens a chat from his review", r.status == 200 and chat["context"]["kind"] == "review")
    prompt = context_chats.session_context_prompt(BOB, chat["session_id"])
    check("context chat: its prompt helps Bob decide (no first-admin name)",
          prompt and "Help Bob decide." in prompt and "Robin" not in prompt, prompt)
    check("context chat: stored as Bob's session", get_session_state(BOB.id, chat["session_id"]) is not None
          and get_session_state(ADMIN.id, chat["session_id"]) is None)
    r = await client.post("/api/chat/context", json={"kind": "review", "id": rev["id"]}, headers=auth(ADMIN))
    check("context chat: the first admin can't open Bob's review (404)", r.status == 404)
    r = await client.post("/api/chat/context", json={"kind": "review", "id": rev["id"]}, headers=auth(EVE))
    check("context chat: a review chat without tasks → 403 not_connected", r.status == 403)
    r = await client.post("/api/chat/context", json={"kind": "task", "id": IDS["bob_task"]}, headers=auth(CARL))
    check("context chat: Carl can't open Bob's task (404)", r.status == 404)
    admin_rev = await weekly_review.generate(ADMIN)
    check("context chat: the first admin's review prompt is the old one",
          weekly_review.context_prompt(ADMIN, admin_rev["id"]) == old_context_prompt(admin_rev))
    st, res = await act(client, BOB, f"review:{rev['id']}", "skip")
    check("now: Bob skips his review (undoable)", st == 200 and weekly_review.get_review(BOB, rev["id"])["status"] == "dismissed")
    r = await client.post(f"/api/undo/{res['undo_id']}", headers=auth(BOB))
    check("now: undo puts Bob's review back to pending",
          r.status == 200 and weekly_review.get_review(BOB, rev["id"])["status"] == "pending")
    r = await client.post(f"/api/review/{rev['id']}/apply", json={}, headers=auth(BOB))
    res = await r.json()
    with get_db_connection() as conn:
        due = conn.execute("SELECT due_date FROM tasks WHERE id = ?", (IDS["bob_overdue"],)).fetchone()[0]
        admin_due = conn.execute("SELECT due_date FROM tasks WHERE id = ?", (IDS["admin_overdue"],)).fetchone()[0]
    check("review: Bob applies his review — his overdue task rescheduled",
          r.status == 200 and res["applied"] >= 1 and due == (TODAY + datetime.timedelta(days=3)).isoformat(), (res, due))
    check("review: the first admin's overdue task untouched", admin_due == YESTERDAY)


# --- 9. Search ------------------------------------------------------------------------------------

async def test_search(client: TestClient) -> None:
    async def titles(user, q):
        r = await client.get(f"/api/search?q={q}", headers=auth(user))
        return {x["title"] for x in (await r.json())["results"]}
    check("search: Bob finds his own task", "Bob overdue zebra form" in await titles(BOB, "zebra"))
    check("search: the first admin and Carl don't", not await titles(ADMIN, "zebra") and not await titles(CARL, "zebra"))
    check("search: Bob finds his person, the first admin finds their own (same name)",
          "Dafna Levi" in await titles(BOB, "Dafna") and "Dafna Levi" in await titles(ADMIN, "Dafna"))
    check("search: Carl (no people) finds no person", not await titles(CARL, "Dafna"))
    check("search: Eve finds nothing", not await titles(EVE, "overdue"))
    sharing.set_level(BOB, task_groups.ITEM_TYPE, task_groups.default_group_id(BOB), CARL.id, "read")
    check("search: Carl finds Bob's task once Bob shares the list", "Bob overdue zebra form" in await titles(CARL, "zebra"))
    check("search: the first admin still doesn't", not await titles(ADMIN, "zebra"))
    sharing.set_level(BOB, task_groups.ITEM_TYPE, task_groups.default_group_id(BOB), CARL.id, "none")
    check("search: unsharing hides it again", not await titles(CARL, "zebra"))


# --- 10. Meeting cards ----------------------------------------------------------------------------

async def test_meetings() -> None:
    SEEN["pings"].clear()
    sent_b = await briefing.check_meetings(BOB)
    cards = pings_of(BOB, "context_card")
    check("meeting cards: Bob's event names his person → his card to his phone",
          sent_b == 1 and len(cards) == 1 and cards[0]["payload"]["card"]["person_id"] == BOB_PERSON, cards)
    check("meeting cards: Carl (calendar, no phone) gets none", await briefing.check_meetings(CARL) == 0
          and not briefing.meetings_available(CARL))
    check("meeting cards: only users with calendar and phone are checked",
          [u.id for u in composers.eligible(briefing.meetings_available)]
          == [ADMIN.id, BOB.id])
    # The loop: the first admin's calendar fails first; Bob's card still goes out.
    briefing._sent_meeting_cards.clear()
    SEEN["pings"].clear()
    SEEN["cal_fail"] = {"admin-refresh"}
    EVENTS["admin-refresh"] = [ev("a2", "Lunch with Dafna Levi", clock.now() + datetime.timedelta(minutes=5))]
    out = await briefing.meetings_once()
    check("meeting cards loop: a failing user does not stop the next",
          out.get(BOB.id) == 1 and ADMIN.id not in out and len(pings_of(BOB, "context_card")) == 1, out)
    check("meeting cards loop: the failing user backs off", briefing._meeting_backoff.get(ADMIN.id, 0) > 0)
    SEEN["cal_fail"] = set()
    briefing._meeting_backoff.clear()
    briefing._sent_meeting_cards.clear()
    SEEN["pings"].clear()
    await briefing.meetings_once()
    a_cards = pings_of(ADMIN, "context_card")
    check("meeting cards: the first admin's card is their own person, to their phone",
          len(a_cards) == 1 and a_cards[0]["payload"]["card"]["person_id"] == ADMIN_PERSON
          and a_cards[0]["payload"]["card"]["points"] == ["Admin's notes on Dafna"], a_cards)


# --- 11. Loops ------------------------------------------------------------------------------------

async def test_loops() -> None:
    # Briefing fallback: every user who gets one and has none yet; the first admin failing doesn't stop the others.
    with get_db_connection() as conn:
        conn.execute("DELETE FROM briefings WHERE date = ?", (clock.now().date().isoformat(),))
        conn.commit()
    await clear_usage()
    real_gather = briefing.gather

    async def gather_failing_for_admin(user):
        if user.id == ADMIN.id:
            raise RuntimeError("admin's data broke")
        return await real_gather(user)

    briefing.gather = gather_failing_for_admin
    try:
        out = await briefing.fallback_once(clock.now().replace(hour=10, minute=30))
    finally:
        briefing.gather = real_gather
    check("briefing fallback: built for Bob and Carl although the first admin failed first",
          out == {BOB.id: True, CARL.id: True}, out)
    check("briefing fallback: not for Eve (no addons)", briefing.get_today(EVE) is None)
    rows = await usage_rows("wake_briefing")
    check("briefing fallback: one usage row per built briefing, each the user's", sorted(rows) == sorted([BOB.id, CARL.id]),
          rows)
    prompts = {m["user"]: m["prompt"] for m in SEEN["model"] if m["job"] == "briefing"}
    check("briefing fallback: Carl's prompt is written for Carl", prompts[CARL.id].startswith("Write Carl Weiss's"))
    check("briefing fallback: not pushed (no wake yet)", briefing.get_today(BOB)["delivered_at"] is None)
    out = await briefing.fallback_once(clock.now().replace(hour=10, minute=30))
    check("briefing fallback: the first admin's built on the next run; nobody else twice",
          out == {ADMIN.id: True, BOB.id: False, CARL.id: False}, out)
    check("briefing fallback: not before the fallback time",
          await briefing.fallback_once(clock.now().replace(hour=8, minute=0)) == {})

    # Weekly review schedule: every user with tasks; Bob failing doesn't stop Carl.
    with get_db_connection() as conn:
        conn.execute("DELETE FROM weekly_reviews")
        conn.commit()
    await clear_usage()
    for u in (ADMIN, CARL):  # something to decide on that Saturday too: the model is asked for both
        add_task(u, f"Long overdue {NAMES[u.id]}", due=(TODAY - datetime.timedelta(days=10)).isoformat())
    SEEN["telegram"].clear()
    SEEN["pings"].clear()
    today = clock.now()
    saturday = (today - datetime.timedelta(days=(today.weekday() - 5) % 7)).replace(hour=20, minute=30)
    real_candidates = weekly_review.candidates

    def candidates_failing_for_bob(user, now=None):
        if user.id == BOB.id:
            raise RuntimeError("bob's data broke")
        return real_candidates(user, now)

    weekly_review.candidates = candidates_failing_for_bob
    try:
        out = await weekly_review.schedule_once(saturday)
    finally:
        weekly_review.candidates = real_candidates
    check("review schedule: made for the first admin and Carl although Bob failed in between",
          out == {ADMIN.id: True, CARL.id: True}, out)
    with get_db_connection() as conn:
        owners = sorted(r[0] for r in conn.execute("SELECT user_id FROM weekly_reviews"))
    check("review schedule: one review per user made, none for Eve", owners == sorted([ADMIN.id, CARL.id]), owners)
    rows = await usage_rows("weekly_review")
    check("review schedule: usage rows carry the right user", sorted(rows) == sorted([ADMIN.id, CARL.id]), rows)
    check("review schedule: delivered to Carl's Telegram chat, the first admin's phone",
          [c for c, _ in SEEN["telegram"]] == [7001] and len(pings_of(ADMIN, "weekly_review")) == 1
          and not pings_of(CARL, "weekly_review"), (SEEN["telegram"], [(p["user"], p["type"]) for p in SEEN["pings"]]))
    out = await weekly_review.schedule_once(saturday)
    check("review schedule: Bob's made on the next run; one per user and week",
          out == {ADMIN.id: False, BOB.id: True, CARL.id: False}, out)
    check("review schedule: not when it isn't due", await weekly_review.schedule_once(saturday.replace(hour=9)) == {})


def test_background_wiring() -> None:
    src = inspect.getsource(server)
    check("server: no interim owner for the background loops",
          "owner_of_unscoped_data" not in src and "owner_of_unscoped_data" not in inspect.getsource(briefing)
          and "owner_of_unscoped_data" not in inspect.getsource(weekly_review)
          and "owner_of_unscoped_data" not in inspect.getsource(wake))
    params = {f.__name__: list(inspect.signature(f).parameters) for f in
              (briefing.fallback_loop, weekly_review.schedule_loop, wake.wake_watch_loop, briefing.meeting_cards_loop)}
    check("server: the composer loops take no user (they go over every user)",
          all("user" not in p for p in params.values()), params)


# --- Run ------------------------------------------------------------------------------------------

async def main() -> None:
    async with TestClient(TestServer(build_app())) as client:
        await test_wake(client)
        await test_wake_poll()
        await test_now(client)
        await test_checks(client)
        await test_routes(client)
        await test_review(client)
        await test_search(client)
    await test_meetings()
    await test_loops()
    test_background_wiring()


test_prompts()
test_briefing_content()
run(main())
passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
