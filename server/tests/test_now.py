"""Now screen, Notifications overview, self-check and search.

Covers: GET /api/now composition (approval, needs-you notification with its
chat and reply flag, call suggestions with transcript lines + trigger choices,
an event suggestion, due reminders with history, failing checks with the week
view, the weekly review with its proposals, quiet suggestions, notification
counts, day slots, lede); every POST /api/now/act action and its undo (accept
with a chosen trigger / home / a slot, dismiss, quiet dismiss-all, notification
snooze / dismiss / schedule, reminder done / snooze / schedule / dismiss, card
snoozes for checks and the review, review skip / apply, errors); no suggestion
pop-up for notification suggestions but still for fresh calls; the
Notifications overview (rule mapping, noise, sorting, summaries) and rule
suggestions accept / decline; clearing by app / the noise with undo; checks
(ok, quiet, ignored, the Now card); search across kinds (Hebrew + English).
Calendar, Home Assistant zones, the model and FCM are mocked; no network.

Run: bin/test test_now
"""
import asyncio
import datetime
import json
import os
import sys
import uuid

from cryptography.fernet import Fernet

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("now_test")
os.environ["DANA_INSECURE_DEV"] = "1"  # no API_TOKEN: local development mode, every request is the first admin
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # throwaway: encrypts the fake credentials
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ["TYPESAFE_API_KEY"] = "test-typesafe"
os.environ["TIMEZONE"] = "Asia/Jerusalem"
os.environ.pop("HOMEASSISTANT_TOKEN", None)
os.environ["FCM_SERVICE_ACCOUNT_JSON"] = '{"project_id": "dana-test"}'

import httpx
import litellm
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users
from addons import bootstrap as addons_bootstrap
from addons.tasks.groups import ensure_default_groups_for_everyone

db.migrate()
users.bootstrap()
addons_bootstrap.bootstrap()  # the first admin's default addons
testenv.connect_admin("calendar", credentials={"refresh_token": "test-refresh-token"})  # Google is mocked
OWNER = users.first_admin()
ensure_default_groups_for_everyone()
from core.addons import store  # noqa: E402
from addons.shopping import lists as shopping_lists  # noqa: E402
from addons.homeassistant import status as ha_status  # noqa: E402
# The first admin has Home Assistant (location triggers need it); its client is mocked.
store.connect(OWNER, "homeassistant", credentials={"host": "http://ha.invalid", "token": "test-ha"})
store.connect(OWNER, "money")  # every check applies to them (no bank added: the money check is ok)
from addons.phone import api as phone_api
from addons.phone.notifications import api as notifications_api
from core.auth import setup_auth
from core import actions_api, checks, clock, now as now_mod, now_api, undo
from addons.phone import popups, push as phone_push, suggestions as phone_suggestions
from addons.phone.notifications import rules as notification_rules, shade as notification_shade
from addons.phone.notifications import summary as notification_summary
from addons.people import store as people_store
from core.db import get_db_connection

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


# --- Fakes -----------------------------------------------------------------------

NOW = clock.now()
UTC = datetime.timezone.utc
seen = {"push": [], "events_added": [], "events_deleted": [], "event_undos": 0, "calendar_fail": False}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)
        self.headers = {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


async def fake_post(self, url, *args, **kwargs):
    body = kwargs.get("json") or {}
    if "fcm.googleapis.com" in url:
        msg = body["message"]
        ev = phone_push.events_after(OWNER, int(msg["data"]["seq"]) - 1)["events"][0]
        seen["push"].append(ev)
        return FakeResponse({"name": "projects/dana-test/messages/1"})
    if "typesafe" in url:  # re-triage after a rule is removed: still noise
        def noul(v):
            return {"type": "noul", "noul": v}
        return FakeResponse({"model": "jev", "usage": {}, "answers": {
            "category": {"type": "choice", "choice": "promotion", "confidence": 0.9, "probabilities": {}},
            "priority": {"type": "score", "score": 0.3, "probabilities": {}},
            "needs_action": noul(0.1), "from_person": noul(0.02), "time_sensitive": noul(0.1), "promotional": noul(0.95)}})
    raise AssertionError(f"unexpected POST {url}")


httpx.AsyncClient.post = fake_post


async def fake_token():
    return "fcm-test-token"

phone_push._access_token = fake_token


def reply(obj):
    return litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": json.dumps(obj)}}])


async def fake_acompletion(**kwargs):
    props = set(kwargs["response_format"]["json_schema"]["schema"]["properties"])
    field = next(iter(props))
    return reply({field: ["written by the model"]})

litellm.acompletion = fake_acompletion

EVENT_AT = (NOW + datetime.timedelta(hours=1)).replace(minute=30, second=0, microsecond=0)


def fake_list_events(conn, time_min, time_max, limit=50):
    if seen["calendar_fail"]:
        raise RuntimeError("invalid_grant: token expired")
    return [{"id": "ev-noa", "title": "Meeting with Noa", "start": EVENT_AT.isoformat(), "end": EVENT_AT.isoformat(),
             "all_day": False, "location": "", "attendees": [], "calendar_id": "primary"},
            {"id": "ev-all", "title": "Holiday", "start": NOW.date().isoformat(), "end": NOW.date().isoformat(),
             "all_day": True, "location": "", "attendees": [], "calendar_id": "primary"}]


def fake_add_event(conn, summary, start, end, description=None, location=None):
    seen["events_added"].append(summary)

    def _undo():
        seen["event_undos"] += 1
    undo.record(f"Added {summary}", _undo, source="fake add_event")
    return f"Successfully added event '{summary}' to your personal calendar with ID: ev123."


def fake_delete_event(conn, event_id):
    seen["events_deleted"].append(event_id)
    return "Successfully deleted"


import addons.calendar.events as calendar_events  # noqa: E402
import addons.calendar.today as calendar_today  # noqa: E402
import addons.calendar.tools as calendar  # noqa: E402
calendar_events.list_events = fake_list_events
calendar.add_event = fake_add_event
calendar.delete_event = fake_delete_event


async def fake_zones(conn):
    return [{"name": "Home", "entity_id": "zone.home"}, {"name": "Work", "entity_id": "zone.work"}]

import addons.homeassistant.client as ha  # noqa: E402
ha.get_ha_zones_list = fake_zones

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


# --- Seed --------------------------------------------------------------------------

def utc_sql(dt):
    """SQLite CURRENT_TIMESTAMP format (naive UTC)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def local(dt):
    return dt.isoformat(timespec="seconds")


TRANSCRIPT = ("המזגן בסלון שוב מטפטף על הרצפה. אני אזמין טכנאי. רק שיהיה לפני שישי, כולם באים לארוחה. "
              "סגור. באיזו שעה? בשבע.")
IDS = {}


def seed():
    with get_db_connection() as conn:
        call_id = str(uuid.uuid4())
        IDS["call"] = call_id
        conn.execute("INSERT INTO phone_calls (id, user_id, filename, caller, receiver, call_timestamp, transcript, summary, "
                     "title, status, duration_seconds) VALUES (?, ?, 'a.m4a', 'אמא', 'Me', ?, ?, "
                     "'AC leaking; book a technician', 'AC in the living room', 'ready', 360)",
                     (call_id, OWNER.id, local(NOW - datetime.timedelta(minutes=20)), TRANSCRIPT))
        tomorrow10 = (NOW + datetime.timedelta(days=1)).strftime("%Y-%m-%d") + " 10:00"

        def sugg(kind, payload, source="call", src_id=None):
            sid = str(uuid.uuid4())
            conn.execute("INSERT INTO call_suggestions (id, user_id, call_id, source_type, kind, payload, status) "
                         "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                         (sid, OWNER.id, src_id or call_id, source, kind, json.dumps(payload, ensure_ascii=False)))
            return sid
        IDS["sug_task"] = sugg("task", {"title": "להזמין טכנאי למזגן", "triggers": {"time": tomorrow10},
                                        "confidence": 0.85, "evidence": "רק שיהיה לפני שישי", "priority": "medium",
                                        "group": "Home", "due_date": (NOW + datetime.timedelta(days=2)).date().isoformat()})
        IDS["sug_task2"] = sugg("task", {"title": "לבדוק מחיר", "triggers": {}, "confidence": 0.8, "evidence": ""})
        IDS["sug_event"] = sugg("event", {"title": "ארוחת שישי", "start": (NOW + datetime.timedelta(days=3)).strftime(
            "%Y-%m-%dT19:00:00"), "end": (NOW + datetime.timedelta(days=3)).strftime("%Y-%m-%dT20:00:00"),
            "confidence": 0.9})
        IDS["sug_low"] = sugg("task", {"title": "Maybe call the plumber", "triggers": {}, "confidence": 0.62})

        # Notifications: a chat that needs Robin, noise, a muted app, a normal one.
        def notif(package, app, title, text, bucket="normal", sender="", status="active", posted=None, key=None,
                  actions=None, removed_reason=None, conversation=""):
            nid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO notifications (id, user_id, device_key, package, app_name, title, text, sender, conversation, "
                "posted_at, content_hash, status, bucket, triage_status, actions, removed_reason, priority) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done', ?, ?, 'normal')",
                (nid, OWNER.id, key or f"k-{nid[:8]}", package, app, title, text, sender, conversation,
                 local(posted or NOW), nid, status, bucket, json.dumps(actions) if actions else None, removed_reason))
            return nid
        notif("com.whatsapp", "WhatsApp", "Gal", "נפגשים בשבע?", sender="Gal", status="archived",
              posted=NOW - datetime.timedelta(minutes=30), key="k-gal", removed_reason="updated")
        IDS["n_gal"] = notif("com.whatsapp", "WhatsApp", "Gal", "מתי אתה מגיע?", bucket="needs_you", sender="Gal",
                             posted=NOW - datetime.timedelta(minutes=6), key="k-gal",
                             actions=[{"title": "Reply", "reply": True}, {"title": "Mark as read", "reply": False}])
        IDS["n_gh"] = [notif("com.github.android", "GitHub", "homelab", f"CI passed on #{i}", bucket="dismiss",
                             posted=NOW - datetime.timedelta(minutes=10 + i)) for i in range(3)]
        IDS["n_x"] = notif("com.twitter.android", "X", "Trending", "What people talk about", bucket="normal",
                           posted=NOW - datetime.timedelta(minutes=40))
        IDS["n_slack"] = notif("com.slack", "Slack", "Amit", "Can you review the auth PR?", bucket="normal",
                               posted=NOW - datetime.timedelta(minutes=5))
        IDS["n_bank"] = notif("com.bank", "Bank", "Statement", "Your statement is ready", bucket="normal",
                              posted=NOW - datetime.timedelta(hours=2))
        conn.execute("INSERT INTO notification_rules (id, user_id, package, sender, action) VALUES ('r1', ?, 'com.twitter.android', '', 'mute')", (OWNER.id,))
        conn.execute("INSERT INTO notification_rules (id, user_id, package, sender, action) VALUES ('r2', ?, 'com.bank', '', 'alert')", (OWNER.id,))
        conn.execute("INSERT INTO notification_rules (id, user_id, package, sender, action) VALUES ('r3', ?, 'com.slack', '', 'important')", (OWNER.id,))
        conn.execute("INSERT INTO notification_rules (id, user_id, package, sender, action) VALUES ('r4', ?, 'com.old', '', 'mute')", (OWNER.id,))
        # GitHub cleared 12 times this month without being opened → a rule suggestion.
        for i in range(12):
            notif("com.github.android", "GitHub", "homelab", f"old build {i}", status="dismissed",
                  posted=NOW - datetime.timedelta(days=1 + i), removed_reason="swiped")
        IDS["sug_notif"] = sugg("task", {"title": "Check the Jira ticket", "triggers": {}, "confidence": 0.8},
                                source="notification", src_id=IDS["n_slack"])

        # Reminders.
        def task(title, triggers=None, updated=None, snooze_count=0, status="needs_action", description=None):
            tid = str(uuid.uuid4())
            conn.execute("INSERT INTO tasks (id, title, description, priority, status, triggers, group_id, created_at, "
                         "updated_at, snooze_count, user_id, created_by) "
                         "VALUES (?, ?, ?, 'medium', ?, ?, 'general', ?, ?, ?, ?, ?)",
                         (tid, title, description, status, json.dumps(triggers or {}),
                          utc_sql(NOW - datetime.timedelta(days=11)), utc_sql(updated or NOW - datetime.timedelta(days=1)),
                          snooze_count, OWNER.id, OWNER.id))
            return tid

        def fired(tid, ttype, at):
            conn.execute("INSERT OR REPLACE INTO task_trigger_marks (user_id, key, at) VALUES (?, ?, ?)",
                         (OWNER.id, f"last_trigger_v2_{ttype}_{tid}", at.astimezone(UTC).isoformat()))
        fire_at = NOW - datetime.timedelta(minutes=10)
        IDS["t_car"] = task("Take the car to the garage", updated=fire_at, snooze_count=2,
                            description="They close at 18:00")
        fired(IDS["t_car"], "time", fire_at)
        conn.execute("INSERT INTO task_links (id, task_id, ref_type, ref_id, role) VALUES (?, ?, 'call', ?, 'origin')",
                     (str(uuid.uuid4()), IDS["t_car"], call_id))
        IDS["t_home_fired"] = task("Take out the trash", {"location": {"location": "home", "event": "arrive"}},
                                   updated=NOW - datetime.timedelta(days=2))
        fired(IDS["t_home_fired"], "location_arrive", NOW - datetime.timedelta(minutes=30))
        IDS["t_missed"] = task("Missed time trigger", {"time": (NOW - datetime.timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M")})
        IDS["t_handled"] = task("Already snoozed", {"time": (NOW + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")},
                                updated=NOW - datetime.timedelta(minutes=1))
        fired(IDS["t_handled"], "time", NOW - datetime.timedelta(hours=3))
        IDS["t_old"] = task("Fired long ago", updated=NOW - datetime.timedelta(days=5))
        fired(IDS["t_old"], "time", NOW - datetime.timedelta(days=5))
        IDS["t_later"] = task("להוציא את הכביסה", {"time": EVENT_AT.strftime("%Y-%m-%d %H:%M")})
        IDS["t_wake"] = task("Take vitamins", {"wake": True})
        IDS["t_done"] = task("Pay the bill", status="completed")

        # Weekly review with two proposals and one "keep".
        review_items = [
            {"ref": IDS["t_wake"], "type": "task", "title": "Take vitamins", "why": "stale", "facts": [],
             "action": "reschedule", "due_date": (NOW + datetime.timedelta(days=3)).date().isoformat(), "enabled": True},
            {"ref": IDS["t_later"], "type": "task", "title": "להוציא את הכביסה", "why": "stale", "facts": [],
             "action": "complete", "enabled": True},
            {"ref": IDS["t_done"], "type": "task", "title": "Pay the bill", "why": "stale", "facts": [],
             "action": "keep", "enabled": False},
        ]
        IDS["review"] = str(uuid.uuid4())
        conn.execute("INSERT INTO weekly_reviews (id, user_id, week, status, headline, data, created_at) "
                     "VALUES (?, ?, '2026-W46', 'pending', 'Two stale reminders', ?, ?)",
                     (IDS["review"], OWNER.id, json.dumps({"items": review_items, "stats": {"done": 9, "created": 4, "open": 12},
                                                 "summary": ["9 done"]}), local(NOW)))
        # A chat waiting for Approve.
        conn.execute("INSERT INTO sessions (user_id, session_id, history, pending_approval, status, title, updated_at) "
                     "VALUES (?, 'main', '[]', ?, 'waiting_approval', 'Dana', CURRENT_TIMESTAMP)",
                     (OWNER.id, json.dumps({"status": "pending_approval", "tool_name": "send_whatsapp_message",
                                  "tool_args": {"chat_id": "Gal", "text": "on my way"}, "user_text": "tell Gal I'm late"}),))
        # Signals for the checks: the watched person entity and car sensor changed (as the watcher marks it).
        for role, at in (("person", NOW - datetime.timedelta(hours=1)), ("car", NOW - datetime.timedelta(days=1))):
            conn.execute("INSERT OR REPLACE INTO system_marks (key, last_at, ok, detail) VALUES (?, ?, 1, '')",
                         (f"ha_event:{OWNER.id}:{role}", at.astimezone(UTC).isoformat()))
        # The last confirmed wake: 4 days ago.
        conn.execute("INSERT INTO wake_log (user_id, date, at, why) VALUES (?, ?, ?, 'unlock')",
                     (OWNER.id, (NOW.date() - datetime.timedelta(days=4)).isoformat(),
                      (NOW - datetime.timedelta(days=4)).isoformat(timespec="seconds")))
        # Search material.
        conn.execute("INSERT INTO shopping_items (id, list_id, name, name_key, checked, seq, created_at) VALUES ('s1', ?, 'Bread', 'bread', 0, 1, '2026-11-01T08:00:00+00:00')",
                     (shopping_lists.ensure_list(OWNER),))
        conn.execute("INSERT INTO projects (id, title, user_id) VALUES ('p1', 'Wedding', ?)", (OWNER.id,))
        conn.execute("INSERT INTO entries (id, project_id, kind, title, body, user_id, created_by) "
                     "VALUES ('e1', 'p1', 'note', 'DJ quote', 'Price, deposit and what is included', ?, ?)",
                     (OWNER.id, OWNER.id))
        conn.commit()
    IDS["gal"] = people_store.create_person(OWNER, "Gal", phones=["0501234567"], relation="friend")["id"]


def build_app():
    app = web.Application()
    setup_auth(app)  # no API_TOKEN: every request is the first admin
    now_api.setup(app)
    actions_api.setup(app)  # POST /api/undo/{id}
    r = app.router
    r.add_post("/api/notifications/batch", notifications_api.notifications_batch_api)
    r.add_post("/api/notifications/status", notifications_api.notifications_status_api)
    r.add_get("/api/notifications/overview", notifications_api.notifications_overview_api)
    r.add_post("/api/notifications/rule-suggestions", notifications_api.notifications_rule_suggestion_api)
    r.add_get("/api/notifications", notifications_api.notifications_list_api)
    r.add_post("/api/phone/register", phone_api.phone_register_api)
    return app


def item(payload, item_id):
    return next((i for i in payload["items"] if i["id"] == item_id), None)


def block(it, type_):
    return next((b for b in (it or {}).get("blocks", []) if b["type"] == type_), None)


def task_row(tid):
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    return dict(r) if r else None


def notif_row(nid):
    with get_db_connection() as conn:
        return dict(conn.execute("SELECT * FROM notifications WHERE id = ?", (nid,)).fetchone())


def sugg_status(sid):
    return phone_suggestions.get_suggestion(OWNER, sid)["status"]


async def main():
    seed()
    async with TestClient(TestServer(build_app())) as client:
        async def get_now():
            r = await client.get("/api/now")
            assert r.status == 200, await r.text()
            return await r.json()

        async def act(item_id, action, args=None, status=200):
            r = await client.post("/api/now/act", json={"id": item_id, "action": action, "args": args or {}})
            body = await r.json()
            if r.status != status:
                print("   unexpected", r.status, body)
            return r.status, body

        async def do_undo(uid):
            r = await client.post(f"/api/undo/{uid}")
            return r.status, await r.json()

        await client.post("/api/phone/register", json={"device_id": "dev-1", "token": "tok", "name": "Phone"})
        await client.post("/api/notifications/batch", json={"posted": [], "removed": [], "active_keys": []})

        # --- GET /api/now -------------------------------------------------------------
        p = await get_now()
        kinds = [i["kind"] for i in p["items"]]
        check("now: top-level shape", {"now", "lede", "items", "quiet", "notifications", "day"} <= set(p), list(p))
        check("now: kinds in order of urgency",
              kinds == sorted(kinds, key=["approval", "notification", "suggestion", "reminder", "check", "review"].index),
              kinds)
        appr = item(p, "approval:main")
        check("approval: first, opens the thread, no server action",
              p["items"][0]["id"] == "approval:main" and appr["ref"] == {"type": "thread", "id": "main"}
              and appr["primary"].get("client") and appr["secondary"] == [], appr)
        check("approval: the title says the action, the message is in a text block (no JSON)",
              appr["title"] == "Send a WhatsApp message to Gal"
              and appr["blocks"] == [{"type": "text", "label": "Message", "text": "on my way"}]
              and "{" not in json.dumps(appr["blocks"], ensure_ascii=False).replace('{"type"', ""), appr)
        check("approval: why quotes what Robin asked", appr["why"] == "You asked: “tell Gal I'm late”", appr.get("why"))
        gal = item(p, f"notif:{IDS['n_gal']}")
        check("notification: no triage answers → no why line (no filler)", gal and "why" not in gal, gal.get("why"))
        check("notification: chat title is the newest message", gal and gal["title"] == "מתי אתה מגיע?", gal["title"])
        check("notification: needs-you chat in the queue with reply + notification object",
              gal and gal["primary"]["action"] == "reply" and gal["notification"]["can_reply"]
              and gal["notification"]["key"] == "k-gal" and gal["notification"]["package"] == "com.whatsapp", gal)
        check("notification: source names app, sender, age", gal and gal["source"].startswith("WhatsApp · Gal · "), gal)
        chat = block(gal, "chat")
        check("notification: chat block carries the earlier message too",
              chat and [m["text"] for m in chat["messages"]] == ["נפגשים בשבע?", "מתי אתה מגיע?"], chat)
        check("notification: actions snooze + dismiss",
              gal and [a["action"] for a in gal["secondary"]] == ["snooze", "dismiss"]
              and gal["secondary"][0]["args"] == {"minutes": 60}, gal and gal["secondary"])
        check("notification: noise and normal ones stay out of the queue",
              not any(i["id"] in (f"notif:{IDS['n_slack']}", f"notif:{IDS['n_x']}") for i in p["items"]))

        st = item(p, f"sug:{IDS['sug_task']}")
        check("suggestion: call suggestion in the queue", st and st["source"].startswith("Call · אמא · "), st)
        lines = block(st, "lines")
        hits = [l for l in (lines or {}).get("lines", []) if l.get("hit")]
        check("suggestion: transcript lines around the quoted one",
              lines and len(hits) == 1 and "לפני שישי" in hits[0]["text"] and len(lines["lines"]) >= 3, lines)
        ch = block(st, "choices")
        opts = (ch or {}).get("options", [])
        check("suggestion: choices = proposed (selected), tomorrow 09:00, home, wake",
              ch and ch["key"] == "triggers" and len(opts) == 4 and opts[0]["selected"]
              and opts[0]["value"] == {"time": (NOW + datetime.timedelta(days=1)).strftime("%Y-%m-%d") + " 10:00"}
              and {"wake": True} in [o["value"] for o in opts]
              and any((o["value"].get("location") or {}).get("location") == "home" for o in opts), opts)
        check("suggestion: primary accepts the proposed trigger",
              st["primary"]["action"] == "accept" and st["primary"]["label"] == "Remind me tomorrow 10:00"
              and st["primary"]["args"]["triggers"] == opts[0]["value"] and st["schedulable"], st["primary"])
        check("suggestion: options carry the label the primary takes",
              all(o.get("action_label") and o.get("message") for o in opts), opts)
        st2 = item(p, f"sug:{IDS['sug_task2']}")
        check("suggestion: no trigger → 'No reminder' first + summary text block (no quote to locate)",
              st2 and block(st2, "choices")["options"][0]["label"] == "No reminder" and block(st2, "text")
              and st2["primary"]["label"] == "Add to reminders", st2)
        ev = item(p, f"sug:{IDS['sug_event']}")
        check("suggestion: event → Add to calendar, not schedulable",
              ev and ev["primary"]["label"].startswith("Add to calendar") and not ev["schedulable"], ev)

        quiet_ids = [q["id"] for q in p["quiet"]]
        check("quiet: notification suggestion and low-confidence one, never in the queue",
              f"sug:{IDS['sug_notif']}" in quiet_ids and f"sug:{IDS['sug_low']}" in quiet_ids
              and not item(p, f"sug:{IDS['sug_notif']}") and not item(p, f"sug:{IDS['sug_low']}"), quiet_ids)
        q_notif = next(q for q in p["quiet"] if q["id"] == f"sug:{IDS['sug_notif']}")
        check("quiet: entry has source + add / dismiss", q_notif["source"].startswith("Slack")
              and q_notif["primary"]["action"] == "accept", q_notif)

        car = item(p, f"task:{IDS['t_car']}")
        check("reminder: fired and unhandled → in the queue with Done", car and car["primary"]["action"] == "done", car)
        facts = (block(car, "facts") or {}).get("items", [])
        check("reminder: history facts (created from the call, moved twice, reminded)",
              any("from a call with אמא" in f for f in facts) and "Moved 2 times" in facts
              and any(f.startswith("Reminded") for f in facts), facts)
        check("reminder: why mentions the moves", car["why"] == "You moved this 2 times.", car["why"])
        check("reminder: snooze options + dismiss",
              [a["action"] for a in car["secondary"]][-1] == "dismiss"
              and {"minutes": 60} in [a.get("args") for a in car["secondary"]]
              and {"slot": "home"} in [a.get("args") for a in car["secondary"]], car["secondary"])
        check("reminder: location reminder that fired shows", item(p, f"task:{IDS['t_home_fired']}") is not None)
        check("reminder: passed time trigger not fired yet shows", item(p, f"task:{IDS['t_missed']}") is not None)
        check("reminder: handled after firing / old / completed ones don't",
              not any(item(p, f"task:{IDS[k]}") for k in ("t_handled", "t_old", "t_done", "t_later")))

        wake = item(p, "check:wake")
        week = block(wake, "week")
        check("check: wake quiet 4 days → card with a 7-cell week view",
              wake and wake["title"] == "No wake signal for 4 days" and week and len(week["cells"]) == 7
              and week["cells"][2]["ok"] is True and week["cells"][-2]["ok"] is False, wake)
        check("check: card actions open (client) + ignore a week",
              wake["primary"] == {"action": "open", "label": "Details", "client": True}
              and wake["secondary"][0] == {"action": "ignore", "label": "Ignore for a week", "args": {"days": 7}})
        check("check: healthy signals make no card",
              not any(item(p, f"check:{k}") for k in ("location", "car", "push", "notification_sync")),
              [i["id"] for i in p["items"] if i["kind"] == "check"])

        rev = item(p, f"review:{IDS['review']}")
        cb = block(rev, "checks")
        check("review: card with the proposals as checks (keep left out)",
              rev and rev["title"] == "3 items, 2 worth changing" and cb and cb["key"] == "items"
              and [o["value"] for o in cb["options"]] == [IDS["t_wake"], IDS["t_later"]]
              and all(o["checked"] for o in cb["options"]) and "move to" in cb["options"][0]["label"], rev)
        check("review: apply + skip", rev["primary"]["action"] == "apply" and rev["secondary"][0]["action"] == "skip")

        check("counts: total / noise (dismiss bucket + muted app) / needs you",
              p["notifications"] == {"total": 7, "noise": 4, "needs_you": 1}, p["notifications"])

        slots = p["day"]["slots"]
        check("day: ends with home + wake", [s["key"] for s in slots][-2:] == ["home", "wake"], [s["key"] for s in slots])
        home = slots[-2]["items"]
        check("day: home slot has the arrive-home reminder",
              [x["task_id"] for x in home] == [IDS["t_home_fired"]] and home[0]["kind"] == "reminder", home)
        check("day: wake slot has the wake reminder", [x["task_id"] for x in slots[-1]["items"]] == [IDS["t_wake"]])
        hour_slots = slots[:-2]
        expect_hours = list(range(max(NOW.hour + 1, now_mod.DAY_FIRST_HOUR), 24))
        check("day: every remaining hour of today through 23:00",
              [int(s["key"]) for s in hour_slots] == expect_hours
              and all(s["label"] == f"{int(s['key']):02d}:00" for s in hour_slots), [s["key"] for s in hour_slots])
        if NOW.hour <= 22:
            # Between midnight and DAY_FIRST_HOUR the event (an hour from now) comes
            # before the first hourly slot, and is listed in that first slot (docs/now.md).
            slot_hour = max(EVENT_AT.hour, now_mod.DAY_FIRST_HOUR)
            s = next((x for x in hour_slots if x["key"] == str(slot_hour)), None)
            kinds_in = sorted(x["kind"] for x in (s or {}).get("items", []))
            check("day: event + timed reminder in their hour", s and kinds_in == ["event", "reminder"]
                  and all(x["time"] == EVENT_AT.strftime("%H:%M") for x in s["items"]), s)
            check("lede: next event when there is no briefing",
                  p["lede"] == f"Meeting with Noa at {EVENT_AT.strftime('%H:%M')}.", p["lede"])
        else:
            check("day: no hourly slots after 23:00", hour_slots == [])

        with get_db_connection() as conn:
            conn.execute("INSERT INTO briefings (user_id, date, headline, points, data, trigger, created_at) "
                         "VALUES (?, ?, ?, '[]', '{}', 'wake', ?)",
                         (OWNER.id, NOW.date().isoformat(), "Noa at 19:30, rain after nine", local(NOW)))
            conn.commit()
        p = await get_now()
        check("lede: today's fresh briefing headline wins", p["lede"] == "Noa at 19:30, rain after nine.", p["lede"])

        # --- lede: fresh vs stale briefing, never contradicting the queue ----------------
        def L(*args):
            return now_mod._lede(OWNER, *args)
        t20 = NOW.replace(hour=20, minute=0, second=0, microsecond=0)
        three = [{"id": f"x{i}"} for i in range(3)]
        dinner = [{"id": "d", "title": "Dinner with Noa", "start": t20.replace(hour=21, minute=15).isoformat(),
                   "all_day": False},
                  {"id": "past", "title": "Earlier thing", "start": t20.replace(hour=9).isoformat(), "all_day": False},
                  {"id": "tmr", "title": "Tomorrow thing", "start": (t20 + datetime.timedelta(days=1)).isoformat(),
                   "all_day": False}]

        def made_at(dt):
            with get_db_connection() as conn:
                conn.execute("UPDATE briefings SET created_at = ? WHERE date = ?", (local(dt), NOW.date().isoformat()))
                conn.commit()
        made_at(t20 - datetime.timedelta(hours=1))
        check("lede: fresh briefing + items → the headline", L(t20, None, None, three) == "Noa at 19:30, rain after nine.")
        check("lede: fresh briefing but an empty queue → not the headline (it would contradict)",
              L(t20, None, None, []) is None and L(t20, dinner, None, []) == "Dinner with Noa at 21:15.",
              (L(t20, None, None, []), L(t20, dinner, None, [])))
        made_at(t20 - datetime.timedelta(hours=now_mod.LEDE_BRIEFING_HOURS + 2))
        check("lede: stale briefing → next event today (past and tomorrow's ignored)",
              L(t20, dinner, None, three) == "Dinner with Noa at 21:15.", L(t20, dinner, None, three))
        check("lede: stale briefing, no event → null (the queue header already counts)",
              L(t20, None, None, three) is None and L(t20, [], None, three[:1]) is None,
              (L(t20, None, None, three), L(t20, [], None, three[:1])))
        check("lede: nothing at all → null (stale headline never comes back)", L(t20, dinner[1:], None, []) is None)
        check("lede: rain likely is added from the cached weather",
              L(t20, None, {"rain_chance": 70, "condition": "rainy", "temperature": 14}, three)
              == "Rain likely (70%)."
              and L(t20, dinner, {"rain_chance": 70}, three) == "Dinner with Noa at 21:15, rain likely (70%).")
        made_at(NOW)

        # --- why lines on notification cards ----------------------------------------------
        def tri(**kw):
            base = {"needs_action": 0.1, "from_person": 0.1, "time_sensitive": 0.1, "urgent_request": 0.1,
                    "promotional": 0.05, "owner_wrote_it": 0.05, "addressed_to_owner": 0.9, "record_only": 0.1}
            base.update(kw)
            return json.dumps(base)
        W = now_mod.notification_why
        wa = {"package": "com.whatsapp", "app_name": "WhatsApp", "sender": "Gal", "title": "Gal", "text": "מתי?",
              "category": "personal_message"}
        check("why: a person asking → '<name> is waiting for an answer'",
              W({**wa, "triage": tri(from_person=0.97, needs_action=0.9)}, None) == "Gal is waiting for an answer")
        check("why: … plus time-sensitive",
              W({**wa, "triage": tri(from_person=0.97, needs_action=0.9, time_sensitive=0.9)}, None)
              == "Gal is waiting for an answer · time-sensitive")
        check("why: a person saying it's urgent",
              W({**wa, "triage": tri(from_person=0.97, urgent_request=0.95)}, None) == "Gal says it's urgent")
        check("why: Robin's own message / a group message to someone else isn't 'waiting'",
              W({**wa, "triage": tri(from_person=0.97, needs_action=0.9, owner_wrote_it=0.95)}, None) is None
              and W({**wa, "triage": tri(from_person=0.97, needs_action=0.9, addressed_to_owner=0.1)}, None) is None)
        mail = {"package": "com.google.android.gm", "app_name": "Gmail", "title": "Noa Levi",
                "text": "Lunch on Thursday?\nAre you free around one?", "category": "personal_message"}
        check("why: a person in a mail (no sender field) is named from the title",
              W({**mail, "triage": tri(from_person=0.95, needs_action=0.9)}, None) == "Noa Levi is waiting for an answer")
        wolt = {"package": "com.wolt", "app_name": "Wolt", "title": "Wolt", "text": "Courier is at the door"}
        check("why: time-sensitive alone", W({**wolt, "category": "other", "triage": tri(time_sensitive=0.95)}, None)
              == "Time-sensitive")
        check("why: a delivery that needs Robin",
              W({**wolt, "category": "delivery", "triage": tri(needs_action=0.9, time_sensitive=0.95)}, None)
              == "A delivery needs you · time-sensitive")
        slack = {"package": "com.slack", "app_name": "Slack", "title": "Amit", "text": "Can you review?",
                 "triage": tri()}
        check("why: app rule important / alert / a sender rule",
              W(slack, "important", "Slack") == "You marked Slack as important"
              and W(slack, "alert", "Slack") == "You asked to be interrupted for Slack"
              and W({**wa, "triage": tri()}, "important", "Gal") == "You marked Gal as important")
        check("why: what a person asks beats the rule that let it through",
              W({**wa, "triage": tri(from_person=0.97, needs_action=0.9)}, "important", "WhatsApp")
              == "Gal is waiting for an answer")
        check("why: security / money matters",
              W({**wolt, "category": "security", "triage": tri()}, None) == "Security alert"
              and W({**wolt, "category": "money", "triage": tri(needs_action=0.9)}, None) == "A payment to handle"
              and W({**wolt, "category": "money", "triage": tri(needs_action=0.9, record_only=0.9)}, None)
              == "About your money")
        check("why: nothing specific → None (no filler)",
              W({**wa, "triage": tri()}, None) is None and W(wa, None) is None
              and W({**wa, "triage": '{"error": "boom"}'}, None) is None)

        # --- card titles per app shape ----------------------------------------------------
        P = now_mod.notification_parts
        shapes = {
            "gmail": ({"app_name": "Gmail", "title": "Bright Data", "sub_text": "robin@example.com",
                       "text": "Your invoice for October is ready\nHi Robin, your monthly invoice is attached."},
                      ("Your invoice for October is ready", "Bright Data", "Hi Robin, your monthly invoice is attached.")),
            "gmail inbox": ({"app_name": "Gmail", "title": "3 new messages",
                             "text": "Bright Data  Your invoice is ready\nNoa Levi  Lunch on Thursday?"},
                            ("Bright Data  Your invoice is ready", "", "Noa Levi  Lunch on Thursday?")),
            "github": ({"app_name": "GitHub", "title": "acme/website", "text": "CI failed on master"},
                       ("CI failed on master", "acme/website", "")),
            "whatsapp": ({"app_name": "WhatsApp", "title": "Gal (2 messages)", "sender": "Gal",
                          "text": "נפגשים?\nמתי אתה מגיע?"}, ("מתי אתה מגיע?", "Gal", "")),
            "whatsapp group": ({"app_name": "WhatsApp", "title": "Family: Noa", "sender": "Noa", "conversation": "Family",
                                "is_group": 1, "text": "Gal: מי מביא?\nNoa: אני מביאה"}, ("אני מביאה", "Noa in Family", "")),
            "slack": ({"app_name": "Slack", "title": "#deploys", "sender": "Dana", "conversation": "#deploys", "is_group": 1,
                       "text": "Dana: Deploy is blocked, can you approve?"},
                      ("Deploy is blocked, can you approve?", "Dana in #deploys", "")),
            "sms": ({"app_name": "Messages", "title": "+972501234567", "sender": "+972501234567",
                     "text": "Leaving now, see you at 8"}, ("Leaving now, see you at 8", "+972501234567", "")),
            "bank (title inside the text)": ({"app_name": "Bank", "title": "Statement", "text": "Your statement is ready"},
                                             ("Your statement is ready", "", "")),
            "title is the message": ({"app_name": "Shop", "title": "Your package has arrived", "text": "Tap to track"},
                                     ("Your package has arrived", "", "Tap to track")),
            "app-named title": ({"app_name": "Wolt", "title": "Wolt", "text": "Courier is at the door"},
                                ("Courier is at the door", "", "")),
            "title only": ({"app_name": "Phone", "title": "Battery low", "text": ""}, ("Battery low", "", "")),
        }
        for name, (n, want) in shapes.items():
            got = P(n)
            check(f"title: {name}", (got["headline"], got["who"], got["rest"]) == want, got)

        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO notifications (id, user_id, device_key, package, app_name, title, text, sub_text, posted_at, "
                "content_hash, status, bucket, triage_status, priority, category, triage) VALUES ('n-mail', ?, 'k-mail', "
                "'com.google.android.gm', 'Gmail', 'Bright Data', ?, 'robin@example.com', ?, 'h-mail', 'active', "
                "'needs_you', 'done', 'important', 'money', ?)",
                (OWNER.id, "Your invoice is overdue\nPlease pay by Monday to keep your account.",
                 local(NOW - datetime.timedelta(minutes=3)), tri(needs_action=0.9)))
            conn.commit()
        mcard = item(await get_now(), "notif:n-mail")
        check("mail card: title = subject, source = app · sender · age, body in the text block, specific why",
              mcard and mcard["title"] == "Your invoice is overdue" and mcard["source"].startswith("Gmail · Bright Data · ")
              and block(mcard, "text") == {"type": "text", "label": "Bright Data",
                                           "text": "Please pay by Monday to keep your account."}
              and mcard["why"] == "A payment to handle", mcard)
        with get_db_connection() as conn:
            conn.execute("DELETE FROM notifications WHERE id = 'n-mail'")
            conn.commit()

        # --- approval wording (shared with Telegram) ------------------------------------
        from core import approval_text
        D = approval_text.describe
        d = D("send_whatsapp_message", {"chat_id": "972501234567@c.us", "text": "בדרך, עשר דקות"})
        check("approval text: a number known in People → the person's name; message as the body",
              d["title"] == "Send a WhatsApp message to Gal" and d["body_label"] == "Message"
              and d["body"] == "בדרך, עשר דקות", d)
        check("approval text: unknown number → +number",
              D("send_whatsapp_message", {"chat_id": "972509999999@c.us", "text": "hi"})["title"]
              == "Send a WhatsApp message to +972509999999")
        check("approval text: delete_task names the reminder, not its id",
              D("delete_task", {"item": IDS["t_wake"]})["title"] == "Delete the reminder “Take vitamins”")
        tmr = (NOW + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        d = D("add_event", {"summary": "Dinner", "start_time": f"{tmr}T19:00:00", "end_time": f"{tmr}T20:00:00",
                            "attendees": "noa@example.com"})
        check("approval text: an event with invites, time in words",
              d["title"] == "Invite noa@example.com to “Dinner”" and ("When", "tomorrow 19:00–20:00") in d["facts"], d)
        d = D("some_new_tool", {"chat_id": "x", "dry_run": True, "empty": ""})
        check("approval text: unknown tool → its name + 'label: value' facts",
              d["title"] == "Some new tool" and d["facts"] == [("Chat", "x"), ("Dry run", "True")], d)
        with get_db_connection() as conn:
            conn.execute("UPDATE sessions SET pending_approval = ? WHERE session_id = 'main'",
                         (json.dumps({"status": "pending_approval", "tool_name": "delete_event",
                                      "tool_args": {"event_id": "ev-noa"}}),))
            conn.commit()
        appr2 = item(await get_now(), "approval:main")
        check("approval card: delete_event names the event (calendar cache); no why without a request",
              appr2 and appr2["title"] == "Delete “Meeting with Noa” from the calendar" and appr2["blocks"] == []
              and "why" not in appr2, appr2)
        with get_db_connection() as conn:
            conn.execute("UPDATE sessions SET pending_approval = ? WHERE session_id = 'main'",
                         (json.dumps({"status": "pending_approval", "tool_name": "send_whatsapp_message",
                                      "tool_args": {"chat_id": "Gal", "text": "on my way"},
                                      "user_text": "tell Gal I'm late"}),))
            conn.commit()
        from core.telegram_bot import TelegramBot
        tg = TelegramBot._format_confirmation_msg(None, "send_whatsapp_message", {"chat_id": "Gal", "text": "a_b *c*"})
        check("telegram: Approve message uses the same wording, Markdown-escaped",
              "Send a WhatsApp message to Gal" in tg and "a\\_b \\*c\\*" in tg and tg.endswith("Send this WhatsApp message?"),
              tg)

        # --- day strip: the rest of today, night behaviour ---------------------------------
        def at(h, m=0):
            return NOW.replace(hour=h, minute=m, second=0, microsecond=0)
        H = now_mod.day_hours
        check("slots: afternoon → every hour through 23:00", H(at(13, 20)) == list(range(14, 24)), H(at(13, 20)))
        check("slots: 22:10 → only 23:00; from 23:00 → none", H(at(22, 10)) == [23] and H(at(23, 30)) == [])
        check("slots: after midnight → from 06:00 (no sleeping hours)", H(at(1, 15)) == list(range(6, 24))
              and H(at(6, 0)) == list(range(7, 24)), H(at(1, 15)))
        check("slots: late at night only home + wake",
              [s["key"] for s in now_mod.day_slots(OWNER, at(23, 30), [])] == ["home", "wake"])
        night = now_mod.day_slots(OWNER, at(1, 0), [{"id": "f", "title": "Early flight", "start": at(3, 30).isoformat(),
                                               "all_day": False}])
        check("slots: at night, something before 06:00 is listed in the first slot with its own time",
              night[0]["key"] == "6" and {"title": "Early flight", "kind": "event", "time": "03:30", "event_id": "f"}
              in night[0]["items"], night[0])

        # --- actions + undo: suggestions ------------------------------------------------
        sid = IDS["sug_task"]
        code, res = await act(f"sug:{sid}", "accept", {"triggers": {"wake": True}})
        acc = phone_suggestions.get_suggestion(OWNER, sid)
        tid = (acc["result_ref"] or "")[5:]
        check("accept: chosen trigger → task with it + message",
              code == 200 and res["message"] == "Reminder set · when you wake" and res.get("undo_id")
              and json.loads(task_row(tid)["triggers"]) == {"wake": True}, res)
        with get_db_connection() as conn:
            link = conn.execute("SELECT ref_type, ref_id FROM task_links WHERE task_id = ?", (tid,)).fetchone()
        check("accept: task linked to the call (existing accept path)", link and link["ref_id"] == IDS["call"], link)
        p = await get_now()
        check("accept: gone from the queue", not item(p, f"sug:{sid}"))
        code, u = await do_undo(res["undo_id"])
        check("accept undo: task deleted, suggestion pending again",
              code == 200 and task_row(tid) is None and sugg_status(sid) == "pending"
              and phone_suggestions.get_suggestion(OWNER, sid)["payload"]["triggers"] != {"wake": True}, u)

        code, res = await act(f"sug:{sid}", "accept", {"triggers": {"location": {"location": "home", "event": "arrive"}}})
        tid = (phone_suggestions.get_suggestion(OWNER, sid)["result_ref"] or "")[5:]
        check("accept: 'home' choice → the HA home zone", code == 200 and res["message"] == "Reminder set · when you get home"
              and json.loads(task_row(tid)["triggers"])["location"]["location"] == "home", res)
        await do_undo(res["undo_id"])
        code, res = await act(f"sug:{sid}", "schedule", {"slot": "wake"})
        tid = (phone_suggestions.get_suggestion(OWNER, sid)["result_ref"] or "")[5:]
        check("schedule: suggestion dropped on 'On waking' → accepted with the wake trigger",
              code == 200 and json.loads(task_row(tid)["triggers"]) == {"wake": True}, res)
        await do_undo(res["undo_id"])
        code, res = await act(f"sug:{sid}", "accept", {"triggers": {"wake": True}})
        code2, res2 = await act(f"sug:{sid}", "accept", status=409)
        check("accept: twice → 409", code2 == 409 and res2["ok"] is False, res2)
        await do_undo(res["undo_id"])

        code, res = await act(f"sug:{IDS['sug_task2']}", "dismiss")
        check("dismiss: suggestion dismissed", code == 200 and sugg_status(IDS["sug_task2"]) == "dismissed", res)
        code, _ = await do_undo(res["undo_id"])
        check("dismiss undo: pending again", code == 200 and sugg_status(IDS["sug_task2"]) == "pending")

        code, res = await act(f"sug:{IDS['sug_task2']}", "snooze", {"minutes": 90})
        p = await get_now()
        check("snooze: suggestion card hidden", code == 200 and not item(p, f"sug:{IDS['sug_task2']}")
              and sugg_status(IDS["sug_task2"]) == "pending", res)
        await do_undo(res["undo_id"])
        check("snooze undo: card back", item(await get_now(), f"sug:{IDS['sug_task2']}") is not None)

        code, res = await act(f"sug:{IDS['sug_event']}", "accept")
        check("accept event: calendar event added", code == 200 and seen["events_added"] == ["ארוחת שישי"]
              and res["message"].startswith("Added to calendar"), res)
        code, _ = await do_undo(res["undo_id"])
        check("accept event undo: the calendar's own undo ran, suggestion pending",
              code == 200 and seen["event_undos"] == 1 and sugg_status(IDS["sug_event"]) == "pending")

        code, res = await act("quiet:all", "dismiss")
        check("quiet: dismiss all", code == 200 and res["count"] == 2
              and sugg_status(IDS["sug_notif"]) == "dismissed" and sugg_status(IDS["sug_low"]) == "dismissed", res)
        await do_undo(res["undo_id"])
        check("quiet: undo brings them back", sugg_status(IDS["sug_notif"]) == "pending"
              and sugg_status(IDS["sug_low"]) == "pending")

        # --- notifications ---------------------------------------------------------------
        nid = IDS["n_gal"]
        code, res = await act(f"notif:{nid}", "snooze", {"minutes": 60})
        check("notif snooze: device key with until_ms for the shade",
              code == 200 and res["device_keys"] and res["device_keys"][0]["key"] == "k-gal"
              and res["device_keys"][0]["until_ms"] > 0 and res["message"].startswith("Snoozed · back"), res)
        check("notif snooze: out of the queue", not item(await get_now(), f"notif:{nid}"))
        await do_undo(res["undo_id"])
        check("notif snooze undo: back", item(await get_now(), f"notif:{nid}") is not None
              and notif_row(nid)["snoozed_until"] is None)
        code, res = await act(f"notif:{nid}", "dismiss")
        check("notif dismiss: device key to cancel", code == 200 and res["device_keys"] == ["k-gal"]
              and notif_row(nid)["status"] == "dismissed", res)
        await do_undo(res["undo_id"])
        check("notif dismiss undo: active again", notif_row(nid)["status"] == "active" and notif_row(nid)["removed_reason"] is None)
        code, res = await act(f"notif:{nid}", "schedule", {"slot": "home"})
        n = notif_row(nid)
        rtask = task_row(res.get("task_id") or "")
        check("notif schedule home: reminder created, pointing at the notification",
              code == 200 and rtask and rtask["title"] == "Reply to Gal"
              and json.loads(rtask["triggers"])["location"]["location"] == "home", res)
        check("notif schedule home: out of Now, not cancelled in the shade",
              n["status"] == "archived" and notification_shade.keys_to_cancel(OWNER, ["k-gal"]) == [], n["status"])
        await do_undo(res["undo_id"])
        check("notif schedule undo: reminder gone, notification active", task_row(res["task_id"]) is None
              and notif_row(nid)["status"] == "active")
        code, res = await act(f"notif:{nid}", "reply", status=400)
        check("notif reply: handled by the phone → 400", code == 400)
        if NOW.hour <= 21:
            code, res = await act(f"notif:{nid}", "schedule", {"slot": str(NOW.hour + 2)})
            check("notif schedule hour: snoozed until then", code == 200 and notif_row(nid)["snoozed_until"], res)
            await do_undo(res["undo_id"])

        # --- reminders ---------------------------------------------------------------------
        t = IDS["t_car"]
        clears_before = len(pushes("reminder_clear"))
        code, res = await act(f"task:{t}", "done")
        check("done: completed + reminder_clear pushed",
              code == 200 and task_row(t)["status"] == "completed" and len(pushes("reminder_clear")) == clears_before + 1
              and pushes("reminder_clear")[-1]["data"]["task_id"] == t, res)
        code, _ = await do_undo(res["undo_id"])
        check("done undo: open again and back in Now", code == 200 and task_row(t)["status"] == "needs_action"
              and item(await get_now(), f"task:{t}") is not None)
        code, res = await act(f"task:{t}", "snooze", {"minutes": 60})
        row = task_row(t)
        check("snooze: time trigger, snooze count +1, reminder cleared",
              code == 200 and "time" in json.loads(row["triggers"]) and row["snooze_count"] == 3
              and res["message"].startswith("Snoozed · "), res)
        check("snooze: out of Now", not item(await get_now(), f"task:{t}"))
        await do_undo(res["undo_id"])
        row = task_row(t)
        check("snooze undo: triggers + count restored, back in Now",
              json.loads(row["triggers"]) == {} and row["snooze_count"] == 2 and item(await get_now(), f"task:{t}") is not None, row)
        code, res = await act(f"task:{t}", "snooze", {"slot": "home"})
        check("snooze home: arrive-home trigger", code == 200
              and json.loads(task_row(t)["triggers"]) == {"location": {"location": "home", "event": "arrive"}}
              and res["message"] == "Snoozed · when you get home", res)
        await do_undo(res["undo_id"])
        code, res = await act(f"task:{t}", "schedule", {"slot": "wake"})
        check("schedule: dropped on 'On waking'", code == 200 and json.loads(task_row(t)["triggers"]) == {"wake": True}
              and res["message"] == "Moved to when you wake", res)
        await do_undo(res["undo_id"])
        if NOW.hour <= 21:
            code, res = await act(f"task:{t}", "schedule", {"slot": str(NOW.hour + 2)})
            check("schedule: dropped on an hour → time trigger that hour",
                  code == 200 and json.loads(task_row(t)["triggers"])["time"].endswith(f"{NOW.hour + 2:02d}:00"), res)
            await do_undo(res["undo_id"])
        code, res = await act(f"task:{t}", "schedule", {"slot": str(max(0, NOW.hour - 1))}, status=400)
        check("schedule: a past hour → 400", code == 400, res)
        code, res = await act(f"task:{t}", "dismiss")
        check("dismiss reminder: out of Now, still open", code == 200 and not item(await get_now(), f"task:{t}")
              and task_row(t)["status"] == "needs_action", res)
        await do_undo(res["undo_id"])
        check("dismiss reminder undo: back", item(await get_now(), f"task:{t}") is not None)

        # --- checks + review card snoozes -----------------------------------------------------
        code, res = await act("check:wake", "ignore")
        r = await client.get("/api/checks")
        wake_c = next(c for c in (await r.json())["checks"] if c["key"] == "wake")
        check("check ignore: hidden from Now, ignored_until in /api/checks",
              code == 200 and not item(await get_now(), "check:wake") and wake_c["ignored_until"], wake_c)
        await do_undo(res["undo_id"])
        check("check ignore undo: card back", item(await get_now(), "check:wake") is not None)
        code, res = await act("check:wake", "snooze", {"minutes": 120})
        check("check snooze: hidden", code == 200 and not item(await get_now(), "check:wake"), res)
        await do_undo(res["undo_id"])
        r = await client.post("/api/checks/wake/ignore", json={"days": 3})
        body = await r.json()
        check("POST /api/checks/{key}/ignore", r.status == 200 and body["until"] and body["undo_id"]
              and not item(await get_now(), "check:wake"), body)
        await do_undo(body["undo_id"])
        r = await client.post("/api/checks/nope/ignore", json={})
        check("ignore unknown check → 404", r.status == 404)

        rid = IDS["review"]
        code, res = await act(f"review:{rid}", "schedule", {"slot": "wake"})
        check("review schedule: hidden until tomorrow morning", code == 200 and not item(await get_now(), f"review:{rid}"), res)
        await do_undo(res["undo_id"])
        code, res = await act(f"review:{rid}", "skip")
        check("review skip: dismissed", code == 200 and not item(await get_now(), f"review:{rid}"), res)
        await do_undo(res["undo_id"])
        check("review skip undo: pending again", item(await get_now(), f"review:{rid}") is not None)
        code, res = await act(f"review:{rid}", "apply", {"items": [IDS["t_wake"]]})
        check("review apply: only the checked proposal applied, no undo",
              code == 200 and res["applied"] == 1 and "undo_id" not in res
              and task_row(IDS["t_later"])["status"] == "needs_action"
              and task_row(IDS["t_wake"])["due_date"], res)

        code, res = await act("approval:main", "accept", status=400)
        check("approval: no server action → 400", code == 400)
        code, res = await act("sug:missing", "accept", status=404)
        check("unknown suggestion → 404", code == 404)
        code, res = await act("bogus:1", "accept", status=400)
        check("unknown kind → 400", code == 400)
        code, res = await act(f"task:{t}", "fly", status=400)
        check("unknown action → 400", code == 400)

        # --- pop-up switch -----------------------------------------------------------------------
        before = len(pushes("suggestions"))
        sent = await popups.announce_suggestions(OWNER, "notification", IDS["n_slack"], "From Slack")
        check("pop-up: off for notification suggestions by default",
              sent is False and len(pushes("suggestions")) == before)
        sent = await popups.announce_suggestions(OWNER, "call", IDS["call"], "From the call with אמא")
        check("pop-up: still on for a fresh call", sent is True and len(pushes("suggestions")) == before + 1
              and pushes("suggestions")[-1]["data"]["source_type"] == "call")
        popups.SUGGESTION_POPUP_NOTIFICATIONS = True
        try:
            sent = await popups.announce_suggestions(OWNER, "notification", IDS["n_slack"], "From Slack")
        finally:
            popups.SUGGESTION_POPUP_NOTIFICATIONS = False
        check("pop-up: SUGGESTION_POPUP_NOTIFICATIONS=1 turns it back on", sent is True)

        # --- Notifications overview ------------------------------------------------------------------
        with get_db_connection() as conn:
            items_gh = notification_summary.active_by_app(conn, OWNER)["com.github.android"]
            conn.execute("INSERT INTO notification_summaries (user_id, scope, signature, summary, count, updated_at) "
                         "VALUES (?, 'com.github.android', ?, 'CI passed on three PRs\nNothing failed', 3, '2026-01-01')",
                         (OWNER.id, notification_summary._signature(items_gh)))
            conn.commit()
        r = await client.get("/api/notifications/overview")
        ov = await r.json()
        apps = {a["package"]: a for a in ov["apps"]}
        check("overview: counts", ov["counts"] == {"total": 7, "noise": 4, "needs_you": 1, "snoozed": 0}, ov["counts"])
        check("overview: rule words (alert, important → normal, none → normal)",
              apps["com.bank"]["rule"] == "alert" and apps["com.slack"]["rule"] == "normal"
              and apps["com.whatsapp"]["rule"] == "normal", {k: a["rule"] for k, a in apps.items()})
        check("overview: needs-you app first, then newest",
              [a["package"] for a in ov["apps"]] == ["com.whatsapp", "com.slack", "com.github.android", "com.bank"],
              [a["package"] for a in ov["apps"]])
        check("overview: per-app counts", apps["com.github.android"]["count"] == 3 and apps["com.github.android"]["noise"] == 3
              and apps["com.whatsapp"]["needs_you"] == 1, apps["com.github.android"])
        check("overview: cached AI summary as points, else the latest line",
              apps["com.github.android"]["summary"] == ["CI passed on three PRs", "Nothing failed"]
              and apps["com.slack"]["summary"] == ["Amit: Can you review the auth PR?"], apps["com.slack"]["summary"])
        quiet_apps = {a["package"]: a for a in ov["quiet_apps"]}
        check("overview: muted apps under quiet_apps (also one with nothing waiting)",
              set(quiet_apps) == {"com.twitter.android", "com.old"} and quiet_apps["com.twitter.android"]["noise"] == 1
              and quiet_apps["com.old"]["count"] == 0, ov["quiet_apps"])
        rs = ov["rule_suggestions"]
        check("overview: rule suggestion for the app cleared without opening",
              len(rs) == 1 and rs[0]["package"] == "com.github.android" and rs[0]["cleared"] == 12
              and rs[0]["action"] == "quiet" and "without opening it" in rs[0]["reason"], rs)

        r = await client.post("/api/notifications/rule-suggestions", json={"package": "com.github.android", "accept": True})
        body = await r.json()
        with get_db_connection() as conn:
            rule = notification_rules.rule_for(conn, OWNER, "com.github.android", "")
        ov = await (await client.get("/api/notifications/overview")).json()
        check("rule suggestion accept: muted, moves to quiet apps, not suggested again",
              r.status == 200 and rule == "mute" and body["undo_id"]
              and "com.github.android" in [a["package"] for a in ov["quiet_apps"]] and ov["rule_suggestions"] == [], body)
        await do_undo(body["undo_id"])
        with get_db_connection() as conn:
            rule = notification_rules.rule_for(conn, OWNER, "com.github.android", "")
        ov = await (await client.get("/api/notifications/overview")).json()
        check("rule suggestion accept undo: rule removed, suggested again", rule is None and len(ov["rule_suggestions"]) == 1)
        r = await client.post("/api/notifications/rule-suggestions", json={"package": "com.github.android", "accept": False})
        body = await r.json()
        ov = await (await client.get("/api/notifications/overview")).json()
        check("rule suggestion decline: remembered (not offered again)", r.status == 200 and ov["rule_suggestions"] == [])
        await do_undo(body["undo_id"])
        ov = await (await client.get("/api/notifications/overview")).json()
        check("rule suggestion decline undo: offered again", len(ov["rule_suggestions"]) == 1)
        r = await client.post("/api/notifications/rule-suggestions", json={"accept": True})
        check("rule suggestion without package → 400", r.status == 400)

        # --- clear by app / the noise -------------------------------------------------------------------
        r = await client.post("/api/notifications/status", json={"package": "com.github.android", "status": "dismissed"})
        body = await r.json()
        check("status by package: all of that app, device keys + undo_id",
              r.status == 200 and body["updated"] == 3 and len(body["device_keys"]) == 3 and body["undo_id"]
              and all(notif_row(i)["status"] == "dismissed" for i in IDS["n_gh"]), body)
        await do_undo(body["undo_id"])
        check("status by package undo: active again in Dana",
              all(notif_row(i)["status"] == "active" and notif_row(i)["removed_reason"] is None for i in IDS["n_gh"]))
        r = await client.post("/api/notifications/status", json={"bucket": "dismiss", "status": "dismissed"})
        body = await r.json()
        check("clear the noise: dismiss bucket + the muted app",
              body["updated"] == 4 and notif_row(IDS["n_x"])["status"] == "dismissed", body)
        await do_undo(body["undo_id"])
        check("clear the noise undo", notif_row(IDS["n_x"])["status"] == "active")
        r = await client.post("/api/notifications/status", json={"ids": [IDS["n_slack"]], "status": "dismissed"})
        body = await r.json()
        check("status by ids still works (+ undo_id)", body["updated"] == 1 and body["undo_id"])
        await do_undo(body["undo_id"])

        # --- checks ------------------------------------------------------------------------------------
        r = await client.get("/api/checks")
        cs = {c["key"]: c for c in (await r.json())["checks"]}
        check("checks: every key with label/ok/detail/since",
              set(cs) == set(checks.all_keys()) and all({"label", "ok", "detail", "since"} <= set(c) for c in cs.values()), cs)
        check("checks: push ok after pings", cs["push"]["ok"] and cs["push"]["detail"].startswith("Last check-in"), cs["push"])
        check("checks: notification sync ok after a batch", cs["notification_sync"]["ok"], cs["notification_sync"])
        check("checks: location + car ok", cs["location"]["ok"] and cs["car"]["ok"], (cs["location"], cs["car"]))
        check("checks: wake not ok, 'quiet for 4 days'", not cs["wake"]["ok"] and cs["wake"]["detail"] == "quiet for 4 days",
              cs["wake"])
        check("checks: calendar read ok", cs["calendar"]["ok"], cs["calendar"])
        with get_db_connection() as conn:
            conn.execute("UPDATE system_marks SET last_at = ? WHERE key = ?",
                         ((NOW - datetime.timedelta(days=5)).astimezone(UTC).isoformat(), f"ha_event:{OWNER.id}:person"))
            conn.execute("INSERT INTO wake_log (user_id, date, at, why) VALUES (?, ?, ?, 'unlock')",
                         (OWNER.id, NOW.date().isoformat(), NOW.isoformat(timespec="seconds")))
            conn.commit()
        seen["calendar_fail"] = True
        await calendar_today.refresh_now(OWNER)
        cs = {c["key"]: c for c in checks.compute()}
        check("checks: location quiet for 5 days → not ok", not cs["location"]["ok"]
              and cs["location"]["detail"] == "quiet for 5 days", cs["location"])
        check("checks: wake confirmed today → ok", cs["wake"]["ok"] and cs["wake"]["detail"] == "confirmed today", cs["wake"])
        check("checks: calendar failing → not ok", not cs["calendar"]["ok"], cs["calendar"])
        p = await get_now()
        check("checks: failing ones become Now cards", item(p, "check:location") and item(p, "check:calendar")
              and not item(p, "check:wake"), [i["id"] for i in p["items"] if i["kind"] == "check"])
        check("checks: the calendar's last events are kept for the day strip",
              p["day"]["calendar"] is True)
        seen["calendar_fail"] = False
        with get_db_connection() as conn:
            conn.execute("DELETE FROM phone_devices")
            conn.commit()
        cs = {c["key"]: c for c in checks.compute()}
        check("checks: no phone registered → push not ok", not cs["push"]["ok"]
              and cs["push"]["detail"] == "No phone registered", cs["push"])
        # The person entity leaving the zone "Work", as the Home Assistant watcher fires it.
        from addons.homeassistant import watcher as ha_watcher
        from addons.tasks import triggers as task_triggers
        for trigger_type, action, target in ha_watcher.transitions("person", "Work", "not_home"):
            await task_triggers.fire(OWNER, trigger_type, action=action, target=target)
        with get_db_connection() as conn:
            keys = {r["key"] for r in conn.execute("SELECT key FROM task_trigger_marks WHERE user_id = ? "
                                                   "AND key LIKE 'last_event_v2_location%work'", (OWNER.id,))}
        check("watcher: the transition is stored under the key the refire check reads",
              keys == {"last_event_v2_location_work", "last_event_v2_location_leave_work"}, keys)
        ha_status.mark_event(OWNER.id, "person", "person.robin")  # what the watcher records with the transition
        cs = {c["key"]: c for c in checks.compute()}
        check("checks: a fresh location transition → location ok again", cs["location"]["ok"], cs["location"])

        # --- search ------------------------------------------------------------------------------------
        async def search(q, limit=5):
            r = await client.get("/api/search", params={"q": q, "limit": str(limit)})
            return (await r.json())["results"]
        res = await search("Gal")
        kinds = {x["kind"] for x in res}
        check("search: person + notification for 'Gal'", {"person", "notification"} <= kinds, res)
        check("search: exact person name ranks first", res and res[0]["kind"] == "person" and res[0]["id"] == IDS["gal"], res)
        res = await search("garage")
        check("search: open reminder by title", any(x["kind"] == "reminder" and x["id"] == IDS["t_car"] for x in res), res)
        res = await search("bill")
        check("search: completed tasks are not found", not any(x["id"] == IDS["t_done"] for x in res), res)
        res = await search("כביסה")
        check("search: Hebrew reminder", any(x["id"] == IDS["t_later"] for x in res), res)
        res = await search("טכנאי")
        check("search: Hebrew call (transcript via FTS / summary)", any(x["kind"] == "call" and x["id"] == IDS["call"] for x in res), res)
        res = await search("אמא")
        check("search: call by caller", any(x["kind"] == "call" for x in res), res)
        res = await search("bread")
        check("search: shopping item", any(x["kind"] == "shopping" and x["title"] == "Bread" for x in res), res)
        res = await search("deposit")
        check("search: note by body", any(x["kind"] == "note" and x["id"] == "e1" and x["subtitle"] == "Wedding" for x in res), res)
        res = await search("wedding")
        check("search: project as a note", any(x["id"] == "project:p1" for x in res), res)
        res = await search("CI passed", limit=2)
        check("search: limit per addon", len([x for x in res if x["kind"] == "notification"]) == 2, res)
        check("search: empty query → nothing", await search("") == [])
        check("search: every result has kind/id/title/subtitle",
              all({"kind", "id", "title", "subtitle"} <= set(x) for x in await search("a")))
        res = await search("CI passed")
        nres = [x for x in res if x["kind"] == "notification"]
        check("search: notification results carry the app's package (routing)",
              nres and all(x["package"] == "com.github.android" for x in nres), nres)
        check("search: notification title is what it says, sender in the subtitle",
              nres and nres[0]["title"].startswith("CI passed on #") and "homelab" in nres[0]["subtitle"], nres)
        res = await search("deposit")
        check("search: note results carry project_id", any(x["id"] == "e1" and x["project_id"] == "p1" for x in res), res)
        res = await search("wedding")
        check("search: a project result carries its own id as project_id",
              any(x["id"] == "project:p1" and x["project_id"] == "p1" for x in res), res)
        res = await search("bread")
        check("search: other kinds unchanged (no routing extras)",
              all(set(x) == {"kind", "id", "title", "subtitle"} for x in res if x["kind"] == "shopping"), res)

    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    # As in production, the work runs as a user: requests as theirs, background
    # loops as the first admin (the interim owner of the phone's data).
    with users.acting_as(OWNER):
        asyncio.run(main())
