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
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from core import users
from core.users import User
from core.addons import registry
from core.addons.hooks import EventType, Handler

logger = logging.getLogger(__name__)

_pending: Set["asyncio.Task[None]"] = set()
# The loop handlers from other threads go to (bind(), or the last one emit ran on).
_loop: Optional[asyncio.AbstractEventLoop] = None
# Handed over with call_soon_threadsafe, not started yet (drain waits for them too).
_handed = 0
_lock = threading.Lock()

# Called for every emit (also when no addon handler exists): fn(name, user, data).
Tap = Callable[[str, User, Dict[str, Any]], Awaitable[None]]
_taps: List[Tap] = []

# Core's own events, for watches (registry.event_types).
CORE_EVENTS = [
    EventType("core.signal.car", "The user got into or out of their car (from Dana's phone app).",
              {"action": "enter | leave", "source": "which source reported it", "late": "true when it arrived late"}),
    EventType("core.signal.location", "The user arrived at or left one of their places (zones).",
              {"zone": "the place's name (home, work...)", "action": "arrive | leave",
               "target": "the place's name as the source gave it", "source": "which source reported it"}),
    EventType("core.user.woke", "The user woke up (first unlock of the morning or a wake signal).",
              {"why": "what showed it"}),
]
registry.register_core_events(CORE_EVENTS)


def tap(fn: Tap) -> None:
    """Call ``fn(name, user, data)`` for every event emitted from now on (started like handlers)."""
    if fn not in _taps:
        _taps.append(fn)


def untap(fn: Tap) -> None:
    """Stop calling ``fn`` for emitted events."""
    if fn in _taps:
        _taps.remove(fn)


def _tap_handler(fn: Tap, name: str) -> Handler:
    """A tap as a handler of one emit (started, awaited and logged like the addons' handlers)."""
    async def call(user: User, data: Dict[str, Any]) -> None:
        await fn(name, user, data)
    call.__name__ = getattr(fn, "__name__", "tap")
    return Handler("tap", name, call)


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
    own = registry.handlers(name, user)
    handlers = own + [_tap_handler(fn, name) for fn in list(_taps)]
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
    return len(own)  # the addons' handlers (taps are not counted)


async def drain() -> None:
    """Wait until every handler started so far (and any they emit, or handed
    over from another thread) has finished (tests)."""
    while _pending or _handed:
        running = [t for t in _pending if not t.done()]
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        else:
            # only finished tasks whose done callbacks have not run yet, or starts handed over from
            # another thread: give the loop a turn (awaiting finished futures alone never yields)
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
