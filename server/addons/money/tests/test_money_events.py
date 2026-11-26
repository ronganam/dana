"""Money events for the catalog (addons/money/event_types.py): money.transaction.added once per new transaction (not on updates, not for old rows, not again for a scraped twin of a provisional row), its data (shekels signed, direction, counterparty kind), the history per user and the catalog per user.

Made-up merchants, people and amounts. Ann and Bob have ``money`` with the
made-up provider (moneyfakes.py); Carl has not. Syncs run as sync.py runs
them (ingest in a thread, then the after-sync hooks: reconciliation, then
classification, which announces); a tap collects what is emitted.

Run: bin/test test_money_events
"""

import asyncio
import datetime
import os
import sys
import uuid
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("money_events_test")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake logins
os.environ["TIMEZONE"] = "Asia/Jerusalem"

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moneyfakes  # noqa: E402  (the made-up provider: moneyfakes.py)

from core import clock, events  # noqa: E402
from core.addons import registry, store as connections  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.users import User  # noqa: E402
from addons.money import after_sync, classify, event_types, ingest, schedule, signals, sources  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ANN = users.update(users.create("Ann").id, currency="ILS")
BOB = users.update(users.create("Bob").id, currency="ILS")
CARL = users.create("Carl")
for u in (ANN, BOB):
    connections.connect(u, "money")
    connections.connect(u, moneyfakes.PROVIDER)

# Sources are added before the event loop runs, so no sync starts.
ANN_BANK = sources.add(ANN, moneyfakes.BANK, {"userCode": "AB12345", "password": "pw-ann-bank"})
BOB_CARD = sources.add(BOB, moneyfakes.CARD, {"username": "bob.card", "password": "pw-bob-card"})
TZ = clock.get_timezone()
TODAY = clock.now().date()
_serial = [0]


def bank(day: datetime.date, amount: float, description: str) -> Dict[str, Any]:
    """A bank row on local date ``day``."""
    _serial[0] += 1
    date = datetime.datetime.combine(day, datetime.time(0), TZ).isoformat()
    return {"type": "normal", "identifier": _serial[0], "date": date, "processedDate": date, "originalAmount": amount,
            "originalCurrency": "ILS", "chargedAmount": amount, "description": description, "status": "completed",
            "memo": ""}


def card(identifier: str, day: datetime.date, amount: float, description: str) -> Dict[str, Any]:
    when = datetime.datetime.combine(day, datetime.time(0), TZ)
    charge = when + datetime.timedelta(days=30)
    return {"identifier": identifier, "type": "normal", "status": "completed", "date": when.isoformat(),
            "processedDate": charge.isoformat(), "originalAmount": amount, "originalCurrency": "₪",
            "chargedAmount": amount, "chargedCurrency": "₪", "description": description, "memo": ""}


def rule(user: User, description: str, flow: str) -> None:
    with get_db_connection() as conn:
        conn.execute("INSERT INTO money_rules (id, user_id, merchant_key, flow, created_at) VALUES (?, ?, ?, ?, ?)",
                     (uuid.uuid4().hex, user.id, ingest.merchant_key(description), flow, "2026-01-01T00:00:00+00:00"))
        conn.commit()


# Bob's card exists from an earlier sync (an old row): no hooks ran, nothing announced.
ingest.apply(BOB, BOB_CARD, [{"accountNumber": "4411", "balance": -10.0,
                              "txns": [card("80000000001", TODAY - datetime.timedelta(days=40), -10.0, "Card fee")]}])
rule(ANN, "Transfer from Example Person", "transfer_in")
rule(ANN, "Transfer to Example Ltd", "transfer_out")
rule(ANN, "Card company bill", "card_bill")

SEEN: List[Tuple[str, str, Dict[str, Any]]] = []  # (user id, acting user id, data)


async def collect(name: str, user: User, data: Dict[str, Any]) -> None:
    if name == "money.transaction.added":
        SEEN.append((user.id, users.current().id, data))


async def run_sync(user: User, source: Dict[str, Any], accounts: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """What sync.py does after a pull, then wait for classification and the events."""
    out = await asyncio.to_thread(ingest.apply, user, source, accounts)
    await after_sync.run(user, source["id"], out["new"], out["changed"])
    await classify.idle()
    await events.drain()
    return out


def test_kinds() -> None:
    k = event_types.counterparty_kind
    got = (k("transfer_in", "Example Person"), k("transfer_out", 'חברה לדוגמה בע"מ'), k("transfer_in", "Example INC"),
           k("card_bill", "Card company"), k("own_transfer", ""), k("purchase", "Corner Shop"), k("", "Corner Shop"),
           k("", ""))
    check("counterparty kind: transfers are people unless a company, bank flows the bank, else a business",
          got == ("person", "business", "business", "bank", "bank", "business", "business", "unknown"), got)


async def test_sync() -> None:
    SEEN.clear()
    accounts = [{"accountNumber": "12-345-678901", "balance": 30000.0, "txns": [
        bank(TODAY, 22000.0, "Transfer from Example Person"),
        bank(TODAY, -1500.0, "Transfer to Example Ltd"),
        bank(TODAY - datetime.timedelta(days=1), -3200.0, "Card company bill"),
        bank(TODAY - datetime.timedelta(days=30), -45.0, "Old bakery"),  # a first sync's history
    ]}]
    with patch.object(schedule, "request_soon", return_value=None):
        out = await run_sync(ANN, ANN_BANK, accounts)
    check("sync: four rows stored", len(out["new"]) == 4, out)
    got = {d["counterparty"]: d for _, _, d in SEEN}
    check("sync: one event per new fresh row, as the user; the month-old row not announced",
          sorted(got) == ["Card company bill", "Transfer from Example Person", "Transfer to Example Ltd"]
          and all(u == acting == ANN.id for u, acting, _ in SEEN), SEEN)
    t = got.get("Transfer from Example Person", {})
    check("sync: the contract's data (shekels signed, a person's transfer)",
          t == {"transaction_id": t.get("transaction_id"), "amount": 22000.0, "direction": "in",
                "counterparty": "Transfer from Example Person", "counterparty_kind": "person", "flow": "transfer_in",
                "category": t.get("category"), "account": t.get("account"), "account_kind": "bank",
                "status": "completed"} and t["transaction_id"] in out["new"] and t["account"].endswith("901")
          and isinstance(t["category"], str), t)
    check("sync: money out is negative; a company's transfer is a business; a card bill is the bank's",
          (got["Transfer to Example Ltd"]["amount"], got["Transfer to Example Ltd"]["direction"],
           got["Transfer to Example Ltd"]["counterparty_kind"], got["Card company bill"]["counterparty_kind"])
          == (-1500.0, "out", "business", "bank"), got)

    SEEN.clear()
    with patch.object(schedule, "request_soon", return_value=None):
        again = await run_sync(ANN, ANN_BANK, accounts)
    check("sync: the same rows again are not new, nothing emitted", not again["new"] and not SEEN, (again, SEEN))


async def test_provisional() -> None:
    SEEN.clear()
    push = {"key": "p1", "package": "com.example.samplecard", "app_name": "Sample card", "title": "Card",
            "text": "Purchase of ₪42.00 at CAFE EXAMPLE on card ending in 4411",
            "posted_at": int(clock.now().timestamp() * 1000)}
    with patch.object(schedule, "request_soon", return_value=None):
        stored = await asyncio.to_thread(signals.record_push, BOB, [push])
        await events.drain()
        prov = [(u, d) for u, _, d in SEEN]
        check("provisional: a payment push → one event for Bob, provisional, a business on his card",
              stored["provisional"] == 1 and len(prov) == 1 and prov[0][0] == BOB.id
              and {k: prov[0][1][k] for k in ("amount", "direction", "counterparty_kind", "flow", "account_kind",
                                             "status")}
              == {"amount": -42.0, "direction": "out", "counterparty_kind": "business", "flow": "purchase",
                  "account_kind": "card", "status": "provisional"}, (stored, prov))
        SEEN.clear()
        out = await run_sync(BOB, BOB_CARD, [{"accountNumber": "4411", "balance": -52.0, "txns": [
            card("80000000002", TODAY, -42.0, "CAFE EXAMPLE TLV")]}])
    check("provisional: its scraped twin is stored but not announced again", len(out["new"]) == 1 and not SEEN,
          (out, SEEN))


def test_history() -> None:
    since = clock.now() - datetime.timedelta(hours=1)
    ann = event_types.history(ANN, since)
    bob = event_types.history(BOB, since)
    check("history: Ann's fresh transactions, oldest first, the emitted shape, aware times",
          sorted(d["counterparty"] for _, d in ann)
          == ["Card company bill", "Transfer from Example Person", "Transfer to Example Ltd"]
          and all(t.tzinfo for t, _ in ann) and all(set(d) == set(ann[0][1]) for _, d in ann), ann)
    check("history: Bob's own rows only (the merged twin; no old row)",
          [(d["amount"], d["account_kind"]) for _, d in bob] == [(-42.0, "card")], bob)
    check("history: nothing stored after since", not event_types.history(ANN, clock.now()
                                                                          + datetime.timedelta(minutes=5)))


def test_catalog() -> None:
    check("catalog: money.transaction.added for users with money, with history",
          all(any(e.name == "money.transaction.added" and e.history for e in registry.event_types(u))
              for u in (ANN, BOB)))
    check("catalog: not for a user without money",
          not any(e.name.startswith("money.") for e in registry.event_types(CARL)))


async def main() -> None:
    events.tap(collect)
    try:
        test_kinds()
        await test_sync()
        await test_provisional()
        test_history()
        test_catalog()
    finally:
        events.untap(collect)


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
