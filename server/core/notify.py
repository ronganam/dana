"""Proactive messages to a user (../docs/addons.md, "The manifest": ``@addon.channel``).

``await send(user, Message(...))`` delivers one message on every channel the
user has, all at once (``asyncio.gather``): Telegram (core's own: the
message's ``text`` to their chat, ``users.telegram_chat_id``) and each
``@addon.channel`` of an addon active for them (the phone addon's ``push``:
the message's ``kind`` and ``data`` as a push event). Each channel takes what
it can use from the message and says whether it delivered it; one failing
(or raising) is logged, counts as not delivered and never stops the others.

``await notify(user, text)`` is the Telegram channel alone (the text to the
user's chat). It is a no-op returning False when
the user has no chat, when no Telegram channel is registered (tests, a server
without the bot) or when sending fails (logged).

The core never imports the Telegram adapter: at startup the adapter
(core/telegram_bot.py) registers itself with :func:`register_telegram` as a
:class:`TelegramChannel`. The same channel also renders a user's inbound
WhatsApp voice transcripts (:func:`whatsapp_voice`, sent to the chat their
``whatsapp`` addon names in its ``voice_telegram_chat_id`` setting).

"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Dict, List, Optional, Protocol

from core.users import User
from core.addons import registry

logger = logging.getLogger(__name__)

TELEGRAM = "telegram"


@dataclass(frozen=True)
class Message:
    """One proactive message. ``text`` is what a person reads (Telegram;
    ``parse_mode`` "Markdown" for Telegram's legacy Markdown, values escaped
    with ``core.text.escape_markdown``); ``kind`` and ``data`` are the
    structured event a device gets (the phone's push: ``kind`` "reminder",
    ``data`` its payload). A channel skips a message without what it needs."""
    text: str = ""
    parse_mode: Optional[str] = None
    kind: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


class TelegramChannel(Protocol):
    async def send_text(self, chat_id: int, text: str, parse_mode: Optional[str] = None) -> None:
        """Send a message to a chat; raise on failure."""
        ...

    async def notify_whatsapp_voice(self, user: User, chat_id: Optional[int], sender: str, transcript: str,
                                    message_id: str) -> bool:
        """Show ``user``'s inbound WhatsApp voice transcript in ``chat_id`` (with a
        Summarize button only ``user`` can use)."""
        ...


_telegram: Optional[TelegramChannel] = None


def register_telegram(channel: Optional[TelegramChannel]) -> None:
    """Make ``channel`` the Telegram sender (None removes it)."""
    global _telegram
    _telegram = channel


async def notify(user: User, text: str, *, parse_mode: Optional[str] = None) -> bool:
    """Send ``text`` to ``user``'s Telegram chat. True when it was sent."""
    chat_id = user.telegram_chat_id
    if chat_id is None or _telegram is None:
        return False
    try:
        await _telegram.send_text(chat_id, text, parse_mode=parse_mode)
    except Exception as e:
        logger.error(f"Telegram message to {user.name} failed: {e}")
        return False
    return True


async def _deliver(user: User, ch: Any, message: Message) -> bool:
    try:
        return bool(await ch.func(user, message))
    except Exception as e:
        logger.error(f"Channel {ch.name} ({ch.addon}) failed for user {user.id}: {e}", exc_info=True)
        return False


async def send(user: User, message: Message) -> Dict[str, bool]:
    """Deliver ``message`` to ``user`` on Telegram (when it has text) and on
    every channel of the addons active for them, concurrently. Returns
    channel name → whether it delivered (Telegram first, then addon order)."""
    names: List[str] = []
    sends: List[Awaitable[bool]] = []
    if message.text:
        names.append(TELEGRAM)
        sends.append(notify(user, message.text, parse_mode=message.parse_mode))
    for ch in registry.channels(user):
        names.append(ch.name)
        sends.append(_deliver(user, ch, message))
    results = await asyncio.gather(*sends, return_exceptions=True)
    out: Dict[str, bool] = {}
    for name, result in zip(names, results):
        if isinstance(result, BaseException):
            logger.error(f"Channel {name} failed for user {user.id}: {result}")
        out[name] = result is True
    return out


async def push_notify(user: User, title: str, text: str, tag: str = "", channel: str = "messages") -> bool:
    """Deliver a plain notification to ``user``'s device channels (e.g. the phone push channel)."""
    res = await send(user, Message(kind="notify", data={"title": title[:200], "text": text[:4000],
                                                        "tag": tag[:120] or None, "channel": channel}))
    return any(res.values())


async def whatsapp_voice(user: User, chat_id: Optional[int], sender: str, transcript: str, message_id: str) -> bool:
    """Hand ``user``'s inbound WhatsApp voice transcript to the Telegram
    channel, for ``chat_id`` (their ``voice_telegram_chat_id`` setting; None:
    none, nothing is sent). False when there is no channel or it could not be sent."""
    if _telegram is None:
        logger.debug("No Telegram channel for the WhatsApp voice transcript")
        return False
    try:
        return bool(await _telegram.notify_whatsapp_voice(user=user, chat_id=chat_id, sender=sender,
                                                          transcript=transcript, message_id=message_id))
    except Exception as e:
        # Telegram must never break the WhatsApp pipeline (or the webhook).
        logger.error(f"Failed to send WhatsApp voice transcript to Telegram: {e}")
        return False
