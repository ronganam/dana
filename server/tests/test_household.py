"""Checks for the household API (core/household_api.py): who may list, add, change and remove members, the last admin, invites (pairing codes for another member) and demo users kept out.
Run: bin/test test_household

Robin (the first admin), Gal and Maya (members). A member sees only themselves and may change nobody; an admin
adds a member (with their default addons), changes a level and a profile, removes a member (their token stops
working); the last admin keeps admin, an admin cannot remove themselves; an invite for another member is a
phone QR and a browser code only with manage_users. Temp DB, no network.
"""

import asyncio
import os
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("household_test")
os.environ["DANA_ADDON_DIRS"] = ""

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import addons  # noqa: F401  (loads the addons: default addons for new members)
from core import db, demo, devices, devices_api, household_api, users
from core.addons import store
from core.auth import setup_auth

db.migrate()
users.bootstrap()

results: List[Tuple[str, bool]] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    household_api.setup(app)
    devices_api.setup(app)
    return app


def bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def call(c: TestClient, method: str, path: str, token: str, body: Any = None) -> Tuple[int, Any]:
    r = await c.request(method, path, headers=bearer(token), json=body)
    try:
        return r.status, await r.json()
    except Exception:
        return r.status, await r.text()


async def run(c: TestClient) -> None:
    robin = users.first_admin()
    assert robin is not None
    gal = users.create("Gal")
    maya = users.create("Maya")
    t_robin, _ = users.issue_token(robin.id)
    t_gal, _ = users.issue_token(gal.id)
    t_maya, _ = users.issue_token(maya.id)
    _, phone = devices.create(gal, "phone", "Gal's phone")
    await demo.load()

    # --- Listing ---
    st, b = await call(c, "GET", "/api/household", t_maya)
    check("member: lists only themselves, can_manage false",
          st == 200 and [m["id"] for m in b["members"]] == [maya.id] and b["can_manage"] is False
          and b["members"][0]["you"] is True, b)
    st, b = await call(c, "GET", "/api/household", t_robin)
    by_id = {m["id"]: m for m in b.get("members", [])}
    check("admin: every real member, oldest first, can_manage true",
          st == 200 and [m["id"] for m in b["members"]] == [robin.id, gal.id, maya.id] and b["can_manage"], b)
    check("admin: a member's devices are counted, their newest use is the last seen",
          by_id[gal.id]["devices"] == 1 and by_id[maya.id]["devices"] == 0 and by_id[robin.id]["last_seen_at"]
          and by_id[gal.id]["level"] == "member" and by_id[robin.id]["level"] == "admin", by_id)
    check("admin: the demo household's users are not members",
          not any(m["id"] in {u.id for u in users.demo_users()} for m in b["members"]) and len(users.demo_users()) == 2)

    # --- Adding ---
    for token, who in ((t_maya, "member"),):
        st, b = await call(c, "POST", "/api/household/members", token, {"name": "Noa"})
        check(f"{who}: adding a member is refused (403 manage_users)",
              st == 403 and b.get("needs") == "manage_users" and not any(u.name == "Noa" for u in users.list_users()), b)
    st, b = await call(c, "POST", "/api/household/members", t_robin,
                       {"name": "Noa", "pronoun": "she", "languages": "he, en", "level": "member"})
    noa = users.get((b.get("member") or {}).get("id", ""))
    check("admin: adds a member with pronoun, languages and level",
          st == 201 and noa is not None and noa.pronoun == "she" and noa.languages == "he,en" and noa.access == "member"
          and b["member"]["languages"] == ["he", "en"] and b["member"]["devices"] == 0, b)
    check("admin: the new member has their default addons",
          noa is not None and {"tasks", "shopping"} <= set(store.connected_addons(noa)),
          noa and store.connected_addons(noa))
    for bad, field in (({"name": ""}, "name"), ({"name": "X", "pronoun": "it"}, "pronoun"),
                       ({"name": "X", "level": "owner"}, "level"), ({"name": "X", "languages": "english!"}, "languages"),
                       ({"name": 5}, "name")):
        st, b = await call(c, "POST", "/api/household/members", t_robin, bad)
        check(f"admin: a bad {field} is 400 invalid naming it", st == 400 and b.get("field") == field, b)
    st, b = await call(c, "POST", "/api/household/members", t_robin, ["not", "an", "object"])
    check("admin: a body that is not an object is 400", st == 400, b)

    # --- Changing ---
    st, b = await call(c, "PATCH", f"/api/household/members/{gal.id}", t_maya, {"level": "admin"})
    check("member: changing someone's level is refused", st == 403 and users.get(gal.id).access == "member", b)
    st, b = await call(c, "PATCH", f"/api/household/members/{maya.id}", t_maya, {"name": "Maya L"})
    check("member: changing themselves here is refused too (Settings → Profile is theirs)", st == 403, b)
    st, b = await call(c, "PATCH", f"/api/household/members/{gal.id}", t_robin, {"level": "admin", "name": "Gal K"})
    check("admin: makes a member admin and renames them",
          st == 200 and users.get(gal.id).access == "admin" and users.get(gal.id).name == "Gal K"
          and b["member"]["level"] == "admin", b)
    st, b = await call(c, "PATCH", f"/api/household/members/{gal.id}", t_robin, {"level": "member"})
    check("admin: an admin back to member while another admin stays", st == 200 and users.get(gal.id).access == "member", b)
    st, b = await call(c, "PATCH", f"/api/household/members/{robin.id}", t_robin, {"level": "member"})
    check("the last admin keeps admin (409 last_admin)", st == 409 and b.get("error") == "last_admin"
          and users.get(robin.id).access == "admin", b)
    st, b = await call(c, "PATCH", f"/api/household/members/{robin.id}", t_robin, {"pronoun": "she"})
    check("admin: the last admin's profile still changes", st == 200 and users.get(robin.id).pronoun == "she", b)
    demo_id = users.demo_users()[0].id
    st, b = await call(c, "PATCH", f"/api/household/members/{demo_id}", t_robin, {"level": "admin"})
    check("a demo user is not a member here (404)", st == 404 and not users.get(demo_id).is_admin, b)
    st, b = await call(c, "PATCH", "/api/household/members/nobody00000", t_robin, {"name": "X"})
    check("an unknown member is 404", st == 404, b)

    # --- Invites ---
    st, b = await call(c, "POST", "/api/devices/pairings", t_robin, {"kind": "phone", "user_id": maya.id})
    check("invite: an admin starts a phone pairing for another member (a QR with the server and code)",
          st == 201 and b["user"]["id"] == maya.id and b["qr"].startswith("dana://pair?") and len(b["code"]) == 8, b)
    st, b = await call(c, "POST", "/api/devices/pairings", t_robin, {"kind": "browser", "user_id": maya.id})
    check("invite: and a browser sign-in code", st == 201 and b["kind"] == "browser" and b["user"]["id"] == maya.id, b)
    st, b = await call(c, "POST", "/api/devices/pairings", t_maya, {"kind": "phone", "user_id": gal.id})
    check("invite: for another member without manage_users is refused", st == 403 and b.get("needs") == "manage_users", b)

    # --- Removing ---
    st, b = await call(c, "DELETE", f"/api/household/members/{gal.id}", t_maya)
    check("member: removing someone is refused", st == 403 and users.get(gal.id) is not None, b)
    st, b = await call(c, "DELETE", f"/api/household/members/{robin.id}", t_robin)
    check("admin: cannot remove themselves here (409 self)", st == 409 and b.get("error") == "self"
          and users.get(robin.id) is not None, b)
    st, b = await call(c, "DELETE", f"/api/household/members/{gal.id}", t_robin)
    check("admin: removes a member, with their devices", st == 200 and users.get(gal.id) is None
          and devices.get(phone.id) is None, b)
    st, b = await call(c, "GET", "/api/household", t_gal)
    check("a removed member's token stops working", st == 401, b)
    st, b = await call(c, "DELETE", f"/api/household/members/{demo_id}", t_robin)
    check("a demo user cannot be removed here (DELETE /api/demo does)", st == 404 and users.get(demo_id) is not None, b)


async def main() -> None:
    async with TestClient(TestServer(build_app())) as c:
        await run(c)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
