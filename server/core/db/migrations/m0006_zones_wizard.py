"""Adds ``zones`` (each user's own places: a name, a centre and a radius in
metres; the one with ``is_home = 1`` is their Home; names are unique per user,
any case) and ``user_wizard`` (the first-run wizard: which of its steps a user
finished or skipped, as a JSON object, and when they finished it). Every user
who exists already gets a finished wizard, so only users made from now on see
it on their first sign-in."""

import datetime
import sqlite3

VERSION = 6
NAME = "zones_wizard"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE zones (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        latitude REAL NOT NULL,
        longitude REAL NOT NULL,
        radius_m INTEGER NOT NULL DEFAULT 100,
        is_home INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE UNIQUE INDEX idx_zones_user_name ON zones(user_id, name COLLATE NOCASE)")
    conn.execute("CREATE UNIQUE INDEX idx_zones_user_home ON zones(user_id) WHERE is_home = 1")
    conn.execute("""CREATE TABLE user_wizard (
        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        steps TEXT NOT NULL DEFAULT '{}',
        finished_at TEXT
    )""")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    conn.execute("INSERT INTO user_wizard (user_id, steps, finished_at) SELECT id, '{}', ? FROM users", (now,))
