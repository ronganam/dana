"""Checks for zones: core/zones.py, core/zones_api.py, core/places.py.
Run: bin/test test_zones

Two users with zones and one without Home Assistant or any zone: CRUD over HTTP works without Home Assistant, each
user sees and changes only their own (another user's id answers 404), names are unique per user in any case, one
Home per user, field checks (400 with the field), ``GET /api/zones`` keeps the shape the reminder editors read (a
list of objects with ``name`` and ``entity_id``, ``zone.home`` for Home: the web's tasks-core.js and the app's
Zones.kt), PUT /api/zones/home, the importer's skip rules, the place search through a fake geocoder (and [] when it
is off or fails), the home zone Now uses. Temp DB, no network beyond localhost.
"""

import asyncio
import os
from typing import Any, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("zones_test")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import addons  # noqa: F401
from core import db, now as now_mod, places, users, zones, zones_api
from core.auth import setup_auth

db.migrate()
users.bootstrap()
ADMIN = users.first_admin()
assert ADMIN is not None
GAL = users.create("Gal")
MAYA = users.create("Maya")  # no Home Assistant, no zones
AUTH = {u.id: {"Authorization": f"Bearer {users.issue_token(u.id, 'test')[0]}"} for u in (ADMIN, GAL, MAYA)}

results: List[Tuple[str, bool]] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    zones_api.setup(app)
    return app


async def fake_geocoder(request: web.Request) -> web.Response:
    q = request.query.get("name", "")
    if q == "boom":
        return web.json_response({"error": True}, status=500)
    return web.json_response({"results": [
        {"name": "Lisbon", "latitude": 38.72, "longitude": -9.14, "country_code": "PT", "country": "Portugal",
         "admin1": "Lisbon", "timezone": "Europe/Lisbon"},
        {"name": "Broken"},  # no coordinates: left out
    ]} if q == "Lisbon" else {})


async def test_http() -> None:
    async with TestClient(TestServer(build_app())) as c:
        r = await c.get("/api/zones", headers=AUTH[MAYA.id])
        check("no zones yet: an empty list (Maya has no Home Assistant)", r.status == 200 and await r.json() == [])

        r = await c.post("/api/zones", headers=AUTH[GAL.id],
                         json={"name": "Home", "latitude": 32.08, "longitude": 34.78, "is_home": True})
        home = (await r.json()).get("zone", {})
        r2 = await c.post("/api/zones", headers=AUTH[GAL.id],
                          json={"name": " Gym ", "latitude": "32.1", "longitude": 34.8, "radius_m": 250})
        gym = (await r2.json()).get("zone", {})
        check("create: 201 with the zone (name trimmed, numbers parsed, default radius 100)",
              r.status == 201 and r2.status == 201 and home["radius_m"] == 100 and home["is_home"]
              and gym["name"] == "Gym" and gym["latitude"] == 32.1 and gym["radius_m"] == 250, (home, gym))

        r = await c.get("/api/zones", headers=AUTH[GAL.id])
        body = await r.json()
        check("GET /api/zones: the shape the reminder editors read (name, entity_id; zone.home for Home)",
              isinstance(body, list) and [z["name"] for z in body] == ["Home", "Gym"]
              and [z["entity_id"] for z in body] == ["zone.home", "zone.gym"]
              and all(isinstance(z["name"], str) and isinstance(z["entity_id"], str) for z in body)
              and list(body[0])[:2] == ["name", "entity_id"] and "user_id" not in body[0], body)

        r = await c.post("/api/zones", headers=AUTH[GAL.id], json={"name": "gym", "latitude": 1, "longitude": 1})
        check("a name she already has (any case) → 400 field name",
              r.status == 400 and (await r.json()).get("field") == "name")
        for bad, field in (({"name": "", "latitude": 1, "longitude": 1}, "name"),
                           ({"name": "X", "latitude": 91, "longitude": 1}, "latitude"),
                           ({"name": "X", "latitude": 1, "longitude": "east"}, "longitude"),
                           ({"name": "X", "latitude": 1, "longitude": 1, "radius_m": 5}, "radius_m"),
                           ({"name": "X", "latitude": True, "longitude": 1}, "latitude")):
            r = await c.post("/api/zones", headers=AUTH[GAL.id], json=bad)
            check(f"a bad {field} → 400 with the field", r.status == 400 and (await r.json()).get("field") == field)
        r = await c.post("/api/zones", headers=AUTH[GAL.id], data="nope")
        check("not JSON → 400", r.status == 400)

        r = await c.post("/api/zones", headers=AUTH[ADMIN.id], json={"name": "Gym", "latitude": 1, "longitude": 2})
        check("the same name for another user is fine", r.status == 201)

        # Isolation both ways.
        for method, path in (("PATCH", f"/api/zones/{gym['id']}"), ("DELETE", f"/api/zones/{gym['id']}")):
            r = await c.request(method, path, headers=AUTH[MAYA.id], json={"name": "Mine"})
            check(f"{method} another user's zone → 404 like an unknown one",
                  r.status == 404 and await r.json() == {"error": "not_found"})
        r = await c.get("/api/zones", headers=AUTH[MAYA.id])
        check("Maya still sees none of Gal's", await r.json() == [])
        r = await c.get("/api/zones", headers=AUTH[ADMIN.id])
        check("the admin sees only his", [z["name"] for z in await r.json()] == ["Gym"])
        check("Gal's Gym untouched", zones.get(GAL, gym["id"]).name == "Gym")

        r = await c.patch(f"/api/zones/{gym['id']}", headers=AUTH[GAL.id], json={"name": "Gym West", "radius_m": 80})
        body = await r.json()
        check("PATCH: renamed and resized", r.status == 200 and body["zone"]["name"] == "Gym West"
              and body["zone"]["radius_m"] == 80 and body["zone"]["entity_id"] == "zone.gym_west", body)
        r = await c.patch(f"/api/zones/{gym['id']}", headers=AUTH[GAL.id], json={"name": "HOME"})
        check("PATCH to a name she has → 400", r.status == 400)
        r = await c.patch(f"/api/zones/{gym['id']}", headers=AUTH[GAL.id], json={"is_home": True})
        mine = {z.name: z for z in zones.list_zones(GAL)}
        check("one Home per user: making Gym West Home unmarks the old one",
              r.status == 200 and mine["Gym West"].is_home and not mine["Home"].is_home, mine)
        await c.patch(f"/api/zones/{home['id']}", headers=AUTH[GAL.id], json={"is_home": True})

        r = await c.put("/api/zones/home", headers=AUTH[MAYA.id], json={"latitude": 40.1, "longitude": -74.0})
        body = await r.json()
        check("PUT /api/zones/home without any zone: a Home zone is made",
              r.status == 200 and body["zone"]["name"] == "Home" and body["zone"]["is_home"], body)
        r = await c.put("/api/zones/home", headers=AUTH[MAYA.id], json={"latitude": 40.5, "longitude": -74.5})
        body = await r.json()
        check("PUT /api/zones/home again: the same zone moves", body["zone"]["id"] == zones.home(MAYA).id
              and zones.home(MAYA).latitude == 40.5 and len(zones.list_zones(MAYA)) == 1, body)

        r = await c.delete(f"/api/zones/{gym['id']}", headers=AUTH[GAL.id])
        check("DELETE: gone", r.status == 200 and zones.get(GAL, gym["id"]) is None)
        r = await c.delete(f"/api/zones/{gym['id']}", headers=AUTH[GAL.id])
        check("DELETE again → 404", r.status == 404)

        # The place search through a fake geocoder.
        geo = web.Application()
        geo.router.add_get("/search", fake_geocoder)
        async with TestClient(TestServer(geo)) as g:
            os.environ["GEOCODING_URL"] = str(g.make_url("/search"))
            r = await c.get("/api/places?q=Lisbon", headers=AUTH[GAL.id])
            body = await r.json()
            check("places: the geocoder's results with timezone and currency (a broken one left out)",
                  body == {"places": [{"name": "Lisbon", "label": "Lisbon, Lisbon, Portugal", "latitude": 38.72,
                                       "longitude": -9.14, "timezone": "Europe/Lisbon", "country_code": "PT",
                                       "currency": "EUR"}]}, body)
            check("places: a failing geocoder → []", await places.search("boom") == [])
            check("places: under two characters → []", await places.search("L") == [])
        os.environ["GEOCODING_URL"] = ""
        check("places: turned off (GEOCODING_URL empty) → []", await places.search("Lisbon") == [])
        os.environ["GEOCODING_URL"] = "http://127.0.0.1:9/nothing"
        check("places: unreachable → []", await places.search("Lisbon") == [])
        os.environ.pop("GEOCODING_URL")
        r = await c.get("/api/zones")
        check("no credentials → 401", r.status == 401)


def test_module() -> None:
    added, skipped = zones.import_zones(GAL, [
        {"name": "Home", "latitude": 1, "longitude": 1, "is_home": True},  # she has one: skipped
        {"name": "Office", "latitude": 32.0, "longitude": 34.7, "radius_m": 150, "is_home": False},
        {"name": "Bad", "latitude": 200, "longitude": 1},
        {"name": "", "latitude": 1, "longitude": 1},
    ])
    check("import: new names added, her own kept, bad ones skipped",
          [z.name for z in added] == ["Office"] and skipped == ["Home", "Bad", ""]
          and zones.home(GAL).latitude == 32.08, (added, skipped))
    added, _ = zones.import_zones(MAYA, [{"name": "Parents", "latitude": 1, "longitude": 1, "is_home": True}])
    check("import: an item marked home does not take over an existing Home", not added[0].is_home)
    check("find: any case, outer spaces", zones.find(GAL, "  office ").name == "Office")
    check("find: never another user's", zones.find(MAYA, "Office") is None)
    with users.acting_as(GAL):
        check("Now: her Home zone, lower case", now_mod.home_zone() == "home" and now_mod.has_home(GAL))
    check("Now: no Home zone → has_home false", not now_mod.has_home(ADMIN))
    check("currency of a country", places.currency_of("pt") == "EUR" and places.currency_of("XX") == "")


async def main() -> None:
    await test_http()
    test_module()


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
raise SystemExit(1 if failed else 0)
