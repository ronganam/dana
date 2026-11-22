"""Adds ``phone_screen_sessions`` (the stretches the user's phone screen was
on, as the app reports them: start and end in epoch milliseconds, one row per
start, kept a few days; addons/phone/signals.py) and ``phone_signal_keys``
(a hash of each event of ``POST /api/signals`` accepted in the last 48 hours,
so a batch the app sends again has no second effect; only the hash and when)."""

import sqlite3

VERSION = 4
NAME = "signals"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE phone_screen_sessions (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        start_ms INTEGER NOT NULL,
        end_ms INTEGER NOT NULL,
        PRIMARY KEY (user_id, start_ms)
    )""")
    conn.execute("""CREATE TABLE phone_signal_keys (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (user_id, key)
    )""")
    conn.execute("CREATE INDEX idx_phone_signal_keys_at ON phone_signal_keys(at)")
