"""Projects for a user: what they can see and change, with sharing
(docs/access.md, "Sharing").

A project belongs to the user who created it (``projects.user_id``). A user
sees their own projects plus the projects shared with them (:mod:`core.addons.sharing`,
item type :data:`ITEM_TYPE`), and nothing without ``knowledge`` connected.
Only the owner renames / changes, deletes or shares a project
(:class:`core.addons.sharing.NotOwner`); the entries inside follow the project
(:mod:`.entries`): ``edit`` may add and change them, ``read`` refuses writes
(:class:`ReadOnly`). A project the user cannot see answers as if it did not
exist. Titles are not unique; where a title matches an own project and a
shared one, the own one wins (:func:`find_owned` only looks at own projects).

Tasks that carry a ``project_id`` stay governed by their own list: a project
shows only the tasks the user can see through ``tasks`` (a project never
grants access to a task, nor a task to a project).

Payloads never carry the owner column; a project seen through a share carries
``shared_by`` / ``access``.
"""

import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from core import user_locale, users
from core.db import get_db_connection
from core.users import User
from core.addons import sharing, store
from addons.knowledge.values import as_number, clean_str, clean_tags, loads_json, serialize_project, with_share
from addons.tasks import service as tasks_service

ADDON = "knowledge"
ITEM_TYPE = "project"
# Columns a person edits (undo compares and restores these).
PROJECT_COLUMNS = ("title", "description", "status", "icon", "color", "tags", "target_date", "budget")


class ReadOnly(PermissionError):
    """The project is shared with the user read-only."""

    def __init__(self) -> None:
        super().__init__("This project is shared with you read-only, so nothing in it can be changed.")


def _payload(row: Any) -> Dict[str, Any]:
    """A project row as the API shows it, plus ``currency``: the owner's
    (core/user_locale.py), what its budget and prices are in, also for a share."""
    return {**serialize_project(row), "currency": user_locale.currency(users.get(row["user_id"]))}


# --- Sharing -------------------------------------------------------------------------

def owner_of(project_id: str) -> Optional[str]:
    """The owner's user id of project ``project_id`` (None: no such project)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT user_id FROM projects WHERE id = ?", (project_id,)).fetchone()
    return row["user_id"] if row else None


def title_of(project_id: str) -> Optional[str]:
    with get_db_connection() as conn:
        # scoped: title of an item the caller resolved for the user (sharing ItemType.title_of)
        row = conn.execute("SELECT title FROM projects WHERE id = ?", (project_id,)).fetchone()
    return row["title"] if row else None


def find_owned(user: User, ref: str) -> Optional[str]:
    """The id of the project ``user`` owns with id or title ``ref`` (title
    case-insensitive; an active project wins over an archived one)."""
    ref = (ref or "").strip()
    with get_db_connection() as conn:
        row = conn.execute("SELECT id FROM projects WHERE user_id = ? AND (id = ? OR LOWER(title) = LOWER(?)) "
                           "ORDER BY id = ? DESC, status = 'active' DESC, created_at DESC LIMIT 1",
                           (user.id, ref, ref, ref)).fetchone()
    return row["id"] if row else None


sharing.register(sharing.ItemType(name=ITEM_TYPE, addon=ADDON, label="project", owner_of=owner_of,
                                  find_owned=find_owned, title_of=title_of))


# --- Scope ---------------------------------------------------------------------------

def has_knowledge(user: Optional[User]) -> bool:
    return user is not None and store.is_connected(user, ADDON)


def shared_projects(user: Optional[User]) -> Dict[str, str]:
    """Id → level of the projects shared with ``user``."""
    return sharing.shared_ids(user, ITEM_TYPE)


def visible_sql(user: Optional[User], alias: str = "p") -> Tuple[str, List[Any]]:
    """A WHERE condition (and its parameters) for the projects ``user`` can
    see: their own plus those shared with them; nothing without a user or
    without ``knowledge``. ``alias`` is the projects table's alias (``""`` for none)."""
    if not has_knowledge(user):
        return "0", []
    assert user is not None
    col = f"{alias}." if alias else ""
    shared = list(shared_projects(user))
    if not shared:
        return f"{col}user_id = ?", [user.id]
    return f"({col}user_id = ? OR {col}id IN ({','.join('?' * len(shared))}))", [user.id, *shared]


def access(user: Optional[User], project_id: Optional[str]) -> Optional[str]:
    """``owner`` / ``edit`` / ``read`` / None for ``user`` on project ``project_id``."""
    return sharing.access(user, ITEM_TYPE, project_id)


def share_note(user: User, owner_id: str, level: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """(``shared_by``, ``access``) for something of ``owner_id``'s that ``user``
    sees with ``level``; (None, None) when it is their own."""
    if owner_id == user.id or level not in (sharing.READ, sharing.EDIT):
        return None, None
    owner = users.get(owner_id)
    return (owner.name if owner else ""), level


def writable_project(user: Optional[User], project_id: Optional[str]) -> str:
    """The owner's id of a project ``user`` may put things into.

    Raises:
        LookupError: they cannot see it (or it does not exist).
        ReadOnly: it is shared with them read-only.
    """
    level = access(user, project_id)
    if level is None:
        raise LookupError("Project not found")
    if level == sharing.READ:
        raise ReadOnly()
    owner = owner_of(project_id or "")
    assert owner is not None
    return owner


def _owned(user: Optional[User], project_id: Optional[str], action: str) -> bool:
    """True when ``user`` owns the project; False when they cannot see it.

    Raises:
        sharing.NotOwner: it is shared with them, not theirs.
    """
    level = access(user, project_id)
    if level is None:
        return False
    if level != sharing.OWNER:
        raise sharing.NotOwner(f"Only the owner of the project can {action} it.")
    return True


# --- Lookups -------------------------------------------------------------------------

def list_projects(user: User, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """The projects ``user`` can see (optionally one status), newest first,
    with their entry count and the count of tasks with that project that
    ``user`` can see (the ``GET /api/projects`` payload)."""
    where, args = visible_sql(user)
    task_where, task_args = tasks_service.visible_sql(user)
    if status:
        where += " AND p.status = ?"
        args = [*args, status]
    levels = shared_projects(user)
    with get_db_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT p.*,
                   (SELECT COUNT(*) FROM entries e WHERE e.project_id = p.id) AS entry_count,
                   (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND {task_where}) AS task_count
            FROM projects p
            WHERE {where}
            ORDER BY p.created_at DESC
            """,
            (*task_args, *args),
        ).fetchall()
        stats = _stats(conn, [r["id"] for r in rows])
    return [with_share({**_payload(r), **stats.get(r["id"], _EMPTY_STATS)},
                       *share_note(user, r["user_id"], levels.get(r["id"]))) for r in rows]


_EMPTY_STATS = {"section_count": 0, "decisions": 0, "decided": 0, "committed": 0}


def _stats(conn: Any, project_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Per project: ``section_count``; ``decisions`` (options sections) and
    ``decided`` (those with a chosen option); ``committed`` — the sum of the
    chosen options' price-column values (columns with role "price")."""
    if not project_ids:
        return {}
    marks = ",".join("?" * len(project_ids))
    out: Dict[str, Dict[str, Any]] = {pid: dict(_EMPTY_STATS) for pid in project_ids}
    sections = conn.execute(f"SELECT id, project_id, kind, columns FROM project_sections WHERE project_id IN ({marks})",
                            project_ids).fetchall()
    price_cols: Dict[str, List[str]] = {}
    for s in sections:
        stats = out[s["project_id"]]
        stats["section_count"] += 1
        if s["kind"] == "options":
            stats["decisions"] += 1
            price_cols[s["id"]] = [c.get("name") for c in loads_json(s["columns"], [])
                                   if isinstance(c, dict) and c.get("role") == "price"]
    if price_cols:
        # scoped: sections of the projects list_projects found for the user
        chosen = conn.execute(f"SELECT project_id, section_id, fields FROM entries WHERE decision = 'chosen' "
                              f"AND section_id IN ({','.join('?' * len(price_cols))})", list(price_cols)).fetchall()
        decided: Set[str] = set()
        for e in chosen:
            decided.add(e["section_id"])
            for field in loads_json(e["fields"], []):
                if isinstance(field, dict) and field.get("name") in price_cols[e["section_id"]]:
                    out[e["project_id"]]["committed"] += as_number(field.get("value")) or 0
        for s in sections:
            if s["id"] in decided:
                out[s["project_id"]]["decided"] += 1
    return out


def get_project(user: Optional[User], project_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Project ``project_id`` if ``user`` can see it (plus ``shared_by`` /
    ``access`` when it is shared with them)."""
    level = access(user, project_id)
    if level is None or user is None:
        return None
    with get_db_connection() as conn:
        # scoped: access(user, project_id) checked above
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        return None
    return with_share(_payload(row), *share_note(user, row["user_id"], level))


def active_projects(user: Optional[User]) -> List[Dict[str, str]]:
    """``[{id, title}]`` of the active projects ``user`` can see (for prompts)."""
    where, args = visible_sql(user)
    with get_db_connection() as conn:
        rows = conn.execute(f"SELECT p.id, p.title FROM projects p WHERE p.status = 'active' AND {where}",
                            args).fetchall()
    return [{"id": r["id"], "title": r["title"]} for r in rows]


def project_tasks(user: User, project_id: str) -> List[Dict[str, Any]]:
    """Tasks with ``project_id`` that ``user`` can see through ``tasks``, open
    first, newest first (API shape: no owner columns, ``shared_by`` /
    ``access`` for tasks of a shared list)."""
    where, args = tasks_service.visible_sql(user, "")
    with get_db_connection() as conn:
        rows = conn.execute(f"SELECT * FROM tasks WHERE project_id = ? AND {where} "
                            "ORDER BY status ASC, created_at DESC", (project_id, *args)).fetchall()
    return [tasks_service.public_task(user, dict(r)) for r in rows]


def search_titles(user: Optional[User], like: str, limit: int = 10) -> List[Dict[str, Any]]:
    """``[{id, title}]`` of the non-archived projects ``user`` can see whose
    title matches the LIKE pattern ``like`` (``\\`` escapes)."""
    where, args = visible_sql(user)
    with get_db_connection() as conn:
        rows = conn.execute(f"SELECT p.id, p.title FROM projects p WHERE p.status != 'archived' "
                            f"AND p.title LIKE ? ESCAPE '\\' AND {where} LIMIT ?", (like, *args, limit)).fetchall()
    return [{"id": r["id"], "title": r["title"]} for r in rows]


# --- Changes -------------------------------------------------------------------------

def create_project(user: User, title: str, description: str = "", icon: str = "📁", color: str = "#6366f1",
                   tags: Any = None, target_date: Optional[str] = None, budget: Any = None) -> str:
    """Create a project owned by ``user`` and return its ID."""
    project_id = str(uuid.uuid4())
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO projects (id, title, description, icon, color, tags, user_id, target_date, budget)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                clean_str(title),
                clean_str(description),
                clean_str(icon, "📁") or "📁",
                clean_str(color, "#6366f1") or "#6366f1",
                clean_tags(tags),
                user.id,
                clean_str(target_date) or None,
                as_number(budget),
            ),
        )
        conn.commit()
    return project_id


def update_project(user: User, project_id: str, *, title: Optional[str] = None, description: Optional[str] = None,
                   status: Optional[str] = None, icon: Optional[str] = None, color: Optional[str] = None,
                   tags: Any = None, target_date: Optional[str] = None,
                   budget: Any = None) -> Optional[Dict[str, Any]]:
    """Partially update a project ``user`` owns (None: leave as is; "" clears
    the date / budget). Returns the updated project, or None when they cannot see it.

    Raises:
        sharing.NotOwner: it is shared with them, not theirs.
    """
    if not _owned(user, project_id, "change"):
        return None
    with get_db_connection() as conn:
        # scoped: the user owns it (_owned, above)
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            return None

        sets, args = [], []
        if title is not None and clean_str(title):
            sets.append("title = ?"); args.append(clean_str(title))
        if description is not None:
            sets.append("description = ?"); args.append(clean_str(description))
        if status is not None:
            new_status = clean_str(status).lower()
            sets.append("status = ?"); args.append(new_status if new_status in ("active", "archived") else row["status"])
        if icon is not None:
            sets.append("icon = ?"); args.append(clean_str(icon) or row["icon"])
        if color is not None:
            sets.append("color = ?"); args.append(clean_str(color) or row["color"])
        if tags is not None:
            sets.append("tags = ?"); args.append(clean_tags(tags))
        if target_date is not None:
            sets.append("target_date = ?"); args.append(clean_str(target_date) or None)
        if budget is not None:
            sets.append("budget = ?"); args.append(as_number(budget))
        if sets:
            sets.append("updated_at = CURRENT_TIMESTAMP")
            # scoped: the user owns it (_owned, above)
            conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", (*args, project_id))
            conn.commit()

        # scoped: the user owns it (_owned, above)
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return _payload(row)


def delete_project(user: User, project_id: str) -> bool:
    """Delete a project ``user`` owns along with its entries and the owner's
    tasks that carry it (another user's task only loses the project), and
    end its shares. Returns False when they cannot see it.

    Raises:
        sharing.NotOwner: it is shared with them, not theirs.
    """
    from addons.knowledge import files  # files → entries → projects
    if not _owned(user, project_id, "delete"):
        return False
    with get_db_connection() as conn:
        # scoped: the user owns the project (_owned, above): its entries go with it
        files.forget_entries(conn, [r["id"] for r in conn.execute("SELECT id FROM entries WHERE project_id = ?",
                                                                   (project_id,))])
        conn.execute("DELETE FROM tasks WHERE project_id = ? AND user_id = ?", (project_id, user.id))
        # scoped: the user owns the project (_owned, above): others' tasks only lose the link
        conn.execute("UPDATE tasks SET project_id = NULL WHERE project_id = ?", (project_id,))
        # scoped: the user owns the project (_owned, above): its entries go with it
        conn.execute("DELETE FROM entries WHERE project_id = ?", (project_id,))
        # scoped: the user owns the project (_owned, above)
        rows = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,)).rowcount
        conn.commit()
    sharing.forget(ITEM_TYPE, project_id)
    return rows > 0
