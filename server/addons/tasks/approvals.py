"""The words of the Approve card of the tasks tools that can ask first
(``@addon.tool(describe=...)``; core/approval_text.py shows them)."""

import datetime
import re
from typing import Any, Dict

from core import clock, now as now_screen, users
from core.addons import Approval
from core.addons.hooks import clean
from addons.tasks import service

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _quote(text: Any, limit: int = 80) -> str:
    return f"“{clean(text, limit)}”"


def _title(item: Any) -> str:
    """The task an ``item`` argument names: its title when it is the id of a
    task the current user can see, else the argument itself ("" for an unseen id)."""
    raw = clean(item, 200)
    m = _UUID.search(raw)
    if not m:
        return raw
    task = service.get_task(users.current(), m.group(0))
    return (task.get("title") or "") if task else ""


def _when(value: Any) -> str:
    """ISO time → 'tomorrow 19:00' (the Now screen's wording); the raw value when unparsable."""
    raw = clean(value, 40)
    if not raw:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=clock.get_timezone())
    return now_screen.when_phrase(dt)


def delete_task(a: Dict[str, Any]) -> Approval:
    name = _title(a.get("item"))
    return Approval(f"Delete the reminder {_quote(name)}" if name else "Delete a reminder", "Delete reminder", "❌",
                    "Delete this reminder?")


def remove_task_group(a: Dict[str, Any]) -> Approval:
    return Approval(f"Remove the group {_quote(a.get('name'))}", "Remove group", "❌", "Remove this group?",
                    facts=[("Its reminders", "move to General")])


def add_task(a: Dict[str, Any]) -> Approval:
    loc = a.get("location")
    return Approval(f"Add the reminder {_quote(a.get('title') or 'Untitled')}", "New reminder", "📋",
                    "Add this reminder?", body_label="Note", body=a.get("description"),
                    facts=[("Due", a.get("due_date")), ("Remind", _when(a.get("time_trigger"))),
                           ("Place", f"{loc} ({a.get('location_event') or 'arrive'})" if loc else ""),
                           ("Car", a.get("car_trigger")), ("On waking", "yes" if a.get("wake_trigger") else ""),
                           ("Priority", a.get("priority"))])


def update_task(a: Dict[str, Any]) -> Approval:
    name = _title(a.get("item"))
    return Approval(f"Change the reminder {_quote(name)}" if name else "Change a reminder", "Change reminder", "🔄",
                    "Make this change?", body_label="Note", body=a.get("description"),
                    facts=[("New title", a.get("summary") or a.get("title")), ("Status", a.get("status")),
                           ("Place", a.get("location"))])
