"""Money alerts: a few pushes worth acting on, each sent once (docs/money.md, "Alerts").

Kinds (:data:`KINDS`; the ``alerts`` setting turns them off: ``off``, or the kinds to keep, comma separated):

- ``low_balance``: the forecast's bank balance goes below :data:`FLOOR_AGOROT` within :data:`LOW_DAYS` days
  (key ``low_balance:<date>``), with what is charged that day.
- ``unusual``: an unusual or duplicate charge of the last :data:`UNUSUAL_DAYS` days (``insights.unusual``;
  ``unusual:<transaction id>``).
- ``salary``: the salary has not arrived :data:`SALARY_LATE_DAYS` days after its usual day
  (``salary:<expected date>``).
- ``renewal``: a yearly charge (an insurance renewal, the car licence) within :data:`RENEWAL_DAYS` days
  (``renewal:<merchant key>:<date>``).
- ``card_bill``: a card bill in the next :data:`BILL_DAYS` days is much bigger than the card's usual bill
  (``card_bill:<account id>:<date>``).
- ``move``: once a month, "this month's move": the best open way to save (``savings.overview``'s first
  open, active item with a saving), with its first step (``move:<YYYY-MM>``; the item key is kept in ``data``
  for the Now card :func:`month_move`).

:func:`candidates` is pure (the tests); :func:`tick` runs from the addon's job: only between
:data:`QUIET_UNTIL` and :data:`QUIET_FROM` local time, at most :data:`PER_TICK` alerts a run, each key recorded in
``money_alerts`` before it is sent (one that fails is not retried: alerts are timely or useless). A push
goes to the phone (``notify.push_notify``); without a phone, the text goes to Telegram.
"""

import datetime
import json
import logging
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

from core import notify
from core.addons import store as connections
from core.users import User
from addons.money import config, dashboard, insights, periods, plan, savings, store

logger = logging.getLogger(__name__)

KINDS = ("low_balance", "unusual", "salary", "renewal", "card_bill", "move")
SETTING = "alerts"
FLOOR_AGOROT = dashboard.LOW_BALANCE_AGOROT
LOW_DAYS = 14
UNUSUAL_DAYS = 2
SALARY_LATE_DAYS = 2
RENEWAL_DAYS = 14
BILL_DAYS = 5
# A card bill is "bigger than usual" at this share of the median of its last bills, and this much more.
BILL_FACTOR = 1.25
BILL_MIN_MORE = 100_000
BILL_HISTORY = 6
QUIET_UNTIL = datetime.time(8, 0)
QUIET_FROM = datetime.time(21, 30)
PER_TICK = 3


@dataclass(frozen=True)
class Alert:
    key: str
    kind: str
    title: str
    text: str
    data: Dict[str, Any] = field(default_factory=dict)


def enabled(user: User) -> Set[str]:
    """The alert kinds the user keeps (the ``alerts`` setting: ``all`` (default), ``off`` or a list)."""
    conn = connections.get(user, config.ADDON)
    raw = str((conn.settings.get(SETTING) if conn else None) or "all").strip().lower()
    if raw in ("all", ""):
        return set(KINDS)
    if raw in ("off", "none", "no"):
        return set()
    return {k.strip() for k in raw.replace(";", ",").split(",")} & set(KINDS)


def _day(d: datetime.date, today: datetime.date) -> str:
    return f"{d.strftime('%a')} {periods.short_date(d, d.year != today.year)}"


def _bill_history(book: dashboard.Book, card_id: str, today: datetime.date) -> List[int]:
    """The card's last :data:`BILL_HISTORY` bills before today (its rows summed per charge date)."""
    per: Dict[str, int] = {}
    for r in book.rows:
        if r.get("account_id") == card_id and r.get("charge_date") and r["charge_date"] < today.isoformat():
            per[r["charge_date"]] = per.get(r["charge_date"], 0) - r["amount"]
    return [v for _, v in sorted(per.items())[-BILL_HISTORY:] if v > 0]


def candidates(book: dashboard.Book, today: datetime.date, *, forecast: Optional[Dict[str, Any]],
               unusual: Sequence[Dict[str, Any]], saving_items: Sequence[Dict[str, Any]]) -> List[Alert]:
    """Every alert that applies today (pure; :func:`tick` drops those already sent)."""
    sh = store.money_text
    out: List[Alert] = []
    if forecast:
        low = next((p for p in forecast["series"][1:LOW_DAYS + 1] if p["balance"] < FLOOR_AGOROT), None)
        if low:
            d = datetime.date.fromisoformat(low["date"])
            that_day = sorted((e for e in forecast["events"] if e["date"] == low["date"] and e["amount"] < 0),
                              key=lambda e: e["amount"])
            why = f" — {that_day[0]['label']} ({sh(-that_day[0]['amount'])}) is due that day" if that_day else ""
            out.append(Alert(f"low_balance:{low['date']}", "low_balance", "Your bank balance may run low",
                             f"Expected {sh(low['balance'])} on {_day(d, today)}{why}."))
        for e in forecast["events"]:
            if e["kind"] != "card_bill" or not e.get("account_id"):
                continue
            d = datetime.date.fromisoformat(e["date"])
            if not today <= d <= today + datetime.timedelta(days=BILL_DAYS):
                continue
            past = _bill_history(book, e["account_id"], today)
            if len(past) < 3:
                continue
            usual = int(statistics.median(past))
            amount = -e["amount"]
            if amount >= usual * BILL_FACTOR and amount - usual >= BILL_MIN_MORE:
                out.append(Alert(f"card_bill:{e['account_id']}:{e['date']}", "card_bill",
                                 f"A bigger card bill on {_day(d, today)}",
                                 f"{e['label']}: about {sh(amount)}, {sh(amount - usual)} more than its usual "
                                 f"{sh(usual)}."))
    for u in unusual:
        t = u["transaction"]
        out.append(Alert(f"unusual:{t['id']}", "unusual", u["title"], u["why"][:1].upper() + u["why"][1:] + "."))
    pay = plan.salary(book)
    if pay is not None:
        expected = dashboard.bank_day(datetime.date.fromisoformat(pay["next_date"]))
        last = datetime.date.fromisoformat(pay["dates"][-1])
        if expected > last and (today - expected).days >= SALARY_LATE_DAYS:
            out.append(Alert(f"salary:{expected.isoformat()}", "salary", "Your salary hasn't arrived",
                             f"{pay['merchant']} usually pays about {sh(pay['amount'])} by {_day(expected, today)}; "
                             f"nothing has reached the bank yet."))
    for y in book.yearly:
        d = datetime.date.fromisoformat(y["next_date"])
        if today <= d <= today + datetime.timedelta(days=RENEWAL_DAYS):
            payments = f" in {y['payments']} payments" if (y.get("payments") or 1) > 1 else ""
            out.append(Alert(f"renewal:{y['merchant_key']}:{y['next_date']}", "renewal",
                             f"{y['merchant']} renews on {_day(d, today)}",
                             f"Last year it was {sh(y['amount'])}{payments}. A good moment to compare prices."))
    pick = month_pick(saving_items)
    if pick is not None:
        s = pick["saving"]
        saves = f"{sh(s['low'])}–{sh(s['high'])}" if s["low"] != s["high"] else sh(s["high"])
        step = f" First step: {pick['steps'][0]['text']}" if pick.get("steps") else ""
        out.append(Alert(f"move:{today.strftime('%Y-%m')}", "move", f"This month's move: {pick['title']}",
                         f"Saves {saves} a year.{step}", {"saving": pick["key"]}))
    return out


def month_pick(saving_items: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The best open way to save: the first open, active item with a saving (``savings.overview`` ranks them)."""
    return next((i for i in saving_items if i["group"] == "save" and i["status"] == "open" and i["active"]
                 and i.get("saving")), None)


def _sent(user: User) -> Set[str]:
    with store.db() as conn:
        return {r["key"] for r in conn.execute("SELECT key FROM money_alerts WHERE user_id = ?", (user.id,))}


def _record(user: User, alert: Alert) -> bool:
    """Record the alert as sent; False when it already was (another run got there first)."""
    with store.db() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO money_alerts (user_id, key, kind, sent_at, data) VALUES (?, ?, ?, ?, ?)",
                           (user.id, alert.key, alert.kind, store.now_iso(), json.dumps(alert.data or {})))
        return cur.rowcount == 1


def _delivered(user: User, key: str) -> None:
    with store.db() as conn:
        conn.execute("UPDATE money_alerts SET delivered = 1 WHERE user_id = ? AND key = ?", (user.id, key))


def month_move(user: User, today: datetime.date) -> Optional[str]:
    """The savings item key of this month's move (sent or not: the pick is recomputed when none was sent)."""
    with store.db() as conn:
        row = conn.execute("SELECT data FROM money_alerts WHERE user_id = ? AND key = ?",
                           (user.id, f"move:{today.strftime('%Y-%m')}")).fetchone()
    if row is not None:
        return (json.loads(row["data"] or "{}") or {}).get("saving")
    return None


async def deliver(user: User, alert: Alert) -> bool:
    """Push the alert to the phone; without a phone, send its text to Telegram."""
    if await notify.push_notify(user, alert.title, alert.text, tag=f"money-{alert.key}", channel="messages"):
        return True
    return await notify.notify(user, f"{alert.title}\n{alert.text}")


def due(user: User, now: datetime.datetime) -> List[Alert]:
    """The alerts to send now: the kinds the user keeps, not sent before, only outside quiet hours."""
    kinds = enabled(user)
    if not kinds or not QUIET_UNTIL <= now.time() < QUIET_FROM:
        return []
    today = now.date()
    book = dashboard.load(user, today)
    fc = dashboard.forecast(user, 60, book) if kinds & {"low_balance", "card_bill"} else None
    unusual = insights.unusual(user, today - datetime.timedelta(days=UNUSUAL_DAYS - 1)) if "unusual" in kinds else []
    items: List[Dict[str, Any]] = []
    if "move" in kinds:
        try:
            items = savings.overview(user, today, book)["items"]
        except Exception as e:
            logger.warning(f"Money alerts: savings for user {user.id} failed: {e}")
    sent = _sent(user)
    return [a for a in candidates(book, today, forecast=fc, unusual=unusual, saving_items=items)
            if a.kind in kinds and a.key not in sent]


async def tick(user: User, now: datetime.datetime) -> List[str]:
    """Send what is due (at most :data:`PER_TICK`); the keys sent."""
    if not connections.is_connected(user, config.ADDON):
        return []
    out = []
    for alert in due(user, now)[:PER_TICK]:
        if not _record(user, alert):
            continue
        try:
            if await deliver(user, alert):
                _delivered(user, alert.key)
        except Exception as e:
            logger.warning(f"Money alert {alert.key} for user {user.id} failed: {e}")
        out.append(alert.key)
    return out
