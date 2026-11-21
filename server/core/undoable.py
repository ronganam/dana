"""Helpers addon tools use to register undos for changes they make at
once (the registry itself is :mod:`core.undo`).

Every undo here is *guarded*: it first checks that the thing still looks the
way the tool left it, and refuses with :class:`UndoConflict` when it changed
meanwhile (edited in the app, completed by a trigger, deleted), so pressing
Undo never throws away a newer change.
"""
import json
import logging
from typing import Any, Callable, Dict, Iterable, Optional, Sequence

from core import undo
from core.db import get_db_connection

logger = logging.getLogger(__name__)


class UndoConflict(RuntimeError):
    """The thing changed after the action, so it is not undone."""


def safe_record(label: str, fn: Callable[[], Any], source: str) -> Optional[str]:
    """``core.undo.record`` that never breaks the tool that calls it."""
    try:
        return undo.record(label, fn, source=source)
    except Exception:
        logger.warning(f"Could not register undo for {source}", exc_info=True)
        return None


def norm(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return value


def row(table: str, key_col: str, key: Any, columns: Optional[Sequence[str]] = None) -> Optional[Dict[str, Any]]:
    cols = ", ".join(columns) if columns else "*"
    with get_db_connection() as conn:
        r = conn.execute(f"SELECT {cols} FROM {table} WHERE {key_col} = ?", (key,)).fetchone()
    return dict(r) if r else None


def same(current: Optional[Dict[str, Any]], expected: Dict[str, Any], columns: Iterable[str]) -> bool:
    if current is None:
        return False
    return all(norm(current.get(c)) == norm(expected.get(c)) for c in columns)


def restore_row(label: str, table: str, key_col: str, key: Any, before: Dict[str, Any],
                after: Dict[str, Any], columns: Sequence[str], source: str,
                touch_updated_at: bool = True, guard: Optional[Callable[[], None]] = None) -> Optional[str]:
    """Register an undo that puts ``columns`` back to ``before`` — only while
    the row still holds ``after`` in those columns. ``guard`` runs first and
    may refuse with :class:`UndoConflict` (e.g. the user lost write access)."""
    columns = [c for c in columns if norm(before.get(c)) != norm(after.get(c))] or list(columns)

    def _undo():
        if guard is not None:
            guard()
        current = row(table, key_col, key, columns)
        if current is None:
            raise UndoConflict("It was deleted in the meantime, so there is nothing to restore.")
        if not same(current, after, columns):
            raise UndoConflict("It was changed again since, so it was left as it is.")
        sets = ", ".join(f"{c} = ?" for c in columns)
        if touch_updated_at:
            sets += ", updated_at = CURRENT_TIMESTAMP"
        with get_db_connection() as conn:
            conn.execute(f"UPDATE {table} SET {sets} WHERE {key_col} = ?",
                         (*[before.get(c) for c in columns], key))
            conn.commit()

    return safe_record(label, _undo, source)


def delete_created_row(label: str, table: str, key_col: str, key: Any, after: Dict[str, Any],
                       columns: Sequence[str], source: str,
                       cascade: Sequence[tuple] = (), guard: Optional[Callable[[], None]] = None) -> Optional[str]:
    """Register an undo that deletes a row the tool just created — only while
    it is unchanged. ``cascade``: extra ``(table, column)`` rows to delete
    with it (e.g. its task links). Already gone counts as undone. ``guard``
    runs first and may refuse with :class:`UndoConflict`."""
    def _undo():
        if guard is not None:
            guard()
        current = row(table, key_col, key, columns)
        if current is None:
            return
        if not same(current, after, columns):
            raise UndoConflict("It was changed since it was created, so it was kept.")
        with get_db_connection() as conn:
            conn.execute(f"DELETE FROM {table} WHERE {key_col} = ?", (key,))
            for other_table, other_col in cascade:
                conn.execute(f"DELETE FROM {other_table} WHERE {other_col} = ?", (key,))
            conn.commit()

    return safe_record(label, _undo, source)
