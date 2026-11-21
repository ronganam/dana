"""The knowledge addon's schema as of Dana 9.0: the old single-numbered
migrations 1-34 squashed into one (a database made by Dana 9.0 is converted in place:
core/db/convert.py). Tables: ``projects``, ``entries``, ``entries_fts``, ``project_sections``, ``entry_files``.
"""

import sqlite3

VERSION = 1
NAME = "baseline"

STATEMENTS = [
    """CREATE TABLE projects (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        icon TEXT DEFAULT '📁',
        color TEXT DEFAULT '#6366f1',
        tags TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    , user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE, target_date TEXT, budget REAL)""",
    """CREATE TABLE entries (
        id TEXT PRIMARY KEY,
        project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
        kind TEXT NOT NULL DEFAULT 'note',
        title TEXT NOT NULL,
        body TEXT,
        fields TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'open',
        priority TEXT NOT NULL DEFAULT 'medium',
        due_at TEXT,
        tags TEXT NOT NULL DEFAULT '[]',
        url TEXT,
        media_path TEXT,
        transcript TEXT,
        summary TEXT,
        comments TEXT NOT NULL DEFAULT '[]',
        source_author TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    , user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE, created_by TEXT NOT NULL DEFAULT '', url_set_by TEXT, section_id TEXT REFERENCES project_sections(id) ON DELETE SET NULL, decision TEXT NOT NULL DEFAULT '')""",
    """CREATE VIRTUAL TABLE entries_fts USING fts5(
        id UNINDEXED, project_id UNINDEXED, title, body, summary, transcript, tags, fields,
        tokenize='unicode61'
    )""",
    """CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
        INSERT INTO entries_fts(id, project_id, title, body, summary, transcript, tags, fields)
        VALUES (new.id, new.project_id, new.title, new.body, new.summary, new.transcript, new.tags, new.fields);
    END""",
    """CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
        DELETE FROM entries_fts WHERE id = old.id;
    END""",
    """CREATE TRIGGER entries_au AFTER UPDATE ON entries BEGIN
        DELETE FROM entries_fts WHERE id = old.id;
        INSERT INTO entries_fts(id, project_id, title, body, summary, transcript, tags, fields)
        VALUES (new.id, new.project_id, new.title, new.body, new.summary, new.transcript, new.tags, new.fields);
    END""",
    """CREATE INDEX idx_projects_user ON projects(user_id, status)""",
    """CREATE INDEX idx_entries_user ON entries(user_id)""",
    """CREATE INDEX idx_entries_project ON entries(project_id)""",
    """CREATE TABLE project_sections (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        icon TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT 'notes',
        columns TEXT NOT NULL DEFAULT '[]',
        summary TEXT NOT NULL DEFAULT '',
        position INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE INDEX idx_sections_project ON project_sections(project_id, position)""",
    """CREATE INDEX idx_entries_section ON entries(section_id)""",
    """CREATE TABLE entry_files (
        id TEXT PRIMARY KEY,
        entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        mime TEXT NOT NULL DEFAULT 'application/octet-stream',
        size INTEGER NOT NULL DEFAULT 0,
        stored TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        transcript TEXT,
        source TEXT NOT NULL DEFAULT 'upload',
        created_by TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE INDEX idx_entry_files_entry ON entry_files(entry_id)""",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
