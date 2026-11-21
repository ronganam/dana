"""Action items → suggestions (``call_suggestions``: ``task|event|alert``),
for calls and notifications alike; each suggestion belongs to its source's
user (``user_id``)."""
from __future__ import annotations

import datetime
import difflib
import json
import re
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

from core import clock
from core.users import User
from integrations.phone.calls.context import open_tasks as user_open_tasks
from integrations.phone.calls.labels import group_for_labels
from integrations.phone.calls.values import MIN_SUGGESTION_CONFIDENCE


def _similar(a: str, b: str) -> float:
    strip = lambda s: re.sub(r"[^\w\s]", "", (s or "").lower()).strip()
    return difflib.SequenceMatcher(None, strip(a), strip(b)).ratio()


def _item_moment(item: Dict[str, Any]) -> Optional[datetime.datetime]:
    tz = clock.get_timezone()
    for key, fmt in (("event_start", "%Y-%m-%dT%H:%M:%S"), ("time_trigger", "%Y-%m-%d %H:%M")):
        if item.get(key):
            try:
                return datetime.datetime.strptime(item[key], fmt).replace(tzinfo=tz)
            except ValueError:
                pass
    if item.get("due_date"):
        try:
            d = datetime.date.fromisoformat(item["due_date"])
            return datetime.datetime(d.year, d.month, d.day, 23, 59, tzinfo=tz)
        except ValueError:
            pass
    return None


def suggestions_from_analysis(user: Optional[User], call: Dict[str, Any], analysis: Dict[str, Any], *,
                              only_future: bool = False, open_tasks: Optional[List[Dict[str, str]]] = None,
                              origin: Optional[str] = None,
                              extra_titles: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Turn action items into suggestion rows ({kind, payload}) for ``user``
    (deduped against their open tasks unless ``open_tasks`` is given).

    ``origin`` replaces the "From the call with …" description prefix (used for
    notification suggestions); ``extra_titles`` are more titles to dedupe
    against (e.g. pending suggestions)."""
    now = clock.now()
    open_titles = [t["title"] for t in (open_tasks if open_tasks is not None else user_open_tasks(user))]
    open_titles += [t for t in (extra_titles or []) if t]
    contact = call.get("contact") or "Unknown"
    when = call.get("when")
    when_text = when.strftime("%d/%m %H:%M") if when else ""
    default_group = group_for_labels(analysis.get("labels") or [])
    out: List[Dict[str, Any]] = []

    if analysis.get("security_alert") and not only_future:
        out.append({"kind": "alert", "payload": {
            "title": f"🔐 {analysis['security_alert']}",
            "text": analysis["security_alert"],
        }})

    for item in analysis.get("action_items") or []:
        if item["owner"] not in ("me", "both") or item["kind"] == "info":
            continue
        if item["confidence"] < MIN_SUGGESTION_CONFIDENCE:
            continue
        moment = _item_moment(item)
        if only_future and (moment is None or moment <= now):
            continue
        if any(_similar(item["text"], t) >= 0.85 for t in open_titles):
            continue
        item_origin = origin or (f"From the call with {contact}" + (f" ({when_text})" if when_text else ""))
        description = item_origin + (f":\n“{item['evidence']}”" if item.get("evidence") else ".")

        if item["kind"] == "event" and item.get("event_start"):
            start = item["event_start"]
            end = item.get("event_end")
            if not end:
                try:
                    end = (datetime.datetime.strptime(start, "%Y-%m-%dT%H:%M:%S")
                           + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    end = start
            out.append({"kind": "event", "payload": {
                "title": item["text"], "start": start, "end": end,
                "location": item.get("event_location"), "description": description,
                "confidence": item["confidence"], "evidence": item.get("evidence") or "",
            }})
            continue

        triggers: Dict[str, Any] = {}
        tt = item.get("time_trigger")
        if tt:
            try:
                if datetime.datetime.strptime(tt, "%Y-%m-%d %H:%M").replace(tzinfo=now.tzinfo) > now:
                    triggers["time"] = tt
            except ValueError:
                pass
        if item.get("location_zone"):
            triggers["location"] = {"location": item["location_zone"].lower(),
                                    "event": item.get("location_event") or "arrive"}
        if item.get("car_trigger"):
            triggers["car"] = item["car_trigger"]
        if item.get("wake_trigger") and not triggers:
            triggers["wake"] = True  # with a time/place it would just fire twice
        out.append({"kind": "task", "payload": {
            "title": item["text"],
            "description": description,
            "due_date": item.get("due_date"),
            "priority": item.get("priority") or "medium",
            "group": item.get("group") or default_group,
            "triggers": triggers,
            "confidence": item["confidence"],
            "evidence": item.get("evidence") or "",
        }})
    return out


def replace_pending_suggestions(conn: sqlite3.Connection, user: User, source_id: str,
                                suggestions: List[Dict[str, Any]], source_type: str = "call") -> int:
    """Replace the pending suggestions of ``user``'s source ``source_id`` (a
    call, or a notification when ``source_type='notification'``) with
    ``suggestions``, in the caller's transaction."""
    conn.execute("DELETE FROM call_suggestions WHERE call_id = ? AND source_type = ? AND status = 'pending' "
                 "AND user_id = ?", (source_id, source_type, user.id))
    for s in suggestions:
        conn.execute(
            "INSERT INTO call_suggestions (id, user_id, call_id, source_type, kind, payload, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (str(uuid.uuid4()), user.id, source_id, source_type, s["kind"],
             json.dumps(s["payload"], ensure_ascii=False)),
        )
    return len(suggestions)
