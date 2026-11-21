"""Tasks for a user: what they can see and change, and the reads and writes
the tools, the HTTP handlers and other services share (docs/tasks-and-triggers.md).

A task belongs to the owner of its list (``tasks.user_id``) and records who
created it (``tasks.created_by``: reminders go to them). A user sees their own
tasks plus every task in a list shared with them (:mod:`.groups`); a ``read``
share refuses writes (:class:`ReadOnly`). A task the user cannot see answers
as if it did not exist. A user who does not have ``tasks`` connected sees no
task at all (:func:`visible_sql`); HTTP handlers check that first with
``store.require`` (403 ``not_connected``).

Payloads never carry the owner columns; a task seen through a share carries
``shared_by`` / ``access`` (:func:`public_task`).
"""

import logging
import sqlite3
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core import undoable, users
from core.db import get_db_connection
from core.undoable import UndoConflict
from core.users import User
from integrations.framework import sharing, store
from integrations.tasks import groups, links

logger = logging.getLogger(__name__)

KIND = "tasks"
# Columns a person edits; an undo only goes ahead while these still hold what
# the tool wrote (so it never overwrites a newer change).
TASK_COLUMNS = ("title", "description", "priority", "due_date", "status", "triggers", "group_id")
# Columns that never leave the server.
OWNER_COLUMNS = ("user_id", "created_by")
# An undo refused because the user lost write access.
NO_LONGER = "You can no longer change this task, so it was left as it is."


class ReadOnly(PermissionError):
    """The task (or list) is shared with the user read-only."""

    def __init__(self, what: str = "task") -> None:
        super().__init__(f"This {what} is shared with you read-only, so it can't be changed.")


class NoHomeAssistant(ValueError):
    """A location trigger needs the user's Home Assistant (its zones)."""

    def __init__(self) -> None:
        super().__init__("Location reminders use Home Assistant zones, and Home Assistant isn't connected for you. "
                         "Use a time, car or wake-up reminder instead.")


# --- Scope -----------------------------------------------------------------------------

def has_tasks(user: Optional[User]) -> bool:
    return user is not None and store.is_connected(user, KIND)


def visible_sql(user: Optional[User], alias: str = "t") -> Tuple[str, List[Any]]:
    """A WHERE condition (and its parameters) for the tasks ``user`` can see:
    their own plus those in lists shared with them; nothing without a user or
    without ``tasks``. ``alias`` is the tasks table's alias in the query
    (``""`` for none)."""
    if not has_tasks(user):
        return "0", []
    assert user is not None
    col = f"{alias}." if alias else ""
    shared = list(groups.shared_lists(user))
    if not shared:
        return f"{col}user_id = ?", [user.id]
    return f"({col}user_id = ? OR {col}group_id IN ({','.join('?' * len(shared))}))", [user.id, *shared]


def level_of(user: Optional[User], task: Dict[str, Any]) -> Optional[str]:
    """``owner`` / ``edit`` / ``read`` / None for ``user`` on a task row."""
    if not has_tasks(user):
        return None
    assert user is not None
    if task.get("user_id") == user.id:
        return sharing.OWNER
    return groups.shared_lists(user).get(task.get("group_id") or "")


def get_task(user: Optional[User], task_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The task row (all columns) if ``user`` can see it."""
    if not task_id:
        return None
    where, args = visible_sql(user, "")
    with get_db_connection() as conn:
        row = conn.execute(f"SELECT * FROM tasks WHERE id = ? AND {where}", (task_id, *args)).fetchone()
    return dict(row) if row else None


def writable_task(user: Optional[User], task_id: Optional[str]) -> Dict[str, Any]:
    """The task row if ``user`` may change it.

    Raises:
        LookupError: they cannot see it (or it does not exist).
        ReadOnly: it is in a list shared with them read-only.
    """
    task = get_task(user, task_id)
    if task is None:
        raise LookupError("Task not found")
    if level_of(user, task) == sharing.READ:
        raise ReadOnly()
    return task


def check_still_writable(user: Optional[User], task_id: str) -> None:
    """For an undo, right before it changes or removes a task: ``user`` may
    still change it (its list may have been unshared or made read-only since
    the action). A task that is gone passes (the undo decides what that means).

    Raises:
        UndoConflict: they can no longer change it.
    """
    try:
        writable_task(user, task_id)
    except ReadOnly:
        raise UndoConflict(NO_LONGER) from None
    except LookupError:
        with get_db_connection() as db:
            if db.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
                raise UndoConflict(NO_LONGER) from None


def find_task(user: Optional[User], ref: str, uuid_like: bool) -> Optional[Dict[str, Any]]:
    """A task by id (``uuid_like``) or, among open tasks, by exact then partial
    title; the user's own task wins a tie (the tools' lookup)."""
    where, args = visible_sql(user, "")
    own = user.id if user else ""
    with get_db_connection() as db:
        if uuid_like:
            row = db.execute(f"SELECT * FROM tasks WHERE id = ? AND {where}", (ref, *args)).fetchone()
        else:
            row = db.execute(f"SELECT * FROM tasks WHERE LOWER(title) = ? AND status = 'needs_action' AND {where} "
                             "ORDER BY user_id = ? DESC", (ref.lower().strip(), *args, own)).fetchone()
            if not row:
                row = db.execute(f"SELECT * FROM tasks WHERE title LIKE ? AND status = 'needs_action' AND {where} "
                                 "ORDER BY user_id = ? DESC", (f"%{ref.strip()}%", *args, own)).fetchone()
    return dict(row) if row else None


def public_task(user: User, task: Dict[str, Any]) -> Dict[str, Any]:
    """A task row as API payloads show it: no owner columns; ``shared_by`` /
    ``access`` when it is seen through a share."""
    out = {k: v for k, v in task.items() if k not in OWNER_COLUMNS}
    level = level_of(user, task)
    if level in (sharing.READ, sharing.EDIT):
        owner = users.get(task.get("user_id") or "")
        out["shared_by"] = owner.name if owner else ""
        out["access"] = level
    return out


def list_tasks(user: User) -> List[Dict[str, Any]]:
    """Every task ``user`` can see with its list's name / emoji / color and
    its links (the ``GET /api/tasks`` payload), open first, newest first."""
    where, args = visible_sql(user)
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            f"""
            SELECT t.*, tg.name AS group_name, tg.emoji AS group_emoji, tg.color AS group_color
            FROM tasks t
            LEFT JOIN task_groups tg ON t.group_id = tg.id
            WHERE {where}
            ORDER BY t.status ASC, t.created_at DESC
            """, args).fetchall()]
    all_links = links.links_for_tasks(user, [t["id"] for t in rows])
    out = []
    for t in rows:
        item = public_task(user, t)
        item["links"] = all_links.get(t["id"], [])
        out.append(item)
    return out


def tasks_for_call(user: Optional[User], call_id: str) -> List[Dict[str, Any]]:
    """Tasks linked to a call that ``user`` can see."""
    where, args = visible_sql(user)
    with get_db_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT t.id, t.title, t.status, t.priority, t.due_date, t.triggers, l.role
            FROM task_links l JOIN tasks t ON t.id = l.task_id
            WHERE l.ref_type = 'call' AND l.ref_id = ? AND {where}
            ORDER BY t.created_at
            """, (call_id, *args)).fetchall()
    return [dict(r) for r in rows]


# --- Writes ------------------------------------------------------------------------------

def _target_group(user: User, group_id: Optional[str]) -> Tuple[str, str]:
    """(group id, its owner) for a task ``user`` puts in list ``group_id``
    (their 'General' list when None).

    Raises:
        LookupError: they cannot see the list.
        ReadOnly: the list is shared with them read-only.
    """
    group_id = group_id or groups.default_group_id(user)
    level = groups.access(user, group_id)
    if level is None:
        raise LookupError("Task group not found")
    if level == sharing.READ:
        raise ReadOnly("list")
    owner = groups.owner_of(group_id)
    assert owner is not None
    return group_id, owner


def create_task(user: User, *, title: str, description: Optional[str] = None, priority: str = "medium",
                due_date: Optional[str] = None, triggers: str = "{}",
                group_id: Optional[str] = None, task_links: Sequence[Tuple[str, str, str]] = (),
                conn: Optional[sqlite3.Connection] = None) -> str:
    """Insert an open task created by ``user`` in list ``group_id`` (their
    'General' list when None), ``triggers`` already as JSON text, and link it (``(ref_type, ref_id, role)`` each).
    The task belongs to the list's owner. With ``conn`` it runs in the
    caller's transaction (the caller commits). Returns the new id.

    Raises:
        LookupError: the list is not one ``user`` can see.
        ReadOnly: the list is shared with them read-only.
    """
    gid, owner = _target_group(user, group_id)
    task_id = str(uuid.uuid4())
    db = conn or get_db_connection()
    try:
        db.execute(
            """
            INSERT INTO tasks (id, title, description, priority, due_date, status, triggers, group_id, created_at,
                               updated_at, user_id, created_by)
            VALUES (?, ?, ?, ?, ?, 'needs_action', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?)
            """,
            (task_id, title, description, priority, due_date, triggers, gid,
             owner, user.id))
        for ref_type, ref_id, role in task_links:
            links.link_task(task_id, ref_type, ref_id, role, conn=db)
        if conn is None:
            db.commit()
    finally:
        if conn is None:
            db.close()
    return task_id


def save_task(user: User, task_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Write ``fields`` (columns of :data:`TASK_COLUMNS`, ``snooze_count``) to
    a task ``user`` may change; moving it to another list (``group_id``) needs
    edit on that list and gives the task to that list's owner. Returns the
    row as it was before.

    Raises:
        LookupError: the task (or the target list) is not one they can see.
        ReadOnly: the task or the target list is shared with them read-only.
    """
    before = writable_task(user, task_id)
    sets = dict(fields)
    if "group_id" in sets and sets["group_id"] != before.get("group_id"):
        sets["group_id"], sets["user_id"] = _target_group(user, sets["group_id"])
    if not sets:
        return before
    with get_db_connection() as conn:
        conn.execute(f"UPDATE tasks SET {', '.join(c + ' = ?' for c in sets)}, updated_at = CURRENT_TIMESTAMP "
                     "WHERE id = ?", (*sets.values(), task_id))
        conn.commit()
    return before


def restore_task(user: User, task_id: str, before: Dict[str, Any], after: Dict[str, Any],
                 columns: Sequence[str] = TASK_COLUMNS) -> None:
    """Undo of a change ``user`` made to a task: put ``columns`` back to
    ``before`` while they still hold ``after`` — through :func:`save_task`, so
    it is checked like a new edit: write access on the list the task is in
    now and, when it goes back to another list, on that list too; the owner
    follows the list it goes back to.

    Raises:
        UndoConflict: it was deleted, changed again, or ``user`` can no longer
            change it where it is or where it would go back to.
    """
    changed = [c for c in columns if undoable.norm(before.get(c)) != undoable.norm(after.get(c))] or list(columns)
    try:
        current = writable_task(user, task_id)
    except ReadOnly:
        raise UndoConflict(NO_LONGER) from None
    except LookupError:
        with get_db_connection() as db:
            if db.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
                raise UndoConflict(NO_LONGER) from None
        raise UndoConflict("It was deleted in the meantime, so there is nothing to restore.") from None
    if not undoable.same(current, after, changed):
        raise UndoConflict("It was changed again since, so it was left as it is.")
    try:
        save_task(user, task_id, {c: before.get(c) for c in changed})
    except (LookupError, ReadOnly):
        raise UndoConflict(NO_LONGER) from None


def record_change_undo(user: User, label: str, task_id: str, before: Dict[str, Any], after: Dict[str, Any],
                       source: str) -> Optional[str]:
    """Register the undo of a change ``user`` just made to a task
    (:func:`restore_task`). Returns the undo id (None if it could not be registered)."""
    return undoable.safe_record(label, lambda: restore_task(user, task_id, before, after), source)


def delete_task(user: User, task_id: str) -> Dict[str, Any]:
    """Delete a task ``user`` may change, with its links. Returns the deleted row.

    Raises:
        LookupError / ReadOnly: as :func:`writable_task`.
    """
    task = writable_task(user, task_id)
    remove_task_rows(task_id)
    return task


def remove_task_rows(task_id: str) -> None:
    """Delete a task and its links, no questions asked: for undoing a task the
    caller itself just created (access was checked when it was created)."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.execute("DELETE FROM task_links WHERE task_id = ?", (task_id,))
        conn.commit()


def complete_task(user: User, task_id: str) -> Dict[str, Any]:
    """Mark a task done. Returns the row as it was before.

    Raises:
        LookupError / ReadOnly: as :func:`writable_task`.
    """
    return save_task(user, task_id, {"status": "completed"})


def snooze_task(user: User, task_id: str, triggers: str) -> Dict[str, Any]:
    """Replace the triggers (JSON text), keep it open and count the snooze
    (``POST /api/tasks/{id}/snooze``, Now). Returns the row as it was before.

    Raises:
        LookupError / ReadOnly: as :func:`writable_task`.
    """
    before = writable_task(user, task_id)
    with get_db_connection() as conn:
        conn.execute("UPDATE tasks SET triggers = ?, status = 'needs_action', "
                     "snooze_count = COALESCE(snooze_count, 0) + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (triggers, task_id))
        conn.commit()
    return before


# --- Location triggers ------------------------------------------------------------------

def check_location_trigger(user: User) -> None:
    """A location trigger needs ``user``'s Home Assistant (its zones).

    Raises:
        NoHomeAssistant: they do not have it connected.
    """
    if not store.is_connected(user, "homeassistant"):
        raise NoHomeAssistant()
