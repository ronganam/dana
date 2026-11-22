"""About-you facts per user (docs/about.md, docs/access.md "Addons").

A temporary database that starts as the 9.0 fixture (its two facts and the
learner's state must become the first admin's, and the first admin owns the
old ``memories``), then users: the first admin Robin (every official addon),
Bob and Eve (``about``), Carl (no ``about``). Through the agent tools (via
the registry, as the model would call them), the real aiohttp handlers
(``/api/about``, the old ``/api/memories``, undo), the Telegram ``/memory``
command and buttons (network faked), the agent's real system message (the
model faked at ``litellm.acompletion``) and the learner (LiteLLM's offline
``mock_response``, so the real usage callback records each call).

Covers: the migration (owners, state, old-memory owner, nothing re-imported);
isolation of facts in both directions for every tool, endpoint and Telegram
path; another user's fact id is "not found" (404 / no match) and their undo
id 404; forgotten facts and "do not re-add" per user; the size cap per user;
each user's system message carries only their own facts under their own name,
none without the addon or after disconnecting (back on reconnect); 403
``not_connected`` and tools not offered without the addon; the old memories
only for their owner; the learner per user (their own chats only, calls only
for the owner of calls, separate bookmarks and back-off, usage rows carrying
the right user, a failing user not stopping the next, users without the addon
skipped).

Run: bin/test test_about_users
"""

import asyncio
import datetime
import json
import os
import sqlite3
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("about_users_test")
ROOT = str(testenv.SERVER)
os.environ["API_TOKEN"] = "about-users-test-env-token"  # a test value: the first admin's token (a headless install)
os.environ["ANTHROPIC_API_KEY"] = "test-key"  # a test value: LiteLLM only ever runs its offline mock here
os.environ["OWNER_NAME"] = "Robin"
os.environ["ALLOWED_TELEGRAM_USER_IDS"] = "111"
os.environ["ABOUT_MAX_FACTS"] = "6"
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, ROOT)

# A 9.0 production database (converted at start): its facts and state are the first admin's, Robin, who uses
# Telegram as 111 (what 9.0 took from the env above when it made them).
_conn = sqlite3.connect(DB_PATH)
with open(os.path.join(testenv.FIXTURES, "db_9_0.sql"), encoding="utf-8") as _f:
    _conn.executescript(_f.read())
_conn.execute("UPDATE users SET name = 'Robin'")
_conn.execute("INSERT INTO user_telegram (telegram_user_id, user_id, created_at) SELECT 111, id, created_at FROM users")
_conn.commit()
_conn.close()

import litellm
from aiogram import types
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices

from core import db, users

db.migrate()
users.bootstrap()

from addons import bootstrap as addons_bootstrap

addons_bootstrap.bootstrap()

import core.telegram_bot as tb
from core import actions_api, usage
from core.agent import AgentOrchestrator
from core.auth import setup_auth
from core.db import get_db_connection
from core.telegram_bot import AuthMiddleware, TelegramBot
from addons.about import api as about_api, facts, learner, legacy, prompt
from core.addons import registry, store

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: str = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ADMIN = users.first_admin()
assert ADMIN is not None
LOOP = asyncio.new_event_loop()


def run(coro):
    return LOOP.run_until_complete(coro)


def texts(user, status: str = "active") -> List[str]:
    return [f["text"] for f in facts.list_facts(user, status)]


def tool(user, name: str, args: Dict[str, Any]) -> str:
    return run(registry.execute(user, name, args))


# --- 1. Migration -------------------------------------------------------------------------------

def test_migration() -> None:
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, user_id, text FROM about_facts ORDER BY id")]
        state = [dict(r) for r in conn.execute("SELECT user_id, key FROM about_state ORDER BY key")]
    check("migration: the fixture's facts are the first admin's, ids kept",
          [(r["id"], r["user_id"]) for r in rows] == [("f1", ADMIN.id), ("f2", ADMIN.id)], str(rows))
    check("migration: the learner's state is the first admin's",
          {(s["user_id"], s["key"]) for s in state} == {(ADMIN.id, "learn_bookmark"), (ADMIN.id, "legacy_memories")},
          str(state))
    check("migration: the first admin owns the old memories", legacy.owner_id() == ADMIN.id)
    check("migration: the old memory already copied is not imported again", legacy.migrate_legacy() == 0
          and texts(ADMIN) == ["Sam is a friend from the hiking club", "Favorite color: blue"], str(texts(ADMIN)))
    facts.load_prompts()
    section = prompt.section(ADMIN)
    check("migration: the first admin's prompt block has their facts under their name",
          "--- ABOUT ROBIN (what you know) ---" in section and "- Sam is a friend from the hiking club" in section,
          section)


test_migration()

BOB = users.create("Bob", telegram_ids=[222], telegram_chat_id=2222)
EVE = users.create("Eve", telegram_ids=[333], telegram_chat_id=3333)
CARL = users.create("Carl", telegram_ids=[444], telegram_chat_id=4444)
for _u in (BOB, EVE):
    store.connect(_u, "about")
store.connect(CARL, "tasks")
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ADMIN, BOB, EVE, CARL)}


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[user.id]}"}


# --- 2. Tools --------------------------------------------------------------------------------------

def test_tools() -> None:
    about_tools = {"remember_info", "recall_info", "forget_info", "list_memories"}
    carl_tools = {t.name for t in registry.tools_for(CARL)}
    check("tools: not offered without about", not (about_tools & carl_tools), str(carl_tools & about_tools))
    try:
        tool(CARL, "remember_info", {"fact": "Carl likes tea"})
        refused = False
    except registry.ToolNotAvailable:
        refused = True
    check("tools: refused without about, like an unknown tool", refused)

    res = tool(BOB, "remember_info", {"fact": "Gal is Bob's brother", "section": "people"})
    check("tools: Bob saves a fact", "Saved in About you" in res and texts(BOB) == ["Gal is Bob's brother"], res)
    res = tool(EVE, "remember_info", {"fact": "Gal is Bob's brother", "section": "people"})
    check("tools: the same text is a separate fact for Eve (dedupe is per user)",
          "Saved in About you" in res and texts(EVE) == ["Gal is Bob's brother"], res)
    check("tools: Bob's and Eve's facts never reach the first admin",
          texts(ADMIN) == ["Sam is a friend from the hiking club", "Favorite color: blue"], str(texts(ADMIN)))

    res = tool(BOB, "list_memories", {})
    check("tools: list_memories lists only Bob's own", "Gal is Bob" in res and "Sam" not in res, res)
    res = tool(ADMIN, "list_memories", {})
    check("tools: the first admin's list has none of Bob's", "Sam is a friend" in res and "Gal" not in res, res)
    res = tool(BOB, "recall_info", {"query": "Sam hiking"})
    check("tools: recall_info never finds another user's fact", res.startswith("Nothing in About you"), res)
    res = tool(ADMIN, "recall_info", {"query": "Gal"})
    check("tools: ... in both directions", res.startswith("Nothing in About you"), res)

    res = tool(BOB, "forget_info", {"fact": "f1"})
    check("tools: another user's fact id is no match", res.startswith("No fact matches")
          and "Sam is a friend from the hiking club" in texts(ADMIN), res)
    res = tool(BOB, "forget_info", {"fact": "Sam is a friend from the hiking club"})
    check("tools: another user's fact text is no match (at most the caller's own candidates)",
          not res.startswith("Forgot") and "hiking" not in res and "id f1)" not in res
          and "Sam is a friend from the hiking club" in texts(ADMIN), res)
    res = tool(BOB, "remember_info", {"fact": "Sam is a neighbour", "replaces": "f1"})
    check("tools: replaces never rewrites another user's fact",
          facts.get_fact(ADMIN, "f1")["text"] == "Sam is a friend from the hiking club"
          and "Sam is a neighbour" in texts(BOB), res)

    # Old memories: only their owner (the first admin) sees or deletes them.
    with get_db_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO memories (key, value, category) VALUES ('wifi_password', 'hunter22hunter', 'general')")
        conn.commit()
    res = tool(ADMIN, "recall_info", {"query": "wifi"})
    check("tools: the owner of the old memories still gets them as older notes", "hunter22hunter" in res, res)
    res = tool(BOB, "recall_info", {"query": "wifi"})
    check("tools: nobody else sees the old memories", "hunter22hunter" not in res, res)
    res = tool(BOB, "forget_info", {"key": "wifi_password"})
    with get_db_connection() as conn:
        kept = conn.execute("SELECT 1 FROM memories WHERE key = 'wifi_password'").fetchone() is not None
    check("tools: nobody else deletes an old memory", kept and "Deleted old memory" not in res
          and "hunter" not in res, res)


# --- 3. Forget / do-not-re-add and undo per user ---------------------------------------------------

def test_forget_per_user() -> None:
    res = tool(BOB, "forget_info", {"fact": "Gal is Bob's brother"})
    check("forget: Bob forgets his fact", res.startswith("Forgot") and "Gal is Bob's brother" in texts(BOB, "forgotten"))
    check("forget: Eve's copy stays active", "Gal is Bob's brother" in texts(EVE))
    check("forget: forgotten for Bob only", facts.is_forgotten(BOB, "Gal is Bob's brother")
          and not facts.is_forgotten(EVE, "Gal is Bob's brother") and not facts.is_forgotten(ADMIN, "Gal is Bob's brother"))
    res = tool(BOB, "remember_info", {"fact": "Gal is Bob's brother."})
    check("forget: Bob's tool will not save it again", "removed this" in res and "Gal is Bob's brother." not in texts(BOB), res)
    res = tool(ADMIN, "remember_info", {"fact": "Gal is Bob's brother"})
    check("forget: another user may still save the same text", "Saved in About you" in res, res)
    tool(ADMIN, "forget_info", {"fact": "Gal is Bob's brother"})
    check("forget: Bob's do-not-re-add list is only his",
          [f["text"] for f in facts.list_facts(BOB, "forgotten")] == ["Gal is Bob's brother"]
          and [f["text"] for f in facts.list_facts(EVE, "forgotten")] == [])


# --- 4. Size cap per user ---------------------------------------------------------------------------

def test_cap_per_user() -> None:
    eve_before = texts(EVE)
    admin_before = texts(ADMIN)
    words = ["apple", "banana", "cherry", "dates", "elder", "figs", "grape", "hazel", "irises"]
    for w in words:
        facts.add_fact(BOB, f"Bob's {w} orchard note number {w}", "other", source_kind="chat")
    check("cap: Bob's active facts stay under ABOUT_MAX_FACTS", len(texts(BOB)) == 6, str(len(texts(BOB))))
    check("cap: Bob's oldest learned facts are dropped", len(facts.list_facts(BOB, "dropped")) >= 3)
    check("cap: other users' facts are untouched", texts(EVE) == eve_before and texts(ADMIN) == admin_before
          and not facts.list_facts(EVE, "dropped") and not facts.list_facts(ADMIN, "dropped"))


# --- 5. The prompt section in each user's system message --------------------------------------------

captured: List[Dict[str, Any]] = []
MODEL_USERS: List[str] = []
LEARN_REPLY: Dict[str, Any] = {"fail_for": None, "reply": {}}
_real_acompletion = litellm.acompletion


async def fake_acompletion(**kwargs):
    if kwargs.get("stream"):  # the agent loop: capture its system message
        captured.append(kwargs)

        async def gen():
            yield ModelResponseStream(model="fake", choices=[StreamingChoices(
                index=0, delta=Delta(content="Done."), finish_reason="stop")])
        return gen()
    # The learner: LiteLLM's offline mock, so the real usage callback records the call.
    current = users.current()
    MODEL_USERS.append(current.id if current else "")
    prompt_text = kwargs["messages"][0]["content"]
    LEARN_REPLY.setdefault("prompts", {})[current.id if current else ""] = prompt_text
    if current is not None and LEARN_REPLY["fail_for"] == current.id:
        kwargs["mock_response"] = Exception("model down")
    else:
        reply = LEARN_REPLY["reply"].get(current.id if current else "", {"add": [], "update": []})
        kwargs["mock_response"] = json.dumps(reply, ensure_ascii=False)
    return await _real_acompletion(**kwargs)


litellm.acompletion = fake_acompletion
litellm.suppress_debug_info = True


def system_message(user) -> str:
    captured.clear()
    with users.acting_as(user):
        run(AgentOrchestrator(model="fake/model").run("hello", [{"role": "user", "content": "hello"}]))
    return captured[0]["messages"][0]["content"]


def test_prompt_per_user() -> None:
    tool(BOB, "remember_info", {"fact": "Pepper is Bob's dog", "section": "home"})
    tool(EVE, "remember_info", {"fact": "Eve's car is the blue Mazda", "section": "home"})
    bob, eve, admin = system_message(BOB), system_message(EVE), system_message(ADMIN)
    check("prompt: Bob's system message has his facts under his name",
          "--- ABOUT BOB (what you know) ---" in bob and "Things you know about Bob." in bob
          and "- Pepper is Bob's dog" in bob, bob[-900:])
    check("prompt: Bob's has nobody else's facts or name",
          "Mazda" not in bob and "Sam is a friend" not in bob and "ABOUT ROBIN" not in bob and "ABOUT EVE" not in bob)
    check("prompt: Eve's has only hers", "--- ABOUT EVE" in eve and "Mazda" in eve and "Pepper" not in eve
          and "Sam is a friend" not in eve)
    check("prompt: the first admin's has only theirs under their name",
          "--- ABOUT ROBIN" in admin and "Sam is a friend" in admin and "Pepper" not in admin and "Mazda" not in admin)
    with get_db_connection() as conn:  # a fact row Carl could only have from an earlier connection
        conn.execute("INSERT INTO about_facts (id, user_id, section, text, status, source_kind, created_at, updated_at) "
                     "VALUES ('carl1', ?, 'other', 'Carl likes tea', 'active', 'owner', 'a', 'a')", (CARL.id,))
        conn.commit()
    facts.refresh_prompt(CARL)
    carl = system_message(CARL)
    check("prompt: no About section without the addon (even with fact rows)",
          "ABOUT" not in carl.split("Active Context:")[0][-2000:] and "Carl likes tea" not in carl
          and registry.prompt_section(CARL, "about") == "")
    store.disconnect(BOB, "about")
    check("prompt: none after disconnecting", "Pepper" not in system_message(BOB)
          and registry.prompt_section(BOB, "about") == "")
    prompt.clear()  # as after a restart: the cache is empty until something builds it
    store.connect(BOB, "about")
    check("prompt: back on reconnect (on_connect builds the block)", "- Pepper is Bob's dog" in system_message(BOB))
    facts.load_prompts()
    check("prompt: load_prompts builds the block of everyone with the addon, nobody else's",
          "Mazda" in prompt.section(EVE) and "Sam is a friend" in prompt.section(ADMIN) and prompt.section(CARL) == "")


# --- 6. HTTP ----------------------------------------------------------------------------------------

async def test_http() -> None:
    app = web.Application()
    setup_auth(app)
    about_api.setup(app)
    actions_api.setup(app)
    app.router.add_get("/api/memories", about_api.get_memories_api)
    app.router.add_post("/api/memories", about_api.create_memory_api)
    app.router.add_delete("/api/memories/{key}", about_api.delete_memory_api)
    app.on_startup.clear()  # the learner loop is tested directly below
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/api/about", headers=auth(BOB))
        body = await r.json()
        bob_texts = [f["text"] for s in body["sections"] for f in s["facts"]]
        check("http: GET /api/about answers with the caller's own facts", r.status == 200
              and "Pepper is Bob's dog" in bob_texts and not any("Sam is a friend" in t or "Mazda" in t for t in bob_texts),
              str(bob_texts))
        r = await client.get("/api/about", headers=auth(ADMIN))
        admin_texts = [f["text"] for s in (await r.json())["sections"] for f in s["facts"]]
        check("http: ... and the first admin's with only theirs",
              "Sam is a friend from the hiking club" in admin_texts and "Pepper is Bob's dog" not in admin_texts)
        for method, path in (("GET", "/api/about"), ("POST", "/api/about/facts"), ("PUT", "/api/about/facts/f1"),
                             ("DELETE", "/api/about/facts/f1"), ("GET", "/api/memories"),
                             ("POST", "/api/memories"), ("DELETE", "/api/memories/fact_f1")):
            r = await client.request(method, path, headers=auth(CARL), json={"text": "x", "key": "k", "value": "v"})
            check(f"http: {method} {path} without about → 403 not_connected", r.status == 403
                  and (await r.json()) == {"error": "not_connected", "addon": "about"})

        r = await client.post("/api/about/facts", headers=auth(EVE), json={"text": "Eve swims on Mondays"})
        body = await r.json()
        eve_fact, eve_undo = body["fact"]["id"], body["undo_id"]
        check("http: POST adds to the caller", r.status == 201 and "Eve swims on Mondays" in texts(EVE)
              and "Eve swims on Mondays" not in texts(BOB))
        r = await client.post(f"/api/undo/{eve_undo}", headers=auth(BOB))
        check("http: another user's undo id → 404", r.status == 404 and "Eve swims on Mondays" in texts(EVE))
        r = await client.put("/api/about/facts/f1", headers=auth(BOB), json={"text": "Sam is nobody"})
        check("http: PUT another user's fact → 404, unchanged", r.status == 404
              and facts.get_fact(ADMIN, "f1")["text"] == "Sam is a friend from the hiking club")
        r = await client.delete(f"/api/about/facts/{eve_fact}", headers=auth(BOB))
        check("http: DELETE another user's fact → 404, still active", r.status == 404
              and facts.get_fact(EVE, eve_fact)["status"] == "active")
        r = await client.delete(f"/api/about/facts/{eve_fact}", headers=auth(EVE))
        check("http: the owner deletes it", r.status == 200 and facts.get_fact(EVE, eve_fact)["status"] == "forgotten")

        r = await client.get("/api/memories", headers=auth(BOB))
        mems = await r.json()
        check("http: GET /api/memories lists the caller's facts only", r.status == 200
              and {m["key"] for m in mems} == {f"fact_{f['id']}" for f in facts.list_facts(BOB)}, str(mems)[:300])
        r = await client.post("/api/memories", headers=auth(BOB), json={"key": "fact_f1", "value": "Sam is gone",
                                                                         "category": "people"})
        key = (await r.json())["key"]
        check("http: POST /api/memories with another user's fact key adds to the caller instead",
              facts.get_fact(ADMIN, "f1")["text"] == "Sam is a friend from the hiking club"
              and facts.get_fact(BOB, key[5:])["text"] == "Sam is gone")
        r = await client.delete("/api/memories/fact_f1", headers=auth(BOB))
        check("http: DELETE /api/memories/fact_<another user's id> → 404", r.status == 404
              and facts.get_fact(ADMIN, "f1")["status"] == "active")
        r = await client.delete("/api/memories/wifi_password", headers=auth(BOB))
        with get_db_connection() as conn:
            kept = conn.execute("SELECT 1 FROM memories WHERE key = 'wifi_password'").fetchone() is not None
        check("http: DELETE /api/memories/<old key> by someone else → 404, row kept", r.status == 404 and kept)
        r = await client.delete("/api/memories/wifi_password", headers=auth(ADMIN))
        with get_db_connection() as conn:
            gone = conn.execute("SELECT 1 FROM memories WHERE key = 'wifi_password'").fetchone() is None
        check("http: ... by their owner still deletes it", r.status == 200 and gone)


# --- 7. Telegram /memory ------------------------------------------------------------------------------

async def test_telegram() -> None:
    bot = TelegramBot.__new__(TelegramBot)
    bot.bot = AsyncMock()
    bot.selection_toggles = {}
    bot.user_memory_states = {}

    def message(telegram_id: int, text: str) -> SimpleNamespace:
        return SimpleNamespace(from_user=SimpleNamespace(id=telegram_id), text=text, reply=AsyncMock(),
                               chat=SimpleNamespace(id=telegram_id))

    async def as_sender(event, handler):
        await AuthMiddleware()(lambda e, d: handler(e, d["user"]), event, {})

    msg = message(222, "/memory list")
    await as_sender(msg, bot.handle_memory)
    reply = msg.reply.await_args.args[0]
    check("telegram: Bob's /memory list shows his facts only", "Pepper" in reply and "Sam is a friend" not in reply
          and "Mazda" not in reply, reply)
    msg = message(222, "/memory remember bike_lock_spot by the stairs")
    await as_sender(msg, bot.handle_memory)
    check("telegram: /memory remember saves for the sender",
          any("Bike lock spot" in t for t in texts(BOB)) and not any("Bike lock" in t for t in texts(ADMIN) + texts(EVE)),
          str(texts(BOB)))
    msg = message(222, "/memory recall hiking")
    await as_sender(msg, bot.handle_memory)
    check("telegram: /memory recall never finds another user's fact",
          "Sam is a friend" not in msg.reply.await_args.args[0], msg.reply.await_args.args[0])
    msg = message(222, "/memory forget f1")
    await as_sender(msg, bot.handle_memory)
    check("telegram: /memory forget with another user's id forgets nothing",
          facts.get_fact(ADMIN, "f1")["status"] == "active", msg.reply.await_args.args[0])
    msg = message(111, "/memory list")
    await as_sender(msg, bot.handle_memory)
    check("telegram: the first admin's /memory list shows theirs",
          "Sam is a friend" in msg.reply.await_args.args[0] and "Pepper" not in msg.reply.await_args.args[0])
    msg = message(444, "/memory list")
    await as_sender(msg, bot.handle_memory)
    check("telegram: without about the short human message", msg.reply.await_args.args[0] == tb.NO_MEMORY_TEXT)

    with get_db_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO memories (key, value, category) VALUES ('old_note', 'v', 'mycat')")
        conn.commit()

    def callback(telegram_id: int, data: str):
        tg_message = types.Message(message_id=9, date=datetime.datetime.now(datetime.timezone.utc),
                                   chat=types.Chat(id=telegram_id, type="private"))
        return SimpleNamespace(from_user=SimpleNamespace(id=telegram_id), data=data, answer=AsyncMock(),
                               message=tg_message)

    with patch.object(types.Message, "edit_text", new=AsyncMock()) as edit:
        await as_sender(callback(222, "mem_forget_select"), bot.handle_approval_callback)
        check("telegram: Bob's forget picker lists no old memory keys",
              "no saved memories" in edit.await_args.args[0], str(edit.await_args))
        await as_sender(callback(111, "mem_forget_select"), bot.handle_approval_callback)
        markup = edit.await_args.kwargs.get("reply_markup")
        labels = [b.text for row in markup.inline_keyboard for b in row] if markup else []
        check("telegram: the owner's forget picker lists the old keys", any("old_note" in t for t in labels), str(labels))
        await as_sender(callback(222, "mem_list_cats"), bot.handle_approval_callback)
        markup = edit.await_args.kwargs.get("reply_markup")
        labels = [b.text for row in markup.inline_keyboard for b in row]
        check("telegram: Bob's category picker has no old memory categories", "Mycat" not in labels, str(labels))
        await as_sender(callback(111, "mem_list_cats"), bot.handle_approval_callback)
        markup = edit.await_args.kwargs.get("reply_markup")
        labels = [b.text for row in markup.inline_keyboard for b in row]
        check("telegram: the owner's has them", "Mycat" in labels, str(labels))
        await as_sender(callback(222, "mem_forget_key:old_note"), bot.handle_approval_callback)
    with get_db_connection() as conn:
        kept = conn.execute("SELECT 1 FROM memories WHERE key = 'old_note'").fetchone() is not None
    check("telegram: a forged forget button from Bob deletes no old memory", kept)


# --- 8. The learner --------------------------------------------------------------------------------

def sql_ts(minutes_ago: float) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def add_session(user, sid: str, text: str, minutes_ago: float = 60) -> None:
    hist = [{"role": "user", "content": text}, {"role": "assistant", "content": "Noted."}]
    with get_db_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO sessions (user_id, session_id, history, status, title, debug_info, updated_at) "
                     "VALUES (?, ?, ?, 'idle', '', '{}', ?)", (user.id, sid, json.dumps(hist), sql_ts(minutes_ago)))
        conn.commit()


async def settle() -> None:
    for _ in range(40):
        await asyncio.sleep(0.05)
    usage.flush_sync()


def learner_rows() -> List[Dict[str, Any]]:
    usage.flush_sync()
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT user_id, ok FROM usage_events WHERE purpose = 'about_learner' ORDER BY id")]


async def test_learner() -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM phone_calls")
        conn.execute("DELETE FROM usage_events")
        conn.commit()
    add_session(ADMIN, "main", "My neighbour Dalia waters the plants whenever we travel abroad")
    add_session(BOB, "main", "My sister Noa lives in Haifa and visits us every other weekend")
    add_session(EVE, "web-1", "I always park the blue Mazda in spot 14 at the office garage")
    add_session(CARL, "main", "My cousin Avi fixes our bikes every spring before the season starts")
    with get_db_connection() as conn:
        conn.execute("INSERT INTO phone_calls (id, user_id, filename, caller, receiver, call_timestamp, summary, title, "
                     "status, importance, direction, created_at) VALUES ('call-1', ?, 'c.m4a', 'אמא', 'Me', ?, "
                     "'Mom said she calls every evening after 8.', 'Call with אמא', 'ready', 'normal', 'incoming', ?)",
                     (ADMIN.id, sql_ts(70), sql_ts(70)))
        conn.commit()
    order = [u.id for u in learner.learners()]
    check("learner: runs for everyone with about, nobody else",
          set(order) == {ADMIN.id, BOB.id, EVE.id}, str(order))
    first, second = order[0], order[1]
    LEARN_REPLY["fail_for"] = first
    LEARN_REPLY["reply"] = {
        ADMIN.id: {"add": [{"text": "Dalia is Robin's neighbour who waters the plants", "section": "people", "from": "chat-1"}],
                   "update": []},
        BOB.id: {"add": [{"text": "Noa is Bob's sister, lives in Haifa", "section": "people", "from": "chat-1"}],
                 "update": []},
        EVE.id: {"add": [{"text": "Eve parks in spot 14 at the office", "section": "habits", "from": "chat-1"}],
                 "update": []},
    }
    MODEL_USERS.clear()
    learner._failures.clear()
    learner._due.clear()
    ran = await learner.run_due(now=1000.0)
    await settle()
    prompts = LEARN_REPLY["prompts"]
    check("learner: one pass per user, each model call as that user (the failing one retried by core/llm)",
          ran == order and set(MODEL_USERS) == set(order) and MODEL_USERS.count(second) == 1
          and MODEL_USERS.count(order[2]) == 1, f"{ran} {MODEL_USERS}")
    check("learner: a failing user does not stop the next",
          learner.failures(users.get(first)) == 1 and learner.failures(users.get(second)) == 0
          and learner.failures(users.get(order[2])) == 0)
    check("learner: Bob's prompt has only his chat, his name, no calls",
          "Noa lives in Haifa" in prompts[BOB.id] and "Dalia" not in prompts[BOB.id] and "Mazda" not in prompts[BOB.id]
          and "Avi" not in prompts[BOB.id] and "Mom said" not in prompts[BOB.id]
          and "durable facts about Bob," in prompts[BOB.id], prompts[BOB.id][-500:])
    check("learner: Bob's prompt lists only his facts and his do-not-re-add",
          "Pepper is Bob's dog" in prompts[BOB.id] and "Sam is a friend" not in prompts[BOB.id]
          and "Gal is Bob's brother" in prompts[BOB.id].split("## Do not re-add")[1]
          and "Mazda" not in prompts[BOB.id].split("## New conversations")[0])
    check("learner: the first admin's prompt has their chat and the calls (the interim owner of calls)",
          "Dalia" in prompts[ADMIN.id] and "Mom said she calls" in prompts[ADMIN.id] and "Noa" not in prompts[ADMIN.id])
    check("learner: Carl's chat is never read", not any("Avi" in p for p in prompts.values()))
    learned = {u.name: texts(u) for u in (ADMIN, BOB, EVE)}
    fail_user = users.get(first)
    check("learner: facts go to the user they were learned for (none for the failed pass)",
          all((u.id == first) != any(t in learned[u.name] for t in (
              "Dalia is Robin's neighbour who waters the plants", "Noa is Bob's sister, lives in Haifa",
              "Eve parks in spot 14 at the office")) for u in (ADMIN, BOB, EVE))
          and "Noa is Bob's sister, lives in Haifa" not in learned["Robin"] + learned["Eve"]
          and "Dalia is Robin's neighbour who waters the plants" not in learned["Bob"] + learned["Eve"],
          json.dumps(learned, ensure_ascii=False))
    rows = learner_rows()
    check("learner: usage rows carry the right user (the failed one too)",
          {r["user_id"] for r in rows} == set(order)
          and {r["ok"] for r in rows if r["user_id"] == first} == {0}
          and [r["ok"] for r in rows if r["user_id"] != first] == [1, 1], str(rows))
    with get_db_connection() as conn:
        bms = {r["user_id"]: json.loads(r["value"]) for r in conn.execute(
            "SELECT user_id, value FROM about_state WHERE key = 'learner'")}
    check("learner: separate bookmarks, none saved for the failed pass",
          set(bms) == set(order) - {first} and all(list(b["sessions"]) for b in bms.values()), str(bms)[:300])
    check("learner: each bookmark names only that user's sessions",
          (BOB.id not in bms or set(bms[BOB.id]["sessions"]) == {"main"})
          and (EVE.id not in bms or set(bms[EVE.id]["sessions"]) == {"web-1"}))

    base = learner.next_delay_seconds(users.get(second))
    check("learner: back-off per user", learner.next_delay_seconds(fail_user) == 2 * base)
    MODEL_USERS.clear()
    LEARN_REPLY["fail_for"] = None
    check("learner: nobody is due again right away", await learner.run_due(now=1001.0) == [])
    ran = await learner.run_due(now=1000.0 + base + 1)
    check("learner: after the interval the others run again, the failed user waits twice as long",
          set(ran) == set(order) - {first}, str(ran))
    check("learner: ... with nothing new, no model call", MODEL_USERS == [], str(MODEL_USERS))
    ran = await learner.run_due(now=1000.0 + 2 * base + 1)
    await settle()
    check("learner: the failed user's pass runs after the back-off and sends the same chat again",
          first in ran and MODEL_USERS == [first] and learner.failures(fail_user) == 0
          and any(t in texts(fail_user) for t in ("Dalia is Robin's neighbour who waters the plants",
                                                  "Noa is Bob's sister, lives in Haifa",
                                                  "Eve parks in spot 14 at the office")), f"{ran} {MODEL_USERS}")


test_tools()
test_forget_per_user()
test_cap_per_user()
test_prompt_per_user()
run(test_http())
run(test_telegram())
run(test_learner())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:\n  " + "\n  ".join(failed))
    sys.exit(1)
