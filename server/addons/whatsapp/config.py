"""WhatsApp configuration of one user, read from their connection: the addon's
name, WAHA's default session name, the voice-transcript chat setting.
Nothing here reads env: the values are set on the Addons page or with
bin/users.py."""

import logging
from typing import Optional

from core.addons import Connection

logger = logging.getLogger(__name__)

ADDON = "whatsapp"

# WAHA's own name for a session nobody named.
DEFAULT_SESSION = "default"

# Setting: the Telegram chat inbound voice-note transcripts go to (None: none).
VOICE_CHAT = "voice_telegram_chat_id"


def voice_chat_id(conn: Connection) -> Optional[int]:
    """The Telegram chat for ``conn``'s voice-note transcripts: the setting as
    a chat id; None (logged) when it is unset or not a number. No fallbacks."""
    raw = conn.settings.get(VOICE_CHAT)
    if raw is None or isinstance(raw, bool) or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        logger.error(f"The {ADDON} setting {VOICE_CHAT} of user {conn.user.id} is not a numeric chat id ({raw!r}): "
                     "their WhatsApp voice transcripts will NOT be sent to Telegram.")
        return None
