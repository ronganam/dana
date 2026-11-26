"""Phone events for the catalog (addons/phone/event_types.py): phone.notification.received / phone.email.received per newly ingested notification (deduped, email apps apart, the first shekel amount), phone.call.ended's data, the history of each per user, and the shekel amount parser.

Two users with ``phone`` (Ann, Bob) and one without (Carl). Notifications go
through the real ingest (in a worker thread, as the API runs it); a tap
collects what is emitted. Every name, message and amount is made up.

Run: bin/test test_phone_events
"""

import asyncio
import datetime
import sys
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("phone_events_test")

from core import clock, db, events, users  # noqa: E402
from core.addons import registry, store  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.users import User  # noqa: E402
from addons.phone import event_types  # noqa: E402
from addons.phone.notifications import ingest  # noqa: E402

db.migrate()
users.bootstrap()

results: List[bool] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


ANN = users.create("Ann")
BOB = users.create("Bob")
CARL = users.create("Carl")
for u in (ANN, BOB):
    if not store.is_connected(u, "phone"):
        store.connect(u, "phone")
if store.is_connected(CARL, "phone"):
    store.disconnect(CARL, "phone")

SEEN: List[Tuple[str, str, str, Dict[str, Any]]] = []  # (event, user id, acting user id, data)


async def collect(name: str, user: User, data: Dict[str, Any]) -> None:
    if name.startswith("phone."):
        SEEN.append((name, user.id, users.current().id, data))


def test_amounts() -> None:
    cases = {"Paid ₪120 at the bakery": 120.0, "חויב 1,250.50 ש״ח": 1250.5, 'סך 35 ש"ח': 35.0,
             "NIS 40 sent": 40.0, "99.90 ILS": 99.9, "2 items for 30₪": 30.0, "50NIS": 50.0,
             "Order 1234 shipped": None, "Nisan 40": None, "": None, "₪ 7 then ₪ 9": 7.0}
    got = {text: event_types.ils_amount(text) for text in cases}
    check("amount: the first shekel amount, symbol or word before or after the number", got == cases, got)


async def test_notifications() -> None:
    SEEN.clear()
    now_ms = int(clock.now().timestamp() * 1000)
    ann_posted = [
        {"key": "k1", "package": "com.whatsapp", "app_name": "WhatsApp", "title": "Dana Example",
         "text": "Sent you ₪250 for the tickets", "sender": "Dana Example", "conversation": "Family",
         "is_group": True, "posted_at": now_ms},
        {"key": "k2", "package": "com.google.android.gm", "app_name": "Gmail", "title": "Shop Example",
         "big_text": "Your order total\nThe total is 89.90 ILS, delivered tomorrow", "posted_at": now_ms},
        {"key": "k3", "package": "com.microsoft.office.outlook", "app_name": "Outlook", "title": "Team Example",
         "text": "Planning meeting", "posted_at": now_ms},
    ]
    result = await asyncio.to_thread(ingest.ingest, ANN, ann_posted)
    await asyncio.to_thread(ingest.ingest, BOB, [{"key": "b1", "package": "com.example.bank", "app_name": "Bank",
                                                  "title": "Card charge", "text": "A charge of 12 NIS",
                                                  "posted_at": now_ms}])
    await events.drain()
    ann = [(n, d) for n, u, acting, d in SEEN if u == ANN.id and acting == ANN.id]
    names = sorted(n for n, _ in ann)
    check("notifications: one event per new notification, as its user (emails apart)",
          names == ["phone.email.received", "phone.email.received", "phone.notification.received"]
          and len(result["ids"]) == 3, (names, result))
    chat = next(d for n, d in ann if n == "phone.notification.received")
    check("notification: the contract's data",
          chat == {"notification_id": chat["notification_id"], "app": "WhatsApp", "sender": "Dana Example",
                   "conversation": "Family", "is_group": True, "title": "Dana Example",
                   "text": "Sent you ₪250 for the tickets", "category": "", "priority": None, "amount": 250.0}
          and chat["notification_id"] in result["ids"], chat)
    mails = {d["account"]: d for n, d in ann if n == "phone.email.received"}
    check("email: account, from, subject, the rest as text and the amount",
          mails.get("Gmail") == {"notification_id": mails["Gmail"]["notification_id"], "account": "Gmail",
                                 "from": "Shop Example", "subject": "Your order total",
                                 "text": "The total is 89.90 ILS, delivered tomorrow", "amount": 89.9}
          and mails.get("Outlook", {}).get("subject") == "Planning meeting"
          and mails["Outlook"]["text"] == "" and mails["Outlook"]["amount"] is None, mails)
    bob = [(n, d["amount"]) for n, u, _, d in SEEN if u == BOB.id]
    check("notifications: Bob's own event, only his", bob == [("phone.notification.received", 12.0)], bob)

    SEEN.clear()
    again = await asyncio.to_thread(ingest.ingest, ANN, ann_posted)
    await events.drain()
    check("notifications: the same content again is not new, nothing emitted",
          again["duplicates"] == 3 and not SEEN, (again, SEEN))

    since = clock.now() - datetime.timedelta(hours=1)
    ann_hist = event_types.notification_history(ANN, since)
    bob_hist = event_types.notification_history(BOB, since)
    mail_hist = event_types.email_history(ANN, since)
    check("history: Ann's notifications only, emitted shape, aware times",
          [d for _, d in ann_hist] == [chat] and all(t.tzinfo for t, _ in ann_hist), ann_hist)
    check("history: Bob's own, not Ann's", [d["title"] for _, d in bob_hist] == ["Card charge"], bob_hist)
    check("history: Ann's emails apart from her notifications",
          sorted(d["account"] for _, d in mail_hist) == ["Gmail", "Outlook"]
          and not event_types.email_history(BOB, since), mail_hist)
    check("history: nothing before since", not event_types.notification_history(ANN, clock.now()
                                                                                  + datetime.timedelta(minutes=5)))

    # Once triaged, history carries the category and the priority as 0-4.
    with get_db_connection() as conn:
        conn.execute("UPDATE notifications SET category = 'money', priority = 'important' WHERE id = ? "
                     "AND user_id = ?", (chat["notification_id"], ANN.id))
        conn.commit()
    triaged = event_types.notification_history(ANN, since)[0][1]
    check("history: a triaged notification's category and priority (3 = important)",
          (triaged["category"], triaged["priority"]) == ("money", 3), triaged)


def _call(user: User, call_id: str, caller: str, receiver: str, direction: Any, when: datetime.datetime) -> None:
    with get_db_connection() as conn:
        conn.execute("INSERT INTO phone_calls (id, user_id, filename, caller, receiver, call_timestamp, title, summary, "
                     "duration_seconds, status, analysis, direction) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', '{}', ?)",
                     (call_id, user.id, f"{call_id}.m4a", caller, receiver,
                      when.replace(tzinfo=None).isoformat(timespec="seconds"), "Plumber visit", "- Comes on Monday",
                      125, direction))
        conn.commit()


async def test_calls() -> None:
    SEEN.clear()
    now = clock.now()
    _call(ANN, "call-ann-1", "Plumber Example", "Me", "incoming", now - datetime.timedelta(minutes=30))
    _call(ANN, "call-ann-2", "Me", "Shop Example", None, now - datetime.timedelta(minutes=10))
    _call(BOB, "call-bob-1", "Friend Example", "Me", None, now - datetime.timedelta(minutes=20))
    ok = event_types.announce_call(ANN, "call-ann-1")
    other = event_types.announce_call(ANN, "call-bob-1")
    await events.drain()
    got = [(n, u, d) for n, u, _, d in SEEN]
    check("call: phone.call.ended with the contract's data",
          ok and got == [("phone.call.ended", ANN.id, {"call_id": "call-ann-1", "person": "Plumber Example",
                                                       "direction": "incoming", "duration_s": 125,
                                                       "title": "Plumber visit", "summary": "- Comes on Monday"})], got)
    check("call: another user's call is not announced", other is False)
    since = now - datetime.timedelta(hours=1)
    hist = event_types.call_history(ANN, since)
    check("call history: Ann's calls oldest first; an outgoing one (caller Me) names the receiver",
          [(d["call_id"], d["person"], d["direction"]) for _, d in hist]
          == [("call-ann-1", "Plumber Example", "incoming"), ("call-ann-2", "Shop Example", "outgoing")], hist)
    check("call history: Bob's own only", [d["call_id"] for _, d in event_types.call_history(BOB, since)]
          == ["call-bob-1"])


def test_catalog() -> None:
    names = {e.name for e in registry.event_types(ANN)}
    carl = {e.name for e in registry.event_types(CARL)}
    phone = {"phone.notification.received", "phone.email.received", "phone.call.ended"}
    check("catalog: the phone's events for a user with phone, with history", phone <= names
          and all(e.history is not None for e in registry.event_types(ANN) if e.name in phone), names)
    check("catalog: none for a user without phone (core's kept)", not (phone & carl) and "core.user.woke" in carl,
          carl)


async def main() -> None:
    events.tap(collect)
    try:
        test_amounts()
        await test_notifications()
        await test_calls()
        test_catalog()
    finally:
        events.untap(collect)


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} passed")
if not all(results):
    sys.exit(1)
