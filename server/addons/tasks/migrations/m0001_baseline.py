"""The tasks addon's schema as of Dana 9.0: the old single-numbered
migrations 1-34 squashed into one (a database made by Dana 9.0 is converted in place:
core/db/convert.py). Tables: ``tasks``, ``task_links``, ``task_groups``, ``task_trigger_marks``.
"""

import sqlite3

VERSION = 1
NAME = "baseline"

STATEMENTS = [
    """CREATE TABLE tasks (
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
        project_id TEXT REFERENCES projects(id)
    , user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE, created_by TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE task_links (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        ref_type TEXT NOT NULL,
        ref_id TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'origin',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(task_id, ref_type, ref_id)
    )""",
    """CREATE INDEX idx_task_links_ref ON task_links(ref_type, ref_id)""",
    """CREATE TABLE "task_groups" (
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
)""",
    """CREATE INDEX idx_tasks_user_status ON tasks(user_id, status)""",
    """CREATE INDEX idx_tasks_group ON tasks(group_id)""",
    """CREATE INDEX idx_tasks_created_by ON tasks(created_by, status)""",
    """CREATE TABLE task_trigger_marks (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (user_id, key)
    )""",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
