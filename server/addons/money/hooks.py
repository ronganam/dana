"""The money addon's background work and composer hooks (docs/money.md):

- at start: end what a previous process left mid-sync, then classify what is
  still missing;
- every minute (after 15 s): start every due source of the user;
- Now screen: unusual charges (``money:<id>``), ways to save (``money_save:<key>``),
  and monthly target warnings/results (``money_target:<key>:<YYYY-MM>``);
- weekly review: money spending & savings summary;
- context chats for ``transaction`` and ``saving``;
- self-check: ``money`` (each bank/card source syncs cleanly);
- providers: ``money_week_lines``, ``telegram_reply``.
"""

import asyncio
import datetime
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from core import cards, now as now_screen
from core.addons import BriefingContext, Card, Check, ContextRequest, NowContext, Section, helpers, minutes, store
from core.db import get_db_connection
from core.users import User
from addons.money import (classify, config, insights, otp, providers, queries, savings, schedule,
                          signals, store as money_store, sync)
from addons.money.addon import addon

logger = logging.getLogger(__name__)

MAX_MONEY = 3
MONEY_DAYS = 3
MAX_SAVINGS = 2
SAVINGS_DUE_DAYS = 14
SAVINGS_NEW_DAYS = 7
TARGET_WARN_SHARE = 0.8
TARGET_RESULT_DAYS = 3


@addon.job(at_start=True)
async def recover(user: User) -> None:
    sync.recover(user)
    await classify.catch_up_user(user)


@addon.job(every=minutes(schedule.INTERVAL_SECONDS / 60), delay=schedule.STARTUP_DELAY_SECONDS)
async def start_due_syncs(user: User) -> List[str]:
    return schedule.tick_user(user)


@addon.on("phone.notification.ingested")
async def on_notifications(user: User, data: Dict[str, Any]) -> None:
    ids = [str(i) for i in (data.get("ids") or []) if i]
    if ids:
        await asyncio.to_thread(signals.observe, user, ids)


# --- Now screen -----------------------------------------------------------------------------

def unusual_cards(user: User, now: datetime.datetime, hidden: Dict[str, Any]) -> List[Card]:
    if not store.is_connected(user, config.ADDON):
        return []
    out: List[Card] = []
    for u in insights.unusual(user, now.date() - datetime.timedelta(days=MONEY_DAYS - 1)):
        t = u["transaction"]
        if f"money:{t['id']}" in hidden:
            continue
        facts = [f"{t['account_label']} · {helpers.fmt_date(t['purchase_date'])}", t["category_label"]]
        facts += [f"Installment {b['number']} of {b['total']}" for b in t["badges"] if b["type"] == "installment"]
        facts += [f"{b['currency']} {b['amount'] / 100:,.2f}" for b in t["badges"] if b["type"] == "foreign"]
        if t["description"] and t["description"] != t["merchant"]:
            facts.append(cards.clip(t["description"], 80))
        out.append(Card(
            id=f"money:{t['id']}", kind="money", source=f"Money · {t['account_label']}",
            title=u["title"], why=u["why"][:1].upper() + u["why"][1:],
            primary=cards.action("open", "Ask Dana", client=True),
            secondary=[cards.action("dismiss", "Dismiss")], schedulable=False,
            ref={"type": "transaction", "id": t["id"]},
            blocks=[{"type": "facts", "label": "Charge", "items": facts}],
        ))
    return out[:MAX_MONEY]


def savings_cards(user: User, now: datetime.datetime, hidden: Dict[str, Any]) -> List[Card]:
    if not store.is_connected(user, config.ADDON):
        return []
    today = now.date()
    try:
        items = savings.overview(user, today)["items"]
    except Exception as e:
        logger.warning(f"Now savings cards for user {user.id} failed: {e}")
        return []
    sh = money_store.money_text
    saves: List[Card] = []
    targets: List[Card] = []
    for i in items:
        if not i["active"]:
            continue
        if i["group"] == "save" and i["status"] in ("open", "doing"):
            until = (i.get("timing") or {}).get("until")
            left = (datetime.date.fromisoformat(until) - today).days if until else None
            fresh = (i["type"] == "bill" and i["status"] == "open"
                     and (today - datetime.date.fromisoformat(i["first_seen"])).days <= SAVINGS_NEW_DAYS)
            if not ((left is not None and 0 <= left <= SAVINGS_DUE_DAYS) or fresh):
                continue
            item_id = f"money_save:{i['key']}"
            if item_id in hidden:
                continue
            s = i["saving"]
            facts = [f"Saves {sh(s['low'])}–{sh(s['high'])} a year" if s and s["low"] != s["high"]
                     else f"Saves {sh(s['high'])} a year" if s else f"{sh(i['at_stake'])} a year at stake"]
            if i["steps"]:
                facts.append(i["steps"][0]["text"])
            why = (f"{i['timing']['label']} · in {left} day{'s' if left != 1 else ''}" if left is not None
                   and (i.get("timing") or {}).get("label") else i["summary"])
            saves.append(Card(
                id=item_id, kind="money", source="Money · Ways to save", title=i["title"], why=why,
                primary=cards.action("open", "Ask Dana", client=True),
                secondary=[cards.action("dismiss", "Dismiss")], schedulable=False,
                ref={"type": "saving", "id": i["key"]},
                blocks=[{"type": "facts", "label": "Saving", "items": facts}],
            ))
        t = i.get("target")
        if i["status"] != "watching" or not t:
            continue
        name = savings.short_name(user, i["key"])
        month = today.strftime("%Y-%m")
        if t["monthly"] and t["this_month"] >= TARGET_WARN_SHARE * t["monthly"] and f"money_target:{i['key']}:{month}" not in hidden:
            share = round(t["this_month"] / t["monthly"] * 100)
            targets.append(Card(
                id=f"money_target:{i['key']}:{month}", kind="money", source="Money · Spending to watch",
                title=f"{name}: {sh(t['this_month'])} of {sh(t['monthly'])} this month",
                why=f"{share}% of your target with {t['days_left']} day{'s' if t['days_left'] != 1 else ''} left",
                primary=cards.action("open", "Ask Dana", client=True),
                secondary=[cards.action("dismiss", "Dismiss")], schedulable=False,
                ref={"type": "saving", "id": i["key"]},
                blocks=[{"type": "facts", "label": "Target",
                         "items": [f"Target {sh(t['monthly'])} a month", f"Spent {sh(t['this_month'])} so far"]}],
            ))
        last = t["months"][-1] if t["months"] else None
        if (today.day <= TARGET_RESULT_DAYS and last and f"money_target:{i['key']}:{last['month']}" not in hidden
                and last["month"] == (today.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")):
            label = datetime.date.fromisoformat(last["month"] + "-01").strftime("%B")
            why = (f"Under your target by {sh(last['target'] - last['spent'])}" if last["under"]
                   else f"Over your target by {sh(last['spent'] - last['target'])}")
            targets.append(Card(
                id=f"money_target:{i['key']}:{last['month']}", kind="money", source="Money · Spending to watch",
                title=f"{name} in {label}: {sh(last['spent'])} of {sh(last['target'])}", why=why,
                primary=cards.action("open", "Ask Dana", client=True),
                secondary=[cards.action("dismiss", "Dismiss")], schedulable=False,
                ref={"type": "saving", "id": i["key"]},
                blocks=[{"type": "facts", "label": "Target",
                         "items": [f"Saved {sh(t['saved'])} against your usual pace so far"]}],
            ))
    return saves[:MAX_SAVINGS] + targets[:MAX_SAVINGS]


@addon.now_cards(order=60)
def money_now_cards(user: User, ctx: NowContext) -> List[Card]:
    return unusual_cards(user, ctx.now, ctx.hidden) + savings_cards(user, ctx.now, ctx.hidden)


@addon.now_action("money")
async def act_money(user: User, txn_id: str, action: str, args: Dict[str, Any],
                    now: datetime.datetime) -> Dict[str, Any]:
    if money_store.get_transaction(user, txn_id) is None:
        raise cards.ActError("Transaction not found", 404)
    if action == "dismiss":
        msg = "Dismissed"
        return cards.result(msg, cards.hide_with_undo(user, f"money:{txn_id}", cards.FAR, msg, reason="dismiss"))
    if action in ("snooze", "schedule"):
        slot = str(args.get("slot") or "")
        until = now_screen.hide_until_for_slot(slot, now) if slot else cards.until_from(args, now)
        if not until:
            raise cards.ActError("snooze needs minutes, until or slot")
        msg = f"Hidden until {helpers.when_phrase(until, now)}"
        return cards.result(msg, cards.hide_with_undo(user, f"money:{txn_id}", until, msg))
    raise cards.ActError(f"'{action}' isn't an action for a charge")


async def _hide_saving_card(user: User, key: str, item_id: str, action: str, args: Dict[str, Any],
                            now: datetime.datetime) -> Dict[str, Any]:
    if await asyncio.to_thread(savings.get_item, user, key, now.date()) is None:
        raise cards.ActError("Saving not found", 404)
    if action == "dismiss":
        msg = "Dismissed"
        return cards.result(msg, cards.hide_with_undo(user, item_id, cards.FAR, msg, reason="dismiss"))
    if action in ("snooze", "schedule"):
        slot = str(args.get("slot") or "")
        until = now_screen.hide_until_for_slot(slot, now) if slot else cards.until_from(args, now)
        if not until:
            raise cards.ActError("snooze needs minutes, until or slot")
        msg = f"Hidden until {helpers.when_phrase(until, now)}"
        return cards.result(msg, cards.hide_with_undo(user, item_id, until, msg))
    raise cards.ActError(f"'{action}' isn't an action for a saving")


@addon.now_action("money_save")
async def act_money_save(user: User, key: str, action: str, args: Dict[str, Any],
                         now: datetime.datetime) -> Dict[str, Any]:
    return await _hide_saving_card(user, key, f"money_save:{key}", action, args, now)


@addon.now_action("money_target")
async def act_money_target(user: User, ref: str, action: str, args: Dict[str, Any],
                           now: datetime.datetime) -> Dict[str, Any]:
    key, _, month = ref.rpartition(":")
    if not key or not re.fullmatch(r"\d{4}-\d{2}", month):
        raise cards.ActError("id must look like 'money_target:<key>:<YYYY-MM>'")
    return await _hide_saving_card(user, key, f"money_target:{ref}", action, args, now)


# --- Weekly review & providers --------------------------------------------------------------

@addon.review("money")
def review_money(user: User, ctx: BriefingContext) -> Optional[Section]:
    try:
        week = queries.week(user, ctx.now.date())
    except Exception as e:
        logger.warning(f"Review money section for user {user.id} failed: {e}")
        return None
    try:
        week["savings"] = savings.week_line(user, ctx.now.date())
    except Exception as e:
        logger.warning(f"Review savings line for user {user.id} failed: {e}")
        week["savings"] = None
    if not (week["spent"] or week["usual"] or week["new_recurring"] or week["price_changes"] or week["savings"]):
        return None
    return Section({"money": week})


@addon.provide("money_week_lines")
def provide_week_lines(week: Dict[str, Any]) -> List[str]:
    return queries.week_lines(week)


@addon.provide("telegram_reply")
def provide_telegram_reply(user: User, text: str) -> Optional[str]:
    return otp.telegram_reply(user, text)


# --- Context chats --------------------------------------------------------------------------

@addon.context_card("transaction")
def transaction_card(user: User, ref_id: str) -> Dict[str, Any]:
    return queries.chat_context(user, ref_id)


@addon.context("transaction")
def transaction_context(user: User, ref_id: str, req: ContextRequest) -> Optional[str]:
    return queries.context_prompt(user, ref_id)


@addon.context_card("saving")
def saving_card(user: User, ref_id: str) -> Dict[str, Any]:
    return savings.chat_context(user, ref_id)


@addon.context("saving")
def saving_context(user: User, ref_id: str, req: ContextRequest) -> Optional[str]:
    return savings.context_prompt(user, ref_id)


# --- Self-check -----------------------------------------------------------------------------

@addon.selfcheck("money", "Bank and card syncs", after="briefing")
def check_money(user: User, now: datetime.datetime) -> Check:
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT company, label, state, state_reason, last_ok_at, created_at FROM money_sources WHERE user_id = ? "
            "ORDER BY created_at, id", (user.id,)).fetchall()]
    if not rows:
        return Check(True, "no bank or card added")
    problems: List[Tuple[str, str]] = []
    for r in rows:
        name = (r.get("label") or "").strip() or providers.name_of(r["company"])
        last_ok = helpers.parse_time(r.get("last_ok_at"))
        if r["state"] == "needs_code":
            problems.append((f"{name} is waiting for a sign-in code",
                             f"{name}: waiting for the SMS sign-in code (enter it on the Money page or in Telegram)"))
        elif r["state"] == "needs_attention":
            problems.append((f"{name} needs its login fixed", f"{name}: {r.get('state_reason') or 'the login failed'}"))
        else:
            since = last_ok or helpers.parse_time(r.get("created_at"))
            if since and (now - since).total_seconds() > config.STALE_HOURS * 3600:
                quiet = helpers.span((now - since).total_seconds())
                why = f" ({r['state_reason']})" if r.get("state_reason") else ""
                problems.append((f"{name} hasn't synced for {quiet}",
                                 f"{name}: no successful sync for {quiet}{why}" if last_ok
                                 else f"{name}: never synced{why}"))
    ok_times = [t for r in rows for t in (helpers.parse_time(r.get("last_ok_at")),) if t]
    latest = max(ok_times) if ok_times else None
    if problems:
        detail = problems[0][1] + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else "")
        return Check(False, detail, latest, title=problems[0][0],
                     why="New transactions are missing from the Money page and Dana's answers.",
                     blocks=[{"type": "facts", "label": "Banks and cards", "items": [p[1] for p in problems]}])
    return Check(True, f"synced {helpers.ago(latest, now) if latest else 'never'}", latest)
