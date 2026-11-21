"""The conversation core belongs to a user (docs/access.md, "What is per user").

- ``sessions``: ``user_id`` first, primary key ``(user_id, session_id)`` — every
  user has their own ``main`` thread row and their own sessions.
- ``thread_messages``: ``user_id``; index ``(user_id, thread_id, seq)``.
- ``shortcuts``: ``user_id``; a phrase is unique per user.
- ``usage_events``: ``user_id`` (``''`` = no user: system work); index
  ``(user_id, day)``.
- ``usage_daily``: ``user_id`` in the primary key (same ``''`` convention).

Every existing row goes to the first admin user. When there is no user yet but
there are rows to give one (a database from before users existed), the first
user is created here from env exactly as ``users.bootstrap()`` would
(:func:`core.user_rows.insert_first_user`); bootstrap then finds it.
Tables are rebuilt (SQLite cannot change a primary key); row values, ``seq``
numbers and ``sqlite_sequence`` are kept.
"""

import sqlite3
from typing import List, Optional

from core import user_rows

VERSION = 3
NAME = "user_scope"

SESSIONS = """CREATE TABLE sessions_new (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    history TEXT DEFAULT '[]',
    pending_approval TEXT DEFAULT NULL,
    status TEXT DEFAULT 'idle',
    title TEXT DEFAULT '',
    debug_info TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    context TEXT DEFAULT NULL,
    PRIMARY KEY (user_id, session_id)
)"""

THREAD_MESSAGES = """CREATE TABLE thread_messages_new (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL,
    ts REAL NOT NULL,
    device TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL
)"""

SHORTCUTS = """CREATE TABLE shortcuts_new (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    phrase_norm TEXT NOT NULL,
    phrase TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    reply TEXT NOT NULL DEFAULT '',
    streak INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 0,
    uses INTEGER NOT NULL DEFAULT 0,
    last_used REAL,
    learned_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (user_id, phrase_norm)
)"""

USAGE_DAILY = """CREATE TABLE usage_daily_new (
    day TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    purpose TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    calls INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    unpriced INTEGER NOT NULL DEFAULT 0,
    ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id, kind, purpose, source, model)
)"""

INDEXES = [
    "CREATE INDEX idx_thread_messages_user_thread ON thread_messages(user_id, thread_id, seq)",
    "CREATE INDEX idx_usage_events_user_day ON usage_events(user_id, day)",
]

_SCOPED = ("sessions", "thread_messages", "shortcuts", "usage_events", "usage_daily")


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")]


def _owner(conn: sqlite3.Connection) -> Optional[str]:
    """The first admin; created from env when there is none and some row needs an owner."""
    row = conn.execute("SELECT id FROM users WHERE access = 'admin' ORDER BY created_at, rowid LIMIT 1").fetchone()
    if row:
        return row[0]
    if not any(conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in _SCOPED):
        return None
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        raise RuntimeError("Users exist but none is an admin: cannot choose who owns the existing conversations.")
    return user_rows.insert_first_user(conn)


def _rebuild(conn: sqlite3.Connection, table: str, owner: Optional[str]) -> None:
    """Copy ``table`` into ``<table>_new`` (``user_id`` = ``owner``), then swap them."""
    new_cols = set(_columns(conn, f"{table}_new"))
    cols = [c for c in _columns(conn, table) if c in new_cols and c != "user_id"]
    names = ", ".join(cols)
    conn.execute(f"INSERT INTO {table}_new (user_id, {names}) SELECT ?, {names} FROM {table}", (owner,))
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")


def up(conn: sqlite3.Connection) -> None:
    owner = _owner(conn)
    seq_row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'thread_messages'").fetchone()

    for table, sql in (("sessions", SESSIONS), ("thread_messages", THREAD_MESSAGES),
                       ("shortcuts", SHORTCUTS), ("usage_daily", USAGE_DAILY)):
        conn.execute(sql)
        _rebuild(conn, table, owner)
    # Keep the thread's seq counter where it was (seq numbers are message ids).
    if seq_row is not None:
        cur = conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'thread_messages'",
                           (seq_row[0],))
        if not cur.rowcount:
            conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('thread_messages', ?)", (seq_row[0],))

    conn.execute("ALTER TABLE usage_events ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
    if owner is not None:
        conn.execute("UPDATE usage_events SET user_id = ?", (owner,))
    for sql in INDEXES:
        conn.execute(sql)
