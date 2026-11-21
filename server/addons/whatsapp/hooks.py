"""The WhatsApp addon's background work (docs/whatsapp.md):

- at start: report where each connected user's inbound voice transcripts go on Telegram;
- every ``accounts.REFRESH_SECONDS``: learn the account of a connection that has none.
"""

import datetime
import logging
import os
from typing import Optional

from core.addons import store
from core.users import User
from addons.whatsapp import accounts, config
from addons.whatsapp.addon import addon
from addons.whatsapp.api import SECRET_ENV

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


@addon.job(at_start=True)
async def log_voice_chat(user: User) -> None:
    report_voice_chat(user)
    if not os.getenv(SECRET_ENV, "").strip():
        logger.error(f"User {user.id} has WhatsApp, but {SECRET_ENV} is not set: every WAHA webhook is refused, so "
                     f"incoming messages and voice notes are not received. Set it to WAHA's WHATSAPP_HOOK_HMAC_KEY "
                     f"(docs/whatsapp.md)")


@addon.job(every=datetime.timedelta(seconds=accounts.REFRESH_SECONDS))
async def learn_account(user: User) -> Optional[str]:
    return await accounts.refresh_user(user)
