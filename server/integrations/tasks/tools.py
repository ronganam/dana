"""The tasks tools (docs/tasks-and-triggers.md). Every tool works on what
the connection's user can see: their own lists and tasks plus the lists shared
with them (``service`` / ``groups``)."""

import logging
import re
import json
import datetime
from typing import Optional, Dict, Any
from integrations.framework import Connection, sharing, store
from integrations.tasks.kind import tasks
from integrations.tasks import groups, service
from integrations.tasks import titles as task_titles
from core import undoable
from core.users import User
from integrations.homeassistant import client as ha_api
from core import clock
from core.db import get_db_connection

logger = logging.getLogger(__name__)

UUID_PATTERN = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.IGNORECASE)

def _parse_location_event(event: Optional[str]) -> str:
    if not event:
        return "arrive"
    val = event.strip().lower()
    if any(x in val for x in ("leave", "exit", "out", "dep", "left")):
        return "leave"
    if any(x in val for x in ("arrive", "enter", "in", "arr", "entr")):
        return "arrive"
    return "arrive"

async def validate_ha_zone(user: User, location: str) -> str:
    """Check that *location* matches an existing HA zone friendly name.

    Returns the properly-cased zone name on success.
    Raises ValueError with available zones on mismatch, and
    ``service.NoHomeAssistant`` (a ValueError) when the user has no Home Assistant.
    Falls back to returning the input lowered if HA is unreachable (no zones).
    """
    service.check_location_trigger(user)
    zones = await ha_api.get_ha_zones_list(store.require(user, "homeassistant"))
    if not zones:
        logger.warning("HA unreachable or no zones configured — skipping zone validation.")
        return location.strip().lower()

    loc_lower = location.strip().lower()
    for z in zones:
        if z["name"].strip().lower() == loc_lower:
            return z["name"]

    zone_list = "\n".join(f"  • {z['name']} ({z['entity_id']})" for z in zones)
    raise ValueError(
        f"'{location}' is not a valid Home Assistant zone. "
        f"Home Assistant only supports zone-based location triggers, so the "
        f"location must match an existing zone exactly.\n\n"
        f"Available zones:\n{zone_list}\n\n"
        f"Call get_ha_zones to see this list and pick the correct zone name."
    )

def _parse_car_action(trigger: Optional[str]) -> Optional[str]:
    if not trigger:
        return None
    val = trigger.strip().lower()
    if any(x in val for x in ("leave", "disconnect", "disconnected", "out", "disc", "stop", "depart")):
        return "leave"
    if any(x in val for x in ("enter", "connect", "connected", "in", "conn", "start", "arrive")):
        return "enter"
    return None

def _extract_uuid(text: str) -> Optional[str]:
    match = UUID_PATTERN.search(text)
    return match.group(0) if match else None

def _format_task_metadata(priority: str, due_date: Optional[str], triggers_str: str) -> str:
    """Format priority, due date, and triggers into a clean readable string."""
    meta_parts = []
    
    if priority:
        prio_emojis = {"high": "🔴 High", "medium": "🟡 Medium", "low": "🟢 Low"}
        meta_parts.append(f"⚡ Priority: {prio_emojis.get(priority.lower(), priority)}")
        
    if due_date:
        meta_parts.append(f"📅 Due: {due_date}")
        
    try:
        triggers = json.loads(triggers_str or '{}')
        loc = triggers.get("location")
        if loc and loc.get("location"):
            action = loc.get("event") or "arrive"
            target = loc.get("location")
            meta_parts.append(f"📍 Location: {action} {target}")
            
        time_trig = triggers.get("time")
        if time_trig:
            meta_parts.append(f"⏰ Time: {time_trig}")
            
        car_trig = triggers.get("car")
        if car_trig:
            meta_parts.append(f"🚗 Car: {car_trig}")
            
        wake_trig = triggers.get("wake")
        if wake_trig:
            meta_parts.append("☀️ Wake: up")
    except Exception:
        pass
        
    if meta_parts:
        return f" ({' | '.join(meta_parts)})"
    return ""

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
        elif unit in ("second", "sec"):
            dt = now + datetime.timedelta(seconds=amount)
        else:
            return None
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


@tasks.tool(is_write=True)
async def add_task(
    conn: Connection,
    title: str,
    due_date: Optional[str] = None,
    priority: str = "medium",
    group: Optional[str] = None,
    location: Optional[str] = None,
    location_event: Optional[str] = None,
    time_trigger: Optional[str] = None,
    car_trigger: Optional[str] = None,
    wake_trigger: Optional[bool] = None,
    description: Optional[str] = None,
    call_id: Optional[str] = None
) -> str:
    """Add a new task to the user's todo list.
    
    Args:
        title: The description of the task (e.g. 'Buy groceries').
        due_date: The optional due date of the task in YYYY-MM-DD. Use get_current_time to compute this; do NOT pass relative expressions like 'in 1 hour'.
        priority: The priority of the task ('low', 'medium', 'high').
        group: The optional group name for this task (e.g. 'Work', 'Home', 'Shopping'). If not provided, the best fitting group will be auto-detected.
        location: The optional location or zone name to trigger this task. IMPORTANT: This must match the lowercase friendly name of a Home Assistant zone (e.g. 'home', 'office'). If you are unsure what zones are available or need to map a name (like 'parents home' or 'work') to a zone, you MUST first call get_ha_zones to see the list of active zones and choose the correct one.
        location_event: The location event action, either 'arrive' or 'leave'.
        time_trigger: The optional date/time trigger (e.g. 'YYYY-MM-DD HH:MM'). Use get_current_time to compute the correct absolute datetime; do NOT pass relative expressions like 'in 1 hour from now'.
        car_trigger: The optional car status trigger ('enter' or 'leave').
        wake_trigger: The optional wake up trigger. Set to True to trigger when the user wakes up.
        description: The optional details or description of the task.
        call_id: Optional ID of the phone call this task came from (from list_recent_calls / search_calls / get_call_transcript). Links the task to that call so the user sees where it came from.
    """
    user = conn.user
    title_sanitized = await task_titles.sanitize_task_title(title)
    group_id = await task_titles.resolve_task_group(user, group, title)

    triggers: Dict[str, Any] = {}
    if location:
        validated_location = await validate_ha_zone(user, location)
        triggers["location"] = {
            "location": validated_location.strip().lower(),
            "event": _parse_location_event(location_event)
        }
    if time_trigger:
        parsed = _parse_relative_time(time_trigger)
        final_time_str = (parsed if parsed else time_trigger).strip()
        now = clock.now()
        try:
            parsed_dt = datetime.datetime.strptime(final_time_str, "%Y-%m-%d %H:%M").replace(tzinfo=now.tzinfo)
            if parsed_dt <= now:
                parsed_dt += datetime.timedelta(days=1)
                final_time_str = parsed_dt.strftime("%Y-%m-%d %H:%M")
                logger.info(f"Time trigger '{time_trigger}' was in the past, advanced to tomorrow: {final_time_str}")
        except ValueError:
            pass
        triggers["time"] = final_time_str
    if car_trigger:
        triggers["car"] = _parse_car_action(car_trigger)
    if wake_trigger is not None:
        triggers["wake"] = bool(wake_trigger)

    try:
        with get_db_connection() as db:
            linked_call = None
            task_links = []
            if call_id and call_id.strip():
                call_row = db.execute("SELECT id FROM phone_calls WHERE id = ? AND user_id = ?",
                                      (call_id.strip(), user.id)).fetchone()
                if call_row:
                    task_links.append(("call", call_row["id"], "origin"))
                    linked_call = call_row["id"]
            task_id = service.create_task(
                user, title=title_sanitized, description=description.strip() if description else None,
                priority=priority.strip().lower() if priority else "medium",
                due_date=(_parse_relative_time(due_date) or due_date).strip() if due_date else None,
                triggers=json.dumps(triggers), group_id=group_id, task_links=task_links, conn=db)
            db.commit()

        created = undoable.row("tasks", "id", task_id, service.TASK_COLUMNS)
        if created:
            undoable.delete_created_row(f"Added task “{title_sanitized}”", "tasks", "id", task_id, created,
                                        service.TASK_COLUMNS, "add_task", cascade=(("task_links", "task_id"),),
                                        guard=lambda: service.check_still_writable(user, task_id))

        group_name = "General"
        if group_id != groups.default_group_id(user):
            g = groups.get_group(user, group_id)
            if g:
                group_name = g["name"]

        msg = f"Successfully added task '{title_sanitized}' to local list (ID: {task_id}) [Group: {group_name}]"
        trigger_labels = []
        if location:
            trigger_labels.append(f"location trigger '{location}' ({location_event or 'arrive'})")
        if time_trigger:
            trigger_labels.append(f"time trigger '{time_trigger}'")
        if car_trigger:
            trigger_labels.append(f"car trigger '{car_trigger}'")
        if wake_trigger:
            trigger_labels.append("wake trigger")
        if trigger_labels:
            msg += f" with {', '.join(trigger_labels)}"
        if linked_call:
            msg += f", linked to call {linked_call}"
        msg += "."
        return msg
    except (LookupError, service.ReadOnly) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"Error adding task to SQLite: {e}", exc_info=True)
        return f"Error: Failed to add task to database: {e}"

@tasks.tool(is_write=True)
async def update_task(
    conn: Connection,
    item: str,
    summary: Optional[str] = None,
    location: Optional[str] = None,
    location_event: Optional[str] = None,
    time_trigger: Optional[str] = None,
    car_trigger: Optional[str] = None,
    wake_trigger: Optional[bool] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    due_date: Optional[str] = None,
    group: Optional[str] = None
) -> str:
    """Update an existing task in the Home Assistant todo list.
    
    Use this tool to modify an existing task's summary/title, change its status (e.g. mark completed),
    update its details/description, or add/change trigger options.
    
    Args:
        item: The UID (unique ID, e.g. '7246aef6-672e-11f1-bbb0-bc2411aeaff6') of the task. Always use the UID (found in the 'ID: ...' part of the list_tasks output) to ensure we target the correct task, especially if there are multiple tasks with similar names.
        summary: The optional new summary/title for the task.
        location: The optional location or zone name to trigger this task. Specifying this will update the location trigger. Pass empty string to remove. IMPORTANT: This must match the lowercase friendly name of a Home Assistant zone (e.g. 'home', 'office'). If you are unsure what zones are available or need to map a name (like 'parents home' or 'work') to a zone, you MUST first call get_ha_zones to see the list of active zones and choose the correct one.
        location_event: The location event action, either 'arrive' or 'leave'.
        time_trigger: The optional new date/time trigger (e.g. 'YYYY-MM-DD HH:MM'). Pass empty string to remove. Use get_current_time to compute the correct absolute datetime; do NOT pass relative expressions like 'in 1 hour from now'.
        car_trigger: The optional new car status trigger ('enter' or 'leave'). Pass empty string to remove.
        wake_trigger: The optional new wake up trigger (True/False). Pass False or empty string to remove.
        description: The optional new description. If provided, it replaces the current description (except the trigger tags, which are preserved/updated).
        status: The optional status of the task ('needs_action' or 'completed').
        priority: The priority of the task ('high', 'medium', 'low'). Pass empty string to reset.
        due_date: The optional due date of the task (YYYY-MM-DD). Pass empty string to remove.
        group: The optional new group name for this task. Pass empty string to reset to 'General'.
    """
    user = conn.user
    target_id = _extract_uuid(item) or item

    try:
        # 1. Resolve task
        row = service.find_task(user, target_id, uuid_like=bool(_extract_uuid(target_id)))
        if not row:
            return f"Error: Task '{item}' was not found in the database."
        if service.level_of(user, row) == sharing.READ:
            return f"Error: {service.ReadOnly()}"

        task_data = dict(row)
        task_id = task_data["id"]

        # 2. Update basic fields
        new_title = await task_titles.sanitize_task_title(summary) if summary else task_data["title"]
        new_desc = description.strip() if description is not None else task_data["description"]
        new_priority = priority.strip().lower() if priority is not None else task_data["priority"]
        if new_priority == "":
            new_priority = "medium"
        new_due_date = (_parse_relative_time(due_date) or due_date).strip() if due_date is not None else task_data["due_date"]
        if new_due_date == "":
            new_due_date = None
        new_status = status.strip().lower() if status else task_data["status"]

        # 2b. Update group
        new_group_id = task_data.get("group_id")
        if group is not None:
            if group == "":
                new_group_id = groups.default_group_id(user)
            else:
                g = groups.find_group(user, group)
                if g:
                    new_group_id = g["id"]
                else:
                    return f"Error: Group '{group}' not found. Use list_task_groups to see available groups."

        # 3. Update triggers JSON
        triggers: Dict[str, Any] = {}
        try:
            triggers = json.loads(task_data["triggers"] or '{}')
        except Exception:
            pass

        if location is not None:
            if location == "":
                triggers.pop("location", None)
            else:
                validated_location = await validate_ha_zone(user, location)
                curr_loc = triggers.get("location") or {}
                evt = _parse_location_event(location_event) if location_event else curr_loc.get("event") or "arrive"
                triggers["location"] = {
                    "location": validated_location.strip().lower(),
                    "event": evt
                }
        elif location_event is not None and "location" in triggers:
            triggers["location"]["event"] = _parse_location_event(location_event)

        if time_trigger is not None:
            if time_trigger == "":
                triggers.pop("time", None)
            else:
                parsed = _parse_relative_time(time_trigger)
                triggers["time"] = (parsed if parsed else time_trigger).strip()

        if car_trigger is not None:
            if car_trigger == "":
                triggers.pop("car", None)
            else:
                triggers["car"] = _parse_car_action(car_trigger)

        if wake_trigger is not None:
            if wake_trigger is False or wake_trigger == "":
                triggers.pop("wake", None)
            else:
                triggers["wake"] = bool(wake_trigger)

        # 4. Save updates
        service.save_task(user, task_id, {
            "title": new_title, "description": new_desc, "priority": new_priority, "due_date": new_due_date,
            "status": new_status, "triggers": json.dumps(triggers), "group_id": new_group_id})

        after = undoable.row("tasks", "id", task_id, service.TASK_COLUMNS)
        if after and not undoable.same(after, task_data, service.TASK_COLUMNS):
            if new_status == "completed" and task_data.get("status") != "completed":
                label = f"Completed “{new_title}”"
            elif new_status != "completed" and task_data.get("status") == "completed":
                label = f"Reopened “{new_title}”"
            else:
                label = f"Changed task “{new_title}”"
            service.record_change_undo(user, label, task_id, task_data, after, "update_task")

        return f"Successfully updated task '{new_title}' (ID: {task_id}) in the local database."
    except (LookupError, service.ReadOnly) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"Error updating task in SQLite: {e}", exc_info=True)
        return f"Error: Failed to update task: {e}"

@tasks.tool(is_write=True, confirm=True)
async def delete_task(conn: Connection, item: str) -> str:
    """Delete a task from the Home Assistant todo list.
    
    Args:
        item: The UID (unique ID, e.g. '7246aef6-672e-11f1-bbb0-bc2411aeaff6') of the task. Always use the UID (found in the 'ID: ...' part of the list_tasks output) to ensure we target the correct task, especially if there are multiple tasks with similar names.
    """
    user = conn.user
    target_id = _extract_uuid(item) or item

    try:
        row = service.find_task(user, target_id, uuid_like=bool(_extract_uuid(target_id)))
        if not row:
            return f"Error: Task '{item}' was not found in the database."
        deleted = service.delete_task(user, row["id"])
        return f"Successfully deleted task '{deleted['title']}' (ID: {deleted['id']}) from the local database."
    except (LookupError, service.ReadOnly) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"Error deleting task from SQLite: {e}", exc_info=True)
        return f"Error: Failed to delete task: {e}"

@tasks.tool()
async def list_tasks(conn: Connection, completed: bool = False) -> str:
    """List todo tasks. Call this first to find the task ID before using update_task or delete_task.
    
    Args:
        completed: If True, lists only completed tasks; if False, lists only active/incomplete tasks.
    """
    user = conn.user
    status_filter = "completed" if completed else "needs_action"
    where, args = service.visible_sql(user)

    try:
        with get_db_connection() as db:
            cursor = db.execute(
                f"""
                SELECT t.*, tg.name AS group_name, tg.emoji AS group_emoji
                FROM tasks t
                LEFT JOIN task_groups tg ON t.group_id = tg.id
                WHERE t.status = ? AND {where}
                ORDER BY t.created_at DESC
                """,
                (status_filter, *args)
            )
            rows = cursor.fetchall()

        if not rows:
            status_str = "completed" if completed else "incomplete"
            return f"You have no {status_str} tasks."

        result = f"Your {'completed' if completed else 'active'} tasks:\n"
        for row in rows:
            title = row["title"]
            desc = row["description"] or ""
            uid = row["id"]
            priority = row["priority"]
            due_date = row["due_date"]
            triggers_str = row["triggers"]
            group_name = row["group_name"] or "General"
            group_emoji = row["group_emoji"] or "📁"
            shared = service.public_task(user, dict(row))
            if shared.get("shared_by") is not None:
                group_name += f" · shared by {shared['shared_by']}" + (", read-only" if shared["access"] == sharing.READ else "")

            meta_str = _format_task_metadata(priority, due_date, triggers_str)
            desc_str = f" - {desc}" if desc else ""

            result += f"- {title} [{group_emoji} {group_name}]{meta_str}{desc_str} (ID: {uid})\n"

        return result
    except Exception as e:
        logger.error(f"Error listing tasks from SQLite: {e}", exc_info=True)
        return f"Error: Failed to fetch tasks from database: {e}"

@tasks.tool()
async def list_task_groups(conn: Connection) -> str:
    """List all available task groups with their emoji, color, and task count."""
    try:
        found = groups.list_groups(conn.user)
        if not found:
            return "No task groups defined."

        result = "Available task groups:\n"
        for g in found:
            name = g["name"]
            emoji = g.get("emoji", "📁")
            count = g.get("task_count", 0)
            desc = g.get("description", "")
            desc_str = f" — {desc}" if desc else ""
            shared_str = ""
            if g.get("shared_by") is not None:
                shared_str = f" [shared by {g['shared_by']}{', read-only' if g['access'] == sharing.READ else ''}]"
            result += f"- {emoji} **{name}** (Tasks: {count}){shared_str}{desc_str}\n"

        return result
    except Exception as e:
        logger.error(f"Error listing task groups: {e}", exc_info=True)
        return f"Error: Failed to list task groups: {e}"

@tasks.tool(is_write=True)
async def add_task_group(conn: Connection, name: str, emoji: str = "📁", color: str = "#6366f1", description: str = "") -> str:
    """Add a new task group for organizing tasks.
    
    Args:
        name: The display name for the group (e.g. 'Fitness', 'Finance').
        emoji: An emoji icon for the group (e.g. '💪', '💰').
        color: A hex color for the group (e.g. '#ff6b6b').
        description: Optional description of what this group is for.
    """
    user = conn.user
    if not name.strip().lower().replace(" ", "_"):
        return "Error: Group name cannot be empty."

    try:
        group_id = groups.create_group(user, name, emoji, color, description)
        cols = ("name", "emoji", "color", "description")
        created = undoable.row("task_groups", "id", group_id, cols)
        if created:
            def _undo_group():
                with get_db_connection() as c:
                    if c.execute("SELECT 1 FROM tasks WHERE group_id = ?", (group_id,)).fetchone():
                        raise undoable.UndoConflict("Tasks were added to the group since, so it was kept.")
                current = undoable.row("task_groups", "id", group_id, cols)
                if current is None:
                    return
                if not undoable.same(current, created, cols):
                    raise undoable.UndoConflict("The group was changed since, so it was kept.")
                with get_db_connection() as c:
                    c.execute("DELETE FROM task_groups WHERE id = ?", (group_id,))
                    c.commit()
                sharing.forget(groups.ITEM_TYPE, group_id)
            undoable.safe_record(f"Added group “{name.strip()}”", _undo_group, "add_task_group")
        return f"Successfully added task group '{emoji} {name}'."
    except Exception as e:
        logger.error(f"Error adding task group: {e}", exc_info=True)
        return f"Error: Failed to add task group. It may already exist: {e}"

@tasks.tool(is_write=True, confirm=True)
async def remove_task_group(conn: Connection, name: str) -> str:
    """Remove a task group. Tasks in this group will be reassigned to 'General'.
    
    Args:
        name: The name of the group to remove (e.g. 'Fitness').
    """

    user = conn.user
    try:
        group = groups.find_group(user, name)
        if not group:
            return f"Error: Group '{name}' not found."

        if group["id"] == groups.default_group_id(user):
            return "Error: Cannot remove the default 'General' group."
        if group.get("shared_by") is not None:
            return f"Error: Only {group['shared_by'] or 'its owner'} can remove the group '{group['name']}'."

        groups.delete_group(user, group["id"])

        return f"Successfully removed group '{group['name']}'. Existing tasks were moved to 'General'."
    except Exception as e:
        logger.error(f"Error removing task group: {e}", exc_info=True)
        return f"Error: Failed to remove task group: {e}"
