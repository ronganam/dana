"""The phone's part of context chats ("Ask the assistant" from a suggestion,
a call or a notification): the attachment card and the system context,
rebuilt from the DB on every turn, both for the chat's user and only from
their own suggestions, calls and notifications (core/context_chats.py
dispatches by kind)."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from core import user_profile
from core.db import get_db_connection
from core.users import User
from addons.phone import suggestions as phone_suggestions
from addons.phone.calls import store as calls_store
from addons.phone.calls.labels import label_text
from addons.phone.notifications import store as notification_store
from addons.tasks import service as tasks_service


def describe_triggers(triggers: Dict[str, Any]) -> List[str]:
    out = []
    if not isinstance(triggers, dict):
        return out
    if triggers.get("time"):
        out.append(f"⏰ {triggers['time']}")
    loc = triggers.get("location")
    if isinstance(loc, dict) and loc.get("location"):
        out.append(f"📍 {'arrive at' if loc.get('event') != 'leave' else 'leave'} {loc['location']}")
    if triggers.get("car"):
        out.append(f"🚗 car {triggers['car']}")
    if triggers.get("wake"):
        out.append("☀️ on wake")
    return out


def suggestion_line(s: Dict[str, Any]) -> str:
    """A suggestion in one line (prompts and tool output)."""
    p = s["payload"]
    if s["kind"] == "event":
        return f"event “{p.get('title')}” {p.get('start')} → {p.get('end')}" + (f" @ {p['location']}" if p.get("location") else "")
    if s["kind"] == "alert":
        return f"security alert: {p.get('text')}"
    bits = [f"task “{p.get('title')}”"]
    if p.get("due_date"):
        bits.append(f"due {p['due_date']}")
    bits.append(f"priority {p.get('priority', 'medium')}")
    if p.get("group"):
        bits.append(f"group {p['group']}")
    bits += describe_triggers(p.get("triggers") or {})
    return ", ".join(bits)


def call_block(user: Optional[User], call_id: str, transcript_chars: int = 6000) -> Tuple[Optional[Dict[str, Any]], str]:
    """``user``'s call as prompt text (``(None, "")`` when it is not theirs)."""
    row = calls_store.get_row(user, call_id)
    if not row:
        return None, ""
    c = calls_store.call_to_api(user, row)
    facts = "\n".join(f"  - {f['text']}" for f in c["key_facts"]) or "  - (none)"
    labels = ", ".join(label_text(user, l) for l in c["labels"]) or "(none)"
    transcript = (row["transcript"] or "")[:transcript_chars]
    block = (
        f"Call {c['id']} with {c['contact']} on {c['call_timestamp']}"
        f"{' (' + c['direction'] + ')' if c.get('direction') else ''}\n"
        f"Title: {c['title']}\nLabels: {labels}\nSummary:\n{c['summary']}\nKey facts:\n{facts}\n"
        f"Transcript (may be cut):\n{transcript}"
    )
    return c, block


def _notification_block(user: Optional[User], notification_id: str) -> str:
    """``user``'s notification as prompt text."""
    with get_db_connection() as conn:
        n = conn.execute("SELECT * FROM notifications WHERE id = ? AND user_id = ?",
                         (notification_id, user.id if user is not None else "")).fetchone()
    if not n:
        return "(the notification was deleted)"
    return (f"Notification {n['id']} from the app {n['app_name']} at {n['posted_at']}"
            f"{' in group ' + n['conversation'] if n['is_group'] and n['conversation'] else ''}\n"
            f"Title: {n['title'] or ''}\nSender: {n['sender'] or ''}\nText:\n{(n['text'] or '')[:4000]}\n"
            f"Category: {n['category'] or 'untriaged'}, priority: {n['priority'] or 'unknown'}")


# --- Cards ------------------------------------------------------------------------------

def suggestion_card(user: Optional[User], ref_id: str) -> Dict[str, Any]:
    s = phone_suggestions.get_suggestion(user, ref_id)
    if not s:
        raise LookupError("Suggestion not found")
    p = s["payload"]
    emoji = {"event": "📅", "alert": "🔐"}.get(s["kind"], "✅")
    if s.get("source_type") == "notification":
        subtitle_bits = [f"From a {(s.get('notification') or {}).get('app_name') or 'phone'} notification"]
    else:
        contact = (s.get("call") or {}).get("contact") or "the call"
        subtitle_bits = [f"From call with {contact}"]
    if s["kind"] == "task":
        if p.get("due_date"):
            subtitle_bits.append(f"due {p['due_date']}")
        subtitle_bits += describe_triggers(p.get("triggers") or {})
    elif s["kind"] == "event":
        subtitle_bits.append(str(p.get("start") or "").replace("T", " ")[:16])
    prompts = {
        "task": ["Create it as is", "Change the reminder time", "Add a location trigger", "Make it high priority", "Dismiss it"],
        "event": ["Add it to my calendar", "Move it by an hour", "Make it a task instead", "Dismiss it"],
        "alert": ["What should I do now?", "Make a task to secure my accounts", "Dismiss it"],
    }[s["kind"]]
    return {
        "kind": "suggestion", "ref_id": s["id"], "call_id": s["call_id"], "emoji": emoji,
        "title": p.get("title") or "Suggestion", "subtitle": " · ".join(subtitle_bits),
        "quick_prompts": prompts, "status": s["status"],
    }


def call_card(user: Optional[User], ref_id: str) -> Dict[str, Any]:
    row = calls_store.get_row(user, ref_id)
    if not row:
        raise LookupError("Call not found")
    c = calls_store.call_to_api(user, row, include_transcript=False)
    emoji = c["label_details"][0]["emoji"] if c["label_details"] else "📞"
    return {
        "kind": "call", "ref_id": c["id"], "call_id": c["id"], "emoji": emoji,
        "title": c["title"] or f"Call with {c['contact']}",
        "subtitle": f"Call with {c['contact']} · {str(c['call_timestamp'] or '')[:16].replace('T', ' ')}",
        "quick_prompts": ["What did we agree on?", "Make tasks from this call", "Draft a follow-up WhatsApp", "Add the key dates to my calendar"],
    }


def notification_card(user: Optional[User], ref_id: str) -> Dict[str, Any]:
    n = notification_store.get_notification(user, ref_id) if user is not None else None
    if not n:
        raise LookupError("Notification not found")
    return {
        "kind": "notification", "ref_id": n["id"], "call_id": None, "emoji": n["category_emoji"],
        "title": n["title"] or n["app_name"] or "Notification",
        "subtitle": f"{n['app_name']} · {str(n['posted_at'] or '')[:16].replace('T', ' ')}",
        "quick_prompts": ["What should I do about this?", "Make a task from it", "Draft a reply", "Remind me later"],
    }


# --- Prompts ----------------------------------------------------------------------------

def suggestion_prompt(user: Optional[User], ref: str) -> Optional[str]:
    s = phone_suggestions.get_suggestion(user, ref)
    if not s:
        return None
    who = user_profile.for_user(user)
    if s.get("source_type") == "notification":
        source_title, source_block = "Opened from a notification suggestion", _notification_block(user, s["source_id"])
    else:
        source_title, (_, source_block) = "Opened from a call suggestion", call_block(user, s["call_id"])
    state_line = {"pending": "still pending", "accepted": f"ALREADY ACCEPTED ({s.get('result_ref')})",
                  "dismissed": "dismissed"}.get(s["status"], s["status"])
    return (
        f"## {source_title}\n"
        f"{who.first_name} opened this chat from suggestion {s['id']} ({state_line}): {suggestion_line(s)}.\n"
        f"Full suggestion JSON: {json.dumps(s['payload'], ensure_ascii=False)}\n"
        f"Help {who.obj} adjust it. When {who.pronoun} {who.agree('asks', 'ask')} to create/add it (or "
        f"{who.agree('approves', 'approve')} your proposed version), call "
        "accept_call_suggestion with this suggestion_id and only the fields that change (title, due_date, "
        "priority, group, time_trigger, location, location_event, car_trigger, wake_trigger, description, "
        f"start_time, end_time). Use get_ha_zones before setting a location. If {who.pronoun} "
        f"{who.agree('wants', 'want')} it gone, call "
        "dismiss_call_suggestion. Don't ask for details you already have; keep replies short.\n\n"
        f"### Source\n{source_block}"
    )


def notification_prompt(user: Optional[User], ref: str) -> str:
    who = user_profile.for_user(user)
    pending = phone_suggestions.list_suggestions(user, "pending", source="notification", source_id=ref)
    sugg = "\n".join(f"- {s['id']}: {suggestion_line(s)}" for s in pending) or "- (none)"
    return (
        "## Opened from a phone notification\n"
        f"{who.first_name} opened this chat from notification {ref}. Answer about it directly. To take a pending "
        "suggestion use accept_call_suggestion; for a new task use add_task.\n"
        f"Pending suggestions:\n{sugg}\n\n### The notification\n{_notification_block(user, ref)}"
    )


def call_prompt(user: Optional[User], ref: str) -> Optional[str]:
    c, block = call_block(user, ref, transcript_chars=20000)
    if not c:
        return None
    who = user_profile.for_user(user)
    pending = phone_suggestions.list_suggestions(user, "pending", call_id=ref)
    sugg = "\n".join(f"- {s['id']}: {suggestion_line(s)}" for s in pending) or "- (none)"
    tasks = "\n".join(f"- {t['title']} ({t['status']})"
                      for t in tasks_service.tasks_for_call(user, ref)) or "- (none)"
    return (
        "## Opened from a phone call\n"
        f"{who.first_name} opened this chat from call {ref}. Answer about it directly. When you create tasks for "
        f"it, pass call_id='{ref}' to add_task so they're linked; to take a pending suggestion use "
        "accept_call_suggestion.\n"
        f"Pending suggestions:\n{sugg}\nTasks already linked:\n{tasks}\n\n### The call\n{block}"
    )
