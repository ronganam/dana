"""The undo of one shopping change (docs/shopping.md, "Undo").

A change records, inside its transaction, the rows it touches before it
touches them (:meth:`Journal.item`, :meth:`Journal.row`) and, at the end, how
they look after (:meth:`Journal.finish`). :meth:`Journal.register` then
registers one undo (``core/undo.py``) that puts every row back:

- items strictly: refused (:class:`~core.undoable.UndoConflict`) when one was
  changed again since (its ``rev`` moved), removed since, or put back on the
  list since; a created item already gone counts as undone;
- other rows (history, aisle choices, invitation answers) only while they
  still look as the change left them;
- refused as a whole when the user may no longer change one of the lists.
"""

import logging
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.undoable import UndoConflict, safe_record
from core.users import User
from core.addons import sharing
from addons.shopping import events
from addons.shopping.values import ITEM_TYPE

logger = logging.getLogger(__name__)

Row = Dict[str, Any]
Key = Tuple[Tuple[str, Any], ...]


def _select(conn: sqlite3.Connection, table: str, key: Key) -> Optional[Row]:
    where = " AND ".join(f"{c} = ?" for c, _ in key)
    row = conn.execute(f"SELECT * FROM {table} WHERE {where}", tuple(v for _, v in key)).fetchone()
    return dict(row) if row else None


def _insert(conn: sqlite3.Connection, table: str, row: Row) -> None:
    cols = list(row)
    conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                 tuple(row[c] for c in cols))


def _delete(conn: sqlite3.Connection, table: str, key: Key) -> None:
    conn.execute(f"DELETE FROM {table} WHERE {' AND '.join(f'{c} = ?' for c, _ in key)}", tuple(v for _, v in key))


class Journal:
    """The rows one change touched, before and after."""

    def __init__(self) -> None:
        self._items: Dict[str, Tuple[Optional[Row], Optional[Row]]] = {}
        self._rows: Dict[Tuple[str, Key], Tuple[Optional[Row], Optional[Row]]] = {}

    def item(self, conn: sqlite3.Connection, item_id: str) -> None:
        """Remember item ``item_id`` as it is now (before the change touches it)."""
        if item_id not in self._items:
            self._items[item_id] = (_select(conn, "shopping_items", (("id", item_id),)), None)

    def row(self, conn: sqlite3.Connection, table: str, **key: Any) -> None:
        """Remember another row (history, aisle choice, invitation answer) as it is now."""
        k: Key = tuple(key.items())
        if (table, k) not in self._rows:
            self._rows[(table, k)] = (_select(conn, table, k), None)

    def finish(self, conn: sqlite3.Connection) -> None:
        """Read every remembered row again: how the change left it."""
        self._items = {i: (b, _select(conn, "shopping_items", (("id", i),))) for i, (b, _) in self._items.items()}
        self._rows = {(t, k): (b, _select(conn, t, k)) for (t, k), (b, _) in self._rows.items()}

    @property
    def changed(self) -> bool:
        """Whether the change did anything to a remembered row."""
        return any(b != a for b, a in self._items.values()) or any(b != a for b, a in self._rows.values())

    def lists(self) -> List[str]:
        """The lists the change touched (items before or after)."""
        out = []
        for before, after in self._items.values():
            for row in (before, after):
                if row is not None and row["list_id"] not in out:
                    out.append(row["list_id"])
        return out

    def register(self, user: User, label: str, source: str,
                 also: Optional[Callable[[], None]] = None) -> Optional[Dict[str, str]]:
        """Register the undo of this change for ``user`` → ``{id, label}`` (None:
        nothing changed). ``also`` runs after the rows are back (e.g. the
        active list put back)."""
        if not self.changed and also is None:
            return None
        items = {i: v for i, v in self._items.items() if v[0] != v[1]}
        rows = {k: v for k, v in self._rows.items() if v[0] != v[1]}
        lists = self.lists()

        def _undo() -> None:
            for list_id in lists:
                if not sharing.can_edit(user, ITEM_TYPE, list_id):
                    raise UndoConflict("You can no longer change that shopping list.")
            with events.change() as tx:
                _restore(tx, items, rows)
            if also is not None:
                also()

        undo_id = safe_record(label, _undo, source)
        return {"id": undo_id, "label": label} if undo_id else None


def _restore(tx: events.Change, items: Dict[str, Tuple[Optional[Row], Optional[Row]]],
             rows: Dict[Tuple[str, Key], Tuple[Optional[Row], Optional[Row]]]) -> None:
    conn = tx.conn
    current = {i: _select(conn, "shopping_items", (("id", i),)) for i in items}
    for item_id, (before, after) in items.items():
        now = current[item_id]
        if after is None:
            if now is not None:
                raise UndoConflict(f"'{now['name']}' is on the list again, so it was left as it is.")
            if before is not None and not before["checked"] and conn.execute(
                    "SELECT 1 FROM shopping_items WHERE list_id = ? AND name_key = ? AND checked = 0",
                    (before["list_id"], before["name_key"])).fetchone():
                raise UndoConflict(f"'{before['name']}' is on the list again, so it was left as it is.")
        elif now is None:
            if before is not None:
                raise UndoConflict(f"'{after['name']}' was removed in the meantime.")
        elif now["rev"] != after["rev"] or now["list_id"] != after["list_id"]:
            raise UndoConflict(f"'{now['name']}' was changed again since, so it was left as it is.")
    for item_id, (before, after) in items.items():
        now = current[item_id]
        for row in (before, after, now):
            if row is not None:
                tx.bump(row["list_id"])
        if before is None:
            if now is not None:
                _delete(conn, "shopping_items", (("id", item_id),))
        elif now is None:
            _insert(conn, "shopping_items", before)
        else:
            cols = [c for c in before if c not in ("id", "rev")]
            conn.execute(f"UPDATE shopping_items SET {', '.join(f'{c} = ?' for c in cols)}, rev = ? WHERE id = ?",
                         (*[before[c] for c in cols], now["rev"] + 1, item_id))
    for (table, key), (before, after) in rows.items():
        if _select(conn, table, key) != after:
            continue  # changed since: left as it is
        _delete(conn, table, key)
        if before is not None:
            _insert(conn, table, before)
        if "list_id" in _either(before, after):
            tx.bump(_either(before, after)["list_id"])
        if table == "shopping_invite_answers":
            tx.signal(_either(before, after)["user_id"])


def _either(before: Optional[Row], after: Optional[Row]) -> Row:
    """Whichever of the two rows exists (one always does)."""
    return before if before is not None else (after or {})
