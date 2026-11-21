"""Adds ``users.languages`` (a comma list of language codes, the first the
default reply language) and ``users.currency`` (ISO 4217). Existing users get
``''`` in both: they follow the server's ``DEFAULT_LANGUAGES`` / ``CURRENCY``
(core/user_locale.py) until they choose their own on the Profile page."""

import sqlite3

VERSION = 4
NAME = "user_locale"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE users ADD COLUMN languages TEXT NOT NULL DEFAULT ''")
    conn.execute("ALTER TABLE users ADD COLUMN currency TEXT NOT NULL DEFAULT ''")
