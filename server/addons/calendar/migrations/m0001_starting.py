"""The calendar addon's first table: ``calendar_starting``, one row per event
start ``calendar.event.starting`` was emitted for (user, event id, start), with
the data it was emitted with: emitted once across restarts, and the event's
history for dry runs of watches (pruned after 30 days)."""

import sqlite3

VERSION = 1
NAME = "starting"

STATEMENTS = [
    """CREATE TABLE calendar_starting (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        event_id TEXT NOT NULL,
        start TEXT NOT NULL,
        emitted_at TEXT NOT NULL,
        data TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (user_id, event_id, start)
    )""",
    "CREATE INDEX idx_calendar_starting_emitted ON calendar_starting(user_id, emitted_at)",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
