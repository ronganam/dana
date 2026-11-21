"""The contacts addon's schema as of Dana 9.0: the old single-numbered
migrations 1-34 squashed into one (a database made by Dana 9.0 is converted in place:
core/db/convert.py). Tables: ``google_contacts``, ``google_contact_phones``, ``google_contacts_sync``.
"""

import sqlite3

VERSION = 1
NAME = "baseline"

STATEMENTS = [
    """CREATE TABLE google_contacts (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        resource_name TEXT NOT NULL,
        etag TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '',
        other_names TEXT NOT NULL DEFAULT '[]',
        phones TEXT NOT NULL DEFAULT '[]',
        emails TEXT NOT NULL DEFAULT '[]',
        birthday TEXT NOT NULL DEFAULT '',
        organisation TEXT NOT NULL DEFAULT '',
        job_title TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, resource_name)
    )""",
    """CREATE TABLE google_contact_phones (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        phone TEXT NOT NULL,
        resource_name TEXT NOT NULL,
        PRIMARY KEY (user_id, phone, resource_name)
    )""",
    """CREATE INDEX idx_google_contact_phones_contact ON google_contact_phones(user_id, resource_name)""",
    """CREATE TABLE google_contacts_sync (
        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        sync_token TEXT NOT NULL DEFAULT '',
        synced_at TEXT
    )""",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
