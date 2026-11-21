"""Other kinds watching a user's new phone notifications (docs/notifications.md).

An addon that wants to see new notifications registers ``fn(user,
notification_ids)`` here (sync or async) instead of being imported by the
phone addon; the money addon reads payment notifications this way.
:func:`notify` runs every observer in the background, as the user, after the
notifications were stored; an observer's error is logged and swallowed and
never holds up the notification pipeline.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable, List, Optional, Sequence, Set

from core import users
from core.users import User

logger = logging.getLogger(__name__)

Observer = Callable[[User, List[str]], Any]

_observers: List[Observer] = []
_tasks: Set["asyncio.Task[None]"] = set()


def register(fn: Observer) -> Observer:
    """Register ``fn(user, notification_ids)`` (usable as a decorator; twice is a no-op)."""
    if fn not in _observers:
        _observers.append(fn)
    return fn


async def _call(fn: Observer, user: User, ids: List[str]) -> None:
    try:
        if inspect.iscoroutinefunction(fn):
            await fn(user, list(ids))
        else:
            result = await asyncio.to_thread(fn, user, list(ids))
            if inspect.isawaitable(result):
                await result
    except Exception as e:
        logger.error(f"Notification observer {getattr(fn, '__qualname__', fn)} failed for user {user.id}: {e}",
                     exc_info=True)


async def run(user: User, ids: Sequence[str]) -> None:
    """Call every observer with ``ids`` as ``user``, one after another, errors logged."""
    with users.acting_as(user):
        for fn in list(_observers):
            await _call(fn, user, list(ids))


def notify(user: User, ids: Sequence[str]) -> Optional["asyncio.Task[None]"]:
    """Hand ``user``'s new notification ids to every observer in the background
    (the returned task; None when there is nothing to do). Without a running
    event loop (a script) the observers run here."""
    ids = [i for i in dict.fromkeys(ids or []) if isinstance(i, str) and i]
    if not ids or not _observers:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(run(user, ids))
        return None
    with users.acting_as(user):  # the task copies the context: it runs as the user
        task = loop.create_task(run(user, ids))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def idle() -> None:
    """Wait until the observer runs started so far have ended."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
