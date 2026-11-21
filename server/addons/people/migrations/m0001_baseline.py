"""The people addon's schema as of Dana 9.0: the old single-numbered
migrations 1-34 squashed into one (a database made by Dana 9.0 is converted in place:
core/db/convert.py). Tables: ``people``, ``person_phones``.
"""

import sqlite3

VERSION = 1
NAME = "baseline"

STATEMENTS = [
    """CREATE TABLE people (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        aliases TEXT NOT NULL DEFAULT '[]',
        relation TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        birthday TEXT NOT NULL DEFAULT '',
        pinned INTEGER NOT NULL DEFAULT 0,
        hidden INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'manual',
        brief TEXT,
        brief_signature TEXT,
        brief_updated_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    , user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE, emails TEXT NOT NULL DEFAULT '[]', contact_resource TEXT NOT NULL DEFAULT '')""",
    """CREATE INDEX idx_people_user ON people(user_id)""",
    """CREATE TABLE "person_phones" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    phone TEXT NOT NULL,
    person_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, phone)
)""",
    """CREATE INDEX idx_person_phones_person ON person_phones(person_id)""",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
