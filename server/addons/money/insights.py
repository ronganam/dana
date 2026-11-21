"""Recurring charges and unusual charges (docs/money.md, "Page API and insights").

- :func:`recurring`: subscriptions, standing orders and regular income — the
  same merchant key at a regular interval (monthly in ≥ 3 distinct months,
  weekly in ≥ 4 weeks, yearly in ≥ 2 years) with similar amounts (one price
  change allowed, and flagged), still active. Card installments are payments
  of one purchase, never a subscription.
- :func:`yearly`: charges that come once a year — an insurance renewal, the
  car licence, an annual subscription (:data:`YEARLY_CATEGORIES`, at least
  ₪300 and at most ₪10,000, the merchant not a monthly or bimonthly series), or a charge the
  user marked yearly. One renewal may be several rows (an installment plan,
  a second policy days later); seen once, it is expected a year later.
- :func:`unusual`: charges since a date that are much larger than usual for
  the merchant (≥ 3× the median of ≥ 4 earlier charges, at least ₪200), a
  first charge from a merchant over ₪500 (not in the first
  :data:`WARMUP_DAYS` of history, when every merchant is new), or the same
  amount at the same merchant twice within 10 minutes (an installment plan's
  payments are not duplicates).

The detection is pure (:func:`detect_recurring`, :func:`detect_yearly`,
:func:`find_unusual`) over rows of ``store.TXN_SELECT``; the entry points only
fetch the user's rows.
"""

import calendar
import datetime
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from core.users import User
from addons.money import categories, periods, store

# How far back recurring detection looks (two yearly charges and some).
HISTORY_DAYS = 800
# Consecutive amounts within this share of each other are "the same price" (a subscription
# billed in dollars moves a little with the rate; a supermarket's monthly shop moves more).
SIMILAR = 0.05
# Bills whose amount follows usage or the household (electricity, water, a health fund, insurance
# premiums): a regular cadence is enough, whatever the amounts (``variable``; the typical amount is
# the median of the last three). Groceries or shopping on a cadence still need similar amounts.
# A salary moves with overtime and bonuses; a mortgage payment with the index.
VARIABLE_CATEGORIES = frozenset({"utilities", "insurance", "phone_internet", "subscriptions", "health", "housing",
                                 "salary", "benefits"})
VARIABLE_TYPICAL_OF = 3
LARGE_FACTOR = 3
LARGE_MIN_EARLIER = 4
# A charge below this is never "much larger than usual" (₪15 at a café that is usually ₪5 is not news).
LARGE_MIN_AGOROT = 20_000
FIRST_TIME_MIN_AGOROT = 50_000
# In the first days of history every merchant is new: no "first charge from them" then.
WARMUP_DAYS = 60
DUPLICATE_MINUTES = 10
# Flows that are never a recurring charge or income of their own (the card bill is the card's rows).
_NOT_RECURRING = ("card_bill", "own_transfer")
# Yearly charges: what renews once a year, from this amount up (a renewal paid in installments
# counts at its total). Rows of one merchant this close together are one renewal.
YEARLY_CATEGORIES = frozenset({"insurance", "car", "subscriptions", "education", "taxes"})
YEARLY_MIN_AGOROT = 30_000
# Above this a charge is a purchase (a car), not a renewal, unless the user marks it yearly.
YEARLY_MAX_AGOROT = 1_000_000
YEARLY_CLUSTER_DAYS = 45
# Two renewals of one merchant closer than this are not yearly (a garage visited twice a year).
YEARLY_MIN_GAP = 300
# A yearly charge not seen again this long after its date has stopped.
YEARLY_GRACE_DAYS = 60


@dataclass(frozen=True)
class Cadence:
    name: str
    gap: Tuple[int, int]          # days between charges, inclusive
    min_buckets: int              # distinct weeks / months / years
    active_days: int              # the last charge at most this long ago
    per_month: float              # charges per month


CADENCES: Tuple[Cadence, ...] = (
    Cadence("weekly", (6, 8), 4, 17, 52 / 12),
    Cadence("monthly", (26, 35), 3, 45, 1.0),
    # Israeli utilities and the municipality often bill every two months.
    Cadence("bimonthly", (50, 80), 3, 90, 0.5),
    Cadence("yearly", (350, 380), 2, 400, 1 / 12),
)


def per_month(cadence: str) -> float:
    """Charges per month of a cadence name."""
    return next(c.per_month for c in CADENCES if c.name == cadence)


def cadence_text(cadence: str) -> str:
    """``a week`` / ``a month`` / ``every 2 months`` / ``a year``."""
    return {"weekly": "a week", "monthly": "a month", "bimonthly": "every 2 months", "yearly": "a year"}[cadence]


def _bucket(cadence: str, d: datetime.date) -> Any:
    if cadence == "weekly":
        return periods.week_start(d)
    if cadence in ("monthly", "bimonthly"):
        return (d.year, d.month)
    return d.year


def _next(cadence: str, d: datetime.date) -> datetime.date:
    if cadence == "weekly":
        return d + datetime.timedelta(days=7)
    return periods.add_months(d, {"monthly": 1, "bimonthly": 2}.get(cadence, 12))


def _cadence(dates: Sequence[datetime.date], today: datetime.date) -> Optional[Cadence]:
    """The cadence the dates follow (every gap in range, one stray gap allowed
    from 3 gaps up; enough distinct buckets; the last one recent), or None."""
    if len(dates) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    for c in CADENCES:
        regular = sum(1 for g in gaps if c.gap[0] <= g <= c.gap[1])
        if regular < len(gaps) - (1 if len(gaps) >= 3 else 0):
            continue
        if len({_bucket(c.name, d) for d in dates}) < c.min_buckets:
            continue
        if (today - dates[-1]).days > c.active_days:
            continue
        return c
    return None


def _runs(amounts: Sequence[int]) -> List[List[int]]:
    """Indexes of consecutive amounts within :data:`SIMILAR` of the one before."""
    runs: List[List[int]] = [[0]]
    for i in range(1, len(amounts)):
        prev = amounts[i - 1]
        if prev and abs(amounts[i] - prev) / prev <= SIMILAR:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def _series(items: List[Dict[str, Any]], key: str, money_out: bool,
            today: datetime.date) -> Optional[Dict[str, Any]]:
    """The recurring item of one merchant's rows, None when they are not a series."""
    items.sort(key=lambda r: (r["purchase_date"], r.get("occurred_at") or ""))
    dates = [datetime.date.fromisoformat(r["purchase_date"]) for r in items]
    cadence = _cadence(dates, today)
    if cadence is None:
        return None
    amounts = [abs(r["amount"]) for r in items]
    runs = _runs(amounts)
    variable = len(runs) > 2 and items[-1].get("category") in VARIABLE_CATEGORIES
    if len(runs) > 2 and not variable:
        return None
    # The latest price: a mortgage payment drifts down month by month within one run.
    typical = int(statistics.median(amounts[-VARIABLE_TYPICAL_OF:] if variable else
                                    [amounts[i] for i in runs[-1]][-VARIABLE_TYPICAL_OF:]))
    change = None
    if len(runs) == 2:
        change = {"from": int(statistics.median(amounts[i] for i in runs[0])), "to": typical,
                  "since": items[runs[1][0]]["purchase_date"]}
    last = items[-1]
    item = store.txn_item(last)
    nxt = _next(cadence.name, dates[-1])
    if cadence.name in ("monthly", "bimonthly"):
        # Its usual day: a salary paid on the 2nd because the 1st was a Saturday is due on the 1st again.
        day = min(Counter(d.day for d in dates[-6:]).most_common(),
                  key=lambda dc: (-dc[1], dc[0]))[0]
        nxt = nxt.replace(day=min(day, calendar.monthrange(nxt.year, nxt.month)[1]))
    return {"merchant_key": key, "merchant_keys": sorted({r["merchant_key"] for r in items}),
            "merchant": item["merchant"], "category": item["category"],
            "category_label": item["category_label"], "cadence": cadence.name,
            "direction": "out" if money_out else "in", "amount": typical,
            "monthly_cost": int(round(typical * cadence.per_month)), "count": len(items),
            "first_date": items[0]["purchase_date"], "last_date": last["purchase_date"],
            "next_date": nxt.isoformat(), "price_change": change,
            "variable": variable,
            "account_id": item["account_id"], "account_label": item["account_label"],
            "account_kind": item["account_kind"], "flow": item["flow"]}


def detect_recurring(rows: Iterable[Dict[str, Any]], today: datetime.date) -> List[Dict[str, Any]]:
    """Recurring series among ``rows`` (``store.txn_rows`` dicts), one item each:
    ``{merchant_key, merchant_keys, merchant, category, category_label, cadence,
    direction ("out" | "in"), amount (typical, positive agorot), monthly_cost,
    count, first_date, last_date, next_date, price_change {from, to, since} | None,
    variable (a bill whose amount varies, :data:`VARIABLE_CATEGORIES`), account_id,
    account_label, account_kind, flow}``, the largest monthly cost first. A
    merchant whose rows come under two spellings (the water company's "בע"מ"
    and "בע"מ הק") is tried again under its display name."""
    series: Dict[Tuple[str, bool], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if not r.get("merchant_key") or (r.get("installment_total") or 0) > 1 or r["status"] == "unmatched":
            continue
        if (r.get("eff_flow") or r.get("flow")) in _NOT_RECURRING or not r["amount"]:
            continue
        series[(r["merchant_key"], r["amount"] < 0)].append(r)
    out: List[Dict[str, Any]] = []
    unmatched: Dict[Tuple[str, bool], List[Dict[str, Any]]] = defaultdict(list)
    for (key, money_out), items in series.items():
        found = _series(items, key, money_out, today)
        if found is not None:
            out.append(found)
        else:
            name = (items[-1].get("merchant") or "").strip().casefold()
            if name:
                unmatched[(name, money_out)].extend(items)
    for (name, money_out), items in unmatched.items():
        if len({r["merchant_key"] for r in items}) > 1:
            found = _series(items, items[-1]["merchant_key"], money_out, today)
            if found is not None:
                out.append(found)
    out.sort(key=lambda i: (-i["monthly_cost"], i["merchant"]))
    return out


def recurring(user: User, today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """The user's recurring charges and income: ``{items: [money out], income:
    [money in], monthly_total}`` (``monthly_total`` = the charges' monthly cost)."""
    today = today or periods.today()
    since = (today - datetime.timedelta(days=HISTORY_DAYS)).isoformat()
    rows = store.txn_rows(user, "t.purchase_date >= ? AND t.status != 'unmatched'", (since,),
                          order="t.purchase_date, t.id")
    found = detect_recurring(rows, today)
    charges = [i for i in found if i["direction"] == "out"]
    return {"items": charges, "income": [i for i in found if i["direction"] == "in"],
            "monthly_total": sum(i["monthly_cost"] for i in charges)}


# --- Yearly charges -------------------------------------------------------------------------

def plan_total(r: Dict[str, Any]) -> int:
    """What a row stands for, positive agorot: an installment plan's total (Cal gives
    it as the original amount), else the row's own amount."""
    n = r.get("installment_total") or 0
    if n > 1:
        if r.get("orig_amount") and (r.get("orig_currency") or "ILS") == "ILS":
            return abs(r["orig_amount"])
        return abs(r["amount"]) * n
    return abs(r["amount"])


def plan_key(r: Dict[str, Any]) -> Tuple[Any, ...]:
    """The rows of one purchase: an installment plan's payments share it, other rows are their own."""
    if (r.get("installment_total") or 0) > 1:
        return ("plan", r.get("account_id"), r.get("merchant_key"), r["purchase_date"], r["installment_total"],
                r.get("orig_amount"))
    return ("row", r["id"])


def _yearly_candidate(r: Dict[str, Any], fixed_keys: Set[str]) -> bool:
    if r.get("yearly") == 1:
        return True
    if r.get("yearly") == 0 or r.get("one_off") == 1:
        return False
    return (r.get("category") in YEARLY_CATEGORIES and r.get("merchant_key") not in fixed_keys
            and YEARLY_MIN_AGOROT <= plan_total(r) <= YEARLY_MAX_AGOROT)


def detect_yearly(rows: Iterable[Dict[str, Any]], today: datetime.date,
                  fixed_keys: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """Yearly charges among ``rows`` (money out, ``store.txn_rows`` dicts); ``fixed_keys``:
    merchant keys of monthly / bimonthly series, never yearly. One item per merchant:
    ``{merchant_key, merchant, category, category_label, amount (the last renewal's total,
    positive agorot), monthly_cost, payments (the last renewal's installments, 1 without),
    count (renewals seen), last_date, next_date, row_ids (every row of the renewals),
    marked (the user marked one), account_id, account_kind}``, the soonest first."""
    fixed = set(fixed_keys)
    by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if (not r.get("merchant_key") or r["status"] == "unmatched" or r["amount"] >= 0
                or (r.get("eff_flow") or r.get("flow")) not in categories.SPENDING_FLOWS):
            continue
        if _yearly_candidate(r, fixed):
            by_key[r["merchant_key"]].append(r)
    out: List[Dict[str, Any]] = []
    for key, items in by_key.items():
        items.sort(key=lambda r: (r["purchase_date"], r["id"]))
        renewals: List[List[Dict[str, Any]]] = []
        for r in items:
            d = datetime.date.fromisoformat(r["purchase_date"])
            if renewals and (d - datetime.date.fromisoformat(renewals[-1][0]["purchase_date"])).days <= YEARLY_CLUSTER_DAYS:
                renewals[-1].append(r)
            else:
                renewals.append([r])
        starts = [datetime.date.fromisoformat(g[0]["purchase_date"]) for g in renewals]
        marked = any(r.get("yearly") == 1 for r in items)
        if not marked and any((b - a).days < YEARLY_MIN_GAP for a, b in zip(starts, starts[1:])):
            continue
        last, last_date = renewals[-1], starts[-1]
        nxt = periods.add_months(last_date, 12)
        if (today - nxt).days > YEARLY_GRACE_DAYS:
            continue
        plans: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        for r in last:
            plans.setdefault(plan_key(r), r)
        amount = sum(plan_total(r) for r in plans.values())
        payments = max((r.get("installment_total") or 1) for r in last)
        item = store.txn_item(last[0])
        out.append({"merchant_key": key, "merchant": item["merchant"], "category": item["category"],
                    "category_label": item["category_label"], "amount": amount,
                    "monthly_cost": int(round(amount / 12)), "payments": payments, "count": len(renewals),
                    "last_date": last_date.isoformat(), "next_date": nxt.isoformat(),
                    "row_ids": [r["id"] for r in items], "marked": marked,
                    "account_id": item["account_id"], "account_kind": item["account_kind"]})
    out.sort(key=lambda i: (i["next_date"], i["merchant"]))
    return out


def yearly(user: User, today: Optional[datetime.date] = None,
           rec: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The user's yearly charges (:func:`detect_yearly`); ``rec``: their :func:`recurring`, when known."""
    today = today or periods.today()
    rec = rec if rec is not None else recurring(user, today)
    since = (today - datetime.timedelta(days=HISTORY_DAYS)).isoformat()
    rows = store.txn_rows(user, "t.purchase_date >= ? AND t.amount < 0 AND t.status != 'unmatched'", (since,),
                          order="t.purchase_date, t.id")
    return detect_yearly(rows, today, fixed_keys(rec))


def fixed_keys(rec: Dict[str, Any]) -> Set[str]:
    """Merchant keys of the recurring charges that come monthly or more often, or every two months."""
    return {k for i in rec["items"] if i["cadence"] != "yearly" for k in i.get("merchant_keys") or [i["merchant_key"]]}


# --- Unusual charges ------------------------------------------------------------------------

def _order(r: Dict[str, Any]) -> Tuple[str, str]:
    return r["purchase_date"], r.get("occurred_at") or ""


def _before(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Tuple[bool, float]]:
    """Whether ``a`` happened before ``b`` (the same moment: by id) and how many
    minutes apart, from their exact times; None when either has none."""
    ta, tb = store.parse(a.get("occurred_at")), store.parse(b.get("occurred_at"))
    if ta is None or tb is None:
        return None
    return (ta, a["id"]) < (tb, b["id"]), abs((ta - tb).total_seconds()) / 60


def find_unusual(candidates: Iterable[Dict[str, Any]], history: Iterable[Dict[str, Any]],
                 first_date: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
    """The unusual charges among ``candidates`` judged against ``history`` (every
    earlier money-out row of the same merchants; the candidates may be in it);
    ``first_date``: the user's first transaction (no first-time flags in the warm-up).
    One entry per charge, newest first: ``{transaction, reason ("duplicate" |
    "large" | "first_time"), title ("₪1,240.00 at X"), why ("about 4× the usual
    ₪300.00 there"), text (both), typical?, duplicate_of?}``."""
    by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in history:
        if r.get("merchant_key") and r["status"] != "unmatched" and r["amount"] < 0:
            by_key[r["merchant_key"]].append(r)
    out: List[Dict[str, Any]] = []
    plans: Set[Tuple[Any, ...]] = set()
    for c in sorted(candidates, key=lambda r: (_order(r), -(r.get("installment_number") or 0)), reverse=True):
        key = c.get("merchant_key")
        if not key or c["amount"] >= 0 or c["status"] == "unmatched":
            continue
        if (c.get("installment_total") or 0) > 1:
            # One flag for an installment plan (its first payment here), judged by its total.
            if plan_key(c) in plans:
                continue
            plans.add(plan_key(c))
        amount = plan_total(c)
        item = store.txn_item(c)
        others = [r for r in by_key.get(key, []) if r["id"] != c["id"]]
        found: Optional[Dict[str, Any]] = None
        plan = (c.get("installment_total") or 0) > 1
        for o in others:
            if plan and plan_key(o) == plan_key(c):
                continue
            timing = _before(o, c)
            if (timing is not None and timing[0] and timing[1] <= DUPLICATE_MINUTES and o["amount"] == c["amount"]
                    and (o["status"] == "provisional") == (c["status"] == "provisional")):
                found = {"reason": "duplicate", "duplicate_of": o["id"],
                         "why": f"charged twice within {max(1, round(timing[1]))} minutes"}
                break
        earlier = [abs(o["amount"]) for o in others if _order(o) < _order(c)]
        if found is None and len(earlier) >= LARGE_MIN_EARLIER and amount >= LARGE_MIN_AGOROT:
            typical = int(statistics.median(earlier))
            if typical and amount >= LARGE_FACTOR * typical:
                found = {"reason": "large", "typical": typical,
                         "why": f"about {amount / typical:.0f}× the usual {store.shekels(typical)} there"}
        warm = first_date is not None and (datetime.date.fromisoformat(c["purchase_date"]) - first_date).days < WARMUP_DAYS
        if (found is None and not warm and amount > FIRST_TIME_MIN_AGOROT
                and not any(_order(o) <= _order(c) and plan_key(o) != plan_key(c) for o in others)):
            found = {"reason": "first_time", "why": "the first charge from them"}
        if found is not None:
            title = f"{store.shekels(amount)} at {item['merchant']}"
            out.append({"transaction": item, "title": title, "text": f"{title}, {found['why']}", **found})
    return out


def unusual(user: User, since: Any) -> List[Dict[str, Any]]:
    """The user's unusual charges with a purchase date from ``since`` (a date or
    ``YYYY-MM-DD``) on, newest first (see :func:`find_unusual`)."""
    start = since.isoformat() if isinstance(since, datetime.date) else str(since)[:10]
    spending = ",".join(f"'{f}'" for f in sorted(categories.SPENDING_FLOWS))
    candidates = store.txn_rows(user, f"t.purchase_date >= ? AND t.amount < 0 AND t.status != 'unmatched' "
                                      f"AND ({store.FLOW_SQL}) IN ({spending})", (start,))
    keys = sorted({c["merchant_key"] for c in candidates if c.get("merchant_key")})
    if not keys:
        return []
    marks = ",".join("?" * len(keys))
    history = store.txn_rows(user, f"t.merchant_key IN ({marks}) AND t.amount < 0 AND t.status != 'unmatched'",
                             keys, order="t.purchase_date, t.id")
    with store.db() as conn:
        first = conn.execute("SELECT MIN(purchase_date) AS d FROM money_transactions WHERE user_id = ? "
                             "AND status != 'unmatched'", (user.id,)).fetchone()["d"]
    return find_unusual(candidates, history, datetime.date.fromisoformat(first) if first else None)
