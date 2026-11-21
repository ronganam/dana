"""Investment accounts: storing and reading a portfolio (docs/money.md, "Investment accounts").

The worker returns Psagot's own figures for each account (bank-worker/README.md,
"Psagot Trade"): ``summary`` (the balances view without its positions),
``holdings`` (one row per security, without names), ``names`` (security number →
names, from the market table), ``activity`` (trades, deposits, dividends,
taxes…) and ``history`` (the account's value per day). :func:`store_account`
reads them for one source, in the ingest's transaction: the account upserted
(``balance`` = its value, ``details`` = the summary), the activity upserted by
key, its holdings replaced (a name from ``names``, else from the activity),
today's value and the daily values upserted. Money is kept in agorot: the site's
₪ figures are shekels; a holding's ``price`` is its value over its quantity, in
its own currency, so the exchange's price units (agorot for Tel Aviv: a trade's
``Price``) never matter. Security numbers lose their leading zeros (the activity
pads them to 8 digits, the holdings do not). Text is scrubbed like transactions
(``ingest.scrub``)."""

import datetime
import hashlib
import html
import json
import logging
import re
import uuid
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence

from core import clock
from core.users import User
from addons.money import companies, ingest, store

logger = logging.getLogger(__name__)

# Psagot's activity words → a kind (bank-worker's site: TradeOne's action names), first match wins.
_ACTION_KINDS = (
    ("קניה", "buy"), ("קנ.", "buy"), ("מכירה", "sell"), ("מכ.", "sell"), ("דיבידנד", "dividend"),
    ("ריבית", "interest"), ("התקבל", "deposit"), ("הפקד", "deposit"), ("משיכ", "withdrawal"), ("הועבר", "withdrawal"),
    ("מס", "tax"), ("פצול", "split"), ("אחוד", "split"), ("פידיון", "redemption"),
    ("פדיון", "redemption"), ("תשלום הוני", "redemption"), ("הטבה", "benefit"), ("פקיעה", "expiry"),
)
KINDS = ("buy", "sell", "dividend", "interest", "deposit", "withdrawal", "tax", "split", "redemption", "benefit",
         "expiry", "other")
# Money in or out of the account (not a gain or a loss): the value chart leaves them out of its change.
FLOW_KINDS = ("deposit", "withdrawal")
# The account's own cash (TradeOne books it as a security: "עו"ש מט"י", numbers 99xxxxxx).
_CASH_NAME = re.compile(r"עו[\"'״]{1,2}ש")
_CASH_NUMBER = re.compile(r"^99\d{6}$")
HISTORY_DAYS = 400


def _num(value: Any) -> Optional[float]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _text(value: Any) -> str:
    return ingest.scrub(html.unescape(str(value or "")).strip())


def action_kind(action: Any) -> str:
    """``קניה בבורסה`` → ``buy``; ``בטול דיבידנד`` → ``dividend`` (a cancellation keeps the kind);
    unknown words → ``other``."""
    text = str(action or "")
    if "קיעה" in text and "פקיעה" not in text:
        text = text.replace("קיעה", "")  # "קיעה" (ex-date) entries are about the action they name
    for word, kind in _ACTION_KINDS:
        if word in text:
            return kind
    return "other"


def parse_date(value: Any) -> Optional[datetime.date]:
    """A site date: ISO (``2026-11-17T00:00:00``), ``17/11/2026`` or ``17112026``; None otherwise."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text[:10])
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d%m%Y", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(text.split()[0], fmt).date()
        except ValueError:
            continue
    return None


def _agorot(value: Any) -> Optional[int]:
    return ingest.agorot(value)


def security_id(value: Any) -> Optional[str]:
    """A security number without its padding: ``01100007`` → ``1100007``; None when empty."""
    text = str(value or "").strip()
    if not text:
        return None
    stripped = text.lstrip("0")
    return stripped or "0"


def is_cash_leg(raw: Mapping[str, Any]) -> bool:
    """The cash account's side of a trade (``נע-קניה110000`` on ``עו"ש מט"י``, twice: the trade day and
    the value day): the trade itself is its own row, and these carry the whole amount in their tax field."""
    return str(raw.get("Action") or "").strip().startswith("נע-")


def is_cash_account(security: Optional[str], name: Any) -> bool:
    """Whether an activity row is on the account's cash (a deposit, tax, interest) rather than a security."""
    return bool(_CASH_NAME.search(str(name or ""))) or bool(security and _CASH_NUMBER.match(security))


def security_name(names: Mapping[str, Any], security: str) -> Optional[str]:
    """A security's name from the worker's ``names`` (``{number: {heb, eng, symbol}}``): Hebrew, else English."""
    entry = names.get(security) if isinstance(names, Mapping) else None
    if isinstance(entry, Mapping):
        for key in ("heb", "eng", "symbol"):
            text = _text(entry.get(key))
            if text:
                return text
    return None


def holding_row(raw: Mapping[str, Any], names: Optional[Mapping[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """One holding as stored (no ids): None without a security number. The balances view sends no
    name (``Key`` is empty); ``names`` (the market table's) fills it."""
    security = security_id(raw.get("EquityNumber"))
    if not security:
        return None
    quantity = _num(raw.get("OnlineNV"))
    value_orig = _num(raw.get("OnlineVL"))
    currency = str(raw.get("CurrencyCode") or "").strip() or None
    value = _agorot(raw.get("OnlineNisVL"))
    if value is None and value_orig is not None and currency in (None, "ILS", "NIS", "₪"):
        value = _agorot(value_orig)
    profit = _agorot(raw.get("AveragePriceProfitLossNis"))
    price = value_orig / quantity if value_orig is not None and quantity else None
    name = _text(raw.get("Key")) or security_name(names or {}, security)
    return {"security": security, "name": name or None, "quantity": quantity,
            "price": round(price, 6) if price is not None else None, "currency": currency, "value": value,
            "cost": value - profit if value is not None and profit is not None else None, "profit": profit,
            "profit_pct": _num(raw.get("AveragePriceProfitLossPercentage")), "weight": _num(raw.get("OnlinePercentage")),
            "raw": json.dumps(ingest.scrub_value(dict(raw)), ensure_ascii=False, sort_keys=True)}


def summary_details(summary: Mapping[str, Any], holdings: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The account's summary as kept in ``details``: value, cash, today's change, profit, holdings value."""
    value = _agorot(summary.get("OnlineValue"))
    morning = _agorot(summary.get("MorningValue"))
    held = [h["value"] for h in holdings if h.get("value") is not None]
    cash_by_currency = []
    for item in summary.get("OnlineCashByCurrency") or []:
        if isinstance(item, Mapping) and _num(item.get("Value")) is not None:
            cash_by_currency.append({"currency": str(item.get("Code") or ""), "amount": _num(item.get("Value"))})
    return {"value": value, "cash": _agorot(summary.get("OnlineCash")),
            "day_change": value - morning if value is not None and morning is not None else None,
            "profit": _agorot(summary.get("AveragePriceNisProfitLoss")),
            "profit_pct": _num(summary.get("AveragePriceNisProfitLossPercentage")),
            "holdings_value": sum(held) if held else None, "cash_by_currency": cash_by_currency,
            "currency": str(summary.get("CurrencyCode") or "") or None,
            "site": ingest.scrub_value({k: v for k, v in summary.items() if not isinstance(v, (dict, list))})}


def trade_rows(activity: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The activity as stored (with ``ext_key``, unique within the pull); rows without a date and the
    cash side of trades (:func:`is_cash_leg`) are skipped; a row on the account's cash (a deposit, tax)
    has no security or name."""
    out: List[Dict[str, Any]] = []
    seen: Counter = Counter()
    for raw in activity:
        if not isinstance(raw, Mapping) or is_cash_leg(raw):
            continue
        day = parse_date(raw.get("ActionDate"))
        if day is None:
            continue
        action = _text(raw.get("Action"))
        security = security_id(raw.get("EquityNumber"))
        quantity, price = _num(raw.get("NV")), _num(raw.get("Price"))
        amount = _agorot(raw.get("NisVL"))
        if amount is None:
            amount = _agorot(raw.get("VL"))
        base = hashlib.sha1("|".join(str(x) for x in (day, action, security, quantity, price, amount)).encode()).hexdigest()[:16]
        key = f"psagot:{base}:{seen[base]}"
        seen[base] += 1
        name = _text(raw.get("Name")) or None
        if is_cash_account(security, name):  # its "name" is the cash account's: no security to show
            security = name = None
        out.append({"ext_key": key, "date": day.isoformat(), "action": action or None, "kind": action_kind(action),
                    "security": security, "name": name, "quantity": quantity, "price": price,
                    "currency": str(raw.get("CurrencyCode") or "").strip() or None, "amount": amount,
                    "commission": _agorot(raw.get("Commission")), "tax": _agorot(raw.get("TaxVL")),
                    "raw": json.dumps(ingest.scrub_value(dict(raw)), ensure_ascii=False, sort_keys=True)})
    return out


def value_points(history: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    """``{date: agorot}`` from the site's daily values (the last of a day wins)."""
    out: Dict[str, int] = {}
    for item in history:
        if not isinstance(item, Mapping):
            continue
        day, value = parse_date(item.get("Date")), _agorot(item.get("Value"))
        if day is not None and value is not None:
            out[day.isoformat()] = value
    return out


def store_account(conn, user: User, source_id: str, company: companies.Company, account: Mapping[str, Any],
                  now: str) -> Optional[str]:
    """Store one investment account of a sync (inside the ingest's transaction); returns its id
    (None for an account without a number)."""
    number = str(account.get("accountNumber") or "").strip()
    if not number:
        logger.warning(f"Money ingest for source {source_id}: an investment account without a number was skipped")
        return None
    summary = account.get("summary") if isinstance(account.get("summary"), Mapping) else {}
    names = account.get("names") if isinstance(account.get("names"), Mapping) else {}
    holdings = [h for h in (holding_row(r, names) for r in account.get("holdings") or [] if isinstance(r, Mapping)) if h]
    details = summary_details(summary, holdings)
    value = details["value"]
    if value is None:
        value = _agorot(account.get("balance"))
        details["value"] = value
    row = conn.execute("SELECT id FROM money_accounts WHERE user_id = ? AND company = ? AND number = ?",
                       (user.id, company.id, number)).fetchone()
    payload = json.dumps(details, ensure_ascii=False, sort_keys=True)
    if row is None:
        account_id = uuid.uuid4().hex
        conn.execute("INSERT INTO money_accounts (id, user_id, source_id, company, kind, number, balance, balance_at, "
                     "details, created_at, updated_at) VALUES (?, ?, ?, ?, 'investment', ?, ?, ?, ?, ?, ?)",
                     (account_id, user.id, source_id, company.id, number, value, now if value is not None else None,
                      payload, now, now))
    else:
        account_id = row["id"]
        # scoped: the account found by user_id above
        conn.execute("UPDATE money_accounts SET source_id = ?, kind = 'investment', balance = ?, balance_at = ?, "
                     "details = ?, updated_at = ? WHERE id = ?",
                     (source_id, value, now if value is not None else None, payload, now, account_id))
    for t in trade_rows(account.get("activity") or []):
        conn.execute(
            "INSERT INTO money_trades (id, user_id, account_id, ext_key, date, action, kind, security, name, quantity, "
            "price, currency, amount, commission, tax, raw, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account_id, ext_key) DO UPDATE SET kind = excluded.kind, name = COALESCE(excluded.name, name), "
            "raw = excluded.raw, updated_at = excluded.updated_at",
            (uuid.uuid4().hex, user.id, account_id, t["ext_key"], t["date"], t["action"], t["kind"], t["security"],
             t["name"], t["quantity"], t["price"], t["currency"], t["amount"], t["commission"], t["tax"], t["raw"],
             now, now))
    # A holding the market table did not name takes the name of its latest activity row.
    for h in holdings:
        if not h["name"]:
            r = conn.execute("SELECT name FROM money_trades WHERE account_id = ? AND user_id = ? AND security = ? "
                             "AND name IS NOT NULL ORDER BY date DESC LIMIT 1", (account_id, user.id, h["security"])).fetchone()
            h["name"] = r["name"] if r else None
    conn.execute("DELETE FROM money_holdings WHERE account_id = ? AND user_id = ?", (account_id, user.id))
    for h in holdings:
        conn.execute("INSERT OR REPLACE INTO money_holdings (id, user_id, account_id, security, name, quantity, price, "
                     "currency, value, cost, profit, profit_pct, weight, raw, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (uuid.uuid4().hex, user.id, account_id, h["security"], h["name"], h["quantity"], h["price"],
                      h["currency"], h["value"], h["cost"], h["profit"], h["profit_pct"], h["weight"], h["raw"], now))
    points = value_points(account.get("history") or [])
    if value is not None:
        points[clock.now().date().isoformat()] = value
    for day, amount in points.items():
        conn.execute("INSERT INTO money_account_values (user_id, account_id, date, value) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(account_id, date) DO UPDATE SET value = excluded.value",
                     (user.id, account_id, day, amount))
    return account_id


# --- Reads ------------------------------------------------------------------------------------

_HOLDING_FIELDS = ("security", "name", "quantity", "price", "currency", "value", "cost", "profit", "profit_pct",
                   "weight")
_TRADE_FIELDS = ("id", "date", "action", "kind", "security", "name", "quantity", "price", "currency", "amount",
                 "commission", "tax")


def _details(account: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        out = json.loads(account.get("details") or "{}")
    except ValueError:
        out = {}
    return out if isinstance(out, dict) else {}


def investment_accounts(user: User) -> List[Dict[str, Any]]:
    """The user's visible investment accounts as the overview lists them: ``{id, label, number, company,
    company_name, value, value_at, cash, day_change, profit, profit_pct}``."""
    out = []
    for a in store.list_accounts(user):
        if a["kind"] != "investment" or a.get("hidden"):
            continue
        d = _details(a)
        out.append({"id": a["id"], "label": store.account_label(a["company"], a["kind"], a["number"], a.get("label")),
                    "number": store.account_payload(a)["number"], "company": a["company"],
                    "company_name": companies.name_of(a["company"]), "value": a.get("balance"),
                    "value_at": a.get("balance_at"), "cash": d.get("cash"), "day_change": d.get("day_change"),
                    "profit": d.get("profit"), "profit_pct": d.get("profit_pct")})
    return out


def portfolio(user: User, days: int = 365, trades: int = 50) -> Dict[str, Any]:
    """Everything the Investments view shows.

    Returns:
        ``{total {value, cash, day_change, profit, cost, as_of}, accounts: [{id, label, number, company_name,
        value, value_at, cash, day_change, profit, profit_pct, holdings: [{security, name, quantity, price,
        currency, value, cost, profit, profit_pct, weight}] (largest first), history: [{date, value}]}],
        history: [{date, value}] (all accounts, days with every account known), trades: [{id, account_id,
        date, action, kind, security, name, quantity, price, currency, amount, commission, tax}] (newest
        first), flows: [{date, amount}] (deposits +, withdrawals −, within ``days``, oldest first),
        allocation: [{name, value, share}] (the largest holdings, the rest as one)}``; amounts in agorot.
    """
    accounts = investment_accounts(user)
    ids = [a["id"] for a in accounts]
    since = (clock.now().date() - datetime.timedelta(days=max(1, min(int(days), HISTORY_DAYS)))).isoformat()
    holdings: Dict[str, List[Dict[str, Any]]] = {i: [] for i in ids}
    series: Dict[str, List[Dict[str, Any]]] = {i: [] for i in ids}
    trade_list: List[Dict[str, Any]] = []
    flows: List[Dict[str, Any]] = []
    if ids:
        marks = ",".join("?" * len(ids))
        with store.db() as conn:
            for r in conn.execute(f"SELECT * FROM money_holdings WHERE user_id = ? AND account_id IN ({marks}) "
                                  "ORDER BY COALESCE(value, 0) DESC, security", (user.id, *ids)):
                holdings[r["account_id"]].append({k: r[k] for k in _HOLDING_FIELDS})
            for r in conn.execute(f"SELECT account_id, date, value FROM money_account_values WHERE user_id = ? "
                                  f"AND account_id IN ({marks}) AND date >= ? ORDER BY date", (user.id, *ids, since)):
                series[r["account_id"]].append({"date": r["date"], "value": r["value"]})
            for r in conn.execute(f"SELECT * FROM money_trades WHERE user_id = ? AND account_id IN ({marks}) "
                                  "ORDER BY date DESC, created_at DESC LIMIT ?", (user.id, *ids, max(1, int(trades)))):
                trade_list.append({"account_id": r["account_id"], **{k: r[k] for k in _TRADE_FIELDS}})
            kinds = ",".join("?" * len(FLOW_KINDS))
            for r in conn.execute(f"SELECT date, amount FROM money_trades WHERE user_id = ? AND account_id IN ({marks}) "
                                  f"AND kind IN ({kinds}) AND amount IS NOT NULL AND date >= ? ORDER BY date",
                                  (user.id, *ids, *FLOW_KINDS, since)):
                flows.append({"date": r["date"], "amount": r["amount"]})
    for a in accounts:
        a["holdings"] = holdings[a["id"]]
        a["history"] = series[a["id"]]
    # The total per day: only days on which every account has a value (no dips where one is missing).
    by_day: Dict[str, Dict[str, int]] = {}
    for a in accounts:
        for p in a["history"]:
            by_day.setdefault(p["date"], {})[a["id"]] = p["value"]
    history = [{"date": d, "value": sum(v.values())} for d, v in sorted(by_day.items()) if len(v) == len(accounts)]

    def total(key: str) -> Optional[int]:
        values = [a.get(key) for a in accounts if a.get(key) is not None]
        return sum(values) if values else None

    every = [h for a in accounts for h in a["holdings"]]
    costs = [h["cost"] for h in every if h.get("cost") is not None]
    value = total("value")
    allocation: List[Dict[str, Any]] = []
    positive = sorted((h for h in every if (h.get("value") or 0) > 0), key=lambda h: -h["value"])
    if value:
        for h in positive[:6]:
            allocation.append({"name": h["name"] or h["security"], "value": h["value"], "share": h["value"] / value})
        rest = sum(h["value"] for h in positive[6:])
        cash = total("cash") or 0
        if rest:
            allocation.append({"name": "Other holdings", "value": rest, "share": rest / value})
        if cash > 0:
            allocation.append({"name": "Cash", "value": cash, "share": cash / value})
    stamps = [a["value_at"] for a in accounts if a.get("value_at")]
    return {"total": {"value": value, "cash": total("cash"), "day_change": total("day_change"), "profit": total("profit"),
                      "cost": sum(costs) if costs else None, "as_of": max(stamps) if stamps else None},
            "accounts": accounts, "history": history, "trades": trade_list, "flows": flows, "allocation": allocation}
