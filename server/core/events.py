"""Events: how addons (and core) react to each other without importing each
other (../docs/addons.md, "Events").

``emit("tasks.task.created", user, task_id=..., title=...)`` calls every
``@addon.on("tasks.task.created")`` handler of an addon ``user`` has, each in
its own background task, as ``user`` (``users.acting_as``), with the keyword
arguments as one ``data`` dict. A failing handler is logged and never reaches
the emitter or the other handlers. Names are ``<addon>.<thing>.<what
happened>``; core's own start with ``core.`` (``core.user.woke``). Each
addon's README lists the events it emits and their data.

Without a running event loop (a script, a thread) the handlers run at once,
one after another, before :func:`emit` returns. Tests wait for the handlers
of everything emitted so far with :func:`drain`.
"""

import asyncio
import logging
from typing import Any, Dict, List, Set

from core import users
from core.users import User
from core.addons import registry
from core.addons.hooks import Handler

logger = logging.getLogger(__name__)

_pending: Set["asyncio.Task[None]"] = set()


async def _run(handler: Handler, user: User, name: str, data: Dict[str, Any]) -> None:
    try:
        with users.acting_as(user):
            await handler.func(user, data)
    except Exception:
        logger.exception(f"Event {name}: the {handler.addon} handler {handler.func.__name__} failed for user {user.id}")


def emit(name: str, user: User, **data: Any) -> int:
    """Hand event ``name`` for ``user`` to every handler of an addon they have;
    returns how many handlers were started. Never raises because of a handler."""
    handlers = registry.handlers(name, user)
    if not handlers:
        return 0
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    for h in handlers:
        if loop is None:
            asyncio.run(_run(h, user, name, dict(data)))
            continue
        task = loop.create_task(_run(h, user, name, dict(data)), name=f"event-{name}-{h.addon}")
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    logger.debug(f"Event {name} for user {user.id}: {len(handlers)} handler(s)")
    return len(handlers)


async def drain() -> None:
    """Wait until every handler started so far (and any they emit) has finished (tests)."""
    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


def pending() -> List["asyncio.Task[None]"]:
    """The handlers still running."""
    return list(_pending)
