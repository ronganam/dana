"""Dana's shopping tools (docs/shopping.md, "Dana's tools"): thin wrappers over
:mod:`.items` on the user's active list.

Items are named by name or id; a name resolves without reading the list
first (:func:`.store_items.find`), and one that matches nothing, or several items,
is reported back with the candidates. Every change runs at once and
registers the same undo the apps get.
"""

import logging
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import WithJsonSchema

from core import users
from core.users import User
from core.addons import Connection
from addons.shopping import aisles, items as store_items, lists
from addons.shopping.addon import addon
from addons.shopping.lists import ShoppingList
from addons.shopping.names import TooMany, item_input
from addons.shopping.values import MAX_ITEMS, Duplicate, NotFound, ReadOnly

logger = logging.getLogger(__name__)

# The items of add_shopping_items: objects with their own quantity and note, inline (no $ref).
ItemList = Annotated[List[Dict[str, Any]], WithJsonSchema({
    "type": "array",
    "items": {"type": "object", "properties": {
        "name": {"type": "string", "description": "The product as the user said it, without the amount "
                                                  "(e.g. 'חלב', 'tomatoes')."},
        "quantity": {"type": "string", "description": "How much, when said: '2', '1 kg', '3 ליטר'."},
        "note": {"type": "string", "description": "A detail, when said: '3%', 'lactose-free', 'the big one'."},
    }, "required": ["name"]},
})]


def _note(lst: ShoppingList) -> str:
    """`` (shared by <owner>)`` for a list shared with the user, else nothing."""
    return f" (shared by {lst.owner_name})" if lst.shared else ""


def _line(item: Dict[str, Any]) -> str:
    extra = f" ({item['quantity']})" if item["quantity"] else ""
    note = f" — {item['note']}" if item["note"] else ""
    return f"- {item['name']}{extra}{note} [id: {item['id']}]"


@addon.tool()
async def get_shopping_list(conn: Connection) -> str:
    """Show the shopping list (groceries and supermarket products) by aisle: each item with its id, quantity and note, and what was bought.

    Use it to answer questions about the list; to add, tick or remove items there is no need to read it first.
    """
    lst = lists.active_list(conn.user)
    found = store_items.list_items(lst)
    to_buy = [i for i in found if not i["checked"]]
    bought = [i for i in found if i["checked"]]
    if not found:
        return f"The shopping list is empty. 🛒{_note(lst)}"
    lines = [f"🛒 Shopping list{_note(lst)}: {len(to_buy)} to buy"]
    section = None
    for item in to_buy:
        if item["category"] != section:
            section = item["category"]
            lines.append(aisles.NAMES.get(section, section))
        lines.append(_line(item))
    if bought:
        lines.append(f"✅ Bought ({len(bought)}):")
        lines.extend(_line(i) for i in bought)
    return "\n".join(lines)


@addon.tool(is_write=True)
async def add_shopping_items(conn: Connection, items: ItemList) -> str:
    """Add groceries and supermarket products to the shopping list, each with its own quantity and note when the user gave them. A product already on the list is not added twice (a quantity or note given now replaces the old one); one bought before comes back to buy.

    Args:
        items: One entry per product, e.g. [{"name": "חלב", "quantity": "2", "note": "3%"}, {"name": "bread"}].
    """
    user = conn.user
    try:
        wanted = [x for x in (item_input(v) for v in (items or [])) if x is not None]
    except ValueError as e:
        return f"Error: {e}"
    if not wanted:
        return "Error: no items to add."
    lst = lists.active_list(user)
    try:
        added = store_items.add(user, lst, wanted, source="add_shopping_items")
    except TooMany:
        return f"Error: at most {MAX_ITEMS} items at a time."
    parts = []
    for result, item in zip(added.results, wanted):
        amount = f" ({item.quantity})" if item.quantity else ""
        if result["status"] == store_items.ADDED:
            parts.append(f"added {result['name']}{amount}")
        elif result["status"] == store_items.RESTORED:
            parts.append(f"put {result['name']}{amount} back on the list")
        else:
            changed = " (updated)" if item.quantity or item.note else ""
            parts.append(f"{result['name']} was already on the list{changed}")
    text = "; ".join(parts)
    return f"{text[:1].upper()}{text[1:]}.{_note(lst)}"


def _resolve(lst: ShoppingList, refs: List[str], prefer_checked: Optional[bool]) -> tuple:
    found, problems = [], []
    for ref in refs:
        match = store_items.find(lst, str(ref), prefer_checked)
        if match.item is not None:
            if match.item["id"] not in [f["id"] for f in found]:
                found.append(match.item)
        elif match.candidates:
            names = ", ".join(f"{c['name']}{' (bought)' if c['checked'] else ''} [id: {c['id']}]"
                              for c in match.candidates[:8])
            problems.append(f"'{ref}' matches several items: {names}")
        else:
            problems.append(f"'{ref}' is not on the list")
    return found, problems


@addon.tool(is_write=True)
async def update_shopping_items(conn: Connection, action: Literal["bought", "to_buy", "remove", "clear_bought"],
                                items: Optional[List[str]] = None) -> str:
    """Mark shopping-list items bought or back to buy, remove them, or clear every bought item. Name the items as the user did, or by id; no need to read the list first.

    Args:
        action: 'bought' (ticked off), 'to_buy' (back on the list), 'remove' (delete), or 'clear_bought' (remove every bought item; no items needed).
        items: The items, by name or id, e.g. ["milk", "bread"].
    """
    user = conn.user
    lst = lists.active_list(user)
    try:
        if action == "clear_bought":
            n, _ = store_items.clear_bought(user, lst, source="update_shopping_items")
            return f"Cleared {n} bought item{'s' if n != 1 else ''}." if n else "There were no bought items to clear."
        prefer = {"bought": False, "to_buy": True}.get(action)
        found, problems = _resolve(lst, list(items or []), prefer)
        done = ""
        if found:
            ids = [i["id"] for i in found]
            if action == "remove":
                names, _ = store_items.remove(user, lst, ids, source="update_shopping_items")
                done = f"Removed {', '.join(names)}."
            else:
                names, _ = store_items.set_checked(user, lst, ids, action == "bought", source="update_shopping_items")
                state = "bought" if action == "bought" else "back on the list"
                done = f"Marked {', '.join(names)} {state}." if names else f"Already {state}."
    except ReadOnly as e:
        return f"Error: {e}"
    except (Duplicate, NotFound) as e:
        return f"Error: {e}"
    out = " ".join(x for x in [done] + [p + "." for p in problems] if x)
    return (out or "Error: no items named.") + _note(lst)


@addon.tool(is_write=True)
async def edit_shopping_item(conn: Connection, item: str, name: Optional[str] = None, quantity: Optional[str] = None,
                             note: Optional[str] = None, aisle: Optional[str] = None) -> str:
    """Change one shopping-list item: its name, quantity, note or aisle. Leave out what stays; an empty text clears a quantity or note.

    Args:
        item: The item, by name or id.
        name: The new name.
        quantity: The new quantity ('2', '1 kg'); '' clears it.
        note: The new note; '' clears it.
        aisle: One of produce, bakery, dairy, meat, pantry, snacks, drinks, frozen, household, care, baby, pets, other (remembered for this name on this list).
    """
    user = conn.user
    lst = lists.active_list(user)
    found, problems = _resolve(lst, [item], None)
    if not found:
        return f"Error: {problems[0]}."
    changes = {k: v for k, v in (("name", name), ("quantity", quantity), ("note", note), ("category", aisle))
               if v is not None}
    if not changes:
        return "Error: nothing to change."
    try:
        store_items.update(user, lst, found[0]["id"], changes, source="edit_shopping_item")
    except (ReadOnly, Duplicate, NotFound, ValueError) as e:
        return f"Error: {e}"
    now = store_items.get(lst, found[0]["id"]) or found[0]
    return f"Changed: {_line(now)[2:]}{_note(lst)}"


def _choice_available(user: User) -> bool:
    """``choose_shopping_list`` is offered only on a server with more than one
    user (a single user has only their own list), like ``share_item``."""
    return len(users.list_users()) > 1


def _match_list(user: User, ref: str) -> Optional[ShoppingList]:
    """The visible list ``ref`` names: its id, its name, or its owner's name
    (full, or the start of it); the user's own list wins a tie."""
    wanted = ref.strip().lower()
    for lst in lists.visible_lists(user):
        owner_name = lst.owner_name.lower()
        if wanted in (lst.id.lower(), lst.name.lower()) or (owner_name and owner_name.startswith(wanted)):
            return lst
    return None


@addon.tool(is_write=True, available=_choice_available)
async def choose_shopping_list(conn: Connection, list_name: str = "") -> str:
    """Show which shopping lists you can use (your own and the ones other people shared with you) and which one
    your shopping changes go to; with list_name, switch to that list. Sharing a list with someone never switches
    it for them: they choose it here.

    Args:
        list_name: The list to switch to: its name, its owner's name or its id. Empty: only show the lists.
    """
    user = conn.user
    if list_name.strip():
        lst = _match_list(user, list_name)
        if lst is None:
            return f"Error: no shopping list matches '{list_name}'."
        try:
            chosen, _ = lists.choose(user, lst.id, source="choose_shopping_list")
        except (ReadOnly, NotFound) as e:
            return f"Error: {e}"
        return f"Your shopping changes now go to: {chosen.name}{_note(chosen)}."
    active = lists.active_list(user)
    lines = ["🛒 Shopping lists you can use:"]
    for lst in lists.visible_lists(user):
        who = "yours" if not lst.shared else f"shared by {lst.owner_name}, {lst.access}"
        lines.append(f"- {lst.name} ({who}){' — active' if lst.id == active.id else ''} [id: {lst.id}]")
    return "\n".join(lines)
