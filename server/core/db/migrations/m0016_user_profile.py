"""What prompts need to know about a user belongs to their row (docs/access.md, "User").

New columns of ``users``:

- ``context``: a short free-text background (who they are, family, work, who
  the people they talk about are), read by the prompts that analyse their
  calls, notifications and people, and by the briefing / weekly review.
- ``pronoun``: ``he`` / ``she`` / ``they``, how prompts refer to them.
- ``other_names``: other spellings of their first name (another script, e.g.
  Hebrew), shown in brackets where a prompt reads transcripts.
- ``prompt_name``: what Dana's own instructions (the agent prompt, the shared
  phone screen) call them; '' means their first name.

New users get '' / ``they`` / '' / ''. The first admin (when one exists:
created from env by ``m0003`` or by an earlier start) gets the profile the
env gives (:func:`core.user_rows.first_user_profile`). A database with no user
yet gets nothing here: ``users.bootstrap()`` creates the first user with the
same values.
"""

import sqlite3

from core import access, user_rows

VERSION = 16
NAME = "user_profile"

STATEMENTS = [
    "ALTER TABLE users ADD COLUMN context TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN pronoun TEXT NOT NULL DEFAULT 'they' CHECK (pronoun IN ('he', 'she', 'they'))",
    "ALTER TABLE users ADD COLUMN other_names TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN prompt_name TEXT NOT NULL DEFAULT ''",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
    row = conn.execute("SELECT id FROM users WHERE access = ? ORDER BY created_at, rowid LIMIT 1",
                       (access.ADMIN,)).fetchone()
    if row is None:
        return
    user_rows.write_profile(conn, row[0], user_rows.first_user_profile())
