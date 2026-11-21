"""Task links: where a task came from or what it is about (a call, a
notification, a person, …), table ``task_links`` (docs/calls.md → Task links).

Links belong to the task: whoever can see the task sees its links, and the
callers check access to the task before linking (``service.writable_task``).
Other code links *to* tasks only through this module, and removes the links
of a thing it deletes with :func:`forget_ref` / :func:`move_ref`.
"""

import sqlite3
import uuid
from typing import Any, Dict, List, Optional, Sequence

from core.db import get_db_connection
from core.users import User


def link_task(task_id: str, ref_type: str, ref_id: str, role: str = "origin",
              conn: Optional[sqlite3.Connection] = None) -> None:
    """Link a task to ``(ref_type, ref_id)`` (a no-op when already linked).
    With ``conn`` it runs in the caller's transaction."""
    sql = "INSERT OR IGNORE INTO task_links (id, task_id, ref_type, ref_id, role) VALUES (?, ?, ?, ?, ?)"
    args = (str(uuid.uuid4()), task_id, ref_type, ref_id, role)
    if conn is not None:
        conn.execute(sql, args)
        return
    with get_db_connection() as c:
        c.execute(sql, args)
        c.commit()


def unlink_task(task_id: str, ref_type: str, ref_id: str) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM task_links WHERE task_id = ? AND ref_type = ? AND ref_id = ?",
                     (task_id, ref_type, ref_id))
        conn.commit()


def is_linked(task_id: str, ref_type: str, ref_id: str) -> bool:
    with get_db_connection() as conn:
        return conn.execute("SELECT 1 FROM task_links WHERE task_id = ? AND ref_type = ? AND ref_id = ?",
                            (task_id, ref_type, ref_id)).fetchone() is not None


def forget_ref(ref_type: str, ref_id: str, conn: Optional[sqlite3.Connection] = None) -> None:
    """Remove every link to a thing that is being deleted (a call, a person).
    With ``conn`` it runs in the caller's transaction."""
    sql, args = "DELETE FROM task_links WHERE ref_type = ? AND ref_id = ?", (ref_type, ref_id)
    if conn is not None:
        conn.execute(sql, args)
        return
    with get_db_connection() as c:
        c.execute(sql, args)
        c.commit()


def move_ref(ref_type: str, old_id: str, new_id: str, conn: sqlite3.Connection) -> None:
    """Point the links of ``old_id`` at ``new_id`` (people merged), in the
    caller's transaction; a task linked to both keeps one link."""
    conn.execute("UPDATE OR IGNORE task_links SET ref_id = ? WHERE ref_type = ? AND ref_id = ?",
                 (new_id, ref_type, old_id))
    conn.execute("DELETE FROM task_links WHERE ref_type = ? AND ref_id = ?", (ref_type, old_id))


def task_ids_linked_to(conn: sqlite3.Connection, ref_type: str, ref_ids: Sequence[str]) -> Dict[str, set]:
    """ref id → ids of the tasks linked to it (any task: callers intersect
    with the tasks the user can see)."""
    out: Dict[str, set] = {}
    if not ref_ids:
        return out
    q = ",".join("?" * len(ref_ids))
    for r in conn.execute(f"SELECT task_id, ref_id FROM task_links WHERE ref_type = ? AND ref_id IN ({q})",
                          (ref_type, *ref_ids)):
        out.setdefault(r["ref_id"], set()).add(r["task_id"])
    return out


def links_for_tasks(user: Optional[User], task_ids: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    """task_id → [{ref_type, ref_id, role, title, …}] for the given tasks, as
    ``user`` sees them (call / notification / person refs resolved; links to
    deleted things left out). A call, notification or person link shows only
    to the owner of that call / notification / person: on a shared list,
    someone else's link to one of their own is left out."""
    wanted = set(task_ids)
    if not wanted:
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    uid = user.id if user is not None else ""
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT l.task_id, l.ref_type, l.ref_id, l.role, l.created_at,
                   c.title AS call_title, c.caller, c.receiver, c.call_timestamp,
                   n.app_name AS notif_app, n.title AS notif_title, n.text AS notif_text, n.posted_at AS notif_posted_at,
                   p.name AS person_name
            FROM task_links l
            LEFT JOIN phone_calls c ON l.ref_type = 'call' AND c.id = l.ref_id AND c.user_id = ?
            LEFT JOIN notifications n ON l.ref_type = 'notification' AND n.id = l.ref_id AND n.user_id = ?
            LEFT JOIN people p ON l.ref_type = 'person' AND p.id = l.ref_id AND p.user_id = ?
            ORDER BY l.created_at
            """, (uid, uid, uid)
        ).fetchall()
    for r in rows:
        if r["task_id"] not in wanted:
            continue
        link = {"ref_type": r["ref_type"], "ref_id": r["ref_id"], "role": r["role"]}
        if r["ref_type"] == "call":
            if r["call_title"] is None and r["caller"] is None:
                continue  # call was deleted, or is not the user's
            contact = r["receiver"] if r["caller"] == "Me" else r["caller"]
            link.update({"title": r["call_title"], "contact": contact, "call_timestamp": r["call_timestamp"]})
        elif r["ref_type"] == "notification":
            if r["notif_app"] is None:
                continue  # notification was deleted, or is not the user's
            link.update({"title": r["notif_title"] or r["notif_text"], "app_name": r["notif_app"],
                         "posted_at": r["notif_posted_at"]})
        elif r["ref_type"] == "person":
            if r["person_name"] is None:
                continue  # person was deleted, or is not the user's
            link.update({"title": r["person_name"]})
        out.setdefault(r["task_id"], []).append(link)
    return out
