"""Adds ``signal_sources`` (per user, signal and source: when that source
last delivered the signal and a short word about it, the one row each;
core/signals.py) and ``wake_windows`` (per user: the wake window they set,
the one learned from their first mornings and those mornings' first unlock
times, at most a week of ``{date: "HH:MM"}``; core/wake_window.py). The
wake signal mark ``system_marks['wake_signal:<user id>']`` is replaced by the
user's ``signal_sources`` row and deleted."""

import sqlite3

VERSION = 8
NAME = "signals"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE signal_sources (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        signal TEXT NOT NULL,
        source TEXT NOT NULL,
        last_at TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (user_id, signal, source)
    )""")
    conn.execute("""CREATE TABLE wake_windows (
        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        set_start TEXT,
        set_end TEXT,
        learned_start TEXT,
        learned_end TEXT,
        mornings TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL
    )""")
    conn.execute("DELETE FROM system_marks WHERE key LIKE 'wake_signal:%'")
