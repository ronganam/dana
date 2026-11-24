"""Pay cycles, the Flow chart, what is left until payday, "can I afford it?" and what the user pays for
(docs/money.md, "Plan").

- :func:`salary`: the user's pay, the largest regular income into a bank account (a ``salary`` one first).
- :func:`cycle_bounds` / :func:`resolve_period`: a **pay cycle** runs from one salary to the next; the period
  names :data:`CYCLE_NAMES` (``this_pay_cycle``, ``last_pay_cycle``) resolve to one, every other name goes to
  ``periods.resolve``.
- :func:`flow_chart`: where a period's money went, for the Flow chart: income sources → the kinds (fixed,
  yearly, everyday, one-offs) or the accounts → groups, plus money set aside and what was left over (or
  taken from savings).
- :func:`left_until_payday`: the pay cycle's income, minus fixed and yearly charges (paid and still due) and
  money set aside, minus everyday spending so far = what is left for everyday spending until the next
  salary, a day, against the usual pace. One-offs are reported, not subtracted (they are paid from savings
  as often as from the month).
- :func:`afford`: the forecast with and without a purchase (once or in payments, on the card or the bank).
- :func:`register`: what the user pays for: every recurring and yearly charge by section (home, insurance,
  phone…) with its cost a month and a year, next date and price change.

Everything is computed in code from a ``dashboard.Book``; a model never produces a number.
"""

import datetime
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from core.users import User
from addons.money import categories, dashboard, insights, periods, store

CYCLE_NAMES: Tuple[str, ...] = ("this_pay_cycle", "last_pay_cycle")
# Salary payments this close together are one payday (a salary and its bonus, a split payment).
PAYDAY_CLUSTER_DAYS = 5
# Income sources shown by name in the Flow chart; the rest are "Other income".
FLOW_SOURCES = 5
# A purchase leaves the balance "fine" above this (the forecast's own warning line).
FLOOR_AGOROT = dashboard.LOW_BALANCE_AGOROT
DAYS_PER_MONTH = 365.25 / 12

KIND_LABELS = {"fixed": "Fixed", "yearly": "Yearly", "everyday": "Everyday", "one_off": "One-offs"}
# The register's sections, in this order when equal; a category in none goes to "other".
SECTIONS: Tuple[Tuple[str, str, frozenset], ...] = (
    ("home", "Home", frozenset({"housing", "utilities", "home"})),
    ("insurance", "Insurance", frozenset({"insurance"})),
    ("phone", "Phone & internet", frozenset({"phone_internet"})),
    ("subscriptions", "Subscriptions", frozenset({"subscriptions", "entertainment"})),
    ("car", "Car & transport", frozenset({"car", "fuel", "parking", "public_transport"})),
    ("health", "Health", frozenset({"health"})),
    ("family", "Kids & education", frozenset({"education", "kids", "pets"})),
    ("finance", "Loans, fees & taxes", frozenset({"fees", "taxes", "investments"})),
    ("other", "Other", frozenset()),
)


def _date(value: str) -> datetime.date:
    return datetime.date.fromisoformat(value[:10])


# --- Salary and pay cycles ---------------------------------------------------------------------

def salary(book: dashboard.Book) -> Optional[Dict[str, Any]]:
    """The user's pay: the regular monthly income into a bank account in the ``salary`` category with the
    largest monthly amount, else the largest regular monthly income there; None without one. The recurring
    item (``insights.detect_recurring``) with ``dates``: its paydays (the first of each cluster), oldest first."""
    items = [i for i in book.income if i["account_kind"] == "bank" and i["cadence"] == "monthly"]
    if not items:
        return None
    item = max(items, key=lambda i: (i["category"] == "salary", i["monthly_cost"]))
    keys = set(item.get("merchant_keys") or [item["merchant_key"]])
    days = sorted({_date(r["purchase_date"]) for r in book.rows
                   if r.get("merchant_key") in keys and r["amount"] > 0
                   and dashboard.flow(r) in categories.INCOME_FLOWS})
    paydays: List[datetime.date] = []
    for d in days:
        if not paydays or (d - paydays[-1]).days > PAYDAY_CLUSTER_DAYS:
            paydays.append(d)
    if not paydays:
        return None
    return {**item, "dates": [d.isoformat() for d in paydays]}


def next_payday(pay: Dict[str, Any], today: datetime.date) -> Tuple[datetime.date, bool]:
    """The next payday after the last one and whether it is late (expected before today, not paid yet): the
    item's next date on a working day; a late one is expected tomorrow."""
    expected = dashboard.bank_day(_date(pay["next_date"]))
    last = _date(pay["dates"][-1])
    if expected <= last:
        expected = dashboard.bank_day(insights._next("monthly", last))
    if expected <= today:
        return today + datetime.timedelta(days=1), True
    return expected, False


def cycle_bounds(book: dashboard.Book, pay: Dict[str, Any], back: int = 0) -> Optional[Tuple[datetime.date, datetime.date, bool]]:
    """The pay cycle ``back`` cycles before the current one: ``(start, end_exclusive, in_progress)``, from
    a payday to the next (the current one to the next expected payday); None before the first payday."""
    paydays = [_date(d) for d in pay["dates"] if _date(d) <= book.today]
    if len(paydays) <= back:
        return None
    start = paydays[-1 - back]
    if back == 0:
        end, _ = next_payday(pay, book.today)
        return start, end, True
    return start, paydays[-back], False


def _cycle_label(start: datetime.date, end_exclusive: datetime.date) -> str:
    return periods.span_label(start, end_exclusive - datetime.timedelta(days=1))


def cycle_period(book: dashboard.Book, back: int = 0) -> Optional[periods.Period]:
    """The pay cycle ``back`` cycles ago as a ``periods.Period`` (in progress: so far, compared with the same
    number of days of the cycle before and all of it; past: compared with the cycle before); None without a
    salary or before the first payday."""
    pay = salary(book)
    if pay is None:
        return None
    bounds = cycle_bounds(book, pay, back)
    if bounds is None:
        return None
    start, end, current = bounds
    key = CYCLE_NAMES[min(back, 1)] if back <= 1 else "custom"
    label = ("this pay cycle" if back == 0 else "last pay cycle" if back == 1 else "pay cycle") + \
        f" ({_cycle_label(start, end)})"
    prev = cycle_bounds(book, pay, back + 1)
    compare: List[periods.Period] = []
    if current:
        so_far = book.today + datetime.timedelta(days=1)
        if prev is not None:
            same = min(prev[0] + (so_far - start), prev[1])
            compare = [periods.Period(prev[0], same, "at this point last pay cycle"),
                       periods.Period(prev[0], prev[1], "all of last pay cycle")]
        return periods.Period(start, so_far, label, key, True, tuple(compare))
    if prev is not None:
        compare = [periods.Period(prev[0], prev[1], f"the cycle before ({_cycle_label(prev[0], prev[1])})")]
    return periods.Period(start, end, label, key, False, tuple(compare))


def resolve_period(user: User, name: Optional[str] = None, *, start: Any = None, end: Any = None,
                   book: Optional[dashboard.Book] = None) -> periods.Period:
    """``periods.resolve`` plus the pay-cycle names (:data:`CYCLE_NAMES`, spaces and dashes work too).

    Raises:
        ValueError: as ``periods.resolve``, and for a pay cycle when no salary is found.
    """
    key = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
    if start in (None, "") and key in CYCLE_NAMES:
        p = cycle_period(book or dashboard.load(user), CYCLE_NAMES.index(key))
        if p is None:
            raise ValueError("no pay cycle: Dana finds no regular salary into a bank account yet")
        return p
    return periods.resolve(name, start=start, end=end)


# --- The Flow chart ----------------------------------------------------------------------------

def _account_labels(user_accounts: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    return {a["id"]: store.account_label(a["company"], a["kind"], a["number"], a.get("label")) for a in user_accounts}


def flow_chart(book: dashboard.Book, period: periods.Period, by: str = "kind",
               accounts: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Where the period's money went: ``{period, by, income, spent, set_aside, left_over, from_savings,
    sources: [{key, label, amount}], middle: [{key, label, amount, groups: [{key, label, amount}]}]}``.

    ``sources``: income by category (the largest :data:`FLOW_SOURCES`, then "Other income"); ``middle``: the
    spending by kind (``by="kind"``: fixed, yearly, everyday, one-offs) or by account (``by="account"``,
    labels from ``accounts``), each split by group (refunds net inside a group; a group with nothing left is
    dropped, so ``spent`` = the sum of ``middle``). ``left_over`` = income − spent − set aside when positive,
    else ``from_savings`` is what was missing. Amounts are positive agorot."""
    if by not in ("kind", "account"):
        raise ValueError("by must be kind or account")
    lo, hi = period.start.isoformat(), period.end_exclusive.isoformat()
    income: Dict[str, int] = defaultdict(int)
    spend: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    set_aside = 0
    labels = _account_labels(accounts)
    for r in book.rows:
        if not lo <= r["purchase_date"] < hi:
            continue
        f = dashboard.flow(r)
        if f in categories.INCOME_FLOWS:
            income[r.get("category") or "other_income"] += r["amount"]
        elif f == "investment":
            set_aside -= r["amount"]
        kind = book.kinds.get(r["id"])
        if kind:
            middle = kind if by == "kind" else (r.get("account_id") or "")
            group = categories.group_of(r["category"]) if r.get("category") else "uncategorized"
            spend[middle][group] -= r["amount"]
    sources = sorted(((k, v) for k, v in income.items() if v > 0), key=lambda kv: -kv[1])
    shown = [{"key": k, "label": categories.CATEGORIES[k].label if k in categories.CATEGORIES else k, "amount": v}
             for k, v in sources[:FLOW_SOURCES]]
    rest = sum(v for _, v in sources[FLOW_SOURCES:])
    if rest:
        shown.append({"key": "other", "label": "Other income", "amount": rest})
    middle = []
    order = list(dashboard.KINDS) if by == "kind" else sorted(spend, key=lambda m: -sum(spend[m].values()))
    for m in order:
        groups = [{"key": g, "label": categories.GROUP_KEYS[g].label if g in categories.GROUP_KEYS else "Uncategorized",
                   "amount": v} for g, v in spend.get(m, {}).items() if v > 0]
        groups.sort(key=lambda g: -g["amount"])
        total = sum(g["amount"] for g in groups)
        if total:
            label = KIND_LABELS[m] if by == "kind" else labels.get(m, "Other account")
            middle.append({"key": m, "label": label, "amount": total, "groups": groups})
    total_in = sum(s["amount"] for s in shown)
    spent = sum(m["amount"] for m in middle)
    set_aside = max(0, set_aside)
    out = total_in - spent - set_aside
    return {"period": period.payload(), "by": by, "income": total_in, "spent": spent, "set_aside": set_aside,
            "left_over": max(0, out), "from_savings": max(0, -out), "sources": shown, "middle": middle}


# --- Left until payday -------------------------------------------------------------------------

def _due(book: dashboard.Book, after: datetime.date, end_exclusive: datetime.date) -> List[Dict[str, Any]]:
    """Fixed and yearly charges expected after ``after`` and before ``end_exclusive``: ``[{date, label,
    amount, kind}]`` (recurring ones at their typical amount, yearly ones at the last renewal)."""
    last = end_exclusive - datetime.timedelta(days=1)
    out: List[Dict[str, Any]] = []
    if last <= after:
        return out
    yearly_keys = {y["merchant_key"] for y in book.yearly}
    for item in book.charges:
        if item["cadence"] == "yearly" or item["merchant_key"] in yearly_keys:
            continue
        for d in dashboard._occurrences(item, after, last):
            out.append({"date": d.isoformat(), "label": item["merchant"], "amount": item["amount"], "kind": "fixed"})
    extra = [i for i in book.charges if i["cadence"] == "yearly" and i["merchant_key"] not in yearly_keys]
    for y in list(book.yearly) + extra:
        d = _date(y["next_date"])
        if after < d <= last:
            out.append({"date": d.isoformat(), "label": y["merchant"], "amount": y["amount"], "kind": "yearly"})
    out.sort(key=lambda e: (e["date"], -e["amount"]))
    return out


def left_until_payday(book: dashboard.Book) -> Optional[Dict[str, Any]]:
    """What is left for everyday spending until the next salary; None without a salary.

    ``{cycle {start, end (the last day)}, payday, late, days_left, salary {merchant, amount}, income, fixed,
    yearly, set_aside, everyday, one_off, due: [{date, label, amount, kind}], due_total, left, per_day,
    usual {rate (a month), ahead (for the days left)}, status: ok | tight | over}``. ``fixed`` / ``yearly``:
    charged so far this cycle; ``due``: still expected before payday (counted in ``left``)."""
    pay = salary(book)
    if pay is None:
        return None
    bounds = cycle_bounds(book, pay)
    if bounds is None:
        return None
    start, payday, _ = bounds
    _, late = next_payday(pay, book.today)
    tomorrow = book.today + datetime.timedelta(days=1)
    s = dashboard._sums(book, start, tomorrow)
    due = _due(book, book.today, payday)
    due_total = sum(e["amount"] for e in due)
    days_left = max(1, (payday - book.today).days)
    rates = dashboard.everyday_months(book, book.today)
    rate = (rates["card"][1] or 0) + (rates["bank"] or 0)
    ahead = int(round(rate * days_left / DAYS_PER_MONTH))
    left = s["income"] - s["fixed"] - s["yearly"] - s["set_aside"] - due_total - s["everyday"]
    status = "over" if left < 0 else "tight" if left < ahead else "ok"
    return {"cycle": {"start": start.isoformat(), "end": (payday - datetime.timedelta(days=1)).isoformat()},
            "payday": payday.isoformat(), "late": late, "days_left": days_left,
            "salary": {"merchant": pay["merchant"], "amount": pay["amount"]},
            "income": s["income"], "fixed": s["fixed"], "yearly": s["yearly"], "set_aside": s["set_aside"],
            "everyday": s["everyday"], "one_off": s["one_off"], "due": due[:12], "due_total": due_total,
            "left": left, "per_day": left // days_left if left > 0 else 0,
            "usual": {"rate": rate, "ahead": ahead}, "status": status}


# --- Can I afford it? --------------------------------------------------------------------------

def _low_from(series: Sequence[Dict[str, Any]], since: str) -> Dict[str, Any]:
    p = min((p for p in series if p["date"] >= since), key=lambda p: p["balance"])
    return {"date": p["date"], "balance": p["balance"]}


def afford(book: dashboard.Book, accounts: Sequence[Dict[str, Any]], amount: int, payments: int = 1,
           when: Optional[datetime.date] = None, on: str = "card", label: str = "") -> Optional[Dict[str, Any]]:
    """The forecast with and without a purchase of ``amount`` agorot (in ``payments`` monthly payments, from
    ``when``, default today, on the main card or the bank); None without a bank balance.

    ``{amount, payments, payment, date, on, before {low {date, balance}, end_balance}, after {…}, drop, verdict:
    fine | tight | short, floor, end, left_until_payday {before, after} | None}``. ``low``: the lowest expected
    balance from the day the first payment leaves the bank (a card's: its bill) to the end of the forecast (the last payment + 45 days, at least 90 days);
    ``drop``: how much lower it goes. ``fine``: it stays above :data:`FLOOR_AGOROT`; ``tight``: above zero;
    ``short``: below."""
    if amount <= 0 or not 1 <= payments <= 36 or on not in ("card", "bank"):
        raise ValueError("amount must be positive, payments 1–36 and on card or bank")
    now = dashboard.balance_at(book, accounts)
    if now is None:
        return None
    balance, as_of = now
    when = max(when or book.today, as_of + datetime.timedelta(days=1))
    days = min(dashboard.FORECAST_MAX_DAYS, max(90, (periods.add_months(when, payments) - as_of).days + 45))
    plan = {"date": when, "amount": amount, "payments": payments, "on": on, "label": label or "This purchase"}
    before = dashboard.project(book, accounts, balance, as_of, days)
    after = dashboard.project(book, accounts, balance, as_of, days, extra=(plan,))
    # From the day the first payment leaves the bank (a card purchase: its bill), the first day the two differ.
    since = next((a["date"] for a, b in zip(after["series"], before["series"]) if a["balance"] != b["balance"]),
                 when.isoformat())
    low_before = _low_from(before["series"], since)
    low_after = _low_from(after["series"], since)
    verdict = "fine" if low_after["balance"] >= FLOOR_AGOROT else "tight" if low_after["balance"] >= 0 else "short"
    payment = -(-amount // payments)
    left = left_until_payday(book)
    left_after = None
    if left is not None and when <= _date(left["cycle"]["end"]):
        left_after = {"before": left["left"], "after": left["left"] - (amount if payments == 1 else payment)}
    return {"amount": amount, "payments": payments, "payment": payment, "date": when.isoformat(), "on": on,
            "before": {"low": low_before, "end_balance": before["series"][-1]["balance"]},
            "after": {"low": low_after, "end_balance": after["series"][-1]["balance"]},
            "drop": low_before["balance"] - low_after["balance"], "verdict": verdict, "floor": FLOOR_AGOROT,
            "end": after["end"], "left_until_payday": left_after}


# --- What I pay for ----------------------------------------------------------------------------

def _section(category: Optional[str]) -> str:
    for key, _, cats in SECTIONS:
        if category in cats:
            return key
    return "other"


def _matches(item: Dict[str, Any], cats: Set[str], text: str) -> bool:
    if cats:
        return item.get("category") in cats
    return text in (item.get("merchant") or "").casefold() or any(text in k for k in item.get("merchant_keys") or [])


def register(book: dashboard.Book, about: Optional[str] = None) -> Dict[str, Any]:
    """What the user pays for: ``{monthly, yearly, income_typical, income_share, about, sections: [{key, label,
    monthly, yearly, items: [{key, merchant_keys, merchant, category, category_label, cadence, amount, payments,
    monthly, yearly, next_date, last_date, since, price_change, variable, account_label, account_kind}]}]}``.

    Recurring charges (not yearly ones) at their typical amount, and yearly charges at their last renewal;
    sections and items by their cost a year, largest first. ``about`` keeps only a category or group (by key
    or label, either language) or merchants whose name contains it."""
    yearly_keys = {y["merchant_key"] for y in book.yearly}
    items: List[Dict[str, Any]] = []
    for i in book.charges:
        if i["merchant_key"] in yearly_keys:
            continue
        per_year = i["amount"] if i["cadence"] == "yearly" else i["monthly_cost"] * 12
        items.append({"key": i["merchant_key"], "merchant_keys": i.get("merchant_keys") or [i["merchant_key"]],
                      "merchant": i["merchant"], "category": i["category"], "category_label": i["category_label"],
                      "cadence": i["cadence"], "amount": i["amount"], "payments": 1,
                      "monthly": i["monthly_cost"] if i["cadence"] != "yearly" else int(round(i["amount"] / 12)),
                      "yearly": per_year, "next_date": i["next_date"], "last_date": i["last_date"],
                      "since": i["first_date"], "price_change": i["price_change"], "variable": i["variable"],
                      "account_label": i["account_label"], "account_kind": i["account_kind"]})
    for y in book.yearly:
        items.append({"key": y["merchant_key"], "merchant_keys": [y["merchant_key"]], "merchant": y["merchant"],
                      "category": y["category"], "category_label": y["category_label"], "cadence": "yearly",
                      "amount": y["amount"], "payments": y.get("payments") or 1, "monthly": y["monthly_cost"],
                      "yearly": y["amount"], "next_date": y["next_date"], "last_date": y["last_date"],
                      "since": None, "price_change": None, "variable": False, "account_label": None,
                      "account_kind": y.get("account_kind")})
    if about and about.strip():
        cats = categories.resolve(about)
        text = about.strip().casefold()
        items = [i for i in items if _matches(i, cats, text)]
    by_section: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for i in items:
        by_section[_section(i["category"])].append(i)
    order = {key: n for n, (key, _, _) in enumerate(SECTIONS)}
    sections = []
    for key, label, _ in SECTIONS:
        its = sorted(by_section.get(key, []), key=lambda i: (-i["yearly"], i["merchant"]))
        if its:
            sections.append({"key": key, "label": label, "monthly": sum(i["monthly"] for i in its),
                             "yearly": sum(i["yearly"] for i in its), "items": its})
    sections.sort(key=lambda s: (-s["yearly"], order[s["key"]]))
    monthly = sum(s["monthly"] for s in sections)
    income = dashboard.cashflow(book, 12)["typical"]["income"]
    return {"monthly": monthly, "yearly": sum(s["yearly"] for s in sections), "income_typical": income,
            "income_share": round(monthly / income, 3) if income else None, "about": about or None,
            "sections": sections}


def link_savings(reg: Dict[str, Any], saving_items: Sequence[Dict[str, Any]]) -> None:
    """Add ``saving {key, title, status} | None`` to each register item: the first active savings item
    (``savings.overview``) whose merchant scope covers it (a price rise, a renewal, a subscription line)."""
    scoped = [(set((s.get("scope") or {}).get("merchant_keys") or []), s) for s in saving_items if s.get("active")]
    for section in reg["sections"]:
        for item in section["items"]:
            keys = set(item["merchant_keys"])
            hit = next((s for k, s in scoped if k & keys), None)
            item["saving"] = {"key": hit["key"], "title": hit["title"], "status": hit["status"]} if hit else None
