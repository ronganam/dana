"""Gmail events for the catalog (addons/gmail/event_types.py): gmail.email.received from Gmail's history —
the first poll only takes a baseline, a new message gives one event with the contract's data (the real
sender and recipients), a repeat poll emits nothing and asks Gmail once, an expired historyId starts over,
spam / drafts / sent-only mail skipped, a failing poll logged once and never raising, two users isolated,
history from what was emitted, the prune, the event in the catalog.

Google's client is faked where it is built (``addons.gmail.client.service``):
each refresh token opens its own fake mailbox (Ann's, Bob's); Carl has no
Gmail. A tap collects what is emitted. Every name and address is made up. No network.

Run: bin/test test_gmail_events
"""

import asyncio
import datetime
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("gmail_events_test")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials
os.environ["TIMEZONE"] = "Asia/Jerusalem"

from core import clock, db, events, users  # noqa: E402
from core.addons import registry, store  # noqa: E402
from core.addons.addon import get_addon  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.users import User  # noqa: E402
from addons.gmail import client as gmail_client, event_types  # noqa: E402

db.migrate()
users.bootstrap()

results: List[bool] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


# --- A fake Gmail, one mailbox per refresh token -----------------------------------------------------

class HttpFail(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.resp = type("Resp", (), {"status": status})()


class Mailbox:
    def __init__(self, address: str) -> None:
        self.address = address
        self.history_id = 100
        self.messages: Dict[str, Dict[str, Any]] = {}
        self.history: List[Tuple[int, str]] = []  # (history id, message id) of messageAdded records
        self.expired_before = 0  # a startHistoryId below this answers 404
        self.down = False
        self.calls: List[str] = []

    def add(self, mid: str, labels: List[str], frm: str, to: str, subject: str, snippet: str = "",
            cc: str = "") -> None:
        self.history_id += 1
        headers = [{"name": "From", "value": frm}, {"name": "To", "value": to}, {"name": "Subject", "value": subject}]
        if cc:
            headers.append({"name": "Cc", "value": cc})
        self.messages[mid] = {"id": mid, "threadId": f"t-{mid}", "labelIds": labels, "snippet": snippet,
                              "payload": {"headers": headers}}
        if "INBOX" in labels:  # as labelId=INBOX filters
            self.history.append((self.history_id, mid))


BOXES = {"ann-refresh": Mailbox("ann@example.com"), "bob-refresh": Mailbox("bob@example.com")}
ANN_BOX, BOB_BOX = BOXES["ann-refresh"], BOXES["bob-refresh"]


class Call:
    def __init__(self, fn) -> None:
        self.fn = fn

    def execute(self) -> Any:
        return self.fn()


class FakeBatch:
    def __init__(self, callback) -> None:
        self.callback, self.items = callback, []

    def add(self, request: Call, request_id: str) -> None:
        self.items.append((request_id, request))

    def execute(self) -> None:
        for key, request in self.items:
            try:
                self.callback(key, request.execute(), None)
            except Exception as e:
                self.callback(key, None, e)


class FakeService:
    def __init__(self, box: Mailbox) -> None:
        self.box = box

    def users(self) -> "FakeService":
        return self

    def history(self) -> "FakeService":
        return self

    def messages(self) -> "FakeService":
        return self

    def new_batch_http_request(self, callback) -> FakeBatch:
        return FakeBatch(callback)

    def _guard(self, what: str) -> None:
        self.box.calls.append(what)
        if self.box.down:
            raise ConnectionError("network down")

    def getProfile(self, userId: str) -> Call:
        def run():
            self._guard("profile")
            return {"emailAddress": self.box.address, "historyId": str(self.box.history_id)}
        return Call(run)

    def list(self, userId: str, startHistoryId: str, historyTypes: Any, labelId: str, maxResults: int,
             pageToken: Optional[str] = None) -> Call:
        def run():
            self._guard("history")
            start = int(startHistoryId)
            if start < self.box.expired_before:
                raise HttpFail(404)
            records = [{"id": str(h), "messagesAdded": [{"message": {"id": mid, "threadId": f"t-{mid}"}}]}
                       for h, mid in self.box.history if h > start]
            return {"history": records, "historyId": str(self.box.history_id)} if records \
                else {"historyId": str(self.box.history_id)}
        return Call(run)

    def get(self, userId: str, id: str, format: str, metadataHeaders: List[str]) -> Call:
        def run():
            self._guard("get")
            if id not in self.box.messages:
                raise HttpFail(404)
            return dict(self.box.messages[id])
        return Call(run)


gmail_client.service = lambda conn: FakeService(BOXES[conn.credentials["refresh_token"]])

ANN = users.create("Ann")
BOB = users.create("Bob")
CARL = users.create("Carl")
store.connect(ANN, "gmail", credentials={"refresh_token": "ann-refresh"}, account="ann@example.com")
store.connect(BOB, "gmail", credentials={"refresh_token": "bob-refresh"})
if store.is_connected(CARL, "gmail"):
    store.disconnect(CARL, "gmail")

SEEN: List[Tuple[str, str, str, Dict[str, Any]]] = []  # (event, user id, acting user id, data)


async def collect(name: str, user: User, data: Dict[str, Any]) -> None:
    if name.startswith("gmail."):
        SEEN.append((name, user.id, users.current().id, data))


async def run(user: User) -> Tuple[int, List[Tuple[str, str, str, Dict[str, Any]]]]:
    SEEN.clear()
    n = await event_types.poll(user)
    await events.drain()
    return n, list(SEEN)


class Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


async def test_poll() -> None:
    ANN_BOX.add("old1", ["INBOX", "UNREAD"], "Old Sender <old@example.com>", "ann@example.com", "Before Dana")
    n, got = await run(ANN)
    check("first poll: a baseline, nothing emitted, the profile asked", n == 0 and got == []
          and ANN_BOX.calls == ["profile"], (got, ANN_BOX.calls))
    check("first poll: the historyId and address stored", event_types._state(ANN) == ("101", "ann@example.com"),
          event_types._state(ANN))

    ANN_BOX.calls.clear()
    n, got = await run(ANN)
    check("nothing new: one history request, nothing emitted", n == 0 and got == [] and ANN_BOX.calls == ["history"],
          ANN_BOX.calls)

    ANN_BOX.add("m1", ["INBOX", "UNREAD", "SENT", "CATEGORY_PERSONAL"], "Ann Tester <Ann@Example.com>",
                "ann+notes@example.com", "Note to self", "Remember the &quot;deck&quot; and pay ₪120")
    ANN_BOX.add("m2", ["INBOX", "UNREAD"], "=?utf-8?q?Shop_Example?= <billing@shop.example.com>",
                "Dana Tester <tester@example.com>", "Your receipt", "Total 45.90 ILS", cc="ann@example.com")
    ANN_BOX.add("spam1", ["INBOX", "SPAM"], "x@spam.example.com", "ann@example.com", "Win")
    ANN_BOX.add("draft1", ["INBOX", "DRAFT"], "ann@example.com", "x@example.com", "Draft")
    ANN_BOX.add("sent1", ["SENT"], "ann@example.com", "friend@example.com", "Sent only")
    ANN_BOX.calls.clear()
    n, got = await run(ANN)
    check("two new messages: two events, as Ann (spam, draft and sent-only skipped)",
          n == 2 and [(e, u, a, d["message_id"]) for e, u, a, d in got]
          == [("gmail.email.received", ANN.id, ANN.id, "m1"), ("gmail.email.received", ANN.id, ANN.id, "m2")], got)
    check("a poll with news: one history request and one batch of gets",
          ANN_BOX.calls == ["history", "get", "get", "get", "get"], ANN_BOX.calls)
    d1 = got[0][3] if got else {}
    check("mail to oneself: the contract's data, the real sender and recipient",
          d1 == {"message_id": "m1", "thread_id": "t-m1", "from": "Ann Tester <Ann@Example.com>",
                 "from_name": "Ann Tester", "from_email": "ann@example.com", "to": "ann+notes@example.com", "cc": "",
                 "subject": "Note to self", "snippet": 'Remember the "deck" and pay ₪120',
                 "labels": "INBOX, UNREAD, SENT, CATEGORY_PERSONAL", "amount": 120.0}, d1)
    d2 = got[1][3] if len(got) > 1 else {}
    check("someone else's mail with the user in Cc: decoded name, the recipients, the amount",
          (d2.get("from"), d2.get("from_email"), d2.get("to"), d2.get("cc"),
           d2.get("amount")) == ("Shop Example <billing@shop.example.com>", "billing@shop.example.com",
                                 "Dana Tester <tester@example.com>", "ann@example.com", 45.9), d2)
    check("no derived self flags: only the real data", not {"from_me", "to_me"} & set(d1), sorted(d1))
    check("the data keys are exactly the contract's", set(d1) == set(event_types.GMAIL_EVENTS[0].fields), sorted(d1))

    n, got = await run(ANN)
    check("a repeat poll: nothing emitted", n == 0 and got == [], got)
    event_types._save_state(ANN, "101", "ann@example.com")  # as if the historyId was never saved (a crash)
    n, got = await run(ANN)
    check("the same messages again from an older historyId: not announced twice", n == 0 and got == [], got)

    ANN_BOX.expired_before = 10_000
    ANN_BOX.add("m3", ["INBOX"], "Someone <someone@example.com>", "ann@example.com", "Lost in the gap")
    ANN_BOX.calls.clear()
    n, got = await run(ANN)
    check("expired historyId: starts over from the profile, nothing emitted",
          n == 0 and got == [] and ANN_BOX.calls == ["history", "profile"]
          and event_types._state(ANN)[0] == str(ANN_BOX.history_id), (got, ANN_BOX.calls))
    ANN_BOX.expired_before = 0
    ANN_BOX.add("m4", ["INBOX"], "Someone <someone@example.com>", "list@example.com", "After the reset")
    n, got = await run(ANN)
    check("after the reset: the next new message is announced (to a list)",
          n == 1 and got[0][3]["message_id"] == "m4" and got[0][3]["to"] == "list@example.com", got)

    records = Records()
    log = logging.getLogger("addons.gmail.event_types")
    log.addHandler(records)
    level = log.level
    log.setLevel(logging.INFO)
    ANN_BOX.down = True
    try:
        before = event_types._state(ANN)
        outcomes = [await run(ANN) for _ in range(3)]
        after = event_types._state(ANN)
    finally:
        ANN_BOX.down = False
    failed_lines = [line for line in records.lines if "not read" in line]
    check("network down: no exception, nothing emitted, the historyId kept",
          all(o == (0, []) for o in outcomes) and before == after, outcomes)
    check("network down: logged once, not every minute", len(failed_lines) == 1, records.lines)
    ANN_BOX.add("m5", ["INBOX"], "Someone <someone@example.com>", "ann@example.com", "While down")
    n, got = await run(ANN)
    log.removeHandler(records)
    log.setLevel(level)
    check("back up: catches up on what came while down, and says so once",
          n == 1 and got[0][3]["message_id"] == "m5" and any("read again" in line for line in records.lines), got)

    n, got = await run(BOB)
    check("Bob's first poll: baseline (his address learned from the profile)",
          n == 0 and event_types._state(BOB) == ("100", "bob@example.com"), event_types._state(BOB))
    BOB_BOX.add("b1", ["INBOX"], "bob@example.com", "bob@example.com", "Bob to Bob")
    n, got = await run(BOB)
    check("Bob: his own message only, as Bob, from his address",
          n == 1 and [(u, a, d["message_id"], d["from_email"]) for _, u, a, d in got]
          == [(BOB.id, BOB.id, "b1", "bob@example.com")],
          got)
    n, got = await run(CARL)
    check("Carl (no Gmail): nothing", n == 0 and got == [])


def test_history_prune_catalog() -> None:
    since = clock.now() - datetime.timedelta(hours=1)
    ann = event_types.received_history(ANN, since)
    check("history: Ann's announced emails as emitted, aware times",
          [d["message_id"] for _, d in ann] == ["m1", "m2", "m4", "m5"] and all(t.tzinfo for t, _ in ann), ann)
    check("history: Bob's own only", [d["message_id"] for _, d in event_types.received_history(BOB, since)] == ["b1"])
    pruned = event_types.prune(ANN, clock.now() + datetime.timedelta(days=31))
    with get_db_connection() as conn:
        left = {r["user_id"] for r in conn.execute("SELECT user_id FROM gmail_received")}
    check("prune: Ann's old rows gone, Bob's kept", pruned == 4 and left == {BOB.id}, (pruned, left))

    names = {e.name for e in registry.event_types(ANN)}
    check("catalog: gmail.email.received for a user with Gmail", "gmail.email.received" in names)
    check("catalog: not for Carl", "gmail.email.received" not in {e.name for e in registry.event_types(CARL)})
    jobs = {j.name for j in get_addon("gmail").jobs}
    check("the addon has the inbox and prune jobs", {"inbox", "prune_received"} <= jobs, jobs)


async def main() -> None:
    events.tap(collect)
    try:
        await test_poll()
    finally:
        events.untap(collect)
    test_history_prune_catalog()


asyncio.run(main())
passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
