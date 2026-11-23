"""Home Assistant per user (docs/access.md, "Addons").
Run: bin/test test_homeassistant_users

Two in-process fake Home Assistants (aiohttp: REST states / services /
config / camera, and the WebSocket auth and entity registry), each with its
own token and entities. The first admin's addon is connected by the operator
and is then pointed at instance A; Bob has instance B; Carl has no Home
Assistant. Checked: tools, proxies, the zone import and the home map hit only
the user's own instance with their own token; the user without the addon
gets 403 / no tools / no map; Home Assistant is no source of any signal (no
wake, screen, car, location or weather source, no watcher service, no
sensor self-checks, the clock never asks it, ``/api/client-config`` gone) and
its first migration drops the old sensor settings, the watcher's marks and
its signal records from a stored connection, keeping the rest; ``/webhook``
is gone while ``/webhook/waha`` takes only signed events (an internal Host
header proves nothing); nothing reads the old env names; the TV tool takes
its TV and channel command from the user's settings (a non-numeric channel
refused). No network beyond localhost.
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
os.environ["API_TOKEN"] = "ha-users-test-env-token"  # a test value: the first admin's token (a headless install)
os.environ["WAHA_WEBHOOK_SECRET"] = "ha-users-test-waha-secret"  # a test value: WAHA's HMAC key
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials
# The first admin's env values, as production has them (fake).
os.environ["HOMEASSISTANT_TOKEN"] = "env-token"  # the old env import's names: read by nothing now
os.environ["PRIMARY_PERSON"] = "person.robin"

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users
from addons.tasks.groups import ensure_default_groups_for_everyone

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()
testenv.connect_admin("homeassistant", credentials={"host": "http://admin-ha.invalid", "token": "admin-token"},
                      settings={"phone_battery_sensor": "sensor.pixel_8_battery_level"})
ensure_default_groups_for_everyone()

from addons.cameras import api as cameras_api  # noqa: E402
from addons.homeassistant import api as homeassistant_api  # noqa: E402
from addons.whatsapp import api as whatsapp_api  # noqa: E402
from core import briefing, checks, clock, now as now_mod, signals, wake, zones as core_zones  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.addons import registry, scheduler, store  # noqa: E402
from addons.homeassistant import home_map  # noqa: E402
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
    "zone.home": ("0", {"friendly_name": "Home", "latitude": 32.1, "longitude": 34.8, "radius": 120}),
    "zone.work": ("0", {"friendly_name": "Work", "latitude": 32.0, "longitude": 34.7, "radius": 200}),
    "zone.gym": ("0", {"friendly_name": "Gym", "latitude": 32.2, "longitude": 34.9}),
    "light.a_kitchen": ("on", {"friendly_name": "A Kitchen"}),
    "weather.forecast_home": ("sunny", {"temperature": 21, "temperature_unit": "°C"}),
}, {"light.a_kitchen": "A Kitchen"}, "Asia/Jerusalem")
HA_B = FakeHA("B", "tok-b", {
    "person.bob": ("home", {"friendly_name": "Bob"}),
    "sensor.b_bt": ("0", {"connected_paired_devices": []}),
    "zone.home": ("0", {"friendly_name": "Home", "latitude": 40.1, "longitude": -74.0, "radius": 100}),
    "zone.work": ("0", {"friendly_name": "Work", "latitude": 40.2, "longitude": -74.1, "radius": 100}),
    "zone.bobplace": ("0", {"friendly_name": "BobPlace", "latitude": 40.3, "longitude": -74.2, "radius": 50}),
    "zone.unplaced": ("0", {"friendly_name": "Unplaced"}),
    "light.b_den": ("off", {"friendly_name": "B Den"}),
    "weather.b": ("rainy", {"temperature": 31, "temperature_unit": "°C"}),
    "media_player.den_tv_adb": ("on", {"friendly_name": "Den TV (ADB)"}),
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


# --- 1. No env import: the old env names -------------------------------------------------------

def test_import() -> None:
    ha = store.require(ADMIN, "homeassistant")
    check("connection: the operator's host / token (env values ignored)",
          dict(ha.credentials) == {"host": "http://admin-ha.invalid", "token": "admin-token"}, dict(ha.credentials))
    check("connection: the battery sensor as connected; only the settings of what it controls and shows",
          dict(ha.settings) == {"phone_battery_sensor": "sensor.pixel_8_battery_level", "tv_entity": None,
                                "tv_channel_command": None}, dict(ha.settings))
    old_names = ("HOMEASSISTANT_HOST", "HOMEASSISTANT_TOKEN", "HOMEASSISTANT_TODO_ENTITY_ID", "PRIMARY_PERSON",
                 "PHONE_INTERACTIVE_SENSOR", "PHONE_BATTERY_SENSOR", "PHONE_NOTIFY_SERVICE", "BRIEFING_WEATHER_ENTITY")
    root = testenv.SERVER
    readers = []
    for path in list((root / "core").rglob("*.py")) + [p for p in (root / "addons").rglob("*.py") if "tests" not in p.parts] + [root / "main.py"]:
        text = path.read_text(encoding="utf-8")
        readers += [f"{path.relative_to(root)}: {n}" for n in old_names if re.search(rf'["\']{n}["\']', text)]
    check("old env names: read by nothing", not readers, readers)
    check("no owner-specific defaults in the addon (entity settings default to nothing)",
          all(f.default is None for f in store.get_addon("homeassistant").settings))
    routes = "".join(p.read_text(encoding="utf-8") for p in
                     list((root / "core").glob("*.py")) + list((root / "addons").rglob("api.py")))
    check("/webhook route is gone (only /webhook/waha)", 'add_post("/webhook",' not in routes
          and 'add_post("/webhook/waha"' in routes)


# --- 2. Connections ------------------------------------------------------------------------------

def connect_users() -> None:
    store.update(ADMIN, "homeassistant", credentials={"host": HA_A.url, "token": "tok-a"})
    store.connect(BOB, "homeassistant", credentials={"host": HA_B.url, "token": "tok-b"},
                  settings={"phone_battery_sensor": "sensor.b_battery"})
    store.connect(BOB, "tasks")
    store.connect(BOB, "cameras", settings={"url": "http://frigate-b.invalid"})  # the HA camera proxy needs both kinds


# --- 3. Tools, home map ------------------------------------------------------------------------

async def test_reads() -> None:
    clear()
    await tool(BOB, "call_ha_service", domain="light", service="turn_on", entity_id="light.b_den")
    check("tool call_ha_service: only on Bob's instance",
          HA_B.service_calls == [("light.turn_on", {"entity_id": "light.b_den"})] and not HA_A.service_calls)
    clear()
    out = await tool(ADMIN, "get_ha_exposed_entities")
    check("tool get_ha_exposed_entities: the admin's entities over A's WebSocket",
          "light.a_kitchen" in out and "light.b_den" not in out and HA_A.tokens_seen() == {"tok-a"}
          and not HA_B.requests, out)
    out = await tool(ADMIN, "get_ha_exposed_entities", search="kitchen")
    check("tool get_ha_exposed_entities: search keeps only the matching entities",
          "light.a_kitchen | A Kitchen | on" in out and "1 of 1" in out, out)
    out = await tool(ADMIN, "get_ha_exposed_entities", search="garage")
    check("tool get_ha_exposed_entities: a search matching nothing says so", out.startswith("No exposed entity matches"), out)
    clear()
    out = await tool(BOB, "call_ha_service", domain="light", service="turn_on", entity_id="den light")
    check("tool call_ha_service: an entity id that is not one → an error naming where ids come from, nothing sent",
          out.startswith("Error:") and "get_ha_exposed_entities" in out and not HA_B.service_calls, out)
    try:
        await tool(CARL, "call_ha_service", domain="light", service="turn_on", entity_id="light.b_den")
        refused = False
    except registry.ToolNotAvailable:
        refused = True
    names = {s["function"]["name"] for s in registry.tool_schemas(CARL)}
    check("no addon: no Home Assistant tools, a call refused like an unknown tool",
          refused and not names & {"call_ha_service", "get_ha_exposed_entities", "control_android_tv"})

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



# --- 4. Not a source of signals ------------------------------------------------------------------

async def test_no_signals() -> None:
    for signal in signals.SIGNALS:
        hooks = registry.providers(signals.source_key(signal), BOB)
        check(f"signals: no {signal} source from Home Assistant for Bob",
              all(h.addon != "homeassistant" for h in hooks), [h.addon for h in hooks])
    check("providers: no weather / screen_sessions from Home Assistant",
          all(h.addon != "homeassistant" for key in ("weather", "screen_sessions")
              for h in registry.providers(key, BOB)))
    check("no Home Assistant service (no entity watcher)",
          not [s for s in registry.services() if s.addon == "homeassistant"], registry.services())
    keys = {c["key"] for c in checks.compute(user=BOB)}
    check("self-checks: no location / car / ha_entities / ha_watcher",
          not keys & {"location", "car", "ha_entities", "ha_watcher"}, keys)

    clear()
    with users.acting_as(BOB):
        w = await briefing.weather(BOB)
    check("weather: never read from Home Assistant (Bob has no Home zone: none)",
          w is None and now_mod.weather_cached(BOB) is None and not HA_B.requests, w)
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)
    source, sessions = await wake.screen_sessions(BOB, since, datetime.datetime.now(datetime.timezone.utc))
    check("wake: no screen sessions from Home Assistant", source is None and sessions is None and not HA_B.requests,
          (source, sessions))
    check("wake: Home Assistant alone is no wake source",
          await wake.unlock(BOB) == "unavailable" and not await wake.check_wake_fallback(BOB))

    saved = clock._timezone
    clock._timezone = None
    tz_env = os.environ.pop("TIMEZONE", None)
    try:
        clear()
        await asyncio.to_thread(clock.get_timezone)
        check("clock: the server's zone never asks Home Assistant", not HA_A.requests and not HA_B.requests,
              HA_A.requests)
    finally:
        clock._timezone = saved
        if tz_env is not None:
            os.environ["TIMEZONE"] = tz_env


def test_migration() -> None:
    """m0001_drop_sensor_settings on a stored connection with the old settings."""
    old = {"person": "person.bob", "car_sensor": "sensor.b_bt", "car_device": "CarB",
           "phone_interactive_sensor": "binary_sensor.b_screen", "weather_entity": "weather.b",
           "phone_notify_service": "mobile_app_b", "phone_battery_sensor": "sensor.b_battery",
           "tv_entity": "media_player.den_tv_adb"}
    with get_db_connection() as conn:
        creds_before = conn.execute("SELECT credentials FROM connections WHERE user_id = ? AND addon = ?",
                                    (BOB.id, "homeassistant")).fetchone()[0]
        conn.execute("UPDATE connections SET settings = ? WHERE user_id = ? AND addon = ?",
                     (json.dumps(old), BOB.id, "homeassistant"))
        conn.execute("UPDATE connections SET settings = ? WHERE user_id = ? AND addon = ?",
                     (json.dumps({"person": "person.robin"}), ADMIN.id, "homeassistant"))
        for key in (f"ha_watcher:{BOB.id}", f"ha_entity:{BOB.id}:person", f"ha_event:{BOB.id}:car", "backups:last"):
            conn.execute("INSERT OR REPLACE INTO system_marks (key, last_at, ok, detail) VALUES (?, ?, 1, '')",
                         (key, "2026-11-20T08:00:00+00:00"))
        for source in ("homeassistant", "phone"):
            conn.execute("INSERT OR REPLACE INTO signal_sources (user_id, signal, source, last_at, detail) "
                         "VALUES (?, 'car', ?, '2026-11-20T08:00:00+00:00', 'enter')", (BOB.id, source))
        conn.execute("DELETE FROM schema_migrations WHERE addon = 'homeassistant' AND version = 1")
        conn.commit()
    applied = db.migrate()
    with get_db_connection() as conn:
        rows = {r["user_id"]: (json.loads(r["settings"]), r["credentials"]) for r in conn.execute(
            "SELECT user_id, settings, credentials FROM connections WHERE addon = 'homeassistant'")}
        marks = {r[0] for r in conn.execute("SELECT key FROM system_marks")}
        sources = {r[0] for r in conn.execute("SELECT source FROM signal_sources WHERE user_id = ?", (BOB.id,))}
    check("migration: applied by the runner", "homeassistant:0001_drop_sensor_settings" in applied, applied)
    check("migration: Bob's sensor settings gone, battery and TV kept, credentials untouched",
          rows[BOB.id][0] == {"phone_battery_sensor": "sensor.b_battery", "tv_entity": "media_player.den_tv_adb"}
          and rows[BOB.id][1] == creds_before, rows[BOB.id])
    check("migration: the admin's settings emptied", rows[ADMIN.id][0] == {}, rows[ADMIN.id])
    check("migration: the watcher's marks gone, other marks kept",
          not any(k.startswith(("ha_watcher:", "ha_entity:", "ha_event:")) for k in marks) and "backups:last" in marks,
          marks)
    check("migration: Home Assistant's signal records gone, the phone's kept", sources == {"phone"}, sources)
    conn_b = store.require(BOB, "homeassistant")
    check("migration: the connection reads back", conn_b.settings["tv_entity"] == "media_player.den_tv_adb")


# --- 5. HTTP endpoints ---------------------------------------------------------------------------

def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    r = app.router
    r.add_post("/api/ha/zones/import", homeassistant_api.import_zones_api)
    r.add_get("/api/ha/state/{entity_id}", homeassistant_api.ha_entity_state_api)
    r.add_get("/api/ha/states", homeassistant_api.ha_states_api)
    r.add_post("/api/ha/service", homeassistant_api.ha_call_service_api)
    r.add_get("/api/camera/{entity_id}/proxy", cameras_api.ha_camera_proxy_api)
    r.add_post("/webhook/waha", whatsapp_api.handle_waha_webhook)
    return app


async def test_http() -> None:
    async with TestClient(TestServer(build_app())) as client:
        ha_routes = web.Application()
        homeassistant_api.setup(ha_routes)
        paths = {r.resource.canonical for r in ha_routes.router.routes()}
        check("client-config: no such route any more", "/api/client-config" not in paths
              and "/api/ha/zones/import" in paths, paths)

        clear()
        core_zones.create(BOB, "Work", 1.0, 2.0)  # his own Work stays as it is
        r = await client.post("/api/ha/zones/import", headers=BOB_AUTH)
        body = await r.json()
        mine = {z.name: z for z in core_zones.list_zones(BOB)}
        check("zone import: Bob's zones copied from B (Home marked Home, his own Work kept, one without a place left out)",
              r.status == 200 and sorted(z["name"] for z in body["added"]) == ["BobPlace", "Home"]
              and body["skipped"] == ["Work"] and mine["Home"].is_home and mine["BobPlace"].radius_m == 50
              and (mine["Work"].latitude, mine["Work"].longitude) == (1.0, 2.0) and "Unplaced" not in mine
              and HA_B.tokens_seen() == {"tok-b"} and not HA_A.requests, body)
        check("zone import: nothing of Bob's reached the admin", core_zones.list_zones(ADMIN) == [])
        r = await client.post("/api/ha/zones/import", headers=BOB_AUTH)
        check("zone import: a second time adds nothing", (await r.json())["added"] == [])
        r = await client.post("/api/ha/zones/import", headers=CARL_AUTH)
        check("zone import: without Home Assistant → 403 not_connected", r.status == 403)
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
        for path in ("/api/ha/state/light.b_den", "/api/ha/states?ids=x",
                     "/api/camera/camera.x/proxy"):
            r = await client.get(path, headers=CARL_AUTH)
            body = await r.json()
            check(f"no addon: GET {path.split('?')[0]} → 403 not_connected", r.status == 403
                  and body == {"error": "not_connected", "addon": "homeassistant"}, (r.status, body))
        r = await client.post("/api/ha/service", headers=CARL_AUTH,
                              json={"domain": "light", "service": "turn_on", "entity_id": "light.b_den"})
        check("no addon: POST /api/ha/service → 403, nothing called", r.status == 403 and not HA_B.service_calls[1:])

        # The old Home Assistant webhook is gone; WAHA's takes signed events only.
        internal = {"Host": "dana.dana"}
        r = await client.post("/webhook", headers=internal, json={"event": "zone_entered", "zone": "work"})
        check("/webhook from an internal host → 401 (not inbound any more)", r.status == 401)
        r = await client.post("/webhook", headers=ADMIN_AUTH, json={"event": "zone_entered", "zone": "work"})
        check("/webhook with a token → 404 (no such route)", r.status == 404)
        event = json.dumps({"event": "message"}).encode()
        signed = {"X-Webhook-Hmac": whatsapp_api.signature("ha-users-test-waha-secret", event),
                  "X-Webhook-Hmac-Algorithm": "sha512", "Content-Type": "application/json"}
        with patch.object(whatsapp_api.whatsapp_events, "handle_webhook", AsyncMock()) as waha:
            r = await client.post("/webhook/waha", headers=internal, data=event)
            check("/webhook/waha from an internal host, unsigned → 403, not handled",
                  r.status == 403 and waha.await_count == 0, r.status)
            r = await client.post("/webhook/waha", headers=signed, data=event)
            await asyncio.sleep(0.05)
        check("/webhook/waha signed with the secret → accepted", r.status == 200 and waha.await_count == 1)


async def test_tv() -> None:
    """control_android_tv: the TV and the channel command come from the user's settings, nothing built in."""
    clear()
    out = await tool(ADMIN, "control_android_tv", action="send_key", command="HOME")
    check("tv: no TV given and no tv_entity setting → an error, nothing sent",
          out.startswith("Error: no TV given") and not HA_A.service_calls, out)
    store.update(BOB, "homeassistant", settings={"tv_entity": "media_player.den_tv_adb"})
    out = await tool(BOB, "control_android_tv", action="watch_channel", channel="12")
    check("tv: watch_channel without tv_channel_command → an error, nothing sent",
          out.startswith("Error: no channel command") and not HA_B.service_calls, out)
    store.update(BOB, "homeassistant", settings={"tv_channel_command": "am start -d \"tv://channel/{channel}\""})
    out = await tool(BOB, "control_android_tv", action="watch_channel", channel="12; reboot")
    check("tv: a channel that is not a number is refused (never reaches the ADB command)",
          out.startswith("Error: 'channel' must be") and not HA_B.service_calls, out)
    out = await tool(BOB, "control_android_tv", action="watch_channel", channel="12")
    check("tv: watch_channel sends the user's command with the channel, to their TV",
          ("androidtv.adb_command", {"entity_id": "media_player.den_tv_adb", "command": "am start -d \"tv://channel/12\""})
          in HA_B.service_calls and not HA_A.service_calls, (out, HA_B.service_calls))
    clear()
    out = await tool(BOB, "control_android_tv", action="rewind")
    check("tv: an unknown action → an error listing the actions, nothing asked of HA",
          out.startswith("Error: Unknown action") and "send_key" in out and not HA_B.requests, out)
    out = await tool(BOB, "control_android_tv", action="play_youtube", query="it's a test; reboot")
    sent = [d["command"] for name, d in HA_B.service_calls if name == "androidtv.adb_command"]
    check("tv: a YouTube search query is one quoted shell word",
          sent == ["am start -a android.intent.action.SEARCH -e query 'it'\"'\"'s a test; reboot' -n "
                   "com.google.android.youtube.tv/com.google.android.apps.youtube.tv.activity.ShellActivity"]
          and out.startswith("Done on 'media_player.den_tv_adb'"), (out, sent))
    check("tv: the TV that is on is asked for its one state, never every state",
          "/api/states" not in [p for p, _ in HA_B.requests]
          and "/api/states/media_player.den_tv_adb" in [p for p, _ in HA_B.requests], HA_B.requests)


async def main() -> None:
    await HA_A.start()
    await HA_B.start()
    test_import()
    connect_users()
    await test_reads()
    await test_no_signals()
    await test_http()
    await test_tv()
    test_migration()
    await HA_A.server.close()
    await HA_B.server.close()


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
