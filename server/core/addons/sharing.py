"""Sharing items between users (docs/access.md, "Sharing").

An item (a task list, a shopping list, a knowledge project) has one owner,
known to its service. The owner can give another user ``read`` or ``edit`` on
it; only the owner shares, unshares or deletes it. Grants live in one table,
``item_access`` (migration ``m0006_item_access``): item type, item id, user,
level. The owner is never a row.

A service registers each shareable item type once, at import::

    sharing.register(sharing.ItemType(
        name="task_list", addon="tasks", label="task list",
        owner_of=groups.owner_of,        # item id -> owner's user id (None: no such item)
        find_owned=groups.find_owned,    # (owner, id or name) -> id of an item they own
        title_of=groups.title_of,        # item id -> its name
    ))

and then asks the two questions everywhere it lists, reads or writes:

- :func:`shared_ids` — the items of a type shared with a user (id → level);
  a listing is the user's own items plus these;
- :func:`access` / :func:`can_see` / :func:`can_edit` — what a user may do
  with one item (``owner`` / ``edit`` / ``read`` / None).

A user sees what is shared with them only while they have the type's addon
connected (a shared task list shows up through their own ``tasks``).
:func:`set_level` (owner only) grants, changes and revokes; :func:`shares`
lists who has access; :func:`forget` drops every grant of a deleted item.
The ``share_item`` tool (``addons/essentials/tools.py``) and the HTTP API
(``core/sharing_api.py``) are generic over the registered types.
"""

import logging
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, List, Optional

from core import user_rows, users
from core.db import get_db_connection
from core.users import User
from core.addons import store

logger = logging.getLogger(__name__)

READ = "read"
EDIT = "edit"
NONE = "none"
OWNER = "owner"
LEVELS = (READ, EDIT)


class NotOwner(PermissionError):
    """Only the owner may share, unshare, rename or delete the item."""


@dataclass(frozen=True)
class ItemType:
    """An addon of shareable item, registered by its service.

    Args:
        name: Wire name (``task_list``): the ``item_type`` of the tool and of
            ``/api/sharing/{item_type}/{id}``.
        addon: The addon the item belongs to (``tasks``); a user
            needs it connected to see, share or receive the item.
        label: The item in words, for messages ("task list").
        owner_of: Item id → the owner's user id; None when there is no such item.
        find_owned: (owner, id or name) → the id of an item that user owns
            (names case-insensitive), None when they own none by that id / name.
        title_of: Item id → its display name (None when there is no such item).
        on_change: Optional hook called with (item id, user id, level before
            or None, level after or None) after a grant changes (shopping: the
            active list, invitations, live updates).
    """
    name: str
    addon: str
    label: str
    owner_of: Callable[[str], Optional[str]]
    find_owned: Callable[[User, str], Optional[str]]
    title_of: Callable[[str], Optional[str]]
    on_change: Optional[Callable[[str, str, Optional[str], Optional[str]], None]] = None


@dataclass(frozen=True)
class Share:
    """One grant: ``user_id`` may ``level`` the item."""
    item_type: str
    item_id: str
    user_id: str
    level: str
    granted_by: str
    granted_at: str


_types: Dict[str, ItemType] = {}


def register(item_type: ItemType) -> ItemType:
    """Register a shareable item type (once, at import of its service).

    Raises:
        ValueError: the name is taken by another registration.
    """
    existing = _types.get(item_type.name)
    if existing is not None and existing != item_type:
        raise ValueError(f"Item type {item_type.name!r} is registered twice")
    _types[item_type.name] = item_type
    return item_type


def item_type(name: str) -> ItemType:
    """The registered type called ``name``.

    Raises:
        KeyError: no such item type.
    """
    try:
        return _types[name]
    except KeyError:
        raise KeyError(f"Unknown item type {name!r} (known: {', '.join(sorted(_types)) or 'none'})") from None


def item_types() -> List[ItemType]:
    """Every registered type, in registration order."""
    return list(_types.values())


def shareable_addons() -> FrozenSet[str]:
    """Addons that have at least one shareable item type."""
    return frozenset(t.addon for t in _types.values())


def tool_available(user: User) -> bool:
    """Whether ``share_item`` is offered to ``user``: the server has more than
    one user and ``user`` has an addon with shareable items connected."""
    if len(users.list_users()) < 2:
        return False
    return bool(shareable_addons() & set(store.connected_addons(user)))


# --- Questions services ask ---------------------------------------------------------

def _rows(sql: str, args: tuple) -> List[Share]:
    with get_db_connection() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [Share(item_type=r["item_type"], item_id=r["item_id"], user_id=r["user_id"], level=r["level"],
                  granted_by=r["granted_by"], granted_at=r["granted_at"]) for r in rows]


def shared_with(user: Optional[User], type_name: str) -> List[Share]:
    """The items of ``type_name`` shared with ``user``, most recent grant
    first (empty without a user or when they lack the type's addon)."""
    t = item_type(type_name)
    if user is None or not store.is_connected(user, t.addon):
        return []
    return _rows("SELECT * FROM item_access WHERE user_id = ? AND item_type = ? ORDER BY granted_at DESC, rowid DESC",
                 (user.id, type_name))


def shared_ids(user: Optional[User], type_name: str) -> Dict[str, str]:
    """Item id → level (``read`` / ``edit``) of the items of ``type_name``
    shared with ``user`` (see :func:`shared_with`)."""
    return {s.item_id: s.level for s in shared_with(user, type_name)}


def access(user: Optional[User], type_name: str, item_id: Optional[str]) -> Optional[str]:
    """What ``user`` may do with the item: ``owner``, ``edit``, ``read`` or
    None (cannot see it, or it does not exist)."""
    t = item_type(type_name)
    if user is None or not item_id or not store.is_connected(user, t.addon):
        return None
    owner = t.owner_of(item_id)
    if owner is None:
        return None
    if owner == user.id:
        return OWNER
    with get_db_connection() as conn:
        row = conn.execute("SELECT level FROM item_access WHERE item_type = ? AND item_id = ? AND user_id = ?",
                           (type_name, item_id, user.id)).fetchone()
    return row["level"] if row else None


def can_see(user: Optional[User], type_name: str, item_id: Optional[str]) -> bool:
    return access(user, type_name, item_id) is not None


def can_edit(user: Optional[User], type_name: str, item_id: Optional[str]) -> bool:
    return access(user, type_name, item_id) in (OWNER, EDIT)


# --- Granting -----------------------------------------------------------------------

def shares(type_name: str, item_id: str) -> List[Share]:
    """Who besides the owner has access to the item, oldest grant first."""
    item_type(type_name)
    return _rows("SELECT * FROM item_access WHERE item_type = ? AND item_id = ? ORDER BY granted_at, rowid",
                 (type_name, item_id))


def set_level(owner: User, type_name: str, item_id: str, user_id: str, level: str) -> Optional[str]:
    """Give ``user_id`` ``level`` (``read`` / ``edit``) on the item, or take
    their access away (``none``). Only the item's owner may. Returns the
    level they had before (None: no access).

    Raises:
        KeyError: unknown item type.
        LookupError: ``owner`` cannot see the item (or it does not exist) —
            answer it like a missing item.
        NotOwner: ``owner`` can see the item but does not own it.
        ValueError: unknown level, unknown user, or the owner themselves.
    """
    t = item_type(type_name)
    level = (level or "").strip().lower()
    if level not in (*LEVELS, NONE):
        raise ValueError(f"level must be one of {', '.join((*LEVELS, NONE))}")
    mine = access(owner, type_name, item_id)
    if mine is None:
        raise LookupError(f"No such {t.label}.")
    if mine != OWNER:
        raise NotOwner(f"Only the owner of the {t.label} can share it.")
    target = users.get(user_id)
    if target is None:
        raise ValueError(f"No user {user_id!r}.")
    if target.id == owner.id:
        raise ValueError(f"You own the {t.label}; it is not shared with yourself.")
    with get_db_connection() as conn:
        row = conn.execute("SELECT level FROM item_access WHERE item_type = ? AND item_id = ? AND user_id = ?",
                           (type_name, item_id, target.id)).fetchone()
        before = row["level"] if row else None
        if level == NONE:
            conn.execute("DELETE FROM item_access WHERE item_type = ? AND item_id = ? AND user_id = ?",
                         (type_name, item_id, target.id))
        elif before is None:
            conn.execute("INSERT INTO item_access (item_type, item_id, user_id, level, granted_by, granted_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (type_name, item_id, target.id, level, owner.id,
                                                       user_rows.now_iso()))
        elif before != level:
            conn.execute("UPDATE item_access SET level = ?, granted_by = ?, granted_at = ? "
                         "WHERE item_type = ? AND item_id = ? AND user_id = ?",
                         (level, owner.id, user_rows.now_iso(), type_name, item_id, target.id))
        conn.commit()
    after = None if level == NONE else level
    if before != after:
        logger.info(f"{type_name} {item_id}: {target.id} {before or 'none'} -> {after or 'none'} (by {owner.id})")
        if t.on_change is not None:
            try:
                t.on_change(item_id, target.id, before, after)
            except Exception:
                logger.error(f"on_change of {type_name} failed", exc_info=True)
    return before


def forget(type_name: str, item_id: str) -> None:
    """Drop every grant of an item (its service calls this when it deletes the item)."""
    item_type(type_name)
    with get_db_connection() as conn:
        conn.execute("DELETE FROM item_access WHERE item_type = ? AND item_id = ?", (type_name, item_id))
        conn.commit()
