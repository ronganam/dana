"""Adds ``connection_reconnects``: a connection whose credentials stopped
working (a sign-in revoked, a token refused, a bank login refused), as its
addon reported it (core/addons/store.py ``needs_reconnect``). ``part`` is ""
for the whole connection, else the piece of it that needs the user (a money
source's id), with ``label`` naming that piece; ``reason`` says why in plain
words and ``since`` when it was first reported. Disconnecting the addon (or
removing the user) removes its rows (``ON DELETE CASCADE``)."""

import sqlite3

VERSION = 10
NAME = "reconnects"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE connection_reconnects (
        user_id TEXT NOT NULL,
        addon TEXT NOT NULL,
        part TEXT NOT NULL DEFAULT '',
        label TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL,
        since TEXT NOT NULL,
        PRIMARY KEY (user_id, addon, part),
        FOREIGN KEY (user_id, addon) REFERENCES connections(user_id, addon) ON DELETE CASCADE
    )""")
