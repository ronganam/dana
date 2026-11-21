"""The ``hello`` addon's table: people to greet, per user (``status`` pending / done).

Applied once, at the first start with the addon; a later change is the next
file (``m0002_<name>.py``), never an edit of this one.
"""

import sqlite3

VERSION = 1
NAME = "baseline"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE hello_greetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX idx_hello_greetings_user ON hello_greetings(user_id, status)")
