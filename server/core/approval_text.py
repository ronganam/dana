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

Each tool declared with ``@addon.tool(describe=fn)`` provides its own wording
(``fn(user, args)`` → :class:`core.addons.Approval`, the same shape; called with
the current user, falling back to the default card when ``fn`` raises). Tools
without a description of their own get a generic one: the tool name as the
action and every argument as "Label: value". Nothing here calls the network.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

from core import users
from core.addons.addon import find_tool
from core.addons.hooks import clean
from core.text import escape_markdown

logger = logging.getLogger(__name__)

Fact = Tuple[str, str]


def _label(key: str) -> str:
    key = re.sub(r"_id$", "", key)
    return key.replace("_", " ").strip().capitalize() or key


def _generic(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    action = clean(tool_name, 80).replace("_", " ").strip() or "an action"
    facts = [(_label(k), clean(v, 300)) for k, v in args.items() if v not in (None, "", [], {})]
    return {"title": action[:1].upper() + action[1:], "heading": "Action", "emoji": "⚠️",
            "body_label": "", "body": "", "facts": facts, "question": "Go ahead?"}


def describe(tool_name: str, tool_args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The pending call in words (see the module docstring for the shape)."""
    a = tool_args if isinstance(tool_args, dict) else {}
    t = tool_name or ""
    tool = find_tool(t)
    if tool is not None and tool.describe is not None:
        try:
            return tool.describe(users.current(), a).to_json()
        except Exception:
            logger.warning(f"describe= of {t} failed; showing the default card", exc_info=True)
            return _generic(t, a)
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
