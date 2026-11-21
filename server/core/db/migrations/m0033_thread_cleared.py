"""``sessions.cleared_seq``: the shared thread's "new topic" mark. The model
sees only thread messages with a ``seq`` above it (core/thread.py); 0 = never
cleared. Older messages stay stored and shown.
"""

import sqlite3

VERSION = 33
NAME = "thread_cleared"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE sessions ADD COLUMN cleared_seq INTEGER NOT NULL DEFAULT 0")
