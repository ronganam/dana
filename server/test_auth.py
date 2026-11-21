"""Checks for the web-server auth middleware (core/auth.py). Run: python test_auth.py"""

import asyncio
import os

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("auth_test")
os.environ["API_TOKEN"] = "s3cret-token"
os.environ["INTERNAL_HOSTS"] = "ganam-assistant.ganam-assistant,ganam-assistant.ganam-assistant.svc.cluster.local"

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

from core import db, users
from core.auth import SESSION_COOKIE, setup_auth

db.migrate()
users.bootstrap()

failures = 0


def check(name: str, cond: bool) -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    failures += 0 if cond else 1


async def ok(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def main() -> None:
    app = web.Application()
    setup_auth(app)
    for path in ("/", "/dashboard", "/api/tasks", "/api/health", "/api/app/version", "/webhook", "/media/x.mp4"):
        app.router.add_get(path, ok)
    app.router.add_post("/webhook", ok)
    app.router.add_post("/webhook/waha", ok)

    async with TestClient(TestServer(app)) as client:
        r = await client.get("/api/tasks")
        check("API without credentials -> 401", r.status == 401)

        r = await client.get("/dashboard?zoom=220&a=b", allow_redirects=False)
        location = URL(r.headers.get("Location", ""))
        check("page without credentials -> login redirect keeps target",
              r.status == 302 and location.path == "/login" and location.query.get("next") == "/dashboard?zoom=220&a=b")

        r = await client.get("/media/x.mp4", allow_redirects=False)
        check("media without credentials -> 401", r.status == 401)

        r = await client.get("/api/tasks", headers={"Authorization": "Bearer wrong"})
        check("wrong bearer -> 401", r.status == 401)

        r = await client.get("/api/tasks", headers={"Authorization": "Bearer s3cret-token"})
        check("correct bearer -> 200", r.status == 200)

        for path in ("/api/health", "/api/app/version"):
            r = await client.get(path)
            check(f"public {path} -> 200", r.status == 200)

        r = await client.get("/api/tasks", headers={"Host": "ganam-assistant.ganam-assistant"})
        check("internal Service host without credentials on an API path -> 401", r.status == 401)

        r = await client.post("/webhook", headers={"Host": "ganam-assistant.ganam-assistant"})
        check("internal Service host on /webhook (the old HA path, not inbound any more) -> 401", r.status == 401)

        r = await client.post("/webhook/waha", headers={"Host": "ganam-assistant.ganam-assistant.svc.cluster.local:80"})
        check("internal Service host with port on /webhook/waha (WAHA) -> 200", r.status == 200)

        r = await client.post("/webhook", headers={"Host": "dana.example.com"})
        check("webhook via public host without token -> 401", r.status == 401)

        r = await client.post("/login", data={"token": "nope", "next": "/dashboard"}, allow_redirects=False)
        check("bad login -> back to login with error",
              r.status == 302 and r.headers["Location"].startswith("/login?error=1"))

        r = await client.post("/login", data={"token": "s3cret-token", "next": "//evil.example"}, allow_redirects=False)
        check("open redirect blocked", r.status == 302 and r.headers["Location"] == "/")

        r = await client.post("/login", data={"token": "s3cret-token", "next": "/dashboard"}, allow_redirects=False)
        cookie = r.cookies.get(SESSION_COOKIE)
        check("good login -> redirect + HttpOnly session cookie",
              r.status == 302 and r.headers["Location"] == "/dashboard"
              and cookie is not None and cookie["httponly"] and cookie.value != "s3cret-token")

        r = await client.get("/api/tasks")  # client keeps the cookie
        check("session cookie authenticates API calls", r.status == 200)


asyncio.run(main())
if failures:
    raise SystemExit(f"{failures} auth check(s) failed")
print("\nAll auth checks passed")
