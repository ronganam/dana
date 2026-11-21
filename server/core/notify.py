"""Proactive messages to a user: the one way core code reaches someone on Telegram.

``await notify(user, text)`` sends ``text`` to the user's Telegram chat
(``users.telegram_chat_id``, core/users.py). It is a no-op returning False when
the user has no chat, when no Telegram channel is registered (tests, a server
without the bot) or when sending fails (logged).

The core never imports the Telegram adapter: at startup the adapter
(core/telegram_bot.py) registers itself with :func:`register_telegram` as a
:class:`TelegramChannel`. The same channel also renders a user's inbound
WhatsApp voice transcripts (:func:`whatsapp_voice`, sent to the chat their
``whatsapp`` integration names in its ``voice_telegram_chat_id`` setting).

Phone push (integrations/phone/push.py, ``push.send(user, ...)``) is separate for
now; docs/access.md describes ``notify`` growing to "the user's phone if they
have one".
"""

import logging
from typing import Optional, Protocol

from core.users import User

logger = logging.getLogger(__name__)


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
