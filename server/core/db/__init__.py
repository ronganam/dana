"""The SQLite database: connections and schema migrations.

Every module opens its connections with :func:`get_db_connection`. The schema
is owned by the migrations of core (``core/db/migrations/``) and of each addon
(``addons/<name>/migrations/``), numbered per addon; one explicit
:func:`migrate` call at startup (``main.py``) and in test setup brings a
database up to date. Importing a module never creates tables.

``schema_migrations (addon, version, name, applied_at)`` records what was
applied. A database made by Dana 9.0 (one sequence of migrations, at 33 or
34) is converted in place first (core/db/convert.py); an older one is refused
with :class:`SchemaError` (start Dana 9.0.0 on it once). Before migrations are
applied to an existing database, a consistent copy is written to
``snapshots/db-before-<addon>-<version>.sqlite3`` (core/db_snapshot.py).
"""

import datetime
import logging
import os
import sqlite3
from pathlib import Path
from typing import List, Optional, Set, Tuple, Union

from core import db_snapshot
from core.db import convert
from core.db.migrations import CORE, Migration, all_migrations, official_with_migrations
from core.paths import DATABASE_PATH

logger = logging.getLogger(__name__)

# SQLite defaults to raising "database is locked" after 5s. A streaming agent run
# holds the write lock while the web UI and apps poll session state on the same
# file, so the default is too tight and shows up as spurious tool errors.
DB_BUSY_TIMEOUT_MS = int(os.getenv("DB_BUSY_TIMEOUT_MS", "30000"))

PathLike = Union[str, Path]


class SchemaError(RuntimeError):
    """The database cannot be migrated (older than Dana 9.0, or newer than this code)."""


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


def applied_migrations(path: Optional[PathLike] = None) -> List[Tuple[str, int]]:
    """(addon, version) of every migration recorded in ``schema_migrations``
    (empty when there is none, or it is still the old 9.0 table)."""
    conn = get_db_connection(path)
    try:
        if "schema_migrations" not in _user_tables(conn) or convert.is_old(conn):
            return []
        return [(r[0], r[1]) for r in conn.execute("SELECT addon, version FROM schema_migrations ORDER BY addon, version")]
    finally:
        conn.close()


def pending_migrations(path: Optional[PathLike] = None) -> List[Migration]:
    """The migrations :func:`migrate` would apply (every one on an unconverted 9.0 database but the baselines it marks)."""
    done = set(applied_migrations(path))
    if not done:
        conn = get_db_connection(path)
        try:
            old = "schema_migrations" in _user_tables(conn) and convert.is_old(conn)
        finally:
            conn.close()
        if old:
            done = {(a, 1) for a in [CORE, *official_with_migrations()]}
    return [m for _, ms in all_migrations() for m in ms if (m.addon, m.version) not in done]


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _snapshot(db_path: Path, what: str) -> None:
    target = db_snapshot.snapshot(db_path, f"{db_path.stem}-before-{what}{db_path.suffix}")
    logger.info(f"Database snapshot before migrating: {target}")


def _convert_old(conn: sqlite3.Connection, db_path: Path) -> None:
    """Convert a Dana 9.0 database (old single-numbered migrations) in place, after a snapshot.

    Raises:
        SchemaError: it is older than 9.0 (before migration 33) or newer than 34.
    """
    versions = convert.old_versions(conn)
    top = max(versions, default=0)
    if top > convert.LAST:
        raise SchemaError(f"The database at {db_path} has migrations this code does not know (up to {top}); "
                          "it was made by a newer version.")
    if top not in convert.CONVERTIBLE:
        raise SchemaError(f"The database at {db_path} is older than Dana 9.0 (migration {top}): upgrade with "
                          "Dana 9.0.0 first (start it once on this database), then this version.")
    _snapshot(db_path, f"addons-from-{top:04d}")
    conn.execute("BEGIN IMMEDIATE")
    try:
        convert.convert(conn, official_with_migrations())
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    logger.info(f"Database converted to per-addon migrations (from version {top})")


def migrate(path: Optional[PathLike] = None) -> List[str]:
    """Bring the database up to date. Returns the labels of the migrations
    applied (``"core:0001_baseline"``, ``"tasks:0002_…"``), empty when it already was.

    Raises:
        SchemaError: the database predates Dana 9.0, or it has migrations of
            core or of a present addon that this code does not know.
    """
    db_path = Path(path or DATABASE_PATH)
    conn = get_db_connection(db_path)
    conn.isolation_level = None  # explicit transactions below
    try:
        tables = _user_tables(conn)
        if tables and "schema_migrations" not in tables:
            raise SchemaError(f"The database at {db_path} is older than Dana 9.0 (it has no migrations): upgrade "
                              "with Dana 9.0.0 first (start it once on this database), then this version.")
        if "schema_migrations" in tables and convert.is_old(conn):
            _convert_old(conn, db_path)
        done: Set[Tuple[str, int]] = set()
        if "schema_migrations" in _user_tables(conn):
            done = {(r[0], r[1]) for r in conn.execute("SELECT addon, version FROM schema_migrations")}
        everything = all_migrations()
        for addon, ms in everything:
            unknown = sorted(v for a, v in done if a == addon and v not in {m.version for m in ms})
            if unknown:
                raise SchemaError(f"The database at {db_path} has migrations of {addon} this code does not know "
                                  f"({unknown}); it was made by a newer version.")
        pending = [m for _, ms in everything for m in ms if (m.addon, m.version) not in done]
        if not pending:
            return []
        if tables:
            _snapshot(db_path, f"{pending[0].addon}-{pending[0].version:04d}")
        applied: List[str] = []
        for m in pending:
            # A migration that rebuilds a table other tables reference (SQLite
            # cannot change a constraint in place) runs with foreign keys off,
            # as SQLite's table-rebuild procedure requires; the pragma only
            # takes effect outside a transaction.
            fk_off = bool(getattr(m.module, "FOREIGN_KEYS_OFF", False))
            if fk_off:
                conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(convert.CREATE_TABLE.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
                m.module.up(conn)
                conn.execute("INSERT INTO schema_migrations (addon, version, name, applied_at) VALUES (?, ?, ?, ?)",
                             (m.addon, m.version, m.name, _now()))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            finally:
                if fk_off:
                    conn.execute("PRAGMA foreign_keys = ON")
            logger.info(f"Applied database migration {m.label}")
            applied.append(m.label)
        return applied
    finally:
        conn.close()
