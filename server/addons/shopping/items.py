"""The items of a shopping list (docs/shopping.md, "Items", "Undo").

Every function works on one list as one user sees it
(:class:`~addons.shopping.lists.ShoppingList`). A change runs in one
transaction that raises the list's version (:mod:`.events`), first clears
the bought items older than 12 hours, and registers one undo for exactly
what it did (:mod:`.journal`), returned as ``{id, label}`` (None when
nothing changed).

**Adding** compares names by their key (:func:`~.names.name_key`): already
on the list to buy → nothing added, a quantity or note given now replaces
the stored one (``already``); on the list as bought → back to buy, with the
quantity and note given now (``restored``); otherwise added (``added``). A
client-given id that is already on the list makes the request a no-op for
that item. New names get their aisle from :mod:`.aisles`.
"""

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.db import get_db_connection
from core.users import User
from addons.shopping import aisles, events, history, values
from addons.shopping.journal import Journal
from addons.shopping.lists import ShoppingList, person
from addons.shopping.names import ItemInput, check_count, clip, name_key
from addons.shopping.values import (CLEAR_AFTER, MAX_NAME, MAX_NOTE, MAX_QUANTITY, Duplicate, NotFound,
                                          iso, parse_iso)

logger = logging.getLogger(__name__)

ADDED, ALREADY, RESTORED = "added", "already", "restored"


# --- Reading ---------------------------------------------------------------------------

def clear_old(tx: events.Change, list_id: str) -> int:
    """Remove the list's items bought more than 12 hours ago (raises the version when any)."""
    cutoff = iso(values.utc_now() - CLEAR_AFTER)
    gone = tx.conn.execute("DELETE FROM shopping_items WHERE list_id = ? AND checked = 1 AND checked_at < ?",
                           (list_id, cutoff)).rowcount
    if gone:
        tx.bump(list_id)
    return gone


def expire(list_id: str) -> None:
    """Before a read: clear the list's old bought items when there are any."""
    cutoff = iso(values.utc_now() - CLEAR_AFTER)
    with get_db_connection() as conn:
        old = conn.execute("SELECT 1 FROM shopping_items WHERE list_id = ? AND checked = 1 AND checked_at < ? LIMIT 1",
                           (list_id, cutoff)).fetchone()
    if old:
        with events.change() as tx:
            clear_old(tx, list_id)


def _sort_key(row: sqlite3.Row) -> Tuple:
    if row["checked"]:
        return (1, 0, "", -_ts(row["checked_at"]))
    return (0, aisles.RANK.get(row["category"], len(aisles.RANK)), row["seq"], 0)


def _ts(value: Optional[str]) -> float:
    try:
        return parse_iso(value).timestamp() if value else 0.0
    except ValueError:
        return 0.0


def payload(row: Any) -> Dict[str, Any]:
    """An item as clients get it."""
    return {"id": row["id"], "name": row["name"], "quantity": row["quantity"], "note": row["note"],
            "category": row["category"], "checked": bool(row["checked"]), "checked_at": row["checked_at"],
            "checked_by": person(row["checked_by"]), "added_by": person(row["added_by"]),
            "created_at": row["created_at"]}


def rows(conn: sqlite3.Connection, list_id: str) -> List[sqlite3.Row]:
    """The list's items in display order: to buy by aisle, then by when added; then bought, newest first."""
    found = conn.execute("SELECT * FROM shopping_items WHERE list_id = ?", (list_id,)).fetchall()
    return sorted(found, key=_sort_key)


def list_items(lst: ShoppingList) -> List[Dict[str, Any]]:
    """The items of ``lst`` in display order (old bought items cleared first)."""
    expire(lst.id)
    with get_db_connection() as conn:
        return [payload(r) for r in rows(conn, lst.id)]


def get(lst: ShoppingList, item_id: str) -> Optional[Dict[str, Any]]:
    """Item ``item_id`` when it is on ``lst`` (None otherwise)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM shopping_items WHERE id = ? AND list_id = ?", (item_id, lst.id)).fetchone()
    return payload(row) if row else None


@dataclass(frozen=True)
class Match:
    """What a name given to a tool found: one item, or none / several (``candidates``)."""
    item: Optional[Dict[str, Any]]
    candidates: List[Dict[str, Any]]


def find(lst: ShoppingList, ref: str, prefer_checked: Optional[bool] = None) -> Match:
    """The item ``ref`` names on ``lst``: its id, else the items with the same
    name key, else the items whose name contains every word of ``ref``. When
    several match, those in the ``prefer_checked`` state win; still several →
    no item, the candidates."""
    expire(lst.id)
    ref = (ref or "").strip()
    with get_db_connection() as conn:
        all_rows = rows(conn, lst.id)
    by_id = [r for r in all_rows if r["id"] == ref]
    if by_id:
        return Match(payload(by_id[0]), [])
    key = name_key(ref)
    if not key:
        return Match(None, [])
    found = [r for r in all_rows if r["name_key"] == key]
    if not found:
        words = key.split()
        found = [r for r in all_rows if all(w in r["name_key"] for w in words)]
    if len(found) > 1 and prefer_checked is not None:
        preferred = [r for r in found if bool(r["checked"]) == prefer_checked]
        found = preferred or found
    if len(found) == 1:
        return Match(payload(found[0]), [])
    return Match(None, [payload(r) for r in found])


# --- Adding ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Added:
    """The outcome of an add: ``[{id, name, status}]`` per item and its undo."""
    results: List[Dict[str, str]]
    undo: Optional[Dict[str, str]]


def _next_seq(conn: sqlite3.Connection, list_id: str) -> int:
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS s FROM shopping_items WHERE list_id = ?", (list_id,)).fetchone()
    return int(row["s"]) + 1


def _new_id(conn: sqlite3.Connection, wanted: Optional[str]) -> str:
    if wanted and not conn.execute("SELECT 1 FROM shopping_items WHERE id = ?", (wanted,)).fetchone():
        return wanted
    return str(uuid.uuid4())


def put(tx: events.Change, journal: Journal, user: User, list_id: str, item: ItemInput,
        unknown: Dict[str, str], keep_id: Optional[str] = None) -> Dict[str, str]:
    """Add one item to ``list_id`` with the add rules, inside ``tx``. ``unknown``
    collects the names whose aisle is not known yet. ``keep_id``: an existing
    item (of the caller's own list) moved here as it is (invitations).
    Returns ``{id, name, status}``."""
    conn = tx.conn
    key = name_key(item.name)
    if item.id:
        row = conn.execute("SELECT id, name FROM shopping_items WHERE id = ? AND list_id = ?",
                           (item.id, list_id)).fetchone()
        if row is not None:
            return {"id": row["id"], "name": row["name"], "status": ALREADY}
    now = iso(values.utc_now())
    row = conn.execute("SELECT * FROM shopping_items WHERE list_id = ? AND name_key = ? AND checked = 0 "
                       "ORDER BY seq LIMIT 1", (list_id, key)).fetchone()
    if row is not None:
        changes = {c: v for c, v in (("quantity", item.quantity), ("note", item.note)) if v and v != row[c]}
        if changes:
            journal.item(conn, row["id"])
            conn.execute(f"UPDATE shopping_items SET {', '.join(f'{c} = ?' for c in changes)}, rev = rev + 1 "
                         "WHERE id = ?", (*changes.values(), row["id"]))
            tx.bump(list_id)
        return {"id": row["id"], "name": row["name"], "status": ALREADY}
    row = conn.execute("SELECT * FROM shopping_items WHERE list_id = ? AND name_key = ? AND checked = 1 "
                       "ORDER BY checked_at DESC LIMIT 1", (list_id, key)).fetchone()
    if row is not None:
        journal.item(conn, row["id"])
        conn.execute("UPDATE shopping_items SET checked = 0, checked_at = NULL, checked_by = NULL, quantity = ?, "
                     "note = ?, added_by = ?, created_at = ?, seq = ?, rev = rev + 1 WHERE id = ?",
                     (item.quantity, item.note, user.id, now, _next_seq(conn, list_id), row["id"]))
        tx.bump(list_id)
        return {"id": row["id"], "name": row["name"], "status": RESTORED}
    if keep_id is not None:
        journal.item(conn, keep_id)
        conn.execute("UPDATE shopping_items SET list_id = ?, seq = ?, rev = rev + 1 WHERE id = ?",
                     (list_id, _next_seq(conn, list_id), keep_id))
        tx.bump(list_id)
        return {"id": keep_id, "name": item.name, "status": ADDED}
    category = aisles.known(conn, list_id, key)
    if category is None:
        category = aisles.OTHER
        unknown[key] = item.name
    item_id = _new_id(conn, item.id)
    journal.item(conn, item_id)
    conn.execute("INSERT INTO shopping_items (id, list_id, name, name_key, quantity, note, category, added_by, "
                 "created_at, seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (item_id, list_id, item.name, key, item.quantity, item.note, category, user.id, now,
                  _next_seq(conn, list_id)))
    tx.bump(list_id)
    return {"id": item_id, "name": item.name, "status": ADDED}


def _names(names: Sequence[str]) -> str:
    quoted = [f"'{n}'" for n in names]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def _add_label(results: List[Dict[str, str]]) -> str:
    added = [r["name"] for r in results if r["status"] == ADDED]
    restored = [r["name"] for r in results if r["status"] == RESTORED]
    if len(added) + len(restored) > 3:
        return f"Added {len(added) + len(restored)} items to the shopping list"
    if added and not restored:
        return f"Added {_names(added)} to the shopping list"
    if restored and not added:
        return f"Put {_names(restored)} back on the shopping list"
    if added or restored:
        return f"Added {_names(added + restored)} to the shopping list"
    return "Changed the shopping list"


def add(user: User, lst: ShoppingList, items: List[ItemInput], source: str = "") -> Added:
    """Add ``items`` (at most 30) to ``lst``.

    Raises:
        ReadOnly: ``lst`` is shared read-only.
        names.TooMany: more than 30 items.
    """
    lst.require_edit()
    check_count(items)
    journal = Journal()
    unknown: Dict[str, str] = {}
    results: List[Dict[str, str]] = []
    with events.change() as tx:
        clear_old(tx, lst.id)
        for item in items:
            if name_key(item.name):
                results.append(put(tx, journal, user, lst.id, item, unknown))
        unknown.update(aisles.waiting(tx.conn, lst.id, set(unknown)))  # sorted by the same call
        journal.finish(tx.conn)
    undo = journal.register(user, _add_label(results), source or "shopping add")
    aisles.look_up_later(user, lst.id, unknown)
    return Added(results, undo)


# --- Changing --------------------------------------------------------------------------

def _row_on(conn: sqlite3.Connection, lst: ShoppingList, item_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM shopping_items WHERE id = ? AND list_id = ?", (item_id, lst.id)).fetchone()
    if row is None:
        raise NotFound("Item not found")
    return row


def _check(tx: events.Change, journal: Journal, user: User, lst: ShoppingList, row: sqlite3.Row,
           checked: bool) -> bool:
    """Tick / untick one item (history follows). False: it already was."""
    if bool(row["checked"]) == checked:
        return False
    conn = tx.conn
    journal.item(conn, row["id"])
    if checked:
        now = iso(values.utc_now())
        conn.execute("UPDATE shopping_items SET checked = 1, checked_at = ?, checked_by = ?, rev = rev + 1 WHERE id = ?",
                     (now, user.id, row["id"]))
        history.tick(conn, journal, lst.id, row["name_key"], row["name"], row["category"], now)
    else:
        if conn.execute("SELECT 1 FROM shopping_items WHERE list_id = ? AND name_key = ? AND checked = 0 AND id != ?",
                        (lst.id, row["name_key"], row["id"])).fetchone():
            raise Duplicate(f"'{row['name']}' is already on the list to buy.")
        conn.execute("UPDATE shopping_items SET checked = 0, checked_at = NULL, checked_by = NULL, rev = rev + 1 "
                     "WHERE id = ?", (row["id"],))
        history.untick(conn, journal, lst.id, row["name_key"])
    tx.bump(lst.id)
    return True


def update(user: User, lst: ShoppingList, item_id: str, changes: Dict[str, Any],
           source: str = "") -> Optional[Dict[str, str]]:
    """Change any of ``checked``, ``name``, ``quantity``, ``note``, ``category``
    of one item. A chosen aisle is remembered for the list. Returns the undo.

    Raises:
        ReadOnly: ``lst`` is shared read-only.
        NotFound: no such item on ``lst``.
        ValueError: an empty name or an unknown aisle; :class:`Duplicate` when
            the new name (or unticking) makes a second item to buy with that name.
    """
    lst.require_edit()
    journal = Journal()
    with events.change() as tx:
        clear_old(tx, lst.id)
        conn = tx.conn
        row = _row_on(conn, lst, item_id)
        sets: Dict[str, Any] = {}
        if changes.get("name") is not None:
            name = clip(changes["name"], MAX_NAME)
            key = name_key(name)
            if not key:
                raise ValueError("The name can't be empty.")
            if name != row["name"]:
                if key != row["name_key"] and not row["checked"] and conn.execute(
                        "SELECT 1 FROM shopping_items WHERE list_id = ? AND name_key = ? AND checked = 0 AND id != ?",
                        (lst.id, key, item_id)).fetchone():
                    raise Duplicate(f"'{name}' is already on the list.")
                sets.update(name=name, name_key=key)
                if key != row["name_key"] and changes.get("category") is None:
                    sets["category"] = aisles.known(conn, lst.id, key) or row["category"]
        for field, limit in (("quantity", MAX_QUANTITY), ("note", MAX_NOTE)):
            if changes.get(field) is not None and clip(changes[field], limit) != row[field]:
                sets[field] = clip(changes[field], limit)
        if changes.get("category") is not None:
            category = str(changes["category"])
            if category not in aisles.RANK:
                raise ValueError(f"Unknown aisle '{category}'.")
            journal.row(conn, "shopping_aisle_choices", list_id=lst.id, name_key=sets.get("name_key", row["name_key"]))
            aisles.remember_choice(conn, lst.id, sets.get("name_key", row["name_key"]), category)
            if category != row["category"]:
                sets["category"] = category
        if sets:
            journal.item(conn, item_id)
            conn.execute(f"UPDATE shopping_items SET {', '.join(f'{c} = ?' for c in sets)}, rev = rev + 1 WHERE id = ?",
                         (*sets.values(), item_id))
            tx.bump(lst.id)
        if changes.get("checked") is not None:
            _check(tx, journal, user, lst, _row_on(conn, lst, item_id), bool(changes["checked"]))
        journal.finish(conn)
    name = sets.get("name", row["name"])
    if set(changes) == {"checked"} and changes["checked"] is not None:
        label = f"Bought '{name}'" if changes["checked"] else f"Put '{name}' back on the list"
    else:
        label = f"Changed '{name}' on the shopping list"
    return journal.register(user, label, source or "shopping update")


def set_checked(user: User, lst: ShoppingList, item_ids: Sequence[str], checked: bool,
                source: str = "") -> Tuple[List[str], Optional[Dict[str, str]]]:
    """Tick (or untick) several items in one change. Returns the names changed and the undo.

    Raises:
        ReadOnly, NotFound (an id not on ``lst``), Duplicate (unticking a name already to buy).
    """
    lst.require_edit()
    journal = Journal()
    changed: List[str] = []
    with events.change() as tx:
        clear_old(tx, lst.id)
        for item_id in item_ids:
            row = _row_on(tx.conn, lst, item_id)
            if _check(tx, journal, user, lst, row, checked):
                changed.append(row["name"])
        journal.finish(tx.conn)
    if not changed:
        return changed, None
    verb = "Bought" if checked else "Put back on the list:"
    label = f"{verb} {_names(changed)}" if len(changed) <= 3 else (
        f"Bought {len(changed)} items" if checked else f"Put {len(changed)} items back on the list")
    return changed, journal.register(user, label, source or "shopping check")


def remove(user: User, lst: ShoppingList, item_ids: Sequence[str],
           source: str = "") -> Tuple[List[str], Optional[Dict[str, str]]]:
    """Remove items from ``lst`` in one change. Returns the names removed and the undo.

    Raises:
        ReadOnly, NotFound (an id not on ``lst``).
    """
    lst.require_edit()
    journal = Journal()
    gone: List[str] = []
    with events.change() as tx:
        clear_old(tx, lst.id)
        for item_id in item_ids:
            row = _row_on(tx.conn, lst, item_id)
            journal.item(tx.conn, item_id)
            tx.conn.execute("DELETE FROM shopping_items WHERE id = ?", (item_id,))
            gone.append(row["name"])
        if gone:
            tx.bump(lst.id)
        journal.finish(tx.conn)
    label = f"Removed {_names(gone)} from the shopping list" if len(gone) <= 3 else f"Removed {len(gone)} items"
    return gone, journal.register(user, label, source or "shopping remove") if gone else None


def clear_bought(user: User, lst: ShoppingList, source: str = "") -> Tuple[int, Optional[Dict[str, str]]]:
    """Remove every bought item at once. Returns how many and the undo.

    Raises:
        ReadOnly: ``lst`` is shared read-only.
    """
    lst.require_edit()
    journal = Journal()
    with events.change() as tx:
        clear_old(tx, lst.id)
        ids = [r["id"] for r in tx.conn.execute("SELECT id FROM shopping_items WHERE list_id = ? AND checked = 1",
                                                (lst.id,))]
        for item_id in ids:
            journal.item(tx.conn, item_id)
        if ids:
            tx.conn.execute("DELETE FROM shopping_items WHERE list_id = ? AND checked = 1", (lst.id,))
            tx.bump(lst.id)
        journal.finish(tx.conn)
    if not ids:
        return 0, None
    return len(ids), journal.register(user, f"Cleared {len(ids)} bought item{'s' if len(ids) != 1 else ''}",
                                      source or "shopping clear")


def forget_suggestion(user: User, lst: ShoppingList, name: str, source: str = "") -> Optional[Dict[str, str]]:
    """Stop suggesting ``name`` on ``lst``. Returns the undo (None: it was not suggested).

    Raises:
        ReadOnly: ``lst`` is shared read-only.
    """
    lst.require_edit()
    journal = Journal()
    with events.change() as tx:
        if history.forget(tx.conn, journal, lst.id, name):
            tx.bump(lst.id)
        journal.finish(tx.conn)
    return journal.register(user, f"Stopped suggesting '{clip(name, MAX_NAME)}'", source or "shopping forget")



def search(lst: ShoppingList, like: str, limit: int = 30) -> List[sqlite3.Row]:
    """``id, name, checked`` of the items of ``lst`` whose name matches the SQL
    LIKE pattern ``like`` (``\\`` escapes), to buy first (the search box)."""
    with get_db_connection() as conn:
        return conn.execute("SELECT id, name, checked FROM shopping_items WHERE list_id = ? AND name LIKE ? "
                            "ESCAPE '\\' ORDER BY checked, seq LIMIT ?", (lst.id, like, limit)).fetchall()
