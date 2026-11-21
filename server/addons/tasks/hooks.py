"""What the tasks addon gives the rest of Dana (docs/tasks-and-triggers.md,
docs/now.md; the hooks: docs/writing-an-addon.md):

- jobs: every ``TIME_CHECK_INTERVAL`` seconds (30) each user's due time
  triggers fire; at start every user gets the default lists they lack;
- events: ``homeassistant.location.changed`` / ``homeassistant.car.changed``
  fire location / car triggers, ``core.user.woke`` the wake ones;
  ``knowledge.project.deleted`` deletes the owner's tasks that carried the
  project and clears it from everyone else's (``tasks.project_id`` has no
  foreign key: the projects table is the knowledge addon's);
- the Now screen: a card per reminder that fired and nobody handled
  (``task:<id>``: done / snooze / schedule / dismiss), reminders in the day
  strip (at their hour, "At home", "On waking");
- the morning briefing (due, overdue, high priority, today's reminders) and
  the weekly review (overdue / snoozed often / stale tasks, the week's
  numbers; complete / reschedule / delete / move_group);
- the global search ("reminder" results), the "Reminder checks" self-check
  (the time-trigger job is alive; a check of the server), the context of a
  chat opened from a task.

The core composers are imported as modules and read when a hook runs (they
import addons themselves).
"""

import datetime
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from core import cards, notify, now as now_screen, undo
from core.addons import BriefingContext, Card, Check, ContextRequest, DayItem, Hit, NowContext, Section
from core.addons import helpers, scheduler
from core.db import get_db_connection
from core.users import User
from addons.tasks import groups, links, service, triggers
from addons.tasks.addon import addon

logger = logging.getLogger(__name__)

TIME_CHECK_SECONDS = int(os.getenv("TIME_CHECK_INTERVAL", "30"))
MAX_REMINDERS = int(os.getenv("NOW_MAX_REMINDERS", "4"))
# A reminder that fired longer ago than this is left to the Reminders screen.
REMINDER_MAX_HOURS = float(os.getenv("NOW_REMINDER_MAX_HOURS", "48"))
FIRE_TOLERANCE_SECONDS = 300
# The time-trigger job is "stopped" after this long without a pass.
TIME_CHECKER_MINUTES = 5
STALE_DAYS = int(os.getenv("REVIEW_STALE_DAYS", "21"))
SNOOZE_MIN = int(os.getenv("REVIEW_SNOOZE_MIN", "3"))

_STARTED = time.monotonic()


# --- Background work ----------------------------------------------------------------

@addon.job(every=datetime.timedelta(seconds=TIME_CHECK_SECONDS))
async def check_time_triggers(user: User) -> int:
    return await triggers.check_time_triggers(user)


@addon.job(at_start=True)
async def ensure_default_groups(user: User) -> None:
    groups.ensure_default_groups(user)


@addon.on("homeassistant.location.changed")
async def on_location(user: User, data: Dict[str, Any]) -> None:
    await triggers.fire(user, "location", action=str(data.get("action") or ""), target=str(data.get("target") or ""))


@addon.on("homeassistant.car.changed")
async def on_car(user: User, data: Dict[str, Any]) -> None:
    await triggers.fire(user, "car", action=str(data.get("action") or ""))


@addon.on("core.user.woke")
async def on_wake(user: User, data: Dict[str, Any]) -> None:
    await triggers.fire(user, "wake")


@addon.on("knowledge.project.deleted")
async def on_project_deleted(user: User, data: Dict[str, Any]) -> None:
    """The project's owner deleted it: their tasks that carry it go, others' lose the link."""
    service.forget_project(user, str(data.get("project_id") or ""))


# --- Now: reminders that fired --------------------------------------------------------

_FIRED_PHRASE = {"time": "due", "location_arrive": "arrived", "location_leave": "left",
                 "car_enter": "in the car", "car_leave": "out of the car", "wake": "on waking"}


def due_reminders(user: User, now: datetime.datetime, hidden: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``user``'s open tasks whose reminder fired (or whose time trigger
    passed) and that nobody handled since: not completed, snoozed, edited or
    dismissed in Now."""
    fires = triggers.last_fired(user)
    where, args = service.visible_sql(user, "")
    with get_db_connection() as conn:
        tasks = [dict(r) for r in conn.execute(f"SELECT * FROM tasks WHERE status = 'needs_action' AND {where}",
                                               args).fetchall()]
    out = []
    for t in tasks:
        fired = None
        if t["id"] in fires:
            at, ttype = fires[t["id"]]
            updated = helpers.parse_time(t.get("updated_at"))
            fresh = (now - at).total_seconds() <= REMINDER_MAX_HOURS * 3600
            if fresh and (not updated or updated <= at + datetime.timedelta(seconds=FIRE_TOLERANCE_SECONDS)):
                fired = (at, ttype)
        trig = cards.loads(t.get("triggers"), {}) or {}
        if not fired and isinstance(trig, dict) and trig.get("time"):
            due = triggers.parse_trigger_time(str(trig["time"]))
            if due and due <= now and (now - due).total_seconds() <= REMINDER_MAX_HOURS * 3600:
                fired = (due, "time")
        if not fired:
            continue
        h = hidden.get(f"task:{t['id']}")
        if h and (helpers.parse_time(h["created_at"]) or cards.FAR) >= fired[0]:
            continue  # dismissed / snoozed in Now after this firing
        out.append({**t, "_fired_at": fired[0], "_fired": fired[1], "_triggers": trig if isinstance(trig, dict) else {}})
    out.sort(key=lambda t: t["_fired_at"], reverse=True)
    return out


def _task_facts(t: Dict[str, Any], task_links: List[Dict[str, Any]], now: datetime.datetime) -> List[str]:
    facts = []
    created = helpers.parse_time(t.get("created_at"))
    origin = ""
    for l in task_links:
        if l["ref_type"] == "call" and l.get("contact"):
            origin = f", from a call with {l['contact']}"
            break
        if l["ref_type"] == "notification" and l.get("app_name"):
            origin = f", from a {l['app_name']} notification"
            break
    if created:
        facts.append(f"Created {created.day} {created.strftime('%b')}{origin}")
    for l in task_links:
        if l["ref_type"] == "person" and l.get("title"):
            facts.append(f"Linked to {l['title']}")
    n = int(t.get("snooze_count") or 0)
    if n:
        facts.append(f"Moved {n} time{'s' if n != 1 else ''}")
    if t.get("due_date"):
        facts.append(f"Due {helpers.fmt_date(t['due_date'])}")
    fired = t.get("_fired_at")
    if fired:
        facts.append(f"Reminded {helpers.ago(fired, now)}")
    return facts


@addon.now_cards
def reminder_cards(user: User, ctx: NowContext) -> List[Card]:
    now = ctx.now
    due = due_reminders(user, now, ctx.hidden)[:MAX_REMINDERS]
    if not due:
        return []
    task_links = links.links_for_tasks(user, [t["id"] for t in due])
    out = []
    for t in due:
        n = int(t.get("snooze_count") or 0)
        if n >= 2:
            why = f"You moved this {n} times."
        elif (t.get("priority") or "") == "high":
            why = "Marked important."
        elif t.get("due_date"):
            why = f"Due {helpers.fmt_date(t['due_date'])}."
        else:
            why = "Its reminder went off."
        blocks: List[Dict[str, Any]] = [{"type": "facts", "label": "History",
                                         "items": _task_facts(t, task_links.get(t["id"], []), now)}]
        desc = (t.get("description") or "").strip()
        if desc:
            blocks.append({"type": "text", "label": "Note", "text": cards.clip(desc, 400)})
        fired_phrase = _FIRED_PHRASE.get(t["_fired"], "due")
        out.append(Card(
            id=f"task:{t['id']}", kind="reminder",
            source=f"Reminder · {fired_phrase} " + ("now" if (now - t["_fired_at"]).total_seconds() < 300
                                                    else helpers.ago(t["_fired_at"], now)),
            title=cards.clip(t["title"], 160), why=why,
            primary=cards.action("done", "Done"),
            secondary=now_screen.snooze_options(user, now) + [cards.action("dismiss", "Dismiss")],
            schedulable=True, ref={"type": "task", "id": t["id"]}, blocks=blocks))
    return out


@addon.day_items
def reminder_day_items(user: User, ctx: NowContext) -> List[DayItem]:
    """Open reminders at an hour today, on arriving home, on waking."""
    where, args = service.visible_sql(user, "")
    with get_db_connection() as conn:
        tasks = [dict(r) for r in conn.execute(
            "SELECT id, title, triggers FROM tasks WHERE status = 'needs_action' AND triggers IS NOT NULL "
            f"AND triggers NOT IN ('', '{{}}') AND {where}", args).fetchall()]
    out = []
    for t in tasks:
        trig = cards.loads(t["triggers"], {})
        if not isinstance(trig, dict):
            continue
        fields = {"task_id": t["id"]}
        if trig.get("time"):
            dt = cards.parse_local(trig["time"])
            if dt:
                out.append(DayItem(t["title"], "reminder", at=dt, fields=fields))
        if now_screen.is_home(trig.get("location")):
            out.append(DayItem(t["title"], "reminder", slot="home", fields=fields))
        if trig.get("wake"):
            out.append(DayItem(t["title"], "reminder", slot="wake", fields=fields))
    return out


# --- Now: acting on a reminder ----------------------------------------------------------

def _writable(user: User, task_id: str) -> Dict[str, Any]:
    """A reminder ``user`` may change (404 when they cannot see it, 403 when
    it is shared with them read-only)."""
    try:
        return service.writable_task(user, task_id)
    except LookupError:
        raise cards.ActError("Reminder not found", 404)
    except service.ReadOnly as e:
        raise cards.ActError(str(e), 403)


async def clear_on_phone(user: User, task_id: str) -> None:
    """Take the reminder off ``user``'s devices (the push channel's ``reminder_clear``)."""
    await notify.send(user, notify.Message(kind="reminder_clear", data={"task_id": str(task_id)}))


def _restore(user: User, task_id: str, before: Dict[str, Any], expect: Dict[str, Any]) -> None:
    cols = ("status", "triggers", "snooze_count", "updated_at")
    service.check_still_writable(user, task_id)
    with get_db_connection() as conn:
        # scoped: service.check_still_writable(user, task_id) above
        cur = conn.execute("SELECT status, triggers FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not cur:
            raise RuntimeError("The reminder was deleted in the meantime.")
        if any(expect.get(k) is not None and cur[k] != expect[k] for k in ("status", "triggers")):
            raise RuntimeError("The reminder was changed again since, so it was left as it is.")
        # scoped: service.check_still_writable(user, task_id) above
        conn.execute(f"UPDATE tasks SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                     (*[before.get(c) for c in cols], task_id))
        conn.commit()


@addon.now_action("task")
async def act_on_reminder(user: User, task_id: str, action: str, args: Dict[str, Any],
                          now: datetime.datetime) -> Dict[str, Any]:
    t = _writable(user, task_id)
    if action == "done":
        if t["status"] == "completed":
            raise cards.ActError("Already done.", 409)
        service.complete_task(user, task_id)
        await clear_on_phone(user, task_id)
        return cards.result("Completed", undo.record(
            f"Completed “{cards.clip(t['title'], 60)}”",
            lambda: _restore(user, task_id, t, {"status": "completed"}), source="now.done"))
    if action in ("snooze", "schedule"):
        if action == "schedule" or args.get("slot"):
            slot = str(args.get("slot") or "")
            if not slot:
                raise cards.ActError("schedule needs args.slot")
            trig = await now_screen.slot_trigger(user, slot, now)
        elif isinstance(args.get("trigger"), dict) and args["trigger"]:
            trig = await now_screen.fix_home(user, dict(args["trigger"]))
        else:
            until = cards.until_from(args, now)
            if not until:
                raise cards.ActError("snooze needs minutes, until, slot or trigger")
            trig = {"time": until.strftime("%Y-%m-%d %H:%M")}
        service.snooze_task(user, task_id, json.dumps(trig, ensure_ascii=False))
        await clear_on_phone(user, task_id)
        phrase = now_screen.trigger_phrase(trig, now)
        msg = (f"Moved to {phrase}" if action == "schedule" else f"Snoozed · {phrase}")
        new_trig = json.dumps(trig, ensure_ascii=False)
        return cards.result(msg, undo.record(msg, lambda: _restore(user, task_id, t, {"triggers": new_trig}),
                                             source=f"now.{action}"))
    if action == "dismiss":
        msg = "Dismissed · still on your reminders"
        return cards.result(msg, cards.hide_with_undo(user, f"task:{task_id}", cards.FAR, msg, reason="dismiss"))
    raise cards.ActError(f"'{action}' isn't an action for a reminder")


# --- Briefing / weekly review ----------------------------------------------------------

def _hhmm(iso: Optional[str]) -> str:
    dt = cards.parse_local(iso)
    return dt.strftime("%H:%M") if dt else ""


def _briefing(user: User, today: str) -> Section:
    where, args = service.visible_sql(user, "")
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, priority, due_date, triggers FROM tasks WHERE status = 'needs_action' "
            f"AND due_date IS NOT NULL AND due_date != '' AND substr(due_date, 1, 10) <= ? AND {where} "
            "ORDER BY due_date, CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END LIMIT 12",
            (today, *args)).fetchall()
        high = conn.execute(
            "SELECT id, title FROM tasks WHERE status = 'needs_action' AND priority = 'high' "
            f"AND (due_date IS NULL OR due_date = '') AND {where} ORDER BY updated_at DESC LIMIT 3", args).fetchall()
        timed_rows = conn.execute("SELECT id, title, triggers FROM tasks WHERE status = 'needs_action' "
                                  f"AND triggers LIKE '%\"time\"%' AND {where}", args).fetchall()
    due = [{"id": r["id"], "title": r["title"], "priority": r["priority"], "due_date": r["due_date"][:10],
            "overdue": r["due_date"][:10] < today} for r in rows]
    timed = []
    for r in timed_rows:
        try:
            t = json.loads(r["triggers"] or "{}").get("time")
        except Exception:
            t = None
        if t and str(t)[:10] == today:
            timed.append({"id": r["id"], "title": r["title"], "time": _hhmm(t) or str(t)[11:16]})
    timed.sort(key=lambda x: x["time"])
    data = {"due": due, "high_priority": [{"id": r["id"], "title": r["title"]} for r in high],
            "reminders_today": timed[:6]}
    overdue = [x for x in due if x["overdue"]]
    today_due = [x for x in due if not x["overdue"]]
    points = []
    if today_due:
        points.append("Due today: " + ", ".join(x["title"] for x in today_due[:3]))
    if overdue:
        points.append(f"{len(overdue)} overdue: " + ", ".join(x["title"] for x in overdue[:2]))
    return Section(data, points=points, headline=f", {len(today_due)} due" if today_due else "",
                   counts={"due": len(today_due)})


def _future_time_trigger(trig: Dict[str, Any], now: datetime.datetime) -> bool:
    t = trig.get("time")
    if not t:
        return False
    dt = triggers.parse_trigger_time(str(t))
    return bool(dt and dt > now)


def _review(user: User, now: datetime.datetime) -> Section:
    """What needs a decision among ``user``'s tasks, and the week's numbers."""
    today = now.date().isoformat()
    week_ago = (now - datetime.timedelta(days=7)).astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    where, args = service.visible_sql(user, "")
    names = {g["id"]: g["name"] for g in groups.list_groups(user)}
    with get_db_connection() as conn:
        tasks = [dict(r) for r in conn.execute(f"SELECT * FROM tasks WHERE status = 'needs_action' AND {where}", args)]
        done = [dict(r) for r in conn.execute(
            f"SELECT title, updated_at FROM tasks WHERE status = 'completed' AND updated_at >= ? AND {where} "
            "ORDER BY updated_at DESC", (week_ago, *args))]
        created = conn.execute(f"SELECT COUNT(*) FROM tasks WHERE created_at >= ? AND {where}",
                               (week_ago, *args)).fetchone()[0]
    items: List[Dict[str, Any]] = []
    seen = set()

    def item(t: Dict[str, Any], why: str, facts: List[str]) -> None:
        if t["id"] in seen:
            return
        seen.add(t["id"])
        items.append({"ref": t["id"], "type": "task", "title": t["title"], "why": why,
                      "facts": [f for f in facts if f], "group": names.get(t["group_id"] or "", ""),
                      "due_date": (t["due_date"] or "")[:10] or None, "priority": t["priority"]})

    for t in sorted(tasks, key=lambda t: t["due_date"] or "9999"):
        due = (t["due_date"] or "")[:10]
        if due and due < today:
            days = (now.date() - datetime.date.fromisoformat(due)).days if re.fullmatch(r"\d{4}-\d{2}-\d{2}", due) \
                else None
            item(t, "overdue", [f"due {due}" + (f" ({days} days ago)" if days else ""),
                                f"snoozed {t['snooze_count']}×" if t.get("snooze_count") else ""])
    for t in tasks:
        if (t.get("snooze_count") or 0) >= SNOOZE_MIN:
            item(t, "snoozed_often", [f"snoozed {t['snooze_count']}×"])
    for t in sorted(tasks, key=lambda t: t["updated_at"] or ""):
        age = helpers.age_days(t["updated_at"], now)
        trig = cards.loads(t["triggers"], {})
        trig = trig if isinstance(trig, dict) else {}
        if age is not None and age >= STALE_DAYS and not _future_time_trigger(trig, now):
            waiting = [k for k in ("location", "car", "wake") if trig.get(k)]
            item(t, "stale", [f"untouched {age} days", f"waits for a {'/'.join(waiting)} trigger" if waiting else ""])
    stats = {"done": len(done), "done_titles": [d["title"] for d in done[:8]], "created": created, "open": len(tasks)}
    return Section({"stats": stats, "groups": sorted(set(names.values()))}, items=items)


@addon.briefing("tasks", counts=("due",))
def briefing_section(user: User, ctx: BriefingContext) -> Section:
    """Due or overdue tasks, high priority ones, today's reminders."""
    return _briefing(user, ctx.today)


@addon.review("tasks")
def review_section(user: User, ctx: BriefingContext) -> Section:
    """Overdue, snoozed-often and stale tasks to decide on; the week's numbers and the groups."""
    return _review(user, ctx.now)


@addon.review_action("task", ("complete", "reschedule", "delete", "move_group", "keep"))
async def apply_review_item(user: User, item: Dict[str, Any], action: str, decision: Dict[str, Any],
                            today: datetime.date) -> str:
    ref = item["ref"]
    try:
        service.writable_task(user, ref)
    except LookupError:
        raise LookupError("task no longer exists")
    if action == "complete":
        service.complete_task(user, ref)
    elif action == "delete":
        service.delete_task(user, ref)
    elif action == "reschedule":
        due = helpers.valid_future_date(decision.get("due_date"), today) \
            or helpers.next_weekday(today, 6).isoformat()
        service.save_task(user, ref, {"due_date": due, "snooze_count": 0})
        return f"due {due}"
    elif action == "move_group":
        g = groups.find_group(user, str(decision.get("group") or ""))
        if not g:
            raise ValueError(f"no group '{decision.get('group')}'")
        service.save_task(user, ref, {"group_id": g["id"]})
        return f"moved to {g['name']}"
    if action in ("complete", "delete"):
        await clear_on_phone(user, ref)
    return action


# --- Search -------------------------------------------------------------------------------

@addon.search
def search_reminders(user: User, q: str, limit: int) -> List[Hit]:
    """``user``'s open tasks (own and on lists shared with them) whose title or note matches."""
    like = helpers.like_pattern(q)
    where, args = service.visible_sql(user, "")
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, description, triggers, due_date FROM tasks WHERE status = 'needs_action' "
            f"AND (title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\') AND {where} "
            "ORDER BY updated_at DESC LIMIT 50",
            (like, like, *args)).fetchall()
    out = []
    for r in rows:
        trig = cards.loads(r["triggers"], {})
        sub = now_screen.trigger_phrase(trig) if isinstance(trig, dict) else ""
        sub = (sub[:1].upper() + sub[1:]) if sub else (f"Due {r['due_date'][:10]}" if r["due_date"] else "No reminder set")
        title_hit = q.casefold() in (r["title"] or "").casefold()
        out.append(Hit("reminder", r["id"], r["title"], sub, score=helpers.match_score(r["title"], q) if title_hit else 0.6))
    return sorted(out, key=lambda h: -h.score)[:limit]


# --- Self-check -----------------------------------------------------------------------------

@addon.selfcheck("reminders", "Reminder checks", server=True, after="wake")
def reminder_checks(user: Optional[User], now: datetime.datetime) -> Check:
    """The time-trigger job is alive (its last pass, ``scheduler.last_run``)."""
    last_utc = scheduler.last_run(addon.name, check_time_triggers.__name__)
    last = helpers.parse_time(last_utc.isoformat()) if last_utc else None
    why = "Time reminders won't fire until the server restarts."
    if not last:
        if time.monotonic() - _STARTED < TIME_CHECKER_MINUTES * 60:
            return Check(True, "starting")
        return Check(False, "never ran", title="Reminder checks aren't running", why=why)
    quiet = (now - last).total_seconds()
    if quiet > TIME_CHECKER_MINUTES * 60:
        return Check(False, f"stopped {helpers.ago(last, now)}", last, title="Reminder checks stopped", why=why)
    return Check(True, f"last {helpers.ago(last, now)}", last)


# --- Context chats --------------------------------------------------------------------------

def _call_link(user: User, task_id: str) -> Optional[Dict[str, Any]]:
    return next((l for l in links.links_for_tasks(user, [task_id]).get(task_id, []) if l["ref_type"] == "call"), None)


@addon.context_card("task")
def task_card(user: User, ref_id: str) -> Dict[str, Any]:
    row = service.get_task(user, ref_id)
    if not row:
        raise LookupError("Task not found")
    call_link = _call_link(user, row["id"])
    return {
        "kind": "task", "ref_id": row["id"], "call_id": call_link["ref_id"] if call_link else None,
        "emoji": "✅", "title": row["title"],
        "subtitle": (f"From call with {call_link['contact']}" if call_link else "Task")
                    + (f" · due {row['due_date']}" if row["due_date"] else ""),
        "quick_prompts": ["Change the reminder", "Break it into steps", "Mark it done"],
    }


@addon.context("task")
def task_context(user: User, ref_id: str, req: ContextRequest) -> Optional[str]:
    t = service.get_task(user, ref_id)
    if not t:
        return None
    block = (f"## Opened from a task\nTask {t['id']}: “{t['title']}” status={t['status']} priority={t['priority']} "
             f"due={t['due_date']} triggers={t['triggers']}\nDescription: {t['description'] or ''}\n"
             "Use update_task with this id for changes.")
    link = _call_link(user, ref_id)
    if link:
        call_block = req.block("call", link["ref_id"])
        if call_block:
            block += f"\n\n### Source call\n{call_block}"
    return block
