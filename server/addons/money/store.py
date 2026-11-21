"""Reads of a user's money data and the payloads the API returns (docs/money.md).

Every query is scoped to the user: another user's id answers like a missing
one. Times are stored as ISO UTC (:func:`iso`), amounts as integer agorot.
Credentials never leave this module except as the encrypted token
(:func:`credentials_token`, read only by ``sync.py``)."""

import contextlib
import datetime
import json
import logging
import sqlite3
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence

from core import clock
from core.db import get_db_connection
from core.users import User
from addons.money import categories, companies

logger = logging.getLogger(__name__)

SOURCE_STATES = ("new", "syncing", "needs_code", "ok", "failing", "needs_attention")
# Login problems after which the user's own "Sync now" may sign in once more (Dana's syncs never do):
# a refused code, a sign-in with no clear answer, codes that twice did not come. The others need new
# login details.
HAND_RETRY_ERRORS = ("CODE_REJECTED", "LOGIN_UNKNOWN", "CODE_TIMEOUT")
# Columns of money_sources returned to callers (never ``credentials``).
_SOURCE_COLUMNS = ("id", "company", "label", "state", "state_reason", "error_type", "failures", "last_sync_at",
                   "last_ok_at", "next_sync_at", "signal_syncs_day", "signal_syncs", "last_manual_at", "created_at",
                   "updated_at")
_TRANSACTION_FIELDS = ("id", "account_id", "source", "status", "occurred_at", "purchase_date", "charge_date",
                       "amount", "orig_amount", "orig_currency", "description", "merchant", "merchant_key", "memo",
                       "category", "category_source", "flow", "installment_number", "installment_total",
                       "notification_id", "note", "created_at", "updated_at")
_SYNC_FIELDS = ("id", "source_id", "trigger", "state", "error_type", "error", "added", "updated", "started_at",
                "finished_at")


# --- Time and the database ----------------------------------------------------------

def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def iso(dt: datetime.datetime) -> str:
    """The stored form of a time: ISO UTC to the second."""
    return dt.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")


def now_iso() -> str:
    return iso(utc_now())


def parse(value: Any) -> Optional[datetime.datetime]:
    """A stored time as an aware UTC datetime (None for empty / malformed)."""
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


@contextlib.contextmanager
def db() -> Iterator[sqlite3.Connection]:
    """A connection whose block is one transaction (committed on success), closed after."""
    conn = get_db_connection()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# --- The self-check mark ------------------------------------------------------------

def mark_key(user_id: str) -> str:
    """The ``system_marks`` key of the user's money sync mark."""
    return f"money_sync:{user_id}"


def mark(user_id: str, ok: bool, detail: str = "") -> None:
    """Record a finished sync of one of the user's sources (read by core/checks.py). Never raises."""
    try:
        now = now_iso()
        with db() as conn:
            conn.execute(
                "INSERT INTO system_marks (key, last_at, ok, last_ok_at, last_fail_at, detail) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET last_at = excluded.last_at, ok = excluded.ok, detail = excluded.detail, "
                "last_ok_at = COALESCE(excluded.last_ok_at, system_marks.last_ok_at), "
                "last_fail_at = COALESCE(excluded.last_fail_at, system_marks.last_fail_at)",
                (mark_key(user_id), now, 1 if ok else 0, now if ok else None, None if ok else now, (detail or "")[:300]))
    except Exception as e:
        logger.debug(f"Money mark for {user_id} not written: {e}")


# --- Sources ------------------------------------------------------------------------------

def _source(row: sqlite3.Row) -> Dict[str, Any]:
    out = {k: row[k] for k in _SOURCE_COLUMNS}
    out["credentials_set"] = bool(row["credentials"])
    return out


def list_sources(user: User) -> List[Dict[str, Any]]:
    """The user's bank / card logins, oldest first (no credentials; ``credentials_set``)."""
    with db() as conn:
        rows = conn.execute("SELECT * FROM money_sources WHERE user_id = ? ORDER BY created_at, id",
                            (user.id,)).fetchall()
    return [_source(r) for r in rows]


def get_source(user: User, source_id: str) -> Optional[Dict[str, Any]]:
    """One of the user's sources (no credentials), None when missing or another user's."""
    with db() as conn:
        row = conn.execute("SELECT * FROM money_sources WHERE id = ? AND user_id = ?", (source_id, user.id)).fetchone()
    return _source(row) if row else None


def credentials_token(user: User, source_id: str) -> Optional[str]:
    """The encrypted credentials of one of the user's sources (for ``sync.py`` only)."""
    with db() as conn:
        row = conn.execute("SELECT credentials FROM money_sources WHERE id = ? AND user_id = ?",
                           (source_id, user.id)).fetchone()
    return row["credentials"] if row else None


def device_token(user: User, source_id: str) -> Optional[str]:
    """The encrypted device key of one of the user's sources (Psagot's quick view; ``sync.py`` only)."""
    with db() as conn:
        row = conn.execute("SELECT device FROM money_sources WHERE id = ? AND user_id = ?",
                           (source_id, user.id)).fetchone()
    return row["device"] if row else None


def set_device(user: User, source_id: str, token: Optional[str]) -> None:
    """Keep (an encrypted token) or forget (None) a source's device key."""
    with db() as conn:
        conn.execute("UPDATE money_sources SET device = ? WHERE id = ? AND user_id = ?", (token, source_id, user.id))


def source_name(source: Dict[str, Any]) -> str:
    """What to call a source in messages: its label, else the company's name."""
    return (source.get("label") or "").strip() or companies.name_of(source.get("company") or "")


# --- Accounts -----------------------------------------------------------------------------

def mask_number(number: str) -> str:
    """``••••901``: only the last 3 digits of an account number."""
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    return "••••" + digits[-3:] if digits else ""


def list_accounts(user: User, source_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """The user's accounts (bank accounts and cards), optionally only one source's."""
    sql, args = "SELECT * FROM money_accounts WHERE user_id = ?", [user.id]
    if source_id is not None:
        sql += " AND source_id = ?"
        args.append(source_id)
    with db() as conn:
        rows = conn.execute(sql + " ORDER BY kind, company, number", args).fetchall()
    return [dict(r) for r in rows]


def get_account(user: User, account_id: str) -> Optional[Dict[str, Any]]:
    with db() as conn:
        row = conn.execute("SELECT * FROM money_accounts WHERE id = ? AND user_id = ?",
                           (account_id, user.id)).fetchone()
    return dict(row) if row else None


def account_payload(account: Dict[str, Any]) -> Dict[str, Any]:
    """``{id, kind, number, label, balance, balance_at, next_charge, next_charge_date}``; a bank
    account's number masked to its last 3 digits, a card's last 4 as the company gives them."""
    number = account["number"]
    return {"id": account["id"], "kind": account["kind"],
            "number": mask_number(number) if account["kind"] in ("bank", "investment") else number,
            "label": account.get("label"), "balance": account.get("balance"), "balance_at": account.get("balance_at"),
            "next_charge": account.get("next_charge"), "next_charge_date": account.get("next_charge_date")}


# --- Code requests ------------------------------------------------------------------------

def code_requests(user: User, *, status: Optional[str] = None,
                  source_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """The user's sign-in code requests, newest first; a pending one past its
    expiry is reported as ``expired``; ``status`` filters on that."""
    sql, args = "SELECT * FROM money_code_requests WHERE user_id = ?", [user.id]
    if source_id is not None:
        sql += " AND source_id = ?"
        args.append(source_id)
    with db() as conn:
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY created_at DESC, id", args).fetchall()]
    now = now_iso()
    for r in rows:
        if r["status"] == "pending" and r["expires_at"] <= now:
            r["status"] = "expired"
    return [r for r in rows if status is None or r["status"] == status]


def get_code_request(user: User, request_id: str) -> Optional[Dict[str, Any]]:
    """One of the user's code requests (status as in :func:`code_requests`), None when missing / another user's."""
    with db() as conn:
        row = conn.execute("SELECT * FROM money_code_requests WHERE id = ? AND user_id = ?",
                           (request_id, user.id)).fetchone()
    if row is None:
        return None
    out = dict(row)
    if out["status"] == "pending" and out["expires_at"] <= now_iso():
        out["status"] = "expired"
    return out


def hand_sync_allowed(source: Mapping[str, Any]) -> bool:
    """Whether the user's own "Sync now" may start the source (a ``needs_attention``
    one only after a :data:`HAND_RETRY_ERRORS` problem)."""
    return source.get("state") != "needs_attention" or source.get("error_type") in HAND_RETRY_ERRORS


def source_payload(user: User, source: Dict[str, Any]) -> Dict[str, Any]:
    """The API's view of a source: its state, sync times, accounts, the pending
    code request and ``can_sync`` (the user's "Sync now" is allowed by its state)."""
    pending = code_requests(user, status="pending", source_id=source["id"])
    return {"id": source["id"], "company": source["company"], "company_name": companies.name_of(source["company"]),
            "label": source.get("label"), "state": source["state"], "state_reason": source.get("state_reason"),
            "error_type": source.get("error_type"), "last_sync_at": source.get("last_sync_at"),
            "last_ok_at": source.get("last_ok_at"), "next_sync_at": source.get("next_sync_at"),
            "credentials_set": bool(source.get("credentials_set")), "can_sync": hand_sync_allowed(source),
            "accounts": [account_payload(a) for a in list_accounts(user, source["id"])],
            "code_request": {"id": pending[0]["id"], "expires_at": pending[0]["expires_at"]} if pending else None}


# --- Syncs --------------------------------------------------------------------------------

def list_syncs(user: User, source_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """The user's sync runs, newest first (``{id, source_id, trigger, state, error_type, error,
    added, updated, started_at, finished_at}``)."""
    sql, args = "SELECT * FROM money_syncs WHERE user_id = ?", [user.id]
    if source_id is not None:
        sql += " AND source_id = ?"
        args.append(source_id)
    args.append(max(1, min(int(limit), 200)))
    with db() as conn:
        rows = conn.execute(sql + " ORDER BY started_at DESC, rowid DESC LIMIT ?", args).fetchall()
    return [{k: r[k] for k in _SYNC_FIELDS} for r in rows]


def get_sync(user: User, sync_id: str) -> Optional[Dict[str, Any]]:
    with db() as conn:
        row = conn.execute("SELECT * FROM money_syncs WHERE id = ? AND user_id = ?", (sync_id, user.id)).fetchone()
    return {k: row[k] for k in _SYNC_FIELDS} if row else None


# --- Transactions -------------------------------------------------------------------------

def transaction_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    """A transaction as callers see it (every column but ``user_id`` and ``raw``)."""
    return {k: row.get(k) for k in _TRANSACTION_FIELDS}


def list_transactions(user: User, *, start: Optional[str] = None, end: Optional[str] = None,
                      account_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """The user's transactions by purchase date, newest first: ``start``
    inclusive, ``end`` exclusive (local ``YYYY-MM-DD``), optionally one account's."""
    sql, args = "SELECT * FROM money_transactions WHERE user_id = ?", [user.id]
    if start:
        sql += " AND purchase_date >= ?"
        args.append(start)
    if end:
        sql += " AND purchase_date < ?"
        args.append(end)
    if account_id:
        sql += " AND account_id = ?"
        args.append(account_id)
    args.append(max(1, min(int(limit), 5000)))
    with db() as conn:
        rows = conn.execute(sql + " ORDER BY purchase_date DESC, COALESCE(occurred_at, '') DESC, id LIMIT ?",
                            args).fetchall()
    return [transaction_payload(dict(r)) for r in rows]


def get_transaction(user: User, txn_id: str) -> Optional[Dict[str, Any]]:
    """One of the user's transactions with ``raw`` (parsed), None when missing / another user's."""
    with db() as conn:
        row = conn.execute("SELECT * FROM money_transactions WHERE id = ? AND user_id = ?",
                           (txn_id, user.id)).fetchone()
    if row is None:
        return None
    out = transaction_payload(dict(row))
    out["raw"] = json.loads(row["raw"]) if row["raw"] else None
    return out


# --- Removing everything ------------------------------------------------------------------

def delete_all(user: User) -> List[str]:
    """Delete every money row of the user (sources, accounts, transactions,
    rules, aliases, phone signals, syncs, code requests, saving choices, price checks, meal card charges) and the
    Money section of their weekly reviews. Returns the removed source ids."""
    with db() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM money_sources WHERE user_id = ?", (user.id,))]
        for table in ("money_code_requests", "money_syncs", "money_signals", "money_transactions", "money_holdings",
                      "money_account_values", "money_trades", "money_meals", "money_accounts", "money_rules", "money_aliases",
                      "money_savings", "money_market_checks", "money_sources"):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user.id,))
        # The weekly reviews' Money sections go too (the rest of a review stays).
        conn.execute("UPDATE weekly_reviews SET data = json_remove(data, '$.money') WHERE user_id = ? "
                     "AND json_valid(data) AND json_extract(data, '$.money') IS NOT NULL", (user.id,))
    return ids


# --- Page and insight reads (queries.py, insights.py) -------------------------------------

# A transaction's flow as the numbers use it: its own, else (not classified yet) a card's
# money out is a purchase and money in a refund, and a bank row is not spending.
FLOW_SQL = ("CASE WHEN t.flow IS NOT NULL THEN t.flow WHEN a.kind = 'bank' THEN 'other' "
            "WHEN t.amount < 0 THEN 'purchase' ELSE 'refund' END")
# The transactions of one user with their account; hidden accounts never show.
TXN_FROM = ("FROM money_transactions t LEFT JOIN money_accounts a ON a.id = t.account_id AND a.user_id = t.user_id "
            "WHERE t.user_id = ? AND COALESCE(a.hidden, 0) = 0")
TXN_SELECT = (f"SELECT t.*, ({FLOW_SQL}) AS eff_flow, a.kind AS account_kind, a.company AS account_company, "
              f"a.number AS account_number, a.label AS account_label_own {TXN_FROM}")
# Newest first; the cursor of a page is these three values of its last row.
TXN_ORDER = "t.purchase_date DESC, COALESCE(t.occurred_at, '') DESC, t.id DESC"


def shekels(agorot: Optional[int]) -> str:
    """``₪1,234.50`` (``-₪50.00`` for a negative amount; ``—`` for none)."""
    if agorot is None:
        return "—"
    sign = "-" if agorot < 0 else ""
    whole, cents = divmod(abs(int(agorot)), 100)
    return f"{sign}₪{whole:,}.{cents:02d}"


def account_label(company: Optional[str], kind: Optional[str], number: Optional[str],
                  label: Optional[str] = None) -> str:
    """What to call an account: its label, else ``Cal 8763`` / ``Bank Hapoalim ••••901``."""
    if (label or "").strip():
        return label.strip()
    if not company:
        return "Unknown account"
    shown = mask_number(number or "") if kind in ("bank", "investment") else (number or "")
    return f"{companies.name_of(company)} {shown}".strip()


def local_date(value: Any) -> Optional[datetime.date]:
    """A stored UTC time as a local date (the server's zone), None for empty / malformed."""
    dt = parse(value)
    return dt.astimezone(clock.get_timezone()).date() if dt else None


def txn_item(row: Any) -> Dict[str, Any]:
    """A row of :data:`TXN_SELECT` as the page lists it: labels, the flow the numbers
    use, whether it counts as spending, and badges (``provisional`` / ``pending`` /
    ``unmatched``, ``installment {number, total}``, ``foreign {amount, currency}``)."""
    r = dict(row)
    category = r.get("category")
    group = categories.group_of(category) if category else ""
    badges: List[Dict[str, Any]] = []
    if r["status"] in ("provisional", "pending", "unmatched"):
        badges.append({"type": r["status"]})
    if (r.get("installment_total") or 0) > 1:
        badges.append({"type": "installment", "number": r.get("installment_number"), "total": r["installment_total"]})
    if r.get("orig_currency") and r["orig_currency"] != "ILS" and r.get("orig_amount") is not None:
        badges.append({"type": "foreign", "amount": r["orig_amount"], "currency": r["orig_currency"]})
    flow = r.get("eff_flow") or r.get("flow")
    return {"id": r["id"], "account_id": r.get("account_id"), "account_kind": r.get("account_kind"),
            "account_label": account_label(r.get("account_company"), r.get("account_kind"),
                                           r.get("account_number"), r.get("account_label_own")),
            "purchase_date": r["purchase_date"], "charge_date": r.get("charge_date"),
            "occurred_at": r.get("occurred_at"), "amount": r["amount"],
            "merchant": r.get("merchant") or r.get("description") or "", "merchant_key": r.get("merchant_key"),
            "description": r.get("description") or "", "memo": r.get("memo"), "category": category,
            "category_label": categories.CATEGORIES[category].label if category in categories.CATEGORIES
            else ("Uncategorized" if not category else category),
            "group": group or None, "group_label": categories.GROUP_KEYS[group].label if group else None,
            "category_source": r.get("category_source"), "flow": flow,
            "spending": flow in categories.SPENDING_FLOWS, "status": r["status"], "note": r.get("note"),
            "badges": badges}


def txn_rows(user: User, where: str = "", args: Sequence[Any] = (), *, order: str = TXN_ORDER,
             limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Rows of :data:`TXN_SELECT` for the user, ``where`` ANDed on (with its ``args``)."""
    sql = TXN_SELECT + (f" AND ({where})" if where else "") + f" ORDER BY {order}"
    params: List[Any] = [user.id, *args]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
