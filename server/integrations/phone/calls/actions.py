"""Acting on a user's suggestion: accept (the task / calendar event it
describes, through that user's own integrations), dismiss, edit, reopen.

Nothing is created without the user: a suggestion becomes a task / calendar
event only through :func:`accept_suggestion` (a tap in the app, or the agent
acting on an explicit request in a chat opened from that suggestion). Every
function takes the user it acts for; another user's suggestion answers like
one that does not exist.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, Optional, Tuple

from core.db import get_db_connection
from core.users import User
from integrations.calendar import config as calendar_config, tools as calendar_tools
from integrations.framework import store
from integrations.phone import suggestions as phone_suggestions
from integrations.phone.calls.analysis import valid_date
from integrations.phone.calls.context import zone_names
from integrations.phone.calls.values import loads, text
from integrations.tasks import groups as task_groups, service as tasks_service, titles as task_titles


def resolve_suggestion_id(user: Optional[User], ref: str) -> Optional[str]:
    """``user``'s suggestion by full id or a unique prefix (the agent sometimes shortens ids)."""
    ref = (ref or "").strip()
    if not ref or user is None:
        return None
    with get_db_connection() as conn:
        rows = conn.execute("SELECT id FROM call_suggestions WHERE user_id = ? AND (id = ? OR id LIKE ?) LIMIT 2",
                            (user.id, ref, ref + "%")).fetchall()
    return rows[0]["id"] if len(rows) == 1 or (rows and rows[0]["id"] == ref) else None


async def create_task_record(user: User, fields: Dict[str, Any], call_id: Optional[str] = None,
                             link: Optional[Tuple[str, str]] = None) -> str:
    """Insert a task for ``user`` (same rules as POST /api/tasks) and link it to ``call_id``
    (or to ``link`` = (ref_type, ref_id)).

    Raises:
        NotConnected: ``user`` does not have ``tasks``.
        ValueError: no title, a zone that does not exist, or a location
            trigger without Home Assistant (``tasks_service.NoHomeAssistant``).
        LookupError / tasks_service.ReadOnly: the list is not one they can add to.
    """
    store.require(user, tasks_service.KIND)
    title = text(fields.get("title"))
    if not title:
        raise ValueError("A task needs a title")
    title = await task_titles.sanitize_task_title(title)
    group_id = None
    gname = text(fields.get("group"))
    if gname:
        g = task_groups.find_group(user, gname)
        group_id = g["id"] if g else None
    if not group_id:
        group_id = await task_titles.resolve_task_group(user, gname or None, title)

    triggers = fields.get("triggers") or {}
    if not isinstance(triggers, dict):
        triggers = loads(triggers, {}) or {}
    loc = triggers.get("location")
    if isinstance(loc, dict) and loc.get("location"):
        tasks_service.check_location_trigger(user)
        zones = {z.lower(): z for z in await zone_names(user)}
        if zones and loc["location"].lower() not in zones:
            raise ValueError(f"'{loc['location']}' is not a Home Assistant zone. Zones: {', '.join(zones.values())}")
        loc["location"] = loc["location"].lower()
        loc["event"] = loc.get("event") if loc.get("event") in ("arrive", "leave") else "arrive"
    triggers = {k: v for k, v in triggers.items() if v not in (None, "", False, {})}

    refs = []
    if call_id:
        refs.append(("call", call_id, "origin"))
    if link and link[1]:
        refs.append((link[0], link[1], "origin"))
    return tasks_service.create_task(
        user, title=title, description=text(fields.get("description")) or None,
        priority=(fields.get("priority") or "medium").lower(), due_date=valid_date(fields.get("due_date")),
        triggers=json.dumps(triggers, ensure_ascii=False), group_id=group_id, task_links=refs)


async def accept_suggestion(user: User, suggestion_id: str,
                            overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create the task / calendar event ``user``'s suggestion describes (with edits), for ``user``.

    Raises:
        LookupError: not their suggestion.
        NotConnected: ``user`` lacks the kind it needs (``tasks`` / ``calendar``).
    """
    s = phone_suggestions.get_suggestion(user, suggestion_id)
    if not s:
        raise LookupError("Suggestion not found")
    if s["status"] != "pending":
        return {"status": s["status"], "id": s["id"], "result_ref": s.get("result_ref"), "already": True}
    payload = dict(s["payload"])
    # Keys present in the request win, including null (clears a due date).
    payload.update(overrides or {})
    # Remember what was suggested when the user rewrote it (feedback for the prompts).
    if text(payload.get("title")) != text(s["payload"].get("title")) and not payload.get("suggested_title"):
        payload["suggested_title"] = s["payload"].get("title")

    if s["kind"] == "alert":
        _resolve(s["id"], "dismissed", None)
        return {"status": "dismissed", "id": s["id"]}

    if s["kind"] == "event":
        result = await asyncio.to_thread(
            calendar_tools.add_event, store.require(user, calendar_config.KIND), text(payload.get("title")) or "Event",
            payload.get("start"), payload.get("end") or payload.get("start"),
            payload.get("description"), payload.get("location"))
        if str(result).startswith("Error"):
            raise RuntimeError(result)
        m = re.search(r"ID: (\S+?)\.?$", str(result))
        _resolve(s["id"], "accepted", f"event:{m.group(1) if m else ''}")
        return {"status": "accepted", "id": s["id"], "kind": "event", "message": result}

    if s.get("source_type") == "notification":
        task_id = await create_task_record(user, payload, link=("notification", s["source_id"]))
    else:
        task_id = await create_task_record(user, payload, call_id=s["call_id"])
    _resolve(s["id"], "accepted", f"task:{task_id}", payload=payload)
    return {"status": "accepted", "id": s["id"], "kind": "task", "task_id": task_id}


def _resolve(suggestion_id: str, status: str, result_ref: Optional[str], payload: Optional[Dict[str, Any]] = None):
    """Close a suggestion the caller already found among the user's own."""
    with get_db_connection() as conn:
        if payload is not None:
            conn.execute("UPDATE call_suggestions SET payload = ? WHERE id = ?",
                         (json.dumps(payload, ensure_ascii=False), suggestion_id))
        conn.execute(
            "UPDATE call_suggestions SET status = ?, result_ref = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, result_ref, suggestion_id))
        conn.commit()


def dismiss_suggestion(user: Optional[User], suggestion_id: str) -> bool:
    """Dismiss ``user``'s suggestion (a no-op when it is no longer pending).
    False: not their suggestion."""
    s = phone_suggestions.get_suggestion(user, suggestion_id)
    if not s:
        return False
    if s["status"] == "pending":
        _resolve(suggestion_id, "dismissed", None)
    return True


def update_suggestion_payload(user: Optional[User], suggestion_id: str,
                              changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Edit ``user``'s pending suggestion (None: not theirs, or not pending)."""
    s = phone_suggestions.get_suggestion(user, suggestion_id)
    if not s or s["status"] != "pending":
        return None
    payload = dict(s["payload"])
    payload.update({k: v for k, v in changes.items() if v is not None})
    with get_db_connection() as conn:
        conn.execute("UPDATE call_suggestions SET payload = ? WHERE id = ?",
                     (json.dumps(payload, ensure_ascii=False), suggestion_id))
        conn.commit()
    return phone_suggestions.get_suggestion(user, suggestion_id)


def reopen_suggestion(user: User, suggestion_id: str, payload: Dict[str, Any]) -> None:
    """Put ``user``'s suggestion back to pending with ``payload`` (an undo)."""
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE call_suggestions SET status = 'pending', result_ref = NULL, resolved_at = NULL, payload = ? "
            "WHERE id = ? AND user_id = ?", (json.dumps(payload, ensure_ascii=False), suggestion_id, user.id))
        conn.commit()
