"""Adds ``devices`` (one row per app instance: a phone, watch, tablet, TV,
browser or other client of a user, with its name, kind, room, household role,
its own API token in ``user_tokens`` and when it was last seen) and
``device_pairings`` (pairing codes, stored hashed, valid a few minutes, used
once). Revoking a device's token removes the device (``ON DELETE CASCADE``).
Kinds and roles are checked in code (core/devices.py), so adding one needs no
migration."""

import sqlite3

VERSION = 5
NAME = "devices"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE devices (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL,
        room TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT '',
        token_id TEXT UNIQUE REFERENCES user_tokens(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        last_seen_at TEXT
    )""")
    conn.execute("CREATE INDEX idx_devices_user ON devices(user_id)")
    conn.execute("""CREATE TABLE device_pairings (
        id TEXT PRIMARY KEY,
        code_hash TEXT NOT NULL UNIQUE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        room TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT,
        device_id TEXT REFERENCES devices(id) ON DELETE SET NULL
    )""")
    conn.execute("CREATE INDEX idx_device_pairings_creator ON device_pairings(created_by)")
