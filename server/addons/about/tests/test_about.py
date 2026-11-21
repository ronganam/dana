"""About you: facts store, prompt section, migration, memory tools, learner, HTTP API.

Covers: add / dedupe / update / forget / restore, the size cap (learned facts
dropped first, the newest never), the prompt section (empty, populated, capped,
refreshed after every change, never raises, missing table), migration of old
memories (real ones only, idempotent, system rows untouched), the model's tools
(immediate, no approval, undo, forgotten facts refused, old key/value/category
arguments), the learner with the model mocked at the LiteLLM boundary (adds with
sources, skips trivial conversations and low calls, respects "do not re-add" and
the sensitive filter, bookmark advances and only new messages are re-read,
processing calls wait, batch cap, failure backs off, no key = no call), and the
HTTP API including the old /api/memories endpoints.

Run: bin/test test_about
"""
import asyncio
import datetime
import json
import os
import sys

from cryptography.fernet import Fernet

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("about_test")
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # throwaway: the startup import encrypts env credentials
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ABOUT_MAX_CHARS"] = "700"
os.environ["ABOUT_MAX_FACTS"] = "12"
os.environ["ABOUT_LEARN_MAX_ITEMS"] = "4"
os.environ["OWNER_NAME"] = "Robin"

import litellm
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users
from addons import bootstrap as addons_bootstrap
from addons.tasks.groups import ensure_default_groups_for_everyone

db.migrate()
users.bootstrap()
addons_bootstrap.bootstrap()  # the first admin's addons (built-ins + env)
OWNER = users.first_admin()
ensure_default_groups_for_everyone()
from core import actions_api, undo
from core.db import get_db_connection
from addons.about import api as about_api, facts as about_facts, learner, legacy, prompt as about_prompt
from addons.about.values import FACT_MAX_CHARS, MAX_CHARS, MAX_FACTS, looks_sensitive

# This database stands for one that existed before migration m0009: the first
# admin owns the old memories table (the migration marks them; a fresh DB has nobody).
with get_db_connection() as _conn:
    _conn.execute("INSERT INTO about_state (user_id, key, value) VALUES (?, ?, 'true')",
                  (OWNER.id, legacy.LEGACY_MEMORIES_KEY))
    _conn.commit()
from core.addons import registry
import core.agent as agent
from core.auth import setup_auth


results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def reset_facts():
    with get_db_connection() as conn:
        conn.execute("DELETE FROM about_facts")
        conn.execute("DELETE FROM about_state WHERE key != 'legacy_memories'")
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM phone_calls")
        conn.commit()
    about_facts.refresh_prompt(OWNER)


def texts(status="active"):
    return [f["text"] for f in about_facts.list_facts(OWNER, status)]


def sql_ts(minutes_ago=0.0):
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# --- Fake model (LiteLLM boundary; core/llm.py runs for real) ---------------------

seen = {"calls": [], "reply": None, "fail": False}


class ModelDown(Exception):
    status_code = 500


async def fake_acompletion(**kwargs):
    props = set(kwargs["response_format"]["json_schema"]["schema"]["properties"])
    assert props == {"add", "update"}, props
    prompt = kwargs["messages"][0]["content"]
    seen["calls"].append(prompt)
    if seen["fail"]:
        raise ModelDown("boom")
    reply = seen["reply"](prompt) if callable(seen["reply"]) else (seen["reply"] or {"add": [], "update": []})
    return litellm.ModelResponse(choices=[{"message": {"role": "assistant",
                                                       "content": json.dumps(reply, ensure_ascii=False)}}])


litellm.acompletion = fake_acompletion


def add_session(sid, messages, minutes_ago=60, status="idle"):
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (user_id, session_id, history, status, title, debug_info, updated_at) "
            "VALUES (?, ?, ?, ?, '', '{}', ?)", (OWNER.id, sid, json.dumps(messages, ensure_ascii=False), status, sql_ts(minutes_ago)))
        conn.commit()


def add_call(cid, caller, summary, minutes_ago=60, status="ready", importance="normal"):
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO phone_calls (id, user_id, filename, caller, receiver, call_timestamp, summary, title, status, "
            "importance, direction, created_at) VALUES (?, ?, ?, ?, 'Me', ?, ?, ?, ?, ?, 'incoming', ?)",
            (cid, OWNER.id, f"{cid}.m4a", caller, sql_ts(minutes_ago), summary, f"Call with {caller}", status, importance,
             sql_ts(minutes_ago)))
        conn.commit()


def user(t):
    return {"role": "user", "content": t}


def dana(t, tools=()):
    m = {"role": "assistant", "content": t}
    if tools:
        m["tool_calls"] = [{"id": f"t{i}", "type": "function", "function": {"name": n, "arguments": "{}"}}
                           for i, n in enumerate(tools)]
    return m


# --- Tests ------------------------------------------------------------------------

def test_empty_and_store():
    check("empty table → prompt_section() is ''", about_prompt.section(OWNER) == "")
    check("agent.about_section(OWNER) is '' when empty", agent.about_section(OWNER) == "")

    f, created = about_facts.add_fact(OWNER, "Gal is Robin's brother", "people", source_kind="told")
    check("add_fact creates", created and f["section"] == "people" and f["source"] == "you told Dana")
    dup, created2 = about_facts.add_fact(OWNER, "gal is Robin's brother.", None, source_kind="chat")
    check("near-duplicate is not added twice", not created2 and dup["id"] == f["id"] and len(texts()) == 1)
    dan, created3 = about_facts.add_fact(OWNER, "Dan is Robin's brother", "people")
    check("different names are different facts", created3 and len(texts()) == 2)
    about_facts.add_fact(OWNER, "Gal lives in Haifa with his wife and two kids", "people")
    _, created4 = about_facts.add_fact(OWNER, "Gal lives in Haifa with his wife and their two kids", "people")
    check("a reworded fact is a duplicate", not created4 and len(texts()) == 3, str(texts()))
    about_facts.forget_fact(OWNER, dan["id"])
    check("reworded removed fact is still 'forgotten'", about_facts.is_forgotten(OWNER, "Robin's brother is Dan")
          and not about_facts.is_forgotten(OWNER, "Dana is Robin's sister"))
    g, _ = about_facts.add_fact(OWNER, "'The gate' means the front gate")
    check("section guessed when omitted (home)", g["section"] == "home", g["section"])
    h, _ = about_facts.add_fact(OWNER, "Prefers reminders as 'when I get home' over a clock time")
    check("section guessed when omitted (habits)", h["section"] == "habits", h["section"])
    try:
        about_facts.add_fact(OWNER, "x" * (FACT_MAX_CHARS + 1))
        check("too long rejected", False)
    except ValueError:
        check("too long rejected", True)
    try:
        about_facts.add_fact(OWNER, "   ")
        check("empty rejected", False)
    except ValueError:
        check("empty rejected", True)

    upd = about_facts.update_fact(OWNER, g["id"], text="'The gate' is the front gate (cover.front_gate)")
    check("update_fact changes text and marks edited", upd["text"].startswith("'The gate' is") and upd["source_kind"] == "edited")
    check("forget_fact hides it", about_facts.forget_fact(OWNER, g["id"]) and g["id"] not in [x["id"] for x in about_facts.list_facts(OWNER)])
    check("forgotten fact is remembered as forgotten", about_facts.is_forgotten(OWNER, "'The gate' is the front gate (cover.front_gate)"))
    check("restore_fact brings it back", about_facts.restore_fact(OWNER, g["id"]) and about_facts.get_fact(OWNER, g["id"])["status"] == "active")
    check("sensitive filter: password", looks_sensitive("The wifi password is hunter22"))
    check("sensitive filter: card number", looks_sensitive("card 4580 1234 5678 9012"))
    check("sensitive filter: Hebrew code", looks_sensitive("קוד אימות 123456"))
    check("sensitive filter: phone and WhatsApp group id are fine",
          not looks_sensitive("Gal's number is 050-1234567") and not looks_sensitive("120363025246125486@g.us"))


def test_prompt_section():
    reset_facts()
    about_facts.add_fact(OWNER, "אמא calls most evenings", "people", source_kind="call")
    about_facts.add_fact(OWNER, "'The gate' means the front gate", "home", source_kind="told")
    p = about_prompt.section(OWNER)
    check("populated: titled section with owner name", "ABOUT ROBIN" in p and "Things you know about Robin" in p, p[:120])
    check("populated: tells the model to use without looking up / mentioning",
          "instead of asking or looking them up" in p and "Never mention" in p)
    check("populated: facts grouped under section labels",
          "People:\n- אמא calls most evenings" in p and "Home:\n- 'The gate' means the front gate" in p, p)
    check("agent.about_section(OWNER) returns the cached block", agent.about_section(OWNER) == p)

    f, _ = about_facts.add_fact(OWNER, "Pepper is the dog", "home")
    check("refreshed after add", "Pepper is the dog" in about_prompt.section(OWNER))
    about_facts.update_fact(OWNER, f["id"], text="Pepper is the family dog")
    check("refreshed after update", "Pepper is the family dog" in about_prompt.section(OWNER))
    about_facts.forget_fact(OWNER, f["id"])
    check("refreshed after forget", "Pepper" not in about_prompt.section(OWNER))

    many = [{"section": "people", "text": f"Person number {i} is a friend from the old neighbourhood, " + "x" * 150}
            for i in range(80)]
    capped = about_prompt.render("Robin", many)
    check("capped: block never exceeds PROMPT_MAX_CHARS", 0 < len(capped) <= about_prompt.PROMPT_MAX_CHARS,
          f"{len(capped)} > {about_prompt.PROMPT_MAX_CHARS}")
    check("capped: ends with the closing rule and whole lines", capped.endswith("-----------------------------------"))

    before = about_prompt.section(OWNER)
    orig = about_facts.list_facts
    about_facts.list_facts = lambda user, status="active": (_ for _ in ()).throw(RuntimeError("db gone"))
    try:
        about_facts.refresh_prompt(OWNER)
        check("refresh failure keeps the previous block", about_prompt.section(OWNER) == before)
    finally:
        about_facts.list_facts = orig
    with get_db_connection() as conn:
        conn.execute("ALTER TABLE about_facts RENAME TO about_facts_x")
        conn.commit()
    try:
        about_facts.refresh_prompt(OWNER)
        ok = isinstance(about_prompt.section(OWNER), str) and isinstance(agent.about_section(OWNER), str)
        check("missing table: prompt_section() never raises", ok)
        about_prompt.clear()
        check("missing table + no cache → ''", about_prompt.section(OWNER) == "")
    finally:
        with get_db_connection() as conn:
            conn.execute("ALTER TABLE about_facts_x RENAME TO about_facts")
            conn.commit()
        about_facts.refresh_prompt(OWNER)


def test_cap():
    reset_facts()
    words = ["apple", "banana", "cherry", "date", "elder", "fig", "grape", "hazel", "iris", "juniper", "kiwi",
             "lemon", "mango", "nectar", "olive", "papaya", "quince", "rasp", "sloe", "tamarind"]

    def filler(i, n=72):
        return " ".join(f"{words[i % len(words)]}{k}" for k in range(n // 8))

    owner_ids = [about_facts.add_fact(OWNER, f"Owner fact {words[i]}: " + filler(i), "other", source_kind="owner")[0]["id"]
                 for i in range(3)]
    learned_ids = [about_facts.add_fact(OWNER, f"Learned fact {words[i + 3]}: " + filler(i + 3), "other", source_kind="chat")[0]["id"]
                   for i in range(5)]
    total = sum(len(t) for t in texts())
    check("under the cap nothing is dropped", len(texts()) == 8 and total <= MAX_CHARS, f"{total}")
    newest, _ = about_facts.add_fact(OWNER, "Brand new learned fact: " + filler(10, 130), "other", source_kind="chat")
    active = [f["id"] for f in about_facts.list_facts(OWNER)]
    dropped = [f["id"] for f in about_facts.list_facts(OWNER, "dropped")]
    check("over the cap: total back under ABOUT_MAX_CHARS", sum(len(t) for t in texts()) <= MAX_CHARS)
    check("over the cap: oldest learned facts dropped first", learned_ids[0] in dropped and all(o in active for o in owner_ids),
          f"dropped={dropped}")
    check("over the cap: the fact just added is kept", newest["id"] in active)
    check("dropped facts are not 'forgotten'", not about_facts.list_facts(OWNER, "forgotten"))
    for i in range(12):
        about_facts.add_fact(OWNER, f"Note {words[(i + 8) % len(words)]} {words[(i + 13) % len(words)]}", "other", source_kind="owner")
    check("count cap ABOUT_MAX_FACTS holds", len(texts()) <= MAX_FACTS, str(len(texts())))


def test_migration():
    reset_facts()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM memories")
        rows = [
            ("wife_phone", "0501234567", "contacts"),
            ("theme_preference", "Dark mode everywhere", "preferences"),
            ("gal_email", "gal@example.com", "general"),
            ("wifi_password", "hunter22hunter", "general"),
            ("selected_calendars", '["primary"]', "preferences"),
            ("last_wake_trigger_date", "2026-11-14", "system"),
            ("notif_digest_last", "2026-11-14T08:00", "system"),
        ]
        conn.executemany("INSERT INTO memories (key, value, category) VALUES (?, ?, ?)", rows)
        conn.commit()
    n1 = legacy.migrate_legacy()
    facts = {f["source_ref"]: f for f in about_facts.list_facts(OWNER)}
    check("migration: real memories become facts", n1 == 3 and set(facts) == {"wife_phone", "theme_preference", "gal_email"},
          f"{n1} {sorted(facts)}")
    check("migration: text and section mapped", facts["wife_phone"]["text"] == "Wife phone: 0501234567"
          and facts["wife_phone"]["section"] == "people" and facts["theme_preference"]["section"] == "habits",
          json.dumps(facts, ensure_ascii=False)[:300])
    check("migration: source is the old Memory list", facts["gal_email"]["source"] == "your old Memory list")
    check("migration: credentials, app state and system rows skipped",
          not any(k in facts for k in ("wifi_password", "selected_calendars", "last_wake_trigger_date")))
    n2 = legacy.migrate_legacy()
    check("migration is idempotent", n2 == 0 and len(about_facts.list_facts(OWNER)) == 3)
    about_facts.forget_fact(OWNER, facts["gal_email"]["id"])
    check("migration does not re-import a fact the owner removed", legacy.migrate_legacy() == 0
          and "gal_email" not in {f["source_ref"] for f in about_facts.list_facts(OWNER)})
    with get_db_connection() as conn:
        left = {r["key"] for r in conn.execute("SELECT key FROM memories").fetchall()}
    check("migration leaves the memories table alone", len(left) == 7)


async def test_tools():
    reset_facts()
    check("remember_info / forget_info never ask for approval",
          not getattr(registry, "needs_confirmation", lambda *a: registry.is_write_tool(a[0]))("remember_info", {"fact": "x"})
          and not getattr(registry, "needs_confirmation", lambda *a: registry.is_write_tool(a[0]))("forget_info", {"fact": "x"}))
    schema = next(s for s in registry.tool_schemas(OWNER) if s["function"]["name"] == "remember_info")
    props = schema["function"]["parameters"]["properties"]
    check("remember_info schema shows fact/section/replaces only", set(props) == {"fact", "section", "replaces"}, str(set(props)))
    check("remember_info docstring tells the model to save on its own",
          "without being asked" in schema["function"]["description"] and "Never save passwords" in schema["function"]["description"])

    with undo.collect() as ids:
        res = await registry.execute(OWNER, "remember_info", {"fact": "Noa is the camera guy", "section": "people"})
    check("remember_info stores at once", "Saved in About you" in res and "Noa is the camera guy" in texts(), res)
    check("remember_info records an undo", len(ids) == 1 and ids[0]["label"].startswith("Saved:"), str(ids))
    check("prompt has the new fact right away", "Noa is the camera guy" in about_prompt.section(OWNER))
    await undo.run(ids[0]["id"])
    check("undo of remember removes it (and marks it forgotten)", "Noa is the camera guy" not in texts()
          and "Noa is the camera guy" in texts("forgotten"))
    res = await registry.execute(OWNER, "remember_info", {"fact": "Noa is the camera guy."})
    check("forgotten fact is not saved again by the tool", "removed this" in res and "Noa is the camera guy." not in texts(), res)

    res = await registry.execute(OWNER, "remember_info", {"fact": "My bank PIN is 1234"})
    check("secrets are refused", res.startswith("Not saved") and not any("PIN" in t for t in texts()), res)

    res = await registry.execute(OWNER, "remember_info", {"key": "girlfriend_chat_id", "value": "972500000301@c.us", "category": "contact"})
    f = next((x for x in about_facts.list_facts(OWNER) if "972500000301@c.us" in x["text"]), None)
    check("old key/value/category arguments still work (Telegram /memory)", f is not None and f["section"] == "people"
          and f["text"] == "Girlfriend chat id: 972500000301@c.us", res)

    await registry.execute(OWNER, "remember_info", {"fact": "Gal is Robin's cousin", "section": "people"})
    with undo.collect() as ids:
        res = await registry.execute(OWNER, "remember_info", {"fact": "Gal is Robin's brother", "replaces": "Gal is Robin's cousin"})
    check("replaces rewrites the fact instead of adding", "Gal is Robin's brother" in texts() and "Gal is Robin's cousin" not in texts(), res)
    await undo.run(ids[0]["id"])
    check("undo of replace restores the old text", "Gal is Robin's cousin" in texts() and "Gal is Robin's brother" not in texts())

    res = await registry.execute(OWNER, "recall_info", {"query": "Gal"})
    check("recall_info finds facts with ids", "Gal is Robin's cousin" in res and "id " in res, res)
    with get_db_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO memories (key, value, category) VALUES ('wifi_password', 'hunter22hunter', 'general')")
        conn.commit()
    res = await registry.execute(OWNER, "recall_info", {"query": "wifi"})
    check("recall_info still answers unmigrated old notes", "hunter22hunter" in res, res)
    res = await registry.execute(OWNER, "list_memories", {})
    check("list_memories lists by section", "People:" in res and "cousin" in res, res)
    res = await registry.execute(OWNER, "list_memories", {"category": "all"})
    check("list_memories accepts the old category argument", "People:" in res, res)

    with undo.collect() as ids:
        res = await registry.execute(OWNER, "forget_info", {"fact": "Gal is Robin's cousin"})
    check("forget_info by text forgets at once", res.startswith("Forgot") and "Gal is Robin's cousin" not in texts(), res)
    check("forget_info records an undo", len(ids) == 1)
    await undo.run(ids[0]["id"])
    check("undo of forget restores it", "Gal is Robin's cousin" in texts())
    fid = next(x["id"] for x in about_facts.list_facts(OWNER) if x["text"] == "Gal is Robin's cousin")
    res = await registry.execute(OWNER, "forget_info", {"key": fid})
    check("forget_info by id via the old 'key' argument", "Gal is Robin's cousin" in texts("forgotten"), res)
    res = await registry.execute(OWNER, "forget_info", {"fact": "something nobody said"})
    check("forget_info with no match says so", res.startswith("No fact matches"), res)
    res = await registry.execute(OWNER, "forget_info", {"key": "wifi_password"})
    with get_db_connection() as conn:
        gone = conn.execute("SELECT 1 FROM memories WHERE key = 'wifi_password'").fetchone() is None
    check("forget_info deletes an old memory key (Telegram picker)", gone and "Deleted old memory" in res, res)


async def test_learner():
    reset_facts()
    learner._failures.clear()
    seen["calls"].clear()
    about_facts.add_fact(OWNER, "The old office was in Herzliya", "other", source_kind="told")
    about_facts.forget_fact(OWNER, about_facts.list_facts(OWNER)[0]["id"])

    add_session("web-1", [user("By the way, Gal is my brother and he lives in Haifa. Can you remind me his birthday is in May?"),
                          dana("Noted, I'll remember that.")], minutes_ago=90)
    add_session("web-trivial", [user("open the gate"), dana("Opened.", tools=["call_ha_service"])], minutes_ago=80)
    add_session("web-active", [user("I'm writing a long message about my mother's routine")], minutes_ago=2)
    add_call("call-1", "אמא", "Mom said she'll call every evening after 8; talked about Friday dinner.", minutes_ago=70)
    add_call("call-low", "Bank bot", "Automated balance message.", minutes_ago=65, importance="low")
    add_call("call-proc", "Noa", None, minutes_ago=60, status="processing")

    def reply(prompt):
        return {"add": [
            {"text": "Gal is Robin's brother, lives in Haifa", "section": "people", "from": "chat-1"},
            {"text": "אמא usually calls in the evening after 8", "section": "people", "from": "call-2"},
            {"text": "Robin's old office was in Herzliya", "section": "other", "from": "chat-1"},
            {"text": "Robin's bank password is hunter2", "section": "other", "from": "chat-1"},
        ], "update": []}
    seen["reply"] = reply
    out = await learner.run_pass(OWNER)
    check("learner: one model call per pass", len(seen["calls"]) == 1, str(len(seen["calls"])))
    prompt = seen["calls"][0]
    check("learner: prompt has current facts, do-not-re-add and the items",
          "Do not re-add" in prompt and "The old office was in Herzliya" in prompt and "Gal is my brother" in prompt
          and "Mom said she'll call" in prompt, prompt[-800:])
    check("learner: trivial home command, active chat, low and processing calls not sent",
          "open the gate" not in prompt and "mother's routine" not in prompt and "Automated balance" not in prompt
          and "### call-3" not in prompt)
    by_text = {f["text"]: f for f in about_facts.list_facts(OWNER)}
    check("learner: adds durable facts", "Gal is Robin's brother, lives in Haifa" in by_text
          and "אמא usually calls in the evening after 8" in by_text, str(list(by_text)))
    check("learner: sources recorded", by_text["Gal is Robin's brother, lives in Haifa"]["source"] == "a chat"
          and by_text["Gal is Robin's brother, lives in Haifa"]["source_ref"] == "web-1"
          and by_text["אמא usually calls in the evening after 8"]["source"] == "a call with אמא"
          and by_text["אמא usually calls in the evening after 8"]["source_ref"] == "call-1")
    check("learner: respects do-not-re-add (even reworded)", not any("Herzliya" in t for t in by_text))
    check("learner: drops sensitive proposals", not any("password" in t for t in by_text))
    check("learner: outcome reported", out and len(out["added"]) == 2, str(out))
    check("learner: learned facts are in the prompt", "Gal is Robin's brother" in about_prompt.section(OWNER))

    seen["calls"].clear()
    seen["reply"] = None
    await learner.run_pass(OWNER)
    check("learner: bookmark advanced, nothing new → no model call", len(seen["calls"]) == 0)

    with get_db_connection() as conn:
        conn.execute("UPDATE phone_calls SET status = 'ready', summary = 'Noa confirmed the camera install.' WHERE id = 'call-proc'")
        hist = json.loads(conn.execute("SELECT history FROM sessions WHERE session_id = 'web-1'").fetchone()["history"])
        hist += [user("Also, when I say 'the TV' I mean the living-room TCL"), dana("Got it.")]
        conn.execute("UPDATE sessions SET history = ?, updated_at = ? WHERE session_id = 'web-1'",
                     (json.dumps(hist), sql_ts(30)))
        conn.commit()
    seen["reply"] = lambda p: {"add": [], "update": [
        {"id": next(f["id"] for f in about_facts.list_facts(OWNER) if f["text"].startswith("Gal is")),
         "text": "Gal is Robin's younger brother, lives in Haifa", "from": "chat-1"}]}
    await learner.run_pass(OWNER)
    prompt = seen["calls"][-1] if seen["calls"] else ""
    check("learner: only the new messages of a conversation are re-read",
          "living-room TCL" in prompt and "Gal is my brother and he lives" not in prompt, prompt[-600:])
    check("learner: a call that finished processing is picked up", "Noa confirmed the camera install" in prompt)
    check("learner: can sharpen a learned fact", "Gal is Robin's younger brother, lives in Haifa" in texts())

    # Owner-made facts are not rewritten by the learner.
    told, _ = about_facts.add_fact(OWNER, "Robin works from home on Fridays", "habits", source_kind="told")
    r = learner.apply_learned(OWNER, {"add": [], "update": [{"id": told["id"], "text": "Robin works from the office on Fridays",
                                                     "from": "chat-1"}]}, [])
    check("learner: never rewrites the owner's own facts", told["id"] in r["skipped"] or "Robin works from home on Fridays" in texts())

    # Batch cap: more quiet conversations than ABOUT_LEARN_MAX_ITEMS.
    for i in range(7):
        add_session(f"bulk-{i}", [user(f"Conversation number {i}: my neighbour Dalia waters the plants when we travel")],
                    minutes_ago=50 - i)
    seen["calls"].clear()
    seen["reply"] = None
    await learner.run_pass(OWNER)
    first = seen["calls"][-1]
    check("learner: batch cap per pass", first.count("### chat-") + first.count("### call-") == learner.LEARN_MAX_ITEMS,
          str(first.count("### chat-")))
    await learner.run_pass(OWNER)
    second = seen["calls"][-1]
    check("learner: the rest comes in the next pass", len(seen["calls"]) == 2 and second.count("### chat-") == 3
          and "Conversation number 0" not in second, str(second.count("### chat-")))

    # Failure: backs off, bookmark not advanced.
    add_session("web-fail", [user("Remember that my sister Maya lives in Tel Aviv near the beach")], minutes_ago=40)
    seen["calls"].clear()
    seen["fail"] = True
    base = learner.next_delay_seconds(OWNER)
    out = await learner.run_pass(OWNER)
    check("learner: failure returns None and counts", out is None and learner.failures(OWNER) == 1)
    check("learner: failure backs off (delay doubles)", learner.next_delay_seconds(OWNER) == 2 * base, f"{learner.next_delay_seconds(OWNER)} vs {base}")
    seen["fail"] = False
    seen["calls"].clear()
    await learner.run_pass(OWNER)
    check("learner: after a failure the same items are sent again", seen["calls"] and "sister Maya" in seen["calls"][-1])
    check("learner: success resets the back-off", learner.failures(OWNER) == 0 and learner.next_delay_seconds(OWNER) == base)

    # No API key: no model call, no crash.
    from core import llm
    orig = llm.configured
    llm.configured = lambda job: False
    add_session("web-nokey", [user("My cousin Eden is getting married in the spring next year")], minutes_ago=35)
    seen["calls"].clear()
    try:
        out = await learner.run_pass(OWNER)
    finally:
        llm.configured = orig
    check("learner: no key → no model call, no error", out is None and not seen["calls"] and learner.failures(OWNER) == 0)


async def test_http():
    reset_facts()
    app = web.Application()
    setup_auth(app)  # no API_TOKEN: every request is the first admin
    about_api.setup(app)
    actions_api.setup(app)  # POST /api/undo/{id}
    app.router.add_get("/api/memories", about_api.get_memories_api)
    app.router.add_post("/api/memories", about_api.create_memory_api)
    app.router.add_delete("/api/memories/{key}", about_api.delete_memory_api)
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/api/about")
        body = await r.json()
        check("GET /api/about: all sections in order, empty", r.status == 200
              and [s["key"] for s in body["sections"]] == ["people", "home", "habits", "other"]
              and all(s["facts"] == [] for s in body["sections"]) and body["max_chars"] == MAX_CHARS, str(body))

        r = await client.post("/api/about/facts", json={"text": "Gal is Robin's brother", "section": "people"})
        body = await r.json()
        fid = body["fact"]["id"]
        check("POST /api/about/facts → 201 with fact + undo_id", r.status == 201 and body["ok"] and body["undo_id"]
              and body["fact"]["source"] == "you added this", str(body))
        r = await client.post("/api/about/facts", json={"text": "The gate means the front gate"})
        gate = (await r.json())["fact"]
        check("POST without section guesses one", gate["section"] == "home", str(gate))
        r = await client.post("/api/about/facts", json={"text": "x", "section": "pets"})
        check("POST bad section → 400", r.status == 400)
        r = await client.post("/api/about/facts", json={"text": ""})
        check("POST empty text → 400", r.status == 400)

        r = await client.get("/api/about")
        body = await r.json()
        people = body["sections"][0]["facts"]
        check("GET /api/about: fact shape", people and set(people[0]) >= {"id", "text", "source", "learned_at"}
              and people[0]["text"] == "Gal is Robin's brother", str(people))

        r = await client.put(f"/api/about/facts/{fid}", json={"text": "Gal is Robin's younger brother"})
        body = await r.json()
        check("PUT edits text, source becomes 'you edited this'", r.status == 200 and body["fact"]["text"] == "Gal is Robin's younger brother"
              and body["fact"]["source"] == "you edited this" and body["undo_id"], str(body))
        r = await client.post(f"/api/undo/{body['undo_id']}")
        check("undo of PUT restores the old text", r.status == 200 and about_facts.get_fact(OWNER, fid)["text"] == "Gal is Robin's brother")
        r = await client.put(f"/api/about/facts/{fid}", json={"section": "other"})
        check("PUT moves section", (await r.json())["fact"]["section"] == "other")
        r = await client.put("/api/about/facts/nope", json={"text": "x"})
        check("PUT unknown → 404", r.status == 404)
        r = await client.put(f"/api/about/facts/{fid}", json={})
        check("PUT with nothing → 400", r.status == 400)

        r = await client.delete(f"/api/about/facts/{fid}")
        body = await r.json()
        check("DELETE → {ok, undo_id} and it's gone from the prompt", r.status == 200 and body["ok"] and body["undo_id"]
              and "Gal" not in about_prompt.section(OWNER), str(body))
        r = await client.delete(f"/api/about/facts/{fid}")
        check("DELETE twice → 404", r.status == 404)
        r = await client.post(f"/api/undo/{body['undo_id']}")
        check("undo of DELETE restores it", r.status == 200 and about_facts.get_fact(OWNER, fid)["status"] == "active")

        # Old endpoints.
        r = await client.get("/api/memories")
        mems = await r.json()
        check("GET /api/memories: facts in the old shape", r.status == 200 and isinstance(mems, list)
              and {"key", "value", "category", "updated_at"} <= set(mems[0])
              and any(m["key"] == f"fact_{fid}" and m["value"] == "Gal is Robin's brother" for m in mems), str(mems))
        r = await client.post("/api/memories", json={"key": "mom_phone", "value": "0521112233", "category": "contact"})
        body = await r.json()
        new_key = body["key"]
        made = about_facts.get_fact(OWNER, new_key[5:])
        check("POST /api/memories new key adds a fact", r.status == 200 and made and made["text"] == "Mom phone: 0521112233"
              and made["section"] == "people", str(body))
        r = await client.post("/api/memories", json={"key": new_key, "value": "Mom's phone is 0521112233", "category": "people"})
        check("POST /api/memories fact_<id> key edits that fact", about_facts.get_fact(OWNER, new_key[5:])["text"] == "Mom's phone is 0521112233")
        r = await client.delete(f"/api/memories/{new_key}")
        body = await r.json()
        check("DELETE /api/memories/fact_<id> forgets it with undo_id", r.status == 200 and body.get("undo_id")
              and about_facts.get_fact(OWNER, new_key[5:])["status"] == "forgotten", str(body))
        with get_db_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO memories (key, value, category) VALUES ('old_note', 'v', 'general')")
            conn.commit()
        r = await client.delete("/api/memories/old_note")
        check("DELETE /api/memories/<old key> still deletes the old row", r.status == 200)
        r = await client.delete("/api/memories/does_not_exist")
        check("DELETE /api/memories unknown → 404", r.status == 404)
        r = await client.post("/api/memories", json={"key": "", "value": ""})
        check("POST /api/memories without key/value → 400", r.status == 400)


async def main():
    test_empty_and_store()
    test_prompt_section()
    test_cap()
    test_migration()
    await test_tools()
    await test_learner()
    await test_http()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("FAILED:\n  " + "\n  ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
