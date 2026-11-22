"""Checks for the LLM provider chosen in the app and its key: core/llm_provider.py, core/llm_api.py (and how core/llm.py,
core/models.py and the agent follow it).
Run: bin/test test_llm_provider

A fake LiteLLM (no real key, no network): the key is tested on the spot with the provider's fast model and the purpose
``setup_check``; a refused key stores nothing; a stored key is encrypted, put in the provider's env name, and never in
any answer, log line or the database in clear (only ``last4``); the choice sets every job's model, the chat
profiles and the agent's model while env stays the override (a model variable, a key the operator set); keeping the
stored key, switching provider, Ollama's address and model, a restart re-reading it; admins only (a member 403).
"""

import asyncio
import logging
import os
import sqlite3
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("llm_provider_test")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import litellm

import addons  # noqa: F401
from core import db, llm, llm_api, llm_provider, models, session_runner, users
from core.access import level
from core.auth import setup_auth
from core.paths import DATABASE_PATH
from addons import bootstrap as addons_bootstrap

db.migrate()
users.bootstrap()
addons_bootstrap.bootstrap()  # the credentials key file
ADMIN = users.first_admin()
assert ADMIN is not None
GAL = users.create("Gal")
AUTH = {u.id: {"Authorization": f"Bearer {users.issue_token(u.id, 'test')[0]}"} for u in (ADMIN, GAL)}
KEY_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OLLAMA_API_BASE", "MODEL")
for name in KEY_NAMES:
    os.environ.pop(name, None)

OPENAI_KEY = "sk-test-openai-0123456789-WXYZ"
BAD_KEY = "sk-test-refused-9876543210-BADK"
CLAUDE_KEY = "sk-ant-test-abcdefghij-QRST"
SECRETS = (OPENAI_KEY, BAD_KEY, CLAUDE_KEY)

results: List[Tuple[str, bool]] = []
SEEN: List[str] = []  # every response body, checked for the keys at the end
CALLS: List[Dict[str, Any]] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


LOG = Capture()
logging.getLogger().addHandler(LOG)
logging.getLogger().setLevel(logging.DEBUG)


class Refused(Exception):
    """What a provider raises for a bad key: its message quotes the key, as some do."""
    status_code = 401


async def fake_acompletion(**kwargs: Any) -> Any:
    CALLS.append(kwargs)
    if kwargs.get("api_key") == BAD_KEY:
        raise Refused(f"Incorrect API key provided: {BAD_KEY}")
    if kwargs.get("api_base") == "http://down.invalid:11434":
        raise ConnectionError("no route")
    return {"choices": [{"message": {"content": "OK"}}]}


litellm.acompletion = fake_acompletion


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    llm_api.setup(app)
    return app


async def call(c: TestClient, method: str, who: Any, body: Any = None) -> Tuple[int, Any]:
    r = await c.request(method, "/api/llm", headers=AUTH[who.id], json=body)
    text = await r.text()
    SEEN.append(text)
    return r.status, (await r.json()) if text else None


def db_text() -> str:
    conn = sqlite3.connect(str(DATABASE_PATH))
    try:
        return "\n".join(str(v) for row in conn.execute("SELECT * FROM server_settings") for v in row)
    finally:
        conn.close()


async def main() -> None:
    async with TestClient(TestServer(build_app())) as c:
        status, body = await call(c, "GET", GAL)
        check("a member: GET /api/llm → 403", status == 403 and body["needs"] == "manage_server")
        status, _ = await call(c, "PUT", GAL, {"provider": "openai", "key": OPENAI_KEY})
        check("a member: PUT → 403, nothing tested", status == 403 and not CALLS)
        status, body = await call(c, "GET", ADMIN)
        check("nothing chosen yet: no provider, no key, Claude's models; the four providers offered",
              status == 200 and body["provider"] is None and body["key"] == {"set": False, "from_env": False, "last4": ""}
              and body["models"]["balanced"] == "anthropic/claude-sonnet-5-5"
              and [p["id"] for p in body["providers"]] == ["claude", "openai", "gemini", "ollama"]
              and [p["experimental"] for p in body["providers"]] == [False, False, False, True], body)
        check("defaults before a choice: Claude", llm.model("chat") == "anthropic/claude-sonnet-5-5"
              and llm.model("calls") == "anthropic/claude-haiku-5-5")

        status, body = await call(c, "PUT", ADMIN, {"provider": "openai", "key": BAD_KEY})
        check("a refused key: 422 check_failed in plain words (not the provider's message), nothing stored",
              status == 422 and body["error"] == "check_failed" and "refused" in body["detail"]
              and BAD_KEY not in body["detail"] and llm_provider.chosen() is None and "OPENAI_API_KEY" not in os.environ, body)
        for bad, field in (({"provider": "mistral", "key": OPENAI_KEY}, "provider"),
                           ({"provider": "openai"}, "key"), ({"provider": "openai", "key": "sk test key"}, "key"),
                           ({"provider": "ollama", "base_url": "localhost"}, "base_url")):
            status, body = await call(c, "PUT", ADMIN, bad)
            check(f"a bad {field} → 400 with the field", status == 400 and body.get("field") == field, body)

        CALLS.clear()
        status, body = await call(c, "PUT", ADMIN, {"provider": "openai", "key": OPENAI_KEY})
        tested = CALLS[0] if CALLS else {}
        check("the key is tested with the provider's fast model, the new key, the purpose setup_check",
              len(CALLS) == 1 and tested["model"] == "openai/gpt-5.4-mini" and tested["api_key"] == OPENAI_KEY
              and tested["litellm_metadata"]["usage"]["purpose"] == "setup_check", {k: v for k, v in tested.items() if k != "api_key"})
        check("saved: the answer says set, ends in WXYZ, never the key",
              status == 200 and body["provider"] == "openai" and body["key"] == {"set": True, "from_env": False, "last4": "WXYZ"}
              and OPENAI_KEY not in str(body), body)
        check("stored encrypted: the database never holds the key in clear",
              OPENAI_KEY not in db_text() and "WXYZ" in db_text() and '"key"' in db_text())
        check("in the provider's env name for LiteLLM", os.environ.get("OPENAI_API_KEY") == OPENAI_KEY)
        check("every job follows: chat / search balanced, background fast, transcription OpenAI's",
              llm.model("chat") == "openai/gpt-5.5" and llm.model("search") == "openai/gpt-5.5"
              and llm.model("calls") == "openai/gpt-5.4-mini" and llm.model("transcribe") == "openai/gpt-4o-transcribe")
        check("purposes follow their profile", llm.model_for("weekly_review") == "openai/gpt-5.4-mini"
              and llm.model_for("chat") == "openai/gpt-5.5")
        profiles = models.list_profiles()
        check("the chat profiles are the provider's (deep thinks hard)",
              [(p.id, p.model) for p in profiles] == [("fast", "openai/gpt-5.4-mini"), ("balanced", "openai/gpt-5.5"),
                                                       ("deep", "openai/gpt-5.5-pro")]
              and profiles[2].reasoning_effort == "high" and models.default_profile().id == "fast"
              and [p.id for p in models.profiles_for(level("member"))] == ["fast", "balanced"], profiles)
        check("the agent's model follows without a restart", session_runner.agent().model == "openai/gpt-5.5")

        os.environ["MODEL"] = "anthropic/claude-sonnet-5-5"
        check("env stays the override: MODEL wins for chat, the rest keep the choice",
              llm.model("chat") == "anthropic/claude-sonnet-5-5" and llm.model("calls") == "openai/gpt-5.4-mini")
        os.environ.pop("MODEL")

        CALLS.clear()
        status, body = await call(c, "PUT", ADMIN, {"provider": "openai"})
        check("no key sent for the same provider: tested with the stored one (from env), kept",
              status == 200 and body["key"]["last4"] == "WXYZ" and "api_key" not in CALLS[0]
              and os.environ.get("OPENAI_API_KEY") == OPENAI_KEY, body)

        # A restart: the environment is fresh, the stored key comes back from the database.
        os.environ.pop("OPENAI_API_KEY")
        llm_provider._ours.clear()
        llm_provider.forget_cache()
        llm_provider.apply()
        check("after a restart: the stored key is put back in env", os.environ.get("OPENAI_API_KEY") == OPENAI_KEY)

        status, body = await call(c, "PUT", ADMIN, {"provider": "claude", "key": CLAUDE_KEY})
        check("switching to Claude: its key in env, OpenAI's (ours) taken out, Claude's models",
              status == 200 and os.environ.get("ANTHROPIC_API_KEY") == CLAUDE_KEY and "OPENAI_API_KEY" not in os.environ
              and llm.model("chat") == "anthropic/claude-sonnet-5-5" and llm.model("transcribe") == "gemini/gemini-3.1-flash-lite"
              and [p.model for p in models.list_profiles()][0] == "anthropic/claude-haiku-5-5"
              and body["key"]["last4"] == "QRST", body)
        status, body = await call(c, "PUT", ADMIN, {"provider": "openai"})
        check("switching back without a key: refused (OpenAI's key was not kept for another provider)",
              status == 400 and body["field"] == "key", body)

        # A key the operator set in env wins over the stored one.
        os.environ.pop("ANTHROPIC_API_KEY")
        llm_provider._ours.clear()
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-operator-key-0000"
        llm_provider.apply()
        status, body = await call(c, "GET", ADMIN)
        check("the operator's key in env is never overwritten; the answer says it comes from env, no last4",
              os.environ["ANTHROPIC_API_KEY"] == "sk-ant-operator-key-0000"
              and body["key"] == {"set": True, "from_env": True, "last4": ""}, body)
        os.environ.pop("ANTHROPIC_API_KEY")

        CALLS.clear()
        status, body = await call(c, "PUT", ADMIN, {"provider": "ollama", "base_url": "http://down.invalid:11434",
                                                    "model": "llama3.1:8b"})
        check("Ollama unreachable: 422 says so", status == 422 and "reached" in body["detail"], body)
        status, body = await call(c, "PUT", ADMIN, {"provider": "ollama", "base_url": "http://gpu.local:11434/",
                                                    "model": "llama3.1:8b"})
        check("Ollama: tested at its address with the model, every job on it, the address in env",
              status == 200 and CALLS[-1]["model"] == "ollama_chat/llama3.1:8b"
              and CALLS[-1]["api_base"] == "http://gpu.local:11434/" and "api_key" not in CALLS[-1]
              and body["base_url"] == "http://gpu.local:11434" and body["model"] == "llama3.1:8b"
              and llm.model("chat") == "ollama_chat/llama3.1:8b" and llm.model("calls") == "ollama_chat/llama3.1:8b"
              and os.environ.get("OLLAMA_API_BASE") == "http://gpu.local:11434"
              and body["key"]["set"] is False, body)

    leaked_responses = [s for s in SECRETS if any(s in text for text in SEEN)]
    check("no key in any answer", not leaked_responses, leaked_responses)
    leaked_logs = [s for s in SECRETS if any(s in line for line in LOG.lines)]
    check("no key in any log line", not leaked_logs, leaked_logs)


asyncio.run(main())
for name in KEY_NAMES:
    os.environ.pop(name, None)
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
raise SystemExit(1 if failed else 0)
