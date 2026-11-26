"""Events: how addons (and core) react to each other without importing each
other (../docs/addons.md, "Events").

``emit("tasks.task.created", user, task_id=..., title=...)`` calls every
``@addon.on("tasks.task.created")`` handler of an addon active for ``user``
(``registry.active_addons``), each in its own background task, as ``user``
(``users.acting_as``), with the keyword arguments as one ``data`` dict. A
failing handler is logged and never reaches the emitter or the other
handlers. Names are ``<addon>.<thing>.<what happened>``; core's own start
with ``core.`` (``core.user.woke``).

**Taps** (:func:`tap` / :func:`untap`) see every event: ``fn(name, user,
data)`` is started like a handler for each :func:`emit`, whoever has a handler
for it (the ``watches`` addon matches any event this way).

**The catalog**: what an event means and its data keys are declared as an
:class:`~core.addons.hooks.EventType`: core's in :data:`CORE_EVENTS`, an
addon's in ``Addon(events=)``; ``core.addons.registry.event_types(user)``
lists those of a user.

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
import datetime
import functools
import logging
import threading
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from core import clock, users
from core.db import get_db_connection
from core.users import User
from core.addons import registry
from core.addons.hooks import EventType, PastEvent

# A tap: fn(name, user, data), called for every emitted event.
Tap = Callable[[str, User, Dict[str, Any]], Awaitable[None]]
# What one emit starts: (who it is, for the log; fn(user, data)).
_Call = Tuple[str, Callable[[User, Dict[str, Any]], Awaitable[None]]]

logger = logging.getLogger(__name__)

_pending: Set["asyncio.Task[None]"] = set()
# The loop handlers from other threads go to (bind(), or the last one emit ran on).
_loop: Optional[asyncio.AbstractEventLoop] = None
# Handed over with call_soon_threadsafe, not started yet (drain waits for them too).
_handed = 0
_lock = threading.Lock()
_taps: List[Tap] = []


async def _run(call: _Call, user: User, name: str, data: Dict[str, Any]) -> None:
    who, fn = call
    try:
        with users.acting_as(user):
            await fn(user, data)
    except Exception:
        logger.exception(f"Event {name}: {who} failed for user {user.id}")


def _start(loop: asyncio.AbstractEventLoop, calls: List[_Call], user: User, name: str,
           data: Dict[str, Any]) -> None:
    for call in calls:
        task = loop.create_task(_run(call, user, name, dict(data)), name=f"event-{name}-{call[0]}")
        _pending.add(task)
        task.add_done_callback(_pending.discard)


def _start_handed(calls: List[_Call], user: User, name: str, data: Dict[str, Any]) -> None:
    global _handed
    try:
        _start(asyncio.get_running_loop(), calls, user, name, data)
    finally:
        with _lock:
            _handed -= 1


def tap(fn: Tap) -> None:
    """Call ``fn(name, user, data)`` for every event emitted from now on, like
    a handler (its own task, as the user, a failure logged); once per function."""
    if fn not in _taps:
        _taps.append(fn)


def untap(fn: Tap) -> None:
    """Stop calling ``fn`` (nothing when it is not tapped)."""
    if fn in _taps:
        _taps.remove(fn)


def bind(loop: asyncio.AbstractEventLoop) -> None:
    """Make ``loop`` (the server's) the one handlers emitted from other threads run on."""
    global _loop
    _loop = loop


def emit(name: str, user: User, /, **data: Any) -> int:
    """Hand event ``name`` for ``user`` to every handler of an addon active for
    them and to every tap (``name`` and ``user`` are positional, so the data may
    carry keys of those names); returns how many handlers were started (or handed
    to the server's loop), taps not counted. Never raises because of a handler."""
    global _loop, _handed
    handlers = registry.handlers(name, user)
    calls: List[_Call] = [(f"the {h.addon} handler {h.func.__name__}", h.func) for h in handlers]
    calls += [(f"the tap {getattr(t, '__name__', 'tap')}", functools.partial(t, name)) for t in list(_taps)]
    if not calls:
        return 0
    try:
        running: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        if _loop is None or _loop.is_closed():
            _loop = running
        _start(running, calls, user, name, data)
    elif _loop is not None and _loop.is_running() and not _loop.is_closed():
        with _lock:
            _handed += 1
        try:
            _loop.call_soon_threadsafe(_start_handed, calls, user, name, dict(data))
        except RuntimeError:  # the loop closed in between
            with _lock:
                _handed -= 1
            for call in calls:
                asyncio.run(_run(call, user, name, dict(data)))
    else:
        for call in calls:
            asyncio.run(_run(call, user, name, dict(data)))
    logger.debug(f"Event {name} for user {user.id}: {len(handlers)} handler(s), {len(calls) - len(handlers)} tap(s)")
    return len(handlers)


async def drain() -> None:
    """Wait until every handler and tap started so far (and any they emit, or
    handed over from another thread) has finished (tests)."""
    while _pending or _handed:
        running = [t for t in _pending if not t.done()]
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        else:
            # only finished tasks whose done callbacks have not run yet, or starts handed over from
            # another thread: give the loop a turn (awaiting finished futures alone never yields)
            await asyncio.sleep(0.01)


def pending() -> List["asyncio.Task[None]"]:
    """The handlers and taps still running."""
    return list(_pending)


async def stop(app: Any = None) -> None:
    """Cancel the handlers and taps still running and forget the bound loop (the app's cleanup)."""
    global _loop
    tasks = list(_pending)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _loop = None


# --- Core's events (the catalog) --------------------------------------------------------


def _woke_history(user: User, since: datetime.datetime) -> List[PastEvent]:
    """``user``'s confirmed wakes since ``since`` (wake_log), oldest first."""
    first_day = (since.astimezone(clock.get_timezone()).date() - datetime.timedelta(days=1)).isoformat()
    with get_db_connection() as conn:
        rows = conn.execute("SELECT at, why FROM wake_log WHERE user_id = ? AND date >= ?",
                            (user.id, first_day)).fetchall()
    out: List[PastEvent] = []
    for at, why in rows:
        try:
            when = datetime.datetime.fromisoformat(at)
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=clock.get_timezone())
        if when >= since:
            out.append((when, {"why": why or ""}))
    out.sort(key=lambda item: item[0])
    return out


_SIGNAL_FIELDS = {"source": "the source that delivered it (\"phone\": Dana's phone app)",
                  "late": "true when it happened more than 30 minutes before it arrived (no reminders then)"}

CORE_EVENTS: Tuple[EventType, ...] = (
    EventType("core.signal.car", "The user got into or out of their car (the phone's car Bluetooth).",
              {"action": "enter | leave", **_SIGNAL_FIELDS}),
    EventType("core.signal.location", "The user arrived at or left one of their places (zones).",
              {"action": "arrive | leave", "zone": "the place's name (\"Home\")",
               "target": "the place's name (the same as zone)", **_SIGNAL_FIELDS}),
    EventType("core.user.woke", "The user is up for the day (once a day, from the phone's unlocks or screen time).",
              {"why": "what showed they are up, in a few words"}, history=_woke_history),
)
