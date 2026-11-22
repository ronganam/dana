"""HTTP routes of the tasks addon: tasks, lists (task groups), snoozes and task
links (docs/tasks-and-triggers.md).

- ``GET|POST /api/tasks``, ``PUT|DELETE /api/tasks/{id}``, ``POST /api/tasks/{id}/snooze``
- ``GET|POST /api/task-groups``, ``PUT|DELETE /api/task-groups/{id}`` (owner only)
- ``POST|DELETE /api/tasks/{id}/links``

Every route answers for the request's user, who must have ``tasks`` connected
(else 403 ``not_connected`` from the middleware): their own tasks and lists
plus the lists shared with them. A location trigger is checked against the
user's own zones (core/zones.py): an unknown one is 400.
"""

import datetime
import json
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from core import clock
from core.auth import request_user
from core.users import User
from core.addons import sharing, store
from addons.people import store as people_store, values as people_values
from addons.phone import config as phone_config
from addons.phone.calls import store as calls_store
from addons.tasks import (
    groups as task_groups,
    links as task_links,
    service as tasks_service,
    titles as task_titles,
)

logger = logging.getLogger(__name__)


def _tasks_user(request: web.Request) -> User:
    """The request's user, who must have ``tasks`` connected (else the
    middleware answers 403 ``not_connected``)."""
    user = request_user(request)
    store.require(user, tasks_service.ADDON)
    return user


def _task_error(e: Exception) -> Optional[web.Response]:
    """The answer for a task / list the user cannot see, may not change or
    cannot give a location trigger (None: not one of those)."""
    if isinstance(e, (tasks_service.ReadOnly, sharing.NotOwner)):
        return web.json_response({"error": str(e)}, status=403)
    if isinstance(e, tasks_service.UnknownZone):
        return web.json_response({"error": str(e)}, status=400)
    if isinstance(e, LookupError):
        return web.json_response({"error": str(e) or "Task not found"}, status=404)
    return None


async def get_tasks_api(request: web.Request) -> web.Response:
    """GET /api/tasks — the caller's tasks and the tasks of lists shared with them."""
    user = _tasks_user(request)
    try:
        return web.json_response(tasks_service.list_tasks(user))
    except Exception as e:
        logger.error(f"API Error fetching tasks: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def create_task_api(request: web.Request) -> web.Response:
    """POST /api/tasks"""
    user = _tasks_user(request)
    try:
        data = await request.json()
        title = (data.get("title") or "").strip()
        if not title:
            return web.json_response({"error": "Title is required"}, status=400)

        title = await task_titles.sanitize_task_title(title)

        description = (data.get("description") or "").strip() or None
        priority = (data.get("priority") or "medium").strip().lower()
        due_date = (data.get("due_date") or "").strip() or None
        triggers = data.get("triggers") or {}

        # Resolve group
        group_id = data.get("group_id")
        if not group_id:
            group_id = await task_titles.resolve_task_group(user, None, title)

        # A location trigger names one of the user's zones
        loc_trigger = triggers.get("location") if isinstance(triggers, dict) else None
        if loc_trigger and isinstance(loc_trigger, dict) and loc_trigger.get("location"):
            loc_trigger["location"] = tasks_service.resolve_zone(user, str(loc_trigger["location"]))

        task_id = tasks_service.create_task(user, title=title, description=description, priority=priority,
                                            due_date=due_date, triggers=json.dumps(triggers), group_id=group_id)

        return web.json_response({"status": "created", "id": task_id, "group_id": group_id})
    except Exception as e:
        handled = _task_error(e)
        if handled is not None:
            return handled
        logger.error(f"API Error creating task: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def update_task_api(request: web.Request) -> web.Response:
    """PUT /api/tasks/{id}"""
    user = _tasks_user(request)
    task_id = request.match_info.get("id")
    try:
        data = await request.json()
        task_data = tasks_service.get_task(user, task_id)
        if not task_data:
            return web.json_response({"error": "Task not found"}, status=404)

        title = data.get("title")
        if title is not None:
            title = await task_titles.sanitize_task_title(str(title))
        else:
            title = task_data["title"]
        if not title:
            return web.json_response({"error": "Title is required"}, status=400)

        description = data.get("description")
        if description is not None:
            description = str(description).strip() or None
        else:
            description = task_data["description"]

        priority = data.get("priority")
        if priority is not None:
            priority = str(priority).strip().lower() or "medium"
        else:
            priority = task_data["priority"]

        due_date = data.get("due_date")
        if due_date is not None:
            due_date = str(due_date).strip() or None
        else:
            due_date = task_data["due_date"]

        status = data.get("status")
        if status is not None:
            status = str(status).strip().lower()
        else:
            status = task_data["status"]

        group_id = data.get("group_id")
        if group_id is not None:
            if group_id == "" or group_id == "general":
                group_id = task_groups.default_group_id(user)
        else:
            group_id = task_data.get("group_id")

        triggers = data.get("triggers")
        if "triggers" not in data:
            try:
                triggers = json.loads(task_data["triggers"] or '{}')
            except Exception:
                triggers = {}
        elif triggers is None:
            triggers = {}

        # A location trigger names one of the user's zones
        if isinstance(triggers, dict):
            loc_trigger = triggers.get("location")
            if loc_trigger and isinstance(loc_trigger, dict) and loc_trigger.get("location"):
                loc_trigger["location"] = tasks_service.resolve_zone(user, str(loc_trigger["location"]))

        tasks_service.save_task(user, task_id, {
            "title": title, "description": description, "priority": priority, "due_date": due_date,
            "status": status, "triggers": json.dumps(triggers), "group_id": group_id})

        return web.json_response({"status": "updated", "id": task_id})
    except Exception as e:
        handled = _task_error(e)
        if handled is not None:
            return handled
        logger.error(f"API Error updating task {task_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def snooze_task_api(request: web.Request) -> web.Response:
    """POST /api/tasks/{id}/snooze"""
    user = _tasks_user(request)
    task_id = request.match_info.get("id")
    try:
        data = await request.json()
        snooze_type = data.get("type", "time")

        if tasks_service.get_task(user, task_id) is None:
            return web.json_response({"error": "Task not found"}, status=404)

        # The snooze replaces every trigger the task had.
        triggers: Dict[str, Any] = {}

        if snooze_type == "time":
            minutes = int(data.get("minutes", 15))
            now = clock.now()
            snooze_time = now + datetime.timedelta(minutes=minutes)
            triggers["time"] = snooze_time.isoformat()
        elif snooze_type == "location":
            raw_loc = (data.get("location") or "").strip()
            if not raw_loc:
                return web.json_response({"error": "location is required for location snooze"}, status=400)
            loc = tasks_service.resolve_zone(user, raw_loc).lower()
            event = data.get("event", "arrive").strip().lower()
            triggers["location"] = {"location": loc, "event": event}
        elif snooze_type == "car":
            action = data.get("action", "enter").strip().lower()
            triggers["car"] = action
        elif snooze_type == "wake":
            triggers["wake"] = True
        else:
            return web.json_response({"error": f"Invalid snooze type: {snooze_type}"}, status=400)

        tasks_service.snooze_task(user, task_id, json.dumps(triggers))

        return web.json_response({"status": "snoozed", "id": task_id, "triggers": triggers})
    except Exception as e:
        handled = _task_error(e)
        if handled is not None:
            return handled
        logger.error(f"API Error snoozing task {task_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def delete_task_api(request: web.Request) -> web.Response:
    """DELETE /api/tasks/{id}"""
    user = _tasks_user(request)
    task_id = request.match_info.get("id")
    try:
        tasks_service.delete_task(user, task_id)
        return web.json_response({"status": "deleted", "id": task_id})
    except Exception as e:
        handled = _task_error(e)
        if handled is not None:
            return handled
        logger.error(f"API Error deleting task {task_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def get_task_groups_api(request: web.Request) -> web.Response:
    """GET /api/task-groups — the caller's lists and the lists shared with them."""
    user = _tasks_user(request)
    try:
        return web.json_response(task_groups.list_groups(user))
    except Exception as e:
        logger.error(f"API Error fetching task groups: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def create_task_group_api(request: web.Request) -> web.Response:
    """POST /api/task-groups"""
    user = _tasks_user(request)
    try:
        data = await request.json()
        name = (data.get("name") or "").strip()
        if not name:
            return web.json_response({"error": "Name is required"}, status=400)

        emoji = (data.get("emoji") or "📁").strip()
        color = (data.get("color") or "#6366f1").strip()
        description = (data.get("description") or "").strip()
        group_id = task_groups.create_group(user, name, emoji, color, description)

        return web.json_response({"status": "created", "id": group_id, "name": name})
    except Exception as e:
        logger.error(f"API Error creating task group: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def update_task_group_api(request: web.Request) -> web.Response:
    """PUT /api/task-groups/{id} (owner only)"""
    user = _tasks_user(request)
    group_id = request.match_info.get("id")
    try:
        data = await request.json()
        row = task_groups.get_group(user, group_id)
        if not row:
            return web.json_response({"error": "Task group not found"}, status=404)

        name = (data.get("name") or row["name"]).strip()
        emoji = (data.get("emoji") or row["emoji"]).strip()
        color = (data.get("color") or row["color"]).strip()
        description = data.get("description")
        if description is not None:
            description = description.strip()
        else:
            description = row["description"]

        task_groups.update_group(user, group_id, {"name": name, "emoji": emoji, "color": color,
                                                  "description": description})

        return web.json_response({"status": "updated", "id": group_id})
    except Exception as e:
        handled = _task_error(e)
        if handled is not None:
            return handled
        logger.error(f"API Error updating task group {group_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def delete_task_group_api(request: web.Request) -> web.Response:
    """DELETE /api/task-groups/{id} (owner only; its tasks move to the owner's General list)"""
    user = _tasks_user(request)
    group_id = request.match_info.get("id")
    try:
        if not task_groups.delete_group(user, group_id):
            return web.json_response({"error": "Task group not found"}, status=404)
        return web.json_response({"status": "deleted", "id": group_id})
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        handled = _task_error(e)
        if handled is not None:
            return handled
        logger.error(f"API Error deleting task group {group_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def add_task_link_api(request: web.Request) -> web.Response:
    """POST /api/tasks/{id}/links {ref_type, ref_id, role?}"""
    user = _tasks_user(request)
    task_id = request.match_info.get("id")
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    ref_type = (data.get("ref_type") or "").strip()
    ref_id = (data.get("ref_id") or "").strip()
    if ref_type not in ("call", "session", "whatsapp", "memory", "project", "person") or not ref_id:
        return web.json_response({"error": "ref_type (call|session|whatsapp|memory|project|person) and ref_id are required"}, status=400)
    try:
        tasks_service.writable_task(user, task_id)
    except (LookupError, tasks_service.ReadOnly) as e:
        return _task_error(e)
    if ref_type == "person":
        # Only one of the user's own people (another user's id is unknown here).
        store.require(user, people_values.ADDON)
        if not people_store.owns(user, ref_id):
            return web.json_response({"error": "Person not found"}, status=404)
    if ref_type == "call":
        # Only one of the user's own calls (another user's id is unknown here).
        store.require(user, phone_config.ADDON)
        if not calls_store.get_row(user, ref_id):
            return web.json_response({"error": "Call not found"}, status=404)
    task_links.link_task(task_id, ref_type, ref_id, data.get("role") or "related")
    return web.json_response({"status": "linked",
                              "links": task_links.links_for_tasks(user, [task_id]).get(task_id, [])})


async def delete_task_link_api(request: web.Request) -> web.Response:
    """DELETE /api/tasks/{id}/links?ref_type=&ref_id="""
    user = _tasks_user(request)
    task_id = request.match_info.get("id")
    try:
        tasks_service.writable_task(user, task_id)
    except (LookupError, tasks_service.ReadOnly) as e:
        return _task_error(e)
    task_links.unlink_task(task_id, request.query.get("ref_type", ""), request.query.get("ref_id", ""))
    return web.json_response({"status": "unlinked"})


def setup(app: web.Application) -> None:
    """Register the tasks routes."""
    app.router.add_get("/api/tasks", get_tasks_api)
    app.router.add_post("/api/tasks", create_task_api)
    app.router.add_put("/api/tasks/{id}", update_task_api)
    app.router.add_post("/api/tasks/{id}/snooze", snooze_task_api)
    app.router.add_delete("/api/tasks/{id}", delete_task_api)
    app.router.add_get("/api/task-groups", get_task_groups_api)
    app.router.add_post("/api/task-groups", create_task_group_api)
    app.router.add_put("/api/task-groups/{id}", update_task_group_api)
    app.router.add_delete("/api/task-groups/{id}", delete_task_group_api)
    app.router.add_post("/api/tasks/{id}/links", add_task_link_api)
    app.router.add_delete("/api/tasks/{id}/links", delete_task_link_api)
