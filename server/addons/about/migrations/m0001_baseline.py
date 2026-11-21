"""The about addon's schema as of Dana 9.0: the old single-numbered
migrations 1-34 squashed into one (a database made by Dana 9.0 is converted in place:
core/db/convert.py). Tables: ``about_facts``, ``about_state``.
"""

import sqlite3

VERSION = 1
NAME = "baseline"

STATEMENTS = [
    """CREATE TABLE about_facts (
        id TEXT PRIMARY KEY,
        section TEXT NOT NULL DEFAULT 'other',
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        source_kind TEXT NOT NULL DEFAULT 'owner',
        source_ref TEXT,
        source_label TEXT,
        legacy_key TEXT UNIQUE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        confirmed_at TEXT,
        inactive_at TEXT
    , user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE)""",
    """CREATE INDEX idx_about_facts_status ON about_facts(status, section)""",
    """CREATE TABLE "about_state" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
)""",
    """CREATE INDEX idx_about_facts_user ON about_facts(user_id, status)""",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
