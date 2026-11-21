"""Google Contacts per user, and what people take from them (docs/contacts.md).

- ``google_contacts``: each user's copy of their Google contacts, one row per
  ``(user_id, resource_name)`` (``people/c…``): the ``etag`` Google gave, the
  name, ``other_names`` (JSON list: other name forms and nicknames),
  ``phones`` (JSON list of ``{number, value, type}``; ``number`` is the digits
  normalised like ``person_phones.phone``, '' when it is not a number),
  ``emails`` (JSON list of ``{value, type}``), ``birthday`` (``YYYY-MM-DD`` /
  ``MM-DD``), ``organisation`` and ``job_title``.
- ``google_contact_phones``: number → contact of the same user, for looking a
  caller up (a number may be on several contacts).
- ``google_contacts_sync``: per user, the People API sync token and when the
  copy was last brought up to date.
- ``people.emails`` (JSON list) and ``people.contact_resource`` (the Google
  contact a person was filled in from, '' for none).
"""

import sqlite3

VERSION = 23
NAME = "google_contacts"

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
    "CREATE INDEX idx_google_contact_phones_contact ON google_contact_phones(user_id, resource_name)",
    """CREATE TABLE google_contacts_sync (
        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        sync_token TEXT NOT NULL DEFAULT '',
        synced_at TEXT
    )""",
    "ALTER TABLE people ADD COLUMN emails TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE people ADD COLUMN contact_resource TEXT NOT NULL DEFAULT ''",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
