"""Sharing: who besides its owner may see or change an item (docs/access.md,
"Sharing"; ``integrations/framework/sharing.py``).

``item_access``: one row per (item type, item id, user) with ``level``
``read`` or ``edit``. The owner is not listed (each item type knows its own
owner); no row means no access. Nothing is shared by an existing database.
"""

import sqlite3

VERSION = 6
NAME = "item_access"

STATEMENTS = [
    """CREATE TABLE item_access (
        item_type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        level TEXT NOT NULL CHECK (level IN ('read', 'edit')),
        granted_by TEXT NOT NULL,
        granted_at TEXT NOT NULL,
        PRIMARY KEY (item_type, item_id, user_id)
    )""",
    "CREATE INDEX idx_item_access_user ON item_access(user_id, item_type)",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
