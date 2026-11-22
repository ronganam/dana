"""Checks for the first-run wizard's state: core/wizard.py, core/wizard_api.py (and the You step's profile fields).
Run: bin/test test_wizard

A new user's wizard is pending; steps are marked done / skipped per user and the wizard resumes at the next one;
finishing; the model step only for ``manage_server`` (a member gets 400 ``unavailable``), the sample step only when
the server has ``POST /api/demo``; isolation between two users; the Home step: the Home zone, the server's timezone
from an admin only (and not while ``TIMEZONE`` in env wins), the currency only when the user has none; PUT
/api/profile name and pronoun. Temp DB, no network.
"""

import asyncio
import os
from typing import Any, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("wizard_test")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import addons  # noqa: F401
from core import clock, db, profile_api, server_settings, users, wizard, wizard_api, zones
from core.auth import setup_auth

db.migrate()
users.bootstrap()
ADMIN = users.first_admin()
assert ADMIN is not None
GAL = users.create("Gal")
MAYA = users.create("Maya")
AUTH = {u.id: {"Authorization": f"Bearer {users.issue_token(u.id, 'test')[0]}"} for u in (ADMIN, GAL, MAYA)}

results: List[Tuple[str, bool]] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def build_app(demo: bool) -> web.Application:
    app = web.Application()
    setup_auth(app)
    wizard_api.setup(app)
    profile_api.setup(app)

    async def fake_demo(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})
    if demo:
        app.router.add_post("/api/demo", fake_demo)
    return app


def by_id(state: dict) -> dict:
    return {s["id"]: s for s in state["steps"]}


async def test_steps() -> None:
    async with TestClient(TestServer(build_app(demo=False))) as c:
        r = await c.get("/api/wizard", headers=AUTH[GAL.id])
        st = await r.json()
        steps = by_id(st)
        check("a new user: every step pending, not finished, starts at You",
              r.status == 200 and not st["finished"] and st["next"] == "you"
              and all(s["status"] == "pending" for s in st["steps"]) and [s["id"] for s in st["steps"]] == list(wizard.STEPS), st)
        check("a member: the model step is not for her; no demo endpoint: no sample step",
              not steps["model"]["available"] and not steps["sample"]["available"] and st["demo"] is False, steps)
        r = await c.get("/api/wizard", headers=AUTH[ADMIN.id])
        check("the admin: the model step is his", by_id(await r.json())["model"]["available"])

        r = await c.post("/api/wizard/steps/you", headers=AUTH[GAL.id], json={"status": "done"})
        r2 = await c.post("/api/wizard/steps/home", headers=AUTH[GAL.id], json={"status": "skipped"})
        st = await r2.json()
        check("marking: done and skipped kept, the next pending available step follows (model skipped over)",
              r.status == 200 and by_id(st)["you"]["status"] == "done" and by_id(st)["home"]["status"] == "skipped"
              and st["next"] == "phone", st)
        r = await c.get("/api/wizard", headers=AUTH[GAL.id])
        check("resumable: a new request picks up at phone", (await r.json())["next"] == "phone")
        r = await c.get("/api/wizard", headers=AUTH[MAYA.id])
        check("isolation: Maya's wizard is untouched by Gal's", (await r.json())["next"] == "you")

        r = await c.post("/api/wizard/steps/model", headers=AUTH[GAL.id], json={"status": "done"})
        check("a member marking the model step → 400 unavailable", r.status == 400
              and (await r.json())["error"] == "unavailable")
        r = await c.post("/api/wizard/steps/sample", headers=AUTH[GAL.id], json={"status": "done"})
        check("the sample step without the demo endpoint → 400", r.status == 400)
        r = await c.post("/api/wizard/steps/nope", headers=AUTH[GAL.id], json={"status": "done"})
        check("an unknown step → 404", r.status == 404)
        r = await c.post("/api/wizard/steps/phone", headers=AUTH[GAL.id], json={"status": "maybe"})
        check("a bad status → 400", r.status == 400 and (await r.json())["field"] == "status")

        r = await c.post("/api/wizard/finish", headers=AUTH[GAL.id])
        st = await r.json()
        check("finish: finished, the steps she did not reach stay pending",
              st["finished"] and st["finished_at"] and by_id(st)["phone"]["status"] == "pending", st)
        r = await c.get("/api/wizard", headers=AUTH[MAYA.id])
        check("isolation: Maya's still not finished", not (await r.json())["finished"])
        r = await c.get("/api/wizard")
        check("no credentials → 401", r.status == 401)

    async with TestClient(TestServer(build_app(demo=True))) as c:
        r = await c.get("/api/wizard", headers=AUTH[MAYA.id])
        st = await r.json()
        check("with POST /api/demo on the server: the sample step is offered", st["demo"] and by_id(st)["sample"]["available"])
        r = await c.post("/api/wizard/steps/sample", headers=AUTH[MAYA.id], json={"status": "done"})
        check("the sample step can be marked then", r.status == 200)


async def test_home_and_profile() -> None:
    async with TestClient(TestServer(build_app(demo=False))) as c:
        r = await c.put("/api/wizard/home", headers=AUTH[GAL.id],
                        json={"latitude": 38.72, "longitude": -9.14, "timezone": "Europe/Lisbon", "currency": "eur"})
        body = await r.json()
        check("a member's Home: her Home zone, her currency (she had none), the server's timezone unchanged",
              r.status == 200 and zones.home(GAL) is not None and zones.home(GAL).latitude == 38.72
              and body["currency"] == "EUR" and users.get(GAL.id).currency == "EUR"
              and body["timezone"]["applied"] is False and server_settings.get("timezone") is None
              and by_id(body["state"])["home"]["status"] == "done", body)
        check("the admin has no Home zone from that", zones.home(ADMIN) is None)

        r = await c.put("/api/wizard/home", headers=AUTH[ADMIN.id],
                        json={"latitude": 52.37, "longitude": 4.9, "timezone": "Europe/Amsterdam", "currency": "EUR"})
        body = await r.json()
        check("the admin's Home: the server's timezone follows at once; his currency (ILS) kept",
              r.status == 200 and body["timezone"] == {"value": "Europe/Amsterdam", "applied": True}
              and str(clock.get_timezone()) == "Europe/Amsterdam" and users.get(ADMIN.id).currency == "ILS", body)
        os.environ["TIMEZONE"] = "Asia/Tokyo"
        clock._timezone = None
        r = await c.put("/api/wizard/home", headers=AUTH[ADMIN.id],
                        json={"latitude": 52.37, "longitude": 4.9, "timezone": "Europe/Paris"})
        body = await r.json()
        check("TIMEZONE in env wins: stored, not applied", body["timezone"] == {"value": "Asia/Tokyo", "applied": False}
              and server_settings.get("timezone") == "Europe/Paris", body)
        os.environ.pop("TIMEZONE")
        clock._timezone = None
        r = await c.put("/api/wizard/home", headers=AUTH[ADMIN.id],
                        json={"latitude": 1, "longitude": 1, "timezone": "Mars/Olympus"})
        check("a bad timezone → 400, nothing moved", r.status == 400 and (await r.json())["field"] == "timezone"
              and zones.home(ADMIN).latitude == 52.37)
        r = await c.put("/api/wizard/home", headers=AUTH[MAYA.id], json={"latitude": 100, "longitude": 1})
        check("a bad latitude → 400 with the field", r.status == 400 and (await r.json())["field"] == "latitude")

        r = await c.put("/api/profile", headers=AUTH[MAYA.id], json={"name": " Maya Cohen ", "pronoun": "she",
                                                                      "languages": "fr, en"})
        body = await r.json()
        check("You: name, pronoun and languages saved", r.status == 200 and body["name"] == "Maya Cohen"
              and body["pronoun"] == "she" and body["languages"] == ["fr", "en"]
              and users.get(MAYA.id).pronoun == "she", body)
        r = await c.put("/api/profile", headers=AUTH[MAYA.id], json={"pronoun": "it"})
        check("an unknown pronoun → 400", r.status == 400 and (await r.json())["field"] == "pronoun")
        r = await c.put("/api/profile", headers=AUTH[MAYA.id], json={"name": "  "})
        check("an empty name → 400, the name kept", r.status == 400 and users.get(MAYA.id).name == "Maya Cohen")
        check("isolation: Gal's profile untouched", users.get(GAL.id).name == "Gal" and users.get(GAL.id).pronoun == "they")


async def main() -> None:
    await test_steps()
    await test_home_and_profile()


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
raise SystemExit(1 if failed else 0)
