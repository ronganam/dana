"""The WhatsApp addon's background work (docs/whatsapp.md):

- at start: report where each connected user's inbound voice transcripts go on Telegram;
- every ``accounts.REFRESH_SECONDS``: learn the account of a connection that has none.
"""

import datetime
import logging
from typing import Optional

from core import users
from core.addons import store
from core.users import User
from addons.whatsapp import accounts, config
from addons.whatsapp.addon import addon

logger = logging.getLogger(__name__)


def report_voice_chat(user: User) -> None:
    """Log where ``user``'s inbound WhatsApp voice transcripts go on Telegram."""
    conn = store.get(user, config.ADDON)
    if conn is None:
        return
    chat_id = config.voice_chat_id(conn)
    if chat_id is None:
        logger.warning(f"WhatsApp voice transcripts of user {user.id} will NOT be sent to Telegram: their "
                       f"{config.ADDON} setting {config.VOICE_CHAT} is not a chat id.")
    else:
        logger.info(f"WhatsApp voice transcripts of user {user.id} go to Telegram chat {chat_id}")


def report_whatsapp_voice_chats() -> None:
    """Log, per user with WhatsApp, where their voice transcripts go."""
    for uid in store.users_with(config.ADDON):
        user = users.get(uid)
        if user is not None:
            report_voice_chat(user)


@addon.job(at_start=True)
async def log_voice_chat(user: User) -> None:
    report_voice_chat(user)


@addon.job(every=datetime.timedelta(seconds=accounts.REFRESH_SECONDS))
async def learn_account(user: User) -> Optional[str]:
    return await accounts.refresh_user(user)
