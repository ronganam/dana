"""Phone calls and suggestions belong to a user (docs/calls.md, docs/access.md "Kinds").

- ``phone_calls.user_id``: whose call it is (it was uploaded with that user's
  API token). The FTS index ``phone_calls_fts`` and its triggers are left
  exactly as they are (rows keyed by the call id, which is kept); searches
  join it to ``phone_calls`` and filter by the owner.
- ``call_suggestions.user_id``: whose suggestion it is, for call-sourced and
  notification-sourced rows alike, so no read needs a join to find the owner.
  A notification's suggestion goes to the notification's user (migration
  ``m0013`` gave every notification an owner); a suggestion whose source is
  gone goes to the first admin, like every call.

Every existing row goes to the first admin (created from env when the
database has rows but no user yet), ids kept. Recording files on disk are not
touched: they are found through their call's row. Adding a column with a
``REFERENCES`` clause and a non-NULL default needs foreign keys off
(``FOREIGN_KEYS_OFF``); '' never names a user, so an insert that forgets the
owner fails its foreign key afterwards.
"""

import sqlite3

from core import user_rows

VERSION = 14
NAME = "calls_per_user"
FOREIGN_KEYS_OFF = True

OWNER = "user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE"

STATEMENTS = [
    f"ALTER TABLE phone_calls ADD COLUMN {OWNER} DEFAULT ''",
    "CREATE INDEX idx_phone_calls_user ON phone_calls(user_id, call_timestamp)",
    f"ALTER TABLE call_suggestions ADD COLUMN {OWNER} DEFAULT ''",
    "CREATE INDEX idx_call_suggestions_user ON call_suggestions(user_id, status)",
]


def up(conn: sqlite3.Connection) -> None:
    owner = user_rows.owner_of_existing_rows(conn, ("phone_calls", "call_suggestions"))
    for sql in STATEMENTS:
        conn.execute(sql)
    # A notification's suggestions follow the notification's user.
    conn.execute("UPDATE call_suggestions SET user_id = (SELECT n.user_id FROM notifications n "
                 "WHERE n.id = call_suggestions.call_id) "
                 "WHERE source_type = 'notification' AND call_id IN (SELECT id FROM notifications)")
    if owner is not None:
        conn.execute("UPDATE phone_calls SET user_id = ?", (owner,))
        conn.execute("UPDATE call_suggestions SET user_id = ? WHERE user_id = ''", (owner,))
