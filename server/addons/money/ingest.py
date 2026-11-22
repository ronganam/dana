"""Storing a sync's result: accounts and transactions (docs/money.md, "Ingest"); an investment
account's portfolio goes to ``portfolio.py``, a meal card's charges to ``meals.py``.

A provider returns accounts in money's ingest format (docs/money.md, "Ingest
format"): ``accountNumber``, ``balance`` and ``txns`` (the shape of
``israeli-bank-scrapers``, which any provider can produce). :func:`apply`
stores them for one source in one transaction: accounts upserted by
``(user, company, number)``, transactions by ``(account, ext_key)``, the
account's ``pending`` rows not in this pull removed. Text is scrubbed before
it is stored (card numbers, and the personal numbers providers register);
amounts become signed agorot; dates (instants; a scraper's UTC instant of
local midnight) become local dates. A category comes only from the user's
rules here; classification runs later, from a hook."""

import datetime
import hashlib
import json
import logging
import re
import unicodedata
import uuid
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core import clock
from core.users import User
from addons.money import meals, portfolio, providers, store

logger = logging.getLogger(__name__)

CURRENCIES = {"₪": "ILS", "$": "USD", "€": "EUR"}

# Fields of a stored transaction a later pull may change (never category / merchant / flow / note).
_SCRAPED = ("status", "occurred_at", "purchase_date", "charge_date", "amount", "orig_amount", "orig_currency",
            "description", "merchant_key", "memo", "installment_number", "installment_total")

# --- Scrubbing ------------------------------------------------------------------------------

_CARD = re.compile(r"(?<!\d)\d{4}([ -]?)\d{4}\1\d{4}\1\d{4}(?!\d)")


def keep_last3(text: str) -> str:
    """A number as ``••••`` + its last 3 digits."""
    digits = re.sub(r"\D", "", text)
    return "••••" + digits[-3:]


def _redact(text: str, redaction: providers.Redaction) -> str:
    def one(match: "re.Match[str]") -> str:
        masked = redaction.mask(match.group(0))
        return match.group(0) if masked is None else masked
    return redaction.pattern.sub(one, text)


def scrub(text: str) -> str:
    """``text`` without card numbers (→ ``••••`` + their last 3 digits) and the personal
    numbers loaded providers mask (``providers.redactions``: ID numbers, account numbers). Names stay."""
    if not text:
        return text
    text = _CARD.sub(lambda m: keep_last3(m.group(0)), text)
    for redaction in providers.redactions():
        text = _redact(text, redaction)
    return text


def scrub_value(value: Any) -> Any:
    """:func:`scrub` applied to every string in a JSON value (and to integers
    that are a card number or a masked personal number)."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        s = str(value)
        masked = scrub(s)
        return masked if masked != s else value
    if isinstance(value, dict):
        return {k: scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_value(v) for v in value]
    return value


# --- Merchant keys --------------------------------------------------------------------------

_QUOTES = re.compile(r"[\"'`׳״]")
_PUNCT = re.compile(r"[^\w\s]")


def merchant_key(text: str) -> str:
    """A description reduced for matching: lower case, quotes dropped, other
    punctuation as spaces, number-only tokens (branch numbers) removed,
    spaces collapsed ("רמי לוי - יבנה 123" → "רמי לוי יבנה")."""
    s = unicodedata.normalize("NFKC", text or "").casefold()
    s = _QUOTES.sub("", s)
    s = _PUNCT.sub(" ", s).replace("_", " ")
    return " ".join(t for t in s.split() if not t.isdigit())


PAYEE_MAX = 60


def transfer_payee(company: Optional[providers.Company], memo: Optional[str]) -> Optional[Tuple[str, str]]:
    """``(payee, purpose)`` from a bank transfer's note, as the company's ``transfer_note`` reads it
    (the user typed both in the bank's app when sending the money); None when the company reads none
    or the note is not one."""
    if company is None or company.transfer_note is None or not memo:
        return None
    found = company.transfer_note(memo)
    if not found:
        return None
    payee = " ".join(found[0].split())[:PAYEE_MAX].strip()
    purpose = " ".join((found[1] or "").split())[:120].strip()
    return (payee, purpose) if payee and merchant_key(payee) else None


def clean_payee(payee: str) -> str:
    """A payee as a display name: without the legal suffix ("בית ההשקעות הראשון בע"מ" → "בית ההשקעות הראשון")."""
    name = re.sub(r"\s*\b(בע(?:[\"'״]?מ)?|בעמ|ltd\.?|inc\.?)\s*$", "", payee or "", flags=re.I).strip(" .,-")
    return name or (payee or "").strip()


# --- Values ---------------------------------------------------------------------------------

def agorot(value: Any) -> Optional[int]:
    """A shekel amount as integer agorot (rounded half up), None when missing."""
    if value is None or value == "":
        return None
    try:
        return int((Decimal(str(value)) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


def currency(value: Any) -> Optional[str]:
    """``₪`` → ILS, ``$`` → USD, ``€`` → EUR; anything else as given."""
    if value is None:
        return None
    text = str(value).strip()
    return CURRENCIES.get(text, text) or None


def _instant(value: Any) -> Optional[datetime.datetime]:
    """An ISO instant (``…Z``) in the server's zone; a naive value is taken as UTC."""
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(clock.get_timezone())


def _local_naive(value: Any) -> Optional[datetime.datetime]:
    """A local time (``purchaseDate``; naive = the server's zone) in the server's zone."""
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt.replace(tzinfo=clock.get_timezone()) if dt.tzinfo is None else dt.astimezone(clock.get_timezone())


def _when(dt: datetime.datetime) -> Optional[str]:
    """The exact time, when there is one (not local midnight)."""
    return None if dt.time() == datetime.time(0, 0) else dt.isoformat(timespec="seconds")


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# Format fields that are not the site's: left out of the stored ``raw``.
_NOT_RAW = ("purchaseDate",)


def _row(txn: Mapping[str, Any], company: Optional[providers.Company] = None) -> Optional[Dict[str, Any]]:
    """The stored fields of one scraped transaction (no ext_key yet); None when it has no date or amount."""
    when = _instant(txn.get("date"))
    amount = agorot(txn.get("chargedAmount"))
    if amount is None:
        amount = agorot(txn.get("originalAmount"))
    if when is None or amount is None:
        return None
    installments = txn.get("installments") if isinstance(txn.get("installments"), dict) else {}
    purchase, occurred = when.date(), _when(when)
    bought = _local_naive(txn.get("purchaseDate"))
    if bought is not None:  # the provider knows the purchase when ``date`` is another day (an installment's month)
        purchase, occurred = bought.date(), _when(bought)
    charged = _instant(txn.get("processedDate"))
    description = scrub(str(txn.get("description") or "")).strip()
    memo = scrub(str(txn.get("memo") or "")).strip()
    # A transfer the user sent is about its payee, not the bank's generic line: the payee is
    # its merchant (memory and "always for this payee" rules then work per payee).
    payee = transfer_payee(company, memo) if amount < 0 else None
    key = merchant_key(payee[0]) if payee else merchant_key(description)
    return {
        "status": "pending" if txn.get("status") == "pending" else "completed",
        "occurred_at": occurred, "purchase_date": purchase.isoformat(),
        "charge_date": charged.date().isoformat() if charged else None,
        "amount": amount, "orig_amount": agorot(txn.get("originalAmount")),
        "orig_currency": currency(txn.get("originalCurrency")),
        "description": description, "merchant_key": key, "memo": memo or None, "payee": payee[0] if payee else None,
        "installment_number": _int(installments.get("number")), "installment_total": _int(installments.get("total")),
        "raw": json.dumps(scrub_value({k: v for k, v in txn.items() if k not in _NOT_RAW}), ensure_ascii=False,
                          sort_keys=True),
        "_identifier": txn.get("identifier"),
    }


def _hash(row: Mapping[str, Any]) -> str:
    """The row's identity for sites without a stable id (scrubbed text: nothing secret is hashed)."""
    parts = (row["purchase_date"], row["charge_date"] or "", str(row["amount"]), row["description"], row["memo"] or "")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _assign_keys(company: providers.Company, rows: Sequence[Dict[str, Any]]) -> None:
    """Give each row of one account's pull its ``ext_key`` (unique within the pull): from the site's
    ``identifier`` when the company's are stable (``id_key``), else from a hash of the row."""
    occurrences: Counter = Counter()
    used = set()
    for row in rows:
        identifier = row.pop("_identifier", None)
        if company.id_key and identifier not in (None, ""):
            key = f"{company.id_key}:{identifier}:{row['installment_number'] or 0}"
        else:
            base = f"{company.hash_key}:{_hash(row)}"
            key = f"{base}:{occurrences[base]}"
            occurrences[base] += 1
        if key in used:  # a site repeating an id within one pull: keep both rows
            n = 1
            while f"{key}#{n}" in used:
                n += 1
            key = f"{key}#{n}"
        used.add(key)
        row["ext_key"] = key


# --- Storing --------------------------------------------------------------------------------

def _upsert_account(conn, user: User, source_id: str, company: providers.Company, number: str,
                    balance: Optional[int], next_charge: Optional[int], next_charge_date: Optional[str],
                    has_balance: bool, now: str) -> str:
    row = conn.execute("SELECT id FROM money_accounts WHERE user_id = ? AND company = ? AND number = ?",
                       (user.id, company.id, number)).fetchone()
    if row is None:
        account_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO money_accounts (id, user_id, source_id, company, kind, number, balance, balance_at, "
            "next_charge, next_charge_date, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, user.id, source_id, company.id, company.kind, number, balance, now if has_balance else None,
             next_charge, next_charge_date, now, now))
        return account_id
    account_id = row["id"]
    # scoped: the account found by user_id above
    conn.execute("UPDATE money_accounts SET source_id = ?, kind = ?, updated_at = ? WHERE id = ?",
                 (source_id, company.kind, now, account_id))
    if has_balance:
        # scoped: the account found by user_id above
        conn.execute("UPDATE money_accounts SET balance = ?, balance_at = ?, next_charge = ?, next_charge_date = ? "
                     "WHERE id = ?", (balance, now, next_charge, next_charge_date, account_id))
    return account_id


def apply(user: User, source: Mapping[str, Any], accounts: Sequence[Mapping[str, Any]]) -> Dict[str, List[str]]:
    """Store one sync's ``accounts`` for ``source`` (its ``id`` and ``company``), as one transaction.

    Returns:
        ``{"new": [transaction ids], "changed": [transaction ids], "accounts": [account ids]}``.

    Raises:
        UnknownCompany: no loaded provider brings the source's company.
    """
    company = providers.company(str(source["company"]))
    if company is None:
        raise providers.UnknownCompany(f"Unknown company {source['company']!r}")
    today = clock.now().date().isoformat()
    now = store.now_iso()
    out: Dict[str, List[str]] = {"new": [], "changed": [], "accounts": []}
    with store.db() as conn:
        rules = rules_of(conn, user)
        for account in accounts:
            if company.kind == "investment":
                account_id = portfolio.store_account(conn, user, str(source["id"]), company, account, now)
                if account_id:
                    out["accounts"].append(account_id)
                continue
            if company.kind == "meal":
                account_id = meals.store_account(conn, user, str(source["id"]), company, account, now)
                if account_id:
                    out["accounts"].append(account_id)
                continue
            number = str(account.get("accountNumber") or "").strip()
            if not number:
                logger.warning(f"Money ingest for source {source['id']}: an account without a number was skipped")
                continue
            rows = [r for r in (_row(t, company) for t in account.get("txns") or []) if r is not None]
            skipped = len(account.get("txns") or []) - len(rows)
            if skipped:
                logger.warning(f"Money ingest for source {source['id']}: {skipped} row(s) without a date or amount")
            _assign_keys(company, rows)
            raw_balance = agorot(account.get("balance"))
            has_balance = raw_balance is not None
            balance = next_charge = None
            next_charge_date = None
            if company.kind == "bank":
                balance = raw_balance
            elif has_balance:
                next_charge = abs(raw_balance or 0)
                upcoming = sorted(r["charge_date"] for r in rows if r["charge_date"] and r["charge_date"] >= today)
                next_charge_date = upcoming[0] if upcoming else None
            account_id = _upsert_account(conn, user, str(source["id"]), company, number, balance, next_charge,
                                         next_charge_date, has_balance, now)
            out["accounts"].append(account_id)
            _store_rows(conn, user, account_id, rows, rules, now, out)
    return out


def insert_row(conn, user: User, account_id: str, row: Mapping[str, Any], rules: Mapping[str, Dict[str, Any]],
               now: str) -> str:
    """Insert one statement row (the fields :func:`_row` gives, with its ``ext_key``) into ``account_id``,
    with the category and name of the user's rule for its merchant key when there is one; returns its id.
    The caller checked the account is the user's."""
    txn_id = uuid.uuid4().hex
    rule = rules.get(row["merchant_key"]) if row["merchant_key"] else None
    category = rule["category"] if rule and rule.get("category") else None
    conn.execute(
        "INSERT INTO money_transactions (id, user_id, account_id, ext_key, source, status, occurred_at, "
        "purchase_date, charge_date, amount, orig_amount, orig_currency, description, merchant, merchant_key, "
        "memo, category, category_source, flow, installment_number, installment_total, raw, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, 'scrape', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (txn_id, user.id, account_id, row["ext_key"], row["status"], row["occurred_at"], row["purchase_date"],
         row["charge_date"], row["amount"], row["orig_amount"], row["orig_currency"], row["description"],
         (rule or {}).get("merchant") or clean_payee(row.get("payee") or "") or row["description"], row["merchant_key"],
         row["memo"], category, "rule" if category else None, (rule or {}).get("flow"), row["installment_number"],
         row["installment_total"], row["raw"], now, now))
    return txn_id


def rules_of(conn, user: User) -> Dict[str, Dict[str, Any]]:
    """The user's merchant rules by merchant key (applied to new rows)."""
    return {r["merchant_key"]: dict(r) for r in conn.execute(
        "SELECT merchant_key, category, merchant, flow FROM money_rules WHERE user_id = ?", (user.id,))}


def _store_rows(conn, user: User, account_id: str, rows: Sequence[Dict[str, Any]], rules: Mapping[str, Dict[str, Any]],
                now: str, out: Dict[str, List[str]]) -> None:
    pending = [r["ext_key"] for r in rows if r["status"] == "pending"]
    marks = ",".join("?" * len(pending))
    conn.execute(f"DELETE FROM money_transactions WHERE account_id = ? AND user_id = ? AND source = 'scrape' "
                 f"AND status = 'pending'" + (f" AND ext_key NOT IN ({marks})" if pending else ""),
                 (account_id, user.id, *pending))
    keys = [r["ext_key"] for r in rows]
    existing: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(keys), 500):
        chunk = keys[i:i + 500]
        existing.update((r["ext_key"], dict(r)) for r in conn.execute(
            f"SELECT * FROM money_transactions WHERE account_id = ? AND user_id = ? "
            f"AND ext_key IN ({','.join('?' * len(chunk))})", (account_id, user.id, *chunk)))
    for row in rows:
        old = existing.get(row["ext_key"])
        if old is None:
            out["new"].append(insert_row(conn, user, account_id, row, rules, now))
            continue
        changes = {k: row[k] for k in _SCRAPED if old.get(k) != row[k]}
        if changes:
            sets = ", ".join(f"{k} = ?" for k in changes)
            # scoped: rows read by account_id AND user_id above
            conn.execute(f"UPDATE money_transactions SET {sets}, raw = ?, updated_at = ? WHERE id = ?",
                         (*changes.values(), row["raw"], now, old["id"]))
            out["changed"].append(old["id"])
        elif old.get("raw") != row["raw"]:
            # scoped: rows read by account_id AND user_id above
            conn.execute("UPDATE money_transactions SET raw = ? WHERE id = ?", (row["raw"], old["id"]))
