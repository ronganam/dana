"""Tool enforcement on every surface and the access-level limits (docs/access.md,
"Enforcement", phase 4).

Users on a temporary database, through the real aiohttp handlers, session
runner, agent loop, realtime relay and Telegram adapter: the first admin (every
addon connected, fake credentials), a member with only ``core``, ``tasks`` and
``shopping``, and a third user with only ``people`` (for ``needs=``). Only the
model (scripted LiteLLM stream), the OpenAI Realtime server (a local mock) and
the Telegram network are faked; the tools under test are replaced by spies, so
every case asserts the tool function itself was or was not called.

Covers: the tool list sent to the model per user; a model call of a tool the
user does not have (ops, WhatsApp send, Home Assistant) refused exactly like a
made-up name, with no approval card, the run finishing normally (and the same
calls running / asking for the admin); the realtime relay's tool list and
function calls; an approval resumed after its addon was disconnected; a
learned phrase whose tool is gone; Telegram ``/memory`` without ``about``; a
``needs=`` tool with one addon missing; direct server actions answering
``not_connected``; chat model profiles per level; admin-only endpoints; a
member connecting ``ops``.

Run: bin/test test_access
"""

import asyncio
import datetime
import inspect
import json
import os
import sys
import time
import uuid
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("access_test")
os.environ["API_TOKEN"] = "access-test-env-token"  # a test value: with it set, DB tokens are honoured
os.environ["ALLOWED_TELEGRAM_USER_IDS"] = "111"
os.environ["TELEGRAM_CHAT_ID"] = "1111"
os.environ.pop("SHORTCUTS", None)
os.environ.pop("ASSISTANT_MODELS", None)  # the built-in model catalog
os.environ.pop("ASSISTANT_DEFAULT_MODEL", None)
os.environ.setdefault("GEMINI_API_KEY", "test-key")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials

import aiohttp
import litellm
from aiogram import types
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from litellm.types.utils import (ChatCompletionDeltaToolCall, Delta, Function,
                                 ModelResponseStream, StreamingChoices)

from core import db, users
from addons.tasks.groups import ensure_default_groups_for_everyone

db.migrate()
users.bootstrap()
ensure_default_groups_for_everyone()

from addons import bootstrap as addons_bootstrap

addons_bootstrap.bootstrap()  # the first admin's official addons (env has no HA / WAHA / Google)

from core import assistant_api, debug_api
from addons.phone import api as phone_api
import core.telegram_bot as tb
import addons.calendar.tools as calendar_tools
from core import actions_api, now_api, session, session_runner, shortcuts
from core.agent import AgentOrchestrator
from core.auth import setup_auth
from core.db import get_db_connection
from core.realtime import assistant_realtime_ws_api
from core.telegram_bot import AuthMiddleware, TelegramBot
from core.addons import registry, store
from core.addons.addon import all_tools, find_tool, get_addon
from addons.phone import suggestions as phone_suggestions

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: str = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


# --- Users -------------------------------------------------------------------------------

FAKE_CREDS = {
    "homeassistant": {"host": "http://ha.invalid", "token": "fake"},
    "whatsapp": {"url": "http://waha.invalid", "session": "default"},
    "calendar": {"refresh_token": "fake"},
    "gmail": {"refresh_token": "fake"},
}

ADMIN = users.first_admin()
for _kind, _creds in FAKE_CREDS.items():
    store.connect(ADMIN, _kind, credentials=_creds)
MEMBER = users.create("Member", telegram_ids=[222], telegram_chat_id=2222)
for _kind in ("tasks", "shopping"):
    store.connect(MEMBER, _kind)
PAT = users.create("Pat")  # only people: link_task_to_person also needs tasks
store.connect(PAT, "people")

ADMIN_TOKEN, _ = users.issue_token(ADMIN.id, "test admin")
MEMBER_TOKEN, _ = users.issue_token(MEMBER.id, "test member")
PAT_TOKEN, _ = users.issue_token(PAT.id, "test pat")
MEMBER_KINDS = ("essentials", "tasks", "shopping")


def auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Spies: the real tool functions are never reached ------------------------------------

CALLS: List[Tuple[str, str]] = []  # (tool, user id)


def spy_result(name: str) -> str:
    return "Event created. ID: ev_spy." if name == "add_event" else f"{name} ran."


def make_spy(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
    if inspect.iscoroutinefunction(original):
        async def spy_async(conn, *args, **kwargs):
            CALLS.append((name, conn.user.id))
            return spy_result(name)
        return spy_async

    def spy_sync(conn, *args, **kwargs):
        CALLS.append((name, conn.user.id))
        return spy_result(name)
    return spy_sync


SPIED = ("run_kubectl", "run_python", "send_whatsapp_message", "call_ha_service", "link_task_to_person",
         "list_memories", "recall_info", "remember_info", "forget_info", "add_event", "delete_event")
for _name in SPIED:
    _tool = find_tool(_name)
    _tool.func = make_spy(_name, _tool.func)  # same schema, spied function
# The direct (non-registry) callers import these from the module at call time.
calendar_tools.add_event = find_tool("add_event").func
calendar_tools.delete_event = find_tool("delete_event").func


def called(name: str, user_id: Optional[str] = None) -> int:
    return sum(1 for n, u in CALLS if n == name and (user_id is None or u == user_id))


# --- Fake model ---------------------------------------------------------------------------

def text_chunk(text=None, finish=None):
    return ModelResponseStream(model="fake", choices=[StreamingChoices(
        index=0, delta=Delta(content=text), finish_reason=finish)])


def tool_chunk(call_id, name, args):
    return ModelResponseStream(model="fake", choices=[StreamingChoices(
        index=0,
        delta=Delta(content=None, tool_calls=[ChatCompletionDeltaToolCall(
            id=call_id, index=0, type="function", function=Function(name=name, arguments=json.dumps(args)))]),
        finish_reason="tool_calls")])


class FakeLLM:
    def __init__(self) -> None:
        self.scripts: List[List[Any]] = []
        self.calls: List[Dict[str, Any]] = []

    def load(self, *scripts) -> None:
        self.scripts = list(scripts)

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        script = self.scripts.pop(0)

        async def gen():
            for item in script:
                yield item
        return gen()


fake = FakeLLM()
litellm.acompletion = fake.acompletion


def answer(text: str) -> List[Any]:
    return [text_chunk(text, finish="stop")]


def tool_names(call: Dict[str, Any]) -> List[str]:
    return [t["function"]["name"] for t in call.get("tools") or []]


# --- HTTP helpers --------------------------------------------------------------------------

async def read_sse(response, max_seconds: float = 15.0) -> List[Dict[str, Any]]:
    frames, buffer = [], ""
    deadline = time.monotonic() + max_seconds
    while time.monotonic() < deadline:
        try:
            chunk = await asyncio.wait_for(response.content.readany(), timeout=deadline - time.monotonic())
        except asyncio.TimeoutError:
            break
        if not chunk:
            break
        buffer += chunk.decode()
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            data = [ln[5:].strip() for ln in block.splitlines() if ln.startswith("data:")]
            if data:
                frames.append(json.loads("".join(data)))
    return frames


async def send(client, token, session_id, message, **extra):
    res = await client.post("/api/assistant/message", headers=auth(token),
                            json={"session_id": session_id, "message": message, **extra})
    if res.status != 200:
        return res.status, await res.json()
    return 200, await read_sse(res)


async def approve(client, token, session_id, action="approve"):
    res = await client.post("/api/assistant/approve", headers=auth(token),
                            json={"session_id": session_id, "action": action})
    if res.status != 200:
        return res.status, await res.json()
    return 200, await read_sse(res)


async def call(client, method, token, path, **kwargs):
    res = await client.request(method, path, headers=auth(token), **kwargs)
    try:
        body = await res.json()
    except Exception:
        body = None
    return res.status, body


def frames_of(frames, type_):
    return [f for f in frames if f.get("type") == type_]


def build_app() -> web.Application:
    session_runner.set_agent(AgentOrchestrator(model="gemini/server-default"))
    app = web.Application()
    setup_auth(app)
    r = app.router
    r.add_get("/api/assistant/models", assistant_api.assistant_models_api)
    r.add_post("/api/assistant/message", assistant_api.assistant_message_api)
    r.add_post("/api/assistant/approve", assistant_api.assistant_approve_api)
    r.add_get("/api/debug/logs", debug_api.get_system_logs_api)
    r.add_delete("/api/debug/logs", debug_api.clear_system_logs_api)
    r.add_get("/api/debug/sessions", debug_api.get_debug_sessions_api)
    r.add_post("/api/suggestions/{id}/accept", phone_api.accept_suggestion_api)
    r.add_post("/api/now/act", now_api.now_act_api)
    r.add_get("/api/checks", now_api.checks_api)
    r.add_post("/api/checks/{key}/ignore", now_api.check_ignore_api)
    actions_api.setup(app)
    return app


# --- 1. Tool lists ----------------------------------------------------------------------------

def admin_tools() -> list:
    """Every tool of the addons the admin has (the env import's), in the fixed order."""
    return [t.name for t in all_tools() if t.addon in store.connected_addons(ADMIN)]


async def test_tool_lists(client):
    fake.load(answer("Hi member."))
    await send(client, MEMBER_TOKEN, "s_list_m", "hello")
    member_names = tool_names(fake.calls[-1])
    allowed = {t.name for t in all_tools() if t.addon in MEMBER_KINDS and set(t.needs) <= set(MEMBER_KINDS)}
    check("lists: the member's model request has only tools of core / tasks / shopping",
          member_names and set(member_names) == allowed
          and member_names == [t.name for t in registry.tools_for(MEMBER)],
          str(sorted(set(member_names) ^ allowed)))
    check("lists: the member's request has no ops / WhatsApp / Home Assistant / calendar tool",
          not {"run_kubectl", "run_python", "send_whatsapp_message", "call_ha_service", "add_event"} & set(member_names))
    fake.load(answer("Hi admin."))
    await send(client, ADMIN_TOKEN, "s_list_a", "hello")
    check("lists: the admin's model request has every tool of their addons, in order (all but contacts, which "
          "only the Google sign-in connects, money, which is turned on by hand, and telegram_account, which "
          "only the QR sign-in connects)",
          tool_names(fake.calls[-1]) == admin_tools() and {t.addon for t in all_tools()} - set(
              store.connected_addons(ADMIN)) == {"contacts", "money", "telegram_account"},
          f"{len(tool_names(fake.calls[-1]))} vs {len(admin_tools())}")


# --- 2. A model call of a tool the user does not have ---------------------------------------

HIDDEN = {
    "run_kubectl": {"command": "get pods -A"},
    "send_whatsapp_message": {"chat_id": "972500000000@c.us", "text": "hi"},
    "call_ha_service": {"domain": "cover", "service": "open_cover", "entity_id": "cover.gate"},
}


async def scripted_call(client, token, session_id, name, args) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """The model calls ``name`` once, then answers. Returns the frames and what
    the model was sent back for the call (None if it was never asked again)."""
    n = len(fake.calls)
    fake.load([tool_chunk("call_1", name, args)], answer("Finished."))
    _, frames = await send(client, token, session_id, f"please use {name}")
    if len(fake.calls) < n + 2:
        return frames, None
    back = [m for m in fake.calls[n + 1]["messages"] if m.get("role") == "tool" and m.get("tool_call_id") == "call_1"]
    return frames, back[0]["content"] if back else None


async def test_hidden_tool_calls(client):
    _, made_up = await scripted_call(client, MEMBER_TOKEN, "s_made_up", "zz_no_such_tool", {})
    check("hidden: baseline: a made-up tool name is refused", made_up == "Error executing tool: Tool 'zz_no_such_tool' "
          "is not registered.", str(made_up))

    for name, args in HIDDEN.items():
        before = called(name)
        frames, back = await scripted_call(client, MEMBER_TOKEN, f"s_hidden_{name}", name, args)
        ends = [f for f in frames_of(frames, "tool_end") if f.get("id") == "call_1"]
        final = frames_of(frames, "final")
        check(f"hidden: {name} for the member: not executed", called(name) == before, str(CALLS[-3:]))
        check(f"hidden: {name} for the member: no approval card, nothing pending",
              not frames_of(frames, "approval_required") and session.get_pending(MEMBER.id, f"s_hidden_{name}") is None)
        check(f"hidden: {name} for the member: the model gets the refusal a made-up name gets",
              back is not None and made_up is not None
              and back.replace(name, "<tool>") == made_up.replace("zz_no_such_tool", "<tool>"), str(back))
        check(f"hidden: {name} for the member: the run completes normally",
              ends and ends[0]["ok"] is False and final and final[-1]["content"] == "Finished."
              and frames[-1].get("type") == "state" and frames[-1]["state"].get("status") == "idle",
              json.dumps(frames[-1])[:200])

    # The same scripts for the admin: run at once, or ask first (WhatsApp send).
    for name in ("run_kubectl", "call_ha_service"):
        before = called(name, ADMIN.id)
        frames, back = await scripted_call(client, ADMIN_TOKEN, f"s_admin_{name}", name, HIDDEN[name])
        check(f"admin: {name} executes as today", called(name, ADMIN.id) == before + 1 and back == f"{name} ran.",
              str(back))
    frames, _ = await scripted_call(client, ADMIN_TOKEN, "s_admin_wa", "send_whatsapp_message",
                                    HIDDEN["send_whatsapp_message"])
    cards = frames_of(frames, "approval_required")
    check("admin: send_whatsapp_message asks for approval as today",
          cards and cards[0]["name"] == "send_whatsapp_message" and called("send_whatsapp_message") == 0
          and session.get_pending(ADMIN.id, "s_admin_wa") is not None)
    fake.load(answer("Not sent."))
    await approve(client, ADMIN_TOKEN, "s_admin_wa", "deny")


# --- 3. needs= ------------------------------------------------------------------------------

async def test_needs(client):
    names = [t.name for t in registry.tools_for(PAT)]
    check("needs: link_task_to_person is not offered with people but no tasks",
          "get_person" in names and "link_task_to_person" not in names, str(names))
    refused = None
    try:
        await registry.execute(PAT, "link_task_to_person", {"task_id": "1", "person": "Dana"})
    except registry.ToolNotAvailable as e:
        refused = str(e)
    check("needs: execute refuses it like a made-up name", refused == "Tool 'link_task_to_person' is not registered.")
    _, back = await scripted_call(client, PAT_TOKEN, "s_needs", "link_task_to_person", {"task_id": "1", "person": "x"})
    check("needs: a model call of it is refused, not run",
          called("link_task_to_person") == 0 and back == "Error executing tool: Tool 'link_task_to_person' is not "
          "registered.", str(back))


# --- 4. Approval resumed after the addon was disconnected -------------------------------

async def test_resume_after_disconnect(client):
    fake.load([tool_chunk("call_1", "send_whatsapp_message", HIDDEN["send_whatsapp_message"])])
    _, frames = await send(client, ADMIN_TOKEN, "s_resume", "message Gal")
    check("resume: the admin's send waits for approval", frames_of(frames, "approval_required"))
    store.disconnect(ADMIN, "whatsapp")
    try:
        n = len(fake.calls)
        fake.load(answer("I can't send WhatsApp messages any more."))
        status, frames = await approve(client, ADMIN_TOKEN, "s_resume")
        ends = [f for f in frames_of(frames, "tool_end") if f.get("id") == "call_1"]
        back = [m for m in fake.calls[n]["messages"] if m.get("role") == "tool"] if len(fake.calls) > n else []
        check("resume: approving after the disconnect does not run the tool", called("send_whatsapp_message") == 0)
        check("resume: the card closes with the refusal",
              status == 200 and ends and ends[0]["ok"] is False
              and ends[0]["result"] == "Error executing tool: Tool 'send_whatsapp_message' is not registered.",
              json.dumps(ends)[:300])
        check("resume: the model gets the made-up-name refusal and the run ends with its message",
              back and back[-1]["content"] == "Error executing tool: Tool 'send_whatsapp_message' is not registered."
              and frames_of(frames, "final")[-1]["content"] == "I can't send WhatsApp messages any more.")
        check("resume: the pending state is cleared",
              session.get_pending(ADMIN.id, "s_resume") is None
              and session.get_session_state(ADMIN.id, "s_resume")["status"] == "idle")
    finally:
        store.connect(ADMIN, "whatsapp", credentials=FAKE_CREDS["whatsapp"])


# --- 5. Learned phrase whose tool is gone ------------------------------------------------------

def lights_entries() -> List[Dict[str, Any]]:
    args = {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"}
    return [{"role": "assistant", "content": None, "tool_calls": [{"id": "l", "type": "function",
             "function": {"name": "call_ha_service", "arguments": json.dumps(args)}}]},
            {"role": "tool", "tool_call_id": "l", "name": "call_ha_service", "content": "Done."},
            {"role": "assistant", "content": "On."}]


async def test_learned_phrase(client):
    outcomes = [shortcuts.learn(ADMIN, "kitchen lights", lights_entries(), "On.") for _ in range(3)]
    check("phrase: learned for the admin", outcomes[-1] == "activated", str(outcomes))
    check("phrase: never learned for a tool the user does not have",
          shortcuts.learn(MEMBER, "kitchen lights", lights_entries(), "On.") is None
          and not shortcuts.list_all(MEMBER.id))
    store.disconnect(ADMIN, "homeassistant")
    try:
        check("phrase: no match once the tool is gone", shortcuts.match(ADMIN, "kitchen lights") is None)
        before, n = called("call_ha_service"), len(fake.calls)
        fake.load(answer("I can't control the lights."))
        _, frames = await send(client, ADMIN_TOKEN, "s_phrase", "kitchen lights")
        check("phrase: the message goes to the model as if no phrase matched",
              len(fake.calls) == n + 1 and called("call_ha_service") == before
              and not frames[-1]["state"].get("shortcut")
              and frames_of(frames, "final")[-1]["content"] == "I can't control the lights.")
        check("phrase: the model's different answer then clears it, like any other different outcome",
              any(s["phrase"] == "kitchen lights" and not s["active"] and s["seen"] == 0
                  for s in shortcuts.list_all(ADMIN.id)), json.dumps(shortcuts.list_all(ADMIN.id))[:300])
    finally:
        store.connect(ADMIN, "homeassistant", credentials=FAKE_CREDS["homeassistant"])


# --- 6. Realtime voice relay ----------------------------------------------------------------

class MockRealtime:
    """A local stand-in for the OpenAI Realtime WebSocket: after session.update
    it asks for the given function calls and records what comes back."""

    def __init__(self, calls: List[Tuple[str, str, Dict[str, Any]]]) -> None:
        self.calls = calls
        self.session_update: Optional[Dict[str, Any]] = None
        self.outputs: Dict[str, str] = {}
        self.done = asyncio.Event()
        self.app = web.Application()
        self.app.router.add_get("/v1/realtime", self.handle)
        self.runner: Optional[web.AppRunner] = None

    async def start(self) -> int:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        return site._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "session.created", "session": {"id": "mock"}})
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            event = json.loads(msg.data)
            if event.get("type") == "session.update":
                self.session_update = event
                for call_id, name, args in self.calls:
                    await ws.send_json({"type": "conversation.item.created",
                                        "item": {"type": "function_call", "call_id": call_id, "name": name}})
                    await ws.send_json({"type": "response.function_call_arguments.done", "call_id": call_id,
                                        "arguments": json.dumps(args)})
            elif event.get("type") == "conversation.item.create":
                item = event.get("item") or {}
                if item.get("type") == "function_call_output":
                    self.outputs[item["call_id"]] = item["output"]
                    if len(self.outputs) == len(self.calls):
                        self.done.set()
        return ws


async def realtime_session(token: str, session_id: str, calls) -> MockRealtime:
    mock = MockRealtime(calls)
    port = await mock.start()
    os.environ["OPENAI_REALTIME_URL"] = f"http://127.0.0.1:{port}/v1/realtime"
    os.environ["OPENAI_API_KEY"] = "mock-sk-test-token"
    relay = web.Application()
    setup_auth(relay)
    relay.router.add_get("/api/assistant/realtime", assistant_realtime_ws_api)
    runner = web.AppRunner(relay)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    relay_port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(f"http://127.0.0.1:{relay_port}/api/assistant/realtime?session_id={session_id}",
                                       headers=auth(token)) as ws:
                await asyncio.wait_for(mock.done.wait(), timeout=10)
                await ws.send_json({"type": "hangup"})
                while True:
                    msg = await asyncio.wait_for(ws.receive(), timeout=10)
                    if msg.type != aiohttp.WSMsgType.TEXT or json.loads(msg.data).get("type") == "call_ended":
                        break
    finally:
        await runner.cleanup()
        await mock.stop()
    return mock


async def test_realtime():
    calls = [("c_hidden", "run_kubectl", HIDDEN["run_kubectl"]), ("c_made_up", "zz_no_such_tool", {}),
             ("c_wa", "send_whatsapp_message", HIDDEN["send_whatsapp_message"])]
    mock = await realtime_session(MEMBER_TOKEN, "rt_member", calls)
    sent = [t["name"] for t in (mock.session_update or {}).get("session", {}).get("tools", [])]
    check("realtime: the member's session tool list is their own",
          sent == [t.name for t in registry.tools_for(MEMBER)] and "run_kubectl" not in sent, str(sent)[:200])
    hidden = json.loads(mock.outputs.get("c_hidden", "{}"))
    made_up = json.loads(mock.outputs.get("c_made_up", "{}"))
    check("realtime: the member's hidden tool calls are not executed",
          called("run_kubectl", MEMBER.id) == 0 and called("send_whatsapp_message") == 0)
    check("realtime: they get the refusal a made-up name gets",
          hidden.get("status") == "failed" and made_up.get("status") == "failed"
          and hidden["error"].replace("run_kubectl", "<t>") == made_up["error"].replace("zz_no_such_tool", "<t>"),
          f"{hidden} / {made_up}")

    before = called("run_kubectl", ADMIN.id)
    mail_call = ("c_mail", "send_email", {"to": "x@example.test", "subject": "s", "body": "b"})
    mock = await realtime_session(ADMIN_TOKEN, "rt_admin", [("c_admin", "run_kubectl", HIDDEN["run_kubectl"]), mail_call,
                                                            ("c_made_up", "zz_no_such_tool", {})])
    sent = [t["name"] for t in (mock.session_update or {}).get("session", {}).get("tools", [])]
    voice = [n for n in admin_tools() if get_addon(find_tool(n).addon).realtime]
    check("realtime: the admin's session has every tool of their addons offered in voice and runs them as today",
          sent == voice and called("run_kubectl", ADMIN.id) == before + 1
          and mock.outputs.get("c_admin") == "run_kubectl ran.", str(mock.outputs))
    mail, made_up = json.loads(mock.outputs.get("c_mail", "{}")), json.loads(mock.outputs.get("c_made_up", "{}"))
    check("realtime: a Gmail tool (kept out of voice: no Approve card there) is refused like a made-up name",
          mail.get("status") == "failed"
          and mail["error"].replace("send_email", "<t>") == made_up["error"].replace("zz_no_such_tool", "<t>"),
          f"{mail} / {made_up}")


# --- 7. Telegram /memory without `about` ----------------------------------------------------------

async def test_telegram_memory():
    bot = TelegramBot.__new__(TelegramBot)
    bot.bot = AsyncMock()
    bot.selection_toggles = {}
    bot.user_memory_states = {}

    def message(telegram_id: int, text: str) -> SimpleNamespace:
        return SimpleNamespace(from_user=SimpleNamespace(id=telegram_id), text=text, reply=AsyncMock(),
                               chat=SimpleNamespace(id=telegram_id))

    async def as_sender(event, handler):
        await AuthMiddleware()(lambda e, d: handler(e, d["user"]), event, {})

    for text in ("/memory list", "/memory recall dentist", "/memory remember k v", "/memory forget k", "/memory"):
        msg = message(222, text)
        await as_sender(msg, bot.handle_memory)
        check(f"telegram: '{text}' without about gets a short human message",
              msg.reply.await_count == 1 and msg.reply.await_args.args[0] == tb.NO_MEMORY_TEXT
              and "not registered" not in msg.reply.await_args.args[0], str(msg.reply.await_args))
    tg_message = types.Message(message_id=9, date=datetime.datetime.now(datetime.timezone.utc),
                               chat=types.Chat(id=222, type="private"))
    with patch.object(types.Message, "edit_text", new=AsyncMock()) as edit:
        cb = SimpleNamespace(from_user=SimpleNamespace(id=222), data="mem_list_cat:all", answer=AsyncMock(),
                             message=tg_message)
        await as_sender(cb, bot.handle_approval_callback)
        check("telegram: a memory button without about gets the same message",
              edit.await_count == 1 and edit.await_args.args[0] == tb.NO_MEMORY_TEXT, str(edit.await_args))
    check("telegram: no About tool ran for the member",
          not any(called(n, MEMBER.id) for n in ("list_memories", "recall_info", "remember_info", "forget_info")))
    msg = message(111, "/memory list")
    await as_sender(msg, bot.handle_memory)
    check("telegram: the admin's /memory list runs the tool as today",
          called("list_memories", ADMIN.id) == 1 and msg.reply.await_args.args[0] == "list_memories ran.",
          str(msg.reply.await_args))


# --- 8. Direct server actions: not_connected ------------------------------------------------------

def event_suggestion(owner) -> str:
    sid = str(uuid.uuid4())
    payload = {"title": "Dentist", "start": "2030-01-02 10:00", "end": "2030-01-02 11:00"}
    with get_db_connection() as conn:
        conn.execute("INSERT INTO call_suggestions (id, user_id, call_id, source_type, kind, payload, status) "
                     "VALUES (?, ?, 'call_x', 'call', 'event', ?, 'pending')", (sid, owner.id, json.dumps(payload)))
        conn.commit()
    return sid


async def test_not_connected(client):
    sid = event_suggestion(MEMBER)
    status, body = await call(client, "POST", MEMBER_TOKEN, f"/api/suggestions/{sid}/accept", json={})
    check("direct: accepting a suggestion without phone → 403 not_connected",
          status == 403 and body == {"error": "not_connected", "addon": "phone"}, f"{status} {body}")
    store.connect(MEMBER, "phone")
    try:
        status, body = await call(client, "POST", MEMBER_TOKEN, f"/api/suggestions/{sid}/accept", json={})
        check("direct: accepting an event suggestion without calendar → 403 not_connected",
              status == 403 and body == {"error": "not_connected", "addon": "calendar"}, f"{status} {body}")
        status, body = await call(client, "POST", MEMBER_TOKEN, "/api/now/act", json={"id": f"sug:{sid}", "action": "accept"})
        check("direct: the Now accept of it → 403 not_connected",
              status == 403 and body == {"error": "not_connected", "addon": "calendar"}, f"{status} {body}")
        check("direct: add_event was never called and the suggestion is still pending",
              called("add_event") == 0 and phone_suggestions.get_suggestion(MEMBER, sid)["status"] == "pending")
    finally:
        store.disconnect(MEMBER, "phone")

    sid = event_suggestion(ADMIN)
    status, body = await call(client, "POST", ADMIN_TOKEN, "/api/now/act", json={"id": f"sug:{sid}", "action": "accept"})
    check("direct: the admin's Now accept adds the event as today",
          status == 200 and body.get("undo_id") and called("add_event", ADMIN.id) == 1, f"{status} {body}")
    store.disconnect(ADMIN, "calendar")
    try:
        status, body = await call(client, "POST", ADMIN_TOKEN, f"/api/undo/{body['undo_id']}")
        check("direct: undoing it after calendar was disconnected → 403 not_connected, delete_event not called",
              status == 403 and body == {"error": "not_connected", "addon": "calendar"}
              and called("delete_event") == 0, f"{status} {body}")
    finally:
        store.connect(ADMIN, "calendar", credentials=FAKE_CREDS["calendar"])


# --- 9. Model profiles ---------------------------------------------------------------------------

async def test_profiles(client):
    status, body = await call(client, "GET", ADMIN_TOKEN, "/api/assistant/models")
    check("profiles: the admin's catalog is unchanged (fast / balanced / deep, default fast)",
          status == 200 and [m["id"] for m in body["models"]] == ["fast", "balanced", "deep"]
          and body["default"] == "fast", str(body)[:200])
    status, body = await call(client, "GET", MEMBER_TOKEN, "/api/assistant/models")
    check("profiles: the member's catalog lists only fast and balanced",
          status == 200 and [m["id"] for m in body["models"]] == ["fast", "balanced"] and body["default"] == "fast",
          str(body)[:200])

    for asked in ("deep", "no-such-profile"):
        fake.load(answer("ok"))
        _, frames = await send(client, MEMBER_TOKEN, "s_prof", "hello", model=asked)
        check(f"profiles: the member asking for '{asked}' gets the default, as an unknown id does",
              frames[0]["model"]["id"] == "fast" and fake.calls[-1]["model"] == "anthropic/claude-haiku-5-5",
              f"{frames[0].get('model')} {fake.calls[-1]['model']}")
    fake.load(answer("ok"))
    _, frames = await send(client, MEMBER_TOKEN, "s_prof", "hello", model="balanced")
    check("profiles: the member may use balanced", fake.calls[-1]["model"] == "anthropic/claude-sonnet-5-5")
    fake.load(answer("ok"))
    _, frames = await send(client, ADMIN_TOKEN, "s_prof_admin", "hello", model="deep")
    check("profiles: the admin still gets deep", frames[0]["model"]["id"] == "deep"
          and fake.calls[-1]["model"] == "anthropic/claude-opus-5-5")


# --- 10. Admin-only endpoints -------------------------------------------------------------------

ADMIN_ONLY = [
    ("GET", "/api/debug/logs", "manage_server"),
    ("DELETE", "/api/debug/logs", "manage_server"),
]


async def test_admin_only(client):
    for method, path, needs in ADMIN_ONLY:
        status, body = await call(client, method, MEMBER_TOKEN, path, json={})
        check(f"admin only: {method} {path} → 403 for the member",
              status == 403 and body == {"error": "forbidden", "needs": needs}, f"{status} {body}")
    for method, path, _ in ADMIN_ONLY:
        status, body = await call(client, method, ADMIN_TOKEN, path, json={"days": 1})
        check(f"admin only: {method} {path} → 200 for the admin", status == 200, f"{status} {str(body)[:200]}")
    status, _ = await call(client, "GET", MEMBER_TOKEN, "/api/debug/sessions")
    check("per user: the member still lists their own debug sessions", status == 200)
    # Self-checks are per user (phase 5): the member sees only their own, the
    # server's own (reminders) only the admin.
    status, body = await call(client, "GET", MEMBER_TOKEN, "/api/checks")
    member_keys = {c["key"] for c in (body or {}).get("checks", [])}
    status_a, body_a = await call(client, "GET", ADMIN_TOKEN, "/api/checks")
    admin_keys = {c["key"] for c in (body_a or {}).get("checks", [])}
    check("checks: the member gets their own checks, without the server's", status == 200
          and "reminders" not in member_keys and status_a == 200 and "reminders" in admin_keys, (member_keys, admin_keys))
    status, body = await call(client, "POST", MEMBER_TOKEN, "/api/checks/reminders/ignore", json={})
    check("checks: the member cannot ignore the server's check (unknown for them)", status == 404, f"{status} {body}")


# --- 11. ops ----------------------------------------------------------------------------------

def test_ops_connect():
    error = None
    try:
        store.connect(MEMBER, "ops")
    except PermissionError as e:
        error = str(e)
    check("ops: a member cannot connect it (PermissionError)", error and "ops" in error, str(error))
    check("ops: so the member has no ops tool",
          not store.is_connected(MEMBER, "ops")
          and not {"run_kubectl", "run_python"} & {t.name for t in registry.tools_for(MEMBER)})
    check("ops: the admin has it", store.is_connected(ADMIN, "ops") and registry.has_tool(ADMIN, "run_kubectl"))


async def main() -> None:
    async with TestClient(TestServer(build_app())) as client:
        await test_tool_lists(client)
        await test_hidden_tool_calls(client)
        await test_needs(client)
        await test_resume_after_disconnect(client)
        await test_learned_phrase(client)
        await test_realtime()
        await test_telegram_memory()
        await test_not_connected(client)
        await test_profiles(client)
        await test_admin_only(client)
        test_ops_connect()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
