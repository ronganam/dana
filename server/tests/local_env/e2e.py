"""End-to-end rehearsal against the running local test environment
(docs/local-testing.md). Run: bin/local-env.sh test (after a fresh `up`).

Drives the running container over HTTP / WebSocket only, plus
``docker exec … python bin/users.py`` for administration and ``docker``
for restarts and the start-up refusals. The stubs' control API
(tests/local_env/stubs.py) scripts the model and reads what every stub was
asked. Prints PASS / FAIL per check and exits non-zero on any failure.

Sections: the upgrade (first start from the 9.0 database, its conversion to per-addon
migrations, the env import, a
restart, the CREDENTIALS_KEY refusals); existing clients (the endpoints the
web UI loads, the login cookie, a streamed chat turn, approval, undo); a
second user made with the CLI (empty data, the admin's ids 404, 403
not_connected, own addons through the API, own tools and prompt, a
hidden tool refused and not executed, admin-only endpoints); sharing (task
list, project through share_item in chat, the shopping list: invitation, use
with moving items and its undo, the live stream, aisles from the model stub); the Home Assistant watcher; WAHA inbound routing and the internal-host
rule; phone events and notifications; Telegram; usage; the security rules
(debug export, cameras admin-only, addresses, Telegram chats, the WAHA session,
webhook and media origin, device rows); the second review's findings (a task
move undone by a member, one connection per WhatsApp account); the web
Addons page (its assets, every request it makes answering with the
fields it reads for the admin and the member, the admin's Home Assistant moved
to the second stub and back with connection tests, a member's refused change
with the reason the page shows).
"""

import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiohttp

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
STATE = HERE / ".state"
COMPOSE = ["docker", "compose", "-f", str(HERE / "compose.yaml")]
BASE = f"http://127.0.0.1:{os.getenv('DANA_LOCAL_PORT', '18080')}"
CTRL = f"http://127.0.0.1:{os.getenv('DANA_LOCAL_CONTROL_PORT', '18090')}"
INTERNAL_HOST = "ganam-assistant.ganam-assistant"

# Identities shared with stubs.py and the generated env file (bin/local-env.sh).
ADMIN_NAME, ADMIN_TG = "Robin", 111111111
MEMBER_NAME, MEMBER_TG = "Maya Katz", 222222222
UNKNOWN_TG = 333333333
ADMIN_ACCOUNT, MEMBER_ACCOUNT = "972500000001@c.us", "972500000002@c.us"
SECRETS_IN_ENV = ("API_TOKEN", "CREDENTIALS_KEY", "HOMEASSISTANT_TOKEN", "WAHA_API_KEY", "GOOGLE_REFRESH_TOKEN",
                  "TELEGRAM_BOT_TOKEN", "GOOGLE_CLIENT_SECRET", "APP_PUBLISH_TOKEN")
ALL_KINDS = ["essentials", "tasks", "shopping", "knowledge", "about", "people", "phone", "homeassistant", "whatsapp",
             "calendar", "cameras", "ops"]

results: List[Tuple[str, bool, str]] = []
EXAMPLES: Dict[str, Any] = {}  # real shopping payloads, written to .state/shopping-examples.json


def check(name: str, ok: Any, detail: Any = "") -> bool:
    ok = bool(ok)
    results.append((name, ok, "" if ok else str(detail)[:600]))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {str(detail)[:600]}"), flush=True)
    return ok


def env_file() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in (STATE / "app.env").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


ENV = env_file()
ADMIN_TOKEN = ENV["API_TOKEN"]


# --- docker helpers --------------------------------------------------------------------------------

def run(cmd: List[str], stdin: Optional[str] = None, timeout: float = 120) -> Tuple[int, str]:
    p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def app_container() -> str:
    code, out = run(COMPOSE + ["ps", "-q", "app"])
    return out.strip()


def dexec(*args: str, stdin: Optional[str] = None, timeout: float = 120) -> Tuple[int, str]:
    return run(["docker", "exec", "-i", app_container(), *args], stdin=stdin, timeout=timeout)


def users_cli(*args: str, stdin: Optional[str] = None) -> Tuple[int, str]:
    return dexec("python", "bin/users.py", *args, stdin=stdin)


def app_logs() -> str:
    return run(["docker", "logs", app_container()])[1]


def no_secret_in(text: str) -> List[str]:
    return [k for k in SECRETS_IN_ENV if ENV.get(k) and ENV[k] in text]


# --- HTTP helpers ----------------------------------------------------------------------------------

class Resp:
    def __init__(self, status: int, body: Any, headers: Any, text: str) -> None:
        self.status, self.body, self.headers, self.text = status, body, headers, text

    def __repr__(self) -> str:
        return f"<{self.status} {self.text[:300]}>"


class Client:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.s = session

    async def req(self, method: str, path: str, token: Optional[str] = ADMIN_TOKEN, json_body: Any = None,
                  headers: Optional[Dict[str, str]] = None, data: Any = None, cookies: Any = None,
                  base: str = BASE) -> Resp:
        h = dict(headers or {})
        if token:
            h["Authorization"] = f"Bearer {token}"
        async with self.s.request(method, base + path, json=json_body, data=data, headers=h, cookies=cookies,
                                  allow_redirects=False) as r:
            text = await r.text()
            try:
                body = json.loads(text)
            except ValueError:
                body = None
            return Resp(r.status, body, r.headers, text)

    async def get(self, path: str, token: Optional[str] = ADMIN_TOKEN, **kw: Any) -> Resp:
        return await self.req("GET", path, token, **kw)

    async def post(self, path: str, body: Any = None, token: Optional[str] = ADMIN_TOKEN, **kw: Any) -> Resp:
        return await self.req("POST", path, token, json_body=body if body is not None else {}, **kw)

    async def put(self, path: str, body: Any, token: Optional[str] = ADMIN_TOKEN, **kw: Any) -> Resp:
        return await self.req("PUT", path, token, json_body=body, **kw)

    async def patch(self, path: str, body: Any, token: Optional[str] = ADMIN_TOKEN, **kw: Any) -> Resp:
        return await self.req("PATCH", path, token, json_body=body, **kw)

    async def delete(self, path: str, token: Optional[str] = ADMIN_TOKEN, **kw: Any) -> Resp:
        return await self.req("DELETE", path, token, **kw)

    async def ctrl(self, method: str, path: str, body: Any = None) -> Any:
        async with self.s.request(method, CTRL + path, json=body) as r:
            return await r.json()

    async def sse(self, path: str, body: Dict[str, Any], token: str = ADMIN_TOKEN) -> Tuple[int, List[Dict[str, Any]]]:
        events: List[Dict[str, Any]] = []
        async with self.s.post(BASE + path, json=body, headers={"Authorization": f"Bearer {token}"},
                               timeout=aiohttp.ClientTimeout(total=90)) as r:
            if r.status != 200 or "text/event-stream" not in r.headers.get("Content-Type", ""):
                return r.status, [{"type": "http_error", "text": await r.text()}]
            async for raw in r.content:
                line = raw.decode().rstrip("\n")
                if line.startswith("data: "):
                    try:
                        events.append(json.loads(line[6:]))
                    except ValueError:
                        pass
        return 200, events

    async def chat(self, session_id: str, message: str, token: str = ADMIN_TOKEN, model: Optional[str] = None,
                   script: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        if script:
            await self.ctrl("POST", "/llm/script", [{"match": message.split()[0], "reply": r} for r in script])
        body: Dict[str, Any] = {"session_id": session_id, "message": message, "device": "web"}
        if model:
            body["model"] = model
        status, events = await self.sse("/api/assistant/message", body, token)
        return events

    async def model_requests(self, match: str) -> List[Dict[str, Any]]:
        return (await self.ctrl("GET", f"/llm/requests?match={match}"))["requests"]

    async def phone_events(self, token: str, after: int = 0, device: str = "") -> Dict[str, Any]:
        return (await self.get(f"/api/phone/events?after={after}&device_id={device}", token)).body or {}

    async def telegram_sent(self) -> List[Dict[str, Any]]:
        return (await self.ctrl("GET", "/telegram/log"))["sent"]


async def until(cond: Callable[[], Any], timeout: float = 20.0, every: float = 0.5) -> Any:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = cond()
        if asyncio.iscoroutine(last):
            last = await last
        if last:
            return last
        await asyncio.sleep(every)
    return last


async def sse_event(resp: aiohttp.ClientResponse, timeout: float = 10.0) -> Tuple[str, Any]:
    """The next server-sent event of an open stream as (event, data); comment lines skipped."""
    event, data = "", None
    while True:
        line = (await asyncio.wait_for(resp.content.readline(), timeout)).decode().rstrip("\n")
        if line.startswith(":"):
            continue
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
        elif line == "" and event:
            return event, data


def types_of(events: List[Dict[str, Any]]) -> List[str]:
    return [e.get("type", "") for e in events]


def ids(items: Any, key: str = "id") -> set:
    return {i.get(key) for i in items or [] if isinstance(i, dict)}


def golden_types() -> set:
    out = set()
    for line in (ROOT / "tests" / "fixtures" / "golden.sse").read_text().splitlines():
        if line.startswith("data: "):
            out.add(json.loads(line[6:])["type"])
    return out


def voice_event(me: str, session: str, mid: str) -> Dict[str, Any]:
    return {"event": "message", "session": session, "engine": "NOWEB", "me": {"id": me, "pushName": "x"},
            "payload": {"id": f"false_137181406429317@lid_{mid}", "timestamp": int(time.time()),
                        "from": "137181406429317@lid", "fromMe": False, "body": None, "hasMedia": True,
                        "media": {"url": "http://stubs:3000/api/files/voice.ogg", "mimetype": "audio/ogg; codecs=opus"},
                        "_data": {"pushName": "Yaffa", "message": {"audioMessage": {"ptt": True}}}}}


# =================================================================================================
# 1. The upgrade
# =================================================================================================

FIXTURE_IDS = {
    "tasks": {"t1", "t2", "t3"},
    "task_groups": {"general", "home", "work", "shopping", "health", "personal"},
    "shopping": {"s1", "s2"},
    "projects": {"p1"},
    "entries": {"e1", "e2"},
    "people": {"pe1"},
    "calls": {"c1"},
    "suggestions": {"cs1", "cs2"},
    "notifications": {"n1"},  # n2 is a muted app's notification: listed under the app, not active
    "facts": {"f1", "f2"},
}


async def upgrade(c: Client) -> Dict[str, Any]:
    print("\n== 1. Upgrade from 9.0")
    logs = app_logs()
    snap = "db-before-addons-from-0033.sqlite3"
    check("upgrade: first start logged the snapshot", f"Database snapshot before migrating: /app/db/snapshots/{snap}" in logs)
    check("upgrade: the 9.0 database converted to per-addon migrations, nothing else applied",
          "Database converted to per-addon migrations (from version 33)" in logs
          and "Applied database migration" not in logs, [l for l in logs.splitlines() if "migrat" in l][:5])
    check("upgrade: env import for the first admin, every addon",
          "Addons imported for Robin: tasks, shopping, knowledge, about, people, phone, homeassistant, "
          "whatsapp, calendar, cameras, ops" in logs)
    check("upgrade: no CREDENTIALS_KEY error, no traceback at start",
          "CREDENTIALS_KEY" not in logs.split("Webhook & Web UI server listening")[0] and "Traceback" not in logs,
          [l for l in logs.splitlines() if "Traceback" in l or "CREDENTIALS_KEY" in l][:3])
    code, out = dexec("ls", "/app/db/snapshots")
    check("upgrade: snapshot file exists in the data volume", code == 0 and out.split() == [snap], out)
    code, out = dexec("python", "-c", f"import sqlite3; c=sqlite3.connect('/app/db/snapshots/{snap}');"
                      "print(c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], "
                      "c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0])")
    check("upgrade: the snapshot is the 9.0 database (3 tasks, old migrations up to 33)", out.split() == ["3", "33"], out)
    code, out = dexec("python", "-c", "import sqlite3; c=sqlite3.connect('/app/db/db.sqlite3');"
                      "print(*sorted(r[0] for r in c.execute('SELECT addon FROM schema_migrations')))")
    check("upgrade: schema_migrations names core's and each official addon's baseline",
          out.split() == sorted(["core", "about", "contacts", "knowledge", "money", "people", "phone", "shopping",
                                 "tasks", "whatsapp"]), out)

    health = await c.get("/api/health", token=None)
    version = (ROOT / "VERSION").read_text().strip()
    check("upgrade: /api/health 200 with this VERSION", health.status == 200 and version in health.text, health)

    me = await c.get("/api/me")
    check("upgrade: /api/me is the admin with every addon",
          me.status == 200 and me.body.get("name") == ADMIN_NAME and me.body.get("access") == "admin"
          and me.body.get("kinds") == ALL_KINDS, me)
    admin_id = (me.body or {}).get("id")

    # Every row of the fixture, with its id, through the API.
    r = await c.get("/api/tasks")
    check("upgrade: tasks t1 t2 t3 visible with ids", FIXTURE_IDS["tasks"] <= ids(r.body), r)
    r = await c.get("/api/task-groups")
    check("upgrade: task lists keep their ids", FIXTURE_IDS["task_groups"] <= ids(r.body), r)
    r = await c.get("/api/shopping/items")
    check("upgrade: shopping items s1 s2 (order kept)", [i["id"] for i in r.body or []][:2] == ["s1", "s2"], r)
    r = await c.get("/api/projects")
    check("upgrade: project p1", FIXTURE_IDS["projects"] <= ids(r.body), r)
    r = await c.get("/api/projects/p1/items")
    check("upgrade: entries e1 e2", FIXTURE_IDS["entries"] <= ids((r.body or {}).get("entries")), r)
    r = await c.get("/api/search?q=Tomato")
    check("upgrade: entries FTS index kept (search finds e1)", "e1" in ids((r.body or {}).get("results")), r)
    r = await c.get("/api/people")
    check("upgrade: person pe1 with its number", any(p["id"] == "pe1" and "15550100001" in p["phones"]
                                                     for p in (r.body or {}).get("people", [])), r)
    r = await c.get("/api/calls")
    check("upgrade: call c1", FIXTURE_IDS["calls"] <= ids(r.body), r)
    r = await c.get("/api/calls/c1")
    check("upgrade: call c1 detail with transcript", r.status == 200 and "ladder" in r.text, r)
    r = await c.get("/api/suggestions?status=pending&source=all")
    check("upgrade: suggestions cs1 (call) cs2 (notification)", FIXTURE_IDS["suggestions"] <= ids(r.body), r)
    r = await c.get("/api/notifications")
    check("upgrade: notification n1", FIXTURE_IDS["notifications"] <= ids((r.body or {}).get("items")), r)
    r = await c.get("/api/notifications/n2")
    check("upgrade: notification n2 (muted app) by id", r.status == 200 and (r.body or {}).get("id") == "n2", r)
    r = await c.get("/api/notifications/apps")
    check("upgrade: notification rule r1", "r1" in ids((r.body or {}).get("rules")), r)
    r = await c.get("/api/notifications/requests/ar1")
    check("upgrade: notification action request ar1", r.status == 200, r)
    r = await c.get("/api/sessions")
    check("upgrade: session s_web_1", "s_web_1" in ids(r.body, "session_id"), r)
    r = await c.get("/api/sessions/s_web_1")
    check("upgrade: session s_web_1 history", r.status == 200 and "Water the plants" in r.text, r)
    r = await c.get("/api/sessions/main")
    check("upgrade: the main thread's messages", r.status == 200 and "Open the gate" in r.text and "Opened." in r.text, r)
    r = await c.get("/api/shortcuts")
    check("upgrade: learned phrase sc1", "sc1" in ids((r.body or {}).get("shortcuts")), r)
    r = await c.get("/api/about")
    facts = {f["id"] for s in (r.body or {}).get("sections", []) for f in s["facts"]}
    check("upgrade: About facts f1 f2", FIXTURE_IDS["facts"] <= facts, r)
    r = await c.get("/api/memories")
    check("upgrade: old /api/memories shape still served", isinstance(r.body, list) and len(r.body) >= 2, r)
    r = await c.get("/api/usage?range=all")
    check("upgrade: usage history (daily roll-up + raw rows) counted", r.status == 200 and
          (r.body or {}).get("totals", {}).get("calls", 0) >= 12, r)
    r = await c.get("/api/review/wr1")
    check("upgrade: weekly review wr1", r.status == 200 and (r.body or {}).get("review", r.body or {}).get("id") == "wr1", r)
    r = await c.get("/api/phone/devices")
    check("upgrade: phone device dev1", "dev1" in ids((r.body or {}).get("devices"), "device_id"), r)
    r = await c.get("/api/shopping")
    check("upgrade: the shopping screen: s1 s2 with their names, under other, version 0",
          r.status == 200 and [(i["id"], i["category"]) for i in (r.body or {}).get("items", [])][:2] == [("s1", "other"), ("s2", "other")]
          and (r.body or {}).get("list", {}).get("version") == 0, r)
    code, out = dexec("python", "-c", "import sqlite3; c=sqlite3.connect('/app/db/db.sqlite3');"
                      "print(sorted(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE name LIKE 'catalog%' "
                      "OR name = 'shopping_operations'\")))")
    check("upgrade: the catalog tables and the shopping operations log are gone", out.strip() == "[]", out)

    # The addons the env import created.
    integ = await c.get("/api/addons")
    by_kind = {i["name"]: i for i in (integ.body or {}).get("addons", [])}
    check("upgrade: /api/addons lists every addon connected",
          integ.status == 200 and all(by_kind.get(k, {}).get("connected") for k in ALL_KINDS[1:]), integ)
    check("upgrade: secret credentials only reported as set; addresses with their value (no secret anywhere)",
          all(cr.get("set") is True and ("value" not in cr if cr["secret"] else cr.get("value", "").startswith("http://"))
              for k in ("homeassistant", "whatsapp", "calendar") for cr in by_kind[k]["credentials"] if cr["required"])
          and not no_secret_in(integ.text), no_secret_in(integ.text))
    settings = {k: {s["name"]: s["value"] for s in v["settings"]} for k, v in by_kind.items()}
    check("upgrade: homeassistant settings from env (+ the car defaults)", settings.get("homeassistant") == {
        "person": "person.robin", "car_sensor": "sensor.pixel_8_bluetooth_connection", "car_device": "CarKit-1234",
        "phone_interactive_sensor": "binary_sensor.robin_phone_interactive",
        "phone_battery_sensor": "sensor.robin_phone_battery", "phone_notify_service": "mobile_app_robin_phone",
        "weather_entity": "weather.forecast_home"}, settings.get("homeassistant"))
    check("upgrade: whatsapp voice chat setting and the account learned from WAHA",
          settings.get("whatsapp", {}).get("voice_telegram_chat_id") == ADMIN_TG
          and by_kind.get("whatsapp", {}).get("account") == ADMIN_ACCOUNT, by_kind.get("whatsapp"))
    check("upgrade: calendar_id setting", settings.get("calendar", {}).get("calendar_id") == "primary", settings.get("calendar"))
    check("upgrade: cameras url; shopping has only the active-list setting", settings.get("cameras", {}).get("url") == "http://stubs:5000"
          and settings.get("shopping") == {"active_list": None}, (settings.get("cameras"), settings.get("shopping")))
    for kind in ("homeassistant", "whatsapp", "cameras"):
        r = await c.post(f"/api/addons/{kind}/test")
        check(f"upgrade: POST /api/addons/{kind}/test ok against the stub",
              r.status == 200 and (r.body or {}).get("ok") is True, r)
    r = await c.post("/api/addons/calendar/test")
    check("upgrade: calendar test (Google is not stubbed) answers ok:false with a short reason, no secret",
          r.status == 200 and (r.body or {}).get("checked") is True and (r.body or {}).get("ok") is False
          and not no_secret_in(r.text), r)

    code, out = users_cli("list")
    check("cli: users.py list runs in the image: one admin, Telegram id, every addon",
          code == 0 and "Robin  [admin] (first admin)" in out and "telegram ids: 111111111" in out
          and "addons: tasks, shopping, knowledge, about, people, phone, homeassistant, whatsapp, calendar, cameras, ops" in out, out)
    code, out = users_cli("addons", "show", "Robin")
    check("cli: addons show: credentials 'set', no secret printed", code == 0 and "set" in out and not no_secret_in(out),
          (code, no_secret_in(out), out[:300]))
    r = await c.get("/api/checks")
    keys = {x["key"]: x for x in (r.body or {}).get("checks", [])}
    check("upgrade: self-check ha_watcher connected, no ha_entities missing",
          keys.get("ha_watcher", {}).get("ok") is True and keys.get("ha_entities", {}).get("ok", True) is True,
          {k: keys.get(k) for k in ("ha_watcher", "ha_entities")})
    return {"admin_id": admin_id, "addons": integ.text}


async def restart_and_key_checks(c: Client, before: Dict[str, Any]) -> None:
    print("\n== 1b. Restart, and starting without / with a wrong CREDENTIALS_KEY")
    _, users_before = users_cli("list")
    code, out = run(COMPOSE + ["restart", "app"], timeout=120)
    healthy = await until(lambda: run(["docker", "inspect", "-f", "{{.State.Health.Status}}", app_container()])[1].strip()
                          == "healthy", timeout=120, every=2)
    check("restart: the container comes back healthy", code == 0 and healthy, out)
    logs = app_logs()
    check("restart: started twice, converted once, imported once, one snapshot",
          logs.count("Initializing modular AI assistant") == 2
          and logs.count("Database converted to per-addon migrations") == 1
          and logs.count("Addons imported for") == 1 and logs.count("Database snapshot before migrating") == 1,
          (logs.count("Initializing modular AI assistant"), logs.count("Database converted to per-addon migrations"),
           logs.count("Addons imported for")))
    code, out = dexec("ls", "/app/db/snapshots")
    check("restart: still one snapshot", out.split() == ["db-before-addons-from-0033.sqlite3"], out)
    r = await c.get("/api/addons")
    check("restart: addons unchanged", r.text == before["addons"],
          "addons payload differs after restart")
    _, users_after = users_cli("list")
    check("restart: users unchanged", users_after == users_before, users_after)

    # A copy of the migrated database, then the fixture: start main.py with a
    # bad key inside the container (another port, no Telegram) and read why it stops.
    copy = ("import sqlite3, os; os.makedirs('/tmp/k', exist_ok=True); s=sqlite3.connect('/app/db/db.sqlite3');"
            " d=sqlite3.connect('/tmp/k/db.sqlite3'); s.backup(d); d.close()")
    dexec("python", "-c", copy)

    def start(key: Optional[str], db: str) -> Tuple[int, str]:
        env = ["env"] + (["-u", "CREDENTIALS_KEY"] if key is None else [f"CREDENTIALS_KEY={key}"])
        return dexec(*env, f"DATABASE_PATH={db}", "PORT=18999", "TELEGRAM_API_BASE=http://127.0.0.1:9",
                     "timeout", "40", "python", "main.py", timeout=60)

    code, out = start(None, "/tmp/k/db.sqlite3")
    check("key: migrated DB without CREDENTIALS_KEY refuses to start with the documented message",
          code == 1 and "CREDENTIALS_KEY is not set but 3 addon(s) have stored credentials." in out
          and "Fernet.generate_key()" in out and "Webhook & Web UI server listening" not in out, (code, out[-500:]))
    code, out = start("not-a-key", "/tmp/k/db.sqlite3")
    check("key: malformed key refuses to start", code == 1 and "CREDENTIALS_KEY is not a valid Fernet key" in out,
          (code, out[-400:]))
    _, other = dexec("python", "-c", "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    code, out = start(other.strip(), "/tmp/k/db.sqlite3")
    check("key: a different key refuses to start ('not the key the stored … were encrypted with')",
          code == 1 and "is not the key the stored addon credentials were encrypted" in out, (code, out[-400:]))
    check("key: no key value in those messages", not no_secret_in(out) and other.strip() not in out)

    # The fixture as production has it today, first start without the key.
    run(["docker", "cp", str(ROOT / "tests" / "fixtures" / "db_9_0.sql"), f"{app_container()}:/tmp/db_9_0.sql"])
    dexec("python", "-c", "import sqlite3, os; os.makedirs('/tmp/f', exist_ok=True);"
          " c=sqlite3.connect('/tmp/f/db.sqlite3'); c.executescript(open('/tmp/db_9_0.sql').read()); c.close()")
    code, out = start(None, "/tmp/f/db.sqlite3")
    check("key: 9.0 DB + env credentials, no key: refuses to start, says how to generate one",
          code == 1 and "CREDENTIALS_KEY is not set" in out and "Fernet.generate_key()" in out
          and "Addons imported" not in out, (code, out[-500:]))
    code, out = start(ENV["CREDENTIALS_KEY"], "/tmp/f/db.sqlite3")
    check("key: the same DB with the key added: import happens on that start",
          "Addons imported for Robin" in out and "Database migrated" not in out, out[-600:])
    dexec("rm", "-rf", "/tmp/k", "/tmp/f", "/tmp/db_9_0.sql")


# =================================================================================================
# 2. Existing clients
# =================================================================================================

def is_list(*keys: str) -> Callable[[Any], bool]:
    return lambda b: isinstance(b, list) and all(all(k in i for k in keys) for i in b)


def has(*keys: str) -> Callable[[Any], bool]:
    return lambda b: isinstance(b, dict) and all(k in b for k in keys)


# The endpoints static/*.js fetch when its screens load, with what the code reads.
WEB_LOAD = [
    ("/api/tasks", is_list("id", "title", "status", "group_id", "triggers", "links")),
    ("/api/task-groups", is_list("id", "name", "emoji", "color", "task_count")),
    ("/api/memories", is_list("key", "value", "category")),
    ("/api/projects", is_list("id", "title", "icon", "color", "status", "entry_count")),
    ("/api/projects/p1/items", has("entries")),
    ("/api/sessions", is_list("session_id", "status", "title", "updated_at")),
    ("/api/sessions/main", has("session_id", "status", "messages", "pending_approval", "undos", "has_more")),
    ("/api/assistant/models", has("default", "models")),
    ("/api/calls", is_list("id", "title", "status", "labels", "call_timestamp")),
    ("/api/calls/labels", is_list("id", "name", "emoji")),
    ("/api/suggestions?status=pending&source=all", is_list("id", "kind", "status", "payload", "source_type")),
    ("/api/notifications?status=active&bucket=needs_you&limit=1", has("items")),
    ("/api/notifications?status=active&limit=50", has("items")),
    ("/api/notifications/apps", has("apps", "rules")),
    ("/api/notifications/summaries", has("overall", "apps", "refreshing")),
    ("/api/people", has("people")),
    ("/api/people/merge-candidates", has("pairs")),
    ("/api/people/pe1", has("id", "name", "phones")),
    ("/api/review/latest", has("review")),
    ("/api/briefing/today", has("briefing")),
    ("/api/usage?range=30d", has("totals", "by_day", "previous")),
    ("/api/usage/events?sort=recent&limit=50", has("events", "next")),
    ("/api/zones", is_list("name", "entity_id")),
    ("/api/client-config", has("battery_sensor", "interactive_sensor", "person", "notify_service")),
    ("/api/ha/state/sensor.robin_phone_battery", has("entity_id", "state", "attributes")),
    ("/api/shopping/items", is_list("id", "name", "checked", "quantity", "note", "category")),
    ("/api/shopping", has("list", "categories", "items", "counts", "usuals", "invites", "lists")),
    ("/api/shopping/suggestions", has("suggestions")),
    ("/api/debug/sessions", is_list("session_id")),
    ("/api/debug/logs", lambda b: b is not None),
    ("/api/calendar/events?start_date=2026-11-15&end_date=2026-11-22", lambda b: isinstance(b, list)),
    ("/api/frigate/events/ptz?limit=12", has("events", "server_time")),
    ("/api/frigate/activity/ptz?hours=12", has("series", "bucket")),
    ("/api/about", has("sections", "used_chars", "max_chars")),
    ("/api/shortcuts", has("shortcuts", "enabled")),
    ("/api/now", has("now", "items")),
    ("/api/notifications/overview", has("counts", "apps")),
    ("/api/checks", has("checks")),
    ("/api/search?q=Sam", has("results")),
    ("/api/phone/devices", has("configured", "devices", "latest_seq")),
    ("/api/shopping/lists", has("active", "lists")),
]

# The web app's own scripts, in static/index.html's order (static/app.js says how they fit together).
APP_SCRIPTS = [f"/static/{name}.js" for name in (
    "app-core", "app-nav", "app-stream", "app-chat", "app-voice", "app-tasks", "app-memory", "app-projects",
    "app-projects-table", "app-calls", "app-settings", "app-calendar", "app-debug", "app")]

PAGES = ["/", "/dashboard", "/calendar", *APP_SCRIPTS, "/static/styles.css", "/manifest.json", "/sw.js",
         "/static/addons.js", "/static/addons.css", "/shopping", "/static/shopping.js",
         "/static/shopping-core.js", "/static/shopping.css"]


async def existing_clients(c: Client) -> None:
    print("\n== 2. Existing clients (admin)")
    for path, valid in WEB_LOAD:
        r = await c.get(path)
        check(f"web: GET {path.split('?')[0]} 200 with the fields the UI reads", r.status == 200 and valid(r.body), r)
    for page in PAGES:
        r = await c.get(page)
        check(f"web: {page} served", r.status == 200, r)
    index, sw = await c.get("/"), await c.get("/sw.js")
    at = [index.text.find(f'"{s}"') for s in APP_SCRIPTS]
    check("web: the page loads the app's scripts in order, before the panel scripts",
          0 < max(at) < index.text.find('"/static/inbox.js"') and at == sorted(at), at)
    check("web: the service worker pre-caches every app script", all(f'"{s}"' in sw.text for s in APP_SCRIPTS), sw.status)

    # Login page → cookie → the same user; a bad token goes back to the login page.
    r = await c.get("/", token=None)
    check("login: / without credentials redirects to /login", r.status in (302, 303) and "/login" in r.headers.get("Location", ""), r)
    r = await c.get("/api/me", token=None)
    check("login: API without credentials is 401", r.status == 401, r)
    r = await c.get("/login", token=None)
    check("login: login page served", r.status == 200 and "token" in r.text, r)
    r = await c.req("POST", "/login", token=None, data={"token": "wrong-token", "next": "/"})
    check("login: a wrong token goes back to /login?error=1", r.status == 302 and "error=1" in r.headers.get("Location", ""), r)
    r = await c.req("POST", "/login", token=None, data={"token": ADMIN_TOKEN, "next": "/"})
    cookie = r.headers.get("Set-Cookie", "")
    check("login: the admin token sets the session cookie (HttpOnly, not the token)",
          r.status == 302 and "ganam_session=" in cookie and "HttpOnly" in cookie and ADMIN_TOKEN not in cookie, r)
    value = re.search(r"ganam_session=([^;]+)", cookie)
    jar = {"ganam_session": value.group(1)} if value else {}
    r = await c.get("/api/me", token=None, cookies=jar)
    check("login: the cookie is the admin", r.status == 200 and (r.body or {}).get("name") == ADMIN_NAME, r)
    r = await c.get("/", token=None, cookies=jar)
    check("login: the web UI loads with the cookie", r.status == 200 and "<html" in r.text.lower(), r)

    # A streamed chat turn: reasoning, a tool, text, as the golden stream.
    events = await c.chat("e2e-chat", "E2E-CHAT-1 what time is it?", model="balanced", script=[
        {"reasoning": ["Checking ", "the clock."], "tool": {"name": "get_current_time", "args": {}}},
        {"text": "It is time for the end-to-end test."}])
    got = set(types_of(events))
    check("chat: SSE event types are exactly the golden stream's", got == golden_types(),
          f"got {sorted(got)} want {sorted(golden_types())}")
    final = next((e for e in events if e.get("type") == "final"), {})
    check("chat: final reply text", final.get("content") == "It is time for the end-to-end test.", final)
    start = next((e for e in events if e.get("type") == "run_start"), {})
    check("chat: run_start names the balanced profile", (start.get("model") or {}).get("id") == "balanced", start)
    tool_end = next((e for e in events if e.get("type") == "tool_end"), {})
    check("chat: the tool ran (get_current_time ok)", tool_end.get("name") == "get_current_time" and tool_end.get("ok"), tool_end)
    seqs = [e["seq"] for e in events if "seq" in e]
    check("chat: seq numbers 1..n without gaps", seqs == list(range(1, len(seqs) + 1)), seqs)
    run_id = start.get("run_id")
    async with c.s.get(f"{BASE}/api/sessions/e2e-chat/stream?run_id={run_id}&after=3",
                       headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}) as resp:
        body = await resp.text()
    tail = [json.loads(l[6:]) for l in body.splitlines() if l.startswith("data: ")]
    check("chat: reattach replays the run after seq 3", resp.status == 200 and tail and tail[0].get("seq") == 4
          and tail[-1].get("type") == "state", (resp.status, tail[:1]))

    # Undo: a write runs at once and carries an undo entry.
    events = await c.chat("e2e-undo", "E2E-UNDO-1 remind me to buy stamps", script=[
        {"tool": {"name": "add_task", "args": {"title": "E2E buy stamps"}}}, {"text": "Added."}])
    tool_end = next((e for e in events if e.get("type") == "tool_end"), {})
    undos = tool_end.get("undos") or []
    tasks = (await c.get("/api/tasks")).body or []
    # The title gets the emoji the model picks (titles.py; the fake model answers 📝).
    check("undo: add_task ran at once with an undo entry", tool_end.get("ok") and undos
          and any(t["title"].endswith("E2E buy stamps") for t in tasks), tool_end)
    if undos:
        r = await c.post(f"/api/undo/{undos[0]['id']}")
        tasks = (await c.get("/api/tasks")).body or []
        check("undo: POST /api/undo/{id} removed the task", r.status == 200
              and not any(t["title"].endswith("E2E buy stamps") for t in tasks), r)

    # Approval: delete asks first; approve resumes the run.
    r = await c.post("/api/tasks", {"title": "E2E approval target"})
    tid = (r.body or {}).get("id")
    events = await c.chat("e2e-approve", "E2E-APPROVE-1 delete the approval target", script=[
        {"tool": {"name": "delete_task", "args": {"item": tid}}}, {"text": "Deleted it."}])
    asked = next((e for e in events if e.get("type") == "approval_required"), None)
    check("approval: delete_task asks (approval_required, task still there)", asked and asked.get("name") == "delete_task"
          and tid in ids((await c.get("/api/tasks")).body), types_of(events))
    status, events = await c.sse("/api/assistant/approve", {"session_id": "e2e-approve", "action": "approve"})
    tool_end = next((e for e in events if e.get("type") == "tool_end"), {})
    check("approval: approve runs the tool (approved: true) and finishes", status == 200 and tool_end.get("approved") is True
          and "final" in types_of(events) and tid not in ids((await c.get("/api/tasks")).body), events[-3:])


# =================================================================================================
# 3. A second user
# =================================================================================================

ADMIN_IDS_404 = [
    ("GET", "/api/calls/c1", None), ("GET", "/api/calls/c1/audio", None), ("PUT", "/api/calls/c1", {"title": "x"}),
    ("DELETE", "/api/calls/c1", None), ("POST", "/api/calls/c1/reanalyze", {}),
    ("GET", "/api/people/pe1", None), ("GET", "/api/people/pe1/card", None), ("PUT", "/api/people/pe1", {"notes": "x"}),
    ("DELETE", "/api/people/pe1", None),
    ("GET", "/api/projects/p1/items", None), ("PUT", "/api/projects/p1", {"title": "x"}), ("DELETE", "/api/projects/p1", None),
    ("PUT", "/api/entries/e1", {"title": "x"}), ("DELETE", "/api/entries/e1", None),
    ("PUT", "/api/tasks/t1", {"title": "x"}), ("DELETE", "/api/tasks/t1", None), ("POST", "/api/tasks/t1/snooze", {"minutes": 10}),
    ("PUT", "/api/task-groups/general", {"name": "x"}), ("DELETE", "/api/task-groups/home", None),
    ("GET", "/api/sessions/s_web_1", None), ("DELETE", "/api/sessions/s_web_1", None),
    ("GET", "/api/sessions/s_web_1/stream", None),
    ("GET", "/api/notifications/n1", None), ("POST", "/api/notifications/n1/act", {"action": "dismiss"}),
    ("GET", "/api/notifications/requests/ar1", None),
    ("POST", "/api/suggestions/cs1/accept", {}), ("POST", "/api/suggestions/cs1/dismiss", {}), ("PUT", "/api/suggestions/cs1", {"payload": {}}),
    ("PATCH", "/api/shopping/items/s1", {"checked": True}), ("DELETE", "/api/shopping/items/s1", None),
    ("PUT", "/api/about/facts/f1", {"text": "x"}), ("DELETE", "/api/about/facts/f1", None),
    ("DELETE", "/api/shortcuts/sc1", None), ("GET", "/api/review/wr1", None),
    ("GET", "/api/sharing/task_list/general", None), ("GET", "/api/sharing/project/p1", None),
]

NOT_CONNECTED = [
    ("/api/tasks", "tasks"), ("/api/task-groups", "tasks"), ("/api/shopping/items", "shopping"), ("/api/projects", "knowledge"),
    ("/api/about", "about"), ("/api/people", "people"), ("/api/calls", "phone"), ("/api/notifications", "phone"),
    ("/api/phone/devices", "phone"), ("/api/ha/states?ids=light.living_room", "homeassistant"), ("/api/zones", "homeassistant"),
    ("/api/calendar/events?start_date=2026-11-15&end_date=2026-11-16", "calendar"),
    ("/api/frigate/events/ptz", "cameras"),
]


async def second_user(c: Client, admin_id: str) -> Dict[str, str]:
    print("\n== 3. A second user (CLI, then their own addons through the API)")
    code, out = users_cli("add", MEMBER_NAME, "--pronoun", "she", "--context",
                          "Maya is a nurse in Haifa. Dalia is her sister.", "--telegram-chat", str(MEMBER_TG))
    m = re.search(r"\(([0-9a-f]{12}), member\)", out)
    check("cli: add a member", code == 0 and m, out)
    maya_id = m.group(1) if m else ""
    code, out = users_cli("tokens", "issue", "maya", "--label", "e2e")
    lines = out.strip().splitlines()
    token = lines[1].strip() if code == 0 and len(lines) > 1 else ""
    check("cli: token issued once (printed, then hashed)", code == 0 and len(token) >= 20, out)
    code, out = users_cli("telegram", "add", "maya", str(MEMBER_TG))
    check("cli: Telegram id added", code == 0, out)
    code, out = users_cli("tokens", "list", "maya")
    check("cli: tokens list never prints the token", code == 0 and token not in out, out)
    code, out = users_cli("addons", "connect", "maya", "ops")
    check("cli: ops cannot be connected for a member", code != 0, out)
    STATE.joinpath("users.json").write_text(json.dumps({
        "url": BASE, "admin": {"name": ADMIN_NAME, "id": admin_id, "token": ADMIN_TOKEN},
        "member": {"name": MEMBER_NAME, "id": maya_id, "token": token}}, indent=2))

    me = await until(lambda: _me(c, token), timeout=15)
    check("member: /api/me with the DB token: member, only core", me and me.get("access") == "member"
          and me.get("kinds") == ["essentials"], me)

    for path, kind in NOT_CONNECTED:
        r = await c.get(path, token)
        check(f"member: {path.split('?')[0]} 403 not_connected ({kind})", r.status == 403 and r.body == {
            "error": "not_connected", "addon": kind}, r)

    # Admin-only endpoints and other users' addons.
    for method, path in [("GET", "/api/debug/logs"), ("DELETE", "/api/debug/logs"),
                         (("GET"), f"/api/addons?user={admin_id}"),
                         ("GET", "/api/usage?user=all")]:
        r = await c.req(method, path, token, json_body={} if method == "POST" else None)
        check(f"member: {method} {path.split('?')[0]}{'?user=…' if 'user=' in path else ''} 403", r.status == 403, r)
    r = await c.put("/api/addons/ops", {}, token)
    check("member: PUT /api/addons/ops 403 forbidden", r.status == 403 and (r.body or {}).get("error") == "forbidden", r)
    r = await c.get("/api/addons", token)
    check("member: /api/addons does not offer ops", r.status == 200 and "ops" not in
          {i["name"] for i in (r.body or {}).get("addons", [])}, r)

    # Tools and prompt are hers; hidden tools refused and not executed.
    await c.ctrl("POST", "/reset")
    await c.chat("e2e-maya", "E2E-MAYA-PROMPT hello", token)
    reqs = await c.model_requests("E2E-MAYA-PROMPT")
    tools = set(reqs[0]["tools"]) if reqs else set()
    system = reqs[0]["system"] if reqs else ""
    check("member: the model got her tool list: core only (no ops / HA / WhatsApp / calendar / tasks)",
          reqs and "get_current_time" in tools and not tools & {"run_kubectl", "run_python", "call_ha_service",
                                                                "send_whatsapp_message", "add_event", "add_task"}, sorted(tools))
    check("member: system message names her and not the admin", "Maya" in system and "Robin" not in system,
          system[:400])
    await c.chat("e2e-admin-tools", "E2E-ADMIN-PROMPT hello")
    reqs = await c.model_requests("E2E-ADMIN-PROMPT")
    check("admin: the model got the admin's full tool list and prompt",
          reqs and {"run_kubectl", "call_ha_service", "send_whatsapp_message", "add_event"} <= set(reqs[0]["tools"])
          and "Robin" in reqs[0]["system"] and "Maya" not in reqs[0]["system"], reqs[:1])

    events = await c.chat("e2e-maya", "E2E-MAYA-HIDDEN turn on the living room light", token, script=[
        {"tools": [{"name": "call_ha_service", "args": {"domain": "light", "service": "turn_on", "entity_id": "light.living_room"}},
                   {"name": "send_whatsapp_message", "args": {"chat_id": "972500000011@c.us", "text": "hi"}},
                   {"name": "run_kubectl", "args": {"command": "get pods"}}]},
        {"text": "I can't do that."}])
    ends = [e for e in events if e.get("type") == "tool_end"]
    check("member: hidden tools refused like unknown names (no approval card, run completes)",
          len(ends) == 3 and all(not e.get("ok") for e in ends) and "approval_required" not in types_of(events)
          and "final" in types_of(events), [(e.get("name"), e.get("ok"), str(e.get("result"))[:60]) for e in ends])
    ha_a, ha_b, waha = (await c.ctrl("GET", "/ha/a/log"), await c.ctrl("GET", "/ha/b/log"), await c.ctrl("GET", "/waha/log"))
    check("member: nothing executed: no Home Assistant service call, no WhatsApp message",
          not [s for s in ha_a["service_calls"] + ha_b["service_calls"] if s["service"].startswith("light.")]
          and not waha["sent"], (ha_a["service_calls"], ha_b["service_calls"], waha["sent"]))

    # Connect her kinds through the API with her own token.
    for kind in ("tasks", "shopping", "knowledge", "about", "people", "phone"):
        r = await c.put(f"/api/addons/{kind}", {}, token)
        check(f"member: PUT /api/addons/{kind} connects (201)", r.status == 201 and (r.body or {}).get("connected"), r)
    r = await c.put("/api/addons/homeassistant", {
        "credentials": {"host": "http://stubs:8124", "token": "local-ha-token-b"},
        "settings": {"person": "person.maya", "car_sensor": "sensor.maya_phone_bluetooth_connection", "car_device": "Maya Car"}}, token)
    check("member: connects her own Home Assistant (201, token not echoed)", r.status == 201 and "local-ha-token-b" not in r.text, r)
    r = await c.put("/api/addons/whatsapp", {
        "credentials": {"url": "http://stubs:3000/api/", "api_key": "local-waha-key", "session": "maya"},
        "settings": {"voice_telegram_chat_id": MEMBER_TG}}, token)
    check("member: connects her own WhatsApp session (201)", r.status == 201 and "local-waha-key" not in r.text, r)
    for kind in ("homeassistant", "whatsapp"):
        r = await c.post(f"/api/addons/{kind}/test", token=token)
        check(f"member: {kind} test ok against the stub", (r.body or {}).get("ok") is True, r)
    acct = await until(lambda: _account(c, token), timeout=15)
    check("member: her WhatsApp account learned on connect", acct == MEMBER_ACCOUNT, acct)
    r = await c.get("/api/addons/homeassistant/options", token)
    opts = json.dumps(r.body or {})
    check("member: options come from her own Home Assistant", r.status == 200 and "person.maya" in opts
          and "person.robin" not in opts, r)
    r = await c.get("/api/client-config", token)
    check("member: client-config from her settings", (r.body or {}).get("person") == "person.maya", r)
    r = await c.get("/api/zones", token)
    check("member: her zones (Gym, not Work)", {z["name"] for z in r.body or []} == {"Home", "Gym"}, r)
    r = await c.get("/api/ha/state/light.living_room", token)
    check("member: the admin's entity is not in her Home Assistant (her HA answers 404 → 502)", r.status == 502, r)
    r = await c.get("/api/ha/state/light.maya_lamp", token)
    check("member: the entity state proxy works with Home Assistant alone (no cameras)",
          r.status == 200 and (r.body or {}).get("entity_id") == "light.maya_lamp", r)

    # Empty data everywhere; none of the admin's ids.
    checks = [
        ("/api/tasks", lambda b: b == []),
        ("/api/task-groups", lambda b: len(b) == 6 and all(g["id"].startswith(maya_id) for g in b)),
        ("/api/shopping/items", lambda b: b == []),
        ("/api/projects", lambda b: b == []),
        ("/api/people", lambda b: b.get("people") == []),
        ("/api/calls", lambda b: b == []),
        ("/api/suggestions?status=pending&source=all", lambda b: b == []),
        ("/api/notifications", lambda b: b.get("items") == []),
        ("/api/sessions", lambda b: "s_web_1" not in ids(b, "session_id")),
        ("/api/sessions/main", lambda b: b.get("messages") == []),
        ("/api/shortcuts", lambda b: b.get("shortcuts") == []),
        ("/api/about", lambda b: all(s["facts"] == [] for s in b.get("sections", []))),
        ("/api/memories", lambda b: b == []),
        ("/api/phone/devices", lambda b: b.get("devices") == []),
        ("/api/search?q=Sam", lambda b: b.get("results") == []),
        ("/api/search?q=Tomato", lambda b: b.get("results") == []),
        ("/api/review/latest", lambda b: b.get("review") is None),
        ("/api/now", lambda b: not any(str(i.get("id", "")).endswith(("n1", "cs1", "cs2")) for i in b.get("items", []))),
        ("/api/notifications/apps", lambda b: b.get("rules") == [] and b.get("apps") == []),
    ]
    for path, ok in checks:
        r = await c.get(path, token)
        try:
            good = r.status == 200 and ok(r.body)
        except Exception as e:
            good = False
        check(f"member: GET {path} shows none of the admin's data", good, r)
    for method, path, body in ADMIN_IDS_404:
        r = await c.req(method, path, token, json_body=body)
        check(f"member: {method} {path} → 404 (the admin's id)", r.status == 404, r)
    r = await c.get("/api/tasks")
    check("member: the admin's data is untouched by her attempts", {"t1", "t3"} <= ids(r.body))

    # Her chat runs her tools on her own services.
    await c.ctrl("POST", "/reset")
    events = await c.chat("e2e-maya", "E2E-MAYA-HA turn on my lamp", token, script=[
        {"tool": {"name": "call_ha_service", "args": {"domain": "light", "service": "turn_on", "entity_id": "light.maya_lamp"}}},
        {"text": "Done."}])
    ha_a, ha_b = await c.ctrl("GET", "/ha/a/log"), await c.ctrl("GET", "/ha/b/log")
    check("member: call_ha_service hits her Home Assistant with her token only",
          any(s["service"] == "light.turn_on" for s in ha_b["service_calls"]) and not ha_a["service_calls"]
          and all(r["token_ok"] for r in ha_b["requests"]), (ha_a["service_calls"], ha_b["service_calls"]))
    reqs = await c.model_requests("E2E-MAYA-HA")
    tools = set(reqs[0]["tools"]) if reqs else set()
    check("member: her tool list now has her kinds, still no ops / calendar",
          {"call_ha_service", "send_whatsapp_message", "add_task", "get_shopping_list"} <= tools
          and not tools & {"run_kubectl", "run_python", "add_event"}, sorted(tools))
    events = await c.chat("e2e-maya", "E2E-MAYA-WA tell Noa hi", token, script=[
        {"tool": {"name": "send_whatsapp_message", "args": {"chat_id": "972500000011@c.us", "text": "hi from Maya"}}},
        {"text": "Asked."}])
    asked = next((e for e in events if e.get("type") == "approval_required"), None)
    if asked:
        await c.sse("/api/assistant/approve", {"session_id": "e2e-maya", "action": "approve"}, token)
    waha = await c.ctrl("GET", "/waha/log")
    check("member: send_whatsapp_message asks, then sends from her session",
          asked and [s.get("session") for s in waha["sent"]] == ["maya"], waha["sent"])
    return {"id": maya_id, "token": token}


async def _me(c: Client, token: str) -> Optional[Dict[str, Any]]:
    r = await c.get("/api/me", token)
    return r.body if r.status == 200 else None


async def _account(c: Client, token: str) -> Optional[str]:
    r = await c.get("/api/addons/whatsapp", token)
    return (r.body or {}).get("account")


# =================================================================================================
# 4. Sharing
# =================================================================================================

async def sharing(c: Client, maya: Dict[str, str]) -> None:
    print("\n== 4. Sharing")
    tok, mid = maya["token"], maya["id"]
    r = await c.put("/api/sharing/task_list/home", {"user_id": mid, "level": "read"})
    check("share: admin shares the Home list read-only (API)", r.status == 200 and
          {"user_id": mid, "name": MEMBER_NAME, "level": "read"} in (r.body or {}).get("shared_with", []), r)
    r = await c.get("/api/tasks", tok)
    t3 = next((t for t in r.body or [] if t["id"] == "t3"), {})
    check("share: she sees the list's tasks with shared_by / access", t3.get("shared_by") == ADMIN_NAME
          and t3.get("access") == "read" and "t2" not in ids(r.body), r)
    r = await c.get("/api/tasks")
    check("share: the admin's own payload has no shared_by", not any("shared_by" in t for t in r.body or []), r)
    r = await c.put("/api/tasks/t3", {"priority": "high"}, tok)
    check("share: a write to a read-only list is 403", r.status == 403, r)
    r = await c.put("/api/sharing/task_list/home", {"user_id": mid, "level": "edit"})
    r = await c.put("/api/tasks/t3", {"priority": "high"}, tok)
    t3 = next((t for t in (await c.get("/api/tasks")).body or [] if t["id"] == "t3"), {})
    check("share: with edit her change lands in the admin's task", r.status == 200 and t3.get("priority") == "high", r)
    r = await c.get("/api/sharing/task_list/home", tok)
    check("share: only the owner manages sharing (403 owner_only)", r.status == 403, r)
    r = await c.put("/api/sharing/task_list/home", {"user_id": mid, "level": "none"})
    r2 = await c.put("/api/tasks/t3", {"priority": "low"}, tok)
    check("share: unshare hides the list and its tasks again", "t3" not in ids((await c.get("/api/tasks", tok)).body)
          and r2.status == 404, r2)

    # A project through share_item in chat (asks first).
    events = await c.chat("e2e-share", "E2E-SHARE-1 share the garden plan with Maya so she can see it", script=[
        {"tool": {"name": "share_item", "args": {"item_type": "project", "item": "Garden plan", "user": "Maya", "level": "read"}}},
        {"text": "Shared."}])
    asked = next((e for e in events if e.get("type") == "approval_required"), None)
    check("share: share_item is offered and asks first", asked and asked.get("name") == "share_item"
          and "p1" not in ids((await c.get("/api/projects", tok)).body), types_of(events))
    status, events = await c.sse("/api/assistant/approve", {"session_id": "e2e-share", "action": "approve"})
    r = await c.get("/api/projects", tok)
    p1 = next((p for p in r.body or [] if p["id"] == "p1"), {})
    check("share: after approval she sees the project read-only", p1.get("shared_by") == ADMIN_NAME and p1.get("access") == "read", r)
    r = await c.get("/api/projects/p1/items", tok)
    check("share: … with its entries", {"e1", "e2"} <= ids((r.body or {}).get("entries")), r)
    r = await c.put("/api/entries/e1", {"title": "x"}, tok)
    check("share: read-only project refuses writes (403 read_only)", r.status == 403 and (r.body or {}).get("error") == "read_only", r)
    r = await c.get("/api/search?q=Tomato", tok)
    check("share: shared entries appear in her search", "e1" in ids((r.body or {}).get("results")), r)
    await c.put("/api/sharing/project/p1", {"user_id": mid, "level": "edit"})
    r = await c.post("/api/entries", {"project_id": "p1", "title": "Maya's herb idea", "kind": "note"}, tok)
    items = (await c.get("/api/projects/p1/items")).body or {}
    check("share: with edit her entry lands in the admin's project", r.status in (200, 201)
          and any(e["title"] == "Maya's herb idea" for e in items.get("entries", [])), r)
    r = await c.put("/api/projects/p1", {"title": "Renamed"}, tok)
    check("share: only the owner renames (403 owner_only)", r.status == 403, r)
    await c.put("/api/sharing/project/p1", {"user_id": mid, "level": "none"})
    r = await c.get("/api/projects/p1/items", tok)
    check("share: unshare hides the project (404)", r.status == 404 and "p1" not in ids((await c.get("/api/projects", tok)).body), r)

    # The shopping list: a share is an invitation; it never switches her list; she uses it.
    lid = ((await c.get("/api/shopping/lists")).body or {}).get("active")
    r = await c.post("/api/shopping/items", {"items": ["Pita", {"name": "חלב", "quantity": "2", "note": "3%"}]}, tok)
    check("share: she adds to her own list first", r.status == 200 and [x["status"] for x in r.body["results"]]
          == ["added", "added"] and r.body["list"]["id"] != lid, r)
    await c.put(f"/api/sharing/shopping_list/{lid}", {"user_id": mid, "level": "edit"})
    screen = await c.get("/api/shopping", tok)
    body = screen.body or {}
    check("share: an edit share leaves her on her own list, with an invitation",
          screen.status == 200 and body.get("list", {}).get("id") != lid
          and [(i["list_id"], i["access"], i["owner"]["name"]) for i in body.get("invites", [])] == [(lid, "edit", ADMIN_NAME)]
          and any(x["id"] == lid and not x["active"] for x in body.get("lists", [])), screen)
    EXAMPLES["GET /api/shopping — the member: her own list, an invitation, the shared list in lists"] = body
    phone_notes = [e for e in ((await c.get("/api/phone/events?after=0", tok)).body or {}).get("events", [])
                   if e.get("type") == "notify" and e.get("data", {}).get("tag") == f"shopping-invite-{lid}"]
    check("share: the invitation reached her phone as a notification", len(phone_notes) == 1
          and phone_notes[0]["data"]["text"] == f"{ADMIN_NAME} shared their shopping list with you", phone_notes)
    async with c.s.get(f"{BASE}/api/shopping/events", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}) as stream:
        first = await sse_event(stream)
        r = await c.post(f"/api/shopping/invites/{lid}", {"action": "use", "move_items": True}, tok)
        moved = await sse_event(stream)
    EXAMPLES["GET /api/shopping/events — frames"] = [first, moved]
    body = r.body or {}
    names = [i["name"] for i in body.get("items", [])]
    check("share: use + move_items: the shared list is hers now, her items moved onto it",
          r.status == 200 and body["list"]["id"] == lid and body["list"]["access"] == "edit"
          and {"Pita", "חלב"} <= set(names) and {"s1", "s2"} <= ids(body.get("items")), r)
    check("share: one undo for the whole step", (body.get("undo") or {}).get("label")
          == f"Switched to {ADMIN_NAME}'s shopping list and moved 2 items", body.get("undo"))
    check("share: the admin's stream saw the change", first[0] == "changed" and first[1]["list_id"] == lid
          and moved == ("changed", {"list_id": lid, "version": body["list"]["version"]}), (first, moved))
    EXAMPLES["GET /api/shopping — the member on the shared list (after use)"] = body
    milk = next((i for i in body.get("items", []) if i["name"] == "חלב"), {})
    check("share: a moved item keeps its quantity, note and who added it", milk.get("quantity") == "2"
          and milk.get("note") == "3%" and (milk.get("added_by") or {}).get("name") == MEMBER_NAME, milk)
    r = await c.post(f"/api/undo/{body['undo']['id']}", None, tok)
    mine = (await c.get("/api/shopping", tok)).body or {}
    check("share: the undo puts her items and her own list back", r.status == 200 and mine["list"]["id"] != lid
          and {"Pita", "חלב"} <= {i["name"] for i in mine["items"]} and len(mine["invites"]) == 1, (r, mine.get("list")))
    await c.post(f"/api/shopping/invites/{lid}", {"action": "use"}, tok)
    r = await c.post("/api/shopping/items", {"text": "2 tomatoes, Soap"}, tok)
    EXAMPLES["POST /api/shopping/items {text: '2 tomatoes, Soap'} — the answer"] = r.body
    check("share: typed text added to the shared list (quantity parsed)", r.status == 200
          and [(x["name"], x["status"]) for x in r.body["results"]] == [("tomatoes", "added"), ("Soap", "added")]
          and next(i for i in r.body["items"] if i["name"] == "tomatoes")["quantity"] == "2", r)
    aisles: Dict[str, str] = {}
    for _ in range(20):
        items = {i["name"]: i["category"] for i in ((await c.get("/api/shopping")).body or {}).get("items", [])}
        if items.get("tomatoes") == "produce" and items.get("Soap") == "household":
            aisles = items
            break
        await asyncio.sleep(0.5)
    check("aisles: the background lookup moved the new items (stub model: produce / household)",
          aisles.get("tomatoes") == "produce" and aisles.get("Soap") == "household", aisles)
    r = await c.get("/api/usage/events?sort=recent&limit=50", tok)
    check("aisles: the lookup ran as her (purpose shopping_category in her usage)", "shopping_category" in r.text, r)
    await c.put(f"/api/sharing/shopping_list/{lid}", {"user_id": mid, "level": "none"})
    mine = (await c.get("/api/shopping", tok)).body or {}
    check("share: unshare puts her back on her own list, no invitation left", mine["list"]["id"] != lid
          and mine["invites"] == [] and {"Pita", "חלב"} <= {i["name"] for i in mine["items"]}, mine.get("list"))
    r = await c.get(f"/api/shopping?list={lid}", tok)
    check("share: the admin's list is unknown to her now (404)", r.status == 404, r)
    code, out = users_cli("sharing")
    check("cli: users.py sharing runs (nothing shared now)", code == 0, out)


# =================================================================================================
# 5. Home Assistant watcher
# =================================================================================================

async def reminders(c: Client, token: str, title: str) -> List[Dict[str, Any]]:
    evs = (await c.phone_events(token)).get("events", [])
    # Task titles carry the emoji the model picked for them (the fake model answers 📝).
    return [e for e in evs if e["type"] == "reminder" and str(e["data"].get("task_title", "")).endswith(title)]


async def watcher(c: Client, maya: Dict[str, str]) -> None:
    print("\n== 5. Home Assistant watcher")
    tok = maya["token"]
    for t, dev in ((ADMIN_TOKEN, "e2e-admin-phone"), (tok, "e2e-maya-phone")):
        r = await c.post("/api/phone/register", {"device_id": dev, "token": f"fcm-{dev}", "name": dev}, t)
        check(f"phone: register {dev}", r.status == 200, r)
    mk = [(ADMIN_TOKEN, "E2E arrive work", {"location": {"location": "work", "event": "arrive"}}),
          (ADMIN_TOKEN, "E2E car enter", {"car": "enter"}),
          (ADMIN_TOKEN, "E2E arrive office", {"location": {"location": "office", "event": "arrive"}}),
          (tok, "E2E Maya gym", {"location": {"location": "gym", "event": "arrive"}}),
          (tok, "E2E Maya car", {"car": "enter"}),
          (tok, "E2E Maya home", {"location": {"location": "home", "event": "arrive"}})]
    for t, title, trig in mk:
        r = await c.post("/api/tasks", {"title": title, "triggers": trig}, t)
        check(f"watcher: task '{title}' created", r.status == 200, r)
    subs_b = await until(lambda: _subscribers(c, "b"), timeout=25)
    check("watcher: her watcher subscribed on her Home Assistant", subs_b, subs_b)
    await c.ctrl("POST", "/reset")

    await c.ctrl("POST", "/ha/a/state", {"entity_id": "person.robin", "state": "Work"})
    got = await until(lambda: reminders(c, ADMIN_TOKEN, "E2E arrive work"), timeout=15)
    check("watcher: admin's person enters Work → admin's reminder queued", got and len(got) == 1, got)
    tg = await until(lambda: _tg_to(c, ADMIN_TG, "E2E arrive work"), timeout=10)
    check("watcher: … and a Telegram message to the admin's chat", tg, await c.telegram_sent())
    await c.ctrl("POST", "/ha/b/state", {"entity_id": "person.maya", "state": "Gym"})
    got = await until(lambda: reminders(c, tok, "E2E Maya gym"), timeout=15)
    check("watcher: her person enters Gym → her reminder queued", got and len(got) == 1, got)
    tg = await until(lambda: _tg_to(c, MEMBER_TG, "E2E Maya gym"), timeout=10)
    check("watcher: … and a Telegram message to her chat", tg, await c.telegram_sent())
    admin_titles = " | ".join(str(e["data"].get("task_title")) for e in (await c.phone_events(ADMIN_TOKEN))["events"]
                              if e["type"] == "reminder")
    maya_titles = " | ".join(str(e["data"].get("task_title")) for e in (await c.phone_events(tok))["events"]
                             if e["type"] == "reminder")
    check("watcher: each reminder only in its user's phone events", "Maya" not in admin_titles
          and "arrive work" not in maya_titles and "Water the plants" not in maya_titles, (admin_titles, maya_titles))
    sent = await c.telegram_sent()
    check("watcher: no Telegram message crossed to the other chat",
          not any(m["chat_id"] == ADMIN_TG and "Maya" in m["text"] for m in sent)
          and not any(m["chat_id"] == MEMBER_TG and "arrive work" in m["text"] for m in sent), sent)

    await c.ctrl("POST", "/ha/a/state", {"entity_id": "sensor.pixel_8_bluetooth_connection", "state": "1",
                                         "attributes": {"connected_paired_devices": "['CarKit-1234']"}})
    got = await until(lambda: reminders(c, ADMIN_TOKEN, "E2E car enter"), timeout=15)
    check("watcher: admin's phone connects to the car → admin's car reminder", got and len(got) == 1, got)
    await c.ctrl("POST", "/ha/b/state", {"entity_id": "sensor.maya_phone_bluetooth_connection", "state": "1",
                                         "attributes": {"connected_paired_devices": "['Maya Car']"}})
    got = await until(lambda: reminders(c, tok, "E2E Maya car"), timeout=15)
    check("watcher: her phone connects to her car → her car reminder", got and len(got) == 1, got)
    check("watcher: car reminders did not cross", not await reminders(c, tok, "E2E car enter")
          and not await reminders(c, ADMIN_TOKEN, "E2E Maya car"))

    # A flap (leave + return within TRIGGER_FLAP_MINUTES) does not refire.
    await c.ctrl("POST", "/ha/a/state", {"entity_id": "person.robin", "state": "home"})
    await asyncio.sleep(1)
    await c.ctrl("POST", "/ha/a/state", {"entity_id": "person.robin", "state": "Work"})
    await asyncio.sleep(5)
    check("watcher: a flap (Work → home → Work) does not refire", len(await reminders(c, ADMIN_TOKEN, "E2E arrive work")) == 1,
          await reminders(c, ADMIN_TOKEN, "E2E arrive work"))

    # A settings change through the API takes effect without a restart.
    r = await c.put("/api/addons/homeassistant", {"settings": {"person": "person.robin_alt"}})
    check("watcher: admin changes the person setting (200)", r.status == 200, r)
    subs = await until(lambda: _subscribed(c, "a", "person.robin_alt"), timeout=25)
    check("watcher: the watcher re-subscribed to the new entity", subs, subs)
    await c.ctrl("POST", "/ha/a/state", {"entity_id": "person.robin", "state": "Office"})
    await asyncio.sleep(4)
    check("watcher: the old entity no longer fires", not await reminders(c, ADMIN_TOKEN, "E2E arrive office"))
    await c.ctrl("POST", "/ha/a/state", {"entity_id": "person.robin_alt", "state": "Office"})
    got = await until(lambda: reminders(c, ADMIN_TOKEN, "E2E arrive office"), timeout=15)
    check("watcher: the new entity fires", got and len(got) == 1, got)
    r = await c.put("/api/addons/homeassistant", {"settings": {"person": "person.robin"}})
    check("watcher: setting put back", r.status == 200, r)

    # Disconnecting stops her watcher.
    r = await c.delete("/api/addons/homeassistant", tok)
    check("watcher: she disconnects Home Assistant", r.status == 200 and (r.body or {}).get("connected") is False, r)
    gone = await until(lambda: _no_subscribers(c, "b"), timeout=25)
    check("watcher: her watcher stopped (no subscriber on her Home Assistant)", gone, await c.ctrl("GET", "/ha/b/log"))
    await c.ctrl("POST", "/ha/b/state", {"entity_id": "person.maya", "state": "home"})
    await asyncio.sleep(4)
    check("watcher: after disconnect her zone change fires nothing", not await reminders(c, tok, "E2E Maya home"))


async def _subscribers(c: Client, which: str) -> int:
    return (await c.ctrl("GET", f"/ha/{which}/log"))["subscribers"]


async def _no_subscribers(c: Client, which: str) -> bool:
    return (await c.ctrl("GET", f"/ha/{which}/log"))["subscribers"] == 0


async def _subscribed(c: Client, which: str, eid: str) -> bool:
    log = await c.ctrl("GET", f"/ha/{which}/log")
    return bool(log["subscriptions"]) and eid in log["subscriptions"][-1] and log["subscribers"] >= 1


async def _tg_to(c: Client, chat: int, text: str) -> List[Dict[str, Any]]:
    return [m for m in await c.telegram_sent() if m["chat_id"] == chat and text in (m["text"] or "")]


# =================================================================================================
# 6. WAHA inbound and the internal-host rule
# =================================================================================================

async def voice_pushes(c: Client, token: str, mid: str) -> List[Dict[str, Any]]:
    evs = (await c.phone_events(token)).get("events", [])
    return [e for e in evs if e["type"] == "notify" and e["data"].get("tag") == f"wa_voice_false_137181406429317@lid_{mid}"]


async def waha_inbound(c: Client, maya: Dict[str, str]) -> None:
    print("\n== 6. WAHA inbound")
    tok = maya["token"]
    internal = {"Host": INTERNAL_HOST}
    await c.ctrl("POST", "/llm/transcript", {"text": "Voice note for whoever owns this account."})
    r = await c.post("/webhook/waha", voice_event(ADMIN_ACCOUNT, "default", "E2EVA"), token=None, headers=internal)
    check("waha: internal host posts without a token (200)", r.status == 200, r)
    got = await until(lambda: voice_pushes(c, ADMIN_TOKEN, "E2EVA"), timeout=15)
    check("waha: the admin's account → the admin's phone", got, (await c.phone_events(ADMIN_TOKEN))["events"][-3:])
    tg = await until(lambda: _tg_to(c, ADMIN_TG, "Voice note for whoever"), timeout=10)
    check("waha: … and the admin's voice Telegram chat", tg, await c.telegram_sent())
    check("waha: nothing for the member", not await voice_pushes(c, tok, "E2EVA"))

    r = await c.post("/webhook/waha", voice_event(MEMBER_ACCOUNT, "maya", "E2EVM"), token=None, headers=internal)
    got = await until(lambda: voice_pushes(c, tok, "E2EVM"), timeout=15)
    check("waha: her account → her phone", r.status == 200 and got, r)
    tg = await until(lambda: _tg_to(c, MEMBER_TG, "Voice note for whoever"), timeout=10)
    check("waha: … and her voice Telegram chat", tg, await c.telegram_sent())
    check("waha: nothing for the admin", not await voice_pushes(c, ADMIN_TOKEN, "E2EVM"))
    waha = await c.ctrl("GET", "/waha/log")
    media = [x for x in waha["requests"] if x["path"].startswith("/api/files/")]
    check("waha: media fetched with the WAHA key", media and all(x["key_ok"] for x in media), media)

    before = (await c.ctrl("GET", "/llm/requests"))["transcriptions"]
    r = await c.post("/webhook/waha", voice_event("972599999999@c.us", "stranger", "E2EVX"), token=None, headers=internal)
    await asyncio.sleep(3)
    after = (await c.ctrl("GET", "/llm/requests"))["transcriptions"]
    check("waha: an unknown account is dropped (nothing transcribed, nobody pushed)", r.status == 200 and after == before
          and not await voice_pushes(c, ADMIN_TOKEN, "E2EVX") and not await voice_pushes(c, tok, "E2EVX"), (before, after))

    r = await c.post("/webhook/waha", voice_event(ADMIN_ACCOUNT, "default", "E2EVY"), token=None)
    check("waha: same request from a non-internal host without a token → 401", r.status == 401, r)
    for method, path in [("GET", "/api/tasks"), ("GET", "/api/phone/events"), ("POST", "/webhook"),
                         ("GET", "/api/addons"), ("POST", "/api/notifications/batch"), ("GET", "/media/x.jpg")]:
        r = await c.req(method, path, None, json_body={} if method == "POST" else None, headers=internal)
        check(f"waha: internal host on {method} {path} without a token → 401", r.status == 401, r)


# =================================================================================================
# 7. Phone events and notifications
# =================================================================================================

async def phone(c: Client, maya: Dict[str, str]) -> None:
    print("\n== 7. Phone")
    tok = maya["token"]
    a = await c.phone_events(ADMIN_TOKEN, 0, "e2e-admin-phone")
    m = await c.phone_events(tok, 0, "e2e-maya-phone")
    a_seq, m_seq = {e["seq"] for e in a["events"]}, {e["seq"] for e in m["events"]}
    check("phone: each device fetches only its user's events (disjoint seqs)", a_seq and m_seq and not a_seq & m_seq, (a_seq, m_seq))
    check("phone: fetching marks the device registered", a.get("registered") is True and m.get("registered") is True, (a, m))
    r = await c.get("/api/phone/events?after=0&device_id=e2e-admin-phone", tok)
    check("phone: another user's device id is not hers (registered: false)", (r.body or {}).get("registered") is False, r)
    devs = (await c.get("/api/phone/devices", tok)).body or {}
    check("phone: device lists are per user", ids(devs.get("devices"), "device_id") == {"e2e-maya-phone"}, devs)
    now_ms = int(time.time() * 1000)
    r = await c.post("/api/notifications/batch", {"posted": [{
        "key": "0|com.maya.clinic|7|null|10001", "package": "com.maya.clinic", "app_name": "Clinic",
        "title": "Shift change", "text": "E2E only for Maya: shift moved to 7", "posted_at": now_ms}]}, tok)
    nid = ((r.body or {}).get("ids") or [None])[0]
    check("phone: her notification batch accepted", r.status == 200 and (r.body or {}).get("accepted") == 1 and nid, r)
    check("phone: … listed for her", nid in ids(((await c.get("/api/notifications", tok)).body or {}).get("items")))
    r = await c.get(f"/api/notifications/{nid}")
    check("phone: … invisible to the admin by id (404)", r.status == 404, r)
    r = await c.get("/api/notifications?limit=200")
    check("phone: … not in the admin's Inbox", r.status == 200 and nid not in ids((r.body or {}).get("items")), r)
    r = await c.get("/api/search?q=shift")
    check("phone: … not in the admin's search", nid not in ids((r.body or {}).get("results")), r)
    r = await c.post("/api/notifications/batch", {"posted": [{
        "key": "0|com.robin.bank|1|null|1", "package": "com.robin.bank", "app_name": "Bank",
        "title": "Transfer", "text": "E2E only for Robin", "posted_at": now_ms}]})
    rid = ((r.body or {}).get("ids") or [None])[0]
    r = await c.get(f"/api/notifications/{rid}", tok)
    check("phone: the admin's new notification is invisible to her (404)", rid and r.status == 404, r)


# =================================================================================================
# 8. Telegram
# =================================================================================================

async def telegram(c: Client, maya: Dict[str, str]) -> None:
    print("\n== 8. Telegram")
    await c.ctrl("POST", "/llm/script", [{"match": "E2E-TG-ADMIN", "reply": {"text": "Hello Robin, from Telegram."}}])
    await c.ctrl("POST", "/telegram/message", {"from_id": ADMIN_TG, "text": "E2E-TG-ADMIN hello"})
    got = await until(lambda: _tg_to(c, ADMIN_TG, "Hello Robin, from Telegram."), timeout=25)
    check("telegram: a message from the admin's id is answered in their chat", got, (await c.telegram_sent())[-3:])
    reqs = await c.model_requests("E2E-TG-ADMIN")
    check("telegram: … run as the admin (their prompt, their tools)", reqs and "Robin" in reqs[0]["system"]
          and "run_kubectl" in reqs[0]["tools"], reqs[:1])
    await c.ctrl("POST", "/llm/script", [{"match": "E2E-TG-MAYA", "reply": {"text": "Hello Maya, from Telegram."}}])
    await c.ctrl("POST", "/telegram/message", {"from_id": MEMBER_TG, "text": "E2E-TG-MAYA hello"})
    got = await until(lambda: _tg_to(c, MEMBER_TG, "Hello Maya, from Telegram."), timeout=25)
    check("telegram: a message from her id (added with the CLI) is answered in her chat", got, (await c.telegram_sent())[-3:])
    reqs = await c.model_requests("E2E-TG-MAYA")
    check("telegram: … run as her (her prompt, no ops tools)", reqs and "Maya" in reqs[0]["system"]
          and "Robin" not in reqs[0]["system"] and "run_kubectl" not in reqs[0]["tools"], reqs[:1])
    await c.ctrl("POST", "/telegram/message", {"from_id": UNKNOWN_TG, "text": "E2E-TG-UNKNOWN hello"})
    got = await until(lambda: _tg_to(c, UNKNOWN_TG, "not authorized"), timeout=15)
    check("telegram: an unknown id is refused", got, (await c.telegram_sent())[-3:])
    check("telegram: … and the model never saw it", not await c.model_requests("E2E-TG-UNKNOWN"))
    # Telegram turns live in the session tg_<chat id> of the sender's user.
    r = await c.get(f"/api/sessions/tg_{MEMBER_TG}", maya["token"])
    check("telegram: her Telegram turn is in her own session", r.status == 200 and "E2E-TG-MAYA" in r.text, r)
    r = await c.get(f"/api/sessions/tg_{MEMBER_TG}")
    check("telegram: … which is 404 for the admin", r.status == 404, r)
    r = await c.get(f"/api/sessions/tg_{ADMIN_TG}")
    check("telegram: the admin's Telegram session has their turn", r.status == 200 and "E2E-TG-ADMIN" in r.text, r)
    r = await c.get(f"/api/sessions/tg_{ADMIN_TG}", maya["token"])
    check("telegram: … which is 404 for her", r.status == 404, r)


# =================================================================================================
# 9. Usage
# =================================================================================================

async def usage(c: Client, maya: Dict[str, str]) -> None:
    print("\n== 9. Usage")
    tok = maya["token"]
    a = (await c.get("/api/usage?range=all")).body or {}
    m = (await c.get("/api/usage?range=all", tok)).body or {}
    alls = await c.get("/api/usage?range=all&user=all")
    check("usage: each user sees their own totals", a.get("totals", {}).get("calls", 0) > 0
          and 0 < m.get("totals", {}).get("calls", 0) < a["totals"]["calls"], (a.get("totals"), m.get("totals")))
    check("usage: user=all for the admin covers both", alls.status == 200 and (alls.body or {}).get("totals", {}).get("calls", 0)
          >= a["totals"]["calls"] + m["totals"]["calls"], alls)
    ev = (await c.get("/api/usage/events?sort=recent&limit=200", tok)).body or {}
    refs = {e.get("ref") for e in ev.get("events", [])}
    check("usage: her events are only her runs", ev.get("events") and not any(
        str(r).startswith(("session:s_web_1", "session:e2e-chat", "person:pe1", "call:c1")) for r in refs), refs)
    r = await c.get("/api/usage?user=all", tok)
    check("usage: user=all is admin-only (403 for her)", r.status == 403, r)


# =================================================================================================
# 10. Security rules (docs/access.md, "Security rules")
# =================================================================================================

async def security(c: Client, maya: Dict[str, str]) -> None:
    print("\n== 10. Security rules")
    tok = maya["token"]

    r = await c.get("/api/debug/sessions/e2e-maya", tok)
    check("debug: a member's export has an empty environment", r.status == 200
          and (r.body or {}).get("environment_variables") == {}, r)
    r = await c.get("/api/debug/sessions/e2e-admin-tools")
    shown = (r.body or {}).get("environment_variables") or {}
    check("debug: the admin's export has the environment, credentials masked",
          r.status == 200 and shown.get("TIMEZONE") == "Asia/Jerusalem"
          and all(shown.get(k) == "******" for k in ("HOMEASSISTANT_HOST", "HOMEASSISTANT_TOKEN", "WAHA_URL",
                                                     "WAHA_API_KEY", "API_TOKEN", "CREDENTIALS_KEY")), sorted(shown)[:8])
    check("debug: no secret value in either export", not no_secret_in(r.text), no_secret_in(r.text))

    r = await c.put("/api/addons/cameras", {"settings": {"url": "http://stubs:5000"}}, tok)
    check("cameras: a member's PUT → 403 forbidden (an admin changes it)", r.status == 403
          and (r.body or {}).get("needs") == "manage_users", r)
    r = await c.delete("/api/addons/cameras", tok)
    check("cameras: a member's DELETE → 403", r.status == 403, r)

    # Her Home Assistant was disconnected by the watcher section: each of these would connect it.
    for host, why in [("http://stubs:9999", "an origin no admin uses"), ("http://stubs:8124/?x=", "a query"),
                      ("http://user:pw@stubs:8124", "a user name"), ("file:///etc/passwd", "not http(s)")]:
        r = await c.put("/api/addons/homeassistant", {"credentials": {"host": host, "token": "local-ha-token-b"}}, tok)
        check(f"address: {why} → 400 on host", r.status == 400 and (r.body or {}).get("field") == "host", r)
    r = await c.put("/api/addons/homeassistant", {"credentials": {"host": "http://stubs:8124/",
                                                                        "token": "local-ha-token-b"}}, tok)
    ha = await c.get("/api/addons/homeassistant/options", tok)
    check("address: the allowed origin connects (stored normalised) and works", r.status == 201 and ha.status == 200,
          (r, ha))
    r = await c.get("/api/ha/state/light.living_room%3Fx", tok)
    check("address: a malformed entity id → 400", r.status == 400, r)

    r = await c.put("/api/addons/whatsapp", {"settings": {"voice_telegram_chat_id": ADMIN_TG}}, tok)
    check("telegram chat: a member can't send her transcripts to another user's chat (400)", r.status == 400
          and (r.body or {}).get("field") == "voice_telegram_chat_id", r)
    r = await c.put("/api/addons/whatsapp", {"credentials": {"session": "default"}}, tok)
    check("whatsapp: a member can't take the admin's WAHA session (400)", r.status == 400
          and (r.body or {}).get("field") == "session", r)

    internal = {"Host": INTERNAL_HOST}
    r = await c.post("/webhook/waha", voice_event(ADMIN_ACCOUNT, "default", "E2EVF"), token=tok)
    check("waha: a member's token on the webhook → 403", r.status == 403, r)
    check("waha: … nothing for the admin", not await voice_pushes(c, ADMIN_TOKEN, "E2EVF"))
    before = len((await c.ctrl("GET", "/frigate/log"))["requests"])
    event = voice_event(ADMIN_ACCOUNT, "default", "E2EVO")
    event["payload"]["media"]["url"] = "http://stubs:5000/api/files/voice.ogg"
    r = await c.post("/webhook/waha", event, token=None, headers=internal)
    await until(lambda: voice_pushes(c, ADMIN_TOKEN, "E2EVO"), timeout=15)
    frigate = (await c.ctrl("GET", "/frigate/log"))["requests"][before:]
    check("waha: media named on another host is fetched from the connection's own WAHA (key never sent elsewhere)",
          r.status == 200 and not [x for x in frigate if "/api/files/" in x], frigate)

    devs = (await c.get("/api/phone/devices")).body or {}
    admin_devices = {d["device_id"]: d for d in devs.get("devices") or []}
    taken = next(iter(admin_devices), "")
    r = await c.post("/api/phone/register", {"device_id": taken, "token": "maya-fcm-token"}, tok)
    after = {d["device_id"]: d for d in ((await c.get("/api/phone/devices")).body or {}).get("devices") or []}
    mine = ids(((await c.get("/api/phone/devices", tok)).body or {}).get("devices"), "device_id")
    check("devices: registering the admin's device id gives her her own row; the admin's stays",
          taken and r.status == 200 and taken in after and after[taken] == admin_devices[taken] and taken in mine,
          (taken, r, sorted(after), sorted(mine)))


# =================================================================================================
# 11. Second review: task moves undone, one connection per WhatsApp account
# =================================================================================================

async def _task(c: Client, token: str, task_id: str) -> Dict[str, Any]:
    return next((t for t in (await c.get("/api/tasks", token)).body or [] if t["id"] == task_id), {})


async def _move_as(c: Client, token: str, tag: str, task_id: str) -> List[Dict[str, Any]]:
    events = await c.chat(f"e2e-{tag.lower()}", f"{tag} move it to my General list", token=token, script=[
        {"tool": {"name": "update_task", "args": {"item": task_id, "group": "General"}}}, {"text": "Moved."}])
    tool_end = next((e for e in events if e.get("type") == "tool_end"), {})
    return tool_end.get("undos") or []


async def second_review(c: Client, maya: Dict[str, str]) -> None:
    print("\n== 11. Task moves undone, WhatsApp account claims")
    tok, mid = maya["token"], maya["id"]

    # A task the member moves out of the admin's shared list, then undoes: back to the admin, as hers.
    r = await c.post("/api/tasks", {"title": "E2E move-undo A", "group_id": "home"})
    ta = (r.body or {}).get("id")
    await c.put("/api/sharing/task_list/home", {"user_id": mid, "level": "edit"})
    undos = await _move_as(c, tok, "E2E-MOVE-A", ta)
    moved = await _task(c, tok, ta)
    check("move: the member moves the admin's task to her own list (hers now, the admin no longer sees it)",
          undos and moved and "shared_by" not in moved and not await _task(c, ADMIN_TOKEN, ta), moved)
    r = await c.post(f"/api/undo/{undos[0]['id']}", token=tok) if undos else None
    back = await _task(c, ADMIN_TOKEN, ta)
    check("move undo: the task is back in the admin's list as the admin's own",
          r is not None and r.status == 200 and back.get("group_id") == "home" and "shared_by" not in back, (r, back))
    await c.put("/api/sharing/task_list/home", {"user_id": mid, "level": "none"})
    check("move undo: after unsharing the member no longer sees it", not await _task(c, tok, ta))

    # The same move, then the admin unshares before the undo: refused, the task stays the member's.
    r = await c.post("/api/tasks", {"title": "E2E move-undo B", "group_id": "home"})
    tb = (r.body or {}).get("id")
    await c.put("/api/sharing/task_list/home", {"user_id": mid, "level": "edit"})
    undos = await _move_as(c, tok, "E2E-MOVE-B", tb)
    await c.put("/api/sharing/task_list/home", {"user_id": mid, "level": "none"})
    r = await c.post(f"/api/undo/{undos[0]['id']}", token=tok) if undos else None
    mine = await _task(c, tok, tb)
    check("move undo: refused (409, the reason) once the list it would go back to is unshared",
          r is not None and r.status == 409 and "no longer change" in str((r.body or {}).get("error"))
          and mine and "shared_by" not in mine and not await _task(c, ADMIN_TOKEN, tb), (r, mine))

    # One connection per WhatsApp account: her session now reports the admin's account.
    r = await c.put("/api/addons/whatsapp", {"credentials": {"session": "maya-lies"}}, tok)
    conflict = await until(lambda: _conflict(c, tok), timeout=15)
    mine = (await c.get("/api/addons/whatsapp", tok)).body or {}
    check("whatsapp: a session reporting the admin's account gets no account (conflict shown)",
          r.status == 200 and conflict == ADMIN_ACCOUNT and mine.get("account") is None, (r, mine))
    seen = (await c.get(f"/api/addons/whatsapp?user={mid}")).body or {}
    admin_own = (await c.get("/api/addons/whatsapp")).body or {}
    check("whatsapp: the admin sees the conflict; her own connection keeps the account",
          seen.get("account_conflict") == ADMIN_ACCOUNT and admin_own.get("account") == ADMIN_ACCOUNT
          and "account_conflict" not in admin_own, (seen, admin_own))
    code, out = users_cli("addons", "show", mid)
    check("whatsapp: users.py addons show names the conflict", code == 0 and "account conflict" in out, out)
    check("whatsapp: the conflict is logged (user ids, no content)",
          "is already connected by user" in app_logs(), "")

    internal = {"Host": INTERNAL_HOST}
    await c.ctrl("POST", "/llm/transcript", {"text": "Second review voice note."})
    r = await c.post("/webhook/waha", voice_event(ADMIN_ACCOUNT, "default", "E2EVC"), token=None, headers=internal)
    got = await until(lambda: voice_pushes(c, ADMIN_TOKEN, "E2EVC"), timeout=15)
    await asyncio.sleep(2)
    check("whatsapp: the admin's voice note still goes to the admin only", r.status == 200 and got
          and not await voice_pushes(c, tok, "E2EVC"), r)

    waha_before = len((await c.ctrl("GET", "/waha/log"))["requests"])
    events = await c.chat("e2e-wa-conflict", "E2E-WACONF-1 what are my last chats", token=tok, script=[
        {"tool": {"name": "find_last_chats", "args": {}}}, {"text": "Done."}])
    tool_end = next((e for e in events if e.get("type") == "tool_end"), {})
    requests = (await c.ctrl("GET", "/waha/log"))["requests"][waha_before:]
    check("whatsapp: her WhatsApp tools refuse with the reason and reach no WAHA",
          "already connected by another user" in json.dumps(tool_end)
          and not [x for x in requests if x["session"] == "maya-lies" and "/sessions" not in x["path"]],
          (tool_end, requests))

    r = await c.put("/api/addons/whatsapp", {"credentials": {"session": "maya"}}, tok)
    ok = await until(lambda: _account_is(c, tok, MEMBER_ACCOUNT), timeout=15)
    mine = (await c.get("/api/addons/whatsapp", tok)).body or {}
    check("whatsapp: back on her own session, her account is learned and the conflict is gone",
          r.status == 200 and ok and "account_conflict" not in mine, mine)


async def _conflict(c: Client, token: str) -> Optional[str]:
    return ((await c.get("/api/addons/whatsapp", token)).body or {}).get("account_conflict")


async def _account_is(c: Client, token: str, account: str) -> bool:
    return await _account(c, token) == account


# =================================================================================================
# 12. The web Addons page (static/addons.js)
# =================================================================================================

FIELD_KEYS = {"name", "description", "type", "can_change", "why_not", "rule"}


def addon_shape(i: Any) -> bool:
    """The fields static/addons.js reads from one <addon>."""
    if not (isinstance(i, dict) and {"name", "description", "connected", "can_change", "why_not", "credentials",
                                     "settings", "account", "check", "options"} <= set(i)):
        return False
    creds_ok = all(FIELD_KEYS | {"required", "set", "secret"} <= set(f) and (f["secret"] or "value" in f)
                   and (not f["secret"] or "value" not in f) for f in i["credentials"])
    return creds_ok and all(FIELD_KEYS | {"default", "value"} <= set(f) for f in i["settings"])


def integ_field(item: Dict[str, Any], name: str) -> Dict[str, Any]:
    return next((f for f in (*item.get("credentials", []), *item.get("settings", [])) if f["name"] == name), {})


async def addons_page(c: Client, maya: Dict[str, str]) -> None:
    print("\n== 12. The Addons page")
    tok, mid = maya["token"], maya["id"]
    index = await c.get("/")
    check("page: the Settings tile, the panel and its assets are in the web UI",
          index.status == 200 and 'id="btn-open-addons-tab"' in index.text and 'id="panel-addons"' in index.text
          and "/static/addons.js" in index.text and "/static/addons.css" in index.text, index)
    js = await c.get("/static/addons.js", token=None)
    sw = await c.get("/sw.js", token=None)
    check("page: its script is public like the other static files and the service worker lets its API through",
          js.status == 200 and "DanaAddonsCore" in js.text and sw.status == 200
          and '"/api/addons"' in sw.text and "ganam-hub-v12" in sw.text, (js.status, sw.status))

    # Every request the page makes, with the fields it reads: the admin, then the member.
    seen: List[str] = []
    for who, token in (("admin", ADMIN_TOKEN), ("member", tok)):
        me = await c.get("/api/me", token)
        check(f"page ({who}): GET /api/me has id, name, access, capabilities",
              me.status == 200 and has("id", "name", "access", "capabilities")(me.body), me)
        users_r = await c.get("/api/users", token)
        if who == "admin":
            check("page (admin): GET /api/users lists id, name, access only",
                  users_r.status == 200 and {u["id"] for u in users_r.body["users"]} >= {mid}
                  and all(set(u) == {"id", "name", "access"} for u in users_r.body["users"]), users_r)
        else:
            check("page (member): GET /api/users → 403 (no picker)", users_r.status == 403
                  and users_r.body == {"error": "forbidden", "needs": "manage_users"}, users_r)
        lst = await c.get("/api/addons", token)
        items = (lst.body or {}).get("addons") or []
        check(f"page ({who}): GET /api/addons has every field the page reads",
              lst.status == 200 and items and all(addon_shape(i) for i in items),
              [i.get("name") for i in items if not addon_shape(i)] or lst)
        seen.append(lst.text)
    admin_items = {i["name"]: i for i in (await c.get("/api/addons")).body["addons"]}
    for_maya = await c.get(f"/api/addons?user={mid}")
    check("page (admin): ?user= gives Maya's addons in the same shape", for_maya.status == 200
          and for_maya.body["user_id"] == mid and all(addon_shape(i) for i in for_maya.body["addons"]), for_maya)
    seen.append(for_maya.text)
    opts = await c.get("/api/addons/homeassistant/options")
    check("page (admin): the Home Assistant suggestions answer {kind, settings: {name: [{value, label}]}}",
          opts.status == 200 and opts.body["name"] == "homeassistant"
          and all(isinstance(v, list) and all({"value", "label"} <= set(o) for o in v) for v in opts.body["settings"].values()),
          opts)

    # The full flow as the page does it: the admin moves their Home Assistant to the second stub and back.
    ha = admin_items["homeassistant"]
    original = integ_field(ha, "host").get("value")
    check("flow: the admin sees their Home Assistant address, the token only as set",
          original == "http://stubs:8123" and integ_field(ha, "token").get("set") is True
          and "value" not in integ_field(ha, "token"), ha["credentials"])
    before = len((await c.ctrl("GET", "/ha/b/log"))["requests"])
    r = await c.put("/api/addons/homeassistant", {"credentials": {"host": "http://stubs:8124/"}})
    check("flow: Save with only the new address → 200, the address stored normalised, the token kept",
          r.status == 200 and integ_field(r.body, "host").get("value") == "http://stubs:8124"
          and integ_field(r.body, "token").get("set") is True, r)
    t = await c.post("/api/addons/homeassistant/test")
    check("flow: Test connection → the second stub refuses the old token, said in short",
          t.status == 200 and t.body["ok"] is False and t.body["reason"] == "Home Assistant refused the token (HTTP 401).", t)
    r = await c.put("/api/addons/homeassistant", {"credentials": {"token": "local-ha-token-b"}})
    t = await c.post("/api/addons/homeassistant/test")
    log_b = (await c.ctrl("GET", "/ha/b/log"))["requests"][before:]
    check("flow: with its token the test passes against the second stub",
          r.status == 200 and t.body == {"name": "homeassistant", "checked": True, "ok": True, "reason": ""}
          and any(x["path"] == "/api/config" and x["token_ok"] for x in log_b), (r, t))
    r = await c.put("/api/addons/homeassistant", {"credentials": {"host": original,
                                                                        "token": ENV["HOMEASSISTANT_TOKEN"]}})
    t = await c.post("/api/addons/homeassistant/test")
    check("flow: back to the first stub, test passes again",
          r.status == 200 and integ_field(r.body, "host").get("value") == original and t.body.get("ok") is True, (r, t))
    seen += [r.text, t.text]

    # A member trying what the page shows as read-only gets the reason the page shows.
    mine = {i["name"]: i for i in (await c.get("/api/addons", tok)).body["addons"]}
    check("member: cameras are shown read-only with the reason", mine["cameras"]["can_change"] is False
          and mine["cameras"]["why_not"] == "Set by an admin", mine["cameras"])
    r = await c.put("/api/addons/cameras", {"settings": {"url": "http://stubs:5000"}}, tok)
    check("member: …saving anyway → 403 manage_users (the page says: only an admin can change this)",
          r.status == 403 and r.body == {"error": "forbidden", "needs": "manage_users"}, r)
    check("member: shopping shows only the active-list setting",
          [f["name"] for f in mine["shopping"]["settings"]] == ["active_list"], mine["shopping"])
    host_rule = integ_field(mine["homeassistant"], "host")
    check("member: her Home Assistant address is shown to her, with the allowed-origins rule",
          (host_rule.get("value") == "http://stubs:8124" or not mine["homeassistant"]["connected"])
          and host_rule.get("can_change") is True
          and host_rule.get("rule") == "Only an address an admin has allowed for this service.", host_rule)
    seen.append(r.text)
    leaks = [k for text in seen for k in no_secret_in(text)]
    check("page: no secret in any answer the page reads", not leaks, leaks)


# =================================================================================================

# =================================================================================================
# 13. The web Shopping panel (static/shopping.js, static/shopping-core.js)
# =================================================================================================

async def shopping_page(c: Client, maya: Dict[str, str]) -> None:
    print("\n== 13. The Shopping panel")
    tok, mid = maya["token"], maya["id"]
    page = await c.get("/shopping")
    check("shopping: /shopping serves the app with the panel, its nav buttons and its assets",
          page.status == 200 and 'id="panel-shopping"' in page.text and page.text.count("data-shopping-nav") == 2
          and all(f"/static/{f}" in page.text for f in ("shopping.js", "shopping-core.js", "shopping.css")), page)
    r = await c.get("/shopping", token=None)
    check("shopping: signed out → the login page, coming back to /shopping",
          r.status in (302, 303) and r.headers.get("Location", "").endswith("next=/shopping"), (r.status, r.headers.get("Location")))
    assets = {f: await c.get(f"/static/{f}", token=None) for f in ("shopping.js", "shopping-core.js", "shopping.css")}
    check("shopping: its assets are public like the other static files",
          all(a.status == 200 for a in assets.values()) and "DanaShoppingCore" in assets["shopping-core.js"].text
          and "window.DanaShopping" in assets["shopping.js"].text and ".shp-row" in assets["shopping.css"].text,
          {f: a.status for f, a in assets.items()})
    sw = await c.get("/sw.js", token=None)
    check("shopping: the service worker caches the new files and never the shopping API",
          '"/static/shopping.js"' in sw.text and '"/static/shopping-core.js"' in sw.text
          and 'requestUrl.pathname.startsWith("/api/shopping/")' in sw.text, sw.status)
    man = (await c.get("/manifest.json", token=None)).body or {}
    check("shopping: the manifest has a Shopping shortcut to /shopping",
          any(sc.get("url") == "/shopping" for sc in man.get("shortcuts", [])), man.get("shortcuts"))
    for who, token in (("admin", ADMIN_TOKEN), ("member", tok)):
        me = (await c.get("/api/me", token)).body or {}
        check(f"shopping: /api/me tells the panel the {who} has shopping (the nav button shows)", "shopping" in me.get("kinds", []), me)
        r = await c.get("/api/shopping/suggestions?q=to", token)
        check(f"shopping: the {who}'s suggestions while typing answer as the panel reads them",
              r.status == 200 and isinstance((r.body or {}).get("suggestions"), list), r)

    # Live: the admin's panel follows his list; the member changes it with ?list= (as the panel writes).
    lid = ((await c.get("/api/shopping/lists")).body or {}).get("active")
    await c.put(f"/api/sharing/shopping_list/{lid}", {"user_id": mid, "level": "edit"})
    async with c.s.get(f"{BASE}/api/shopping/events", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
                       timeout=aiohttp.ClientTimeout(total=30)) as stream:
        first = await sse_event(stream)
        while first[0] == "lists":
            first = await sse_event(stream)
        r = await c.post(f"/api/shopping/items?list={lid}", {"text": "Lemons"}, tok)
        check("shopping: the member adds to the shared list with ?list= without making it hers",
              r.status == 200 and (r.body or {}).get("list", {}).get("id") == lid, r)
        nxt = await sse_event(stream)
        while nxt[0] == "lists":
            nxt = await sse_event(stream)
    check("shopping: the admin's stream delivers the member's change with a newer version",
          first[0] == "changed" and nxt[0] == "changed" and nxt[1]["list_id"] == lid and nxt[1]["version"] > first[1]["version"],
          (first, nxt))
    screen = (await c.get("/api/shopping")).body or {}
    lemons = next((i for i in screen.get("items", []) if i["name"] == "Lemons"), {})
    check("shopping: the admin's next read shows it, added by the member",
          (lemons.get("added_by") or {}).get("id") == mid and screen["list"]["version"] == nxt[1]["version"], lemons)
    await c.put(f"/api/sharing/shopping_list/{lid}", {"user_id": mid, "level": "none"})


async def main() -> int:
    if (STATE / "users.json").exists():
        print("This environment already ran the end-to-end script once (tests/local_env/.state/users.json exists).\n"
              "Run `bin/local-env.sh reset` for a fresh environment, then `bin/local-env.sh test`.")
        return 2
    async with aiohttp.ClientSession() as session:
        c = Client(session)
        try:
            await c.ctrl("GET", "/health")
        except Exception as e:
            print(f"The environment is not reachable ({e}); start it with `bin/local-env.sh up`.")
            return 2
        try:
            state = await upgrade(c)
            await restart_and_key_checks(c, state)
            await existing_clients(c)
            maya = await second_user(c, state["admin_id"])
            for fn in (sharing, watcher, waha_inbound, phone, telegram, usage, security, second_review,
                       addons_page, shopping_page):
                try:
                    await fn(c, maya)
                except Exception as e:
                    check(f"{fn.__name__}: section ran to the end", False, traceback.format_exc())
        except Exception:
            check("end-to-end script ran to the end", False, traceback.format_exc())
    failed = [n for n, ok, _ in results if not ok]
    logs = app_logs()
    # FCM stays unconfigured here (pushes queue for the phone to fetch); the
    # WhatsApp voice path logs that as ERROR, which is expected in this environment.
    expected = ("wasn't delivered now (queued, or no phone)",)
    errors = [l for l in logs.splitlines() if (" - ERROR - " in l or "Traceback" in l)
              and not any(x in l for x in expected)]
    check("app log: no ERROR lines or tracebacks during the run", not errors, "\n".join(errors[:10]))
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    for n in failed:
        print(f"  FAIL {n}")
    (STATE / "shopping-examples.json").write_text(json.dumps(EXAMPLES, indent=1, ensure_ascii=False))
    (STATE / "e2e-results.json").write_text(json.dumps([{"check": n, "ok": ok, "detail": d} for n, ok, d in results], indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
