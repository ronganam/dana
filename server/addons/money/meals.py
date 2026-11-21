"""Meal cards: storing a sync's charges and reading the budget (docs/money.md, "Meal cards").

A provider returns a meal card (a company of kind ``meal``) in the ingest
format's meal shape (docs/money.md, "Ingest format"): ``budget`` (the current
cycle: ``{amount, left, start, end}``) and ``meals`` (every charge since the
sync's start date, each with its ``ext_key``). :func:`store_account` keeps
them in the ingest's transaction: the account upserted (``balance`` = what is
left of the budget, ``details`` = the cycle), each charge upserted by its key
into ``money_meals``. A meal is never a ``money_transactions`` row: the
employer's budget is not the user's spending. What the budget did not cover
goes on the user's own card, which the meal card company may bill once a
month for a whole cycle: :func:`billed_lines` finds that line for each cycle
by its exact amount among the rows whose merchant key holds one of the
company's ``card_line_words``, and :func:`card_line` says which cycle a card
line paid for.

A cycle runs from the budget's day of the month (the 22nd: 22 Oct – 21 Nov)
to the day before it a month later; past cycles take the current cycle's day."""

import calendar
import datetime
import json
import logging
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core import clock
from core.users import User
from addons.money import ingest, providers, store

logger = logging.getLogger(__name__)

# A meal's ``order_type`` in the ingest format (anything else is shown as given).
ORDER_LABELS = {"restaurant": "At the restaurant / pickup", "delivery": "Delivery", "voucher": "Voucher"}
# The card line comes this many days after the cycle ends, at most (seen: 19 days, the 10th of the next month).
BILL_DAYS = 75
PLACES_TOP = 10


def store_account(conn, user: User, source_id: str, company: providers.Company, account: Mapping[str, Any],
                  now: str) -> Optional[str]:
    """Store one meal card of a sync (inside the ingest's transaction); returns its id. ``account``:
    ``{accountNumber, budget {amount, left, start, end}, balance?, meals [...]}`` (agorot; ``balance``:
    what is left when the budget does not say)."""
    number = str(account.get("accountNumber") or "").strip()
    if not number:
        logger.warning(f"Money ingest for source {source_id}: a meal card without a number was skipped")
        return None
    budget = account.get("budget") if isinstance(account.get("budget"), Mapping) else {}
    details = {k: budget.get(k) for k in ("amount", "left", "start", "end")}
    left = details["left"] if details["left"] is not None else account.get("balance")
    payload = json.dumps(details, sort_keys=True)
    row = conn.execute("SELECT id FROM money_accounts WHERE user_id = ? AND company = ? AND number = ?",
                       (user.id, company.id, number)).fetchone()
    if row is None:
        account_id = uuid.uuid4().hex
        conn.execute("INSERT INTO money_accounts (id, user_id, source_id, company, kind, number, balance, balance_at, "
                     "details, created_at, updated_at) VALUES (?, ?, ?, ?, 'meal', ?, ?, ?, ?, ?, ?)",
                     (account_id, user.id, source_id, company.id, number, left, now if left is not None else None,
                      payload, now, now))
    else:
        account_id = row["id"]
        # scoped: the account found by user_id above
        conn.execute("UPDATE money_accounts SET source_id = ?, kind = 'meal', balance = ?, balance_at = ?, details = ?, "
                     "updated_at = ? WHERE id = ?",
                     (source_id, left, now if left is not None else None, payload, now, account_id))
    for d in account.get("meals") or []:
        if not isinstance(d, Mapping) or not d.get("ext_key") or not d.get("date") or d.get("total") is None:
            continue
        card_paid = d.get("card_paid") or 0
        raw = d.get("raw")
        conn.execute(
            "INSERT INTO money_meals (id, user_id, account_id, ext_key, date, occurred_at, place, place_id, order_type, "
            "total, budget_paid, card_paid, discount, active, budget_name, raw, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account_id, ext_key) DO UPDATE SET date = excluded.date, occurred_at = excluded.occurred_at, "
            "place = excluded.place, order_type = excluded.order_type, total = excluded.total, "
            "budget_paid = excluded.budget_paid, card_paid = excluded.card_paid, discount = excluded.discount, "
            "active = excluded.active, budget_name = excluded.budget_name, raw = excluded.raw, "
            "updated_at = excluded.updated_at",
            (uuid.uuid4().hex, user.id, account_id, str(d["ext_key"]), d["date"], d.get("occurred_at"),
             _text(d.get("place")), d.get("place_id"), d.get("order_type"), d["total"],
             d.get("budget_paid", d["total"] - card_paid), card_paid, d.get("discount"), 0 if d.get("active") in (0, False) else 1,
             _text(d.get("budget_name")),
             json.dumps(ingest.scrub_value(dict(raw)), ensure_ascii=False, sort_keys=True) if isinstance(raw, Mapping) else None,
             now, now))
    return account_id


def _text(value: Any) -> Optional[str]:
    return ingest.scrub(str(value).strip()) or None if value not in (None, "") else None


# --- Cycles -------------------------------------------------------------------------------

def _on_day(year: int, month: int, day: int) -> datetime.date:
    return datetime.date(year, month, min(day, calendar.monthrange(year, month)[1]))


def cycle_of(day: datetime.date, start_day: int) -> Tuple[datetime.date, datetime.date]:
    """The budget cycle holding ``day``: ``(start, end)``, both included (start day 22: 22 Oct – 21 Nov)."""
    start = _on_day(day.year, day.month, start_day)
    if day < start:
        y, m = (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)
        start = _on_day(y, m, start_day)
    y, m = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return start, _on_day(y, m, start_day) - datetime.timedelta(days=1)


def _details(account: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        d = json.loads(account.get("details") or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def _start_day(account: Mapping[str, Any]) -> int:
    start = _details(account).get("start")
    try:
        return datetime.date.fromisoformat(start).day if start else 1
    except ValueError:
        return 1


def _meal_accounts(user: User) -> List[Dict[str, Any]]:
    return [a for a in store.list_accounts(user) if a["kind"] == "meal" and not a.get("hidden")]


def _meal_rows(user: User, account_ids: Sequence[str], since: Optional[str] = None) -> List[Dict[str, Any]]:
    if not account_ids:
        return []
    sql = (f"SELECT * FROM money_meals WHERE user_id = ? AND account_id IN ({','.join('?' * len(account_ids))})")
    args: List[Any] = [user.id, *account_ids]
    if since:
        sql += " AND date >= ?"
        args.append(since)
    with store.db() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY date DESC, occurred_at DESC, id", args)]


def _cycles(rows: Sequence[Mapping[str, Any]], start_day: int) -> List[Dict[str, Any]]:
    """Active meals per cycle, newest first: ``{start, end, total, budget_paid, card_paid, count}``."""
    out: Dict[Tuple[datetime.date, datetime.date], Dict[str, Any]] = {}
    for r in rows:
        if not r["active"]:
            continue
        key = cycle_of(datetime.date.fromisoformat(r["date"]), start_day)
        c = out.setdefault(key, {"start": key[0].isoformat(), "end": key[1].isoformat(), "total": 0,
                                 "budget_paid": 0, "card_paid": 0, "count": 0})
        c["total"] += r["total"]
        c["budget_paid"] += r["budget_paid"]
        c["card_paid"] += r["card_paid"]
        c["count"] += 1
    return [out[k] for k in sorted(out, reverse=True)]


def card_line_words(accounts: Sequence[Mapping[str, Any]]) -> List[str]:
    """The words of the card line of these meal cards' companies (``Company.card_line_words``)."""
    words = {w for a in accounts for w in getattr(providers.company(a["company"]), "card_line_words", ())}
    return sorted(words)


def _card_line_rows(user: User, accounts: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The user's card / bank rows that may be a meal card's monthly overflow line (money out)."""
    words = card_line_words(accounts)
    if not words:
        return []
    where = " OR ".join("t.merchant_key LIKE ?" for _ in words)
    with store.db() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT t.id, t.purchase_date, t.amount, t.account_id FROM money_transactions t "
            f"JOIN money_accounts a ON a.id = t.account_id WHERE t.user_id = ? AND a.kind != 'meal' "
            f"AND t.amount < 0 AND t.status != 'unmatched' AND ({where}) ORDER BY t.purchase_date",
            (user.id, *[f"%{w}%" for w in words]))]


def billed_lines(cycles: Sequence[Dict[str, Any]], lines: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Each cycle's card line (cycle start → ``{txn_id, date, amount}``): a line of exactly the cycle's
    overflow, after the cycle ended and within :data:`BILL_DAYS`; each line pays one cycle."""
    used = set()
    out: Dict[str, Dict[str, Any]] = {}
    for c in sorted(cycles, key=lambda c: c["start"]):
        if c["card_paid"] <= 0:
            continue
        end = datetime.date.fromisoformat(c["end"])
        last = (end + datetime.timedelta(days=BILL_DAYS)).isoformat()
        for line in lines:
            if line["id"] in used or not (c["end"] < line["purchase_date"] <= last) or -line["amount"] != c["card_paid"]:
                continue
            used.add(line["id"])
            out[c["start"]] = {"txn_id": line["id"], "date": line["purchase_date"], "amount": -line["amount"]}
            break
    return out


# --- Reads --------------------------------------------------------------------------------

def _current(account: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], today: datetime.date) -> Dict[str, Any]:
    """The account's cycle now: budget, left, days, what was used and the pace."""
    d = _details(account)
    start, end = cycle_of(today, _start_day(account))
    if d.get("start") and d.get("end") and d["start"] <= today.isoformat() <= d["end"]:
        start, end = datetime.date.fromisoformat(d["start"]), datetime.date.fromisoformat(d["end"])
    mine = [r for r in rows if r["active"] and start.isoformat() <= r["date"] <= end.isoformat()]
    days = (end - start).days + 1
    elapsed = (today - start).days + 1
    left = account.get("balance")
    used = sum(r["budget_paid"] for r in mine)
    # When the budget runs out at this cycle's pace so far (None: it lasts the cycle, or nothing used yet).
    run_out = None
    if left == 0 and mine:
        run_out = today.isoformat()
    elif left and used > 0:
        out_day = today + datetime.timedelta(days=int(left / (used / elapsed)))
        run_out = out_day.isoformat() if out_day < end else None
    return {"start": start.isoformat(), "end": end.isoformat(), "days": days, "days_left": max(0, (end - today).days),
            "budget": d.get("amount"), "left": left, "used": used, "card_paid": sum(r["card_paid"] for r in mine),
            "count": len(mine), "per_day_left": int(left / max(1, (end - today).days + 1)) if left else 0,
            "runs_out": run_out}


def meal_accounts(user: User) -> List[Dict[str, Any]]:
    """The user's meal cards as the overview lists them: ``{id, label, number, company, company_name, left,
    budget, start, end, days_left, used, card_paid, runs_out, balance_at}``."""
    today = clock.now().date()
    out = []
    for a in _meal_accounts(user):
        cur = _current(a, _meal_rows(user, [a["id"]], since=(today - datetime.timedelta(days=40)).isoformat()), today)
        out.append({"id": a["id"], "label": store.account_label(a["company"], a["kind"], a["number"], a.get("label")),
                    "number": a["number"], "company": a["company"], "company_name": providers.name_of(a["company"]),
                    "balance_at": a.get("balance_at"),
                    **{k: cur[k] for k in ("left", "budget", "start", "end", "days_left", "used", "card_paid",
                                           "runs_out")}})
    return out


def _item(r: Mapping[str, Any]) -> Dict[str, Any]:
    return {"id": r["id"], "date": r["date"], "occurred_at": r["occurred_at"], "place": r["place"],
            "order_type": r["order_type"], "total": r["total"], "budget_paid": r["budget_paid"],
            "card_paid": r["card_paid"], "active": bool(r["active"])}


def meals(user: User, months: int = 12, recent: int = 40) -> Dict[str, Any]:
    """Everything the Meal card view shows.

    Returns:
        ``{accounts: [meal_accounts items + current {start, end, days, days_left, budget, left, used,
        card_paid, count, per_day_left, runs_out}], cycles: [{start, end, total, budget_paid, card_paid,
        count, billed: {txn_id, date, amount} | None}] (newest first, ``months`` of them), meals: [{id, date,
        occurred_at, place, order_type, total, budget_paid, card_paid, active}] (newest first, ``recent``),
        places: [{place, count, total}] (over the cycles shown, most visits first), kinds: [{order_type,
        label, count, total}], totals {total, budget_paid, card_paid, count}}``.
    """
    today = clock.now().date()
    accounts = _meal_accounts(user)
    since = (today - datetime.timedelta(days=31 * (months + 1))).isoformat()
    rows = _meal_rows(user, [a["id"] for a in accounts], since=since)
    start_day = _start_day(accounts[0]) if accounts else 1
    cycles = _cycles(rows, start_day)[:months]
    billed = billed_lines(cycles, _card_line_rows(user, accounts)) if cycles else {}
    for c in cycles:
        c["billed"] = billed.get(c["start"])
    first = cycles[-1]["start"] if cycles else today.isoformat()
    shown = [r for r in rows if r["active"] and r["date"] >= first]
    places: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "total": 0})
    kinds: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "total": 0})
    for r in shown:
        p = places[r["place"] or "—"]
        p["count"] += 1
        p["total"] += r["total"]
        k = kinds[r["order_type"] or "other"]
        k["count"] += 1
        k["total"] += r["total"]
    summaries = {m["id"]: m for m in meal_accounts(user)}
    listed = [{**summaries.get(a["id"], {}), "current": _current(a, [r for r in rows if r["account_id"] == a["id"]], today)}
              for a in accounts]
    return {
        "accounts": listed,
        "cycles": cycles,
        "meals": [_item(r) for r in rows[:recent]],
        "places": [{"place": k, **v} for k, v in sorted(places.items(), key=lambda kv: (-kv[1]["count"], -kv[1]["total"]))][:PLACES_TOP],
        "kinds": [{"order_type": k, "label": ORDER_LABELS.get(k, k), **v}
                  for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]["total"])],
        "totals": {"total": sum(r["total"] for r in shown), "budget_paid": sum(r["budget_paid"] for r in shown),
                   "card_paid": sum(r["card_paid"] for r in shown), "count": len(shown)},
    }


def card_line(user: User, txn_id: str) -> Optional[Dict[str, Any]]:
    """The meal card cycle a card / bank row paid for (its monthly overflow line): ``{start, end, count,
    card_paid, meals: [items with card_paid > 0]}``; None when the row is not one."""
    accounts = _meal_accounts(user)
    if not accounts:
        return None
    lines = _card_line_rows(user, accounts)
    if not any(line["id"] == txn_id for line in lines):
        return None
    rows = _meal_rows(user, [a["id"] for a in accounts])
    cycles = _cycles(rows, _start_day(accounts[0]))
    for start, bill in billed_lines(cycles, lines).items():
        if bill["txn_id"] != txn_id:
            continue
        c = next(c for c in cycles if c["start"] == start)
        paid = [_item(r) for r in rows if r["active"] and r["card_paid"] > 0 and c["start"] <= r["date"] <= c["end"]]
        return {"start": c["start"], "end": c["end"], "count": c["count"], "card_paid": c["card_paid"], "meals": paid}
    return None
