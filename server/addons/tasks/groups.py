"""Task groups (the user's task lists): the default set, lookups and changes,
per user, with sharing (docs/access.md, "Sharing"; docs/tasks-and-triggers.md).

A list belongs to the user who created it (``task_groups.user_id``); names
are unique per user. A user sees their own lists plus the lists shared with
them (:mod:`core.addons.sharing`, item type :data:`ITEM_TYPE`); only the owner
renames, deletes or shares a list. Where a name matches both an own list and
a shared one, the own list wins.

Ids stay what they were: the user who owns the ``general`` row (the first
admin, who owned every list before lists were per user) has the bare ids
(``general``, ``home``, a new list ``fitness``); everyone else's ids carry
their user id (``<user id>-general``), so two users never collide and the
first admin's defaults keep their ids.
"""

import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from core import users
from core.db import get_db_connection
from core.users import User
from core.addons import sharing, store

DEFAULT_GROUP_KEY = "general"
ITEM_TYPE = "task_list"

DEFAULT_GROUPS: List[Dict[str, Any]] = [
    {"id": "general", "name": "General", "emoji": "📁", "color": "#6366f1", "description": "General tasks", "sort_order": 0},
    {"id": "home", "name": "Home", "emoji": "🏠", "color": "#10b981", "description": "Home and family tasks", "sort_order": 1},
    {"id": "work", "name": "Work", "emoji": "💼", "color": "#3b82f6", "description": "Work and office tasks", "sort_order": 2},
    {"id": "shopping", "name": "Shopping", "emoji": "🛒", "color": "#f59e0b", "description": "Shopping and errands", "sort_order": 3},
    {"id": "health", "name": "Health", "emoji": "❤️", "color": "#ef4444", "description": "Health and wellness", "sort_order": 4},
    {"id": "personal", "name": "Personal", "emoji": "👤", "color": "#a855f7", "description": "Personal tasks", "sort_order": 5},
]

# The columns of a group in API payloads and tool output, in their order.
PUBLIC_COLUMNS = ("id", "name", "emoji", "color", "description", "sort_order", "created_at", "updated_at")
_SELECT = ", ".join(f"tg.{c}" for c in PUBLIC_COLUMNS)


# --- Ids -----------------------------------------------------------------------------

def _id_prefix(conn: sqlite3.Connection, user: User) -> str:
    row = conn.execute("SELECT user_id FROM task_groups WHERE id = ?", (DEFAULT_GROUP_KEY,)).fetchone()
    return "" if row is None or row["user_id"] == user.id else f"{user.id}-"


def group_id_for(user: User, key: str) -> str:
    """The id ``user``'s list with slug ``key`` has (or gets)."""
    with get_db_connection() as conn:
        return _id_prefix(conn, user) + key


def default_group_id(user: User) -> str:
    """The id of ``user``'s own 'General' list (tasks without a list go there)."""
    return group_id_for(user, DEFAULT_GROUP_KEY)


def ensure_default_groups(user: User) -> None:
    """Give ``user`` any default list they lack (by id; a name they already
    use is left alone). Run when ``tasks`` is connected for them and at every
    start, so a deleted default list comes back on restart."""
    with get_db_connection() as conn:
        prefix = _id_prefix(conn, user)
        for g in DEFAULT_GROUPS:
            conn.execute(
                "INSERT OR IGNORE INTO task_groups (id, user_id, name, emoji, color, description, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (prefix + g["id"], user.id, g["name"], g["emoji"], g["color"], g["description"], g["sort_order"]))
        conn.commit()


def ensure_default_groups_for_everyone() -> None:
    """:func:`ensure_default_groups` for every user with ``tasks`` connected
    (at startup)."""
    for uid in store.users_with("tasks"):
        user = users.get(uid)
        if user is not None:
            ensure_default_groups(user)


# --- Sharing -------------------------------------------------------------------------

def owner_of(group_id: str) -> Optional[str]:
    """The owner's user id of list ``group_id`` (None: no such list)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT user_id FROM task_groups WHERE id = ?", (group_id,)).fetchone()
    return row["user_id"] if row else None


def title_of(group_id: str) -> Optional[str]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT name FROM task_groups WHERE id = ?", (group_id,)).fetchone()
    return row["name"] if row else None


def find_owned(user: User, ref: str) -> Optional[str]:
    """The id of the list ``user`` owns with id or name ``ref`` (name case-insensitive)."""
    ref = (ref or "").strip()
    with get_db_connection() as conn:
        row = conn.execute("SELECT id FROM task_groups WHERE user_id = ? AND (id = ? OR LOWER(name) = LOWER(?)) "
                           "ORDER BY id = ? DESC LIMIT 1", (user.id, ref, ref, ref)).fetchone()
    return row["id"] if row else None


sharing.register(sharing.ItemType(name=ITEM_TYPE, addon="tasks", label="task list", owner_of=owner_of,
                                  find_owned=find_owned, title_of=title_of))


def shared_lists(user: Optional[User]) -> Dict[str, str]:
    """Id → level of the lists shared with ``user``."""
    return sharing.shared_ids(user, ITEM_TYPE)


def access(user: Optional[User], group_id: Optional[str]) -> Optional[str]:
    """``owner`` / ``edit`` / ``read`` / None for ``user`` on list ``group_id``."""
    return sharing.access(user, ITEM_TYPE, group_id)


def _with_share(group: Dict[str, Any], user: User, owner_id: str, level: Optional[str]) -> Dict[str, Any]:
    """A group payload, plus ``shared_by`` / ``access`` when it is shared with ``user``."""
    if owner_id != user.id and level:
        owner = users.get(owner_id)
        group["shared_by"] = owner.name if owner else ""
        group["access"] = level
    return group


# --- Lookups -------------------------------------------------------------------------

def _visible_where(user: User, shared: Dict[str, str], alias: str = "tg") -> Tuple[str, List[Any]]:
    if not shared:
        return f"{alias}.user_id = ?", [user.id]
    return (f"({alias}.user_id = ? OR {alias}.id IN ({','.join('?' * len(shared))}))", [user.id, *shared])


def list_groups(user: User) -> List[Dict[str, Any]]:
    """``user``'s lists and the lists shared with them, with their task
    counts, ordered by sort_order then name (the ``/api/task-groups`` shape)."""
    levels = shared_lists(user)
    where, args = _visible_where(user, levels)
    with get_db_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {_SELECT}, COUNT(t.id) AS task_count, tg.user_id AS _owner
            FROM task_groups tg
            LEFT JOIN tasks t ON t.group_id = tg.id
            WHERE {where}
            GROUP BY tg.id
            ORDER BY tg.sort_order ASC, tg.name ASC
            """, args).fetchall()
    out = []
    for r in rows:
        g = {k: r[k] for k in (*PUBLIC_COLUMNS, "task_count")}
        out.append(_with_share(g, user, r["_owner"], levels.get(r["id"])))
    return out


def get_group(user: User, group_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """List ``group_id`` if ``user`` can see it (plus ``shared_by`` / ``access`` when shared)."""
    if not group_id:
        return None
    level = access(user, group_id)
    if level is None:
        return None
    with get_db_connection() as conn:
        r = conn.execute(f"SELECT {_SELECT}, tg.user_id AS _owner FROM task_groups tg WHERE tg.id = ?",
                         (group_id,)).fetchone()
    if r is None:
        return None
    return _with_share({k: r[k] for k in PUBLIC_COLUMNS}, user, r["_owner"],
                       None if level == sharing.OWNER else level)


def find_group(user: User, name: str) -> Optional[Dict[str, Any]]:
    """The list called ``name`` (case-insensitive) that ``user`` can see: their own first."""
    where, args = _visible_where(user, shared_lists(user))
    with get_db_connection() as conn:
        r = conn.execute(f"SELECT tg.id FROM task_groups tg WHERE {where} AND LOWER(tg.name) = LOWER(?) "
                         "ORDER BY tg.user_id = ? DESC, tg.sort_order LIMIT 1",
                         (*args, (name or "").strip(), user.id)).fetchone()
    return get_group(user, r["id"]) if r else None


def group_names(user: Optional[User]) -> List[str]:
    """Names of the lists ``user`` can add tasks to (own, and shared with
    them with ``edit``), by sort order (for prompts)."""
    if user is None:
        return []
    shared = {gid: lvl for gid, lvl in shared_lists(user).items() if lvl == sharing.EDIT}
    where, args = _visible_where(user, shared)
    with get_db_connection() as conn:
        return [r["name"] for r in conn.execute(
            f"SELECT tg.name FROM task_groups tg WHERE {where} ORDER BY tg.sort_order", args).fetchall()]


# --- Changes -------------------------------------------------------------------------

def create_group(user: User, name: str, emoji: str = "📁", color: str = "#6366f1", description: str = "",
                 sort_order: int = 99) -> str:
    """Add a list owned by ``user``. Returns its id.

    Raises:
        ValueError: empty name.
        sqlite3.IntegrityError: ``user`` already has a list by that name (or id).
    """
    slug = name.strip().lower().replace(" ", "_")
    if not slug:
        raise ValueError("Group name cannot be empty.")
    with get_db_connection() as conn:
        group_id = _id_prefix(conn, user) + slug
        conn.execute("INSERT INTO task_groups (id, user_id, name, emoji, color, description, sort_order) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (group_id, user.id, name.strip(), emoji.strip(), color.strip(), description.strip(), sort_order))
        conn.commit()
    return group_id


def update_group(user: User, group_id: str, changes: Dict[str, Any]) -> bool:
    """Change ``name`` / ``emoji`` / ``color`` / ``description`` of a list
    ``user`` owns. Returns False when they cannot see it.

    Raises:
        sharing.NotOwner: it is shared with them, not theirs.
    """
    level = access(user, group_id)
    if level is None:
        return False
    if level != sharing.OWNER:
        raise sharing.NotOwner("Only the owner of the list can change it.")
    cols = [c for c in ("name", "emoji", "color", "description") if c in changes]
    if cols:
        with get_db_connection() as conn:
            conn.execute(f"UPDATE task_groups SET {', '.join(c + ' = ?' for c in cols)}, "
                         "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (*[changes[c] for c in cols], group_id))
            conn.commit()
    return True


def delete_group(user: User, group_id: str) -> bool:
    """Delete a list ``user`` owns; its tasks move to the owner's 'General'
    list and every share of it ends. Returns False when they cannot see it.

    Raises:
        sharing.NotOwner: it is shared with them, not theirs.
        ValueError: it is the default 'General' list.
    """
    level = access(user, group_id)
    if level is None:
        return False
    if level != sharing.OWNER:
        raise sharing.NotOwner("Only the owner of the list can delete it.")
    general = default_group_id(user)
    if group_id == general:
        raise ValueError("Cannot delete the default General group")
    with get_db_connection() as conn:
        conn.execute("UPDATE tasks SET group_id = ?, user_id = ? WHERE group_id = ?", (general, user.id, group_id))
        conn.execute("DELETE FROM task_groups WHERE id = ?", (group_id,))
        conn.commit()
    sharing.forget(ITEM_TYPE, group_id)
    return True
