"""One user's WAHA (WhatsApp HTTP API), over that user's own connection:
address, API key and session from the connection's credentials, the
session's own identifiers ("me"), the chat-id → name map, media downloads.

Every function takes the user's :class:`~core.addons.Connection`;
nothing is shared between connections, caches included (keyed by
:func:`signature`).
"""
import datetime
import logging
import os
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import httpx

from core.addons import Connection, addresses
from addons.whatsapp.config import DEFAULT_SESSION

logger = logging.getLogger(__name__)

Signature = Tuple[str, str, str, str]


# ---------------------------------------------------------------------------
# Connection details
# ---------------------------------------------------------------------------

def base_url(conn: Connection) -> str:
    """The WAHA API base (e.g. ``http://waha:3000/api``, no trailing slash)."""
    return (conn.credentials.get("url") or "").strip().rstrip("/")


def api_url(conn: Connection, *parts: object) -> str:
    """The URL of an API path on the connection's WAHA: the base URL joined
    with ``parts``, each one path segment, percent-encoded (a chat id or a
    message id can never add a segment, a query or another host;
    docs/access.md, "Security rules").

    Raises:
        ValueError: the stored base URL is not a valid address.
    """
    return addresses.join(base_url(conn), "/" + "/".join(addresses.segment(p) for p in parts))


def api_key(conn: Connection) -> str:
    return conn.credentials.get("api_key") or ""


def session(conn: Connection) -> str:
    return conn.credentials.get("session") or DEFAULT_SESSION


def headers(conn: Connection) -> Dict[str, str]:
    """Request headers for the connection's WAHA (no ``X-Api-Key`` when it has no key)."""
    out = {"Content-Type": "application/json"}
    key = api_key(conn)
    if key:
        out["X-Api-Key"] = key
    return out


def config(conn: Connection) -> Tuple[str, Dict[str, str], str]:
    """``(base url, headers, session)`` of the connection."""
    return base_url(conn), headers(conn), session(conn)


def signature(conn: Connection) -> Signature:
    """What identifies one connection's WAHA session (caches key on it)."""
    return (conn.user.id, base_url(conn), api_key(conn), session(conn))


def normalize_to_international(phone: str) -> str:
    cleaned = "".join(c for c in phone if c.isdigit())
    if cleaned.startswith("00"):
        cleaned = cleaned[2:]
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if cleaned.startswith("0"):
        cc = os.getenv("DEFAULT_COUNTRY_CODE", "972")
        cleaned = cc + cleaned[1:]
    return cleaned


# ---------------------------------------------------------------------------
# The session's own identifiers ("me")
# ---------------------------------------------------------------------------

_me_cache: Dict[Signature, Tuple[str, str]] = {}


async def fetch_me(conn: Connection, client: Optional[httpx.AsyncClient] = None) -> Optional[Dict[str, Any]]:
    """The session's ``me`` object from WAHA (``GET {url}/sessions``, the
    entry whose ``name`` is the connection's session): ``{"id":
    "<number>@c.us", "lid": "...@lid", "pushName": ...}``; None when WAHA
    cannot be reached, answers with an error or the session has no ``me``
    (not logged in)."""
    url, hdrs, sess = config(conn)
    try:
        if client is None:
            async with httpx.AsyncClient() as own:
                r = await own.get(api_url(conn, "sessions"), headers=hdrs, timeout=10.0)
        else:
            r = await client.get(api_url(conn, "sessions"), headers=hdrs, timeout=10.0)
    except Exception as e:
        logger.warning(f"Error fetching WAHA session details for user {conn.user.id}: {e}")
        return None
    if r.status_code != 200:
        logger.warning(f"WAHA session details for user {conn.user.id} returned {r.status_code}")
        return None
    try:
        for s in r.json():
            if s.get("name") == sess:
                me = s.get("me") or None
                return me if isinstance(me, dict) else None
    except Exception as e:
        logger.warning(f"Unexpected WAHA sessions payload for user {conn.user.id}: {e}")
    return None


async def get_my_identifiers(conn: Connection, client: httpx.AsyncClient) -> Tuple[Optional[str], Optional[str]]:
    """``(jid, lid)`` of the connection's own WhatsApp account (kept once both are known)."""
    key = signature(conn)
    if key in _me_cache:
        return _me_cache[key]
    me = await fetch_me(conn, client) or {}
    jid, lid = me.get("id"), me.get("lid")
    if jid and lid:
        _me_cache[key] = (jid, lid)
    return jid, lid


# ---------------------------------------------------------------------------
# Chat id -> name map
# ---------------------------------------------------------------------------

NAME_MAP_TTL_SECONDS = 300
_name_maps: Dict[Signature, Tuple[datetime.datetime, Dict[str, str]]] = {}


async def get_chat_id_to_name_map(conn: Connection) -> Dict[str, str]:
    """Retrieves all contacts and active chats to build a mapping from IDs/LIDs to friendly names.
    Results are cached per connection for 5 minutes to reduce WAHA API calls.
    """
    key = signature(conn)
    now = datetime.datetime.now()
    hit = _name_maps.get(key)
    if hit is not None and (now - hit[0]).total_seconds() < NAME_MAP_TTL_SECONDS:
        return hit[1]

    mapping: Dict[str, str] = {}
    url, hdrs, sess = config(conn)

    # 1. Fetch contacts
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(api_url(conn, "contacts", "all"), headers=hdrs, params={"session": sess}, timeout=10.0)
            if r.status_code == 200:
                contacts = r.json()
                for c in contacts:
                    c_id = c.get("id")
                    c_name = c.get("name")
                    if c_id and c_name:
                        mapping[c_id] = c_name
                        c_id_clean = c_id.split("@")[0]
                        mapping[c_id_clean] = c_name
    except Exception as e:
        logger.warning(f"Error building contact name mapping: {e}")

    # 2. Fetch chats overview
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(api_url(conn, sess, "chats", "overview"), headers=hdrs, params={"limit": 100}, timeout=10.0)
            if r.status_code == 200:
                chats = r.json()
                for chat in chats:
                    chat_id = chat.get("id")
                    chat_name = chat.get("name")

                    last_msg = chat.get("lastMessage")
                    push_name_to_use = None
                    if last_msg:
                        _d = last_msg.get("_data") or {}
                        push_name = _d.get("pushName")
                        if push_name:
                            push_name = push_name.lstrip("~").strip()
                        biz_name = _d.get("verifiedBizName")

                        name_to_use = push_name or biz_name
                        push_name_to_use = name_to_use
                        if name_to_use:
                            key_data = _d.get("key") or {}
                            sender_jid = key_data.get("participant") or key_data.get("remoteJid")
                            remote_jid = key_data.get("remoteJid")
                            remote_jid_alt = key_data.get("remoteJidAlt")
                            participant = key_data.get("participant")
                            participant_alt = key_data.get("participantAlt")
                            if sender_jid:
                                mapping[sender_jid] = name_to_use
                            if remote_jid:
                                mapping[remote_jid] = name_to_use
                            if remote_jid_alt:
                                mapping[remote_jid_alt] = name_to_use
                            if participant:
                                mapping[participant] = name_to_use
                            if participant_alt:
                                mapping[participant_alt] = name_to_use

                    if chat_id:
                        effective_name = chat_name or push_name_to_use
                        if effective_name:
                            mapping[chat_id] = effective_name
                            mapping[chat_id.split("@")[0]] = effective_name
                        elif chat_id not in mapping:
                            mapping[chat_id] = chat_id
                            mapping[chat_id.split("@")[0]] = chat_id
    except Exception as e:
        logger.warning(f"Error building chat name mapping from overview: {e}")

    _name_maps[key] = (datetime.datetime.now(), mapping)
    return mapping


def get_message_sender_name(msg: dict, chat_id_to_name: dict) -> str:
    """Helper to cleanly resolve message senders using pushName or contact map."""
    if msg.get("fromMe"):
        return "Me"

    _d = msg.get("_data") or {}
    push_name = _d.get("pushName")
    if push_name:
        return push_name.lstrip("~").strip()

    biz_name = _d.get("verifiedBizName")
    if biz_name:
        return biz_name.strip()

    part_id = msg.get("participant")
    if part_id and part_id in chat_id_to_name:
        return chat_id_to_name[part_id]

    part_alt = (_d.get("key") or {}).get("participantAlt")
    if part_alt and part_alt in chat_id_to_name:
        return chat_id_to_name[part_alt]

    from_id = msg.get("from")
    if from_id and from_id in chat_id_to_name:
        return chat_id_to_name[from_id]

    if from_id:
        return from_id.split("@")[0]

    return "Them"


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------

def media_url_on_connection(conn: Connection, media_url: str) -> Optional[str]:
    """Where to fetch a media URL from a WAHA event: always the connection's
    own WAHA (its scheme, host and port) with only the path and query taken
    from ``media_url`` — WAHA reports files under its own public address,
    which the server may not reach, and an event's URL must never send the
    API key anywhere else (docs/access.md, "Security rules"). None when
    ``media_url`` is not an http(s) URL or an absolute path, or the
    connection's address is not valid."""
    try:
        own = urlsplit(addresses.normalize(base_url(conn)))
        parts = urlsplit(str(media_url or "").strip())
    except ValueError:
        return None
    if parts.scheme not in ("", "http", "https") or not parts.path.startswith("/"):
        return None
    try:
        path = addresses.join(urlunsplit((own.scheme, own.netloc, "", "", "")), parts.path)
    except ValueError:
        return None
    return path + (f"?{parts.query}" if parts.query else "")


async def download_media_bytes(conn: Connection, media_url: str) -> Optional[bytes]:
    """Download an event's media from the connection's own WAHA, with its API
    key (:func:`media_url_on_connection`); None when it cannot be fetched or
    the URL is refused."""
    _, hdrs, _ = config(conn)
    target = media_url_on_connection(conn, media_url)
    if target is None:
        logger.warning(f"WAHA media URL refused for user {conn.user.id} (not a path on their WAHA)")
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(target, headers=hdrs, timeout=20.0)
            if r.status_code == 200:
                return r.content
            logger.error(f"Failed to download media from {target}: status {r.status_code} - {r.text[:200]}")
    except Exception as e:
        logger.error(f"Exception downloading media from {target}: {e}")
    return None


# ---------------------------------------------------------------------------
# Connection check (Addon(check=): POST /api/addons/whatsapp/test)
# ---------------------------------------------------------------------------

async def check(conn: Connection) -> Optional[str]:
    """One cheap request (``GET {url}/sessions``): None when WAHA answers with
    the key and the connection's session is there and working, else a short
    reason (never the key)."""
    url, hdrs, sess = config(conn)
    if not url:
        return "No WAHA address is set."
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(api_url(conn, "sessions"), headers=hdrs, timeout=10.0)
    except Exception as e:
        return f"Could not reach WAHA at {url} ({type(e).__name__})."
    if r.status_code in (401, 403):
        return f"WAHA refused the API key (HTTP {r.status_code})."
    if r.status_code != 200:
        return f"WAHA answered HTTP {r.status_code}."
    try:
        found = next((s for s in r.json() if isinstance(s, dict) and s.get("name") == sess), None)
    except ValueError:
        return "WAHA's answer was not JSON."
    if found is None:
        return f"WAHA has no session named {sess!r}."
    status = str(found.get("status") or "")
    if status and status != "WORKING":
        return f"The WAHA session {sess!r} is {status}, not WORKING."
    return None
