"""The Shopping screen as one payload (docs/shopping.md, "API").

``screen(user, lst)`` → ``{list, categories, items, counts, usuals, invites,
lists}``: what every client shows, read in one go after the list's old
bought items are cleared. Every write answers with the same fields.
"""

from typing import Any, Dict

from core.db import get_db_connection
from core.users import User
from integrations.shopping import aisles, history, items, lists
from integrations.shopping.lists import ShoppingList


def list_info(lst: ShoppingList, version: int) -> Dict[str, Any]:
    """``list``: ``{id, name, access, owner: {id, name}, people: [{id, name, access}], version}``."""
    return {"id": lst.id, "name": lst.name, "access": lst.access,
            "owner": lists.person(lst.owner_id) or {"id": lst.owner_id, "name": ""},
            "people": lists.people(lst), "version": version}


def screen(user: User, lst: ShoppingList) -> Dict[str, Any]:
    """Everything the Shopping screen shows for ``user`` looking at ``lst``."""
    items.expire(lst.id)
    with get_db_connection() as conn:
        row = conn.execute("SELECT version FROM shopping_lists WHERE id = ?", (lst.id,)).fetchone()
        found = items.rows(conn, lst.id)
    to_buy = sum(1 for r in found if not r["checked"])
    return {
        "list": list_info(lst, int(row["version"]) if row else 0),
        "categories": aisles.payload(),
        "items": [items.payload(r) for r in found],
        "counts": {"to_buy": to_buy, "bought": len(found) - to_buy},
        "usuals": history.usuals(lst.id),
        "invites": lists.pending_invites(user),
        "lists": lists.lists_payload(user)["lists"],
    }
