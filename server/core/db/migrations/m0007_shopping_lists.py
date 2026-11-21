"""Shopping lists belong to a user (docs/access.md, "Sharing").

- ``shopping_lists``: one row per list (id, owner ``user_id``, name); a name
  is unique per user. Every user with ``shopping`` has their own list.
- ``shopping_items.list_id``: the list an item is on.
- ``shopping_operations.list_id`` / ``user_id``: the list an operation (the
  undo log) changed and the user whose action it was ("undo last" reverses
  only that user's own changes on that list).

The one implicit list there was becomes the first admin's list (created from
env when the database has rows but no user yet): every item keeps its id and
position, every operation goes to that list and to the first admin. Adding a
column with a ``REFERENCES`` clause and a non-NULL default needs foreign keys
off (``FOREIGN_KEYS_OFF``); '' never names a list or user, so an insert that
forgets either fails its foreign key afterwards.
"""

import sqlite3
import uuid

from core import user_rows

VERSION = 7
NAME = "shopping_lists"
FOREIGN_KEYS_OFF = True

# The name a user's own list gets (integrations/shopping/lists.DEFAULT_NAME).
DEFAULT_NAME = "Shopping list"

STATEMENTS = [
    """CREATE TABLE shopping_lists (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, name)
    )""",
    "ALTER TABLE shopping_items ADD COLUMN list_id TEXT NOT NULL DEFAULT '' "
    "REFERENCES shopping_lists(id) ON DELETE CASCADE",
    "ALTER TABLE shopping_operations ADD COLUMN list_id TEXT NOT NULL DEFAULT '' "
    "REFERENCES shopping_lists(id) ON DELETE CASCADE",
    "ALTER TABLE shopping_operations ADD COLUMN user_id TEXT NOT NULL DEFAULT '' "
    "REFERENCES users(id) ON DELETE CASCADE",
    "CREATE INDEX idx_shopping_items_list ON shopping_items(list_id, checked, position)",
    "CREATE INDEX idx_shopping_ops_list_user ON shopping_operations(list_id, user_id, undone, created_at)",
]


def up(conn: sqlite3.Connection) -> None:
    owner = user_rows.owner_of_existing_rows(conn, ("shopping_items", "shopping_operations"))
    for sql in STATEMENTS:
        conn.execute(sql)
    if owner is None:
        return
    list_id = uuid.uuid4().hex[:12]
    conn.execute("INSERT INTO shopping_lists (id, user_id, name) VALUES (?, ?, ?)", (list_id, owner, DEFAULT_NAME))
    conn.execute("UPDATE shopping_items SET list_id = ?", (list_id,))
    conn.execute("UPDATE shopping_operations SET list_id = ?, user_id = ?", (list_id, owner))
