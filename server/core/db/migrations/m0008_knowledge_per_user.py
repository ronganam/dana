"""Projects and their entries belong to a user (docs/access.md, "Sharing").

- ``projects.user_id``: the owner (only they rename, delete or share it).
- ``entries.user_id``: the owner of the entry — the owner of its project, or
  the user who created it when it has no project; ``entries.created_by``: who
  created it (an entry added to a shared project belongs to the project's
  owner and records who added it).

Every existing project and entry goes to the first admin (created from env
when the database has rows but no user yet), ids kept. The FTS index
``entries_fts`` is left exactly as it was: its update trigger is set aside
while the owners are filled in (an UPDATE of ``entries`` would otherwise
delete and re-insert every indexed row) and put back with its original SQL.
Adding a column with a ``REFERENCES`` clause and a non-NULL default needs
foreign keys off (``FOREIGN_KEYS_OFF``); '' never names a user, so an insert
that forgets the owner fails its foreign key afterwards.
"""

import sqlite3

from core import user_rows

VERSION = 8
NAME = "knowledge_per_user"
FOREIGN_KEYS_OFF = True

STATEMENTS = [
    "ALTER TABLE projects ADD COLUMN user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE",
    "ALTER TABLE entries ADD COLUMN user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE",
    "ALTER TABLE entries ADD COLUMN created_by TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX idx_projects_user ON projects(user_id, status)",
    "CREATE INDEX idx_entries_user ON entries(user_id)",
    "CREATE INDEX idx_entries_project ON entries(project_id)",
]

_UPDATE_TRIGGER = "entries_au"


def up(conn: sqlite3.Connection) -> None:
    owner = user_rows.owner_of_existing_rows(conn, ("projects", "entries"))
    for sql in STATEMENTS:
        conn.execute(sql)
    if owner is None:
        return
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                       (_UPDATE_TRIGGER,)).fetchone()
    if row is not None:
        conn.execute(f"DROP TRIGGER {_UPDATE_TRIGGER}")
    conn.execute("UPDATE projects SET user_id = ?", (owner,))
    # An entry belongs to its project's owner; one without a project to the first admin.
    conn.execute("UPDATE entries SET user_id = ?, created_by = ?", (owner, owner))
    if row is not None:
        conn.execute(row[0])
