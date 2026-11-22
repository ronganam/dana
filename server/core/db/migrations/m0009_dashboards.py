"""Adds ``dashboards``: the dashboard of a tablet or TV (core/dashboard.py), one
row per device once someone chose a preset or edited it: the device's owner,
the preset (a room: kitchen, bedroom, hall, living_room) and the widgets as
JSON (NULL: the preset's own, unedited), who changed it and when. Removing the
device or its user removes the row (``ON DELETE CASCADE``)."""

import sqlite3

VERSION = 9
NAME = "dashboards"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE dashboards (
        device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        preset TEXT NOT NULL,
        widgets TEXT,
        updated_by TEXT,
        updated_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX idx_dashboards_user ON dashboards(user_id)")
