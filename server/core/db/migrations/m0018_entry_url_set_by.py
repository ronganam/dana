"""Who last set an entry's URL (docs/knowledge.md, docs/access.md "Security rules").

``entries.url_set_by``: the user who last set the entry's link (saved it or
changed it). A link download runs restricted whenever that user lacks
``manage_server``, whoever starts it (saving, Retry), so a link a member put
on someone else's entry is never fetched with an admin's freedom. Existing
rows: the entry's creator (``created_by``). The FTS update trigger is set
aside while the column is filled (an UPDATE of ``entries`` would delete and
re-insert every indexed row) and put back with its original SQL, as in m0008.
"""

import sqlite3

VERSION = 18
NAME = "entry_url_set_by"

_UPDATE_TRIGGER = "entries_au"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE entries ADD COLUMN url_set_by TEXT")
    if conn.execute("SELECT 1 FROM entries LIMIT 1").fetchone() is None:
        return
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                       (_UPDATE_TRIGGER,)).fetchone()
    if row is not None:
        conn.execute(f"DROP TRIGGER {_UPDATE_TRIGGER}")
    conn.execute("UPDATE entries SET url_set_by = NULLIF(created_by, '')")
    if row is not None:
        conn.execute(row[0])
