"""Converting a database made by Dana 9.0 to per-addon migrations
(docs/operations.md, "Database schema").

Dana 9.0 numbered every migration in one sequence (``schema_migrations
(version, name, applied_at)``, 1 to 33 in the 9.0 release, 34 after it). Those
migrations are squashed into ``m0001_baseline`` of core and of every official
addon, which together create exactly the schema migration 34 left. A database
of that scheme is converted in place by :func:`convert` (after a snapshot):

1. at version 33, the last old migration (34: ``integrations`` → ``connections``,
   the ``essentials`` addon's name) is applied first (:data:`M0034`);
2. ``schema_migrations`` is replaced by ``schema_migrations (addon, version,
   name, applied_at)``, marking core's baseline and the baseline of each given
   addon applied.

An older database (version < 33, or one made before migrations existed) is
refused by the runner: start Dana 9.0.0 on it once first.
"""

import datetime
import sqlite3
from typing import Iterable, Set

# The versions an old-scheme database must be at to be converted.
CONVERTIBLE = (33, 34)
LAST = 34

# Old migration 34 ("connections"), applied to a database still at 33.
M0034 = [
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


def old_versions(conn: sqlite3.Connection) -> Set[int]:
    """The versions an old-scheme ``schema_migrations`` records."""
    return {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}


def is_old(conn: sqlite3.Connection) -> bool:
    """Whether ``schema_migrations`` is the old single-numbered table (no ``addon`` column)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(schema_migrations)")}
    return bool(cols) and "addon" not in cols


def convert(conn: sqlite3.Connection, addons: Iterable[str]) -> None:
    """Convert an old-scheme database at version 33 or 34 (checked by the
    caller) inside the caller's transaction: old migration 34 when missing,
    then the new ``schema_migrations`` with core's and each of ``addons``'
    baselines marked applied."""
    if LAST not in old_versions(conn):
        for sql in M0034:
            conn.execute(sql)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    conn.execute("DROP TABLE schema_migrations")
    conn.execute(CREATE_TABLE)
    for addon in ["core", *addons]:
        conn.execute("INSERT INTO schema_migrations (addon, version, name, applied_at) VALUES (?, 1, 'baseline', ?)",
                     (addon, now))


CREATE_TABLE = ("CREATE TABLE schema_migrations (addon TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL, "
                "applied_at TEXT NOT NULL, PRIMARY KEY (addon, version))")
