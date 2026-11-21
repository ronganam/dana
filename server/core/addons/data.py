"""Per-user table helpers for addons (../docs/addons.md, "Data").

:class:`UserTable` wraps the common queries on a table that carries a
``user_id`` column so every read and write is scoped to the acting user:

    items = UserTable("my_items")
    items.insert(user, {"id": "1", "title": "Buy milk"})
    row = items.get(user, "1")
    rows = items.list(user, where="done = 0", order="created_at DESC", limit=20)
    items.update(user, "1", {"done": 1})
    items.delete(user, "1")

Every method accepts a :class:`~core.users.User` (or user id string) and an
optional open ``sqlite3.Connection`` (when omitted it opens one via
:func:`core.db.get_db_connection` and commits writes). Table and column names
are validated as SQL identifiers; values are always parameterised.
"""

import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Union

from core.db import get_db_connection
from core.users import User

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
UserRef = Union[User, str]


def _ident(name: str, what: str) -> str:
    if not isinstance(name, str) or not _IDENT.match(name):
        raise ValueError(f"Invalid SQL {what}: {name!r}")
    return name


def _uid(user: UserRef) -> str:
    uid = user.id if isinstance(user, User) else str(user)
    if not uid:
        raise ValueError("User id must not be empty")
    return uid


class UserTable:
    """User-scoped CRUD helpers for one SQLite table with a ``user_id`` column."""

    def __init__(self, table: str, *, id_col: str = "id", user_col: str = "user_id") -> None:
        self.table = _ident(table, "table name")
        self.id_col = _ident(id_col, "id column")
        self.user_col = _ident(user_col, "user column")

    @contextmanager
    def _conn(self, conn: Optional[sqlite3.Connection], *, write: bool = False) -> Iterator[sqlite3.Connection]:
        if conn is not None:
            yield conn
            return
        with get_db_connection() as c:
            yield c
            if write:
                c.commit()

    def get(self, user: UserRef, row_id: Any, *, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
        """One row owned by ``user`` with ``id_col == row_id``, or None."""
        uid = _uid(user)
        with self._conn(conn) as c:
            row = c.execute(f"SELECT * FROM {self.table} WHERE {self.id_col} = ? AND {self.user_col} = ?",
                            (row_id, uid)).fetchone()
        return dict(row) if row is not None else None

    def exists(self, user: UserRef, row_id: Any, *, conn: Optional[sqlite3.Connection] = None) -> bool:
        """True when ``user`` owns a row with ``id_col == row_id``."""
        uid = _uid(user)
        with self._conn(conn) as c:
            row = c.execute(f"SELECT 1 FROM {self.table} WHERE {self.id_col} = ? AND {self.user_col} = ?",
                            (row_id, uid)).fetchone()
        return row is not None

    def list(self, user: UserRef, *, where: str = "", params: Sequence[Any] = (), order: str = "",
             limit: Optional[int] = None, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
        """Rows owned by ``user``; optional SQL ``where`` (ANDed with the user filter), ``order`` and ``limit``."""
        uid = _uid(user)
        sql = f"SELECT * FROM {self.table} WHERE {self.user_col} = ?"
        args: List[Any] = [uid]
        if where:
            sql += f" AND ({where})"
            args.extend(params)
        if order:
            sql += f" ORDER BY {order}"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(int(limit))
        with self._conn(conn) as c:
            rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def count(self, user: UserRef, *, where: str = "", params: Sequence[Any] = (),
              conn: Optional[sqlite3.Connection] = None) -> int:
        """Number of rows owned by ``user`` matching optional ``where``."""
        uid = _uid(user)
        sql = f"SELECT COUNT(*) FROM {self.table} WHERE {self.user_col} = ?"
        args: List[Any] = [uid]
        if where:
            sql += f" AND ({where})"
            args.extend(params)
        with self._conn(conn) as c:
            return int(c.execute(sql, args).fetchone()[0])

    def insert(self, user: UserRef, values: Mapping[str, Any], *,
               conn: Optional[sqlite3.Connection] = None) -> Any:
        """Insert a row for ``user`` (always sets ``user_col``). Returns ``values[id_col]`` or ``lastrowid``."""
        uid = _uid(user)
        data = { _ident(k, "column name"): v for k, v in values.items() }
        if self.user_col in data and data[self.user_col] != uid:
            raise ValueError(f"Cannot insert a row for another user ({data[self.user_col]!r} != {uid!r})")
        data[self.user_col] = uid
        cols = list(data.keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO {self.table} ({', '.join(cols)}) VALUES ({placeholders})"
        with self._conn(conn, write=True) as c:
            cur = c.execute(sql, [data[k] for k in cols])
        return data[self.id_col] if self.id_col in data else cur.lastrowid

    def update(self, user: UserRef, row_id: Any, values: Mapping[str, Any], *,
               conn: Optional[sqlite3.Connection] = None) -> bool:
        """Update columns on ``user``'s row ``row_id``. Returns True when a row matched."""
        uid = _uid(user)
        if not values:
            return False
        cols = [_ident(k, "column name") for k in values]
        if self.user_col in cols:
            raise ValueError(f"Cannot change {self.user_col!r} with UserTable.update()")
        sets = ", ".join(f"{col} = ?" for col in cols)
        sql = f"UPDATE {self.table} SET {sets} WHERE {self.id_col} = ? AND {self.user_col} = ?"
        with self._conn(conn, write=True) as c:
            cur = c.execute(sql, [*values.values(), row_id, uid])
        return cur.rowcount > 0

    def delete(self, user: UserRef, row_id: Any, *, conn: Optional[sqlite3.Connection] = None) -> bool:
        """Delete ``user``'s row ``row_id``. Returns True when a row was deleted."""
        uid = _uid(user)
        with self._conn(conn, write=True) as c:
            cur = c.execute(f"DELETE FROM {self.table} WHERE {self.id_col} = ? AND {self.user_col} = ?",
                            (row_id, uid))
        return cur.rowcount > 0

    def delete_where(self, user: UserRef, where: str = "", params: Sequence[Any] = (), *,
                     conn: Optional[sqlite3.Connection] = None) -> int:
        """Delete ``user``'s rows matching optional ``where`` (all of ``user``'s rows when empty). Returns count."""
        uid = _uid(user)
        sql = f"DELETE FROM {self.table} WHERE {self.user_col} = ?"
        args: List[Any] = [uid]
        if where:
            sql += f" AND ({where})"
            args.extend(params)
        with self._conn(conn, write=True) as c:
            cur = c.execute(sql, args)
        return int(cur.rowcount)
