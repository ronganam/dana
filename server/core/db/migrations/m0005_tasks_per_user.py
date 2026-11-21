"""Task lists and tasks belong to a user (docs/access.md, "Sharing";
docs/tasks-and-triggers.md).

- ``task_groups`` (the lists): ``user_id`` (owner); a name is unique per user
  (it was unique on the whole server). The table is rebuilt to change that
  constraint, keeping every row and id; ``tasks.group_id`` references it, so
  this migration runs with foreign keys off (``FOREIGN_KEYS_OFF``).
- ``tasks``: ``user_id`` (owner: the owner of the task's list) and
  ``created_by`` (who created it; reminders go to them).
- ``task_trigger_marks``: the refire / flap / cooldown marks of the trigger
  engine per user (they were global ``last_trigger_v2_*`` /
  ``last_event_v2_*`` rows of ``memories``; moved here with their keys).

Every existing list, task and mark goes to the first admin (created from env
when the database has rows but no user yet).
"""

import sqlite3

from core import user_rows

VERSION = 5
NAME = "tasks_per_user"
FOREIGN_KEYS_OFF = True

TASK_GROUPS = """CREATE TABLE task_groups_new (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    emoji TEXT DEFAULT '📁',
    color TEXT DEFAULT '#6366f1',
    description TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, name)
)"""

GROUP_COLUMNS = "id, name, emoji, color, description, sort_order, created_at, updated_at"

STATEMENTS_AFTER = [
    # '' never names a user: an insert that forgets the owner fails its foreign key.
    "ALTER TABLE tasks ADD COLUMN user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE",
    "ALTER TABLE tasks ADD COLUMN created_by TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX idx_tasks_user_status ON tasks(user_id, status)",
    "CREATE INDEX idx_tasks_group ON tasks(group_id)",
    "CREATE INDEX idx_tasks_created_by ON tasks(created_by, status)",
    """CREATE TABLE task_trigger_marks (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (user_id, key)
    )""",
]

_MARK_PATTERNS = ("last_trigger_v2_%", "last_event_v2_%")


def up(conn: sqlite3.Connection) -> None:
    owner = user_rows.owner_of_existing_rows(conn, ("task_groups", "tasks"))
    conn.execute(TASK_GROUPS)
    conn.execute(f"INSERT INTO task_groups_new (user_id, {GROUP_COLUMNS}) SELECT ?, {GROUP_COLUMNS} FROM task_groups",
                 (owner,))
    conn.execute("DROP TABLE task_groups")
    conn.execute("ALTER TABLE task_groups_new RENAME TO task_groups")
    for sql in STATEMENTS_AFTER:
        conn.execute(sql)
    if owner is not None:
        conn.execute("UPDATE tasks SET user_id = ?, created_by = ?", (owner, owner))
        for pattern in _MARK_PATTERNS:
            conn.execute("INSERT INTO task_trigger_marks (user_id, key, at) "
                         "SELECT ?, key, value FROM memories WHERE key LIKE ?", (owner, pattern))
    for pattern in _MARK_PATTERNS:
        conn.execute("DELETE FROM memories WHERE key LIKE ?", (pattern,))
