"""What Dana is about to do, in words, for a tool call waiting for the user's OK.

One description per pending call, rendered by two clients:

- the Telegram bot's Approve / Deny message (``core/telegram_bot.py``, Markdown);
- the ``approval`` card of ``GET /api/now`` (``core/now.py``, plain text).

:func:`describe` returns plain strings (no Markdown, nothing escaped)::

    {"title": "Send a WhatsApp message to דן",       # the action, as a sentence
     "heading": "WhatsApp message", "emoji": "💬",     # Telegram's header
     "body_label": "Message", "body": "בדרך, עשר דקות",  # the content itself, if any
     "facts": [("When", "tomorrow 19:00"), …],        # other details, "label: value"
     "question": "Send this WhatsApp message?"}

A tool declared with ``@addon.tool(describe=fn)`` is described by its addon
(``fn(user, args)`` → ``core.addons.Approval``, the same shape; the current
user's, and the default card when ``fn`` raises); the others named
here are described below. Tools without a description of their own get a
generic one: the tool name as the action and every argument as "Label: value". Names are looked up where
that is a plain DB read (a reminder's title by its id, a WhatsApp number in
People); nothing here calls the network.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from core import clock, users
from core.text import escape_markdown
from addons.calendar import today as calendar_today
from addons.contacts import store as contacts_store
from core.addons import helpers, store as addon_store
from core.addons.addon import find_tool
from addons.gmail import compose as gmail_compose, content as gmail_content, seen as gmail_seen
from addons.gmail.config import ADDON as GMAIL
from addons.knowledge import entries as knowledge_entries, projects as knowledge_projects
from addons.people import store as people_store

logger = logging.getLogger(__name__)

Fact = Tuple[str, str]


def _s(value: Any, limit: int = 600) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    s = re.sub(r"[ \t]+", " ", str(value)).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _quote(text: Any, limit: int = 80) -> str:
    return f"“{_s(text, limit)}”"


def _when(value: Any) -> str:
    """ISO time → 'tomorrow 19:00' (the Now screen's wording); the raw value when unparsable."""
    raw = _s(value, 40)
    if not raw:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=clock.get_timezone())
        return helpers.when_phrase(dt)
    except Exception:
        return raw


def _span(start: Any, end: Any) -> str:
    a, b = _when(start), _when(end)
    if a and b:
        # Same day: "tomorrow 19:00–20:00".
        head, _, a_hm = a.rpartition(" ")
        b_head, _, b_hm = b.rpartition(" ")
        if head == b_head and a_hm and b_hm:
            return f"{a}–{b_hm}"
        return f"{a} – {b}"
    return a or b


def whatsapp_who(chat_id: Any) -> str:
    """A WhatsApp chat id as a person: a name as given, a known number as the
    person's name, else the number; groups by id → 'a WhatsApp group'."""
    raw = _s(chat_id, 120)
    if not raw:
        return "someone"
    if raw.endswith("@g.us"):
        return "a WhatsApp group"
    if "@" not in raw and not re.fullmatch(r"[\d\s+\-()]+", raw):
        return raw  # a contact or group name, resolved by the tool itself
    digits = re.sub(r"\D", "", raw.split("@")[0])
    try:
        user = users.current()
        pid = people_store.find_by_phone(user, digits)
        name = people_store.name_of(user, pid) if pid else None
        if name:
            return name
    except Exception:
        pass
    return f"+{digits}" if digits else raw


def _whatsapp_source(message_id: Any) -> str:
    """The chat a WhatsApp message id belongs to, as a person
    (``false_<chat id>_<id>``); empty when the id names no phone chat."""
    parts = _s(message_id, 200).split("_")
    if len(parts) < 3 or "@" not in parts[1] or parts[1].endswith("@lid"):
        return ""  # a LID is no phone number to show
    return whatsapp_who(parts[1])


def _event_title(event_id: Any) -> str:
    """From the current user's cache of today's events (the Now screen's) — no calendar call."""
    try:
        for e in calendar_today.cached_events(users.current()):
            if e.get("id") == event_id:
                return e.get("title") or ""
    except Exception:
        pass
    return ""


def _contact_name(contact_id: Any) -> str:
    """From the current user's copy of their Google contacts — no Google call."""
    try:
        user = users.current()
        contact = contacts_store.get(user, contact_id) if user is not None else None
        return (contact or {}).get("name") or ""
    except Exception:
        return ""


def _label(key: str) -> str:
    key = re.sub(r"_id$", "", key)
    return key.replace("_", " ").strip().capitalize() or key


def _generic(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    action = _s(tool_name, 80).replace("_", " ").strip() or "an action"
    facts = [(_label(k), _s(v, 300)) for k, v in args.items() if v not in (None, "", [], {})]
    return {"title": action[:1].upper() + action[1:], "heading": "Action", "emoji": "⚠️",
            "body_label": "", "body": "", "facts": facts, "question": "Go ahead?"}


def _d(title: str, heading: str, emoji: str, question: str, *, body_label: str = "", body: Any = "",
       facts: Optional[List[Tuple[str, Any]]] = None, fact_limit: int = 300) -> Dict[str, Any]:
    return {"title": title, "heading": heading, "emoji": emoji, "body_label": body_label if body else "",
            "body": _s(body, 1500), "facts": [(k, _s(v, fact_limit)) for k, v in (facts or []) if _s(v)],
            "question": question}


# Every recipient of an email is shown, never a cut list (at most MAX_RECIPIENTS addresses).
_RECIPIENTS_LIMIT = 4000


def _recipients(value: Any, field: str) -> str:
    """Email recipients as given to a tool: each one's real address with its
    name; the raw value on one line when the tool would refuse it."""
    if value in (None, "", []):
        return ""
    return gmail_compose.shown(value, field) or f"{gmail_content.one_line(value, 300)} (not a valid address)"


def _send_email(a: Dict[str, Any]) -> Dict[str, Any]:
    to = _recipients(a.get("to"), "to")
    return _d(f"Send an email to {to or 'nobody'}", "Email", "✉️", "Send this email?",
              body_label="Message", body=a.get("body"),
              facts=[("Subject", gmail_content.one_line(a.get("subject")) or "(no subject)"),
                     ("Cc", _recipients(a.get("cc"), "cc")), ("Bcc", _recipients(a.get("bcc"), "bcc"))],
              fact_limit=_RECIPIENTS_LIMIT)


def _reply_to_email(a: Dict[str, Any]) -> Dict[str, Any]:
    """Who the reply goes to, from the headers the user's Gmail tools read
    (addons/gmail/seen.py); the message's id when they are not known."""
    everyone = gmail_compose.flag(a.get("reply_all"))
    try:
        user = users.current()
        original = gmail_seen.get(user, a.get("message_id"))
        conn = addon_store.get(user, GMAIL) if user is not None and original else None
        reply = gmail_compose.reply(original, (conn.account if conn else "") or "", a.get("body"), everyone) \
            if original else None
    except Exception:
        reply = None
    if reply is None:
        return _d("Reply to an email" + (" (to everyone on it)" if everyone else ""), "Email reply", "✉️",
                  "Send this reply?", body_label="Reply", body=a.get("body"),
                  facts=[("Message", a.get("message_id"))])
    return _d(f"Reply to {gmail_content.show_all(reply.to, limit=len(reply.to))}", "Email reply", "✉️",
              "Send this reply?", body_label="Reply", body=a.get("body"),
              facts=[("Subject", reply.subject), ("Cc", gmail_content.show_all(reply.cc, limit=len(reply.cc)))],
              fact_limit=_RECIPIENTS_LIMIT)


def describe(tool_name: str, tool_args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The pending call in words (see the module docstring for the shape)."""
    a = tool_args if isinstance(tool_args, dict) else {}
    t = tool_name or ""
    tool = find_tool(t)
    if tool is not None and tool.describe is not None:
        try:
            return tool.describe(users.current(), a).to_json()  # the addon's own words (@addon.tool(describe=...))
        except Exception:
            logger.warning(f"describe= of {t} failed; showing the default card", exc_info=True)
            return _generic(t, a)
    if t == "send_whatsapp_message":
        who = whatsapp_who(a.get("chat_id"))
        return _d(f"Send a WhatsApp message to {who}", "WhatsApp message", "💬", "Send this WhatsApp message?",
                  body_label="Message", body=a.get("text"))
    if t == "send_whatsapp_file":
        who = whatsapp_who(a.get("chat_id"))
        return _d(f"Send a file on WhatsApp to {who}", "WhatsApp file", "💬", "Send this file?",
                  body_label="Caption", body=a.get("caption"),
                  facts=[("File", a.get("filename") or a.get("file_url")), ("Type", a.get("mimetype"))])
    if t == "forward_whatsapp_message":
        who = whatsapp_who(a.get("chat_id"))
        return _d(f"Forward a WhatsApp message to {who}", "Forward on WhatsApp", "💬", "Forward this message?",
                  body_label="What", body=a.get("what"),
                  facts=[("From", _whatsapp_source(a.get("message_id")))])
    if t == "edit_whatsapp_message":
        who = whatsapp_who(a.get("chat_id"))
        return _d(f"Edit your WhatsApp message to {who}", "Edit WhatsApp message", "💬", "Edit this message?",
                  body_label="New text", body=a.get("new_text"))
    if t == "delete_whatsapp_message":
        who = whatsapp_who(a.get("chat_id"))
        return _d(f"Delete your WhatsApp message to {who}", "Delete WhatsApp message", "💬",
                  "Delete this message for everyone?")
    if t == "send_email":
        return _send_email(a)
    if t == "reply_to_email":
        return _reply_to_email(a)
    if t in ("add_event", "update_event"):
        name = a.get("summary") or (_event_title(a.get("event_id")) if t == "update_event" else "") or ""
        invites = _s(a.get("attendees"), 300)
        if t == "add_event":
            title = f"Invite {invites} to {_quote(name or 'an event')}" if invites else \
                f"Add {_quote(name or 'an event')} to the calendar"
        else:
            title = f"Change {_quote(name)} in the calendar" if name else "Change a calendar event"
            if invites:
                title += f" and invite {invites}"
        return _d(title, "Calendar event" if t == "add_event" else "Change calendar event", "📅",
                  "Add this event?" if t == "add_event" else "Make this change?",
                  body_label="Description", body=a.get("description"),
                  facts=[("When", _span(a.get("start_time"), a.get("end_time"))), ("Where", a.get("location")),
                         ("New title", a.get("summary") if t == "update_event" else "")])
    if t == "delete_event":
        name = _event_title(a.get("event_id"))
        return _d(f"Delete {_quote(name)} from the calendar" if name else "Delete an event from the calendar",
                  "Delete calendar event", "❌", "Delete this event?",
                  facts=[] if name else [("Event", a.get("event_id"))])
    if t == "add_contact":
        return _d(f"Add {_s(a.get('name'), 80) or 'a contact'} to Google Contacts", "New contact", "👤",
                  "Add this contact?",
                  facts=[("Phone", a.get("phone")), ("Email", a.get("email")), ("Birthday", a.get("birthday")),
                         ("Organisation", a.get("organisation"))])
    if t == "update_contact":
        name = _contact_name(a.get("contact_id"))
        return _d(f"Change {name} in Google Contacts" if name else "Change a Google contact", "Change contact",
                  "👤", "Make this change?",
                  facts=[("Contact", "" if name else a.get("contact_id")), ("New name", a.get("name")),
                         ("Add number", a.get("add_phone")), ("Remove number", a.get("remove_phone")),
                         ("Add email", a.get("add_email")), ("Remove email", a.get("remove_email")),
                         ("Birthday", "remove" if a.get("birthday") == "" else a.get("birthday")),
                         ("Organisation", "remove" if a.get("organisation") == "" else a.get("organisation"))])
    if t == "delete_project":
        project = knowledge_projects.get_project(users.current(), _s(a.get("project_id"), 200))
        name = (project.get("title") or "") if project else ""  # only a project the current user can see
        return _d(f"Delete the project {_quote(name)} and everything in it" if name else "Delete a project",
                  "Delete project", "❌", "Delete this project?")
    if t == "delete_knowledge_item":
        entry = knowledge_entries.entry_row(users.current(), _s(a.get("item_id"), 200))
        name = (entry.get("title") or "") if entry else ""  # only an entry the current user can see
        return _d(f"Delete the note {_quote(name)}" if name else "Delete a note", "Delete note", "❌",
                  "Delete this note?")
    if t == "share_item":
        what = str(a.get("item_type") or "item").replace("_", " ")
        level = str(a.get("level") or "").strip().lower()
        if level == "none":
            return _d(f"Stop sharing the {what} {_quote(a.get('item'))} with {_s(a.get('user'), 80)}",
                      "Stop sharing", "🔒", "Stop sharing it?")
        return _d(f"Share the {what} {_quote(a.get('item'))} with {_s(a.get('user'), 80)}", "Share", "🤝",
                  "Share it?", facts=[("They can", {"read": "see it", "edit": "see and change it"}.get(level, level))])
    if t == "set_device_state":
        return _d(f"Set {a.get('entity_id') or 'a device'} to {a.get('state') or '?'}", "Home", "🏠", "Go ahead?",
                  facts=[("Attributes", a.get("attributes"))])
    # Tools that ask only in a turn with the shared phone screen (acts_outside).
    if t == "call_ha_service":
        what = f"{a.get('domain') or '?'}.{a.get('service') or '?'}"
        return _d(f"Home: {what} on {a.get('entity_id') or 'a device'}", "Home", "🏠", "Go ahead?",
                  facts=[("Data", a.get("service_data"))])
    if t == "control_android_tv":
        detail = a.get("app_id") or a.get("channel") or a.get("query") or a.get("video_id") \
            or a.get("title_id") or a.get("command") or ""
        return _d(f"TV: {str(a.get('action') or 'command').replace('_', ' ')}"
                  + (f" {_quote(detail)}" if detail else ""), "TV", "📺", "Go ahead?",
                  facts=[("Device", a.get("entity_id"))])
    if t == "run_kubectl":
        return _d("Run kubectl on the cluster", "Cluster", "⚙️", "Run this command?",
                  body_label="Command", body=a.get("command"), facts=[("Namespace", a.get("namespace"))])
    if t == "run_python":
        return _d("Run Python code", "Code", "🐍", "Run this code?", body_label="Code", body=a.get("code"))
    if t == "remember_info":
        return _d(f"Remember {_quote(a.get('key'))}", "Memory", "🧠", "Save this?", body_label="Value",
                  body=a.get("value"))
    if t == "forget_info":
        return _d(f"Forget {_quote(a.get('key'))}", "Memory", "🧠", "Forget this?")
    return _generic(t, a)


def plain(desc: Dict[str, Any]) -> str:
    """One plain-text rendering (logs, clients without blocks)."""
    lines = [desc["title"]]
    if desc.get("body"):
        lines.append(f"{desc['body_label']}: {desc['body']}" if desc.get("body_label") else desc["body"])
    lines += [f"{k}: {v}" for k, v in desc.get("facts") or []]
    return "\n".join(lines)


def telegram(tool_name: str, tool_args: Optional[Dict[str, Any]]) -> str:
    """The Telegram Approve / Deny message (legacy Markdown, values escaped)."""
    d = describe(tool_name, tool_args)
    msg = f"{d['emoji']} *{escape_markdown(d['heading'])}*\n\n{escape_markdown(d['title'])}\n"
    if d["body"]:
        msg += f"• *{escape_markdown(d['body_label'] or 'Content')}:* {escape_markdown(d['body'])}\n"
    for k, v in d["facts"]:
        msg += f"• *{escape_markdown(k)}:* {escape_markdown(v)}\n"
    return msg + f"\n{escape_markdown(d['question'])}"
