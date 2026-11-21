"""Cross-user security rules (docs/access.md, "Security rules").
Run: bin/test test_security

One check group per finding of the security reviews of the multi-user
branch, each against the real handlers / functions: the first admin (the
default addons plus Home Assistant, WhatsApp and Frigate), Bob (a member with his own token and Telegram id)
and Carl (another member). In-process fakes only (Home Assistant, WAHA,
Frigate, an "internal service", an "attacker" server); no network beyond
localhost (and name lookups that are refused).

1. The session debug export: no environment for a member (web and Telegram
   ``/debug``); an admin's environment with credentials, service-account JSON
   and private keys masked.
2. Addon addresses: ``cameras`` changed only by a user with
   ``manage_users``; address fields validated and normalised (scheme, host,
   no userinfo / query / fragment); a member's address only on an origin an
   admin allowed (their own connection of the addon, ``MEMBER_ALLOWED_ORIGINS``);
   ``test`` / ``options`` only on a stored, valid address; request URLs built so
   a path can never become a query or another host (stored legacy values,
   entity ids and camera names).
2b. Knowledge link downloads, through saving, changing (HTTP, tools) and
   Retry: a member's link refused early when plainly internal, and downloaded
   restricted whenever the user who set it lacks ``manage_server`` — whoever
   presses Retry; editing other fields or undoing keeps who set the link.
3. ``/webhook/waha``: only a body WAHA signed with ``WAHA_WEBHOOK_SECRET``
   (HMAC-SHA512; the ``Host`` header counts for nothing) or a user with
   ``manage_server``; media always fetched from the connection's own origin,
   the API key never sent elsewhere.
3b. A WhatsApp account belongs to one connection: a second connection whose
   WAHA reports it (a member's own WAHA, the admin's WAHA under another
   hostname) gets no account, is never routed to, its tools refuse; an admin
   sees the conflict (API, bin/users.py); two claims at once → one holder.
4. Telegram chat settings: a member only their own chat (never someone
   else's, never a group); shopping has no Telegram setting.
5. Shopping: a share never changes a user's active list, the user chooses it
   (``share_item`` says so); an invitation answered for a list not shared
   with the user answers like an unknown list; ``move_items`` moves only the
   caller's own items; a stream never delivers another list's events; undo
   after unsharing is refused.
6. Undo re-checks access to a task (Now, the task tools): the specific
   refusal, the rows afterwards; a move between lists undone puts the owner
   back with the list and is refused when either list is no longer writable;
   moves over HTTP follow the same rule.
7. Device registration never moves another user's device without its token.
8. A route module that cannot be imported makes the server's startup fail.
9. Telegram multi-select state is per user and per prompt message.
"""

import asyncio
import datetime
import importlib.util
import io
import json
import logging
import os
import pathlib
import subprocess
import sys
import threading
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("security_test")
os.environ["API_TOKEN"] = "security-test-env-token"
os.environ["ALLOWED_TELEGRAM_USER_IDS"] = "111"
os.environ.setdefault("GEMINI_API_KEY", "test-not-a-key")
WAHA_SECRET = "test-waha-webhook-secret"  # a test value
os.environ["WAHA_WEBHOOK_SECRET"] = WAHA_SECRET
os.environ["TIMEZONE"] = "UTC"  # no Home Assistant lookup for the server's zone
from cryptography.fernet import Fernet  # noqa: E402
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
# Values a production pod carries (fake here), for the debug export.
FAKE_SA = '{"type": "service_account", "private_key": "-----BEGIN PRIVATE KEY-----\\nFAKE\\n-----END PRIVATE KEY-----"}'
os.environ["FCM_SERVICE_ACCOUNT_JSON"] = FAKE_SA
os.environ["SOME_HARMLESS_NAME"] = '{"client": "x", "private_key": "abc"}'
os.environ["OWNER_CONTEXT"] = "The admin's private profile text"
os.environ["HOMEASSISTANT_TOKEN"] = "fake-ha-token"  # a secret-looking name in the env: masked for an admin
os.environ["WAHA_SESSION"] = "default"
os.environ["PLAIN_SETTING_FOR_TEST"] = "visible-value"

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from aiogram import types  # noqa: E402

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()
# The admin's own services (what an operator connects with bin/users.py).
testenv.connect_admin("homeassistant", credentials={"host": "http://homeassistant.internal:8123", "token": "fake-ha-token"})
testenv.connect_admin("whatsapp", credentials={"url": "http://waha.internal:3000/api", "session": "default"})
testenv.connect_admin("cameras", settings={"url": "http://frigate.internal:5000"})

import core.addons_api as addons_api  # noqa: E402
import core.now as now  # noqa: E402
import core.server as server  # noqa: E402
from core import debug_api, sessions_api  # noqa: E402
from addons.cameras import api as cameras_api  # noqa: E402
from addons.homeassistant import api as homeassistant_api  # noqa: E402
from addons.knowledge import api as knowledge_api  # noqa: E402
from addons.phone import api as phone_api  # noqa: E402
from addons.tasks import api as tasks_api  # noqa: E402
from addons.whatsapp import api as whatsapp_api  # noqa: E402
import core.telegram_bot as tb  # noqa: E402
from core import outbound, session, undo, undoable  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.telegram_bot import AuthMiddleware, TelegramBot  # noqa: E402
from core.addons import addresses, crypto, registry, sharing, store  # noqa: E402
from addons.cameras import client as cameras_client  # noqa: E402
from addons.homeassistant import client as ha_client  # noqa: E402
from addons.knowledge import media as knowledge_media, projects as knowledge_projects  # noqa: E402
from addons.phone import push  # noqa: E402
from addons.shopping import api as shopping_api, lists as shopping_lists  # noqa: E402
from addons.tasks import groups as task_groups, service as tasks_service  # noqa: E402
from addons.whatsapp import accounts as wa_accounts, client as wa_client  # noqa: E402
from addons.tasks import titles as task_titles  # noqa: E402
from testfakes import FakeHA, FakeWAHA  # noqa: E402
import litellm  # noqa: E402


async def _plain_title(title: str) -> str:
    return title


async def _no_model(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("no model calls in this test")


task_titles.sanitize_task_title = _plain_title  # no model call for an emoji
litellm.acompletion = _no_model  # nothing leaves the machine

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ADMIN = users.first_admin()
assert ADMIN is not None
BOB = users.create("Bob Builder", telegram_ids=[222])
CARL = users.create("Carl Coder", telegram_ids=[333])
BOB_TOKEN, _ = users.issue_token(BOB.id, "bob")
CARL_TOKEN, _ = users.issue_token(CARL.id, "carl")
ADMIN_TOKEN = os.environ["API_TOKEN"]
for u in (BOB, CARL):
    for addon in ("tasks", "shopping", "knowledge", "phone"):
        store.connect(u, addon)


def h(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def signed_by_waha(event: Dict[str, Any]) -> Dict[str, Any]:
    """Request arguments for a webhook body signed the way WAHA signs it (HMAC-SHA512 with the secret)."""
    raw = json.dumps(event).encode()
    return {"data": raw, "headers": {"Content-Type": "application/json", "X-Webhook-Hmac-Algorithm": "sha512",
                                     "X-Webhook-Hmac": whatsapp_api.signature(WAHA_SECRET, raw)}}


async def tool(user, name: str, **args: Any) -> str:
    with users.acting_as(user):
        return str(await registry.execute(user, name, args))


class Recorder:
    """A tiny server that records every request (path, query, headers) and answers 200."""

    def __init__(self, body: bytes = b"recorded") -> None:
        self.hits: List[Dict[str, Any]] = []
        self.body = body
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.any)
        self.server = TestServer(app)

    async def any(self, request: web.Request) -> web.Response:
        self.hits.append({"path": request.path, "query": request.query_string, "headers": dict(request.headers)})
        return web.Response(body=self.body, content_type="application/octet-stream")

    async def start(self) -> None:
        await self.server.start_server()
        self.origin = str(self.server.make_url("")).rstrip("/")

    async def close(self) -> None:
        await self.server.close()


def make_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    addons_api.setup(app)
    shopping_api.setup(app)
    r = app.router
    r.add_post("/api/sessions", sessions_api.create_session_api)
    r.add_get("/api/debug/sessions/{session_id}", debug_api.get_debug_session_detail_api)
    r.add_post("/webhook/waha", whatsapp_api.handle_waha_webhook)
    r.add_get("/api/frigate/events/{camera_name}", cameras_api.frigate_events_api)
    r.add_get("/api/frigate/event/{event_id}/clip.mp4", cameras_api.frigate_event_clip_api)
    r.add_get("/api/frigate/{camera_name}", cameras_api.frigate_camera_proxy_api)
    r.add_get("/api/camera/{entity_id}/proxy", cameras_api.ha_camera_proxy_api)
    r.add_get("/api/ha/state/{entity_id}", homeassistant_api.ha_entity_state_api)
    r.add_post("/api/phone/register", phone_api.phone_register_api)
    r.add_put("/api/tasks/{id}", tasks_api.update_task_api)
    r.add_post("/api/entries", knowledge_api.create_entry_api)
    r.add_put("/api/entries/{id}", knowledge_api.update_entry_api)
    r.add_post("/api/entries/{id}/retry", knowledge_api.retry_entry_api)
    return app


# --- 1. Debug export ------------------------------------------------------------------------------

async def test_debug_export(http: TestClient) -> None:
    await http.post("/api/sessions", json={"session_id": "dbg"}, headers=h(BOB_TOKEN))
    await http.post("/api/sessions", json={"session_id": "dbg-admin"}, headers=h(ADMIN_TOKEN))
    r = await http.get("/api/debug/sessions/dbg", headers=h(BOB_TOKEN))
    body = await r.json()
    check("debug: a member's export has an empty environment (same payload shape)",
          r.status == 200 and body.get("environment_variables") == {} and "history" in body, str(body)[:300])
    text = json.dumps(body)
    check("debug: nothing of the server's env in a member's export",
          "BEGIN PRIVATE KEY" not in text and "private profile" not in text and "waha.internal" not in text)

    r = await http.get("/api/debug/sessions/dbg-admin", headers=h(ADMIN_TOKEN))
    admin_body = await r.json()
    env = admin_body.get("environment_variables") or {}
    if r.status != 200:
        print("admin debug:", r.status, admin_body)
    masked = lambda k: env.get(k) == "******"  # noqa: E731
    check("debug: an admin still gets the environment", r.status == 200 and env.get("PLAIN_SETTING_FOR_TEST") ==
          "visible-value", str(env.get("PLAIN_SETTING_FOR_TEST")))
    for name in ("FCM_SERVICE_ACCOUNT_JSON", "SOME_HARMLESS_NAME", "API_TOKEN", "CREDENTIALS_KEY",
                 "HOMEASSISTANT_TOKEN", "WAHA_SESSION", "GEMINI_API_KEY"):
        check(f"debug: an admin's export masks {name}", masked(name), str(env.get(name))[:60])
    check("debug: no private key anywhere in an admin's export", "PRIVATE KEY" not in json.dumps(env))

    # Telegram /debug sends the same export as a file.
    bot = TelegramBot.__new__(TelegramBot)
    bot.bot = AsyncMock()
    bot.get_keyboard = lambda: None
    session.save_session_debug(BOB.id, "tg_222")
    await bot._send_debug_file(BOB, 222, 222, "boom")
    doc = bot.bot.send_document.await_args.kwargs["document"]
    sent = json.loads(doc.data.decode())
    check("debug: Telegram /debug for a member carries no environment",
          sent.get("environment_variables") == {} and "BEGIN PRIVATE KEY" not in doc.data.decode(),
          str(sent.get("environment_variables"))[:200])


# --- 2. Addon addresses ---------------------------------------------------------------------

def _raw_settings(user, addon: str, settings: Dict[str, Any], credentials: Dict[str, str] = None) -> None:
    """Write a connection straight into the table (a value stored before validation existed)."""
    with db.get_db_connection() as c:
        c.execute("DELETE FROM connections WHERE user_id = ? AND addon = ?", (user.id, addon))
        c.execute("INSERT INTO connections (user_id, addon, credentials, settings, account, created_at, updated_at) "
                  "VALUES (?, ?, ?, ?, NULL, '2026-01-01', '2026-01-01')",
                  (user.id, addon, crypto.encrypt(credentials) if credentials else None, json.dumps(settings)))
        c.commit()
    store.invalidate()


async def test_addresses(http: TestClient, frigate: Recorder, internal: Recorder, ha_a: FakeHA,
                         ha_b: FakeHA) -> None:
    # Unit: normalisation and refusals.
    check("address: normalised (scheme / host lower case, default port and trailing slash dropped)",
          addresses.normalize("HTTP://Frigate.Example.COM:80/") == "http://frigate.example.com"
          and addresses.normalize("https://h.example:8443/api/") == "https://h.example:8443/api")
    for bad in ("ftp://x.example", "http://user:pw@x.example", "http://x.example/a?x=", "http://x.example/#f",
                "x.example:8123", "http:///nohost", "javascript:alert(1)", "http://x.example/a b"):
        try:
            addresses.normalize(bad)
            ok = False
        except ValueError:
            ok = True
        check(f"address: {bad!r} refused", ok)
    try:
        addresses.join("http://x.example/admin?x=", "/api/events")
        ok = False
    except ValueError:
        ok = True
    check("address: joining onto a base with a query is refused", ok)
    check("address: join keeps the base's path and origin",
          addresses.join("http://x.example:5/api", "/sessions") == "http://x.example:5/api/sessions")
    try:
        addresses.join("http://x.example", "//evil.example/x")
        ok = False
    except ValueError:
        ok = True
    check("address: a path cannot change the host", ok)

    # (a) cameras: only manage_users connects / changes / disconnects it.
    store.update(ADMIN, "cameras", settings={"url": frigate.origin})
    r = await http.put("/api/addons/cameras", json={"settings": {"url": frigate.origin}}, headers=h(BOB_TOKEN))
    body = await r.json()
    check("cameras: a member's own PUT is 403 forbidden (needs manage_users)",
          r.status == 403 and body.get("needs") == "manage_users" and not store.is_connected(BOB, "cameras"), body)
    r = await http.get("/api/frigate/events/front", headers=h(BOB_TOKEN))
    check("cameras: …so a member reaches no Frigate", r.status == 403 and not frigate.hits, r.status)
    r = await http.put(f"/api/addons/cameras?user={BOB.id}", json={"settings": {"url": frigate.origin}},
                       headers=h(ADMIN_TOKEN))
    check("cameras: an admin gives a member camera access (?user=)", r.status == 201 and
          store.is_connected(BOB, "cameras"), r.status)
    r = await http.put("/api/addons/cameras", json={"settings": {"url": internal.origin}}, headers=h(BOB_TOKEN))
    check("cameras: the member still cannot change it", r.status == 403 and
          store.get(BOB, "cameras").settings["url"] == frigate.origin, r.status)
    r = await http.delete("/api/addons/cameras", headers=h(BOB_TOKEN))
    check("cameras: …nor disconnect it", r.status == 403 and store.is_connected(BOB, "cameras"), r.status)
    r = await http.get("/api/frigate/events/front", headers=h(BOB_TOKEN))
    check("cameras: the connection the admin made works for the member", r.status in (200, 502) and frigate.hits,
          r.status)
    try:
        store.update(BOB, "cameras", settings={"url": internal.origin}, by=BOB)
        ok = False
    except store.Forbidden:
        ok = True
    check("cameras: store.update by the member himself is refused", ok)

    # (b) a base URL with a query: refused on save, and a stored legacy one never turns a path into a query.
    r = await http.put(f"/api/addons/cameras?user={BOB.id}",
                       json={"settings": {"url": f"{internal.origin}/admin/secrets?x="}}, headers=h(ADMIN_TOKEN))
    body = await r.json()
    check("address: a URL with a query is refused even for an admin (400 invalid, field url)",
          r.status == 400 and body.get("field") == "url", body)
    _raw_settings(BOB, "cameras", {"url": f"{internal.origin}/admin/secrets?x="})
    internal.hits.clear()
    r = await http.get("/api/frigate/event/1700000000.1-abc/clip.mp4", headers=h(BOB_TOKEN))
    check("address: a stored legacy URL with a query never reaches another path",
          r.status >= 400 and not any(x["path"].startswith("/admin") for x in internal.hits),
          f"{r.status} {internal.hits}")
    r = await http.post("/api/addons/cameras/test", headers=h(BOB_TOKEN))
    body = await r.json()
    check("address: test refuses a stored address that is not valid (no request made)",
          r.status == 400 and body.get("field") == "url" and not internal.hits, f"{r.status} {body}")
    store.update(BOB, "cameras", settings={"url": frigate.origin})  # by=None: an operator

    # (c) Home Assistant host and WAHA url: a member only on an origin an admin allowed.
    store.update(ADMIN, "homeassistant", credentials={"host": ha_a.url, "token": ha_a.token})
    r = await http.put("/api/addons/homeassistant",
                       json={"credentials": {"host": f"{internal.origin}/", "token": "t"}}, headers=h(BOB_TOKEN))
    body = await r.json()
    check("address: a member's HA host on an origin no admin allowed → 400 (field host)",
          r.status == 400 and body.get("field") == "host" and not store.is_connected(BOB, "homeassistant"), body)
    r = await http.put("/api/addons/homeassistant",
                       json={"credentials": {"host": ha_a.url + "/", "token": "bob-token"}}, headers=h(BOB_TOKEN))
    check("address: a member's HA host on the admin's HA origin is accepted, stored normalised",
          r.status == 201 and store.get(BOB, "homeassistant").credentials["host"] == ha_a.url, r.status)
    r = await http.put("/api/addons/homeassistant", json={"credentials": {"host": ha_b.url}},
                       headers=h(BOB_TOKEN))
    check("address: changing it to another origin is refused as well", r.status == 400 and
          store.get(BOB, "homeassistant").credentials["host"] == ha_a.url, r.status)
    os.environ["MEMBER_ALLOWED_ORIGINS"] = f"homeassistant={ha_b.url}, whatsapp=http://waha-b.example:3000"
    r = await http.put("/api/addons/homeassistant", json={"credentials": {"host": ha_b.url, "token": ha_b.token}},
                       headers=h(BOB_TOKEN))
    check("address: an origin in MEMBER_ALLOWED_ORIGINS for the addon is accepted",
          r.status == 200 and store.get(BOB, "homeassistant").credentials["host"] == ha_b.url, r.status)
    r = await http.put("/api/addons/whatsapp", json={"credentials": {"url": f"{ha_b.url}/api",
                                                                           "session": "bob"}}, headers=h(BOB_TOKEN))
    body = await r.json()
    check("address: MEMBER_ALLOWED_ORIGINS is per addon",
          r.status == 400 and body.get("field") == "url" and not store.is_connected(BOB, "whatsapp"), body)
    del os.environ["MEMBER_ALLOWED_ORIGINS"]
    r = await http.put(f"/api/addons/homeassistant?user={CARL.id}",
                       json={"credentials": {"host": internal.origin, "token": "t"}}, headers=h(ADMIN_TOKEN))
    check("address: an admin may set any (valid) address for someone",
          r.status == 201 and store.get(CARL, "homeassistant").credentials["host"] == internal.origin, r.status)
    store.disconnect(CARL, "homeassistant")
    r = await http.put("/api/addons/homeassistant",
                       json={"credentials": {"host": "http://u:p@" + ha_a.url[7:], "token": "t"}}, headers=h(CARL_TOKEN))
    body = await r.json()
    check("address: userinfo refused",
          r.status == 400 and body.get("field") == "host" and not store.is_connected(CARL, "homeassistant"), body)
    # test / options only against a stored, valid address.
    _raw_settings(CARL, "homeassistant", {}, {"host": f"{internal.origin}/x?y=", "token": "t"})
    internal.hits.clear()
    r1 = await http.post("/api/addons/homeassistant/test", headers=h(CARL_TOKEN))
    r2 = await http.get("/api/addons/homeassistant/options", headers=h(CARL_TOKEN))
    check("address: test / options refuse a stored address that is not valid, without a request",
          r1.status == 400 and r2.status == 400 and not internal.hits, f"{r1.status} {r2.status} {internal.hits}")

    # Request URLs are joined so they cannot leave the stored host, also for a value stored before the rules.
    def refused(build) -> bool:
        try:
            build()
            return False
        except ValueError:
            return True
    legacy = store.require(CARL, "homeassistant")
    check("join: a stored Home Assistant host with a query builds no URL",
          refused(lambda: ha_client.url(legacy, "/api/states")))
    store.disconnect(CARL, "homeassistant")
    good = store.require(ADMIN, "homeassistant")
    check("join: a path with a query, '..', '//' or no leading '/' builds no Home Assistant URL",
          all(refused(lambda p=p: ha_client.url(good, p))
              for p in ("/api/states?x=1", "/api/../config", "//evil.example/x", "api/states", "/api/states#x")))
    check("join: a plain path stays on the stored host",
          ha_client.url(good, "/api/states") == addresses.normalize(good.credentials["host"]) + "/api/states")
    frigate_conn = store.require(ADMIN, "cameras")
    check("join: Frigate URLs cannot leave its address",
          refused(lambda: cameras_client.url(frigate_conn, "/api/events?x=1"))
          and refused(lambda: cameras_client.url(frigate_conn, "//evil.example/api")))
    wa = store.require(ADMIN, "whatsapp")
    built = wa_client.api_url(wa, "api", "sessions", "x?y=1#z/w")
    check("join: WAHA path parts are encoded (a part cannot add a query, fragment or segment)",
          built.startswith(addresses.normalize(wa.credentials["url"]).rsplit("/api", 1)[0])
          and "?" not in built and "#" not in built and built.endswith("/x%3Fy=1%23z%2Fw"), built)
    check("join: a '..' part builds no WAHA URL", refused(lambda: wa_client.api_url(wa, "api", "..", "admin")))

    # Entity ids and camera names go into Home Assistant paths: strictly checked.
    store.update(BOB, "homeassistant", credentials={"host": ha_a.url, "token": ha_a.token})
    ha_a.requests.clear()
    for path in ("/api/camera/camera.x%3Fa=1/proxy", "/api/ha/state/sensor.x%3Fa=1", "/api/frigate/x%3Fa=1",
                 "/api/ha/state/..%2Fconfig"):
        r = await http.get(path, headers=h(BOB_TOKEN))
        check(f"entity ids: {path} → 400, nothing sent to Home Assistant", r.status == 400 and not ha_a.requests,
              f"{r.status} {ha_a.requests}")

    # (d) knowledge link downloads: internal addresses refused for a member.
    for url in ("http://127.0.0.1:8080/x", "http://localhost/x", "http://10.1.2.3/x", "http://169.254.169.254/latest",
                "http://[::1]/x", "http://192.168.1.10/", "http://dana.dana.svc/x",
                "file:///etc/passwd", "ftp://example.com/x", "http://100.64.0.1/x"):
        try:
            await outbound.check_public_url(url)
            ok = False
        except outbound.Refused:
            ok = True
        check(f"fetch: {url} refused for a member", ok)
    try:
        await outbound.check_public_url("https://93.184.216.34/video")
        ok = True
    except outbound.Refused:
        ok = False
    check("fetch: a public address is allowed", ok)


# --- 2b. Knowledge link downloads (through saving, changing and Retry) ------------------------------

def entry_link(entry_id: str) -> Tuple[Any, Any]:
    with db.get_db_connection() as c:
        r = c.execute("SELECT url, url_set_by FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return (r["url"], r["url_set_by"]) if r else (None, None)


async def test_link_downloads(http: TestClient) -> None:
    spawned: List[List[str]] = []

    async def fake_exec(*cmd, **kw):
        spawned.append(list(cmd))
        raise RuntimeError("not running yt-dlp in a test")

    async def settle() -> None:
        await asyncio.gather(*list(knowledge_media._tasks), return_exceptions=True)

    def last_cmd() -> List[str]:
        return spawned[-1] if spawned else []

    def restricted(cmd: List[str]) -> bool:
        return "--use-extractors" in cmd and cmd[cmd.index("--use-extractors") + 1] == "default,-generic"

    pid = knowledge_projects.create_project(ADMIN, "Trip links")
    sharing.set_level(ADMIN, knowledge_projects.ITEM_TYPE, pid, BOB.id, "edit")
    with patch.object(knowledge_media.asyncio, "create_subprocess_exec", new=fake_exec):
        r = await http.post("/api/entries", json={"project_id": pid, "kind": "link", "title": "video",
                                                  "url": "https://93.184.216.34/admin-video"}, headers=h(ADMIN_TOKEN))
        eid = (await r.json()).get("id")
        await settle()
        check("fetch: the admin's own link downloads as before (no extractor limit)",
              r.status == 200 and last_cmd() and not restricted(last_cmd()) and last_cmd()[-1].endswith("admin-video")
              and entry_link(eid) == ("https://93.184.216.34/admin-video", ADMIN.id), f"{r.status} {last_cmd()}")

        # Setting a link: refused early for a member when it is plainly not a public http(s) address.
        for bad in ("http://10.96.0.1:443/", "http://127.0.0.1:8080/x", "http://localhost/x", "file:///etc/passwd",
                    "--exec=touch /tmp/pwned", "http://[::1]/x", "http://169.254.169.254/latest"):
            spawned.clear()
            r = await http.put(f"/api/entries/{eid}", json={"url": bad}, headers=h(BOB_TOKEN))
            body = await r.json()
            check(f"fetch: a member setting {bad!r} on an entry → 400 (field url), the link unchanged",
                  r.status == 400 and body.get("field") == "url" and body.get("message")
                  and entry_link(eid) == ("https://93.184.216.34/admin-video", ADMIN.id), f"{r.status} {body}")
        out = await tool(BOB, "update_knowledge_item", item_id=eid, url="http://10.96.0.1:443/")
        check("fetch: …the same through update_knowledge_item (an error, the link unchanged)",
              out.startswith("Error:") and "public" in out
              and entry_link(eid) == ("https://93.184.216.34/admin-video", ADMIN.id), out)
        r = await http.post("/api/entries", json={"project_id": pid, "kind": "link", "url": "http://10.1.2.3/x"},
                            headers=h(BOB_TOKEN))
        out = await tool(BOB, "save_knowledge_item", project_id=pid, kind="link", title="x", url="http://10.1.2.3/y")
        with db.get_db_connection() as c:
            leftover = c.execute("SELECT COUNT(*) FROM entries WHERE url LIKE 'http://10.1.2.3/%'").fetchone()[0]
        check("fetch: a member saving a new entry with such a link is refused (HTTP 400, tool error, no entry)",
              r.status == 400 and out.startswith("Error:") and leftover == 0, f"{r.status} {out} {leftover}")

        # A name only a lookup can judge is stored — and whoever presses Retry, it downloads restricted.
        spawned.clear()
        r = await http.put(f"/api/entries/{eid}", json={"url": "http://dana.dana.svc/x"},
                           headers=h(BOB_TOKEN))
        check("fetch: a member's link on the admin's entry records him as who set it",
              r.status == 200 and entry_link(eid) == ("http://dana.dana.svc/x", BOB.id),
              f"{r.status} {entry_link(eid)}")
        r = await http.post(f"/api/entries/{eid}/retry", headers=h(ADMIN_TOKEN))
        await settle()
        check("fetch: the admin's Retry of a link a member set is restricted (internal name refused, nothing run)",
              r.status == 200 and not spawned, f"{r.status} {spawned}")
        r = await http.put(f"/api/entries/{eid}", json={"url": "https://93.184.216.34/bob-video"}, headers=h(BOB_TOKEN))
        r2 = await http.post(f"/api/entries/{eid}/retry", headers=h(ADMIN_TOKEN))
        await settle()
        check("fetch: …a public link a member set downloads with site extractors only, even on the admin's Retry",
              r.status == 200 and r2.status == 200 and restricted(last_cmd())
              and last_cmd()[-1].endswith("bob-video"), last_cmd())

        # Editing something else (the web client sends the unchanged link back) never changes who set it.
        r = await http.put(f"/api/entries/{eid}", json={"title": "renamed", "url": "https://93.184.216.34/bob-video"},
                           headers=h(ADMIN_TOKEN))
        spawned.clear()
        await http.post(f"/api/entries/{eid}/retry", headers=h(ADMIN_TOKEN))
        await settle()
        check("fetch: the admin editing the title (same link sent back) leaves it Bob's link → still restricted",
              r.status == 200 and entry_link(eid)[1] == BOB.id and restricted(last_cmd()), f"{entry_link(eid)}")

        # The admin replaces the link: hers now, unrestricted; her undo puts Bob's back as Bob's.
        with undo.collect() as got:
            out = await tool(ADMIN, "update_knowledge_item", item_id=eid, url="https://93.184.216.34/admin-2")
        spawned.clear()
        await http.post(f"/api/entries/{eid}/retry", headers=h(ADMIN_TOKEN))
        await settle()
        check("fetch: a link the admin set herself downloads as before",
              entry_link(eid) == ("https://93.184.216.34/admin-2", ADMIN.id) and last_cmd()
              and not restricted(last_cmd()), f"{entry_link(eid)} {last_cmd()}")
        res = await run_undo(ADMIN, got[-1]["id"]) if got else ("no undo", "")
        spawned.clear()
        await http.post(f"/api/entries/{eid}/retry", headers=h(ADMIN_TOKEN))
        await settle()
        check("fetch: undoing her change puts Bob's link back as Bob's (restricted again)",
              res == ("done", "") and entry_link(eid) == ("https://93.184.216.34/bob-video", BOB.id)
              and restricted(last_cmd()), f"{res} {entry_link(eid)} {last_cmd()}")

        # A link that looks like a yt-dlp option is always passed after "--" (the admin may set anything).
        r = await http.put(f"/api/entries/{eid}", json={"url": "--exec=touch /tmp/pwned"}, headers=h(ADMIN_TOKEN))
        spawned.clear()
        await http.post(f"/api/entries/{eid}/retry", headers=h(ADMIN_TOKEN))
        await settle()
        check("fetch: a link is always passed after '--' (never read as an option)",
              r.status == 200 and last_cmd()[-2:] == ["--", "--exec=touch /tmp/pwned"], last_cmd())

        # The setter is unknown (removed user / hand-edited row): restricted.
        with db.get_db_connection() as c:
            c.execute("UPDATE entries SET url = 'https://93.184.216.34/v', url_set_by = 'gone-user' WHERE id = ?",
                      (eid,))
            c.commit()
        spawned.clear()
        await http.post(f"/api/entries/{eid}/retry", headers=h(ADMIN_TOKEN))
        await settle()
        check("fetch: a link whose setter is unknown downloads restricted", restricted(last_cmd()), last_cmd())


# --- 3. WAHA webhook ------------------------------------------------------------------------------

async def test_waha(http: TestClient, waha: FakeWAHA, evil: Recorder) -> None:
    store.update(ADMIN, "whatsapp", credentials={"url": waha.url, "api_key": "ADMIN-WAHA-KEY", "session": "default"},
                 account="972501111111@c.us")
    body = {"event": "message", "session": "default", "me": {"id": "972501111111@c.us"},
            "payload": {"id": "false_972502222222@c.us_ABC", "from": "972502222222@c.us", "fromMe": False,
                        "hasMedia": True, "media": {"mimetype": "audio/ogg", "url": f"//{evil.origin[7:]}/steal"}}}
    handled: List[Any] = []

    async def fake_handle(data):
        handled.append(data)

    with patch.object(whatsapp_api.whatsapp_events, "handle_webhook", new=fake_handle):
        r = await http.post("/webhook/waha", json=body, headers=h(BOB_TOKEN))
        await asyncio.sleep(0.05)
        check("webhook: a member's POST /webhook/waha is 403 and nothing is processed", r.status == 403 and not handled,
              r.status)
        r = await http.post("/webhook/waha", json=body, headers=h(ADMIN_TOKEN))
        await asyncio.sleep(0.05)
        check("webhook: a user with manage_server may post (testing)", r.status == 200 and len(handled) == 1, r.status)
        raw = json.dumps(body).encode()
        signed = {"Content-Type": "application/json", "X-Webhook-Hmac-Algorithm": "sha512",
                  "X-Webhook-Hmac": whatsapp_api.signature(WAHA_SECRET, raw)}
        r = await http.post("/webhook/waha", data=raw, headers={"Host": "dana.internal",
                                                                 "Content-Type": "application/json"})
        await asyncio.sleep(0.05)
        check("webhook: an unsigned POST is refused, whatever its Host (no trusted internal hosts)",
              r.status == 403 and len(handled) == 1, r.status)
        r = await http.post("/webhook/waha", data=raw, headers={**signed, "X-Webhook-Hmac": "0" * 128})
        await asyncio.sleep(0.05)
        check("webhook: a forged signature is refused", r.status == 403 and len(handled) == 1, r.status)
        r = await http.post("/webhook/waha", data=raw + b" ", headers=signed)
        await asyncio.sleep(0.05)
        check("webhook: a signature of another body is refused", r.status == 403 and len(handled) == 1, r.status)
        r = await http.post("/webhook/waha", data=raw, headers=signed)
        await asyncio.sleep(0.05)
        check("webhook: WAHA's valid signature (HMAC-SHA512 of the raw body) is accepted",
              r.status == 200 and len(handled) == 2 and handled[-1] == body, r.status)
        r = await http.post("/webhook/waha", data=raw, headers={**signed, **h(BOB_TOKEN)})
        await asyncio.sleep(0.05)
        check("webhook: a member's credentials are refused even with a valid signature",
              r.status == 403 and len(handled) == 2, r.status)
        with patch.dict(os.environ, {"WAHA_WEBHOOK_SECRET": ""}):
            r = await http.post("/webhook/waha", data=raw, headers=signed)
        await asyncio.sleep(0.05)
        check("webhook: without WAHA_WEBHOOK_SECRET every unauthenticated webhook is refused",
              r.status == 403 and len(handled) == 2, r.status)

    # Own pass: a member cannot connect a WAHA session another user already uses on that WAHA.
    r = await http.put("/api/addons/whatsapp", json={"credentials": {"url": waha.url, "session": "default"}},
                       headers=h(CARL_TOKEN))
    body = await r.json()
    check("waha: a member cannot connect the admin's WAHA session (400, field session)",
          r.status == 400 and body.get("field") == "session" and not store.is_connected(CARL, "whatsapp"), body)
    r = await http.put("/api/addons/whatsapp", json={"credentials": {"url": waha.url, "session": "carl"}},
                       headers=h(CARL_TOKEN))
    check("waha: …their own session on the same WAHA is fine",
          r.status == 201 and store.get(CARL, "whatsapp").credentials["session"] == "carl", r.status)
    r = await http.put("/api/addons/whatsapp", json={"credentials": {"session": "default"}},
                       headers=h(CARL_TOKEN))
    check("waha: …and switching to the admin's session later is refused too", r.status == 400 and
          store.get(CARL, "whatsapp").credentials["session"] == "carl", r.status)
    r = await http.put("/api/addons/whatsapp", json={"credentials": {"session": "../x?y"}}, headers=h(CARL_TOKEN))
    body = await r.json()
    check("waha: a session name that is not a plain name is refused",
          r.status == 400 and body.get("field") == "session"
          and store.get(CARL, "whatsapp").credentials["session"] == "carl", body)
    store.disconnect(CARL, "whatsapp")
    await asyncio.sleep(0.3)  # let the account lookup started by connecting finish

    conn = store.get(ADMIN, "whatsapp")
    waha.calls()
    for media_url in (f"//{evil.origin[7:]}/api/files/steal.oga", f"{evil.origin}/api/files/steal.oga",
                      "http://evil.example/api/files/steal.oga"):
        data = await wa_client.download_media_bytes(conn, media_url)
        got = waha.calls()
        check(f"media: {media_url!r} is fetched from the connection's own WAHA only",
              not evil.hits and got and got[0][1] == "/api/files/steal.oga" and got[0][2] == "ADMIN-WAHA-KEY",
              f"{evil.hits} {got}")
    for media_url in ("javascript:alert(1)", "steal", "ftp://x/steal", ""):
        data = await wa_client.download_media_bytes(conn, media_url)
        check(f"media: {media_url!r} refused (nothing fetched)", data is None and not waha.calls() and not evil.hits)
    data = await wa_client.download_media_bytes(conn, f"{waha.root}/api/files/voice.oga?x=1")
    check("media: a normal WAHA file URL still downloads", data is not None and waha.calls()[0][1] == "/api/files/voice.oga")


# --- 3b. One connection per WhatsApp account -----------------------------------------------------

ADMIN_ACCOUNT = "972501111111@c.us"


async def until(cond, seconds: float = 3.0) -> bool:
    for _ in range(int(seconds / 0.05)):
        if cond():
            return True
        await asyncio.sleep(0.05)
    return bool(cond())


def load_cli():
    spec = importlib.util.spec_from_file_location("users_cli", testenv.SERVER / "bin" / "users.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


async def test_waha_accounts(http: TestClient, waha: FakeWAHA, bobs: FakeWAHA) -> None:
    """Bob's own WAHA (allowed by MEMBER_ALLOWED_ORIGINS) reports the admin's WhatsApp account."""
    store.update(ADMIN, "whatsapp", credentials={"url": waha.url, "api_key": "ADMIN-WAHA-KEY", "session": "default"})
    await until(lambda: store.require(ADMIN, "whatsapp").account == ADMIN_ACCOUNT)
    check("accounts: the admin's connection holds her account", store.require(ADMIN, "whatsapp").account == ADMIN_ACCOUNT)
    os.environ["MEMBER_ALLOWED_ORIGINS"] = f"whatsapp={bobs.root}, whatsapp=http://localhost:{waha.root.rsplit(':', 1)[1]}"
    records: List[logging.LogRecord] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)
    keep = Keep(level=logging.WARNING)
    logging.getLogger(wa_accounts.__name__).addHandler(keep)
    try:
        r = await http.put("/api/addons/whatsapp", json={"credentials": {"url": bobs.url, "api_key": "BOB-KEY",
                                                                               "session": "bob"}}, headers=h(BOB_TOKEN))
        await until(lambda: bool(store.require(BOB, "whatsapp").account_conflict))
    finally:
        logging.getLogger(wa_accounts.__name__).removeHandler(keep)
    bob = store.require(BOB, "whatsapp")
    check("accounts: Bob's connection does not get an account another user holds (kept as a conflict)",
          r.status == 201 and not bob.account and bob.account_conflict == ADMIN_ACCOUNT,
          f"{r.status} {bob.account} {bob.account_conflict}")
    check("accounts: the admin keeps hers", store.require(ADMIN, "whatsapp").account == ADMIN_ACCOUNT)
    logged = [rec.getMessage() for rec in records]
    check("accounts: the conflict is logged with the user ids (no message content)",
          any(BOB.id in m and ADMIN.id in m and "already connected" in m for m in logged), logged)

    event = {"event": "message", "session": "default", "me": {"id": ADMIN_ACCOUNT},
             "payload": {"id": "false_972599999999@c.us_ABC", "from": "972599999999@c.us", "fromMe": False}}
    routed = await wa_accounts.route(event)
    check("accounts: an event for the admin's account routes to her connection only",
          routed is not None and routed.user.id == ADMIN.id, routed and routed.user.id)
    seen: List[str] = []

    async def fake_process(conn, data):
        seen.append(conn.user.id)
    with patch.object(whatsapp_api.whatsapp_events, "process_event", new=fake_process):
        r = await http.post("/webhook/waha", **signed_by_waha(event))
        await asyncio.sleep(0.1)
        r2 = await http.post("/webhook/waha", **signed_by_waha({**event, "me": None, "session": "bob"}))
        await asyncio.sleep(0.1)
    check("accounts: through the webhook, processed as the admin only (never also as Bob)",
          r.status == 200 and seen == [ADMIN.id], seen)
    check("accounts: an event without 'me' for the conflicted connection's session is dropped",
          r2.status == 200 and seen == [ADMIN.id], seen)

    bobs.calls()
    out = await tool(BOB, "find_last_chats")
    out2 = await tool(BOB, "send_whatsapp_message", chat_id="972599999999@c.us", text="hi")
    check("accounts: Bob's WhatsApp tools refuse with a clear message and reach no WAHA",
          out == f"Error: {wa_accounts.TAKEN}" and out2 == f"Error: {wa_accounts.TAKEN}" and not bobs.calls()
          and "already connected by another user" in wa_accounts.TAKEN.lower(), f"{out} | {out2}")
    check("accounts: the admin's tools are unaffected", not str(await tool(ADMIN, "find_last_chats")).startswith(
        f"Error: {wa_accounts.TAKEN}"))

    r = await http.get(f"/api/addons/whatsapp?user={BOB.id}", headers=h(ADMIN_TOKEN))
    body = await r.json()
    r_admin = await http.get("/api/addons/whatsapp", headers=h(ADMIN_TOKEN))
    admin_body = await r_admin.json()
    check("accounts: an admin sees the conflict in /api/addons for Bob",
          r.status == 200 and body.get("account") is None and body.get("account_conflict") == ADMIN_ACCOUNT, body)
    check("accounts: the admin's own payload has no conflict field (unchanged shape)",
          "account_conflict" not in admin_body and admin_body.get("account") == ADMIN_ACCOUNT, admin_body)
    buf = io.StringIO()
    code = load_cli().main(["addons", "show", BOB.id], stdout=buf, stderr=io.StringIO())
    check("accounts: bin/users.py addons show names the conflict",
          code == 0 and "account conflict: " + ADMIN_ACCOUNT in buf.getvalue(), buf.getvalue())

    # Bob's WAHA reports another account: the conflict clears and the connection is used.
    bobs.sessions["bob"] = {"id": "972503333333@c.us"}
    await wa_accounts.refresh_missing(force=True)
    bob = store.require(BOB, "whatsapp")
    routed = await wa_accounts.route({**event, "me": {"id": "972503333333@c.us"}})
    check("accounts: once his WAHA reports his own account, Bob's connection holds it and is routed to",
          bob.account == "972503333333@c.us" and not bob.account_conflict and routed is not None
          and routed.user.id == BOB.id, f"{bob.account} {bob.account_conflict}")
    check("accounts: …and his tools run again",
          not str(await tool(BOB, "find_last_chats")).startswith(f"Error: {wa_accounts.TAKEN}"))
    store.disconnect(BOB, "whatsapp")

    # The admin's WAHA session under a second hostname: the session check compares addresses, the account does not.
    second = f"http://localhost:{waha.root.rsplit(':', 1)[1]}/api"
    r = await http.put("/api/addons/whatsapp", json={"credentials": {"url": second, "api_key": "ADMIN-WAHA-KEY",
                                                                           "session": "default"}},
                       headers=h(CARL_TOKEN))
    await until(lambda: store.is_connected(CARL, "whatsapp")
                and bool(store.require(CARL, "whatsapp").account_conflict))
    carl = store.get(CARL, "whatsapp")
    out = await tool(CARL, "find_last_chats") if carl else ""
    check("accounts: the admin's session reached under another hostname gets no account and its tools refuse",
          carl is not None and not carl.account and carl.account_conflict == ADMIN_ACCOUNT
          and out == f"Error: {wa_accounts.TAKEN}", f"{r.status} {carl and (carl.account, carl.account_conflict)}")
    routed = await wa_accounts.route(event)
    check("accounts: …and events still go to the admin only", routed is not None and routed.user.id == ADMIN.id)
    if carl is not None:
        store.disconnect(CARL, "whatsapp")
    del os.environ["MEMBER_ALLOWED_ORIGINS"]

    # Two claims of one new account at the same moment: exactly one wins.
    for u in (BOB, CARL):
        store.connect(u, "whatsapp", credentials={"url": bobs.url, "session": u.name.split()[0].lower()})
    await asyncio.sleep(0.2)
    barrier = threading.Barrier(2)
    won: Dict[str, Any] = {}

    def claim(u) -> None:
        barrier.wait()
        won[u.id] = store.claim_account(u, "whatsapp", "972504444444:3@s.whatsapp.net", wa_accounts.account_key)
    threads = [threading.Thread(target=claim, args=(u,)) for u in (BOB, CARL)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    holders = [u for u in (BOB, CARL) if store.require(u, "whatsapp").account]
    check("accounts: two claims at the same moment → one holder, the other a conflict naming that holder",
          len(holders) == 1 and sorted(v is None for v in won.values()) == [False, True]
          and won[next(u.id for u in (BOB, CARL) if u not in holders)] == holders[0].id, f"{won} {holders}")
    for u in (BOB, CARL):
        store.disconnect(u, "whatsapp")


# --- 4. Telegram chat settings --------------------------------------------------------------------

async def test_telegram_chats(http: TestClient) -> None:
    store.connect(BOB, "whatsapp", credentials={"url": store.get(ADMIN, "whatsapp").credentials["url"],
                                                "session": "bob"})
    r = await http.put("/api/addons/whatsapp", json={"settings": {"voice_telegram_chat_id": 111}},
                       headers=h(BOB_TOKEN))
    check("chat: a member's voice-transcript chat cannot be someone else's",
          r.status == 400 and store.get(BOB, "whatsapp").settings.get("voice_telegram_chat_id") is None, r.status)
    r = await http.put("/api/addons/whatsapp", json={"settings": {"voice_telegram_chat_id": -100555}},
                       headers=h(BOB_TOKEN))
    check("chat: …nor a group",
          r.status == 400 and store.get(BOB, "whatsapp").settings.get("voice_telegram_chat_id") is None, r.status)
    r = await http.put("/api/addons/whatsapp", json={"settings": {"voice_telegram_chat_id": 222}},
                       headers=h(BOB_TOKEN))
    check("chat: a member's own private chat is accepted", r.status == 200 and
          store.get(BOB, "whatsapp").settings["voice_telegram_chat_id"] == 222, r.status)
    store.disconnect(BOB, "whatsapp")
    check("chat: shopping has no Telegram setting any more",
          [f.name for f in store.get_addon("shopping").settings] == ["active_list"]
          and "telegram_chat_id" not in store.get(ADMIN, "shopping").settings)


# --- 4c. What a client is told it may change is what the store accepts ----------------------------

LOCKED_TRIES = {"address": "http://somewhere.example:8123", "telegram_chat": 222, "name": "x", "text": "x"}


async def test_change_rules(http: TestClient) -> None:
    """Every field / addon the addon payload marks ``can_change: false`` for the caller is refused
    when the caller tries it anyway (nothing stored), for a member on their own and for an admin
    acting for them; an address is shown to its user and an admin, never to another member."""
    for label, token, query in (("a member", BOB_TOKEN, ""), ("an admin for the member", ADMIN_TOKEN,
                                                              f"?user={BOB.id}"),
                                ("the admin", ADMIN_TOKEN, "")):
        r = await http.get(f"/api/addons{query}", headers=h(token))
        listing = (await r.json())["addons"]
        locked, leaked = [], []
        for item in listing:
            addon = item["name"]
            before = json.dumps(store.list_connections(BOB if query or token == BOB_TOKEN else ADMIN), default=str)
            if not item["can_change"]:
                locked.append(addon)
                r1 = await http.put(f"/api/addons/{addon}{query}", json={}, headers=h(token))
                r2 = await http.delete(f"/api/addons/{addon}{query}", headers=h(token))
                if r1.status != 403 or r2.status != 403:
                    leaked.append((addon, r1.status, r2.status))
                continue
            for group in ("credentials", "settings"):
                for f in item[group]:
                    if f["can_change"]:
                        continue
                    locked.append(f"{addon}.{f['name']}")
                    r = await http.put(f"/api/addons/{addon}{query}",
                                       json={group: {f["name"]: LOCKED_TRIES[f["type"]]}}, headers=h(token))
                    body = await r.json()
                    if r.status not in (400, 403) or (r.status == 400 and body.get("field") != f["name"]):
                        leaked.append((addon, f["name"], r.status, body))
            after = json.dumps(store.list_connections(BOB if query or token == BOB_TOKEN else ADMIN), default=str)
            if before != after:
                leaked.append((addon, "stored something"))
        check(f"rules: every change {label} is told is not theirs is refused, nothing stored",
              not leaked, leaked)
        if token == BOB_TOKEN:
            check("rules: a member is told cameras are not theirs", "cameras" in locked, locked)
        elif not query:
            check("rules: the admin is told nothing of their own is locked", not locked, locked)

    conn = store.require(BOB, "homeassistant")  # connected in section 2
    bob_host, bob_token = conn.credentials["host"], conn.credentials["token"]
    own = await (await http.get("/api/addons/homeassistant", headers=h(BOB_TOKEN))).json()
    admin = await (await http.get(f"/api/addons/homeassistant?user={BOB.id}", headers=h(ADMIN_TOKEN))).json()
    r = await http.get(f"/api/addons/homeassistant?user={BOB.id}", headers=h(CARL_TOKEN))
    other = await r.text()
    carl_own = await (await http.get("/api/addons", headers=h(CARL_TOKEN))).text()
    host = lambda p: next(c for c in p["credentials"] if c["name"] == "host")  # noqa: E731
    check("address: shown to its user and to an admin acting for them; the token never",
          host(own).get("value") == host(admin).get("value") == bob_host
          and all("value" not in c for c in own["credentials"] if c["name"] == "token")
          and bob_token not in json.dumps(own) + json.dumps(admin), (host(own), host(admin)))
    carl_conn = store.get(CARL, "homeassistant")
    check("address: another member gets 403 without the address", r.status == 403 and bob_host not in other, other)
    check("address: …and their own list does not carry it (unless it is their own address too)",
          bob_host not in carl_own or (carl_conn is not None and carl_conn.credentials.get("host") == bob_host))


# --- 5. The shopping list: active list, invitations, moving items, the stream, undo ----------------

def shop_names(list_id: str, checked: Any = None) -> List[str]:
    with db.get_db_connection() as c:
        rows = c.execute("SELECT name, checked FROM shopping_items WHERE list_id = ? ORDER BY seq", (list_id,)).fetchall()
    return [r["name"] for r in rows if checked is None or bool(r["checked"]) == checked]


def shop_rows(list_id: str) -> List[Tuple[Any, ...]]:
    with db.get_db_connection() as c:
        return [tuple(r) for r in c.execute("SELECT id, name, quantity, note, checked, rev FROM shopping_items "
                                            "WHERE list_id = ? ORDER BY id", (list_id,))]


async def sse_event(resp, timeout: float = 2.0) -> Tuple[str, Any]:
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


async def test_active_list(http: TestClient) -> None:
    own = shopping_lists.own_list_id(ADMIN)
    bobs = shopping_lists.own_list_id(BOB)
    sharing.set_level(BOB, shopping_lists.ITEM_TYPE, bobs, ADMIN.id, "edit")
    check("active: an edit share does not change the recipient's active list",
          shopping_lists.active_list(ADMIN).id == own)
    await tool(ADMIN, "add_shopping_items", items=["admin milk"])
    check("active: the recipient's tools keep writing to their own list", "admin milk" not in shop_names(bobs),
          shop_names(bobs))
    r = await http.get("/api/shopping/lists", headers=h(ADMIN_TOKEN))
    body = await r.json()
    check("active: the shared list shows, not active", body.get("active") == own and
          any(x["id"] == bobs and not x["active"] for x in body.get("lists", [])), body)
    r = await http.put("/api/shopping/active", json={"list_id": bobs}, headers=h(ADMIN_TOKEN))
    body = await r.json()
    check("active: PUT /api/shopping/active switches to a list shared with edit", r.status == 200 and
          body["list"]["id"] == bobs and shopping_lists.active_list(ADMIN).id == bobs, body.get("list"))
    out = await tool(ADMIN, "add_shopping_items", items=["chosen eggs"])
    check("active: after choosing, the tools write to the chosen list", "chosen eggs" in shop_names(bobs), out)
    sharing.set_level(BOB, shopping_lists.ITEM_TYPE, bobs, ADMIN.id, "read")
    check("active: downgraded to read → back on the own list", shopping_lists.active_list(ADMIN).id == own)
    check("active: …and the choice is forgotten",
          store.get(ADMIN, "shopping").settings.get("active_list") in (None, ""))
    r = await http.put("/api/shopping/active", json={"list_id": bobs}, headers=h(ADMIN_TOKEN))
    check("active: a read share cannot be chosen", r.status == 403 and shopping_lists.active_list(ADMIN).id == own,
          r.status)
    carls = shopping_lists.own_list_id(CARL)
    r = await http.put("/api/shopping/active", json={"list_id": carls}, headers=h(ADMIN_TOKEN))
    check("active: a list not shared with the user answers 404",
          r.status == 404 and shopping_lists.active_list(ADMIN).id == own, r.status)
    r = await http.put("/api/shopping/active", json={"list_id": own}, headers=h(ADMIN_TOKEN))
    check("active: choosing the own list works", r.status == 200 and shopping_lists.active_list(ADMIN).id == own, r.status)
    # The chat tool: only with more than one user; lists and chooses.
    sharing.set_level(BOB, shopping_lists.ITEM_TYPE, bobs, ADMIN.id, "edit")
    names = [t["function"]["name"] for t in registry.tool_schemas(ADMIN)]
    check("active: the choose tool is offered on a multi-user server", "choose_shopping_list" in names)
    share_doc = next((t["function"]["description"] for t in registry.tool_schemas(ADMIN)
                      if t["function"]["name"] == "share_item"), "")
    check("active: share_item's description says sharing a list is an invitation the recipient chooses to use",
          "choose_shopping_list" in share_doc and "invitation" in share_doc and "never switches" in share_doc,
          share_doc[-300:])
    out = await tool(ADMIN, "choose_shopping_list")
    check("active: the tool lists the lists and which one is active", "Bob" in out and "active" in out.lower(), out)
    out = await tool(ADMIN, "choose_shopping_list", list_name="Bob")
    check("active: the tool switches to a shared list", shopping_lists.active_list(ADMIN).id == bobs, out)
    sharing.set_level(BOB, shopping_lists.ITEM_TYPE, bobs, ADMIN.id, "none")
    check("active: unshared → back on the own list", shopping_lists.active_list(ADMIN).id == own)
    r = await http.put("/api/shopping/active", json={"list_id": "nope"}, headers=h(BOB_TOKEN))
    check("active: an unknown list id → 404", r.status == 404, r.status)

    # An invitation answered for a list that is not shared (any more) answers like an unknown list.
    for action in ("use", "dismiss"):
        r1 = await http.post(f"/api/shopping/invites/{bobs}", json={"action": action, "move_items": True},
                             headers=h(ADMIN_TOKEN))
        r2 = await http.post("/api/shopping/invites/no-such-list", json={"action": action, "move_items": True},
                             headers=h(ADMIN_TOKEN))
        check(f"invite: {action} on an unshared list → 404 exactly like an unknown list",
              r1.status == r2.status == 404 and await r1.json() == await r2.json()
              and shopping_lists.active_list(ADMIN).id == own)

    # move_items moves only the caller's own items to buy; nobody else's.
    await tool(CARL, "add_shopping_items", items=["carl bread", "carl jam"])
    sharing.set_level(CARL, shopping_lists.ITEM_TYPE, shopping_lists.own_list_id(CARL), BOB.id, "edit")
    await tool(BOB, "add_shopping_items", items=["bob soap"])
    sharing.set_level(BOB, shopping_lists.ITEM_TYPE, bobs, ADMIN.id, "edit")
    with users.acting_as(ADMIN):
        await registry.execute(ADMIN, "add_shopping_items", {"items": ["admin rice"]})
    carl_before = shop_rows(carls)
    bob_items_before = [n for n in shop_names(bobs) if not n.startswith(("admin", "chosen"))]
    r = await http.post(f"/api/shopping/invites/{bobs}", json={"action": "use", "move_items": True},
                        headers=h(ADMIN_TOKEN))
    check("move_items: the admin's own items to buy moved to Bob's list", r.status == 200
          and "admin rice" in shop_names(bobs) and "admin rice" not in shop_names(own), shop_names(bobs))
    check("move_items: Carl's list (shared with Bob, not with the admin) untouched", shop_rows(carls) == carl_before)
    check("move_items: Bob's own items untouched",
          [n for n in shop_names(bobs) if not n.startswith(("admin", "chosen"))] == bob_items_before)
    undo_id = (await r.json())["undo"]["id"]

    # The stream never delivers another list's events.
    stream = await http.get("/api/shopping/events", headers=h(CARL_TOKEN))
    first = await sse_event(stream)
    await tool(BOB, "add_shopping_items", items=["bob tea"])
    await tool(ADMIN, "add_shopping_items", items=["admin plums"])
    try:
        leaked = await sse_event(stream, 0.5)
    except asyncio.TimeoutError:
        leaked = None
    check("stream: Carl's stream never carries Bob's list's changes", first[1]["list_id"] == carls and leaked is None,
          leaked)
    stream.close()
    stream = await http.get(f"/api/shopping/events?list={bobs}", headers=h(CARL_TOKEN))
    check("stream: ?list=<a list not shared with him> → 404", stream.status == 404, stream.status)

    # Undo after unsharing is refused, and leaves the list as it is.
    sharing.set_level(BOB, shopping_lists.ITEM_TYPE, bobs, ADMIN.id, "none")
    before = shop_rows(bobs)
    res = await run_undo(ADMIN, undo_id)
    check("undo: the admin's 'use and move' undo after Bob unshared is refused, nothing changed",
          res == ("refused", "You can no longer change that shopping list.") and shop_rows(bobs) == before, res)
    sharing.set_level(CARL, shopping_lists.ITEM_TYPE, carls, BOB.id, "none")


# --- 6. Undo re-checks access ---------------------------------------------------------------------

async def run_undo(user, undo_id: str) -> Tuple[str, str]:
    """Press Undo as ``user``: ``("done", "")``, ``("refused", message)`` for an
    UndoConflict, ``("missing", "")`` when the id is unknown to them (not a
    refusal), ``("error", repr)`` for anything else."""
    with users.acting_as(user):
        try:
            await undo.run(undo_id)
            return "done", ""
        except KeyError:
            return "missing", ""
        except undoable.UndoConflict as e:
            return "refused", str(e)
        except Exception as e:  # noqa: BLE001 — reported as a failed check, not a refusal
            return "error", repr(e)


def task_row(task_id: str) -> Dict[str, Any]:
    """The owner / list / title / status of a task straight from the table (None: gone)."""
    with db.get_db_connection() as c:
        r = c.execute("SELECT user_id, group_id, title, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(r) if r else None


def refused_no_longer(res: Tuple[str, str]) -> bool:
    return res == ("refused", tasks_service.NO_LONGER)


async def test_undo_access() -> None:
    gid = task_groups.create_group(BOB, "Chores")
    sharing.set_level(BOB, task_groups.ITEM_TYPE, gid, ADMIN.id, "edit")
    t1 = tasks_service.create_task(BOB, title="Bob's chore", group_id=gid)
    res = await now.act(ADMIN, f"task:{t1}", "done")
    sharing.set_level(BOB, task_groups.ITEM_TYPE, gid, ADMIN.id, "none")
    out = await run_undo(ADMIN, res["undo_id"])
    check("undo: Now's undo of a task on a list no longer shared is refused (the task stays done, Bob's)",
          refused_no_longer(out) and task_row(t1) == {"user_id": BOB.id, "group_id": gid, "title": "Bob's chore",
                                                      "status": "completed"}, f"{out} {task_row(t1)}")
    check("undo: …and a refused undo is used up (pressing it again: unknown id, not a second refusal)",
          await run_undo(ADMIN, res["undo_id"]) == ("missing", ""))

    sharing.set_level(BOB, task_groups.ITEM_TYPE, gid, ADMIN.id, "edit")
    t2 = tasks_service.create_task(BOB, title="Bob's other chore", group_id=gid)
    with undo.collect() as got:
        await tool(ADMIN, "update_task", item=t2, summary="renamed by admin")
    sharing.set_level(BOB, task_groups.ITEM_TYPE, gid, ADMIN.id, "none")
    out = await run_undo(ADMIN, got[-1]["id"])
    check("undo: update_task's undo on a list no longer shared is refused",
          refused_no_longer(out) and task_row(t2)["title"] == "renamed by admin", f"{out} {task_row(t2)}")

    sharing.set_level(BOB, task_groups.ITEM_TYPE, gid, ADMIN.id, "edit")
    with undo.collect() as got:
        await tool(ADMIN, "add_task", title="added by admin", group="Chores")
    added = [t for t in tasks_service.list_tasks(BOB) if t["title"] == "added by admin"]
    sharing.set_level(BOB, task_groups.ITEM_TYPE, gid, ADMIN.id, "none")
    out = await run_undo(ADMIN, got[-1]["id"])
    check("undo: add_task's undo on a list no longer shared is refused (the task stays, Bob's)",
          refused_no_longer(out) and added and task_row(added[0]["id"]) is not None
          and task_row(added[0]["id"])["user_id"] == BOB.id, f"{out} {added}")

    # A move between lists: the owner follows the list, both ways, and both lists are checked.
    home = task_groups.create_group(ADMIN, "Admin home")
    bob_general = task_groups.default_group_id(BOB)
    sharing.set_level(ADMIN, task_groups.ITEM_TYPE, home, BOB.id, "edit")

    async def bob_moves(title: str) -> Tuple[str, str]:
        task_id = tasks_service.create_task(ADMIN, title=title, group_id=home)
        with undo.collect() as got:
            out = await tool(BOB, "update_task", item=task_id, group="General")
        moved = task_row(task_id)
        check(f"move: Bob moves the admin's “{title}” to his own list → it is Bob's there",
              moved["group_id"] == bob_general and moved["user_id"] == BOB.id and got, f"{out} {moved}")
        return task_id, got[-1]["id"] if got else ""

    t3, u3 = await bob_moves("Admin task A")
    out = await run_undo(BOB, u3)
    row = task_row(t3)
    check("move undo: the task goes back to the admin's list and to the admin (not a task of Bob's in her list)",
          out == ("done", "") and row["group_id"] == home and row["user_id"] == ADMIN.id, f"{out} {row}")
    check("move undo: the admin sees it as her own again",
          any(t["id"] == t3 and "shared_by" not in t for t in tasks_service.list_tasks(ADMIN))
          and tasks_service.level_of(ADMIN, tasks_service.get_task(ADMIN, t3)) == sharing.OWNER)
    sharing.set_level(ADMIN, task_groups.ITEM_TYPE, home, BOB.id, "none")
    check("move undo: after unsharing, Bob no longer sees it (no owner access left behind)",
          tasks_service.get_task(BOB, t3) is None)

    sharing.set_level(ADMIN, task_groups.ITEM_TYPE, home, BOB.id, "edit")
    t4, u4 = await bob_moves("Admin task B")
    sharing.set_level(ADMIN, task_groups.ITEM_TYPE, home, BOB.id, "none")
    out = await run_undo(BOB, u4)
    row = task_row(t4)
    check("move undo: refused when the list it would go back to is no longer shared (the task stays Bob's)",
          refused_no_longer(out) and row["group_id"] == bob_general and row["user_id"] == BOB.id, f"{out} {row}")
    check("move undo: …so nothing invisible is left in the admin's list",
          all(t["id"] != t4 for t in tasks_service.list_tasks(ADMIN)) and tasks_service.get_task(BOB, t4) is not None)

    sharing.set_level(ADMIN, task_groups.ITEM_TYPE, home, BOB.id, "edit")
    t5, u5 = await bob_moves("Admin task C")
    sharing.set_level(ADMIN, task_groups.ITEM_TYPE, home, BOB.id, "read")
    out = await run_undo(BOB, u5)
    row = task_row(t5)
    check("move undo: refused when the list it would go back to is now read-only for him",
          refused_no_longer(out) and row["group_id"] == bob_general and row["user_id"] == BOB.id, f"{out} {row}")

    # The other direction: the admin moves Bob's task out of his shared list into her own; Bob unshares
    # his list; the admin's undo must not put it back (she cannot write there any more).
    sharing.set_level(BOB, task_groups.ITEM_TYPE, gid, ADMIN.id, "edit")
    t6 = tasks_service.create_task(BOB, title="Bob task D", group_id=gid)
    with undo.collect() as got:
        await tool(ADMIN, "update_task", item=t6, group="Admin home")
    moved = task_row(t6)
    sharing.set_level(BOB, task_groups.ITEM_TYPE, gid, ADMIN.id, "none")
    out = await run_undo(ADMIN, got[-1]["id"])
    row = task_row(t6)
    check("move undo: the admin cannot put a task back into Bob's list once he unshared it",
          moved["user_id"] == ADMIN.id and refused_no_longer(out) and row == moved, f"{moved} {out} {row}")

    # A plain edit undone after someone else moved the task: refused as changed, the move kept.
    sharing.set_level(ADMIN, task_groups.ITEM_TYPE, home, BOB.id, "edit")
    t7 = tasks_service.create_task(ADMIN, title="Admin task E", group_id=home)
    with undo.collect() as got:
        await tool(BOB, "update_task", item=t7, summary="Admin task E (Bob)")
    tasks_service.save_task(ADMIN, t7, {"group_id": task_groups.default_group_id(ADMIN)})
    out = await run_undo(BOB, got[-1]["id"])
    check("undo: a task moved since is not changed (Bob cannot see it any more → refused)",
          out[0] == "refused" and task_row(t7)["title"] == "Admin task E (Bob)", f"{out} {task_row(t7)}")


async def test_task_moves_http(http: TestClient) -> None:
    """Moves outside the tool follow the same rule (save_task): HTTP PUT /api/tasks/{id}."""
    home = task_groups.create_group(ADMIN, "Admin errands")
    sharing.set_level(ADMIN, task_groups.ITEM_TYPE, home, BOB.id, "read")
    t = tasks_service.create_task(BOB, title="Bob's own errand")
    r = await http.put(f"/api/tasks/{t}", json={"group_id": home}, headers=h(BOB_TOKEN))
    check("move (HTTP): into a list shared read-only → 403, nothing moved",
          r.status == 403 and task_row(t)["user_id"] == BOB.id
          and task_row(t)["group_id"] == task_groups.default_group_id(BOB), f"{r.status} {task_row(t)}")
    sharing.set_level(ADMIN, task_groups.ITEM_TYPE, home, BOB.id, "edit")
    r = await http.put(f"/api/tasks/{t}", json={"group_id": home}, headers=h(BOB_TOKEN))
    check("move (HTTP): into a list shared to edit → the task belongs to the list's owner",
          r.status == 200 and task_row(t) == {"user_id": ADMIN.id, "group_id": home, "title": "Bob's own errand",
                                              "status": "needs_action"}, f"{r.status} {task_row(t)}")
    carls = task_groups.default_group_id(CARL)
    r = await http.put(f"/api/tasks/{t}", json={"group_id": carls}, headers=h(BOB_TOKEN))
    check("move (HTTP): into a list he cannot see → 404, nothing moved",
          r.status == 404 and task_row(t)["group_id"] == home, f"{r.status} {task_row(t)}")


# --- 7. Device registration -----------------------------------------------------------------------

async def test_devices(http: TestClient) -> None:
    push.register(ADMIN, "dev-1", "token-admin", "Admin phone")
    r = await http.post("/api/phone/register", json={"device_id": "dev-1", "token": "token-bob", "name": "Bob phone"},
                        headers=h(BOB_TOKEN))
    check("devices: another user registering the same device id gets his own row",
          r.status == 200 and [d["device_id"] for d in push.devices(ADMIN)] == ["dev-1"]
          and [d["device_id"] for d in push.devices(BOB)] == ["dev-1"], r.status)
    with db.get_db_connection() as c:
        admin_token = c.execute("SELECT token FROM phone_devices WHERE user_id = ? AND device_id = 'dev-1'",
                                (ADMIN.id,)).fetchone()
    check("devices: the admin's row keeps the admin's token", admin_token and admin_token[0] == "token-admin")
    push.register(ADMIN, "dev-1", "token-admin-2", "Admin phone")
    check("devices: the owner's re-registration with the same device id keeps working",
          len(push.devices(ADMIN)) == 1 and len(push.devices(BOB)) == 1)
    push.register(CARL, "dev-1", "token-admin-2", "Carl took the phone")
    check("devices: with the stored token (the same phone), the device moves",
          not push.devices(ADMIN) and len(push.devices(CARL)) == 1 and len(push.devices(BOB)) == 1)


# --- 8. Route modules -----------------------------------------------------------------------------

ROUTE_MODULES = tuple(module.__name__ for module in server.route_modules())


def test_route_modules() -> None:
    """A route module (core's or an official addon's api.py) that cannot be imported makes the server's
    startup fail (it is never skipped silently): each one blocked in a fresh interpreter; the unblocked import as the control."""
    def boot(blocked: str) -> subprocess.Popen:
        code = ("import sys, testenv; testenv.isolate('routes_boot'); "
                + (f"sys.modules[{blocked!r}] = None; " if blocked else "")
                + "import core.server; core.server.route_modules(); print('BOOTED')")
        return subprocess.Popen([sys.executable, "-c", code], cwd=str(testenv.SERVER),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    procs = {m: boot(m) for m in ("", *ROUTE_MODULES)}
    outs: Dict[str, Tuple[int, str, str]] = {}
    for module, proc in procs.items():
        out, err = proc.communicate(timeout=180)
        outs[module] = (proc.returncode, out, err)
    rc, out, err = outs.pop("")
    check("routes: the server module imports cleanly (control)", rc == 0 and "BOOTED" in out, err[-500:])
    for module, (rc, out, err) in outs.items():
        check(f"routes: startup fails when {module} cannot be imported",
              rc != 0 and "BOOTED" not in out and module in err, f"rc={rc} {err[-300:]}")


# --- 9. Telegram multi-select ---------------------------------------------------------------------

async def test_selection_toggles() -> None:
    bot = TelegramBot.__new__(TelegramBot)
    bot.bot = AsyncMock()
    bot.selection_toggles = {}
    bot.selection_messages = {}
    bot.user_memory_states = {}
    bot._handle_agent_result = AsyncMock()
    bot.get_keyboard = lambda: None
    chat = -600
    opts = [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}, {"label": "C", "value": "c"}]
    sel = {"status": "awaiting_selection", "options": opts, "multiple": True, "chat_id": chat, "prompt": "pick"}
    sent_ids = iter([71, 72])

    async def send_message(**kw):
        return SimpleNamespace(message_id=next(sent_ids))
    bot.bot.send_message = send_message
    for u in (ADMIN, BOB):
        session.set_pending(u.id, f"tg_{chat}", sel)
    await bot._send_selection_prompt(chat, sel, ADMIN)
    await bot._send_selection_prompt(chat, sel, BOB)
    msg = {mid: types.Message(message_id=mid, date=datetime.datetime.now(datetime.timezone.utc),
                              chat=types.Chat(id=chat, type="group")) for mid in (71, 72)}
    started = []

    def fake_resume(user, session_id, selection):
        started.append((user.id, session_id, list(selection)))
        done = asyncio.get_running_loop().create_future()
        done.set_result(("ok", []))
        return SimpleNamespace(task=done)

    async def press(telegram_id: int, mid: int, data: str):
        cb = SimpleNamespace(from_user=SimpleNamespace(id=telegram_id), data=data, answer=AsyncMock(), message=msg[mid])
        await AuthMiddleware()(lambda event, d: bot.handle_approval_callback(event, d["user"]), cb, {})
        return cb

    with patch.object(types.Message, "edit_text", new=AsyncMock()), \
            patch.object(types.Message, "edit_reply_markup", new=AsyncMock()), \
            patch.object(tb, "start_resume_selection_task", new=fake_resume):
        await press(111, 71, "sel_toggle:0")
        await press(222, 72, "sel_toggle:1")
        cb = await press(222, 71, "sel_toggle:2")
        check("selection: another user's press on someone's prompt gets an alert",
              cb.answer.await_args.kwargs.get("show_alert") is True, str(cb.answer.await_args))
        await press(111, 71, "sel_done")
        await press(222, 72, "sel_done")
    check("selection: each user's multi-select is their own (no mixing)",
          started == [(ADMIN.id, f"tg_{chat}", [0]), (BOB.id, f"tg_{chat}", [1])], started)


async def main() -> None:
    frigate, internal, evil = Recorder(b"frigate"), Recorder(b"INTERNAL"), Recorder(b"evil")
    for s in (frigate, internal, evil):
        await s.start()
    ha_a = FakeHA("a", "tok-a", {"camera.front": ("idle", {})}, {}, "UTC")
    ha_b = FakeHA("b", "tok-b", {}, {}, "UTC")
    waha = FakeWAHA("w", "ADMIN-WAHA-KEY", {"default": {"id": "972501111111@c.us"}}, ("1@c.us", "X"), "chat")
    bobs = FakeWAHA("bob", "BOB-KEY", {"bob": {"id": "972501111111@c.us"}}, ("1@c.us", "X"), "chat")  # lies
    for f in (ha_a, ha_b, waha, bobs):
        await f.start()
    async with TestClient(TestServer(make_app())) as http:
        await test_debug_export(http)
        await test_addresses(http, frigate, internal, ha_a, ha_b)
        await test_link_downloads(http)
        await test_waha(http, waha, evil)
        await test_waha_accounts(http, waha, bobs)
        await test_telegram_chats(http)
        await test_change_rules(http)
        await test_active_list(http)
        await test_undo_access()
        await test_task_moves_http(http)
        await test_devices(http)
        test_route_modules()
        await test_selection_toggles()
    for s in (frigate, internal, evil):
        await s.close()


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
