"""The user's own categories, notes and marks (docs/money.md, "Categories", "Dashboards").

:func:`set_category` sets one transaction's category (``category_source =
'user'``) and, by default, saves a rule for its merchant key that applies to
the user's other rows of that merchant (except those they set by hand) and
to every later ingest. :func:`set_flow` sets what one row counts as
(spending, money set aside, my own money, income…); :func:`set_mark` marks
it a one-off or yearly (or gives it back to Dana's call). Every change
registers an Undo (core/undo.py) that restores the previous values."""

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from core import undo, users
from core.users import User
from integrations.money import categories, store

logger = logging.getLogger(__name__)

MAX_NOTE = 500


def _txn(conn, user: User, txn_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT id, merchant, merchant_key, category, category_source, note FROM money_transactions "
                       "WHERE id = ? AND user_id = ?", (txn_id, user.id)).fetchone()
    if row is None:
        raise LookupError(f"unknown transaction {txn_id!r}")
    return dict(row)


def set_category(user: User, txn_id: str, category: str, for_merchant: bool = True) -> Dict[str, Any]:
    """Set one of the user's transactions to ``category`` (a category key);
    with ``for_merchant``, also for its merchant from now on.

    Returns:
        ``{"changed": <rows whose category changed>, "undo_id": str}``.

    Raises:
        ValueError: not a category key.
        LookupError: no such transaction of the user.
    """
    if not categories.is_category(category):
        raise ValueError(f"unknown category {category!r}")
    now = store.now_iso()
    with store.db() as conn:
        txn = _txn(conn, user, txn_id)
        key = txn["merchant_key"] or ""
        before: List[Tuple[str, Optional[str], Optional[str]]] = [(txn["id"], txn["category"],
                                                                    txn["category_source"])]
        old_rule: Optional[Dict[str, Any]] = None
        rule_set = False
        if for_merchant and key:
            old = conn.execute("SELECT * FROM money_rules WHERE user_id = ? AND merchant_key = ?",
                               (user.id, key)).fetchone()
            old_rule = dict(old) if old else None
            others = conn.execute(
                "SELECT id, category, category_source FROM money_transactions WHERE user_id = ? AND merchant_key = ? "
                "AND id != ? AND COALESCE(category_source, '') != 'user'", (user.id, key, txn_id)).fetchall()
            before += [(r["id"], r["category"], r["category_source"]) for r in others]
            if old_rule:
                conn.execute("UPDATE money_rules SET category = ? WHERE id = ?", (category, old_rule["id"]))
            else:
                conn.execute("INSERT INTO money_rules (id, user_id, merchant_key, category, created_at) "
                             "VALUES (?, ?, ?, ?, ?)", (uuid.uuid4().hex, user.id, key, category, now))
            rule_set = True
            conn.execute("UPDATE money_transactions SET category = ?, category_source = 'rule', updated_at = ? "
                         "WHERE user_id = ? AND merchant_key = ? AND id != ? "
                         "AND COALESCE(category_source, '') != 'user'", (category, now, user.id, key, txn_id))
        conn.execute("UPDATE money_transactions SET category = ?, category_source = 'user', updated_at = ? "
                     "WHERE id = ? AND user_id = ?", (category, now, txn_id, user.id))
    changed = sum(1 for _, cat, _ in before if cat != category)

    def restore() -> None:
        with store.db() as conn:
            for row_id, cat, source in before:
                conn.execute("UPDATE money_transactions SET category = ?, category_source = ?, updated_at = ? "
                             "WHERE id = ? AND user_id = ?", (cat, source, store.now_iso(), row_id, user.id))
            if rule_set:
                if old_rule is None:
                    conn.execute("DELETE FROM money_rules WHERE user_id = ? AND merchant_key = ?", (user.id, key))
                else:
                    conn.execute("UPDATE money_rules SET category = ?, merchant = ?, flow = ? WHERE id = ? "
                                 "AND user_id = ?", (old_rule["category"], old_rule["merchant"], old_rule["flow"],
                                                     old_rule["id"], user.id))

    label = categories.CATEGORIES[category].label
    what = f"{txn['merchant'] or 'the transaction'}" + (" and its merchant" if rule_set else "")
    with users.acting_as(user):
        undo_id = undo.record(f"Set {what} to {label}", restore, source="money.set_category")
    return {"changed": changed, "undo_id": undo_id}


def set_note(user: User, txn_id: str, note: str) -> Dict[str, Any]:
    """Set (or clear, with "") the note on one of the user's transactions.

    Returns:
        ``{"undo_id": str}``.

    Raises:
        LookupError: no such transaction of the user.
    """
    text = (note or "").strip()[:MAX_NOTE] or None
    with store.db() as conn:
        txn = _txn(conn, user, txn_id)
        conn.execute("UPDATE money_transactions SET note = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                     (text, store.now_iso(), txn_id, user.id))
    previous = txn["note"]

    def restore() -> None:
        with store.db() as conn:
            conn.execute("UPDATE money_transactions SET note = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                         (previous, store.now_iso(), txn_id, user.id))

    with users.acting_as(user):
        undo_id = undo.record("Changed the note" if text else "Removed the note", restore, source="money.set_note")
    return {"undo_id": undo_id}


# --- What a transaction counts as (the dashboards) ------------------------------------------

# The flows the user may give a row ("counts as"): spending, a transfer to a person, money set
# aside, money moved between their own accounts (not spending), income, a refund.
USER_FLOWS = ("purchase", "transfer_out", "investment", "own_transfer", "income", "transfer_in", "refund", "loan",
              "fee", "cash")
FLOW_WORDS = {"purchase": "spending", "transfer_out": "a transfer to a person", "investment": "money set aside",
              "own_transfer": "my own money", "income": "income", "transfer_in": "money received",
              "refund": "a refund", "loan": "a loan payment", "fee": "a fee", "cash": "cash"}


def set_flow(user: User, txn_id: str, flow: str) -> Dict[str, Any]:
    """Set what one of the user's transactions counts as (``flow``, one of :data:`USER_FLOWS`);
    the row is then theirs (``category_source = 'user'``: never classified again).

    Returns:
        ``{"undo_id": str}``.

    Raises:
        ValueError: not one of :data:`USER_FLOWS`.
        LookupError: no such transaction of the user.
    """
    if flow not in USER_FLOWS:
        raise ValueError(f"unknown flow {flow!r}")
    with store.db() as conn:
        txn = _txn(conn, user, txn_id)
        before = conn.execute("SELECT flow FROM money_transactions WHERE id = ?", (txn_id,)).fetchone()["flow"]
        source = txn["category_source"] if txn["category_source"] in ("user", "rule") else "user"
        conn.execute("UPDATE money_transactions SET flow = ?, category_source = ?, updated_at = ? "
                     "WHERE id = ? AND user_id = ?", (flow, source, store.now_iso(), txn_id, user.id))

    def restore() -> None:
        with store.db() as conn:
            conn.execute("UPDATE money_transactions SET flow = ?, category_source = ?, updated_at = ? "
                         "WHERE id = ? AND user_id = ?",
                         (before, txn["category_source"], store.now_iso(), txn_id, user.id))

    with users.acting_as(user):
        undo_id = undo.record(f"Counted {txn['merchant'] or 'the transaction'} as {FLOW_WORDS[flow]}", restore,
                              source="money.set_flow")
    return {"undo_id": undo_id}


_MARKS = {"one_off": ("a one-off", "not a one-off"), "yearly": ("yearly", "not yearly")}


def set_mark(user: User, txn_id: str, mark: str, value: Optional[bool]) -> Dict[str, Any]:
    """Mark one of the user's transactions a one-off or yearly (``value`` True), not one
    (False) or back to Dana's call (None). Every payment of an installment plan gets it.

    Returns:
        ``{"changed": <rows>, "undo_id": str}``.

    Raises:
        ValueError: ``mark`` is not ``one_off`` / ``yearly``.
        LookupError: no such transaction of the user.
    """
    if mark not in _MARKS:
        raise ValueError(f"unknown mark {mark!r}")
    stored = None if value is None else int(bool(value))
    with store.db() as conn:
        row = conn.execute(
            "SELECT id, merchant, account_id, merchant_key, purchase_date, installment_total, orig_amount "
            "FROM money_transactions WHERE id = ? AND user_id = ?", (txn_id, user.id)).fetchone()
        if row is None:
            raise LookupError(f"unknown transaction {txn_id!r}")
        if (row["installment_total"] or 0) > 1:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM money_transactions WHERE user_id = ? AND account_id = ? AND merchant_key IS ? "
                "AND purchase_date = ? AND installment_total = ? AND orig_amount IS ?",
                (user.id, row["account_id"], row["merchant_key"], row["purchase_date"], row["installment_total"],
                 row["orig_amount"]))]
        else:
            ids = [txn_id]
        marks = ",".join("?" * len(ids))
        before = [(r["id"], r[mark]) for r in conn.execute(
            f"SELECT id, {mark} FROM money_transactions WHERE user_id = ? AND id IN ({marks})", (user.id, *ids))]
        conn.execute(f"UPDATE money_transactions SET {mark} = ?, updated_at = ? WHERE user_id = ? AND id IN ({marks})",
                     (stored, store.now_iso(), user.id, *ids))

    def restore() -> None:
        with store.db() as conn:
            for row_id, old in before:
                conn.execute(f"UPDATE money_transactions SET {mark} = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                             (old, store.now_iso(), row_id, user.id))

    words = _MARKS[mark]
    what = row["merchant"] or "the transaction"
    label = f"Marked {what} {words[0] if value else words[1]}" if value is not None else f"{what}: back to automatic"
    with users.acting_as(user):
        undo_id = undo.record(label, restore, source=f"money.set_{mark}")
    return {"changed": len(ids), "undo_id": undo_id}
