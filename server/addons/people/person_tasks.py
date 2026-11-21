"""Tasks per person: tasks linked to them (``task_links`` ref ``person``),
tasks linked to their calls, and open tasks whose title names them (whole
words, ≥ 3 letters) — only among the tasks the user can see
(``tasks.service.visible_sql``)."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Sequence

from core.users import User
from addons.people import activity, store
from addons.people.values import norm_name
from addons.tasks import links as task_links, service as tasks_service


def mentions(title: str, keys: List[str]) -> bool:
    t = f" {norm_name(title)} "
    return any(len(k) >= 3 and f" {k} " in t for k in keys)


def open_task_counts(conn: sqlite3.Connection, user: User, people_rows: Sequence[sqlite3.Row]) -> Dict[str, int]:
    """person id → how many of ``user``'s open tasks involve them."""
    where, args = tasks_service.visible_sql(user, "")
    tasks = conn.execute(f"SELECT id, title FROM tasks WHERE status != 'completed' AND {where}", args).fetchall()
    linked = task_links.task_ids_linked_to(conn, "person", [r["id"] for r in people_rows])
    open_ids = {t["id"] for t in tasks}
    out: Dict[str, int] = {}
    for r in people_rows:
        keys = store.names_of(r)
        ids = {t["id"] for t in tasks if mentions(t["title"], keys)} | (linked.get(r["id"], set()) & open_ids)
        if ids:
            out[r["id"]] = len(ids)
    return out


def tasks_for(conn: sqlite3.Connection, user: User, person_id: str, *,
              include_done: bool = False) -> List[Dict[str, Any]]:
    """``user``'s (visible) tasks that involve their person ``person_id``, newest first."""
    r = store.row(conn, user, person_id)
    if not r:
        return []
    keys = store.names_of(r)
    linked = task_links.task_ids_linked_to(conn, "person", [person_id]).get(person_id, set())
    call_ids = activity.call_ids(conn, user, person_id)
    for ids in task_links.task_ids_linked_to(conn, "call", call_ids).values():
        linked |= ids
    where, args = tasks_service.visible_sql(user, "")
    status = "" if include_done else "AND status != 'completed'"
    out = []
    for t in conn.execute(f"SELECT id, title, status, priority, due_date, triggers, updated_at FROM tasks "
                          f"WHERE {where} {status} ORDER BY updated_at DESC", args):
        if t["id"] in linked or mentions(t["title"], keys):
            out.append({"id": t["id"], "title": t["title"], "status": t["status"], "priority": t["priority"],
                        "due_date": t["due_date"], "linked": t["id"] in linked})
    return out[:30]
