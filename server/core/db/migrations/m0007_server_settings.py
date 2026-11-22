"""Adds ``server_settings``: server-wide values chosen in the app rather than
in env (core/server_settings.py), one row per key with its JSON value, who
changed it (a user id, NULL for the server itself) and when. Secret values
(the LLM key) are stored encrypted with the credentials key inside the value."""

import sqlite3

VERSION = 7
NAME = "server_settings"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE server_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        changed_by TEXT,
        changed_at TEXT NOT NULL
    )""")
