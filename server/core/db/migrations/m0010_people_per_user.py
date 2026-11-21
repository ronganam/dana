"""People belong to a user (docs/people.md, docs/access.md "Kinds").

- ``people.user_id``: whose person it is (their aliases, relation, notes,
  birthday, pinned / hidden flags and cached brief + signature come with the
  row).
- ``person_phones``: rebuilt with ``user_id`` and the primary key
  ``(user_id, phone)``: a number is unique per user, not across users (two
  users may each have a person with the same number).

Calls and notifications keep ``person_id`` as it is (a person id belongs to
one user). Every existing person and number goes to the first admin (created
from env when the database has rows but no user yet), ids kept. Adding a
column with a ``REFERENCES`` clause and a non-NULL default needs foreign keys
off (``FOREIGN_KEYS_OFF``); '' never names a user, so an insert that forgets
the owner fails its foreign key afterwards.
"""

import sqlite3

from core import user_rows

VERSION = 10
NAME = "people_per_user"
FOREIGN_KEYS_OFF = True

PERSON_PHONES = """CREATE TABLE person_phones_new (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    phone TEXT NOT NULL,
    person_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, phone)
)"""

STATEMENTS = [
    "ALTER TABLE people ADD COLUMN user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE",
    "CREATE INDEX idx_people_user ON people(user_id)",
]


def up(conn: sqlite3.Connection) -> None:
    owner = user_rows.owner_of_existing_rows(conn, ("people", "person_phones"))
    for sql in STATEMENTS:
        conn.execute(sql)
    conn.execute(PERSON_PHONES)
    if owner is not None:
        conn.execute("UPDATE people SET user_id = ?", (owner,))
        conn.execute("INSERT INTO person_phones_new (user_id, phone, person_id, created_at) "
                     "SELECT ?, phone, person_id, created_at FROM person_phones", (owner,))
    conn.execute("DROP TABLE person_phones")
    conn.execute("ALTER TABLE person_phones_new RENAME TO person_phones")
    conn.execute("CREATE INDEX idx_person_phones_person ON person_phones(person_id)")
