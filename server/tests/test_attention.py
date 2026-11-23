"""Needs attention: reconnects, fixes, the Reconnect card.

Covers: a refused Home Assistant token marks only that user's connection for
reconnecting (two users with Home Assistant, one without); GET /api/checks
lists it first with its why, since and Reconnect fix, and gives the addon's
other failing checks that fix; the Addons page payload's ``reconnect``; the
Reconnect card on Now (one per connection, its client action, the addon's
other cards left out) dismissible for a day at most and back after it, still
listed while dismissed; a good connection test and new credentials clear it;
a server fix (phone's "Send a test push") runs for the user who pressed it
only, refused when the check is fine or offers no such fix, unknown to a user
without the addon; a fix through the Now card; a dropped push registration
marks the phone for reconnecting and registering again clears it; the
store's helpers (per part, keeping the first ``since``, nothing for an addon
the user lacks). Captures tests/fixtures/attention_web.json (WRITE_WEB_FIXTURE=1),
else checks its shape.

Run: bin/test test_attention
"""
import asyncio
import datetime
import json
import os
import re
from unittest.mock import patch
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("attention_test")
os.environ["API_TOKEN"] = "attention-test-env-token"  # a test value: the first admin's token
os.environ["TIMEZONE"] = "Asia/Jerusalem"
os.environ["FCM_SERVICE_ACCOUNT_JSON"] = '{"project_id": "dana-test"}'
from cryptography.fernet import Fernet  # noqa: E402
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # throwaway: encrypts the fake credentials

import httpx  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import db, devices, users  # noqa: E402

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402

addons_bootstrap.bootstrap()

from core import addons_api, checks, clock, now_api, now_hidden  # noqa: E402
from core.addons import store  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from addons.homeassistant import client as ha_client  # noqa: E402
from addons.phone import push  # noqa: E402
from testfakes import FakeHA  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

ROBIN = users.first_admin()
assert ROBIN is not None
GAL = users.create("Gal Amir")
MAYA = users.create("Maya Levin")  # no Home Assistant, no phone
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ROBIN, GAL, MAYA)}
HA = {name: FakeHA(name, f"tok-{name}", {"person.someone": ("home", {})}, {}, "Asia/Jerusalem")
      for name in ("robin", "gal")}
for _fake in HA.values():
    LOOP.run_until_complete(_fake.start())
for _user, _name in ((ROBIN, "robin"), (GAL, "gal")):
    if not store.is_connected(_user, "phone"):
        store.connect(_user, "phone")
    store.connect(_user, "homeassistant", credentials={"host": HA[_name].url, "token": f"tok-{_name}"})
PHONES = {u.id: devices.create(u, "phone", "Pixel")[1] for u in (ROBIN, GAL)}
for _user, _name in ((ROBIN, "robin"), (GAL, "gal")):
    push.register(_user, PHONES[_user.id], "fcm", f"fcm-{_name}")
FIXTURE = os.path.join(testenv.FIXTURES, "attention_web.json")

# FCM, faked: a token in FCM_FAILING is refused (HTTP 500), one in FCM_GONE is unregistered.
FCM_FAILING: set = set()
FCM_GONE: set = set()
FCM_SENT: List[str] = []


class FakeResponse:
    def __init__(self, status: int, payload: Dict[str, Any]) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> Dict[str, Any]:
        return self._payload


_real_post = httpx.AsyncClient.post


async def fake_post(self, url, *args, **kwargs):
    if "fcm.googleapis.com" not in str(url):
        return await _real_post(self, url, *args, **kwargs)
    token = kwargs["json"]["message"]["token"]
    if token in FCM_GONE:
        return FakeResponse(404, {"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}})
    if token in FCM_FAILING:
        return FakeResponse(400, {"error": {"status": "INVALID_ARGUMENT", "message": "bad message"}})
    FCM_SENT.append(token)
    return FakeResponse(200, {"name": "projects/dana-test/messages/1"})


async def fake_token() -> str:
    return "fcm-test-token"


httpx.AsyncClient.post = fake_post
push._access_token = fake_token
push.RETRY_DELAY = 0


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[user.id]}"}


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    now_api.setup(app)
    addons_api.setup(app)
    return app


async def call(client: TestClient, method: str, path: str, user, body: Any = None) -> Tuple[int, Any]:
    r = await client.request(method, path, headers=auth(user), json=body)
    try:
        payload = await r.json()
    except Exception:
        payload = await r.text()
    return r.status, payload


async def checks_of(client: TestClient, user) -> List[Dict[str, Any]]:
    status, body = await call(client, "GET", "/api/checks", user)
    assert status == 200, body
    return body["checks"]


async def now_items(client: TestClient, user) -> List[Dict[str, Any]]:
    status, body = await call(client, "GET", "/api/now", user)
    assert status == 200, body
    return body["items"] + body.get("quiet", [])


def addon_item(body: Dict[str, Any], name: str) -> Dict[str, Any]:
    return next(i for i in body["addons"] if i["name"] == name)


KEY = "reconnect:homeassistant"
captured: Dict[str, Any] = {}


# --- 1. The store's helpers -----------------------------------------------------------------------

def test_store() -> None:
    check("store: nothing needs reconnecting at first", store.reconnects(ROBIN) == [] and store.reconnects(GAL) == [])
    check("store: an addon the user lacks is not marked (False)",
          store.needs_reconnect(MAYA, "homeassistant", "refused") is False and store.reconnects(MAYA) == [])
    check("store: a part of a connection is marked on its own",
          store.needs_reconnect(GAL, "homeassistant", "First reason.", part="p1", label="Part one") is True)
    first = store.reconnects(GAL, "homeassistant")[0]
    store.needs_reconnect(GAL, "homeassistant", "Second reason.", part="p1", label="Part one")
    again = store.reconnects(GAL, "homeassistant")[0]
    check("store: marking it again keeps the first since, takes the new reason",
          again.since == first.since and again.reason == "Second reason." and again.part == "p1", again)
    check("store: clearing another part changes nothing; clearing all of the addon clears it",
          store.reconnected(GAL, "homeassistant", "p2") is False and len(store.reconnects(GAL)) == 1
          and store.reconnected(GAL, "homeassistant", None) is True and store.reconnects(GAL) == [])
    check("store: Robin's are not touched by Gal's", store.reconnects(ROBIN) == [])


# --- 2. A refused call marks the connection; the checks list it first --------------------------------

async def test_refused(client: TestClient) -> None:
    HA["gal"].token = "tok-gal-rotated"  # Gal's Home Assistant no longer takes the stored token
    reason = await ha_client.check(store.require(GAL, "homeassistant"))
    ok_robin = await ha_client.check(store.require(ROBIN, "homeassistant"))
    pending = store.reconnects(GAL)
    check("refused: Gal's check says the token was refused", reason == "Home Assistant refused the token (HTTP 401).",
          reason)
    check("refused: Gal's Home Assistant needs reconnecting, Robin's (working) and Maya's (none) do not",
          [(r.addon, r.part) for r in pending] == [("homeassistant", "")] and ok_robin is None
          and store.reconnects(ROBIN) == [] and store.reconnects(MAYA) == [], pending)

    # The afternoon, so the morning checks (briefing, the last 7 mornings) read the same whenever this runs.
    afternoon = clock.now().replace(hour=15, minute=0, second=0, microsecond=0)
    with patch.object(clock, "now", lambda: afternoon):
        gal = await checks_of(client, GAL)
        robin = await checks_of(client, ROBIN)
        maya = await checks_of(client, MAYA)
    first = gal[0]
    check("checks: Gal's reconnect is listed first, failing, with why, since and the Reconnect fix",
          first["key"] == KEY and first["ok"] is False and first["title"] == "Reconnect Home Assistant"
          and "refused the token" in first["why"] and first["since"] and first["addon"] == "homeassistant"
          and first["server"] is False
          and first["fix"] == {"kind": "connect", "label": "Reconnect", "addon": "homeassistant"}, first)
    others = [c for c in gal if c["addon"] == "homeassistant" and not c["ok"] and c["key"] != KEY]
    check("checks: Gal's other failing Home Assistant checks offer the same fix",
          all(c["fix"] for c in others), [(c["key"], c["fix"]) for c in others])
    check("checks: nobody else sees it (Robin with Home Assistant, Maya without)",
          not any(checks.is_reconnect(c["key"]) for c in robin + maya), [c["key"] for c in robin + maya])
    captured["checks_reconnect"] = {"status": 200, "body": {"checks": gal}}
    captured["checks_maya"] = {"status": 200, "body": {"checks": maya}}

    status, body = await call(client, "GET", "/api/addons", GAL)
    item = addon_item(body, "homeassistant")
    check("addons: Gal's Home Assistant carries reconnect with its reason, since and fix",
          [(r["part"], r["label"], r["fix"]["kind"]) for r in item.get("reconnect", [])] == [("", "", "connect")]
          and "refused the token" in item["reconnect"][0]["reason"], item.get("reconnect"))
    _, robins = await call(client, "GET", "/api/addons", ROBIN)
    check("addons: Robin's has none", "reconnect" not in addon_item(robins, "homeassistant"))
    captured["addon_reconnect"] = {"status": status, "body": {"addons": [item]}}


# --- 3. The Reconnect card on Now and its day-long dismissal -----------------------------------------

async def test_card(client: TestClient) -> None:
    items = await now_items(client, GAL)
    card = next((i for i in items if i["id"] == f"check:{KEY}"), None)
    check("now: one Reconnect card for Gal's Home Assistant, its primary the addon's connect flow",
          card is not None and card["kind"] == "reconnect" and card["title"] == "Reconnect Home Assistant"
          and card["source"] == "Reconnect · Home Assistant"
          and card["primary"] == {"action": "connect", "label": "Reconnect", "args": {"addon": "homeassistant"},
                                  "client": True}
          and [a["action"] for a in card["secondary"]] == ["ignore"] and card["secondary"][0]["args"] == {"days": 1}
          and card["ref"] == {"type": "check", "id": KEY}, card)
    check("now: the addon's other check cards are left out while it needs reconnecting",
          not [i for i in items if i["kind"] == "check" and i["ref"]["id"] in
               {c.key for c in checks._hooks() if c.addon == "homeassistant"}], [i["id"] for i in items])
    check("now: Robin and Maya get no Reconnect card",
          not [i for u in (ROBIN, MAYA) for i in await now_items(client, u) if i["kind"] == "reconnect"])
    captured["now_reconnect_card"] = card

    status, body = await call(client, "POST", "/api/now/act", GAL,
                              {"id": f"check:{KEY}", "action": "ignore", "args": {"days": 30}})
    until = datetime.datetime.fromisoformat(body.get("until") or "2000-01-01T00:00:00+00:00")
    check("dismiss: asking for 30 days hides it for a day at most",
          status == 200 and until <= clock.now() + datetime.timedelta(days=1, minutes=1), (status, body))
    check("dismiss: the card is gone from Now",
          not [i for i in await now_items(client, GAL) if i["id"] == f"check:{KEY}"])
    listed = next(c for c in await checks_of(client, GAL) if c["key"] == KEY)
    _, addons_body = await call(client, "GET", "/api/addons", GAL)
    check("dismiss: still listed in Needs attention (ignored until) and on the Addons page",
          listed["ok"] is False and listed["ignored_until"] and addon_item(addons_body, "homeassistant").get("reconnect"),
          listed)
    status, body = await call(client, "POST", f"/api/checks/{KEY}/ignore", GAL, {"days": 365})
    until = datetime.datetime.fromisoformat(body.get("until") or "2000-01-01T00:00:00+00:00")
    check("dismiss: the checks' ignore is capped at a day too",
          status == 200 and until <= clock.now() + datetime.timedelta(days=1, minutes=1), (status, body))
    status, body = await call(client, "POST", f"/api/checks/{KEY}/ignore", ROBIN, {"days": 1})
    check("dismiss: Robin cannot ignore Gal's reconnect (unknown to him)", status == 404, (status, body))

    row = now_hidden.row(GAL, f"check:{KEY}")
    with get_db_connection() as conn:  # a day later: the dismissal has run out
        conn.execute("UPDATE now_hidden SET until = ? WHERE user_id = ? AND item_id = ?",
                     (now_hidden.utc_iso(clock.now() - datetime.timedelta(seconds=1)), GAL.id, f"check:{KEY}"))
        conn.commit()
    back = [i for i in await now_items(client, GAL) if i["id"] == f"check:{KEY}"]
    check("dismiss: it was hidden for Gal only", row is not None and now_hidden.row(ROBIN, f"check:{KEY}") is None)
    check("dismiss: the card is back a day later (never hidden for good)", len(back) == 1, back)


# --- 4. Reconnecting clears it -----------------------------------------------------------------------

async def test_cleared(client: TestClient) -> None:
    HA["gal"].token = "tok-gal"
    status, body = await call(client, "POST", "/api/addons/homeassistant/test", GAL)
    check("cleared: a connection test that passes clears it",
          status == 200 and body["ok"] is True and store.reconnects(GAL) == [], (body, store.reconnects(GAL)))
    check("cleared: no Reconnect card, no reconnect check",
          not [i for i in await now_items(client, GAL) if i["kind"] == "reconnect"]
          and not [c for c in await checks_of(client, GAL) if checks.is_reconnect(c["key"])])

    HA["gal"].token = "tok-gal-2"
    await ha_client.check(store.require(GAL, "homeassistant"))
    marked = bool(store.reconnects(GAL))
    status, body = await call(client, "PUT", "/api/addons/homeassistant", GAL,
                              {"credentials": {"token": "tok-gal-2"}})
    check("cleared: refused again, then new credentials on the Addons page clear it",
          marked and status == 200 and store.reconnects(GAL) == [], (marked, status, body, store.reconnects(GAL)))


# --- 5. Server fixes ---------------------------------------------------------------------------------

async def test_fixes(client: TestClient) -> None:
    FCM_FAILING.add("fcm-gal")
    await push.notify(GAL, "Dana", "A reminder")
    await push.notify(ROBIN, "Dana", "A reminder")
    FCM_FAILING.discard("fcm-gal")
    gal_push = next(c for c in await checks_of(client, GAL) if c["key"] == "push")
    robin_push = next(c for c in await checks_of(client, ROBIN) if c["key"] == "push")
    check("fix: Gal's failing push offers the server fix; Robin's push is fine",
          gal_push["ok"] is False and gal_push["fix"] == {"kind": "server", "label": "Send a test push", "action": "test"}
          and robin_push["ok"] is True and robin_push["fix"] is None, (gal_push, robin_push))
    captured["checks_push_failing"] = {"status": 200, "body": {"checks": [gal_push]}}

    card = next(i for i in await now_items(client, GAL) if i["id"] == "check:push")
    check("fix: the Now card's primary runs the fix on the server; Details and Ignore stay",
          card["kind"] == "check" and card["primary"] == {"action": "fix", "label": "Send a test push",
                                                          "args": {"fix": "test"}}
          and [a["action"] for a in card["secondary"]] == ["open", "ignore"], card)
    captured["now_fix_card"] = card

    FCM_SENT.clear()
    status, body = await call(client, "POST", "/api/checks/push/fix", ROBIN, {"fix": "test"})
    check("fix: refused for Robin, whose push is fine (409), and nothing sent",
          status == 409 and "fine" in body.get("error", "") and FCM_SENT == [], (status, body, FCM_SENT))
    captured["fix_refused"] = {"status": status, "body": body}
    status, body = await call(client, "POST", "/api/checks/push/fix", MAYA, {"fix": "test"})
    check("fix: unknown to Maya, who has no phone (404)", status == 404 and FCM_SENT == [], (status, body))
    status, body = await call(client, "POST", "/api/checks/push/fix", GAL, {"fix": "restart"})
    check("fix: a fix the check does not offer is refused (409)", status == 409 and FCM_SENT == [], (status, body))
    status, body = await call(client, "POST", "/api/checks/push/fix", GAL, {"fix": "test"})
    check("fix: Gal's test push goes to Gal's phone only, and says so",
          status == 200 and body["ok"] is True and "accepted" in body["message"] and FCM_SENT == ["fcm-gal"],
          (status, body, FCM_SENT))
    captured["fix_ok"] = {"status": status, "body": body}
    gal_push = next(c for c in await checks_of(client, GAL) if c["key"] == "push")
    check("fix: Gal's push check passes afterwards", gal_push["ok"] is True, gal_push)

    FCM_FAILING.add("fcm-gal")
    await push.notify(GAL, "Dana", "Another reminder")
    FCM_FAILING.discard("fcm-gal")
    FCM_SENT.clear()
    status, body = await call(client, "POST", "/api/now/act", GAL,
                              {"id": "check:push", "action": "fix", "args": {"fix": "test"}})
    check("fix: through the Now card it runs the same, for Gal",
          status == 200 and "accepted" in body.get("message", "") and FCM_SENT == ["fcm-gal"], (status, body))
    status, body = await call(client, "POST", "/api/now/act", ROBIN,
                              {"id": "check:push", "action": "fix", "args": {"fix": "test"}})
    check("fix: Robin's card for a fine check refuses it (409)", status == 409, (status, body))


# --- 6. Phone push: a dropped registration ------------------------------------------------------------

async def test_push_dropped(client: TestClient) -> None:
    FCM_GONE.add("fcm-gal")
    await push.notify(GAL, "Dana", "A reminder")
    FCM_GONE.discard("fcm-gal")
    pending = store.reconnects(GAL)
    check("push: the push service dropped Gal's only registration → the phone needs reconnecting, Robin's does not",
          [(r.addon, r.part) for r in pending] == [("phone", "")] and store.reconnects(ROBIN) == [], pending)
    first = (await checks_of(client, GAL))[0]
    check("push: its fix opens Devices (the phone addon's reconnect fix)",
          first["key"] == "reconnect:phone" and first["fix"] == {"kind": "screen", "label": "Open Devices",
                                                                "screen": "devices"}, first)
    card = next(i for i in await now_items(client, GAL) if i["id"] == "check:reconnect:phone")
    check("push: the Reconnect card opens Devices",
          card["primary"] == {"action": "screen", "label": "Open Devices", "args": {"screen": "devices"},
                              "client": True}, card)
    captured["checks_phone_reconnect"] = {"status": 200, "body": {"checks": [first]}}
    push.register(GAL, PHONES[GAL.id], "fcm", "fcm-gal-new")
    check("push: registering again clears it", store.reconnects(GAL) == [], store.reconnects(GAL))


# --- The fixture -------------------------------------------------------------------------------------

def shape(value: Any) -> Any:
    """The keys of a payload, recursively (lists by their items' shapes, values dropped)."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(v) for v in value]
    return type(value).__name__ if value is not None else None


def deterministic(text: str) -> str:
    """The captured payloads without this run's ports, user ids and times."""
    for name, fake in HA.items():
        text = text.replace(fake.url, f"http://ha-{name}.example.test")
    for user, name in ((ROBIN, "robin"), (GAL, "gal"), (MAYA, "maya")):
        text = text.replace(user.id, f"user-{name}")
    return re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?([+-]\d\d:\d\d|Z)?", "2026-11-22T08:30:00+02:00", text)


def test_fixture() -> None:
    text = deterministic(json.dumps(captured, indent=1, ensure_ascii=False, sort_keys=True))
    check("fixture: no token in it", not [t for t in ("tok-robin", "tok-gal", "fcm-gal", "fcm-robin") if t in text])
    check("fixture: nothing of this run in it (ports, user ids)",
          not any(f":{fake.server.port}" in text for fake in HA.values())
          and not any(u.id in text for u in users.list_users()))
    if os.getenv("WRITE_WEB_FIXTURE"):
        with open(FIXTURE, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"wrote {FIXTURE}")
        return
    with open(FIXTURE, encoding="utf-8") as f:
        stored = json.load(f)
    drift = [k for k in captured if shape(captured[k]) != shape(stored.get(k))]
    check("fixture: tests/fixtures/attention_web.json has the payloads' current shape "
          "(regenerate with WRITE_WEB_FIXTURE=1)", not drift and set(stored) == set(captured), drift)


async def main() -> None:
    test_store()
    async with TestClient(TestServer(build_app())) as client:
        await test_refused(client)
        await test_card(client)
        await test_cleared(client)
        await test_fixes(client)
        await test_push_dropped(client)
    test_fixture()


if __name__ == "__main__":
    LOOP.run_until_complete(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
