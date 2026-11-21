"""Events: how addons (and core) react to each other without importing each
other (../docs/addons.md, "Events").

``emit("tasks.task.created", user, task_id=..., title=...)`` calls every
``@addon.on("tasks.task.created")`` handler of an addon active for ``user``
(``registry.active_addons``), each in its own background task, as ``user``
(``users.acting_as``), with the keyword arguments as one ``data`` dict. A
failing handler is logged and never reaches the emitter or the other
handlers. Names are ``<addon>.<thing>.<what happened>``; core's own start
with ``core.`` (``core.user.woke``). Each addon's README lists the events it
emits and their data.

Where the handlers run:

- emitted on the event loop's thread: tasks on that loop;
- emitted from another thread (a sync function the loop runs with
  ``asyncio.to_thread``) while the server's loop runs: handed to that loop
  with ``call_soon_threadsafe`` (the loop :func:`bind` named, else the last
  loop :func:`emit` ran on);
- without any running loop (a script): at once, one after another, before
  :func:`emit` returns.

:func:`stop` (the app's cleanup) cancels the handlers still running. Tests
wait for the handlers of everything emitted so far with :func:`drain`.
"""

import asyncio
import logging
import threading
from typing import Any, Dict, List, Optional, Set

from core import users
from core.users import User
from core.addons import registry
from core.addons.hooks import Handler

logger = logging.getLogger(__name__)

_pending: Set["asyncio.Task[None]"] = set()
# The loop handlers from other threads go to (bind(), or the last one emit ran on).
_loop: Optional[asyncio.AbstractEventLoop] = None
# Handed over with call_soon_threadsafe, not started yet (drain waits for them too).
_handed = 0
_lock = threading.Lock()


async def _run(handler: Handler, user: User, name: str, data: Dict[str, Any]) -> None:
    try:
        with users.acting_as(user):
            await handler.func(user, data)
    except Exception:
        logger.exception(f"Event {name}: the {handler.addon} handler {handler.func.__name__} failed for user {user.id}")


def _start(loop: asyncio.AbstractEventLoop, handlers: List[Handler], user: User, name: str,
           data: Dict[str, Any]) -> None:
    for h in handlers:
        task = loop.create_task(_run(h, user, name, dict(data)), name=f"event-{name}-{h.addon}")
        _pending.add(task)
        task.add_done_callback(_pending.discard)


def _start_handed(handlers: List[Handler], user: User, name: str, data: Dict[str, Any]) -> None:
    global _handed
    try:
        _start(asyncio.get_running_loop(), handlers, user, name, data)
    finally:
        with _lock:
            _handed -= 1


def bind(loop: asyncio.AbstractEventLoop) -> None:
    """Make ``loop`` (the server's) the one handlers emitted from other threads run on."""
    global _loop
    _loop = loop


def emit(name: str, user: User, **data: Any) -> int:
    """Hand event ``name`` for ``user`` to every handler of an addon active for
    them; returns how many handlers were started (or handed to the server's
    loop). Never raises because of a handler."""
    global _loop, _handed
    handlers = registry.handlers(name, user)
    if not handlers:
        return 0
    try:
        running: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        if _loop is None or _loop.is_closed():
            _loop = running
        _start(running, handlers, user, name, data)
    elif _loop is not None and _loop.is_running() and not _loop.is_closed():
        with _lock:
            _handed += 1
        try:
            _loop.call_soon_threadsafe(_start_handed, handlers, user, name, dict(data))
        except RuntimeError:  # the loop closed in between
            with _lock:
                _handed -= 1
            for h in handlers:
                asyncio.run(_run(h, user, name, dict(data)))
    else:
        for h in handlers:
            asyncio.run(_run(h, user, name, dict(data)))
    logger.debug(f"Event {name} for user {user.id}: {len(handlers)} handler(s)")
    return len(handlers)


async def drain() -> None:
    """Wait until every handler started so far (and any they emit, or handed
    over from another thread) has finished (tests)."""
    while _pending or _handed:
        if _pending:
            await asyncio.gather(*list(_pending), return_exceptions=True)
        else:
            await asyncio.sleep(0.01)


def pending() -> List["asyncio.Task[None]"]:
    """The handlers still running."""
    return list(_pending)


async def stop(app: Any = None) -> None:
    """Cancel the handlers still running and forget the bound loop (the app's cleanup)."""
    global _loop
    tasks = list(_pending)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _loop = None
