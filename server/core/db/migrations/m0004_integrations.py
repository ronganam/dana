"""Integrations: one user's connection to one service (docs/access.md, "Integration").

- ``integrations``: one row per (user, kind). ``credentials`` is the Fernet
  token of the JSON credentials (``integrations/framework/crypto.py``, key
  from ``CREDENTIALS_KEY``), NULL for a kind without credentials; never
  plaintext. ``settings`` is plain JSON. ``account`` names the account on the
  other side when routing needs it (e.g. the WhatsApp account id of inbound
  webhooks), NULL otherwise.
- ``integration_imports``: the kinds the startup env import
  (``integrations/bootstrap.py``) has connected for the first admin, so a kind
  that admin disconnects later is not created again at the next start.

Nothing is created for existing users here: the import runs at startup, after
``users.bootstrap()``.
"""

import sqlite3

VERSION = 4
NAME = "integrations"

STATEMENTS = [
    """CREATE TABLE integrations (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        credentials TEXT,
        settings TEXT NOT NULL DEFAULT '{}',
        account TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, kind)
    )""",
    "CREATE INDEX idx_integrations_kind_account ON integrations(kind, account)",
    """CREATE TABLE integration_imports (
        kind TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        imported_at TEXT NOT NULL
    )""",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
