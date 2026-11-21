"""Weekly review: every Saturday evening, each user tidies their task list in one go.

1. Plain queries find what needs a decision among the user's own data:
   each addon's part (``@addon.briefing`` for the weekly review, acted on by
   its ``@addon.review_action``; the tasks addon: overdue tasks, tasks
   untouched for ``REVIEW_STALE_DAYS`` (21), tasks snoozed 3+ times),
   suggestions pending over a week (with ``phone``), and
   people they usually talk to but haven't lately (with ``people``). With
   ``money``, a Money section: spent this week vs a usual week, the top
   groups, new recurring charges and price changes (``addons/money/queries.py::week``)
   and a savings line (``addons/money/savings.py::week_line``).
2. The model proposes one action per item (complete / reschedule / delete /
   move to a group / keep; accept or dismiss a suggestion; a "reach out"
   task for a person) plus a few lines about the week, written for them
   (``core/user_profile.for_user``).
3. They review the list (phone, web), untick what they disagree with and
   apply it with one call: ``POST /api/review/{id}/apply``.

For every user with an addon the review is about (``tasks``); reviews belong to their user
(``weekly_reviews.user_id``). Schedule: ``WEEKLY_REVIEW_AT`` ("sat 20:00"); a
review missed by a restart is still made until noon the next day. One review
per user and ISO week.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional

from core import briefing, clock, composers, llm, user_profile, users
from core.db import get_db_connection
from core.notify import notify
from core.users import User
from addons.calendar import config as calendar_config, events as calendar_events
from addons.money import config as money_config, queries as money_queries, savings as money_savings
from core.addons import registry, store
from core.addons.helpers import age_days, next_weekday, valid_future_date
from addons.people import cadence as people_cadence
from addons.people.values import ADDON as PEOPLE
from addons.phone import push as phone_push, suggestions as phone_suggestions
from addons.phone.calls import actions as suggestion_actions
from addons.phone.config import ADDON as PHONE

logger = logging.getLogger(__name__)

REVIEW_AT = os.getenv("WEEKLY_REVIEW_AT", "sat 20:00")
STALE_DAYS = int(os.getenv("REVIEW_STALE_DAYS", "21"))
SNOOZE_MIN = int(os.getenv("REVIEW_SNOOZE_MIN", "3"))
SUGGESTION_DAYS = int(os.getenv("REVIEW_SUGGESTION_DAYS", "7"))
MAX_ITEMS = int(os.getenv("REVIEW_MAX_ITEMS", "25"))
REVIEW_TELEGRAM = os.getenv("REVIEW_TELEGRAM", "1") != "0"
_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# Core's own item types and what may be proposed for each; an addon adds its
# own with ``@addon.review_action(type, actions)`` (the tasks addon: "task").
ACTIONS = {
    "suggestion": ("accept", "dismiss", "keep"),
    "person": ("reach_out", "keep"),
}
# user id → the lock that keeps one generation of their review at a time.
_locks: Dict[str, asyncio.Lock] = {}


def _review_addons() -> List[str]:
    """The addons a review is about: those with an ``@addon.review_action`` (the tasks addon), in addon order."""
    return list(dict.fromkeys(h.addon for h in registry.hooks("review_action", None)))


def actions_for(item_type: str) -> tuple:
    """What may be proposed for an item of ``item_type`` (() for an unknown type; core's types first)."""
    if item_type in ACTIONS:
        return ACTIONS[item_type]
    hook = registry.hook_for("review_action", item_type, None)
    return tuple(hook.option("actions", ())) if hook else ()


def available(user: Optional[User]) -> bool:
    """Whether ``user`` gets a weekly review (an addon it is about connected: ``tasks``)."""
    return user is not None and any(store.is_connected(user, a) for a in _review_addons())


def require(user: User) -> None:
    """Raise ``NotConnected`` (403 ``not_connected``, ``tasks``) when ``user`` gets no review."""
    if not available(user):
        raise store.NotConnected(user, (_review_addons() or ["tasks"])[0])


def _now_iso() -> str:
    return clock.now().isoformat(timespec="seconds")


def week_id(day: Optional[datetime.date] = None) -> str:
    y, w, _ = (day or clock.now().date()).isocalendar()
    return f"{y}-W{w:02d}"


def _parse_at(spec: str):
    m = re.match(r"\s*([a-z]{3})\w*\s+(\d{1,2}):(\d{2})", (spec or "").lower())
    if not m or m.group(1) not in _DAYS:
        return 5, 20, 0
    return _DAYS.index(m.group(1)), int(m.group(2)), int(m.group(3))


def is_due(now: datetime.datetime) -> bool:
    wd, hh, mm = _parse_at(REVIEW_AT)
    if now.weekday() == wd and (now.hour, now.minute) >= (hh, mm):
        return True
    return now.weekday() == (wd + 1) % 7 and now.hour < 12


def _loads(raw, default):
    try:
        v = json.loads(raw) if raw else default
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Finding what needs a decision
# ---------------------------------------------------------------------------

def candidates(user: User, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """What needs a decision among ``user``'s own items: each addon's section
    (``@addon.review``: the tasks addon's overdue, snoozed-often and stale
    tasks, the week's numbers and the groups), old suggestions and people."""
    now = now or clock.now()
    parts = list(briefing.collect("review", user, now).values())
    items: List[Dict[str, Any]] = [i for p in parts for i in p.items]
    data: Dict[str, Any] = {}
    for p in parts:
        data.update(p.data or {})
    groups = list(data.get("groups") or [])
    with get_db_connection() as conn:
        # Only the user's own suggestions (they come from the phone: none without it).
        vis, vis_args = phone_suggestions.visible_sql(user)
        suggestions = conn.execute(
            f"SELECT s.id, s.kind, s.payload, s.created_at FROM call_suggestions s WHERE s.status = 'pending' AND {vis} "
            "ORDER BY s.created_at", vis_args).fetchall() if store.is_connected(user, PHONE) else []

    for s in suggestions:
        age = age_days(s["created_at"], now)
        if age is not None and age >= SUGGESTION_DAYS:
            p = _loads(s["payload"], {})
            items.append({"ref": s["id"], "type": "suggestion", "title": p.get("title") or "Suggestion",
                          "why": "old_suggestion", "facts": [f"pending {age} days", f"a {s['kind']}"],
                          "due_date": p.get("due_date")})
    try:
        drifting = people_cadence.list_people(user, sort="drifting")[:3] if store.is_connected(user, PEOPLE) else []
        for p in drifting:
            st = p["stats"]
            items.append({"ref": p["id"], "type": "person", "title": p["name"], "why": "drifting",
                          "facts": [f"last contact {st['days_since']} days ago",
                                    f"usually every ~{st['typical_gap_days']} days", p.get("relation") or ""]})
    except Exception as e:
        logger.debug(f"Review people failed: {e}")

    order = {"overdue": 0, "snoozed_often": 1, "stale": 2, "old_suggestion": 3, "drifting": 4}
    items.sort(key=lambda i: order.get(i["why"], 9))
    stats = {"done": 0, "done_titles": [], "created": 0, "open": 0, **(data.get("stats") or {}),
             "overdue": sum(1 for i in items if i["why"] == "overdue")}
    return {"items": items[:MAX_ITEMS], "stats": stats, "groups": groups, "money": money_week(user, now)}


def money_week(user: User, now: datetime.datetime) -> Optional[Dict[str, Any]]:
    """``user``'s money this week (``addons/money/queries.py::week``) with its savings line
    (``savings`` — saved so far, ideas open, targets: ``addons/money/savings.py::week_line``);
    None without ``money`` or with nothing to say (no spending this week or before, no recurring
    news, no savings line)."""
    if not store.is_connected(user, money_config.ADDON):
        return None
    try:
        week = money_queries.week(user, now.date())
    except Exception as e:
        logger.warning(f"Review money section for user {user.id} failed: {e}")
        return None
    try:
        week["savings"] = money_savings.week_line(user, now.date())
    except Exception as e:
        logger.warning(f"Review savings line for user {user.id} failed: {e}")
        week["savings"] = None
    if not (week["spent"] or week["usual"] or week["new_recurring"] or week["price_changes"] or week["savings"]):
        return None
    return week


async def next_week(user: User, now: Optional[datetime.datetime] = None) -> Optional[Dict[str, Any]]:
    """Events per day of ``user``'s calendar for the coming 7 days (from tomorrow); None without it."""
    now = now or clock.now()
    conn = store.get(user, calendar_config.ADDON)
    if conn is None:
        return None
    try:
        start = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        evs = await asyncio.to_thread(calendar_events.list_events, conn, start, start + datetime.timedelta(days=7), 100)
    except Exception as e:
        logger.info(f"Review: calendar unavailable ({e})")
        return None
    days: Dict[str, List[str]] = {}
    for e in evs:
        days.setdefault(str(e["start"])[:10], []).append(e["title"])
    per_day = [{"date": d, "weekday": datetime.date.fromisoformat(d).strftime("%a"), "count": len(t), "titles": t[:4]}
               for d, t in sorted(days.items())]
    busiest = max(per_day, key=lambda d: d["count"]) if per_day else None
    return {"events": len(evs), "days": per_day, "busiest": busiest}


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------

def default_action(item: Dict[str, Any], today: datetime.date) -> Dict[str, Any]:
    """The no-model proposal: reschedule overdue to Sunday, keep the rest unticked."""
    if item["why"] == "overdue" and "reschedule" in actions_for(item["type"]):
        return {"action": "reschedule", "due_date": next_weekday(today, 6).isoformat(),
                "reason": "Overdue — give it a fresh date"}
    if item["type"] == "suggestion":
        return {"action": "dismiss", "reason": "Pending for over a week"}
    if item["type"] == "person":
        return {"action": "reach_out", "due_date": next_weekday(today, 6).isoformat(),
                "reason": "It's been longer than usual"}
    return {"action": "keep", "reason": ""}


def review_prompt(c: Dict[str, Any], nw: Optional[Dict[str, Any]], today: datetime.date,
                  user: Optional[User]) -> str:
    """The model prompt for ``user``'s review, written for them (their name, pronoun and context)."""
    who = user_profile.for_user(user)
    his = who.possessive
    listing = [{"ref": i["ref"], "type": i["type"], "title": i["title"], "why": i["why"], "facts": i["facts"],
                "group": i.get("group"), "allowed": list(actions_for(i["type"]))} for i in c["items"]]
    money = f"Money this week: {'; '.join(money_queries.week_lines(c['money']))}\n" if c.get("money") else ""
    return f"""You're helping {who.name} with {his} weekly review (Saturday evening, before the Israeli work week starts Sunday).

{who.about()}Today: {clock.describe_now()}
This week: {c['stats']['done']} tasks done ({', '.join(c['stats']['done_titles']) or 'none'}), {c['stats']['created']} created, {c['stats']['open']} open.
Next week's calendar: {json.dumps(nw, ensure_ascii=False) if nw else 'unknown'}
{money}Task groups: {', '.join(c['groups'])}

Items that need a decision:
{json.dumps(listing, ensure_ascii=False, indent=1)}

For every item return one entry in `items` with its `ref`, an `action` from its `allowed` list, and a short `reason` (≤ 10 words).
- reschedule → `due_date` (YYYY-MM-DD, after {today.isoformat()}); spread work away from next week's busiest day.
- move_group → `group` (one of the task groups).
- reach_out → `due_date` for a "call / message them" task.
- complete only when the title or facts suggest it's already done or no longer relevant; delete for clearly dead items; keep when unsure.
- Dismiss suggestions that are old and vague; accept ones still clearly useful.
Also return `headline` (3–8 words on the week) and `summary`: 1–3 short fragments about the week and the one coming (no advice-speak, no emoji).
Write in English; keep titles and names as written."""


_SCHEMA = {"type": "object", "properties": {
    "headline": {"type": "string"},
    "summary": {"type": "array", "items": {"type": "string"}},
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "ref": {"type": "string"}, "action": {"type": "string"}, "due_date": {"type": "string"},
        "group": {"type": "string"}, "reason": {"type": "string"}}, "required": ["ref", "action"]}}},
    "required": ["headline", "summary", "items"]}


def merge_proposals(c: Dict[str, Any], raw: Optional[Dict[str, Any]], today: datetime.date) -> List[Dict[str, Any]]:
    by_ref = {str(p.get("ref")): p for p in ((raw or {}).get("items") or []) if isinstance(p, dict)}
    groups_lower = {g.lower(): g for g in c["groups"]}
    out = []
    for item in c["items"]:
        p = by_ref.get(item["ref"]) or {}
        action = str(p.get("action") or "").strip().lower()
        proposal = default_action(item, today) if action not in actions_for(item["type"]) else {
            "action": action, "reason": re.sub(r"\s+", " ", str(p.get("reason") or "")).strip()[:120]}
        if proposal["action"] in ("reschedule", "reach_out"):
            proposal["due_date"] = valid_future_date(p.get("due_date") or proposal.get("due_date"), today) \
                or next_weekday(today, 6).isoformat()
        if proposal["action"] == "move_group":
            g = groups_lower.get(str(p.get("group") or "").strip().lower())
            if not g or g == item.get("group"):
                proposal = {"action": "keep", "reason": proposal.get("reason", "")}
            else:
                proposal["group"] = g
        out.append({**item, **proposal, "enabled": proposal["action"] != "keep"})
    return out


async def generate(user: User, now: Optional[datetime.datetime] = None,
                   week: Optional[str] = None) -> Dict[str, Any]:
    """Make ``user``'s review (superseding their pending one for the same week)."""
    async with _locks.setdefault(user.id, asyncio.Lock()):
        now = now or clock.now()
        today = now.date()
        c = await asyncio.to_thread(candidates, user, now)  # money's savings read the database and the BoI
        nw = await next_week(user, now)
        raw = None
        if c["items"] and llm.configured("briefs"):
            try:
                with users.acting_as(user):  # usage is theirs
                    raw = await llm.complete_json(review_prompt(c, nw, today, user), _SCHEMA, job="briefs",
                                                  timeout=90, purpose="weekly_review")
            except Exception as e:
                logger.warning(f"Weekly review: the model failed, using defaults ({e})")
        items = merge_proposals(c, raw, today)
        headline = re.sub(r"\s+", " ", str((raw or {}).get("headline") or "")).strip().rstrip(".")[:80]
        if not headline:
            headline = f"{c['stats']['done']} done this week, {len(items)} to decide" if items else "All tidy this week"
        summary = llm.clean_points((raw or {}).get("summary"), 3) if raw else []
        if not summary:
            summary = [f"{c['stats']['done']} tasks done, {c['stats']['created']} new"]
            if nw and nw.get("busiest") and nw["busiest"]["count"] >= 3:
                summary.append(f"Busiest day ahead: {nw['busiest']['weekday']} ({nw['busiest']['count']} events)")
            if c.get("money"):
                summary.append(money_queries.week_lines(c["money"])[0])
        rid = str(uuid.uuid4())
        data = {"week": week or week_id(today), "stats": c["stats"], "next_week": nw, "summary": summary,
                "groups": c["groups"], "items": items, "model": bool(raw), "money": c.get("money")}
        with get_db_connection() as conn:
            # A newer review replaces the user's unapplied one for the same week.
            conn.execute("UPDATE weekly_reviews SET status = 'superseded', resolved_at = ? "
                         "WHERE user_id = ? AND week = ? AND status = 'pending'", (_now_iso(), user.id, data["week"]))
            conn.execute("INSERT INTO weekly_reviews (id, user_id, week, status, headline, data, created_at) "
                         "VALUES (?, ?, ?, 'pending', ?, ?, ?)",
                         (rid, user.id, data["week"], headline, json.dumps(data, ensure_ascii=False), _now_iso()))
            conn.commit()
        logger.info(f"Weekly review {data['week']} of user {user.id}: {len(items)} items "
                    f"({'model' if raw else 'defaults'})")
        return get_review(user, rid) or {}


# ---------------------------------------------------------------------------
# Reading / delivering / applying
# ---------------------------------------------------------------------------

def _to_api(user: User, r) -> Dict[str, Any]:
    d = _loads(r["data"], {})
    money = d.get("money") if d.get("money") and store.is_connected(user, money_config.ADDON) else None
    return {"id": r["id"], "week": r["week"], "status": r["status"], "headline": r["headline"],
            "summary": d.get("summary") or [], "stats": d.get("stats") or {}, "next_week": d.get("next_week"),
            "groups": d.get("groups") or [], "items": d.get("items") or [], "money": money,
            "result": _loads(r["result"], None) if r["result"] else None,
            "created_at": r["created_at"], "resolved_at": r["resolved_at"]}


def get_review(user: User, rid: str) -> Optional[Dict[str, Any]]:
    """``user``'s review ``rid`` (None: no such review, or someone else's)."""
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM weekly_reviews WHERE id = ? AND user_id = ?", (rid, user.id)).fetchone()
    return _to_api(user, r) if r else None


def latest(user: User) -> Optional[Dict[str, Any]]:
    """``user``'s newest review that was not superseded (None without ``tasks``)."""
    if not available(user):
        return None
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM weekly_reviews WHERE user_id = ? AND status != 'superseded' "
                         "ORDER BY created_at DESC LIMIT 1", (user.id,)).fetchone()
    return _to_api(user, r) if r else None


def dismiss(user: User, rid: str) -> bool:
    with get_db_connection() as conn:
        cur = conn.execute("UPDATE weekly_reviews SET status = 'dismissed', resolved_at = ? "
                           "WHERE id = ? AND user_id = ? AND status = 'pending'", (_now_iso(), rid, user.id))
        conn.commit()
        return cur.rowcount > 0


def restore_pending(user: User, rid: str) -> bool:
    """Undo of :func:`dismiss`: ``user``'s dismissed review back to pending (False when it changed since)."""
    with get_db_connection() as conn:
        cur = conn.execute("UPDATE weekly_reviews SET status = 'pending', resolved_at = NULL "
                           "WHERE id = ? AND user_id = ? AND status = 'dismissed'", (rid, user.id))
        conn.commit()
        return cur.rowcount > 0


async def deliver(r: Dict[str, Any], user: User) -> bool:
    """Push the review to ``user``'s phone and, unless ``REVIEW_TELEGRAM=0``, to them on Telegram."""
    n = sum(1 for i in r["items"] if i.get("enabled"))
    ok = await phone_push.send(user, "weekly_review", {"id": r["id"], "headline": r["headline"],
                                                       "items": len(r["items"]), "proposed": n,
                                                       "summary": r["summary"]})
    if REVIEW_TELEGRAM:
        lines = [f"🗓️ Weekly review — {r['headline']}", ""] + [f"• {s}" for s in r["summary"]]
        if r["items"]:
            lines += ["", f"{len(r['items'])} items to decide ({n} with a proposed change). Review in the app or the web."]
        await notify(user, "\n".join(lines))
    return ok


async def apply(user: User, rid: str, decisions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Apply the enabled items of ``user``'s review. ``decisions`` (from the
    client) override the stored proposals per ref: {ref, action?, due_date?,
    group?, enabled?}. Without it, the stored proposals are applied as they are."""
    r = get_review(user, rid)
    if not r:
        raise LookupError("Review not found")
    if r["status"] != "pending":
        raise ValueError(f"This review is already {r['status']}")
    today = clock.now().date()
    over = {str(d.get("ref")): d for d in (decisions or []) if isinstance(d, dict) and d.get("ref")}
    results = []
    for item in r["items"]:
        d = {**item, **{k: v for k, v in over.get(item["ref"], {}).items() if k in ("action", "due_date", "group", "enabled")}}
        if not d.get("enabled") or d.get("action") == "keep":
            continue
        action = str(d.get("action") or "")
        if action not in actions_for(item["type"]):
            results.append({"ref": item["ref"], "ok": False, "error": f"'{action}' isn't allowed here"})
            continue
        try:
            msg = await _apply_one(item, action, d, today, user)
            results.append({"ref": item["ref"], "action": action, "ok": True, "result": msg})
        except Exception as e:
            results.append({"ref": item["ref"], "action": action, "ok": False, "error": str(e)})
    summary = {"applied": sum(1 for x in results if x["ok"]), "failed": sum(1 for x in results if not x["ok"]),
               "items": results}
    with get_db_connection() as conn:
        conn.execute("UPDATE weekly_reviews SET status = 'applied', result = ?, resolved_at = ? WHERE id = ? AND user_id = ?",
                     (json.dumps(summary, ensure_ascii=False), _now_iso(), rid, user.id))
        conn.commit()
    logger.info(f"Weekly review {r['week']} applied: {summary['applied']} ok, {summary['failed']} failed")
    return summary


async def _apply_one(item, action, d, today, user: User) -> str:
    ref = item["ref"]
    if item["type"] not in ACTIONS:
        hook = registry.hook_for("review_action", item["type"], user)  # NotConnected: no longer theirs
        if hook is None:
            raise LookupError(f"no addon acts on '{item['type']}' items")
        return str(await hook.func(user, item, action, d, today))
    if item["type"] == "suggestion":
        if action == "accept":
            res = await suggestion_actions.accept_suggestion(user, ref)
            return str(res.get("result_ref") or "accepted")
        if not suggestion_actions.dismiss_suggestion(user, ref):
            raise LookupError("suggestion is no longer pending")
        return "dismissed"
    # person → reach_out
    due = valid_future_date(d.get("due_date"), today) or next_weekday(today, 6).isoformat()
    tid = await suggestion_actions.create_task_record(
        user, {"title": f"📞 Reach out to {item['title']}", "due_date": due, "group": "Personal", "priority": "low"},
        link=("person", ref))
    return tid


# ---------------------------------------------------------------------------
# Chat context + schedule
# ---------------------------------------------------------------------------

def chat_context(user: User, rid: str) -> Dict[str, Any]:
    r = get_review(user, rid)
    if not r:
        raise LookupError("Review not found")
    return {"kind": "review", "ref_id": rid, "call_id": None, "emoji": "🗓️", "title": f"Weekly review · {r['week']}",
            "subtitle": f"{r['headline']} · {len(r['items'])} items",
            "quick_prompts": ["Go through it with me", "Reschedule everything overdue to next week",
                              "Which of these can I drop?", "Apply it as proposed"]}


def context_prompt(user: User, rid: str) -> Optional[str]:
    r = get_review(user, rid)
    if not r:
        return None
    who = user_profile.for_user(user)
    lines = [f"- [{i['type']} {i['ref']}] {i['title']} — {i['why']} ({'; '.join(i.get('facts') or [])}) → proposed "
             f"{i.get('action')}{' ' + str(i.get('due_date')) if i.get('due_date') and i.get('action') in ('reschedule', 'reach_out') else ''}"
             f"{' ' + str(i.get('group')) if i.get('action') == 'move_group' else ''}" for i in r["items"]]
    money = f"Money this week: {'; '.join(money_queries.week_lines(r['money']))}.\n" if r.get("money") else ""
    return (f"## Opened from the weekly review {r['week']} (status {r['status']})\n"
            f"{r['headline']}. {' '.join(r['summary'])}\n{money}"
            "Items and the proposed actions:\n" + ("\n".join(lines) or "- none") +
            f"\nHelp {who.first_name} decide. Make changes with the normal tools (update_task, complete_task, delete_task, "
            "accept_call_suggestion, dismiss_call_suggestion, add_task + link_task_to_person). Keep replies short.")


def review_day(now: datetime.datetime) -> datetime.date:
    """The scheduled review day this moment belongs to (yesterday during the next-morning catch-up)."""
    wd = _parse_at(REVIEW_AT)[0]
    return now.date() if now.weekday() == wd else now.date() - datetime.timedelta(days=1)


async def _scheduled_for(user: User, now: datetime.datetime, week: str) -> bool:
    with get_db_connection() as conn:
        has = conn.execute("SELECT 1 FROM weekly_reviews WHERE user_id = ? AND week = ? AND status != 'superseded'",
                           (user.id, week)).fetchone()
    if has:
        return False
    await deliver(await generate(user, now, week=week), user)
    return True


async def schedule_once(now: Optional[datetime.datetime] = None) -> Dict[str, bool]:
    """When the review is due: make and deliver this week's review of every
    user with ``tasks`` who has none yet (each as that user, a failing one
    not stopping the next). user id → whether one was made."""
    now = now or clock.now()
    if not is_due(now):
        return {}
    week = week_id(review_day(now))
    return await composers.for_each("Weekly review", available, lambda user: _scheduled_for(user, now, week))


async def schedule_loop(interval_seconds: int = 60) -> None:
    """Make and deliver each user's review of the week when it is due."""
    await asyncio.sleep(20)
    while True:
        await schedule_once()
        await asyncio.sleep(interval_seconds)
