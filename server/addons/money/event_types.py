"""The event the ``money`` addon emits for the event catalog (../docs/addons.md,
"Events"): ``money.transaction.added``, once per transaction stored for the
first time, with its history from the user's own transactions (dry runs of
watches).

Where it is emitted: a provisional row made from a payment notification
(``signals.py``) at once; a synced or imported row after the sync's
reconciliation and classification (``classify.on_sync``), so its flow and
category are known; a synced row a payment signal points at (the scraped twin
a provisional row was merged into, or the row that replaced a pending one) is
not announced again. A row dated more than
:data:`MAX_AGE_DAYS` before it was stored (a first sync's history, an old
statement file) is not announced.
"""

import datetime
import re
from typing import Any, Dict, Iterable, List, Mapping

from core import events
from core.addons import EventType
from core.addons.hooks import PastEvent
from core.users import User
from addons.money import store

# A row dated this many days before it was stored is history, not news.
MAX_AGE_DAYS = 7
# Flows whose other side is a bank (or the user's own money); transfers' is a person.
BANK_FLOWS = frozenset({"card_bill", "loan", "interest", "investment", "own_transfer", "fee"})
TRANSFER_FLOWS = frozenset({"transfer_in", "transfer_out"})
# A company's name: בע"מ, LTD, INC.
_COMPANY = re.compile(r"בע[\"״']?מ|(?<![A-Za-z])(?:ltd|inc)(?![A-Za-z])", re.IGNORECASE)

_SELECT = ("SELECT t.id, t.amount, t.merchant, t.description, t.flow, t.category, t.status, t.purchase_date, "
           "t.created_at, EXISTS (SELECT 1 FROM money_signals s WHERE s.user_id = t.user_id "
           "AND s.transaction_id = t.id) AS signalled, a.company, a.kind, a.number, a.label "
           "FROM money_transactions t JOIN money_accounts a ON a.id = t.account_id WHERE t.user_id = ? ")


def counterparty_kind(flow: str, counterparty: str) -> str:
    """person | business | bank | unknown: who is on the other side of a transaction, by its flow
    (a transfer is a person's unless the name is a company's) and name."""
    if flow in TRANSFER_FLOWS:
        return "business" if _COMPANY.search(counterparty or "") else "person"
    if flow in BANK_FLOWS:
        return "bank"
    if not flow and not (counterparty or "").strip():
        return "unknown"
    return "business"


def transaction_data(row: Mapping[str, Any]) -> Dict[str, Any]:
    """``money.transaction.added``'s data of a transaction row joined with its account."""
    amount = round(int(row["amount"] or 0) / 100, 2)
    counterparty = (row["merchant"] or row["description"] or "").strip()
    flow = row["flow"] or ""
    return {"transaction_id": row["id"], "amount": amount, "direction": "in" if amount > 0 else "out",
            "counterparty": counterparty, "counterparty_kind": counterparty_kind(flow, counterparty),
            "flow": flow, "category": row["category"] or "",
            "account": store.account_label(row["company"], row["kind"], row["number"], row["label"]),
            "account_kind": row["kind"] or "", "status": row["status"] or ""}


def _fresh(row: Mapping[str, Any]) -> bool:
    """The row is news: dated at most :data:`MAX_AGE_DAYS` before it was stored."""
    stored = store.local_date(row["created_at"])
    try:
        dated = datetime.date.fromisoformat(str(row["purchase_date"])[:10])
    except ValueError:
        return False
    return stored is None or (stored - dated).days <= MAX_AGE_DAYS


def announce(user: User, ids: Iterable[str], *, synced: bool = True) -> int:
    """Emit ``money.transaction.added`` for ``user``'s new transactions ``ids``
    (those still there and fresh; ``synced``: leave out those a payment signal
    points at, announced already as the provisional row). Returns how many."""
    wanted = list(dict.fromkeys(ids))
    rows: List[Any] = []
    with store.db() as conn:
        for i in range(0, len(wanted), 500):
            chunk = wanted[i:i + 500]
            rows += conn.execute(_SELECT + f"AND t.id IN ({','.join('?' * len(chunk))})",
                                 (user.id, *chunk)).fetchall()
    n = 0
    for row in sorted(rows, key=lambda r: (r["purchase_date"], r["id"])):
        if not _fresh(row) or (synced and row["signalled"]):
            continue
        events.emit("money.transaction.added", user, **transaction_data(row))
        n += 1
    return n


def history(user: User, since: datetime.datetime) -> List[PastEvent]:
    """``user``'s transactions stored since ``since`` (fresh ones, not unmatched), oldest first."""
    with store.db() as conn:
        rows = conn.execute(_SELECT + "AND t.created_at >= ? AND t.status != 'unmatched' ORDER BY t.created_at",
                            (user.id, store.iso(since))).fetchall()
    out: List[PastEvent] = []
    for row in rows:
        when = store.parse(row["created_at"])
        if when is not None and _fresh(row):
            out.append((when, transaction_data(row)))
    return out


MONEY_EVENTS = (
    EventType("money.transaction.added", "A new bank or card transaction (synced, imported, or from a payment "
              "notification).",
              {"transaction_id": "the transaction's id",
               "amount": "in shekels (ILS), signed: + money in, - money out",
               "direction": "in | out",
               "counterparty": "the merchant or the other side's name",
               "counterparty_kind": "person | business | bank | unknown",
               "flow": "purchase | refund | transfer_out | transfer_in | income | card_bill | own_transfer | fee | "
                       "cash | loan | investment | other, or empty (card rows)",
               "category": "its spending category, or empty",
               "account": "the account's name",
               "account_kind": "bank | card",
               "status": "provisional (from a notification) | pending | completed"},
              history=history),
)
