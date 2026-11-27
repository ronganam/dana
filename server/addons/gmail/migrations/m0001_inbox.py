"""The gmail addon's first tables, for ``gmail.email.received`` (event_types.py):
``gmail_inbox``, one row per user: where the inbox was last read (Gmail's
``historyId``) and the address it belongs to; ``gmail_received``, one row per
message the event was emitted for, with the data it was emitted with: emitted
once across restarts, and the event's history for dry runs of watches (pruned
after 30 days)."""

import sqlite3

VERSION = 1
NAME = "inbox"

STATEMENTS = [
    """CREATE TABLE gmail_inbox (
        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        history_id TEXT NOT NULL DEFAULT '',
        address TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE gmail_received (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        message_id TEXT NOT NULL,
        emitted_at TEXT NOT NULL,
        data TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (user_id, message_id)
    )""",
    "CREATE INDEX idx_gmail_received_emitted ON gmail_received(user_id, emitted_at)",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
