"""Payment notifications from the phone → instant transactions (docs/money.md, "Instant updates from the phone").

Two ways in: a Google Wallet notification of the Inbox (:func:`observe`,
registered with the phone addon's notification observers) and the card /
bank apps' pushes the phone posts to ``/api/money/signals``
(:func:`record_push`). Every one is stored in ``money_signals`` (kept
:data:`KEEP_DAYS` days) so parsers can be written from real samples.

A parsed shekel payment on one of the user's cards (by its last 4 digits)
becomes a ``provisional`` transaction at once, named and categorised from
the merchant memory, and pulls that card's sync earlier
(``schedule.request_soon``). After every sync :func:`reconcile` (a hook that
runs before classification) merges each provisional row into the scraped
row of the same amount within :data:`MATCH_DAYS` days, keeping the exact
time and the notification and learning the merchant's other spelling;
a provisional row still alone :data:`UNMATCHED_DAYS` days after its date
becomes ``unmatched`` (declined or reversed: shown greyed, never counted).
"""

import asyncio
import datetime
import logging
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from core import clock
from core.text import redact_otp as redact
from core.users import User
from core.addons import store as connections
from addons.money import classify, config, ingest, providers, schedule, store

logger = logging.getLogger(__name__)

WALLET_PACKAGE = "com.google.android.apps.walletnfcrel"
KEEP_DAYS = 60
# A provisional row merges with a scraped one of the same amount this many days apart.
MATCH_DAYS = 2
# A provisional row with no scraped twin this many days after its date is ``unmatched``.
UNMATCHED_DAYS = 5
# Two signals of one amount on one card this close together are one payment (Wallet and the card's app).
SAME_PAYMENT_MINUTES = 15
# At signal time an already-scraped row is taken only this close to the notification (when both have a time).
SCRAPED_HOURS = 12
MAX_BATCH = 200
# A provisional row also takes the category the card company gave the merchant's earlier rows.
PROVISIONAL_MEMORY = classify.MEMORY_SOURCES + ("bank",)


# --- Parsing ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Payment:
    """What a notification says: ``amount`` signed agorot (a payment is
    negative, a refund positive), ``currency`` (ILS, USD, EUR, GBP), the card's
    last 4 digits and the merchant, when given."""
    amount: int
    currency: str
    card_last4: Optional[str]
    merchant: Optional[str]


_BIDI = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069\u061c]")
# 1,234.50 · 1.234,50 · 1 234,50 (a space only before decimals with a comma) · 350.30 · 350,30 · 350
_NUMBER = (r"(?<![\d.,])(?:\d{1,3}(?:[,.]\d{3})+(?:[.,]\d{1,2})?|\d{1,3}(?: \d{3})+,\d{1,2}"
           r"|\d+(?:[.,]\d{1,2})?)(?!\d)")
_SYMBOLS = {"₪": "ILS", "$": "USD", "€": "EUR", "£": "GBP"}
_CODES = {"ש\"ח": "ILS", "ש״ח": "ILS", "שח": "ILS", "nis": "ILS", "ils": "ILS", "usd": "USD", "eur": "EUR",
          "gbp": "GBP"}
_CURRENCY_AFTER = r"[₪$€£]|ש[\"״]?ח|NIS|ILS|USD|EUR|GBP"
_AMOUNTS = (
    re.compile(rf"(?P<cur>[₪$€£])\s*(?P<num>{_NUMBER})"),
    re.compile(rf"(?P<num>{_NUMBER})\s*(?P<cur>{_CURRENCY_AFTER})(?![A-Za-z])", re.I),
    re.compile(rf"(?<![A-Za-z])(?P<cur>NIS|ILS|USD|EUR|GBP)\s*(?P<num>{_NUMBER})", re.I),
)
_CARD = (
    re.compile(r"(?:[•·*●∙]{2,}|[xX]{2,})\s*(\d{4})(?!\d)"),
    re.compile(r"(?:ending(?:\s+in)?|המסתיים\s+ב|המסתיימת\s+ב|שמסתיים\s+ב|מסתיים\s+ב|בכרטיס(?:\s+(?:מספר|שמספרו))?"
               r"|כרטיס(?:\s+(?:מספר|שמספרו))?|card(?:\s+no\.?)?)\s*[-:]?\s*(\d{4})(?!\d)", re.I),
)
_MERCHANT = (
    re.compile(r"(?:בבית\s+העסק|בבית\s+עסק|אצל)\s*[-:]?\s*(?P<m>[^\n,;.]+)"),
    re.compile(r"\bat\s+(?P<m>[^\n,;]+?)(?=\s+(?:on|with|using|for|by)\b|[\n,;]|\.(?:\s|$)|$)", re.I),
    re.compile(r"(?<!\w)ב-\s*(?P<m>[^\d\s\n,;.][^\n,;.]*)"),
)
# Where a merchant's name ends: an amount word, a date / card word, a digit or a currency sign.
_MERCHANT_END = re.compile(r"\s+(?:בסך|בסכום|על\s+סך|סך|בכרטיס|בתאריך|בשעה|ביום|on|with|using|for|by)(?:\s|$)"
                           r"|\s*[\d₪$€£]", re.I)
_REFUND = re.compile(r"\brefund|\breturned|זיכוי|החזר", re.I)
# A card app's notice about a bill or a statement, not a payment.
_NOT_PAYMENT = re.compile(r"חיוב\s+(?:החודשי|הבא|צפוי)|החיוב\s+החודשי|דף\s+(?:ה)?פירוט|יחויב\s+ב|"
                          r"\bstatement\b|\byour\s+bill\b|\bupcoming\s+charge\b", re.I)


def _clean(text: Any) -> str:
    """Text without bidi marks, NFKC, spaces collapsed."""
    s = unicodedata.normalize("NFKC", _BIDI.sub("", str(text or "")))
    return re.sub(r"[ \t]+", " ", s).strip()


def _number(text: str) -> Optional[Decimal]:
    """``1,234.50`` / ``1.234,50`` / ``350,30`` / ``350.30`` → a Decimal."""
    s = re.sub(r"\s", "", text)  # NFKC already made no-break spaces plain ones
    if "," in s and "." in s:
        decimal = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+", s) else s.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def find_amount(text: str) -> Optional[Tuple[int, str]]:
    """The first money amount in ``text`` as ``(agorot > 0, currency)``, None when there is none."""
    clean = _clean(text)
    best = None
    for pattern in _AMOUNTS:
        m = pattern.search(clean)
        if m and (best is None or m.start() < best.start()):
            best = m
    if best is None:
        return None
    value = _number(best.group("num"))
    cur = best.group("cur")
    currency = _SYMBOLS.get(cur) or _CODES.get(cur.casefold()) or _CODES.get(cur)
    if value is None or value <= 0 or currency is None:
        return None
    return int((value * 100).to_integral_value()), currency


def find_card(text: str) -> Optional[str]:
    """The card's last 4 digits a notification names (``••8763``, ``בכרטיס 8763``, ``ending in 8763``)."""
    clean = _clean(text)
    for pattern in _CARD:
        m = pattern.search(clean)
        if m:
            return m.group(1)
    return None


def _merchant(text: str) -> Optional[str]:
    clean = _clean(text)
    for pattern in _MERCHANT:
        m = pattern.search(clean)
        if m:
            name = _MERCHANT_END.split(m.group("m"), maxsplit=1)[0].strip(" -:\"'״")[:80]
            if name and not name.isdigit():
                return name
    return None


def parse_wallet(title: str, text: str) -> Optional[Payment]:
    """A Google Wallet payment notification: the title is the merchant, the text
    the amount and card (``₪350.30 with Mastercard ••8763``, ``$12.00 with …``,
    ``350.30 ₪ באמצעות … ••8763``). None when the text holds no amount."""
    found = find_amount(text)
    if found is None:
        return None
    agorot, currency = found
    sign = 1 if _REFUND.search(_clean(text)) else -1
    return Payment(sign * agorot, currency, find_card(text), _clean(title)[:120] or None)


def parse_push(title: str, text: str) -> Optional[Payment]:
    """Best effort for a card / bank app's push: an amount with its currency
    and the card's last 4 digits or a merchant ("… ב-<merchant>", "at
    <merchant>"). None when it does not read as a payment (no amount, or a
    bill / statement notice)."""
    whole = f"{_clean(title)}\n{_clean(text)}"
    if _NOT_PAYMENT.search(whole):
        return None
    found = find_amount(whole)
    if found is None:
        return None
    card, merchant = find_card(whole), _merchant(whole)
    if card is None and merchant is None:
        return None
    sign = 1 if _REFUND.search(whole) else -1
    return Payment(sign * found[0], found[1], card, merchant)


# The packages whose notifications may become transactions: Google Wallet and the card / bank apps
# of the user's providers (``providers.payment_apps``); any other app's push the phone sends is only
# stored (an app can name itself anything, but not another app's package).
def parse(user: User, package: str, title: str, text: str) -> Optional[Payment]:
    """The parser for a package: Google Wallet's, the generic one for the user's providers'
    card / bank apps, None for any other package."""
    if package == WALLET_PACKAGE:
        return parse_wallet(title, text)
    return parse_push(title, text) if package in providers.payment_apps(user) else None


# --- Matching ---------------------------------------------------------------------------------

def similarity(conn: sqlite3.Connection, user: User, merchant_key: str, row: Mapping[str, Any]) -> float:
    """How alike a notification's merchant key and a scraped row's merchant are:
    2 for a learned alias, 1.5 for the same key, else the share of common
    words or of a common prefix (0..1)."""
    if not merchant_key:
        return 0.0
    keys = {k for k in (row.get("merchant_key") or "", ingest.merchant_key(row.get("merchant") or "")) if k}
    known = classify.alias(conn, user, merchant_key)
    if known and known["merchant_key"] in keys:
        return 2.0
    if merchant_key in keys:
        return 1.5
    best = 0.0
    words = set(merchant_key.split())
    flat = merchant_key.replace(" ", "")
    for key in keys:
        other = set(key.split())
        best = max(best, len(words & other) / max(1, min(len(words), len(other))))
        other_flat = key.replace(" ", "")
        n = 0
        while n < min(len(flat), len(other_flat)) and flat[n] == other_flat[n]:
            n += 1
        if n >= 3:
            best = max(best, n / min(len(flat), len(other_flat)))
    return best


def _moment(row: Mapping[str, Any]) -> Optional[datetime.datetime]:
    """A row's exact time, else noon of its purchase date (local)."""
    when = store.parse(row.get("occurred_at"))
    if when is not None:
        return when
    try:
        day = datetime.date.fromisoformat(row["purchase_date"])
    except (KeyError, TypeError, ValueError):
        return None
    return datetime.datetime.combine(day, datetime.time(12), clock.get_timezone()).astimezone(datetime.timezone.utc)


def best_match(conn: sqlite3.Connection, user: User, account_id: str, amount: int, purchase_date: str,
               merchant_key: str, occurred_at: Optional[str], *, at_signal: bool = False) -> Optional[Dict[str, Any]]:
    """The scraped row (pending or completed) of the account with exactly
    ``amount``, a purchase date within :data:`MATCH_DAYS`, not already matched
    to a signal: the most similar merchant, ties → the nearest time. At signal
    time only rows of that day or the day before, and within
    :data:`SCRAPED_HOURS` when both times are known."""
    day = datetime.date.fromisoformat(purchase_date)
    lo = day - datetime.timedelta(days=1 if at_signal else MATCH_DAYS)
    hi = day if at_signal else day + datetime.timedelta(days=MATCH_DAYS)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM money_transactions t WHERE t.user_id = ? AND t.account_id = ? AND t.source = 'scrape' "
        "AND t.amount = ? AND t.purchase_date BETWEEN ? AND ? AND NOT EXISTS (SELECT 1 FROM money_signals s "
        "WHERE s.user_id = t.user_id AND s.transaction_id = t.id)",
        (user.id, account_id, amount, lo.isoformat(), hi.isoformat()))]
    when = store.parse(occurred_at) or _moment({"purchase_date": purchase_date})
    if at_signal and when is not None:
        rows = [r for r in rows if not r.get("occurred_at")
                or abs((store.parse(r["occurred_at"]) - when).total_seconds()) <= SCRAPED_HOURS * 3600]
    if not rows:
        return None

    def rank(r: Dict[str, Any]) -> tuple:
        moment = _moment(r)
        distance = abs((moment - when).total_seconds()) if moment is not None and when is not None else 1e12
        return (-similarity(conn, user, merchant_key, r), distance)

    return min(rows, key=rank)


def _merge(conn: sqlite3.Connection, user: User, provisional: Mapping[str, Any], scraped: Mapping[str, Any]) -> None:
    """Fold a provisional row into its scraped twin: keep the exact time, the
    notification, a category the user gave (or any, when the scraped row has
    none) and the note; point its signals at the scraped row; learn the alias."""
    changes: Dict[str, Any] = {}
    if not scraped.get("occurred_at") and provisional.get("occurred_at"):
        changes["occurred_at"] = provisional["occurred_at"]
    if not scraped.get("notification_id") and provisional.get("notification_id"):
        changes["notification_id"] = provisional["notification_id"]
    if provisional.get("category") and scraped.get("category_source") not in classify.LOCKED_SOURCES:
        if provisional.get("category_source") == "user":
            changes.update(category=provisional["category"], category_source="user")
        elif not scraped.get("category"):
            changes.update(category=provisional["category"], category_source="memory")
    if provisional.get("note") and not scraped.get("note"):
        changes["note"] = provisional["note"]
    if changes:
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"UPDATE money_transactions SET {sets}, updated_at = ? WHERE id = ? AND user_id = ?",
                     (*changes.values(), store.now_iso(), scraped["id"], user.id))
    conn.execute("UPDATE money_signals SET transaction_id = ? WHERE user_id = ? AND transaction_id = ?",
                 (scraped["id"], user.id, provisional["id"]))
    conn.execute("DELETE FROM money_transactions WHERE id = ? AND user_id = ?", (provisional["id"], user.id))
    _learn(conn, user, provisional.get("merchant_key") or "", {**scraped, **changes})


def _learn(conn: sqlite3.Connection, user: User, alias_key: str, scraped: Mapping[str, Any]) -> None:
    target = scraped.get("merchant_key") or ""
    if not alias_key or not target or alias_key == target:
        return
    merchant = scraped.get("merchant")
    named = merchant if merchant and merchant != scraped.get("description") else None
    classify.learn_alias(conn, user, alias_key, target, named, scraped.get("category"))


def _today() -> datetime.date:
    return clock.now().date()


def mark_unmatched(conn: sqlite3.Connection, user: User) -> int:
    """Provisional rows of the user :data:`UNMATCHED_DAYS` days past their date → ``unmatched``."""
    cutoff = (_today() - datetime.timedelta(days=UNMATCHED_DAYS)).isoformat()
    return conn.execute("UPDATE money_transactions SET status = 'unmatched', updated_at = ? WHERE user_id = ? "
                        "AND source = 'notification' AND status = 'provisional' AND purchase_date <= ?",
                        (store.now_iso(), user.id, cutoff)).rowcount


def prune(conn: sqlite3.Connection, user: User) -> int:
    """Delete the user's signals older than :data:`KEEP_DAYS`."""
    cutoff = store.iso(store.utc_now() - datetime.timedelta(days=KEEP_DAYS))
    return conn.execute("DELETE FROM money_signals WHERE user_id = ? AND created_at < ?", (user.id, cutoff)).rowcount


def _card_account(conn: sqlite3.Connection, user: User, last4: Optional[str]) -> Optional[Dict[str, Any]]:
    if not last4:
        return None
    row = conn.execute("SELECT id, source_id FROM money_accounts WHERE user_id = ? AND kind = 'card' AND number = ? "
                       "ORDER BY hidden, created_at LIMIT 1", (user.id, last4)).fetchone()
    return dict(row) if row else None


def _reattach(conn: sqlite3.Connection, user: User) -> int:
    """Signals whose scraped row a later pull replaced (a pending row that came
    back completed under another key): match them again to keep the time."""
    since = store.iso(store.utc_now() - datetime.timedelta(days=MATCH_DAYS + UNMATCHED_DAYS))
    orphans = conn.execute(
        "SELECT s.* FROM money_signals s WHERE s.user_id = ? AND s.transaction_id IS NOT NULL AND s.parsed = 1 "
        "AND s.created_at >= ? AND NOT EXISTS (SELECT 1 FROM money_transactions t WHERE t.id = s.transaction_id)",
        (user.id, since)).fetchall()
    n = 0
    for s in orphans:
        account = _card_account(conn, user, s["card_last4"])
        when = store.parse(s["posted_at"])
        if account is None or when is None or s["amount"] is None:
            # scoped: a signal from the user's orphans (query above)
            conn.execute("UPDATE money_signals SET transaction_id = NULL WHERE id = ?", (s["id"],))
            continue
        local = when.astimezone(clock.get_timezone())
        match = best_match(conn, user, account["id"], s["amount"], local.date().isoformat(),
                           ingest.merchant_key(s["merchant"] or ""), local.isoformat(timespec="seconds"))
        if match is None:
            continue
        changes = {}
        if not match.get("occurred_at"):
            changes["occurred_at"] = local.isoformat(timespec="seconds")
        if not match.get("notification_id") and s["notification_id"]:
            changes["notification_id"] = s["notification_id"]
        if changes:
            sets = ", ".join(f"{k} = ?" for k in changes)
            conn.execute(f"UPDATE money_transactions SET {sets} WHERE id = ? AND user_id = ?",
                         (*changes.values(), match["id"], user.id))
        # scoped: a signal from the user's orphans (query above)
        conn.execute("UPDATE money_signals SET transaction_id = ? WHERE id = ?", (match["id"], s["id"]))
        n += 1
    return n


def reconcile(user: User, source_id: Optional[str] = None) -> Dict[str, int]:
    """Merge the provisional (and unmatched) rows of the source's accounts (all
    the user's accounts without one) into their scraped twins; then re-match
    signals whose row a pull replaced, mark old lonely provisional rows
    ``unmatched`` and drop old signals. Returns counts."""
    with store.db() as conn:
        sql = ("SELECT t.* FROM money_transactions t JOIN money_accounts a ON a.id = t.account_id "
               "WHERE t.user_id = ? AND t.source = 'notification' AND t.status IN ('provisional', 'unmatched')")
        args: List[Any] = [user.id]
        if source_id:
            sql += " AND a.source_id = ?"
            args.append(source_id)
        provisional = [dict(r) for r in conn.execute(sql + " ORDER BY t.occurred_at, t.id", args)]
        merged = 0
        for p in provisional:
            match = best_match(conn, user, p["account_id"], p["amount"], p["purchase_date"],
                               p.get("merchant_key") or "", p.get("occurred_at"))
            if match is not None:
                _merge(conn, user, p, match)
                merged += 1
        out = {"merged": merged, "reattached": _reattach(conn, user), "unmatched": mark_unmatched(conn, user),
               "pruned": prune(conn, user)}
    if merged:
        logger.info(f"Money: {merged} provisional transaction(s) of user {user.id} matched their scraped rows")
    return out


async def reconcile_hook(user: User, source_id: str, new_ids: List[str], changed_ids: List[str]) -> None:
    """The ``after_sync.on_transactions`` handler (registered before classification)."""
    await asyncio.to_thread(reconcile, user, source_id)


# --- Recording signals ------------------------------------------------------------------------

def _posted(value: Any) -> datetime.datetime:
    """Epoch millis (or seconds) / an ISO time (naive = local) → aware UTC;
    now when missing, malformed or in the future."""
    now = store.utc_now()
    when: Optional[datetime.datetime] = None
    try:
        if isinstance(value, bool):
            when = None
        elif isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            ts = float(value)
            when = datetime.datetime.fromtimestamp(ts / 1000.0 if ts > 1e11 else ts, datetime.timezone.utc)
        elif isinstance(value, str) and value.strip():
            dt = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            when = (dt if dt.tzinfo else dt.replace(tzinfo=clock.get_timezone())).astimezone(datetime.timezone.utc)
    except (ValueError, OverflowError, OSError):
        when = None
    if when is None or when > now + datetime.timedelta(minutes=5):
        return now
    return when


def _provisional(conn: sqlite3.Connection, user: User, signal_id: str, payment: Payment, posted: datetime.datetime,
                 notification_id: Optional[str]) -> Optional[str]:
    """Turn a stored signal into a provisional transaction (or link it to the
    payment it repeats / the scraped row already there). Returns the source id
    to sync sooner when a new provisional row was made."""
    if payment.amount >= 0 or payment.currency != "ILS":
        return None  # a refund, or a foreign amount whose shekel charge is not known yet
    account = _card_account(conn, user, payment.card_last4)
    if account is None:
        return None
    local = posted.astimezone(clock.get_timezone())
    occurred, day = local.isoformat(timespec="seconds"), local.date().isoformat()
    key = ingest.merchant_key(payment.merchant or "")
    window = datetime.timedelta(minutes=SAME_PAYMENT_MINUTES)
    for row in conn.execute("SELECT id, occurred_at, notification_id FROM money_transactions WHERE user_id = ? "
                            "AND account_id = ? AND source = 'notification' AND status = 'provisional' AND amount = ?",
                            (user.id, account["id"], payment.amount)).fetchall():
        when = store.parse(row["occurred_at"])
        if when is not None and abs(when - posted) <= window:  # the same payment from another app
            # scoped: the user's signal and provisional row (query above)
            conn.execute("UPDATE money_signals SET transaction_id = ? WHERE id = ?", (row["id"], signal_id))
            if notification_id and not row["notification_id"]:
                # scoped: the user's provisional row (query above)
                conn.execute("UPDATE money_transactions SET notification_id = ? WHERE id = ?",
                             (notification_id, row["id"]))
            return None
    scraped = best_match(conn, user, account["id"], payment.amount, day, key, occurred, at_signal=True)
    if scraped is not None:  # the sync was quicker than the notification
        changes = {k: v for k, v in (("occurred_at", occurred), ("notification_id", notification_id))
                   if v and not scraped.get(k)}
        if changes:
            sets = ", ".join(f"{k} = ?" for k in changes)
            conn.execute(f"UPDATE money_transactions SET {sets}, updated_at = ? WHERE id = ? AND user_id = ?",
                         (*changes.values(), store.now_iso(), scraped["id"], user.id))
        # scoped: the user's signal, stored for them before this call
        conn.execute("UPDATE money_signals SET transaction_id = ? WHERE id = ?", (scraped["id"], signal_id))
        _learn(conn, user, key, scraped)
        return None
    if local.date() <= _today() - datetime.timedelta(days=UNMATCHED_DAYS):
        return None  # too old to wait for: only recorded
    known = classify.memory(conn, user, key, kind="card", amount=payment.amount, sources=PROVISIONAL_MEMORY)
    category = known["category"]
    txn_id = uuid.uuid4().hex
    now = store.now_iso()
    description = ingest.scrub(payment.merchant or "")
    conn.execute(
        "INSERT INTO money_transactions (id, user_id, account_id, ext_key, source, status, occurred_at, purchase_date, "
        "amount, orig_amount, orig_currency, description, merchant, merchant_key, category, category_source, flow, "
        "notification_id, created_at, updated_at) VALUES (?, ?, ?, ?, 'notification', 'provisional', ?, ?, ?, ?, "
        "'ILS', ?, ?, ?, ?, ?, 'purchase', ?, ?, ?)",
        (txn_id, user.id, account["id"], f"n:{signal_id}", occurred, day, payment.amount, payment.amount,
         description or None, known["merchant"] or description or None, key or None, category,
         "memory" if category else None, notification_id, now, now))
    # scoped: the user's signal, stored for them before this call
    conn.execute("UPDATE money_signals SET transaction_id = ? WHERE id = ?", (txn_id, signal_id))
    return account["source_id"]


def _store(user: User, source: str, items: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    """Store signals (deduplicated) and act on the new parsed ones; then pull the cards' syncs."""
    accepted = duplicates = provisional = 0
    pull: Set[str] = set()
    now = store.now_iso()
    with store.db() as conn:
        for item in items:
            posted = _posted(item.get("posted_at"))
            package, title, text = item["package"], item["title"], item["text"]
            payment = parse(user, package, title, text)
            signal_id = uuid.uuid4().hex
            inserted = conn.execute(
                "INSERT OR IGNORE INTO money_signals (id, user_id, source, notification_id, key, package, title, text, "
                "posted_at, parsed, amount, currency, card_last4, merchant, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (signal_id, user.id, source, item.get("notification_id"), item["key"], package, title, text,
                 store.iso(posted), 1 if payment else 0, payment.amount if payment else None,
                 payment.currency if payment else None, payment.card_last4 if payment else None,
                 payment.merchant if payment else None, now)).rowcount
            if not inserted:
                duplicates += 1
                continue
            accepted += 1
            if payment is None:
                continue
            source_id = _provisional(conn, user, signal_id, payment, posted, item.get("notification_id"))
            if source_id is not None:
                provisional += 1
                pull.add(source_id)
        mark_unmatched(conn, user)
        prune(conn, user)
    for source_id in sorted(pull):
        schedule.request_soon(user, source_id, minutes=30, reason="signal")
    return {"accepted": accepted, "duplicates": duplicates, "provisional": provisional}


def _plain(value: Any, limit: int) -> str:
    return str(value).strip()[:limit] if value is not None else ""


def _text(value: Any, limit: int) -> str:
    """A notification's text as stored: codes redacted, ID / card / account numbers scrubbed."""
    return ingest.scrub(redact(_plain(value, 8000)))[:limit]


def record_push(user: User, notifications: Iterable[Any]) -> Dict[str, int]:
    """``POST /api/money/signals``: the card / bank apps' pushes the phone sends
    (``{key, package, app_name, title, text, posted_at}``, at most
    :data:`MAX_BATCH`). Each is stored once per ``(key, text)``; one without a
    package or without any text is dropped. Returns ``{accepted, duplicates,
    provisional}``."""
    items = []
    for raw in list(notifications or [])[:MAX_BATCH]:
        if not isinstance(raw, dict):
            continue
        package = _plain(raw.get("package"), 200)
        title, text = _text(raw.get("title"), 500), _text(raw.get("text"), 4000)
        if not package or not (title or text):
            continue
        items.append({"key": _plain(raw.get("key"), 500), "package": package, "title": title, "text": text,
                      "posted_at": raw.get("posted_at")})
    return _store(user, "phone_app", items)


def observe(user: User, notification_ids: List[str]) -> int:
    """The phone addon's notification observer: record the user's new Google
    Wallet notifications (only for users with ``money``). Returns how many were new."""
    if not connections.is_connected(user, config.ADDON) or not notification_ids:
        return 0
    ids = list(dict.fromkeys(notification_ids))
    rows: List[Dict[str, Any]] = []
    with store.db() as conn:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows += [dict(r) for r in conn.execute(
                f"SELECT id, device_key, package, title, text, posted_at FROM notifications WHERE user_id = ? "
                f"AND package = ? AND id IN ({','.join('?' * len(chunk))})", (user.id, WALLET_PACKAGE, *chunk))]
    if not rows:
        return 0
    items = [{"notification_id": r["id"], "key": r["device_key"] or "", "package": r["package"],
              "title": _text(r["title"], 500), "text": _text(r["text"], 4000), "posted_at": r["posted_at"]}
             for r in rows]
    return _store(user, "notification", items)["accepted"]
