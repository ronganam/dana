"""Checks for the Home Assistant home map in the agent prompt. Run: bin/test test_home_map

HA is mocked: addons.homeassistant.client.fetch_registry / fetch_all_states are
replaced, so no network is used. The agent's LLM call is faked at
litellm.acompletion to capture the system message it sends. The map belongs to
the first admin's connection (per-user isolation: test_homeassistant_users.py)."""

import asyncio
import os
import sys
import time

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("home_map_test")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # throwaway: the test connection's token is encrypted

import litellm
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices

from core import db, users
from core.agent import AgentOrchestrator
from core.addons import store
from addons.homeassistant.addon import addon as homeassistant_addon
from addons.homeassistant import client as ha_client, home_map as ha

db.migrate()  # the agent reads the About section from the database
users.bootstrap()
ADMIN = users.first_admin()
store.connect(ADMIN, "homeassistant", credentials={"host": "http://ha.invalid", "token": "test-token"})
CONN = store.require(ADMIN, "homeassistant")

failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


# --- Fake HA ------------------------------------------------------------------

EXPOSED = {"cover.gate", "light.kitchen", "climate.main_ac", "switch.boiler"}
REGISTRY = {
    "cover.gate": {"name": "Gate", "aliases": ["the gate", "שער"]},
    "light.kitchen": {"name": "Kitchen Light", "aliases": []},
    "climate.main_ac": {"name": "Main AC", "aliases": ["AC", "מזגן"]},
    # Registry has no name: the friendly name from the states is used.
    "switch.boiler": {"name": "switch.boiler", "aliases": []},
}
STATES = {
    "cover.gate": {"entity_id": "cover.gate", "state": "closed", "attributes": {"current_position": 37}},
    "light.kitchen": {"entity_id": "light.kitchen", "state": "on", "attributes": {"brightness": 200}},
    "climate.main_ac": {"entity_id": "climate.main_ac", "state": "cool",
                        "attributes": {"temperature": 24, "current_temperature": 26.5}},
    "switch.boiler": {"entity_id": "switch.boiler", "state": "off",
                      "attributes": {"friendly_name": "Boiler"}},
}


class FakeHA:
    def __init__(self):
        self.delay = 0.0
        self.fail = False
        self.calls = 0
        self.exposed = set(EXPOSED)

    async def registry(self, conn):
        assert conn.credentials["token"] == "test-token"
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionRefusedError("HA unreachable")
        return set(self.exposed), {k: v for k, v in REGISTRY.items() if k in self.exposed}

    async def states(self, conn):
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ConnectionRefusedError("HA unreachable")
        return STATES


fake_ha = FakeHA()
ha_client.fetch_registry = fake_ha.registry
ha_client.fetch_all_states = fake_ha.states


def cache() -> ha.HomeMapCache:
    return ha.cache_for(CONN)


def reset_cache() -> None:
    ha._caches.clear()
    fake_ha.delay, fake_ha.fail, fake_ha.calls, fake_ha.exposed = 0.0, False, 0, set(EXPOSED)


# --- Fake LLM -----------------------------------------------------------------

captured = []


async def fake_acompletion(**kwargs):
    captured.append(kwargs)

    async def gen():
        yield ModelResponseStream(model="fake", choices=[StreamingChoices(
            index=0, delta=Delta(content="Done."), finish_reason="stop")])
    return gen()


litellm.acompletion = fake_acompletion


# --- Tests --------------------------------------------------------------------

def test_format() -> None:
    text = ha.format_home_map(EXPOSED, REGISTRY, STATES)
    check("format: ids present", all(eid in text for eid in EXPOSED))
    check("format: names present", all(n in text for n in ("Gate", "Kitchen Light", "Main AC")))
    check("format: aliases present", "aliases: the gate, שער" in text and "aliases: AC, מזגן" in text)
    check("format: friendly name fills a missing registry name", "switch.boiler | Boiler" in text, text)
    check("format: grouped by domain, sorted",
          text.index("[climate]") < text.index("[cover]") < text.index("[light]") < text.index("[switch]"))
    no_state = not any(s in text for s in ("closed", "cool", " on", " off", "37", "24", "26.5", "brightness"))
    check("format: no live state or attributes", no_state, text)
    check("format: stable output for the same input", text == ha.format_home_map(set(EXPOSED), REGISTRY, STATES))
    check("format: empty exposure -> empty map", ha.format_home_map(set(), {}) == "")


async def test_cold_cache() -> None:
    reset_cache()
    fake_ha.delay = 0.5
    started = time.monotonic()
    first = ha.get_home_map(CONN)
    elapsed = time.monotonic() - started
    check("cold: returns nothing", first == "")
    check("cold: does not wait on HA", elapsed < 0.05, f"{elapsed:.3f}s")
    check("cold: a background refresh was started", cache().task is not None and not cache().task.done())
    ha.get_home_map(CONN)
    await cache().task
    check("cold: only one refresh in flight", fake_ha.calls == 1, str(fake_ha.calls))
    check("cold: map is cached once the refresh finishes", "cover.gate | Gate" in ha.get_home_map(CONN))


async def test_stale_cache() -> None:
    reset_cache()
    await ha.refresh_home_map(CONN)
    old = ha.get_home_map(CONN)
    calls_before = fake_ha.calls
    check("fresh: no refresh while within the TTL", ha.get_home_map(CONN) == old and fake_ha.calls == calls_before)

    cache().next_refresh = time.monotonic() - 1  # TTL passed
    fake_ha.exposed = EXPOSED - {"light.kitchen"}
    fake_ha.delay = 0.3
    started = time.monotonic()
    got = ha.get_home_map(CONN)
    elapsed = time.monotonic() - started
    check("stale: old map returned at once", got == old and elapsed < 0.05, f"{elapsed:.3f}s")
    task = cache().task
    check("stale: refresh runs in the background", task is not None and not task.done())
    await task
    new = ha.get_home_map(CONN)
    check("stale: refreshed map replaces the old one", "light.kitchen" not in new and "cover.gate" in new)
    check("stale: next refresh scheduled a TTL ahead",
          cache().next_refresh - time.monotonic() > ha.HOME_MAP_TTL_SECONDS - 5)


async def test_failed_refresh() -> None:
    reset_cache()
    await ha.refresh_home_map(CONN)
    old = ha.get_home_map(CONN)
    fake_ha.fail = True
    cache().next_refresh = time.monotonic() - 1
    ha.get_home_map(CONN)
    try:
        result = await cache().task
        raised = False
    except Exception:
        result, raised = None, True
    check("failure: refresh does not raise", not raised)
    check("failure: old map kept", result == old and ha.get_home_map(CONN) == old)
    wait = cache().next_refresh - time.monotonic()
    check("failure: retried sooner than the TTL", 0 < wait <= ha.HOME_MAP_RETRY_SECONDS, f"{wait:.1f}s")

    # States failing alone still gives a map (names from the registry).
    reset_cache()
    async def broken_states(conn):
        raise RuntimeError("states 500")
    ha_client.fetch_all_states = broken_states
    try:
        text = await ha.refresh_home_map(CONN)
    finally:
        ha_client.fetch_all_states = fake_ha.states
    check("failure: map built without states", "cover.gate | Gate" in text and "switch.boiler | switch.boiler" in text)

    # HA hanging past the timeout counts as a failure.
    reset_cache()
    cache().text = old
    fake_ha.delay = 5
    saved_timeout = ha.HOME_MAP_FETCH_TIMEOUT
    ha.HOME_MAP_FETCH_TIMEOUT = 0.1
    try:
        text = await ha.refresh_home_map(CONN)
    finally:
        ha.HOME_MAP_FETCH_TIMEOUT = saved_timeout
    check("failure: a hung fetch times out and keeps the old map", text == old)


async def test_no_kind() -> None:
    reset_cache()
    store.disconnect(ADMIN, "homeassistant")
    try:
        check("no addon: empty map, nothing scheduled, no prompt section",
              ha.cached_text(ADMIN) == "" and not ha._caches and ha.warm_all() == []
              and ha.prompt_section(ADMIN) == "")
    finally:
        store.connect(ADMIN, "homeassistant", credentials={"host": "http://ha.invalid", "token": "test-token"})


async def test_warm() -> None:
    reset_cache()
    tasks = ha.warm_all()
    check("warm: one background task per user with Home Assistant", len(tasks) == 1)
    await tasks[0]
    check("warm: cache filled", "climate.main_ac | Main AC" in ha.get_home_map(CONN))

    reset_cache()
    fake_ha.fail = True
    task = ha.warm_home_map(CONN)
    await task
    check("warm: HA down leaves the cache empty without raising", task.exception() is None and cache().text == "")


async def system_message(agent: AgentOrchestrator, **kwargs) -> str:
    captured.clear()
    with users.acting_as(ADMIN):
        await agent.run("open the gate", [{"role": "user", "content": "open the gate"}], **kwargs)
    return captured[-1]["messages"][0]["content"]


async def test_agent_prompt() -> None:
    agent = AgentOrchestrator(model="gemini/server-default")
    import core.agent as agent_mod
    agent_mod.clock.describe_now = lambda: "Saturday, 14 November 2026, 10:00"
    instructions = agent.instructions_for(users.first_admin())
    baseline = (instructions
                + "\n\nActive Context:\n- Current Date and Time: Saturday, 14 November 2026, 10:00")

    reset_cache()
    fake_ha.delay = 0.2
    cold = await system_message(agent)
    check("agent: cold cache -> system message unchanged from before", cold == baseline)
    await cache().task

    warm = await system_message(agent)
    check("agent: cached map is in the system message",
          "HOME DEVICES" in warm and "cover.gate | Gate  aliases: the gate, שער" in warm)
    check("agent: map sits before the date/time line",
          warm.index("HOME DEVICES") < warm.index("Active Context:"))
    check("agent: map comes after the guidelines (stable prefix kept)",
          warm.startswith(instructions))
    check("agent: instruction says to use the ids directly",
          "use them directly with call_ha_service" in warm)

    extra = await system_message(agent, extra_context="Opened from a call suggestion.")
    check("agent: extra_context still last",
          extra.endswith("Current Date and Time: Saturday, 14 November 2026, 10:00\n\nOpened from a call suggestion."))

    # Without Home Assistant connected: no map, and the instructions drop the smart-home parts.
    store.disconnect(ADMIN, "homeassistant")
    try:
        without = await system_message(agent)
        check("agent: no HA connected -> no map, no Home Assistant in the instructions",
              without == agent.instructions_for(ADMIN) + "\n\nActive Context:\n- Current Date and Time: "
              "Saturday, 14 November 2026, 10:00"
              and "HOME DEVICES" not in without and "Home Assistant" not in without
              and "HomeAssistant" not in without, without[:300])
    finally:
        store.connect(ADMIN, "homeassistant", credentials={"host": "http://ha.invalid", "token": "test-token"})


def test_docstrings() -> None:
    descs = {t.name: t.schema["function"]["description"] for t in homeassistant_addon.tools}
    check("docs: discovery no longer forced",
          "FIRST" not in descs["get_ha_exposed_entities"]
          and "MUST call get_ha_exposed_entities" not in descs["call_ha_service"])
    check("docs: both tools point at the Home Devices list",
          "Home Devices" in descs["get_ha_exposed_entities"] and "Home Devices" in descs["call_ha_service"])


async def main() -> None:
    test_format()
    await test_cold_cache()
    await test_stale_cache()
    await test_failed_refresh()
    await test_no_kind()
    await test_warm()
    await test_agent_prompt()
    test_docstrings()


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
    raise SystemExit(1 if failures else 0)
