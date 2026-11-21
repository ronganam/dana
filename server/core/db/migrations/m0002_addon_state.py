"""``addon_state``: the addons an admin disabled on this server, for everyone
(core/addons/state.py; ../docs/addons.md, "Where addons come from"). One row
per addon whose state was ever changed: ``disabled`` 1 or 0, who changed it
(a user id, NULL for the server's own CLI) and when.
"""

import sqlite3

VERSION = 2
NAME = "addon_state"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE addon_state (
        addon TEXT PRIMARY KEY,
        disabled INTEGER NOT NULL DEFAULT 0,
        changed_by TEXT,
        changed_at TEXT NOT NULL
    )""")
