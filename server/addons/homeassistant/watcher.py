"""Dana watches each user's Home Assistant entities itself: their location /
car signals from Home Assistant (docs/signals.md).

One watcher per user who has the addon connected and a person entity or a car
sensor (with the car's Bluetooth name) set, over that user's own WebSocket
connection, subscribed only to those entities (``subscribe_entities``). The
transitions:

- person entity, old state ≠ new state: old not in :data:`IGNORED_STATES` →
  left zone ``old``; new not in them → entered zone ``new`` (the zone is the
  person's state, normalised by the trigger engine: stripped, lower case);
- car sensor: the car's Bluetooth name in the ``connected_paired_devices``
  attribute (as a string); not connected → connected = car enter, connected →
  not connected = car leave.

The watcher remembers the last value it saw of each entity. The first value
after it starts is only recorded (nothing fires on the initial state); after
a reconnect the current value is compared with the last one seen, so a
transition missed while disconnected fires once (the trigger engine's
refire / flap marks still drop duplicates). It reconnects with back-off
forever; one user's failing connection never touches another's.
:class:`Watchers` keeps the set in line with the connections (connect,
settings change, disconnect): at once when the change is made in this
process (the addon's ``on_change`` → :func:`request_reconcile`), else within
:data:`RECONCILE_SECONDS` (a change made by ``bin/dana addons``). What the
self-checks need is written by :mod:`.status`.

The addon never imports the tasks addon: whoever starts :class:`Watchers`
gives it what to do with a transition; the addon's service (``hooks.py``)
delivers it as the ``location`` / ``car`` signal from the ``homeassistant``
source (core/signals.py), which emits ``core.signal.location`` /
``core.signal.car`` unless Dana's phone app is the active source; the
``tasks`` addon handles those.
"""

import asyncio
import json
import logging
import os
import weakref
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import websockets  # type: ignore

from core import users
from core.users import User
from core.addons import Connection, store
from addons.homeassistant import client as ha_api, status
from addons.homeassistant.config import CAR_DEVICE, CAR_SENSOR, ADDON, PERSON, setting

logger = logging.getLogger(__name__)

# Person states that are not a zone (the component's list, compared as is).
IGNORED_STATES = ("not_home", "unknown", "unavailable")
RETRY_MIN_SECONDS = 1.0
RETRY_MAX_SECONDS = float(os.getenv("HA_WATCH_RETRY_MAX_SECONDS", "300"))
RECONCILE_SECONDS = float(os.getenv("HA_WATCH_RECONCILE_SECONDS", "10"))

# fire(user, trigger_type, *, action=, target=): hooks.emit_transition (an event)
FireFn = Callable[..., Awaitable[Any]]


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


# The entity does not exist (removed, or not there when subscribing).
MISSING = _Missing()


@dataclass(frozen=True)
class WatchSpec:
    """What one user's watcher watches, and over which connection."""
    host: str
    token: str
    person: Optional[str]
    car_sensor: Optional[str]
    car_device: Optional[str]

    @property
    def entities(self) -> Dict[str, str]:
        """role (``person`` / ``car``) → entity id, only for roles that are fully set."""
        out: Dict[str, str] = {}
        if self.person:
            out["person"] = self.person
        if self.car_sensor and self.car_device:
            out["car"] = self.car_sensor
        return out


def spec_for(conn: Optional[Connection]) -> Optional[WatchSpec]:
    """``conn``'s watch spec; None when there is nothing to watch."""
    if conn is None:
        return None
    spec = WatchSpec(ha_api.host(conn), ha_api.token(conn), setting(conn, PERSON), setting(conn, CAR_SENSOR),
                     setting(conn, CAR_DEVICE))
    return spec if spec.entities else None


class UserWatcher:
    """One user's WebSocket subscription, reconnecting forever."""

    def __init__(self, conn: Connection, spec: WatchSpec, fire: FireFn, seen: Dict[str, Any]) -> None:
        self.conn = conn
        self.user: User = conn.user
        self.spec = spec
        self._fire = fire
        self._seen = seen  # entity id → last value seen (kept across restarts by Watchers)
        self._connected: Optional[bool] = None
        self._roles = {eid: role for role, eid in spec.entities.items()}

    # --- the connection --------------------------------------------------------------

    async def run(self) -> None:
        with users.acting_as(self.user):
            delay = RETRY_MIN_SECONDS
            while True:
                try:
                    await self._session()
                    error = "connection closed"
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    error = str(e).strip() or type(e).__name__
                if self._connected:
                    delay = RETRY_MIN_SECONDS  # it was up: start the back-off again
                self._set_connected(False, error)
                logger.warning(f"[HA watch] user {self.user.id}: {error}; reconnecting in {delay:.0f}s")
                await asyncio.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_SECONDS)

    def _set_connected(self, connected: bool, detail: str = "") -> None:
        if self._connected is connected:
            return
        self._connected = connected
        status.set_connected(self.user.id, connected, detail)

    async def _session(self) -> None:
        async with websockets.connect(ha_api.ws_uri(self.conn)) as ws:
            await ha_api.authenticate(ws, self.conn)
            await ws.send(json.dumps({"id": 1, "type": "subscribe_entities",
                                      "entity_ids": sorted(self._roles)}))
            states: Dict[str, Dict[str, Any]] = {}
            snapshot = True
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("id") != 1:
                    continue
                if msg.get("type") == "result":
                    if not msg.get("success"):
                        raise RuntimeError(f"subscribe_entities refused: {(msg.get('error') or {}).get('message')}")
                    self._set_connected(True)
                    logger.info(f"[HA watch] user {self.user.id}: watching {', '.join(sorted(self._roles))}")
                elif msg.get("type") == "event":
                    await self._on_event(msg.get("event") or {}, states, snapshot)
                    snapshot = False

    # --- state updates ---------------------------------------------------------------

    async def _on_event(self, event: Dict[str, Any], states: Dict[str, Dict[str, Any]], snapshot: bool) -> None:
        """One ``subscribe_entities`` message: ``a`` (added / initial), ``c``
        (changes as ``+`` / ``-`` diffs), ``r`` (removed)."""
        changed: List[str] = []
        for eid, st in (event.get("a") or {}).items():
            states[eid] = {"s": st.get("s"), "a": dict(st.get("a") or {})}
            changed.append(eid)
        for eid, diff in (event.get("c") or {}).items():
            cur = states.get(eid)
            if cur is None:
                continue
            plus, minus = diff.get("+") or {}, diff.get("-") or {}
            attrs = {**cur["a"], **(plus.get("a") or {})}
            for key in minus.get("a") or []:
                attrs.pop(key, None)
            states[eid] = {"s": plus.get("s", cur["s"]), "a": attrs}
            changed.append(eid)
        for eid in event.get("r") or []:
            states.pop(eid, None)
            changed.append(eid)
        if snapshot:
            # The first message is the current state of every entity that exists.
            for role, eid in self.spec.entities.items():
                status.set_entity(self.user.id, role, eid, eid in states)
            changed = list(self._roles)
        elif event.get("a") or event.get("r"):
            for eid in set(event.get("a") or {}) | set(event.get("r") or []):
                if eid in self._roles:
                    status.set_entity(self.user.id, self._roles[eid], eid, eid in states)
        for eid in changed:
            if eid in self._roles:
                await self._observe(self._roles[eid], eid, states.get(eid))

    def _value(self, role: str, state: Optional[Dict[str, Any]]) -> Any:
        if state is None:
            return MISSING
        if role == "person":
            return state.get("s")
        devices = str((state.get("a") or {}).get("connected_paired_devices") or "")
        return bool(self.spec.car_device) and str(self.spec.car_device) in devices

    async def _observe(self, role: str, entity_id: str, state: Optional[Dict[str, Any]]) -> None:
        new = self._value(role, state)
        if entity_id not in self._seen:
            self._seen[entity_id] = new  # the initial state: recorded, never fired
            return
        old = self._seen[entity_id]
        self._seen[entity_id] = new
        if old == new:
            return
        if new is not MISSING:
            status.mark_event(self.user.id, role, entity_id)
        for trigger_type, action, target in transitions(role, old, new):
            logger.info(f"[HA watch] user {self.user.id}: {trigger_type} {action} {target}".rstrip())
            try:
                await self._fire(self.user, trigger_type, action=action, target=target)
            except Exception as e:
                logger.error(f"[HA watch] firing {trigger_type} {action} for user {self.user.id} failed: {e}",
                             exc_info=True)


def transitions(role: str, old: Any, new: Any) -> List[Tuple[str, str, str]]:
    """The ``(trigger type, action, target)`` triggers one change of an
    entity's value fires, as the Home Assistant component sent them."""
    if old == new or new is MISSING:
        return []
    if role == "person":
        if old is MISSING:
            return []  # the entity appeared: no old state, nothing fired
        out: List[Tuple[str, str, str]] = []
        if old not in IGNORED_STATES:
            out.append(("location", "leave", str(old)))
        if new not in IGNORED_STATES:
            out.append(("location", "arrive", str(new)))
        return out
    was = old is not MISSING and bool(old)
    if new and not was:
        return [("car", "enter", "")]
    if was and not new:
        return [("car", "leave", "")]
    return []


_instances: "weakref.WeakSet[Watchers]" = weakref.WeakSet()


def request_reconcile(user: Optional[User] = None) -> None:
    """``Addon(on_change=)``: a user's Home Assistant connection changed
    (connected, settings or credentials updated, disconnected); every running
    :class:`Watchers` reconciles now instead of at its next tick. Safe from
    any thread and without a running loop (then nothing happens: the next
    tick sees it)."""
    for watchers in list(_instances):
        watchers.wake()


class Watchers:
    """Every user's watcher, kept in line with their connections."""

    def __init__(self, fire: FireFn) -> None:
        self._fire = fire
        self._running: Dict[str, Tuple[WatchSpec, UserWatcher, "asyncio.Task[None]"]] = {}
        self._seen: Dict[str, Dict[str, Any]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._wake: Optional[asyncio.Event] = None
        _instances.add(self)

    def wake(self) -> None:
        """Reconcile now (from :meth:`run_forever`'s loop)."""
        loop, event = self._loop, self._wake
        if loop is None or event is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(event.set)

    def running(self) -> Dict[str, WatchSpec]:
        """user id → the spec their watcher runs with."""
        return {uid: spec for uid, (spec, _, _) in self._running.items()}

    def reconcile(self) -> None:
        """Start, restart (settings or credentials changed) and stop watchers
        so there is exactly one per user with something to watch."""
        desired: Dict[str, Tuple[Connection, WatchSpec]] = {}
        for uid in store.users_with(ADDON):
            user = users.get(uid)
            try:
                conn = store.get(user, ADDON) if user is not None else None
            except Exception as e:
                logger.error(f"[HA watch] user {uid}: connection unreadable: {e}")
                continue
            spec = spec_for(conn)
            if conn is not None and spec is not None:
                desired[uid] = (conn, spec)
        for uid, (spec, _, task) in list(self._running.items()):
            if uid in desired and desired[uid][1] == spec:
                continue
            task.cancel()
            del self._running[uid]
            if uid not in desired:
                self._seen.pop(uid, None)
                status.forget(uid)
                logger.info(f"[HA watch] user {uid}: stopped")
        loop = asyncio.get_running_loop()
        for uid, (conn, spec) in desired.items():
            if uid in self._running:
                continue
            watcher = UserWatcher(conn, spec, self._fire, self._seen.setdefault(uid, {}))
            with users.acting_as(conn.user):
                task = loop.create_task(watcher.run(), name=f"ha-watch-{uid}")
            self._running[uid] = (spec, watcher, task)

    async def run_forever(self) -> None:
        """Reconcile now, every :data:`RECONCILE_SECONDS` and whenever
        :func:`request_reconcile` asks, until cancelled."""
        self._loop, self._wake = asyncio.get_running_loop(), asyncio.Event()
        try:
            while True:
                self._wake.clear()
                try:
                    self.reconcile()
                except Exception as e:
                    logger.error(f"[HA watch] reconcile failed: {e}", exc_info=True)
                try:
                    await asyncio.wait_for(self._wake.wait(), RECONCILE_SECONDS)
                except asyncio.TimeoutError:
                    pass
        finally:
            self._loop = self._wake = None
            await self.stop()

    async def stop(self) -> None:
        """Stop every watcher (marks and last values are kept)."""
        tasks = [task for _, _, task in self._running.values()]
        self._running.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
