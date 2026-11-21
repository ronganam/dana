"""Checks for users, tokens, access levels and how requests resolve to a user.
Run: bin/test test_users

Covers core/users.py, core/access.py, the web auth middleware (core/auth.py)
and the Telegram AuthMiddleware (core/telegram_bot.py). Temp DB, no network.
"""

import asyncio
import hashlib
import hmac
import os
import sqlite3
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("users_test")
os.environ["OWNER_NAME"] = "Robin"
os.environ["ALLOWED_TELEGRAM_USER_IDS"] = "111, 222,abc"
os.environ["TELEGRAM_CHAT_ID"] = "-1001"
os.environ["API_TOKEN"] = "env-token"
os.environ["INTERNAL_HOSTS"] = "ganam-assistant.ganam-assistant"

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import access, db, users
from core.auth import SESSION_COOKIE, setup_auth
from core.db import get_db_connection
from core.models import list_profiles
from core.paths import DATABASE_PATH
from core.telegram_bot import AuthMiddleware

db.migrate()

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def all_db_text() -> str:
    """Every value in the database file, for "is the token stored?" checks."""
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        out = []
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            for row in conn.execute(f"SELECT * FROM '{t}'"):
                out.extend(str(v) for v in row)
        return "\n".join(out)
    finally:
        conn.close()


# --- Bootstrap ----------------------------------------------------------------------

def test_bootstrap():
    check("before bootstrap: no first admin", users.first_admin() is None)
    created = users.bootstrap()
    admin = users.first_admin()
    check("bootstrap: creates the first user", created is not None and admin is not None and created.id == admin.id)
    check("bootstrap: named from OWNER_NAME, admin", admin.name == "Robin" and admin.access == "admin" and admin.is_admin)
    check("bootstrap: Telegram ids from ALLOWED_TELEGRAM_USER_IDS (non-numeric skipped)",
          admin.telegram_ids == (111, 222), str(admin.telegram_ids))
    check("bootstrap: proactive chat from TELEGRAM_CHAT_ID", admin.telegram_chat_id == -1001)
    check("bootstrap: idempotent", users.bootstrap() is None and len(users.list_users()) == 1)


# --- Tokens -------------------------------------------------------------------------

STATE = {}


def test_tokens():
    admin = users.first_admin()
    check("env API_TOKEN is the first admin's", users.by_token("env-token") == admin)
    check("unknown or empty token: nobody", users.by_token("nope") is None and users.by_token("") is None)

    member = users.create("Dana Tester", access.MEMBER)
    STATE["member"] = member
    raw, tok = users.issue_token(member.id, "Pixel")
    STATE["raw"], STATE["tok"] = raw, tok
    check("issued token resolves to its user", users.by_token(raw) == member)
    check("issued token: tolerant of surrounding spaces", users.by_token(f"  {raw} ") == member)
    text = all_db_text()
    check("token stored hashed: the raw token is not in the DB", raw not in text)
    check("token stored hashed: SHA-256 of the token is", hashlib.sha256(raw.encode()).hexdigest() in text)
    check("cookie stored hashed: the cookie value is not in the DB", users.session_cookie_value(raw) not in text)
    listed = users.list_tokens(member.id)
    check("list_tokens: label, created, used", len(listed) == 1 and listed[0].label == "Pixel"
          and listed[0].created_at and listed[0].last_used_at, str(listed))
    raw2, tok2 = users.issue_token(member.id, "Watch")
    check("one token per device: both work", users.by_token(raw2) == member and users.by_token(raw) == member)
    check("revoke: True, then the token stops working", users.revoke_token(tok2.id) and users.by_token(raw2) is None)
    check("revoke again: False", users.revoke_token(tok2.id) is False)
    try:
        users.issue_token("no-such-user")
        ok = False
    except LookupError:
        ok = True
    check("issue for an unknown user: LookupError", ok)


def test_cookies():
    admin, member, raw = users.first_admin(), STATE["member"], STATE["raw"]
    legacy = hmac.new(b"env-token", b"ganam-web-session", hashlib.sha256).hexdigest()
    check("cookie format unchanged (HMAC of the token, same constant)",
          users.session_cookie_value("env-token") == legacy)
    check("existing env-token cookie resolves to the first admin", users.by_session_cookie(legacy) == admin)
    check("cookie from a DB token resolves to its user", users.by_session_cookie(users.session_cookie_value(raw)) == member)
    check("unknown cookie: nobody", users.by_session_cookie("deadbeef") is None and users.by_session_cookie("") is None)


# --- Telegram -----------------------------------------------------------------------

def test_telegram():
    admin, member = users.first_admin(), STATE["member"]
    check("telegram: known id resolves", users.by_telegram_id(111) == admin and users.by_telegram_id(222) == admin)
    check("telegram: unknown id refused", users.by_telegram_id(999) is None)
    check("telegram: not open while ids exist", not users.telegram_open())
    users.add_telegram_id(member.id, 333)
    check("telegram: added id resolves to that user", users.by_telegram_id(333).id == member.id)
    try:
        users.add_telegram_id(member.id, 111)
        dup = False
    except ValueError:
        dup = True
    check("telegram: an id belongs to one user", dup and users.by_telegram_id(111) == admin)
    before = len(users.list_users())
    try:
        users.create("Clash", telegram_ids=[222])
        clash = False
    except ValueError:
        clash = True
    check("create with a taken Telegram id: refused, nothing half-created",
          clash and len(users.list_users()) == before)


async def test_telegram_middleware():
    admin, member = users.first_admin(), STATE["member"]
    mw = AuthMiddleware()

    async def run(from_id):
        seen = {}

        async def handler(event, data):
            seen["user"] = data.get("user")
            return "handled"

        event = SimpleNamespace(from_user=SimpleNamespace(id=from_id) if from_id is not None else None,
                                reply=AsyncMock(), answer=AsyncMock())
        out = await mw(handler, event, {})
        return out, seen, event

    out, seen, _ = await run(333)
    check("telegram middleware: known id → handler gets the user", out == "handled" and seen["user"].id == member.id)
    out, seen, event = await run(999)
    check("telegram middleware: unknown id refused as before",
          out is None and not seen and event.reply.await_args.args[0] == "⛔ You are not authorized to use this bot.")
    out, seen, _ = await run(None)
    check("telegram middleware: no sender → dropped", out is None and not seen)

    with get_db_connection() as conn:
        saved = [tuple(r) for r in conn.execute("SELECT * FROM user_telegram")]
        conn.execute("DELETE FROM user_telegram")
        conn.commit()
    users.invalidate()
    try:
        out, seen, _ = await run(424242)
        check("telegram open (no ids at all): anyone is the first admin", out == "handled" and seen["user"].id == admin.id)
        out, seen, _ = await run(None)
        check("telegram open: updates without a sender still pass", out == "handled")
    finally:
        with get_db_connection() as conn:
            conn.executemany("INSERT INTO user_telegram VALUES (?, ?, ?)", saved)
            conn.commit()
        users.invalidate()


# --- Access levels ------------------------------------------------------------------

def test_access():
    admin, member = access.level("admin"), access.level("member")
    check("access: admin may use every profile", all(admin.may_use_profile(p.id) for p in list_profiles()))
    check("access: member gets fast and balanced only",
          member.may_use_profile("fast") and member.may_use_profile("balanced") and not member.may_use_profile("deep"))
    check("access: member profiles exist in the catalog",
          set(member.chat_profiles or ()) <= {p.id for p in list_profiles()})
    check("access: ops / manage users / see everyone are admin only",
          admin.ops and admin.manage_users and admin.see_everyone
          and not (member.ops or member.manage_users or member.see_everyone))
    check("access: a user's level", users.first_admin().level == admin and STATE["member"].level == member)
    try:
        access.level("root")
        bad = False
    except ValueError:
        bad = True
    check("access: unknown level → ValueError", bad)
    try:
        users.create("X", "root")
        bad_user = False
    except ValueError:
        bad_user = True
    check("create with an unknown level: refused", bad_user)


# --- Cache --------------------------------------------------------------------------

def test_cache():
    raw = STATE["raw"]
    # CPU time, not wall time: the suite runs scripts side by side, and waiting for a
    # CPU is not the lookup's cost.
    started = time.process_time()
    for _ in range(20000):
        users.by_token(raw)
        users.by_session_cookie("deadbeef")
        users.by_telegram_id(111)
    elapsed = time.process_time() - started
    check("lookups are cheap (60k in well under a second of CPU, no DB per call)", elapsed < 1.0, f"{elapsed:.2f}s")

    # A change made by another process (bin/users.py) shows up within the TTL.
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET name = 'Dana Renamed' WHERE id = ?", (STATE["member"].id,))
        conn.commit()
    saved = users.CACHE_TTL_SECONDS
    users.CACHE_TTL_SECONDS = 0.0
    try:
        check("another process's change is picked up after the TTL",
              users.get(STATE["member"].id).name == "Dana Renamed")
    finally:
        users.CACHE_TTL_SECONDS = saved


# --- Web auth middleware ------------------------------------------------------------

async def whoami(request: web.Request) -> web.Response:
    user = request.get("user")
    return web.json_response({"user": user.id if user else None})


async def test_web_auth():
    admin, member, raw = users.first_admin(), STATE["member"], STATE["raw"]
    app = web.Application()
    setup_auth(app)
    app.router.add_get("/api/whoami", whoami)
    app.router.add_post("/webhook", whoami)
    app.router.add_post("/webhook/waha", whoami)
    app.router.add_get("/api/health", whoami)

    async def who(client, path="/api/whoami", method="get", **kw):
        r = await getattr(client, method)(path, **kw)
        return r.status, (await r.json() if r.status == 200 else None)

    async with TestClient(TestServer(app)) as client:
        check("web: env bearer → first admin",
              await who(client, headers={"Authorization": "Bearer env-token"}) == (200, {"user": admin.id}))
        check("web: DB bearer → its user",
              await who(client, headers={"Authorization": f"Bearer {raw}"}) == (200, {"user": member.id}))
        check("web: wrong bearer → 401", (await who(client, headers={"Authorization": "Bearer nope"}))[0] == 401)
        check("web: public path needs nothing, sets no user",
              await who(client, "/api/health") == (200, {"user": None}))
        legacy = users.session_cookie_value("env-token")
        check("web: existing env-token cookie → first admin",
              await who(client, cookies={SESSION_COOKIE: legacy}) == (200, {"user": admin.id}))

    async with TestClient(TestServer(app)) as client:
        r = await client.post("/login", data={"token": raw, "next": "/"}, allow_redirects=False)
        cookie = r.cookies.get(SESSION_COOKIE)
        check("web: /login accepts a DB token", r.status == 302 and cookie is not None
              and cookie.value == users.session_cookie_value(raw))
        check("web: that cookie → its user", await who(client) == (200, {"user": member.id}))

    async with TestClient(TestServer(app)) as client:
        r = await client.post("/login", data={"token": "env-token", "next": "/"}, allow_redirects=False)
        check("web: /login with API_TOKEN sets the same cookie as before",
              r.status == 302 and r.cookies[SESSION_COOKIE].value == users.session_cookie_value("env-token"))
        check("web: env cookie → first admin", await who(client) == (200, {"user": admin.id}))
        r = await client.post("/login", data={"token": "wrong", "next": "/"}, allow_redirects=False)
        check("web: /login refuses an unknown token", r.headers["Location"].startswith("/login?error=1"))

    raw3, tok3 = users.issue_token(member.id, "Tablet")
    async with TestClient(TestServer(app)) as client:
        await client.post("/login", data={"token": raw3, "next": "/"}, allow_redirects=False)
        users.revoke_token(tok3.id)
        check("web: a revoked token's cookie stops working", (await who(client))[0] == 401)

    async with TestClient(TestServer(app)) as client:
        internal = {"Host": "ganam-assistant.ganam-assistant"}
        check("web: internal host on /webhook (Home Assistant's old path) without credentials → 401",
              (await who(client, "/webhook", "post", headers=internal))[0] == 401)
        check("web: internal host on /webhook/waha without credentials → accepted, no user",
              await who(client, "/webhook/waha", "post", headers=internal) == (200, {"user": None}))
        check("web: internal host on an API path without credentials → 401",
              (await who(client, headers=internal))[0] == 401)
        check("web: internal host with a valid token → its user",
              await who(client, "/webhook/waha", "post",
                        headers={**internal, "Authorization": f"Bearer {raw}"}) == (200, {"user": member.id}))
        check("web: public host on /webhook without credentials → 401",
              (await who(client, "/webhook", "post"))[0] == 401)

    os.environ["API_TOKEN"] = ""
    try:
        async with TestClient(TestServer(app)) as client:
            check("web: no API_TOKEN (dev) → every request is the first admin",
                  await who(client) == (200, {"user": admin.id}))
    finally:
        os.environ["API_TOKEN"] = "env-token"


def test_bootstrap_default_name():
    with get_db_connection() as conn:
        conn.execute("DELETE FROM users")
        conn.commit()
    users.invalidate()
    del os.environ["OWNER_NAME"]
    created = users.bootstrap()
    check("bootstrap without OWNER_NAME: today's default name",
          created is not None and created.name == users.DEFAULT_FIRST_USER_NAME)


test_bootstrap()
test_tokens()
test_cookies()
test_telegram()
asyncio.run(test_telegram_middleware())
test_access()
test_cache()
asyncio.run(test_web_auth())
test_bootstrap_default_name()

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
if passed != len(results):
    sys.exit(1)
