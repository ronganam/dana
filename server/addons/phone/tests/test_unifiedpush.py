"""Push without Firebase: UnifiedPush registrations, endpoint checks and pings (addons/phone/push.py).

Two users with the ``phone`` addon (Ann, Bob) and one without (Carl), on the
real handlers: each paired phone registers its own UnifiedPush endpoint (a
token of no device, a user without the addon, an FCM token on a server with
no Firebase key and every malformed endpoint are refused, nothing stored);
``send(user, …)`` POSTs the same ``{"seq", "type"}`` an FCM data message
carries to that user's endpoints only, through a fake ntfy server (no real
ntfy): the body equals the FCM ping's data, redirects are not followed, a
404 / 410 drops the registration, 5xx is retried once, a slow endpoint times
out, an http endpoint stops being pinged once ``DANA_INSECURE_DEV`` is off;
the endpoint is never logged or listed (only its host); one registration per
endpoint; removing the device removes its registration; the self-check is
fine without an FCM key when no phone uses FCM; a phone fetching in a loop with
nothing new gets 429 + Retry-After (events and a fetch after a pause still go).

Run: bin/test test_unifiedpush
"""

import asyncio
import io
import json
import logging
import os
import sys
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("unifiedpush_test")
os.environ["API_TOKEN"] = "unifiedpush-test-env-token"  # a test value: the first admin's token (a headless install)
os.environ["DANA_INSECURE_DEV"] = "1"  # the fake ntfy server is plain http on 127.0.0.1
from cryptography.fernet import Fernet  # noqa: E402
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()

import aiohttp  # noqa: E402
import httpx  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import db, devices, users  # noqa: E402

db.migrate()
users.bootstrap()

from core import checks  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.addons import store  # noqa: E402
from addons.phone import api as phone_api, push  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ANN = users.create("Ann")
BOB = users.create("Bob")
CARL = users.create("Carl")
for _u in (ANN, BOB, users.first_admin()):  # the first admin: API_TOKEN's user, a token of no device
    store.connect(_u, "phone")
store.connect(CARL, "tasks")
PAIRED = {u.id: devices.create(u, "phone", f"{u.name}'s phone") for u in (ANN, BOB, CARL)}
TOKENS = {uid: token for uid, (token, _) in PAIRED.items()}
DEVICE = {uid: device for uid, (_, device) in PAIRED.items()}
push.RETRY_DELAY = 0


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[user.id]}"}


# --- The fake ntfy server ------------------------------------------------------------------------

PINGS: List[Dict[str, Any]] = []
FLAKY: Dict[str, int] = {}


async def ntfy(request: web.Request) -> web.Response:
    topic = request.match_info["topic"]
    PINGS.append({"topic": topic, "body": await request.read(), "ttl": request.headers.get("TTL"),
                  "query": request.query_string})
    if topic.startswith("gone-"):
        return web.json_response({"error": "gone"}, status=410)
    if topic.startswith("moved-"):
        raise web.HTTPFound("/up-elsewhere")
    if topic.startswith("flaky-") and FLAKY.get(topic, 0) == 0:
        FLAKY[topic] = 1
        return web.json_response({"error": "busy"}, status=503)
    if topic.startswith("slow-"):
        await asyncio.sleep(2)
    return web.json_response({"id": "m1", "topic": topic})


def pings_to(topic: str) -> List[Dict[str, Any]]:
    return [p for p in PINGS if p["topic"] == topic]


# --- FCM, faked at httpx (for the body comparison) ------------------------------------------------

FCM: List[Dict[str, Any]] = []


class FakeFcmResponse:
    status_code = 200

    def json(self) -> Dict[str, Any]:
        return {"name": "projects/dana-test/messages/1"}


async def fake_post(self, url, *args, **kwargs):
    assert "fcm.googleapis.com" in url, url
    FCM.append(kwargs["json"]["message"])
    return FakeFcmResponse()


async def fake_fcm_token() -> str:
    return "fcm-test-token"


httpx.AsyncClient.post = fake_post
push._access_token = fake_fcm_token


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    r = app.router
    r.add_post("/api/phone/register", phone_api.phone_register_api)
    r.add_get("/api/phone/events", phone_api.phone_events_api)
    r.add_get("/api/phone/devices", phone_api.phone_devices_api)
    r.add_post("/api/phone/test", phone_api.phone_test_api)
    return app


def rows() -> Dict[str, Tuple[str, str, str]]:
    with get_db_connection() as conn:
        return {r["device_id"]: (r["user_id"], r["transport"], r["address"])
                for r in conn.execute("SELECT device_id, user_id, transport, address FROM phone_push")}


# --- 1. Endpoint checks ----------------------------------------------------------------------------

def test_check_endpoint() -> None:
    good = "https://ntfy.example.com/upAbC123?up=1"
    check("endpoint: https is accepted as it is", push.check_endpoint(f"  {good} ") == good)

    def refused(endpoint: str) -> str:
        try:
            push.check_endpoint(endpoint)
        except push.Refused as e:
            return e.code
        return ""
    os.environ.pop("DANA_INSECURE_DEV")
    try:
        http_off = refused("http://ntfy.example.com/up1")
    finally:
        os.environ["DANA_INSECURE_DEV"] = "1"
    cases = {
        "ftp://ntfy.example.com/up1": "invalid_endpoint",
        "https://": "invalid_endpoint",
        "https://user:pw@ntfy.example.com/up1": "invalid_endpoint",
        "https://user@ntfy.example.com/up1": "invalid_endpoint",
        "https://ntfy.example.com:99999/up1": "invalid_endpoint",
        "ntfy.example.com/up1": "invalid_endpoint",
        "https://ntfy.example.com/up 1": "invalid_endpoint",
        "https://ntfy.example.com/up\r\nX-Injected: 1": "invalid_endpoint",
        "https://ntfy.example.com/" + "a" * push.MAX_ENDPOINT: "invalid_endpoint",
        "": "invalid",
        "   ": "invalid",
    }
    got = {e: refused(e) for e in cases}
    check("endpoint: other schemes, no host, a user name or password, a bad port, no scheme, spaces or line "
          "breaks, too long, none → refused",
          got == cases, got)
    check("endpoint: plain http only with DANA_INSECURE_DEV=1 (no LAN allow-list)",
          http_off == "invalid_endpoint" and refused("http://ntfy.example.com/up1") == "", http_off)
    check("endpoint: only the host is shown", push.endpoint_host(good) == "ntfy.example.com"
          and push.endpoint_host("::not a url") == "?")


# --- 2. Registration --------------------------------------------------------------------------------

async def test_register(client: TestClient, base: str) -> None:
    r = await client.post("/api/phone/register", headers=auth(ANN),
                          json={"transport": "unifiedpush", "endpoint": f"{base}/up-ann?up=1", "app_version": "10.1.0"})
    ann = await r.json()
    r = await client.post("/api/phone/register", headers=auth(BOB),
                          json={"transport": "unifiedpush", "endpoint": f"{base}/up-bob?up=1"})
    bob = await r.json()
    check("register: each paired phone registers its endpoint (transport unifiedpush, the user's latest seq)",
          ann == {"ok": True, "transport": "unifiedpush", "latest_seq": 0} and bob.get("transport") == "unifiedpush", (ann, bob))
    check("register: each registration is the request's own device, of its token's user",
          rows() == {DEVICE[ANN.id].id: (ANN.id, "unifiedpush", f"{base}/up-ann?up=1"),
                     DEVICE[BOB.id].id: (BOB.id, "unifiedpush", f"{base}/up-bob?up=1")}, rows())

    r = await client.post("/api/phone/register", headers=auth(CARL),
                          json={"transport": "unifiedpush", "endpoint": f"{base}/up-carl"})
    check("register: no phone addon → 403 not_connected", r.status == 403
          and (await r.json()).get("error") == "not_connected")
    r = await client.post("/api/phone/register", headers={"Authorization": "Bearer unifiedpush-test-env-token"},
                          json={"transport": "unifiedpush", "endpoint": f"{base}/up-script"})
    check("register: a token of no device → 400 device_required", r.status == 400
          and (await r.json()).get("error") == "device_required")

    before = rows()
    r = await client.post("/api/phone/register", headers=auth(BOB), json={"transport": "fcm", "token": "fcm-bob"})
    body = await r.json()
    check("register: FCM on a server without FCM_SERVICE_ACCOUNT_JSON → 400 fcm_not_configured with a reason, "
          "the UnifiedPush registration kept", r.status == 400 and body.get("error") == "fcm_not_configured"
          and "UnifiedPush" in body.get("detail", "") and rows() == before, body)
    answers = []
    for payload in ({"endpoint": f"{base}/up-bob"}, {"transport": "apns", "token": "x"},
                    {"transport": "unifiedpush"}, {"transport": "unifiedpush", "token": f"{base}/up-bob"},
                    {"transport": "unifiedpush", "endpoint": "ftp://example.com/up"},
                    {"transport": "unifiedpush", "endpoint": "https://u:p@ntfy.example.com/up"}):
        r = await client.post("/api/phone/register", headers=auth(BOB), json=payload)
        answers.append((r.status, (await r.json()).get("error")))
    check("register: no or an unknown transport, no endpoint, the endpoint as a token, a bad endpoint → 400, "
          "nothing changed", answers == [(400, "invalid")] * 4 + [(400, "invalid_endpoint")] * 2
          and rows() == before, answers)

    r = await client.get("/api/phone/devices", headers=auth(BOB))
    dv = await r.json()
    check("devices: the caller's only, with the transport and the endpoint's host, never the endpoint",
          [(d["device_id"], d["transport"], d["endpoint_host"]) for d in dv["devices"]]
          == [(DEVICE[BOB.id].id, "unifiedpush", "127.0.0.1")] and "up-bob" not in json.dumps(dv)
          and dv["fcm_configured"] is False, dv)


# --- 3. Pings ---------------------------------------------------------------------------------------

async def test_send(client: TestClient, base: str) -> None:
    PINGS.clear()
    ok = await push.send(BOB, "reminder", {"task_id": "t-bob"})
    seq = push.latest_seq(BOB)
    got = pings_to("up-bob")
    check("send: Bob's event pings only Bob's endpoint", ok and len(PINGS) == 1 and len(got) == 1, PINGS)
    check("send: the ping is the event's number and type and nothing else (no content), with its TTL",
          got and json.loads(got[0]["body"]) == {"seq": str(seq), "type": "reminder"}
          and got[0]["ttl"] == str(push.TTL["reminder"]) and b"t-bob" not in got[0]["body"]
          and got[0]["query"] == "up=1", got)
    r = await client.get("/api/phone/events?after=0", headers=auth(BOB))
    ev = await r.json()
    check("send: the app fetches the event itself with its device token",
          [e["data"].get("task_id") for e in ev["events"]] == ["t-bob"] and ev["registered"] is True, ev)
    PINGS.clear()
    ok = await push.send(CARL, "reminder", {"task_id": "t-carl"})
    check("send: a user without the addon → nothing stored, nothing pinged", not ok and PINGS == []
          and push.latest_seq(CARL) == 0)
    PINGS.clear()
    r = await client.post("/api/phone/test", headers=auth(ANN))
    check("test push: to the caller's endpoint only", (await r.json()).get("delivered") is True
          and [p["topic"] for p in PINGS] == ["up-ann"], PINGS)

    # The same ping on both transports: Ann adds a phone that uses FCM (a server with a Firebase key).
    os.environ["FCM_SERVICE_ACCOUNT_JSON"] = '{"project_id": "dana-test"}'
    try:
        fcm_token, fcm_phone = devices.create(ANN, "phone", "Ann's other phone")
        r = await client.post("/api/phone/register", headers={"Authorization": f"Bearer {fcm_token}"},
                              json={"transport": "fcm", "token": "fcm-ann"})
        check("register: FCM is accepted once the server has its key", r.status == 200
              and (await r.json()).get("transport") == "fcm")
        PINGS.clear()
        FCM.clear()
        ok = await push.send(ANN, "alert", {"notification_id": "n1", "title": "secret text"})
        up = pings_to("up-ann")
        check("send: the UnifiedPush body equals the FCM data message's data",
              ok and len(FCM) == 1 and len(up) == 1 and FCM[0]["token"] == "fcm-ann"
              and json.loads(up[0]["body"]) == FCM[0]["data"] == {"seq": str(push.latest_seq(ANN)), "type": "alert"},
              (FCM, up))
        with users.acting_as(ANN):
            ann_check = {c["key"]: c for c in checks.compute(user=ANN)}["push"]
        devices.remove(fcm_phone)
    finally:
        os.environ.pop("FCM_SERVICE_ACCOUNT_JSON")
    check("self-check: lists each phone's transport", "FCM" in json.dumps(ann_check)
          and "UnifiedPush via 127.0.0.1" in json.dumps(ann_check), ann_check)
    with users.acting_as(BOB):
        bob_check = {c["key"]: c for c in checks.compute(user=BOB)}["push"]
    check("self-check: no FCM key is fine when no phone uses FCM", bob_check["ok"], bob_check)


async def test_failures(client: TestClient, base: str) -> None:
    async def register(user, endpoint: str) -> int:
        r = await client.post("/api/phone/register", headers=auth(user),
                              json={"transport": "unifiedpush", "endpoint": endpoint})
        return r.status

    await register(BOB, f"{base}/moved-bob")
    PINGS.clear()
    ok = await push.send(BOB, "notify", {"title": "x", "text": "y"})
    check("redirect: not followed (no request to its target), not delivered, the registration kept",
          not ok and [p["topic"] for p in PINGS] == ["moved-bob"] and DEVICE[BOB.id].id in rows(), PINGS)

    await register(BOB, f"{base}/flaky-bob")
    PINGS.clear()
    ok = await push.send(BOB, "notify", {"title": "x", "text": "y"})
    check("5xx: retried once, then delivered", ok and [p["topic"] for p in PINGS] == ["flaky-bob", "flaky-bob"], PINGS)

    await register(BOB, f"{base}/slow-bob")
    real_timeout = push.UNIFIEDPUSH_TIMEOUT
    push.UNIFIEDPUSH_TIMEOUT = aiohttp.ClientTimeout(total=0.3)
    try:
        started = asyncio.get_running_loop().time()
        ok = await push.send(BOB, "notify", {"title": "x", "text": "y"})
        took = asyncio.get_running_loop().time() - started
    finally:
        push.UNIFIEDPUSH_TIMEOUT = real_timeout
    check("timeout: a slow endpoint gives up quickly (two short tries), not delivered", not ok and took < 1.8, took)

    await register(BOB, f"{base}/up-bob")
    os.environ.pop("DANA_INSECURE_DEV")
    try:
        PINGS.clear()
        ok = await push.send(BOB, "notify", {"title": "x", "text": "y"})
    finally:
        os.environ["DANA_INSECURE_DEV"] = "1"
    check("http endpoint: not pinged once DANA_INSECURE_DEV is off", not ok and PINGS == [], PINGS)

    await register(ANN, f"{base}/gone-ann")
    PINGS.clear()
    ok = await push.send(ANN, "notify", {"title": "x", "text": "y"})
    r = await client.get("/api/phone/events?after=0", headers=auth(ANN))
    check("gone (410): only that registration is removed (the device and Bob's stay); the app is told to register again",
          not ok and DEVICE[ANN.id].id not in rows() and DEVICE[BOB.id].id in rows()
          and devices.get(DEVICE[ANN.id].id) is not None and (await r.json())["registered"] is False, rows())

    # One registration per endpoint: the app paired again (a new device) takes it over.
    await register(ANN, f"{base}/up-ann")
    new_token, new_phone = devices.create(ANN, "phone", "Ann's phone, paired again")
    r = await client.post("/api/phone/register", headers={"Authorization": f"Bearer {new_token}"},
                          json={"transport": "unifiedpush", "endpoint": f"{base}/up-ann"})
    check("one registration per endpoint: the same endpoint from another device moves it",
          r.status == 200 and DEVICE[ANN.id].id not in rows() and rows().get(new_phone.id, ("",))[0] == ANN.id, rows())
    devices.remove(new_phone)
    check("device removed → its push registration is gone (Bob's stays)",
          new_phone.id not in rows() and DEVICE[BOB.id].id in rows(), rows())


async def test_flood(client: TestClient) -> None:
    """A phone fetching in a loop with nothing new is told to wait; events and a fetch after a pause still go."""
    after = push.latest_seq(BOB)
    phone_api._empty_fetches.clear()
    statuses = []
    for _ in range(phone_api.FLOOD_MAX + 2):
        r = await client.get(f"/api/phone/events?after={after}", headers=auth(BOB))
        statuses.append((r.status, r.headers.get("Retry-After")))
    check("flood: more than FLOOD_MAX empty fetches within the window → 429 with Retry-After",
          statuses[:phone_api.FLOOD_MAX] == [(200, None)] * phone_api.FLOOD_MAX
          and statuses[phone_api.FLOOD_MAX:] == [(429, str(phone_api.FLOOD_RETRY_AFTER))] * 2, statuses)
    r = await client.get("/api/phone/events?after=0", headers=auth(ANN))
    check("flood: another phone is not affected", r.status == 200, r.status)
    await push.send(BOB, "reminder", {"task_id": "t-flood"})
    r = await client.get(f"/api/phone/events?after={after}", headers=auth(BOB))
    got = await r.json() if r.status == 200 else {}
    check("flood: a fetch that has events gets them even mid-flood", r.status == 200
          and [e["data"].get("task_id") for e in got.get("events", [])] == ["t-flood"], (r.status, got))
    real_window = phone_api.FLOOD_WINDOW
    phone_api.FLOOD_WINDOW = 0.2
    try:
        await asyncio.sleep(0.3)
        r = await client.get(f"/api/phone/events?after={push.latest_seq(BOB)}", headers=auth(BOB))
    finally:
        phone_api.FLOOD_WINDOW = real_window
    check("flood: a ping's fetch after a quiet moment goes through", r.status == 200 and (await r.json())["events"] == [],
          r.status)


def test_logs(log: io.StringIO) -> None:
    text = log.getvalue()
    check("logs: endpoints never logged in full (only the host)", "UnifiedPush via 127.0.0.1" in text
          and not any(t in text for t in ("up-bob", "up-ann", "gone-ann", "moved-bob", "flaky-bob", "slow-bob")), text)


async def main() -> None:
    log = io.StringIO()
    handler = logging.StreamHandler(log)
    logging.getLogger("addons.phone.push").addHandler(handler)
    logging.getLogger("addons.phone.push").setLevel(logging.DEBUG)
    ntfy_app = web.Application()
    ntfy_app.router.add_post("/{topic}", ntfy)
    async with TestServer(ntfy_app) as ntfy_server, TestClient(TestServer(build_app())) as client:
        base = str(ntfy_server.make_url("")).rstrip("/")
        await test_register(client, base)
        await test_send(client, base)
        await test_failures(client, base)
        await test_flood(client)
    test_logs(log)


test_check_endpoint()
asyncio.run(main())

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
