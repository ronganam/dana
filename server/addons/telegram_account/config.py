"""The ``telegram_account`` addon's name and the server's Telegram app.

A user's own Telegram account is read through Telegram's user API (MTProto),
which needs an *app* id and hash (my.telegram.org → API development tools).
They identify Dana, not a person, so they are server-wide env values; each
user's signed-in session is the credential of their connection."""

import os
from typing import Optional, Tuple

ADDON = "telegram_account"

# The connection's credential: Telethon's StringSession (full account access).
SESSION = "session"

API_ID_ENV = "TELEGRAM_API_ID"
API_HASH_ENV = "TELEGRAM_API_HASH"


def app() -> Optional[Tuple[int, str]]:
    """The server's Telegram app (id, hash), or None when it is not configured."""
    raw_id = (os.getenv(API_ID_ENV) or "").strip()
    api_hash = (os.getenv(API_HASH_ENV) or "").strip()
    if not raw_id.isdigit() or not api_hash:
        return None
    return int(raw_id), api_hash


def configured() -> bool:
    return app() is not None
