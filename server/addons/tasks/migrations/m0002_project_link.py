"""``tasks.project_id`` loses its foreign key to ``projects`` (the knowledge
addon's table): an addon references only its own tables and core's. The
column and every row stay; when a project is deleted the knowledge addon
emits ``knowledge.project.deleted`` and the tasks addon clears the link
(addons/tasks/hooks.py). SQLite cannot drop a constraint in place, so the
table is rebuilt (with foreign keys off, the table-rebuild procedure) and its
indexes made again.
"""

import sqlite3

VERSION = 2
NAME = "project_link"
FOREIGN_KEYS_OFF = True

COLUMNS = ("id, title, description, priority, due_date, status, triggers, group_id, created_at, updated_at, "
           "snooze_count, project_id, user_id, created_by")

STATEMENTS = [
    """CREATE TABLE tasks_new (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        priority TEXT DEFAULT 'medium',
        due_date TEXT,
        status TEXT DEFAULT 'needs_action',
        triggers TEXT DEFAULT '{}',
        group_id TEXT REFERENCES task_groups(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        snooze_count INTEGER NOT NULL DEFAULT 0,
        project_id TEXT,
        user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE,
        created_by TEXT NOT NULL DEFAULT ''
    )""",
    f"INSERT INTO tasks_new ({COLUMNS}) SELECT {COLUMNS} FROM tasks",
    "DROP TABLE tasks",
    "ALTER TABLE tasks_new RENAME TO tasks",
    "CREATE INDEX idx_tasks_user_status ON tasks(user_id, status)",
    "CREATE INDEX idx_tasks_group ON tasks(group_id)",
    "CREATE INDEX idx_tasks_created_by ON tasks(created_by, status)",
    "CREATE INDEX idx_tasks_project ON tasks(project_id)",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
