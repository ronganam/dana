"""The Money dashboards (docs/money.md, "Dashboards").

- :func:`spend_kinds`: what each spending row is for the numbers. **fixed**: a
  loan payment, or a merchant of a monthly / bimonthly series (the mortgage,
  the municipality, the phone). **yearly**: :func:`insights.detect_yearly`.
  **one_off**: the user said so, or a purchase of at least
  :data:`ONE_OFF_MIN_AGOROT` that is neither of those (an installment plan
  counts at its total; a refund of a one-off is one too). **everyday**: the
  rest. The user's ``one_off`` / ``yearly`` marks win.
- :func:`cashflow`: per month, income, spending by kind, money set aside
  (investments) and the net.
- :func:`balance_history`: the bank balance per day, rebuilt backwards from
  the last balance through the bank's own rows.
- :func:`forecast`: the bank balance day by day ahead, **one-offs left out**:
  recurring income and bank debits at their next dates, yearly charges on
  theirs, and each card bill on its day. A bill holds what is already on it,
  the remaining payments of installment plans, the recurring and yearly
  card charges falling in its cycle, and everyday spending at its usual
  rate: the mean of the last :data:`TYPICAL_MONTHS` whole months without the
  busiest and the quietest, with a band from the quietest to the busiest.
  A payment due on a Saturday reaches the bank on Sunday.
- :func:`trends`, :func:`merchants`, :func:`everyday_pace` and :func:`month_insights` (the rules in
  :func:`insights_for`, plus the cards registered with :func:`on_insights` for the month in progress).

Everything is computed from a :class:`Book`: the user's rows of
``store.TXN_SELECT`` with the recurring and yearly charges and the kinds,
built once per request by :func:`load` (pure :func:`build` for tests and
backtests).
"""

import calendar
import datetime
import logging
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from core.users import User
from addons.money import categories, insights, periods, store

logger = logging.getLogger(__name__)

# A purchase of at least this much that is not fixed or yearly is a one-off (a car, furniture, a flight).
ONE_OFF_MIN_AGOROT = 200_000
# A refund this soon after a one-off from the same merchant is part of it.
ONE_OFF_REFUND_DAYS = 120
# "Typical" = the median of up to this many whole months before (months with data).
TYPICAL_MONTHS = 6
FORECAST_DAYS = 180
FORECAST_MAX_DAYS = 366
# A card purchase is charged on the first billing day at least this many days later (a card's
# cycle commonly closes two days before the charge day).
CARD_CUTOFF_DAYS = 2
DEFAULT_BILLING_DAY = 10
# A recurring payment this late is still expected (the next working day, a slow posting).
LATE_DAYS = {"weekly": 3, "monthly": 10, "bimonthly": 20, "yearly": 30}
# Movers: a category this far from typical, both in agorot and as a share of typical.
MOVER_MIN_AGOROT = 15_000
MOVER_MIN_SHARE = 0.25
# A month in progress is projected ("on track for") only after this many days.
ON_TRACK_MIN_DAYS = 7
# The forecast low point warns below this balance.
LOW_BALANCE_AGOROT = 200_000
# "Coming up": the next this many days.
SOON_DAYS = 45
KINDS = ("fixed", "yearly", "everyday", "one_off")


@dataclass
class Book:
    today: datetime.date
    rows: List[Dict[str, Any]]            # countable rows, oldest first
    recurring: List[Dict[str, Any]]       # insights.detect_recurring
    yearly: List[Dict[str, Any]]          # insights.detect_yearly
    kinds: Dict[str, str]                 # spending row id → kind
    first: Optional[datetime.date]        # the first row's date

    @property
    def charges(self) -> List[Dict[str, Any]]:
        return [i for i in self.recurring if i["direction"] == "out"]

    @property
    def income(self) -> List[Dict[str, Any]]:
        return [i for i in self.recurring if i["direction"] == "in"]


def _date(value: str) -> datetime.date:
    return datetime.date.fromisoformat(value[:10])


def flow(r: Dict[str, Any]) -> str:
    return r.get("eff_flow") or r.get("flow") or ""


def is_spending(r: Dict[str, Any]) -> bool:
    return flow(r) in categories.SPENDING_FLOWS


def spend_kinds(rows: Sequence[Dict[str, Any]], fixed: Set[str], yearly_ids: Set[str]) -> Dict[str, str]:
    """Row id → ``fixed`` | ``yearly`` | ``one_off`` | ``everyday`` for the spending rows of ``rows``
    (``fixed``: merchant keys of monthly / bimonthly series; ``yearly_ids``: rows of yearly charges)."""
    out: Dict[str, str] = {}
    one_off_at: Dict[str, List[datetime.date]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["purchase_date"], r["id"])):
        if not is_spending(r):
            continue
        key = r.get("merchant_key") or ""
        if r.get("one_off") == 1:
            kind = "one_off"
        elif r["id"] in yearly_ids or r.get("yearly") == 1:
            kind = "yearly"
        elif flow(r) == "loan" or (key and key in fixed):
            kind = "fixed"
        elif r.get("one_off") == 0:
            kind = "everyday"
        elif r["amount"] < 0 and insights.plan_total(r) >= ONE_OFF_MIN_AGOROT:
            kind = "one_off"
        elif r["amount"] > 0 and key and any(0 <= (_date(r["purchase_date"]) - d).days <= ONE_OFF_REFUND_DAYS
                                            for d in one_off_at.get(key, [])):
            kind = "one_off"
        else:
            kind = "everyday"
        if kind == "one_off" and r["amount"] < 0 and key:
            one_off_at[key].append(_date(r["purchase_date"]))
        out[r["id"]] = kind
    return out


def build(rows: Iterable[Dict[str, Any]], today: datetime.date) -> Book:
    """A :class:`Book` from rows of ``store.TXN_SELECT`` (any order)."""
    countable = sorted((r for r in rows if r["status"] != "unmatched" and r["amount"]),
                       key=lambda r: (r["purchase_date"], r.get("occurred_at") or "", r["id"]))
    found = insights.detect_recurring(countable, today)
    charges = [i for i in found if i["direction"] == "out"]
    fixed = insights.fixed_keys({"items": charges})
    yearly = insights.detect_yearly([r for r in countable if r["amount"] < 0], today, fixed)
    yearly_ids = {i for y in yearly for i in y["row_ids"]}
    first = _date(countable[0]["purchase_date"]) if countable else None
    return Book(today, countable, found, yearly, spend_kinds(countable, fixed, yearly_ids), first)


def load(user: User, today: Optional[datetime.date] = None) -> Book:
    """The user's :class:`Book` (rows of the last ``insights.HISTORY_DAYS``)."""
    today = today or periods.today()
    since = (today - datetime.timedelta(days=insights.HISTORY_DAYS)).isoformat()
    rows = store.txn_rows(user, "t.purchase_date >= ? AND t.status != 'unmatched' AND t.amount != 0", (since,),
                          order="t.purchase_date, t.id")
    return build(rows, today)


# --- Months ---------------------------------------------------------------------------------

def _month_key(d: datetime.date) -> str:
    return d.strftime("%Y-%m")


def _month_label(d: datetime.date) -> str:
    return f"{d.strftime('%b')} {d.year}"


def whole_months(book: Book, before: datetime.date, n: int = TYPICAL_MONTHS) -> List[datetime.date]:
    """Up to ``n`` whole months with data before the month of ``before`` (newest first): a
    month counts from the first one that starts on or after the first row's date."""
    if book.first is None:
        return []
    out = []
    m = periods.add_months(periods.month_start(before), -1)
    while len(out) < n and m >= book.first:
        out.append(m)
        m = periods.add_months(m, -1)
    return out


def _median(values: Sequence[float]) -> Optional[int]:
    return int(round(statistics.median(values))) if values else None


def _quartiles(values: Sequence[float]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    if not values:
        return None, None, None
    if len(values) == 1:
        v = int(round(values[0]))
        return v, v, v
    q = statistics.quantiles(values, n=4, method="inclusive")
    return int(round(q[0])), int(round(statistics.median(values))), int(round(q[2]))


def _sums(book: Book, start: datetime.date, end_exclusive: datetime.date) -> Dict[str, int]:
    """Agorot in [start, end): ``income``, each spend kind, ``spent``, ``set_aside``."""
    out = {"income": 0, "spent": 0, "set_aside": 0, **{k: 0 for k in KINDS}}
    lo, hi = start.isoformat(), end_exclusive.isoformat()
    for r in book.rows:
        if not lo <= r["purchase_date"] < hi:
            continue
        f = flow(r)
        if f in categories.INCOME_FLOWS:
            out["income"] += r["amount"]
        elif f == "investment":
            out["set_aside"] -= r["amount"]
        kind = book.kinds.get(r["id"])
        if kind:
            out[kind] -= r["amount"]
            out["spent"] -= r["amount"]
    return out


def cashflow(book: Book, n: int = 12, end: Optional[str] = None) -> Dict[str, Any]:
    """Money in and out per month: ``{months: [{month, label, complete, income, fixed,
    yearly, everyday, one_off, spent, set_aside, net, savings_rate}] (oldest first),
    totals {income, spent, one_off, set_aside, net}, typical {income, fixed, yearly,
    everyday, spent, net} (medians of the whole months shown)}``."""
    n = max(1, min(int(n), 36))
    last = periods.month_start(_date(end + "-01") if end else book.today)
    out = []
    for k in range(n - 1, -1, -1):
        m = periods.add_months(last, -k)
        s = _sums(book, m, periods.month_end_exclusive(m))
        complete = periods.month_end_exclusive(m) <= book.today and book.first is not None and m >= book.first
        net = s["income"] - s["spent"]
        out.append({"month": _month_key(m), "label": _month_label(m), "complete": complete, **s, "net": net,
                    "savings_rate": round(net / s["income"], 3) if s["income"] > 0 else None})
    whole = [m for m in out if m["complete"]]
    totals = {k: sum(m[k] for m in out) for k in ("income", "spent", "one_off", "set_aside", "net")}
    typical = {k: _median([m[k] for m in whole]) for k in ("income", "fixed", "yearly", "everyday", "spent", "net")}
    return {"months": out, "totals": totals, "typical": typical}


# --- Balance --------------------------------------------------------------------------------

def _banks(accounts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [a for a in accounts if a["kind"] == "bank" and not a.get("hidden") and a.get("balance") is not None]


def balance_at(book: Book, accounts: Sequence[Dict[str, Any]]) -> Optional[Tuple[int, datetime.date]]:
    """The banks' balance together and the day it is from (the oldest of theirs), None without one."""
    banks = _banks(accounts)
    if not banks:
        return None
    day = min(store.local_date(b.get("balance_at")) or book.today for b in banks)
    return sum(b["balance"] for b in banks), day


def balance_history(book: Book, accounts: Sequence[Dict[str, Any]], days: int = 365) -> List[Dict[str, Any]]:
    """``[{date, balance}]``: the banks' balance at the end of each day, oldest first, rebuilt
    backwards from their balance through their rows (at most ``days`` days, from the first row)."""
    now = balance_at(book, accounts)
    if now is None:
        return []
    balance, day = now
    ids = {b["id"] for b in _banks(accounts)}
    per_day: Dict[str, int] = defaultdict(int)
    for r in book.rows:
        if r.get("account_id") in ids:
            per_day[r["purchase_date"]] += r["amount"]
    first = min((_date(d) for d in per_day), default=day)
    start = max(first - datetime.timedelta(days=1), day - datetime.timedelta(days=days))
    out = []
    d = day
    while d >= start:
        out.append({"date": d.isoformat(), "balance": balance})
        balance -= per_day.get(d.isoformat(), 0)
        d -= datetime.timedelta(days=1)
    return out[::-1]


# --- Forecast -------------------------------------------------------------------------------

def _billing_day(card: Dict[str, Any], rows: Sequence[Dict[str, Any]]) -> int:
    if card.get("next_charge_date"):
        return _date(card["next_charge_date"]).day
    days = Counter(_date(r["charge_date"]).day for r in rows
                   if r.get("account_id") == card["id"] and r.get("charge_date") and (r.get("installment_total") or 0) <= 1)
    return days.most_common(1)[0][0] if days else DEFAULT_BILLING_DAY


def bill_date(purchase: datetime.date, billing_day: int) -> datetime.date:
    """The card bill a purchase on ``purchase`` is charged in."""
    earliest = purchase + datetime.timedelta(days=CARD_CUTOFF_DAYS)
    m = periods.month_start(earliest)
    while True:
        d = m.replace(day=min(billing_day, calendar.monthrange(m.year, m.month)[1]))
        if d >= earliest:
            return d
        m = periods.add_months(m, 1)


def bank_day(d: datetime.date) -> datetime.date:
    """The day a payment due on ``d`` reaches the bank account: a Saturday's on Sunday."""
    return d + datetime.timedelta(days=1) if d.weekday() == 5 else d


def _occurrences(item: Dict[str, Any], as_of: datetime.date, end: datetime.date) -> List[datetime.date]:
    """The dates in (as_of, end] a recurring item is expected; one a little late moves to tomorrow."""
    cadence = item["cadence"]
    d = _date(item["next_date"])
    late = datetime.timedelta(days=LATE_DAYS.get(cadence, 10))
    while d < as_of - late:
        d = insights._next(cadence, d)
    out = []
    if d <= as_of:
        out.append(as_of + datetime.timedelta(days=1))
        d = insights._next(cadence, d)
    while d <= end:
        out.append(d)
        d = insights._next(cadence, d)
    return [x for x in out if x <= end]


def _rate(values: Sequence[int]) -> Optional[int]:
    """The monthly rate a forecast adds up: the mean of the months, without the busiest
    and the quietest from five months up (a median under-counts: busy months are further
    above it than quiet months are below)."""
    if not values:
        return None
    v = sorted(values)
    if len(v) >= 5:
        v = v[1:-1]
    return int(round(sum(v) / len(v)))


def everyday_months(book: Book, before: datetime.date) -> Dict[str, Any]:
    """Everyday spending of the whole months before ``before``'s month: ``{months: [{month,
    card, bank}], card: (low, rate, high), bank: rate}`` (an everyday installment plan
    counts at its total in the month it was bought; low / high: the quietest and the
    busiest month — backtested, a narrower band missed most months; rate: :func:`_rate`)."""
    months = whole_months(book, before)
    card: Dict[str, int] = {_month_key(m): 0 for m in months}
    bank: Dict[str, int] = {_month_key(m): 0 for m in months}
    seen: Set[Tuple[Any, ...]] = set()
    for r in book.rows:
        key = r["purchase_date"][:7]
        if key not in card or book.kinds.get(r["id"]) != "everyday":
            continue
        pk = insights.plan_key(r)
        if pk in seen:
            continue
        seen.add(pk)
        amount = insights.plan_total(r) if r["amount"] < 0 else -r["amount"]
        (card if r.get("account_kind") == "card" else bank)[key] += amount
    rows = [{"month": k, "card": card[k], "bank": bank[k]} for k in sorted(card)]
    values = list(card.values())
    return {"months": rows, "card": (min(values) if values else None, _rate(values), max(values) if values else None),
            "bank": _rate(list(bank.values())) or 0}


def _plans_left(rows: Sequence[Dict[str, Any]], as_of: datetime.date) -> List[Dict[str, Any]]:
    """Installment plans with payments still to come after ``as_of``: ``{account_id, merchant,
    amount (a payment), dates, total, left (agorot)}``."""
    plans: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for r in rows:
        if r.get("account_kind") != "card" or (r.get("installment_total") or 0) <= 1 or not r.get("charge_date"):
            continue
        pk = insights.plan_key(r)
        if pk not in plans or (r.get("installment_number") or 0) > (plans[pk].get("installment_number") or 0):
            plans[pk] = r
    out = []
    for r in plans.values():
        left = r["installment_total"] - (r.get("installment_number") or 0)
        last = _date(r["charge_date"])
        dates = [periods.add_months(last, i) for i in range(1, left + 1)]
        dates = [d for d in dates if d > as_of]
        item = store.txn_item(r)
        if dates or last > as_of:
            out.append({"account_id": r["account_id"], "merchant": item["merchant"], "amount": abs(r["amount"]),
                        "dates": [d.isoformat() for d in dates], "total": insights.plan_total(r),
                        "left": abs(r["amount"]) * len(dates), "number": r.get("installment_number"),
                        "of": r["installment_total"]})
    out.sort(key=lambda p: -p["left"])
    return out


def _main_card(book: Book, cards: Sequence[Dict[str, Any]], as_of: datetime.date) -> Optional[Dict[str, Any]]:
    """The card everyday spending is charged to: the most of it in the last 60 days."""
    if not cards:
        return None
    since = (as_of - datetime.timedelta(days=60)).isoformat()
    spent: Dict[str, int] = defaultdict(int)
    for r in book.rows:
        if r["purchase_date"] >= since and book.kinds.get(r["id"]) == "everyday":
            spent[r.get("account_id") or ""] -= r["amount"]
    return max(cards, key=lambda c: (spent.get(c["id"], 0), c.get("next_charge") or 0))


def project(book: Book, accounts: Sequence[Dict[str, Any]], balance: int, as_of: datetime.date,
            days: int = FORECAST_DAYS) -> Dict[str, Any]:
    """The forecast from ``balance`` at the end of ``as_of`` (pure: :func:`forecast` and the backtests).

    Returns ``{as_of, end, balance, series: [{date, balance, low, high}], events: [{date, kind
    (income | fixed | yearly | card_bill), label, amount (signed agorot), estimated, parts?,
    items?, range?, account_id?}], low_point {date, balance} (the lowest after today),
    soon_low {date, balance, days} (the lowest in the next :data:`SOON_DAYS`), rates {card {low,
    typical, high, months}, bank}, plans [_plans_left], yearly [upcoming yearly charges],
    left_out [the biggest one-offs of the last 12 months], left_out_total (all of them, net of
    their refunds)}``."""
    days = max(7, min(int(days), FORECAST_MAX_DAYS))
    end = as_of + datetime.timedelta(days=days)
    cards = [a for a in accounts if a["kind"] == "card" and not a.get("hidden")]
    by_id = {c["id"]: c for c in cards}
    billing = {c["id"]: _billing_day(c, book.rows) for c in cards}
    main = _main_card(book, cards, as_of)
    rates = everyday_months(book, as_of)
    low_rate, typical_rate, high_rate = rates["card"]
    bank_rate = rates["bank"]
    bank_events: Dict[datetime.date, List[Dict[str, Any]]] = defaultdict(list)
    bills: Dict[Tuple[str, datetime.date], Dict[str, Any]] = {}

    def bill(card_id: str, d: datetime.date) -> Optional[Dict[str, Any]]:
        if d > end:
            return None
        key = (card_id, d)
        if key not in bills:
            bills[key] = {"parts": {"known": 0, "installments": 0, "fixed": 0, "yearly": 0, "everyday": 0},
                          "everyday_low": 0, "everyday_high": 0, "items": []}
        return bills[key]

    # What the cards already know: rows charged after as_of, and the card company's next bill.
    plans = _plans_left(book.rows, as_of)
    for c in cards:
        known: Dict[datetime.date, int] = defaultdict(int)
        for r in book.rows:
            if r.get("account_id") == c["id"] and r.get("charge_date") and _date(r["charge_date"]) > as_of:
                known[_date(r["charge_date"])] -= r["amount"]
        if c.get("next_charge") and c.get("next_charge_date") and _date(c["next_charge_date"]) > as_of:
            d = _date(c["next_charge_date"])
            known[d] = max(known[d], c["next_charge"])
        for d, amount in known.items():
            b = bill(c["id"], d)
            if b is not None and amount:
                b["parts"]["known"] += amount
    for p in plans:
        if p["account_id"] not in by_id:
            continue
        for d in p["dates"]:
            b = bill(p["account_id"], _date(d))
            if b is not None:
                b["parts"]["installments"] += p["amount"]
                b["items"].append({"label": p["merchant"], "amount": p["amount"], "kind": "installment"})
    # Recurring income and charges at their next dates.
    yearly_keys = {y["merchant_key"] for y in book.yearly}
    for item in book.recurring:
        if item["cadence"] == "yearly" or item["merchant_key"] in yearly_keys:
            continue
        for d in _occurrences(item, as_of, end):
            if item["direction"] == "in":
                if item["account_kind"] == "bank":
                    bank_events[bank_day(d)].append({"kind": "income", "label": item["merchant"], "amount": item["amount"],
                                           "account_id": item["account_id"], "estimated": item["variable"]})
            elif item["account_kind"] == "bank":
                bank_events[bank_day(d)].append({"kind": "fixed", "label": item["merchant"], "amount": -item["amount"],
                                       "account_id": item["account_id"], "estimated": item["variable"]})
            elif item["account_id"] in by_id:
                b = bill(item["account_id"], bill_date(d, billing[item["account_id"]]))
                if b is not None:
                    b["parts"]["fixed"] += item["amount"]
                    b["items"].append({"label": item["merchant"], "amount": item["amount"], "kind": "fixed"})
    # Yearly charges (and recurring ones that come once a year), on their dates.
    upcoming = []
    extra = [{**i, "payments": 1} for i in book.charges if i["cadence"] == "yearly" and i["merchant_key"] not in yearly_keys]
    for y in list(book.yearly) + extra:
        d = _date(y["next_date"])
        late = datetime.timedelta(days=LATE_DAYS["yearly"])
        if d < as_of - late:
            continue
        d = max(d, as_of + datetime.timedelta(days=1))
        if d > end:
            continue
        upcoming.append({**{k: y[k] for k in ("merchant", "category", "amount", "last_date", "account_kind")},
                         "date": d.isoformat(), "payments": y.get("payments", 1)})
        n = max(1, y.get("payments") or 1)
        each = y["amount"] // n
        for i in range(n):
            part = each + (y["amount"] - each * n if i == 0 else 0)
            if y.get("account_kind") == "card" and y.get("account_id") in by_id:
                b = bill(y["account_id"], bill_date(periods.add_months(d, i), billing[y["account_id"]]))
                if b is not None:
                    b["parts"]["yearly"] += part
                    b["items"].append({"label": y["merchant"], "amount": part, "kind": "yearly"})
            elif y.get("account_kind") == "card" and main is not None:
                b = bill(main["id"], bill_date(periods.add_months(d, i), billing[main["id"]]))
                if b is not None:
                    b["parts"]["yearly"] += part
                    b["items"].append({"label": y["merchant"], "amount": part, "kind": "yearly"})
            else:
                when = periods.add_months(d, i)
                if when <= end:
                    bank_events[bank_day(when)].append({"kind": "yearly", "label": y["merchant"], "amount": -part,
                                              "estimated": True})
    # Everyday card spending, day by day from as_of, into the main card's bills.
    if main is not None and typical_rate:
        per_day = 12 / 365.25
        d = as_of
        while d <= end:
            b = bill(main["id"], bill_date(d, billing[main["id"]]))
            if b is not None:
                b["parts"]["everyday"] += typical_rate * per_day
                b["everyday_low"] += (low_rate or 0) * per_day
                b["everyday_high"] += (high_rate or 0) * per_day
            d += datetime.timedelta(days=1)
    events: List[Dict[str, Any]] = []
    for d, items in bank_events.items():
        events += [{"date": d.isoformat(), **e} for e in items]
    shift: Dict[datetime.date, Tuple[int, int]] = defaultdict(lambda: (0, 0))
    for (card_id, d), b in bills.items():
        parts = {k: int(round(v)) for k, v in b["parts"].items()}
        total = sum(parts.values())
        if not total:
            continue
        lo, hi = int(round(b["everyday_low"])), int(round(b["everyday_high"]))
        d = bank_day(d)
        events.append({"date": d.isoformat(), "kind": "card_bill", "label": by_id[card_id]["label"],
                       "account_id": card_id, "amount": -total, "estimated": parts["everyday"] > 0,
                       "parts": parts, "items": b["items"],
                       "range": [-(total - parts["everyday"] + hi), -(total - parts["everyday"] + lo)]})
        s = shift[d]
        shift[d] = (s[0] + parts["everyday"] - lo, s[1] + hi - parts["everyday"])
    events.sort(key=lambda e: (e["date"], 0 if e["amount"] > 0 else 1, e["amount"]))
    by_date: Dict[str, int] = defaultdict(int)
    for e in events:
        by_date[e["date"]] += e["amount"]
    series = []
    mid, ahead_low, ahead_high = balance, 0, 0
    bank_per_day = bank_rate * 12 / 365.25
    drip = 0.0
    for i in range(days + 1):
        d = as_of + datetime.timedelta(days=i)
        if i:
            drip += bank_per_day
            mid += by_date.get(d.isoformat(), 0)
            up, down = shift.get(d, (0, 0))
            ahead_low += up      # a quieter month leaves this much more
            ahead_high += down   # a busier month this much less
        value = mid - int(round(drip))
        series.append({"date": d.isoformat(), "balance": value, "low": value - ahead_high, "high": value + ahead_low})
    low = min(series[1:], key=lambda p: p["balance"])
    soon = min(series[1:SOON_DAYS + 1], key=lambda p: p["balance"])
    year_ago = (as_of - datetime.timedelta(days=365)).isoformat()
    one_offs = [r for r in book.rows if book.kinds.get(r["id"]) == "one_off" and r["purchase_date"] >= year_ago]
    left_out = sorted((store.txn_item(r) for r in one_offs if r["amount"] < 0), key=lambda t: t["amount"])
    return {"as_of": as_of.isoformat(), "end": end.isoformat(), "balance": balance, "series": series,
            "events": events, "low_point": {"date": low["date"], "balance": low["balance"]},
            "soon_low": {"date": soon["date"], "balance": soon["balance"], "days": SOON_DAYS},
            "rates": {"card": {"low": low_rate, "typical": typical_rate, "high": high_rate,
                               "months": rates["months"]}, "bank": bank_rate},
            "plans": plans, "yearly": upcoming,
            "left_out": [{k: t[k] for k in ("id", "purchase_date", "amount", "merchant", "category_label")}
                         for t in left_out[:10]],
            "left_out_total": -sum(r["amount"] for r in one_offs)}


def forecast(user: User, days: int = FORECAST_DAYS, book: Optional[Book] = None) -> Optional[Dict[str, Any]]:
    """The user's forecast from their banks' last balance (:func:`project`); None without one."""
    book = book or load(user)
    accounts = [{**a, "label": store.account_label(a["company"], a["kind"], a["number"], a.get("label"))}
                for a in store.list_accounts(user)]
    now = balance_at(book, accounts)
    if now is None:
        return None
    return project(book, accounts, now[0], now[1], days)


def balance(user: User, days: int = FORECAST_DAYS, history_days: int = 365) -> Dict[str, Any]:
    """``{history: balance_history, forecast: forecast | None}`` for the Cash flow view."""
    book = load(user)
    accounts = store.list_accounts(user)
    return {"history": balance_history(book, accounts, history_days), "forecast": forecast(user, days, book)}


# --- Trends ---------------------------------------------------------------------------------

def _elapsed(month: datetime.date, today: datetime.date) -> Tuple[int, int, bool]:
    days = calendar.monthrange(month.year, month.month)[1]
    if periods.month_start(today) == month:
        return today.day, days, True
    return days, days, False


def trends(book: Book, month: Optional[str] = None, n: int = 12) -> Dict[str, Any]:
    """Spending by group and category for a month against typical, one-offs left out.

    Returns ``{month, label, in_progress, elapsed, days, months: [labels], everyday {total,
    typical, on_track}, groups: [{key, label, total, typical, on_track, delta, months:
    [n values], categories: [the same per category]}], movers: [{key, label, group, value,
    typical, delta, direction ("up" | "down" | "new")}], one_offs: [transactions of the month]}``
    — ``typical`` is the median of :data:`TYPICAL_MONTHS` whole months before (a month without
    any counts as 0), ``on_track`` (a month in progress, from day :data:`ON_TRACK_MIN_DAYS`):
    what was spent so far, plus the typical everyday spending of the days left (scaling up
    what was spent would make one ₪1,500 payment a ₪2,500 month), plus the recurring charges
    still due this month. Movers compare everyday spending only (a bimonthly bill doubles its
    category every other month, a renewal is expected; a fixed cost that changes is a price
    change) and leave out drops while the month runs (the month is not over)."""
    first = periods.month_start(_date(month + "-01") if month else book.today)
    elapsed, days, current = _elapsed(first, book.today)
    span = [periods.add_months(first, -k) for k in range(n - 1, -1, -1)]
    keys = [_month_key(m) for m in span]
    typical_keys = {_month_key(m) for m in whole_months(book, first)}
    per: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))   # category → month → agorot
    every: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))  # everyday only
    everyday_months: Dict[str, int] = defaultdict(int)
    one_offs = []
    this = _month_key(first)
    for r in book.rows:
        kind = book.kinds.get(r["id"])
        if kind is None:
            continue
        mk = r["purchase_date"][:7]
        if kind == "one_off":
            if mk == this:
                one_offs.append(store.txn_item(r))
            continue
        cat = r.get("category") or ""
        per[cat][mk] -= r["amount"]
        if kind == "everyday":
            everyday_months[mk] -= r["amount"]
            every[cat][mk] -= r["amount"]
    project_now = current and elapsed >= ON_TRACK_MIN_DAYS
    rest = (days - elapsed) / days
    due: Dict[str, int] = defaultdict(int)
    if current:
        month_end = periods.month_end_exclusive(first).isoformat()
        for item in book.charges:
            if item["cadence"] != "yearly" and book.today.isoformat() < item["next_date"] < month_end:
                due[item.get("category") or ""] += item["amount"]

    def ahead(total: int, everyday: Dict[str, int], still_due: int) -> Optional[int]:
        if not project_now:
            return None
        typical = _median([everyday.get(k, 0) for k in typical_keys]) or 0
        return int(round(total + max(0, typical) * rest + still_due))

    def line(values: Dict[str, int], everyday: Dict[str, int], still_due: int) -> Dict[str, Any]:
        total = values.get(this, 0)
        typical = _median([values.get(k, 0) for k in typical_keys]) if typical_keys else None
        on_track = ahead(total, everyday, still_due)
        value = on_track if on_track is not None else total
        return {"total": total, "typical": typical, "on_track": on_track,
                "delta": value - typical if typical is not None else None,
                "months": [values.get(k, 0) for k in keys]}

    groups: Dict[str, Dict[str, Any]] = {}
    movers = []
    for cat, values in per.items():
        g = categories.group_of(cat) or "uncategorized"
        entry = {"key": cat or "uncategorized", "label": categories.CATEGORIES[cat].label if cat in categories.CATEGORIES
                 else "Uncategorized", **line(values, every.get(cat, {}), due.get(cat, 0))}
        if not any(entry["months"]) and not entry["typical"]:
            continue
        grp = groups.setdefault(g, {"key": g, "label": categories.GROUP_KEYS[g].label if g in categories.GROUP_KEYS
                                    else "Uncategorized", "categories": [], "_values": defaultdict(int),
                                    "_every": defaultdict(int), "_due": 0})
        grp["categories"].append(entry)
        for k, v in values.items():
            grp["_values"][k] += v
        for k, v in every.get(cat, {}).items():
            grp["_every"][k] += v
        grp["_due"] += due.get(cat, 0)
        if current and not project_now:
            continue
        # Movers compare everyday spending: a bimonthly bill doubles its category every other month
        # and a renewal is expected (fixed costs that change are price changes).
        ev = every.get(cat, {})
        value = ahead(ev.get(this, 0), ev, 0) if project_now else ev.get(this, 0)
        typical = (_median([ev.get(k, 0) for k in typical_keys]) if typical_keys else 0) or 0
        delta = value - typical
        if current and delta < 0:
            continue
        if abs(delta) >= MOVER_MIN_AGOROT and (typical == 0 or abs(delta) >= MOVER_MIN_SHARE * typical):
            movers.append({"key": entry["key"], "label": entry["label"], "group": g, "value": value,
                           "typical": typical, "delta": delta,
                           "direction": "new" if typical == 0 else ("up" if delta > 0 else "down")})
    out_groups = []
    for g in groups.values():
        g.update(line(g.pop("_values"), g.pop("_every"), g.pop("_due")))
        g["categories"].sort(key=lambda c: -(c["on_track"] if c["on_track"] is not None else c["total"]))
        out_groups.append(g)
    out_groups.sort(key=lambda g: -max(g["total"], g["typical"] or 0))
    movers.sort(key=lambda m: -abs(m["delta"]))
    e_total = everyday_months.get(this, 0)
    e_typical = _median([everyday_months.get(k, 0) for k in typical_keys]) if typical_keys else None
    return {"month": this, "label": periods.month_label(first), "in_progress": current, "elapsed": elapsed,
            "days": days, "months": [_month_label(m) for m in span],
            "everyday": {"total": e_total, "typical": e_typical, "on_track": ahead(e_total, everyday_months, 0)},
            "groups": out_groups, "movers": movers[:6], "one_offs": sorted(one_offs, key=lambda t: t["amount"])}


def everyday_pace(book: Book, month: Optional[str] = None) -> Dict[str, Any]:
    """Everyday spending day by day: ``{month, label, days, today (day of the month, None for
    another month), this_month: [cumulative agorot | None after today], typical: [median of the
    whole months before, day by day] | None, low / high: [quartiles] | None, months}``."""
    first = periods.month_start(_date(month + "-01") if month else book.today)
    days = calendar.monthrange(first.year, first.month)[1]
    months = whole_months(book, first)
    wanted = {_month_key(m) for m in months} | {_month_key(first)}
    daily: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for r in book.rows:
        if book.kinds.get(r["id"]) == "everyday" and r["purchase_date"][:7] in wanted:
            daily[r["purchase_date"][:7]][_date(r["purchase_date"]).day] -= r["amount"]

    def cumulative(key: str, n: int) -> List[int]:
        out, running = [], 0
        for d in range(1, n + 1):
            running += daily[key].get(d, 0)
            out.append(running)
        return out

    this = cumulative(_month_key(first), days)
    current = periods.month_start(book.today) == first
    if current:
        this = [v if i < book.today.day else None for i, v in enumerate(this)]
    elif first > book.today:
        this = [None] * days
    prior = [cumulative(_month_key(m), calendar.monthrange(m.year, m.month)[1]) for m in months]
    typical = low = high = None
    if prior:
        at = [[c[min(d, len(c) - 1)] for c in prior] for d in range(days)]
        q = [_quartiles(v) for v in at]
        low, typical, high = [x[0] for x in q], [x[1] for x in q], [x[2] for x in q]
    return {"month": _month_key(first), "label": periods.month_label(first), "days": days,
            "today": book.today.day if current else None, "this_month": this, "typical": typical,
            "low": low, "high": high, "months": len(prior)}


# --- Merchants ------------------------------------------------------------------------------

def merchants(book: Book, start: datetime.date, end_exclusive: datetime.date, limit: int = 15) -> List[Dict[str, Any]]:
    """The merchants spent at most in [start, end): ``[{merchant_key, merchant, category,
    category_label, total, count, average, first_date, months_active (of the last 12),
    months: [12 monthly totals, oldest first], one_off (every row of theirs here was one)}]``."""
    lo, hi = start.isoformat(), end_exclusive.isoformat()
    span = [periods.add_months(periods.month_start(book.today), -k) for k in range(11, -1, -1)]
    keys = [_month_key(m) for m in span]
    agg: Dict[str, Dict[str, Any]] = {}
    for r in book.rows:
        kind = book.kinds.get(r["id"])
        key = r.get("merchant_key")
        if kind is None or not key:
            continue
        a = agg.setdefault(key, {"rows": [], "months": defaultdict(int), "first": r["purchase_date"], "last": r})
        a["months"][r["purchase_date"][:7]] -= r["amount"]
        a["last"] = r
        if lo <= r["purchase_date"] < hi:
            a["rows"].append((r, kind))
    out = []
    for key, a in agg.items():
        if not a["rows"]:
            continue
        total = -sum(r["amount"] for r, _ in a["rows"])
        count = sum(1 for r, _ in a["rows"] if r["amount"] < 0)
        if total <= 0:
            continue
        item = store.txn_item(a["last"])
        out.append({"merchant_key": key, "merchant": item["merchant"], "category": item["category"],
                    "category_label": item["category_label"], "total": total, "count": count,
                    "average": total // max(1, count), "first_date": a["first"],
                    "months_active": sum(1 for k in keys if a["months"].get(k, 0) > 0),
                    "months": [a["months"].get(k, 0) for k in keys],
                    "one_off": all(kind == "one_off" for _, kind in a["rows"])})
    out.sort(key=lambda m: -m["total"])
    return out[:limit]


# --- Insights -------------------------------------------------------------------------------

def _change(value: int, typical: int) -> str:
    """``2.4× the usual`` from double up, else ``up 35%`` / ``down 40%``."""
    if typical and value >= 2 * typical:
        return f"{value / typical:.1f}× the usual".replace(".0×", "×")
    return f"{'up' if value >= typical else 'down'} {abs(value - typical) / typical * 100:.0f}%" if typical else ""


def insights_for(book: Book, month: Optional[str] = None, fc: Optional[Dict[str, Any]] = None,
                 unusual: Sequence[Dict[str, Any]] = ()) -> List[Dict[str, Any]]:
    """The month's insights, most useful first: ``[{kind, tone ("good" | "bad" | "warn" |
    "info"), title, detail, amount?, link {view, …}?}]``. Every number comes from the
    engine (``fc``: the forecast, for the month in progress; ``unusual``: the month's
    unusual charges, ``insights.unusual``)."""
    t = trends(book, month)
    first = _date(t["month"] + "-01")
    cf = cashflow(book, 12, t["month"])
    this = cf["months"][-1]
    out: List[Dict[str, Any]] = []
    sh = store.shekels
    e = t["everyday"]
    if e["typical"]:
        margin = max(MOVER_MIN_AGOROT, e["typical"] // 10)
        if t["in_progress"] and e["on_track"] is not None:
            diff = e["on_track"] - e["typical"]
            tone = "bad" if diff > margin else ("good" if diff < -margin else "info")
            out.append({"kind": "everyday_pace", "tone": tone,
                        "title": f"Everyday spending on track for {sh(e['on_track'])}",
                        "detail": f"A typical month is {sh(e['typical'])}; {sh(e['total'])} so far, day {t['elapsed']} "
                                  f"of {t['days']}. One-offs and fixed costs left out.",
                        "amount": e["on_track"], "link": {"view": "overview"}})
        elif not t["in_progress"]:
            diff = e["total"] - e["typical"]
            tone = "bad" if diff > margin else ("good" if diff < -margin else "info")
            word = "more" if diff > 0 else "less"
            out.append({"kind": "everyday_month", "tone": tone,
                        "title": f"Everyday spending {sh(e['total'])}",
                        "detail": f"{sh(abs(diff))} {word} than a typical month ({sh(e['typical'])}).",
                        "amount": e["total"], "link": {"view": "spending"}})
    if fc and t["in_progress"]:
        soon, low = fc["soon_low"], fc["low_point"]
        worst = min(soon["balance"], low["balance"])
        tone = "bad" if worst < 0 else ("warn" if worst < LOW_BALANCE_AGOROT else "info")
        nxt = [e for e in fc["events"] if e["date"] <= (book.today + datetime.timedelta(days=SOON_DAYS)).isoformat()][:3]
        detail = "; ".join(f"{e['label']} {'about ' if e['estimated'] else ''}{sh(abs(e['amount']))} on "
                           f"{periods.short_date(_date(e['date']), False)}" for e in nxt)
        detail = (f"Next: {detail}. " if detail else "") + "One-offs left out."
        if low["date"] != soon["date"] and low["balance"] < soon["balance"]:
            detail = f"Lower later: {sh(low['balance'])} on {periods.short_date(_date(low['date']))}. " + detail
        if soon["balance"] >= fc["balance"]:
            title = f"Balance stays above {sh(fc['balance'])} for the next {soon['days']} days"
        else:
            title = (f"Lowest balance in the next {soon['days']} days: {sh(soon['balance'])} on "
                     f"{periods.short_date(_date(soon['date']), False)}")
        out.append({"kind": "low_point", "tone": tone, "title": title, "detail": detail,
                    "amount": soon["balance"], "link": {"view": "cashflow"}})
    if not t["in_progress"] and this["income"] > 0:
        typical_net = cf["typical"]["net"]
        tone = "good" if this["net"] > 0 else "bad"
        detail = f"Earned {sh(this['income'])}, spent {sh(this['spent'])}"
        if this["one_off"] > 0:
            detail += f" ({sh(this['one_off'])} of it one-offs)"
        if this["set_aside"] > 0:
            detail += f", set aside {sh(this['set_aside'])}"
        if typical_net is not None:
            detail += (f". A typical month leaves {sh(typical_net)}" if typical_net >= 0
                       else f". A typical month spends {sh(-typical_net)} more than it earns")
        out.append({"kind": "net", "tone": tone,
                    "title": (f"Saved {sh(this['net'])}" if this["net"] >= 0 else f"Spent {sh(-this['net'])} more than earned"),
                    "detail": detail + ".", "amount": this["net"], "link": {"view": "cashflow"}})
    for m in t["movers"][:3]:
        if m["direction"] == "new":
            title, tone = f"{m['label']}: {sh(m['value'])}, usually nothing", "info"
        elif m["value"] == 0:
            title, tone = f"{m['label']}: nothing this month", "good"
        else:
            title, tone = f"{m['label']} {_change(m['value'], m['typical'])}", "bad" if m["direction"] == "up" else "good"
        what = f"On track for {sh(m['value'])}" if t["in_progress"] else f"{sh(m['value'])} this month"
        detail = (f"{what}; a typical month {sh(m['typical'])}." if m["typical"] and m["value"]
                  else f"A typical month: {sh(m['typical'])}." if m["typical"]
                  else "Nothing here in a typical month (the median of the months before).")
        out.append({"kind": "mover", "tone": tone, "title": title, "detail": detail, "amount": m["delta"],
                    "link": {"view": "spending", "category": m["key"]}})
    since, until = periods.add_months(first, -1).isoformat(), periods.month_end_exclusive(first).isoformat()
    for item in book.charges:
        ch = item.get("price_change")
        if ch and since <= ch["since"] < until:
            up = ch["to"] > ch["from"]
            per_year = int(round((ch["to"] - ch["from"]) * 12 * insights.per_month(item["cadence"])))
            out.append({"kind": "price_change", "tone": "bad" if up else "good",
                        "title": f"{item['merchant']} {'up' if up else 'down'} to {sh(ch['to'])}",
                        "detail": f"Was {sh(ch['from'])} {insights.cadence_text(item['cadence'])}: "
                                  f"{sh(abs(per_year))} a year {'more' if up else 'less'}.",
                        "amount": ch["to"] - ch["from"], "link": {"view": "cashflow"}})
    soon = (book.today + datetime.timedelta(days=45)).isoformat()
    for y in book.yearly if t["in_progress"] else ():
        if book.today.isoformat() < y["next_date"] <= soon:
            out.append({"kind": "yearly_soon", "tone": "info",
                        "title": f"{y['merchant']} {sh(y['amount'])} around {periods.short_date(_date(y['next_date']), False)}",
                        "detail": f"Once a year (last on {periods.short_date(_date(y['last_date']))}); it is in the forecast.",
                        "amount": y["amount"], "link": {"view": "cashflow"}})
    if fc and t["in_progress"] and fc["plans"]:
        left = sum(p["left"] for p in fc["plans"])
        names = ", ".join(p["merchant"] for p in fc["plans"][:3])
        if left:
            out.append({"kind": "installments", "tone": "info",
                        "title": f"{sh(left)} left on installment plans",
                        "detail": f"{len(fc['plans'])} running: {names}.", "amount": left, "link": {"view": "cashflow"}})
    if t["one_offs"]:
        big = t["one_offs"][0]
        rest = len(t["one_offs"]) - 1
        out.append({"kind": "one_off", "tone": "info", "title": f"One-off: {big['merchant']} {sh(-big['amount'])}",
                    "detail": "Left out of typical spending and the forecast"
                              + (f" (and {rest} more this month)." if rest else "."),
                    "amount": -big["amount"], "link": {"view": "transactions", "id": big["id"]}})
    # A one-off is its own card, and a yearly renewal is expected (only a double charge of one stands out).
    for u in [u for u in unusual if book.kinds.get(u["transaction"]["id"]) != "one_off"
              and (book.kinds.get(u["transaction"]["id"]) != "yearly" or u["reason"] == "duplicate")][:2]:
        out.append({"kind": "unusual", "tone": "warn", "title": u["title"], "detail": u["why"].capitalize() + ".",
                    "amount": -u["transaction"]["amount"], "link": {"view": "transactions", "id": u["transaction"]["id"]}})
    year_start = first.replace(month=1).isoformat()
    set_aside = -sum(r["amount"] for r in book.rows if flow(r) == "investment" and r["purchase_date"] >= year_start
                     and r["purchase_date"] < periods.month_end_exclusive(first).isoformat())
    if set_aside > 0:
        out.append({"kind": "set_aside", "tone": "good", "title": f"Set aside {sh(set_aside)} in {first.year}",
                    "detail": "Investments and savings; not counted as spending.", "amount": set_aside,
                    "link": {"view": "cashflow"}})
    return out


# Cards other parts of the addon add to the month in progress (savings.py's "ways to save"), each
# ``fn(user, book) -> [card]``; registered in kind.py.
InsightSource = Callable[[User, Book], List[Dict[str, Any]]]
_insight_sources: List[InsightSource] = []
# Where those cards go in the ranked list (after the everyday pace and the low point).
EXTRA_INSIGHTS_AT = 2


def on_insights(fn: InsightSource) -> InsightSource:
    """Register ``fn(user, book)`` to add cards to the month in progress (registering twice is a no-op)."""
    if fn not in _insight_sources:
        _insight_sources.append(fn)
    return fn


def month_insights(user: User, month: Optional[str] = None, book: Optional[Book] = None) -> Dict[str, Any]:
    """``{month, items: insights_for}`` for the user (with the forecast and the registered cards
    (:func:`on_insights`) for this month)."""
    book = book or load(user)
    first = periods.month_start(_date(month + "-01") if month else book.today)
    current = first == periods.month_start(book.today)
    fc = forecast(user, FORECAST_DAYS, book) if current else None
    end = periods.month_end_exclusive(first).isoformat()
    unusual = [u for u in insights.unusual(user, first) if u["transaction"]["purchase_date"] < end]
    items = insights_for(book, _month_key(first), fc, unusual)
    if current:
        extra: List[Dict[str, Any]] = []
        for fn in list(_insight_sources):
            try:
                extra += fn(user, book)
            except Exception as e:
                logger.error(f"Money insight source {getattr(fn, '__qualname__', fn)} failed for user {user.id}: {e}",
                             exc_info=True)
        items[EXTRA_INSIGHTS_AT:EXTRA_INSIGHTS_AT] = extra
    return {"month": _month_key(first), "items": items}


# --- For the page and Dana's tools ----------------------------------------------------------

def tiles(user: User, month: Optional[str] = None, book: Optional[Book] = None) -> Dict[str, Any]:
    """The Overview's numbers for a month: ``{everyday {total, typical, on_track}, net, income_month,
    income_typical, set_aside, one_off, fixed_monthly (recurring charges and yearly ones, a month),
    yearly_monthly, forecast {balance, as_of, soon_low, low_point, end, end_balance, next: [4 events]}
    | None (the month in progress only)}``."""
    book = book or load(user)
    t = trends(book, month)
    cf = cashflow(book, 12, t["month"])
    this = cf["months"][-1]
    yearly_monthly = sum(y["monthly_cost"] for y in book.yearly)
    out: Dict[str, Any] = {
        "everyday": t["everyday"], "net": this["net"], "income_month": this["income"],
        "income_typical": cf["typical"]["income"], "set_aside": this["set_aside"], "one_off": this["one_off"],
        "fixed_monthly": sum(i["monthly_cost"] for i in book.charges if i["cadence"] != "yearly") + yearly_monthly,
        "yearly_monthly": yearly_monthly, "forecast": None}
    if t["in_progress"]:
        fc = forecast(user, FORECAST_DAYS, book)
        if fc is not None:
            out["forecast"] = {"balance": fc["balance"], "as_of": fc["as_of"], "soon_low": fc["soon_low"],
                               "low_point": fc["low_point"], "end": fc["end"],
                               "end_balance": fc["series"][-1]["balance"], "next": fc["events"][:4]}
    return out


def annotate(user: User, items: List[Dict[str, Any]]) -> None:
    """Add ``kind`` (:func:`spend_kinds`, None when not spending) to transaction items."""
    if not items:
        return
    kinds = load(user).kinds
    for item in items:
        item["kind"] = kinds.get(item["id"])


def with_kind(user: User, txn: Dict[str, Any]) -> Dict[str, Any]:
    """A transaction in full with ``kind`` and the user's marks ``one_off`` / ``yearly`` (true,
    false, or null: Dana decides)."""
    with store.db() as conn:
        row = conn.execute("SELECT one_off, yearly FROM money_transactions WHERE id = ? AND user_id = ?",
                           (txn["id"], user.id)).fetchone()
    marks = {k: (None if row is None or row[k] is None else bool(row[k])) for k in ("one_off", "yearly")}
    return {**txn, "kind": load(user).kinds.get(txn["id"]), **marks}
