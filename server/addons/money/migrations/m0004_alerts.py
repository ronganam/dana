"""``money_alerts``: the money alerts sent to a user (addons/money/alerts.py), one row per alert key
(``low_balance:2026-12-10``, ``unusual:<transaction id>``, ``move:2026-12``…) so each goes out once;
``data`` keeps what the alert said (for "this month's move": the savings item it picked)."""

import sqlite3

VERSION = 4
NAME = "alerts"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE money_alerts (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        kind TEXT NOT NULL,
        sent_at TEXT NOT NULL,
        delivered INTEGER NOT NULL DEFAULT 0,
        data TEXT,
        PRIMARY KEY (user_id, key)
    )""")
