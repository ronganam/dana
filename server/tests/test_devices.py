"""Checks for devices, pairing and setup mode: core/devices.py, core/devices_api.py, core/setup.py, core/setup_api.py.
Run: bin/test test_devices

Setup mode (no users, no API_TOKEN: only the setup page, its API, /api/health and static files answer; the first
admin created once with the one-time code, signed in, their browser their first device), pairing (codes: 8
characters, 5 minutes, single use, stored hashed; the QR string; wrong kind; expiry; reuse; the wrong-code limit per
client and overall), devices (the request's device and its last_seen_at, rename / room / role, revoke: the token
stops at once), isolation between two members and the admin, /login with a pairing code, no code or token in the
logs, and the phone addon's phone_devices rows moved onto core devices by its migration. Temp DB, no network.
"""

import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("devices_test")
os.environ["DANA_ADDON_DIRS"] = ""

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import addons  # noqa: F401  (loads the addons: the first admin's default addons)
from core import access, db, devices, devices_api, pages_api, public_url, ratelimit, setup, setup_api, users
from core.auth import SESSION_COOKIE, request_device, setup_auth
from core.db import get_db_connection
from core.addons import store
from core.paths import DATABASE_PATH

db.migrate()

results: List[Tuple[str, bool]] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


class Capture(logging.Handler):
    """Every log line of the run, to prove no code or token reaches the log."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


LOG = Capture()
logging.getLogger().addHandler(LOG)
logging.getLogger().setLevel(logging.INFO)
SECRETS: List[str] = []  # every code and token handed out, checked against the log at the end


def all_db_text(path: str = str(DATABASE_PATH)) -> str:
    conn = sqlite3.connect(path)
    try:
        out = []
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            for row in conn.execute(f"SELECT * FROM '{t}'"):
                out.extend(str(v) for v in row)
        return "\n".join(out)
    finally:
        conn.close()


async def whoami(request: web.Request) -> web.Response:
    user = request.get("user")
    device = request_device(request)
    return web.json_response({"user": user.id if user else None, "device": device.id if device else None})


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    pages_api.setup(app)
    devices_api.setup(app)
    setup_api.setup(app)
    app.router.add_get("/api/whoami", whoami)
    app.router.add_get("/somewhere", whoami)
    return app


def bearer(token: str, client_ip: str = "") -> Dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if client_ip:
        h["X-Forwarded-For"] = client_ip
    return h


async def body(r) -> Any:
    try:
        return await r.json()
    except Exception:
        return await r.text()


# --- 1. Setup mode -------------------------------------------------------------------------------

def test_setup_required() -> None:
    check("setup: a fresh database with no API_TOKEN waits for its first admin", setup.required())
    os.environ["API_TOKEN"] = "headless-token"
    check("setup: with API_TOKEN (a headless install) → no setup mode", not setup.required())
    del os.environ["API_TOKEN"]
    os.environ["DANA_INSECURE_DEV"] = "1"
    check("setup: DANA_INSECURE_DEV=1 (local development) → no setup mode", not setup.required())
    del os.environ["DANA_INSECURE_DEV"]
    check("setup: no setup URL before begin() has drawn a code", not setup.code_matches(""))


async def test_setup(client: TestClient) -> Dict[str, Any]:
    url = setup.begin("http://dana.test/")
    code = (url or "").split("code=")[-1]
    SECRETS.append(code)
    check("setup: begin() gives <public url>/setup?code=<random>",
          bool(url) and url.startswith("http://dana.test/setup?code=") and len(code) >= 20, url)

    r = await client.get("/api/whoami")
    check("setup: an API path → 503 setup_required", r.status == 503 and (await body(r)) == {"error": "setup_required"})
    r = await client.get("/somewhere")
    check("setup: a page → 503, a page saying where the setup link is",
          r.status == 503 and "setup link" in await r.text())
    for path in ("/login", "/api/devices", "/api/app/version"):
        r = await client.get(path)
        check(f"setup: {path} → 503", r.status == 503, r.status)
    r = await client.post("/api/pair", json={"code": "ABCDEFGH", "kind": "phone"})
    check("setup: /api/pair → 503", r.status == 503)
    r = await client.get("/api/health")
    check("setup: /api/health still answers", r.status == 200 and (await r.json())["ok"] is True)
    r = await client.get("/setup")
    check("setup: the setup page is open", r.status == 200 and "setup.js" in await r.text())
    r = await client.get("/api/setup")
    check("setup: GET /api/setup says so", r.status == 200 and (await r.json())["setup_required"] is True)

    r = await client.post("/api/setup/admin", json={"code": "nope", "name": "Robin Levi"})
    check("setup: a wrong code → 403 invalid_code", r.status == 403 and (await r.json())["error"] == "invalid_code")
    r = await client.post("/api/setup/admin", json={"code": code, "name": "   "})
    b = await body(r)
    check("setup: no name → 400 invalid name", r.status == 400 and b.get("field") == "name", b)
    r = await client.post("/api/setup/admin", data="not json")
    check("setup: not JSON → 400", r.status == 400)
    check("setup: nothing created by the refused attempts", setup.required() and not users.list_users())

    ratelimit.SETUP_FAILURES.clear()
    statuses = []
    for _ in range(11):
        r = await client.post("/api/setup/admin", json={"code": "guess", "name": "X"},
                              headers={"X-Forwarded-For": "198.51.100.7"})
        statuses.append(r.status)
    r = await client.post("/api/setup/admin", json={"code": code, "name": "Robin Levi"},
                          headers={"X-Forwarded-For": "198.51.100.7"})
    check("setup: wrong setup codes are limited per client (10, then 429 even with the right code)",
          statuses == [403] * 10 + [429] and r.status == 429 and r.headers.get("Retry-After"), statuses)
    ratelimit.SETUP_FAILURES.clear()

    r = await client.post("/api/setup/admin", json={"code": code, "name": "Robin Levi"},
                          headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"})
    created = await body(r)
    cookie = r.cookies.get(SESSION_COOKIE)
    admin = users.get(created.get("user", {}).get("id", "")) if isinstance(created, dict) else None
    token = created.get("token", "") if isinstance(created, dict) else ""
    SECRETS.append(token)
    check("setup: the right code + a name → 201, the first admin", r.status == 201 and admin is not None
          and admin.name == "Robin Levi" and admin.access == access.ADMIN and admin == users.first_admin(), created)
    check("setup: the browser is signed in (the session cookie of the returned token)",
          cookie is not None and cookie.value == users.session_cookie_value(token) and cookie["httponly"])
    device = created.get("device", {})
    check("setup: their browser is their first device (kind browser, named from the browser)",
          device.get("kind") == "browser" and device.get("name") == "Firefox on Linux" and device.get("paired")
          and device.get("user_id") == (admin.id if admin else None), device)
    with get_db_connection() as conn:
        mark = conn.execute("SELECT 1 FROM system_marks WHERE key = ?", (store.FIRST_ADMIN_DEFAULTS_MARK,)).fetchone()
    check("setup: the first admin got their default addons (once)", mark is not None
          and set(store.connect_defaults(admin)) == set() if admin else False)
    r = await client.get("/api/whoami")
    check("setup: the signed-in browser is the admin, from their browser device",
          (await body(r)) == {"user": admin.id if admin else None, "device": device.get("id")})

    check("setup: setup mode has ended (a user exists)", not setup.required())
    r = await client.post("/api/setup/admin", json={"code": code, "name": "Mallory"})
    check("setup: a second /api/setup/admin → 409 already_set_up", r.status == 409
          and (await r.json())["error"] == "already_set_up")
    check("setup: the code is spent", not setup.code_matches(code))
    check("setup: still one user", [u.name for u in users.list_users()] == ["Robin Levi"])
    r = await client.get("/api/setup")
    check("setup: GET /api/setup says it is done", (await r.json())["setup_required"] is False)
    client.session.cookie_jar.clear()
    r = await client.get("/api/whoami")
    check("setup over: a request without credentials → 401", r.status == 401)
    r = await client.get("/setup", allow_redirects=False)
    check("setup over: the setup page needs signing in like any page", r.status == 302
          and r.headers["Location"].startswith("/login"))
    return {"admin": admin, "token": token, "device": device}


# --- 2. Pairing ----------------------------------------------------------------------------------

async def start(client: TestClient, token: str, payload: Dict[str, Any], **headers: str) -> Tuple[int, Any]:
    r = await client.post("/api/devices/pairings", json=payload, headers={**bearer(token), **headers})
    b = await body(r)
    if isinstance(b, dict) and b.get("code"):
        SECRETS.append(b["code"])
    return r.status, b


async def pair(client: TestClient, code: str, kind: str, name: str = "", ip: str = "") -> Tuple[int, Any]:
    r = await client.post("/api/pair", json={"code": code, "kind": kind, "name": name},
                          headers={"X-Forwarded-For": ip} if ip else {})
    b = await body(r)
    if isinstance(b, dict) and b.get("token"):
        SECRETS.append(b["token"])
    return r.status, b


async def test_pairing(client: TestClient, S: Dict[str, Any]) -> None:
    gal, maya = S["gal"], S["maya"]
    client.session.cookie_jar.clear()  # the login test signed this client in
    status, p = await start(client, S["gal_token"], {"kind": "phone"},
                            **{"X-Forwarded-Proto": "https", "X-Forwarded-Host": "dana.example.test"})
    code = p.get("code", "")
    check("pairing: a user starts one for themselves → 201", status == 201 and p.get("user") == {"id": gal.id,
                                                                                                "name": gal.name}, p)
    check("pairing: the code is 8 characters of the unambiguous alphabet",
          len(code) == 8 and all(ch in devices.CODE_ALPHABET for ch in code) and not set(code) & set("01IO"), code)
    left = (time.mktime(time.strptime(p.get("expires_at", "")[:19], "%Y-%m-%dT%H:%M:%S")) - time.mktime(time.gmtime()))
    check("pairing: valid 5 minutes", 290 <= left <= 301, left)
    check("pairing: the QR string names the public address (forwarded host / proto) and the code",
          p.get("qr") == f"dana://pair?server=https%3A%2F%2Fdana.example.test&code={code}", p.get("qr"))
    os.environ["PUBLIC_URL"] = "https://dana.public.test/"
    _, p2 = await start(client, S["gal_token"], {"kind": "watch"})
    del os.environ["PUBLIC_URL"]
    check("pairing: PUBLIC_URL overrides the request's address",
          p2.get("qr", "").startswith("dana://pair?server=https%3A%2F%2Fdana.public.test&code="), p2.get("qr"))
    check("pairing: the code is stored hashed, never as typed", code not in all_db_text())

    r = await client.get(f"/api/devices/pairings/{p['id']}", headers=bearer(S["gal_token"]))
    check("pairing: status pending", (await r.json()).get("status") == "pending")
    status, b = await pair(client, code, "tablet")
    check("pairing: a code made for another kind → 409 wrong_kind (the code stays usable)",
          status == 409 and b.get("error") == "wrong_kind" and b.get("expected") == "phone", b)
    status, b = await pair(client, code, "spaceship")
    check("pairing: an unknown kind → 400", status == 400 and b.get("field") == "kind", b)
    typed = f"{code[:4].lower()}-{code[4:].lower()}"
    status, b = await pair(client, typed, "phone", "Pixel 9")
    dev = b.get("device", {}) if isinstance(b, dict) else {}
    check("pairing: the code as typed (any case, a dash) → the token, the device, the user, the server's version",
          status == 200 and b.get("token") and dev.get("kind") == "phone" and dev.get("name") == "Pixel 9"
          and dev.get("user_id") == gal.id and b.get("user") == {"id": gal.id, "name": gal.name}
          and b.get("server", {}).get("version") == pages_api.server_version(), b)
    phone_token = b.get("token", "")
    check("pairing: the device's token is stored hashed only", phone_token and phone_token not in all_db_text())
    status, b = await pair(client, code, "phone")
    check("pairing: a code works once → 403 invalid_code", status == 403 and b.get("error") == "invalid_code", b)
    r = await client.get(f"/api/devices/pairings/{p['id']}", headers=bearer(S["gal_token"]))
    st = await r.json()
    check("pairing: status paired, with the device (what the Devices page polls)",
          st.get("status") == "paired" and st.get("device", {}).get("id") == dev.get("id"), st)

    r = await client.get("/api/whoami", headers=bearer(phone_token))
    check("device: a request with its token carries the user and the device (request['device'])",
          (await r.json()) == {"user": gal.id, "device": dev.get("id")})
    with get_db_connection() as conn:
        conn.execute("UPDATE devices SET last_seen_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (dev["id"],))
        conn.commit()
    devices._touched.clear()
    await client.get("/api/whoami", headers=bearer(phone_token))
    seen = devices.get(dev["id"]).last_seen_at
    await client.get("/api/whoami", headers=bearer(phone_token))
    check("device: a request refreshes last_seen_at (at most every few minutes)",
          seen > "2026" and devices.get(dev["id"]).last_seen_at == seen, seen)
    r = await client.get("/api/whoami", headers=bearer(S["gal_token"]))
    check("device: a token of no device → the user, no device", (await r.json()) == {"user": gal.id, "device": None})
    r = await client.get("/api/devices", headers=bearer(phone_token))
    listed = (await r.json()).get("devices", [])
    check("devices: the list marks the device making the request as current",
          [(d["id"], d["current"]) for d in listed] == [(dev["id"], True)], listed)
    S["gal_phone"] = (phone_token, dev["id"])

    # Expiry
    _, p3 = await start(client, S["gal_token"], {"kind": "phone"})
    with get_db_connection() as conn:
        conn.execute("UPDATE device_pairings SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (p3["id"],))
        conn.commit()
    status, b = await pair(client, p3["code"], "phone")
    r = await client.get(f"/api/devices/pairings/{p3['id']}", headers=bearer(S["gal_token"]))
    check("pairing: an expired code → 403 invalid_code; status expired",
          status == 403 and b.get("error") == "invalid_code" and (await r.json()).get("status") == "expired", b)

    # Cancel
    _, p4 = await start(client, S["gal_token"], {"kind": "phone"})
    r = await client.delete(f"/api/devices/pairings/{p4['id']}", headers=bearer(maya.token))
    check("pairing: someone else cannot cancel it (404)", r.status == 404)
    r = await client.get(f"/api/devices/pairings/{p4['id']}", headers=bearer(maya.token))
    check("pairing: someone else cannot see its status (404)", r.status == 404)
    r = await client.delete(f"/api/devices/pairings/{p4['id']}", headers=bearer(S["gal_token"]))
    status, b = await pair(client, p4["code"], "phone")
    check("pairing: cancelled → the code stops working", r.status == 200 and status == 403, (r.status, b))

    # Fields and who may pair for whom
    status, b = await start(client, S["gal_token"], {"kind": "phone", "role": "kitchen"})
    check("pairing: a role on a phone → 400 role", status == 400 and b.get("field") == "role", b)
    status, b = await start(client, S["gal_token"], {"kind": "tablet", "role": "garage"})
    check("pairing: an unknown role → 400 role", status == 400 and b.get("field") == "role", b)
    status, b = await start(client, S["gal_token"], {"kind": "toaster"})
    check("pairing: an unknown kind → 400 kind", status == 400 and b.get("field") == "kind", b)
    status, b = await start(client, S["gal_token"], {"kind": "phone", "user_id": maya.user.id})
    check("pairing: a member pairing for someone else → 403 needs manage_users",
          status == 403 and b == {"error": "forbidden", "needs": "manage_users"}, b)
    status, b = await start(client, S["admin_token"], {"kind": "phone", "user_id": "nobody"})
    check("pairing: an admin pairing for an unknown user → 404", status == 404, b)
    status, b = await start(client, S["admin_token"], {"kind": "tablet", "user_id": gal.id, "name": "Kitchen tablet",
                                                       "room": "Kitchen", "role": "kitchen"})
    check("pairing: an admin pairs a household tablet for another user", status == 201
          and b.get("user", {}).get("id") == gal.id and b.get("role") == "kitchen", b)
    status, t = await pair(client, b.get("code", ""), "tablet", "Lenovo Tab")
    tab = t.get("device", {}) if isinstance(t, dict) else {}
    check("pairing: the tablet is that user's, with the pairing's name, room and role",
          status == 200 and tab.get("user_id") == gal.id and tab.get("name") == "Kitchen tablet"
          and tab.get("room") == "Kitchen" and tab.get("role") == "kitchen", t)
    S["gal_tablet"] = (t.get("token", ""), tab.get("id", ""))
    r = await client.get(f"/api/devices/pairings/{b.get('id')}", headers=bearer(S["gal_token"]))
    check("pairing: only the one who started it polls its status (the admin, not the device's user)", r.status == 404)


async def test_limits(client: TestClient, S: Dict[str, Any]) -> None:
    ratelimit.PAIRING_FAILURES.clear()
    _, p = await start(client, S["gal_token"], {"kind": "phone"})
    statuses = []
    for i in range(11):
        status, _ = await pair(client, "WRONGCDE" if i % 2 else "ZZZZZZZZ", "phone", ip="203.0.113.9")
        statuses.append(status)
    status, b = await pair(client, p["code"], "phone", ip="203.0.113.9")
    check("limit: 10 wrong codes from one client, then 429 (the right code too)",
          statuses == [403] * 10 + [429] and status == 429 and b.get("error") == "rate_limited", statuses)
    status, b = await pair(client, p["code"], "phone", ip="203.0.113.10")
    check("limit: another client is not held back", status == 200, b)
    S["gal_other_phone"] = b.get("device", {}).get("id", "") if isinstance(b, dict) else ""
    ratelimit.PAIRING_FAILURES.clear()

    lim = ratelimit.FailureLimit(per_client=5, overall=3, window=60.0)
    for key in ("a", "b", "c"):
        lim.fail(key)
    check("limit: past the overall limit every client waits (guessing from many addresses)",
          lim.retry_after("d") is not None and lim.retry_after("a") is not None)
    lim = ratelimit.FailureLimit(per_client=2, overall=100, window=0.2)
    lim.fail("a"), lim.fail("a")
    blocked = lim.retry_after("a") is not None
    time.sleep(0.25)
    check("limit: the window moves on", blocked and lim.retry_after("a") is None)


# --- 3. Devices: list, change, revoke, isolation -------------------------------------------------

async def test_devices(client: TestClient, S: Dict[str, Any]) -> None:
    gal, maya, admin = S["gal"], S["maya"], S["admin"]
    phone_token, phone_id = S["gal_phone"]
    tablet_token, tablet_id = S["gal_tablet"]
    _, p = await start(client, maya.token, {"kind": "watch"})
    _, m = await pair(client, p["code"], "watch", "Galaxy Watch")
    maya_token, maya_watch = m["token"], m["device"]["id"]

    async def listed(token: str, query: str = "") -> Tuple[int, Any]:
        r = await client.get(f"/api/devices{query}", headers=bearer(token))
        return r.status, await body(r)

    _, g = await listed(S["gal_token"])
    _, mm = await listed(maya.token)
    check("isolation: each member lists only their own devices",
          {d["id"] for d in g["devices"]} == {phone_id, tablet_id, S["gal_other_phone"]} and [d["id"] for d in mm["devices"]] == [maya_watch],
          (g, mm))
    check("isolation: no token or token id in the list", all("token" not in k for d in g["devices"] for k in d), g)
    status, b = await listed(S["gal_token"], f"?user={maya.user.id}")
    status_all, _ = await listed(S["gal_token"], "?user=all")
    check("isolation: a member asking for someone else's or everyone's → 403", status == 403 and status_all == 403, b)
    _, everyone = await listed(S["admin_token"], "?user=all")
    names = {d["id"]: d.get("user_name") for d in everyone["devices"]}
    check("admin (manage_users): ?user=all lists everyone's, with whose they are",
          {phone_id, tablet_id, maya_watch, S["setup"]["device"]["id"]} <= set(names)
          and names[maya_watch] == maya.user.name, everyone)
    _, gals = await listed(S["admin_token"], f"?user={gal.id}")
    check("admin: ?user=<id> lists that user's",
          {d["id"] for d in gals["devices"]} == {phone_id, tablet_id, S["gal_other_phone"]}, gals)

    r = await client.patch(f"/api/devices/{maya_watch}", json={"name": "Mine now"}, headers=bearer(S["gal_token"]))
    r2 = await client.delete(f"/api/devices/{maya_watch}", headers=bearer(S["gal_token"]))
    r3 = await client.get("/api/whoami", headers=bearer(maya_token))
    check("isolation: another member's device answers 404 to change and revoke, and keeps working",
          r.status == 404 and r2.status == 404 and (await r3.json())["device"] == maya_watch
          and devices.get(maya_watch).name == "Galaxy Watch")

    r = await client.patch(f"/api/devices/{tablet_id}", json={"name": "  Hall   tablet ", "room": "Hall", "role": "hall"},
                           headers=bearer(S["gal_token"]))
    d = (await body(r)).get("device", {})
    check("change: rename, room and role of one's own tablet", r.status == 200 and d.get("name") == "Hall tablet"
          and d.get("room") == "Hall" and d.get("role") == "hall" and d.get("current") is False, d)
    with get_db_connection() as conn:
        label = conn.execute("SELECT label FROM user_tokens WHERE id = ?", (devices.get(tablet_id).token_id,)).fetchone()
    check("change: the token's label follows the name", label and label[0] == "Hall tablet")
    r = await client.patch(f"/api/devices/{phone_id}", json={"role": "kitchen"}, headers=bearer(S["gal_token"]))
    check("change: a role on a phone → 400", r.status == 400 and (await r.json()).get("field") == "role")
    r = await client.patch(f"/api/devices/{phone_id}", json={"room": "x" * 41}, headers=bearer(S["gal_token"]))
    check("change: a room too long → 400", r.status == 400)
    r = await client.patch(f"/api/devices/{phone_id}", json={"name": 5}, headers=bearer(S["gal_token"]))
    check("change: a name that is not text → 400", r.status == 400)
    r = await client.patch(f"/api/devices/{tablet_id}", json={"role": ""}, headers=bearer(S["admin_token"]))
    check("change: an admin changes another user's device", r.status == 200 and devices.get(tablet_id).role == "")

    r = await client.delete(f"/api/devices/{phone_id}", headers=bearer(S["gal_token"]))
    r2 = await client.get("/api/whoami", headers=bearer(phone_token))
    with get_db_connection() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM devices WHERE id = ?", (phone_id,)).fetchone()[0]
    check("revoke: one's own device → its token fails on the very next request; the device is gone",
          r.status == 200 and r2.status == 401 and rows == 0, (r.status, r2.status))
    r = await client.delete(f"/api/devices/{tablet_id}", headers=bearer(S["admin_token"]))
    r2 = await client.get("/api/whoami", headers=bearer(tablet_token))
    check("revoke: an admin revokes another user's device", r.status == 200 and r2.status == 401)
    r = await client.delete(f"/api/devices/{phone_id}", headers=bearer(S["gal_token"]))
    check("revoke: twice → 404", r.status == 404)

    stored = devices.get(maya_watch)
    users.revoke_token(stored.token_id)
    check("revoke: revoking a device's token (bin/dana) removes the device", devices.get(maya_watch) is None)
    tok, dev = devices.create(maya.user, "other", "")
    r = await client.get("/api/whoami", headers=bearer(tok))
    check("devices.create (server code): an ordinary user token linked to a device, named after its kind",
          (await r.json()) == {"user": maya.user.id, "device": dev.id} and dev.name == "Device")


async def test_login_with_code(client: TestClient, S: Dict[str, Any]) -> None:
    ratelimit.PAIRING_FAILURES.clear()
    _, p = await start(client, S["gal_token"], {"kind": "browser"})
    client.session.cookie_jar.clear()
    r = await client.post("/login", data={"token": p["code"].lower(), "next": "/"}, allow_redirects=False,
                          headers={"User-Agent": "Mozilla/5.0 (Macintosh; Mac OS X 14) Safari/605"})
    signed = r.status == 302 and r.headers["Location"] == "/" and r.cookies.get(SESSION_COOKIE) is not None
    who = await (await client.get("/api/whoami")).json()
    dev = devices.get(who.get("device") or "")
    check("login: a browser pairing code typed on /login signs the browser in as a device of its user",
          signed and who.get("user") == S["gal"].id and dev is not None and dev.kind == "browser"
          and dev.name == "Safari on macOS", (r.status, who))
    client.session.cookie_jar.clear()
    _, p = await start(client, S["gal_token"], {"kind": "phone"})
    r = await client.post("/login", data={"token": p["code"], "next": "/"}, allow_redirects=False)
    check("login: a phone's code on /login → refused (error=kind), the code stays usable",
          r.status == 302 and "error=kind" in r.headers["Location"]
          and (await pair(client, p["code"], "phone"))[0] == 200)
    r = await client.post("/login", data={"token": "ABCDEFGH", "next": "/"}, allow_redirects=False)
    check("login: a wrong code → error=1", r.status == 302 and "error=1" in r.headers["Location"])
    r = await client.post("/login", data={"token": S["gal_token"], "next": "/"}, allow_redirects=False)
    check("login: a token still signs in", r.status == 302 and r.cookies.get(SESSION_COOKIE) is not None)


async def test_unpaired_tokens(client: TestClient, S: Dict[str, Any]) -> None:
    gal, maya = S["gal"], S["maya"]
    client.session.cookie_jar.clear()  # the login test signed this client in
    old_phone, gal_old = users.issue_token(gal.id, "Gal's old phone")
    maya_raw, maya_old = users.issue_token(maya.user.id, "Maya's tablet")
    SECRETS.extend([old_phone, maya_raw])
    await client.get("/api/whoami", headers=bearer(old_phone))

    r = await client.get("/api/devices", headers=bearer(S["gal_token"]))
    g = await r.json()
    toks = {t["id"]: t for t in g.get("unpaired_tokens", [])}
    check("tokens: the list adds the caller's tokens of no device (label, made, last used), not a device's",
          gal_old.id in toks and toks[gal_old.id]["label"] == "Gal's old phone" and toks[gal_old.id]["last_used_at"]
          and maya_old.id not in toks and maya.token not in json.dumps(g)
          and not ({d.token_id for d in devices.list_for(gal.id)} & set(toks)), g)
    check("tokens: never the token or its hash; `current` marks the one making the request",
          all(set(t) == {"id", "user_id", "label", "created_at", "last_used_at", "current"} for t in toks.values())
          and old_phone not in json.dumps(g) and sum(t["current"] for t in toks.values()) == 1
          and not toks[gal_old.id]["current"], g)
    r = await client.get("/api/devices", headers=bearer(maya.token))
    m = await r.json()
    check("tokens isolation: Maya sees only hers",
          {t["user_id"] for t in m["unpaired_tokens"]} == {maya.user.id} and maya_old.id in {t["id"] for t in m["unpaired_tokens"]}, m)
    r = await client.get("/api/devices?user=all", headers=bearer(S["admin_token"]))
    everyone = {t["id"]: t for t in (await r.json())["unpaired_tokens"]}
    check("tokens: an admin's ?user=all lists everyone's, with whose they are",
          {gal_old.id, maya_old.id} <= set(everyone) and everyone[maya_old.id]["user_name"] == maya.user.name, everyone)

    r = await client.delete(f"/api/devices/tokens/{maya_old.id}", headers=bearer(S["gal_token"]))
    r2 = await client.delete("/api/devices/tokens/nosuchtoken", headers=bearer(S["gal_token"]))
    still = await client.get("/api/whoami", headers=bearer(maya_raw))
    check("revoke token isolation: another user's token id answers 404 like a missing one, and keeps working",
          r.status == 404 and r2.status == 404 and await r.json() == await r2.json() and still.status == 200)
    phone_tok, phone_dev = devices.create(gal, "phone", "Gal's new phone")
    SECRETS.append(phone_tok)
    r = await client.delete(f"/api/devices/tokens/{phone_dev.token_id}", headers=bearer(S["gal_token"]))
    check("revoke token: a device's token → 404 (it goes with its device)",
          r.status == 404 and devices.get(phone_dev.id) is not None)
    r = await client.delete(f"/api/devices/tokens/{gal_old.id}", headers=bearer(S["gal_token"]))
    r2 = await client.get("/api/whoami", headers=bearer(old_phone))
    r3 = await client.get("/api/devices", headers=bearer(S["gal_token"]))
    check("revoke token: one's own → it stops working at once and leaves the list",
          r.status == 200 and r2.status == 401
          and gal_old.id not in {t["id"] for t in (await r3.json())["unpaired_tokens"]}, (r.status, r2.status))
    r = await client.delete(f"/api/devices/tokens/{maya_old.id}", headers=bearer(S["admin_token"]))
    r2 = await client.get("/api/whoami", headers=bearer(maya_raw))
    check("revoke token: an admin (manage_users) revokes another user's", r.status == 200 and r2.status == 401)


# --- 4. The phone addon's devices moved onto core devices ----------------------------------------

def test_phone_migration() -> None:
    path = testenv.scratch_path("phone_move.sqlite3")
    conn = sqlite3.connect(path)
    with open(os.path.join(testenv.FIXTURES, "db_9_0.sql"), encoding="utf-8") as f:
        conn.executescript(f.read())
    admin_id = conn.execute("SELECT id FROM users").fetchone()[0]
    conn.execute("INSERT INTO users (id, name, access, created_at) VALUES ('gal0000000001', 'Gal', 'member', "
                 "'2026-11-02T08:00:00+00:00')")
    conn.execute("INSERT INTO phone_devices (user_id, device_id, token, name, app_version, created_at, updated_at, "
                 "last_fetch_at, last_seq) VALUES (?, 'tab1', 'fcm-tablet', 'Old tablet', '8.0.0', "
                 "'2026-10-01T08:00:00+00:00', '2026-10-02T08:00:00+00:00', NULL, NULL)", (admin_id,))
    conn.execute("INSERT INTO phone_devices (user_id, device_id, token, name, app_version, created_at, updated_at, "
                 "last_fetch_at, last_seq) VALUES ('gal0000000001', 'dev1', 'fcm-gal', NULL, '8.1.0', "
                 "'2026-11-03T08:00:00+00:00', '2026-11-03T09:00:00+00:00', '2026-11-04T10:00:00+00:00', 7)")
    conn.commit()
    conn.close()
    applied = db.migrate(path)
    check("migration: core m0005 and the phone's m0002 / m0003 run on a database with phone_devices rows",
          "core:0005_devices" in applied and "phone:0002_push_on_devices" in applied
          and "phone:0003_push_transports" in applied, applied)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        rows = [dict(r) for r in conn.execute(
            "SELECT d.id, d.user_id, d.name, d.kind, d.room, d.role, d.token_id, d.created_at, d.last_seen_at, "
            "p.device_id, p.user_id AS push_user, p.transport, p.address, p.app_version, p.updated_at, p.last_fetch_at, p.last_seq "
            "FROM devices d LEFT JOIN phone_push p ON p.device_id = d.id ORDER BY d.created_at")]
    finally:
        conn.close()
    check("migration: phone_devices is gone, devices and phone_push are there",
          "phone_devices" not in tables and {"devices", "phone_push", "device_pairings"} <= tables, tables)
    got = [(r["user_id"], r["name"], r["kind"], r["room"], r["role"], r["token_id"], r["last_seen_at"],
            (r["transport"], r["address"]), r["app_version"], r["last_fetch_at"], r["last_seq"],
            r["push_user"] == r["user_id"]) for r in rows]
    check("migration: every row is a core phone device of the same user (name, created, last seen) with its push "
          "registration (an fcm one with its token, app version, last fetch, last seq)", got == [
              (admin_id, "Old tablet", "phone", "", "", None, "2026-10-02T08:00:00+00:00",
               ("fcm", "fcm-tablet"), "8.0.0", None, None, True),
              (admin_id, "Test phone", "phone", "", "", None, "2026-11-01T08:00:00+00:00",
               ("fcm", "fcm-token-sample"), "8.1.0", "2026-11-01T08:00:00+00:00", 1, True),
              ("gal0000000001", "Phone", "phone", "", "", None, "2026-11-04T10:00:00+00:00",
               ("fcm", "fcm-gal"), "8.1.0", "2026-11-04T10:00:00+00:00", 7, True)], got)
    check("migration: the old client-made device ids are not kept as ids",
          not {"tab1", "dev1"} & {r["id"] for r in rows} and len({r["id"] for r in rows}) == 3)


# --- Run -----------------------------------------------------------------------------------------

class Member:
    def __init__(self, user: users.User) -> None:
        self.user = user
        self.token = users.issue_token(user.id, "script")[0]  # a token of no device
        SECRETS.append(self.token)


async def main() -> None:
    test_setup_required()
    async with TestClient(TestServer(build_app())) as client:
        S: Dict[str, Any] = {"setup": await test_setup(client)}
        S["admin"] = S["setup"]["admin"]
        S["admin_token"] = S["setup"]["token"]
        gal = Member(users.create("Gal Cohen"))
        maya = Member(users.create("Maya"))
        S.update(gal=gal.user, gal_token=gal.token, maya=maya)
        await test_pairing(client, S)
        await test_limits(client, S)
        await test_devices(client, S)
        await test_login_with_code(client, S)
        await test_unpaired_tokens(client, S)
    test_phone_migration()
    leaked = [s for s in SECRETS if s and any(s in line or s.lower() in line for line in LOG.lines)]
    check("logs: no pairing code, setup code or token in any log line", SECRETS and not leaked,
          [s[:4] for s in leaked])


asyncio.run(main())
passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
