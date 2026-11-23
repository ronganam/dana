"""The ``get_latest_messages`` tool: recent messages across the user's chats,
filtered by read status and chat type (registered after the tools in
:mod:`addons.whatsapp.tools`, so the tool order stays the same)."""

import logging

import httpx

from core.addons import Connection
from addons.whatsapp import client as waha, messages as messages_fmt
from addons.whatsapp.addon import addon

logger = logging.getLogger(__name__)


@addon.tool(untrusted_result=True)
async def get_latest_messages(
    conn: Connection,
    read_filter: str = "both",
    chat_filter: str = "both",
    limit_chats: int = 15,
    messages_per_chat: int = 5,
) -> str:
    """Fetch recent messages from WhatsApp chats with filters for read status and chat type.

    Media is described like in read_chat_messages (voice transcribed, images saved, other files
    by name and type), and each message ends with its id (`id: ...`) for forward_whatsapp_message,
    replies, edits and deletes.

    Args:
        read_filter: Filter by read status. Options: 'unread' (only unread messages/chats), 'read' (only read messages/chats), 'both' (all messages/chats). Default is 'both'.
        chat_filter: Filter by chat type. Options: 'dm' (direct messages/individual chats), 'groups' (group chats), 'both' (all chats). Default is 'both'.
        limit_chats: Maximum number of recent chats to check (default is 15).
        messages_per_chat: Maximum number of recent messages to return per chat (default is 5).
    """
    url, headers, session = waha.config(conn)
    endpoint = waha.api_url(conn, session, "chats", "overview")

    read_filter = read_filter.lower().strip()
    chat_filter = chat_filter.lower().strip()

    if read_filter not in ("unread", "read", "both"):
        return "Error: read_filter must be 'unread', 'read', or 'both'."
    if chat_filter not in ("dm", "groups", "both"):
        return "Error: chat_filter must be 'dm', 'groups', or 'both'."

    logger.info(f"Fetching latest messages (read_filter: {read_filter}, chat_filter: {chat_filter})")
    async with httpx.AsyncClient() as client:
        try:
            r = None
            try:
                r = await client.get(endpoint, headers=headers, params={"limit": limit_chats}, timeout=20.0)
                if r.status_code != 200:
                    logger.error(f"Failed to fetch chats from WAHA overview: {r.status_code} - {r.text}")
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                logger.warning(f"Error fetching from WAHA overview endpoint: {e or type(e).__name__}. Trying fallback...")

            if r is None or r.status_code != 200:
                fallback_endpoint = waha.api_url(conn, session, "chats")
                r = await client.get(fallback_endpoint, headers=headers, params={"limit": limit_chats}, timeout=20.0)
                if r.status_code != 200:
                    return f"Error fetching chats from WAHA: {r.text}"

            chats = r.json()
            if not chats:
                return "No WhatsApp chats found."

            mapping = await waha.get_chat_id_to_name_map(conn)

            filtered_chats = []
            for chat in chats:
                chat_id = chat.get("id")
                if not chat_id:
                    continue
                is_archived = chat.get("archived") or chat.get("_chat", {}).get("archived") or False
                if is_archived:
                    continue

                # 1. Chat type filter (DM / Group)
                is_group = "@g.us" in chat_id
                if chat_filter == "dm" and is_group:
                    continue
                if chat_filter == "groups" and not is_group:
                    continue

                # 2. Read status filter (Unread / Read)
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

                has_unread = (unread_count > 0 or unread_mention_count > 0 or marked_as_unread or last_msg_unread)

                if read_filter == "unread" and not has_unread:
                    continue
                if read_filter == "read" and has_unread:
                    continue

                chat_name = chat.get("name") or mapping.get(chat_id) or chat_id
                filtered_chats.append({
                    "id": chat_id,
                    "name": chat_name,
                    "has_unread": has_unread,
                    "is_group": is_group,
                    "unread_count": unread_count,
                    "unread_mention_count": unread_mention_count,
                })

            if not filtered_chats:
                return f"No messages found matching read_filter='{read_filter}' and chat_filter='{chat_filter}'."

            result = f"Latest messages (read_filter='{read_filter}', chat_filter='{chat_filter}'):\n\n"
            for fc in filtered_chats:
                chat_id = fc["id"]
                chat_name = fc["name"]

                # Fetch recent messages for this chat
                msg_endpoint = waha.api_url(conn, session, "chats", chat_id, "messages")
                msg_res = await client.get(msg_endpoint, headers=headers, params=messages_fmt.message_query(messages_per_chat), timeout=20.0)

                if msg_res.status_code == 200:
                    messages = msg_res.json()
                    if not messages:
                        result += f"### Chat: **{chat_name}** (ID: `{chat_id}`):\n"
                        result += "  *(No messages retrieved)*\n\n"
                        continue

                    try:
                        messages = sorted(messages, key=lambda m: m.get("timestamp", 0))
                    except Exception:
                        pass

                    # Filter messages based on read_filter
                    is_group_chat = fc["is_group"]
                    u_count = fc["unread_count"]
                    u_mention_count = fc["unread_mention_count"]

                    # Get our identifiers for filtering individual messages
                    my_jid, my_lid = await waha.get_my_identifiers(conn, client)

                    if read_filter == "unread":
                        if is_group_chat:
                            selected_msgs = []
                            for m in messages:
                                if m.get("fromMe"):
                                    continue
                                receipts = m.get("_data", {}).get("userReceipt") or []
                                has_our_receipt = False
                                if my_jid or my_lid:
                                    for receipt in receipts:
                                        ujid = receipt.get("userJid")
                                        if ujid and ujid in (my_jid, my_lid):
                                            has_our_receipt = True
                                            break
                                    if not has_our_receipt:
                                        selected_msgs.append(m)
                                else:
                                    if m.get("ack", 0) < 3:
                                        selected_msgs.append(m)
                            if not selected_msgs:
                                selected_msgs = [messages[-1]]
                        else:
                            selected_msgs = [m for m in messages if not m.get("fromMe") and m.get("ack", 0) < 3]
                            if not selected_msgs:
                                selected_msgs = [messages[-1]] # fallback to last message
                    elif read_filter == "read":
                        if is_group_chat:
                            selected_msgs = []
                            for m in messages:
                                if m.get("fromMe"):
                                    selected_msgs.append(m)
                                    continue
                                receipts = m.get("_data", {}).get("userReceipt") or []
                                has_our_receipt = False
                                if my_jid or my_lid:
                                    for receipt in receipts:
                                        ujid = receipt.get("userJid")
                                        if ujid and ujid in (my_jid, my_lid):
                                            has_our_receipt = True
                                            break
                                    if has_our_receipt:
                                        selected_msgs.append(m)
                                else:
                                    if m.get("ack", 0) >= 3:
                                        selected_msgs.append(m)
                            selected_msgs = sorted(selected_msgs, key=lambda m: m.get("timestamp", 0))
                            if not selected_msgs:
                                selected_msgs = [messages[-1]]
                        else:
                            selected_msgs = [m for m in messages if m.get("fromMe") or m.get("ack", 0) >= 3]
                            if not selected_msgs:
                                selected_msgs = [messages[-1]]
                    else:
                        selected_msgs = messages

                    status_lbl = " [Unread]" if fc["has_unread"] else ""
                    result += f"### Chat: **{chat_name}** (ID: `{chat_id}`){status_lbl}:\n"

                    for msg in selected_msgs:
                        result += "- " + await messages_fmt.format_message(conn, msg, mapping) + "\n"
                    result += "\n"
                else:
                    result += f"### Chat: **{chat_name}** (ID: `{chat_id}`):\n"
                    result += f"  *(Error retrieving messages: {msg_res.text})*\n\n"

            return result.strip()
        except Exception as e:
            logger.error(f"Exception in get_latest_messages: {e}", exc_info=True)
            err_msg = str(e).strip() or type(e).__name__
            return f"Error connecting to WAHA API: {err_msg}"
