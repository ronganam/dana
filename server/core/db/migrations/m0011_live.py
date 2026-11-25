"""Adds ``live_activities``: each user's live activities (core/live.py), one
row per (user, addon, key); the wire id is ``<addon>:<key>``. ``state`` is the
activity as JSON (what the addon gave), ``selected`` the choice the user is on,
``rev`` counts the changes clients saw, ``alert_seen`` the last alert value
pushed. ``refreshed_at`` is when the addon's ``live_refresh`` last ran (or the
start), ``refresh_at`` when it is due next (NULL: never), ``ends_at`` when the
activity expires by itself. An ended activity with a ``final`` keeps its row
(``ended_at`` set) listed for a few minutes; one without (or past its final)
stays hidden (``final`` NULL) for a day so a new start under the same key
carries on its ``rev`` (core/live.py), then is deleted. Times
are UTC ISO 8601 with microseconds, so they compare as text. Removing the user
removes their rows (``ON DELETE CASCADE``)."""

import sqlite3

VERSION = 11
NAME = "live"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE live_activities (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        addon TEXT NOT NULL,
        key TEXT NOT NULL,
        state TEXT NOT NULL,
        selected TEXT NOT NULL DEFAULT '',
        rev INTEGER NOT NULL DEFAULT 1,
        alert_seen TEXT NOT NULL DEFAULT '',
        started_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        refreshed_at TEXT NOT NULL,
        refresh_at TEXT,
        ends_at TEXT,
        ended_at TEXT,
        final TEXT,
        PRIMARY KEY (user_id, addon, key)
    )""")
    conn.execute("CREATE INDEX live_activities_refresh ON live_activities(refresh_at) WHERE ended_at IS NULL")
    conn.execute("CREATE INDEX live_activities_ends ON live_activities(ends_at) WHERE ended_at IS NULL")
