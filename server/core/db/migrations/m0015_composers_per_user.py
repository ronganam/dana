"""The composers' own state belongs to a user (docs/now.md, docs/people.md, docs/access.md).

- ``now_hidden`` (hidden / snoozed / ignored Now cards): key ``(user_id,
  item_id)``; two users may hide the same check.
- ``briefings``: one per user and local date (key ``(user_id, date)``).
- ``weekly_reviews.user_id``: whose review it is (one per user and ISO week).
- ``wake_log``: one row per user and confirmed wake date (key ``(user_id,
  date)``). It is now the only record of a wake: the old
  ``last_wake_trigger_date`` row of ``memories`` (not per user) moves here,
  to the first admin, as a row whose ``at`` is '' (the date was known, not
  the time: the week view shows it as "✓", as before) unless that date is
  already logged; then it leaves ``memories``.
- The self-check mark ``wake_signal`` becomes the first admin's
  (``wake_signal:<user id>``), like the phone's marks in ``m0013``.

Every existing row goes to the first admin (created from env when the
database has rows but no user yet), values kept. Adding a column with a
``REFERENCES`` clause and a non-NULL default needs foreign keys off
(``FOREIGN_KEYS_OFF``); '' never names a user, so an insert that forgets the
owner fails its foreign key afterwards.
"""

import sqlite3
from typing import Optional, Sequence

from core import user_rows

VERSION = 15
NAME = "composers_per_user"
FOREIGN_KEYS_OFF = True

OWNER = "user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE"

NOW_HIDDEN = f"""CREATE TABLE now_hidden_new (
    {OWNER},
    item_id TEXT NOT NULL,
    until TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, item_id)
)"""
BRIEFINGS = f"""CREATE TABLE briefings_new (
    {OWNER},
    date TEXT NOT NULL,
    headline TEXT NOT NULL,
    points TEXT NOT NULL DEFAULT '[]',
    data TEXT NOT NULL DEFAULT '{{}}',
    trigger TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    PRIMARY KEY (user_id, date)
)"""
WAKE_LOG = f"""CREATE TABLE wake_log_new (
    {OWNER},
    date TEXT NOT NULL,
    at TEXT NOT NULL,
    why TEXT,
    PRIMARY KEY (user_id, date)
)"""

STATEMENTS = [
    f"ALTER TABLE weekly_reviews ADD COLUMN {OWNER} DEFAULT ''",
    "CREATE INDEX idx_weekly_reviews_user ON weekly_reviews(user_id, week)",
]

WAKE_MEMORY = "last_wake_trigger_date"
MARKS = ("wake_signal",)


def _rebuild(conn: sqlite3.Connection, table: str, create: str, columns: Sequence[str], owner: Optional[str]) -> None:
    """``table`` again from ``create`` (which makes ``<table>_new``), its rows
    copied with ``owner`` as their user."""
    conn.execute(create)
    if owner is not None:
        cols = ", ".join(columns)
        conn.execute(f"INSERT INTO {table}_new (user_id, {cols}) SELECT ?, {cols} FROM {table}", (owner,))
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")


def up(conn: sqlite3.Connection) -> None:
    owner = user_rows.owner_of_existing_rows(conn, ("now_hidden", "briefings", "weekly_reviews", "wake_log"))
    wake = conn.execute("SELECT value FROM memories WHERE key = ?", (WAKE_MEMORY,)).fetchone()
    if owner is None and wake is not None:
        owner = user_rows.owner_of_existing_rows(conn, ("memories",))
    for sql in STATEMENTS:
        conn.execute(sql)
    if owner is not None:
        conn.execute("UPDATE weekly_reviews SET user_id = ?", (owner,))
    _rebuild(conn, "now_hidden", NOW_HIDDEN, ("item_id", "until", "reason", "created_at"), owner)
    _rebuild(conn, "briefings", BRIEFINGS,
             ("date", "headline", "points", "data", "trigger", "created_at", "delivered_at"), owner)
    _rebuild(conn, "wake_log", WAKE_LOG, ("date", "at", "why"), owner)

    if wake is not None:
        if owner is not None and wake[0]:
            conn.execute("INSERT OR IGNORE INTO wake_log (user_id, date, at, why) VALUES (?, ?, '', '')",
                         (owner, wake[0]))
        conn.execute("DELETE FROM memories WHERE key = ?", (WAKE_MEMORY,))

    for mark in MARKS:
        if owner is not None:
            conn.execute("UPDATE system_marks SET key = ? WHERE key = ?", (f"{mark}:{owner}", mark))
        else:
            conn.execute("DELETE FROM system_marks WHERE key = ?", (mark,))
