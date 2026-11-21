"""The SQLite database: connections and schema migrations.

Every module opens its connections with :func:`get_db_connection`. The schema
is owned by the ordered migrations in ``core/db/migrations/``; one explicit
:func:`migrate` call at startup (``main.py``) and in test setup brings a
database up to date. Importing a module never creates tables.

A database made before migrations existed (Dana 8.1.0) is adopted: the
baseline migration verifies it has the complete 8.1.0 schema and then changes
nothing. An older or incomplete one is refused with :class:`SchemaError`.
Before migrations are applied to an existing database, a consistent copy is
written to ``snapshots/db-before-<version>.sqlite3`` (core/db_snapshot.py).
"""

import datetime
import logging
import os
import sqlite3
from pathlib import Path
from typing import List, Optional, Set, Union

from core import db_snapshot
from core.db.migrations import MIGRATIONS, m0001_baseline
from core.paths import DATABASE_PATH

logger = logging.getLogger(__name__)

# SQLite defaults to raising "database is locked" after 5s. A streaming agent run
# holds the write lock while the web UI and apps poll session state on the same
# file, so the default is too tight and shows up as spurious tool errors.
DB_BUSY_TIMEOUT_MS = int(os.getenv("DB_BUSY_TIMEOUT_MS", "30000"))

PathLike = Union[str, Path]


class SchemaError(RuntimeError):
    """The database cannot be migrated (older than 8.1.0, or newer than this code)."""


def get_db_connection(path: Optional[PathLike] = None) -> sqlite3.Connection:
    """A connection to the database (``DATABASE_PATH`` unless ``path`` is
    given): rows as :class:`sqlite3.Row`, foreign keys on, a generous busy
    timeout. Use it as a context manager to commit / roll back."""
    conn = sqlite3.connect(str(path or DATABASE_PATH), timeout=DB_BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _user_tables(conn: sqlite3.Connection) -> Set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}


def applied_migrations(path: Optional[PathLike] = None) -> List[int]:
    """Versions recorded in ``schema_migrations`` (empty when there is none)."""
    conn = get_db_connection(path)
    try:
        if "schema_migrations" not in _user_tables(conn):
            return []
        return [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    finally:
        conn.close()


def migrate(path: Optional[PathLike] = None) -> List[str]:
    """Bring the database up to date. Returns the names of the migrations
    applied (``"0002_users"``), empty when it already was.

    Raises:
        SchemaError: the database predates 8.1.0 (or is incomplete), or it
            has migrations this code does not know.
    """
    db_path = Path(path or DATABASE_PATH)
    conn = get_db_connection(db_path)
    conn.isolation_level = None  # explicit transactions below
    try:
        tables = _user_tables(conn)
        if "schema_migrations" in tables:
            done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        else:
            done = set()
            if tables:
                missing = m0001_baseline.missing_from(conn)
                if missing:
                    raise SchemaError(
                        f"The database at {db_path} is older than Dana 8.1.0 (or incomplete) and cannot be "
                        f"migrated: missing {', '.join(missing[:12])}"
                        f"{f' and {len(missing) - 12} more' if len(missing) > 12 else ''}. "
                        "Start Dana 8.1.0 on it once (it upgrades the schema), then this version.")
        known = {m.VERSION for m in MIGRATIONS}
        unknown = sorted(done - known)
        if unknown:
            raise SchemaError(f"The database at {db_path} has migrations this code does not know "
                              f"({unknown}); it was made by a newer version.")
        pending = [m for m in MIGRATIONS if m.VERSION not in done]
        if not pending:
            return []
        if tables:
            target = db_snapshot.snapshot(db_path, f"{db_path.stem}-before-{pending[0].VERSION:04d}{db_path.suffix}")
            logger.info(f"Database snapshot before migrating: {target}")
        applied: List[str] = []
        for m in pending:
            label = f"{m.VERSION:04d}_{m.NAME}"
            # A migration that rebuilds a table other tables reference (SQLite
            # cannot change a constraint in place) runs with foreign keys off,
            # as SQLite's table-rebuild procedure requires; the pragma only
            # takes effect outside a transaction.
            fk_off = bool(getattr(m, "FOREIGN_KEYS_OFF", False))
            if fk_off:
                conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                             "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
                m.up(conn)
                conn.execute("INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                             (m.VERSION, m.NAME,
                              datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            finally:
                if fk_off:
                    conn.execute("PRAGMA foreign_keys = ON")
            logger.info(f"Applied database migration {label}")
            applied.append(label)
        return applied
    finally:
        conn.close()
