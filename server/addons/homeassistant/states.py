"""Home Assistant state changes as Dana events (``homeassistant.state.changed``,
declared in :mod:`.event_types`), so watches can react to the home ("when
the dishwasher finishes", "phone battery under 50%").

One WebSocket per user connection: :func:`run` (the addon's service) keeps
one task per user who has the addon active, looked at again every
:data:`RECONCILE_SECONDS` and at once after any change of a connection
(:func:`poke`); a disconnected user's task is cancelled, a new host or token
starts a new one. Each task: connect, authenticate (a refused token marks the
connection for reconnecting, :func:`client.authenticate`), ``get_states`` (the
entity list: :func:`known_entities`), subscribe, then read; on any failure it
connects again after a backoff (1 s doubling to :data:`BACKOFF_MAX`, the
longest after a refused token).

What it subscribes to follows the consumers' interest
(``events.interest(user.id, EVENT, "entity_id")``, set by the ``watches``
addon from the entity ids its active watches name; :func:`wanted_entities`):

- ``None`` (no narrowing): ``subscribe_events`` for ``state_changed``, every
  entity;
- some entity ids: ``subscribe_entities`` for just those (Home Assistant
  2022.4+, no admin needed; its compressed messages become the same event
  data, :meth:`Tracker.compressed`); a Home Assistant that refuses it gets
  ``state_changed`` with the others dropped here;
- none: no WebSocket at all; the entity list (the compiler's known values) is
  read over REST every :data:`ENTITY_REFRESH_SECONDS` instead.

A change of interest (:func:`events.on_interest`) re-subscribes within the
open session after :data:`DEBOUNCE_SECONDS` (``unsubscribe_events`` the old
id, subscribe the new), or closes / opens the WebSocket.

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
from typing import Any, Deque, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

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
# A change of interest is applied this long after it first arrived (several watches saved at once: one re-subscribe).
DEBOUNCE_SECONDS = 1.0
# With no entity wanted (no WebSocket), how often the entity list is read over REST.
ENTITY_REFRESH_SECONDS = 900.0
# Attributes the tracker keeps, and what each is when Home Assistant removes it.
_ATTRS = {"friendly_name": "name", "unit_of_measurement": "unit", "device_class": "device_class"}


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
        self.read_at = -math.inf          # monotonic time the whole entity list was last read
        self.no_subscribe_entities = False  # Home Assistant refused subscribe_entities (too old)

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
        return self._state(entity_id, ent, state, str((old or {}).get("state") or ""), now)

    def compressed(self, ev: Dict[str, Any], now: float) -> List[Dict[str, Any]]:
        """One ``subscribe_entities`` message (``a``: entities in full, the first message and new ones;
        ``c``: changes, ``+`` with ``s`` the new state and ``a`` the changed attributes, ``-`` with ``a``
        the removed attribute names; ``r``: removed ids): the events to emit, like :meth:`change`
        (an ``a`` entity is read like ``get_states``, an attribute-only change is none)."""
        added = ev.get("a")
        if isinstance(added, dict):
            self.load({"entity_id": eid, "state": c.get("s"), "attributes": c.get("a") or {}}
                      for eid, c in added.items() if isinstance(c, dict))
        out: List[Dict[str, Any]] = []
        changes = ev.get("c")
        for eid, c in (changes.items() if isinstance(changes, dict) else ()):
            if not isinstance(c, dict) or not client.valid_entity_id(eid):
                continue
            plus, minus = c.get("+") or {}, c.get("-") or {}
            ent = self.entities.get(eid)
            if ent is None:
                ent = self.entities[eid] = Entity(eid, eid.split(".", 1)[0], "", "")
            attrs = dict(plus.get("a")) if isinstance(plus.get("a"), dict) else {}
            for key in minus.get("a") or ():
                if key in _ATTRS:
                    attrs.setdefault(key, None)
            for key, field in _ATTRS.items():
                if key in attrs:
                    setattr(ent, field, str(attrs[key] or (eid if key == "friendly_name" else "")))
            if "s" in plus:
                data = self._state(eid, ent, str(plus["s"] if plus["s"] is not None else ""), "", now)
                if data is not None:
                    out.append(data)
        removed = ev.get("r")
        for eid in (removed if isinstance(removed, list) else ()):
            self.entities.pop(str(eid), None)
        return out

    def _state(self, entity_id: str, ent: Entity, state: str, before: str, now: float) -> Optional[Dict[str, Any]]:
        """``entity_id``'s state is now ``state`` (``before``: what Home Assistant says it was, used only
        for an entity Dana has no state of yet): the event's data to emit, or None."""
        if state.lower() in NO_STATE:
            return None
        if ent.reported is None:
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
# Per user with a task: set when the interest in their state changes changed (:func:`_interest_changed`).
_changed: Dict[str, asyncio.Event] = {}


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


def wanted_entities(user: User) -> Optional[FrozenSet[str]]:
    """The entity ids whose changes are wanted for ``user`` (the consumers' interest, valid ids only):
    None = every entity, empty = none."""
    want = events.interest(user.id, EVENT, "entity_id")
    return None if want is None else frozenset(e for e in want if client.valid_entity_id(e))


class _Subscriber:
    """One WebSocket session's requests: their ids, and what its live subscription follows."""

    def __init__(self, ws: Any, t: Tracker) -> None:
        self.ws, self.t = ws, t
        self.next_id = 1
        self.sub_id: Optional[int] = None         # the live subscription's request id
        self.entities_id: Optional[int] = None    # the last subscribe_entities request
        self.states_id: Optional[int] = None      # the get_states request
        self.unsub_ids: Set[int] = set()
        self.following: Optional[FrozenSet[str]] = None  # None: every entity
        self.only: Optional[FrozenSet[str]] = None       # emit only these (state_changed standing in)

    async def send(self, msg: Dict[str, Any]) -> int:
        mid = self.next_id
        self.next_id += 1
        await self.ws.send(json.dumps({"id": mid, **msg}))
        return mid

    async def follow(self, want: Optional[FrozenSet[str]]) -> None:
        """Subscribe to ``want`` (None: every entity; not empty), unsubscribing the live one first."""
        if self.sub_id is not None:
            self.unsub_ids.add(await self.send({"type": "unsubscribe_events", "subscription": self.sub_id}))
            self.sub_id = None
        self.following, self.only = want, None
        if want is not None and not self.t.no_subscribe_entities:
            self.sub_id = self.entities_id = await self.send({"type": "subscribe_entities",
                                                              "entity_ids": sorted(want)})
        else:
            self.only = want
            self.sub_id = await self.send({"type": "subscribe_events", "event_type": "state_changed"})

    async def handle(self, msg: Dict[str, Any], now: float) -> List[Dict[str, Any]]:
        """One message from Home Assistant: the events to emit.

        Raises:
            RuntimeError: Home Assistant refused a request (other than an unsubscribe or a
                subscribe_entities, which falls back to ``state_changed``).
        """
        if msg.get("type") == "result":
            mid = msg.get("id")
            if msg.get("success"):
                if mid == self.states_id and isinstance(msg.get("result"), list):
                    self.t.load(msg["result"])
                    self.t.read_at = now
            elif mid in self.unsub_ids:
                logger.debug(f"[HA] unsubscribe {mid} refused: {msg.get('error')}")
            elif mid is not None and mid == self.entities_id:
                logger.info(f"[HA] subscribe_entities refused ({msg.get('error')}); following state_changed instead")
                self.t.no_subscribe_entities = True
                if self.sub_id == mid:
                    self.sub_id = None
                    await self.follow(self.following)
            else:
                raise RuntimeError(f"Home Assistant refused request {mid}: {msg.get('error')}")
            return []
        if msg.get("type") != "event":
            return []
        ev = msg.get("event") or {}
        if not isinstance(ev, dict):
            return []
        if ev.get("event_type") == "state_changed":
            d = ev.get("data") or {}
            eid = str(d.get("entity_id") or "")
            if self.only is not None and eid not in self.only:
                new = d.get("new_state")
                if isinstance(new, dict) and client.valid_entity_id(eid):
                    self.t.load([{**new, "entity_id": eid}])  # kept current, not emitted
                return []
            data = self.t.change(eid, d.get("old_state"), d.get("new_state"), now)
            return [data] if data is not None else []
        if "event_type" not in ev and any(k in ev for k in ("a", "c", "r")):
            return self.t.compressed(ev, now)
        return []


async def session(conn: Connection, changed: Optional[asyncio.Event] = None) -> None:
    """One WebSocket session of ``conn``: subscribe to the wanted entities (:func:`wanted_entities`) and
    emit until it breaks; ``changed`` set = the interest may have changed: re-subscribe after
    :data:`DEBOUNCE_SECONDS`. Returns when no entity is wanted any more.

    Raises:
        PermissionError: Home Assistant refused the token.
        Exception: the connection failed or Home Assistant refused the subscription.
    """
    user = conn.user
    t = tracker_for(conn)
    changed = changed if changed is not None else asyncio.Event()
    async with websockets.connect(client.ws_uri(conn), max_size=MAX_MESSAGE, open_timeout=OPEN_TIMEOUT) as ws:
        await asyncio.wait_for(client.authenticate(ws, conn), OPEN_TIMEOUT)
        sub = _Subscriber(ws, t)
        sub.states_id = await sub.send({"type": "get_states"})
        changed.clear()
        want = wanted_entities(user)
        if want is not None and not want:
            return
        await sub.follow(want)
        since: Optional[float] = None  # when the interest change being debounced arrived
        recv: "asyncio.Future[Any]" = asyncio.ensure_future(ws.recv())
        woke: "asyncio.Future[Any]" = asyncio.ensure_future(changed.wait())
        try:
            while True:
                timeout = FLUSH_SECONDS
                if since is not None:
                    timeout = max(0.0, min(timeout, since + DEBOUNCE_SECONDS - time.monotonic()))
                done, _ = await asyncio.wait({recv, woke}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if woke in done:
                    changed.clear()
                    woke = asyncio.ensure_future(changed.wait())
                    since = time.monotonic() if since is None else since
                if recv in done:
                    raw = recv.result()  # a closed socket raises here
                    recv = asyncio.ensure_future(ws.recv())
                    for msg in _messages(raw):
                        for data in await sub.handle(msg, time.monotonic()):
                            emit(user, data)
                for data in t.due(time.monotonic()):
                    emit(user, data)
                if since is not None and time.monotonic() - since >= DEBOUNCE_SECONDS:
                    since = None
                    want = wanted_entities(user)
                    if want is not None and not want:
                        return
                    if want != sub.following:
                        await sub.follow(want)
        finally:
            recv.cancel()
            woke.cancel()
            await asyncio.gather(recv, woke, return_exceptions=True)


async def idle(conn: Connection, changed: asyncio.Event) -> None:
    """No entity is wanted: no WebSocket; read the entity list (the known values) over REST when it is
    older than :data:`ENTITY_REFRESH_SECONDS`, then wait until the interest changes or it is due again."""
    t = tracker_for(conn)
    wait = ENTITY_REFRESH_SECONDS
    if time.monotonic() - t.read_at >= ENTITY_REFRESH_SECONDS:
        try:
            t.load(await client.fetch_states(conn))
            t.read_at = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            wait = 60.0
            logger.warning(f"[HA] entity list of user {conn.user.id}: {type(e).__name__}: {str(e)[:200]}")
    try:
        await asyncio.wait_for(changed.wait(), timeout=wait)
    except asyncio.TimeoutError:
        pass


async def follow(user: User, signature: Tuple[str, str, str], changed: Optional[asyncio.Event] = None) -> None:
    """Keep ``user``'s subscription for the connection ``signature`` names, reconnecting with backoff;
    ``changed`` is set when the interest changes. Returns once that connection is gone or changed."""
    changed = changed if changed is not None else asyncio.Event()
    backoff = BACKOFF_MIN
    while True:
        conn = store.get(user, ADDON)
        if conn is None or client.signature(conn) != signature:
            return
        changed.clear()
        want = wanted_entities(user)
        if want is not None and not want:
            await idle(conn, changed)
            continue
        started = time.monotonic()
        wait = None
        try:
            await session(conn, changed)
            continue  # no entity wanted any more
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
            _changed.pop(uid, None)
            if uid not in wanted:
                _trackers.pop(uid, None)
    for uid, (user, sig) in wanted.items():
        if uid not in _tasks:
            changed = _changed[uid] = asyncio.Event()
            _tasks[uid] = (sig, asyncio.get_running_loop().create_task(follow(user, sig, changed),
                                                                       name=f"ha-states-{uid}"))


async def stop_all() -> None:
    """Cancel every subscription."""
    tasks = [task for _, task in _tasks.values()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
    _changed.clear()


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


def _interest_changed(user_id: str, event_type: str) -> None:
    """``events.on_interest``: wake that user's task to re-subscribe (safe from any thread)."""
    changed, loop = _changed.get(user_id), _loop
    if event_type != EVENT or changed is None or loop is None or loop.is_closed():
        return
    try:
        running: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        changed.set()
    else:
        loop.call_soon_threadsafe(changed.set)


async def run(app: Any = None) -> None:
    """The service: keep one subscription per user connection until cancelled."""
    global _wake, _loop
    _wake, _loop = asyncio.Event(), asyncio.get_running_loop()
    events.on_interest(_interest_changed)
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
