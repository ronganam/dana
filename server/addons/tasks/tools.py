"""The tasks tools (docs/tasks-and-triggers.md). Every tool works on what
the connection's user can see: their own lists and tasks plus the lists shared
with them (``service`` / ``groups``). A task is named by its id or its title
(``service.find_task``)."""

import asyncio
import datetime
import json
import logging
import re
import sqlite3
from typing import Any, Dict, List, Literal, Optional, Tuple

from core import clock, events, undoable, users, zones
from core.addons import Connection, sharing
from core.db import get_db_connection
from core.users import User
from addons.tasks import approvals, groups, service
from addons.tasks import titles as task_titles
from addons.tasks.addon import addon

logger = logging.getLogger(__name__)

PRIORITIES = ("low", "medium", "high")
DESCRIPTION_SHOWN = 200  # characters of a description list_tasks shows


def _extract_uuid(text: str) -> Optional[str]:
    match = service.TASK_ID.search(text)
    return match.group(0) if match else None


def _parse_relative_time(text: str) -> Optional[str]:
    """Try to parse a natural language relative time into an absolute YYYY-MM-DD HH:MM string.

    Supports: 'now', 'in X hours/minutes/seconds', 'X minutes from now',
    'today/tomorrow at HH:MM', 'HH:MM' (today).
    Returns None if the text cannot be parsed as a relative expression.
    """
    t = text.strip().lower()
    now = clock.now()

    if t == "now":
        return now.strftime("%Y-%m-%d %H:%M")

    m = re.match(r'(?:in\s+)?(\d+)\s*(hour|minute|second|hr|min|sec)s?(?:\s+from\s+now)?$', t)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        if unit in ("hour", "hr"):
            dt = now + datetime.timedelta(hours=amount)
        elif unit in ("minute", "min"):
            dt = now + datetime.timedelta(minutes=amount)
        else:
            dt = now + datetime.timedelta(seconds=amount)
        return dt.strftime("%Y-%m-%d %H:%M")

    m = re.match(r'tomorrow(?:\s+at\s+|\s+)(\d{1,2}):(\d{2})(?::(\d{2}))?$', t)
    if m:
        tomorrow = now + datetime.timedelta(days=1)
        dt = tomorrow.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=int(m.group(3) or 0), microsecond=0)
        return dt.strftime("%Y-%m-%d %H:%M")

    m = re.match(r'today(?:\s+at\s+|\s+)(\d{1,2}):(\d{2})(?::(\d{2}))?$', t)
    if m:
        dt = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=int(m.group(3) or 0), microsecond=0)
        return dt.strftime("%Y-%m-%d %H:%M")

    m = re.match(r'^(\d{1,2}):(\d{2})(?::(\d{2}))?$', t)
    if m:
        dt = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=int(m.group(3) or 0), microsecond=0)
        if dt <= now:
            dt += datetime.timedelta(days=1)
        return dt.strftime("%Y-%m-%d %H:%M")

    return None


def _time_trigger(text: str) -> str:
    """A time trigger as stored ('YYYY-MM-DD HH:MM'): relative words resolved,
    a time already past moved to the next day."""
    final = (_parse_relative_time(text) or text).strip()
    now = clock.now()
    try:
        when = datetime.datetime.strptime(final, "%Y-%m-%d %H:%M").replace(tzinfo=now.tzinfo)
    except ValueError:
        return final
    if when <= now:
        final = (when + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        logger.info(f"Time trigger '{text}' was in the past, advanced to tomorrow: {final}")
    return final


def _due(text: str) -> Optional[str]:
    """A due date as stored: relative words resolved; '' → None."""
    return (_parse_relative_time(text) or text).strip() or None


def _trigger_words(triggers_json: Optional[str]) -> List[str]:
    """A task's triggers in words: 'remind 2026-11-24 09:00', 'arriving at home', 'car enter', 'on waking'."""
    try:
        trig = json.loads(triggers_json or "{}")
    except ValueError:
        return []
    out = [f"remind {trig['time']}"] if trig.get("time") else []
    loc = trig.get("location") or {}
    if loc.get("location"):
        out.append(f"{'leaving' if loc.get('event') == 'leave' else 'arriving at'} {loc['location']}")
    if trig.get("car"):
        out.append(f"car {trig['car']}")
    if trig.get("wake"):
        out.append("on waking")
    return out


def _bad_choice(location_event: Optional[str], car_trigger: Optional[str], priority: Optional[str]) -> str:
    """The error for an argument outside its choices ("" when all are fine)."""
    if location_event not in (None, "", "arrive", "leave"):
        return "Error: location_event must be 'arrive' or 'leave'."
    if car_trigger not in (None, "", "enter", "leave"):
        return "Error: car_trigger must be 'enter' or 'leave'."
    if priority not in (None, "") and priority.strip().lower() not in PRIORITIES:
        return "Error: priority must be 'low', 'medium' or 'high'."
    return ""


def _no_group(user: User, name: str) -> str:
    return f"Error: no task group called '{name}'. Groups: {', '.join(groups.group_names(user))}."


def _task_or_error(user: User, item: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """The task ``item`` names, or the error to return (nothing / several match)."""
    if not (item or "").strip():
        return None, "Error: name the task (its ID from list_tasks, or its title)."
    task, candidates = service.find_task(user, item)
    if task:
        return task, ""
    if candidates:
        listed = "; ".join(f"{c['title']} (ID: {c['id']})" for c in candidates[:8])
        return None, f"Error: '{item}' matches several open tasks: {listed}. Call again with the ID."
    return None, f"Error: no task matches '{item}'. Use list_tasks to find it."


@addon.tool(is_write=True, describe=approvals.add_task)
async def add_task(
    conn: Connection,
    title: str,
    due_date: Optional[str] = None,
    priority: Literal["low", "medium", "high"] = "medium",
    group: Optional[str] = None,
    location: Optional[str] = None,
    location_event: Literal["arrive", "leave"] = "arrive",
    time_trigger: Optional[str] = None,
    car_trigger: Optional[Literal["enter", "leave"]] = None,
    wake_trigger: Optional[bool] = None,
    description: Optional[str] = None,
    call_id: Optional[str] = None
) -> str:
    """Add a task to the user's task list; a time, place, car or wake-up trigger makes it a reminder.

    Args:
        title: What to do, e.g. 'Buy a gift for Maya'.
        due_date: YYYY-MM-DD (compute it with get_current_time).
        priority: 'low', 'medium' or 'high'.
        group: The task group by name, e.g. 'Work'; picked automatically when left out.
        location: One of the user's zones, e.g. 'home' (get_zones maps 'work' or 'my parents' to one).
        location_event: 'arrive' at or 'leave' the location.
        time_trigger: When to remind, 'YYYY-MM-DD HH:MM' (compute it with get_current_time); a time already past moves to the next day.
        car_trigger: 'enter' or 'leave' the car.
        wake_trigger: True to remind when the user wakes up.
        description: Details.
        call_id: The phone call it came from (from list_recent_calls / search_calls); the task is linked to it.
    """
    user = conn.user
    if not (title or "").strip():
        return "Error: the task needs a title."
    error = _bad_choice(location_event, car_trigger, priority)
    if error:
        return error
    triggers: Dict[str, Any] = {}
    try:
        if location:
            triggers["location"] = {"location": service.resolve_zone(user, location).strip().lower(),
                                    "event": location_event or "arrive"}
        if time_trigger:
            triggers["time"] = _time_trigger(time_trigger)
        if car_trigger:
            triggers["car"] = car_trigger
        if wake_trigger:
            triggers["wake"] = True
        # The emoji and the group are one small model call each: ask both at once.
        title_sanitized, group_id = await asyncio.gather(task_titles.sanitize_task_title(title),
                                                         task_titles.resolve_task_group(user, group, title))
        call_note = ""
        with get_db_connection() as db:
            task_links = []
            if call_id and call_id.strip():
                call_row = db.execute("SELECT id FROM phone_calls WHERE id = ? AND user_id = ?",
                                      (call_id.strip(), user.id)).fetchone()
                if call_row:
                    task_links.append(("call", call_row["id"], "origin"))
                    call_note = f", linked to call {call_row['id']}"
                else:
                    call_note = f" (no call {call_id.strip()} was found, so it is not linked)"
            task_id = service.create_task(
                user, title=title_sanitized, description=(description or "").strip() or None,
                priority=(priority or "medium").strip().lower(), due_date=_due(due_date) if due_date else None,
                triggers=json.dumps(triggers), group_id=group_id, task_links=task_links, conn=db)
            db.commit()
    except (LookupError, ValueError, service.ReadOnly) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"Error adding task: {e}", exc_info=True)
        return f"Error: Failed to add the task: {e}"

    created = undoable.row("tasks", "id", task_id, service.TASK_COLUMNS)
    if created:
        undoable.delete_created_row(f"Added task “{title_sanitized}”", "tasks", "id", task_id, created,
                                    service.TASK_COLUMNS, "add_task", cascade=(("task_links", "task_id"),),
                                    guard=lambda: service.check_still_writable(user, task_id))
    g = groups.get_group(user, group_id)
    when = _trigger_words(json.dumps(triggers))
    return (f"Added task '{title_sanitized}' (ID: {task_id}) to {g['name'] if g else 'General'}"
            + (f", {', '.join(when)}" if when else "") + call_note + ".")


@addon.tool(is_write=True, describe=approvals.update_task)
async def update_task(
    conn: Connection,
    item: str,
    summary: Optional[str] = None,
    location: Optional[str] = None,
    location_event: Optional[Literal["arrive", "leave"]] = None,
    time_trigger: Optional[str] = None,
    car_trigger: Optional[str] = None,
    wake_trigger: Optional[bool] = None,
    description: Optional[str] = None,
    status: Optional[Literal["needs_action", "completed"]] = None,
    priority: Optional[str] = None,
    due_date: Optional[str] = None,
    group: Optional[str] = None
) -> str:
    """Change a task: complete or reopen it, rename it, or change its details, group or triggers. Only what you pass changes; '' removes a field.

    Args:
        item: The task's ID (from list_tasks) or its title.
        summary: The new title.
        location: A zone to remind at (see get_zones); '' removes the location trigger.
        location_event: 'arrive' or 'leave'.
        time_trigger: 'YYYY-MM-DD HH:MM' (compute it with get_current_time); '' removes it.
        car_trigger: 'enter' or 'leave'; '' removes it.
        wake_trigger: True to remind on waking, False to stop.
        description: The new details (replaces them); '' clears them.
        status: 'completed' (done) or 'needs_action' (open again).
        priority: 'low', 'medium' or 'high'.
        due_date: YYYY-MM-DD; '' removes it.
        group: Move it to this task group by name; '' moves it to General.
    """
    user = conn.user
    error = _bad_choice(location_event, car_trigger, priority)
    if status not in (None, "", "needs_action", "completed"):
        error = "Error: status must be 'completed' or 'needs_action'."
    if error:
        return error
    try:
        before, error = _task_or_error(user, item)
        if before is None:
            return error
        if service.level_of(user, before) == sharing.READ:
            return f"Error: {service.ReadOnly()}"
        task_id = before["id"]
        new: Dict[str, Any] = {}
        if group is not None:
            g = groups.find_group(user, group) if group else None
            if group and g is None:
                return _no_group(user, group)
            new["group_id"] = g["id"] if g else groups.default_group_id(user)

        try:
            old_triggers = json.loads(before["triggers"] or "{}")
        except ValueError:
            old_triggers = {}
        triggers = json.loads(json.dumps(old_triggers))
        if location == "":
            triggers.pop("location", None)
        elif location:
            event = location_event or (triggers.get("location") or {}).get("event") or "arrive"
            triggers["location"] = {"location": service.resolve_zone(user, location).strip().lower(), "event": event}
        elif location_event and "location" in triggers:
            triggers["location"]["event"] = location_event
        for key, value in (("time", time_trigger), ("car", car_trigger)):
            if value == "":
                triggers.pop(key, None)
            elif value:
                triggers[key] = _time_trigger(value) if key == "time" else value
        if wake_trigger is not None:
            triggers.pop("wake", None)
            if wake_trigger:
                triggers["wake"] = True
        if triggers != old_triggers:
            new["triggers"] = json.dumps(triggers)

        if summary:
            new["title"] = await task_titles.sanitize_task_title(summary)
        if description is not None:
            new["description"] = description.strip() or None
        if priority is not None:
            new["priority"] = priority.strip().lower() or "medium"
        if due_date is not None:
            new["due_date"] = _due(due_date)
        if status:
            new["status"] = status
        new = {k: v for k, v in new.items() if undoable.norm(v) != undoable.norm(before.get(k))}
        title = new.get("title", before["title"])
        if not new:
            return f"Nothing to change: '{title}' (ID: {task_id}) is already like that."
        service.save_task(user, task_id, new)
    except (LookupError, ValueError, service.ReadOnly) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"Error updating task: {e}", exc_info=True)
        return f"Error: Failed to update the task: {e}"

    if new.get("status") == "completed":
        events.emit("tasks.task.completed", user, task_id=task_id, title=title)
        label = f"Completed “{title}”"
    elif new.get("status") == "needs_action":
        label = f"Reopened “{title}”"
    else:
        label = f"Changed task “{title}”"
    after = undoable.row("tasks", "id", task_id, service.TASK_COLUMNS)
    if after:
        service.record_change_undo(user, label, task_id, before, after, "update_task")
    changed = ", ".join({"group_id": "group", "title": "title"}.get(k, k) for k in new)
    return f"Updated task '{title}' (ID: {task_id}): {changed}."


@addon.tool(is_write=True, confirm=True, describe=approvals.delete_task)
async def delete_task(conn: Connection, item: str) -> str:
    """Delete a task for good (the user is asked first). A finished task is completed with update_task instead.

    Args:
        item: The task's ID (from list_tasks) or its title.
    """
    try:
        task, error = _task_or_error(conn.user, item)
        if task is None:
            return error
        deleted = service.delete_task(conn.user, task["id"])
        return f"Deleted task '{deleted['title']}' (ID: {deleted['id']})."
    except (LookupError, service.ReadOnly) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"Error deleting task: {e}", exc_info=True)
        return f"Error: Failed to delete the task: {e}"


@addon.tool()
async def get_zones(conn: Connection) -> str:
    """List the user's zones: the places a location reminder can name (Home and the others they added).

    Call this before add_task or update_task with a location, to map what the user said
    ('work', 'my parents') to one of these names. If none matches, show them the list and ask;
    new zones are added in Settings → Zones.
    """
    found = zones.list_zones(conn.user)
    if not found:
        return "You have no zones yet. Add your places in Settings → Zones to use location reminders."
    return "Your zones:\n" + "\n".join(f"- {z.name}{' (Home)' if z.is_home else ''}" for z in found)


@addon.tool()
async def list_tasks(conn: Connection, completed: bool = False, group: Optional[str] = None, limit: int = 50) -> str:
    """List open tasks (or completed ones, latest first) with their IDs, groups, due dates and triggers. Not needed before update_task / delete_task when you know the title.

    Args:
        completed: True for completed tasks instead of open ones.
        group: Only this task group, by name (e.g. 'Work').
        limit: At most this many (default 50).
    """
    user = conn.user
    where, args = service.visible_sql(user)
    if group:
        g = groups.find_group(user, group)
        if g is None:
            return _no_group(user, group)
        where, args = f"{where} AND t.group_id = ?", [*args, g["id"]]
    limit = max(1, min(int(limit or 50), 200))
    state = "completed" if completed else "open"
    try:
        with get_db_connection() as db:
            rows = db.execute(
                f"""
                SELECT t.id, t.title, t.description, t.priority, t.due_date, t.triggers, t.group_id, t.user_id,
                       tg.name AS group_name
                FROM tasks t LEFT JOIN task_groups tg ON t.group_id = tg.id
                WHERE t.status = ? AND {where}
                ORDER BY {'t.updated_at' if completed else 't.created_at'} DESC LIMIT ?
                """, ("completed" if completed else "needs_action", *args, limit + 1)).fetchall()
    except Exception as e:
        logger.error(f"Error listing tasks: {e}", exc_info=True)
        return f"Error: Failed to read the tasks: {e}"
    if not rows:
        return f"You have no {state} tasks{f' in {group}' if group else ''}."

    levels = groups.shared_lists(user)
    owners: Dict[str, str] = {}
    lines = [f"Your {state} tasks:"]
    for r in rows[:limit]:
        place = r["group_name"] or "General"
        level = levels.get(r["group_id"] or "") if r["user_id"] != user.id else None
        if level:
            if r["user_id"] not in owners:
                owner = users.get(r["user_id"])
                owners[r["user_id"]] = owner.name if owner else ""
            place += f" · shared by {owners[r['user_id']]}" + (", read-only" if level == sharing.READ else "")
        bits = [f"{r['priority']} priority"] if r["priority"] in ("low", "high") else []
        bits += ([f"due {r['due_date']}"] if r["due_date"] else []) + _trigger_words(r["triggers"])
        desc = r["description"] or ""
        if len(desc) > DESCRIPTION_SHOWN:
            desc = desc[:DESCRIPTION_SHOWN] + "… (cut)"
        lines.append(f"- {r['title']} [{place}]" + (f" ({' · '.join(bits)})" if bits else "")
                     + (f" — {desc}" if desc else "") + f" (ID: {r['id']})")
    if len(rows) > limit:
        lines.append(f"(The first {limit} only; there are more: pass a group or a higher limit.)")
    return "\n".join(lines)


@addon.tool()
async def list_task_groups(conn: Connection) -> str:
    """List the task groups the user can see (their own and shared ones) with emoji and task count."""
    try:
        found = groups.list_groups(conn.user)
        if not found:
            return "No task groups defined."

        result = "Available task groups:\n"
        for g in found:
            desc = g.get("description", "")
            shared_str = ""
            if g.get("shared_by") is not None:
                shared_str = f" [shared by {g['shared_by']}{', read-only' if g['access'] == sharing.READ else ''}]"
            result += (f"- {g.get('emoji', '📁')} **{g['name']}** (Tasks: {g.get('task_count', 0)}){shared_str}"
                       f"{f' — {desc}' if desc else ''}\n")
        return result
    except Exception as e:
        logger.error(f"Error listing task groups: {e}", exc_info=True)
        return f"Error: Failed to list task groups: {e}"


@addon.tool(is_write=True)
async def add_task_group(conn: Connection, name: str, emoji: str = "📁", color: str = "#6366f1", description: str = "") -> str:
    """Add a new task group for organizing tasks.

    Args:
        name: The display name for the group (e.g. 'Fitness', 'Finance').
        emoji: An emoji icon for the group (e.g. '💪', '💰').
        color: A hex color for the group (e.g. '#ff6b6b').
        description: Optional description of what this group is for.
    """
    user = conn.user
    try:
        group_id = groups.create_group(user, name, emoji, color, description)
    except ValueError as e:
        return f"Error: {e}"
    except sqlite3.IntegrityError:
        return f"Error: you already have a task group called '{name.strip()}'."
    except Exception as e:
        logger.error(f"Error adding task group: {e}", exc_info=True)
        return f"Error: Failed to add the task group: {e}"
    cols = ("name", "emoji", "color", "description")
    created = undoable.row("task_groups", "id", group_id, cols)
    if created:
        def _undo_group():
            with get_db_connection() as c:
                # scoped: undo of the list this user just created: tasks added since keep it
                if c.execute("SELECT 1 FROM tasks WHERE group_id = ?", (group_id,)).fetchone():
                    raise undoable.UndoConflict("Tasks were added to the group since, so it was kept.")
            current = undoable.row("task_groups", "id", group_id, cols)
            if current is None:
                return
            if not undoable.same(current, created, cols):
                raise undoable.UndoConflict("The group was changed since, so it was kept.")
            with get_db_connection() as c:
                # scoped: the list this user just created, unchanged since (checked above)
                c.execute("DELETE FROM task_groups WHERE id = ?", (group_id,))
                c.commit()
            sharing.forget(groups.ITEM_TYPE, group_id)
        undoable.safe_record(f"Added group “{name.strip()}”", _undo_group, "add_task_group")
    return f"Added task group '{emoji} {name.strip()}'."


@addon.tool(is_write=True, confirm=True, describe=approvals.remove_task_group)
async def remove_task_group(conn: Connection, name: str) -> str:
    """Remove a task group (the user is asked first). Its tasks move to 'General'.

    Args:
        name: The name of the group to remove (e.g. 'Fitness').
    """
    user = conn.user
    try:
        group = groups.find_group(user, name)
        if not group:
            return _no_group(user, name)
        if group["id"] == groups.default_group_id(user):
            return "Error: Cannot remove the default 'General' group."
        if group.get("shared_by") is not None:
            return f"Error: Only {group['shared_by'] or 'its owner'} can remove the group '{group['name']}'."
        groups.delete_group(user, group["id"])
        return f"Removed group '{group['name']}'. Its tasks moved to 'General'."
    except Exception as e:
        logger.error(f"Error removing task group: {e}", exc_info=True)
        return f"Error: Failed to remove task group: {e}"
