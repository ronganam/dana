"""Drops ``connection_imports`` and ``connection_import_settings``: addons are no
longer imported from env (they are connected on the Addons page / with
bin/users.py). A database that had imported addons is marked as having given the
first admin their default addons (``system_marks['addons:first_admin_defaults']``),
so a default addon that admin turned off is not turned on again."""

import datetime
import sqlite3

VERSION = 3
NAME = "drop_env_imports"


def up(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM connection_imports LIMIT 1").fetchone():
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        conn.execute("INSERT OR IGNORE INTO system_marks (key, last_at, ok, last_ok_at, detail) VALUES (?, ?, 1, ?, ?)",
                     ("addons:first_admin_defaults", now, now, "env import era"))
    conn.execute("DROP TABLE connection_import_settings")
    conn.execute("DROP TABLE connection_imports")
