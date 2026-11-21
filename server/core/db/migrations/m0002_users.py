"""Users and their identities (docs/access.md, "User").

- ``users``: one row per person. ``access`` is an access level name
  (core/access.py). ``telegram_chat_id`` is where proactive Telegram messages
  go (NULL: none).
- ``user_tokens``: API tokens, one per device, stored hashed: ``token_hash`` is
  the SHA-256 of the token (Bearer lookups), ``session_hash`` the SHA-256 of the
  web session cookie made from it (cookie lookups). The token itself is never
  stored.
- ``user_telegram``: Telegram user ids that resolve to a user.
"""

import sqlite3

VERSION = 2
NAME = "users"

STATEMENTS = [
    """CREATE TABLE users (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        access TEXT NOT NULL,
        telegram_chat_id INTEGER,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE user_tokens (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        label TEXT NOT NULL DEFAULT '',
        token_hash TEXT NOT NULL UNIQUE,
        session_hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        last_used_at TEXT
    )""",
    "CREATE INDEX idx_user_tokens_user ON user_tokens(user_id)",
    """CREATE TABLE user_telegram (
        telegram_user_id INTEGER PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_user_telegram_user ON user_telegram(user_id)",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
