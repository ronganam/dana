"""The bank worker's results in money's ingest format (docs/money-il.md, "What the worker returns";
docs/money.md, "Ingest format").

- Banks and cards (Hapoalim, Cal): the worker returns ``israeli-bank-scrapers``
  accounts, which are money's format already; a Cal installment gets its
  ``purchaseDate`` from ``rawTransaction.trnPurchaseDate`` (the library moves
  ``date`` to the payment's month).
- Psagot Trade (:func:`investment_account`): the site's own figures —
  ``summary`` (the balances view without its positions), ``holdings`` (one row
  per security, without names), ``names`` (security number → names, from the
  market table), ``activity`` (trades, deposits, dividends, taxes…) and
  ``history`` (the account's value per day). The site's ₪ figures are shekels
  (→ agorot); a holding's ``price`` is its value over its quantity, in its own
  currency, so the exchange's price units (agorot for Tel Aviv: a trade's
  ``Price``) never matter. Security numbers lose their leading zeros (the
  activity pads them to 8 digits, the holdings do not).
- Pluxee (:func:`meal_account`): ``budgets`` (the current cycle's:
  ``CreatioBudget``, ``CurrBudget``, ``CreationDate``, ``ExpirationDate``) and
  ``deals`` (every charge since the job's start date).

Text is scrubbed like transactions (``ingest.scrub``)."""

import datetime
import hashlib
import html
import re
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence

from core import clock
from addons.money import ingest
from addons.money.providers import Company

# --- Cal ------------------------------------------------------------------------------------


def card_account(company_id: str, account: Mapping[str, Any]) -> Dict[str, Any]:
    """A bank / card account as money stores it: Cal's installments get their ``purchaseDate``."""
    if company_id != "visaCal":
        return dict(account)
    txns = []
    for t in account.get("txns") or []:
        raw = t.get("rawTransaction") if isinstance(t, Mapping) and isinstance(t.get("rawTransaction"), dict) else {}
        if isinstance(t, Mapping) and t.get("type") == "installments" and raw.get("trnPurchaseDate"):
            t = {**t, "purchaseDate": raw["trnPurchaseDate"]}  # a naive local time
        txns.append(t)
    return {**account, "txns": txns}


# --- Psagot Trade ---------------------------------------------------------------------------

# Psagot's activity words → a kind (bank-worker's site: TradeOne's action names), first match wins.
_ACTION_KINDS = (
    ("קניה", "buy"), ("קנ.", "buy"), ("מכירה", "sell"), ("מכ.", "sell"), ("דיבידנד", "dividend"),
    ("ריבית", "interest"), ("התקבל", "deposit"), ("הפקד", "deposit"), ("משיכ", "withdrawal"), ("הועבר", "withdrawal"),
    ("מס", "tax"), ("פצול", "split"), ("אחוד", "split"), ("פידיון", "redemption"),
    ("פדיון", "redemption"), ("תשלום הוני", "redemption"), ("הטבה", "benefit"), ("פקיעה", "expiry"),
)
# The account's own cash (TradeOne books it as a security: "עו"ש מט"י", numbers 99xxxxxx).
_CASH_NAME = re.compile(r"עו[\"'״]{1,2}ש")
_CASH_NUMBER = re.compile(r"^99\d{6}$")


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
    """One holding in the ingest format: None without a security number. The balances view sends no
    name (``Key`` is empty); ``names`` (the market table's) fills it."""
    security = security_id(raw.get("EquityNumber"))
    if not security:
        return None
    quantity = _num(raw.get("OnlineNV"))
    value_orig = _num(raw.get("OnlineVL"))
    currency = str(raw.get("CurrencyCode") or "").strip() or None
    value = ingest.agorot(raw.get("OnlineNisVL"))
    if value is None and value_orig is not None and currency in (None, "ILS", "NIS", "₪"):
        value = ingest.agorot(value_orig)
    profit = ingest.agorot(raw.get("AveragePriceProfitLossNis"))
    price = value_orig / quantity if value_orig is not None and quantity else None
    name = _text(raw.get("Key")) or security_name(names or {}, security)
    return {"security": security, "name": name or None, "quantity": quantity,
            "price": round(price, 6) if price is not None else None, "currency": currency, "value": value,
            "cost": value - profit if value is not None and profit is not None else None, "profit": profit,
            "profit_pct": _num(raw.get("AveragePriceProfitLossPercentage")), "weight": _num(raw.get("OnlinePercentage")),
            "raw": dict(raw)}


def summary_details(summary: Mapping[str, Any], holdings: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The account's summary as kept in ``details``: value, cash, today's change, profit, holdings value."""
    value = ingest.agorot(summary.get("OnlineValue"))
    morning = ingest.agorot(summary.get("MorningValue"))
    held = [h["value"] for h in holdings if h.get("value") is not None]
    cash_by_currency = []
    for item in summary.get("OnlineCashByCurrency") or []:
        if isinstance(item, Mapping) and _num(item.get("Value")) is not None:
            cash_by_currency.append({"currency": str(item.get("Code") or ""), "amount": _num(item.get("Value"))})
    return {"value": value, "cash": ingest.agorot(summary.get("OnlineCash")),
            "day_change": value - morning if value is not None and morning is not None else None,
            "profit": ingest.agorot(summary.get("AveragePriceNisProfitLoss")),
            "profit_pct": _num(summary.get("AveragePriceNisProfitLossPercentage")),
            "holdings_value": sum(held) if held else None, "cash_by_currency": cash_by_currency,
            "currency": str(summary.get("CurrencyCode") or "") or None,
            "site": ingest.scrub_value({k: v for k, v in summary.items() if not isinstance(v, (dict, list))})}


def trade_rows(activity: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The activity in the ingest format (with ``ext_key``, unique within the pull); rows without a date
    and the cash side of trades (:func:`is_cash_leg`) are skipped; a row on the account's cash (a deposit,
    tax) has no security or name."""
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
        amount = ingest.agorot(raw.get("NisVL"))
        if amount is None:
            amount = ingest.agorot(raw.get("VL"))
        base = hashlib.sha1("|".join(str(x) for x in (day, action, security, quantity, price, amount)).encode()).hexdigest()[:16]
        key = f"psagot:{base}:{seen[base]}"
        seen[base] += 1
        name = _text(raw.get("Name")) or None
        if is_cash_account(security, name):  # its "name" is the cash account's: no security to show
            security = name = None
        out.append({"ext_key": key, "date": day.isoformat(), "action": action or None, "kind": action_kind(action),
                    "security": security, "name": name, "quantity": quantity, "price": price,
                    "currency": str(raw.get("CurrencyCode") or "").strip() or None, "amount": amount,
                    "commission": ingest.agorot(raw.get("Commission")), "tax": ingest.agorot(raw.get("TaxVL")),
                    "raw": dict(raw)})
    return out


def value_points(history: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    """``{date: agorot}`` from the site's daily values (the last of a day wins)."""
    out: Dict[str, int] = {}
    for item in history:
        if not isinstance(item, Mapping):
            continue
        day, value = parse_date(item.get("Date")), ingest.agorot(item.get("Value"))
        if day is not None and value is not None:
            out[day.isoformat()] = value
    return out


def investment_account(account: Mapping[str, Any]) -> Dict[str, Any]:
    """A Psagot account in the ingest format's investment shape."""
    summary = account.get("summary") if isinstance(account.get("summary"), Mapping) else {}
    names = account.get("names") if isinstance(account.get("names"), Mapping) else {}
    holdings = [h for h in (holding_row(r, names) for r in account.get("holdings") or [] if isinstance(r, Mapping)) if h]
    details = summary_details(summary, holdings)
    if details["value"] is None:
        details["value"] = ingest.agorot(account.get("balance"))
    return {"accountNumber": account.get("accountNumber"), "details": details, "holdings": holdings,
            "trades": trade_rows(account.get("activity") or []), "values": value_points(account.get("history") or [])}


# --- Pluxee ---------------------------------------------------------------------------------

ORDER_TYPES = {0: "restaurant", 1: "delivery", 2: "voucher"}


def parse_dmy(value: Any) -> Optional[datetime.date]:
    """``21/11/2026`` → a date; None otherwise."""
    try:
        return datetime.datetime.strptime(str(value or "").strip()[:10], "%d/%m/%Y").date()
    except ValueError:
        return None


def deal_row(raw: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """One charge in the ingest format: None without a deal id, a date or a price. Amounts in agorot:
    ``total`` the whole meal, ``card_paid`` what went on the user's own card (``otl_price``), ``budget_paid``
    the rest (from the employer's budget)."""
    deal_id = str(raw.get("deal_id") or "").strip()
    day = parse_dmy(raw.get("date"))
    total = ingest.agorot(raw.get("price"))
    if not deal_id or day is None or total is None:
        return None
    card_paid = ingest.agorot(raw.get("otl_price")) or 0
    occurred = None
    time = str(raw.get("time") or "").strip()
    try:
        hh, mm = (int(x) for x in time.split(":")[:2])
        occurred = datetime.datetime.combine(day, datetime.time(hh, mm), clock.get_timezone()).isoformat(timespec="seconds")
    except ValueError:
        pass
    order = raw.get("order_type")
    try:
        order_type = ORDER_TYPES.get(int(order), str(order))
    except (TypeError, ValueError):
        order_type = None
    return {
        "ext_key": f"pluxee:{deal_id}", "date": day.isoformat(), "occurred_at": occurred,
        "place": ingest.scrub(str(raw.get("rest_name") or "").strip()) or None,
        "place_id": str(raw.get("restaurant_id") or "").strip() or None, "order_type": order_type,
        "total": total, "card_paid": card_paid, "budget_paid": total - card_paid,
        "discount": ingest.agorot(raw.get("discount")) or None,
        "active": 0 if str(raw.get("is_active")) == "0" else 1,
        "budget_name": ingest.scrub(str(raw.get("rule_name") or "").strip()) or None,
        "raw": dict(raw),
    }


def budget_details(budgets: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The current cycle from the site's budgets: ``{amount, left, start, end}`` (agorot, ISO dates)."""
    rows = [b for b in budgets if isinstance(b, Mapping)]
    amount = sum(ingest.agorot(b.get("CreatioBudget")) or 0 for b in rows)
    left = sum(ingest.agorot(b.get("CurrBudget")) or 0 for b in rows)
    starts = [d for d in (parse_dmy(b.get("CreationDate")) for b in rows) if d]
    ends = [d for d in (parse_dmy(b.get("ExpirationDate")) for b in rows) if d]
    return {"amount": amount if rows else None, "left": left if rows else None,
            "start": min(starts).isoformat() if starts else None, "end": max(ends).isoformat() if ends else None}


def meal_account(account: Mapping[str, Any]) -> Dict[str, Any]:
    """A Pluxee card in the ingest format's meal shape."""
    return {"accountNumber": account.get("accountNumber"), "budget": budget_details(account.get("budgets") or []),
            "balance": ingest.agorot(account.get("balance")),
            "meals": [d for d in (deal_row(r) for r in account.get("deals") or [] if isinstance(r, Mapping)) if d]}


# --- Any company --------------------------------------------------------------------------


def accounts(company: Company, raw_accounts: Sequence[Any]) -> List[Dict[str, Any]]:
    """A job's ``accounts`` for ``company`` in money's ingest format."""
    out: List[Dict[str, Any]] = []
    for account in raw_accounts:
        if not isinstance(account, Mapping):
            continue
        if company.kind == "investment":
            out.append(investment_account(account))
        elif company.kind == "meal":
            out.append(meal_account(account))
        else:
            out.append(card_account(company.id, account))
    return out
