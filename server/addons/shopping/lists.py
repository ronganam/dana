"""Shopping lists per user, the active list, sharing and invitations
(docs/shopping.md, "Lists"; docs/access.md, "Sharing").

Every user with ``shopping`` connected owns one list (``shopping_lists``,
made when the addon is connected and at every start). A list can be shared
with other users (:mod:`core.addons.sharing`, item type
``shopping_list``, ``read`` / ``edit``).

**The active list** is the list a user's shopping requests go to: the
``shopping`` setting ``active_list`` (unset: their own list), which may name
only their own list or one shared with them with ``edit``
(:func:`set_active`). A share never changes it by itself; when the chosen
list's share is removed or lowered to ``read`` they are back on their own
list and the choice is cleared.

**Invitations.** A list shared with a user is an invitation for them while
it is not their active list and they have not answered it
(``shopping_invite_answers``: ``use`` / ``dismiss``). A new share also puts a
notification on their phone. Removing the share forgets the answer.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core import notify, users
from core.db import get_db_connection
from core.undoable import UndoConflict, safe_record
from core.users import User
from core.addons import sharing, store
from core.addons.addon import InvalidValue
from addons.shopping import events
from addons.shopping.values import ACTIVE_SETTING, DEFAULT_NAME, ITEM_TYPE, ADDON, NotFound, ReadOnly

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShoppingList:
    """A list as one user sees it.

    Attributes:
        id: The list id.
        owner_id: The owner's user id.
        name: The list's name.
        user_id: The user this view is for.
        access: What that user may do: ``owner``, ``edit`` or ``read``.
    """
    id: str
    owner_id: str
    name: str
    user_id: str
    access: str

    @property
    def shared(self) -> bool:
        """Shared with the user (not their own)."""
        return self.access != sharing.OWNER

    @property
    def editable(self) -> bool:
        return self.access in (sharing.OWNER, sharing.EDIT)

    @property
    def owner_name(self) -> str:
        owner = users.get(self.owner_id)
        return owner.name if owner else ""

    def require_edit(self) -> None:
        """Raises:
            ReadOnly: the list is shared with the user read-only."""
        if not self.editable:
            raise ReadOnly("This shopping list is shared with you read-only.")


def person(user_id: Optional[str]) -> Optional[Dict[str, str]]:
    """``{id, name}`` of a user (None: no such user)."""
    who = users.get(user_id) if user_id else None
    return {"id": who.id, "name": who.name} if who else None


# --- Own lists -----------------------------------------------------------------------

def own_list_id(user: User) -> Optional[str]:
    """The id of the list ``user`` owns (None: none yet)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT id FROM shopping_lists WHERE user_id = ? ORDER BY created_at, rowid LIMIT 1",
                           (user.id,)).fetchone()
    return row["id"] if row else None


def ensure_list(user: User) -> str:
    """Give ``user`` their own list when they have none (``on_connect`` of the
    kind, and at every start). Returns its id."""
    existing = own_list_id(user)
    if existing is not None:
        return existing
    list_id = uuid.uuid4().hex[:12]
    with get_db_connection() as conn:
        conn.execute("INSERT INTO shopping_lists (id, user_id, name) VALUES (?, ?, ?)",
                     (list_id, user.id, DEFAULT_NAME))
        conn.commit()
    logger.info(f"Shopping list {list_id} made for user {user.id}")
    return list_id


def ensure_lists_for_everyone() -> None:
    """:func:`ensure_list` for every user with ``shopping`` connected (at startup)."""
    for uid in store.users_with(ADDON):
        user = users.get(uid)
        if user is not None:
            ensure_list(user)


# --- Sharing -------------------------------------------------------------------------

def owner_of(list_id: str) -> Optional[str]:
    """The owner's user id of list ``list_id`` (None: no such list)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT user_id FROM shopping_lists WHERE id = ?", (list_id,)).fetchone()
    return row["user_id"] if row else None


def title_of(list_id: str) -> Optional[str]:
    with get_db_connection() as conn:
        # scoped: title of a list the caller resolved for the user (sharing ItemType.title_of)
        row = conn.execute("SELECT name FROM shopping_lists WHERE id = ?", (list_id,)).fetchone()
    return row["name"] if row else None


def find_owned(user: User, ref: str) -> Optional[str]:
    """The list ``user`` owns: by id or name (case-insensitive), else their one
    own list whatever ``ref`` says (a user has a single shopping list)."""
    ref = (ref or "").strip()
    with get_db_connection() as conn:
        row = conn.execute("SELECT id FROM shopping_lists WHERE user_id = ? AND (id = ? OR LOWER(name) = LOWER(?)) "
                           "LIMIT 1", (user.id, ref, ref)).fetchone()
    return row["id"] if row else own_list_id(user)


def _share_changed(list_id: str, user_id: str, before: Optional[str], after: Optional[str]) -> None:
    """A share of ``list_id`` with ``user_id`` changed (the list's people did):
    the list's version rises and the user's streams are told; without
    ``edit`` it can no longer be their active list (the choice is cleared);
    without any access their answer to the invitation is forgotten; a new
    share puts the invitation on their phone."""
    user = users.get(user_id)
    if user is None:
        return
    conn = store.get(user, ADDON)
    if after != sharing.EDIT and conn is not None and conn.settings.get(ACTIVE_SETTING) == list_id:
        store.update(user, ADDON, settings={ACTIVE_SETTING: None})
        logger.info(f"Shopping list {list_id} is no longer editable for user {user_id}: back on their own list")
    with events.change() as tx:
        if after is None:
            tx.conn.execute("DELETE FROM shopping_invite_answers WHERE list_id = ? AND user_id = ?", (list_id, user_id))
        tx.bump(list_id)
        tx.signal(user_id)
    if before is None and after is not None and conn is not None:
        _notify_invite(user, list_id)


def _notify_invite(user: User, list_id: str) -> None:
    """"<owner> shared their shopping list with you" on ``user``'s phone (needs a running loop and ``phone``)."""
    owner = users.get(owner_of(list_id) or "")
    if owner is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(notify.push_notify(user, "Shopping list", f"{owner.name} shared their shopping list with you",
                                        tag=f"shopping-invite-{list_id}"))


sharing.register(sharing.ItemType(name=ITEM_TYPE, addon=ADDON, label="shopping list", owner_of=owner_of,
                                  find_owned=find_owned, title_of=title_of, on_change=_share_changed))


# --- Lookups -------------------------------------------------------------------------

def _view(user: User, list_id: str, level: str) -> Optional[ShoppingList]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT id, user_id, name FROM shopping_lists WHERE id = ?", (list_id,)).fetchone()
    if row is None:
        return None
    return ShoppingList(id=row["id"], owner_id=row["user_id"], name=row["name"], user_id=user.id, access=level)


def get_list(user: User, list_id: Optional[str]) -> Optional[ShoppingList]:
    """List ``list_id`` as ``user`` sees it (None: they cannot see it, or no such list)."""
    level = sharing.access(user, ITEM_TYPE, list_id)
    if level is None or not list_id:
        return None
    return _view(user, list_id, level)


def _chosen(user: User) -> Optional[str]:
    conn = store.get(user, ADDON)
    value = conn.settings.get(ACTIVE_SETTING) if conn is not None else None
    return str(value) if value else None


def active_list(user: User) -> ShoppingList:
    """The list ``user``'s shopping actions work on: the one they chose
    while it is shared with them with ``edit``, else their own (made now if
    they have none)."""
    chosen = _chosen(user)
    if chosen and sharing.access(user, ITEM_TYPE, chosen) == sharing.EDIT:
        lst = _view(user, chosen, sharing.EDIT)
        if lst is not None:
            return lst
    own = _view(user, own_list_id(user) or ensure_list(user), sharing.OWNER)
    assert own is not None
    return own


def set_active(user: User, list_id: str, by: Optional[User] = None) -> ShoppingList:
    """Make ``list_id`` (their own, or one shared with them with ``edit``)
    ``user``'s active list. ``by``: who makes the change (None: ``user``
    themselves or an operator).

    Raises:
        NotFound: ``user`` cannot see that list.
        ReadOnly: it is shared with them read-only.
    """
    level = sharing.access(user, ITEM_TYPE, list_id)
    if level is None:
        raise NotFound("Shopping list not found")
    if level not in (sharing.OWNER, sharing.EDIT):
        raise ReadOnly("This list is shared with you read-only, so it cannot be your active list.")
    store.update(user, ADDON, settings={ACTIVE_SETTING: None if level == sharing.OWNER else list_id}, by=by)
    events.signal_user(user.id)
    return active_list(user)


def choose(user: User, list_id: str, source: str = "") -> Tuple[ShoppingList, Optional[Dict[str, str]]]:
    """:func:`set_active` that registers its undo (``{id, label}``; None when it
    already was the active list).

    Raises:
        NotFound, ReadOnly: as :func:`set_active`.
    """
    before = active_list(user)
    chosen = set_active(user, list_id, by=user)
    if chosen.id == before.id:
        return chosen, None

    def _undo() -> None:
        try:
            set_active(user, before.id, by=user)
        except (NotFound, ReadOnly):
            raise UndoConflict("You can no longer use the list you were on before.") from None
    label = "Switched to your own shopping list" if not chosen.shared else \
        f"Switched to {chosen.owner_name}'s shopping list"
    undo_id = safe_record(label, _undo, source or "shopping choose")
    return chosen, ({"id": undo_id, "label": label} if undo_id else None)


def validate_settings(user: User, changes: Mapping[str, Any], credentials: Mapping[str, Any],
                      settings: Mapping[str, Any], by: Optional[User]) -> None:
    """``Addon(validate=)`` of ``shopping``: the active list is the
    user's own or one shared with them with ``edit``.

    Raises:
        InvalidValue: the rule is broken.
    """
    active = settings.get(ACTIVE_SETTING) if ACTIVE_SETTING in changes else None
    if active not in (None, "") and sharing.access(user, ITEM_TYPE, str(active)) not in (sharing.OWNER, sharing.EDIT):
        raise InvalidValue(ACTIVE_SETTING, "The active list can only be your own list or one shared with you to edit.")


def visible_lists(user: User) -> List[ShoppingList]:
    """``user``'s own list and the lists shared with them (newest share first)."""
    out: List[ShoppingList] = []
    own = own_list_id(user)
    if own is not None:
        lst = _view(user, own, sharing.OWNER)
        if lst is not None:
            out.append(lst)
    for share in sharing.shared_with(user, ITEM_TYPE):
        lst = _view(user, share.item_id, share.level)
        if lst is not None:
            out.append(lst)
    return out


def lists_payload(user: User) -> Dict[str, Any]:
    """``GET /api/shopping/lists``: ``{active, lists: [{id, name, owner: {id, name}, access, active}]}``."""
    active = active_list(user)
    return {"active": active.id,
            "lists": [{"id": lst.id, "name": lst.name, "owner": person(lst.owner_id) or {"id": lst.owner_id, "name": ""},
                       "access": lst.access, "active": lst.id == active.id} for lst in visible_lists(user)]}


def people(lst: ShoppingList) -> List[Dict[str, str]]:
    """Everyone on the list: ``[{id, name, access}]``, the owner first."""
    out = [{"id": lst.owner_id, "name": lst.owner_name, "access": sharing.OWNER}]
    for share in sharing.shares(ITEM_TYPE, lst.id):
        who = users.get(share.user_id)
        if who is not None:
            out.append({"id": who.id, "name": who.name, "access": share.level})
    return out


def pending_invites(user: User) -> List[Dict[str, Any]]:
    """The invitations ``user`` has not answered: lists shared with them that
    are not their active list, ``[{list_id, name, owner: {id, name}, access}]``,
    newest share first."""
    active = active_list(user).id
    with get_db_connection() as conn:
        answered = {r["list_id"] for r in conn.execute("SELECT list_id FROM shopping_invite_answers WHERE user_id = ?",
                                                       (user.id,))}
    out = []
    for share in sharing.shared_with(user, ITEM_TYPE):
        if share.item_id == active or share.item_id in answered:
            continue
        lst = _view(user, share.item_id, share.level)
        if lst is not None:
            out.append({"list_id": lst.id, "name": lst.name, "owner": person(lst.owner_id), "access": lst.access})
    return out
