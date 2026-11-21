"""Live updates of shopping lists (docs/shopping.md, "Live updates").

Every change of a list runs inside :func:`change` and raises the list's
``version`` in the same transaction (:meth:`Change.bump`). After the commit
the new version is published:

- to the open ``GET /api/shopping/events`` streams following that list
  (in-process publish / subscribe: :class:`Listener`);
- as a silent phone push of type ``shopping`` (``{list_id, version}``) to
  every person on the list who has ``phone``, at most one per user per
  :data:`PUSH_EVERY` seconds; the last change of a burst is always sent
  (trailing).

A user's own view can change without any list changing (a list shared with
them, an invitation answered, their active list switched): :meth:`Change.signal`
tells their streams to read the screen again (``lists`` event).
"""

import asyncio
import contextlib
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Set, Tuple

from core import notify, users
from core.db import get_db_connection
from core.addons import registry, sharing
from addons.shopping.values import ITEM_TYPE

logger = logging.getLogger(__name__)

PUSH_EVERY = 10.0       # seconds between two pushes to one user
PING_EVERY = 25.0       # seconds between two keep-alive comments on a stream


# --- Transactions --------------------------------------------------------------------

@dataclass
class Change:
    """One write transaction on shopping data: the connection, the lists
    whose version it raised and the users whose view changed."""
    conn: sqlite3.Connection
    versions: Dict[str, int] = field(default_factory=dict)
    users: Set[str] = field(default_factory=set)

    def bump(self, list_id: str) -> int:
        """Raise ``list_id``'s version (once per transaction); returns the new version."""
        if list_id not in self.versions:
            # scoped: the list's version counter; callers checked access to the list
            self.conn.execute("UPDATE shopping_lists SET version = version + 1 WHERE id = ?", (list_id,))
            # scoped: the list's version counter; callers checked access to the list
            row = self.conn.execute("SELECT version FROM shopping_lists WHERE id = ?", (list_id,)).fetchone()
            self.versions[list_id] = int(row["version"]) if row else 0
        return self.versions[list_id]

    def signal(self, user_id: str) -> None:
        """Tell ``user_id``'s streams their lists / invitations / active list changed."""
        self.users.add(user_id)


@contextlib.contextmanager
def change() -> Iterator[Change]:
    """A write transaction (``BEGIN IMMEDIATE``); after it commits, every list
    it bumped is published and every user it signalled is told."""
    conn = get_db_connection()
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")
    tx = Change(conn)
    try:
        yield tx
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    for list_id, version in tx.versions.items():
        publish(list_id, version)
    for user_id in tx.users:
        signal_user(user_id)


def version_of(list_id: str) -> int:
    """The list's current version (0: no such list)."""
    with get_db_connection() as conn:
        # scoped: a version number only; callers checked access to the list
        row = conn.execute("SELECT version FROM shopping_lists WHERE id = ?", (list_id,)).fetchone()
    return int(row["version"]) if row else 0


# --- Streams -------------------------------------------------------------------------

_by_list: Dict[str, Set["Listener"]] = {}
_by_user: Dict[str, Set["Listener"]] = {}


class Listener:
    """One open stream: gets ``("changed", list_id, version)`` for the list it
    follows and ``("lists",)`` when its user's view changed."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.list_id: Optional[str] = None
        self.queue: "asyncio.Queue[Tuple]" = asyncio.Queue(maxsize=64)
        _by_user.setdefault(user_id, set()).add(self)

    def follow(self, list_id: str) -> None:
        """Get the changes of ``list_id`` (instead of the list followed before)."""
        if self.list_id == list_id:
            return
        self._leave()
        self.list_id = list_id
        _by_list.setdefault(list_id, set()).add(self)

    def put(self, message: Tuple) -> None:
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            pass  # a stuck client: it reads the screen again on its next event anyway

    def _leave(self) -> None:
        if self.list_id is not None:
            subs = _by_list.get(self.list_id)
            if subs is not None:
                subs.discard(self)
                if not subs:
                    _by_list.pop(self.list_id, None)
        self.list_id = None

    def close(self) -> None:
        """Stop listening (the stream ended or the client went away)."""
        self._leave()
        subs = _by_user.get(self.user_id)
        if subs is not None:
            subs.discard(self)
            if not subs:
                _by_user.pop(self.user_id, None)


def listeners(list_id: Optional[str] = None) -> int:
    """How many streams follow ``list_id`` (None: streams open in all)."""
    if list_id is None:
        return sum(len(s) for s in _by_user.values())
    return len(_by_list.get(list_id, ()))


def signal_user(user_id: str) -> None:
    """Tell ``user_id``'s open streams their view changed."""
    for listener in list(_by_user.get(user_id, ())):
        listener.put(("lists",))


# --- Publishing ----------------------------------------------------------------------

def people_on(list_id: str) -> List[str]:
    """The ids of everyone on the list: its owner and the users it is shared with."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT user_id FROM shopping_lists WHERE id = ?", (list_id,)).fetchone()
    if row is None:
        return []
    return [row["user_id"]] + [s.user_id for s in sharing.shares(ITEM_TYPE, list_id)]


def publish(list_id: str, version: int) -> None:
    """``list_id`` is now at ``version``: tell its streams and queue the phone pushes."""
    for listener in list(_by_list.get(list_id, ())):
        listener.put(("changed", list_id, version))
    for user_id in people_on(list_id):
        _queue_push(user_id, list_id, version)


@dataclass
class _PushState:
    last: float = float("-inf")
    pending: Optional[Tuple[str, int]] = None
    task: Optional["asyncio.Task[None]"] = None


_pushes: Dict[str, _PushState] = {}


def _queue_push(user_id: str, list_id: str, version: int) -> None:
    """Push ``{list_id, version}`` to ``user_id``'s phone now, or when their
    :data:`PUSH_EVERY` window ends (then with the newest version)."""
    user = users.get(user_id)
    if user is None or not registry.channels(user):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no event loop (a command-line change): the phone catches up on its next sync
    state = _pushes.setdefault(user_id, _PushState())
    state.pending = (list_id, version)
    if state.task is None or state.task.done():
        state.task = loop.create_task(_push_loop(user_id, state))


async def _push_loop(user_id: str, state: _PushState) -> None:
    while state.pending is not None:
        wait = state.last + PUSH_EVERY - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        list_id, version = state.pending
        state.pending = None
        state.last = time.monotonic()
        user = users.get(user_id)
        if user is None:
            return
        try:
            await notify.send(user, notify.Message(kind="shopping", data={"list_id": list_id, "version": version}))
        except Exception:
            logger.warning(f"Shopping push to user {user_id} failed", exc_info=True)


async def drain_pushes() -> None:
    """Wait until every queued push is sent (tests, shutdown)."""
    while True:
        tasks = [s.task for s in _pushes.values() if s.task is not None and not s.task.done()]
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)
