"""Saving opportunities (docs/money.md, "Savings").

**Ways to save** (group ``save``): concrete savings in the user's own rows — a
bill that went up, idle cash in an investment account or the bank,
foreign-currency markups, bank and card fees, insurance and car renewals, an
insurance review, subscriptions, the mortgage — plus the ways to save the
user's providers bring (``providers.SavingsRules``: a local utility or tax). **Spending to
watch** (group ``watch``): habits — a discretionary bucket creeping up, a
spree at one merchant, small charges that add up, two services of one niche,
fines and late fees, late-night shopping — each with an optional monthly
target.

Every number is computed here (or by a provider's detector) from the rows, the reference rates of the
user's provider (``providers.rates``: a central bank's, named ``Context.bank``) and
the price checks the user asked for (``providers.price_checks``); without
rates the items that need them (``cash``, ``cushion``) are left out, without price checks an item's check reads ``unavailable``;
each saving says its basis: ``measured``, ``increase`` or ``market``. Without
a credible saving an item has ``at_stake`` (money a year) and ``saving: None``.

:func:`detect` is pure over a :class:`Context`; :func:`overview` loads one,
stores what it found in ``money_savings`` (first seen, a snapshot) and adds
the user's choices: the status (never hidden — an item the user decided
about stays listed after its detector stops firing, rendered from its
snapshot with ``active: False``), the outcome measured from later rows, and
the target's progress. Writes (:func:`update` and its wrappers) register an
Undo (core/undo.py).

The text is English; a provider's ``SavingsRules`` adds the local names of
generic things (:func:`term`), its words in statements (:func:`words`), public
pages for steps (:func:`link`) and phone scripts in its language
(:func:`script`). :func:`make_item`, :func:`make_outcome` and the helpers
next to them are what a provider's detectors build with."""

import calendar
import datetime
import json
import logging
import math
import re
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from core import clock, undo, users
from core.text import untrusted
from core.users import User
from addons.money import dashboard, ingest, insights, periods, portfolio, providers, store

logger = logging.getLogger(__name__)

SAVE, WATCH = "save", "watch"
STATUSES = ("open", "doing", "done", "dismissed", "snoozed", "accepted", "watching")
GROUP_STATUSES = {SAVE: ("open", "doing", "done", "dismissed", "snoozed"), WATCH: ("open", "accepted", "watching")}
# An item is "new" this many days after Dana first found it.
NEW_DAYS = 7
SNOOZE_DAYS = 30
# A money market fund pays about the policy rate minus this (percent, its fees).
FUND_FEE = 0.3

# Ways to save.
BILL_CATEGORIES = frozenset({"phone_internet", "insurance", "subscriptions", "health", "utilities"})
BILL_MIN_SHARE = 0.10
BILL_MIN_MONTHLY = 1_000
BILL_MONTHS = 18
MOBILE = ("mobile", "mobile_and_internet")
CASH_MIN = 200_000
CASH_QUIET_DAYS = 90
CUSHION_DAYS = 90
CUSHION_MIN_DAYS = 60
CUSHION_BUFFER = 300_000
CUSHION_MIN_IDLE = 300_000
FX_MIN_CHARGED = 300_000
FX_MIN_MARKUP = 10_000
FEES_MIN = 6_000
INSURANCE_MIN = 150_000
SUBSCRIPTIONS_MIN = 30_000
MORTGAGE_MIN = 200_000
RENEWAL_LEAD_DAYS = 60
RENEWAL_LATE_DAYS = 14
# Spending to watch.
DISCRETIONARY = ("restaurants", "delivery", "shopping", "electronics", "clothing", "entertainment", "subscriptions")
BUCKETS = {"eating_out": ("restaurants", "delivery"), "shopping": ("shopping", "clothing", "electronics"),
           "entertainment": ("entertainment",), "subscriptions": ("subscriptions",)}
BUCKET_LABELS = {"eating_out": "Eating out", "shopping": "Shopping", "entertainment": "Entertainment",
                 "subscriptions": "Subscriptions"}
SPREE_ORDERS, SPREE_DAYS, SPREE_MIN = 5, 7, 50_000
LEAK_COUNT, LEAK_MEDIAN, LEAK_MIN, LEAK_RISE_MIN = 6, 6_000, 25_000, 6_000
CREEP_MORE_A_MONTH = 20_000
FX_OUTCOME_MIN = 50_000  # foreign purchases (at the official rate) before a new markup is judged
LEAK_RECENT_DAYS = 45  # a merchant not charged for this long is no longer a leak
OVERLAP_DAYS = 60
OVERLAP_CATEGORIES = ("subscriptions", "entertainment")
LATE_DAYS, LATE_COUNT, LATE_MIN = 90, 8, 100_000
NIGHT = (23, 5)
# Words in a merchant's name that place it in a niche (whole words).
NICHES: Dict[str, Tuple[str, ...]] = {
    "ai": ("openai", "chatgpt", "anthropic", "claude", "perplexity", "midjourney"),
    "video": ("netflix", "disney", "apple tv", "sting", "amazon prime", "hbo"),
    "music": ("spotify", "apple music", "youtube premium", "deezer"),
    "storage": ("google one", "icloud", "dropbox", "onedrive"),
}
NICHE_LABELS = {"ai": "AI assistants", "video": "video services", "music": "music services", "storage": "cloud storage"}
# Words (whole words, English; a provider adds its own: SavingsRules.words) for fines and late fees,
# an insurer, compulsory car insurance and a money market fund among the holdings.
PENALTY_WORDS = ("penalty", "parking fine", "traffic fine", "parking ticket", "late fee", "late payment",
                 "overdraft interest", "interest on arrears", "withdrawal fee")
INSURANCE_WORDS = ("insurance", "insurer")
COMPULSORY_WORDS = ("compulsory", "mandatory")
FUND_WORDS = ("money market",)

SERVICE_QUESTION = {"id": "service", "text": "What is this bill for?", "options": [
    {"value": "mobile", "label": "Mobile lines"}, {"value": "home_internet", "label": "Home internet"},
    {"value": "triple", "label": "Internet, TV and a home phone"},
    {"value": "mobile_and_internet", "label": "Mobile lines and home internet"}]}
LINES_QUESTION = {"id": "lines", "text": "How many mobile lines are on it?",
                  "options": [{"value": n, "label": "5 or more" if n == 5 else str(n)} for n in range(1, 6)]}
QUESTIONS = {"service": SERVICE_QUESTION, "lines": LINES_QUESTION}
# Price checks the start of which said "unavailable": (user id, topic) → when; shown for an hour.
UNAVAILABLE_SECONDS = 3600
_unavailable: Dict[Tuple[str, str], float] = {}


class Invalid(ValueError):
    """A change the item does not accept (``field`` names the part of the request)."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


class NotFound(LookupError):
    """No such item for the user (another user's key answers the same)."""


class NoCheck(ValueError):
    """The item has no price check."""


class NeedsAnswer(Exception):
    """The price check needs the answer to a question first."""

    def __init__(self, question: Dict[str, Any]) -> None:
        super().__init__(question["text"])
        self.question = question


@dataclass
class Context:
    """Everything :func:`detect` reads (built by :func:`context`)."""
    today: datetime.date
    book: dashboard.Book
    accounts: List[Dict[str, Any]]                    # store.list_accounts, with ``label``
    investments: List[Dict[str, Any]]                 # portfolio accounts with their holdings
    trades: List[Dict[str, Any]]                      # their recent activity
    rate: Optional[Dict[str, Any]]                    # providers.Rates.policy_rate
    fx: Dict[str, Dict[str, float]]                   # currency → {date: units of the user's currency a unit}
    answers: Dict[str, Dict[str, Any]]                # item key → the user's answers
    market: Optional[Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]]]  # None: no price checks
    unavailable: Set[str] = field(default_factory=set)  # price-check topics that said "unavailable"
    bank: str = "central bank"                        # providers.Rates.bank: whose rate it is
    rules: List[providers.SavingsRules] = field(default_factory=list)  # the user's providers' savings rules

    @property
    def Bank(self) -> str:
        """The central bank's name at the start of a sentence."""
        return self.bank[:1].upper() + self.bank[1:]

    @property
    def fixed(self) -> Set[str]:
        return insights.fixed_keys({"items": self.book.charges})

    def since(self, days: int) -> str:
        return (self.today - datetime.timedelta(days=days)).isoformat()


# --- Small helpers ----------------------------------------------------------------------------

sh = store.money_text


def round_half(x: float) -> int:
    """Round half up to an integer (hundredths)."""
    return int(math.floor(x + 0.5))


def as_date(value: str) -> datetime.date:
    return datetime.date.fromisoformat(value[:10])


def _whole(amount: Optional[int]) -> str:
    """``€1,235``: whole units of the user's currency, for titles."""
    return sh(amount or 0, cents=False)


def exact(amount: int) -> str:
    """``€109`` or ``€109.90`` (cents only when there are any), for the scripts to say."""
    return sh(abs(int(amount)), cents=bool(abs(int(amount)) % 100))


def _range(low: int, high: int) -> str:
    return _whole(high) if round_half(low / 100) == round_half(high / 100) else f"{_whole(low)}–{_whole(high)}"


def percent(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") + "%"


def day_text(value: Any) -> str:
    d = value if isinstance(value, datetime.date) else as_date(str(value))
    return periods.short_date(d)


def merchant_of(r: Dict[str, Any]) -> str:
    return r.get("merchant") or r.get("description") or ""


def norm(text: str) -> str:
    return ingest.merchant_key(text or "")


def _group(r: Dict[str, Any]) -> str:
    """The merchant a row belongs to for habits and subscriptions: its display name, normalised, so the
    spellings of one merchant count together (a cloud bill with an invoice code in every description has a
    new merchant key each month, one display name); the merchant key when there is no display name."""
    return norm(r.get("merchant") or "") or (r.get("merchant_key") or "")


def row_text(r: Dict[str, Any]) -> str:
    """Merchant, description and memo, normalised (quotes dropped, punctuation as spaces)."""
    return norm(" ".join(str(r.get(k) or "") for k in ("merchant_key", "merchant", "description", "memo")))


def has_word(text: str, word: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text) is not None


def _foreign(r: Dict[str, Any]) -> bool:
    return store.is_foreign(r)


def _spent(rows: Iterable[Dict[str, Any]]) -> int:
    """What the rows spent, positive hundredths (refunds reduce it)."""
    return -sum(r["amount"] for r in rows)


def _next_date(cadence: str, d: datetime.date) -> datetime.date:
    if cadence == "weekly":
        return d + datetime.timedelta(days=7)
    return periods.add_months(d, {"monthly": 1, "bimonthly": 2}.get(cadence, 12))


def _round50(amount: float) -> int:
    """Rounded to the nearest 50 units (half up)."""
    return int(math.floor(amount / 5_000 + 0.5)) * 5_000


def _monthly_series(rows: Iterable[Dict[str, Any]], months: Sequence[datetime.date]) -> List[Dict[str, Any]]:
    totals: Dict[str, int] = defaultdict(int)
    for r in rows:
        totals[r["purchase_date"][:7]] -= r["amount"]
    return [{"date": m.isoformat(), "amount": totals.get(m.strftime("%Y-%m"), 0)} for m in months]


def _last_months(today: datetime.date, n: int) -> List[datetime.date]:
    """The month starts of the last ``n`` months up to this one, oldest first."""
    first = periods.month_start(today)
    return [periods.add_months(first, -k) for k in range(n - 1, -1, -1)]


def fund_yield(ctx: Context) -> Optional[float]:
    """What a money market fund pays a year (percent), None without the policy rate."""
    if not ctx.rate or ctx.rate.get("rate") is None:
        return None
    return float(ctx.rate["rate"]) - FUND_FEE


def words(ctx: Context, meaning: str) -> Tuple[str, ...]:
    """The user's providers' words for ``meaning`` (``SavingsRules.words``: ``penalty``, ``insurance``…)."""
    return tuple(w for r in ctx.rules for w in r.words.get(meaning, ()))


def term(ctx: Context, key: str, text: str) -> str:
    """``text`` with the local name of the thing in brackets when a provider of the user's has one
    ("a money market fund (fonds monétaire)"), else ``text``."""
    local = next((r.terms[key] for r in ctx.rules if r.terms.get(key)), "")
    return f"{text} ({local})" if local else text


def link(ctx: Context, key: str) -> Optional[Tuple[str, str]]:
    """A public page a provider of the user's names for a step: ``(name, url)``, None without one."""
    return next((r.links[key] for r in ctx.rules if r.links.get(key)), None)


def script(ctx: Context, name: str, *args: Any) -> Optional[Dict[str, str]]:
    """What to say on the phone (``{lang, text}``) from a provider of the user's, None without one."""
    for r in ctx.rules:
        fn = r.scripts.get(name)
        if fn is not None:
            return {"lang": r.script_lang, "text": fn(*args)}
    return None


def _any_word(text: str, english: Sequence[str], local: Sequence[str]) -> bool:
    """Whether ``text`` (normalised) holds one of the English words (whole words) or a provider's (anywhere:
    languages that join words to their prefixes)."""
    return any(has_word(text, norm(w)) for w in english) or any(norm(w) in text for w in local if norm(w))


def saving_range(low: float, high: float, basis: str) -> Optional[Dict[str, Any]]:
    lo, hi = sorted((round_half(low), round_half(high)))
    if hi <= 0:
        return None
    return {"low": max(0, lo), "high": hi, "basis": basis}


def make_evidence(lines: Sequence[str], series: Sequence[Dict[str, Any]] = (),
              link: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"lines": list(lines), "series": list(series), "link": link}


def make_step(text: str, url: Optional[str] = None) -> Dict[str, Any]:
    return {"text": text, "url": url}


def make_item(key: str, kind: str, group: str, title: str, summary: str, *, saving: Optional[Dict[str, Any]] = None,
          at_stake: Optional[int] = None, effort: Optional[Dict[str, Any]] = None,
          timing: Optional[Dict[str, Any]] = None, evidence: Optional[Dict[str, Any]] = None,
          math_lines: Sequence[str] = (), steps: Sequence[Dict[str, Any]] = (),
          script: Optional[Dict[str, str]] = None,
          questions: Sequence[Dict[str, Any]] = (), market_info: Optional[Dict[str, Any]] = None,
          subitems: Optional[List[Dict[str, Any]]] = None, scope: Optional[Dict[str, Any]] = None,
          suggested_target: Optional[int] = None, baseline: Optional[Dict[str, Any]] = None,
          label: Optional[str] = None) -> Dict[str, Any]:
    """One detected item (lifecycle fields are added by :func:`overview`); ``script``: :func:`script`'s;
    ``_baseline`` and ``_label`` are kept in the snapshot, never returned."""
    return {"key": key, "type": kind, "group": group, "title": title, "summary": summary, "saving": saving,
            "at_stake": at_stake, "effort": effort, "timing": timing, "evidence": evidence or make_evidence([]),
            "math": list(math_lines), "steps": list(steps),
            "script": script, "questions": list(questions),
            "market": market_info, "subitems": subitems, "scope": scope, "suggested_target": suggested_target,
            "_baseline": baseline or {}, "_label": label or title}


def make_effort(minutes: int, how: str) -> Dict[str, Any]:
    return {"minutes": minutes, "how": how}


def market_state(ctx: Context, topic: str, params: Dict[str, Any], needs_answer: Optional[str] = None) -> Dict[str, Any]:
    """The item's price-check state: the newest check with these params (none while a question is unanswered)."""
    if ctx.market is None:
        return {"topic": topic, "params": params, "state": "unavailable", "checked_at": None, "stale": False,
                "items": [], "sources": [], "error": None, "needs_answer": needs_answer}
    try:
        found = None if needs_answer else ctx.market(topic, params)
    except ValueError:
        # Params the price checks refuse (a town name they can't take, a bill out of range): this item has no
        # check, and the rest of the page stands.
        return {"topic": topic, "params": None, "state": "none", "checked_at": None, "stale": False, "items": [],
                "sources": [], "error": None, "needs_answer": None}
    if found is None:
        state = "unavailable" if topic in ctx.unavailable and not needs_answer else "none"
        return {"topic": topic, "params": params, "state": state, "checked_at": None, "stale": False, "items": [],
                "sources": [], "error": None, "needs_answer": needs_answer}
    return {"topic": topic, "params": params, "state": found.get("state") or "none",
            "checked_at": found.get("checked_at"), "stale": bool(found.get("stale")),
            "items": list(found.get("items") or []), "sources": list(found.get("sources") or []),
            "error": found.get("error"), "needs_answer": None}


def checked(m: Optional[Dict[str, Any]]) -> bool:
    return bool(m and m["state"] == "ok" and m["items"])


def _night(r: Dict[str, Any]) -> bool:
    dt = store.parse(r.get("occurred_at"))
    if dt is None:
        return False
    local = dt.astimezone(clock.get_timezone())
    if local.hour == 0 and local.minute == 0 and local.second == 0:
        return False  # a date without a time
    return local.hour >= NIGHT[0] or local.hour < NIGHT[1]


def _habit(ctx: Context, r: Dict[str, Any]) -> bool:
    """A discretionary purchase in the user's currency that is not a one-off (habit trends)."""
    return (dashboard.is_spending(r) and r.get("category") in DISCRETIONARY and not _foreign(r)
            and ctx.book.kinds.get(r["id"]) != "one_off")


def in_scope(ctx: Context, r: Dict[str, Any], scope: Dict[str, Any]) -> bool:
    """Whether a row counts toward a watch item's target: a merchant scope counts all of the merchant's
    spending; the rest leave out one-offs and foreign-currency rows."""
    if not dashboard.is_spending(r):
        return False
    if scope.get("merchants"):
        return _group(r) in scope["merchants"]
    if scope.get("merchant_keys"):
        return r.get("merchant_key") in scope["merchant_keys"]
    if ctx.book.kinds.get(r["id"]) == "one_off" or _foreign(r):
        return False
    if scope.get("categories") and r.get("category") not in scope["categories"]:
        return False
    if scope.get("keywords") and (dashboard.flow(r) == "cash" or not any(norm(w) in row_text(r) for w in scope["keywords"])):
        return False
    if scope.get("hours") and not _night(r):
        return False
    return True


# --- Ways to save -----------------------------------------------------------------------------

def _bills(ctx: Context) -> List[Dict[str, Any]]:
    out = []
    since_limit = periods.add_months(ctx.today, -BILL_MONTHS).isoformat()
    for c in ctx.book.charges:
        ch = c.get("price_change")
        if c.get("category") not in BILL_CATEGORIES or not ch or ch["to"] <= ch["from"] or ch["since"] < since_limit:
            continue
        if any(r.own_bills is not None and r.own_bills(c) for r in ctx.rules):
            continue
        per = insights.per_month(c["cadence"])
        if ch["to"] < ch["from"] * (1 + BILL_MIN_SHARE) or (ch["to"] - ch["from"]) * per < BILL_MIN_MONTHLY:
            continue
        key = f"bill:{c['merchant_key']}"
        keys = c.get("merchant_keys") or [c["merchant_key"]]
        rows = [r for r in ctx.book.rows if r.get("merchant_key") in keys and r["amount"] < 0
                and r["purchase_date"] >= since_limit]
        increase = (ch["to"] - ch["from"]) * 12 * per
        current = ch["to"] * per
        every = insights.cadence_text(c["cadence"])
        saving = None
        questions: List[Dict[str, Any]] = []
        market_info = None
        offer = None
        math_lines = [f"{sh(ch['to'])} − {sh(ch['from'])} = {sh(ch['to'] - ch['from'])} more {every}",
                      f"{sh(ch['to'] - ch['from'])} × {round_half(12 * per)} = {sh(round_half(increase))} a year"]
        steps = [make_step(f"Call {c['merchant']} and ask for the old price ({sh(ch['from'])} {every}) back"),
                 make_step("If they refuse, ask which cheaper plan gives you the same"),
                 make_step("Still no? Cancel or switch: the new price is what you'd keep paying")]
        if c["category"] == "phone_internet":
            answers = ctx.answers.get(key, {})
            service, lines = answers.get("service"), answers.get("lines")
            questions.append({**SERVICE_QUESTION, "answer": service})
            if service in MOBILE:
                questions.append({**LINES_QUESTION, "answer": lines})
            needs = "service" if not service else ("lines" if service in MOBILE and not lines else None)
            params = {"service": service, "lines": lines if service in MOBILE else None,
                      "current_price": round_half(current / 100)}
            market_info = market_state(ctx, "telecom", params, needs)
            if checked(market_info) and service != "mobile_and_internet":
                # What each offer would cost for the user's lines (a mobile price is usually per line; an offer
                # says how many lines its price covers); a bundle of mobile and internet has no fair comparison.
                n = lines if service in MOBILE and lines else 1
                costed = sorted(((round_half(i["avg_12"] * n / (i.get("lines") or 1)) if service in MOBILE else i["avg_12"], i)
                                 for i in market_info["items"] if isinstance(i.get("avg_12"), int)),
                                key=lambda pair: pair[0])
                if costed:
                    avgs = [cost for cost, _ in costed]
                    ref = avgs[2] if len(avgs) >= 3 else statistics.median(avgs)
                    saving = saving_range((current - ref) * 12, (current - avgs[0]) * 12, "market")
                    if saving:
                        best, offer = costed[0]
                        lines_text = f" for {n} lines" if n > 1 else ""
                        math_lines.append(f"Best offer: {offer['provider']} {offer['plan']}, {sh(best)} a month{lines_text} "
                                          f"over 12 months: ({sh(round_half(current))} − {sh(best)}) × 12 = "
                                          f"{sh(saving['high'])} a year")
                        steps[1] = make_step(f"Tell them {offer['provider']} offers {offer['plan']} at "
                                         f"{sh(best)} a month{lines_text} (12-month average)",
                                         (offer.get("source") or {}).get("url"))
                        offer = {**offer, "avg_12": best}
            elif checked(market_info):
                math_lines.append("A bundle of mobile and internet: compare the offers with what your bundle includes")
            else:
                steps[1] = make_step("Run a price check to see what other companies charge for the same")
        if saving is None:
            saving = saving_range(increase, increase, "increase")
        out.append(make_item(
            key, "bill", SAVE, f"{c['merchant']} went up {_whole((ch['to'] - ch['from']) * per)} a month",
            f"From {sh(ch['from'])} to {sh(ch['to'])} {every} since {day_text(ch['since'])}: "
            f"{sh(round_half(increase))} a year more. Ask for the old price back or switch.",
            saving=saving, at_stake=round_half(ch["to"] * 12 * per), effort=make_effort(20, "call"),
            evidence=make_evidence([f"{sh(ch['from'])} {every} until {day_text(ch['since'])}, {sh(ch['to'])} since",
                                f"{c['category_label']} · {c['account_label']}"],
                               [{"date": r["purchase_date"], "amount": -r["amount"]} for r in rows],
                               {"view": "transactions", "q": c["merchant"]}),
            math_lines=math_lines, steps=steps, script=script(ctx, "bill", c["merchant"], ch["from"], ch["to"], c["cadence"], offer),
            questions=questions, market_info=market_info,
            baseline={"amount": ch["to"], "from": ch["from"], "cadence": c["cadence"], "merchant_keys": keys,
                      "category": c["category"]},
            label=c["merchant"]))
    return out


def _cash(ctx: Context) -> List[Dict[str, Any]]:
    y = fund_yield(ctx)
    if y is None or y <= 0:
        return []
    out = []
    quiet = ctx.since(CASH_QUIET_DAYS)
    for a in ctx.investments:
        cash = a.get("cash") or 0
        if cash < CASH_MIN:
            continue
        if any(t.get("account_id") == a["id"] and t.get("kind") == "interest" and t["date"] >= quiet for t in ctx.trades):
            continue
        funds = [h for h in a.get("holdings") or []
                 if _any_word(norm(h.get("name") or ""), FUND_WORDS, words(ctx, "money_market_fund"))]
        value = cash * y / 100
        first = (make_step(f"You already hold {funds[0]['name']}: buy more of it with the cash") if funds else
                 make_step(f"Buy {term(ctx, 'money_market_fund', 'a money market fund')} with the cash in {a['label']}"))
        out.append(make_item(
            f"cash:{a['id']}", "cash", SAVE, f"{_whole(cash)} cash earning nothing in {a['label']}",
            f"A money market fund pays about {percent(y)} a year now (the {ctx.bank} rate minus fees): "
            f"about {sh(round_half(value))} a year on it.",
            saving=saving_range(value, value, "measured"), at_stake=round_half(value), effort=make_effort(10, "app"),
            evidence=make_evidence([f"{sh(cash)} cash in {a['label']}" + (f" as of {day_text(a['value_at'])}" if a.get("value_at") else ""),
                                f"No interest on it in the last {CASH_QUIET_DAYS} days"], [], {"view": "investments"}),
            math_lines=[f"{sh(cash)} × ({percent(ctx.rate['rate'])} − {percent(FUND_FEE)}) = {sh(round_half(value))} a year",
                        f"{ctx.Bank} rate {percent(ctx.rate['rate'])} on {day_text(ctx.rate['date'])}"],
            steps=[first, make_step("Keep a little cash for fees and your next buy"),
                   make_step("A fund like that can be sold any trading day")],
            baseline={"cash": cash, "account_id": a["id"], "yield": y}, label=f"Cash in {a['label']}"))
    return out


def _cushion(ctx: Context) -> List[Dict[str, Any]]:
    y = fund_yield(ctx)
    if y is None or y <= 0:
        return []
    history = [p for p in dashboard.balance_history(ctx.book, ctx.accounts, CUSHION_DAYS)
               if p["date"] >= ctx.since(CUSHION_DAYS)]
    if len(history) < CUSHION_MIN_DAYS:
        return []
    low_point = min(history, key=lambda p: p["balance"])
    floor = low_point["balance"]
    fixed = dashboard.cashflow(ctx.book, 12)["typical"]["fixed"] or 0
    idle_fixed, idle_buffer = floor - fixed, floor - CUSHION_BUFFER
    high_idle, low_idle = max(idle_fixed, idle_buffer), max(0, min(idle_fixed, idle_buffer))
    if high_idle < CUSHION_MIN_IDLE:
        return []
    saving = saving_range(low_idle * y / 100, high_idle * y / 100, "measured")
    return [make_item(
        "cushion", "cushion", SAVE, f"{_range(low_idle, high_idle)} sitting idle in the bank account",
        f"The balance never went below {sh(floor)} in the last {CUSHION_DAYS} days. Keeping a month of fixed "
        f"costs there and moving the rest to a money market fund or a deposit earns about "
        f"{_range(saving['low'], saving['high'])} a year." if saving else "",
        saving=saving, at_stake=saving["high"] if saving else None, effort=make_effort(10, "app"),
        evidence=make_evidence([f"Lowest balance in the last {CUSHION_DAYS} days: {sh(floor)} on {day_text(low_point['date'])}",
                            f"A month of fixed costs is about {sh(fixed)}"],
                           [{"date": p["date"], "amount": p["balance"]} for p in history], {"view": "cashflow"}),
        math_lines=[f"{sh(floor)} − {sh(fixed)} (a month of fixed costs) = {sh(idle_fixed)}",
                    f"{sh(floor)} − {sh(CUSHION_BUFFER)} = {sh(idle_buffer)}",
                    f"× {percent(y)} a year (the {ctx.bank} rate {percent(ctx.rate['rate'])} minus fees)"],
        steps=[make_step(f"Keep about {sh(max(fixed, CUSHION_BUFFER))} in the account for the month's bills"),
               make_step(f"Move the rest to {term(ctx, 'money_market_fund', 'a money market fund')} or "
                         f"{term(ctx, 'deposit', 'a short deposit')} in the bank's app"),
               make_step("Take it back any time the balance runs low")],
        baseline={"floor": floor, "idle_low": low_idle, "idle_high": high_idle, "yield": y,
                  "saving_low": saving["low"] if saving else 0},
        label="Idle money in the bank")]


def _fx_lines(ctx: Context, rows: Iterable[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], float, float]]:
    """``(row, base, markup)`` for foreign purchases with a known rate (base: at the central bank's rate, in
    hundredths of the user's currency)."""
    out = []
    for r in rows:
        rate = providers.rate_on(ctx.fx.get((r.get("orig_currency") or "").upper(), {}), r["purchase_date"])
        if rate is None:
            continue
        base = abs(r["orig_amount"]) * rate
        out.append((r, base, abs(r["amount"]) - base))
    return out


def _fx_rows(ctx: Context, since: str) -> List[Dict[str, Any]]:
    return [r for r in ctx.book.rows if r.get("account_kind") == "card" and dashboard.flow(r) == "purchase"
            and r["amount"] < 0 and _foreign(r) and r["purchase_date"] >= since]


def _fx(ctx: Context) -> List[Dict[str, Any]]:
    by_account: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in _fx_rows(ctx, ctx.since(365)):
        by_account[r.get("account_id") or ""].append(r)
    labels = {a["id"]: a["label"] for a in ctx.accounts}
    out = []
    for account_id, rows in by_account.items():
        lines = _fx_lines(ctx, rows)
        charged = sum(abs(r["amount"]) for r, _, _ in lines)
        markup = sum(m for _, _, m in lines)
        base = sum(b for _, b, _ in lines)
        if charged < FX_MIN_CHARGED or markup < FX_MIN_MARKUP or base <= 0:
            continue
        total_pct = markup / base * 100
        median_pct = statistics.median(m / b * 100 for _, b, m in lines)
        currencies = sorted({(r.get("orig_currency") or "").upper() for r, _, _ in lines})
        label = labels.get(account_id, "your card")
        m = market_state(ctx, "fx_card", {})
        saving = saving_range(markup, markup, "measured")
        math_lines = [f"Charged {sh(charged)} for purchases worth {sh(round_half(base))} at the {ctx.bank} rate of each day",
                      f"{sh(charged)} − {sh(round_half(base))} = {sh(round_half(markup))} ({percent(round(total_pct, 2))})"]
        summary = (f"{len(lines)} purchases in {'/'.join(currencies)} on {label} cost {percent(round(total_pct, 1))} over "
                   f"the {ctx.bank} rate: {sh(round_half(markup))} in 12 months.")
        if checked(m):
            nets = []
            for alt in m["items"]:
                fee_pct = alt.get("fx_fee_pct") if isinstance(alt.get("fx_fee_pct"), (int, float)) else None
                if fee_pct is None:
                    continue
                cost = base * fee_pct / 100 + (alt.get("monthly_fee") or 0) * 12
                nets.append((markup - cost, alt))
            if nets:
                nets.sort(key=lambda n: -n[0])
                best_net, best = nets[0]
                saving = saving_range(statistics.median(n for n, _ in nets), best_net, "market")
                if saving:
                    math_lines.append(f"With {best['name']} ({percent(best['fx_fee_pct'])} fee, {sh(best.get('monthly_fee') or 0)} "
                                      f"a month): {sh(round_half(markup))} − {sh(round_half(base * best['fx_fee_pct'] / 100 + (best.get('monthly_fee') or 0) * 12))} "
                                      f"= {sh(round_half(best_net))} a year")
                else:
                    summary += " The price check found no card that would cost less."
        out.append(make_item(
            f"fx:{account_id}", "fx", SAVE, f"{_whole(markup)} a year in foreign-currency fees", summary,
            saving=saving, at_stake=round_half(markup), effort=make_effort(20, "online"),
            evidence=make_evidence([f"{len(lines)} purchases in foreign currency in 12 months, {sh(charged)} charged",
                                f"{percent(round(total_pct, 2))} over the {ctx.bank} rate in total, median "
                                f"{percent(round(median_pct, 2))} a purchase"],
                               _monthly_series([{"purchase_date": r["purchase_date"], "amount": -mk} for r, _, mk in lines],
                                               _last_months(ctx.today, 12)),
                               {"view": "transactions"}),
            math_lines=math_lines,
            steps=[make_step("Run a price check for cards and wallets without a conversion fee"),
                   make_step("Use one of them abroad and for purchases in dollars or euros online"),
                   make_step(f"Or ask the card company to waive {term(ctx, 'conversion_fee', 'the conversion fee')} on {label}")],
            market_info=m,
            baseline={"markup": round_half(markup), "base": round_half(base), "pct": total_pct, "account_id": account_id},
            label=f"Foreign-currency fees on {label}"))
    return out


def _is_penalty(ctx: Context, r: Dict[str, Any]) -> bool:
    """A fine, late fee, interest or withdrawal fee (never the cash taken out itself)."""
    if dashboard.flow(r) == "cash":
        return False
    return _any_word(row_text(r), PENALTY_WORDS, words(ctx, "penalty"))


def _fee_rows(ctx: Context, since: str) -> List[Dict[str, Any]]:
    return [r for r in ctx.book.rows if r["purchase_date"] >= since and r["amount"]
            and (dashboard.flow(r) == "fee" or r.get("category") == "fees") and not _is_penalty(ctx, r)]


def _fees(ctx: Context) -> List[Dict[str, Any]]:
    rows = _fee_rows(ctx, ctx.since(365))
    total = _spent(rows)
    if total < FEES_MIN:
        return []
    names = sorted({merchant_of(r) for r in rows if r["amount"] < 0})
    return [make_item(
        "fees", "fees", SAVE, f"{_whole(total)} in bank and card fees",
        f"{sh(total)} in the last 12 months. Ask to waive them, or move to "
        f"{term(ctx, 'basic_track', 'the bank\'s basic track')}.",
        saving=saving_range(total, total, "measured"), at_stake=total, effort=make_effort(15, "call"),
        evidence=make_evidence([f"{sum(1 for r in rows if r['amount'] < 0)} fees in 12 months: {', '.join(names[:4])}"],
                           _monthly_series(rows, _last_months(ctx.today, 12)), {"view": "transactions", "category": "fees"}),
        math_lines=[f"Fees in the last 12 months: {sh(total)}"],
        steps=[make_step("Call the bank and ask to waive the account fees"),
               make_step(f"Ask for {term(ctx, 'basic_track', 'the basic track')} if your activity fits it"),
               make_step("For a card fee, ask the card company to waive it or switch to a card without one")],
        script=script(ctx, "fees", total),
        baseline={"fees": total, "monthly": total / 12}, label="Fees")]


def _renewals(ctx: Context) -> List[Dict[str, Any]]:
    out = []
    for y in ctx.book.yearly:
        text = norm(y["merchant"] + " " + y["merchant_key"])
        if not (y["category"] == "insurance"
                or (y["category"] == "car" and _any_word(text, INSURANCE_WORDS, words(ctx, "insurance")))):
            continue
        nd = as_date(y["next_date"])
        if ctx.today > nd + datetime.timedelta(days=RENEWAL_LATE_DAYS):
            continue
        start = nd - datetime.timedelta(days=RENEWAL_LEAD_DAYS)
        steps = [make_step(f"Ask {y['merchant']} for the renewal price before {day_text(nd)}"),
                 make_step("Get quotes from two other insurers for the same cover"),
                 make_step("Tell them the best quote; switch if they won't match it")]
        prices = link(ctx, "car_insurance_prices")
        if prices and _any_word(text, COMPULSORY_WORDS, words(ctx, "compulsory_insurance")):
            steps.insert(1, make_step(f"Compare compulsory car insurance prices on {prices[0]}", prices[1]))
        series = [{"date": r["purchase_date"], "amount": insights.plan_total(r)} for r in ctx.book.rows
                  if r.get("merchant_key") == y["merchant_key"] and r["amount"] < 0]
        out.append(make_item(
            f"renewal:{y['merchant_key']}:{nd:%Y-%m}", "renewal", SAVE, f"{y['merchant']} renews around {day_text(nd)}",
            f"Last year {sh(y['amount'])}" + (f" in {y['payments']} payments" if y["payments"] > 1 else "")
            + ". Compare quotes before it renews.",
            at_stake=y["amount"], effort=make_effort(30, "compare"),
            timing={"from": start.isoformat(), "until": nd.isoformat(), "label": f"Renews around {day_text(nd)}"},
            evidence=make_evidence([f"Last renewal {sh(y['amount'])} on {day_text(y['last_date'])}"], series,
                               {"view": "transactions", "q": y["merchant"]}),
            math_lines=[f"Last year: {sh(y['amount'])}"], steps=steps,
            baseline={"amount": y["amount"], "merchant_key": y["merchant_key"], "next_date": y["next_date"],
                      "last_date": y["last_date"]},
            label=y["merchant"]))
    return out


def _insurance_review(ctx: Context) -> List[Dict[str, Any]]:
    policies = [(c["merchant"], c["monthly_cost"] * 12, f"{sh(c['amount'])} {insights.cadence_text(c['cadence'])}")
                for c in ctx.book.charges if c.get("category") == "insurance" and c["cadence"] != "yearly"]
    policies += [(y["merchant"], y["amount"], "once a year") for y in ctx.book.yearly if y["category"] == "insurance"]
    total = sum(p[1] for p in policies)
    if total < INSURANCE_MIN:
        return []
    listing = link(ctx, "insurance_policies")
    first = (make_step(f"Check {listing[0]} for every policy in your name", listing[1]) if listing else
             make_step("List every policy you pay for, from your insurers' and your employer's papers"))
    return [make_item(
        "insurance_review", "insurance_review", SAVE, f"Review your insurance: {_whole(total)} a year",
        f"{len(policies)} policies. " + (f"{listing[0]} lists every policy in your name, so the same cover paid twice "
                                         "stands out." if listing else "The same cover paid twice is money for nothing."),
        at_stake=total, effort=make_effort(30, "review"),
        evidence=make_evidence([f"{name}: {sh(yearly)} a year ({how})" for name, yearly, how in policies], [],
                           {"view": "transactions", "category": "insurance"}),
        math_lines=[" + ".join(sh(p[1]) for p in policies) + f" = {sh(total)} a year"],
        steps=[first,
               make_step("Look for the same cover twice (health or life insurance through work and privately)"),
               make_step("Cancel the duplicate with the insurer that has it")],
        baseline={"total": total}, label="Insurance")]


def _sub_lines(ctx: Context) -> List[Dict[str, Any]]:
    lines: Dict[str, Dict[str, Any]] = {}
    for c in ctx.book.charges:
        if c.get("category") == "subscriptions":
            lines[c["merchant_key"]] = {"key": c["merchant_key"], "merchant": c["merchant"], "amount": c["amount"],
                                        "cadence": c["cadence"], "yearly": c["monthly_cost"] * 12,
                                        "next_date": c["next_date"]}
    for y in ctx.book.yearly:
        if y["category"] == "subscriptions" and y["merchant_key"] not in lines:
            lines[y["merchant_key"]] = {"key": y["merchant_key"], "merchant": y["merchant"], "amount": y["amount"],
                                        "cadence": "yearly", "yearly": y["amount"], "next_date": y["next_date"]}
    # The rest by merchant (display name): one service's spellings and invoice codes are one line.
    taken = {k for c in ctx.book.charges if c["merchant_key"] in lines for k in c.get("merchant_keys") or [c["merchant_key"]]}
    taken |= set(lines)
    named = {norm(l["merchant"]) for l in lines.values()}
    rest: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in ctx.book.rows:
        if (r.get("category") == "subscriptions" and r["amount"] < 0 and r["purchase_date"] >= ctx.since(365)
                and _group(r) and r.get("merchant_key") not in taken and _group(r) not in named
                and dashboard.is_spending(r)):
            rest[_group(r)].append(r)
    for key, rows in rest.items():
        if len(rows) >= 2:
            lines[key] = {"key": key, "merchant": merchant_of(rows[-1]), "amount": -rows[-1]["amount"], "cadence": None,
                          "yearly": _spent(rows), "next_date": None}
    return sorted(lines.values(), key=lambda l: (-l["yearly"], l["merchant"]))


def _subscriptions(ctx: Context) -> List[Dict[str, Any]]:
    lines = _sub_lines(ctx)
    answers = ctx.answers.get("subscriptions", {})
    total = sum(l["yearly"] for l in lines)
    if total < SUBSCRIPTIONS_MIN:
        return []
    stopped = {k[4:] for k, v in answers.items() if k.startswith("sub:") and isinstance(v, dict)
               and v.get("choice") == "cancel" and _missed_charges(ctx, k[4:], v)}
    lines = [l for l in lines if l["key"] not in stopped]
    total = sum(l["yearly"] for l in lines)
    subitems = []
    for l in lines:
        choice = (answers.get(f"sub:{l['key']}") or {}).get("choice")
        subitems.append({**l, "choice": choice, "active": True})
    seen = {l["key"] for l in lines}
    for name, value in sorted(answers.items()):
        if name.startswith("sub:") and isinstance(value, dict) and name[4:] not in seen:
            subitems.append({"key": name[4:], "merchant": value.get("merchant") or name[4:], "amount": value.get("amount"),
                             "cadence": value.get("cadence"), "yearly": value.get("yearly"),
                             "next_date": value.get("next_date"), "choice": value.get("choice"), "active": False})
    cancel = sum(s["yearly"] or 0 for s in subitems if s["choice"] == "cancel" and s["active"])
    every = {"monthly": "a month", "bimonthly": "every 2 months", "weekly": "a week", "yearly": "a year"}
    return [make_item(
        "subscriptions", "subscriptions", SAVE, f"Subscriptions: {_whole(total)} a year",
        f"{len(lines)} subscriptions. Mark the ones to cancel" + (f": {sh(cancel)} a year marked so far." if cancel else "."),
        saving=saving_range(cancel, cancel, "measured") if cancel else None, at_stake=total, effort=make_effort(10, "review"),
        evidence=make_evidence([f"{l['merchant']}: {sh(l['amount'])} {every.get(l['cadence'] or '', 'now and then')}"
                            for l in lines],
                           _monthly_series([r for r in ctx.book.rows if r.get("category") == "subscriptions"
                                            and dashboard.is_spending(r)], _last_months(ctx.today, 12)),
                           {"view": "transactions", "category": "subscriptions"}),
        math_lines=[" + ".join(sh(l["yearly"]) for l in lines) + f" = {sh(total)} a year"],
        steps=[make_step("Mark each subscription keep or cancel"),
               make_step("Cancel in each service's app or site (the card stops being charged from the next cycle)")],
        subitems=subitems, baseline={"total": total}, label="Subscriptions")]


def _mortgage(ctx: Context) -> List[Dict[str, Any]]:
    loans = [c for c in ctx.book.charges if c.get("flow") == "loan" and c["cadence"] == "monthly"
             and c["amount"] >= MORTGAGE_MIN]
    if not loans:
        return []
    monthly = sum(c["amount"] for c in loans)
    lines = [f"{c['merchant']}: {sh(c['amount'])} a month" for c in loans]
    rate = ctx.rate
    if rate and rate.get("year_ago") is not None:
        lines.append(f"{ctx.Bank} rate {percent(rate['rate'])} now, {percent(rate['year_ago'])} a year ago")
    elif rate:
        lines.append(f"{ctx.Bank} rate {percent(rate['rate'])} now")
    summary = f"{sh(monthly)} a month, {sh(monthly * 12)} a year."
    if rate and rate.get("year_ago") is not None and rate["rate"] < rate["year_ago"]:
        summary += (f" Rates are lower than a year ago ({percent(rate['rate'])} vs {percent(rate['year_ago'])}): offers from "
                    f"other banks may be worth a look.")
    else:
        summary += " Worth comparing offers once in a while."
    return [make_item(
        "mortgage", "mortgage", SAVE, "Mortgage: worth checking", summary, at_stake=monthly * 12,
        effort=make_effort(60, "compare"),
        evidence=make_evidence(lines, [{"date": r["purchase_date"], "amount": -r["amount"]} for r in ctx.book.rows
                                   if r.get("merchant_key") in {k for c in loans for k in c.get("merchant_keys") or [c["merchant_key"]]}
                                   and r["amount"] < 0 and r["purchase_date"] >= ctx.since(365)],
                           {"view": "transactions", "category": "housing"}),
        math_lines=[f"{sh(monthly)} × 12 = {sh(monthly * 12)} a year"],
        steps=[make_step(f"Ask the bank for {term(ctx, 'early_repayment', 'an early-repayment statement')}"),
               make_step("Get offers from two other banks or a mortgage advisor for the balance"),
               make_step("Mind the early-repayment fee on fixed-rate tracks before switching")],
        baseline={"monthly": monthly}, label="Mortgage")]


# --- Spending to watch ------------------------------------------------------------------------

def _creep(ctx: Context) -> List[Dict[str, Any]]:
    months = dashboard.whole_months(ctx.book, ctx.today, 12)  # newest first
    if len(months) < 6:
        return []
    # The last half of the whole months against the half before (6 and 6 from a year of data); at least
    # 200 a month more.
    span = min(6, len(months) // 2)
    more = CREEP_MORE_A_MONTH * span
    keys = [m.strftime("%Y-%m") for m in months]
    out = []
    shown = sorted(months) + [periods.month_start(ctx.today)]
    for bucket, cats in BUCKETS.items():
        rows = [r for r in ctx.book.rows if _habit(ctx, r) and r.get("category") in cats]
        per: Dict[str, int] = defaultdict(int)
        by_cat: Dict[str, int] = defaultdict(int)
        for r in rows:
            per[r["purchase_date"][:7]] -= r["amount"]
            if r["purchase_date"][:7] in keys[:span]:
                by_cat[r["category"]] -= r["amount"]
        new = [per.get(k, 0) for k in keys[:span]]
        old = [per.get(k, 0) for k in keys[span:2 * span]]
        s_new, s_old = sum(new), sum(old)
        if s_new < 1.5 * s_old or s_new - s_old < more or statistics.median(new) < 1.3 * statistics.median(old):
            continue
        new_avg, old_avg = s_new / span, s_old / span
        label = BUCKET_LABELS[bucket]
        at_stake = round_half((new_avg - old_avg) * 12)
        top = max(by_cat, key=lambda k: by_cat[k]) if by_cat else cats[0]
        out.append(make_item(
            f"creep:{bucket}", "creep", WATCH, f"{label} is up: {_whole(new_avg)} a month",
            f"{sh(round_half(new_avg))} a month in the last {span} months, {sh(round_half(old_avg))} in the {span} before: "
            f"{sh(at_stake)} a year more at this pace.",
            at_stake=at_stake,
            evidence=make_evidence([f"Last {span} months: {sh(s_new)}", f"The {span} before: {sh(s_old)}"],
                               _monthly_series(rows, shown), {"view": "transactions", "category": top}),
            math_lines=[f"{sh(s_new)} ÷ {span} = {sh(round_half(new_avg))} a month", f"{sh(s_old)} ÷ {span} = {sh(round_half(old_avg))} a month",
                        f"({sh(round_half(new_avg))} − {sh(round_half(old_avg))}) × 12 = {sh(at_stake)} a year"],
            steps=[make_step(f"Set a monthly target: halfway back is {_whole(_round50((old_avg + new_avg) / 2))}")],
            scope={"categories": list(cats)}, suggested_target=_round50((old_avg + new_avg) / 2),
            baseline={"pace": round_half(new_avg), "old_pace": round_half(old_avg)}, label=label))
    return out


def _sprees(ctx: Context) -> List[Dict[str, Any]]:
    by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in ctx.book.rows:
        if (dashboard.is_spending(r) and r["amount"] < 0 and r.get("category") in DISCRETIONARY
                and _group(r) and r["purchase_date"] >= ctx.since(365)):
            by_key[_group(r)].append(r)
    out = []
    for key, rows in by_key.items():
        rows.sort(key=lambda r: (r["purchase_date"], r.get("occurred_at") or "", r["id"]))
        clusters: List[List[int]] = []
        for i, r in enumerate(rows):
            end = as_date(r["purchase_date"]) + datetime.timedelta(days=SPREE_DAYS - 1)
            window = [j for j in range(i, len(rows)) if as_date(rows[j]["purchase_date"]) <= end]
            if len(window) < SPREE_ORDERS or _spent(rows[j] for j in window) < SPREE_MIN:
                continue
            if clusters and window[0] <= clusters[-1][-1]:
                clusters[-1] = sorted(set(clusters[-1]) | set(window))
            else:
                clusters.append(window)
        if not clusters:
            continue
        spree = [rows[j] for j in clusters[-1]]
        total = _spent(spree)
        start, end = as_date(spree[0]["purchase_date"]), as_date(spree[-1]["purchase_date"])
        days = (end - start).days + 1
        name = merchant_of(spree[-1])
        year_total = _spent(rows)
        out.append(make_item(
            f"spree:{key}:{start.isoformat()}", "spree", WATCH, f"{len(spree)} orders at {name} in {days} days",
            f"{sh(total)} between {day_text(start)} and {day_text(end)}.",
            at_stake=total,
            evidence=make_evidence([f"{len(spree)} orders, {sh(total)}", f"{sh(year_total)} there in 12 months"],
                               [{"date": r["purchase_date"], "amount": -r["amount"]} for r in spree],
                               {"view": "transactions", "q": name}),
            math_lines=[" + ".join(sh(-r["amount"]) for r in spree) + f" = {sh(total)}"],
            steps=[make_step("Worth it? Mark it, or set a monthly limit for this shop")],
            scope={"merchants": [key]}, baseline={"pace": round_half(year_total / 12)}, label=name))
    return out


def _leaks(ctx: Context) -> List[Dict[str, Any]]:
    fixed = ctx.fixed
    months = list(reversed(dashboard.whole_months(ctx.book, ctx.today, 6)))  # oldest first
    by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in ctx.book.rows:
        if _habit(ctx, r) and _group(r) and r["purchase_date"] >= ctx.since(365):
            by_key[_group(r)].append(r)
    out = []
    for key, rows in by_key.items():
        if max(r["purchase_date"] for r in rows) < ctx.since(LEAK_RECENT_DAYS):
            continue
        charges = [-r["amount"] for r in rows if r["amount"] < 0]
        total = _spent(rows)
        recurring = any(r.get("merchant_key") in fixed for r in rows)
        small = (not recurring and len(charges) >= LEAK_COUNT and statistics.median(charges) <= LEAK_MEDIAN
                 and total >= LEAK_MIN)
        rising, last3, prev3 = False, 0, 0
        if len(months) == 6:
            per: Dict[str, int] = defaultdict(int)
            for r in rows:
                per[r["purchase_date"][:7]] -= r["amount"]
            prev3 = sum(per.get(m.strftime("%Y-%m"), 0) for m in months[:3])
            last3 = sum(per.get(m.strftime("%Y-%m"), 0) for m in months[3:])
            rising = prev3 > 0 and last3 >= 1.5 * prev3 and last3 - prev3 >= LEAK_RISE_MIN
        if not (small or rising):
            continue
        name = merchant_of(rows[-1])
        lines = [f"{len(charges)} charges, {sh(total)} in 12 months (median {sh(round_half(statistics.median(charges)))})"]
        if rising:
            lines.append(f"The last 3 months {sh(last3)}, the 3 before {sh(prev3)}")
        out.append(make_item(
            f"leak:{key}", "leak", WATCH,
            f"{name} is growing" if rising else f"Small charges at {name} add up",
            f"{sh(total)} in 12 months" + (f"; {sh(last3)} in the last 3 months against {sh(prev3)} before." if rising
                                           else f" in {len(charges)} charges."),
            at_stake=total,
            evidence=make_evidence(lines, _monthly_series(rows, _last_months(ctx.today, 12)), {"view": "transactions", "q": name}),
            math_lines=[f"12 months at {name}: {sh(total)}"],
            steps=[make_step("Worth it? Mark it, or set a monthly limit for it")],
            scope={"merchants": [key]}, baseline={"pace": round_half(total / 12)}, label=name))
    return out


def _overlaps(ctx: Context) -> List[Dict[str, Any]]:
    since, year_ago = ctx.since(OVERLAP_DAYS), ctx.since(365)
    recurring = {k: c for c in ctx.book.charges for k in c.get("merchant_keys") or [c["merchant_key"]]}
    out = []
    for niche, tokens in NICHES.items():
        # One service per merchant (display name), charged in the last OVERLAP_DAYS; its cost a year is its
        # recurring charge when it has one, else what it cost in the last 12 months (never a projection of
        # two recent charges).
        services: Dict[str, Dict[str, Any]] = {}
        for r in ctx.book.rows:
            if (r["purchase_date"] < year_ago or r["amount"] >= 0 or not dashboard.is_spending(r)
                    or r.get("category") not in OVERLAP_CATEGORIES):
                continue
            text = norm((r.get("merchant_key") or "") + " " + merchant_of(r))
            if any(has_word(text, token) for token in tokens):
                s = services.setdefault(_group(r), {"keys": set(), "rows": [], "name": merchant_of(r)})
                s["keys"].add(r.get("merchant_key"))
                s["rows"].append(r)
        services = {g: s for g, s in services.items() if max(r["purchase_date"] for r in s["rows"]) >= since}
        if len(services) < 2:
            continue
        yearly: Dict[str, int] = {}
        how: Dict[str, str] = {}
        for s in services.values():
            charges = {id(c): c for c in (recurring[k] for k in s["keys"] if k in recurring)}.values()
            yearly[s["name"]] = sum(c["monthly_cost"] * 12 for c in charges) if charges else _spent(s["rows"])
            how[s["name"]] = "a year" if charges else "in the last 12 months"
        total = sum(yearly.values())
        names = sorted(yearly)
        groups = sorted(services)
        out.append(make_item(
            f"overlap:{niche}", "overlap", WATCH, f"{len(names)} {NICHE_LABELS[niche]} at once",
            f"{' and '.join(names)}: {sh(total)} a year together. Do you need both?",
            at_stake=total,
            evidence=make_evidence([f"{n}: {sh(yearly[n])} {how[n]}" for n in names],
                               [{"date": r["purchase_date"], "amount": -r["amount"]}
                                for s in services.values() for r in s["rows"]], {"view": "transactions"}),
            math_lines=[" + ".join(sh(yearly[n]) for n in names) + f" = {sh(total)} a year"],
            steps=[make_step("Keep the one you use most; cancel the other in its app or site")],
            scope={"merchants": groups}, baseline={"pace": round_half(total / 12)}, label=NICHE_LABELS[niche].capitalize()))
    return out


def _penalties(ctx: Context) -> List[Dict[str, Any]]:
    rows = [r for r in ctx.book.rows if r["purchase_date"] >= ctx.since(365) and r["amount"] < 0
            and dashboard.is_spending(r) and _is_penalty(ctx, r)]
    total = _spent(rows)
    if total < 100:
        return []
    return [make_item(
        "penalties", "penalties", WATCH, f"{_whole(total)} in fines and late fees",
        f"{len(rows)} charges in the last 12 months: fines, late-payment interest or ATM fees.",
        at_stake=total,
        evidence=make_evidence([f"{day_text(r['purchase_date'])}: {merchant_of(r)} {sh(-r['amount'])}" for r in rows[-6:]],
                           [{"date": r["purchase_date"], "amount": -r["amount"]} for r in rows], {"view": "transactions"}),
        math_lines=[" + ".join(sh(-r["amount"]) for r in rows) + f" = {sh(total)}"],
        steps=[make_step("Set standing orders or reminders for the bills that came late"),
               make_step("Withdraw cash at your own bank's machines")],
        scope={"keywords": list(PENALTY_WORDS)}, baseline={"pace": round_half(total / 12)}, label="Fines and late fees")]


def _late_night(ctx: Context) -> List[Dict[str, Any]]:
    fixed = ctx.fixed
    rows = [r for r in ctx.book.rows if r["purchase_date"] >= ctx.since(LATE_DAYS) and r["amount"] < 0
            and _habit(ctx, r) and r.get("merchant_key") not in fixed and _night(r)]
    total = _spent(rows)
    if len(rows) < LATE_COUNT and total < LATE_MIN:
        return []
    yearly = round_half(total * 365 / LATE_DAYS)
    return [make_item(
        "late_night", "late_night", WATCH, f"Late-night shopping: {_whole(total)} in {LATE_DAYS} days",
        f"{len(rows)} purchases between 23:00 and 05:00: about {sh(yearly)} a year at this pace.",
        at_stake=yearly,
        evidence=make_evidence([f"{len(rows)} purchases, {sh(total)}"],
                           [{"date": r["purchase_date"], "amount": -r["amount"]} for r in rows], {"view": "transactions"}),
        math_lines=[f"{sh(total)} × 365 ÷ {LATE_DAYS} = {sh(yearly)} a year"],
        steps=[make_step("Leave the cart until the morning before buying")],
        scope={"categories": list(DISCRETIONARY), "hours": list(NIGHT)}, baseline={"pace": round_half(yearly / 12)},
        label="Late-night shopping")]


DETECTORS: Tuple[Callable[[Context], List[Dict[str, Any]]], ...] = (
    _bills, _cash, _cushion, _fx, _fees, _renewals, _insurance_review, _subscriptions,
    _mortgage, _creep, _sprees, _leaks, _overlaps, _penalties, _late_night)


def detect(ctx: Context) -> List[Dict[str, Any]]:
    """Every item the detectors (savings' and the user's providers') find in ``ctx`` (without the user's
    choices); a failing detector is logged."""
    out: List[Dict[str, Any]] = []
    for fn in (*DETECTORS, *(d for r in ctx.rules for d in r.detectors)):
        try:
            out += fn(ctx)
        except Exception as e:
            logger.error(f"Savings detector {getattr(fn, '__name__', fn)} failed: {e}", exc_info=True)
    return out


# --- Loading and storing ----------------------------------------------------------------------

def _rows(user: User) -> Dict[str, Dict[str, Any]]:
    with store.db() as conn:
        return {r["key"]: dict(r) for r in conn.execute("SELECT * FROM money_savings WHERE user_id = ?", (user.id,))}


def _answers(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        out = json.loads((row or {}).get("answers") or "{}")
    except ValueError:
        out = {}
    return out if isinstance(out, dict) else {}


def _json(value: Optional[str]) -> Dict[str, Any]:
    try:
        out = json.loads(value or "{}")
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def context(user: User, today: Optional[datetime.date] = None, book: Optional[dashboard.Book] = None,
            rows: Optional[Dict[str, Dict[str, Any]]] = None) -> Context:
    """The user's :class:`Context`: their book, accounts, investments, the reference rates the
    detectors need (their provider's), their answers and their price checks."""
    today = today or periods.today()
    book = book or dashboard.load(user, today)
    accounts = [{**a, "label": store.account_label(a["company"], a["kind"], a["number"], a.get("label"))}
                for a in store.list_accounts(user)]
    folio = portfolio.portfolio(user, days=CASH_QUIET_DAYS, trades=500)
    year_ago = (today - datetime.timedelta(days=365)).isoformat()
    # Every supported currency or none: which ones are fetched must say nothing about where the user spent.
    spent_abroad = any(_foreign(r) and r.get("account_kind") == "card" and r["purchase_date"] >= year_ago for r in book.rows)
    rates = providers.rates(user)
    fx = ({c: rates.fx_rates(c, today - datetime.timedelta(days=372), today) for c in sorted(rates.currencies)}
          if spent_abroad and rates else {})
    checks = providers.price_checks(user)
    rows = rows if rows is not None else _rows(user)
    now = time.time()
    unavailable = {topic for (uid, topic), at in _unavailable.items() if uid == user.id and now - at < UNAVAILABLE_SECONDS}
    return Context(today=today, book=book, accounts=accounts, investments=folio["accounts"], trades=folio["trades"],
                   rate=rates.policy_rate(today) if rates else None, fx=fx,
                   answers={k: _answers(r) for k, r in rows.items()},
                   market=(lambda topic, params: checks.latest(user, topic, params)) if checks else None,
                   unavailable=unavailable, bank=rates.bank if rates else "central bank",
                   rules=providers.savings_rules(user))


def _snapshot(item: Dict[str, Any]) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def _store(user: User, detected: Sequence[Dict[str, Any]], rows: Dict[str, Dict[str, Any]],
           today: datetime.date) -> Dict[str, Dict[str, Any]]:
    """Record what was found (a new row per new item, the snapshot when it changed), end snoozes that are
    over, and return the user's rows."""
    now = store.now_iso()
    with store.db() as conn:
        for item in detected:
            snap = _snapshot(item)
            row = rows.get(item["key"])
            if row is None:
                conn.execute("INSERT INTO money_savings (user_id, key, type, status, first_seen_at, answers, snapshot, "
                             "created_at, updated_at) VALUES (?, ?, ?, 'open', ?, '{}', ?, ?, ?) "
                             "ON CONFLICT(user_id, key) DO NOTHING",
                             (user.id, item["key"], item["type"], now, snap, now, now))
            elif row.get("snapshot") != snap:
                conn.execute("UPDATE money_savings SET snapshot = ?, type = ?, updated_at = ? WHERE user_id = ? AND key = ?",
                             (snap, item["type"], now, user.id, item["key"]))
        conn.execute("UPDATE money_savings SET status = 'open', status_at = ?, snooze_until = NULL, updated_at = ? "
                     "WHERE user_id = ? AND status = 'snoozed' AND snooze_until IS NOT NULL AND snooze_until <= ?",
                     (now, now, user.id, today.isoformat()))
    return _rows(user)


def _visible_without_detector(row: Dict[str, Any]) -> bool:
    """The user decided about it: a status, a target or a subscription choice."""
    return (row["status"] != "open" or row.get("target") is not None
            or any(k.startswith("sub:") for k in _answers(row)))


# --- Outcomes ---------------------------------------------------------------------------------

def _since(row: Dict[str, Any], baseline: Dict[str, Any]) -> Optional[datetime.date]:
    stamp = row.get("done_at") if row["status"] == "done" else (baseline.get("at") or row.get("status_at"))
    return store.local_date(stamp)


def make_outcome(state: str, saved: float = 0, yearly: float = 0, detail: str = "") -> Dict[str, Any]:
    return {"state": state, "saved": max(0, round_half(saved)) if state == "confirmed" else 0,
            "saved_yearly": max(0, round_half(yearly)) if state == "confirmed" else 0, "detail": detail}


def _bill_outcome(ctx: Context, b: Dict[str, Any], since: datetime.date) -> Dict[str, Any]:
    keys, amount, cadence = b.get("merchant_keys") or [], b.get("amount") or 0, b.get("cadence") or "monthly"
    per = insights.per_month(cadence)
    # The bill's own charges since (≥ 40% of it: a charger bought from the same company is not the bill).
    after = [r for r in ctx.book.rows if r.get("merchant_key") in keys and r["amount"] < 0
             and r["purchase_date"] > since.isoformat() and -r["amount"] >= amount * 0.4]
    if after:
        latest = -after[-1]["amount"]
        if latest <= amount * 0.97:
            # A drop of more than 40% is confirmed by a second lower charge (a one-time credit looks the same).
            if latest < amount * 0.6 and not (len(after) >= 2 and -after[-2]["amount"] <= amount * 0.97):
                return make_outcome("waiting", detail=f"{sh(latest)} on {day_text(after[-1]['purchase_date'])}: waiting for "
                                                  f"the next charge to confirm")
            return make_outcome("confirmed", sum(max(0, amount + r["amount"]) for r in after), (amount - latest) * 12 * per,
                            f"Now {sh(latest)} {insights.cadence_text(cadence)}, was {sh(amount)}")
        return make_outcome("no_change", detail=f"Still {sh(latest)} (charged {day_text(after[-1]['purchase_date'])})")
    new = [c for c in ctx.book.charges if c.get("category") == b.get("category") and c["first_date"] > since.isoformat()
           and not set(c.get("merchant_keys") or [c["merchant_key"]]) & set(keys)]
    if new:
        c = min(new, key=lambda c: c["monthly_cost"])
        diff = amount * per - c["monthly_cost"]
        if diff > 0:
            days = (ctx.today - as_date(c["first_date"])).days
            return make_outcome("confirmed", diff * 12 * days / 365, diff * 12,
                            f"Switched to {c['merchant']} at {sh(c['amount'])} {insights.cadence_text(c['cadence'])}")
        return make_outcome("no_change", detail=f"Switched to {c['merchant']} at {sh(c['amount'])}")
    return make_outcome("waiting", detail="Waiting for the next charge")


def _cash_outcome(ctx: Context, b: Dict[str, Any], since: datetime.date) -> Dict[str, Any]:
    account = next((a for a in ctx.investments if a["id"] == b.get("account_id")), None)
    days = (ctx.today - since).days
    if account is not None and b.get("cash") and (account.get("cash") or 0) < 0.25 * b["cash"]:
        moved = b["cash"] - (account.get("cash") or 0)
        yearly = moved * (b.get("yield") or 0) / 100
        return make_outcome("confirmed", yearly * days / 365, yearly, f"{sh(moved)} of the cash is invested now")
    if days >= 30:
        return make_outcome("no_change", detail=f"Still {sh(account.get('cash') if account else None)} in cash")
    return make_outcome("waiting", detail="Waiting for the investment account's next sync")


def _cushion_outcome(ctx: Context, b: Dict[str, Any], since: datetime.date) -> Dict[str, Any]:
    days = (ctx.today - since).days
    if b.get("idle_low") is not None:
        # Only money set aside from the bank since (a fund, a deposit, the trading account) counts: a lower
        # balance alone may be a trip paid from it.
        moved = -sum(r["amount"] for r in ctx.book.rows if r["purchase_date"] > since.isoformat() and r["amount"] < 0
                     and r.get("account_kind") == "bank" and dashboard.flow(r) in ("investment", "own_transfer"))
        if moved > 0 and moved >= 0.5 * max(b.get("idle_low") or 0, CUSHION_MIN_IDLE):
            yearly = min(moved, b.get("idle_high") or moved) * (b.get("yield") or 0) / 100
            return make_outcome("confirmed", yearly * days / 365, yearly, f"{sh(moved)} set aside from the account since")
    if days >= 30:
        return make_outcome("no_change", detail="The balance stayed as high as before")
    return make_outcome("waiting", detail="Waiting for the bank's next sync")


def _fx_outcome(ctx: Context, b: Dict[str, Any], since: datetime.date) -> Dict[str, Any]:
    rows = [r for r in _fx_rows(ctx, (since + datetime.timedelta(days=1)).isoformat())
            if r.get("account_id") == b.get("account_id")]
    lines = _fx_lines(ctx, rows)
    base, markup = sum(x[1] for x in lines), sum(x[2] for x in lines)
    if base < FX_OUTCOME_MIN:
        if not lines and (ctx.today - since).days > 120:
            return make_outcome("no_change", detail="No purchase in foreign currency on that card since: if they moved to "
                                                "another card, the saving can't be measured here")
        return make_outcome("waiting", detail="Waiting for more purchases in foreign currency to compare")
    pct = markup / base * 100 if base else 0
    if b.get("pct") and pct <= b["pct"] * 0.5:
        return make_outcome("confirmed", base * b["pct"] / 100 - markup, (b.get("markup") or 0) * (1 - pct / b["pct"]),
                        f"{percent(round(pct, 2))} over the rate since, was {percent(round(b['pct'], 2))}")
    return make_outcome("no_change", detail=f"Still {percent(round(pct, 2))} over the {ctx.bank} rate")


def _fees_outcome(ctx: Context, b: Dict[str, Any], since: datetime.date) -> Dict[str, Any]:
    days = (ctx.today - since).days
    if days < 35:
        return make_outcome("waiting", detail="Waiting for the next month's fees")
    spent = _spent(_fee_rows(ctx, (since + datetime.timedelta(days=1)).isoformat()))
    monthly_now = spent / days * 30.44
    if monthly_now <= (b.get("monthly") or 0) * 0.5:
        yearly = ((b.get("monthly") or 0) - monthly_now) * 12
        return make_outcome("confirmed", yearly * days / 365, yearly, f"{sh(spent)} in fees since, about {sh(round_half(monthly_now))} a month")
    return make_outcome("no_change", detail=f"{sh(spent)} in fees since")


def _renewal_outcome(ctx: Context, b: Dict[str, Any], since: datetime.date) -> Dict[str, Any]:
    nd = as_date(b["next_date"]) if b.get("next_date") else ctx.today
    lo = max((nd - datetime.timedelta(days=insights.YEARLY_CLUSTER_DAYS)).isoformat(),
             (as_date(b["last_date"]) + datetime.timedelta(days=insights.YEARLY_CLUSTER_DAYS)).isoformat()
             if b.get("last_date") else "")
    rows = [r for r in ctx.book.rows if r.get("merchant_key") == b.get("merchant_key") and r["amount"] < 0
            and r["purchase_date"] >= lo]
    if not rows:
        if ctx.today > nd + datetime.timedelta(days=45):
            return make_outcome("no_change", detail=f"No renewal charge since {day_text(nd)}: if you moved to another insurer, "
                                                f"the saving can't be measured here")
        return make_outcome("waiting", detail=f"Renews around {day_text(nd)}")
    plans = {insights.plan_key(r): r for r in rows}
    amount = sum(insights.plan_total(r) for r in plans.values())
    last = b.get("amount") or 0
    if amount < last:
        return make_outcome("confirmed", last - amount, last - amount, f"Renewed at {sh(amount)}, {sh(last)} last year")
    return make_outcome("no_change", detail=f"Renewed at {sh(amount)}, {sh(last)} last year")


def _missed_charges(ctx: Context, key: str, line: Dict[str, Any]) -> Optional[int]:
    """How many expected charges of a subscription line marked cancel never came (0 while none is due yet or
    without a known cadence), or None when it was charged after the choice."""
    at = store.local_date(line.get("at")) or ctx.today
    if any((r.get("merchant_key") == key or _group(r) == key) and r["amount"] < 0 and r["purchase_date"] > at.isoformat()
           for r in ctx.book.rows):
        return None
    if line.get("cadence") not in ("weekly", "monthly", "bimonthly", "yearly") or not line.get("next_date"):
        return 0
    d, missed = as_date(line["next_date"]), 0
    while d <= ctx.today - datetime.timedelta(days=5):
        if d > at:
            missed += 1
        d = _next_date(line["cadence"], d)
    return missed


def _subscriptions_outcome(ctx: Context, answers: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cancels = [(k[4:], v) for k, v in answers.items() if k.startswith("sub:") and isinstance(v, dict)
               and v.get("choice") == "cancel"]
    if not cancels:
        return None
    saved, yearly, charged, waiting, stopped = 0.0, 0.0, [], False, []
    for key, line in cancels:
        missed = _missed_charges(ctx, key, line)
        if missed is None:
            charged.append(line.get("merchant") or key)
            continue
        if missed:
            saved += missed * (line.get("amount") or 0)
            yearly += line.get("yearly") or 0
            stopped.append(line.get("merchant") or key)
        else:
            waiting = True
    if saved > 0:
        detail = "No charge since you cancelled: " + ", ".join(stopped)
        if charged:
            detail += "; still charging: " + ", ".join(charged)
        return make_outcome("confirmed", saved, yearly, detail)
    if charged and not waiting:
        return make_outcome("no_change", detail="Still charging: " + ", ".join(charged))
    return make_outcome("waiting", detail="Waiting for the next expected charge")


_OUTCOMES: Dict[str, Callable[[Context, Dict[str, Any], datetime.date], Dict[str, Any]]] = {
    "bill": _bill_outcome, "cash": _cash_outcome, "cushion": _cushion_outcome,
    "fx": _fx_outcome, "fees": _fees_outcome, "renewal": _renewal_outcome}


def outcome(ctx: Context, item: Dict[str, Any], row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What became of a way to save the user acted on (doing / done), measured from later rows:
    ``{state: confirmed | waiting | no_change, saved (so far), saved_yearly, detail}``; None when
    there is nothing to measure."""
    if item["type"] == "subscriptions":
        return _subscriptions_outcome(ctx, _answers(row))
    fn = _OUTCOMES.get(item["type"]) or next((r.outcomes[item["type"]] for r in ctx.rules
                                               if item["type"] in r.outcomes), None)
    if fn is None or row["status"] not in ("doing", "done"):
        return None
    baseline = _json(row.get("baseline"))
    since = _since(row, baseline)
    if since is None or not baseline:
        return None
    return fn(ctx, baseline, since)


# --- Targets ----------------------------------------------------------------------------------

def _scope_spent(ctx: Context, scope: Dict[str, Any], start: datetime.date, end_exclusive: datetime.date) -> int:
    lo, hi = start.isoformat(), end_exclusive.isoformat()
    return _spent(r for r in ctx.book.rows if lo <= r["purchase_date"] < hi and in_scope(ctx, r, scope))


def target(ctx: Context, item: Dict[str, Any], row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A watched item's target: ``{monthly, this_month, on_track, days_left, months: [{month, spent,
    target, under}], saved}`` — ``months`` from the month it was set to the last one; ``saved``: over
    the months under target, the old monthly pace minus what was spent."""
    monthly = row.get("target")
    if monthly is None or not item.get("scope"):
        return None
    scope = item["scope"]
    first = periods.month_start(ctx.today)
    days = calendar.monthrange(first.year, first.month)[1]
    this = _scope_spent(ctx, scope, first, ctx.today + datetime.timedelta(days=1))
    allowed = monthly * ctx.today.day / days
    on_track: Optional[bool] = this <= allowed
    if ctx.today.day < 3 and this <= monthly:
        on_track = None
    set_on = store.local_date(row.get("status_at")) or ctx.today
    baseline = _json(row.get("baseline"))
    pace = baseline.get("pace") or 0
    history = sorted((h for h in baseline.get("targets") or [] if isinstance(h, dict)), key=lambda h: h.get("month") or "")
    months, saved = [], 0
    m = periods.month_start(set_on)
    while m < first:
        key = m.strftime("%Y-%m")
        # The target in effect that month (a later edit never re-judges it).
        then = next((h["monthly"] for h in reversed(history) if (h.get("month") or "") <= key), None)
        then = then if then is not None else (history[0]["monthly"] if history else monthly)
        spent = _scope_spent(ctx, scope, m, periods.month_end_exclusive(m))
        under = spent <= then
        months.append({"month": key, "spent": spent, "target": then, "under": under})
        if under:
            saved += max(0, pace - spent)
        m = periods.add_months(m, 1)
    return {"monthly": monthly, "this_month": this, "on_track": on_track, "days_left": days - ctx.today.day,
            "months": months, "saved": saved}


# --- The payload ------------------------------------------------------------------------------

def _payload(ctx: Context, item: Dict[str, Any], row: Dict[str, Any], active: bool) -> Dict[str, Any]:
    """The API's item: the detected (or snapshot) payload with the user's choices."""
    answers = _answers(row)
    questions = [{**q, "answer": answers.get(q["id"])} for q in item.get("questions") or []]
    first_seen = store.local_date(row.get("first_seen_at")) or ctx.today
    out = {"key": item["key"], "type": item["type"], "group": item["group"], "status": row["status"],
           "status_at": row.get("status_at"), "snooze_until": row.get("snooze_until"),
           "first_seen": first_seen.isoformat(), "new": (ctx.today - first_seen).days <= NEW_DAYS, "active": active}
    for k in ("title", "summary", "saving", "at_stake", "effort", "timing", "evidence", "math", "steps", "script"):
        out[k] = item.get(k)
    out["questions"] = questions
    out["market"] = item.get("market")
    subs = item.get("subitems")
    out["subitems"] = ([{**x, "choice": (answers.get(f"sub:{x['key']}") or {}).get("choice")} for x in subs]
                       if subs is not None else None)
    out["scope"] = item.get("scope")
    out["target"] = target(ctx, item, row) if item["group"] == WATCH else None
    out["suggested_target"] = item.get("suggested_target")
    out["outcome"] = outcome(ctx, item, row) if item["group"] == SAVE else None
    return out


def _rank(item: Dict[str, Any], today: str) -> Tuple[Any, ...]:
    timing = item.get("timing") or {}
    due = 0 if timing.get("from") and timing["from"] <= today else 1
    s = item.get("saving")
    if s:
        minutes = (item.get("effort") or {}).get("minutes") or 30
        return (due, 0, -((s["low"] + s["high"]) / 2) / minutes, item["key"])
    return (due, 1, -(item.get("at_stake") or 0), item["key"])


def _open(item: Dict[str, Any]) -> bool:
    return item["active"] and item["status"] in ("open", "doing")


def _assemble(user: User, ctx: Context) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]],
                                                Dict[str, Dict[str, Any]]]:
    """``(items, detected by key incl. snapshots, rows)`` for the user."""
    rows = _rows(user)
    ctx.answers = {k: _answers(r) for k, r in rows.items()}
    detected = detect(ctx)
    rows = _store(user, detected, rows, ctx.today)
    found = {i["key"]: i for i in detected}
    items, sources = [], dict(found)
    for key, row in rows.items():
        item = found.get(key)
        active = item is not None
        if item is None:
            if not _visible_without_detector(row) or not row.get("snapshot"):
                continue
            item = _json(row["snapshot"])
            if not item.get("key"):
                continue
            sources[key] = item
        items.append(_payload(ctx, item, row, active))
    today = ctx.today.isoformat()
    items.sort(key=lambda i: _rank(i, today))
    return items, sources, rows


def _totals(items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    open_save = [i for i in items if i["group"] == SAVE and _open(i) and i["saving"]]
    outcomes = [i["outcome"] for i in items if i.get("outcome") and i["outcome"]["state"] == "confirmed"]
    targets = [i["target"]["saved"] for i in items if i.get("target")]
    return {"low": sum(i["saving"]["low"] for i in open_save), "high": sum(i["saving"]["high"] for i in open_save),
            "count": len(open_save), "saved": sum(o["saved"] for o in outcomes) + sum(targets),
            "saved_yearly": sum(o["saved_yearly"] for o in outcomes),
            "watch_count": sum(1 for i in items if i["group"] == WATCH and i["active"] and i["status"] in ("open", "watching"))}


def overview(user: User, today: Optional[datetime.date] = None, book: Optional[dashboard.Book] = None) -> Dict[str, Any]:
    """``GET /api/money/savings``: ``{as_of, rate {policy, date, year_ago, year_ago_date, bank} | None, totals {low,
    high, count, saved, saved_yearly, watch_count}, items}`` (ranked: due first, then saving per minute
    of effort, then the money at stake)."""
    ctx = context(user, today, book, rows={})
    items, _, _ = _assemble(user, ctx)
    rate = ctx.rate
    return {"as_of": ctx.today.isoformat(),
            "rate": {"policy": rate["rate"], "date": rate["date"], "year_ago": rate["year_ago"],
                     "year_ago_date": rate["year_ago_date"], "bank": ctx.Bank} if rate else None,
            "totals": _totals(items), "items": items}


def get_item(user: User, key: str, today: Optional[datetime.date] = None) -> Optional[Dict[str, Any]]:
    """One of the user's items (as listed by :func:`overview`), None when they have no such item."""
    return next((i for i in overview(user, today)["items"] if i["key"] == key), None)


# --- Chats opened from an item ------------------------------------------------------------------

STATUS_WORDS = {"open": "open", "doing": "the user is on it", "done": "the user did it", "dismissed": "not for the user",
                "snoozed": "snoozed", "accepted": "the user says it is worth it", "watching": "watching with a target"}


def chat_context(user: User, key: str) -> Dict[str, Any]:
    """The card of a chat opened from one of the user's savings items (the ``saving`` chat kind).

    Raises:
        NotFound: not one of their items.
    """
    item = get_item(user, key)
    if item is None:
        raise NotFound(key)
    s = item["saving"]
    worth = (f"saves {_range(s['low'], s['high'])} a year" if s else
             f"{sh(item['at_stake'])} a year at stake" if item.get("at_stake") else "")
    prompts = (["How did you work this out?", "What should I say when I call?", "Mark it done"] if item["group"] == SAVE
               else ["Is this really more than usual?", "Set me a monthly target", "It's worth it to me"])
    return {"kind": "saving", "ref_id": key, "call_id": None, "emoji": "💡" if item["group"] == SAVE else "👀",
            "title": item["title"][:80],
            "subtitle": " · ".join(x for x in ("Ways to save" if item["group"] == SAVE else "Spending to watch", worth) if x),
            "quick_prompts": prompts}


def context_prompt(user: User, key: str) -> Optional[str]:
    """The system context of a chat opened from one of the user's savings items (None when not theirs). The
    item's words carry merchant names written by others: they are framed (``core.text.untrusted``)."""
    item = get_item(user, key)
    if item is None:
        return None
    s, t, o = item["saving"], item.get("target"), item.get("outcome")
    facts = [f"group {'ways to save' if item['group'] == SAVE else 'spending to watch'}", f"type {item['type']}",
             f"status {STATUS_WORDS.get(item['status'], item['status'])}"]
    if s:
        facts.append(f"saves {_range(s['low'], s['high'])} a year ({s['basis']})")
    if item.get("at_stake"):
        facts.append(f"{sh(item['at_stake'])} a year at stake")
    if t:
        facts.append(f"target {sh(t['monthly'])} a month, {sh(t['this_month'])} spent this month")
    if o:
        facts.append(f"outcome {o['state']}: {o['detail']}")
    text = [f"Title: {item['title']}", f"Summary: {item['summary']}"]
    text += [f"Math: {m}" for m in item.get("math") or []]
    text += [f"Step {n}: {st['text']}" for n, st in enumerate(item.get("steps") or [], 1)]
    if item.get("script"):
        text.append(f"Call script (Hebrew): {item['script']['text']}")
    lines = [f"## Opened from a saving idea\nSaving {key}: " + "; ".join(facts) + ".",
             untrusted("the saving idea, whose words include merchant names written by others", "\n".join(text))]
    lines.append("Every number above comes from the user's own charges or official data; do not invent other "
                 "numbers. To record what the user decides use update_saving with this key; saving_opportunities lists "
                 "the rest.")
    return "\n".join(lines)


# --- Writes -----------------------------------------------------------------------------------

_UNSET: Any = object()
_COLUMNS = ("status", "status_at", "snooze_until", "answers", "target", "baseline", "done_at")


def _valid_answer(question: Dict[str, Any], value: Any) -> bool:
    return value is None or any(o["value"] == value and type(o["value"]) is type(value) for o in question["options"])


def update(user: User, key: str, *, status: Optional[str] = None, answers: Optional[Dict[str, Any]] = None,
           snooze_days: Optional[int] = None, target: Any = _UNSET, subitem: Optional[Dict[str, Any]] = None,
           today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """Change one of the user's items in one step with one Undo: ``status`` (its group's: open / doing /
    done / dismissed / snoozed, or open / accepted / watching), ``answers`` (question id → an option's
    value, None to clear), ``snooze_days`` (with ``snoozed``; default 30), ``target`` (watch items:
    hundredths a month, None to remove — setting one makes it ``watching``), ``subitem`` (subscriptions:
    ``{key, choice: keep | cancel | None}``).

    Returns:
        ``{"item": the item after the change, "undo_id": str}``.

    Raises:
        NotFound: no such item of the user.
        Invalid: a value the item does not accept (``field`` says which).
    """
    ctx = context(user, today, rows={})
    items, sources, rows = _assemble(user, ctx)
    item = next((i for i in items if i["key"] == key), None)
    if item is None or key not in rows:
        raise NotFound(key)
    row, source = rows[key], sources[key]
    group = item["group"]
    if status is not None and status not in GROUP_STATUSES[group]:
        raise Invalid("status", f"status must be one of {', '.join(GROUP_STATUSES[group])}")
    if snooze_days is not None:
        if isinstance(snooze_days, bool) or not isinstance(snooze_days, int) or not 1 <= snooze_days <= 365:
            raise Invalid("snooze_days", "snooze_days must be a whole number of days, 1 to 365")
        if (status or row["status"]) != "snoozed":
            raise Invalid("snooze_days", "snooze_days goes with status snoozed")
    if target is not _UNSET:
        if group != WATCH:
            raise Invalid("target", "only spending to watch takes a monthly target")
        if target is not None and (isinstance(target, bool) or not isinstance(target, int) or not 0 <= target <= 10**10):
            raise Invalid("target", "target must be hundredths of the currency a month (a whole number) or null")
        if target is not None and status is not None and status != "watching":
            raise Invalid("target", "a target goes with status watching")
    new_answers = _answers(row)
    if answers is not None:
        if not isinstance(answers, dict) or not answers:
            raise Invalid("answers", "answers must be an object of question id → value")
        allowed = {q["id"] for q in source.get("questions") or []}
        if "service" in allowed:
            allowed.add("lines")
        for qid, value in answers.items():
            if qid not in allowed or not _valid_answer(QUESTIONS[qid], value):
                raise Invalid("answers", f"{qid!r} is not a question of this item or {value!r} not one of its options")
            if value is None:
                new_answers.pop(qid, None)
            else:
                new_answers[qid] = value
    now = store.now_iso()
    if subitem is not None:
        subs = {s["key"]: s for s in item.get("subitems") or []}
        if item["type"] != "subscriptions":
            raise Invalid("subitem", "only the subscriptions item has lines")
        if not isinstance(subitem, dict) or subitem.get("key") not in subs or subitem.get("choice") not in ("keep", "cancel", None):
            raise Invalid("subitem", "subitem must be {key: one of its lines, choice: keep | cancel | null}")
        line = subs[subitem["key"]]
        if subitem["choice"] is None:
            new_answers.pop(f"sub:{line['key']}", None)
        else:
            new_answers[f"sub:{line['key']}"] = {"choice": subitem["choice"], "at": now,
                                                 **{k: line.get(k) for k in ("merchant", "amount", "cadence",
                                                                             "yearly", "next_date")}}
    if status is None and answers is None and snooze_days is None and target is _UNSET and subitem is None:
        raise Invalid("body", "nothing to change: give status, answers, snooze_days, target or subitem")
    new = {k: row.get(k) for k in _COLUMNS}
    new["answers"] = json.dumps(new_answers, ensure_ascii=False, sort_keys=True)
    if target is not _UNSET:
        new["target"] = target
        if status is None:
            status = "watching" if target is not None else ("open" if row["status"] == "watching" else None)
    if status is not None:
        if status == "watching" and new["target"] is None:
            new["target"] = item.get("suggested_target")
            if new["target"] is None:
                raise Invalid("target", "watching needs a monthly target")
        if status != "watching":
            new["target"] = None
        if status in ("doing", "done", "watching"):
            # The numbers from when the user first acted: doing → done keeps them, a target edit too.
            keep = row.get("baseline") and (row["status"] == status
                                            or (row["status"] in ("doing", "done") and status in ("doing", "done")))
            if not keep:
                new["baseline"] = json.dumps({**(source.get("_baseline") or {}), "at": now}, ensure_ascii=False,
                                             sort_keys=True)
        else:
            new["baseline"] = None
        if status == "watching" and new["target"] != row.get("target"):
            b = _json(new["baseline"])
            month = (today or periods.today()).strftime("%Y-%m")
            history = [h for h in b.get("targets") or [] if isinstance(h, dict) and h.get("month") != month]
            b["targets"] = history + [{"month": month, "monthly": new["target"]}]
            new["baseline"] = json.dumps(b, ensure_ascii=False, sort_keys=True)
        if status != row["status"]:
            new["status_at"] = now
        new["status"] = status
        new["done_at"] = (row.get("done_at") or now if row["status"] == "done" else now) if status == "done" else None
        if status != "snoozed":
            new["snooze_until"] = None
    if (status or row["status"]) == "snoozed" and (status == "snoozed" or snooze_days is not None):
        new["snooze_until"] = ((today or periods.today()) + datetime.timedelta(days=snooze_days or SNOOZE_DAYS)).isoformat()
    before = {k: row.get(k) for k in _COLUMNS}
    _write(user, key, new)

    def restore() -> None:
        _write(user, key, before)

    with users.acting_as(user):
        undo_id = undo.record(_undo_label(item, before, new), restore, source="money.savings")
    return {"item": get_item(user, key, today), "undo_id": undo_id}


def _write(user: User, key: str, values: Dict[str, Any]) -> None:
    sets = ", ".join(f"{k} = ?" for k in _COLUMNS)
    with store.db() as conn:
        conn.execute(f"UPDATE money_savings SET {sets}, updated_at = ? WHERE user_id = ? AND key = ?",
                     (*[values[k] for k in _COLUMNS], store.now_iso(), user.id, key))


_STATUS_WORDS = {"open": "back to Dana's call", "doing": "on it", "done": "done", "dismissed": "not for you",
                 "snoozed": "snoozed", "accepted": "worth it", "watching": "watched with a target"}


def _undo_label(item: Dict[str, Any], before: Dict[str, Any], after: Dict[str, Any]) -> str:
    name = item.get("title") or item["key"]
    if after["status"] != before["status"]:
        return f"{name}: {_STATUS_WORDS.get(after['status'], after['status'])}"
    if after["target"] != before["target"]:
        return f"{name}: target {_whole(after['target'])} a month" if after["target"] is not None else f"{name}: target removed"
    return f"{name}: answers changed"


def set_status(user: User, key: str, status: str, snooze_days: Optional[int] = None) -> Dict[str, Any]:
    """Set an item's status (:func:`update`)."""
    return update(user, key, status=status, snooze_days=snooze_days)


def set_answers(user: User, key: str, answers: Dict[str, Any]) -> Dict[str, Any]:
    """Answer an item's questions (:func:`update`)."""
    return update(user, key, answers=answers)


def set_target(user: User, key: str, monthly: Optional[int]) -> Dict[str, Any]:
    """Set (hundredths a month) or remove (None) a watch item's target (:func:`update`)."""
    return update(user, key, target=monthly)


def set_subitem(user: User, key: str, sub_key: str, choice: Optional[str]) -> Dict[str, Any]:
    """Mark a subscription keep / cancel / undecided (:func:`update`)."""
    return update(user, key, subitem={"key": sub_key, "choice": choice})


def check_request(user: User, key: str) -> Tuple[str, Dict[str, Any]]:
    """The ``(topic, params)`` of the item's price check (reads the database: run it off the event loop).

    Raises:
        NotFound: no such item of the user.
        NoCheck: the item has no price check (or none it can run now).
        NeedsAnswer: a question must be answered first.
    """
    item = get_item(user, key)
    if item is None:
        raise NotFound(key)
    m = item.get("market")
    if not m or m.get("params") is None:
        raise NoCheck(key)
    if m.get("needs_answer"):
        raise NeedsAnswer(next((q for q in item["questions"] if q["id"] == m["needs_answer"]), QUESTIONS[m["needs_answer"]]))
    return m["topic"], dict(m["params"])


def start_check(user: User, topic: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Start a check from :func:`check_request` (``providers.PriceChecks.start``); call it on the event loop (it
    starts a task). Remembers a topic the model cannot search for an hour.

    Raises:
        providers.PriceCheckUnavailable (also without price checks) / PriceCheckRunning / PriceCheckTooSoon /
        ValueError: from the price checks.
    """
    checks = providers.price_checks(user)
    try:
        if checks is None:
            raise providers.PriceCheckUnavailable("no price checks for this user")
        return checks.start(user, topic, params)
    except providers.PriceCheckUnavailable:
        _unavailable[(user.id, topic)] = time.time()
        raise


# --- For Now, the weekly review and the insights ------------------------------------------------

def label(item: Dict[str, Any]) -> str:
    """A short name for an item (``Eating out``, a merchant), from its snapshot."""
    return item.get("_label") or item.get("title") or item["key"]


def short_name(user: User, key: str) -> str:
    """The short name of one of the user's items, from its stored snapshot (the key when unknown)."""
    with store.db() as conn:
        row = conn.execute("SELECT snapshot FROM money_savings WHERE user_id = ? AND key = ?", (user.id, key)).fetchone()
    return label(_json(row["snapshot"])) if row and row["snapshot"] else key


def target_lines(user: User, items: Sequence[Dict[str, Any]]) -> List[str]:
    """``Eating out 300 of 700 this month`` for each watched target."""
    out = []
    for i in items:
        t = i.get("target")
        if t:
            out.append(f"{short_name(user, i['key'])} {_whole(t['this_month'])} of {_whole(t['monthly'])} this month")
    return out


def week_line(user: User, today: Optional[datetime.date] = None) -> Optional[str]:
    """The weekly review's savings line ("Savings: saved 120 so far; 3 ideas open worth 800–1,400 a
    year; Eating out 300 of 700 this month"), None with nothing to say."""
    data = overview(user, today)
    t = data["totals"]
    parts = []
    if t["saved"] > 0:
        parts.append(f"saved {_whole(t['saved'])} so far")
    if t["count"]:
        parts.append(f"{t['count']} idea{'s' if t['count'] != 1 else ''} open worth {_range(t['low'], t['high'])} a year")
    parts += target_lines(user, data["items"])
    return "Savings: " + "; ".join(parts) if parts else None


def insight_cards(user: User, book: dashboard.Book) -> List[Dict[str, Any]]:
    """The Overview's card for the month in progress: ``N ways to save a–b a year`` (none without an
    open way to save that has a saving)."""
    data = overview(user, book.today, book)
    t = data["totals"]
    if not t["count"]:
        return []
    top = next(i for i in data["items"] if i["group"] == SAVE and _open(i) and i["saving"])
    return [{"kind": "savings", "tone": "good",
             "title": f"{t['count']} way{'s' if t['count'] != 1 else ''} to save {_range(t['low'], t['high'])} a year",
             "detail": f"Start with: {top['title']}.", "amount": t["high"], "link": {"view": "save"}}]
