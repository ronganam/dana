"""Notifications pipeline: batch ingest (dedupe, redaction, loop guards) →
TypeSafe triage (mocked) → buckets / escalation → model suggestions (mocked)
→ accept (task linked to the notification) / bulk dismiss, shade sync
(removals + cancel_keys), rules, search, agent tools, digest, retention, and
the truth table in addons/phone/notifications/triage.decide.

Run: bin/test test_notifications
"""
import asyncio
import datetime
import json
import re
import os
import sys

from cryptography.fernet import Fernet

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("notifications_test")
os.environ["DANA_INSECURE_DEV"] = "1"  # no API_TOKEN: local development mode, every request is the first admin
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # throwaway: encrypts the fake credentials
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["TYPESAFE_API_KEY"] = "test-typesafe"
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
testenv.connect_admin("homeassistant", credentials={"host": "http://homeassistant.local:8123", "token": "test-ha"})
OWNER = users.first_admin()
ensure_default_groups_for_everyone()
from core import sessions_api
from addons.phone import api as phone_api
from addons.phone.notifications import api as notifications_api
from addons.tasks import api as tasks_api
from core.auth import setup_auth
from core import clock, context_chats
from addons.phone.calls import context as call_context, store as call_store
from core.db import get_db_connection
from core.addons import registry, store
from addons.phone import notification_tools as phone_tools, popups, push as phone_push
from addons.phone.notifications import digest, escalation, ingest, processing, retention, rules, shade
from addons.phone.notifications import summary as ns, triage, values

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


# --- Fakes -----------------------------------------------------------------------

TOMORROW = (clock.now() + datetime.timedelta(days=1)).date().isoformat()
seen = {"typesafe": 0, "model": 0, "push": [], "last_state": None}


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


def answers_for(text: str):
    """Scripted Jev answers keyed on the notification text."""
    if "SALE" in text:
        return {"category": {"type": "choice", "choice": "promotion", "confidence": 0.9, "probabilities": {}},
                "priority": {"type": "score", "score": 0.3, "probabilities": {}},
                "needs_action": noul(0.1), "from_person": noul(0.02), "time_sensitive": noul(0.2), "promotional": noul(0.95)}
    if "milk" in text:
        return {"category": {"type": "choice", "choice": "personal_message", "confidence": 0.9, "probabilities": {}},
                "priority": {"type": "score", "score": 2.8, "probabilities": {}},
                "needs_action": noul(0.92), "from_person": noul(0.97), "time_sensitive": noul(0.4), "promotional": noul(0.01)}
    if "door" in text:
        return {"category": {"type": "choice", "choice": "delivery", "confidence": 0.9, "probabilities": {}},
                "priority": {"type": "score", "score": 3.8, "probabilities": {}},
                "needs_action": noul(0.8), "from_person": noul(0.1), "time_sensitive": noul(0.95), "promotional": noul(0.0)}
    return {"category": {"type": "choice", "choice": "news", "confidence": 0.6, "probabilities": {}},
            "priority": {"type": "score", "score": 1.8, "probabilities": {}},
            "needs_action": noul(0.1), "from_person": noul(0.5), "time_sensitive": noul(0.1), "promotional": noul(0.1)}


async def fake_post(self, url, *args, **kwargs):
    body = kwargs.get("json") or {}
    if "typesafe" in url:
        seen["typesafe"] += 1
        seen["last_state"] = body.get("state")
        assert set(body["questions"]) == set(triage.QUESTION_IDS), "all questions bundled"
        text = json.dumps(body["state"]["notification"], ensure_ascii=False)
        if "BROKEN" in text:
            return FakeResponse({"detail": "boom"}, status=500)
        return FakeResponse({"model": "jev-1.14.0", "answers": answers_for(text), "usage": {}})
    if "fcm.googleapis.com" in url:
        # Phone push: record the event the ping points at (the ping itself
        # carries only seq + type).
        msg = body["message"]
        assert set(msg["data"]) == {"seq", "type"} and msg["android"]["priority"] == "HIGH", msg
        ev = phone_push.events_after(OWNER, int(msg["data"]["seq"]) - 1)["events"][0]
        seen["push"].append({**ev, "ping": msg})
        return FakeResponse({"name": "projects/dana-test/messages/1"})
    raise AssertionError(f"unexpected POST {url}")


httpx.AsyncClient.post = fake_post


class ModelDown(Exception):
    status_code = 500


def reply(obj):
    return litellm.ModelResponse(choices=[{"message": {"role": "assistant",
                                                       "content": json.dumps(obj, ensure_ascii=False)}}])


async def fake_acompletion(**kwargs):
    """The model, at the LiteLLM boundary (core/llm.py runs for real)."""
    props = set(kwargs["response_format"]["json_schema"]["schema"]["properties"])
    prompt = kwargs["messages"][0]["content"]
    if props in ({"summary"}, {"overall"}):
        field = next(iter(props))
        seen.setdefault("summary_prompts", []).append((field, prompt))
        if seen.get("summary_fail"):
            raise ModelDown("boom")
        n = sum(1 for f, _ in seen["summary_prompts"] if f == field)
        app = (re.findall(r'from the app "([^"]+)"', prompt) or ["?"])[0]
        text = f"sum {app} v{n}" if field == "summary" else f"briefing v{n}"
        # Echoes a needs-you tag the way the model sometimes did: it must never reach the owner.
        return reply({field: [f"[needs Robin] {text}", "- second point. [needs Robin]"]})
    seen["model"] += 1
    items = [{"text": "Buy milk", "owner": "me", "kind": "task", "due_date": TOMORROW,
              "time_trigger": f"{TOMORROW} 17:00", "car_trigger": "enter", "priority": "medium",
              "confidence": 0.85, "evidence": "can you buy milk"}]
    return reply({"action_items": items})


litellm.acompletion = fake_acompletion


async def fake_fcm_token():
    return "fcm-test-token"

phone_push._access_token = fake_fcm_token


def pushes(type_):
    return [p for p in seen["push"] if p["type"] == type_]


async def no_sleep(*_a, **_k):
    return None

triage.asyncio.sleep = no_sleep  # retries without waiting


async def fake_zones(conn):
    return [{"name": "Home", "entity_id": "zone.home"}]

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


def build_app():
    app = web.Application()
    setup_auth(app)  # no API_TOKEN: every request is the first admin
    r = app.router
    r.add_get("/api/suggestions", phone_api.get_suggestions_api)
    r.add_post("/api/suggestions/bulk", phone_api.bulk_suggestions_api)
    r.add_post("/api/suggestions/{id}/accept", phone_api.accept_suggestion_api)
    r.add_post("/api/notifications/batch", notifications_api.notifications_batch_api)
    r.add_get("/api/notifications", notifications_api.notifications_list_api)
    r.add_delete("/api/notifications", notifications_api.notifications_delete_app_api)
    r.add_post("/api/notifications/status", notifications_api.notifications_status_api)
    r.add_post("/api/notifications/snooze", notifications_api.notifications_snooze_api)
    r.add_get("/api/notifications/apps", notifications_api.notifications_apps_api)
    r.add_post("/api/notifications/rules", notifications_api.notifications_rule_api)
    r.add_post("/api/notifications/seen", notifications_api.notifications_seen_api)
    r.add_post("/api/phone/register", phone_api.phone_register_api)
    r.add_get("/api/phone/events", phone_api.phone_events_api)
    r.add_get("/api/phone/devices", phone_api.phone_devices_api)
    r.add_get("/api/notifications/digest", notifications_api.notifications_digest_api)
    r.add_get("/api/notifications/summaries", notifications_api.notifications_summaries_api)
    r.add_get("/api/notifications/requests/{rid}", notifications_api.notification_request_api)
    r.add_post("/api/notifications/requests/{rid}/result", notifications_api.notification_request_result_api)
    r.add_post("/api/notifications/{id}/act", notifications_api.notification_act_api)
    r.add_get("/api/notifications/{id}", notifications_api.notification_detail_api)
    r.add_post("/api/notifications/{id}/triage", notifications_api.notifications_retriage_api)
    r.add_post("/api/chat/context", sessions_api.start_context_chat_api)
    from core import actions_api
    actions_api.setup(app)  # POST /api/undo/{id}
    r.add_get("/api/tasks", tasks_api.get_tasks_api)
    return app


def note(key, text, title="Title", package="com.whatsapp", app="WhatsApp", **kw):
    return {"key": key, "package": package, "app_name": app, "title": title, "text": text,
            "posted_at": int(clock.now().timestamp() * 1000), **kw}


async def settle():
    for _ in range(200):
        if not processing._tasks:
            return
        await asyncio.sleep(0.02)


def row(nid):
    with get_db_connection() as conn:
        return dict(conn.execute("SELECT * FROM notifications WHERE id = ?", (nid,)).fetchone())


async def main():
    # --- truth table (no network) ---
    d = triage.decide(answers_for("SALE"))
    check("decide: promotion → dismiss", d["bucket"] == "dismiss" and not d["extract"], d)
    d = triage.decide(answers_for("milk"))
    check("decide: request from a person → needs_you + extract", d["bucket"] == "needs_you" and d["extract"], d)
    d = triage.decide(answers_for("door"))
    check("decide: courier at the door → urgent + escalate", d["priority"] == "urgent" and d["escalate"], d)
    d = triage.decide(answers_for("other"))
    check("decide: unsettled news → normal", d["bucket"] == "normal" and d["priority"] == "normal", d)
    d = triage.decide(answers_for("other"), low_engagement=True)
    check("decide: low engagement app → dismiss", d["bucket"] == "dismiss", d)
    d = triage.decide(answers_for("SALE"), rule="important")
    check("decide: important rule wins", d["bucket"] == "needs_you" and d["priority"] == "important", d)
    d = triage.decide(answers_for("door"), rule="mute")
    check("decide: mute rule → no escalation", d["bucket"] == "dismiss" and not d["escalate"], d)
    hype = {"category": {"type": "choice", "choice": "social_media", "confidence": 0.9, "probabilities": {}},
            "priority": {"type": "score", "score": 3.9, "probabilities": {}},
            "needs_action": noul(0.8), "from_person": noul(0.2), "time_sensitive": noul(0.95), "promotional": noul(0.1)}
    d = triage.decide(hype)
    check("decide: alarming social post → not needs_you, no escalation, no extract",
          d["bucket"] == "normal" and not d["escalate"] and not d["extract"], d)
    d = triage.decide(hype, rule="important")
    check("decide: feed app marked important → needs_you + escalate", d["bucket"] == "needs_you" and d["escalate"], d)
    dm = dict(hype, from_person=noul(0.95))
    d = triage.decide(dm)
    check("decide: a person asking on a social app → needs_you", d["bucket"] == "needs_you" and d["extract"], d)
    for flag, why in (("owner_wrote_it", "Robin's own message"), ("tracked_elsewhere", "a work tool keeps it"),
                      ("record_only", "a receipt"), ("self_clearing", "a device state")):
        d = triage.decide(dict(answers_for("milk"), **{flag: noul(0.9)}))
        check(f"decide: no extraction for {why}", d["bucket"] == "needs_you" and not d["extract"], d)
    d = triage.decide(dict(answers_for("milk"), addressed_to_owner=noul(0.1)))
    check("decide: no extraction for a group message to someone else", not d["extract"], d)
    d = triage.decide(dict(answers_for("milk"), addressed_to_owner=noul(0.5), tracked_elsewhere=noul(0.5)))
    check("decide: unsettled gates don't block extraction", d["extract"], d)
    cal = dict(answers_for("milk"), category={"type": "choice", "choice": "calendar_reminder", "confidence": 0.9,
                                              "probabilities": {}})
    check("decide: calendar reminder → no new event", not triage.decide(cal)["extract"])
    d = triage.decide(answers_for("other"), rule="alert")
    check("decide: alert rule → needs_you + escalate even when not urgent",
          d["bucket"] == "needs_you" and d["escalate"], d)
    asks = dict(answers_for("milk"), time_sensitive=noul(0.64), urgent_request=noul(0.93), addressed_to_owner=noul(0.81))
    d = triage.decide(asks)
    check("decide: a person saying 'urgent' → urgent + escalate", d["priority"] == "urgent" and d["escalate"], d)
    d = triage.decide(dict(asks, from_person=noul(0.1)))
    check("decide: an app saying 'urgent' → not escalated", not d["escalate"], d)
    d = triage.decide(dict(hype, urgent_request=noul(0.95)))
    check("decide: a feed post saying 'urgent' → not escalated", not d["escalate"], d)
    check("decide: missing answers stay unsettled",
          triage.decide({})["bucket"] == "normal")
    check("redact: OTP hidden", ingest.redact("Your verification code is 482913") == "Your verification code is [code]")
    check("redact: plain numbers kept", ingest.redact("Meet at 1930 near gate 4") == "Meet at 1930 near gate 4")

    async with TestClient(TestServer(build_app())) as client:
        # --- phone registers for direct push ---
        r = await client.post("/api/phone/register", json={"device_id": "dev-1", "token": "tok-phone",
                                                          "name": "Pixel 8", "app_version": "7.11.0"})
        reg = await r.json()
        check("push: register → latest_seq", r.status == 200 and reg["latest_seq"] == 0, reg)
        r = await client.post("/api/phone/register", json={"device_id": "dev-1"})
        check("push: register without a token → 400", r.status == 400)

        # --- ingest ---
        batch = {"posted": [
            note("k1", "Gal: can you buy milk on the way?", title="Gal", sender="Gal"),
            note("k2", "BIG SALE 50% today only", title="Shop", package="com.shop", app="Shop"),
            note("k3", "Courier is at the door", title="Wolt", package="com.wolt", app="Wolt"),
            note("k4", "Headline about something", title="News", package="com.news", app="News"),
            note("k1", "Gal: can you buy milk on the way?", title="Gal", sender="Gal"),  # duplicate
            note("k5", "self", package="io.github.ronganam.dana"),  # own app
            note("k6", "loop", title=f"{values.ESCALATION_PREFIX} Wolt", package="io.homeassistant.companion.android"),
            note("k7", "Your login code is 123456", title="Bank", package="com.bank", app="Bank"),
            note("k8", "BROKEN payload", title="Odd", package="com.odd", app="Odd"),
        ]}
        r = await client.post("/api/notifications/batch", json=batch)
        res = await r.json()
        check("batch: 200", r.status == 200, res)
        check("batch: 6 new, 1 duplicate, 2 dropped",
              res["accepted"] == 6 and res["duplicates"] == 1 and res["dropped"] == 2, res)
        await settle()
        ids = {row(i)["device_key"]: i for i in res["ids"]}
        check("ingest: OTP redacted before storage", "[code]" in row(ids["k7"])["text"], row(ids["k7"])["text"])
        check("triage: one TypeSafe call per notification", seen["typesafe"] >= 6, seen["typesafe"])
        st = seen["last_state"] or {}
        check("triage: state has owner, time and notification", {"owner", "now", "notification"} <= set(st), st)

        milk, sale, door, news = (row(ids[k]) for k in ("k1", "k2", "k3", "k4"))
        check("triage: milk → needs_you", milk["bucket"] == "needs_you" and milk["triage_status"] == "done", milk)
        check("triage: sale → dismiss", sale["bucket"] == "dismiss" and sale["category"] == "promotion", sale)
        check("triage: courier → urgent", door["priority"] == "urgent", door)
        check("triage: news → normal", news["bucket"] == "normal", news)
        broken = row(ids["k8"])
        check("triage failure → failed + normal (no guess)",
              broken["triage_status"] == "failed" and broken["bucket"] == "normal", broken)

        def wolt_alerts():
            return [p for p in pushes("alert") if p["data"].get("app") == "Wolt"]
        alerts = wolt_alerts()
        check("escalation: urgent → one full-screen alert pushed to the phone",
              len(alerts) == 1 and door["escalated_at"] and alerts[0]["ping"]["token"] == "tok-phone", seen["push"])
        extras = alerts[0]["data"] if alerts else {}
        check("escalation: alert carries id/key/app/text",
              extras.get("notification_id") == door["id"] and extras.get("key") == "k3"
              and extras.get("text") == "Courier is at the door" and extras.get("attempt") == 1, extras)
        await escalation.escalate(OWNER, row(ids["k3"]))
        check("escalation: not repeated", len(wolt_alerts()) == 1)

        # --- re-nudge until seen ---
        first = datetime.datetime.fromisoformat(row(ids["k3"])["escalated_at"])
        await escalation.renudge_due(OWNER, first + datetime.timedelta(minutes=1))
        check("re-nudge: not before 3 min", len(wolt_alerts()) == 1)
        await escalation.renudge_due(OWNER, first + datetime.timedelta(minutes=4))
        nudge = wolt_alerts()
        check("re-nudge: unseen after 3 min → alert again, attempt 2",
              len(nudge) == 2 and nudge[-1]["data"]["attempt"] == 2, nudge)
        r = await client.post("/api/notifications/seen", json={"ids": [ids["k3"]]})
        seen_res = await r.json()
        check("seen: 200", r.status == 200 and seen_res.get("updated") == 1, (r.status, seen_res))
        await escalation.renudge_due(OWNER, first + datetime.timedelta(minutes=11))
        check("re-nudge: stops once seen", len(wolt_alerts()) == 2 and row(ids["k3"])["nudges"] == 2)

        # --- snooze ---
        r = await client.post("/api/notifications/snooze", json={"ids": [ids["k4"]], "minutes": 60})
        snz = await r.json()
        check("snooze: 200 + shade key with until_ms",
              r.status == 200 and snz["updated"] == 1 and snz["device_keys"][0]["key"] == "k4"
              and snz["device_keys"][0]["until_ms"] > clock.now().timestamp() * 1000 + 3500 * 1000, snz)
        r = await client.get("/api/notifications?status=active&limit=300")
        act = await r.json()
        check("snooze: hidden from active list, counted as snoozed",
              ids["k4"] not in [n["id"] for n in act["items"]] and act["counts"]["snoozed"] == 1, act["counts"])
        r = await client.get("/api/notifications?status=snoozed")
        snoozed_items = (await r.json())["items"]
        check("snooze: listed under status=snoozed with snoozed_until",
              [n["id"] for n in snoozed_items] == [ids["k4"]] and snoozed_items[0]["snoozed_until"], snoozed_items)
        r = await client.post("/api/notifications/batch", json={
            "posted": [], "removed": [{"key": "k4", "reason": "snoozed"}], "active_keys": ["k1", "k4"]})
        b = await r.json()
        check("snooze: batch tells the phone to snooze keys still in its shade",
              [k["key"] for k in b["snooze_keys"]] == ["k4"] and "k1" not in [k["key"] for k in b["snooze_keys"]], b)
        check("snooze: a 'snoozed' shade removal doesn't dismiss or mark it",
              row(ids["k4"])["status"] == "active" and row(ids["k4"])["removed_reason"] is None, row(ids["k4"]))
        r = await client.post("/api/notifications/snooze", json={"ids": [ids["k4"]], "minutes": -5})
        check("snooze: past time rejected", r.status == 400)
        r = await client.post("/api/notifications/snooze", json={"ids": [ids["k4"]]})
        check("snooze: needs until/minutes/clear", r.status == 400)
        # Expired snooze → back in the Inbox by itself.
        with get_db_connection() as conn:
            conn.execute("UPDATE notifications SET snoozed_until = ? WHERE id = ?",
                         ((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).isoformat(timespec="seconds"),
                          ids["k4"]))
            conn.commit()
        r = await client.get("/api/notifications?status=active&limit=300")
        act = await r.json()
        check("snooze: due → active again", ids["k4"] in [n["id"] for n in act["items"]] and act["counts"]["snoozed"] == 0)
        out = await phone_tools.snooze_notifications(store.require(OWNER, "phone"), ids["k4"], minutes=30)
        check("agent: snooze_notifications", out.startswith("Snoozed 1") and row(ids["k4"])["snoozed_until"], out)
        r = await client.post("/api/notifications/snooze", json={"ids": [ids["k4"]], "clear": True})
        check("snooze: clear wakes it", (await r.json())["updated"] == 1 and row(ids["k4"])["snoozed_until"] is None)

        # --- suggestions ---
        check("extract: the model only for the actionable ones", seen["model"] == 2, seen["model"])

        # Old notifications delivered by the first sync: triaged, but neither
        # announced nor turned into suggestions.
        push_before, model_before = len(seen["push"]), seen["model"]
        old_ms = int((clock.now().timestamp() - 2 * 86400) * 1000)
        r = await client.post("/api/notifications/batch", json={"posted": [
            note("old1", "Second courier is at the door", title="Wolt", package="com.wolt", app="Wolt", posted_at=old_ms),
            note("old2", "Gal: can you buy milk again?", title="Gal", sender="Gal", posted_at=old_ms),
        ]})
        old_ids = (await r.json())["ids"]
        await settle()
        olds = [row(i) for i in old_ids]
        check("stale: triaged as usual", all(o["triage_status"] == "done" for o in olds), olds)
        check("stale: urgent one not announced", len(seen["push"]) == push_before and not olds[0]["escalated_at"], seen["push"])
        check("stale: no model extraction", seen["model"] == model_before and olds[1]["action_status"] == "none", olds[1])

        # A newer app sends real app labels: older rows named after the package get fixed.
        r = await client.post("/api/notifications/batch", json={"posted": [
            note("lbl1", "hello", package="com.labelme", app="com.labelme")]})
        lbl_id = (await r.json())["ids"][0]
        await client.post("/api/notifications/batch", json={"posted": [
            note("lbl2", "hello again", package="com.labelme", app="Label Me")]})
        await settle()
        check("app name backfilled from a newer batch", row(lbl_id)["app_name"] == "Label Me", row(lbl_id))
        with get_db_connection() as conn:  # keep the later counts as they were
            conn.execute("DELETE FROM notifications WHERE device_key IN ('old1', 'old2', 'lbl1', 'lbl2')")
            conn.commit()
        r = await client.get("/api/suggestions")
        legacy = await r.json()
        check("suggestions: default source=call hides notification ones", legacy == [], legacy)
        r = await client.get("/api/suggestions?source=all")
        allsugg = await r.json()
        milk_s = [s for s in allsugg if s["source_id"] == ids["k1"]]
        check("suggestions: notification suggestion created", len(milk_s) == 1 and milk_s[0]["source_type"] == "notification", allsugg)
        check("suggestions: call_id null for notification source", milk_s and milk_s[0]["call_id"] is None)
        check("suggestions: notification summary attached",
              milk_s and (milk_s[0].get("notification") or {}).get("app_name") == "WhatsApp", milk_s)
        check("suggestions: origin text mentions the app",
              milk_s and "WhatsApp notification" in milk_s[0]["payload"]["description"], milk_s)
        check("suggestions: one trigger per notification item (time wins over car)",
              milk_s and milk_s[0]["payload"]["triggers"] == {"time": f"{TOMORROW} 17:00"}, milk_s)
        door_s = [s for s in allsugg if s["source_id"] == ids["k3"]]
        check("suggestions: repeated item deduped against pending", len(door_s) == 0, allsugg)
        check("pop-up: notification suggestions don't pop up by default (they wait in Now)",
              pushes("suggestions") == [], pushes("suggestions"))
        popups.SUGGESTION_POPUP_NOTIFICATIONS = True
        try:
            await popups.announce_suggestions(OWNER, "notification", ids["k1"], "From WhatsApp · Gal")
        finally:
            popups.SUGGESTION_POPUP_NOTIFICATIONS = False
        pops = pushes("suggestions")
        pop_ex = pops[0]["data"] if pops else {}
        check("pop-up: with SUGGESTION_POPUP_NOTIFICATIONS=1 announced with its id",
              len(pops) == 1 and pop_ex.get("ids") == [milk_s[0]["id"]]
              and pop_ex.get("source_type") == "notification", pops)

        r = await client.get("/api/notifications")
        lst = await r.json()
        check("list: active items with counts", lst["counts"]["active"] == 6 and lst["counts"]["needs_you"] == 2, lst["counts"])
        n_milk = next(i for i in lst["items"] if i["id"] == ids["k1"])
        check("list: suggestions embedded", len(n_milk["suggestions"]) == 1)

        r = await client.post(f"/api/suggestions/{milk_s[0]['id']}/accept", json={"title": "Buy oat milk"})
        acc = await r.json()
        check("accept: task created", acc.get("kind") == "task" and acc.get("task_id"), acc)
        fb = call_context.suggestion_feedback(OWNER)["notification"]
        check("feedback: an edited accept shows the rewrite",
              any("“Buy milk” → “Buy oat milk”" in line for line in fb), fb)
        prompt_text = processing.build_extraction_prompt(OWNER, {"app_name": "WhatsApp", "text": "x"},
                                              {"feedback": call_context.suggestion_feedback(OWNER), "open_tasks": []})
        check("feedback: extraction prompt lists Robin's decisions", "Buy oat milk" in prompt_text, prompt_text[-400:])
        r = await client.get("/api/tasks")
        tasks = await r.json()
        tasks = tasks if isinstance(tasks, list) else tasks.get("tasks", [])
        t = next((t for t in tasks if t["id"] == acc.get("task_id")), {})
        check("accept: task linked to the notification",
              any(l["ref_type"] == "notification" and l["ref_id"] == ids["k1"] for l in t.get("links") or []), t)
        check("call counts unaffected", call_store.pending_suggestion_counts(OWNER) == {})

        # --- chat context ---
        r = await client.post("/api/chat/context", json={"kind": "notification", "id": ids["k1"]})
        ctx = await r.json()
        check("context: notification chat", r.status == 200 and ctx["context"]["kind"] == "notification", ctx)
        prompt = context_chats.session_context_prompt(OWNER, ctx["session_id"])
        check("context: prompt has the notification text", prompt and "buy milk" in prompt, prompt)

        # --- shade sync ---
        r = await client.post("/api/notifications/batch", json={"removed": [{"key": "k4", "reason": "click"}]})
        await r.json()
        check("removal: click → dismissed (opened)",
              row(ids["k4"])["status"] == "dismissed" and row(ids["k4"])["removed_reason"] == "opened")
        r = await client.post("/api/notifications/batch", json={"posted": [note("k20", "read it on the laptop", app="Nekogram",
                                                                               package="tw.nekomimi.nekogram"),
                                                                          note("k21", "your session expires", app="Bank",
                                                                               package="com.bank")]})
        fresh = {row(i)["device_key"]: i for i in (await r.json())["ids"]}
        await settle()
        await client.post("/api/notifications/batch", json={"removed": [{"key": "k20", "reason": "app"},
                                                                       {"key": "k21", "reason": "timeout"}]})
        check("removal: cleared by its app → out of the Inbox (archived)",
              row(fresh["k20"])["status"] == "archived" and row(fresh["k20"])["removed_reason"] == "app", row(fresh["k20"]))
        check("removal: timed out → still in the Inbox",
              row(fresh["k21"])["status"] == "active" and row(fresh["k21"])["removed_reason"] == "timeout", row(fresh["k21"]))
        r = await client.post("/api/notifications/status", json={"bucket": "dismiss", "status": "dismissed"})
        st = await r.json()
        check("bulk dismiss by bucket returns device keys", "k2" in st["device_keys"] and st["updated"] >= 1, st)
        r = await client.post("/api/notifications/batch", json={"active_keys": ["k2", "k1", "k3"]})
        res = await r.json()
        check("cancel_keys: only the ones dismissed in Dana", res["cancel_keys"] == ["k2"], res)
        r = await client.post("/api/notifications/batch", json={"active_keys": ["k1"], "snoozed_keys": ["k2"]})
        res = await r.json()
        check("cancel_keys: also a dismissed one parked in the phone's snooze list",
              res["cancel_keys"] == ["k2"] and res["snooze_keys"] == [], res)
        r = await client.post("/api/notifications/status", json={"ids": [ids["k2"]], "status": "active"})
        await r.json()
        check("restore: back to active", row(ids["k2"])["status"] == "active")
        r = await client.post("/api/notifications/batch", json={"active_keys": ["k2"]})
        check("restore: no longer cancelled", (await r.json())["cancel_keys"] == [])

        # --- same shade entry, new content ---
        r = await client.post("/api/notifications/batch", json={"posted": [
            note("k1", "Gal: and bread please", title="Gal", sender="Gal")]})
        upd = await r.json()
        await settle()
        check("update: old version archived, kept",
              row(ids["k1"])["status"] == "archived" and row(ids["k1"])["removed_reason"] == "updated")
        check("update: new version active", row(upd["ids"][0])["status"] == "active")

        # --- rules ---
        before = seen["typesafe"]
        shade.set_status(OWNER, [ids["k4"]], "active")  # opened above; back in the Inbox for the rule checks
        check("rule: news starts in the normal bucket", row(ids["k4"])["bucket"] == "normal", row(ids["k4"])["bucket"])
        r = await client.post("/api/notifications/rules", json={"package": "com.news", "action": "mute"})
        rule = await r.json()
        mute_undo = rule.get("undo_id")
        check("rule: saved", rule["action"] == "mute", rule)
        check("rule: response carries undo_id + message, moved what was waiting",
              mute_undo and rule["message"] == "News will stay quiet" and rule["moved"] == 1
              and row(ids["k4"])["bucket"] == "dismiss", rule)
        r = await client.post("/api/notifications/batch", json={"posted": [
            note("k9", "Another headline", title="News", package="com.news", app="News")]})
        nres = await r.json()
        await settle()
        n9 = row(nres["ids"][0])
        check("rule: muted app skips the model", seen["typesafe"] == before and n9["bucket"] == "dismiss"
              and n9["triage_status"] == "skipped", n9)
        r = await client.get("/api/notifications/apps")
        apps = await r.json()
        check("apps: summary with rule", any(a["package"] == "com.news" and a["rule"] == "mute" for a in apps["apps"]), apps)

        # Un-muting puts what triage answered back where it belongs (no new model call for it);
        # the one muted at ingest (never answered) gets its first triage.
        before = seen["typesafe"]
        r = await client.post("/api/notifications/rules", json={"package": "com.news", "action": "none"})
        unmute = await r.json()
        await settle()
        check("rule removed: answered row back to where triage put it (normal), no re-ask",
              row(ids["k4"])["bucket"] == "normal" and row(ids["k4"])["priority"] == "normal", row(ids["k4"]))
        check("rule removed: the never-triaged (muted at ingest) row gets its first triage only",
              seen["typesafe"] == before + 1 and row(n9["id"])["triage_status"] == "done"
              and row(n9["id"])["bucket"] == "normal", (seen["typesafe"] - before, row(n9["id"])["bucket"]))
        check("rule removed: message + undo_id", unmute["message"] == "Dana decides for News" and unmute["undo_id"], unmute)
        r = await client.post(f"/api/undo/{unmute['undo_id']}")
        with get_db_connection() as conn:
            back = rules.rule_for(conn, OWNER, "com.news", "")
        check("rule removed → undo: mute rule back, both rows back in dismiss",
              r.status == 200 and back == "mute" and row(ids["k4"])["bucket"] == "dismiss"
              and row(n9["id"])["bucket"] == "dismiss", (back, row(ids["k4"])["bucket"], row(n9["id"])["bucket"]))
        r = await client.post(f"/api/undo/{mute_undo}")
        with get_db_connection() as conn:
            back = rules.rule_for(conn, OWNER, "com.news", "")
        check("mute → undo: no rule again, the row it moved is back in normal",
              r.status == 200 and back is None and row(ids["k4"])["bucket"] == "normal", (back, row(ids["k4"])["bucket"]))

        # The other direction: important lifts noise into needs-you; removing it puts it back.
        k2 = row(ids["k2"])
        r = await client.post("/api/notifications/rules", json={"package": "com.shop", "action": "important"})
        imp = await r.json()
        check("rule important: noise → needs_you, priority at least important",
              row(ids["k2"])["bucket"] == "needs_you" and row(ids["k2"])["priority"] == "important"
              and imp["message"] == "Shop marked important", (row(ids["k2"])["bucket"], imp))
        r = await client.post("/api/notifications/rules", json={"package": "com.shop", "action": "none"})
        check("rule important removed: back to dismiss with its own priority",
              row(ids["k2"])["bucket"] == k2["bucket"] == "dismiss" and row(ids["k2"])["priority"] == k2["priority"],
              (row(ids["k2"])["bucket"], row(ids["k2"])["priority"], k2["priority"]))
        r = await client.post(f"/api/undo/{(await r.json())['undo_id']}")
        check("rule important removed → undo: important again, needs_you again",
              row(ids["k2"])["bucket"] == "needs_you" and row(ids["k2"])["priority"] == "important")
        r = await client.post(f"/api/undo/{imp['undo_id']}")
        with get_db_connection() as conn:
            back = rules.rule_for(conn, OWNER, "com.shop", "")
        check("rule important → undo: no rule, back in dismiss", back is None and row(ids["k2"])["bucket"] == "dismiss"
              and row(ids["k2"])["priority"] == k2["priority"])
        # A sender rule only covers that sender; the app rule still applies to the rest.
        r = await client.post("/api/notifications/rules", json={"package": "com.whatsapp", "sender": "Gal", "action": "mute"})
        sm_ = await r.json()
        check("sender rule: label names the sender", sm_["message"] == "Gal on WhatsApp will stay quiet", sm_)
        await client.post(f"/api/undo/{sm_['undo_id']}")
        r = await client.post("/api/notifications/rules", json={"package": "com.news", "action": "loud"})
        check("rule: bad action → 400", r.status == 400)
        shade.set_status(OWNER, [ids["k4"]], "dismissed", reason="opened")  # as it was

        # --- retriage after fixing ---
        with get_db_connection() as conn:
            conn.execute("UPDATE notifications SET text = 'fixed' WHERE id = ?", (ids["k8"],))
            conn.commit()
        r = await client.post(f"/api/notifications/{ids['k8']}/triage")
        rt = await r.json()
        check("retriage: done", rt["notification"]["triage_status"] == "done", rt)

        # --- search + tools ---
        r = await client.get("/api/notifications?status=all&q=courier")
        found = await r.json()
        check("search: FTS finds the courier", [i["id"] for i in found["items"]] == [ids["k3"]], found)
        out = await registry.execute(OWNER, "search_notifications", {"query": "bread"})
        check("tool: search_notifications", "bread" in str(out), out)
        out = await registry.execute(OWNER, "list_notifications", {"bucket": "needs_you"})
        check("tool: list_notifications", "Wolt" in str(out), out)
        check("tool: dismiss is a write tool", registry.is_write_tool("dismiss_notifications"))

        # --- AI summaries (per app + briefing) ---
        ns.MIN_SECONDS = 0
        await client.post("/api/notifications/batch", json={"posted": [
            note("s1", "Deploy is blocked, can you approve?", title="Dana", package="com.slack", app="Slack", sender="Dana"),
            note("s2", "Lunch at 1?", title="Gal", package="com.slack", app="Slack", sender="Gal"),
            note("s3", "Your ride is here", title="Gett", package="com.single", app="Single")]})
        await settle()
        r = await client.get("/api/notifications/summaries")
        sm = await r.json()
        slack = sm["apps"].get("com.slack") or {}
        check("summaries: app with 2+ listed, stale before the first run, refresh started",
              slack.get("count") == 2 and slack.get("summary") is None and slack.get("stale") and sm["refreshing"], sm)
        check("summaries: single-notification app not summarized", "com.single" not in sm["apps"], sm["apps"].keys())
        await ns._tasks[OWNER.id]
        r = await client.get("/api/notifications/summaries")
        sm = await r.json()
        app_prompts = [p for f, p in seen["summary_prompts"] if f == "summary"]
        brief_prompts = [p for f, p in seen["summary_prompts"] if f == "overall"]
        slack_prompt = next(p for p in app_prompts if 'from the app "Slack"' in p)
        check("summaries: one call per app + the briefing, fresh",
              sm["apps"]["com.slack"]["summary"].startswith("sum Slack v") and not sm["apps"]["com.slack"]["stale"]
              and sm["overall"]["points"][0] == "briefing v1" and len(brief_prompts) == 1 and not sm["refreshing"], sm)
        check("summaries: an app's prompt holds only its own notifications",
              "Deploy is blocked" in slack_prompt and "Your ride is here" not in slack_prompt
              and "milk" not in slack_prompt, slack_prompt[-600:])
        check("summaries: briefing sees app summaries and single notifications",
              "sum Slack" in brief_prompts[0] and "Your ride is here" in brief_prompts[0], brief_prompts[0][-900:])
        calls = len(seen["summary_prompts"])
        r = await client.get("/api/notifications/summaries")
        check("summaries: an echoed needs-you tag never reaches the stored text or the API",
              "needs robin" not in json.dumps(sm, ensure_ascii=False).lower(), sm)
        with get_db_connection() as conn:
            stored = [r[0] for r in conn.execute("SELECT summary FROM notification_summaries")]
        check("summaries: stored text has no tag", stored and not any("needs Robin" in t for t in stored), stored)
        check("summaries: prompts carry no per-line tag",
              not any("[needs Robin]" in p for _, p in seen["summary_prompts"]), "")
        prompt = ns.app_prompt(OWNER, [
            {"id": "1", "package": "p", "app_name": "Chat", "sender": "Gal", "title": "Gal", "text": "Friday?",
             "bucket": "needs_you", "posted_at": "2026-11-14T10:00:00"},
            {"id": "2", "package": "p", "app_name": "Chat", "sender": "Noa", "title": "Noa", "text": "lol",
             "bucket": "normal", "posted_at": "2026-11-14T10:01:00"}])
        check("summaries: what needs Robin is a separate list, first",
              "Waiting for Robin:\n" in prompt and "Other notifications:\n" in prompt
              and prompt.index("Friday?") < prompt.index("lol") and "[needs Robin]" not in prompt, prompt[-700:])
        with get_db_connection() as conn:
            conn.execute("UPDATE notification_summaries SET summary = ? WHERE scope = 'com.slack'",
                         ("[needs Robin] Dana asks to approve the deploy\nGal asks about lunch",))
            conn.commit()
        legacy = (await (await client.get("/api/notifications/summaries")).json())["apps"]["com.slack"]
        check("summaries: an old cached text with the tag is cleaned when returned",
              legacy["points"] == ["Dana asks to approve the deploy", "Gal asks about lunch"]
              and "needs Robin" not in legacy["summary"], legacy)
        check("summaries: points cleaned, joined by newlines, listed in the API",
              sm["apps"]["com.slack"]["points"][1] == "second point" and sm["apps"]["com.slack"]["summary"].count("\n") == 1,
              sm["apps"]["com.slack"])
        check("summaries: in the user's first language (Robin: Hebrew, for English notifications too)",
              ns.language(OWNER) == "he" and "Write the points in natural, short Hebrew" in slack_prompt, slack_prompt[-600:])
        hebrew_items = [{"id": "h1", "package": "p", "app_name": "Chat", "sender": "גל", "title": "גל",
                         "text": "דחוף תתרגם לי את זה, PR", "bucket": "normal", "posted_at": "2026-11-14T10:00:00"}]
        german = users.create("Maya")
        german = users.update(german.id, languages="de, en")
        de_prompt = ns.app_prompt(german, hebrew_items)
        de_brief = ns.overall_prompt(german, {"p": hebrew_items}, {})
        check("summaries: a German-first user gets German, whatever the notifications' language",
              ns.language(german) == "de" and "short German, whatever language" in de_prompt
              and "short German" in de_brief and "English" not in de_prompt.split("Write the points")[1].split("\n")[0],
              de_prompt[-600:])
        check("summaries: a language change rewrites the cached summaries",
              ns._signature(hebrew_items, "de") != ns._signature(hebrew_items, "en")
              and ns._overall_signature({"p": hebrew_items}, "de") != ns._overall_signature({"p": hebrew_items}, "en"))
        check("summaries: unchanged inbox → no new call", len(seen["summary_prompts"]) == calls and not (await r.json())["refreshing"])
        prev_wa = (sm["apps"].get("com.whatsapp") or {}).get("summary")
        await client.post("/api/notifications/batch", json={"posted": [
            note("s4", "Never mind, Robin approved", title="Dana", package="com.slack", app="Slack", sender="Dana")]})
        await settle()
        await client.get("/api/notifications/summaries")
        await ns._tasks[OWNER.id]
        r = await client.get("/api/notifications/summaries")
        sm = await r.json()
        new = seen["summary_prompts"][calls:]
        check("summaries: only the changed app is rewritten (+ briefing)",
              [f for f, _ in new] == ["summary", "overall"] and 'from the app "Slack"' in new[0][1]
              and sm["apps"]["com.slack"]["count"] == 3 and sm["overall"]["points"][0] == "briefing v2"
              and (sm["apps"].get("com.whatsapp") or {}).get("summary") == prev_wa, [f for f, _ in new])
        slack_before = sm["apps"]["com.slack"]["summary"]
        seen["summary_fail"] = True
        await client.post("/api/notifications/batch", json={"posted": [
            note("s5", "One more thing", title="Gal", package="com.slack", app="Slack", sender="Gal")]})
        await settle()
        await client.get("/api/notifications/summaries")
        await ns._tasks[OWNER.id]
        r = await client.get("/api/notifications/summaries")
        sm = await r.json()
        check("summaries: model failure keeps the old text, marked stale, backs off",
              sm["apps"]["com.slack"]["stale"] and sm["apps"]["com.slack"]["summary"] == slack_before
              and not sm["refreshing"], sm["apps"]["com.slack"])
        seen["summary_fail"] = False
        ns._last_runs[OWNER.id] = 0
        out = await registry.execute(OWNER, "summarize_notifications", {})
        check("tool: summarize_notifications refreshes and lists apps", "Briefing" in str(out) and "Slack (4)" in str(out), out)

        # --- acting on a notification from the web (the phone carries it out) ---
        await client.post("/api/notifications/batch", json={"posted": [
            note("a1", "Are you coming tonight?", title="Noa", package="com.whatsapp", app="WhatsApp", sender="Noa",
                 actions=[{"title": "Reply", "reply": True}, {"title": "Mark as read"}, {"title": ""}, "junk"])]})
        await settle()
        r = await client.get("/api/notifications?q=coming")
        a1 = (await r.json())["items"][0]
        check("act: buttons stored and listed, on_phone while in the shade",
              a1["actions"] == [{"title": "Reply", "reply": True}, {"title": "Mark as read", "reply": False}]
              and a1["on_phone"], a1)
        push_before = len(seen["push"])
        r = await client.post(f"/api/notifications/{a1['id']}/act", json={"action": "mark as read"})
        req = await r.json()
        bc = seen["push"][-1] if len(seen["push"]) > push_before else {}
        check("act: 202, pending, the push carries only the request id",
              r.status == 202 and req["status"] == "pending" and req["action"] == "Mark as read"
              and bc.get("type") == "notification_action" and bc["data"] == {"request_id": req["id"]}, (req, bc))
        r = await client.get(f"/api/notifications/requests/{req['id']}")
        got = await r.json()
        check("act: the phone fetches key + action", got["key"] == "a1" and got["action"] == "Mark as read", got)
        r = await client.post(f"/api/notifications/requests/{req['id']}/result", json={"ok": True})
        check("act: result → done, notification seen", (await r.json())["status"] == "done"
              and row(a1["id"])["seen_at"], row(a1["id"])["seen_at"])
        r = await client.post(f"/api/notifications/requests/{req['id']}/result", json={"ok": False, "error": "late"})
        check("act: a second answer doesn't change it", (await r.json())["status"] == "done")
        r = await client.post(f"/api/notifications/{a1['id']}/act", json={"action": "Reply"})
        check("act: reply without text → 400", r.status == 400)
        r = await client.post(f"/api/notifications/{a1['id']}/act", json={"action": "Reply", "text": "  On my way  "})
        rr = await r.json()
        check("act: reply carries the text", r.status == 202 and rr["text"] == "On my way", rr)
        r = await client.post(f"/api/notifications/{a1['id']}/act", json={"action": "Delete everything"})
        check("act: unknown button → 400", r.status == 400)
        r = await client.post(f"/api/notifications/{a1['id']}/act", json={"action": "open"})
        op = await r.json()
        with get_db_connection() as conn:
            conn.execute("UPDATE notification_action_requests SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (op["id"],))
            conn.commit()
        r = await client.get(f"/api/notifications/requests/{op['id']}")
        check("act: open works; unanswered requests expire", op["action"] == "open" and (await r.json())["status"] == "expired")
        await client.post("/api/notifications/batch", json={"removed": [{"key": "a1", "reason": "cancel"}]})
        r = await client.post(f"/api/notifications/{a1['id']}/act", json={"action": "open"})
        check("act: gone from the shade → 400", r.status == 400)
        r = await client.post("/api/notifications/nope/act", json={"action": "open"})
        check("act: unknown notification → 404", r.status == 404)

        # --- digest ---
        r = await client.get("/api/notifications/digest?hours=2")
        dg = await r.json()
        check("digest: text with counts", dg.get("total", 0) >= 6 and "need you" in dg["text"], dg)
        await digest.send_digest(OWNER, (clock.now() - datetime.timedelta(hours=12)).isoformat())
        dg_push = pushes("notify")
        check("push: digest → notify event on the digest channel",
              dg_push and dg_push[-1]["data"]["channel"] == "digest" and dg_push[-1]["data"]["tag"] == "dana_digest", dg_push)

        # --- bulk suggestion dismiss ---
        r = await client.get("/api/suggestions?source=notification")
        pend = await r.json()
        r = await client.post("/api/suggestions/bulk", json={"ids": [s["id"] for s in pend], "action": "dismiss"})
        bd = await r.json()
        check("suggestions: bulk dismiss", bd["dismissed"] == len(pend), bd)

        # --- retention ---
        old = (clock.now() - datetime.timedelta(days=45)).isoformat(timespec="seconds")
        with get_db_connection() as conn:
            conn.execute("UPDATE notifications SET posted_at = ?", (old,))
            conn.commit()
        removed = retention.purge_old(OWNER, 30)
        with get_db_connection() as conn:
            left = [r["id"] for r in conn.execute("SELECT id FROM notifications")]
            fts_left = conn.execute("SELECT COUNT(*) FROM notifications_fts").fetchone()[0]
        check("retention: old deleted, task-linked one kept", left == [ids["k1"]] and removed >= 7, (left, removed))
        check("retention: FTS rows follow", fts_left == 1, fts_left)

        # --- forget an app ---
        r = await client.delete("/api/notifications?package=com.whatsapp")
        check("delete app: task-linked notification survives", (await r.json())["deleted"] == 0)

        # --- direct push: catch-up, expiry, digest, dead tokens ---
        r = await client.get("/api/phone/events?after=0&device_id=dev-1")
        evs = await r.json()
        types = [e["type"] for e in evs["events"]]
        check("push: catch-up returns queued events in order",
              "alert" in types and "suggestions" in types and [e["seq"] for e in evs["events"]] == sorted(e["seq"] for e in evs["events"])
              and evs["latest"] == evs["events"][-1]["seq"], evs)
        dev = phone_push.devices(OWNER)[0]
        check("push: fetch recorded on the device", dev["last_seq"] == evs["latest"] and dev["last_fetch_at"], dev)
        check("push: known device → registered", evs.get("registered") is True, evs.get("registered"))
        r = await client.get("/api/phone/events?after=0&device_id=forgotten")
        check("push: unknown device → registered false (phone re-registers)", (await r.json()).get("registered") is False)
        r = await client.get(f"/api/phone/events?after={evs['latest']}")
        check("push: nothing newer → empty", (await r.json())["events"] == [])
        with get_db_connection() as conn:
            conn.execute("UPDATE phone_events SET expires_at = '2000-01-01T00:00:00+00:00' WHERE type = 'alert'")
            conn.commit()
        r = await client.get("/api/phone/events?after=0")
        check("push: expired alerts aren't replayed", all(e["type"] != "alert" for e in (await r.json())["events"]))
        r = await client.get("/api/phone/devices")
        dv = await r.json()
        check("push: devices list hides tokens", dv["configured"] and "token" not in json.dumps(dv["devices"]), dv)

        real_post = httpx.AsyncClient.post

        async def gone_post(self, url, *a, **k):
            if "fcm.googleapis.com" in url:
                return FakeResponse({"error": {"code": 404, "status": "NOT_FOUND", "message": "Requested entity was not found.",
                                               "details": [{"errorCode": "UNREGISTERED"}]}}, status=404)
            return await real_post(self, url, *a, **k)
        httpx.AsyncClient.post = gone_post
        try:
            ok = await phone_push.notify(OWNER, "x", "y")
        finally:
            httpx.AsyncClient.post = real_post
        check("push: unregistered token → device removed, not delivered", not ok and phone_push.devices(OWNER) == [])
        check("push: no device → queued but not delivered", not await phone_push.notify(OWNER, "x", "y")
              and phone_push.latest_seq(OWNER) > evs["latest"])
        phone_push.register(OWNER, "old-phone", "tok-old")
        with get_db_connection() as conn:
            conn.execute("UPDATE phone_devices SET updated_at = '2000-01-01T00:00:00+00:00' WHERE device_id = 'old-phone'")
            conn.commit()
        phone_push.register(OWNER, "new-phone", "tok-new")
        await phone_push.notify(OWNER, "x", "y")
        check("push: a device silent for 60 days is dropped", [d["device_id"] for d in phone_push.devices(OWNER)] == ["new-phone"],
              phone_push.devices(OWNER))

    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
