"""The caller and their addons over HTTP (docs/access.md, "Managing users").
Run: bin/test test_addons_api

``GET /api/me`` (``addons``, ``languages``, ``currency``), ``GET /api/addons``, ``GET`` / ``PUT`` / ``DELETE
/api/addons/{name}``, ``POST /api/addons/{name}/test`` and ``GET
/api/addons/{name}/options`` for the first admin (``API_TOKEN``) and two
members (DB tokens): payload shapes, the access level (``ops`` 403 for a
member), ``?user=`` only with ``manage_users``, validation (400 with the field),
credentials never returned, and that a change takes effect without a restart:
the tool list and the agent's instructions, the Home Assistant watcher
reconciling at once (the periodic tick set far away), the WhatsApp account
learned on connect and again after a credentials change, the default task
lists / shopping list made by ``on_connect``. The connection checks run
against in-process fakes (Home Assistant from testfakes.py, WAHA, Frigate) and
a faked Google client; the Home Assistant options come from the fake's
entities. No network beyond localhost.
"""

import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("addons_api_test")
os.environ["API_TOKEN"] = "addons-api-env-token"  # a test value: with it set, DB tokens are honoured
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials
os.environ["HA_WATCH_RECONCILE_SECONDS"] = "3600"  # the periodic tick never comes during the test
os.environ["GOOGLE_CLIENT_ID"] = "test-client"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
# A site addon that fails to load (listed as failed), and a catalog with one valid and one invalid entry.
_SITE = os.path.join(testenv.scratch_dir(), "site_addons")
os.makedirs(os.path.join(_SITE, "broken"))
with open(os.path.join(_SITE, "broken", "addon.py"), "w", encoding="utf-8") as _f:
    _f.write("raise ImportError('a missing package')\n")
os.environ["DANA_ADDON_DIRS"] = _SITE
_CATALOG = os.path.join(testenv.scratch_dir(), "catalog.json")
with open(_CATALOG, "w", encoding="utf-8") as _f:
    json.dump({"addons": [
        {"name": "weather", "title": "Weather", "description": "The weather where you are", "author": "Someone",
         "homepage": "https://example.invalid/weather", "tags": ["home"], "min_dana": "9.0.0",
         "source": {"repo": "https://example.invalid/dana-addons.git", "path": "weather", "ref": "a" * 40}},
        {"name": "later", "source": {"repo": "https://example.invalid/x.git", "path": "", "ref": "FILL-ME"}}]}, _f)
os.environ["DANA_CATALOG_FILE"] = _CATALOG

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()
testenv.connect_admin("homeassistant", credentials={"host": "http://127.0.0.1:9", "token": "admin-ha-token"})  # replaced below

from addons.tasks import api as tasks_api  # noqa: E402
from core import agent, addons_api  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from addons.calendar import client as cal_client  # noqa: E402
from core.addons import addresses, registry, store  # noqa: E402
from core.addons.addon import get_addon  # noqa: E402
from addons.homeassistant import watcher as ha_watcher  # noqa: E402
from addons.shopping import lists as shopping_lists  # noqa: E402
from addons.tasks import groups  # noqa: E402
from testfakes import FakeHA  # noqa: E402

results: List[Tuple[str, bool]] = []
bodies: List[str] = []  # every response body, checked for secrets at the end


def check(name: str, condition: Any, detail: Any = "") -> None:
    ok = bool(condition)
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n       {detail}" if detail and not ok else ""))


async def until(cond, timeout: float = 3.0) -> bool:
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return bool(cond())


ADMIN = users.first_admin()
BOB = users.create("Bob Builder")
CARL = users.create("Carl")
BOB_TOKEN, _ = users.issue_token(BOB.id, "bob")
CARL_TOKEN, _ = users.issue_token(CARL.id, "carl")
ADMIN_AUTH = {"Authorization": "Bearer addons-api-env-token"}
BOB_AUTH = {"Authorization": f"Bearer {BOB_TOKEN}"}
CARL_AUTH = {"Authorization": f"Bearer {CARL_TOKEN}"}
SECRETS = ["env-ha-token", "tok-a", "tok-b", "bob-wrong-token", "waha-key-1", "bob-refresh-token"]

HA_A = FakeHA("A", "tok-a", {
    "person.robin": ("home", {"friendly_name": "Robin"}),
    "zone.home": ("0", {"friendly_name": "Home"}),
    "weather.forecast_home": ("sunny", {"friendly_name": "Forecast Home"}),
}, {}, "Asia/Jerusalem")
HA_B = FakeHA("B", "tok-b", {
    "person.bob": ("home", {"friendly_name": "Bob"}),
    "person.wendy": ("away", {"friendly_name": "Wendy"}),
    "sensor.b_bt": ("1", {"friendly_name": "Bob BT", "connected_paired_devices": "['AA:BB (Bob Car)', 'CC:DD (Buds)']"}),
    "sensor.b_battery": ("80", {"friendly_name": "Bob Battery", "device_class": "battery"}),
    "sensor.b_temp": ("21", {"friendly_name": "Den temperature", "device_class": "temperature"}),
    "binary_sensor.b_interactive": ("on", {"friendly_name": "Bob screen"}),
    "weather.b": ("rainy", {"friendly_name": "Bob weather"}),
    "light.b_den": ("off", {"friendly_name": "B Den"}),
}, {}, "Europe/London")


class FakeWAHA:
    """A WAHA with one key and two sessions (``default`` working, ``other`` stopped)."""

    def __init__(self) -> None:
        self.key = "waha-key-1"
        app = web.Application()
        app.router.add_get("/api/sessions", self.sessions)
        self.server = TestServer(app)

    async def start(self) -> None:
        await self.server.start_server()
        self.url = str(self.server.make_url("/api"))

    async def sessions(self, request: web.Request) -> web.Response:
        if request.headers.get("X-Api-Key") != self.key:
            return web.json_response({"message": "unauthorized"}, status=401)
        return web.json_response([{"name": "default", "status": "WORKING", "me": {"id": "972500000001@c.us"}},
                                  {"name": "other", "status": "STOPPED", "me": {"id": "972500000002@c.us"}}])


class FakeFrigate:
    def __init__(self) -> None:
        app = web.Application()
        app.router.add_get("/api/version", lambda r: web.Response(text="0.14.1"))
        self.server = TestServer(app)

    async def start(self) -> None:
        await self.server.start_server()
        self.url = str(self.server.make_url("")).rstrip("/")


class RefreshError(Exception):
    """Named like google.auth's: the check reports a refused refresh token."""


class _List:
    def __init__(self, token: str) -> None:
        self.token = token

    def list(self, **kw: Any) -> "_List":
        return self

    def execute(self) -> Dict[str, Any]:
        if self.token == "revoked-token":
            raise RefreshError("invalid_grant")
        return {"items": [{"id": "primary", "summary": "Bob"}]}


class _FakeCalendarService:
    def __init__(self, token: str) -> None:
        self.token = token

    def calendarList(self) -> _List:
        return _List(self.token)


cal_client.build = lambda name, version, credentials=None, **kw: _FakeCalendarService(credentials.refresh_token)

WAHA = FakeWAHA()
FRIGATE = FakeFrigate()


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    addons_api.setup(app)
    app.router.add_get("/api/task-groups", tasks_api.get_task_groups_api)
    return app


async def call(client: TestClient, method: str, path: str, auth: Dict[str, str],
               body: Any = None, raw: Optional[str] = None) -> Tuple[int, Any]:
    kw: Dict[str, Any] = {"headers": auth}
    if raw is not None:
        kw["data"] = raw
    elif body is not None:
        kw["json"] = body
    r = await client.request(method, path, **kw)
    text = await r.text()
    bodies.append(text)
    try:
        return r.status, json.loads(text)
    except ValueError:
        return r.status, text


def by_name(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {i["name"]: i for i in payload["addons"]}


# --- 1. Who I am, what I may connect -------------------------------------------------------------

async def test_me_and_list(client: TestClient) -> None:
    status, me = await call(client, "GET", "/api/me", ADMIN_AUTH)
    check("me: the first admin", status == 200 and me["id"] == ADMIN.id and me["access"] == "admin"
          and me["capabilities"] == ["ops", "manage_users", "see_everyone", "manage_server"]
          and me["addons"][0] == "essentials" and "homeassistant" in me["addons"] and "ops" not in me["addons"]
          and me["languages"] == ["he", "en"] and me["currency"] == "ILS" and "kinds" not in me, me)
    status, me = await call(client, "GET", "/api/me", BOB_AUTH)
    check("me: a member with nothing connected (the server's languages and currency)", status == 200 and me == {
        "id": BOB.id, "name": "Bob Builder", "access": "member", "capabilities": [], "addons": ["essentials"],
        "languages": ["en"], "currency": "USD"}, me)
    status, _ = await call(client, "GET", "/api/me", {})
    check("me: needs credentials", status == 401)

    status, body = await call(client, "GET", "/api/addons", ADMIN_AUTH)
    addons = by_name(body)
    ha = addons.get("homeassistant", {})
    check("list: every addon for the admin, essentials (everyone has it) and ops included",
          status == 200 and body["user_id"] == ADMIN.id and "ops" in addons
          and list(addons)[:4] == ["essentials", "tasks", "shopping", "knowledge"]
          and body["restart_needed"] is False and body["can_manage"] is True and body["pending"] == [], list(addons))
    ess = addons["essentials"]
    check("list: essentials is on for everyone and cannot be turned off",
          ess["everyone"] is True and ess["state"] == "on" and ess["connected"] is True
          and ess["can_change"] is False and ess["why_not"] == "Every user has it", ess)
    phone_pages = addons["phone"]["web"]
    check("list: an addon's pages, each with its id, file URLs and navigation entry (phone: Inbox and Calls)",
          phone_pages == [
              {"id": "inbox", "panel": "/addons/phone/web/inbox.js", "style": "/addons/phone/web/inbox.css",
               "nav": {"label": "Inbox", "icon": "inbox", "order": 30}},
              {"id": "calls", "panel": "/addons/phone/web/calls.js", "style": "/addons/phone/web/calls.css",
               "nav": {"label": "Calls", "icon": "phone", "order": 90}}]
          and [p["id"] for p in addons["tasks"]["web"]] == ["tasks"], phone_pages)
    check("list: one connection's shape (credentials only as set / not set)",
          set(ha) == {"name", "title", "description", "version", "source", "state", "needs", "everyone", "web",
                      "worker", "connected", "requires", "can_change", "why_not", "credentials",
                      "settings", "account", "check", "options", "connect_url", "connect_label",
                      "icon", "features", "pages", "setup", "examples", "disconnect_warning"}
          and ha["icon"] == "house" and ha["title"] == "Home Assistant" and ha["pages"] == []
          and "Turn off the living room lights" in ha["examples"] and ha["disconnect_warning"] is None
          and addons["shopping"]["pages"] == ["Shopping"]
          and addons["money"]["disconnect_warning"].startswith("Your saved bank and card logins")
          and addons["whatsapp"]["setup"][0].startswith("Run a WAHA server")
          and isinstance(ha["title"], str) and ha["source"] == "official" and ha["state"] == "on"
          and ha["needs"] == [] and ha["everyone"] is False and ha["web"] == [] and ha["worker"] is False
          and addons["money"]["worker"] is False and addons["money_il"]["worker"] is True
          and addons["money_il"]["needs"] == ["money"]
          and ha["connect_url"] is None and addons["calendar"]["connect_url"] == "/api/google/connect"
          and ha["connect_label"] is None and addons["calendar"]["connect_label"] is None
          and addons["telegram_account"]["connect_label"] == "Connect Telegram"
          and ha["connected"] is True and ha["check"] is True and ha["options"] is True
          and ha["can_change"] is True and ha["why_not"] is None
          and ha["credentials"] == [
              {"name": "host", "label": "Address",
               "description": "Home Assistant base URL, e.g. https://home.example.com",
               "help": get_addon("homeassistant").credentials[0].help, "url": None,
               "placeholder": "https://home.example.com",
               "required": True, "set": True, "type": "address", "secret": False, "value": "http://127.0.0.1:9",
               "can_change": True, "why_not": None,
               "rule": "An http:// or https:// address, without a user name, ? or #."},
              {"name": "token", "label": "Access token", "description": "Long-lived access token",
               "help": get_addon("homeassistant").credentials[1].help,
               "url": "https://www.home-assistant.io/docs/authentication/#your-account-profile", "placeholder": "",
               "required": True, "set": True,
               "type": "text", "secret": True, "can_change": True, "why_not": None, "rule": None}]
          and {"name": "weather_entity", "label": "Weather",
               "description": "Weather entity for the wake briefing and the Now screen",
               "help": get_addon("homeassistant").field("weather_entity").help, "url": None,
               "placeholder": "weather.forecast_home",
               "default": "weather.forecast_home", "value": "weather.forecast_home", "type": "text",
               "can_change": True, "why_not": None, "rule": None} in ha["settings"], ha)
    check("list: ops says what it requires", addons["ops"]["requires"] == "ops" and addons["tasks"]["requires"] is None)
    status, body = await call(client, "GET", "/api/addons", BOB_AUTH)
    addons = by_name(body)
    check("list: a member sees no ops and nothing connected but essentials; no admin fields",
          status == 200 and "ops" not in addons and [n for n, i in addons.items() if i["connected"]] == ["essentials"]
          and addons["tasks"]["state"] == "available" and addons["cameras"]["state"] == "off"
          and body["can_manage"] is False and "pending" not in body
          and addons["calendar"]["settings"][0] == {"name": "calendar_id", "label": "Calendar for new events",
                                                   "description": "Calendar new events go to",
                                                   "help": get_addon("calendar").settings[0].help, "url": None,
                                                   "placeholder": "primary",
                                                   "default": "primary", "value": "primary", "type": "text",
                                                   "can_change": True, "why_not": None, "rule": None}
          and all(not c["set"] for c in addons["homeassistant"]["credentials"]), addons.get("calendar"))

    status, body = await call(client, "GET", "/api/addons/ops", BOB_AUTH)
    check("ops: 403 for a member (read)", status == 403 and body == {"error": "forbidden", "needs": "ops"})
    status, body = await call(client, "PUT", "/api/addons/ops", BOB_AUTH, {})
    check("ops: 403 for a member (connect)", status == 403 and not store.is_connected(BOB, "ops"))
    status, body = await call(client, "GET", "/api/addons/nope", BOB_AUTH)
    check("unknown addon: 404", status == 404 and body["error"] == "unknown_addon")
    status, body = await call(client, "GET", "/api/addons/essentials", BOB_AUTH)
    check("essentials: readable, every user has it", status == 200 and body["everyone"] is True and body["state"] == "on")
    status, _ = await call(client, "PUT", "/api/addons/essentials", BOB_AUTH, {})
    check("essentials: not connectable (404)", status == 404)


# --- 2. Connect, update, validation --------------------------------------------------------------

async def test_put(client: TestClient) -> None:
    path = "/api/addons/homeassistant"
    status, body = await call(client, "PUT", path, BOB_AUTH, {"credentials": {"host": HA_B.url}})
    check("connect: a missing required credential → 400 naming it",
          status == 400 and body == {"error": "invalid", "field": "token",
                                     "message": "Connecting homeassistant needs the credential 'token'."}
          and not store.is_connected(BOB, "homeassistant"), body)
    status, body = await call(client, "PUT", path, BOB_AUTH,
                              {"credentials": {"host": HA_B.url, "token": "tok-b"}, "settings": {"colour": "red"}})
    check("connect: an unknown setting → 400 naming it", status == 400 and body["field"] == "colour")
    status, body = await call(client, "PUT", path, BOB_AUTH, {"credentials": {"host": HA_B.url, "token": 5}})
    check("connect: a credential that is not text → 400", status == 400 and body["field"] == "token")
    status, body = await call(client, "PUT", path, BOB_AUTH, {"credentials": {"host": HA_B.url, "token": "t"},
                                                              "account": "x"})
    check("connect: an unknown top-level field → 400", status == 400 and body["field"] == "account")
    status, body = await call(client, "PUT", path, BOB_AUTH, raw="[1, 2]")
    check("connect: a body that is not an object → 400", status == 400 and body["field"] == "body")
    status, body = await call(client, "PUT", path, BOB_AUTH, raw="{not json")
    check("connect: a body that is not JSON → 400", status == 400 and body["field"] == "body")
    check("no tools while not connected", not registry.has_tool(BOB, "call_ha_service")
          and "HomeAssistant control" not in agent.instructions(BOB))

    status, body = await call(client, "PUT", path, BOB_AUTH, {"credentials": {"host": HA_B.url, "token": "tok-b"},
                                                              "settings": {"person": "person.bob"}})
    settings = {s["name"]: s["value"] for s in body.get("settings", [])}
    check("connect: 201, connected, credentials set, settings merged with defaults",
          status == 201 and body["connected"] is True and all(c["set"] for c in body["credentials"])
          and settings["person"] == "person.bob" and settings["weather_entity"] == "weather.forecast_home", body)
    check("connect: takes effect at once (tools, the agent's instructions)",
          registry.has_tool(BOB, "call_ha_service") and "HomeAssistant control" in agent.instructions(BOB))
    status, body = await call(client, "PUT", path, BOB_AUTH, {"settings": {"car_sensor": "sensor.b_bt",
                                                                           "car_device": "Bob Car"}})
    conn = store.get(BOB, "homeassistant")
    check("update: 200, settings merged, credentials kept",
          status == 200 and conn.settings["person"] == "person.bob" and conn.settings["car_device"] == "Bob Car"
          and conn.credentials["token"] == "tok-b", body)
    status, body = await call(client, "PUT", path, BOB_AUTH, {"settings": {"car_device": None}})
    check("update: a null setting goes back to its default", status == 200
          and store.get(BOB, "homeassistant").settings["car_device"] is None)
    status, body = await call(client, "PUT", path, BOB_AUTH, {"credentials": {"token": ""}})
    check("update: emptying a required credential → 400", status == 400 and body["field"] == "token"
          and store.get(BOB, "homeassistant").credentials["token"] == "tok-b")
    status, body = await call(client, "GET", path, BOB_AUTH)
    check("get: one addon", status == 200 and body["name"] == "homeassistant" and body["connected"])


# --- 3. Another user (?user=) --------------------------------------------------------------------

async def test_other_user(client: TestClient) -> None:
    status, body = await call(client, "GET", f"/api/addons?user={BOB.id}", ADMIN_AUTH)
    addons = by_name(body)
    check("?user=: the Google sign-in is offered only for the caller's own addons",
          status == 200 and by_name(body)["calendar"]["connect_url"] is None, by_name(body).get("calendar"))
    check("?user=: an admin reads a member's addons (their level: no ops)",
          status == 200 and body["user_id"] == BOB.id and addons["homeassistant"]["connected"] and "ops" not in addons)
    status, body = await call(client, "GET", f"/api/addons?user={ADMIN.id}", BOB_AUTH)
    check("?user=: a member may not (403 manage_users)", status == 403 and body == {"error": "forbidden",
                                                                                    "needs": "manage_users"})
    status, body = await call(client, "GET", f"/api/addons/homeassistant?user={ADMIN.id}", BOB_AUTH)
    check("?user=: not on one addon either", status == 403)
    status, body = await call(client, "GET", f"/api/addons?user={BOB.id}", BOB_AUTH)
    check("?user=: one's own id is fine", status == 200 and body["user_id"] == BOB.id)
    status, body = await call(client, "GET", "/api/addons?user=nobody", ADMIN_AUTH)
    check("?user=: an unknown user → 404", status == 404 and body["error"] == "user_not_found")
    status, body = await call(client, "GET", f"/api/addons/ops?user={CARL.id}", ADMIN_AUTH)
    check("?user=: the target's level counts (ops for a member → 403)", status == 403)

    check("Carl has no lists before", groups.list_groups(CARL) == [] or not store.is_connected(CARL, "tasks"))
    status, body = await call(client, "PUT", f"/api/addons/tasks?user={CARL.id}", ADMIN_AUTH, {})
    status2, lists = await call(client, "GET", "/api/task-groups", CARL_AUTH)
    check("?user=: an admin connects tasks for Carl; on_connect made his default lists at once",
          status == 201 and store.is_connected(CARL, "tasks") and status2 == 200 and len(lists) >= 1
          and any(g.get("name") == "General" for g in lists), lists)
    status, body = await call(client, "PUT", "/api/addons/shopping", CARL_AUTH, {})
    check("connect shopping: a member connects it themselves; his own list made by on_connect",
          status == 201 and shopping_lists.own_list_id(CARL) is not None
          and shopping_lists.active_list(CARL).owner_id == CARL.id
          and [f["name"] for f in body["settings"]] == ["active_list"], body)


# --- 4. Connection checks and the Home Assistant options -----------------------------------------

async def test_checks_and_options(client: TestClient) -> None:
    status, body = await call(client, "POST", "/api/addons/homeassistant/test", BOB_AUTH)
    check("test: Home Assistant answers with Bob's token", status == 200 and body == {
        "name": "homeassistant", "checked": True, "ok": True, "reason": ""}, body)
    check("test: only Bob's Home Assistant, with his token", ("/api/config", "tok-b") in HA_B.requests
          and not any(p == "/api/config" for p, _ in HA_A.requests))
    await call(client, "PUT", "/api/addons/homeassistant", BOB_AUTH, {"credentials": {"token": "bob-wrong-token"}})
    status, body = await call(client, "POST", "/api/addons/homeassistant/test", BOB_AUTH)
    check("test: a refused token → ok false with a short reason (no token)",
          status == 200 and body["ok"] is False and body["reason"] == "Home Assistant refused the token (HTTP 401).",
          body)
    await call(client, "PUT", "/api/addons/homeassistant", BOB_AUTH, {"credentials": {"token": "tok-b"}})
    status, body = await call(client, "POST", "/api/addons/whatsapp/test", BOB_AUTH)
    check("test: not connected → 403 not_connected", status == 403 and body == {"error": "not_connected",
                                                                                "addon": "whatsapp"})
    status, body = await call(client, "POST", "/api/addons/tasks/test", CARL_AUTH)
    check("test: an addon without a check", status == 200 and body["checked"] is False and body["ok"] is None)

    status, body = await call(client, "GET", "/api/addons/homeassistant/options", BOB_AUTH)
    s = body.get("settings", {})
    check("options: candidates from Bob's own Home Assistant",
          status == 200 and body["name"] == "homeassistant"
          and s["person"] == [{"value": "person.bob", "label": "Bob"}, {"value": "person.wendy", "label": "Wendy"}]
          and s["car_sensor"] == [{"value": "sensor.b_bt", "label": "Bob BT"}]
          and s["car_device"] == [{"value": "AA:BB (Bob Car)", "label": "AA:BB (Bob Car)"},
                                  {"value": "CC:DD (Buds)", "label": "CC:DD (Buds)"}]
          and s["phone_interactive_sensor"] == [{"value": "binary_sensor.b_interactive", "label": "Bob screen"}]
          and s["phone_battery_sensor"] == [{"value": "sensor.b_battery", "label": "Bob Battery"}]
          and s["weather_entity"] == [{"value": "weather.b", "label": "Bob weather"}], s)
    check("options: nothing of the other Home Assistant", "person.robin" not in json.dumps(s))
    store.update(ADMIN, "homeassistant", credentials={"host": HA_A.url, "token": "tok-a"})
    status, body = await call(client, "GET", "/api/addons/homeassistant/options", ADMIN_AUTH)
    check("options: the admin's from theirs", status == 200
          and body["settings"]["person"] == [{"value": "person.robin", "label": "Robin"}]
          and body["settings"]["car_sensor"] == [] and ("/api/states", "tok-a") in HA_A.requests)
    status, body = await call(client, "GET", "/api/addons/homeassistant/options", CARL_AUTH)
    check("options: not connected → 403", status == 403 and body["error"] == "not_connected")
    status, body = await call(client, "GET", "/api/addons/tasks/options", CARL_AUTH)
    check("options: an addon without any → 404", status == 404 and body["error"] == "no_options")
    HA_B.down = True
    try:
        status, body = await call(client, "GET", "/api/addons/homeassistant/options", BOB_AUTH)
        check("options: Home Assistant down → 502", status == 502 and body["error"] == "unavailable")
        status, body = await call(client, "POST", "/api/addons/homeassistant/test", BOB_AUTH)
        check("test: Home Assistant down → its status", body["ok"] is False and "503" in body["reason"], body)
    finally:
        HA_B.down = False

    # cameras needs an admin to change (docs/access.md, "Security rules"): the admin sets Bob's Frigate
    status, body = await call(client, "PUT", "/api/addons/cameras", BOB_AUTH, {"settings": {"url": FRIGATE.url}})
    check("cameras: a member's own PUT → 403 (an admin changes it)",
          status == 403 and body["error"] == "forbidden" and store.get(BOB, "cameras") is None, body)
    status, body = await call(client, "PUT", f"/api/addons/cameras?user={BOB.id}", ADMIN_AUTH,
                              {"settings": {"url": FRIGATE.url}})
    check("cameras: the admin connects it for Bob (?user=)", status == 201, body)
    status, body = await call(client, "POST", "/api/addons/cameras/test", BOB_AUTH)
    check("test: Frigate answers", status == 200 and body["ok"] is True, body)
    await call(client, "PUT", f"/api/addons/cameras?user={BOB.id}", ADMIN_AUTH,
               {"settings": {"url": "http://127.0.0.1:9"}})
    status, body = await call(client, "POST", "/api/addons/cameras/test", BOB_AUTH)
    check("test: Frigate unreachable", body["ok"] is False and body["reason"].startswith("Could not reach Frigate"))

    status, _ = await call(client, "PUT", "/api/addons/calendar", BOB_AUTH,
                           {"credentials": {"refresh_token": "bob-refresh-token"}})
    status, body = await call(client, "POST", "/api/addons/calendar/test", BOB_AUTH)
    check("test: Google Calendar reads the calendar list", body == {"name": "calendar", "checked": True, "ok": True,
                                                                    "reason": ""}, body)
    await call(client, "PUT", "/api/addons/calendar", BOB_AUTH, {"credentials": {"refresh_token": "revoked-token"}})
    status, body = await call(client, "POST", "/api/addons/calendar/test", BOB_AUTH)
    check("test: a refused refresh token", body["ok"] is False and "refused the refresh token" in body["reason"], body)


# --- 5. No restart needed: the watcher, the WhatsApp account, disconnect -------------------------

async def test_live_effects(client: TestClient) -> None:
    fire = AsyncMock()
    watchers = ha_watcher.Watchers(fire)
    task = asyncio.create_task(watchers.run_forever())
    try:
        check("watcher: Bob's starts (person set)", await until(lambda: BOB.id in watchers.running()))
        await call(client, "PUT", "/api/addons/homeassistant", BOB_AUTH, {"settings": {"person": "person.wendy"}})
        check("watcher: a settings change is picked up at once (the tick is an hour away)",
              await until(lambda: BOB.id in watchers.running()
                          and watchers.running()[BOB.id].person == "person.wendy"))
        status, body = await call(client, "DELETE", "/api/addons/homeassistant", BOB_AUTH)
        check("disconnect: 200, connected false, disconnected true",
              status == 200 and body["connected"] is False and body["disconnected"] is True
              and not any(c["set"] for c in body["credentials"]), body)
        check("watcher: stops at once on disconnect", await until(lambda: BOB.id not in watchers.running()))
        check("disconnect: tools and instructions follow at once", not registry.has_tool(BOB, "call_ha_service")
              and "HomeAssistant control" not in agent.instructions(BOB))
        status, body = await call(client, "DELETE", "/api/addons/homeassistant", BOB_AUTH)
        check("disconnect: twice → disconnected false", status == 200 and body["disconnected"] is False)
        await call(client, "PUT", "/api/addons/homeassistant", BOB_AUTH,
                   {"credentials": {"host": HA_B.url, "token": "tok-b"}, "settings": {"person": "person.bob"}})
        check("watcher: connecting again starts it at once", await until(lambda: BOB.id in watchers.running()))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    status, body = await call(client, "PUT", "/api/addons/whatsapp", BOB_AUTH,
                              {"credentials": {"url": WAHA.url, "api_key": "waha-key-1", "session": "default"}})
    check("whatsapp: connected (201)", status == 201)
    check("whatsapp: the account learned right after connecting",
          await until(lambda: (store.get(BOB, "whatsapp").account or "") == "972500000001@c.us"))
    status, body = await call(client, "GET", "/api/addons/whatsapp", BOB_AUTH)
    check("whatsapp: the account is in the payload, the key only as set",
          body["account"] == "972500000001@c.us" and {"name": "api_key", "label": "WAHA API key",
                                                      "description": "WAHA API key",
                                                      "help": get_addon("whatsapp").credentials[1].help, "url": None,
                                                      "placeholder": "", "required": False, "set": True, "type": "text", "secret": True,
                                                      "can_change": True, "why_not": None, "rule": None}
          in body["credentials"], body)
    status, body = await call(client, "POST", "/api/addons/whatsapp/test", BOB_AUTH)
    check("test: WAHA answers, session working", body["ok"] is True, body)
    await call(client, "PUT", "/api/addons/whatsapp", BOB_AUTH, {"credentials": {"session": "other"}})
    check("whatsapp: another session → its account, learned again at once",
          await until(lambda: (store.get(BOB, "whatsapp").account or "") == "972500000002@c.us"))
    status, body = await call(client, "POST", "/api/addons/whatsapp/test", BOB_AUTH)
    check("test: a stopped session is reported", body["ok"] is False and "STOPPED" in body["reason"], body)
    await call(client, "PUT", "/api/addons/whatsapp", BOB_AUTH, {"credentials": {"api_key": None}})
    check("update: null removes an optional credential", "api_key" not in store.get(BOB, "whatsapp").credentials)
    status, body = await call(client, "POST", "/api/addons/whatsapp/test", BOB_AUTH)
    check("test: WAHA refusing the missing key", body["ok"] is False and "401" in body["reason"], body)


# --- 6. What the caller may change, addresses, the users list --------------------------------------

def field(payload: Dict[str, Any], name: str) -> Dict[str, Any]:
    return next((f for f in (*payload.get("credentials", []), *payload.get("settings", [])) if f["name"] == name), {})


async def test_rules_and_users(client: TestClient) -> None:
    # The admin on their own: everything changeable, the address shown, secrets only as set.
    status, body = await call(client, "GET", "/api/addons", ADMIN_AUTH)
    addons = by_name(body)
    host, token = field(addons["homeassistant"], "host"), field(addons["homeassistant"], "token")
    check("rules: the admin's Home Assistant address is shown (normalised), the token only as set",
          host.get("value") == addresses.normalize(HA_A.url) and host["secret"] is False and host["type"] == "address"
          and token["secret"] is True and "value" not in token and token["set"] is True, (host, token))
    check("rules: the admin may change every addon and field of their own",
          all(k["can_change"] and k["why_not"] is None for k in addons.values()
              if not k["everyone"] and k["state"] != "failed")
          and all(f["can_change"] for k in addons.values() for f in (*k["credentials"], *k["settings"])),
          [(k["name"], f["name"]) for k in addons.values() for f in (*k["credentials"], *k["settings"])
           if not f["can_change"]])
    check("rules: the shopping addon has one setting, the active list",
          [f["name"] for f in addons["shopping"]["settings"]] == ["active_list"]
          and addons["shopping"]["credentials"] == [], addons["shopping"])

    # A member on their own.
    status, body = await call(client, "GET", "/api/addons", BOB_AUTH)
    addons = by_name(body)
    cams = addons["cameras"]
    check("rules: a member may not change cameras (addon and its field), with the reason",
          cams["can_change"] is False and cams["why_not"] == "Set by an admin"
          and field(cams, "url")["can_change"] is False and field(cams, "url")["why_not"] == "Set by an admin"
          and field(cams, "url")["value"] == "http://127.0.0.1:9", cams)  # set by the admin in section 4
    status, refused = await call(client, "PUT", "/api/addons/cameras", BOB_AUTH, {"settings": {"url": FRIGATE.url}})
    check("rules: …and the store refuses it (403)", status == 403 and refused["needs"] == "manage_users")
    bob_host = field(addons["homeassistant"], "host")
    check("rules: a member's own address is shown to them; changeable within the allowed origins",
          bob_host.get("value") == addresses.normalize(HA_B.url) and bob_host["can_change"] is True
          and bob_host["rule"] == "Only an address an admin has allowed for this service.", bob_host)
    voice = field(addons["whatsapp"], "voice_telegram_chat_id")
    check("rules: a member without a Telegram id cannot set a Telegram chat",
          voice["can_change"] is False and voice["why_not"] == "Set by an admin", voice)
    users.add_telegram_id(BOB.id, 424242)
    status, body = await call(client, "GET", "/api/addons/whatsapp", BOB_AUTH)
    voice = field(body, "voice_telegram_chat_id")
    check("rules: …with one, they may, and the rule says which",
          voice["can_change"] is True and voice["rule"] == "Only your own Telegram id (your private chat with the bot).",
          voice)
    status, _ = await call(client, "PUT", "/api/addons/whatsapp", BOB_AUTH,
                           {"settings": {"voice_telegram_chat_id": 424242}})
    status2, refused = await call(client, "PUT", "/api/addons/whatsapp", BOB_AUTH,
                                  {"settings": {"voice_telegram_chat_id": 111}})
    check("rules: their own id saves, another is refused on the field", status == 200 and status2 == 400
          and refused["field"] == "voice_telegram_chat_id", refused)
    check("rules: the WAHA address is shown to its member, the key and session are not",
          field(body, "url").get("value") == addresses.normalize(WAHA.url) and "value" not in field(body, "session")
          and "value" not in field(body, "api_key"), body["credentials"])

    # No origin allowed for the addon (no admin uses one, nothing in the env): the address is set by an admin.
    allowed = os.environ["MEMBER_ALLOWED_ORIGINS"]
    os.environ["MEMBER_ALLOWED_ORIGINS"] = f"homeassistant={addresses.origin(HA_B.url)}"
    try:
        status, body = await call(client, "GET", "/api/addons/whatsapp", BOB_AUTH)
        url = field(body, "url")
        check("rules: no allowed origin for a member → the address is read-only, set by an admin",
              url["can_change"] is False and url["why_not"] == "Set by an admin" and url["value"], url)
        status, refused = await call(client, "PUT", "/api/addons/whatsapp", BOB_AUTH,
                                     {"credentials": {"url": WAHA.url}})
        check("rules: …and the store refuses it with the same reason",
              status == 400 and refused["field"] == "url" and "Set by an admin" in refused["message"], refused)
        status, body = await call(client, "GET", f"/api/addons/whatsapp?user={BOB.id}", ADMIN_AUTH)
        check("rules: an admin acting for him may change it", field(body, "url")["can_change"] is True)
    finally:
        os.environ["MEMBER_ALLOWED_ORIGINS"] = allowed

    # An admin acting for the member (?user=): the admin's rights, the member's values.
    status, body = await call(client, "GET", f"/api/addons?user={BOB.id}", ADMIN_AUTH)
    addons = by_name(body)
    check("rules: an admin acting for a member may change cameras and their Telegram chat",
          addons["cameras"]["can_change"] and field(addons["cameras"], "url")["can_change"]
          and field(addons["whatsapp"], "voice_telegram_chat_id")["can_change"], addons["cameras"])
    check("rules: an admin acting for a member sees the member's address",
          field(addons["homeassistant"], "host").get("value") == addresses.normalize(HA_B.url))

    # An address is not a secret, but never shown to another member.
    status, body = await call(client, "GET", f"/api/addons/homeassistant?user={BOB.id}", CARL_AUTH)
    check("address: another member gets 403 for it, without the value",
          status == 403 and addresses.normalize(HA_B.url) not in json.dumps(body))
    status, body = await call(client, "GET", "/api/addons", CARL_AUTH)
    check("address: nothing of Bob's in another member's own list",
          status == 200 and addresses.normalize(HA_B.url) not in json.dumps(body)
          and FRIGATE.url not in json.dumps(body), [field(i, "host") for i in body["addons"]][:1])

    # A stored value from before the rules: reported as a problem.
    with db.get_db_connection() as c:
        c.execute("UPDATE connections SET settings = ? WHERE user_id = ? AND addon = 'cameras'",
                  (json.dumps({"url": "http://127.0.0.1:9/?x=1"}), BOB.id))
        c.commit()
    store.invalidate()
    status, body = await call(client, "GET", "/api/addons/cameras", BOB_AUTH)
    check("problem: a stored address that is not valid is reported on its field",
          status == 200 and (body.get("problem") or {}).get("field") == "url", body)
    store.update(BOB, "cameras", settings={"url": FRIGATE.url})
    status, body = await call(client, "GET", "/api/addons/cameras", BOB_AUTH)
    check("problem: absent once valid", "problem" not in body, body)

    # The users list for the picker.
    status, body = await call(client, "GET", "/api/users", ADMIN_AUTH)
    listed = {u["id"]: u for u in body.get("users", [])}
    check("users: an admin lists everyone with id, name, access only",
          status == 200 and set(listed) >= {ADMIN.id, BOB.id, CARL.id}
          and all(set(u) == {"id", "name", "access"} for u in listed.values())
          and listed[BOB.id] == {"id": BOB.id, "name": "Bob Builder", "access": "member"}
          and listed[ADMIN.id]["access"] == "admin", body)
    status, body = await call(client, "GET", "/api/users", BOB_AUTH)
    check("users: a member gets 403 manage_users", status == 403 and body == {"error": "forbidden",
                                                                              "needs": "manage_users"})
    status, _ = await call(client, "GET", "/api/users", {})
    check("users: needs credentials", status == 401)


# --- 7. The first-run welcome -----------------------------------------------------------------------

async def test_welcome(client: TestClient) -> None:
    """Shown while a user has no optional addon on, per user; dismissing is theirs alone and can be undone."""
    welcome = {}
    for who, auth in (("admin", ADMIN_AUTH), ("bob", BOB_AUTH), ("carl", CARL_AUTH)):
        status, body = await call(client, "GET", "/api/addons", auth)
        welcome[who] = body["welcome"]
    check("welcome: the admin has Home Assistant on → not shown",
          welcome["admin"]["show"] is False and welcome["admin"]["dismissed"] is False, welcome["admin"])
    bob = welcome["bob"]
    check("welcome: a member with nothing on is welcomed, with the addons they can turn on",
          bob["show"] is True and bob["dismissed"] is False and bob["suggested"][:3] == ["homeassistant", "whatsapp",
                                                                                     "calendar"]
          and len(bob["suggested"]) <= addons_api.WELCOME_SUGGESTIONS
          and not {"cameras", "ops", "money_il", "tasks", "essentials"} & set(bob["suggested"]), bob)
    check("welcome: Carl too", welcome["carl"]["show"] is True, welcome["carl"])
    store.connect(CARL, "tasks")  # an addon every new user gets does not count
    status, body = await call(client, "GET", "/api/addons", CARL_AUTH)
    check("welcome: a default addon on still welcomes", body["welcome"]["show"] is True, body["welcome"])
    store.disconnect(CARL, "tasks")
    status, body = await call(client, "POST", "/api/addons/welcome", BOB_AUTH, {"dismissed": True})
    check("welcome: Bob dismisses it", status == 200 and body["show"] is False and body["dismissed"] is True, body)
    status, carl = await call(client, "GET", "/api/addons", CARL_AUTH)
    status, bob_list = await call(client, "GET", "/api/addons", BOB_AUTH)
    check("welcome: dismissed for Bob only; Carl still sees his, Bob's suggestions stay listed",
          carl["welcome"]["show"] is True and bob_list["welcome"]["show"] is False
          and bob_list["welcome"]["suggested"] == bob["suggested"]
          and all(i["state"] == "available" for i in bob_list["addons"] if i["name"] in bob["suggested"]))
    status, body = await call(client, "GET", f"/api/addons?user={CARL.id}", ADMIN_AUTH)
    check("welcome: an admin acting for Carl gets none", body["welcome"] == {"show": False, "dismissed": False,
                                                                           "suggested": []}, body["welcome"])
    status, body = await call(client, "POST", "/api/addons/welcome", BOB_AUTH, {"dismissed": False})
    check("welcome: Bob brings it back", status == 200 and body["show"] is True and body["dismissed"] is False, body)
    status, body = await call(client, "POST", "/api/addons/welcome", BOB_AUTH, {"dismissed": "yes"})
    check("welcome: a bad body is a 400 naming the field", status == 400 and body["field"] == "dismissed", body)
    status, _ = await call(client, "POST", "/api/addons/welcome", {}, {"dismissed": True})
    check("welcome: needs credentials", status == 401)


# --- 8. The web page's fixture: real payloads for test_web_addons.mjs ------------------------

WEB_FIXTURE = os.path.join(testenv.FIXTURES, "addons_web.json")


def shape(value: Any) -> Any:
    """The keys of a payload, recursively (lists by their items' shapes, values dropped)."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(v) for v in value]
    return type(value).__name__ if value is not None else None


def run_ports() -> List[str]:
    """The ports this run's fakes listen on (random)."""
    return [str(fake.server.port) for fake in (HA_A, HA_B, WAHA, FRIGATE)]


def deterministic(text: str) -> str:
    """The fixture's text without what changes from run to run: the fakes' ports, the scratch folder, user ids."""
    for fake, port in ((HA_A, 18123), (HA_B, 18124), (WAHA, 13000), (FRIGATE, 15000)):
        text = text.replace(f"127.0.0.1:{fake.server.port}", f"127.0.0.1:{port}")
    for user in users.list_users():
        text = text.replace(user.id, f"user-{user.name.split()[0].lower()}")
    return text.replace(testenv.scratch_dir(), "/scratch")


async def test_web_fixture(client: TestClient) -> None:
    # A conflict: Bob holds the "other" session's account again; an admin connects Carl to the same session.
    await call(client, "PUT", "/api/addons/whatsapp", BOB_AUTH, {"credentials": {"api_key": "waha-key-1"}})
    check("fixture: Bob's account learned again",
          await until(lambda: (store.get(BOB, "whatsapp").account or "") == "972500000002@c.us"))
    await call(client, "PUT", f"/api/addons/whatsapp?user={CARL.id}", ADMIN_AUTH,
               {"credentials": {"url": WAHA.url, "api_key": "waha-key-1", "session": "other"}})
    check("fixture: Carl's connection reports Bob's account → a conflict",
          await until(lambda: (store.get(CARL, "whatsapp").account_conflict or "") == "972500000002@c.us"))
    captured: Dict[str, Any] = {}
    for key, method, path, auth, body in [
        ("me_admin", "GET", "/api/me", ADMIN_AUTH, None),
        ("me_member", "GET", "/api/me", BOB_AUTH, None),
        ("users", "GET", "/api/users", ADMIN_AUTH, None),
        ("list_admin", "GET", "/api/addons", ADMIN_AUTH, None),
        ("list_member", "GET", "/api/addons", BOB_AUTH, None),
        ("list_admin_for_member", "GET", f"/api/addons?user={BOB.id}", ADMIN_AUTH, None),
        ("list_conflict", "GET", f"/api/addons?user={CARL.id}", ADMIN_AUTH, None),
        ("options_member", "GET", "/api/addons/homeassistant/options", BOB_AUTH, None),
        ("test_ok", "POST", "/api/addons/homeassistant/test", BOB_AUTH, None),
        ("test_failed", "POST", "/api/addons/whatsapp/test", CARL_AUTH, None),
        ("error_field", "PUT", "/api/addons/homeassistant", CARL_AUTH,
         {"credentials": {"host": "http://127.0.0.1:1", "token": "t"}}),
        ("error_forbidden", "PUT", "/api/addons/cameras", BOB_AUTH, {"settings": {"url": FRIGATE.url}}),
        ("users_forbidden", "GET", "/api/users", BOB_AUTH, None),
        ("disconnect", "DELETE", f"/api/addons/whatsapp?user={CARL.id}", ADMIN_AUTH, None),
    ]:
        status, payload = await call(client, method, path, auth, body)
        captured[key] = {"status": status, "body": payload}
    # A new user, nothing on yet: the first-run welcome.
    dee = users.create("Dee")
    dee_token, _ = users.issue_token(dee.id, "dee")
    status, payload = await call(client, "GET", "/api/addons", {"Authorization": f"Bearer {dee_token}"})
    captured["list_new_user"] = {"status": status, "body": payload}
    check("fixture: a new user is welcomed", payload["welcome"]["show"] is True, payload["welcome"])
    # A field the member may not change inside an addon they may: their WAHA address with no origin allowed.
    allowed = os.environ["MEMBER_ALLOWED_ORIGINS"]
    os.environ["MEMBER_ALLOWED_ORIGINS"] = f"homeassistant={addresses.origin(HA_B.url)}"
    try:
        status, payload = await call(client, "GET", "/api/addons/whatsapp", BOB_AUTH)
        captured["member_readonly_field"] = {"status": status, "body": payload}
    finally:
        os.environ["MEMBER_ALLOWED_ORIGINS"] = allowed
    # Running the server's addons: the catalog, a disabled addon, a refused install.
    for key, method, path, auth, body in [
        ("catalog_admin", "GET", "/api/addons/catalog", ADMIN_AUTH, None),
        ("catalog_member", "GET", "/api/addons/catalog", BOB_AUTH, None),
        ("disable", "POST", "/api/addons/shopping/disable", ADMIN_AUTH, None),
        ("list_member_disabled", "GET", "/api/addons", BOB_AUTH, None),
        ("enable", "POST", "/api/addons/shopping/enable", ADMIN_AUTH, None),
        ("install_refused", "POST", "/api/addons/install", ADMIN_AUTH,
         {"repo": "https://example.invalid/dana-addons.git", "path": "weather", "ref": "main"}),
    ]:
        status, payload = await call(client, method, path, auth, body)
        captured[key] = {"status": status, "body": payload}
    check("fixture: the catalog, a disabled addon and a refused install captured",
          captured["catalog_admin"]["status"] == 200 and captured["catalog_member"]["status"] == 403
          and [e["name"] for e in captured["catalog_admin"]["body"]["entries"]] == ["weather"]
          and captured["disable"]["body"]["state"] == "disabled-on-server"
          and captured["enable"]["body"]["state"] == "on" and captured["install_refused"]["status"] == 400,
          {k: captured[k]["status"] for k in ("catalog_admin", "disable", "enable", "install_refused")})
    failed = next((i for i in captured["list_admin"]["body"]["addons"] if i["state"] == "failed"), {})
    check("fixture: the failed site addon is listed, its error for the admin only",
          failed.get("name") == "broken" and "a missing package" in failed.get("error", "")
          and "error" not in next(i for i in captured["list_member"]["body"]["addons"] if i["name"] == "broken"), failed)
    conflict = next(i for i in captured["list_conflict"]["body"]["addons"] if i["name"] == "whatsapp")
    check("fixture: the conflict is in the admin's view of Carl",
          conflict.get("account_conflict") == "972500000002@c.us" and conflict["account"] is None, conflict)
    check("fixture: a field-level error and a 403 captured",
          captured["error_field"]["status"] == 400 and captured["error_field"]["body"]["field"] == "host"
          and captured["error_forbidden"]["status"] == 403, captured["error_field"])
    text = deterministic(json.dumps(captured, indent=1, ensure_ascii=False, sort_keys=True))
    check("fixture: no credential value in it", not [s for s in SECRETS if s in text])
    check("fixture: nothing of this machine or this run in it (paths, ports, user ids)",
          testenv.scratch_dir() not in text and not any(f":{port}" in text for port in run_ports())
          and not any(u.id in text for u in users.list_users()))
    if os.getenv("WRITE_WEB_FIXTURE"):
        with open(WEB_FIXTURE, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"wrote {WEB_FIXTURE}")
    else:
        with open(WEB_FIXTURE, encoding="utf-8") as f:
            stored = json.load(f)
        drift = [k for k in captured if shape(captured[k]) != shape(stored.get(k))]
        check("fixture: tests/fixtures/addons_web.json has the payloads' current shape "
              "(regenerate with WRITE_WEB_FIXTURE=1)", not drift, drift)


async def main() -> None:
    for fake in (HA_A, HA_B, WAHA, FRIGATE):
        await fake.start()
    # Bob (a member) may use only addresses an admin allowed (docs/access.md, "Security rules"): his
    # Home Assistant and the WAHA through the env list; the refusals are in test_security.py.
    os.environ["MEMBER_ALLOWED_ORIGINS"] = (f"homeassistant={addresses.origin(HA_B.url)},"
                                            f"whatsapp={addresses.origin(WAHA.url)}")
    async with TestClient(TestServer(build_app())) as client:
        await test_welcome(client)
        await test_me_and_list(client)
        await test_put(client)
        await test_other_user(client)
        await test_checks_and_options(client)
        await test_live_effects(client)
        await test_rules_and_users(client)
        await test_web_fixture(client)
    for fake in (HA_A, HA_B, WAHA, FRIGATE):
        await fake.server.close()
    leaks = [s for s in SECRETS if any(s in b for b in bodies)]
    check("no credential value in any response", not leaks, leaks)


asyncio.run(main())
passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
