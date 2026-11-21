"""Processing a user's new notifications: triage → suggestions → escalation.

:func:`schedule` first hands the new ids to the other addons' observers
(:mod:`.observers`, e.g. money's payment signals), then queues each new
notification for :func:`process_notification` (run as its user), which
triages it with TypeSafe (:mod:`.triage`) →
``category``, ``priority``, ``bucket``. A failed triage leaves
``triage_status='failed'`` and ``bucket='normal'``. When the triage finds
something to do, the model extracts it into task / event suggestions (the
``call_suggestions`` table, ``source_type='notification'``), with the user's
own context (open tasks, zones, task groups), accepted only by them like call
suggestions. Urgent ones are announced on the user's phone (:mod:`.escalation`).
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Set

import httpx

from core import clock, llm, user_profile, users
from core.db import get_db_connection
from core.users import User
from addons.phone import popups, suggestions
from addons.phone.calls.analysis import analysis_schema, normalize_analysis
from addons.phone.calls.context import feedback_prompt, gather_context
from addons.phone.calls.suggest import replace_pending_suggestions, suggestions_from_analysis
from addons.phone.notifications import escalation, observers, rules, triage
from addons.phone.notifications.values import (CONCURRENCY, ESCALATE_MAX_AGE_SECONDS, EXTRACT_MAX_AGE_HOURS,
                                                     age_seconds, row)

logger = logging.getLogger(__name__)

_sem: Optional[asyncio.Semaphore] = None
_scheduled: Set[str] = set()
_tasks: Set[asyncio.Task] = set()


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(CONCURRENCY)
    return _sem


def _start(user: User, coro) -> Optional[asyncio.Task]:
    """Run ``coro`` in the background as ``user`` (None without a running loop)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return None
    with users.acting_as(user):  # the task copies the context: it runs as the user
        t = loop.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return t


def schedule(user: User, ids: Iterable[str]) -> int:
    """Hand ``user``'s stored notifications to the other addons' observers
    (:mod:`.observers`), then queue their background triage (no-op without a
    running loop, e.g. in scripts)."""
    ids = list(ids)
    observers.notify(user, ids)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return 0
    n = 0
    for nid in ids:
        if nid in _scheduled:
            continue
        _scheduled.add(nid)
        _start(user, _run(user, nid))
        n += 1
    return n


async def _run(user: User, nid: str) -> None:
    try:
        async with _semaphore():
            await process_notification(user, nid)
    except Exception as e:
        logger.error(f"Notification {nid} processing failed: {e}", exc_info=True)
    finally:
        _scheduled.discard(nid)


async def process_notification(user: User, nid: str, *,
                               client: Optional[httpx.AsyncClient] = None) -> Optional[Dict[str, Any]]:
    """Triage ``user``'s notification ``nid`` (as ``user``), then extract / escalate. None when not found or failed."""
    with users.acting_as(user):
        return await _process(user, nid, client)


async def _process(user: User, nid: str, client: Optional[httpx.AsyncClient]) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        n = row(conn, user, nid)
        if not n:
            return None
        rule = rules.rule_for(conn, user, n["package"], n.get("sender") or "")
        low = rules.is_low_engagement(rules.engagement(conn, user, n["package"]))

    if rule == "mute":
        decision = triage.decide_rule_only(rule)
        status = "skipped"
    else:
        who = user_profile.for_user(user)
        state = triage.build_state(n, who.name, who.context)
        try:
            answers = await triage.ask(state, me=who.first_name, pronoun=who.pronoun, client=client, ref=f"notification:{nid}")
        except triage.TriageError as e:
            logger.warning(f"Triage failed for notification {nid}: {e}")
            with get_db_connection() as conn:
                conn.execute("UPDATE notifications SET triage_status = 'failed', triage = ?, "
                             "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                             (json.dumps({"error": str(e)[:300]}), nid))
                conn.commit()
            return None
        decision = triage.decide(answers, rule=rule, low_engagement=low)
        status = "done"

    with get_db_connection() as conn:
        conn.execute(
            "UPDATE notifications SET triage_status = ?, category = ?, priority = ?, priority_score = ?, bucket = ?, "
            "triage = ?, action_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, decision["category"], decision["priority"], decision["priority_score"], decision["bucket"],
             json.dumps({"model": triage.MODEL, "low_engagement": low, "rule": rule, **decision["signals"]},
                        ensure_ascii=False),
             "pending" if decision["extract"] else "none", nid))
        conn.commit()
        n = row(conn, user, nid)

    # The first sync after access is granted (and a phone that was offline)
    # delivers old notifications: announcing them now, or suggesting work from
    # days ago, is noise.
    age = age_seconds(n.get("posted_at") if n else None)
    if decision["escalate"] and n and n["status"] == "active" and age <= ESCALATE_MAX_AGE_SECONDS:
        await escalation.escalate(user, n)
    if decision["extract"] and n:
        if age <= EXTRACT_MAX_AGE_HOURS * 3600:
            made = await extract_suggestions(user, n)
            if made and age <= popups.SUGGESTION_POPUP_MAX_AGE_HOURS * 3600:
                who = n.get("sender") or n.get("title") or ""
                await popups.announce_suggestions(user, "notification", n["id"],
                                                  f"From {n.get('app_name')}" + (f" · {who}" if who and who != n.get("app_name") else ""))
        else:
            with get_db_connection() as conn:
                conn.execute("UPDATE notifications SET action_status = 'none' WHERE id = ?", (nid,))
                conn.commit()
    return decision


def resume_unfinished(user: User, max_age_hours: int = 24) -> int:
    """After a restart: triage what ``user`` had queued, retry their recent failures."""
    since = (clock.now() - datetime.timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM notifications WHERE user_id = ? AND triage_status IN ('pending', 'failed') "
            "AND posted_at >= ? ORDER BY posted_at DESC LIMIT 300", (user.id, since)).fetchall()]
        retry = [r["id"] for r in conn.execute(
            "SELECT id FROM notifications WHERE user_id = ? AND action_status = 'pending' AND triage_status = 'done' "
            "AND posted_at >= ? LIMIT 50", (user.id, since)).fetchall()]
    n = schedule(user, ids)
    for nid in retry:
        if _start(user, _extract_by_id(user, nid)) is None:
            break
    if n or retry:
        logger.info(f"Notifications: resumed {n} triage(s), {len(retry)} extraction(s) for user {user.id}")
    return n


async def _extract_by_id(user: User, nid: str) -> None:
    async with _semaphore():
        with get_db_connection() as conn:
            n = row(conn, user, nid)
        if n:
            await extract_suggestions(user, n)


# --- Suggestion extraction ---------------------------------------------------

def _extraction_schema() -> Dict[str, Any]:
    items = analysis_schema()["properties"]["action_items"]
    return {"type": "object", "properties": {"action_items": items}, "required": ["action_items"]}


def build_extraction_prompt(user: User, n: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """The extraction prompt for ``user``'s notification (worded for them; for
    the first admin exactly as it always was)."""
    who_am_i = user_profile.for_user(user)
    me = who_am_i.first_name
    he, him, himself = who_am_i.pronoun, who_am_i.obj, who_am_i.reflexive
    has, does = who_am_i.agree("has", "have"), who_am_i.agree("does", "do")
    open_tasks = "\n".join(f"- {t['title']}" for t in ctx.get("open_tasks") or []) or "- (none)"
    zones = ", ".join(ctx.get("zones") or []) or "(unavailable — do not set location triggers)"
    groups = ", ".join(ctx.get("groups") or [])
    who = n.get("sender") or n.get("title") or ""
    conv = f" in the group \"{n['conversation']}\"" if n.get("conversation") and n.get("is_group") else ""
    return f"""A notification appeared on {who_am_i.name}'s phone. Decide whether {who_am_i.called} needs a to-do or calendar event because of it — most notifications need none.

{who_am_i.about()}Now: {clock.describe_now()}
Notification from the app "{n.get('app_name')}", posted {str(n.get('posted_at') or '')[:16].replace('T', ' ')}{conv}:
Title: {n.get('title') or ''}
Sender: {who}
Text:
{(n.get('text') or '')[:6000]}

Return JSON with action_items: an empty list unless there is a clear thing {me} must remember to do later (at most 2).
A good suggestion is something nothing else will remind {me} of: a person asking {him} to do something, a benefit or form with a deadline, a bill {he} must pay {himself} before it is due, a pickup.
Return nothing for:
- Items a work tool keeps tracking (GitHub pull requests, failed builds, review/approval requests, Jira tickets and mentions) — the tool is the reminder.
- Receipts, charges, statements or reports "ready to view", invoices that are charged automatically or whose due date has passed.
- Device or home states (stuck robot, full tank, storage full, battery) and alarms.
- Marketing, recruiting or "you have a new message on <platform>" mails that don't say what is asked.
- Messages {me} wrote {himself}, or group messages meant for someone else.
- Meetings already in a calendar (a reminder is not a new event).
Rules for an item you do return:
- Only things {me} {himself} {has} to do (owner "me"), or attend (kind "event" with event_start "YYYY-MM-DDTHH:MM").
- Write it in the notification's language, short, concrete, starting with the verb ("להחזיר לדן תשובה על שישי", "Pay the electricity bill").
- Resolve relative dates against "Now"; write absolute dates. due_date "YYYY-MM-DD" when there is a deadline.
- At most one trigger, and only when it helps: time_trigger "YYYY-MM-DD HH:MM" for the moment {he} can act (not a default 09:00 for work {he} {does} at work anyway); location_zone only from: {zones}; car_trigger "enter" for pickups on the way. No wake_trigger together with another trigger.
- priority high only for deadlines within 2 days or security issues. group one of: {groups}.
- confidence 0-1: how sure you are {me} wants this tracked. Below 0.6 if you hesitate.
- evidence: the words from the notification that support it.
{feedback_prompt(user, ctx, "notification")}- Skip anything already on {me}'s open task list:
{open_tasks}"""


async def _model_items(user: User, n: Dict[str, Any], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = await llm.complete_json(build_extraction_prompt(user, n, ctx), _extraction_schema(), job="notifications",
                                  purpose="notification_extract", source="phone",
                                  ref=f"notification:{n['id']}" if n.get("id") else "")
    return raw.get("action_items") or []


async def extract_suggestions(user: User, n: Dict[str, Any]) -> int:
    """Turn ``user``'s notification into pending suggestions (as ``user``). Returns how many."""
    try:
        with users.acting_as(user):
            ctx = await gather_context(user)
            raw_items = await _model_items(user, n, ctx)
        analysis = normalize_analysis({"action_items": raw_items}, ctx)
        who = n.get("sender") or n.get("title") or n.get("app_name")
        origin = f"From a {n.get('app_name')} notification" + (f" ({who})" if who and who != n.get("app_name") else "")
        existing = suggestions.pending_titles(user)
        found = suggestions_from_analysis(
            user, {"contact": who, "when": None}, analysis, open_tasks=ctx.get("open_tasks"),
            origin=origin, extra_titles=existing)
        for sg in found:
            # One reminder moment per notification item: the most specific one.
            trig = (sg.get("payload") or {}).get("triggers") or {}
            if len(trig) > 1:
                key = next(k for k in ("time", "location", "car", "wake") if k in trig)
                sg["payload"]["triggers"] = {key: trig[key]}
        with get_db_connection() as conn:
            replace_pending_suggestions(conn, user, n["id"], found, source_type="notification")
            conn.execute("UPDATE notifications SET action_status = 'done', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (n["id"],))
            conn.commit()
        return len(found)
    except Exception as e:
        logger.warning(f"Suggestion extraction failed for notification {n.get('id')}: {e}")
        with get_db_connection() as conn:
            conn.execute("UPDATE notifications SET action_status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (n["id"],))
            conn.commit()
        return 0
