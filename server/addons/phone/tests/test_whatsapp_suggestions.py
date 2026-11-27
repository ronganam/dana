"""WhatsApp suggestions through WAHA (addons/phone/notifications/whatsapp.py): a chat's burst of messages becomes one hidden notification row (with quoted text, transcripts and the chat's history from WAHA) that is triaged and suggested from; the phone's WhatsApp notifications then make no suggestions, and are the only ones escalated; users without WAHA keep the phone path; isolation.

Ann has phone + WhatsApp (fake WAHA), Bob has phone only, Carl has WhatsApp
only. The judge and the extraction model are fakes. Every name, number and
message is made up.

Run: bin/test test_whatsapp_suggestions
"""

import asyncio
import json
import os
import sys
from typing import Any, Dict, List
from unittest.mock import patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("whatsapp_suggestions_test")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials
os.environ["WAHA_WEBHOOK_SECRET"] = "test-secret"
os.environ["WHATSAPP_SUGGEST_QUIET_SECONDS"] = "0.3"

from core import db, events, users  # noqa: E402
from core.addons import store  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from addons.phone.notifications import ingest, judges, processing, store as notif_store  # noqa: E402
from addons.phone.notifications import whatsapp as notif_wa  # noqa: E402
from addons.whatsapp import client as wa_client, events as wa_events  # noqa: E402
from testfakes import FakeWAHA  # noqa: E402

db.migrate()
users.bootstrap()

results: List[bool] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


A_ACCOUNT, C_ACCOUNT = "972500000201@c.us", "972500000203@c.us"
NOA = "972500000211@c.us"
WA_A = FakeWAHA("A", "key-a", {"default": {"id": A_ACCOUNT, "pushName": "Ann"}}, (NOA, "Noa Example"), "Noa chat")
WA_C = FakeWAHA("C", "key-c", {"default": {"id": C_ACCOUNT, "pushName": "Carl"}}, (NOA, "Noa Example"), "Noa chat")

ANN = users.create("Ann")
BOB = users.create("Bob")
CARL = users.create("Carl")

ACTION = {"category": {"choice": "personal_message"}, "priority": {"score": 3.0},
          "needs_action": {"noul": 0.95}, "from_person": {"noul": 0.95}, "time_sensitive": {"noul": 0.2},
          "urgent_request": {"noul": 0.1}, "promotional": {"noul": 0.02}, "owner_wrote_it": {"noul": 0.02},
          "addressed_to_owner": {"noul": 0.95}, "tracked_elsewhere": {"noul": 0.05},
          "record_only": {"noul": 0.05}, "self_clearing": {"noul": 0.05}}
URGENT = {**ACTION, "urgent_request": {"noul": 0.95}}


class FakeJudge:
    name, batch_size = "fake", 10
    answers: Dict[str, Any] = ACTION

    def model(self) -> str:
        return "fake-judge"

    async def judge(self, who, notes):
        return {n["id"]: self.answers for n in notes}


PROMPTS: List[str] = []


async def fake_complete_json(prompt: str, schema: Dict[str, Any], **kw: Any) -> Dict[str, Any]:
    PROMPTS.append(prompt)
    return {"action_items": [{"text": "Bring Noa the blue folder on Sunday", "owner": "me", "kind": "task",
                              "priority": "medium", "confidence": 0.9, "evidence": "bring the blue folder"}]}


def body(**payload: Any) -> Dict[str, Any]:
    return {"event": "message", "session": "default", "engine": "NOWEB", "me": {"id": A_ACCOUNT, "pushName": "Ann"},
            "payload": {"timestamp": 1792271419, "fromMe": False, "hasMedia": False, "from": NOA,
                        "_data": {"pushName": "Noa Example"}, **payload}}


def rows(user, source: str) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM notifications WHERE user_id = ? AND source = ? "
                                              "ORDER BY created_at", (user.id, source)).fetchall()]


def suggestions_of(user, nid: str) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM call_suggestions WHERE user_id = ? AND call_id = ? "
                                              "AND source_type = 'notification'", (user.id, nid)).fetchall()]


async def settle() -> None:
    for _ in range(3):
        await events.drain()
        await asyncio.sleep(0.05)
        while processing._tasks:
            await asyncio.gather(*list(processing._tasks), return_exceptions=True)


def phone_whatsapp(user, text: str) -> str:
    out = ingest.ingest(user, [{"key": f"0|com.whatsapp|{text[:8]}", "package": "com.whatsapp", "app_name": "WhatsApp",
                                "title": "Noa Example", "text": text, "sender": "Noa Example",
                                "posted_at": int(__import__("time").time() * 1000)}])
    processing.schedule(user, out["ids"])
    return out["ids"][0]


async def test_covered() -> None:
    check("covered: Ann (WhatsApp with its account, secret set)", notif_wa.covered(ANN))
    check("covered: not Bob (no WhatsApp)", not notif_wa.covered(BOB))
    os.environ["WAHA_WEBHOOK_SECRET"] = ""
    check("covered: not without WAHA_WEBHOOK_SECRET (no events can arrive)", not notif_wa.covered(ANN))
    os.environ["WAHA_WEBHOOK_SECRET"] = "test-secret"
    store.needs_reconnect(ANN, "whatsapp", "signed out")
    check("covered: not while WhatsApp waits to be reconnected", not notif_wa.covered(ANN))
    store.reconnected(ANN, "whatsapp")
    check("covered: again once reconnected", notif_wa.covered(ANN))


async def test_burst() -> None:
    PROMPTS.clear()
    await wa_events.handle_webhook(body(id=f"false_{NOA}_B1", body="Hi!"))
    await wa_events.handle_webhook(body(id=f"false_{NOA}_B2", body="Can you bring the blue folder on Sunday?",
                                        replyTo={"id": "q", "body": "What do you need from me?"}))
    await wa_events.handle_webhook(body(id=f"true_{NOA}_B3", fromMe=True, to=NOA, **{"from": A_ACCOUNT},
                                        body="sent from my laptop"))
    await asyncio.sleep(0.5)
    await settle()
    got = rows(ANN, "whatsapp")
    check("burst: two incoming messages in one chat → one WhatsApp row (the user's own message not one)",
          len(got) == 1, got)
    r = got[0] if got else {}
    text = r.get("text") or ""
    check("burst row: hidden from the Inbox (archived, removed_reason whatsapp), WhatsApp's package",
          (r.get("status"), r.get("removed_reason"), r.get("package"), r.get("device_key"))
          == ("archived", "whatsapp", "com.whatsapp", None), r)
    check("burst row: both messages, in order, with the quoted text",
          text.index("Hi!") < text.index("Can you bring the blue folder")
          and 'replying to "What do you need from me?"' in text, text)
    check("burst row: the chat's earlier messages from the user's WAHA as context",
          "Earlier in this chat" in text and "text from A" in text, text)
    check("burst row: the context read from Ann's WAHA with her key only",
          any(c[2] == "key-a" and "/messages" in c[1] for c in WA_A.calls())
          and not any("/messages" in c[1] for c in WA_C.calls()))
    check("burst row: triaged, suggestions extracted (action_status done), never escalated",
          (r.get("triage_status"), r.get("escalated_at")) == ("done", None)
          and rows(ANN, "whatsapp")[0]["action_status"] == "done", rows(ANN, "whatsapp"))
    sug = suggestions_of(ANN, r.get("id"))
    check("burst row: its suggestion is Ann's, an ordinary notification suggestion",
          len(sug) == 1 and json.loads(sug[0]["payload"])["title"].startswith("Bring Noa"), sug)
    check("extraction prompt: says it is the chat itself (new messages first, earlier for context)",
          PROMPTS and "read from the chat itself" in PROMPTS[-1] and "Earlier in this chat" in PROMPTS[-1],
          PROMPTS[-1:] if PROMPTS else None)
    listed = notif_store.list_notifications(ANN, status="all")
    check("Inbox: the WhatsApp row is not listed, even under Everything",
          all(n["id"] != r.get("id") for n in listed), [n["id"] for n in listed])

    await wa_events.handle_webhook(body(id=f"false_{NOA}_B2", body="Can you bring the blue folder on Sunday?"))
    await asyncio.sleep(0.5)
    await settle()
    check("redelivery: no second row", len(rows(ANN, "whatsapp")) == 1, rows(ANN, "whatsapp"))


async def test_phone_path() -> None:
    PROMPTS.clear()
    nid = phone_whatsapp(ANN, "Can you bring the blue folder on Sunday?")
    await settle()
    with get_db_connection() as conn:
        r = dict(conn.execute("SELECT * FROM notifications WHERE id = ?", (nid,)).fetchone())
    check("Ann's phone WhatsApp notification: triaged and in the Inbox, but no suggestion (WAHA makes it)",
          r["triage_status"] == "done" and r["status"] == "active" and r["action_status"] == "none"
          and not suggestions_of(ANN, nid) and not PROMPTS, (r["action_status"], len(PROMPTS)))
    check("its triage records why", json.loads(r["triage"]).get("suggested_from") == "waha", r["triage"])

    nid = phone_whatsapp(BOB, "Can you bring the red folder on Monday?")
    await settle()
    check("Bob (no WAHA): his phone's WhatsApp notification still makes the suggestion",
          len(suggestions_of(BOB, nid)) == 1 and len(PROMPTS) == 1, suggestions_of(BOB, nid))

    FakeJudge.answers = URGENT
    escalated: List[str] = []

    async def fake_escalate(user, n):
        escalated.append(n.get("source") or "phone")

    with patch.object(processing.escalation, "escalate", fake_escalate):
        phone_whatsapp(ANN, "Call me now please!!")
        await wa_events.handle_webhook(body(id=f"false_{NOA}_U1", body="Call me now please!!"))
        await asyncio.sleep(0.5)
        await settle()
    FakeJudge.answers = ACTION
    check("urgent: only the phone's notification is announced, never the WAHA row", escalated == ["phone"], escalated)


async def test_isolation() -> None:
    before = len(rows(CARL, "whatsapp"))
    carl_body = body(id=f"false_{NOA}_C1", body="Please send me the invoice")
    carl_body["me"] = {"id": C_ACCOUNT, "pushName": "Carl"}
    await wa_events.handle_webhook(carl_body)
    await asyncio.sleep(0.5)
    await settle()
    check("Carl (WhatsApp, no phone addon): nothing stored", len(rows(CARL, "whatsapp")) == before == 0)
    check("Bob: no WhatsApp rows", rows(BOB, "whatsapp") == [])


async def main() -> None:
    await WA_A.start()
    await WA_C.start()
    store.connect(ANN, "phone")
    store.connect(BOB, "phone")
    store.connect(ANN, "whatsapp", credentials={"url": WA_A.url, "api_key": "key-a", "session": "default"},
                  account=A_ACCOUNT)
    store.connect(CARL, "whatsapp", credentials={"url": WA_C.url, "api_key": "key-c", "session": "default"},
                  account=C_ACCOUNT)
    for u in (BOB,):
        if store.is_connected(u, "whatsapp"):
            store.disconnect(u, "whatsapp")
    if store.is_connected(CARL, "phone"):
        store.disconnect(CARL, "phone")
    wa_client._name_maps.clear()
    WA_A.calls(), WA_C.calls()
    try:
        with patch.object(judges, "judge", lambda: FakeJudge()), \
                patch.object(processing.llm, "complete_json", fake_complete_json):
            await test_covered()
            await test_burst()
            await test_phone_path()
            await test_isolation()
    finally:
        await WA_A.server.close()
        await WA_C.server.close()


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
if not all(results):
    sys.exit(1)
