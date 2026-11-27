"""One WhatsApp message as a line of a tool result, shared by
``read_chat_messages`` and ``get_latest_messages``.

Every line ends with the message's id (`id: …`), the handle the model passes
to ``forward_whatsapp_message``, ``edit_whatsapp_message``,
``delete_whatsapp_message`` and ``send_whatsapp_message(reply_to=)``. A
message with media says what it is (voice note transcribed, any other file by
name and type: ``read_whatsapp_file`` reads it) and keeps its caption.

Only voice notes are downloaded by WAHA (:func:`message_query`): any other file
is listed by the name and type WAHA reports without downloading it, so reading
a chat with photos, PDFs or videos stays cheap."""

import logging
from collections import OrderedDict
from typing import Any, Dict, List, Tuple

import httpx

from core import clock, llm
from core.addons import Connection
from addons.whatsapp import client as waha

logger = logging.getLogger(__name__)

# The media WAHA downloads when listing messages (prefixes of the mimetype);
# every other file keeps its name and type, without a URL.
DOWNLOADED_MEDIA = ("audio/",)
# Voice notes transcribed lately ((user id, message id) → transcript): reading a chat again
# does not transcribe them again.
_transcripts: "OrderedDict[Tuple[str, str], str]" = OrderedDict()


def remember_transcript(conn: Connection, message_id: str, transcript: str) -> None:
    """Keep a voice note's transcript (made when it arrived), so reading the chat does not transcribe it again."""
    if not message_id or not transcript:
        return
    _transcripts[(conn.user.id if conn.user else "", message_id)] = transcript
    while len(_transcripts) > 256:
        _transcripts.popitem(last=False)


def message_query(limit: int) -> Dict[str, Any]:
    """Query parameters for WAHA's chat messages endpoint."""
    return {"limit": limit, "downloadMedia": "true", "downloadMediaMimetypes": list(DOWNLOADED_MEDIA)}


def _time(msg: Dict[str, Any]) -> str:
    ts = msg.get("timestamp")
    if not ts:
        return ""
    try:
        return clock.from_timestamp(ts).strftime("[%Y-%m-%d %H:%M:%S] ")
    except Exception:
        return ""


def _kind_of(mimetype: str) -> str:
    major = mimetype.split("/", 1)[0]
    return {"video": "Video", "audio": "Audio", "image": "Image"}.get(major, "File")


async def _media(conn: Connection, msg: Dict[str, Any]) -> str:
    """What a media message carries, in words (downloads voice notes to
    transcribe them)."""
    media = msg.get("media") or {}
    mimetype = str(media.get("mimetype") or "")
    filename = str(media.get("filename") or "").strip()
    url = media.get("url")
    if mimetype.startswith("audio/") and url:
        key = (conn.user.id if conn.user else "", str(msg.get("id") or url))
        if key in _transcripts:
            return f'[Voice message transcript]: "{_transcripts[key]}"'
        try:
            audio = await waha.download_media_bytes(conn, url)
            if audio:
                transcript = await llm.transcribe(audio, mimetype, source="whatsapp")
                if transcript:
                    _transcripts[key] = transcript
                    while len(_transcripts) > 256:
                        _transcripts.popitem(last=False)
                    return f'[Voice message transcript]: "{transcript}"'
        except llm.TranscriptionUnavailable:
            return f"[Voice message ({mimetype}); not transcribed: the server needs a transcription model]"
        except Exception as e:
            logger.error(f"Failed transcribing a WhatsApp voice message: {e}")
        return f"[Voice message ({mimetype})]"
    if not mimetype:
        return f"[File: {filename}]" if filename else "[Media]"
    label = _kind_of(mimetype)
    return f"[{label}: {filename} ({mimetype})]" if filename else f"[{label} ({mimetype})]"


async def format_message(conn: Connection, msg: Dict[str, Any], names: Dict[str, str]) -> str:
    """``[time] **sender**: content (id: `…`)`` for one WAHA message."""
    sender = waha.get_message_sender_name(msg, names)
    body = msg.get("body")
    if msg.get("hasMedia"):
        content = await _media(conn, msg)
        if body:
            content += f" {body}"
    elif body:
        content = body
    else:
        content = "[Empty or system message]"
    mid = msg.get("id")
    if isinstance(mid, dict):  # some engines nest it
        mid = mid.get("_serialized") or mid.get("id")
    suffix = f" (id: `{mid}`)" if mid else ""
    return f"{_time(msg)}**{sender}**: {content}{suffix}"


def _plain_media(conn: Connection, msg: Dict[str, Any], mid: str) -> str:
    """What a media message carries, without downloading anything (a voice note's
    transcript only when one was already made)."""
    media = msg.get("media") or {}
    mimetype = str(media.get("mimetype") or "")
    filename = str(media.get("filename") or "").strip()
    if mimetype.startswith("audio/"):
        said = _transcripts.get((conn.user.id if conn.user else "", mid))
        return f'[Voice message]: "{said}"' if said else "[Voice message]"
    if not mimetype:
        return f"[File: {filename}]" if filename else "[Media]"
    label = _kind_of(mimetype)
    return f"[{label}: {filename}]" if filename else f"[{label}]"


async def recent(conn: Connection, chat_id: str, limit: int = 12) -> List[Dict[str, Any]]:
    """The last ``limit`` messages of the chat ``chat_id`` (a WhatsApp id), oldest first, as
    ``{id, time, sender, from_me, text}``; nothing is downloaded or transcribed. Raises on a
    WAHA error (the caller decides what to do without them)."""
    url, headers, session = waha.config(conn)
    async with httpx.AsyncClient() as client:
        r = await client.get(waha.api_url(conn, session, "chats", chat_id, "messages"), headers=headers,
                             params={"limit": max(1, min(limit, 50)), "downloadMedia": "false"}, timeout=15.0)
    r.raise_for_status()
    rows = r.json() or []
    names = await waha.get_chat_id_to_name_map(conn)
    out: List[Dict[str, Any]] = []
    for msg in sorted((m for m in rows if isinstance(m, dict)), key=lambda m: m.get("timestamp") or 0):
        mid = msg.get("id")
        if isinstance(mid, dict):  # some engines nest it
            mid = mid.get("_serialized") or mid.get("id")
        mid = str(mid or "")
        body = str(msg.get("body") or "").strip()
        text = _plain_media(conn, msg, mid) + (f" {body}" if body else "") if msg.get("hasMedia") else body
        if not text:
            continue
        ts = msg.get("timestamp")
        try:
            when = clock.from_timestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
        except Exception:
            when = ""
        out.append({"id": mid, "time": when, "from_me": bool(msg.get("fromMe")),
                    "sender": waha.get_message_sender_name(msg, names) or "", "text": text})
    return out
