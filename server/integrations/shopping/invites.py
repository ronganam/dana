"""Answering an invitation to a shared list (docs/shopping.md, "Lists").

``use`` makes a list shared with the user to edit their active list and, with
``move_items``, moves the items to buy of their own list onto it with the add
rules (a name already there to buy is not added twice; their own row goes).
``dismiss`` hides the invitation. Each answer is one change with one undo
(``use``: the moved items come back and the active list goes back too). A
list that is not shared with the user answers like an unknown list; a list
shared read-only cannot be used (it can still be looked at with ``?list=``).
"""

import logging
from typing import Dict, List, Optional, Tuple

from core.undoable import UndoConflict
from core.users import User
from integrations.shopping import events, lists, values
from integrations.shopping.items import put
from integrations.shopping.journal import Journal
from integrations.shopping.lists import ShoppingList
from integrations.shopping.names import ItemInput
from integrations.shopping.values import NotFound, ReadOnly, iso

logger = logging.getLogger(__name__)

USE, DISMISS = "use", "dismiss"


def _shared(user: User, list_id: str) -> ShoppingList:
    lst = lists.get_list(user, list_id)
    if lst is None or not lst.shared:
        raise NotFound("Shopping list not found")
    return lst


def _answer(tx: events.Change, journal: Journal, user: User, list_id: str, answer: str) -> None:
    journal.row(tx.conn, "shopping_invite_answers", list_id=list_id, user_id=user.id)
    tx.conn.execute("INSERT INTO shopping_invite_answers (list_id, user_id, answer, answered_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(list_id, user_id) DO UPDATE SET answer = excluded.answer, "
                    "answered_at = excluded.answered_at", (list_id, user.id, answer, iso(values.utc_now())))
    tx.signal(user.id)


def use(user: User, list_id: str, move_items: bool = False) -> Tuple[List[Dict[str, str]], Optional[Dict[str, str]]]:
    """Make the shared list ``list_id`` ``user``'s active list; with
    ``move_items`` move their own items to buy onto it. Returns the moved
    items' results (``[{id, name, status}]``) and the undo.

    Raises:
        NotFound: the list is not shared with ``user``.
        ReadOnly: it is shared with them read-only.
    """
    target = _shared(user, list_id)
    target.require_edit()
    before = lists.active_list(user).id
    lists.set_active(user, target.id, by=user)
    own_id = lists.own_list_id(user)
    journal = Journal()
    moved: List[Dict[str, str]] = []
    with events.change() as tx:
        _answer(tx, journal, user, target.id, USE)
        if move_items and own_id is not None and own_id != target.id:
            own = tx.conn.execute("SELECT * FROM shopping_items WHERE list_id = ? AND checked = 0 ORDER BY seq",
                                  (own_id,)).fetchall()
            for row in own:
                result = put(tx, journal, user, target.id, ItemInput(row["name"], row["quantity"], row["note"]), {},
                             keep_id=row["id"])
                if result["id"] != row["id"]:
                    journal.item(tx.conn, row["id"])
                    tx.conn.execute("DELETE FROM shopping_items WHERE id = ?", (row["id"],))
                moved.append(result)
            if own:
                tx.bump(own_id)
        journal.finish(tx.conn)

    def _back() -> None:
        try:
            lists.set_active(user, before, by=user)
        except (NotFound, ReadOnly):
            raise UndoConflict("You can no longer use the list you were on before.") from None

    label = f"Switched to {target.owner_name}'s shopping list"
    if moved:
        label += f" and moved {len(moved)} item{'s' if len(moved) != 1 else ''}"
    return moved, journal.register(user, label, "shopping invite", also=_back)


def dismiss(user: User, list_id: str) -> Optional[Dict[str, str]]:
    """Hide the invitation to ``list_id``. Returns the undo.

    Raises:
        NotFound: the list is not shared with ``user``.
    """
    target = _shared(user, list_id)
    journal = Journal()
    with events.change() as tx:
        _answer(tx, journal, user, target.id, DISMISS)
        journal.finish(tx.conn)
    return journal.register(user, f"Hid {target.owner_name}'s shopping list invitation", "shopping invite")

