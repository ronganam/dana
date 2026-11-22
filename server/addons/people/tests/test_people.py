"""People, incoming-call card, wake briefing, meeting cards and the weekly review.

Covers: phone/name normalisation, linking calls (by number, by name, new
callers become people, unknown callers don't) and notifications (by sender,
never creating people), cadence + drifting, list / detail / lookup card (no
model calls), update / merge / delete / merge candidates, model briefs
(cached by signature, rewritten only on change), resolve + mentions, agent
tools, task links, snooze count, the wake flow (confirm delay → wake tasks +
briefing push, once a day, not when still asleep), briefing compose (model and
plain fallback), meeting cards, weekly review (candidates, proposals, apply,
schedule) and context chats. The model (at the LiteLLM boundary) / HA / calendar / FCM are mocked.

Run: bin/test test_people
"""
import asyncio
import datetime
import json
import os
import sys
import time

from cryptography.fernet import Fernet

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("people_test")
os.environ["DEFAULT_COUNTRY_CODE"] = "972"  # local numbers (0…) in these tests are Israeli
os.environ["DANA_INSECURE_DEV"] = "1"  # no API_TOKEN: local development, a request without credentials is the first admin
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # throwaway: encrypts the fake credentials
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["FCM_SERVICE_ACCOUNT_JSON"] = '{"project_id": "dana-test"}'
os.environ["WAKE_CONFIRM_SECONDS"] = "0"
os.environ["WAKE_POLL_SECONDS"] = "3600"

import httpx
import litellm
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, devices, users
from addons import bootstrap as addons_bootstrap
from addons.tasks.groups import ensure_default_groups_for_everyone

db.migrate()
users.bootstrap()
addons_bootstrap.bootstrap()  # the first admin's default addons
testenv.connect_admin("homeassistant", credentials={"host": "http://homeassistant.local:8123", "token": "test-ha"},
                      settings={"phone_interactive_sensor": "binary_sensor.phone_interactive"})  # wake reads its history
testenv.connect_admin("calendar", credentials={"refresh_token": "test-refresh-token"})  # Google is mocked
OWNER = users.first_admin()
ensure_default_groups_for_everyone()
from core import sessions_api
from addons.tasks import api as tasks_api
from core.auth import setup_auth
from core import briefing, briefing_api, clock, context_chats, wake, weekly_review
from addons.phone import push as phone_push
from addons.people import api as people_api, briefs as people_briefs, cadence, detail, linking, merge
from addons.people import store as people_store, values as people_values
from core.db import get_db_connection
from core.addons import helpers, registry

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


NOW = clock.now()
TODAY = NOW.date()


def iso(days_ago=0, hours=0):
    return (NOW - datetime.timedelta(days=days_ago, hours=hours)).isoformat(timespec="seconds")


# --- Fakes -----------------------------------------------------------------------

seen = {"model": [], "push": [], "fail": set(), "history": [], "events": []}


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


class ModelDown(Exception):
    status_code = 500


def model_reply(obj):
    return litellm.ModelResponse(choices=[{"message": {"role": "assistant",
                                                       "content": json.dumps(obj, ensure_ascii=False)}}])


async def fake_acompletion(**kwargs):
    """The model, at the LiteLLM boundary (core/llm.py runs for real)."""
    props = set(kwargs["response_format"]["json_schema"]["schema"]["properties"])
    prompt = kwargs["messages"][0]["content"]
    kind = "brief" if props == {"points", "open"} else "briefing" if props == {"headline", "points"} else \
        "review" if props == {"headline", "summary", "items"} else "?"
    seen["model"].append((kind, prompt))
    if kind in seen["fail"]:
        raise ModelDown("boom")
    if kind == "brief":
        return model_reply({"points": ["Planning the wedding venue.", "- Asked about Friday dinner"],
                            "open": ["Robin to send the caterer quote"]})
    if kind == "briefing":
        return model_reply({"headline": "Busy morning, free after 2.", "points": ["09:00 Standup", "Rain later"]})
    if kind == "review":
        refs = json.loads(prompt.split("Items that need a decision:\n", 1)[1].split("\n\nFor every item", 1)[0])
        items = []
        for it in refs:
            if it["type"] == "task" and it["why"] == "overdue":
                items.append({"ref": it["ref"], "action": "reschedule", "due_date": "1999-01-01", "reason": "bad date"})
            elif it["type"] == "task" and it["why"] == "stale":
                items.append({"ref": it["ref"], "action": "move_group", "group": "Home", "reason": "it's a home thing"})
            elif it["type"] == "task":
                items.append({"ref": it["ref"], "action": "fly_to_moon"})
            elif it["type"] == "suggestion":
                items.append({"ref": it["ref"], "action": "dismiss", "reason": "old"})
            else:
                items.append({"ref": it["ref"], "action": "reach_out", "due_date": (TODAY + datetime.timedelta(days=2)).isoformat()})
        return model_reply({"headline": "Solid week.", "summary": ["5 done", "Busy Tuesday ahead"], "items": items})
    raise AssertionError(f"unexpected schema {props}")


litellm.acompletion = fake_acompletion


async def fake_post(self, url, *args, **kwargs):
    body = kwargs.get("json") or {}
    if "fcm.googleapis.com" in url:
        msg = body["message"]
        ev = phone_push.events_after(users.first_admin(), int(msg["data"]["seq"]) - 1)["events"][0]
        seen["push"].append(ev)
        return FakeResponse({"name": "projects/dana-test/messages/1"})
    if "/api/services/weather/get_forecasts" in url:
        return FakeResponse({"service_response": {"weather.forecast_home": {"forecast": [
            {"temperature": 24, "templow": 14, "precipitation_probability": 70}]}}})
    raise AssertionError(f"unexpected POST {url}")


async def fake_get(self, url, *args, **kwargs):
    if url.endswith("/api/states/weather.forecast_home"):
        return FakeResponse({"state": "rainy", "attributes": {"temperature": 18, "temperature_unit": "°C"}})
    if "/api/history/period/" in url:
        if seen.get("history") is None:
            return FakeResponse({}, status=500)
        now = datetime.datetime.now(datetime.timezone.utc)
        return FakeResponse([[{"state": st, "last_changed": (now - datetime.timedelta(seconds=ago)).isoformat()}
                              for ago, st in seen["history"]]])
    raise AssertionError(f"unexpected GET {url}")


httpx.AsyncClient.post = fake_post
httpx.AsyncClient.get = fake_get


async def fake_fcm_token():
    return "fcm-test-token"

phone_push._access_token = fake_fcm_token
_owner = users.first_admin()
phone_push.register(_owner, devices.create(_owner, "phone", "Fold")[1], "fcm", "tok-1")


def fake_list_events(conn, time_min, time_max, limit=50):
    return [e for e in seen["events"] if time_min.isoformat() <= e["start"] <= time_max.isoformat() or e["all_day"]]

import addons.calendar.events as calendar_events  # noqa: E402
calendar_events.list_events = fake_list_events

import addons.tasks.tools as todos
import addons.tasks.titles as task_titles  # noqa: E402


async def plain_title(t):
    return t


async def plain_group(user, group, title):
    return "general"

task_titles.sanitize_task_title = plain_title
task_titles.resolve_task_group = plain_group


def pushes(type_):
    return [p for p in seen["push"] if p["type"] == type_]


def build_app():
    app = web.Application()
    setup_auth(app)  # no API_TOKEN: a request without credentials is the first admin
    people_api.setup(app)
    briefing_api.setup(app)
    app.router.add_post("/api/chat/context", sessions_api.start_context_chat_api)
    app.router.add_post("/api/tasks/{id}/snooze", tasks_api.snooze_task_api)
    app.router.add_post("/api/phone/unlock", briefing_api.phone_unlock_api)
    app.router.add_post("/api/tasks/{id}/links", tasks_api.add_task_link_api)
    app.router.add_get("/api/tasks", tasks_api.get_tasks_api)
    return app


# --- Fixtures --------------------------------------------------------------------

def add_call(cid, caller, number, days_ago, title="Call", summary="We talked.", receiver="Me", status="ready", direction="incoming"):
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO phone_calls (id, user_id, filename, caller, receiver, call_timestamp, transcript, summary, title, "
            "status, phone_number, direction) VALUES (?, ?, ?, ?, ?, ?, 'hello there, a long enough transcript', ?, ?, ?, ?, ?)",
            (cid, OWNER.id, f"{cid}.m4a", caller, receiver, iso(days_ago), summary, title, status, number, direction))
        conn.commit()


def add_note(nid, sender, text, days_ago=0, conversation=None, is_group=0, hours=0):
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO notifications (id, user_id, package, app_name, title, text, sender, conversation, is_group, "
            "posted_at, content_hash) VALUES (?, ?, 'com.whatsapp', 'WhatsApp', ?, ?, ?, ?, ?, ?, ?)",
            (nid, users.first_admin().id, sender, text, sender, conversation or sender, is_group, iso(days_ago, hours),
             f"h-{nid}"))
        conn.commit()


def add_task(tid, title, status="needs_action", due=None, triggers=None, updated_days_ago=0, snoozes=0, group="general"):
    upd = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=updated_days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO tasks (id, title, status, due_date, triggers, group_id, created_at, updated_at, snooze_count, "
            "user_id, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, title, status, due, json.dumps(triggers or {}), group, upd, upd, snoozes, OWNER.id, OWNER.id))
        conn.commit()


def person_of(table, rid):
    with get_db_connection() as conn:
        return conn.execute(f"SELECT person_id FROM {table} WHERE id = ?", (rid,)).fetchone()["person_id"]


async def main():
    # --- normalisation -----------------------------------------------------------
    check("phone: local mobile → international", people_values.norm_phone("050-123-4567") == "972501234567")
    check("phone: +972 with spaces", people_values.norm_phone("+972 50 123 4567") == "972501234567")
    check("phone: WhatsApp chat id", people_values.norm_phone("972501234567@c.us") == "972501234567")
    check("phone: 00 prefix", people_values.norm_phone("00972501234567") == "972501234567")
    check("phone: short codes aren't numbers", people_values.norm_phone("*6060") == "")
    check("name: emoji and case dropped", people_values.norm_name("גל כהן 🚀❤️🔥") == "גל כהן"
          and people_values.norm_name("  Yossi  COHEN!") == "yossi cohen")
    check("ago: wording", helpers.ago(NOW - datetime.timedelta(days=12)) == "12 days ago"
          and helpers.ago(NOW - datetime.timedelta(days=1, hours=1)).startswith(("yesterday", "1 days")) is not None)

    # --- linking ------------------------------------------------------------------
    # Gal: calls every ~3 days for a month, then 40 days of silence → drifting.
    for i, d in enumerate(range(70, 40, -3)):
        add_call(f"gal-{i}", "גל כהן 🚀❤️🔥", "+972 50-111-2222", d, title=f"Wedding call {i}", summary="Venue and caterer.")
    add_call("mom-1", "אמא", "0521234567", 2, title="Mom checks in", summary="אמא שאלה על ארוחת שישי")
    add_call("mom-2", "Me", "052-1234567", 1, receiver="אמא", direction="outgoing", summary="אישרנו את שישי")
    add_call("unknown-1", "Unknown", None, 5)
    add_call("biz-1", "", "+97231234567", 3, title="Delivery company")
    add_note("n-mom-1", "אמא", "תביא יין לשישי", hours=3)
    add_note("n-mom-2", "אמא 💕", "ואת הכלב", hours=2)
    add_note("n-group", "אמא", "שבת שלום לכולם", conversation="משפחה", is_group=1)
    add_note("n-stranger", "Random Person", "hello?")
    r = linking.link_pending(OWNER)
    with get_db_connection() as conn:
        everyone = conn.execute("SELECT * FROM people").fetchall()
    names = sorted(p["name"] for p in everyone)
    check("link: one person per caller (by number), unknown caller skipped",
          len(everyone) == 3 and "אמא" in names and any("גל" in n for n in names), names)
    gal = people_store.find_by_phone(OWNER, "0501112222")
    mom = people_store.find_by_phone(OWNER, "+972521234567")
    check("link: outgoing call matched to the receiver", person_of("phone_calls", "mom-2") == mom)
    check("link: unknown caller left unmatched ('')", person_of("phone_calls", "unknown-1") == "")
    biz = person_of("phone_calls", "biz-1")
    with get_db_connection() as conn:
        biz_row = conn.execute("SELECT * FROM people WHERE id = ?", (biz,)).fetchone()
    check("link: number-only caller becomes a hidden person", biz_row and biz_row["hidden"] == 1)
    check("link: notification by sender (emoji ignored), group sender too",
          person_of("notifications", "n-mom-1") == mom and person_of("notifications", "n-mom-2") == mom
          and person_of("notifications", "n-group") == mom)
    check("link: unknown sender never creates a person", person_of("notifications", "n-stranger") == ""
          and len(everyone) == 3)
    check("link: idempotent", linking.link_pending(OWNER) == {"created": 0, "linked": 0})

    # A name-only call (no number) for an existing person matches by name.
    add_call("mom-3", "אמא", None, 0)
    linking.link_pending(OWNER)
    check("link: call without a number matches by name", person_of("phone_calls", "mom-3") == mom)

    # --- cadence / list -------------------------------------------------------------
    lst = cadence.list_people(OWNER)
    check("list: hidden people left out by default", all(p["id"] != biz for p in lst) and len(lst) == 2)
    check("list: most recent contact first", lst[0]["id"] == mom, [p["name"] for p in lst])
    g = next(p for p in lst if p["id"] == gal)
    check("cadence: typical gap and drifting", g["stats"]["typical_gap_days"] == 3 and g["stats"]["drifting"]
          and g["stats"]["calls"] == 10, g["stats"])
    check("list: drifting sort only drifting people", [p["id"] for p in cadence.list_people(OWNER, sort="drifting")] == [gal])
    check("list: search by Hebrew name and by phone", [p["id"] for p in cadence.list_people(OWNER, q="אמא")] == [mom]
          and [p["id"] for p in cadence.list_people(OWNER, q="0501112222")] == [gal])

    # --- lookup card (no model) -----------------------------------------------------
    add_task("t-mom", "Buy wine for אמא", due=TODAY.isoformat())
    before = len(seen["model"])
    t0 = time.perf_counter()
    lk = detail.lookup(OWNER, "052-123-4567")
    took = time.perf_counter() - t0
    check("lookup: found with headline, last call, open task", lk["found"] and lk["card"]["name"] == "אמא"
          and lk["card"]["headline"].startswith(("Last spoke", "Last message")) and lk["card"]["last_call"]
          and "Buy wine for אמא" in lk["card"]["open"], lk)
    check("lookup: no model call, fast", len(seen["model"]) == before and took < 0.5, f"{took:.3f}s")
    check("lookup: unknown number → not found", detail.lookup(OWNER, "+15550001111") == {"found": False})
    check("lookup: number-only person without a ready call → not found", not detail.lookup(OWNER, "+97231234567")["found"]
          or detail.lookup(OWNER, "+97231234567")["card"]["last_call"])
    check("lookup: matches on the last 9 digits", people_store.find_by_phone(OWNER, "521234567") == mom)

    # --- briefs ---------------------------------------------------------------------
    n = await people_briefs.refresh_briefs(OWNER)
    briefs = [k for k, _ in seen["model"] if k == "brief"]
    check("brief: written for people with enough activity", n == 2 and len(briefs) == 2, (n, briefs))
    p = detail.get_person(OWNER, gal, schedule_brief=False)
    check("brief: cleaned points + open loops", p["brief"]["points"][0] == "Planning the wedding venue"
          and p["brief"]["points"][1] == "Asked about Friday dinner" and p["brief"]["open"] == ["Robin to send the caterer quote"])
    check("brief: not rewritten when nothing changed", await people_briefs.refresh_briefs(OWNER) == 0 and not p["brief_stale"])
    mom_prompt = [pr for k, pr in seen["model"] if k == "brief" and "אמא" in pr.split("\n")[0]]
    gal_prompt = [pr for k, pr in seen["model"] if k == "brief" and "Wedding call" in pr]
    check("brief: in the user's first language (Hebrew for Robin), whatever the calls' language",
          mom_prompt and "Write in Hebrew, whatever language" in mom_prompt[0]
          and gal_prompt and "Write in Hebrew, whatever language" in gal_prompt[0])
    german = users.update(OWNER.id, languages="de,en")
    with db.get_db_connection() as conn:
        sig_he = people_briefs.signature(conn, OWNER, people_store.row(conn, OWNER, gal))
        g_prompt = people_briefs.brief_prompt(conn, german, people_store.row(conn, german, gal))
        sig_de = people_briefs.signature(conn, german, people_store.row(conn, german, gal))
    users.update(OWNER.id, languages=testenv.OWNER_LANGUAGES)
    check("brief: a German-first user's brief is in German; a language change rewrites it",
          "Write in German, whatever language" in g_prompt and sig_he and sig_de and sig_he != sig_de)
    add_call("gal-new", "גל כהן 🚀❤️🔥", "0501112222", 0, title="Back in touch")
    linking.link_pending(OWNER)
    check("brief: new call makes it stale", detail.get_person(OWNER, gal, schedule_brief=False)["brief_stale"])
    seen["fail"].add("brief")
    check("brief: failure keeps the old brief", await people_briefs.refresh_briefs(OWNER, [gal]) == 0
          and detail.get_person(OWNER, gal, schedule_brief=False)["brief"]["points"])
    seen["fail"].discard("brief")
    card = detail.card(OWNER, gal)
    check("card: brief open loop + points included", card["open"][0] == "Robin to send the caterer quote" and card["points"])

    # --- update / resolve / merge / delete --------------------------------------------
    up = people_store.update_person(OWNER, gal, {"name": "Gal", "relation": "partner", "birthday": "03-14", "pinned": True,
                                    "add_alias": "גל"})
    check("update: rename keeps the old name as an alias", up["name"] == "Gal" and "גל כהן 🚀❤️🔥" in up["aliases"]
          and up["pinned"] and up["relation"] == "partner")
    check("update: bad birthday rejected", _raises(lambda: people_store.update_person(OWNER, gal, {"birthday": "14/3"}), ValueError))
    check("resolve: by alias, by English name, by phone, fuzzy", people_store.resolve(OWNER, "גל") == gal and people_store.resolve(OWNER, "gal") == gal
          and people_store.resolve(OWNER, "+972501112222") == gal and people_store.resolve(OWNER, "Gall") == gal)
    check("mentions: whole-word names in event titles", people_store.mentioned_in(OWNER, "Dinner with Gal at 8") == [gal]
          and people_store.mentioned_in(OWNER, "Galbert's party") == [])
    dup = people_store.create_person(OWNER, "Gal Levi", phones=["054-999-8888"])
    add_note("n-dup", "Gal Levi", "on my way")
    linking.link_pending(OWNER)
    cands = merge.merge_candidates(OWNER)
    check("merge: candidates find the near-duplicate", any({c["a"]["id"], c["b"]["id"]} == {gal, dup["id"]} for c in cands), cands)
    merged = merge.merge_people(OWNER, gal, dup["id"])
    check("merge: numbers, names and activity move over", "972549998888" in merged["phones"] and "Gal Levi" in merged["aliases"]
          and person_of("notifications", "n-dup") == gal)
    check("merge: the other person is gone", detail.get_person(OWNER, dup["id"]) is None)
    check("phones: a number can't belong to two people",
          _raises(lambda: people_store.create_person(OWNER, "X", phones=["0501112222"]), ValueError))
    tmp = people_store.create_person(OWNER, "Temp Person", phones=["0539990000"])
    people_store.delete_person(OWNER, tmp["id"])
    add_call("tmp-call", "Temp Person", "0539990000", 0)
    linking.link_pending(OWNER)
    check("delete: removed and activity left unmatched", detail.get_person(OWNER, tmp["id"]) is None
          and people_store.find_by_phone(OWNER, "0539990000") is not None)  # a new call recreates them (they're a real caller)

    # --- API ------------------------------------------------------------------------
    async with TestClient(TestServer(build_app())) as client:
        r = await client.get("/api/people")
        body = await r.json()
        check("api: list", r.status == 200 and any(p["id"] == gal for p in body["people"]))
        r = await client.get(f"/api/people/{gal}")
        d = await r.json()
        check("api: detail has timeline, tasks, stats", r.status == 200 and d["timeline"] and "stats" in d
              and d["timeline"][0]["at"] >= d["timeline"][-1]["at"])
        r = await client.get("/api/people/lookup", params={"phone": "+972521234567"})
        check("api: lookup", (await r.json())["found"])
        r = await client.post("/api/people", json={"name": "Dana Friend", "phones": ["0541112233"], "relation": "friend"})
        newp = await r.json()
        check("api: create", r.status == 201 and newp["phones"] == ["972541112233"])
        r = await client.put(f"/api/people/{newp['id']}", json={"notes": "Met at Wix", "phones": ["0541112244"]})
        check("api: update replaces phones", (await r.json())["phones"] == ["972541112244"])
        r = await client.post(f"/api/people/{newp['id']}/merge", json={"other_id": newp["id"]})
        check("api: merge into self rejected", r.status == 400)
        r = await client.delete(f"/api/people/{newp['id']}")
        check("api: delete", r.status == 200 and (await client.get(f"/api/people/{newp['id']}")).status == 404)
        r = await client.post("/api/tasks/t-mom/links", json={"ref_type": "person", "ref_id": gal})
        check("api: task linked to a person", r.status == 200 and any(l["ref_type"] == "person" and l["title"] == "Gal"
                                                                     for l in (await r.json())["links"]))
        check("tasks: linked task shows on the person", any(t["id"] == "t-mom" for t in detail.get_person(OWNER, gal)["tasks"]))
        r = await client.post("/api/chat/context", json={"kind": "person", "id": gal})
        ctx = await r.json()
        check("context chat: person card", r.status == 200 and ctx["context"]["kind"] == "person"
              and ctx["context"]["title"] == "Gal")
        sp = context_chats.session_context_prompt(OWNER, ctx["session_id"]) or ""
        check("context chat: person prompt has calls and brief", "Opened from a person: Gal" in sp and "Wedding call" in sp)

        # snooze count
        add_task("t-snooze", "Fix the gate")
        for _ in range(3):
            await client.post("/api/tasks/t-snooze/snooze", json={"type": "time", "minutes": 10})
        with get_db_connection() as conn:
            sc = conn.execute("SELECT snooze_count FROM tasks WHERE id = 't-snooze'").fetchone()[0]
        check("snooze: count goes up per snooze", sc == 3)

        # --- agent tools ---------------------------------------------------------------
        out = await registry.execute(OWNER, "get_person", {"person": "אמא"})
        check("tool: get_person", "אמא" in out and "Recent calls" in out and "שישי" in out, out[:300])
        out = await registry.execute(OWNER, "list_people", {"sort": "recent"})
        check("tool: list_people", "Gal" in out and "אמא" in out)
        check("tool: update/link are write tools, reads aren't", registry.is_write_tool("update_person")
              and registry.is_write_tool("link_task_to_person") and not registry.is_write_tool("get_person"))

        # --- briefing --------------------------------------------------------------------
        seen["events"] = [
            {"id": "e1", "title": "Standup", "start": NOW.replace(hour=9, minute=0, second=0, microsecond=0).isoformat(),
             "end": NOW.replace(hour=9, minute=30, second=0, microsecond=0).isoformat(), "all_day": False,
             "location": "", "attendees": [], "calendar_id": "primary"}]
        add_task("t-overdue", "Renew passport", due=(TODAY - datetime.timedelta(days=4)).isoformat(), updated_days_ago=30)
        d = await briefing.gather(OWNER)
        check("briefing: gathers calendar, due/overdue tasks, weather", d["events"][0]["title"] == "Standup"
              and any(t["overdue"] for t in d["tasks"]["due"]) and any(not t["overdue"] for t in d["tasks"]["due"])
              and d["weather"]["high"] == 24 and d["weather"]["condition"] == "rainy")
        plain = briefing.plain_briefing(d)
        check("briefing: plain version", plain["headline"].startswith(d["weekday"]) and any("Renew passport" in p for p in plain["points"]))
        seen["fail"].add("briefing")
        b = await briefing.build_and_store(OWNER, "manual")
        check("briefing: model failure → plain version stored", b["headline"] == plain["headline"] and not b["data"]["model"])
        seen["fail"].discard("briefing")
        r = await client.get("/api/briefing/today")
        check("api: briefing today", (await r.json())["briefing"]["date"] == TODAY.isoformat())

        # --- wake flow ---------------------------------------------------------------------
        with get_db_connection() as conn:
            conn.execute("DELETE FROM briefings")
            conn.commit()
        add_task("t-wake", "Take vitamins", triggers={"wake": True})
        wake.WAKE_WINDOW = (0, 24)

        def clear_wake():
            with get_db_connection() as conn:
                conn.execute("DELETE FROM wake_log WHERE user_id = ?", (OWNER.id,))
                conn.commit()

        # Unlock, then the screen was on only 30 s (dismissed the alarm, back to sleep).
        seen["history"] = [(40, "off"), (30, "on"), (0, "off")]
        status = await wake.handle_wake(OWNER, source="unlock")
        await asyncio.sleep(0.05)
        check("wake: unlock but screen barely on → nothing fired", status == "confirming" and not pushes("reminder") and not pushes("briefing"))
        check("wake: not recorded, so the next unlock retries", not wake.done_today(OWNER, clock.now().date().isoformat()))
        # The alarm screen alone (one long session) never trips the fallback poll.
        seen["history"] = [(1500, "off"), (1200, "on"), (600, "off")]
        check("wake: alarm-length screen doesn't trip the fallback", not await wake.check_wake_fallback(OWNER))
        # Next unlock through the API: screen on 4 min since → up.
        seen["history"] = [(400, "off"), (300, "on"), (60, "off"), (40, "on"), (0, "on")]
        r = await client.post("/api/phone/unlock", json={"at": int((time.time() - 300) * 1000)})
        check("api: phone unlock → confirming", (await r.json())["status"] == "confirming")
        for _ in range(100):
            if pushes("briefing") and (briefing.get_today(OWNER) or {}).get("delivered_at"):
                break
            await asyncio.sleep(0.02)
        rem = [p for p in pushes("reminder") if p["data"]["task_id"] == "t-wake"]
        bp = pushes("briefing")
        check("wake: awake → wake task fired and briefing pushed", len(rem) == 1 and len(bp) == 1, (rem, bp))
        check("wake: briefing used the model", bp and bp[0]["data"]["headline"] == "Busy morning, free after 2"
              and bp[0]["data"]["next_event"] == "09:00 Standup")
        check("wake: once a day", await wake.handle_wake(OWNER, source="unlock") == "done"
              and not await wake.check_wake_fallback(OWNER))
        check("wake: briefing marked delivered", briefing.get_today(OWNER)["delivered_at"])
        # Fallback poll: several real sessions in the last 30 min → up, without any signal.
        clear_wake()
        seen["history"] = [(1700, "on"), (1500, "off"), (900, "on"), (700, "off"), (300, "on"), (100, "off")]
        check("wake: fallback poll fires on real use", await wake.check_wake_fallback(OWNER))
        check("wake: fallback recorded today's wake", wake.done_today(OWNER, clock.now().date().isoformat()))
        # HA down: an unlock is trusted.
        clear_wake()
        seen["history"] = None
        await wake.handle_wake(OWNER, source="unlock")
        for _ in range(100):
            if wake.done_today(OWNER, clock.now().date().isoformat()):
                break
            await asyncio.sleep(0.02)
        check("wake: no HA history → unlock trusted", wake.done_today(OWNER, clock.now().date().isoformat()))
        seen["history"] = []
        wake.WAKE_WINDOW = (25, 26)
        clear_wake()
        check("wake: outside the window ignored", await wake.handle_wake(OWNER) == "outside_window")
        # on_wake after a fallback briefing rebuilds it with fresh data and pushes it once.
        with get_db_connection() as conn:
            conn.execute("UPDATE briefings SET trigger = 'fallback', delivered_at = NULL")
            conn.commit()
        await briefing.on_wake(OWNER)
        check("wake: fallback briefing rebuilt and pushed", len(pushes("briefing")) == 2 and briefing.get_today(OWNER)["trigger"] == "wake")
        await briefing.on_wake(OWNER)
        check("wake: never pushed twice", len(pushes("briefing")) == 2)

        # --- meeting cards -----------------------------------------------------------------
        soon = NOW + datetime.timedelta(minutes=8)
        seen["events"] = [{"id": "m1", "title": "Dinner with Gal", "start": soon.isoformat(), "end": soon.isoformat(),
                           "all_day": False, "location": "", "attendees": [], "calendar_id": "primary"},
                          {"id": "m2", "title": "Dentist", "start": soon.isoformat(), "end": soon.isoformat(),
                           "all_day": False, "location": "", "attendees": [], "calendar_id": "primary"}]
        sent = await briefing.check_meetings(OWNER, NOW)
        cc = pushes("context_card")
        check("meeting card: pushed for the person in the title", sent == 1 and cc[0]["data"]["card"]["name"] == "Gal"
              and cc[0]["data"]["event_title"] == "Dinner with Gal")
        check("meeting card: once per event", await briefing.check_meetings(OWNER, NOW) == 0)

        # --- weekly review -------------------------------------------------------------------
        add_task("t-stale", "Clean the storage room", updated_days_ago=40)
        add_task("t-waiting", "Buy batteries at the store", triggers={"location": {"location": "store", "event": "arrive"}},
                 updated_days_ago=40)
        add_task("t-done", "Old thing", status="completed", updated_days_ago=2)
        with get_db_connection() as conn:
            conn.execute("INSERT INTO call_suggestions (id, user_id, call_id, kind, payload, status, created_at) VALUES "
                         "('s-old', ?, 'mom-1', 'task', '{\"title\": \"Call the plumber\"}', 'pending', ?)",
                         (OWNER.id, (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S"),))
            conn.commit()
        for i, dd in enumerate(range(90, 60, -4)):  # Noa: every ~4 days, then nothing for 2 months
            add_call(f"noa-{i}", "Noa", "0547778888", dd)
        linking.link_pending(OWNER)
        noa = people_store.find_by_phone(OWNER, "0547778888")
        c = weekly_review.candidates(OWNER)
        whys = {i["ref"]: i["why"] for i in c["items"]}
        check("review: overdue, snoozed, stale, old suggestion, drifting found",
              whys.get("t-overdue") == "overdue" and whys.get("t-snooze") == "snoozed_often" and whys.get("t-stale") == "stale"
              and whys.get("s-old") == "old_suggestion" and whys.get(noa) == "drifting" and "t-done" not in whys, whys)
        check("review: done this week counted", c["stats"]["done"] >= 1 and "Old thing" in c["stats"]["done_titles"])
        rv = await weekly_review.generate(OWNER)
        items = {i["ref"]: i for i in rv["items"]}
        check("review: invalid model date replaced with a future one", items["t-overdue"]["action"] == "reschedule"
              and items["t-overdue"]["due_date"] > TODAY.isoformat())
        check("review: unknown action falls back to the default (keep, unticked)",
              items["t-snooze"]["action"] == "keep" and not items["t-snooze"]["enabled"])
        check("review: move_group validated", items["t-stale"]["action"] == "move_group" and items["t-stale"]["group"] == "Home")
        check("review: headline + summary from the model", rv["headline"] == "Solid week" and rv["summary"][0] == "5 done")
        rv2 = await weekly_review.generate(OWNER)
        check("review: regenerating supersedes the pending one", weekly_review.get_review(OWNER, rv["id"])["status"] == "superseded"
              and weekly_review.latest(OWNER)["id"] == rv2["id"])
        await weekly_review.deliver(rv2, OWNER)
        check("review: pushed to the phone", pushes("weekly_review")[-1]["data"]["id"] == rv2["id"])
        r = await client.post("/api/chat/context", json={"kind": "review", "id": rv2["id"]})
        rctx = await r.json()
        check("context chat: review", r.status == 200 and "Weekly review" in rctx["context"]["title"]
              and "Renew passport" in (context_chats.session_context_prompt(OWNER, rctx["session_id"]) or ""))
        r = await client.post(f"/api/review/{rv2['id']}/apply", json={"items": [
            {"ref": "t-stale", "enabled": False},
            {"ref": "t-snooze", "action": "complete", "enabled": True},
        ]})
        res = await r.json()
        with get_db_connection() as conn:
            t = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM tasks")}
            sug = conn.execute("SELECT status FROM call_suggestions WHERE id = 's-old'").fetchone()["status"]
            reach = conn.execute("SELECT t.title FROM tasks t JOIN task_links l ON l.task_id = t.id "
                                 "WHERE l.ref_type = 'person' AND l.ref_id = ? AND t.title LIKE '%Reach out to Noa%'", (noa,)).fetchall()
        check("apply: reschedule sets due date and resets snoozes", t["t-overdue"]["due_date"] > TODAY.isoformat())
        check("apply: client override (complete) wins; unticked item untouched",
              t["t-snooze"]["status"] == "completed" and t["t-stale"]["group_id"] == "general")
        check("apply: suggestion dismissed, reach-out task linked to the person", sug == "dismissed" and len(reach) == 1,
              (sug, reach, res))
        check("apply: result recorded, can't apply twice", res["failed"] == 0 and res["applied"] >= 4
              and (await client.post(f"/api/review/{rv2['id']}/apply", json={})).status == 400, res)
        check("apply: completion clears the reminder on the phone", any(p["data"].get("task_id") == "t-snooze"
                                                                       for p in pushes("reminder_clear")))

    # schedule
    sat = datetime.datetime(2026, 11, 14, 20, 5, tzinfo=clock.get_timezone())
    check("review schedule: Saturday 20:05 due, 19:55 not",
          weekly_review.is_due(sat) and not weekly_review.is_due(sat.replace(hour=19, minute=55)))
    check("review schedule: Sunday-morning catch-up for Saturday's week",
          weekly_review.is_due(sat + datetime.timedelta(hours=13)) and not weekly_review.is_due(sat + datetime.timedelta(hours=17))
          and weekly_review.review_day(sat + datetime.timedelta(hours=13)) == sat.date())

    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    return False


if __name__ == "__main__":
    # As in production, the work runs as a user: requests as theirs, background
    # loops as the first admin (the interim owner of the phone's data).
    with users.acting_as(OWNER):
        asyncio.run(main())
