"""WhatsApp contacts and saved images belong to a user (docs/whatsapp.md,
docs/access.md "Kinds").

- ``whatsapp_contacts``: the contact rows the WhatsApp kind and the call
  analysis read, until now kept in the old ``memories`` table (not per user):
  rows whose value is a WhatsApp chat id (``…@c.us`` / ``…@g.us`` /
  ``…@newsletter``: nicknames for the name resolver and the contact search)
  and rows the call-analysis prompt lists as known people (category
  ``contact`` / ``people`` / ``person`` / ``family``, or a key with
  ``contact``). They are copied with their key, value, category and time, in
  their old order, to the first admin (who owns the old memories); the rows in
  ``memories`` stay for the About kind's one-time import of old memories
  (integrations/about/legacy.py), nothing else reads them for contacts.
  :data:`PREFERRED_CONTACTS` (the chat a first name means when several
  contacts match, held in code before this migration) become the first
  admin's rows with category ``preferred``.
- ``uploaded_images``: the owner of each file in ``uploads/images`` (served at
  ``/api/images/{name}``: chat uploads, Telegram photos, images a WhatsApp
  tool fetched). Files already in the folder next to the database go to the
  first admin; a file with no row is served to nobody.
"""

import sqlite3
from pathlib import Path
from typing import List

from core import paths, user_rows

VERSION = 11
NAME = "whatsapp_per_user"

STATEMENTS = [
    """CREATE TABLE whatsapp_contacts (
        id INTEGER PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        category TEXT,
        updated_at TEXT,
        UNIQUE (user_id, key)
    )""",
    """CREATE TABLE uploaded_images (
        name TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_uploaded_images_user ON uploaded_images(user_id)",
]

# The rows of ``memories`` that are contacts (see the module docstring).
CONTACT_ROWS = ("(value LIKE '%@c.us' OR value LIKE '%@g.us' OR value LIKE '%@newsletter' "
                "OR category IN ('contact', 'people', 'person', 'family') OR key LIKE '%contact%')")


# The first admin's preferred contacts, as (first name, chat id), held in
# integrations/whatsapp/client.py before this migration. Empty in the public code:
# an install that had them already ran this migration.
PREFERRED_CONTACTS: tuple = ()


def _image_files(conn: sqlite3.Connection) -> List[str]:
    """Names of the files in ``uploads/images`` next to the database being migrated."""
    row = conn.execute("PRAGMA database_list").fetchone()
    if not row or not row[2]:
        return []
    folder = Path(row[2]).parent / paths.IMAGES_DIR.relative_to(paths.DATA_DIR)
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
    owner = user_rows.owner_of_existing_rows(conn, ("memories",))
    if owner is not None:
        conn.execute("INSERT INTO whatsapp_contacts (user_id, key, value, category, updated_at) "
                     f"SELECT ?, key, value, category, updated_at FROM memories WHERE {CONTACT_ROWS} ORDER BY rowid",
                     (owner,))
        conn.executemany("INSERT OR IGNORE INTO whatsapp_contacts (user_id, key, value, category, updated_at) "
                         "VALUES (?, ?, ?, 'preferred', ?)",
                         [(owner, key, value, user_rows.now_iso()) for key, value in PREFERRED_CONTACTS])
    files = _image_files(conn)
    if files:
        if owner is None:
            owner = user_rows.insert_first_user(conn)
        now = user_rows.now_iso()
        conn.executemany("INSERT INTO uploaded_images (name, user_id, created_at) VALUES (?, ?, ?)",
                         [(name, owner, now) for name in files])
