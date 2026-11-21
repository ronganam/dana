"""Checks for the compact HA states proxy (GET /api/ha/states). Run: python test_ha_states.py

HA is mocked: integrations.homeassistant.client.cached_states is replaced, so no
network is used. The request is the first admin's (no API_TOKEN), who has Home
Assistant connected (per-user isolation: test_homeassistant_users.py)."""

import asyncio

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("ha_states_test")

import os

from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # throwaway: the test connection's token is encrypted

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from integrations.homeassistant import api as homeassistant_api
from core import db, users
from core.auth import setup_auth
from integrations.framework import store
from integrations.homeassistant import client as ha_client

db.migrate()
users.bootstrap()
store.connect(users.first_admin(), "homeassistant", credentials={"host": "http://ha.invalid", "token": "t"})

failures = 0


def check(name: str, cond: bool) -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    failures += 0 if cond else 1


STATES = [
    {"entity_id": "light.kitchen", "state": "on", "last_changed": "t1",
     "attributes": {"friendly_name": "Kitchen", "brightness": 200, "entity_picture": "/x.png",
                    "effect_list": ["a"] * 50}},
    {"entity_id": "climate.main_ac", "state": "cool", "last_changed": "t2",
     "attributes": {"friendly_name": "Main AC", "temperature": 24, "current_temperature": 26.5,
                    "min_temp": 16, "max_temp": 30, "hvac_modes": ["off", "cool", "heat"]}},
    {"entity_id": "light.attic", "state": "off", "last_changed": "t3",
     "attributes": {"friendly_name": "attic"}},
    {"entity_id": "sensor.temp", "state": "21", "last_changed": "t4",
     "attributes": {"friendly_name": "Temp", "unit_of_measurement": "°C"}},
]


def unit_checks() -> None:
    out = homeassistant_api.compact_ha_states(STATES, ids=["climate.main_ac", "light.kitchen", "switch.gone"])
    check("ids keep request order", [s["entity_id"] for s in out] == ["climate.main_ac", "light.kitchen", "switch.gone"])
    check("climate keeps tile attrs", out[0]["attributes"].get("temperature") == 24
          and out[0]["attributes"].get("hvac_modes") == ["off", "cool", "heat"])
    check("noise attrs dropped", "entity_picture" not in out[1]["attributes"]
          and "effect_list" not in out[1]["attributes"])
    check("friendly name + domain", out[1]["name"] == "Kitchen" and out[1]["domain"] == "light")
    check("unknown id -> unavailable", out[2]["state"] == "unavailable" and out[2]["domain"] == "switch")

    out = homeassistant_api.compact_ha_states(STATES, domains=["light"])
    check("domains filter, sorted by name", [s["entity_id"] for s in out] == ["light.attic", "light.kitchen"])


async def api_checks() -> None:
    async def fake_fetch(conn):
        return STATES

    ha_client.cached_states = fake_fetch
    app = web.Application()
    setup_auth(app)
    app.router.add_get("/api/ha/states", homeassistant_api.ha_states_api)
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/api/ha/states")
        check("no ids/domains -> 400", r.status == 400)

        r = await client.get("/api/ha/states?ids=light.kitchen, sensor.temp")
        body = await r.json()
        check("ids query -> 200 compact list", r.status == 200 and [s["entity_id"] for s in body] == ["light.kitchen", "sensor.temp"])
        check("unit kept", body[1]["attributes"].get("unit_of_measurement") == "°C")

        r = await client.get("/api/ha/states?domains=climate,sensor")
        body = await r.json()
        check("domains query", [s["entity_id"] for s in body] == ["climate.main_ac", "sensor.temp"])

        async def broken(conn):
            raise RuntimeError("HA returned 500")

        ha_client.cached_states = broken
        r = await client.get("/api/ha/states?ids=light.kitchen")
        check("upstream failure -> 502", r.status == 502)


if __name__ == "__main__":
    unit_checks()
    asyncio.run(api_checks())
    print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
    raise SystemExit(1 if failures else 0)
