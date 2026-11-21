"""Addons (docs/addons.md): a user's stored instance of an addon is a connection.

- ``integrations`` → ``connections``, its ``kind`` column → ``addon``; the
  index on (kind, account) → ``idx_connections_addon_account``.
- ``integration_imports`` → ``connection_imports`` and
  ``integration_import_settings`` → ``connection_import_settings``, their
  ``kind`` column → ``addon``.
- The addon every user has is ``essentials`` (was ``core``) wherever an addon
  name is stored.
- The notification triage answers ``ron_wrote_it`` / ``addressed_to_ron`` are
  ``owner_wrote_it`` / ``addressed_to_owner`` in the stored triage results
  (``notifications.triage``).
"""

import sqlite3

VERSION = 34
NAME = "connections"

STATEMENTS = [
    "DROP INDEX IF EXISTS idx_integrations_kind_account",
    "ALTER TABLE integrations RENAME TO connections",
    "ALTER TABLE connections RENAME COLUMN kind TO addon",
    "CREATE INDEX idx_connections_addon_account ON connections(addon, account)",
    "ALTER TABLE integration_imports RENAME TO connection_imports",
    "ALTER TABLE connection_imports RENAME COLUMN kind TO addon",
    "ALTER TABLE integration_import_settings RENAME TO connection_import_settings",
    "ALTER TABLE connection_import_settings RENAME COLUMN kind TO addon",
    "UPDATE connections SET addon = 'essentials' WHERE addon = 'core'",
    "UPDATE connection_imports SET addon = 'essentials' WHERE addon = 'core'",
    "UPDATE connection_import_settings SET addon = 'essentials' WHERE addon = 'core'",
    """UPDATE notifications
       SET triage = replace(replace(triage, '"ron_wrote_it"', '"owner_wrote_it"'),
                            '"addressed_to_ron"', '"addressed_to_owner"')
       WHERE triage LIKE '%"ron_wrote_it"%' OR triage LIKE '%"addressed_to_ron"%'""",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
