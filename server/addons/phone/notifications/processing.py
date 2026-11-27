"""Processing a user's new notifications: triage → suggestions → escalation.

:func:`schedule` first hands the new ids to the other addons' observers
(``phone.notification.ingested``, e.g. money's payment signals), then queues
them for triage. Each user has one queue; a worker takes up to the judge's
batch size (:mod:`.judges`: the LLM, or TypeSafe) at a time, at most
``CONCURRENCY`` batches at once across users, so notifications that arrive
while a call is running go together in the next one. :func:`process_batch`
(run as the user) asks the judge, then :func:`triage.decide` →
``category``, ``priority``, ``bucket``. A notification the judge did not
answer stays ``triage_status='pending'`` (``bucket='normal'``, never a guessed
answer) with its attempts counted in ``triage``; :func:`retry_pending` tries
it again later, and after :data:`MAX_ATTEMPTS` it is ``failed``. When the
triage finds something to do, the model extracts it into task / event
suggestions (the ``call_suggestions`` table, ``source_type='notification'``),
with the user's own context (open tasks, zones, task groups), accepted only by
them like call suggestions. Urgent ones are announced on the user's phone
(:mod:`.escalation`).
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from core import budget, clock, events, llm, user_profile, users
from core.db import get_db_connection
from core.text import untrusted
from core.users import User
from addons.phone import popups, suggestions
from addons.phone.calls.analysis import analysis_schema, normalize_analysis
from addons.phone.calls.context import feedback_prompt, gather_context
from addons.phone.calls.suggest import replace_pending_suggestions, suggestions_from_analysis
from addons.phone.notifications import escalation, judges, rules, triage
from addons.phone.notifications import whatsapp as notif_whatsapp
from addons.phone.notifications.values import (CONCURRENCY, ESCALATE_MAX_AGE_SECONDS, EXTRACT_MAX_AGE_HOURS,
                                                     age_seconds, row)

logger = logging.getLogger(__name__)

# A notification the judge did not answer this many times is marked failed.
MAX_ATTEMPTS = 3
# retry_pending leaves alone what was tried less than this long ago.
RETRY_AFTER_SECONDS = 120

_sem: Optional[asyncio.Semaphore] = None
_scheduled: Set[str] = set()
_tasks: Set[asyncio.Task] = set()
_queues: Dict[str, Dict[str, None]] = {}   # user id → ids waiting for triage, in order
_workers: Dict[str, asyncio.Task] = {}     # user id → the task draining their queue


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
    """Emit ``phone.notification.ingested`` for other addons (e.g. money's
    payment signals), then queue background triage. Returns how many were queued."""
    ids = [i for i in dict.fromkeys(ids or []) if isinstance(i, str) and i]
    if ids:
        events.emit("phone.notification.ingested", user, ids=ids)
    return _enqueue(user, ids)


def queue(user: User, ids: Iterable[str]) -> int:
    """Queue ``user``'s ``ids`` for triage without telling other addons (rows that are not
    the phone's own notifications, e.g. WhatsApp bursts from WAHA). Returns how many."""
    return _enqueue(user, [i for i in dict.fromkeys(ids or []) if isinstance(i, str) and i])


def _enqueue(user: User, ids: Sequence[str]) -> int:
    """Queue ``user``'s ``ids`` for triage (those not already queued or running)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return 0
    queue = _queues.setdefault(user.id, {})
    n = 0
    for nid in ids:
        if nid in _scheduled:
            continue
        _scheduled.add(nid)
        queue[nid] = None
        n += 1
    worker = _workers.get(user.id)
    if queue and (worker is None or worker.done() or worker.get_loop() is not loop):
        started = _start(user, _drain(user))
        if started is not None:
            _workers[user.id] = started
    return n


async def _drain(user: User) -> None:
    """Hand ``user``'s queue to the judge a batch at a time, waiting for a free slot
    before taking each batch (so what arrives meanwhile joins it)."""
    queue = _queues.get(user.id)
    while queue:
        await _semaphore().acquire()
        chunk = list(queue)[:judges.judge().batch_size]
        if not chunk:
            _semaphore().release()
            break
        for nid in chunk:
            del queue[nid]
        if _start(user, _run(user, chunk)) is None:
            _semaphore().release()
            break


async def _run(user: User, ids: List[str]) -> None:
    try:
        await process_batch(user, ids)
    except Exception as e:
        logger.error(f"Notifications {ids} processing failed: {e}", exc_info=True)
    finally:
        _semaphore().release()
        for nid in ids:
            _scheduled.discard(nid)


async def process_notification(user: User, nid: str) -> Optional[Dict[str, Any]]:
    """Triage ``user``'s notification ``nid`` now (as ``user``), then extract / escalate.
    None when not found or not judged."""
    return (await process_batch(user, [nid])).get(nid)


async def process_batch(user: User, ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """Triage ``user``'s notifications ``ids`` (as ``user``; ids that are not theirs are
    skipped), then extract / escalate. Returns the decision per notification judged."""
    with users.acting_as(user):
        return await _process(user, ids)


async def _process(user: User, ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    todo: List[Dict[str, Any]] = []
    with get_db_connection() as conn:
        for nid in dict.fromkeys(ids):
            n = row(conn, user, nid)
            if not n:
                continue
            n["_rule"] = rules.rule_for(conn, user, n["package"], n.get("sender") or "")
            n["_low"] = rules.is_low_engagement(rules.engagement(conn, user, n["package"]))
            todo.append(n)

    decided: Dict[str, Dict[str, Any]] = {}
    for n in [n for n in todo if n["_rule"] == "mute"]:
        decided[n["id"]] = _store(user, n, triage.decide_rule_only("mute"), "skipped", "")
    ask = [n for n in todo if n["_rule"] != "mute"]
    judge = judges.judge()
    if ask and judge.name == "llm" and budget.holds(judges.PURPOSE):
        ask = []  # the monthly budget holds the model: they stay pending, judged once it lets go
    if ask:
        who = user_profile.for_user(user)
        for i in range(0, len(ask), judge.batch_size):
            part = ask[i:i + judge.batch_size]
            error = "not judged"
            try:
                answers = await judge.judge(who, part)
            except triage.TriageError as e:
                answers, error = {}, str(e)
                logger.warning(f"Triage failed for {len(part)} notification(s) of user {user.id}: {e}")
            for n in part:
                if n["id"] in answers:
                    d = _source_rules(user, n, triage.decide(answers[n["id"]], rule=n["_rule"],
                                                             low_engagement=n["_low"]))
                    decided[n["id"]] = _store(user, n, d, "done", judge.model())
                else:
                    _not_judged(user, n, error)

    for n in todo:
        if n["id"] in decided:
            await _follow_up(user, n["id"], decided[n["id"]])
    return decided


def _source_rules(user: User, n: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    """WhatsApp through WAHA (:mod:`.whatsapp`): its bursts make the suggestions and are never
    announced (the phone's own notification is); the phone's WhatsApp notifications then make none."""
    if (n.get("source") or "phone") != "phone":
        return {**decision, "escalate": False}
    if decision["extract"] and n.get("package") in notif_whatsapp.PACKAGES and notif_whatsapp.suppresses(user, n):
        return {**decision, "extract": False, "signals": {**decision["signals"], "suggested_from": "waha"}}
    return decision


def _store(user: User, n: Dict[str, Any], decision: Dict[str, Any], status: str, model: str) -> Dict[str, Any]:
    """Write ``decision`` on ``user``'s notification ``n``."""
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE notifications SET triage_status = ?, category = ?, priority = ?, priority_score = ?, bucket = ?, "
            "triage = ?, action_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (status, decision["category"], decision["priority"], decision["priority_score"], decision["bucket"],
             json.dumps({"model": model, "low_engagement": n["_low"], "rule": n["_rule"], **decision["signals"]},
                        ensure_ascii=False),
             "pending" if decision["extract"] else "none", n["id"], user.id))
        conn.commit()
    return decision


def _attempts(n: Dict[str, Any]) -> int:
    """How many times the judge has failed ``n`` so far (kept in ``triage`` while it is unanswered)."""
    if n.get("triage_status") not in ("pending", "failed"):
        return 0
    try:
        return int((json.loads(n.get("triage") or "{}") or {}).get("attempts") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def _not_judged(user: User, n: Dict[str, Any], error: str) -> None:
    """``n`` got no answer: pending for a later try, failed after :data:`MAX_ATTEMPTS`."""
    attempts = _attempts(n) + 1
    with get_db_connection() as conn:
        conn.execute("UPDATE notifications SET triage_status = ?, triage = ?, updated_at = CURRENT_TIMESTAMP "
                     "WHERE id = ? AND user_id = ?",
                     ("failed" if attempts >= MAX_ATTEMPTS else "pending",
                      json.dumps({"error": error[:300], "attempts": attempts}), n["id"], user.id))
        conn.commit()


async def _follow_up(user: User, nid: str, decision: Dict[str, Any]) -> None:
    """Escalate / extract suggestions for a freshly triaged notification."""
    with get_db_connection() as conn:
        n = row(conn, user, nid)
    if not n:
        return
    # The first sync after access is granted (and a phone that was offline)
    # delivers old notifications: announcing them now, or suggesting work from
    # days ago, is noise.
    age = age_seconds(n.get("posted_at"))
    if decision["escalate"] and n["status"] == "active" and age <= ESCALATE_MAX_AGE_SECONDS:
        await escalation.escalate(user, n)
    if decision["extract"]:
        if age <= EXTRACT_MAX_AGE_HOURS * 3600:
            made = await extract_suggestions(user, n)
            if made and age <= popups.SUGGESTION_POPUP_MAX_AGE_HOURS * 3600:
                who = n.get("sender") or n.get("title") or ""
                await popups.announce_suggestions(user, "notification", n["id"],
                                                  f"From {n.get('app_name')}" + (f" · {who}" if who and who != n.get("app_name") else ""))
        else:
            with get_db_connection() as conn:
                conn.execute("UPDATE notifications SET action_status = 'none' WHERE id = ? AND user_id = ?",
                             (nid, user.id))
                conn.commit()


def retry_pending(user: User, max_age_hours: int = 24) -> int:
    """Queue ``user``'s recent notifications still pending (not answered yet, under
    :data:`MAX_ATTEMPTS`, not tried in the last :data:`RETRY_AFTER_SECONDS`). Returns how many."""
    since = (clock.now() - datetime.timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
    before = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(seconds=RETRY_AFTER_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, triage, triage_status FROM notifications WHERE user_id = ? AND triage_status = 'pending' "
            "AND posted_at >= ? AND updated_at <= ? ORDER BY posted_at DESC LIMIT 300",
            (user.id, since, before)).fetchall()]
    return _enqueue(user, [r["id"] for r in rows if _attempts(r) < MAX_ATTEMPTS])


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
    conv = f"In the group \"{n['conversation']}\"\n" if n.get("conversation") and n.get("is_group") else ""
    shown = untrusted("the notification", f"{conv}Title: {n.get('title') or ''}\nSender: {who}\n"
                                          f"Text:\n{(n.get('text') or '')[:6000]}")
    chat_note = ""
    if (n.get("source") or "phone") == notif_whatsapp.SOURCE:
        chat_note = (f"These are new WhatsApp messages{' in a group' if n.get('is_group') else ''}, read from the chat itself: "
                     f"the new messages first (with the text each one replies to), then earlier messages of the chat for context. "
                     f"Suggest only from the new messages; use the earlier ones to understand them (who asks what, a date said before) "
                     f"and to skip what {me} already answered, refused or did.\n")
    return f"""A notification appeared on {who_am_i.name}'s phone. Decide whether {who_am_i.called} needs a to-do or calendar event because of it — most notifications need none.

{who_am_i.about()}Now: {clock.describe_now()}
Notification from the app "{n.get('app_name')}", posted {str(n.get('posted_at') or '')[:16].replace('T', ' ')}:
{chat_note}{shown}

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
    """Turn ``user``'s notification into pending suggestions (as ``user``). Returns how many
    (0 while the monthly budget holds background work: it stays pending for a later try)."""
    if budget.holds("notification_extract"):
        return 0
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
            # scoped: n is the user's notification (read with row(conn, user, …))
            conn.execute("UPDATE notifications SET action_status = 'done', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (n["id"],))
            conn.commit()
        return len(found)
    except Exception as e:
        logger.warning(f"Suggestion extraction failed for notification {n.get('id')}: {e}")
        with get_db_connection() as conn:
            # scoped: n is the user's notification (read with row(conn, user, …))
            conn.execute("UPDATE notifications SET action_status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (n["id"],))
            conn.commit()
        return 0
