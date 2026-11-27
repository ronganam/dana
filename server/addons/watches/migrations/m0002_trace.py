"""``watch_trace``: per active watch, each event of a type it listens to and what
became of it (``drop`` with the first failing condition and the value seen,
``semantic_no`` / ``semantic_yes`` with the small model's reason, ``fired``,
``cooldown``, ``skipped``, ``outside_window``, ``during``, ``unstored``: a count of far
misses over the hourly cap). Kept 30 days, at most 2000 rows per watch
(docs/watches.md, "Trace"). No foreign key on the watch: a deleted watch's
rows stay for its undo and age out."""

import sqlite3

VERSION = 2
NAME = "trace"

STATEMENTS = [
    """CREATE TABLE watch_trace (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        watch_id TEXT NOT NULL,
        at TEXT NOT NULL,
        trigger_id TEXT NOT NULL DEFAULT '',
        event_type TEXT NOT NULL DEFAULT '',
        event_id TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        data TEXT NOT NULL DEFAULT '{}',
        outcome TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        near INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX idx_watch_trace_watch ON watch_trace(user_id, watch_id, id)",
    "CREATE INDEX idx_watch_trace_at ON watch_trace(at)",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
