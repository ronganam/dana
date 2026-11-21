"""Knowledge sections, decisions and files (docs/knowledge.md).

- ``project_sections``: the parts of a project. ``kind`` 'options' (a decision
  among options sharing typed ``columns``, a JSON array of {key, name, type,
  role?, options?}) or 'notes' (free items); ``summary`` is the section's
  Markdown write-up (the TL;DR / recommendation); ``position`` orders them.
  A section follows its project (no owner column of its own).
- ``entries.section_id``: the section an entry sits in (NULL: none, shown as
  the project's loose items; a deleted section leaves its entries loose).
  ``entries.decision``: '' | 'shortlist' | 'chosen' | 'rejected' for an
  option.
- ``projects.target_date`` (ISO date, the day the project points at, e.g. the
  wedding) and ``projects.budget`` (a number in the user's currency).
- ``entry_files``: files kept on an entry (a quote PDF, a voice note, a video),
  stored under ``<data dir>/knowledge/`` as ``stored``; ``transcript`` for
  audio; ``source`` 'upload' | 'whatsapp'; ``created_by`` who added it.

Existing rows: entries keep no section and no decision; projects get no date
and no budget. The FTS update trigger is not involved (the new entry columns
are not indexed and no existing row is updated).
"""

import sqlite3

VERSION = 32
NAME = "knowledge_sections"

_STATEMENTS = (
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
    "CREATE INDEX idx_sections_project ON project_sections(project_id, position)",
    "ALTER TABLE entries ADD COLUMN section_id TEXT REFERENCES project_sections(id) ON DELETE SET NULL",
    "ALTER TABLE entries ADD COLUMN decision TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX idx_entries_section ON entries(section_id)",
    "ALTER TABLE projects ADD COLUMN target_date TEXT",
    "ALTER TABLE projects ADD COLUMN budget REAL",
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
    "CREATE INDEX idx_entry_files_entry ON entry_files(entry_id)",
)


def up(conn: sqlite3.Connection) -> None:
    for statement in _STATEMENTS:
        conn.execute(statement)
