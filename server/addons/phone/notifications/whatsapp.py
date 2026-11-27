"""WhatsApp messages from WAHA as the source of WhatsApp suggestions (docs/notifications.md,
"WhatsApp through WAHA").

The phone's WhatsApp notification is a poor source for suggestions: Android
folds a chat into "Dor: …" / "3 new messages", cuts long text, never shows the
message being replied to, and says nothing about what was said before. When the
user's WhatsApp is connected through WAHA (:func:`covered`), the
``whatsapp.message.received`` events of the ``whatsapp`` addon become the
source instead:

* :func:`on_message` collects a chat's incoming messages into a **burst**
  (people write "hey" / "can you…" / "tomorrow at 5" as three messages) and
  waits until the chat has been quiet for ``WHATSAPP_SUGGEST_QUIET_SECONDS``
  (or a burst is ``WHATSAPP_SUGGEST_MAX_HOLD_SECONDS`` old);
* :func:`flush` stores the burst as one notification row (``source='whatsapp'``,
  package ``com.whatsapp``, so the user's WhatsApp rules apply): the new
  messages, with the text they quote and voice notes' transcripts, then the
  chat's last messages read from WAHA as context (so a request the user
  already answered or did is not suggested again). The row is archived at once
  (``removed_reason='whatsapp'``) and never listed in the Inbox; it goes
  through the same triage and extraction as any notification, and its
  suggestions are ordinary notification suggestions. It is never escalated:
  the phone's own notification still does that.

While WAHA covers the user, their phone's WhatsApp notifications stay in the
Inbox (triaged, with their buttons) but no longer make suggestions
(:func:`suppresses`), so one message never makes two.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core import clock, user_profile
from core.addons import store
from core.db import get_db_connection
from core.users import User

logger = logging.getLogger(__name__)

SOURCE = "whatsapp"
# The phone's WhatsApp apps: their notifications stop making suggestions while WAHA covers the user.
PACKAGES = ("com.whatsapp", "com.whatsapp.w4b")
PACKAGE = "com.whatsapp"
APP_NAME = "WhatsApp"
ADDON = "whatsapp"
WEBHOOK_SECRET_ENV = "WAHA_WEBHOOK_SECRET"
MAX_BURST = 20
NEW_LIMIT = 3000
CONTEXT_LIMIT = 2500

# (user id, chat id) → the burst waiting: {"messages": [...], "first": monotonic time, "last": message key}
_bursts: Dict[Tuple[str, str], Dict[str, Any]] = {}


def _seconds(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, "") or default))
    except ValueError:
        return default


def quiet_seconds() -> float:
    """How long a chat stays quiet before its burst is read (``WHATSAPP_SUGGEST_QUIET_SECONDS``, 60)."""
    return _seconds("WHATSAPP_SUGGEST_QUIET_SECONDS", 60)


def max_hold_seconds() -> float:
    """The longest a burst waits in a chat that keeps going (``WHATSAPP_SUGGEST_MAX_HOLD_SECONDS``, 300)."""
    return _seconds("WHATSAPP_SUGGEST_MAX_HOLD_SECONDS", 300)


def history_limit() -> int:
    """How many of the chat's last messages are read as context (``WHATSAPP_SUGGEST_HISTORY``, 12; 0: none)."""
    return int(_seconds("WHATSAPP_SUGGEST_HISTORY", 12))


def covered(user: User) -> bool:
    """Whether ``user``'s WhatsApp messages reach Dana through WAHA: the ``whatsapp`` addon
    connected with its account learned, not waiting to be reconnected, and the server
    accepting WAHA's signed webhooks (``WAHA_WEBHOOK_SECRET``)."""
    if not os.getenv(WEBHOOK_SECRET_ENV, "").strip():
        return False
    try:
        conn = store.get(user, ADDON)
        return bool(conn is not None and conn.account and not store.reconnects(user, ADDON))
    except Exception as e:
        logger.warning(f"Checking WhatsApp for user {user.id} failed: {e}")
        return False


def suppresses(user: User, n: Mapping[str, Any]) -> bool:
    """Whether the phone notification ``n`` should make no suggestions because WAHA's copy does."""
    return (n.get("source") or "phone") == "phone" and n.get("package") in PACKAGES and covered(user)


def _has_content(m: Mapping[str, Any]) -> bool:
    return bool(str(m.get("text") or "").strip() or str(m.get("transcript") or "").strip() or m.get("has_media"))


async def on_message(user: User, data: Dict[str, Any]) -> None:
    """The ``whatsapp.message.received`` handler: add an incoming message to its chat's
    burst, then, if no newer message came while the chat was quiet (or the burst is old
    enough), store the burst (:func:`flush`)."""
    if data.get("from_me") or not _has_content(data):
        return
    chat_id = str(data.get("chat_id") or "").strip()
    if not chat_id or not covered(user):
        return
    key = (user.id, chat_id)
    mark = str(data.get("message_id") or "") or uuid.uuid4().hex
    burst = _bursts.setdefault(key, {"messages": [], "first": time.monotonic()})
    if len(burst["messages"]) < MAX_BURST:
        burst["messages"].append(dict(data))
    burst["last"] = mark
    wait = quiet_seconds()
    if wait:
        await asyncio.sleep(wait)
    burst = _bursts.get(key)
    if burst is None:
        return  # already stored
    if burst.get("last") != mark and time.monotonic() - burst["first"] < max_hold_seconds():
        return  # a newer message's handler stores it
    _bursts.pop(key, None)
    await flush(user, chat_id, burst["messages"])


def _line(m: Mapping[str, Any], group: bool) -> str:
    who = str(m.get("sender") or "").strip()
    head = f"{who}" if group and who else ""
    quoted = str(m.get("reply_to") or "").strip()
    if quoted:
        head += (" " if head else "") + f'(replying to "{quoted[:300]}")'
    said = str(m.get("transcript") or "").strip()
    text = str(m.get("text") or "").strip()
    media = str(m.get("media_type") or "")
    if said:
        body = f'[voice note]: "{said}"'
    elif media in ("voice", "audio"):
        body = "[voice note, not transcribed]" + (f" {text}" if text else "")
    elif media:
        body = f"[{media}]" + (f" {text}" if text else "")
    else:
        body = text
    return f"{head}: {body}" if head else body


async def _history(user: User, chat_id: str, skip: set) -> List[Dict[str, Any]]:
    """The chat's last messages from the user's WAHA, without the burst's own; [] on any error."""
    limit = history_limit()
    if not limit:
        return []
    try:
        from addons.whatsapp import messages as wa_messages  # the WAHA side; imported only when covered
        conn = store.get(user, ADDON)
        if conn is None:
            return []
        rows = await wa_messages.recent(conn, chat_id, limit + len(skip))
    except Exception as e:
        logger.warning(f"Reading WhatsApp chat history for user {user.id} failed: {e}")
        return []
    return [r for r in rows if r.get("id") not in skip][-limit:]


def compose(user: User, burst: List[Mapping[str, Any]], history: List[Mapping[str, Any]]) -> str:
    """The row's text: the new messages, then the earlier ones as context."""
    first = burst[0]
    group = bool(first.get("is_group"))
    new = "\n".join(_line(m, group) for m in burst)[:NEW_LIMIT]
    if not history:
        return new
    me = user_profile.for_user(user).first_name or "Me"
    lines = []
    for h in history:
        who = f"{me} (the user)" if h.get("from_me") else (h.get("sender") or "them")
        lines.append(f"[{str(h.get('time') or '')[-5:]}] {who}: {str(h.get('text') or '')[:400]}")
    context = "\n".join(lines)
    if len(context) > CONTEXT_LIMIT:
        context = "…" + context[-CONTEXT_LIMIT:]
    return f"{new}\n\n--- Earlier in this chat (context only, oldest first) ---\n{context}"


async def flush(user: User, chat_id: str, burst: List[Mapping[str, Any]]) -> Optional[str]:
    """Store ``user``'s burst of messages in ``chat_id`` as a WhatsApp notification row and
    queue it for triage. Returns the row's id (None when nothing was stored)."""
    burst = [m for m in burst if _has_content(m)]
    if not burst or not covered(user):
        return None
    from addons.phone.notifications import processing
    last = burst[-1]
    group = bool(last.get("is_group"))
    ids = {str(m.get("message_id") or "") for m in burst} - {""}
    text = compose(user, burst, await _history(user, chat_id, ids))
    chat = str(last.get("chat") or "").strip()
    sender = str(last.get("sender") or "").strip()
    digest = hashlib.sha256("\x1f".join([SOURCE, chat_id, *sorted(ids)] if ids else [SOURCE, chat_id, text])
                            .encode("utf-8")).hexdigest()
    nid = str(uuid.uuid4())
    now = clock.now().isoformat(timespec="seconds")
    with get_db_connection() as conn:
        if conn.execute("SELECT 1 FROM notifications WHERE user_id = ? AND content_hash = ?",
                        (user.id, digest)).fetchone():
            return None
        conn.execute(
            """INSERT INTO notifications (id, user_id, device_key, package, app_name, title, text, sub_text, sender,
                   conversation, is_group, posted_at, content_hash, status, removed_reason, removed_at, source)
               VALUES (?, ?, NULL, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, 'archived', 'whatsapp', ?, ?)""",
            (nid, user.id, PACKAGE, APP_NAME, chat or sender, text, sender,
             chat if group else "", 1 if group else 0, now, digest, now, SOURCE))
        conn.commit()
    logger.info(f"WhatsApp: {len(burst)} message(s) in one chat stored as notification {nid} for user {user.id}")
    processing.queue(user, [nid])
    return nid
