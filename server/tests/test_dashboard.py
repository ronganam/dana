"""Dashboards of tablets and TVs: the widget hook (core/addons/addon.py), presets and layouts (core/dashboard.py), the API and page (core/dashboard_api.py), the official widgets.
Run: bin/test test_dashboard

Robin (the first admin) has a bedroom tablet, a TV without a role and a phone; Gal (a member) a hall tablet;
Maya (a member) no device and fewer addons. Checked: the hook's rules; the four presets (widgets, sizes, order,
the bedroom's settings); who reads a dashboard (a tablet / TV token only its own, a user who may manage the device,
anyone else 404 like a missing id; data only for the device or its owner, an admin managing someone else's device
gets the layout with the data withheld); each widget's data is its owner's (tasks, shopping, Home Assistant, the
calendar, money, Now, the clock's weather) and a widget of an addon the owner lacks answers not_connected (not
offered in the catalog, 403 on a refresh); choosing a preset and saving edits (order, size, settings), the
refusals, a device token cannot save; removing the device removes its dashboard; the page; the web fixture
(WRITE_WEB_FIXTURE=1 rewrites tests/fixtures/dashboard_web.json). Temp DB, in-process fakes, no network.
"""

import asyncio
import datetime
import json
import os
import re
import sys
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("dashboard_test")
os.environ["API_TOKEN"] = "dashboard-test-env-token"  # a test value: with it set, Bearer tokens decide the user
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # throwaway: encrypts the fake credentials

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import addons  # noqa: F401  (loads the addons)
from core import clock, dashboard, dashboard_api, db, devices, users
from core.addons import Addon, Field, WidgetContext, registry, store
from core.addons.addon import forget, key_clash
from core.auth import setup_auth
from core.db import get_db_connection
from addons import bootstrap as addons_bootstrap
from addons.calendar import events as calendar_events
from addons.shopping import items as shopping_items, lists as shopping_lists
from addons.shopping.names import ItemInput
from addons.tasks import service as tasks_service
from testfakes import FakeHA

db.migrate()
users.bootstrap()
addons_bootstrap.bootstrap()

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "dashboard_web.json")
results: List[Tuple[str, bool]] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


ROBIN = users.first_admin()
GAL = users.create("Gal Mizrahi")
MAYA = users.create("Maya Cohen")
for u in (GAL,):
    for name in ("tasks", "shopping"):
        store.connect(u, name)
store.connect(GAL, "cameras", settings={"url": "http://frigate-gal.invalid"})
store.connect(ROBIN, "calendar", credentials={"refresh_token": "test-refresh-token"})  # Google is mocked below
store.connect(ROBIN, "money")
HA = FakeHA("robin", "tok-robin", {
    "climate.bedroom": ("cool", {"friendly_name": "Bedroom AC", "hvac_modes": ["off", "cool", "heat"],
                                 "fan_modes": ["auto", "low", "high"], "fan_mode": "auto", "current_temperature": 23.5,
                                 "temperature": 22, "current_humidity": 48, "min_temp": 16, "max_temp": 30,
                                 "temperature_unit": "°C"}),
    "climate.office": ("off", {"friendly_name": "Office AC", "hvac_modes": ["off", "cool"], "temperature": 24}),
    "light.hall": ("on", {"friendly_name": "Hall", "brightness": 128}),
    "light.kitchen": ("off", {"friendly_name": "Kitchen"}),
    "sensor.robin_phone_battery": ("81", {"friendly_name": "Robin's phone battery", "unit_of_measurement": "%"}),
    "sensor.robin_watch_battery": ("15", {"friendly_name": "Robin's watch battery", "unit_of_measurement": "%"}),
    "weather.forecast_home": ("sunny", {"temperature": 27, "temperature_unit": "°C"}),
}, {}, "UTC")

TOKENS: Dict[str, str] = {}
DEV: Dict[str, devices.Device] = {}


def auth(who: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[who]}"}


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    dashboard_api.setup(app)
    return app


def setup_devices() -> None:
    TOKENS["robin_tablet"], DEV["robin_tablet"] = devices.create(ROBIN, "tablet", "Bedroom tablet", role="bedroom")
    TOKENS["robin_tv"], DEV["robin_tv"] = devices.create(ROBIN, "tv", "Living room TV")
    TOKENS["robin_phone"], DEV["robin_phone"] = devices.create(ROBIN, "phone", "Robin's phone")
    TOKENS["gal_tablet"], DEV["gal_tablet"] = devices.create(GAL, "tablet", "Hall tablet", role="hall")
    TOKENS["robin"], _ = users.issue_token(ROBIN.id, "robin script")
    TOKENS["gal"], _ = users.issue_token(GAL.id, "gal script")
    TOKENS["maya"], _ = users.issue_token(MAYA.id, "maya script")


def fake_events(conn, time_min, time_max, limit=50):
    today = time_min.date()
    tomorrow = today + datetime.timedelta(days=1)
    return [
        {"id": "e1", "title": "Dentist", "start": f"{today}T09:30:00+00:00", "end": f"{today}T10:00:00+00:00",
         "all_day": False, "location": "", "attendees": [], "calendar_id": "primary"},
        {"id": "e2", "title": "Gal's birthday", "start": str(tomorrow), "end": str(tomorrow + datetime.timedelta(days=1)),
         "all_day": True, "location": "", "attendees": [], "calendar_id": "family"},
    ]


# --- The hook ---------------------------------------------------------------------------------

def test_hook() -> None:
    keys = {h.key: h.addon for h in registry.hooks("widget", None)}
    check("hook: the official widgets, each from its addon",
          keys == {"clock": "essentials", "now": "essentials", "reminders": "tasks", "shopping": "shopping",
                   "ha_climate": "homeassistant", "ha_lights": "homeassistant", "ha_entities": "homeassistant",
                   "ha_batteries": "homeassistant", "calendar": "calendar", "money": "money", "cameras": "cameras"},
          keys)
    probe = Addon("dash_probe", title="Probe")
    refused = []
    for kwargs in ({"key": "Bad-Key"}, {"sizes": ("huge",)}, {"sizes": ()}, {"script": "../x.js"},
                   {"script": "widget.css"}, {"refresh": 5}, {"title": ""},
                   {"settings": (Field("x", "", type="address"),)}, {"settings": (Field("a"), Field("a"))}):
        args = {"key": "probe", "title": "Probe", "script": "widget.js", **kwargs}
        try:
            probe.widget(args.pop("key"), **args)(lambda user, ctx: {})
            refused.append(False)
        except (ValueError, TypeError):
            refused.append(True)
    check("hook: a bad key, size, script, refresh, title or setting is refused when declared", all(refused), refused)

    @probe.widget("clock", title="Another clock", script="widget.js")
    def other_clock(user, ctx: WidgetContext) -> Dict[str, Any]:
        return {}
    clash = key_clash(probe, [a for a in (registry.get_addon(n) for n in ("essentials",))])
    check("hook: a widget key is the one addon's (a second addon declaring it cannot load)",
          clash and "already declared by the essentials addon" in clash, clash)
    forget("dash_probe")
    hook = next(h for h in registry.hooks("widget", None) if h.key == "cameras")
    check("hook: the options it keeps (title, script, sizes, settings, refresh)",
          hook.option("title") == "Camera" and hook.option("script") == "widget.js"
          and hook.option("sizes") == ("large", "medium") and [f.name for f in hook.option("settings")] == ["camera"]
          and hook.option("refresh") == 15)


# --- Presets ----------------------------------------------------------------------------------

def test_presets() -> None:
    table = {k: [w for w, _, _ in p.widgets] for k, p in dashboard.PRESETS.items()}
    check("presets: the four rooms", list(table) == ["kitchen", "bedroom", "hall", "living_room"], list(table))
    has = lambda key: sorted(room for room, ws in table.items() if key in ws)  # noqa: E731
    check("presets: clock and weather on all four", has("clock") == ["bedroom", "hall", "kitchen", "living_room"])
    check("presets: Now in the kitchen, hall and living room", has("now") == ["hall", "kitchen", "living_room"])
    check("presets: shopping in the kitchen", has("shopping") == ["kitchen"])
    check("presets: calendar in the kitchen, hall and living room", has("calendar") == ["hall", "kitchen", "living_room"])
    check("presets: reminders in the kitchen, bedroom and hall", has("reminders") == ["bedroom", "hall", "kitchen"])
    check("presets: cameras in the bedroom, hall and living room", has("cameras") == ["bedroom", "hall", "living_room"])
    ha = sorted(room for room, ws in table.items() if any(w.startswith("ha_") for w in ws))
    check("presets: Home Assistant in the kitchen, bedroom and living room", ha == ["bedroom", "kitchen", "living_room"])
    check("presets: money in the living room", has("money") == ["living_room"])
    bedroom = dashboard.preset_widgets("bedroom")
    check("presets: the bedroom's widgets, sizes and order (the old bedroom dashboard)",
          [(w["widget"], w["size"]) for w in bedroom] == [("clock", "medium"), ("cameras", "large"),
                                                          ("reminders", "medium"), ("ha_climate", "medium"),
                                                          ("ha_batteries", "small")], bedroom)
    check("presets: the bedroom's camera, climate entity and battery sensors are settings (empty in the preset)",
          {w["widget"]: w["settings"] for w in bedroom if w["settings"]}
          == {"cameras": {"camera": ""}, "ha_climate": {"entity": ""}, "ha_batteries": {"sensors": ""}})
    declared = {h.key: {f.name for f in h.option("settings", ())} for h in registry.hooks("widget", None)}
    check("presets: every preset widget exists, at a size it has, with settings it declares",
          all(w in declared and set(s) <= declared[w]
              and size in next(h for h in registry.hooks("widget", None) if h.key == w).option("sizes")
              for p in dashboard.PRESETS.values() for w, size, s in p.widgets))
    check("presets: ids are unique per dashboard", all(len({w["id"] for w in dashboard.preset_widgets(k)})
                                                       == len(dashboard.PRESETS[k].widgets) for k in dashboard.PRESETS))


# --- The API ----------------------------------------------------------------------------------

def by_widget(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    return next(w for w in payload["widgets"] if w["widget"] == key)


async def get(client: TestClient, path: str, who: str) -> Tuple[int, Any]:
    r = await client.get(path, headers=auth(who))
    try:
        return r.status, await r.json()
    except Exception:
        return r.status, await r.text()


async def test_read(client: TestClient, captured: Dict[str, Any]) -> None:
    tasks_service.create_task(ROBIN, title="Pay the water bill", due_date=clock.now().date().isoformat())
    tasks_service.create_task(GAL, title="Book the vet", due_date=clock.now().date().isoformat())
    shopping_items.add(ROBIN, shopping_lists.active_list(ROBIN), [ItemInput("Milk", "2")])
    rt, gt, tv, phone = (DEV[k].id for k in ("robin_tablet", "gal_tablet", "robin_tv", "robin_phone"))

    s, own = await get(client, "/api/dashboard", "robin_tablet")
    captured["bedroom_tablet"] = {"status": s, "body": own}
    check("read: a tablet without ?device= gets its own dashboard, its role's preset",
          s == 200 and own["device"]["id"] == rt and own["preset"] == "bedroom" and own["edited"] is False
          and [w["widget"] for w in own["widgets"]] == ["clock", "cameras", "reminders", "ha_climate", "ha_batteries"],
          (s, own))
    rem = by_widget(own, "reminders")
    check("read: its data is its owner's (Robin's task, not Gal's)",
          [t["title"] for t in rem["data"]["due"]] == ["Pay the water bill"] and rem["script"].startswith(
              "/addons/tasks/web/widget.js?v="), rem)
    cam = by_widget(own, "cameras")
    check("read: a widget of an addon its owner lacks answers not_connected (no data, no error elsewhere)",
          cam["error"] == "not_connected" and cam["addon"] == "cameras" and cam["data"] is None, cam)
    climate = by_widget(own, "ha_climate")
    check("read: Home Assistant climate with no entity chosen: the first climate device, from Robin's own instance",
          climate["data"]["entity_id"] == "climate.bedroom" and climate["data"]["current"] == 23.5
          and climate["data"]["modes"] == ["off", "cool", "heat"]
          and all(tok == "tok-robin" for _, tok in HA.requests), climate)
    batt = by_widget(own, "ha_batteries")
    check("read: batteries with none chosen: the phone battery sensor from the Home Assistant settings",
          batt["data"]["batteries"] == [{"entity_id": "sensor.robin_phone_battery", "name": "Robin's phone battery",
                                         "level": 81}], batt)
    clock_w = by_widget(own, "clock")
    check("read: the clock's weather through the weather provider (Robin's Home Assistant)",
          (clock_w["data"]["weather"] or {}).get("temperature") == 27, clock_w)

    for who, target, why in (("robin_tablet", gt, "another user's tablet"), ("robin_tablet", tv, "its own owner's TV"),
                             ("gal_tablet", rt, "another user's tablet"), ("maya", rt, "a member, not hers"),
                             ("maya", gt, "a member, not hers"), ("gal", rt, "a member, not hers"),
                             ("robin", phone, "a phone has no dashboard"), ("robin", "nope", "a missing id")):
        s, body = await get(client, f"/api/dashboard?device={target}", who)
        check(f"read: 404 like a missing one — {who} → {why}", s == 404 and body.get("error") == "not_found", (s, body))
    s, body = await get(client, "/api/dashboard", "robin")
    check("read: a credential of no tablet / TV must name one (400 device_required)",
          s == 400 and body["error"] == "device_required", (s, body))
    s, tvd = await get(client, f"/api/dashboard?device={tv}", "robin")
    check("read: a TV without a role starts from the living room preset; its owner reads it with another token",
          s == 200 and tvd["preset"] == "living_room" and by_widget(tvd, "money")["data"]["currency"], (s, tvd))
    s, phone_view = await get(client, f"/api/dashboard?device={rt}", "robin_phone")
    check("read: the owner's phone (not a tablet / TV token) reads the tablet's dashboard", s == 200, s)
    cal = by_widget(tvd, "calendar")["data"]["days"]
    check("read: calendar: today and tomorrow (an all-day event on its day only)",
          [d["label"] for d in cal] == ["Today", "Tomorrow"] and [e["title"] for e in cal[0]["events"]] == ["Dentist"]
          and [e["title"] for e in cal[1]["events"]] == ["Gal's birthday"], cal)
    lights = by_widget(tvd, "ha_lights")["data"]["lights"]
    check("read: lights with none chosen: every light, on / off and brightness",
          lights == [{"entity_id": "light.hall", "name": "Hall", "on": True, "brightness": 50},
                     {"entity_id": "light.kitchen", "name": "Kitchen", "on": False, "brightness": None}], lights)
    now_w = by_widget(tvd, "now")
    check("read: Now: the lede, the first items and how many", set(now_w["data"]) == {"lede", "items", "total"}, now_w)

    s, gal_d = await get(client, "/api/dashboard", "gal_tablet")
    check("read: Gal's hall tablet: her task only; her camera widget asks for a camera (none chosen)",
          s == 200 and gal_d["preset"] == "hall"
          and [t["title"] for t in by_widget(gal_d, "reminders")["data"]["due"]] == ["Book the vet"]
          and by_widget(gal_d, "cameras")["data"]["camera"] is None
          and by_widget(gal_d, "calendar")["error"] == "not_connected", gal_d)
    s, admin_view = await get(client, f"/api/dashboard?device={gt}", "robin")
    check("read: an admin managing Gal's tablet gets its layout, the data withheld (private)",
          s == 200 and all(w["data"] is None and w["error"] == "private" for w in admin_view["widgets"]), admin_view)

    # One widget again.
    s, one = await get(client, "/api/dashboard/widget?id=reminders-1", "robin_tablet")
    check("widget: one widget's data again", s == 200 and one["id"] == "reminders-1" and one["data"]["due"], (s, one))
    s, one = await get(client, "/api/dashboard/widget?id=cameras-1", "robin_tablet")
    check("widget: an addon its owner lacks: 403 not_connected", s == 403 and one == {"error": "not_connected",
                                                                                         "addon": "cameras"}, (s, one))
    s, one = await get(client, "/api/dashboard/widget?id=nope-1", "robin_tablet")
    check("widget: an id not on the dashboard: 404", s == 404, s)
    s, one = await get(client, f"/api/dashboard/widget?device={rt}&id=reminders-1", "gal_tablet")
    check("widget: another user's device: 404", s == 404, s)
    s, shop = await get(client, f"/api/dashboard/widget?device={DEV['robin_tv'].id}&id=calendar-1", "robin")
    check("widget: by device id and widget id", s == 200 and shop["data"]["days"], (s, shop))

    s, cat = await get(client, f"/api/dashboard/catalog?device={rt}", "robin")
    keys = [w["key"] for w in cat["widgets"]]
    captured["catalog"] = {"status": s, "body": cat}
    check("catalog: the presets and the widgets Robin can place (not cameras: he lacks the addon)",
          s == 200 and [p["key"] for p in cat["presets"]] == list(dashboard.PRESETS) and "cameras" not in keys
          and {"clock", "now", "reminders", "shopping", "calendar", "money", "ha_climate"} <= set(keys)
          and cat["default_preset"] == "bedroom", (s, keys))
    s, cat = await get(client, f"/api/dashboard/catalog?device={gt}", "gal")
    check("catalog: Gal's offers her cameras, not calendar / money / Home Assistant",
          s == 200 and "cameras" in [w["key"] for w in cat["widgets"]]
          and not {"calendar", "money", "ha_climate"} & {w["key"] for w in cat["widgets"]}, cat)
    s, _ = await get(client, f"/api/dashboard/catalog?device={gt}", "maya")
    check("catalog: someone else's device: 404", s == 404, s)


async def put(client: TestClient, device_id: str, who: str, body: Any) -> Tuple[int, Any]:
    r = await client.put(f"/api/dashboard?device={device_id}", headers=auth(who), json=body)
    return r.status, await r.json()


async def test_write(client: TestClient, captured: Dict[str, Any]) -> None:
    rt, tv, gt = DEV["robin_tablet"].id, DEV["robin_tv"].id, DEV["gal_tablet"].id
    s, body = await put(client, rt, "robin_tablet", {"preset": "kitchen", "widgets": None})
    check("save: a tablet's own token cannot change its dashboard (403)", s == 403, (s, body))
    s, body = await put(client, rt, "gal", {"preset": "kitchen", "widgets": None})
    check("save: another member's device: 404", s == 404, (s, body))
    s, body = await put(client, tv, "robin", {"preset": "kitchen", "widgets": None})
    check("save: choosing a preset", s == 200 and body["preset"] == "kitchen" and body["edited"] is False
          and [w["widget"] for w in body["widgets"]][:3] == ["clock", "now", "shopping"], body)
    edited = [{"id": "shopping-1", "widget": "shopping", "size": "medium"},
              {"widget": "ha_climate", "size": "large", "settings": {"entity": "climate.office"}},
              {"id": "clock-1", "widget": "clock", "size": "small"},
              {"widget": "cameras", "settings": {"camera": "front_door"}}]
    s, body = await put(client, tv, "robin", {"preset": "kitchen", "widgets": edited})
    captured["save"] = {"status": s, "body": body}
    check("save: edits (order, size, settings, a widget added, others removed); new ones get ids, given ids kept",
          s == 200 and body["edited"] is True
          and [(w["id"], w["widget"], w["size"]) for w in body["widgets"]]
          == [("shopping-1", "shopping", "medium"), ("ha_climate-1", "ha_climate", "large"),
              ("clock-1", "clock", "small"), ("cameras-1", "cameras", "large")]
          and body["widgets"][1]["settings"] == {"entity": "climate.office"}, body)
    s, tvd = await get(client, f"/api/dashboard?device={tv}", "robin")
    check("save: the saved settings reach the widget (the chosen climate device); the shopping list is Robin's",
          by_widget(tvd, "ha_climate")["data"]["entity_id"] == "climate.office"
          and [i["name"] for i in by_widget(tvd, "shopping")["data"]["items"]] == ["Milk"]
          and by_widget(tvd, "cameras")["error"] == "not_connected", tvd)
    s, own = await get(client, "/api/dashboard", "robin_tablet")
    check("save: per device: the tablet still shows its bedroom preset", own["preset"] == "bedroom" and not own["edited"])
    for bad, why in (([{"widget": "clock", "size": "huge"}], "a size it lacks"),
                     ([{"widget": "clock", "settings": {"color": "red"}}], "a setting it does not declare"),
                     ([{"widget": "nope"}], "an unknown widget"),
                     ([{"widget": "ha_climate", "settings": {"entity": ["x"]}}], "a setting that is not text"),
                     ([{"widget": "clock"}] * 25, "too many widgets"), ("clock", "not a list")):
        s, body = await put(client, tv, "robin", {"preset": "kitchen", "widgets": bad})
        check(f"save: refused (400 invalid) — {why}", s == 400 and body["error"] == "invalid", (s, body))
    s, body = await put(client, tv, "robin", {"preset": "garage", "widgets": None})
    check("save: an unknown preset is refused", s == 400 and body["error"] == "invalid", body)
    s, body = await put(client, gt, "robin", {"preset": "hall", "widgets": [{"widget": "now"}]})
    check("save: an admin may edit Gal's tablet (who may manage it); it stays Gal's", s == 200
          and [w["widget"] for w in body["widgets"]] == ["now"], body)
    s, gal_d = await get(client, "/api/dashboard", "gal_tablet")
    check("save: Gal's tablet shows the edit with her own data", [w["widget"] for w in gal_d["widgets"]] == ["now"]
          and gal_d["widgets"][0]["data"] is not None, gal_d)
    with get_db_connection() as conn:
        rows = {r["device_id"]: r["user_id"] for r in conn.execute("SELECT device_id, user_id FROM dashboards")}
    check("save: each row is its device's owner's", rows == {tv: ROBIN.id, gt: GAL.id}, rows)
    s, body = await put(client, tv, "robin", {"preset": "kitchen", "widgets": None})
    check("save: back to the preset's own (widgets null)", s == 200 and body["edited"] is False, body)
    devices.remove(DEV["robin_tv"])
    with get_db_connection() as conn:
        left = [r[0] for r in conn.execute("SELECT device_id FROM dashboards")]
    check("save: removing the device removes its dashboard", left == [gt], left)


async def test_page(client: TestClient) -> None:
    r = await client.get("/dashboard?zoom=98", headers=auth("robin_tablet"))
    html = await r.text()
    check("page: served no-cache, its files versioned by the web release",
          r.status == 200 and r.headers.get("Cache-Control") == "no-cache" and "/static/dashboard.js?v=" in html
          and "/static/dana.js?v=" in html, (r.status, html[:200]))
    r = await client.get(f"/dashboard?device={DEV['robin_tablet'].id}", headers=auth("maya"))
    check("page: a device the caller may not read: 404", r.status == 404, r.status)
    r = await client.get("/dashboard", headers=auth("robin"))
    check("page: without a device it still loads (it lists the user's tablets and TVs)", r.status == 200, r.status)
    statics = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    check("page: the old hard-coded bedroom dashboard is gone (no camera / climate constants in the page)",
          "climate." not in open(os.path.join(statics, "dashboard.js"), encoding="utf-8").read())


def shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(v) for v in value]
    return type(value).__name__ if value is not None else None


def write_fixture(captured: Dict[str, Any]) -> None:
    text = json.dumps(captured, indent=1, ensure_ascii=False, sort_keys=True)
    for key, dev in DEV.items():
        text = text.replace(dev.id, f"device-{key.replace('_', '-')}")
    for u in (ROBIN, GAL, MAYA):
        text = text.replace(u.id, f"user-{u.name.split()[0].lower()}")
    release = captured["bedroom_tablet"]["body"]["release"]
    text = text.replace(release, "RELEASE").replace(captured["bedroom_tablet"]["body"]["time"], "2026-11-22T08:00:00+00:00")
    text = text.replace(str(HA.server.port), "18123").replace(testenv.scratch_dir(), "/scratch")
    for n, found in enumerate(dict.fromkeys(re.findall(r'"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"',
                                                      text)), 1):
        text = text.replace(found, f'"task-{n}"')
    check("fixture: no user id, port or scratch path in it", not any(u.id in text for u in (ROBIN, GAL, MAYA))
          and testenv.scratch_dir() not in text)
    if os.getenv("WRITE_WEB_FIXTURE"):
        with open(FIXTURE, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"wrote {FIXTURE}")
        return
    with open(FIXTURE, encoding="utf-8") as f:
        stored = json.load(f)
    drift = [k for k in captured if shape(json.loads(text)[k]) != shape(stored.get(k))]
    check("fixture: tests/fixtures/dashboard_web.json has the payloads' current shape "
          "(regenerate with WRITE_WEB_FIXTURE=1)", not drift, drift)


async def main() -> None:
    await HA.start()
    store.connect(ROBIN, "homeassistant", credentials={"host": HA.url, "token": "tok-robin"},
                  settings={"phone_battery_sensor": "sensor.robin_phone_battery"})
    calendar_events.list_events = fake_events  # Google is not reached
    setup_devices()
    test_hook()
    test_presets()
    captured: Dict[str, Any] = {}
    async with TestClient(TestServer(build_app())) as client:
        await test_read(client, captured)
        await test_page(client)
        await test_write(client, captured)
    write_fixture(captured)
    await HA.server.close()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
