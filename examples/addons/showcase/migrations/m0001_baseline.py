"""The ``showcase`` tables: plants per user (when each was last watered), and the messages its channel kept.

Applied once, at the first start with the addon; a later change is the next
file (``m0002_<name>.py``), never an edit of this one.
"""

import sqlite3

# --- reference: migration ---
VERSION = 1
NAME = "baseline"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE showcase_plants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        every_days INTEGER NOT NULL,
        last_watered TEXT,
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX idx_showcase_plants_user ON showcase_plants(user_id)")
    conn.execute("""CREATE TABLE showcase_inbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
# --- end ---
