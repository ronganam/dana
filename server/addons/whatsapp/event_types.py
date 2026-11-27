"""The event the ``whatsapp`` addon emits for the event catalog (../docs/addons.md,
"Events"): ``whatsapp.message.received``, once per new message WAHA's webhook
delivers for a user's connection (:func:`announce_message`, called by
:mod:`.events` after routing and the per-connection dedupe of redeliveries).

The data is other people's text: the watches engine frames it as untrusted;
nothing here puts it in a prompt. Dana keeps no copy of WhatsApp messages
(tools read them from WAHA), so the event has no history.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

from core import events
from core.addons import EventType
from core.users import User
from addons.whatsapp.client import get_message_sender_name

EVENT = "whatsapp.message.received"
GROUP_SUFFIX = "@g.us"


def chat_id(payload: Mapping[str, Any]) -> str:
    """The chat a message is in: the recipient for one the user sent, else its sender (a group's id in a group)."""
    return str((payload.get("to") if payload.get("fromMe") else payload.get("from")) or "").strip()


def _name(jid: str, names: Mapping[str, str]) -> str:
    return names.get(jid) or names.get(jid.split("@")[0]) or ""


def media_type(payload: Mapping[str, Any]) -> str:
    """voice | audio | image | video | sticker | document, or "" without media."""
    if not payload.get("hasMedia"):
        return ""
    kinds = ((payload.get("_data") or {}).get("message") or {})
    if isinstance(kinds, Mapping) and "stickerMessage" in kinds:
        return "sticker"
    mimetype = str((payload.get("media") or {}).get("mimetype") or "").lower()
    major = mimetype.split("/", 1)[0]
    if major == "audio":
        audio = kinds.get("audioMessage") if isinstance(kinds, Mapping) else None
        return "audio" if isinstance(audio, Mapping) and audio.get("ptt") is False else "voice"
    return major if major in ("image", "video") else "document"


def message_data(payload: Mapping[str, Any], names: Mapping[str, str]) -> Dict[str, Any]:
    """``whatsapp.message.received``'s data of one WAHA ``message`` payload;
    ``names``: the connection's chat id → name map (``client.get_chat_id_to_name_map``)."""
    jid = chat_id(payload)
    is_group = jid.endswith(GROUP_SUFFIX)
    from_me = bool(payload.get("fromMe"))
    sender = get_message_sender_name(dict(payload), dict(names)) or ""
    if is_group:
        chat = _name(jid, names) or jid.split("@")[0]
    else:
        chat = (_name(jid, names) or jid.split("@")[0]) if from_me else (sender or _name(jid, names))
    mid = payload.get("id")
    if isinstance(mid, Mapping):  # some engines nest it
        mid = mid.get("_serialized") or mid.get("id")
    return {"message_id": str(mid or ""), "chat_id": jid, "chat": chat, "sender": sender, "is_group": is_group,
            "from_me": from_me, "text": str(payload.get("body") or ""),
            "has_media": bool(payload.get("hasMedia")), "media_type": media_type(payload)}


def announce_message(user: User, payload: Mapping[str, Any], names: Mapping[str, str]) -> Dict[str, Any]:
    """Emit ``whatsapp.message.received`` for ``user``'s new message; returns its data."""
    data = message_data(payload, names)
    events.emit(EVENT, user, **data)
    return data


WHATSAPP_EVENTS = (
    EventType(EVENT, "A new WhatsApp message in one of the user's chats or groups (as WAHA delivers it).",
              {"message_id": "the message's id (forward_whatsapp_message / read_whatsapp_file take it)",
               "chat_id": "the chat's id (e.g. 15550100301@c.us, 120363…@g.us, or …@lid): pass it as chat_id to "
                          "send_whatsapp_message / read_chat_messages to answer in this chat",
               "chat": "the chat's name: the contact for a 1:1 chat, the group's name in a group",
               "sender": "who wrote it (a display name; \"Me\" for the user's own)",
               "is_group": "true for a group chat",
               "from_me": "true when the user sent it (from another device); watches usually want "
                          "from_me eq false",
               "text": "its text, or a file's caption (empty for a voice note or a file without one)",
               "has_media": "true when it carries a file, photo, video or voice note",
               "media_type": "voice | audio | image | video | sticker | document, empty without media"}),
)
