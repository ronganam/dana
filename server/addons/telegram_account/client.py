"""The user's own Telegram account through Telethon (MTProto), per connection.

- :func:`make_client` builds a Telethon client for a session string (tests
  replace it with a fake that has the same few methods).
- :func:`acquire` returns a connected, signed-in client for a connection,
  kept per (user, session) and dropped after :data:`IDLE_SECONDS` unused;
  reading clients take no updates (nothing runs in the background, and the
  account is never shown online).
- :func:`dialogs` lists the user's chats (cached :data:`DIALOGS_TTL`);
  :func:`messages` reads one chat, optionally only what is unread. Reading
  never marks anything read: that takes an explicit ``ReadHistory`` request,
  which nothing here sends.
- :func:`send_to_saved_messages` is the one write: a note to the account's
  own Saved Messages (``'me'``), never to anyone else.

Every value a tool prints is plain data built here (:class:`Chat`,
:class:`Message`), so the tools never touch Telethon objects."""

import asyncio
import datetime
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from core import clock
from core.users import User
from core.addons import Connection, store
from addons.telegram_account import config

logger = logging.getLogger(__name__)

IDLE_SECONDS = 600
DIALOGS_TTL = 60.0
MAX_TEXT = 1500  # characters of one message's text
TIMEOUT = 30.0  # seconds one chat's read may take (Telethon retries inside it)
DIALOGS_TIMEOUT = 60.0  # listing every chat of a big account takes several requests


class TelegramUnavailable(Exception):
    """The account cannot be read now; the message is for the user (no secret)."""


def make_client(session: str, *, receive_updates: bool = False) -> Any:
    """A Telethon client for ``session`` ("" for a new sign-in), not connected."""
    app = config.app()
    if app is None:
        raise TelegramUnavailable(f"The server has no Telegram app ({config.API_ID_ENV} / {config.API_HASH_ENV}).")
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    return TelegramClient(StringSession(session or None), app[0], app[1], receive_updates=receive_updates,
                          device_model="Dana", system_version="server", app_version="1.0")


# ---------------------------------------------------------------------------
# Connected clients, per connection
# ---------------------------------------------------------------------------

Key = Tuple[str, str]


@dataclass
class _Entry:
    client: Any
    used: float
    dialogs: Optional[List["Chat"]] = None
    dialogs_at: float = 0.0


_clients: Dict[Key, _Entry] = {}
_locks: Dict[Key, asyncio.Lock] = {}


def _key(conn: Connection) -> Key:
    session = str(conn.credentials.get(config.SESSION) or "")
    return conn.user.id, hashlib.sha256(session.encode()).hexdigest()[:16]


def _drop(key: Key) -> None:
    entry = _clients.pop(key, None)
    if entry is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_disconnect(entry.client))


async def _disconnect(client: Any) -> None:
    try:
        await client.disconnect()
    except Exception as e:  # already gone
        logger.debug(f"Telegram disconnect: {e}")


def forget(user: User) -> None:
    """Drop ``user``'s connected clients (their connection changed or went away)."""
    for key in [k for k in _clients if k[0] == user.id]:
        _drop(key)


def _reap(now: float) -> None:
    for key in [k for k, e in _clients.items() if now - e.used > IDLE_SECONDS]:
        _drop(key)


async def acquire(conn: Connection) -> Any:
    """A connected client signed in as the connection's account.

    Raises:
        TelegramUnavailable: no app, no session, or the session was signed out.
    """
    now = time.monotonic()
    _reap(now)
    key = _key(conn)
    entry = _clients.get(key)
    if entry is not None:
        entry.used = now
        return entry.client
    if not conn.credentials.get(config.SESSION):
        raise TelegramUnavailable("Telegram is not signed in; sign in on the Addons page.")
    async with _locks.setdefault(key, asyncio.Lock()):
        entry = _clients.get(key)
        if entry is not None:
            return entry.client
        client = make_client(str(conn.credentials[config.SESSION]))
        try:
            await client.connect()
            authorized = await client.is_user_authorized()
        except TelegramUnavailable:
            raise
        except Exception as e:
            await _disconnect(client)
            logger.warning(f"Telegram connect failed for user {conn.user.id}: {type(e).__name__}: {e}")
            raise TelegramUnavailable(f"Could not reach Telegram ({type(e).__name__}).")
        if not authorized:
            await _disconnect(client)
            store.needs_reconnect(conn.user, config.ADDON, SIGNED_OUT)
            raise TelegramUnavailable(f"{SIGNED_OUT} Sign in again on the Addons page.")
        _clients[key] = _Entry(client, time.monotonic())
        return client


SIGNED_OUT = "The Telegram session was signed out (removed from Telegram's devices?)."
# Telethon errors that mean the session itself is gone: only signing in again fixes them.
REVOKED = ("AuthKeyUnregisteredError", "SessionRevokedError", "UserDeactivatedError", "AuthKeyDuplicatedError")


def explain(e: Exception, conn: Optional[Connection] = None) -> str:
    """A Telethon error as a short reason for the user (never a secret). With
    ``conn`` (a read of a signed-in account), an error that means the session
    is gone marks the connection for reconnecting."""
    name = type(e).__name__
    if isinstance(e, TelegramUnavailable):
        return str(e)
    if isinstance(e, asyncio.TimeoutError):
        return "Telegram did not answer in time; try again in a minute."
    seconds = getattr(e, "seconds", None)
    if name.startswith("FloodWait") and seconds is not None:
        return f"Telegram asked to wait {seconds} seconds before reading more."
    if name in REVOKED:
        if conn is not None:
            store.needs_reconnect(conn.user, config.ADDON, "The Telegram session is no longer valid.")
        return "The Telegram session is no longer valid. Sign in again on the Addons page."
    return f"Telegram error ({name})."


# ---------------------------------------------------------------------------
# Chats and messages as plain data
# ---------------------------------------------------------------------------

@dataclass
class Chat:
    id: int
    name: str
    type: str  # "channel" | "group" | "private" | "bot"
    unread: int
    mentions: int
    muted: bool
    archived: bool
    read_inbox_max_id: int
    entity: Any


@dataclass
class Message:
    id: int
    when: str
    sender: str
    text: str


def display_name(entity: Any) -> str:
    if entity is None:
        return ""
    title = getattr(entity, "title", None)
    if title:
        return str(title)
    name = " ".join(p for p in (getattr(entity, "first_name", None), getattr(entity, "last_name", None)) if p)
    if name:
        return name
    username = getattr(entity, "username", None)
    return f"@{username}" if username else ""


def _muted(raw_dialog: Any) -> bool:
    settings = getattr(raw_dialog, "notify_settings", None)
    until = getattr(settings, "mute_until", None)
    if isinstance(until, datetime.datetime):
        if until.tzinfo is None:
            until = until.replace(tzinfo=datetime.timezone.utc)
        return until > datetime.datetime.now(datetime.timezone.utc)
    if isinstance(until, (int, float)):
        return until > time.time()
    return False


def _chat_type(d: Any) -> str:
    if getattr(d, "is_user", False):
        return "bot" if getattr(d.entity, "bot", False) else "private"
    if getattr(d, "is_group", False):
        return "group"
    return "channel"


def _to_chat(d: Any) -> Chat:
    raw = getattr(d, "dialog", None)
    return Chat(id=int(d.id), name=getattr(d, "name", "") or display_name(d.entity) or str(d.id),
                type=_chat_type(d), unread=int(getattr(d, "unread_count", 0) or 0),
                mentions=int(getattr(d, "unread_mentions_count", 0) or 0), muted=_muted(raw),
                archived=bool(getattr(d, "archived", False)),
                read_inbox_max_id=int(getattr(raw, "read_inbox_max_id", 0) or 0), entity=d.entity)


async def dialogs(conn: Connection, *, fresh: bool = False) -> List[Chat]:
    """Every chat of the account, most recent first (archived ones included)."""
    client = await acquire(conn)
    entry = _clients.get(_key(conn))
    if entry is not None and entry.dialogs is not None and not fresh and time.monotonic() - entry.dialogs_at < DIALOGS_TTL:
        return entry.dialogs
    async def listed() -> List[Chat]:
        return [_to_chat(d) async for d in client.iter_dialogs()]
    chats = await asyncio.wait_for(listed(), DIALOGS_TIMEOUT)
    if entry is not None:
        entry.dialogs, entry.dialogs_at = chats, time.monotonic()
    return chats


def _media(m: Any) -> str:
    """What a message carries besides text, in words ("" for none / a link preview)."""
    if getattr(m, "photo", None):
        return "[Photo]"
    if getattr(m, "voice", None):
        return "[Voice message]"
    if getattr(m, "video_note", None):
        return "[Video message]"
    if getattr(m, "gif", None):
        return "[GIF]"
    if getattr(m, "sticker", None):
        return "[Sticker]"
    if getattr(m, "video", None):
        return "[Video]"
    poll = getattr(m, "poll", None)
    if poll is not None:
        question = getattr(getattr(poll, "poll", None), "question", None)
        text = getattr(question, "text", question)
        return f"[Poll: {text}]" if text else "[Poll]"
    if getattr(m, "geo", None):
        return "[Location]"
    if getattr(m, "contact", None):
        return "[Contact]"
    if getattr(m, "document", None):
        file = getattr(m, "file", None)
        name = getattr(file, "name", None)
        mime = getattr(file, "mime_type", None)
        return f"[File: {name or 'unnamed'}{f' ({mime})' if mime else ''}]"
    return ""


def _to_message(m: Any, chat: Chat) -> Message:
    when = ""
    date = getattr(m, "date", None)
    if isinstance(date, datetime.datetime):
        when = clock.from_timestamp(date.timestamp()).strftime("%Y-%m-%d %H:%M")
    if getattr(m, "out", False):
        sender = "Me"
    elif chat.type == "channel":
        sender = str(getattr(m, "post_author", "") or "")
    else:
        sender = display_name(getattr(m, "sender", None))
    text = str(getattr(m, "message", "") or "").strip()
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "…"
    media = _media(m)
    if getattr(m, "fwd_from", None):
        media = ("[Forwarded] " + media).strip()
    body = " ".join(p for p in (media, text) if p) or "[Service message]"
    return Message(id=int(m.id), when=when, sender=sender, text=body)


async def messages(conn: Connection, chat: Chat, *, limit: int, unread_only: bool = False,
                   search: Optional[str] = None) -> List[Message]:
    """The chat's latest ``limit`` messages (only those after the read mark with
    ``unread_only``, only those containing ``search``), oldest first."""
    client = await acquire(conn)
    kwargs: Dict[str, Any] = {"limit": max(1, limit)}
    if unread_only:
        kwargs["min_id"] = chat.read_inbox_max_id
    if search:
        kwargs["search"] = search
    found = await asyncio.wait_for(client.get_messages(chat.entity, **kwargs), TIMEOUT)
    return [_to_message(m, chat) for m in reversed(list(found or []))]


def describe_chat(chat: Chat) -> str:
    """``**name** (channel, 12 unread, 2 mentions, muted) id: `…```."""
    bits = [chat.type]
    if chat.unread:
        bits.append(f"{chat.unread} unread")
    if chat.mentions:
        bits.append(f"{chat.mentions} mentions")
    if chat.muted:
        bits.append("muted")
    if chat.archived:
        bits.append("archived")
    return f"**{chat.name}** ({', '.join(bits)}) id: `{chat.id}`"


def format_message(msg: Message) -> str:
    who = f"**{msg.sender}**: " if msg.sender else ""
    return f"[{msg.when}] {who}{msg.text}"


# ---------------------------------------------------------------------------
# Writing: the account's own Saved Messages, nothing else
# ---------------------------------------------------------------------------

SAVED_MESSAGES = "me"  # Telethon's name for the signed-in account's own chat (Saved Messages)
MAX_SEND = 4096        # characters of one Telegram message


def chunks(text: str, size: int = MAX_SEND) -> List[str]:
    """``text`` cut into messages of at most ``size`` characters, at a line
    break when there is one in the second half of a piece."""
    out: List[str] = []
    rest = text
    while len(rest) > size:
        cut = rest.rfind("\n", size // 2, size)
        cut = cut if cut > 0 else size
        out.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest:
        out.append(rest)
    return out


async def send_to_saved_messages(conn: Connection, text: str) -> List[int]:
    """Send ``text`` to the connection's own Saved Messages (the only chat
    this module ever writes to; the recipient is fixed here, never taken
    from a caller). Returns the sent messages' ids."""
    client = await acquire(conn)
    ids: List[int] = []
    for piece in chunks(text):
        sent = await asyncio.wait_for(client.send_message(SAVED_MESSAGES, piece), TIMEOUT)
        ids.append(int(getattr(sent, "id", 0) or 0))
    return ids


# ---------------------------------------------------------------------------
# Connection check (Addon(check=))
# ---------------------------------------------------------------------------

async def check(conn: Connection) -> Optional[str]:
    """None when the session is signed in, else a short reason."""
    try:
        client = await acquire(conn)
        me = await client.get_me()
    except Exception as e:
        return explain(e, conn)
    return None if me is not None else "Telegram did not return the signed-in account."
