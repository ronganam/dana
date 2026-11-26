"""The events the ``tasks`` addon emits, for the event catalog (../docs/addons.md,
"Events"): a task created (``service.add_task``) and completed
(``service.complete_task``, the ``complete_task`` tool). Created tasks have a
history from the user's own tasks; completions keep no time, so none."""

import datetime
from typing import List

from core.addons import EventType
from core.addons.hooks import PastEvent
from core.db import get_db_connection
from core.users import User


def created_history(user: User, since: datetime.datetime) -> List[PastEvent]:
    """``user``'s own tasks created since ``since``, oldest first."""
    utc = since.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        rows = conn.execute("SELECT id, title, group_id, created_at FROM tasks WHERE user_id = ? AND created_at >= ? "
                            "ORDER BY created_at", (user.id, utc)).fetchall()
    out: List[PastEvent] = []
    for row in rows:
        try:
            when = datetime.datetime.fromisoformat(str(row["created_at"])).replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
        out.append((when, {"task_id": row["id"], "title": row["title"] or "", "group_id": row["group_id"]}))
    return out


TASK_EVENTS = (
    EventType("tasks.task.created", "A task or reminder was added to the user's lists.",
              {"task_id": "the task's id", "title": "the task's title",
               "group_id": "the id of the list (group) it is in, or null"},
              history=created_history),
    EventType("tasks.task.completed", "A task of the user's was marked done.",
              {"task_id": "the task's id", "title": "the task's title"}),
)
