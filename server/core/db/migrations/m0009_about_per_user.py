"""About-you facts and the learner's state belong to a user (docs/about.md,
docs/access.md "Kinds").

- ``about_facts.user_id``: whose fact it is (active, forgotten and dropped
  rows alike, so "do not re-add" and the size cap are per user).
- ``about_state``: rebuilt with ``user_id`` in its primary key (the learner's
  bookmark and any other state is per user; it was keyed by ``key`` alone).
- The old ``memories`` table is not per user and other code still keeps its
  own rows there; the About kind reads its real memories (the one-time copy
  into facts, older notes for ``recall_info``, old keys the Telegram picker
  and ``DELETE /api/memories/{key}`` remove) only for the user marked by the
  ``about_state`` row ``legacy_memories``: the first admin of a database that
  existed before this migration. A database created after it has no such
  user (it never had real memories).

Every existing fact and state row goes to the first admin (created from env
when the database has rows but no user yet), ids kept. Adding a column with a
``REFERENCES`` clause and a non-NULL default needs foreign keys off
(``FOREIGN_KEYS_OFF``); '' never names a user, so an insert that forgets the
owner fails its foreign key afterwards.
"""

import sqlite3

from core import user_rows

VERSION = 9
NAME = "about_per_user"
FOREIGN_KEYS_OFF = True

LEGACY_MEMORIES_KEY = "legacy_memories"

ABOUT_STATE = """CREATE TABLE about_state_new (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
)"""

STATEMENTS = [
    "ALTER TABLE about_facts ADD COLUMN user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE",
    "CREATE INDEX idx_about_facts_user ON about_facts(user_id, status)",
]


def up(conn: sqlite3.Connection) -> None:
    owner = user_rows.owner_of_existing_rows(conn, ("about_facts", "about_state", "memories"))
    conn.execute(ABOUT_STATE)
    if owner is not None:
        conn.execute("INSERT INTO about_state_new (user_id, key, value) SELECT ?, key, value FROM about_state",
                     (owner,))
        conn.execute("INSERT OR REPLACE INTO about_state_new (user_id, key, value) VALUES (?, ?, 'true')",
                     (owner, LEGACY_MEMORIES_KEY))
    conn.execute("DROP TABLE about_state")
    conn.execute("ALTER TABLE about_state_new RENAME TO about_state")
    for sql in STATEMENTS:
        conn.execute(sql)
    if owner is not None:
        conn.execute("UPDATE about_facts SET user_id = ?", (owner,))
