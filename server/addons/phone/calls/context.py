"""What the call and notification prompts know about their user: their saved
contacts, open tasks, task lists, active projects, zones and
recent decisions on suggestions.

Every piece comes from that user's own addons through the functions
the other addons offer, and is simply absent when they lack the addon; nothing
of anyone else's is read.
"""
from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Dict, List, Optional

from core import clock, user_profile, zones
from core.db import get_db_connection
from core.users import User
from core.addons import store
from addons.knowledge import projects as knowledge_projects
from addons.people import store as people_store
from addons.phone.calls.values import text
from addons.tasks import groups as task_groups, service as tasks_service
from addons.whatsapp import contacts as whatsapp_contacts

logger = logging.getLogger(__name__)


def known_people(user: Optional[User]) -> List[str]:
    """The user's saved contacts for the call-analysis prompt (their WhatsApp
    contacts: addons/whatsapp/contacts.py); none without ``people``."""
    if user is None or not people_store.has_people(user):
        return []
    return whatsapp_contacts.known_people(user)


def active_projects(user: Optional[User]) -> List[Dict[str, str]]:
    """Active projects the user can see (none without ``knowledge``)."""
    try:
        return knowledge_projects.active_projects(user)
    except Exception:
        return []


def open_tasks(user: Optional[User]) -> List[Dict[str, str]]:
    """Open tasks the user can see (none without ``tasks``)."""
    try:
        where, args = tasks_service.visible_sql(user, "")
        with get_db_connection() as conn:
            rows = conn.execute(
                f"SELECT id, title FROM tasks WHERE status != 'completed' AND {where} ORDER BY created_at DESC LIMIT 60",
                args).fetchall()
        return [{"id": r["id"], "title": r["title"]} for r in rows]
    except Exception:
        return []


def task_group_names(user: Optional[User]) -> List[str]:
    """Names of the user's task lists (the defaults when there are none)."""
    try:
        return task_groups.group_names(user) or [g["name"] for g in task_groups.DEFAULT_GROUPS]
    except Exception:
        return ["General", "Home", "Work", "Shopping", "Health", "Personal"]


# user id → (loop time, zone names)
def zone_names(user: Optional[User]) -> List[str]:
    """The names of ``user``'s zones (core/zones.py; empty without a user)."""
    return [z.name for z in zones.list_zones(user)] if user is not None else []


async def gather_context(user: Optional[User]) -> Dict[str, Any]:
    """Everything the prompts use about ``user`` (see the module docstring)."""
    return {
        "people": known_people(user),
        "projects": active_projects(user),
        "open_tasks": open_tasks(user),
        "groups": task_group_names(user),
        "zones": zone_names(user),
        "feedback": suggestion_feedback(user),
    }


def suggestion_feedback(user: Optional[User], days: int = 45, accepted: int = 8,
                        dismissed: int = 12) -> Dict[str, List[str]]:
    """The user's recent decisions on their suggestions, as short lines for the
    prompts: what they accepted (and how they rewrote it) and what they dismissed."""
    since = (clock.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    out: Dict[str, List[str]] = {"call": [], "notification": []}
    if user is None:
        return out
    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT source_type, status, payload FROM call_suggestions WHERE user_id = ? "
                "AND status IN ('accepted', 'dismissed') "
                "AND kind != 'alert' AND COALESCE(resolved_at, created_at) >= ? "
                "ORDER BY COALESCE(resolved_at, created_at) DESC LIMIT 200", (user.id, since)).fetchall()
    except Exception as e:
        logger.warning(f"Couldn't read suggestion feedback: {e}")
        return out
    counts: Dict[tuple, int] = {}
    for r in rows:
        src = r["source_type"] or "call"
        if src not in out:
            continue
        cap = accepted if r["status"] == "accepted" else dismissed
        if counts.get((src, r["status"]), 0) >= cap:
            continue
        try:
            p = json.loads(r["payload"] or "{}")
        except (ValueError, TypeError):
            continue
        origin = text((p.get("description") or "").split("\n")[0]).rstrip(".:")
        title = text(p.get("title"))
        if not title:
            continue
        if r["status"] == "accepted" and p.get("suggested_title"):
            line = f"- accepted, rewritten: “{p['suggested_title']}” → “{title}” ({origin})"
        else:
            line = f"- {r['status']}: “{title}” ({origin})"
        out[src].append(line)
        counts[(src, r["status"])] = counts.get((src, r["status"]), 0) + 1
    return out


def feedback_prompt(user: Optional[User], ctx: Dict[str, Any], source: str) -> str:
    """The prompts' block of ``user``'s recent decisions ("" when there are none)."""
    lines = (ctx.get("feedback") or {}).get(source) or []
    if not lines:
        return ""
    who = user_profile.for_user(user)
    return (f"\n{who.first_name}'s recent decisions on suggestions like these (learn what {who.pronoun} "
            f"{who.agree('finds', 'find')} useful; "
            "don't suggest things like the dismissed ones):\n" + "\n".join(lines) + "\n")
