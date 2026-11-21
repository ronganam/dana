"""Home Assistant per user (docs/access.md, "Addons", "Home Assistant triggers").
Run: bin/test test_homeassistant_users

Two in-process fake Home Assistants (aiohttp: REST states / services /
history / config / camera, and the WebSocket auth, entity registry and
``subscribe_entities`` messages), each with its own token and entities. The
first admin's addon comes from the env import (the car entities set
after it) and is then pointed at instance A; Bob has instance B; Carl has no
Home Assistant. Checked: tools, proxies, zones, the home map, the clock's
zone, weather and wake reads hit only the user's own instance with their own
token; the user without the addon gets 403 / no tools / no map / no wake; the
entity watcher fires the right user's location / car triggers (targets as the
old webhook produced them), ignores ``not_home`` / ``unknown`` /
``unavailable`` as the old component did, never fires on the initial state,
fires a missed transition once after a reconnect (a duplicate does not
refire), re-subscribes on a settings change, stops on disconnect, keeps going
for one user while the other's Home Assistant is down; the self-check items;
``/webhook`` is gone while ``/webhook/waha`` still accepts an internal host;
nothing outside the env import reads the old env names. Phone push / Telegram
are mocked; no network beyond localhost.
"""

import asyncio
import datetime
import json
import os
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("homeassistant_users_test")
os.environ["API_TOKEN"] = "ha-users-test-env-token"  # a test value: with it set, DB tokens are honoured
os.environ["INTERNAL_HOSTS"] = "ganam-assistant.ganam-assistant"
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials
# The first admin's env values, as production has them (fake).
os.environ["HOMEASSISTANT_TOKEN"] = "env-token"
os.environ["HOMEASSISTANT_HOST"] = "http://env-ha.invalid"
os.environ["PRIMARY_PERSON"] = "person.robin"
os.environ["PHONE_INTERACTIVE_SENSOR"] = "binary_sensor.pixel_8_interactive"
os.environ["PHONE_BATTERY_SENSOR"] = "sensor.pixel_8_battery_level"
os.environ["PHONE_NOTIFY_SERVICE"] = "mobile_app_pixel_8"

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users
from addons.tasks.groups import ensure_default_groups_for_everyone

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()
ensure_default_groups_for_everyone()

from addons.cameras import api as cameras_api  # noqa: E402
from addons.homeassistant import api as homeassistant_api  # noqa: E402
from addons.whatsapp import api as whatsapp_api  # noqa: E402
from core import briefing, checks, clock, notify as core_notify, now as now_mod, wake  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.addons import registry, scheduler, store  # noqa: E402
from addons.homeassistant import client as ha_client, home_map, status as ha_status  # noqa: E402
from addons.homeassistant import watcher as ha_watcher  # noqa: E402
from addons.tasks import service as tasks_service, tools as tasks_tools, triggers  # noqa: E402
from testfakes import FakeHA  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


async def until(cond, timeout: float = 5.0) -> bool:
    for _ in range(int(timeout / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return bool(cond())


# --- A fake Home Assistant ---------------------------------------------------------------------

HA_A = FakeHA("A", "tok-a", {
    "person.robin": ("home", {"friendly_name": "Robin"}),
    "sensor.pixel_8_bluetooth_connection": ("1", {"connected_paired_devices": "[]"}),
    "zone.home": ("0", {"friendly_name": "Home"}),
    "zone.work": ("0", {"friendly_name": "Work"}),
    "zone.gym": ("0", {"friendly_name": "Gym"}),
    "light.a_kitchen": ("on", {"friendly_name": "A Kitchen"}),
    "weather.forecast_home": ("sunny", {"temperature": 21, "temperature_unit": "°C"}),
}, {"light.a_kitchen": "A Kitchen"}, "Asia/Jerusalem")
HA_B = FakeHA("B", "tok-b", {
    "person.bob": ("home", {"friendly_name": "Bob"}),
    "sensor.b_bt": ("0", {"connected_paired_devices": []}),
    "zone.home": ("0", {"friendly_name": "Home"}),
    "zone.work": ("0", {"friendly_name": "Work"}),
    "zone.bobplace": ("0", {"friendly_name": "BobPlace"}),
    "light.b_den": ("off", {"friendly_name": "B Den"}),
    "weather.b": ("rainy", {"temperature": 31, "temperature_unit": "°C"}),
}, {"light.b_den": "B Den"}, "Europe/London")

ADMIN = users.first_admin()
BOB = users.create("Bob Builder")
CARL = users.create("Carl")  # no Home Assistant
BOB_TOKEN, _ = users.issue_token(BOB.id, "test bob")
CARL_TOKEN, _ = users.issue_token(CARL.id, "test carl")
ADMIN_AUTH = {"Authorization": "Bearer ha-users-test-env-token"}
BOB_AUTH = {"Authorization": f"Bearer {BOB_TOKEN}"}
CARL_AUTH = {"Authorization": f"Bearer {CARL_TOKEN}"}


def clear() -> None:
    for ha in (HA_A, HA_B):
        ha.requests.clear()
        ha.service_calls.clear()


async def tool(user, name: str, **args) -> Any:
    with users.acting_as(user):
        return await registry.execute(user, name, args)


# --- 1. The env import and the old env names ---------------------------------------------------

def test_import() -> None:
    ha = store.require(ADMIN, "homeassistant")
    check("import: host / token from env", dict(ha.credentials) == {"host": "http://env-ha.invalid", "token": "env-token"},
          dict(ha.credentials))
    check("import: person and phone entities from env, no car entities (no env names)",
          ha.settings["person"] == "person.robin"
          and ha.settings["car_sensor"] is None
          and ha.settings["car_device"] is None
          and ha.settings["phone_interactive_sensor"] == "binary_sensor.pixel_8_interactive"
          and ha.settings["phone_battery_sensor"] == "sensor.pixel_8_battery_level"
          and ha.settings["phone_notify_service"] == "mobile_app_pixel_8"
          and ha.settings["weather_entity"] == "weather.forecast_home", dict(ha.settings))
    old_names = ("HOMEASSISTANT_HOST", "HOMEASSISTANT_TOKEN", "HOMEASSISTANT_TODO_ENTITY_ID", "PRIMARY_PERSON",
                 "PHONE_INTERACTIVE_SENSOR", "PHONE_BATTERY_SENSOR", "PHONE_NOTIFY_SERVICE", "BRIEFING_WEATHER_ENTITY")
    root = testenv.SERVER
    readers = []
    for path in list((root / "core").rglob("*.py")) + [p for p in (root / "addons").rglob("*.py") if "tests" not in p.parts] + [root / "main.py"]:
        if path.name == "bootstrap.py" and path.parent.name == "addons":
            continue
        text = path.read_text(encoding="utf-8")
        readers += [f"{path.relative_to(root)}: {n}" for n in old_names if re.search(rf'["\']{n}["\']', text)]
    check("old env names: read only by the env import", not readers, readers)
    check("no owner-specific defaults in the addon (entity settings default to nothing)",
          all(f.default is None for f in store.get_addon("homeassistant").settings if f.name != "weather_entity"))
    routes = "".join(p.read_text(encoding="utf-8") for p in
                     list((root / "core").glob("*.py")) + list((root / "addons").rglob("api.py")))
    check("/webhook route is gone (only /webhook/waha)", 'add_post("/webhook",' not in routes
          and 'add_post("/webhook/waha"' in routes)


# --- 2. Connections ------------------------------------------------------------------------------

def connect_users() -> None:
    store.update(ADMIN, "homeassistant", credentials={"host": HA_A.url, "token": "tok-a"},
                 settings={"car_sensor": "sensor.pixel_8_bluetooth_connection", "car_device": "CarKit-1234"})
    store.connect(BOB, "homeassistant", credentials={"host": HA_B.url, "token": "tok-b"},
                  settings={"person": "person.bob", "car_sensor": "sensor.b_bt", "car_device": "CarB",
                            "phone_interactive_sensor": "binary_sensor.b_screen", "weather_entity": "weather.b",
                            "phone_battery_sensor": "sensor.b_battery"})
    store.connect(BOB, "tasks")
    store.connect(BOB, "cameras", settings={"url": "http://frigate-b.invalid"})  # the HA camera proxy needs both kinds


# --- 3. Tools, zones, home map, clock, weather, wake ------------------------------------------

async def test_reads() -> None:
    clear()
    out_a = await tool(ADMIN, "get_ha_zones")
    check("tool get_ha_zones: the admin's zones from A with A's token",
          "Gym" in out_a and "BobPlace" not in out_a and HA_A.tokens_seen() == {"tok-a"} and not HA_B.requests, out_a)
    clear()
    out_b = await tool(BOB, "get_ha_zones")
    check("tool get_ha_zones: Bob's zones from B with B's token",
          "BobPlace" in out_b and "Gym" not in out_b and HA_B.tokens_seen() == {"tok-b"} and not HA_A.requests, out_b)
    clear()
    await tool(BOB, "call_ha_service", domain="light", service="turn_on", entity_id="light.b_den")
    check("tool call_ha_service: only on Bob's instance",
          HA_B.service_calls == [("light.turn_on", {"entity_id": "light.b_den"})] and not HA_A.service_calls)
    clear()
    out = await tool(ADMIN, "get_ha_exposed_entities")
    check("tool get_ha_exposed_entities: the admin's entities over A's WebSocket",
          "light.a_kitchen" in out and "light.b_den" not in out and HA_A.tokens_seen() == {"tok-a"}
          and not HA_B.requests, out)
    try:
        await tool(CARL, "get_ha_zones")
        refused = False
    except registry.ToolNotAvailable:
        refused = True
    names = {s["function"]["name"] for s in registry.tool_schemas(CARL)}
    check("no addon: no Home Assistant tools, a call refused like an unknown tool",
          refused and not names & {"get_ha_zones", "call_ha_service", "get_ha_exposed_entities", "control_android_tv"})

    # Location triggers validate against the user's own zones.
    check("task location: Bob's zone is valid for Bob",
          await tasks_tools.validate_ha_zone(BOB, "bobplace") == "BobPlace")
    try:
        await tasks_tools.validate_ha_zone(ADMIN, "BobPlace")
        msg = ""
    except ValueError as e:
        msg = str(e)
    check("task location: Bob's zone is not valid for the admin (lists the admin's zones)",
          "Gym" in msg and "BobPlace" in msg.split("Available")[0], msg)

    # Home map per connection, in the right prompt only.
    home_map._caches.clear()
    out = await scheduler.run_once(registry.job("homeassistant.warm_home_map"))
    sec_a, sec_b = registry.prompt_section(ADMIN, "homeassistant"), registry.prompt_section(BOB, "homeassistant")
    check("home map: one warm-up per user with the addon", sorted(out) == sorted([ADMIN.id, BOB.id]), out)
    check("home map: each user's prompt has their own map only",
          "light.a_kitchen | A Kitchen" in sec_a and "light.b_den" not in sec_a
          and "light.b_den | B Den" in sec_b and "light.a_kitchen" not in sec_b, (sec_a, sec_b))
    check("home map: none for the user without the addon",
          registry.prompt_section(CARL, "homeassistant") == "" and home_map.cached_text(CARL) == "")

    # The server's timezone: the first admin's Home Assistant.
    saved = clock._timezone
    clock._timezone = None
    os.environ.pop("TIMEZONE", None)
    try:
        clear()
        tz = await asyncio.to_thread(clock.get_timezone)
        check("clock: the zone comes from the first admin's Home Assistant",
              str(tz) == "Asia/Jerusalem" and HA_A.tokens_seen() == {"tok-a"} and not HA_B.requests, str(tz))
    finally:
        clock._timezone = saved

    # Weather (briefing / Now) from the user's own weather entity.
    clear()
    with users.acting_as(BOB):
        w = await briefing.weather(BOB)
    check("weather: Bob's entity on B", w and w["condition"] == "rainy" and w["high"] == 30
          and HA_B.tokens_seen() == {"tok-b"} and not HA_A.requests, w)
    with users.acting_as(CARL):
        check("weather: nothing without the addon", await briefing.weather(CARL) is None and now_mod.weather_cached(CARL) is None)

    # Wake reads Bob's screen sensor from B; Carl is skipped.
    clear()
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)
    HA_B.history = [{"state": "on", "last_changed": since.isoformat()}]
    sessions = await wake._screen_sessions(BOB, since, datetime.datetime.now(datetime.timezone.utc))
    check("wake: Bob's screen history from B, his own sensor",
          sessions and sessions[0] > 500 and HA_B.service_calls == [("history", {"entity": "binary_sensor.b_screen"})]
          and not HA_A.requests, (sessions, HA_B.service_calls))
    check("wake: a user without Home Assistant is skipped",
          await wake.handle_wake(CARL) == "unavailable" and not await wake.check_wake_fallback(CARL))


# --- 4. HTTP endpoints ---------------------------------------------------------------------------

def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    r = app.router
    r.add_get("/api/zones", homeassistant_api.get_zones_api)
    r.add_get("/api/client-config", homeassistant_api.client_config_api)
    r.add_get("/api/ha/state/{entity_id}", homeassistant_api.ha_entity_state_api)
    r.add_get("/api/ha/states", homeassistant_api.ha_states_api)
    r.add_post("/api/ha/service", homeassistant_api.ha_call_service_api)
    r.add_get("/api/camera/{entity_id}/proxy", cameras_api.ha_camera_proxy_api)
    r.add_post("/webhook/waha", whatsapp_api.handle_waha_webhook)
    return app


async def test_http() -> None:
    async with TestClient(TestServer(build_app())) as client:
        clear()
        r = await client.get("/api/client-config", headers=ADMIN_AUTH)
        check("client-config: the first admin's response is what it was, byte for byte",
              await r.text() == json.dumps({"notify_service": "mobile_app_pixel_8",
                                            "interactive_sensor": "binary_sensor.pixel_8_interactive",
                                            "battery_sensor": "sensor.pixel_8_battery_level",
                                            "person": "person.robin"}), await r.text())
        r = await client.get("/api/client-config", headers=BOB_AUTH)
        body = await r.json()
        check("client-config: Bob's own entities", body["person"] == "person.bob"
              and body["battery_sensor"] == "sensor.b_battery" and body["notify_service"] is None, body)

        r = await client.get("/api/zones", headers=BOB_AUTH)
        body = await r.json()
        check("zones: Bob's from B", r.status == 200 and {z["name"] for z in body} == {"Home", "Work", "BobPlace"}
              and not HA_A.requests, body)
        clear()
        r = await client.get("/api/ha/state/person.robin", headers=ADMIN_AUTH)
        check("ha/state: the admin's entity from A", (await r.json())["state"] == "home"
              and HA_A.tokens_seen() == {"tok-a"} and not HA_B.requests)
        r = await client.get("/api/ha/state/person.robin", headers=BOB_AUTH)
        check("ha/state: not in Bob's Home Assistant → 502 (B answered 404)", r.status == 502)
        # The entity state proxy (the dashboard's tiles) is not a camera route:
        # Home Assistant alone is enough.
        store.disconnect(BOB, "cameras")
        r = await client.get("/api/ha/state/light.b_den", headers=BOB_AUTH)
        check("ha/state: Home Assistant without cameras is enough (200, Bob's entity)",
              r.status == 200 and (await r.json())["state"] == "off", r.status)
        store.connect(BOB, "cameras", settings={"url": "http://frigate-b.invalid"})
        clear()
        r = await client.get("/api/ha/states?ids=light.b_den,light.a_kitchen", headers=BOB_AUTH)
        body = await r.json()
        check("ha/states: Bob's states only (A's light unavailable to him)",
              body[0]["state"] == "off" and body[1]["state"] == "unavailable" and not HA_A.requests, body)
        r = await client.get("/api/ha/states?ids=light.a_kitchen", headers=ADMIN_AUTH)
        check("ha/states: the cache is per connection (the admin sees A)", (await r.json())[0]["state"] == "on")
        clear()
        r = await client.post("/api/ha/service", headers=ADMIN_AUTH,
                              json={"domain": "light", "service": "turn_off", "entity_id": "light.a_kitchen"})
        check("ha/service: on the admin's instance only", r.status == 200
              and HA_A.service_calls == [("light.turn_off", {"entity_id": "light.a_kitchen"})] and not HA_B.service_calls)
        r = await client.get("/api/camera/camera.x/proxy", headers=BOB_AUTH)
        check("camera proxy: through Bob's Home Assistant", await r.read() == b"jpeg-B")
        for path in ("/api/zones", "/api/client-config", "/api/ha/state/light.b_den", "/api/ha/states?ids=x",
                     "/api/camera/camera.x/proxy"):
            r = await client.get(path, headers=CARL_AUTH)
            body = await r.json()
            check(f"no addon: GET {path.split('?')[0]} → 403 not_connected", r.status == 403
                  and body == {"error": "not_connected", "addon": "homeassistant"}, (r.status, body))
        r = await client.post("/api/ha/service", headers=CARL_AUTH,
                              json={"domain": "light", "service": "turn_on", "entity_id": "light.b_den"})
        check("no addon: POST /api/ha/service → 403, nothing called", r.status == 403 and not HA_B.service_calls[1:])

        # The old Home Assistant webhook is gone; WAHA's stays open to internal hosts.
        internal = {"Host": "ganam-assistant.ganam-assistant"}
        r = await client.post("/webhook", headers=internal, json={"event": "zone_entered", "zone": "work"})
        check("/webhook from an internal host → 401 (not inbound any more)", r.status == 401)
        r = await client.post("/webhook", headers=ADMIN_AUTH, json={"event": "zone_entered", "zone": "work"})
        check("/webhook with a token → 404 (no such route)", r.status == 404)
        with patch.object(whatsapp_api.whatsapp_events, "handle_webhook", AsyncMock()) as waha:
            r = await client.post("/webhook/waha", headers=internal, json={"event": "message"})
            await asyncio.sleep(0.05)
        check("/webhook/waha from an internal host still accepted", r.status == 200 and waha.await_count == 1)


# --- 5. The watcher ----------------------------------------------------------------------------

FIRED: List[Tuple[str, str, str, str, Optional[str]]] = []


async def spy_fire(user, trigger_type, *, action="", target=""):
    FIRED.append((user.id, trigger_type, action, target, users.current_id()))
    return await triggers.fire(user, trigger_type, action=action, target=target)


def fired(user=None) -> List[Tuple[str, str, str]]:
    return [(t, a, tg) for uid, t, a, tg, _ in FIRED if user is None or uid == user.id]


def task(user, title: str, trig: Dict[str, Any]) -> str:
    return tasks_service.create_task(user, title=title, triggers=json.dumps(trig))


def marks(user) -> set:
    with get_db_connection() as conn:
        return {r[0] for r in conn.execute("SELECT key FROM task_trigger_marks WHERE user_id = ? "
                                           "AND key LIKE 'last_event_v2_%'", (user.id,))}


def checks_of(user) -> Dict[str, Dict[str, Any]]:
    return {c["key"]: c for c in checks.compute(user=user)}


async def test_watcher() -> None:
    ha_watcher.RETRY_MIN_SECONDS, ha_watcher.RETRY_MAX_SECONDS = 0.05, 0.2
    t_work = task(ADMIN, "Admin at work", {"location": {"location": "Work", "event": "arrive"}})
    t_gym = task(ADMIN, "Admin at gym", {"location": {"location": "gym", "event": "arrive"}})
    t_bob_work = task(BOB, "Bob at work", {"location": {"location": "work", "event": "arrive"}})
    t_bob_car = task(BOB, "Bob in the car", {"car": "enter"})
    t_bob_car_out = task(BOB, "Bob out of the car", {"car": "leave"})
    push = AsyncMock(return_value={"telegram": True})  # core.notify.send: what each reminder sends

    def pushed(task_id: str) -> int:
        return sum(1 for c in push.await_args_list
                   if c.args[1].kind == "reminder" and c.args[1].data["task_id"] == task_id)

    watchers = ha_watcher.Watchers(spy_fire)
    with patch.object(core_notify, "send", push):
        with users.acting_as(None):
            watchers.reconcile()
        check("watchers: one per user with entities set, none for Carl",
              set(watchers.running()) == {ADMIN.id, BOB.id})
        check("watchers: both connected (recorded per user)", await until(
            lambda: ha_status.read(ADMIN.id).connected and ha_status.read(BOB.id).connected))
        check("watchers: each subscribed only to their own entities",
              HA_A.subscriptions == [["person.robin", "sensor.pixel_8_bluetooth_connection"]]
              and HA_B.subscriptions == [["person.bob", "sensor.b_bt"]], (HA_A.subscriptions, HA_B.subscriptions))
        await asyncio.sleep(0.1)
        check("no fire on the initial state", FIRED == [], FIRED)
        st = ha_status.read(ADMIN.id)
        check("entities recorded as existing", st.entities["person"].exists and st.entities["car"].exists)

        # Zone enter / leave: the admin's triggers only.
        await HA_A.set_state("person.robin", "Work")
        check("person home → Work: leave home, arrive Work (the admin's)", await until(
            lambda: fired() == [("location", "leave", "home"), ("location", "arrive", "Work")]), FIRED)
        check("fired as that user", all(uid == cur == ADMIN.id for uid, *_, cur in FIRED))
        check("the admin's arrive-at-work task fired, not Bob's", await until(lambda: pushed(t_work) == 1)
              and pushed(t_bob_work) == 0)
        check("targets as the old webhook produced them (lower case)",
              {"last_event_v2_location_arrive_work", "last_event_v2_location_leave_home"} <= marks(ADMIN)
              and not marks(BOB), marks(ADMIN))
        # Attribute-only changes and ignored states.
        FIRED.clear()
        await HA_A.set_state("person.robin", "Work", {"friendly_name": "Robin", "latitude": 32.1})
        await HA_A.set_state("person.robin", "not_home")
        await HA_A.set_state("person.robin", "unknown")
        await HA_A.set_state("person.robin", "unavailable")
        await HA_A.set_state("person.robin", "home")
        check("ignored states: Work → not_home leaves Work, not_home → unknown → unavailable nothing, → home arrives",
              await until(lambda: fired() == [("location", "leave", "Work"), ("location", "arrive", "home")]), fired())

        # Car enter / leave: Bob's.
        FIRED.clear()
        await HA_B.set_state("sensor.b_bt", "1", {"connected_paired_devices": ["Phone speaker", "CarB (00:11)"]})
        check("car connected: car enter for Bob only", await until(lambda: fired() == [("car", "enter", "")])
              and fired(ADMIN) == [], FIRED)
        check("Bob's car task reminded", await until(lambda: pushed(t_bob_car) == 1))
        await HA_B.set_state("sensor.b_bt", "1", {"connected_paired_devices": ["Phone speaker", "CarB (00:11)"],
                                                  "extra": 1})
        await HA_B.set_state("sensor.b_bt", "0", {"connected_paired_devices": []})
        check("car disconnected: car leave (an unrelated attribute change fires nothing)",
              await until(lambda: fired() == [("car", "enter", ""), ("car", "leave", "")]), fired())
        check("Bob's car-leave task reminded", await until(lambda: pushed(t_bob_car_out) == 1))
        FIRED.clear()
        await HA_A.set_state("sensor.pixel_8_bluetooth_connection", "1", {"connected_paired_devices": "['Headphones']"})
        await asyncio.sleep(0.1)
        check("a device that is not the car: no car enter", fired() == [], fired())
        await HA_A.set_state("sensor.pixel_8_bluetooth_connection", "2",
                             {"connected_paired_devices": "['Headphones', 'CarKit-1234']"})
        check("the admin's car (the imported default name): car enter",
              await until(lambda: fired() == [("car", "enter", "")]), fired())

        # A transition missed while disconnected fires once after the reconnect.
        FIRED.clear()
        HA_A.subscriptions.clear()
        await HA_A.drop_connections()
        await HA_A.set_state("person.robin", "Gym", push=False)
        check("reconnect: re-subscribed", await until(lambda: len(HA_A.subscriptions) == 1))
        check("reconnect: the missed home → Gym fires once",
              await until(lambda: fired() == [("location", "leave", "home"), ("location", "arrive", "Gym")]), fired())
        check("reconnect: the gym task reminded once", await until(lambda: pushed(t_gym) == 1))
        HA_A.subscriptions.clear()
        await HA_A.drop_connections()
        resubscribed = await until(lambda: len(HA_A.subscriptions) == 1)
        await asyncio.sleep(0.2)
        check("reconnect without a change: nothing fires", resubscribed
              and fired() == [("location", "leave", "home"), ("location", "arrive", "Gym")], fired())
        await triggers.fire(ADMIN, "location", action="arrive", target="Gym")
        await asyncio.sleep(0.1)
        check("a duplicate arrive (as a double-posted event) does not refire the reminder", pushed(t_gym) == 1)

        # Checks: connected, entities found.
        cs = checks_of(BOB)
        check("checks: Bob's watcher connected, his entities found",
              cs["ha_watcher"]["ok"] and cs["ha_watcher"]["detail"] == "connected"
              and cs["ha_entities"]["ok"] and cs["ha_entities"]["detail"] == "all found", (cs["ha_watcher"], cs["ha_entities"]))
        cs = checks_of(BOB)
        check("checks: location / car per user (Bob's car update seen)", cs["car"]["ok"], cs["car"])
        cs = checks_of(CARL)
        check("checks: nothing to report for a user without the addon",
              cs["ha_watcher"]["detail"] == "not connected" and cs["ha_entities"]["detail"] == "not connected")

        # Settings change: re-subscribes (to an entity B does not have → check item).
        HA_B.subscriptions.clear()
        store.update(BOB, "homeassistant", settings={"person": "person.bobby"})
        watchers.reconcile()
        check("settings change: re-subscribed with the new entity", await until(
            lambda: HA_B.subscriptions == [["person.bobby", "sensor.b_bt"]]), HA_B.subscriptions)
        check("missing entity recorded", await until(lambda: ha_status.read(BOB.id).entities.get("person")
                                                    and not ha_status.read(BOB.id).entities["person"].exists))
        cs = checks_of(BOB)
        check("checks: a selected entity that does not exist → not ok",
              not cs["ha_entities"]["ok"] and "person.bobby" in cs["ha_entities"]["detail"], cs["ha_entities"])
        FIRED.clear()
        await HA_B.set_state("person.bobby", "Work")
        await asyncio.sleep(0.1)
        check("an entity that appears: recorded, nothing fired", fired() == [], fired())
        await HA_B.set_state("person.bobby", "home")
        check("…its next change fires (leave Work, arrive home)",
              await until(lambda: fired(BOB) == [("location", "leave", "Work"), ("location", "arrive", "home")]), FIRED)
        check("entity now exists", ha_status.read(BOB.id).entities["person"].exists)

        # One instance down: the other keeps working; the check reports it.
        HA_B.down = True
        await HA_B.drop_connections()
        check("B down: Bob's watcher not connected", await until(lambda: ha_status.read(BOB.id).connected is False))
        FIRED.clear()
        await HA_A.set_state("person.robin", "Work")
        check("B down: the admin's watcher still fires", await until(
            lambda: fired(ADMIN) == [("location", "leave", "Gym"), ("location", "arrive", "Work")]), FIRED)
        saved = checks.HA_WATCHER_MINUTES
        checks.HA_WATCHER_MINUTES = 0
        try:
            cs = checks_of(BOB)
            check("checks: a disconnected watcher → not ok", not cs["ha_watcher"]["ok"]
                  and cs["ha_watcher"]["detail"].startswith("disconnected for"), cs["ha_watcher"])
            check("checks: the admin's watcher unaffected", checks_of(ADMIN)["ha_watcher"]["ok"])
        finally:
            checks.HA_WATCHER_MINUTES = saved
        HA_B.down = False
        check("B back: Bob's watcher reconnects by itself", await until(lambda: ha_status.read(BOB.id).connected, 5))

        # Disconnect stops the watcher.
        store.disconnect(BOB, "homeassistant")
        watchers.reconcile()
        check("disconnect: Bob's watcher stopped", set(watchers.running()) == {ADMIN.id})
        check("disconnect: his connection to B is closed", await until(lambda: not HA_B.sockets))
        check("disconnect: his watcher marks are gone", ha_status.read(BOB.id).connected is None
              and not ha_status.read(BOB.id).entities)
        FIRED.clear()
        await HA_B.set_state("person.bobby", "Work")
        await asyncio.sleep(0.1)
        check("disconnect: nothing fires for him any more", fired() == [], FIRED)
        await watchers.stop()
        check("stop: every watcher ended", not watchers.running() and await until(lambda: not HA_A.sockets))


async def main() -> None:
    await HA_A.start()
    await HA_B.start()
    test_import()
    connect_users()
    await test_reads()
    await test_http()
    await test_watcher()
    await HA_A.server.close()
    await HA_B.server.close()


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
