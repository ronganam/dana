"""Undo registry: reversible actions run at once and say how to reverse them.

Instead of asking "Approve?" before a reversible change (complete a task, add a
calendar event, add to the shopping list, dismiss a notification), the code
makes the change and calls :func:`record` with a function that reverses it.
The client gets the returned id and shows an Undo button; pressing it calls
``POST /api/undo/{id}`` (route in core/actions_api.py).

Entries live in memory for ``UNDO_TTL_SECONDS`` (15 min) and are dropped by a
restart: undo is a short-lived safety net, not a history.

Collecting ids: wrap a piece of work in ``with undo.collect() as ids:`` and
every :func:`record` call made inside it (same task, or tasks started inside
it) appends ``{"id", "label"}`` to ``ids``. The agent loop uses this to attach
the undo ids of a tool call to its ``tool_end`` event.

Every entry belongs to the user whose work recorded it (the current user,
:func:`core.users.current`: the run's, the request's, the Telegram sender's).
Only that user can see or use it; for anyone else the id does not exist (404).
"""
import contextlib
import contextvars
import inspect
import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, Iterator, List, Optional

from core import users

logger = logging.getLogger(__name__)

UNDO_TTL_SECONDS = int(os.getenv("UNDO_TTL_SECONDS", "900"))
MAX_ENTRIES = 500

_entries: Dict[str, Dict[str, Any]] = {}
_collector: contextvars.ContextVar[Optional[List[Dict[str, str]]]] = contextvars.ContextVar(
    "undo_collector", default=None
)


def _prune() -> None:
    now = time.monotonic()
    for uid in [u for u, e in _entries.items() if now - e["at"] > UNDO_TTL_SECONDS]:
        _entries.pop(uid, None)
    while len(_entries) > MAX_ENTRIES:
        _entries.pop(next(iter(_entries)), None)


def record(label: str, undo: Callable[[], Any], *, source: str = "") -> str:
    """Register how to reverse an action that has just been done.

    Args:
        label: What was done, as the user would read it ("Added milk to the
            shopping list"). Shown next to the Undo button and in the reply to
            the undo request.
        undo: Sync or async callable with no arguments that reverses the action.
            It should be safe to call when the thing has since changed (e.g.
            the task was deleted): do nothing or raise a clear error.
        source: Free text for logs (tool or endpoint name).

    Returns:
        The undo id to hand to the client.
    """
    _prune()
    uid = uuid.uuid4().hex[:12]
    _entries[uid] = {"label": label, "undo": undo, "at": time.monotonic(), "source": source,
                     "user_id": users.current_id()}
    collected = _collector.get()
    if collected is not None:
        collected.append({"id": uid, "label": label})
    return uid


@contextlib.contextmanager
def collect() -> Iterator[List[Dict[str, str]]]:
    """Collect the ``{"id", "label"}`` of every undo recorded inside the block."""
    ids: List[Dict[str, str]] = []
    token = _collector.set(ids)
    try:
        yield ids
    finally:
        _collector.reset(token)


def describe(undo_id: str, user_id: Optional[str]) -> Optional[Dict[str, str]]:
    """``{"id", "label"}`` of a still-valid entry of ``user_id``, else None."""
    _prune()
    entry = _entries.get(undo_id)
    if entry is None or entry["user_id"] != user_id:
        return None
    return {"id": undo_id, "label": entry["label"]}


async def run(undo_id: str) -> str:
    """Reverse one of the current user's actions and forget the entry.
    Returns its label.

    Raises:
        KeyError: the id is unknown, already used, expired, or another user's.
    """
    _prune()
    entry = _entries.get(undo_id)
    if entry is None or entry["user_id"] != users.current_id():
        raise KeyError(undo_id)
    del _entries[undo_id]
    result = entry["undo"]()
    if inspect.isawaitable(result):
        await result
    logger.info(f"Undone: {entry['label']} ({entry['source'] or 'no source'})")
    return entry["label"]
