"""The watches addon's tables: ``watches`` (one row per watch: its spec JSON,
status, the reminder's task, counters and the next time it acts) and
``watch_fires`` (what fired, on which event, and what each action did; pruned
after 90 days)."""

import sqlite3

VERSION = 1
NAME = "watches"

STATEMENTS = [
    """CREATE TABLE watches (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'watch',
        status TEXT NOT NULL DEFAULT 'active',
        request TEXT NOT NULL DEFAULT '',
        spec TEXT NOT NULL DEFAULT '{}',
        task_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        next_time TEXT,
        fires_count INTEGER NOT NULL DEFAULT 0,
        last_fired_at TEXT,
        closed_at TEXT
    )""",
    """CREATE TABLE watch_fires (
        id TEXT PRIMARY KEY,
        watch_id TEXT NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        at TEXT NOT NULL,
        trigger_id TEXT NOT NULL DEFAULT '',
        event_type TEXT NOT NULL DEFAULT '',
        event TEXT NOT NULL DEFAULT '{}',
        actions TEXT NOT NULL DEFAULT '[]'
    )""",
    "CREATE INDEX idx_watches_user_status ON watches(user_id, status)",
    "CREATE INDEX idx_watches_task ON watches(user_id, task_id)",
    "CREATE INDEX idx_watch_fires_watch ON watch_fires(user_id, watch_id, at)",
    "CREATE INDEX idx_watch_fires_at ON watch_fires(at)",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
