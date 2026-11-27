"""Home Assistant state changes as watch events (``homeassistant.state.changed``, docs/watches.md).
Run: bin/test test_ha_state_events

Two in-process fake Home Assistants (tests/testfakes.py ``FakeHA``, which
pushes ``state_changed`` over its WebSocket): Bob has instance A, Maya
instance B, Carl none. Checked: the tracker alone (attribute-only and
unavailable / unknown changes skipped, 50 → unavailable → 45 is one change,
the numeric throttle: one event a minute unless the tens bucket changes,
a held value emitted when its time is up with the last reported old state);
the service (one subscription per connection, events with the declared
shape to the right user only, reconnecting after Home Assistant drops the
socket, stopped once the addon is disconnected); the consumers' interest
(``events.set_interest``): some entity ids → ``subscribe_entities`` for just
those on the same socket (the old subscription unsubscribed, quick changes
debounced into one, again after a reconnect, the compressed messages giving
the same event data), none → no socket (the entity list over REST), a Home
Assistant without ``subscribe_entities`` → ``state_changed`` filtered here,
None → every entity; the known values (the
phone's battery sensor first and labelled, each user's own entities only,
none before the first read) and the compiler's catalog showing them;
validation naming close matches for an entity id that is not one; the
dry run's history; and a watch compiled from "let me know when my phone
battery is under 50% and tell Gil" (a fake model) firing once per crossing
with its notify and agent actions. No network beyond localhost.
"""

import asyncio
import datetime as dt
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("ha_state_events_test")
os.environ["TIMEZONE"] = "Asia/Jerusalem"
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials

from core import clock, db, events, llm, notify, runs, session_runner, users

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()

from core.addons import registry, store  # noqa: E402
from addons.homeassistant import event_types, states  # noqa: E402
from addons.watches import actions, compiler, engine, service, store as wstore  # noqa: E402
from addons.watches import spec as S  # noqa: E402
from testfakes import FakeHA  # noqa: E402

failures = 0


def check(name: str, cond: Any, detail: Any = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


async def until(cond, timeout: float = 5.0) -> bool:
    for _ in range(int(timeout / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return bool(cond())


# --- Users and fakes -------------------------------------------------------------------------------

BOB = users.create("Bob Builder")
MAYA = users.create("Maya")
CARL = users.create("Carl")  # no Home Assistant

HA_A = FakeHA("A", "tok-a", {
    "sensor.phone_battery_level": ("80", {"friendly_name": "Bob phone battery", "unit_of_measurement": "%",
                                          "device_class": "battery"}),
    "binary_sensor.front_door": ("off", {"friendly_name": "Front door", "device_class": "door"}),
    "sensor.dishwasher_status": ("running", {"friendly_name": "Dishwasher"}),
    "light.a_kitchen": ("off", {"friendly_name": "A Kitchen"}),
}, {"light.a_kitchen": "A Kitchen"}, "Asia/Jerusalem")
HA_B = FakeHA("B", "tok-b", {
    "light.b_den": ("off", {"friendly_name": "B Den"}),
    "sensor.b_temperature": ("21", {"friendly_name": "Den temperature", "unit_of_measurement": "°C"}),
}, {"light.b_den": "B Den"}, "Europe/London")

SEEN: List[Tuple[str, Dict[str, Any]]] = []  # (user id, data) of every homeassistant.state.changed


async def collect(name: str, user: Any, data: Dict[str, Any]) -> None:
    if name == states.EVENT:
        SEEN.append((user.id, dict(data)))


events.tap(collect)


def seen(user: Any) -> List[Dict[str, Any]]:
    return [d for uid, d in SEEN if uid == user.id]


SENT: List[Tuple[str, notify.Message]] = []


async def fake_send(user: Any, message: notify.Message) -> Dict[str, bool]:
    SENT.append((user.id, message))
    return {"telegram": True}


notify.send = fake_send
AGENT_TEXTS: List[Tuple[str, str]] = []


def fake_start_agent_task(user: Any, session_id: str, user_text: str, **kw: Any) -> runs.Run:
    async def body() -> None:
        AGENT_TEXTS.append((user.id, user_text))
    return runs.runs.start(user, session_id, "message", body)


session_runner.start_agent_task = fake_start_agent_task
COMPILE_REPLIES: List[str] = []
COMPILE_CALLS: List[List[Dict[str, Any]]] = []


async def fake_complete(messages: List[Dict[str, Any]], *, purpose: str = "", **kwargs: Any) -> str:
    if purpose == "watch_compile":
        COMPILE_CALLS.append([dict(m) for m in messages])
        return COMPILE_REPLIES.pop(0)
    raise RuntimeError("offline in this test")


llm.complete = fake_complete


# --- 1. The tracker alone --------------------------------------------------------------------------

def st(state: str, **attrs: Any) -> Dict[str, Any]:
    return {"state": state, "attributes": attrs}


def test_tracker() -> None:
    t = states.Tracker(("u", "h", "t"))
    t.load([{"entity_id": "sensor.bat", "state": "80", "attributes": {"friendly_name": "Battery",
                                                                      "unit_of_measurement": "%",
                                                                      "device_class": "battery"}},
            {"entity_id": "binary_sensor.door", "state": "off", "attributes": {"device_class": "door"}},
            {"entity_id": "sensor.gone", "state": "unavailable", "attributes": {}}])
    battery = {"friendly_name": "Battery", "unit_of_measurement": "%", "device_class": "battery"}
    out = t.change("sensor.bat", st("80", **battery), st("79", **battery), 0.0)
    check("tracker: a state change → the event's data, in the declared shape", out == {
        "entity_id": "sensor.bat", "name": "Battery", "domain": "sensor", "old_state": "80", "new_state": "79",
        "unit": "%", "device_class": "battery"}, out)
    check("tracker: the declared fields are exactly the data's keys",
          set(out or {}) == set(event_types.HOMEASSISTANT_EVENTS[0].fields))
    check("tracker: an attribute-only update is no event",
          t.change("sensor.bat", st("79", **battery), st("79", friendly_name="Battery (renamed)"), 100.0) is None
          and t.entities["sensor.bat"].name == "Battery (renamed)")
    check("tracker: to unavailable / unknown is no event",
          t.change("binary_sensor.door", st("off"), st("unavailable"), 1.0) is None
          and t.change("binary_sensor.door", st("unavailable"), st("unknown"), 2.0) is None)
    check("tracker: back from unavailable unchanged is no event",
          t.change("binary_sensor.door", st("unknown"), st("off"), 3.0) is None)
    out = t.change("binary_sensor.door", st("off"), st("on", device_class="door"), 4.0)
    check("tracker: a non-numeric change emits at once", out and out["old_state"] == "off" and out["new_state"] == "on"
          and out["device_class"] == "door", out)
    out = t.change("binary_sensor.door", st("on"), st("off"), 4.5)
    check("tracker: non-numeric states are never throttled", out and out["new_state"] == "off", out)
    check("tracker: an entity first seen unavailable starts from its first good state",
          t.change("sensor.gone", st("unavailable"), st("5"), 5.0) is None and t.entities["sensor.gone"].reported == "5")

    # Throttle: bat last emitted at t=0 (80 → 79).
    t2 = states.Tracker(("u", "h", "t"))
    t2.load([{"entity_id": "sensor.bat", "state": "55", "attributes": battery}])
    first = t2.change("sensor.bat", st("55"), st("52"), 0.0)
    held = t2.change("sensor.bat", st("52"), st("51"), 10.0)
    crossed = t2.change("sensor.bat", st("51"), st("49"), 20.0)
    check("throttle: the first change emits; within 60 s in the same tens it is held; another tens emits at once "
          "with the last reported state as old_state",
          first and first["new_state"] == "52" and held is None
          and crossed and (crossed["old_state"], crossed["new_state"]) == ("52", "49"), (first, held, crossed))
    check("throttle: unavailable in between is skipped too (50 → unavailable → 45 is one change from the last reported)",
          t2.change("sensor.bat", st("49"), st("unavailable"), 25.0) is None)
    held = t2.change("sensor.bat", st("unavailable"), st("47"), 30.0)
    check("throttle: a held value is not emitted before its minute is up",
          held is None and t2.due(50.0) == [] and t2.entities["sensor.bat"].held == "47")
    due = t2.due(81.0)
    check("throttle: once its minute is up the held value is emitted from the last reported state",
          [(d["old_state"], d["new_state"]) for d in due] == [("49", "47")] and t2.due(200.0) == [], due)
    t2.change("sensor.bat", st("47"), st("46"), 90.0)
    check("throttle: a held value that goes back to the reported one is dropped",
          t2.change("sensor.bat", st("46"), st("47"), 95.0) is None and t2.due(500.0) == [])
    out = t2.change("sensor.bat", st("47"), st("44"), 200.0)
    check("throttle: after a minute any change emits", out and (out["old_state"], out["new_state"]) == ("47", "44"), out)
    check("tracker: the emitted events are its recent history", [d["new_state"] for _, d in t2.recent] == ["52", "49", "47", "44"])

    # subscribe_entities' compressed messages: the same events as state_changed.
    t3 = states.Tracker(("u", "h", "t"))
    out = t3.compressed({"a": {"sensor.bat": {"s": "80", "a": battery, "c": "x", "lc": 1.0},
                               "binary_sensor.door": {"s": "off", "a": {"device_class": "door"}}}}, 0.0)
    check("compressed: the first message (a) is read like get_states, no event",
          out == [] and t3.entities["sensor.bat"].reported == "80" and t3.entities["sensor.bat"].unit == "%")
    out = t3.compressed({"c": {"sensor.bat": {"+": {"s": "79", "lc": 2.0, "lu": 2.0}}}}, 1.0)
    check("compressed: a state change → the same data as state_changed gives", out == [{
        "entity_id": "sensor.bat", "name": "Battery", "domain": "sensor", "old_state": "80", "new_state": "79",
        "unit": "%", "device_class": "battery"}], out)
    out = t3.compressed({"c": {"binary_sensor.door": {"+": {"a": {"friendly_name": "Front door"}, "lu": 3.0}}}}, 2.0)
    check("compressed: an attribute-only change is no event, its attributes are kept",
          out == [] and t3.entities["binary_sensor.door"].name == "Front door")
    out = t3.compressed({"c": {"binary_sensor.door": {"+": {"s": "on", "lc": 4.0}, "-": {"a": ["device_class"]}}}}, 3.0)
    check("compressed: a removed attribute is empty in the event",
          [(d["old_state"], d["new_state"], d["device_class"], d["name"]) for d in out] == [("off", "on", "", "Front door")],
          out)
    check("compressed: to unavailable is no event, r forgets the entity",
          t3.compressed({"c": {"sensor.bat": {"+": {"s": "unavailable"}}}}, 4.0) == []
          and t3.compressed({"r": ["sensor.bat"]}, 5.0) == [] and "sensor.bat" not in t3.entities)


# --- 2. The service over the fake Home Assistants --------------------------------------------------

async def test_service() -> Optional["asyncio.Task[None]"]:
    states.RECONCILE_SECONDS = 0.2
    states.BACKOFF_MIN = 0.05
    states.FLUSH_SECONDS = 0.05
    await HA_A.start()
    await HA_B.start()
    store.connect(BOB, "homeassistant", credentials={"host": HA_A.url, "token": "tok-a"},
                  settings={"phone_battery_sensor": "sensor.phone_battery_level"})
    store.connect(MAYA, "homeassistant", credentials={"host": HA_B.url, "token": "tok-b"})
    for who in (BOB, MAYA):
        for name in ("watches", "tasks"):
            if not store.is_connected(who, name):
                store.connect(who, name)

    check("known values: none before Home Assistant was read (nothing is checked then)",
          event_types.known_values(BOB) == {} and event_types.known_values(CARL) == {})
    task = asyncio.get_running_loop().create_task(states.run())
    ok = await until(lambda: HA_A.subscribers and HA_B.subscribers and states.known_entities(BOB)
                     and states.known_entities(MAYA))
    check("service: one state_changed subscription per connection, each with its own token", ok
          and len(HA_A.subscribers) == 1 and len(HA_B.subscribers) == 1
          and HA_A.tokens_seen() == {"tok-a"} and HA_B.tokens_seen() == {"tok-b"}, (HA_A.requests, HA_B.requests))
    check("service: no subscription for the user without Home Assistant", CARL.id not in states._tasks)

    SEEN.clear()
    await HA_A.push("sensor.phone_battery_level", "79")
    await HA_B.push("light.b_den", "on")
    await until(lambda: seen(BOB) and seen(MAYA))
    await events.drain()
    check("service: Bob's change reaches Bob only, in the declared shape", seen(BOB) == [{
        "entity_id": "sensor.phone_battery_level", "name": "Bob phone battery", "domain": "sensor",
        "old_state": "80", "new_state": "79", "unit": "%", "device_class": "battery"}], seen(BOB))
    check("service: Maya's change reaches Maya only",
          [(d["entity_id"], d["old_state"], d["new_state"]) for d in seen(MAYA)] == [("light.b_den", "off", "on")],
          seen(MAYA))

    SEEN.clear()
    await HA_A.push("binary_sensor.front_door", "off", {"friendly_name": "Front door", "device_class": "door",
                                                        "last_tripped": "now"})
    await HA_A.push("light.a_kitchen", "unavailable")
    await HA_A.push("light.a_kitchen", "off")
    await HA_A.push("sensor.dishwasher_status", "finished")
    await until(lambda: seen(BOB))
    await asyncio.sleep(0.1)
    await events.drain()
    check("service: attribute-only and unavailable round trips are skipped, a real change is not",
          [(d["entity_id"], d["new_state"]) for d in seen(BOB)] == [("sensor.dishwasher_status", "finished")], seen(BOB))

    vals = event_types.known_values(BOB)["entity_id"]
    check("known values: the phone battery sensor first, labelled as the user's phone battery",
          vals[0] == "sensor.phone_battery_level (Bob phone battery, battery, the user's phone battery)", vals)
    check("known values: the home map's and dashboards' entities next, then the rest; Bob's own only",
          {S.value_of(v) for v in vals} == set(HA_A.states) and not any("b_den" in v for v in vals), vals)
    check("known values: Maya's are hers", {S.value_of(v) for v in event_types.known_values(MAYA)["entity_id"]}
          == set(HA_B.states))
    check("known values: none for Carl", event_types.known_values(CARL) == {})

    await HA_A.drop_sockets()
    ok = await until(lambda: HA_A.subscribers, timeout=5)
    SEEN.clear()
    await HA_A.push("binary_sensor.front_door", "on")
    await until(lambda: seen(BOB))
    check("service: after Home Assistant drops the socket it subscribes again and keeps emitting",
          ok and [(d["entity_id"], d["new_state"]) for d in seen(BOB)] == [("binary_sensor.front_door", "on")],
          seen(BOB))
    await HA_A.push("binary_sensor.front_door", "off")
    return task


def ws_count(ha: FakeHA) -> int:
    return sum(1 for path, _ in ha.requests if path == "/api/websocket")


def sent(ha: FakeHA, kind: str) -> List[Dict[str, Any]]:
    return [c for c in ha.commands if c.get("type") == kind]


async def test_interest() -> None:
    """The consumers' interest (events.set_interest, set by the watches addon) narrows the subscription."""
    states.DEBOUNCE_SECONDS = 0.05
    battery, door = "sensor.phone_battery_level", "binary_sensor.front_door"
    check("interest: None (nothing said) = state_changed for every entity",
          HA_A.subscriptions() == [None] and HA_B.subscriptions() == [None], (HA_A.subscriptions(), HA_B.subscriptions()))

    sockets, old_id = ws_count(HA_A), HA_A.subscribers[0][1]
    HA_A.commands.clear()
    events.set_interest(BOB.id, states.EVENT, "entity_id", [door])
    events.set_interest(BOB.id, states.EVENT, "entity_id", [battery, "not an id"])
    events.set_interest(BOB.id, states.EVENT, "entity_id", [battery, door])
    ok = await until(lambda: HA_A.subscriptions() == [{battery, door}])
    await asyncio.sleep(0.2)
    check("interest: some entity ids → subscribe_entities for just those, on the same socket, the old one unsubscribed",
          ok and ws_count(HA_A) == sockets and sent(HA_A, "unsubscribe_events") == [
              {"id": sent(HA_A, "unsubscribe_events")[0]["id"], "type": "unsubscribe_events", "subscription": old_id}],
          (HA_A.commands, HA_A.subscriptions()))
    check("interest: three changes in a row → one re-subscribe (debounced)",
          [c["entity_ids"] for c in sent(HA_A, "subscribe_entities")] == [sorted([battery, door])], HA_A.commands)

    SEEN.clear()
    await HA_A.push("sensor.dishwasher_status", "running")      # not wanted: Home Assistant does not send it
    await HA_A.push(door, "off", {"friendly_name": "Front door", "device_class": "door", "x": 1})  # attributes only
    await HA_A.push(battery, "69")  # another tens: not held by the throttle
    await until(lambda: seen(BOB))
    await asyncio.sleep(0.1)
    await events.drain()
    check("interest: only the wanted entities' changes, in the declared shape", seen(BOB) == [{
        "entity_id": battery, "name": "Bob phone battery", "domain": "sensor", "old_state": "79", "new_state": "69",
        "unit": "%", "device_class": "battery"}], seen(BOB))

    HA_A.commands.clear()
    events.set_interest(BOB.id, states.EVENT, "entity_id", ["sensor.dishwasher_status"])
    ok = await until(lambda: HA_A.subscriptions() == [{"sensor.dishwasher_status"}])
    SEEN.clear()
    await HA_A.push("sensor.dishwasher_status", "finished")
    await HA_A.push(door, "on")
    await until(lambda: seen(BOB))
    await asyncio.sleep(0.1)
    await events.drain()
    check("interest: a change of interest re-subscribes to the new entities",
          ok and len(sent(HA_A, "unsubscribe_events")) == 1 and ws_count(HA_A) == sockets
          and [(d["entity_id"], d["old_state"], d["new_state"]) for d in seen(BOB)]
          == [("sensor.dishwasher_status", "running", "finished")], (seen(BOB), HA_A.commands))
    await HA_A.push(door, "off")

    await HA_A.drop_sockets()
    ok = await until(lambda: HA_A.subscriptions() == [{"sensor.dishwasher_status"}])
    check("interest: after Home Assistant drops the socket it subscribes to the same entities again", ok,
          HA_A.subscriptions())

    # Maya: nothing wanted → no WebSocket at all; the entity list is still read (over REST).
    states._trackers[MAYA.id].read_at = float("-inf")
    rest_before = sum(1 for path, _ in HA_B.requests if path == "/api/states")
    events.set_interest(MAYA.id, states.EVENT, "entity_id", [])
    ok = await until(lambda: not HA_B.subscribers)
    sockets_b = ws_count(HA_B)
    await until(lambda: sum(1 for path, _ in HA_B.requests if path == "/api/states") > rest_before)
    SEEN.clear()
    await HA_B.push("light.b_den", "off")
    await asyncio.sleep(0.3)
    check("interest: none wanted → no subscription and no socket, the entity list read over REST instead",
          ok and not seen(MAYA) and ws_count(HA_B) == sockets_b and states.known_entities(MAYA)
          and sum(1 for path, _ in HA_B.requests if path == "/api/states") > rest_before, HA_B.requests[-5:])

    HA_B.refuse_subscribe_entities = True
    events.set_interest(MAYA.id, states.EVENT, "entity_id", ["light.b_den"])
    ok = await until(lambda: HA_B.subscriptions() == [None])
    SEEN.clear()
    await HA_B.push("sensor.b_temperature", "35")
    await HA_B.push("light.b_den", "on")
    await until(lambda: seen(MAYA))
    await asyncio.sleep(0.1)
    await events.drain()
    check("interest: a Home Assistant without subscribe_entities → state_changed, the others dropped here",
          ok and [(d["entity_id"], d["new_state"]) for d in seen(MAYA)] == [("light.b_den", "on")], seen(MAYA))
    HA_B.refuse_subscribe_entities = False

    events.set_interest(MAYA.id, states.EVENT, "entity_id", None)
    events.set_interest(BOB.id, states.EVENT, "entity_id", None)
    ok = await until(lambda: HA_A.subscriptions() == [None] and HA_B.subscriptions() == [None])
    SEEN.clear()
    await HA_A.push("sensor.dishwasher_status", "running")
    await until(lambda: seen(BOB))
    check("interest: None again → every entity again", ok and [d["entity_id"] for d in seen(BOB)]
          == ["sensor.dishwasher_status"], (HA_A.subscriptions(), HA_B.subscriptions(), seen(BOB)))


async def test_disconnect() -> None:
    store.disconnect(MAYA, "homeassistant")
    ok = await until(lambda: not HA_B.subscribers and MAYA.id not in states._tasks)
    check("service: disconnecting the addon stops that user's subscription and forgets their entities",
          ok and states.known_entities(MAYA) == {} and event_types.known_values(MAYA) == {})
    SEEN.clear()
    await HA_B.push("light.b_den", "off")
    await asyncio.sleep(0.2)
    check("service: no events for a disconnected user", not seen(MAYA))


# --- 3. Catalog, compiler, validation, end to end --------------------------------------------------

BATTERY_SPEC = {
    "title": "Phone battery under 50%", "kind": "watch", "repeat": "each", "cooldown_minutes": 0,
    "triggers": [{"id": "low", "kind": "event", "event_type": "homeassistant.state.changed",
                  "where": [{"field": "entity_id", "op": "eq", "value": "sensor.phone_battery_level"},
                            {"field": "old_state", "op": "gte", "value": "50"},
                            {"field": "new_state", "op": "lt", "value": "50"}]}],
    "on_match": [{"type": "notify", "text": "Your phone battery is under 50%"},
                 {"type": "agent", "instructions": "Tell Gil on WhatsApp that my phone battery is low",
                  "autonomy": "act_and_tell"}]}


async def test_watch_end_to_end() -> None:
    engine.forget_caches()
    types = {e.name: e for e in registry.event_types(BOB)}
    check("catalog: homeassistant.state.changed for Bob (the addon is active), not for Carl",
          "homeassistant.state.changed" in types
          and "homeassistant.state.changed" not in {e.name for e in registry.event_types(CARL)})
    known = S.known_values(types.values(), BOB)
    wrong = json.loads(json.dumps(BATTERY_SPEC))
    wrong["triggers"][0]["where"][0]["value"] = "sensor.phone_battery"
    _, errs = S.validate(wrong, clock.now(), types, None, known)
    check("validation: an entity id that is not one → a path-shaped error naming close matches",
          len(errs) == 1 and errs[0].startswith("triggers[0].where[0].value: 'sensor.phone_battery' is not one")
          and "sensor.phone_battery_level" in errs[0], errs)
    inlist = json.loads(json.dumps(BATTERY_SPEC))
    inlist["triggers"][0]["where"][0] = {"field": "entity_id", "op": "in",
                                         "value": "binary_sensor.front_door|binary_sensor.back_door"}
    _, errs = S.validate(inlist, clock.now(), types, None, known)
    check("validation: each value of an `in` is checked", len(errs) == 1 and "binary_sensor.back_door" in errs[0]
          and "binary_sensor.front_door" in errs[0].split("close:")[-1], errs)
    _, errs = S.validate(wrong, clock.now(), types, None, None)
    check("validation: without known values nothing is checked", errs == [], errs)
    _, errs = S.validate(BATTERY_SPEC, clock.now(), types, None, known)
    check("validation: a real entity id passes", errs == [], errs)

    COMPILE_CALLS.clear()
    COMPILE_REPLIES[:] = [json.dumps(wrong), json.dumps(BATTERY_SPEC)]
    with users.acting_as(BOB):
        row, compiled = await service.create_from_text(BOB, "let me know when my phone battery is under 50% and tell Gil")
    sysmsg = COMPILE_CALLS[0][0]["content"]
    check("compiler: the catalog shows the known entity ids, the phone battery first and labelled",
          '"known_values": {"entity_id": ["sensor.phone_battery_level (Bob phone battery, battery, the user\'s phone '
          'battery)"' in sysmsg and "b_den" not in sysmsg, sysmsg[:400])
    check("compiler: the fields teach the crossing (old_state gte AND new_state lt)",
          "old_state gte 50 AND new_state lt 50" in sysmsg)
    check("compiler: a made-up entity id is sent back with the close match, then accepted",
          compiled.attempts == 2 and "sensor.phone_battery_level" in COMPILE_CALLS[1][-1]["content"]
          and row["spec"]["triggers"][0]["where"][0]["value"] == "sensor.phone_battery_level",
          COMPILE_CALLS[-1][-1]["content"][:300])

    SENT.clear()
    AGENT_TEXTS.clear()
    states.THROTTLE_SECONDS = 60.0
    for value in ("52", "49"):  # 79 → 52 and 52 → 49: each another tens, so neither is held
        await HA_A.push("sensor.phone_battery_level", value)
        await asyncio.sleep(0.05)
    await until(lambda: wstore.fires(BOB, row["id"], 10))
    await events.drain()
    await actions.drain()
    fires = wstore.fires(BOB, row["id"], 10)
    check("end to end: the crossing 52 → 49 fires the watch once with the event's data", len(fires) == 1
          and fires[0]["event"]["old_state"] == "52" and fires[0]["event"]["new_state"] == "49"
          and fires[0]["event_type"] == "homeassistant.state.changed", fires)
    told = [m.text for uid, m in SENT if uid == BOB.id and m.kind != "live"]
    check("end to end: the notify action told Bob, once, naming the entity and the change",
          len(told) == 1 and "Your phone battery is under 50%" in told[0] and "Bob phone battery · 52 → 49" in told[0]
          and not [uid for uid, _ in SENT if uid != BOB.id], SENT)
    await until(lambda: AGENT_TEXTS)
    check("end to end: the agent action got the instructions to tell Gil and the event",
          AGENT_TEXTS and AGENT_TEXTS[0][0] == BOB.id and "Tell Gil" in AGENT_TEXTS[0][1]
          and "sensor.phone_battery_level" in AGENT_TEXTS[0][1], AGENT_TEXTS)

    await HA_A.push("sensor.phone_battery_level", "48")  # held by the throttle; below 50 already: no crossing
    states.THROTTLE_SECONDS = 0.0
    await HA_A.push("sensor.phone_battery_level", "47")
    await asyncio.sleep(0.2)
    await events.drain()
    await actions.drain()
    check("end to end: staying under 50 does not fire again", len(wstore.fires(BOB, row["id"], 10)) == 1,
          wstore.fires(BOB, row["id"], 10))
    for value in ("60", "45"):
        await HA_A.push("sensor.phone_battery_level", value)
        await asyncio.sleep(0.05)
    await until(lambda: len(wstore.fires(BOB, row["id"], 10)) == 2)
    await events.drain()
    await actions.drain()
    check("end to end: the next crossing (60 → 45) fires again", len(wstore.fires(BOB, row["id"], 10)) == 2)

    hist = engine.history(BOB, only={"homeassistant.state.changed"})["homeassistant.state.changed"]
    dry = S.dry_run(row["spec"], {"homeassistant.state.changed": hist})
    check("dry run: the history is the changes Dana saw, and the trigger matches its crossings",
          dry["low"]["matches"] == 2 and [s["data"]["new_state"] for s in dry["low"]["sample"]] == ["49", "45"], dry)
    check("dry run: nothing of Maya's in Bob's history", all(d["entity_id"] in HA_A.states for _, d in
                                                             states.history(BOB, clock.now() - dt.timedelta(days=1))))
    check("Carl has no history", states.history(CARL, clock.now() - dt.timedelta(days=1)) == [])


async def main() -> None:
    test_tracker()
    task = await test_service()
    try:
        await test_interest()
        await test_watch_end_to_end()
        await test_disconnect()
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await events.drain()
        await actions.drain()
        check("service: cancelling it closes every subscription",
              not states._tasks and await until(lambda: not HA_A.subscribers))
        await HA_A.server.close()
        await HA_B.server.close()


asyncio.run(main())
print(f"\n{'All passed' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
