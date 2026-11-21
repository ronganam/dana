"""One person in full, the incoming-call / meeting card, and the context chat about a person.

Everything answers for one user from that user's people and activity only;
another user's person id answers like an unknown one. The card is plain DB
reads + the cached brief, never a model call (the phone asks while it rings).
A number none of the user's people has (or only a hidden number-only one
without history) still gets a card with the name their Google contacts give
it, when the user has ``contacts``.
"""
from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional

from core import clock
from core.db import get_db_connection
from core.users import User
from integrations.contacts import store as contacts
from integrations.people import activity, briefs, cadence, linking, person_tasks, store
from integrations.people.values import ago, initials, loads, parse_time, upcoming_birthday


def get_person(user: User, person_id: str, *, schedule_brief: bool = True) -> Optional[Dict[str, Any]]:
    """``user``'s person with stats, timeline (calls + message groups), tasks,
    suggestions and whether the brief is stale (which schedules a rewrite)."""
    linking.link_pending(user)
    with get_db_connection() as conn:
        r = store.row(conn, user, person_id)
        if not r:
            return None
        p = store.to_api(r, conn)
        p["stats"] = cadence.person_stats(conn, user, person_id)
        p["birthday_in_days"] = upcoming_birthday(r["birthday"])
        calls = activity.calls(conn, user, person_id)
        msgs = activity.message_groups(conn, user, person_id)
        p["timeline"] = sorted(calls + msgs, key=lambda e: e["at"], reverse=True)[:60]
        p["tasks"] = person_tasks.tasks_for(conn, user, person_id)
        p["suggestions"] = activity.suggestions_for(conn, user, person_id)
        sig = briefs.signature(conn, user, r)
    p["brief_stale"] = bool(sig and sig != r["brief_signature"])
    if schedule_brief and p["brief_stale"]:
        briefs.schedule_briefs(user, [person_id])
    return p


def card(user: User, person_id: str) -> Optional[Dict[str, Any]]:
    """What to show while the phone rings: pure DB reads + the cached brief, no model calls."""
    with get_db_connection() as conn:
        r = store.row(conn, user, person_id)
        if not r:
            return None
        stats = cadence.person_stats(conn, user, person_id)
        last_call = activity.last_ready_call(conn, user, person_id)
        tasks = person_tasks.tasks_for(conn, user, person_id)
        sugg = activity.suggestions_for(conn, user, person_id)
    brief = loads(r["brief"], {})
    last = parse_time(stats["last_contact_at"])
    if not last:
        headline = "First time you're talking"
    else:
        verb = "Last spoke" if stats["last_contact_kind"] == "call" else "Last message"
        headline = f"{verb} {ago(last)}"
    open_items: List[str] = []
    for item in [*(brief.get("open") or []), *[t["title"] for t in tasks]]:
        if item and not any(difflib.SequenceMatcher(None, item.lower(), o.lower()).ratio() > 0.75 for o in open_items):
            open_items.append(item)
    bday = upcoming_birthday(r["birthday"], within=7)
    lc = None
    if last_call:
        lc = {"id": last_call["id"], "title": last_call["title"] or "", "summary": (last_call["summary"] or "")[:280],
              "at": (parse_time(last_call["call_timestamp"]) or clock.now()).isoformat(),
              "ago": ago(parse_time(last_call["call_timestamp"]))}
    return {
        "person_id": person_id, "name": r["name"], "initials": initials(r["name"]),
        "relation": r["relation"] or "", "pinned": bool(r["pinned"]), "headline": headline,
        "points": (brief.get("points") or [])[:2], "open": open_items[:3], "last_call": lc,
        "pending_suggestions": len(sugg), "open_tasks": len(tasks),
        "birthday": ("today" if bday == 0 else f"in {bday} days") if bday is not None else None,
        "drifting": stats["drifting"], "typical_gap_days": stats["typical_gap_days"],
    }


def lookup(user: User, phone: str) -> Dict[str, Any]:
    """The incoming-call card for ``phone``: ``user``'s person with that number, else the
    name their Google contacts give it (``{found: False}`` when neither)."""
    linking.link_pending(user)
    pid = store.find_by_phone(user, phone)
    if pid:
        c = card(user, pid)
        with get_db_connection() as conn:
            r = store.row(conn, user, pid)
        hidden = bool(r and r["hidden"])
        # Numbers-only entries (no saved name) still get a card when there's history worth showing.
        if c and not (hidden and not c["last_call"]):
            return {"found": True, "card": c}
    known = contacts.caller_name(user, phone)  # None without the contacts kind
    return {"found": True, "card": contact_card(known)} if known else {"found": False}


def contact_card(name: str) -> Dict[str, Any]:
    """The card of a caller who is in the user's Google contacts but is none
    of their people yet: the shape of :func:`card` with an empty ``person_id``."""
    return {"person_id": "", "name": name, "initials": initials(name), "relation": "", "pinned": False,
            "headline": "In your contacts", "points": [], "open": [], "last_call": None,
            "pending_suggestions": 0, "open_tasks": 0, "birthday": None, "drifting": False,
            "typical_gap_days": None}


def chat_context(user: User, person_id: str) -> Dict[str, Any]:
    """The context of a chat opened from ``user``'s person (LookupError when not theirs)."""
    with get_db_connection() as conn:
        r = store.row(conn, user, person_id)
    if not r:
        raise LookupError("Person not found")
    c = card(user, person_id) or {}
    return {"kind": "person", "ref_id": person_id, "call_id": None, "emoji": "👤", "title": r["name"],
            "subtitle": " · ".join(x for x in (r["relation"], c.get("headline")) if x),
            "quick_prompts": ["What's open between us?", "What did we last talk about?",
                              "Draft a WhatsApp to them", "Remind me to call them"]}


def context_prompt(user: User, person_id: str) -> Optional[str]:
    """The system context of a chat opened from ``user``'s person (None when not theirs)."""
    p = get_person(user, person_id, schedule_brief=False)
    if not p:
        return None
    calls = [e for e in p["timeline"] if e["type"] == "call"][:6]
    msgs = [e for e in p["timeline"] if e["type"] == "messages"][:8]
    b = p.get("brief") or {}
    return (
        f"## Opened from a person: {p['name']}\n"
        f"Person id {p['id']}; relation: {p['relation'] or '-'}; phones: {', '.join(p['phones']) or '-'}; "
        f"birthday: {p['birthday'] or '-'}; notes: {p['notes'] or '-'}.\n"
        f"Last contact {p['stats']['last_contact_ago'] or 'unknown'}; typical gap {p['stats']['typical_gap_days'] or '?'} days.\n"
        f"Brief: {'; '.join(b.get('points') or []) or '-'}. Open: {'; '.join(b.get('open') or []) or '-'}.\n"
        "Recent calls:\n" + ("\n".join(f"- {c['at'][:10]}: {c['title']} — {c['summary'][:500]}" for c in calls) or "- none")
        + "\nRecent messages from them:\n"
        + ("\n".join(f"- {m['at'][:10]} ({m['count']}): {' / '.join(m['texts'])}" for m in msgs) or "- none")
        + "\nOpen tasks:\n" + ("\n".join(f"- {t['title']} ({t['id']})" for t in p["tasks"]) or "- none")
        + "\nFor full call transcripts use the call tools; to change their details use update_person; "
          "link new tasks with link_task_to_person."
    )
