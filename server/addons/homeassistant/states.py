"""Home Assistant state changes as Dana events (``homeassistant.state.changed``,
declared in :mod:`.event_types`), so watches can react to the home ("when
the dishwasher finishes", "phone battery under 50%").

One WebSocket per user connection: :func:`run` (the addon's service) keeps
one task per user who has the addon active, looked at again every
:data:`RECONCILE_SECONDS` and at once after any change of a connection
(:func:`poke`); a disconnected user's task is cancelled, a new host or token
starts a new one. Each task: connect, authenticate (a refused token marks the
connection for reconnecting, :func:`client.authenticate`), ``get_states`` (the
entity list: :func:`known_entities`), ``subscribe_events`` for
``state_changed``, then read; on any failure it connects again after a
backoff (1 s doubling to :data:`BACKOFF_MAX`, the longest after a refused
token).

What is emitted (:class:`Tracker`, one per user): only a change of the
state itself (attribute-only updates are not), never to or from
``unavailable`` / ``unknown`` (``old_state`` is the last good state Dana
reported, so 50 → unavailable → 45 is one change 50 → 45, and an entity that
comes back unchanged emits nothing). Numeric states are throttled per
entity: at most one event per :data:`THROTTLE_SECONDS`, unless the value
moved to another tens bucket (52 → 49); a held value is emitted once its
time is up (``old_state`` is still the last reported one, so a crossing is
never lost). The last events (:data:`KEEP_RECENT`, in memory since the
server started) are the dry runs' history.
"""

import asyncio
import collections
import datetime
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

import websockets  # type: ignore

from core import clock, events, users
from core.addons import Connection, registry, store
from core.addons.hooks import PastEvent
from core.users import User
from addons.homeassistant import client
from addons.homeassistant.config import ADDON

logger = logging.getLogger(__name__)

EVENT = "homeassistant.state.changed"
# States that are no state: transitions to them are skipped, from them compared with the last good state.
NO_STATE = frozenset({"unavailable", "unknown", "none", ""})
THROTTLE_SECONDS = 60.0
RECONCILE_SECONDS = 15.0
# How long a read waits before the held (throttled) values are looked at.
FLUSH_SECONDS = 5.0
BACKOFF_MIN = 1.0
BACKOFF_MAX = 300.0
# A session that lasted this long starts the backoff over.
HEALTHY_SECONDS = 60.0
OPEN_TIMEOUT = 20.0
# get_states of a big home is one large message.
MAX_MESSAGE = 32 * 1024 * 1024
KEEP_RECENT = 5000


def _number(state: Any) -> Optional[float]:
    try:
        n = float(str(state))
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _bucket(n: float) -> int:
    return math.floor(n / 10)


@dataclass
class Entity:
    """One entity as the tracker knows it."""
    name: str
    domain: str
    unit: str
    device_class: str
    reported: Optional[str] = None   # the last good state Dana reported (or first saw)
    last_emit: float = -math.inf     # monotonic time of its last event
    held: Optional[str] = None       # a numeric state the throttle holds back


class Tracker:
    """One user's entities, what was last reported of each, and their recent events."""

    def __init__(self, signature: Tuple[str, str, str]) -> None:
        self.signature = signature
        self.entities: Dict[str, Entity] = {}
        self.recent: Deque[PastEvent] = collections.deque(maxlen=KEEP_RECENT)

    def _entity(self, entity_id: str, state: Dict[str, Any]) -> Entity:
        attrs = state.get("attributes") or {}
        ent = self.entities.get(entity_id)
        if ent is None:
            ent = self.entities[entity_id] = Entity("", entity_id.split(".", 1)[0], "", "")
        ent.name = str(attrs.get("friendly_name") or entity_id)
        ent.unit = str(attrs.get("unit_of_measurement") or "")
        ent.device_class = str(attrs.get("device_class") or "")
        return ent

    def load(self, states: Iterable[Dict[str, Any]]) -> None:
        """Every entity's current state (``get_states``): the starting point of what is reported."""
        for s in states:
            eid = str(s.get("entity_id") or "")
            if not client.valid_entity_id(eid):
                continue
            ent = self._entity(eid, s)
            state = str(s.get("state") if s.get("state") is not None else "")
            if state.lower() not in NO_STATE:
                ent.reported = state

    def change(self, entity_id: str, old: Optional[Dict[str, Any]], new: Optional[Dict[str, Any]],
               now: float) -> Optional[Dict[str, Any]]:
        """One ``state_changed``: the event's data to emit, or None (see the module docstring)."""
        if not client.valid_entity_id(entity_id):
            return None
        if new is None:
            self.entities.pop(entity_id, None)
            return None
        ent = self._entity(entity_id, new)
        state = str(new.get("state") if new.get("state") is not None else "")
        if state.lower() in NO_STATE:
            return None
        if ent.reported is None:
            before = str((old or {}).get("state") or "")
            ent.reported = state if before.lower() in NO_STATE else before
        if state == ent.reported:
            ent.held = None
            return None
        n, was = _number(state), _number(ent.reported)
        if (n is not None and was is not None and now - ent.last_emit < THROTTLE_SECONDS
                and _bucket(n) == _bucket(was)):
            ent.held = state
            return None
        return self._emit(entity_id, ent, state, now)

    def due(self, now: float) -> List[Dict[str, Any]]:
        """The held numeric states whose throttle time is up, as events to emit."""
        out = []
        for eid, ent in self.entities.items():
            if ent.held is not None and now - ent.last_emit >= THROTTLE_SECONDS:
                held, ent.held = ent.held, None
                if held != ent.reported:
                    out.append(self._emit(eid, ent, held, now))
        return out

    def _emit(self, entity_id: str, ent: Entity, state: str, now: float) -> Dict[str, Any]:
        data = {"entity_id": entity_id, "name": ent.name, "domain": ent.domain, "old_state": ent.reported or "",
                "new_state": state, "unit": ent.unit, "device_class": ent.device_class}
        ent.reported, ent.last_emit, ent.held = state, now, None
        self.recent.append((clock.now(), dict(data)))
        return data


_trackers: Dict[str, Tracker] = {}
_tasks: Dict[str, Tuple[Tuple[str, str, str], "asyncio.Task[None]"]] = {}
_wake: Optional[asyncio.Event] = None
_loop: Optional[asyncio.AbstractEventLoop] = None


def tracker_for(conn: Connection) -> Tracker:
    """``conn``'s tracker (a new host or token starts an empty one)."""
    sig = client.signature(conn)
    t = _trackers.get(conn.user.id)
    if t is None or t.signature != sig:
        t = _trackers[conn.user.id] = Tracker(sig)
    return t


def tracker(user: User) -> Optional[Tracker]:
    """``user``'s tracker while their connection is the one it was made for (else None)."""
    t = _trackers.get(user.id)
    conn = store.get(user, ADDON) if t is not None else None
    return t if conn is not None and t is not None and client.signature(conn) == t.signature else None


def known_entities(user: User) -> Dict[str, Entity]:
    """``user``'s entities as last read from their Home Assistant ({} before the first read)."""
    t = tracker(user)
    return dict(t.entities) if t is not None else {}


def history(user: User, since: datetime.datetime) -> List[PastEvent]:
    """``user``'s state-change events since ``since`` that Dana saw (in memory since the server started)."""
    t = tracker(user)
    return [(when, dict(data)) for when, data in (t.recent if t is not None else ()) if when >= since]


def emit(user: User, data: Dict[str, Any]) -> None:
    events.emit(EVENT, user, **data)


def _messages(raw: Any) -> List[Dict[str, Any]]:
    try:
        msg = json.loads(raw)
    except (TypeError, ValueError):
        return []
    items = msg if isinstance(msg, list) else [msg]  # coalesced messages arrive as a list
    return [m for m in items if isinstance(m, dict)]


async def session(conn: Connection) -> None:
    """One WebSocket session of ``conn``: subscribe and emit until it breaks.

    Raises:
        PermissionError: Home Assistant refused the token.
        Exception: the connection failed or Home Assistant refused the subscription.
    """
    user = conn.user
    t = tracker_for(conn)
    async with websockets.connect(client.ws_uri(conn), max_size=MAX_MESSAGE, open_timeout=OPEN_TIMEOUT) as ws:
        await asyncio.wait_for(client.authenticate(ws, conn), OPEN_TIMEOUT)
        await ws.send(json.dumps({"id": 1, "type": "get_states"}))
        await ws.send(json.dumps({"id": 2, "type": "subscribe_events", "event_type": "state_changed"}))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=FLUSH_SECONDS)
            except asyncio.TimeoutError:
                raw = None
            for msg in _messages(raw) if raw is not None else []:
                if msg.get("type") == "result":
                    if not msg.get("success"):
                        raise RuntimeError(f"Home Assistant refused request {msg.get('id')}: {msg.get('error')}")
                    if msg.get("id") == 1 and isinstance(msg.get("result"), list):
                        t.load(msg["result"])
                elif msg.get("type") == "event":
                    ev = msg.get("event") or {}
                    if ev.get("event_type") != "state_changed":
                        continue
                    d = ev.get("data") or {}
                    data = t.change(str(d.get("entity_id") or ""), d.get("old_state"), d.get("new_state"),
                                    time.monotonic())
                    if data is not None:
                        emit(user, data)
            for data in t.due(time.monotonic()):
                emit(user, data)


async def follow(user: User, signature: Tuple[str, str, str]) -> None:
    """Keep ``user``'s subscription for the connection ``signature`` names, reconnecting with backoff;
    returns once that connection is gone or changed."""
    backoff = BACKOFF_MIN
    while True:
        conn = store.get(user, ADDON)
        if conn is None or client.signature(conn) != signature:
            return
        started = time.monotonic()
        wait = None
        try:
            await session(conn)
        except asyncio.CancelledError:
            raise
        except PermissionError:
            wait = BACKOFF_MAX
            logger.warning(f"[HA] state changes of user {user.id}: the token was refused; trying again later")
        except Exception as e:
            logger.warning(f"[HA] state changes of user {user.id}: {type(e).__name__}: {str(e)[:200]}; "
                           f"connecting again in {backoff:.0f}s")
        if time.monotonic() - started >= HEALTHY_SECONDS:
            backoff = BACKOFF_MIN
        await asyncio.sleep(wait or backoff)
        backoff = min(BACKOFF_MAX, backoff * 2)


def _wanted() -> Dict[str, Tuple[User, Tuple[str, str, str]]]:
    """Each user who has the addon active with an address and a token: (user, connection signature)."""
    out: Dict[str, Tuple[User, Tuple[str, str, str]]] = {}
    for user in users.list_users():
        if ADDON not in registry.active_addons(user):
            continue
        conn = store.get(user, ADDON)
        if conn is not None and client.token(conn):
            out[user.id] = (user, client.signature(conn))
    return out


async def reconcile() -> None:
    """Start the missing users' subscriptions, stop those whose connection is gone or changed."""
    wanted = _wanted()
    for uid, (sig, task) in list(_tasks.items()):
        if uid not in wanted or wanted[uid][1] != sig or task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            del _tasks[uid]
            if uid not in wanted:
                _trackers.pop(uid, None)
    for uid, (user, sig) in wanted.items():
        if uid not in _tasks:
            _tasks[uid] = (sig, asyncio.get_running_loop().create_task(follow(user, sig), name=f"ha-states-{uid}"))


async def stop_all() -> None:
    """Cancel every subscription."""
    tasks = [task for _, task in _tasks.values()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()


def poke(user: Optional[User] = None) -> None:
    """Look at the connections again now (``Addon(on_change=)``; safe from any thread, a no-op before :func:`run`)."""
    wake = _wake
    if wake is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        wake.set()
    elif _loop is not None and not _loop.is_closed():
        _loop.call_soon_threadsafe(wake.set)


async def run(app: Any = None) -> None:
    """The service: keep one subscription per user connection until cancelled."""
    global _wake, _loop
    _wake, _loop = asyncio.Event(), asyncio.get_running_loop()
    try:
        while True:
            await reconcile()
            try:
                await asyncio.wait_for(_wake.wait(), timeout=RECONCILE_SECONDS)
            except asyncio.TimeoutError:
                pass
            _wake.clear()
    finally:
        _wake = None
        await stop_all()
