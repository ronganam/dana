"""WhatsApp tools over the user's own WAHA (see :mod:`addons.whatsapp.client`):
chats, contact search, reading (lines from :mod:`addons.whatsapp.messages`),
sending, forwarding, editing and deleting messages, reading a received file
(``read_whatsapp_file``: ``llm.read_file``) and keeping one on a project item
(``save_whatsapp_file``, docs/knowledge.md "Files").
``get_latest_messages`` is in :mod:`addons.whatsapp.latest`."""

import logging
import re
from collections import OrderedDict
from typing import Any, Dict, NamedTuple, Optional

import httpx

from core import clock, llm
from core.addons import Approval, Connection
from core.addons.hooks import clean
from core.users import User
from addons.whatsapp import client as waha, contacts as saved_contacts, messages as messages_fmt, resolve
from addons.whatsapp.addon import addon

logger = logging.getLogger(__name__)


def _message_id(message_id: str) -> Optional[str]:
    """A message id as read_chat_messages shows it (backticks dropped), or None when it is not one."""
    message_id = (message_id or "").strip().strip("`")
    return message_id if "_" in message_id and "@" in message_id else None


BAD_ID = ("Error: message_id must be a message's full id as shown (`id: ...`) by read_chat_messages / "
          "get_latest_messages (e.g. 'false_15550101234@c.us_3EB0C0FFEE'). Read the chat first.")


def _whatsapp_who(user: Optional[User], chat_id: Any) -> str:
    """A WhatsApp chat id as a person: a name as given, a known number as the
    person's name, else the number; groups by id → 'a WhatsApp group'."""
    raw = clean(chat_id, 120)
    if not raw:
        return "someone"
    if raw.endswith("@g.us"):
        return "a WhatsApp group"
    if "@" not in raw and not re.fullmatch(r"[\d\s+\-()]+", raw):
        return raw
    digits = re.sub(r"\D", "", raw.split("@")[0])
    if user is not None and digits:
        try:
            from addons.people import store as people_store
            pid = people_store.find_by_phone(user, digits)
            name = people_store.name_of(user, pid) if pid else None
            if name:
                return name
        except Exception:
            pass
    return f"+{digits}" if digits else raw


def _whatsapp_source(user: Optional[User], message_id: Any) -> str:
    parts = clean(message_id, 200).split("_")
    if len(parts) < 3 or "@" not in parts[1] or parts[1].endswith("@lid"):
        return ""
    return _whatsapp_who(user, parts[1])


def _describe_send(user: Optional[User], a: Dict[str, Any]) -> Approval:
    return Approval(f"Send a WhatsApp message to {_whatsapp_who(user, a.get('chat_id'))}", "WhatsApp message", "💬",
                    "Send this WhatsApp message?", body_label="Message", body=a.get("text"))


def _describe_file(user: Optional[User], a: Dict[str, Any]) -> Approval:
    return Approval(f"Send a file on WhatsApp to {_whatsapp_who(user, a.get('chat_id'))}", "WhatsApp file", "💬",
                    "Send this file?", body_label="Caption", body=a.get("caption"),
                    facts=[("File", a.get("filename") or a.get("file_url")), ("Type", a.get("mimetype"))])


def _describe_forward(user: Optional[User], a: Dict[str, Any]) -> Approval:
    return Approval(f"Forward a WhatsApp message to {_whatsapp_who(user, a.get('chat_id'))}", "Forward on WhatsApp", "💬",
                    "Forward this message?", body_label="What", body=a.get("what"),
                    facts=[("From", _whatsapp_source(user, a.get("message_id")))])


def _describe_edit(user: Optional[User], a: Dict[str, Any]) -> Approval:
    return Approval(f"Edit your WhatsApp message to {_whatsapp_who(user, a.get('chat_id'))}", "Edit WhatsApp message",
                    "💬", "Edit this message?", body_label="New text", body=a.get("new_text"))


def _describe_delete(user: Optional[User], a: Dict[str, Any]) -> Approval:
    return Approval(f"Delete your WhatsApp message to {_whatsapp_who(user, a.get('chat_id'))}",
                    "Delete WhatsApp message", "💬", "Delete this message for everyone?")


@addon.tool(untrusted_result=True)
async def find_last_chats(conn: Connection, limit: int = 10) -> str:
    """Find the most recent WhatsApp chats (conversation history overview).

    Args:
        limit: The maximum number of chats to return (default 10, at most 50).
    """
    limit = max(1, min(limit, 50))
    url, headers, session = waha.config(conn)
    endpoint = waha.api_url(conn, session, "chats", "overview")
    params = {"limit": limit}

    logger.info(f"Fetching last {limit} chats from WAHA session: {session}")
    async with httpx.AsyncClient() as client:
        try:
            r = None
            try:
                r = await client.get(endpoint, headers=headers, params=params, timeout=20.0)
                if r.status_code != 200:
                    logger.error(f"Failed to fetch chats from WAHA overview: {r.status_code} - {r.text}")
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                logger.warning(f"Error fetching from WAHA overview endpoint: {e or type(e).__name__}. Trying fallback...")

            if r is None or r.status_code != 200:
                fallback_endpoint = waha.api_url(conn, session, "chats")
                r = await client.get(fallback_endpoint, headers=headers, params=params, timeout=20.0)
                if r.status_code != 200:
                    return f"Error fetching chats from WAHA: {r.text}"

            chats = r.json()
            if not chats:
                return "No WhatsApp chats found."

            mapping = await waha.get_chat_id_to_name_map(conn)
            result = f"Found {len(chats)} recent WhatsApp chats:\n"
            for chat in chats:
                chat_id = chat.get("id", "Unknown ID")
                is_archived = chat.get("archived") or chat.get("_chat", {}).get("archived") or False
                if is_archived:
                    continue
                chat_name = chat.get("name") or mapping.get(chat_id) or chat_id

                is_group = "@g.us" in chat_id
                unread_info = ""
                unread_count = chat.get("unreadCount") or chat.get("_chat", {}).get("unreadCount") or 0
                unread_mention_count = chat.get("unreadMentionCount") or chat.get("_chat", {}).get("unreadMentionCount") or 0
                marked_as_unread = chat.get("markedAsUnread") or chat.get("_chat", {}).get("markedAsUnread") or False

                last_msg = chat.get("lastMessage")
                last_msg_unread = False
                if last_msg and not last_msg.get("fromMe"):
                    if is_group:
                        my_jid, my_lid = await waha.get_my_identifiers(conn, client)
                        receipts = last_msg.get("_data", {}).get("userReceipt") or []
                        has_our_receipt = False
                        if my_jid or my_lid:
                            for receipt in receipts:
                                ujid = receipt.get("userJid")
                                if ujid and ujid in (my_jid, my_lid):
                                    has_our_receipt = True
                                    break
                            if not has_our_receipt:
                                last_msg_unread = True
                        else:
                            if last_msg.get("ack", 0) < 3:
                                last_msg_unread = True
                    else:
                        if last_msg.get("ack", 0) < 3:
                            last_msg_unread = True

                if unread_count > 0:
                    unread_info += f" ({unread_count} unread)"
                elif unread_mention_count > 0:
                    unread_info += f" ({unread_mention_count} unread mentions)"
                elif last_msg_unread:
                    unread_info += " (unread)"
                elif marked_as_unread:
                    unread_info += " (marked unread)"

                last_msg_str = "No messages"
                last_msg = chat.get("lastMessage")
                if last_msg:
                    body = last_msg.get("body")
                    sender_name = waha.get_message_sender_name(last_msg, mapping)

                    ts = last_msg.get("timestamp")
                    time_str = ""
                    if ts:
                        try:
                            dt = clock.from_timestamp(ts)
                            time_str = dt.strftime(" [%Y-%m-%d %H:%M:%S]")
                        except Exception:
                            pass

                    if body:
                        body = body if len(body) <= 200 else body[:200] + "…"
                        last_msg_str = f"'{body}' (sent by {sender_name}){time_str}"
                    elif last_msg.get("hasMedia"):
                        mimetype = last_msg.get("media", {}).get("mimetype", "unknown media") if last_msg.get("media") else "unknown media"
                        last_msg_str = f"[Media: {mimetype}] (sent by {sender_name}){time_str}"

                result += f"- **{chat_name}** (ID: `{chat_id}`){unread_info}\n  Last Message: {last_msg_str}\n"
            return result
        except Exception as e:
            logger.error(f"Exception fetching WAHA chats: {e}", exc_info=True)
            err_msg = str(e).strip() or type(e).__name__
            return f"Error connecting to WAHA API: {err_msg}"


@addon.tool(untrusted_result=True)
async def search_whatsapp_contact_or_chat(conn: Connection, query: str) -> str:
    """Search for a WhatsApp contact or group chat by name to find their chat ID.

    Args:
        query: The contact name or chat group title to search for (e.g. 'John', 'Family').
    """
    url, headers, session = waha.config(conn)
    query_lower = query.lower()
    matches = []

    # Search the user's saved contacts first
    try:
        query_normalized = query_lower.replace(" ", "_").replace("-", "_")
        for row in saved_contacts.search_rows(conn.user, query_lower, query_normalized):
            resolved_val = row["value"].strip()
            resolved_key_name = row["key"].replace("_", " ").title()
            if not any(m["id"] == resolved_val for m in matches):
                matches.append({
                    "type": "Memory/Nickname",
                    "name": resolved_key_name,
                    "id": resolved_val
                })
    except Exception as e:
        logger.error(f"Error querying saved contacts in search: {e}")

    async with httpx.AsyncClient() as client:
        # Search active chats overview first (contains names for non-contacts/groups)
        chats = []
        try:
            r = await client.get(waha.api_url(conn, session, "chats", "overview"), headers=headers, params={"limit": 100}, timeout=20.0)
            if r.status_code == 200:
                chats = r.json()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning(f"Timeout/Error fetching chats overview in search: {e or type(e).__name__}")

        if not chats:
            try:
                r = await client.get(waha.api_url(conn, session, "chats"), headers=headers, timeout=20.0)
                if r.status_code == 200:
                    chats = r.json()
            except Exception as e:
                logger.error(f"Error querying chats fallback in search: {e}")

        for chat in chats:
            chat_id = chat.get("id", "")
            chat_name = chat.get("name") or ""
            if query_lower in chat_name.lower() or query_lower in chat_id.lower():
                matches.append({
                    "type": "Chat/Group",
                    "name": chat_name,
                    "id": chat_id
                })

        # Search contact book
        try:
            r = await client.get(waha.api_url(conn, "contacts", "all"), headers=headers, params={"session": session}, timeout=20.0)
            if r.status_code == 200:
                contacts = r.json()
                for contact in contacts:
                    contact_id = contact.get("id", "")
                    contact_name = contact.get("name") or ""

                    # Avoid duplicates
                    if any(m["id"] == contact_id for m in matches):
                        continue

                    if query_lower in contact_name.lower() or query_lower in contact_id.lower():
                        matches.append({
                            "type": "Contact",
                            "name": contact_name,
                            "id": contact_id
                        })
        except Exception as e:
            logger.warning(f"Error querying contacts in search: {e}")

    if not matches:
        return f"No WhatsApp contacts or chats matching '{query}' found."

    res_str = f"Found {len(matches)} matching contacts/chats for '{query}'"
    res_str += " (the first 20; narrow the query):\n" if len(matches) > 20 else ":\n"
    for m in matches[:20]:
        name_str = m["name"] or "Unnamed"
        res_str += f"- **{name_str}** ({m['type']}) -> ID: `{m['id']}`\n"
    return res_str


@addon.tool(untrusted_result=True)
async def read_chat_messages(conn: Connection, chat_id: str, limit: int = 10) -> str:
    """Read recent messages from a specific WhatsApp chat by contact/group name or chat ID.

    The chat_id can be a chat ID (with @c.us/@g.us suffix) or contact/group name (e.g. 'John', 'ענבל').
    It supports bilingual matching (Hebrew <-> English translations/transliterations, e.g. 'ענבל' matches 'Inbal Cohen'), nicknames, and partial searches.
    When a name matches multiple contacts, the tool automatically prefers the most recently active one.
    If the name query is still ambiguous after recency filtering, it returns all candidates so you can ask the user to clarify.
    Voice messages are transcribed; photos and other files (PDFs, documents, videos) are listed
    by name and type: read_whatsapp_file reads one. Each message ends with its id (`id: ...`):
    pass it to read_whatsapp_file, forward_whatsapp_message, or to reply / edit / delete.

    Args:
        chat_id: The WhatsApp chat ID (with @c.us/@g.us suffix), contact name, or group name.
        limit: The maximum number of messages to retrieve (default 10, at most 100).
    """
    limit = max(1, min(limit, 100))
    url, headers, session = waha.config(conn)
    try:
        resolved_id, resolved_name = await resolve.resolve_chat_id_and_name(conn, chat_id)
    except ValueError as ve:
        return str(ve)

    endpoint = waha.api_url(conn, session, "chats", resolved_id, "messages")
    params = messages_fmt.message_query(limit)

    logger.info(f"Reading last {limit} messages for resolved chat {resolved_name} (ID: {resolved_id})")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(endpoint, headers=headers, params=params, timeout=20.0)
            if r.status_code != 200:
                logger.error(f"Failed to read messages for {resolved_id}: {r.status_code} - {r.text}")
                return f"Error reading messages from WAHA: {r.text}"

            messages = r.json()
            if not messages:
                return f"No messages found in chat '{resolved_name}' ({resolved_id})."

            mapping = await waha.get_chat_id_to_name_map(conn)
            result = f"Recent messages in chat **{resolved_name}** (ID: `{resolved_id}`) (showing {len(messages)}):\n"
            try:
                messages = sorted(messages, key=lambda m: m.get("timestamp", 0))
            except Exception:
                pass
            for msg in messages:
                result += await messages_fmt.format_message(conn, msg, mapping) + "\n"
            return result
        except Exception as e:
            logger.error(f"Exception reading WAHA messages for {resolved_id}: {e}", exc_info=True)
            err_msg = str(e).strip() or type(e).__name__
            return f"Error connecting to WAHA API: {err_msg}"


@addon.tool(is_write=True, confirm=True, describe=_describe_send)
async def send_whatsapp_message(conn: Connection, chat_id: str, text: str, reply_to: Optional[str] = None) -> str:
    """Send a WhatsApp text message to a chat by contact/group name or chat ID.

    To pass along something someone else sent (a file, photo, voice note or their message),
    use forward_whatsapp_message instead of describing it in text.

    IMPORTANT: The 'text' parameter must contain the user's exact original phrasing.
    Do not rephrase, formalize, translate, or add greetings/closings — send exactly
    what the user wrote unless they explicitly ask you to rewrite it.

    The chat_id can be:
      - An existing contact/group name from your WhatsApp (e.g. 'John', 'ענבל', 'Family') — auto-resolved.
      - A properly formatted chat ID (e.g. '15550100301@c.us') — use this for new/newly-validated numbers.

    WhatsApp chat IDs ALWAYS include a suffix: '@c.us' for individuals, '@g.us' for groups.
    Bare phone numbers like '0500000302' are NOT valid chat IDs.

    If you only have a bare phone number (not in your contacts/chats yet):
      1. First call check_whatsapp_number(phone) to validate it and get the chat ID.
      2. Then pass the returned chat ID (e.g. '15550100302@c.us') here.

    The tool supports bilingual matching (Hebrew <-> English translations/transliterations, e.g. 'ענבל' matches 'Inbal Cohen'), nicknames, and partial searches.
    When a name matches multiple contacts, the tool automatically prefers the most recently active one.
    If the name query is still ambiguous after recency filtering, it returns all candidates so you can ask the user to clarify.

    Args:
        chat_id: The WhatsApp chat ID (with @c.us/@g.us suffix), contact name, or group name.
        text: The text message content to send.
        reply_to: Optional id of the message to reply to, as shown (`id: ...`) by read_chat_messages / get_latest_messages (e.g. 'false_11111111111@c.us_AAAAAAAAAAAAAAAAAAAA').
    """
    url, headers, session = waha.config(conn)
    try:
        resolved_id, resolved_name = await resolve.resolve_chat_id_and_name(conn, chat_id)
    except ValueError as ve:
        return str(ve)

    endpoint = waha.api_url(conn, "sendText")

    payload = {
        "session": session,
        "chatId": resolved_id,
        "text": text
    }
    if reply_to:
        payload["reply_to"] = reply_to

    logger.info(f"Sending WhatsApp message to {resolved_name} (ID: {resolved_id}) via WAHA session {session}")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(endpoint, headers=headers, json=payload, timeout=20.0)
            if r.status_code not in (200, 201):
                logger.error(f"Failed to send WhatsApp message to {resolved_id}: {r.status_code} - {r.text}")
                return f"Error sending message via WAHA: {r.text}"

            res_json = r.json()
            msg_id = res_json.get("key", {}).get("id") or res_json.get("id") or "Unknown ID"
            return f"Successfully sent WhatsApp message to **{resolved_name}** (ID: `{resolved_id}`). Message ID: {msg_id}"
        except Exception as e:
            logger.error(f"Exception sending WhatsApp message: {e}", exc_info=True)
            err_msg = str(e).strip() or type(e).__name__
            return f"Error connecting to WAHA API: {err_msg}"


@addon.tool()
async def check_whatsapp_number(conn: Connection, phone: str) -> str:
    """Check if a phone number is registered on WhatsApp and get its chat ID.

    Use this FIRST when you need to send a message to a bare phone number (not yet in your chats/contacts).
    The returned chat ID (in the format 'XXXXXXXXX@c.us') can then be passed directly to send_whatsapp_message.

    Args:
        phone: The phone number to check (e.g. '+15550100301' or '15550100301').
    """
    url, headers, session = waha.config(conn)
    endpoint = waha.api_url(conn, "contacts", "check-exists")

    # Normalize to international format (a leading 0 → DEFAULT_COUNTRY_CODE when set)
    cleaned_phone = waha.normalize_to_international(phone)

    params = {
        "phone": cleaned_phone,
        "session": session
    }

    logger.info(f"Checking if phone number exists on WhatsApp: {cleaned_phone}")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(endpoint, headers=headers, params=params, timeout=20.0)
            if r.status_code != 200:
                logger.error(f"Failed to check WhatsApp number: {r.status_code} - {r.text}")
                return f"Error checking WhatsApp number via WAHA: {r.text}"

            res = r.json()
            # WAHA returns numberExists/chatId (v2+) or exists/id (v1)
            exists = res.get("numberExists", res.get("exists", False))
            chat_id = res.get("chatId", res.get("id")) or f"{cleaned_phone}@c.us"
            if exists:
                return f"Phone number `{phone}` is registered on WhatsApp. Chat ID: `{chat_id}`"
            else:
                return f"Phone number `{phone}` is NOT registered on WhatsApp."
        except Exception as e:
            logger.error(f"Exception checking WhatsApp number: {e}", exc_info=True)
            err_msg = str(e).strip() or type(e).__name__
            return f"Error connecting to WAHA API: {err_msg}"


@addon.tool(is_write=True, confirm=True, describe=_describe_file)
async def send_whatsapp_file(
    conn: Connection,
    chat_id: str,
    file_url: str,
    mimetype: str,
    filename: Optional[str] = None,
    caption: Optional[str] = None
) -> str:
    """Send a file (image, document, audio, video) at a public URL to a WhatsApp chat.

    Only for a file at a public http(s) URL. A file someone sent on WhatsApp has no public
    URL: pass it along with forward_whatsapp_message (the `id: ...` read_chat_messages shows).

    The chat_id can be:
      - An existing contact/group name from your WhatsApp — auto-resolved.
      - A properly formatted chat ID (e.g. '15550100301@c.us') — use for new/newly-validated numbers.

    WhatsApp chat IDs ALWAYS include a suffix: '@c.us' for individuals, '@g.us' for groups.
    Bare phone numbers are NOT valid chat IDs.

    Args:
        chat_id: The WhatsApp chat ID (with @c.us/@g.us suffix), contact name, or group name.
        file_url: The public URL of the file to send.
        mimetype: The MIME type of the file (e.g. 'image/jpeg', 'application/pdf', 'audio/mp3').
        filename: Optional name for the file as it will appear in the chat.
        caption: Optional caption/text to send with the file.
    """
    url, headers, session = waha.config(conn)
    try:
        resolved_id, resolved_name = await resolve.resolve_chat_id_and_name(conn, chat_id)
    except ValueError as ve:
        return str(ve)

    endpoint = waha.api_url(conn, "sendFile")

    file_payload = {
        "url": file_url,
        "mimetype": mimetype
    }
    if filename:
        file_payload["filename"] = filename

    payload = {
        "session": session,
        "chatId": resolved_id,
        "file": file_payload
    }
    if caption:
        payload["caption"] = caption

    logger.info(f"Sending file to {resolved_name} (ID: {resolved_id}) via WAHA session {session}")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(endpoint, headers=headers, json=payload, timeout=20.0)
            if r.status_code not in (200, 201):
                logger.error(f"Failed to send file via WAHA to {resolved_id}: {r.status_code} - {r.text}")
                return f"Error sending file via WAHA: {r.text}"

            res_json = r.json()
            msg_id = res_json.get("key", {}).get("id") or res_json.get("id") or "Unknown ID"
            return f"Successfully sent file to **{resolved_name}** (ID: `{resolved_id}`). Message ID: {msg_id}"
        except Exception as e:
            logger.error(f"Exception sending file via WAHA: {e}", exc_info=True)
            err_msg = str(e).strip() or type(e).__name__
            return f"Error connecting to WAHA API: {err_msg}"


@addon.tool(is_write=True, confirm=True, describe=_describe_forward)
async def forward_whatsapp_message(conn: Connection, chat_id: str, message_id: str,
                                   what: Optional[str] = None) -> str:
    """Forward a WhatsApp message to another chat exactly as it is: a file (PDF, document),
    photo, video, voice note or text that someone sent. Use this whenever the user asks to
    send / pass along / forward something they received; nothing is downloaded and no URL
    is needed.

    First read the source chat (read_chat_messages or get_latest_messages) and take the
    message's id from its `id: ...`; never make one up. To forward several messages
    (e.g. a file and the text around it), call this once per message.

    Args:
        chat_id: Where to forward it: chat ID (with @c.us/@g.us suffix), contact name, or group name.
        message_id: The full id of the message to forward, exactly as shown (e.g. 'false_15550101234@c.us_3EB0C0FFEE').
        what: A few words on what is being forwarded, shown to the user on the approval card (e.g. "Ori's price quote PDF").
    """
    url, headers, session = waha.config(conn)
    message_id = _message_id(message_id) or ""
    if not message_id:
        return BAD_ID
    try:
        resolved_id, resolved_name = await resolve.resolve_chat_id_and_name(conn, chat_id)
    except ValueError as ve:
        return str(ve)

    payload = {"session": session, "chatId": resolved_id, "messageId": message_id}
    logger.info(f"Forwarding a WhatsApp message to {resolved_name} (ID: {resolved_id}) via WAHA session {session}")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(waha.api_url(conn, "forwardMessage"), headers=headers, json=payload, timeout=60.0)
            if r.status_code not in (200, 201):
                logger.error(f"Failed to forward WhatsApp message to {resolved_id}: {r.status_code} - {r.text}")
                if r.status_code == 422:
                    return (f"Error: WhatsApp has no message with id '{message_id}' (it may be too old for WAHA's "
                            "store). Read the chat again and use an id it shows.")
                return f"Error forwarding message via WAHA: {r.text}"
            try:
                res_json = r.json()
            except ValueError:
                res_json = {}
            msg_id = (res_json.get("id") or res_json.get("key", {}).get("id") or "Unknown ID") if isinstance(res_json, dict) else "Unknown ID"
            return f"Successfully forwarded the message to **{resolved_name}** (ID: `{resolved_id}`). Message ID: {msg_id}"
        except Exception as e:
            logger.error(f"Exception forwarding WhatsApp message: {e}", exc_info=True)
            err_msg = str(e).strip() or type(e).__name__
            return f"Error connecting to WAHA API: {err_msg}"


@addon.tool(is_write=True, confirm=True, describe=_describe_edit)
async def edit_whatsapp_message(conn: Connection, chat_id: str, message_id: str, new_text: str) -> str:
    """Edit a WhatsApp text message previously sent.

    IMPORTANT: The 'new_text' parameter must contain the user's exact original phrasing.
    Do not rephrase, formalize, or translate — send exactly what the user wrote unless
    they explicitly ask you to rewrite it.

    Args:
        chat_id: The WhatsApp chat ID (e.g. '15550100301@c.us') or contact/group name (e.g. 'John', 'ORA').
        message_id: The id of your own message to edit, as shown (`id: ...`) by read_chat_messages / get_latest_messages (e.g. 'true_15550100301@c.us_3EB01234567890').
        new_text: The new text content for the message.
    """
    message_id = _message_id(message_id) or ""
    if not message_id:
        return BAD_ID
    url, headers, session = waha.config(conn)
    try:
        resolved_id, resolved_name = await resolve.resolve_chat_id_and_name(conn, chat_id)
    except ValueError as ve:
        return str(ve)

    endpoint = waha.api_url(conn, session, "chats", resolved_id, "messages", message_id)

    payload = {
        "text": new_text
    }

    logger.info(f"Editing message {message_id} in chat {resolved_name} (ID: {resolved_id})")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.put(endpoint, headers=headers, json=payload, timeout=20.0)
            if r.status_code not in (200, 201, 204):
                logger.error(f"Failed to edit message via WAHA: {r.status_code} - {r.text}")
                return f"Error editing message via WAHA: {r.text}"
            return f"Successfully edited message ID `{message_id}` in chat **{resolved_name}**."
        except Exception as e:
            logger.error(f"Exception editing message: {e}", exc_info=True)
            err_msg = str(e).strip() or type(e).__name__
            return f"Error connecting to WAHA API: {err_msg}"


@addon.tool(is_write=True, confirm=True, describe=_describe_delete)
async def delete_whatsapp_message(conn: Connection, chat_id: str, message_id: str) -> str:
    """Delete (revoke) a sent message from a WhatsApp chat.

    Args:
        chat_id: The WhatsApp chat ID (e.g. '15550100301@c.us') or contact/group name (e.g. 'John', 'ORA').
        message_id: The id of your own message to delete, as shown (`id: ...`) by read_chat_messages / get_latest_messages (e.g. 'true_15550100301@c.us_3EB01234567890').
    """
    message_id = _message_id(message_id) or ""
    if not message_id:
        return BAD_ID
    url, headers, session = waha.config(conn)
    try:
        resolved_id, resolved_name = await resolve.resolve_chat_id_and_name(conn, chat_id)
    except ValueError as ve:
        return str(ve)

    endpoint = waha.api_url(conn, session, "chats", resolved_id, "messages", message_id)

    logger.info(f"Deleting message {message_id} in chat {resolved_name} (ID: {resolved_id})")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.delete(endpoint, headers=headers, timeout=20.0)
            if r.status_code not in (200, 201, 204):
                logger.error(f"Failed to delete message via WAHA: {r.status_code} - {r.text}")
                return f"Error deleting message via WAHA: {r.text}"
            return f"Successfully deleted/revoked message ID `{message_id}` in chat **{resolved_name}**."
        except Exception as e:
            logger.error(f"Exception deleting message: {e}", exc_info=True)
            err_msg = str(e).strip() or type(e).__name__
            return f"Error connecting to WAHA API: {err_msg}"


class _Media(NamedTuple):
    """A file someone sent: its bytes, type and name (the sender's, or the chat and date)."""
    data: bytes
    mime: str
    name: str


async def _fetch_media(conn: Connection, chat_id: str, message_id: str) -> "_Media | str":
    """The file a message carries, or an error to return."""
    message_id = _message_id(message_id) or ""
    if not message_id:
        return BAD_ID
    try:
        resolved_id, resolved_name = await resolve.resolve_chat_id_and_name(conn, chat_id)
    except ValueError as ve:
        return str(ve)
    _, headers, session = waha.config(conn)
    endpoint = waha.api_url(conn, session, "chats", resolved_id, "messages", message_id)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(endpoint, headers=headers, params={"downloadMedia": "true"}, timeout=60.0)
        if r.status_code != 200:
            return f"Error: WhatsApp has no message '{message_id}' in {resolved_name} (status {r.status_code})."
        msg = r.json()
    except Exception as e:
        logger.error(f"Exception fetching WhatsApp message {message_id}: {e}", exc_info=True)
        return f"Error connecting to WAHA API: {str(e).strip() or type(e).__name__}"
    media = (msg or {}).get("media") or {}
    if not media.get("url"):
        return "Error: That message carries no file (or WhatsApp no longer has it)."
    data = await waha.download_media_bytes(conn, media["url"])
    if not data:
        return "Error: The file could not be downloaded from WhatsApp."
    mime = str(media.get("mimetype") or "application/octet-stream").split(";")[0].strip()
    name = str(media.get("filename") or "").strip()
    if not name:
        stamp = clock.from_timestamp(msg["timestamp"]).strftime("%Y-%m-%d %H.%M") if msg.get("timestamp") else ""
        ext = {"audio/ogg": ".ogg", "video/mp4": ".mp4", "image/jpeg": ".jpg", "image/png": ".png",
               "application/pdf": ".pdf"}.get(mime, "")
        name = f"{resolved_name} {stamp}".strip() + ext
    return _Media(data, mime, name)


# What read_whatsapp_file read lately ((user, message id) → text), so keeping the file
# right after (save_whatsapp_file) does not read it again.
_read_texts: "OrderedDict[tuple, str]" = OrderedDict()
FILE_TEXT_CHARS = 10_000


@addon.tool(untrusted_result=True)
async def read_whatsapp_file(conn: Connection, chat_id: str, message_id: str) -> str:
    """Read a file someone sent on WhatsApp: a PDF's or document's text (a quote, a contract), what
    a photo shows and its text, or a voice note's transcript. Use it whenever the details are in the
    file rather than the messages. Take the id from read_chat_messages / get_latest_messages.

    Args:
        chat_id: The chat the message is in: chat ID (with @c.us/@g.us suffix), contact name, or group name.
        message_id: The full id of the message carrying the file, exactly as shown (`id: ...`).
    """
    media = await _fetch_media(conn, chat_id, message_id)
    if isinstance(media, str):
        return media
    head = f"{media.name} ({media.mime}, {round(len(media.data) / 1024)} KB)"
    if not llm.readable(media.mime, len(media.data)):
        return f"Error: Dana can't read {head}: only PDFs, photos, audio and text files up to 14 MB."
    try:
        text = await llm.read_file(media.data, media.mime, purpose="whatsapp_file", ref=message_id)
    except Exception as e:
        logger.warning(f"Reading WhatsApp file {message_id} failed: {e}")
        return f"Error: {head} could not be read ({str(e).strip() or type(e).__name__})."
    if not text:
        return f"{head}: no text in it."
    _read_texts[(conn.user.id, _message_id(message_id))] = text
    while len(_read_texts) > 32:
        _read_texts.popitem(last=False)
    cut = f"\n… (cut, {len(text):,} chars)" if len(text) > FILE_TEXT_CHARS else ""
    return f"{head}:\n{text[:FILE_TEXT_CHARS]}{cut}"


# The result names the file as its sender named it.
@addon.tool(is_write=True, untrusted_result=True)
async def save_whatsapp_file(conn: Connection, chat_id: str, message_id: str, item_id: str,
                             note: Optional[str] = None) -> str:
    """Keep a file someone sent on WhatsApp (a price quote PDF, a voice note, a video, a photo) on a
    saved project item, so it opens from the project; Dana reads its text onto the file
    (get_knowledge_item shows it).

    Take the message's id from read_chat_messages / get_latest_messages (`id: ...`); never make one
    up. Save the item first (save_knowledge_item) to have its ID.

    Args:
        chat_id: The chat the message is in: chat ID (with @c.us/@g.us suffix), contact name, or group name.
        message_id: The full id of the message carrying the file, exactly as shown.
        item_id: The ID of the project item (entry) to keep the file on.
        note: Optional few words on what the file is (e.g. "הצעת מחיר", "Voice note about the discount").
    """
    from core.addons import store
    from addons.knowledge import files as knowledge_files, projects as knowledge_projects
    from addons.knowledge import tools as knowledge_tools

    if not store.is_connected(conn.user, knowledge_projects.ADDON):
        return "Error: Projects (knowledge) are not connected for this user, so there is nowhere to keep the file."
    media = await _fetch_media(conn, chat_id, message_id)
    if isinstance(media, str):
        return media
    text = _read_texts.get((conn.user.id, _message_id(message_id)))
    try:
        saved = knowledge_files.add_file(conn.user, item_id, media.name, media.mime, media.data, source="whatsapp",
                                         note=note or "", transcript=text)
    except Exception as e:  # ReadOnly, TooLarge
        return f"Error: {e}"
    if saved is None:
        return f"Error: No project item with ID `{item_id}`. Use list_project_entries to find it."
    knowledge_tools.record_file_added(conn.user, item_id, saved)
    extra = " Its text is being read." if not text and llm.readable(media.mime, len(media.data)) else ""
    return f"Kept '{saved['name']}' ({round(len(media.data) / 1024)} KB) on the item.{extra}"
