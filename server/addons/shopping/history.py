"""What was bought on a list, and the suggestions made from it
(docs/shopping.md, "History and suggestions").

``shopping_history`` holds one row per list and name key: the name as last
bought, its aisle, how often and when last. A name enters it when it is
ticked (so a typo that was deleted is never suggested); unticking takes that
purchase back. Suggestions: names matching what is typed, most bought
first; the usuals: the most bought names that are not on the list to buy now.
"""

import sqlite3
from typing import Dict, List

from core.db import get_db_connection
from addons.shopping.journal import Journal
from addons.shopping.names import name_key
from addons.shopping.values import SUGGESTIONS, USUALS


def tick(conn: sqlite3.Connection, journal: Journal, list_id: str, key: str, name: str, category: str,
         when: str) -> None:
    """One purchase of ``name`` on the list."""
    journal.row(conn, "shopping_history", list_id=list_id, name_key=key)
    conn.execute("INSERT INTO shopping_history (list_id, name_key, name, category, times, last_at) "
                 "VALUES (?, ?, ?, ?, 1, ?) ON CONFLICT(list_id, name_key) DO UPDATE SET name = excluded.name, "
                 "category = excluded.category, times = times + 1, last_at = excluded.last_at",
                 (list_id, key, name, category, when))


def untick(conn: sqlite3.Connection, journal: Journal, list_id: str, key: str) -> None:
    """Take one purchase of the name back (the row goes when none is left)."""
    journal.row(conn, "shopping_history", list_id=list_id, name_key=key)
    conn.execute("UPDATE shopping_history SET times = times - 1 WHERE list_id = ? AND name_key = ?", (list_id, key))
    conn.execute("DELETE FROM shopping_history WHERE list_id = ? AND name_key = ? AND times <= 0", (list_id, key))


def _to_buy(conn: sqlite3.Connection, list_id: str) -> set:
    return {r["name_key"] for r in conn.execute("SELECT name_key FROM shopping_items WHERE list_id = ? AND checked = 0",
                                                (list_id,))}


def _on_list(conn: sqlite3.Connection, list_id: str) -> set:
    return {r["name_key"] for r in conn.execute("SELECT name_key FROM shopping_items WHERE list_id = ?", (list_id,))}


def _payload(row: sqlite3.Row) -> Dict[str, object]:
    return {"name": row["name"], "category": row["category"], "times": row["times"]}


def usuals(list_id: str, limit: int = USUALS) -> List[Dict[str, object]]:
    """The most bought names that are not on the list now, to buy or bought
    (a name just bought is offered again once it has left the list):
    ``[{name, category, times}]``."""
    with get_db_connection() as conn:
        skip = _on_list(conn, list_id)
        rows = conn.execute("SELECT * FROM shopping_history WHERE list_id = ? ORDER BY times DESC, last_at DESC, rowid DESC",
                            (list_id,)).fetchall()
    return [_payload(r) for r in rows if r["name_key"] not in skip][:limit]


def matching(list_id: str, q: str, limit: int = SUGGESTIONS) -> List[Dict[str, object]]:
    """Names from the history matching ``q`` (a word starting with it first, then
    containing it; most bought first), not on the list to buy now. Empty ``q``: the usuals."""
    key = name_key(q)
    if not key:
        return usuals(list_id, limit)
    with get_db_connection() as conn:
        skip = _to_buy(conn, list_id)
        rows = conn.execute("SELECT * FROM shopping_history WHERE list_id = ? AND instr(name_key, ?) > 0 "
                            "ORDER BY times DESC, last_at DESC, rowid DESC", (list_id, key)).fetchall()
    rows = [r for r in rows if r["name_key"] not in skip]
    starts = [r for r in rows if r["name_key"].startswith(key) or f" {key}" in r["name_key"]]
    rest = [r for r in rows if r not in starts]
    return [_payload(r) for r in starts + rest][:limit]


def forget(conn: sqlite3.Connection, journal: Journal, list_id: str, name: str) -> bool:
    """Forget a name (it is no longer suggested). False: it was not in the history."""
    key = name_key(name)
    journal.row(conn, "shopping_history", list_id=list_id, name_key=key)
    return conn.execute("DELETE FROM shopping_history WHERE list_id = ? AND name_key = ?", (list_id, key)).rowcount > 0
