"""Parity guard for the first user's experience. Run: bin/test test_parity

Compares, against fixtures in ``tests/fixtures/parity/``:

- the chat tool list exactly as the model receives it,
- the realtime voice tool list,
- the agent system message (as sent to the model) with a home map and
  About-you facts, and with neither,
- the realtime voice session instructions.

Everything that varies is fixed: the clock, the home map (HA is never called),
the About-you facts and ``OWNER_NAME``. The LLM is faked at
``litellm.acompletion`` so the system message is captured from a real
``AgentOrchestrator.run``.

The fixtures describe what the first (admin) user gets today and must not
change during the multi-user refactor. When the code changes how the list or
the prompt is obtained (per user), change only the four ``obtain_*`` functions
below, never the fixtures. Regenerate the fixtures only on purpose:
``WRITE_PARITY=1 bin/test test_parity``.

Tool order: the chat tool list is compared by content per tool name (strict),
and its order separately (reported, not failed): the fixtures were written in
``os.listdir`` order of the old ``skills/``; the list now follows the fixed
addon order of ``addons/``.

The first user is the first admin with every addon connected: the env
values the startup import reads are set to fake values (nothing is called).
"""

import asyncio
import datetime
import difflib
import json
import os
import sys
import zoneinfo

from cryptography.fernet import Fernet

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("parity_test")
os.environ["OWNER_NAME"] = "Robin"
os.environ["TIMEZONE"] = "Asia/Jerusalem"
# Fake values so the startup import connects every addon for the first admin (never called).
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway test key

import litellm
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices

from core import clock, db, users
from core.agent import AgentOrchestrator
from core.realtime import build_realtime_session_instructions
from core.db import get_db_connection
from addons import bootstrap as addons_bootstrap
from addons.about import facts as about_facts
from core.addons import registry, store
from addons.homeassistant import home_map as ha

FIXTURES = os.path.join(testenv.FIXTURES, "parity")
WRITE = os.getenv("WRITE_PARITY") == "1"

# --- Fixed inputs -----------------------------------------------------------------

TZ = zoneinfo.ZoneInfo("Asia/Jerusalem")
FIXED_NOW = datetime.datetime(2026, 3, 17, 9, 41, 5, tzinfo=TZ)

HOME_EXPOSED = {"cover.gate", "light.kitchen", "climate.main_ac", "switch.boiler"}
HOME_REGISTRY = {
    "cover.gate": {"name": "Gate", "aliases": ["the gate", "שער"]},
    "light.kitchen": {"name": "Kitchen Light", "aliases": []},
    "climate.main_ac": {"name": "Main AC", "aliases": ["AC", "מזגן"]},
    "switch.boiler": {"name": "Boiler", "aliases": []},
}
FACTS = [
    ("Gal is Robin's brother", "people"),
    ("'The gate' means the front gate", "home"),
    ("Robin runs on Tuesday mornings", "habits"),
    ("Robin prefers metric units", "other"),
]

db.migrate()
users.bootstrap()
addons_bootstrap.bootstrap()
testenv.connect_admin("homeassistant", credentials={"host": "http://homeassistant.local:8123",
                                                    "token": "parity-token"})  # never used: the home map is set directly
testenv.connect_admin("whatsapp", credentials={"url": "https://waha.example.com/api/", "api_key": "parity-waha-key",
                                               "session": "default"})
testenv.connect_admin("calendar", credentials={"refresh_token": "parity-refresh-token"})
testenv.connect_admin("ops")  # opt-in, turned on here so the fixtures cover its tools
FIRST_USER = users.first_admin()
clock._timezone = TZ
clock.now = lambda: FIXED_NOW


def set_home_map(enabled: bool) -> None:
    cache = ha.cache_for(store.require(FIRST_USER, "homeassistant"))
    cache.text = ha.format_home_map(HOME_EXPOSED, HOME_REGISTRY, {}) if enabled else ""
    cache.next_refresh = float("inf")  # never refresh: HA is not called


def set_facts(enabled: bool) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM about_facts")
        conn.commit()
    if enabled:
        for text, section in FACTS:
            about_facts.add_fact(FIRST_USER, text, section)
    about_facts.refresh_prompt(FIRST_USER)


# --- Fake LLM ---------------------------------------------------------------------

captured: list = []


async def fake_acompletion(**kwargs):
    captured.append(kwargs)

    async def gen():
        yield ModelResponseStream(model="fake", choices=[StreamingChoices(
            index=0, delta=Delta(content="Done."), finish_reason="stop")])
    return gen()


litellm.acompletion = fake_acompletion
litellm.suppress_debug_info = True


# --- How the first user's list / prompt is obtained (the only part to change) -----

def obtain_chat_tools() -> list:
    return registry.tool_schemas(FIRST_USER)


def obtain_realtime_tools() -> list:
    return registry.realtime_tool_schemas(FIRST_USER)


def obtain_system_message() -> str:
    captured.clear()
    with users.acting_as(FIRST_USER):
        asyncio.run(AgentOrchestrator(model="fake/model").run("hello", [{"role": "user", "content": "hello"}]))
    messages = captured[0]["messages"]
    assert messages[0]["role"] == "system", messages[0]
    return messages[0]["content"]


def obtain_realtime_instructions() -> str:
    return build_realtime_session_instructions(FIRST_USER)


# --- Comparison -------------------------------------------------------------------

failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"\n{detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


def _path(name: str) -> str:
    return os.path.join(FIXTURES, name)


def _diff(expected: str, actual: str, name: str) -> str:
    lines = list(difflib.unified_diff(expected.splitlines(), actual.splitlines(),
                                      f"{name} (fixture)", f"{name} (now)", lineterm="", n=2))
    return "\n".join(lines[:120]) + ("\n… (diff truncated)" if len(lines) > 120 else "")


def compare_text(name: str, actual: str) -> None:
    path = _path(name)
    if WRITE:
        os.makedirs(FIXTURES, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(actual)
        print(f"[WROTE] {name}")
        return
    with open(path, encoding="utf-8") as f:
        expected = f.read()
    check(name, actual == expected, _diff(expected, actual, name))


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def compare_tools(name: str, actual: list) -> None:
    path = _path(name)
    if WRITE:
        os.makedirs(FIXTURES, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_dump(actual))
        print(f"[WROTE] {name} ({len(actual)} tools)")
        return
    with open(path, encoding="utf-8") as f:
        expected = json.load(f)

    def key(t: dict) -> str:
        return (t.get("function") or t).get("name", "")

    exp = {key(t): t for t in expected}
    act = {key(t): t for t in actual}
    missing, extra = sorted(set(exp) - set(act)), sorted(set(act) - set(exp))
    check(f"{name}: same tool names", not missing and not extra, f"missing: {missing}\nextra: {extra}")
    check(f"{name}: no duplicate names", len(act) == len(actual))
    changed = [n for n in sorted(set(exp) & set(act)) if exp[n] != act[n]]
    check(f"{name}: same name, description and schema for every tool", not changed,
          "\n\n".join(_diff(_dump(exp[n]), _dump(act[n]), n) for n in changed))
    exp_order = [key(t) for t in expected if key(t) in act]
    act_order = [key(t) for t in actual if key(t) in exp]
    if exp_order != act_order:
        print(f"[NOTE] {name}: tool order differs from the fixture (not a failure; filesystem order)")


def main() -> None:
    compare_tools("chat_tools.json", obtain_chat_tools())
    compare_tools("realtime_tools.json", obtain_realtime_tools())

    set_home_map(True)
    set_facts(True)
    full = obtain_system_message()
    check("full variant has the home map and the About section",
          "HOME DEVICES" in full and "ABOUT ROBIN" in full)
    compare_text("system_message_full.txt", full)
    compare_text("realtime_instructions_full.txt", obtain_realtime_instructions())

    set_home_map(False)
    set_facts(False)
    empty = obtain_system_message()
    check("empty variant has neither", "HOME DEVICES" not in empty and "ABOUT ROBIN" not in empty)
    compare_text("system_message_empty.txt", empty)
    compare_text("realtime_instructions_empty.txt", obtain_realtime_instructions())

    if WRITE:
        print("Fixtures written.")
        return
    print()
    if failures:
        print(f"{failures} parity check(s) FAILED")
        sys.exit(1)
    print("All parity checks passed")


if __name__ == "__main__":
    main()
