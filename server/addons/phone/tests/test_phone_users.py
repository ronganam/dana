"""The phone addon per user: devices, direct push and the notifications Inbox.

Two users with the ``phone`` addon (the first admin, converted from the 9.0
fixture, and Bob) and one without it (Carl), on the real handlers: devices
register with their own token and fetch only their own events (catch-up,
expiry, dead tokens, the first admin's seq continuing across the migration);
``send(user, …)`` reaches only that user's devices and queue (nothing stored
for a user without the addon); notifications ingested, deduped, redacted,
triaged (questions worded for their user), bucketed, escalated (rate limit
per user), turned into suggestions, summarized (one model call per app per
user, the prompt naming only that user), accepted / dismissed / bulk
dismissed, synced back to the shade, ruled / declined, searched (FTS),
overviewed and used by the agent tools — each isolated in both directions;
every loop (retention, digest, re-nudge, summaries, triage resume) per user
with usage rows carrying the right user and a failing user not stopping the
next; a task reminder, a WhatsApp voice transcript and the briefing pushed
only to their user's phone; people linking only for the notification's
owner; no addon → 403 ``not_connected`` and no tools.

TypeSafe and FCM are faked at httpx; the model is LiteLLM's offline
``mock_response`` (so the real usage callback records each call).

Run: bin/test test_phone_users
"""

import asyncio
import datetime
import json
import os
import re
import sqlite3
import sys
import time
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("phone_users_test")
ROOT = str(testenv.SERVER)
os.environ["API_TOKEN"] = "phone-users-test-env-token"  # a test value: the first admin's token (a headless install)
os.environ["ANTHROPIC_API_KEY"] = "test-key"  # a test value: LiteLLM only ever runs its offline mock here
os.environ["TYPESAFE_API_KEY"] = "test-typesafe"
os.environ["FCM_SERVICE_ACCOUNT_JSON"] = '{"project_id": "dana-test"}'
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, ROOT)

# A 9.0 production database (converted at start): its devices, events and notifications are the first admin's.
_conn = sqlite3.connect(DB_PATH)
with open(os.path.join(testenv.FIXTURES, "db_9_0.sql"), encoding="utf-8") as _f:
    _conn.executescript(_f.read())
_conn.close()

import httpx
import litellm
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, devices, users

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap

addons_bootstrap.bootstrap()

from core import sessions_api
from addons.phone import api as phone_api
from addons.phone.notifications import api as notifications_api
from core import briefing, checks, clock, notify as core_notify, now as now_mod, search, usage, user_profile, user_rows
from core.auth import setup_auth
from core.db import get_db_connection
from core.addons import Connection, registry, store
from core.addons.registry import ToolNotAvailable
from addons.people import linking, store as people_store
from addons.phone import config as phone_config, push
from addons.phone.notifications import digest, escalation, processing, retention, shade, summary, triage
from addons.phone.notifications.values import ESCALATIONS_PER_HOUR
from addons.tasks import groups as task_groups, titles as task_titles, triggers as task_triggers
from addons.whatsapp import events as wa_events

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


LOOP = asyncio.new_event_loop()


def run(coro):
    return LOOP.run_until_complete(coro)


ADMIN = users.first_admin()
assert ADMIN is not None
NOTIFICATION_TOOLS = {"search_notifications", "list_notifications", "summarize_notifications",
                      "dismiss_notifications", "snooze_notifications"}


# --- 1. Migration ---------------------------------------------------------------------------------

def test_migration() -> None:
    with get_db_connection() as conn:
        owners = {t: {r[0] for r in conn.execute(f"SELECT DISTINCT user_id FROM {t}")}
                  for t in ("devices", "phone_push", "phone_events", "notifications", "notification_rules",
                            "notification_summaries", "notification_rule_declines", "notification_action_requests")}
        fts = [r[0] for r in conn.execute("SELECT id FROM notifications_fts WHERE notifications_fts MATCH '\"Friday\"*'")]
    check("migration: devices, events, notifications, rules, summaries, declines, requests are the first admin's",
          all(v == {ADMIN.id} for v in owners.values()), owners)
    check("migration: the FTS index still finds the first admin's notification", fts == ["n1"], fts)
    check("migration: the first admin's latest seq is the fixture's", push.latest_seq(ADMIN) == 1)
    moved = push.devices(ADMIN)
    check("migration: the fixture's phone is a core device (kind phone, not paired) with its push registration",
          [(d["name"], d["kind"], d["app_version"], d["last_seq"]) for d in moved] == [("Test phone", "phone", "8.1.0", 1)]
          and not devices.get(moved[0]["device_id"]).paired, moved)
    FIXTURE_DEVICE.append(moved[0]["device_id"] if moved else "")


FIXTURE_DEVICE: List[str] = []


test_migration()

BOB = users.create("Bob Stone")
CARL = users.create("Carl")
for _kind in ("phone", "tasks", "people"):
    store.connect(BOB, _kind)
store.connect(CARL, "tasks")
task_groups.ensure_default_groups_for_everyone()
# Each user's paired phone: its own token (core/devices.py).
PAIRED = {u.id: devices.create(u, "phone", f"{u.name}'s phone") for u in (ADMIN, BOB, CARL)}
TOKENS = {uid: token for uid, (token, _) in PAIRED.items()}
DEV = {uid: device.id for uid, (_, device) in PAIRED.items()}
SCRIPT_TOKEN = users.issue_token(BOB.id, "script")[0]  # a token of no device


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[user.id]}"}


# --- Fakes ----------------------------------------------------------------------------------------

SEEN: Dict[str, Any] = {"pings": [], "typesafe": [], "model": [], "summary_fail_for": ""}


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


def noul(p):
    return {"type": "noul", "noul": p}


def answers_for(text: str) -> Dict[str, Any]:
    if "door" in text:
        return {"category": {"type": "choice", "choice": "delivery", "confidence": 0.9, "probabilities": {}},
                "priority": {"type": "score", "score": 3.8, "probabilities": {}},
                "needs_action": noul(0.8), "from_person": noul(0.1), "time_sensitive": noul(0.95), "promotional": noul(0.0)}
    if "milk" in text:
        return {"category": {"type": "choice", "choice": "personal_message", "confidence": 0.9, "probabilities": {}},
                "priority": {"type": "score", "score": 2.8, "probabilities": {}},
                "needs_action": noul(0.92), "from_person": noul(0.97), "time_sensitive": noul(0.4), "promotional": noul(0.01)}
    return {"category": {"type": "choice", "choice": "news", "confidence": 0.6, "probabilities": {}},
            "priority": {"type": "score", "score": 1.8, "probabilities": {}},
            "needs_action": noul(0.1), "from_person": noul(0.5), "time_sensitive": noul(0.1), "promotional": noul(0.1)}


async def fake_post(self, url, *args, **kwargs):
    body = kwargs.get("json") or {}
    if "typesafe" in url:
        SEEN["typesafe"].append({"user": users.current_id(), "body": body})
        text = json.dumps(body["state"]["notification"], ensure_ascii=False)
        return FakeResponse({"model": "jev-1.14.0", "answers": answers_for(text), "usage": {}})
    if "fcm.googleapis.com" in url:
        msg = body["message"]
        with get_db_connection() as conn:
            ev = conn.execute("SELECT user_id, type FROM phone_events WHERE seq = ?", (int(msg["data"]["seq"]),)).fetchone()
        SEEN["pings"].append({"token": msg["token"], "seq": int(msg["data"]["seq"]), "type": msg["data"]["type"],
                              "event_user": ev["user_id"] if ev else None})
        if SEEN.get("gone") == msg["token"]:
            return FakeResponse({"error": {"code": 404, "status": "NOT_FOUND", "message": "gone",
                                           "details": [{"errorCode": "UNREGISTERED"}]}}, status=404)
        return FakeResponse({"name": "projects/dana-test/messages/1"})
    raise AssertionError(f"unexpected POST {url}")


httpx.AsyncClient.post = fake_post


async def fake_fcm_token():
    return "fcm-test-token"

push._access_token = fake_fcm_token


_real_acompletion = litellm.acompletion
TOMORROW = (clock.now() + datetime.timedelta(days=1)).date().isoformat()


async def fake_acompletion(**kwargs):
    """LiteLLM's offline mock, so the real usage callback records the call."""
    props = set(kwargs["response_format"]["json_schema"]["schema"]["properties"])
    prompt = kwargs["messages"][0]["content"]
    uid = users.current_id() or ""
    if props in ({"summary"}, {"overall"}):
        field = next(iter(props))
        SEEN["model"].append({"user": uid, "field": field, "prompt": prompt})
        if SEEN["summary_fail_for"] == uid:
            kwargs["mock_response"] = Exception("model down")
        else:
            app = (re.findall(r'from the app "([^"]+)"', prompt) or ["all"])[0]
            kwargs["mock_response"] = json.dumps({field: [f"{field} {app} for {uid[:4]}"]})
    else:
        SEEN["model"].append({"user": uid, "field": "items", "prompt": prompt})
        # Only the milk message holds an action item (the courier one must not get one,
        # whichever order the background extractions finish in).
        items = [{"text": "Buy milk", "owner": "me", "kind": "task", "due_date": TOMORROW, "priority": "medium",
                  "confidence": 0.85, "evidence": "can you buy milk"}] if "milk" in prompt else []
        kwargs["mock_response"] = json.dumps({"action_items": items})
    return await _real_acompletion(**kwargs)


litellm.acompletion = fake_acompletion
litellm.suppress_debug_info = True


async def plain_title(t):
    return t


async def plain_group(user, group, title):
    return task_groups.default_group_id(user)

task_titles.sanitize_task_title = plain_title
task_titles.resolve_task_group = plain_group


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    r = app.router
    r.add_get("/api/suggestions", phone_api.get_suggestions_api)
    r.add_post("/api/suggestions/bulk", phone_api.bulk_suggestions_api)
    r.add_post("/api/suggestions/{id}/accept", phone_api.accept_suggestion_api)
    r.add_post("/api/suggestions/{id}/dismiss", phone_api.dismiss_suggestion_api)
    r.add_post("/api/notifications/batch", notifications_api.notifications_batch_api)
    r.add_get("/api/notifications", notifications_api.notifications_list_api)
    r.add_delete("/api/notifications", notifications_api.notifications_delete_app_api)
    r.add_post("/api/notifications/status", notifications_api.notifications_status_api)
    r.add_post("/api/notifications/snooze", notifications_api.notifications_snooze_api)
    r.add_get("/api/notifications/overview", notifications_api.notifications_overview_api)
    r.add_post("/api/notifications/rule-suggestions", notifications_api.notifications_rule_suggestion_api)
    r.add_get("/api/notifications/apps", notifications_api.notifications_apps_api)
    r.add_post("/api/notifications/rules", notifications_api.notifications_rule_api)
    r.add_post("/api/notifications/seen", notifications_api.notifications_seen_api)
    r.add_get("/api/notifications/digest", notifications_api.notifications_digest_api)
    r.add_get("/api/notifications/summaries", notifications_api.notifications_summaries_api)
    r.add_get("/api/notifications/requests/{rid}", notifications_api.notification_request_api)
    r.add_post("/api/notifications/requests/{rid}/result", notifications_api.notification_request_result_api)
    r.add_post("/api/notifications/{id}/act", notifications_api.notification_act_api)
    r.add_get("/api/notifications/{id}", notifications_api.notification_detail_api)
    r.add_post("/api/notifications/{id}/triage", notifications_api.notifications_retriage_api)
    r.add_post("/api/phone/register", phone_api.phone_register_api)
    r.add_get("/api/phone/events", phone_api.phone_events_api)
    r.add_get("/api/phone/devices", phone_api.phone_devices_api)
    r.add_post("/api/phone/test", phone_api.phone_test_api)
    r.add_post("/api/chat/context", sessions_api.start_context_chat_api)
    return app


def note(key: str, text: str, title: str = "Gal", package: str = "com.whatsapp", app: str = "WhatsApp", **kw):
    return {"key": key, "package": package, "app_name": app, "title": title, "text": text,
            "posted_at": int(clock.now().timestamp() * 1000), **kw}


async def settle() -> None:
    for _ in range(300):
        if not processing._tasks:
            return
        await asyncio.sleep(0.02)


def nrow(nid: str) -> Dict[str, Any]:
    with get_db_connection() as conn:
        return dict(conn.execute("SELECT * FROM notifications WHERE id = ?", (nid,)).fetchone())


def pings_since(n: int) -> List[Dict[str, Any]]:
    return SEEN["pings"][n:]


async def usage_rows(purpose: str) -> List[Dict[str, Any]]:
    for _ in range(20):  # the usage callback records after the call returns
        await asyncio.sleep(0.05)
    await usage.flush()
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT user_id, ok, ref FROM usage_events WHERE purpose = ? ORDER BY id",
                                              (purpose,))]


def events_of(user_id: str) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT seq, type, user_id FROM phone_events WHERE user_id = ? ORDER BY seq",
                                              (user_id,))]


# --- 2. Devices and push --------------------------------------------------------------------------

async def test_push(client: TestClient) -> None:
    r = await client.post("/api/phone/register", headers=auth(ADMIN), json={"transport": "fcm", "token": "tok-a", "app_version": "9.0"})
    reg_a = await r.json()
    r = await client.post("/api/phone/register", headers=auth(BOB), json={"transport": "fcm", "token": "tok-b"})
    reg_b = await r.json()
    check("register: each device registers for its token's user; the first admin's latest seq continues",
          reg_a.get("latest_seq") == 1 and reg_b.get("latest_seq") == 0, (reg_a, reg_b))
    r = await client.post("/api/phone/register", headers=auth(CARL), json={"transport": "fcm", "token": "tok-c"})
    check("register: no phone addon → 403 not_connected", r.status == 403
          and (await r.json()).get("error") == "not_connected")
    r = await client.post("/api/phone/register", headers={"Authorization": f"Bearer {SCRIPT_TOKEN}"},
                          json={"transport": "fcm", "token": "tok-script"})
    check("register: a token of no device → 400 device_required", r.status == 400
          and (await r.json()).get("error") == "device_required")
    with get_db_connection() as conn:
        owners = dict(conn.execute("SELECT device_id, user_id FROM phone_push").fetchall())
    check("register: each registration is the request's own device (the fixture's stays the first admin's)",
          owners == {DEV[ADMIN.id]: ADMIN.id, DEV[BOB.id]: BOB.id, FIXTURE_DEVICE[0]: ADMIN.id}, owners)

    n = len(SEEN["pings"])
    ok = await push.send(BOB, "reminder", {"task_id": "t-bob", "task_title": "Bob's thing"})
    got = pings_since(n)
    check("send: reaches only that user's devices", ok and [p["token"] for p in got] == ["tok-b"]
          and got[0]["event_user"] == BOB.id, got)
    seq_bob = got[0]["seq"] if got else 0
    check("send: the server's seq keeps counting after the fixture's", seq_bob == 2, seq_bob)
    n = len(SEEN["pings"])
    ok = await push.send(ADMIN, "reminder", {"task_id": "t-admin", "task_title": "Admin's thing"})
    got = pings_since(n)
    check("send: the first admin's event pings their devices only", ok and sorted(p["token"] for p in got)
          == ["fcm-token-sample", "tok-a"] and all(p["event_user"] == ADMIN.id for p in got), got)
    before = events_of(CARL.id)
    n = len(SEEN["pings"])
    ok = await push.send(CARL, "reminder", {"task_id": "t-carl"})
    check("send: a user without the addon → nothing stored, nothing pinged",
          not ok and events_of(CARL.id) == before == [] and pings_since(n) == [])

    r = await client.get("/api/phone/events?after=0", headers=auth(BOB))
    eb = await r.json()
    r = await client.get("/api/phone/events?after=0", headers=auth(ADMIN))
    ea = await r.json()
    check("events: each user fetches only their own events",
          [e["data"].get("task_id") for e in eb["events"]] == ["t-bob"]
          and "t-bob" not in [e["data"].get("task_id") for e in ea["events"]]
          and "t-admin" in [e["data"].get("task_id") for e in ea["events"]], (eb, ea))
    check("events: latest is the user's own newest seq", eb["latest"] == seq_bob and ea["latest"] > seq_bob,
          (eb["latest"], ea["latest"]))
    r = await client.get(f"/api/phone/events?after=0&device_id={DEV[ADMIN.id]}",
                         headers={"Authorization": f"Bearer {SCRIPT_TOKEN}"})
    other = await r.json()
    with get_db_connection() as conn:
        dev_a = dict(conn.execute("SELECT user_id, last_seq FROM phone_push WHERE device_id = ?",
                                  (DEV[ADMIN.id],)).fetchone())
    check("events: registered says whether the request's device is registered; a device id in the query is ignored",
          ea.get("registered") is True and eb.get("registered") is True and "registered" not in other
          and dev_a == {"user_id": ADMIN.id, "last_seq": ea["latest"]}, (other, dev_a))
    r = await client.get("/api/phone/events?after=0", headers=auth(CARL))
    check("events: no phone addon → 403", r.status == 403)
    r = await client.get("/api/phone/devices", headers=auth(BOB))
    dv = await r.json()
    check("devices: only the caller's phones, no tokens", [d["device_id"] for d in dv["devices"]] == [DEV[BOB.id]]
          and dv["latest_seq"] == seq_bob and "token" not in json.dumps(dv["devices"]), dv)

    await push.send(BOB, "alert", {"notification_id": "x"})
    await push.send(ADMIN, "alert", {"notification_id": "y"})
    with get_db_connection() as conn:
        conn.execute("UPDATE phone_events SET expires_at = '2000-01-01T00:00:00+00:00' WHERE type = 'alert' "
                     "AND user_id = ?", (BOB.id,))
        conn.commit()
    r = await client.get("/api/phone/events?after=0", headers=auth(BOB))
    bob_types = [e["type"] for e in (await r.json())["events"]]
    r = await client.get("/api/phone/events?after=0", headers=auth(ADMIN))
    admin_types = [e["type"] for e in (await r.json())["events"]]
    check("expiry: per user (Bob's expired alert not replayed, the first admin's still is)",
          "alert" not in bob_types and "alert" in admin_types, (bob_types, admin_types))

    r = await client.post("/api/phone/test", headers=auth(BOB))
    check("test push: to the caller's phone", (await r.json()).get("delivered") is True
          and SEEN["pings"][-1]["token"] == "tok-b")
    r = await client.post("/api/phone/test", headers=auth(CARL))
    check("test push: no phone addon → 403", r.status == 403)

    SEEN["gone"] = "tok-b"
    try:
        ok = await push.send(BOB, "reminder", {"task_id": "t-bob-2"})
    finally:
        SEEN["gone"] = None
    check("dead token: only that user's push registration is removed (the device stays)", not ok and push.devices(BOB) == []
          and {d["device_id"] for d in push.devices(ADMIN)} == {FIXTURE_DEVICE[0], DEV[ADMIN.id]}
          and devices.get(DEV[BOB.id]) is not None, (push.devices(BOB), push.devices(ADMIN)))
    with get_db_connection() as conn:
        marks = {r["key"]: r["ok"] for r in conn.execute("SELECT key, ok FROM system_marks WHERE key LIKE 'push:%'")}
    check("push mark per user (Bob's failure doesn't mark the first admin's)",
          marks.get(f"push:{BOB.id}") == 0 and marks.get(f"push:{ADMIN.id}") == 1, marks)
    with users.acting_as(BOB):
        bob_checks = {c["key"]: c for c in checks.compute(user=BOB)}
    admin_checks = {c["key"]: c for c in checks.compute(user=ADMIN)}
    carl_checks = {c["key"]: c for c in checks.compute(user=CARL)}
    check("self-check: push per user (Bob: no phone registered; the first admin fine; Carl: not connected)",
          not bob_checks["push"]["ok"] and admin_checks["push"]["ok"]
          and carl_checks["push"]["ok"] and carl_checks["push"]["detail"] == "not connected",
          (bob_checks["push"]["detail"], admin_checks["push"]["detail"], carl_checks["push"]["detail"]))
    await client.post("/api/phone/register", headers=auth(BOB), json={"transport": "fcm", "token": "tok-b"})


# --- 3. Notifications -----------------------------------------------------------------------------

IDS: Dict[str, Dict[str, str]] = {}


async def batch(client: TestClient, user, posted, **kw) -> Dict[str, Any]:
    r = await client.post("/api/notifications/batch", headers=auth(user), json={"posted": posted, **kw})
    out = await r.json()
    await settle()
    return out


async def test_notifications(client: TestClient) -> None:
    shared = note("g1", "can you buy milk on the way?", conversation="Family", is_group=True, sender="Gal")
    a = await batch(client, ADMIN, [shared, note("a-door", "The courier is at the door", title="Wolt", package="com.wolt",
                                                 app="Wolt"), note("a-only", "Admin secret plans", title="Noa")])
    n_pings = len(SEEN["pings"])
    escalation._escalations[BOB.id] = [time.time()] * ESCALATIONS_PER_HOUR  # Bob already hit his hourly limit
    b = await batch(client, BOB, [shared, note("b-door", "The courier is at the door", title="Wolt", package="com.wolt",
                                               app="Wolt"), note("b-only", "Bob zebra code 482913 verification",
                                                                 title="Bank", package="com.bank", app="Bank")])
    IDS["admin"] = dict(zip(("g1", "a-door", "a-only"), a["ids"]))
    IDS["bob"] = dict(zip(("g1", "b-door", "b-only"), b["ids"]))
    check("ingest: the same notification on two phones is stored once for each user",
          a["accepted"] == 3 and b["accepted"] == 3 and IDS["admin"]["g1"] != IDS["bob"]["g1"]
          and nrow(IDS["bob"]["g1"])["user_id"] == BOB.id, (a, b))
    again = await batch(client, BOB, [shared])
    check("ingest: dedupe per user", again["accepted"] == 0 and again["duplicates"] == 1, again)
    r = await client.post("/api/notifications/batch", headers=auth(CARL), json={"posted": [note("c1", "hi")]})
    check("ingest: no phone addon → 403 not_connected", r.status == 403)
    check("ingest: OTP redacted for Bob too", "[code]" in nrow(IDS["bob"]["b-only"])["text"])

    by_user = {}
    for t in SEEN["typesafe"]:
        by_user.setdefault(t["user"], []).append(json.dumps(t["body"], ensure_ascii=False))
    bob_q = " ".join(by_user.get(BOB.id, []))
    admin_q = " ".join(by_user.get(ADMIN.id, []))
    check("triage: runs as each user, questions and state name only that user",
          "Bob" in bob_q and "Robin" not in bob_q and "Robin" in admin_q and "Bob" not in admin_q,
          (bob_q[:300], admin_q[:300]))
    check("triage: questions for the first admin unchanged (worded for Robin)",
          any(set(json.loads(x)["questions"]) == set(triage.questions("Robin")) for x in by_user.get(ADMIN.id, [])))
    tri_usage = await usage_rows("notification_triage")
    tri_users = {r["ref"]: r["user_id"] for r in tri_usage}
    check("triage: usage rows carry the notification's user",
          tri_users.get(f"notification:{IDS['bob']['b-door']}") == BOB.id
          and tri_users.get(f"notification:{IDS['admin']['a-door']}") == ADMIN.id, tri_users)
    check("buckets: each user's urgent one needs them", nrow(IDS["bob"]["b-door"])["bucket"] == "needs_you"
          and nrow(IDS["admin"]["a-door"])["bucket"] == "needs_you")
    bob_alerts = [p for p in pings_since(n_pings) if p["type"] == "alert"]
    check("escalation: rate limit per user (Bob at his limit, the first admin's alert went out)",
          not nrow(IDS["bob"]["b-door"])["escalated_at"] and nrow(IDS["admin"]["a-door"])["escalated_at"]
          and bob_alerts == [], bob_alerts)
    escalation._escalations[BOB.id] = []

    # Lists, detail, search: own only, both directions.
    r = await client.get("/api/notifications?status=all", headers=auth(BOB))
    bl = await r.json()
    r = await client.get("/api/notifications?status=all", headers=auth(ADMIN))
    al = await r.json()
    bob_ids, admin_ids = {n["id"] for n in bl["items"]}, {n["id"] for n in al["items"]}
    check("list: each user sees only their own", bob_ids == set(IDS["bob"].values())
          and not (admin_ids & bob_ids) and set(IDS["admin"].values()) <= admin_ids, (bob_ids, admin_ids))
    check("list: counts are the user's", bl["counts"]["needs_you"] == 2 and bl["counts"]["active"] == 3, bl["counts"])
    r = await client.get(f"/api/notifications/{IDS['admin']['a-only']}", headers=auth(BOB))
    check("detail: another user's notification → 404", r.status == 404)
    r = await client.get(f"/api/notifications/{IDS['bob']['b-only']}", headers=auth(ADMIN))
    check("detail: … in both directions", r.status == 404)
    r = await client.get("/api/notifications?status=all&q=zebra", headers=auth(ADMIN))
    a_hits = (await r.json())["items"]
    r = await client.get("/api/notifications?status=all&q=zebra", headers=auth(BOB))
    b_hits = (await r.json())["items"]
    r = await client.get("/api/notifications?status=all&q=secret", headers=auth(BOB))
    b_secret = (await r.json())["items"]
    check("search (FTS): never another user's notification",
          a_hits == [] and [n["id"] for n in b_hits] == [IDS["bob"]["b-only"]] and b_secret == [], (a_hits, b_secret))
    r = await client.post(f"/api/notifications/{IDS['admin']['a-only']}/triage", headers=auth(BOB))
    check("retriage: another user's notification → 404", r.status == 404)
    r = await client.get("/api/notifications", headers=auth(CARL))
    check("list: no phone addon → 403", r.status == 403)

    # Suggestions from the shared message: one per user, each visible only to its user.
    r = await client.get("/api/suggestions?source=notification", headers=auth(BOB))
    bs = await r.json()
    r = await client.get("/api/suggestions?source=notification", headers=auth(ADMIN))
    as_ = await r.json()
    check("suggestions: extracted per user, visible only to the notification's user (the fixture's stays the "
          "first admin's)", [s["source_id"] for s in bs] == [IDS["bob"]["g1"]]
          and sorted(s["source_id"] for s in as_) == sorted([IDS["admin"]["g1"], "n1"]), (bs, as_))
    extraction = [m for m in SEEN["model"] if m["field"] == "items"]
    bob_x = [m["prompt"] for m in extraction if m["user"] == BOB.id]
    check("suggestions: the extraction prompt names only its user",
          bob_x and "Bob" in bob_x[0] and "Robin" not in bob_x[0] and "About " not in bob_x[0], bob_x[:1])
    r = await client.post(f"/api/suggestions/{bs[0]['id']}/accept", headers=auth(ADMIN), json={})
    check("suggestions: another user's → 404 on accept", r.status == 404)
    r = await client.post(f"/api/suggestions/{bs[0]['id']}/dismiss", headers=auth(ADMIN))
    check("suggestions: … and on dismiss", r.status == 404)
    r = await client.post("/api/suggestions/bulk", headers=auth(ADMIN), json={"ids": [bs[0]["id"]], "action": "dismiss"})
    check("suggestions: bulk dismiss skips another user's", (await r.json())["dismissed"] == 0)
    r = await client.post(f"/api/suggestions/{bs[0]['id']}/accept", headers=auth(BOB), json={})
    acc = await r.json()
    with get_db_connection() as conn:
        t = conn.execute("SELECT user_id FROM tasks WHERE id = ?", (acc.get("task_id"),)).fetchone()
    check("suggestions: Bob accepts his own → his task", r.status == 200 and t and t["user_id"] == BOB.id, acc)

    # Status / bulk dismiss / shade sync.
    r = await client.post("/api/notifications/status", headers=auth(BOB),
                          json={"ids": [IDS["admin"]["a-only"]], "status": "dismissed"})
    check("status: another user's ids are not touched", (await r.json())["updated"] == 0
          and nrow(IDS["admin"]["a-only"])["status"] == "active")
    r = await client.post("/api/notifications/status", headers=auth(BOB), json={"bucket": "needs_you", "status": "dismissed"})
    st = await r.json()
    check("status: a bucket clears only the caller's", st["updated"] == 2
          and nrow(IDS["admin"]["a-door"])["status"] == "active" and nrow(IDS["admin"]["g1"])["status"] == "active"
          and nrow(IDS["bob"]["b-door"])["status"] == "dismissed", st)
    out = await batch(client, ADMIN, [], active_keys=["b-door", "a-only"])
    check("shade sync: cancel keys only from the caller's own dismissals", out["cancel_keys"] == [], out)
    out = await batch(client, BOB, [], active_keys=["b-door", "a-only"])
    check("shade sync: Bob's dismissed one is cancelled on Bob's phone", out["cancel_keys"] == ["b-door"], out)
    r = await client.post("/api/notifications/snooze", headers=auth(BOB),
                          json={"ids": [IDS["admin"]["a-only"]], "minutes": 30})
    check("snooze: another user's ids are not touched", (await r.json())["updated"] == 0)
    r = await client.post(f"/api/notifications/{IDS['admin']['a-door']}/act", headers=auth(BOB), json={"action": "open"})
    check("act: another user's notification → 404", r.status == 404)
    r = await client.post(f"/api/notifications/{IDS['admin']['a-door']}/act", headers=auth(ADMIN), json={"action": "open"})
    req = await r.json()
    r = await client.get(f"/api/notifications/requests/{req['id']}", headers=auth(BOB))
    check("act: the request is only its user's", r.status == 404 and SEEN["pings"][-1]["event_user"] == ADMIN.id)

    # Rules, rule suggestions and declines.
    r = await client.post("/api/notifications/rules", headers=auth(BOB), json={"package": "com.wolt", "action": "mute"})
    rule = await r.json()
    r = await client.get("/api/notifications/apps", headers=auth(ADMIN))
    admin_rules = (await r.json())["rules"]
    check("rules: per user (Bob's mute isn't the first admin's)", rule.get("action") == "mute"
          and not any(x["package"] == "com.wolt" for x in admin_rules), admin_rules)
    r = await client.post("/api/notifications/rule-suggestions", headers=auth(BOB),
                          json={"package": "com.example.news", "accept": False})
    with get_db_connection() as conn:
        declines = sorted(tuple(x) for x in conn.execute("SELECT user_id, package FROM notification_rule_declines"))
    check("declines: per user (the fixture's stays the first admin's, Bob's is his)",
          r.status == 200 and declines == sorted([(ADMIN.id, "com.example.news"), (BOB.id, "com.example.news")]), declines)

    # Overview and summaries.
    summary.MIN_SECONDS = 0
    await batch(client, BOB, [note("b-s1", "Deploy blocked", title="Dana", package="com.slack", app="Slack", sender="Dana"),
                              note("b-s2", "Lunch?", title="Gal", package="com.slack", app="Slack", sender="Gal")])
    await batch(client, ADMIN, [note("a-s1", "Admin roadmap", title="Noa", package="com.slack", app="Slack", sender="Noa"),
                                note("a-s2", "Admin budget", title="Avi", package="com.slack", app="Slack", sender="Avi")])
    n_model = len(SEEN["model"])
    await client.get("/api/notifications/summaries", headers=auth(BOB))
    await client.get("/api/notifications/summaries", headers=auth(ADMIN))
    for u in (BOB, ADMIN):
        task = summary._tasks.get(u.id)
        if task:
            await task
    calls = [m for m in SEEN["model"][n_model:] if m["field"] in ("summary", "overall")]
    bob_calls = [m for m in calls if m["user"] == BOB.id]
    admin_calls = [m for m in calls if m["user"] == ADMIN.id]
    bob_slack = [m["prompt"] for m in bob_calls if 'from the app "Slack"' in m["prompt"]]
    check("summaries: one call per app per user, run as that user",
          len(bob_slack) == 1 and len([m for m in admin_calls if 'from the app "Slack"' in m["prompt"]]) == 1,
          [(m["user"][:4], m["field"]) for m in calls])
    check("summaries: a user's prompt holds only their notifications and names only them",
          "Deploy blocked" in bob_slack[0] and "Admin roadmap" not in bob_slack[0]
          and "Bob Stone's phone" in bob_slack[0] and "Robin" not in bob_slack[0], bob_slack[0][:400])
    r = await client.get("/api/notifications/summaries", headers=auth(BOB))
    bsm = await r.json()
    with get_db_connection() as conn:
        stored = sorted({x[0] for x in conn.execute("SELECT user_id FROM notification_summaries")})
    check("summaries: cached per user", bsm["apps"]["com.slack"]["points"] == [f"summary Slack for {BOB.id[:4]}"]
          and stored == sorted({ADMIN.id, BOB.id}), (bsm["apps"].get("com.slack"), stored))
    sum_users = {r["user_id"] for r in await usage_rows("notification_summary")}
    check("summaries: usage rows carry each user", {ADMIN.id, BOB.id} <= sum_users, sum_users)
    r = await client.get("/api/notifications/overview", headers=auth(BOB))
    ov = await r.json()
    pkgs = {a["package"] for a in ov["apps"]} | {a["package"] for a in ov["quiet_apps"]}
    check("overview: the user's apps, rules and counts only",
          "com.bank" in pkgs and "com.slack" in pkgs and "com.example.shop" not in pkgs
          and any(a["package"] == "com.wolt" for a in ov["quiet_apps"]) and ov["counts"]["total"] == 3, ov["counts"])
    r = await client.get("/api/notifications/overview", headers=auth(CARL))
    check("overview: no phone addon → 403", r.status == 403)

    # Agent tools.
    out = await registry.execute(BOB, "search_notifications", {"query": "zebra"})
    out_a = await registry.execute(ADMIN, "search_notifications", {"query": "zebra"})
    check("tools: search on the acting user's notifications only", "Bank" in str(out) and "No notifications" in str(out_a),
          (out, out_a))
    out = await registry.execute(ADMIN, "dismiss_notifications", {"notification_ids": IDS["bob"]["b-only"]})
    check("tools: dismiss never touches another user's", "Dismissed 0" in str(out)
          and nrow(IDS["bob"]["b-only"])["status"] == "active", out)
    out = await registry.execute(BOB, "list_notifications", {})
    check("tools: list shows the user's counts", "Admin" not in str(out) and "Deploy blocked" in str(out), out)
    names = {s["function"]["name"] for s in registry.tool_schemas(CARL)}
    try:
        await registry.execute(CARL, "list_notifications", {})
        refused = False
    except ToolNotAvailable:
        refused = True
    check("tools: none for a user without the addon (refused like an unknown name)",
          not (names & NOTIFICATION_TOOLS) and refused)

    # Context chat, Now and search.
    r = await client.post("/api/chat/context", headers=auth(BOB), json={"kind": "notification", "id": IDS["admin"]["a-only"]})
    check("context chat: another user's notification → 404", r.status == 404)
    with users.acting_as(BOB):
        bob_now = await now_mod.build(BOB)
        bob_search = search.search(BOB, "zebra")
    with users.acting_as(CARL):
        carl_now = await now_mod.build(CARL)
        carl_search = search.search(CARL, "zebra")
    with users.acting_as(ADMIN):
        admin_search = search.search(ADMIN, "zebra")
    notif_items = [i["ref"]["id"] for i in bob_now["items"] if i["kind"] == "notification"]
    check("Now: needs-you cards and counts are the user's own",
          all(i in IDS["bob"].values() for i in notif_items) and bob_now["notifications"]["total"] == 3, bob_now["notifications"])
    check("Now: nothing about notifications without the addon",
          not [i for i in carl_now["items"] if i["kind"] == "notification"]
          and carl_now["notifications"] == {"total": 0, "noise": 0, "needs_you": 0})
    check("search: notifications only the user's own",
          [x["id"] for x in bob_search if x["kind"] == "notification"] == [IDS["bob"]["b-only"]]
          and not [x for x in admin_search if x["kind"] == "notification"]
          and not [x for x in carl_search if x["kind"] == "notification"])

    # People: linked only into the notification's owner's people.
    gal_bob = people_store.create_person(BOB, "Gal")["id"]
    gal_admin = people_store.create_person(ADMIN, "Gal")["id"]
    linking.link_pending(BOB)
    check("people: Bob's linking links only his notification",
          nrow(IDS["bob"]["g1"])["person_id"] == gal_bob and nrow(IDS["admin"]["g1"])["person_id"] in (None, ""))
    linking.link_pending(ADMIN)
    check("people: the first admin's notification goes to the first admin's Gal",
          nrow(IDS["admin"]["g1"])["person_id"] == gal_admin and nrow(IDS["bob"]["g1"])["person_id"] == gal_bob)

    r = await client.delete("/api/notifications?package=com.bank", headers=auth(ADMIN))
    check("delete app: only the caller's", (await r.json())["deleted"] == 0 and nrow(IDS["bob"]["b-only"]))


# --- 4. Loops per user ----------------------------------------------------------------------------

def old(user, nid: str, days: int) -> None:
    with get_db_connection() as conn:
        conn.execute("INSERT INTO notifications (id, user_id, package, posted_at, content_hash) VALUES (?, ?, 'p', ?, ?)",
                     (nid, user.id, (clock.now() - datetime.timedelta(days=days)).isoformat(timespec="seconds"), nid))
        conn.commit()


def exists(nid: str) -> bool:
    with get_db_connection() as conn:
        return conn.execute("SELECT 1 FROM notifications WHERE id = ?", (nid,)).fetchone() is not None


async def test_loops() -> None:
    check("loops: every user with phone, nobody without it",
          sorted(u.id for u in phone_config.phone_users()) == sorted([ADMIN.id, BOB.id]))
    old(ADMIN, "old-admin", 40)
    old(BOB, "old-bob", 40)
    real_purge = retention.purge_old

    def flaky(user, days=30):
        if user.id == ADMIN.id:
            raise RuntimeError("broken")
        return real_purge(user, days)
    with patch.object(retention, "purge_old", flaky):
        out = await retention.purge_all()
    check("retention: per user, a failing user does not stop the next",
          not exists("old-bob") and exists("old-admin") and out == {BOB.id: 1}, out)
    await retention.purge_all()
    check("retention: the first admin's old one goes on the next run", not exists("old-admin"))

    with get_db_connection() as conn:  # something that needs Bob, so his digest has a reason to go out
        conn.execute("UPDATE notifications SET bucket = 'needs_you', posted_at = ? WHERE id = ?",
                     (clock.now().isoformat(timespec="seconds"), IDS["bob"]["b-only"]))
        conn.commit()
    tell, phone_note = AsyncMock(), AsyncMock(return_value=True)
    slot = clock.now().replace(hour=8, minute=31, second=0, microsecond=0)
    with patch.object(digest, "notify", tell), patch.object(push, "notify", phone_note), \
            patch.object(digest, "DIGEST_TIMES", "08:30"):
        out = await phone_config.for_each_user("digest", lambda u: digest.digest_due(u, slot))
    sent_to = sorted(c.args[0].id for c in tell.await_args_list)
    pushed_to = sorted(c.args[0].id for c in phone_note.await_args_list)
    bob_text = next((c.args[1] for c in tell.await_args_list if c.args[0].id == BOB.id), "")
    check("digest: once per user, to that user (Telegram and their phone)",
          out == {ADMIN.id: 1, BOB.id: 1} and sent_to == pushed_to and BOB.id in sent_to, (out, sent_to))
    check("digest: a user's digest counts only their notifications", "Admin" not in bob_text and "notifications" in bob_text,
          bob_text)
    with get_db_connection() as conn:
        state = sorted(tuple(r) for r in conn.execute("SELECT user_id, slot FROM notification_digest_state"))
    check("digest: state per user", (BOB.id, "0830") in state and (ADMIN.id, "0830") in state, state)
    with patch.object(digest, "notify", tell), patch.object(push, "notify", phone_note), \
            patch.object(digest, "DIGEST_TIMES", "08:30"):
        again = await phone_config.for_each_user("digest", lambda u: digest.digest_due(u, slot))
    check("digest: a slot fires once per user per day", again == {ADMIN.id: 0, BOB.id: 0}, again)

    nudged: List[str] = []

    async def spy_renudge(user, now=None):
        nudged.append(users.current_id())
        if user.id == ADMIN.id:
            raise RuntimeError("broken")
        return 0
    with patch.object(escalation, "renudge_due", spy_renudge):
        await phone_config.for_each_user("re-nudge", escalation.renudge_due)
    check("re-nudge: per user, as that user, a failing one not stopping the next",
          sorted(nudged) == sorted([ADMIN.id, BOB.id]), nudged)

    # Summaries: a failing user doesn't stop the next one's.
    SEEN["summary_fail_for"] = ADMIN.id
    try:
        results_by = await phone_config.for_each_user("summaries", lambda u: summary.refresh_now(u, force=True))
    finally:
        SEEN["summary_fail_for"] = ""
    check("summaries loop: Bob's rewritten while the first admin's model call fails",
          results_by.get(BOB.id, {}).get("updated", 0) >= 1 and results_by.get(ADMIN.id, {}).get("errors"), results_by)
    fails = [r for r in await usage_rows("notification_summary") if not r["ok"]]
    check("summaries loop: the failed calls are the first admin's usage rows", fails
          and {r["user_id"] for r in fails} == {ADMIN.id}, fails)

    # Triage resume at startup: per user.
    for user, nid in ((ADMIN, "pend-admin"), (BOB, "pend-bob")):
        with get_db_connection() as conn:
            conn.execute("INSERT INTO notifications (id, user_id, package, app_name, title, text, posted_at, content_hash) "
                         "VALUES (?, ?, 'com.x', 'X', 'T', 'hello', ?, ?)", (nid, user.id,
                                                                            clock.now().isoformat(timespec="seconds"), nid))
            conn.commit()
    n = processing.resume_unfinished(BOB)
    await settle()
    check("triage resume: only that user's unfinished ones", n == 1 and nrow("pend-bob")["triage_status"] == "done"
          and nrow("pend-admin")["triage_status"] == "pending", (n, nrow("pend-admin")["triage_status"]))
    processing.resume_unfinished(ADMIN)
    await settle()
    check("triage resume: the first admin's on their own run", nrow("pend-admin")["triage_status"] == "done")


# --- 5. Pushes from other parts reach only their user's phone ---------------------------------------

async def test_callers() -> None:
    n = len(SEEN["pings"])
    with patch.object(core_notify, "notify", AsyncMock()):
        await task_triggers.trigger_reminder({"id": "t-r1", "title": "Water the plants"}, "car", BOB)
    got = pings_since(n)
    check("task reminder: pushed only to its user's phone", [p["token"] for p in got] == ["tok-b"]
          and got[0]["type"] == "reminder", got)
    n = len(SEEN["pings"])
    with patch.object(core_notify, "notify", AsyncMock()):
        await task_triggers.trigger_reminder({"id": "t-r2", "title": "Carl's thing"}, "car", CARL)
    check("task reminder: nothing for a user without the addon", pings_since(n) == [] and events_of(CARL.id) == [])
    n = len(SEEN["pings"])
    ok = await wa_events._notify_phone(Connection(user=BOB, addon="whatsapp"), "Gal", "hello there", "m1")
    got = pings_since(n)
    check("WhatsApp transcript: pushed only to the connection's user's phone",
          ok and [p["token"] for p in got] == ["tok-b"] and got[0]["event_user"] == BOB.id, got)
    ok = await wa_events._notify_phone(Connection(user=CARL, addon="whatsapp"), "Gal", "hello", "m2")
    check("WhatsApp transcript: nothing without the phone addon", not ok and events_of(CARL.id) == [])
    n = len(SEEN["pings"])
    b = {"date": clock.now().date().isoformat(), "headline": "Morning", "points": ["one"], "data": {}}
    with get_db_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO briefings (user_id, date, headline, points, data, trigger, created_at) "
                     "VALUES (?, ?, 'Morning', '[]', '{}', 'test', 'x')", (BOB.id, b["date"]))
        conn.commit()
    await briefing.deliver(b, BOB)
    got = pings_since(n)
    check("briefing: pushed only to its user's phone", [p["token"] for p in got] == ["tok-b"]
          and got[0]["type"] == "briefing", got)


# --- 6. The user's profile in prompts --------------------------------------------------------------

def test_user_profile() -> None:
    who = user_profile.for_user(users.get(ADMIN.id))
    bob = user_profile.for_user(users.get(BOB.id))
    check("profile: the first admin keeps today's name / context (from their row), Bob his own name and no context",
          who.name == testenv.OWNER_NAME and who.context == testenv.OWNER_CONTEXT
          and who.called == "Robin (רובין)" and who.pronoun == "he"
          and bob.name == "Bob Stone" and bob.first_name == "Bob" and bob.context == "" and bob.called == "Bob"
          and bob.pronoun == "they" and bob.about() == "")
    items = [{"id": "1", "package": "p", "app_name": "Chat", "sender": "Gal", "title": "Gal", "text": "Friday?",
              "bucket": "needs_you", "posted_at": "2026-11-14T10:00:00"}]
    admin_prompt = summary.app_prompt(ADMIN, items)
    bob_prompt = summary.app_prompt(BOB, items)
    check("profile: the first admin's summary prompt as before (About block, Robin, he)",
          f"About Robin:\n{testenv.OWNER_CONTEXT}\n\nNow:" in admin_prompt
          and f"waiting on {testenv.OWNER_NAME}'s phone, so he can" in admin_prompt
          and "Waiting for Robin:\n" in admin_prompt)
    check("profile: Bob's prompt names Bob, no About block, nothing about Robin",
          "Waiting for Bob:\n" in bob_prompt and "so they can" in bob_prompt and "About " not in bob_prompt
          and "Robin" not in bob_prompt, bob_prompt[:300])
    check("profile: Bob's triage questions are worded for Bob",
          "Robin" not in json.dumps(triage.questions("Bob")) and json.dumps(triage.questions("Robin")).count("Robin") > 3)
    check("profile: a tag echoed for Bob is cleaned from his points",
          summary.points("[needs Bob] Gal asks\nWaiting for Bob:", "Bob") == ["Gal asks"])


async def main() -> None:
    app = build_app()
    async with TestClient(TestServer(app)) as client:
        await test_push(client)
        await test_notifications(client)
    await test_loops()
    await test_callers()


run(main())
test_user_profile()

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
