"""Multi-user conversation core (docs/access.md, phase 2).

Two users — the first admin and a member, each with a DB token — drive the
real aiohttp handlers, session runner, agent loop and Telegram adapter on a
temporary database that starts as the 8.1.0 fixture. Only the LLM (scripted
LiteLLM stream), phone push and the Telegram network are faked.

Covers: the migration giving 8.1.0 rows to the first admin; own sessions and
own ``main`` thread per user; 404 for another user's session, run, stream,
debug and undo ids; approvals only for their user (HTTP and the Telegram
callback); learned phrases per user; usage rows and ``/api/usage`` scoping;
``notify`` reaching the right chat (and nothing without one); a background
entry point running as the given user.

Run: python test_multi_user_core.py
"""

import asyncio
import datetime
import json
import os
import sqlite3
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from cryptography.fernet import Fernet

ROOT = os.path.dirname(os.path.abspath(__file__))
import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("multi_user_core")
os.environ["API_TOKEN"] = "mu-test-env-token"  # a test value: with it set, DB tokens are honoured
os.environ["ALLOWED_TELEGRAM_USER_IDS"] = "111"
os.environ["TELEGRAM_CHAT_ID"] = "1111"
os.environ.pop("SHORTCUTS", None)
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake HA credentials
sys.path.insert(0, ROOT)

# The production database before users existed: its rows must become the first admin's.
_conn = sqlite3.connect(DB_PATH)
with open(os.path.join(ROOT, "test_fixtures", "db_8_1_0.sql"), encoding="utf-8") as _f:
    _conn.executescript(_f.read())
_conn.close()

import litellm
from aiogram import types
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from litellm.types.utils import (ChatCompletionDeltaToolCall, Delta, Function,
                                 ModelResponseStream, StreamingChoices)

from core import db, users
from integrations.tasks.groups import ensure_default_groups_for_everyone
from integrations.tasks import groups as task_groups, triggers as task_triggers

MIGRATED = db.migrate()
users.bootstrap()
ensure_default_groups_for_everyone()

from core import assistant_api, debug_api, sessions_api
import core.telegram_bot as tb
from core import actions_api, notify, session, session_runner, shortcuts, undo, usage, usage_api
from core.agent import AgentOrchestrator
from core.auth import setup_auth
from core.db import get_db_connection
from core.telegram_bot import AuthMiddleware, TelegramBot
from integrations.core import core as core_kind
from integrations.framework.store import Connection
from integrations.homeassistant import homeassistant, watcher as ha_watcher
from integrations.framework import store

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


# --- Users ---------------------------------------------------------------------------

ADMIN = users.first_admin()
MEMBER = users.create("Member", telegram_ids=[222], telegram_chat_id=2222)
NO_CHAT = users.create("No Chat")
ADMIN_TOKEN, _ = users.issue_token(ADMIN.id, "test admin")
MEMBER_TOKEN, _ = users.issue_token(MEMBER.id, "test member")


def auth(token):
    return {"Authorization": f"Bearer {token}"}


# --- Test tools ----------------------------------------------------------------------

WHO = []
SENT = []
HA = []
CHANGED = {"value": None}


@core_kind.tool(name="zz_mu_whoami")
async def zz_mu_whoami(conn) -> str:
    """Say who this runs for (records one usage row)."""
    WHO.append(users.current_id())
    usage.record(kind="llm", purpose="chat", model="zz-tool", cost=0.01)
    return f"you are {users.current_id()}"


@core_kind.tool(name="zz_mu_send", is_write=True, confirm=True)
async def zz_mu_send(conn, text: str) -> str:
    """Send something to someone (asks first).

    Args:
        text: What to send.
    """
    SENT.append((users.current_id(), text))
    return "sent"


@core_kind.tool(name="zz_mu_change", is_write=True)
async def zz_mu_change(conn, value: str) -> str:
    """Change something reversibly (runs at once, registers an undo).

    Args:
        value: The new value.
    """
    previous = CHANGED["value"]
    CHANGED["value"] = value
    undo.record(f"Set to {value}", lambda: CHANGED.update(value=previous), source="zz_mu_change")
    return f"set {value}"


@homeassistant.tool(name="call_ha_service", is_write=True)
async def call_ha_service(conn, domain: str, service: str, entity_id: str) -> str:
    """Call a Home Assistant service (fake).

    Args:
        domain: Service domain.
        service: Service name.
        entity_id: Target entity.
    """
    HA.append((users.current_id(), entity_id))
    return "Done."


# The admin's Home Assistant (fake credentials: the HA tool above is a fake); the member has none.
store.connect(ADMIN, "homeassistant", credentials={"host": "http://ha.invalid", "token": "fake"})


# --- Fake LLM -------------------------------------------------------------------------

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
    def __init__(self):
        self.scripts, self.calls = [], []

    def load(self, *scripts):
        self.scripts = list(scripts)

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        script = self.scripts.pop(0)

        async def gen():
            for item in script:
                if isinstance(item, (int, float)):
                    await asyncio.sleep(item)
                else:
                    yield item
        return gen()


fake = FakeLLM()
litellm.acompletion = fake.acompletion


def answer(text, delay=0.0):
    return ([delay] if delay else []) + [text_chunk(text, finish="stop")]


def tool_turn(call_id, name, args, reply="Done."):
    return [[tool_chunk(call_id, name, args)], answer(reply)]


def sent_text(call):
    return json.dumps(call["messages"], ensure_ascii=False)


# --- HTTP helpers ------------------------------------------------------------------------

async def read_sse(response, max_seconds=15.0):
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


async def get(client, token, path):
    res = await client.get(path, headers=auth(token))
    try:
        body = await res.json()
    except Exception:
        body = None
    return res.status, body


def frame(frames, type_):
    return next((f for f in frames if f["type"] == type_), None)


def build_app():
    session_runner.set_agent(AgentOrchestrator(model="gemini/server-default"))
    app = web.Application()
    setup_auth(app)
    r = app.router
    r.add_post("/api/assistant/message", assistant_api.assistant_message_api)
    r.add_post("/api/assistant/approve", assistant_api.assistant_approve_api)
    r.add_get("/api/sessions", sessions_api.get_sessions_api)
    r.add_post("/api/sessions", sessions_api.create_session_api)
    r.add_get("/api/sessions/{session_id}", sessions_api.get_session_detail_api)
    r.add_delete("/api/sessions/{session_id}", sessions_api.delete_session_api)
    r.add_get("/api/sessions/{session_id}/stream", sessions_api.session_stream_api)
    r.add_get("/api/debug/sessions", debug_api.get_debug_sessions_api)
    r.add_get("/api/debug/sessions/{session_id}", debug_api.get_debug_session_detail_api)
    r.add_delete("/api/debug/sessions/{session_id}", debug_api.delete_debug_session_api)
    actions_api.setup(app)
    usage_api.setup(app)
    return app


# --- 1. Migration ------------------------------------------------------------------------

def owners(table):
    with get_db_connection() as conn:
        return {r[0] for r in conn.execute(f"SELECT DISTINCT user_id FROM {table}")}


async def test_migration(client):
    check("migration: every migration ran on the 8.1.0 database",
          MIGRATED == [f"{m.VERSION:04d}_{m.NAME}" for m in db.MIGRATIONS],
          str(MIGRATED))
    check("migration: the first admin was made from env for the existing rows",
          ADMIN is not None and ADMIN.level.see_everyone and ADMIN.telegram_chat_id == 1111
          and users.by_telegram_id(111).id == ADMIN.id)
    check("migration: 8.1.0 sessions, thread, phrases and usage are the first admin's",
          all(owners(t) == {ADMIN.id} for t in ("thread_messages", "shortcuts", "usage_events"))
          and owners("sessions") >= {ADMIN.id} and len(owners("sessions")) == 1,
          str({t: owners(t) for t in ("sessions", "thread_messages", "shortcuts", "usage_events")}))
    status, body = await get(client, ADMIN_TOKEN, "/api/sessions/main")
    check("migration: the admin's main thread holds the 8.1.0 messages",
          status == 200 and [m["message"] for m in body["messages"]][:2] == ["Open the gate", "Opened."], str(body)[:300])
    status, body = await get(client, ADMIN_TOKEN, "/api/sessions/s_web_1")
    check("migration: the admin still opens an old session", status == 200 and body["messages"])
    check("no interim owner any more", not hasattr(users, "owner_of_unscoped_data"))


# --- 2. Own sessions and own main --------------------------------------------------------

RUN_IDS = {}


async def test_own_sessions(client):
    fake.load(answer("Hi admin."))
    status, frames = await send(client, ADMIN_TOKEN, "s_admin", "admin private question")
    check("sessions: the admin's message runs", status == 200 and frame(frames, "final")["content"] == "Hi admin.")
    fake.load(answer("Hi member."))
    status, frames = await send(client, MEMBER_TOKEN, "s_member", "member private question")
    check("sessions: the member's message runs", status == 200 and frame(frames, "final")["content"] == "Hi member.")

    _, admin_list = await get(client, ADMIN_TOKEN, "/api/sessions")
    _, member_list = await get(client, MEMBER_TOKEN, "/api/sessions")
    a_ids = {s["session_id"] for s in admin_list}
    m_ids = {s["session_id"] for s in member_list}
    check("sessions: each user lists only their own", {"s_admin", "s_web_1"} <= a_ids and "s_member" not in a_ids
          and m_ids == {"s_member"}, f"{a_ids} / {m_ids}")

    fake.load(answer("Fresh."))
    status, _ = await send(client, MEMBER_TOKEN, "s_admin2", "a fresh id")
    check("sessions: a new id is free for anyone", status == 200)


async def test_own_thread(client):
    fake.load(answer("Noted, admin.", delay=0.4), answer("Noted, member."))
    admin_task = asyncio.create_task(send(client, ADMIN_TOKEN, "main", "admin says pineapple", device="phone"))
    await asyncio.sleep(0.15)
    m_status, m_frames = await send(client, MEMBER_TOKEN, "main", "member says coconut", device="watch")
    a_status, a_frames = await admin_task
    check("main: both users talk to `main` at once (no 409: one thread each)",
          a_status == 200 and m_status == 200, f"{a_status} {m_status}")
    RUN_IDS["admin_main"] = a_frames[0]["run_id"]
    member_call = next(c for c in fake.calls if "coconut" in sent_text(c))
    check("main: the member's model call never sees the admin's thread",
          "pineapple" not in sent_text(member_call) and "Open the gate" not in sent_text(member_call))

    _, a_main = await get(client, ADMIN_TOKEN, "/api/sessions/main")
    _, m_main = await get(client, MEMBER_TOKEN, "/api/sessions/main")
    a_msgs = [m["message"] for m in a_main["messages"]]
    m_msgs = [m["message"] for m in m_main["messages"]]
    check("main: the wire id `main` resolves to each user's own thread",
          "admin says pineapple" in a_msgs and "member says coconut" not in a_msgs
          and m_msgs == ["member says coconut", "Noted, member."] and m_main.get("thread") is True, str(m_msgs))


# --- 3. Someone else's ids ---------------------------------------------------------------

async def test_foreign_ids(client):
    for path in ("/api/sessions/s_admin", "/api/sessions/s_admin/stream", "/api/debug/sessions/s_admin"):
        status, _ = await get(client, MEMBER_TOKEN, path)
        check(f"404: GET {path} as another user", status == 404, str(status))
    status, _ = await send(client, MEMBER_TOKEN, "s_admin", "let me in")
    check("404: a message to another user's session", status == 404, str(status))
    status, _ = await approve(client, MEMBER_TOKEN, "s_admin", "cancel")
    check("404: cancelling another user's session", status == 404, str(status))
    r = await client.delete("/api/sessions/s_admin", headers=auth(MEMBER_TOKEN))
    r2 = await client.delete("/api/debug/sessions/s_admin", headers=auth(MEMBER_TOKEN))
    status, _ = await get(client, ADMIN_TOKEN, "/api/sessions/s_admin")
    check("404: deleting another user's session (it stays)", r.status == 404 and r2.status == 404 and status == 200,
          f"{r.status} {r2.status} {status}")
    _, debug_list = await get(client, MEMBER_TOKEN, "/api/debug/sessions")
    ids = [d.get("session_id") for d in (debug_list.get("sessions", debug_list) if isinstance(debug_list, dict)
                                         else debug_list)]
    check("debug: the list holds only the caller's sessions", "s_admin" not in ids and "s_web_1" not in ids, str(ids))
    status, _ = await get(client, ADMIN_TOKEN, "/api/debug/sessions/s_admin")
    check("debug: the owner still reads it", status == 200)

    run_id = RUN_IDS["admin_main"]
    status, _ = await get(client, MEMBER_TOKEN, f"/api/sessions/main/stream?run_id={run_id}&after=0")
    check("404: reattaching to another user's run (even on `main`)", status == 404, str(status))
    res = await client.get(f"/api/sessions/main/stream?run_id={run_id}&after=0", headers=auth(ADMIN_TOKEN))
    frames = await read_sse(res)
    check("runs: the owner reattaches to it", res.status == 200 and frame(frames, "final") is not None)

    fake.load(*tool_turn("ch1", "zz_mu_change", {"value": "admin's"}, reply="Changed."))
    status, frames = await send(client, ADMIN_TOKEN, "s_admin", "change it")
    undo_id = (frame(frames, "tool_end").get("undos") or [{}])[0].get("id")
    check("undo: the admin's change has an undo id", status == 200 and undo_id and CHANGED["value"] == "admin's")
    r = await client.post(f"/api/undo/{undo_id}", headers=auth(MEMBER_TOKEN))
    check("404: undoing another user's change (nothing reversed)", r.status == 404 and CHANGED["value"] == "admin's",
          str(r.status))
    RUN_IDS["admin_undo"] = undo_id


# --- 4. Approvals ------------------------------------------------------------------------

async def test_approvals_http(client):
    fake.load([tool_chunk("snd1", "zz_mu_send", {"text": "hello Gal"})])
    status, frames = await send(client, ADMIN_TOKEN, "s_appr", "tell Gal hello")
    check("approval: a confirm tool suspends the admin's session",
          status == 200 and frames[-1]["state"]["status"] == "waiting_approval")
    status, _ = await approve(client, MEMBER_TOKEN, "s_appr")
    check("approval: another user approving it gets 404, nothing sent", status == 404 and not SENT, str(status))

    fake.load([tool_chunk("snd2", "zz_mu_send", {"text": "main thing"})])
    await send(client, ADMIN_TOKEN, "main", "send the main thing")
    _, m_main = await get(client, MEMBER_TOKEN, "/api/sessions/main")
    status, body = await approve(client, MEMBER_TOKEN, "main")
    _, a_main = await get(client, ADMIN_TOKEN, "/api/sessions/main")
    check("approval: the admin's waiting main is not the member's main",
          m_main["pending_approval"] is None and status == 400 and a_main["pending_approval"] is not None,
          f"{status} {body}")

    fake.load(answer("Sent it."))
    status, frames = await approve(client, ADMIN_TOKEN, "s_appr")
    check("approval: the owner approves; the tool runs as them",
          status == 200 and SENT == [(ADMIN.id, "hello Gal")] and frame(frames, "final")["content"] == "Sent it.", str(SENT))
    fake.load(answer("Not sent."))
    await approve(client, ADMIN_TOKEN, "main", "deny")


async def test_approvals_telegram():
    bot = TelegramBot.__new__(TelegramBot)
    bot.bot = AsyncMock()
    bot.selection_toggles = {}
    bot.user_memory_states = {}
    bot._handle_agent_result = AsyncMock()
    session.set_pending(ADMIN.id, "tg_-500", {"status": "pending_approval", "tool_name": "zz_mu_send",
                                             "tool_args": {"text": "group hello"}, "chat_id": -500})
    message = types.Message(message_id=7, date=datetime.datetime.now(datetime.timezone.utc),
                            chat=types.Chat(id=-500, type="group"))
    started = []

    def fake_resume(user, session_id, action, device=""):
        started.append((user.id, session_id, action, users.current_id()))
        done = asyncio.get_running_loop().create_future()
        done.set_result(("Sent.", []))
        return SimpleNamespace(task=done)

    async def press(telegram_id, data):
        cb = SimpleNamespace(from_user=SimpleNamespace(id=telegram_id), data=data, answer=AsyncMock(), message=message)
        await AuthMiddleware()(lambda event, d: bot.handle_approval_callback(event, d["user"]), cb, {})
        return cb

    with patch.object(types.Message, "edit_text", new=AsyncMock()) as edit, \
            patch.object(tb, "start_resume_task", new=fake_resume):
        cb = await press(222, "approve_action")
        check("telegram: another user's press on the buttons gets an alert, nothing runs",
              cb.answer.await_args.kwargs.get("show_alert") is True and not started and not edit.await_count
              and session.get_pending(ADMIN.id, "tg_-500") is not None, str(cb.answer.await_args))
        await press(111, "approve_action")
        check("telegram: the owner's press resumes their run, as them",
              started == [(ADMIN.id, "tg_-500", "approve", ADMIN.id)], str(started))

        undo_id = RUN_IDS["admin_undo"]
        cb = SimpleNamespace(from_user=SimpleNamespace(id=222), data=f"undo:{undo_id}", answer=AsyncMock(),
                             message=None)
        await AuthMiddleware()(lambda event, d: bot.handle_undo_callback(event), cb, {})
        check("telegram: another user's Undo button press is refused",
              cb.answer.await_args.kwargs.get("show_alert") is True and CHANGED["value"] == "admin's")
        cb = SimpleNamespace(from_user=SimpleNamespace(id=111), data=f"undo:{undo_id}", answer=AsyncMock(),
                             message=None)
        await AuthMiddleware()(lambda event, d: bot.handle_undo_callback(event), cb, {})
        check("telegram: the owner's Undo works", CHANGED["value"] is None, str(cb.answer.await_args))


# --- 5. Learned phrases ------------------------------------------------------------------

LIGHTS = {"domain": "light", "service": "turn_on", "entity_id": "light.kitchen"}


def lights_entries(args):
    return [{"role": "assistant", "content": None, "tool_calls": [{"id": "l", "type": "function",
             "function": {"name": "call_ha_service", "arguments": json.dumps(args)}}]},
            {"role": "tool", "tool_call_id": "l", "name": "call_ha_service", "content": "Done."},
            {"role": "assistant", "content": "On."}]


async def test_shortcuts(client):
    # Both users have Home Assistant here, so phrases are kept apart by user, not by
    # integration (a phrase whose tool the user lacks is covered in test_access.py).
    store.connect(MEMBER, "homeassistant", credentials={"host": "http://ha.invalid", "token": "fake"})
    try:
        await _test_shortcuts(client)
    finally:
        store.disconnect(MEMBER, "homeassistant")


async def _test_shortcuts(client):
    outcomes = [shortcuts.learn(ADMIN, "kitchen lights", lights_entries(LIGHTS), "On.") for _ in range(3)]
    check("phrases: learned for the admin", outcomes[-1] == "activated", str(outcomes))
    check("phrases: matched only for the user who taught them",
          shortcuts.match(ADMIN, "kitchen lights") and shortcuts.match(MEMBER, "kitchen lights") is None
          and shortcuts.match(MEMBER, "open the gate") is None)

    n_calls = len(fake.calls)
    fake.load(answer("Which lights?"))
    status, frames = await send(client, MEMBER_TOKEN, "s_member", "kitchen lights")
    check("phrases: the member's same words go to the model",
          len(fake.calls) == n_calls + 1 and not frames[-1]["state"].get("shortcut") and not HA)
    status, frames = await send(client, ADMIN_TOKEN, "s_admin", "kitchen lights")
    check("phrases: the admin's run without the model, as the admin",
          len(fake.calls) == n_calls + 1 and frames[-1]["state"].get("shortcut") is True
          and HA == [(ADMIN.id, "light.kitchen")], str(HA))

    other = dict(LIGHTS, entity_id="light.porch")
    shortcuts.learn(MEMBER, "kitchen lights", lights_entries(other), "On.")
    check("phrases: the member learning the same words leaves the admin's alone",
          shortcuts.match(ADMIN, "kitchen lights")["args"]["entity_id"] == "light.kitchen")

    _, a_list = await get(client, ADMIN_TOKEN, "/api/shortcuts")
    _, m_list = await get(client, MEMBER_TOKEN, "/api/shortcuts")
    a_item = next(s for s in a_list["shortcuts"] if s["phrase"] == "kitchen lights")
    check("phrases: each user lists their own",
          {s["phrase"] for s in a_list["shortcuts"]} == {"Open the gate", "kitchen lights"}
          and [s["seen"] for s in m_list["shortcuts"]] == [1], json.dumps(m_list, ensure_ascii=False)[:300])
    r = await client.delete(f"/api/shortcuts/{a_item['id']}", headers=auth(MEMBER_TOKEN))
    check("phrases: deleting another user's phrase is 404 (it stays)",
          r.status == 404 and shortcuts.match(ADMIN, "kitchen lights") is not None)


# --- 6. Usage ------------------------------------------------------------------------------

def usage_count(user_id=None):
    """Calls stored for ``user_id`` (None: everyone), raw rows plus rolled-up days."""
    usage.flush_sync()
    where, params = ("WHERE user_id = ?", (user_id,)) if user_id is not None else ("", ())
    with get_db_connection() as conn:
        raw = conn.execute(f"SELECT COUNT(*) FROM usage_events {where}", params).fetchone()[0]
        daily = conn.execute(f"SELECT COALESCE(SUM(calls), 0) FROM usage_daily {where}", params).fetchone()[0]
    return raw + daily


async def test_usage(client):
    fake.load(*tool_turn("who1", "zz_mu_whoami", {}, reply="You are you."))
    await send(client, MEMBER_TOKEN, "s_member", "who am I?")
    check("usage: a tool in the member's run runs as the member", WHO == [MEMBER.id], str(WHO))
    with users.acting_as(ADMIN):
        usage.record(kind="llm", purpose="chat", model="zz", cost=0.02)
    usage.record(kind="llm", purpose="people_brief", model="zz", cost=0.04)
    usage.flush_sync()
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT user_id, model, purpose FROM usage_events WHERE model LIKE 'zz%'")]
    check("usage: rows carry the user (and '' for work with no user)",
          sorted((r["user_id"], r["model"]) for r in rows)
          == sorted([(MEMBER.id, "zz-tool"), (ADMIN.id, "zz"), ("", "zz")]), str(rows))

    n_admin, n_member, n_all = usage_count(ADMIN.id), usage_count(MEMBER.id), usage_count()
    status, body = await get(client, MEMBER_TOKEN, "/api/usage?range=all")
    check("/api/usage: the member sees only their own by default",
          status == 200 and body["totals"]["calls"] == n_member == 1, f"{status} {body and body.get('totals')}")
    status, body = await get(client, ADMIN_TOKEN, "/api/usage?range=all")
    check("/api/usage: the admin sees their own by default (8.1.0 rows included)",
          status == 200 and body["totals"]["calls"] == n_admin and n_admin > 1, f"{body and body.get('totals')} {n_admin}")
    status, body = await get(client, ADMIN_TOKEN, f"/api/usage?range=all&user={MEMBER.id}")
    check("/api/usage: the admin may read one user's", status == 200 and body["totals"]["calls"] == n_member)
    status, body = await get(client, ADMIN_TOKEN, "/api/usage?range=all&user=all")
    check("/api/usage: the admin may read everyone's (no-user rows included)",
          status == 200 and body["totals"]["calls"] == n_all == n_admin + n_member + 1, f"{body and body.get('totals')}")
    status, _ = await get(client, ADMIN_TOKEN, "/api/usage?range=all&user=nobody")
    check("/api/usage: an unknown user is 404", status == 404)
    for q in ("user=all", f"user={ADMIN.id}"):
        for path in ("/api/usage?range=all&", "/api/usage/events?range=all&"):
            status, body = await get(client, MEMBER_TOKEN, path + q)
            check(f"/api/usage: a member asking for {q.split('=')[1][:8]} on {path.split('?')[0]} gets 403",
                  status == 403 and body.get("ok") is False, str(status))
    status, body = await get(client, MEMBER_TOKEN, f"/api/usage?range=all&user={MEMBER.id}")
    check("/api/usage: naming yourself is fine", status == 200 and body["totals"]["calls"] == n_member)
    status, body = await get(client, MEMBER_TOKEN, "/api/usage/events?range=all")
    check("/api/usage/events: the member's own calls only, same event shape",
          status == 200 and [e["model"] for e in body["events"]] == ["zz-tool"] and "user_id" not in body["events"][0])


# --- 7. notify ----------------------------------------------------------------------------

class FakeChannel:
    def __init__(self):
        self.send_text = AsyncMock()
        self.notify_whatsapp_voice = AsyncMock(return_value=True)


async def test_notify():
    channel = FakeChannel()
    notify.register_telegram(channel)
    try:
        ok_a = await notify.notify(ADMIN, "for the admin")
        ok_m = await notify.notify(MEMBER, "for the member", parse_mode="Markdown")
        check("notify: each user's text goes to their own chat",
              ok_a and ok_m and [c.args[:2] for c in channel.send_text.await_args_list]
              == [(1111, "for the admin"), (2222, "for the member")]
              and channel.send_text.await_args_list[1].kwargs == {"parse_mode": "Markdown"},
              str(channel.send_text.await_args_list))
        n = channel.send_text.await_count
        check("notify: a user without a chat gets nothing", await notify.notify(NO_CHAT, "x") is False
              and channel.send_text.await_count == n)
        channel.send_text.side_effect = RuntimeError("telegram down")
        check("notify: a failed send is False, never raises", await notify.notify(ADMIN, "x") is False)
    finally:
        notify.register_telegram(None)
    check("notify: no channel registered → nothing, False", await notify.notify(ADMIN, "x") is False)


# --- 8. A background entry point ------------------------------------------------------------

async def test_background_entry():
    store.connect(MEMBER, "tasks")  # the member's own tasks (default lists come with it)
    with get_db_connection() as conn:
        conn.execute("INSERT INTO tasks (id, title, status, triggers, group_id, user_id, created_by) VALUES "
                     "('t_car', 'Take the umbrella', 'needs_action', ?, ?, ?, ?)",
                     (json.dumps({"car": "enter"}), task_groups.default_group_id(MEMBER), MEMBER.id, MEMBER.id))
        conn.commit()
    channel = FakeChannel()
    notify.register_telegram(channel)
    seen = []
    original = task_triggers.trigger_reminder

    async def watched(item, trigger_type, user, target=""):
        seen.append((user.id, users.current_id()))
        await original(item, trigger_type, user, target)

    try:
        with patch.object(task_triggers.phone_push, "send", new=AsyncMock(return_value=True)), \
                patch.object(task_triggers, "trigger_reminder", new=watched):
            # The member's Home Assistant watcher sees their phone connect to the car.
            spec = ha_watcher.WatchSpec("http://ha.invalid", "fake", None, "sensor.bt", "Car")
            watcher = ha_watcher.UserWatcher(Connection(user=MEMBER, kind="homeassistant"), spec,
                                             task_triggers.fire, {})

            async def scripted_session():
                states = {}
                await watcher._on_event({"a": {"sensor.bt": {"s": "on", "a": {"connected_paired_devices": "[]"}}}},
                                        states, True)
                await watcher._on_event({"c": {"sensor.bt": {"+": {"a": {"connected_paired_devices": "['Car']"}}}}},
                                        states, False)
                await asyncio.Event().wait()

            watcher._session = scripted_session
            running = asyncio.create_task(watcher.run())
            for _ in range(40):
                if channel.send_text.await_count:
                    break
                await asyncio.sleep(0.05)
            running.cancel()
    finally:
        notify.register_telegram(None)
    check("background: a Home Assistant watcher's trigger runs as its user",
          seen == [(MEMBER.id, MEMBER.id)], str(seen))
    calls = channel.send_text.await_args_list
    check("background: its reminder reaches that user's chat",
          len(calls) == 1 and calls[0].args[0] == 2222 and "Take the umbrella" in calls[0].args[1], str(calls))
    check("background: outside it no user is bound", users.current() is None)


async def main():
    client = TestClient(TestServer(build_app()))
    await client.start_server()
    try:
        await test_migration(client)
        await test_own_sessions(client)
        await test_own_thread(client)
        await test_foreign_ids(client)
        await test_approvals_http(client)
        await test_approvals_telegram()
        await test_shortcuts(client)
        await test_usage(client)
        await test_notify()
        await test_background_entry()
    finally:
        await client.close()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
