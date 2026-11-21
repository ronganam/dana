"""Inbound WhatsApp (WAHA) events (docs/whatsapp.md).

WAHA pushes WhatsApp events to us via a global webhook (``WHATSAPP_HOOK_URL`` /
``WHATSAPP_HOOK_EVENTS`` env vars on the WAHA deployment), which points at
``POST /webhook/waha`` (route in integrations/whatsapp/api.py → :func:`handle_webhook`).

Each event is routed to the connection(s) whose WhatsApp account it names
(:mod:`integrations.whatsapp.accounts`) and processed as that connection's
user. We only act on **voice messages**: incoming 1:1 audio notes are
downloaded from the user's WAHA, transcribed (``core/llm.transcribe``, usage
theirs) and delivered to the phone (:func:`_notify_phone`) and to the
Telegram chat of the user's ``voice_telegram_chat_id`` setting, where the
transcript carries a "✨ Summarize" button handled by core/telegram_bot.py.
Everything else is ignored.
"""

import logging
from collections import OrderedDict
from typing import Any, Dict, Optional

import httpx

from core import llm, notify, users
from integrations.framework import Connection
from integrations.phone import push as phone_push
from integrations.whatsapp import accounts
from integrations.whatsapp.client import (api_url, config, download_media_bytes, get_chat_id_to_name_map,
                                          get_message_sender_name, signature)
from integrations.whatsapp.config import voice_chat_id

logger = logging.getLogger(__name__)

# Bounded set of recently handled message ids per connection so WAHA webhook
# redeliveries (network timeouts, retry policy) don't transcribe + notify the
# same voice note twice. Single-process deployment, so in-memory is enough.
_SEEN_MESSAGE_IDS: "Dict[Any, OrderedDict[str, None]]" = {}
_SEEN_MESSAGE_IDS_MAX = 500

# Direct-chat JIDs. Groups (@g.us), broadcasts and newsletters are ignored.
# Contacts increasingly arrive as @lid (Linked ID) rather than @c.us.
_DIRECT_CHAT_SUFFIXES = ("@c.us", "@lid", "@s.whatsapp.net")


def _is_direct_chat(jid: str) -> bool:
    """True for a 1:1 chat JID (``@c.us``, ``@lid``, ``@s.whatsapp.net``)."""
    return bool(jid) and jid.endswith(_DIRECT_CHAT_SUFFIXES)


def _mark_seen(conn: Connection, message_id: str) -> bool:
    """Record a message id for ``conn``; return True if it was already seen (a duplicate)."""
    seen = _SEEN_MESSAGE_IDS.setdefault(signature(conn), OrderedDict())
    if message_id in seen:
        return True
    seen[message_id] = None
    while len(seen) > _SEEN_MESSAGE_IDS_MAX:
        seen.popitem(last=False)
    return False


def _voice_mimetype(payload: Dict[str, Any]) -> Optional[str]:
    """Return the audio mimetype if this payload is a voice note, else None.

    A forwarded audio *file* (``ptt: false``) is not a voice note. When the
    engine doesn't expose the flag we stay permissive and treat any ``audio/*``
    message as a voice note.
    """
    media = payload.get("media") or {}
    mimetype = str(media.get("mimetype") or "").strip()
    if not mimetype.lower().startswith("audio/"):
        return None

    audio = ((payload.get("_data") or {}).get("message") or {}).get("audioMessage") or {}
    if audio.get("ptt") is False:
        return None
    return mimetype


def _clean_mimetype(mimetype: str) -> str:
    """Strip codec parameters so the model gets e.g. ``audio/ogg``, not ``audio/ogg; codecs=opus``."""
    return mimetype.split(";")[0].strip().lower()


def _raw_message_id(full_id: str) -> str:
    """Extract the engine-side message id from WAHA's ``{fromMe}_{jid}_{id}`` format."""
    return full_id.rsplit("_", 1)[-1] if full_id else ""


async def _resolve_media_url(conn: Connection, payload: Dict[str, Any]) -> Optional[str]:
    """Return a downloadable media URL, refetching the message if WAHA didn't download it."""
    media = payload.get("media") or {}
    url = media.get("url")
    if url:
        return url

    # hasMedia is true but url is null: WAHA skipped the download (config).
    # Refetch the single message with downloadMedia=true to populate media.url.
    raw_id = _raw_message_id(str(payload.get("id") or ""))
    if not raw_id:
        return None
    url_base, headers, session = config(conn)

    endpoint = api_url(conn, session, "chats", "all", "messages", raw_id)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                endpoint,
                headers=headers,
                params={"downloadMedia": "true"},
                timeout=20.0,
            )
        if r.status_code == 200:
            return (r.json().get("media") or {}).get("url")
        logger.warning(f"Refetching WAHA message {raw_id} returned {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"Failed to refetch WAHA message {raw_id} for media: {e}")
    return None


async def _notify_phone(conn: Connection, sender: str, transcript: str, message_id: str) -> bool:
    """The phone delivery seam: push the transcript to the connection's user's
    phone app (direct push, integrations/phone/push.py); nothing without the
    ``phone`` kind."""
    # Tag so a redelivery replaces the notification instead of stacking.
    ok = await phone_push.notify(conn.user, f"🎙️ WhatsApp voice — {sender}",
                                 transcript.strip() or "(no speech detected)",
                                 tag=f"wa_voice_{message_id}", channel="messages")
    if ok:
        logger.info(f"Sent WhatsApp voice notification for message {message_id} from '{sender}'")
    else:
        logger.error(f"WhatsApp voice notification for message {message_id} wasn't delivered now (queued, or no phone)")
    return ok


async def handle_webhook(data: Dict[str, Any]) -> None:
    """One WAHA webhook body: route it to its connection and process it as
    that connection's user. Never raises (the webhook has already been answered)."""
    try:
        conn = await accounts.route(data)
    except Exception as e:
        logger.error(f"Routing a WAHA event failed: {e}", exc_info=True)
        return
    if conn is not None:
        await process_event(conn, data)


async def process_event(conn: Connection, data: Dict[str, Any]) -> None:
    """Handle one WAHA event for ``conn`` (whose WhatsApp it is), as its user.

    Only incoming 1:1 voice messages are acted on: download -> transcribe ->
    notify. Never raises; errors are logged so the webhook always stays healthy.
    """
    with users.acting_as(conn.user):
        await _process(conn, data)


async def _process(conn: Connection, data: Dict[str, Any]) -> None:
    try:
        event = str(data.get("event") or "").strip()
        if event != "message":
            logger.debug(f"Ignoring WAHA event '{event}'")
            return

        payload = data.get("payload") or {}
        message_id = str(payload.get("id") or "").strip()
        if not message_id:
            return

        if payload.get("fromMe"):
            logger.debug(f"Ignoring outgoing WhatsApp message {message_id}")
            return

        sender_jid = str(payload.get("from") or "").strip()
        if not _is_direct_chat(sender_jid):
            logger.info(f"Ignoring non-1:1 WhatsApp message from '{sender_jid}'")
            return

        mimetype = _voice_mimetype(payload)
        if not mimetype:
            logger.debug(f"Ignoring non-voice WhatsApp message {message_id}")
            return

        if _mark_seen(conn, message_id):
            logger.info(f"Ignoring duplicate WhatsApp voice message {message_id} (webhook redelivery)")
            return

        logger.info(f"Processing WhatsApp voice message {message_id} from '{sender_jid}' ({mimetype}) "
                    f"for user {conn.user.id}")

        media_url = await _resolve_media_url(conn, payload)
        if not media_url:
            logger.warning(f"No media URL for WhatsApp voice message {message_id}; skipping")
            return

        audio_bytes = await download_media_bytes(conn, media_url)
        if not audio_bytes:
            logger.warning(f"Failed to download audio for WhatsApp voice message {message_id}; skipping")
            return

        transcript = await llm.transcribe(audio_bytes, _clean_mimetype(mimetype), source="whatsapp",
                                         ref=f"whatsapp:{message_id}")
        logger.info(f"Transcribed WhatsApp voice message {message_id}: {transcript[:100]!r}")

        try:
            mapping = await get_chat_id_to_name_map(conn)
        except Exception as e:
            logger.warning(f"Could not resolve WhatsApp contact names: {e}")
            mapping = {}
        sender = get_message_sender_name(payload, mapping) or "Unknown"

        await _notify_phone(conn, sender, transcript, message_id)
        await notify.whatsapp_voice(conn.user, voice_chat_id(conn), sender, transcript, message_id)

    except Exception as e:
        logger.error(f"Error processing WAHA event: {e}", exc_info=True)
