"""Coding-agent sessions on the phone (integrations/phone/agent_sessions.py): hook events → session state → the agent_sessions push.
Run: python test_agent_sessions.py

Two users with the ``phone`` kind (Ann, Bob) and one without (Carl), on the
real handlers with FCM faked at httpx. Checked: Claude Code and Codex hook
events become running / waiting / done / failed with the activity, plan steps,
title and summary the phone shows; ``rev`` rises only on a status change; a
status change pushes at once, activity changes at most every
``PUSH_INTERVAL`` (the trailing push carries the latest); each push replaces
the user's earlier ``agent_sessions`` events; stale and old finished sessions
drop out; ``SessionEnd`` and Hide remove one; sessions, pushes and Hide are
each user's own (the same session id under two users stays two sessions,
another user's id answers 404); no kind → 403 ``not_connected``; bad input → 400.
Subagents (Claude Code's own, T3 Code delegated children) show inside their
parent with the group's status: a waiting subagent alerts through the parent's
rev, the grace after one finishes, a done parent kept while its subagents run,
a finished group pruned as one, orphans on their own, Hide and SessionEnd take
the subagents; a T3 thread title wins over the prompt.
"""

import asyncio
import datetime
import json
import os
import sys
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("agent_sessions_test")
os.environ["API_TOKEN"] = "agent-sessions-test-env-token"  # a test value: with it set, DB tokens are honoured
os.environ["FCM_SERVICE_ACCOUNT_JSON"] = '{"project_id": "dana-test"}'
os.environ["AGENT_SESSIONS_PUSH_INTERVAL"] = "0.4"
from cryptography.fernet import Fernet  # noqa: E402
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()

from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from integrations.framework import store  # noqa: E402
from integrations.phone import agent_sessions, api as phone_api, push  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ANN = users.create("Ann")
BOB = users.create("Bob")
CARL = users.create("Carl")
for _u in (ANN, BOB):
    store.connect(_u, "phone")
store.connect(CARL, "tasks")
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ANN, BOB, CARL)}
PINGS: List[Dict[str, Any]] = []


def auth(user) -> Dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[user.id]}"}


class FakeResponse:
    status_code = 200

    def json(self):
        return {"name": "projects/dana-test/messages/1"}


async def fake_post(self, url, *args, **kwargs):
    assert "fcm.googleapis.com" in url, url
    msg = kwargs["json"]["message"]
    PINGS.append({"token": msg["token"], "seq": int(msg["data"]["seq"]), "type": msg["data"]["type"]})
    return FakeResponse()


async def fake_fcm_token():
    return "fcm-test-token"

httpx.AsyncClient.post = fake_post
push._access_token = fake_fcm_token


def events_of(user) -> List[Dict[str, Any]]:
    return [e for e in push.events_after(user, 0)["events"] if e["type"] == "agent_sessions"]


def latest_list(user) -> List[Dict[str, Any]]:
    ev = events_of(user)
    return ev[-1]["data"]["sessions"] if ev else []


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)
    pending = [t for t in phone_api._hook_pushes]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def hook(client, user, agent: str, event: Dict[str, Any], host: str = "laptop", open_url: str = "",
               **params: str) -> Dict[str, Any]:
    r = await client.post("/api/agent-sessions/hook", params={"agent": agent, "host": host, "open_url": open_url, **params},
                          headers=auth(user), json=event)
    body = await r.json()
    await settle()
    return {"status": r.status, **body}


def claude(name: str, **fields) -> Dict[str, Any]:
    return {"session_id": "s-1", "hook_event_name": name, "cwd": "/home/x/projects/ganam-assistant",
            "transcript_path": "/tmp/t.jsonl", **fields}


def unit_activity() -> None:
    a = agent_sessions.activity
    check("activity: edits name the file", a("Edit", {"file_path": "/a/b/push.py"}) == "Editing push.py")
    check("activity: Bash prefers its description", a("Bash", {"command": "bin/test", "description": "Run tests"})
          == "Run tests")
    check("activity: Bash without one shows the command's first line",
          a("Bash", {"command": "bin/test --changed\necho done"}) == "Running bin/test --changed")
    check("activity: Codex shell commands given as a list", a("shell", {"command": ["git", "status"]})
          == "Running git status")
    check("activity: a question waits for the user", a("AskUserQuestion", {}) == "Asking you a question")
    check("activity: MCP tools by their short name", a("mcp__t3__device_open", {}) == "Using device_open")
    check("activity: long text is cut", len(a("Bash", {"command": "x" * 500})) <= agent_sessions.TEXT_MAX)


async def test_flow(client) -> None:
    await client.post("/api/phone/register", headers=auth(ANN), json={"device_id": "dev-a", "token": "tok-a"})
    await client.post("/api/phone/register", headers=auth(BOB), json={"device_id": "dev-b", "token": "tok-b"})
    PINGS.clear()
    r = await hook(client, ANN, "claude", claude("SessionStart", source="startup"))
    check("SessionStart alone shows nothing", r["status"] == 200 and r["change"] is None
          and agent_sessions.snapshot(ANN) == [], r)

    r = await hook(client, ANN, "claude", claude("UserPromptSubmit", prompt="\n  Fix the   push retry\nmore text"))
    s = latest_list(ANN)
    check("prompt: the session runs, titled by the prompt's first line, pushed at once",
          r["change"] == "status" and len(s) == 1 and s[0]["status"] == "running"
          and s[0]["title"] == "Fix the push retry" and s[0]["project"] == "ganam-assistant"
          and s[0]["agent"] == "claude" and s[0]["host"] == "laptop" and s[0]["id"] == "claude:s-1"
          and s[0]["rev"] == 1 and len(PINGS) == 1 and PINGS[0]["type"] == "agent_sessions", (r, s, PINGS))

    r = await hook(client, ANN, "claude", claude("PreToolUse", tool_name="TodoWrite", tool_input={"todos": [
        {"content": "Read", "activeForm": "Reading push.py", "status": "completed"},
        {"content": "Fix", "activeForm": "Fixing the retry", "status": "in_progress"},
        {"content": "Test", "activeForm": "Testing", "status": "pending"}]}))
    row = agent_sessions.snapshot(ANN)[0]
    check("plan: steps done / total and the current step", r["change"] == "activity" and row["steps_done"] == 1
          and row["steps_total"] == 3 and row["activity"] == "Fixing the retry" and row["rev"] == 1, row)

    # Activity changes inside the interval: one trailing push with the newest state.
    await asyncio.sleep(agent_sessions.PUSH_INTERVAL)
    await hook(client, ANN, "claude", claude("PreToolUse", tool_name="Edit", tool_input={"file_path": "/x/push.py"}))
    n = len(PINGS)
    await hook(client, ANN, "claude", claude("PreToolUse", tool_name="Bash", tool_input={"command": "bin/test"}))
    await hook(client, ANN, "claude", claude("PostToolUse", tool_name="Bash", tool_input={"command": "bin/test"},
                                             tool_response={"stdout": "ok"}))
    check("throttle: activity inside the interval waits", len(PINGS) == n, PINGS)
    await asyncio.sleep(agent_sessions.PUSH_INTERVAL + 0.2)
    s = latest_list(ANN)
    check("throttle: the trailing push carries the latest activity", len(PINGS) == n + 1
          and s[0]["activity"] == "Running bin/test" and s[0]["tools"] == 3, (PINGS, s))
    check("replace: only the newest agent_sessions event is kept for the phone", len(events_of(ANN)) == 1,
          events_of(ANN))

    n = len(PINGS)
    r = await hook(client, ANN, "claude", claude("PermissionRequest", tool_name="Bash",
                                                 tool_input={"command": "rm -rf build"}))
    s = latest_list(ANN)
    check("permission: waiting, pushed at once, rev up", r["change"] == "status" and s[0]["status"] == "waiting"
          and s[0]["activity"] == "Needs permission: Running rm -rf build" and s[0]["rev"] == 2
          and len(PINGS) == n + 1, s)
    r = await hook(client, ANN, "claude", claude("PostToolUse", tool_name="Bash", tool_input={"command": "rm -rf build"}))
    check("permission granted: running again", r["change"] == "status" and latest_list(ANN)[0]["status"] == "running"
          and latest_list(ANN)[0]["rev"] == 3)
    url = "https://app.t3.codes/threads/env-1/thread-1"
    r = await hook(client, ANN, "claude", claude("PostToolUse", tool_name="Read", tool_input={}), open_url=url)
    check("open_url: an https link is kept and pushed", r["change"] == "activity"
          and agent_sessions.snapshot(ANN)[0]["open_url"] == url)
    await hook(client, ANN, "claude", claude("PostToolUse", tool_name="Read", tool_input={}),
               open_url="javascript:alert(1)")
    check("open_url: a non-https link is refused, the old one kept", agent_sessions.snapshot(ANN)[0]["open_url"] == url)
    r = await hook(client, ANN, "claude", claude("Notification", notification_type="idle_prompt",
                                                 message="Claude is waiting for your input"))
    check("idle notification changes nothing", r["change"] is None)

    r = await hook(client, ANN, "claude", claude("Stop", stop_hook_active=False,
                                                 last_assistant_message="Fixed the retry.\nAll tests pass."))
    s = latest_list(ANN)
    check("stop: done with the start of the last message", r["change"] == "status" and s[0]["status"] == "done"
          and s[0]["summary"] == "Fixed the retry." and s[0]["rev"] == 4, s)

    # Codex, same user: a second session; a failure.
    await hook(client, ANN, "codex", {"session_id": "c-9", "hook_event_name": "UserPromptSubmit",
                                      "cwd": "/w/homelab", "prompt": "bump the tag", "model": "gpt-5.5"})
    await hook(client, ANN, "codex", {"session_id": "c-9", "hook_event_name": "PreToolUse", "cwd": "/w/homelab",
                                      "tool_name": "update_plan", "tool_input": {"plan": [
                                          {"step": "Find the values file", "status": "completed"},
                                          {"step": "Bump it", "status": "completed"}]}})
    ids = [x["id"] for x in agent_sessions.snapshot(ANN)]
    row = next(x for x in agent_sessions.snapshot(ANN) if x["id"] == "codex:c-9")
    check("codex: its own session, plan from update_plan (no current step keeps the activity)",
          ids == ["codex:c-9", "claude:s-1"] and row["steps_done"] == 2 and row["steps_total"] == 2
          and row["activity"] == "Thinking" and row["project"] == "homelab", (ids, row))
    await hook(client, ANN, "claude", claude("StopFailure", error="rate_limit"))
    row = next(x for x in agent_sessions.snapshot(ANN) if x["id"] == "claude:s-1")
    check("StopFailure: failed with the error", row["status"] == "failed" and row["summary"] == "rate_limit", row)

    # Questions wait; ExitPlanMode too.
    await hook(client, ANN, "codex", {"session_id": "c-9", "hook_event_name": "PreToolUse",
                                      "tool_name": "AskUserQuestion", "tool_input": {}})
    row = next(x for x in agent_sessions.snapshot(ANN) if x["id"] == "codex:c-9")
    check("question: waiting", row["status"] == "waiting" and row["activity"] == "Asking you a question", row)

    # Old rows drop out.
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=agent_sessions.DONE_KEEP + 5))
    with get_db_connection() as conn:
        conn.execute("UPDATE agent_sessions SET updated_at = ? WHERE id = 'claude:s-1'",
                     (old.isoformat(timespec="seconds"),))
        conn.commit()
    check("finished sessions leave after DONE_KEEP", [x["id"] for x in agent_sessions.snapshot(ANN)] == ["codex:c-9"])
    stale = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=agent_sessions.STALE_AFTER + 5)
    with get_db_connection() as conn:
        conn.execute("UPDATE agent_sessions SET updated_at = ? WHERE id = 'codex:c-9'",
                     (stale.isoformat(timespec="seconds"),))
        conn.commit()
    check("silent running sessions leave after STALE_AFTER", agent_sessions.snapshot(ANN) == [])

    await hook(client, ANN, "claude", claude("UserPromptSubmit", prompt="again"))
    r = await hook(client, ANN, "claude", claude("SessionEnd", reason="exit"))
    check("SessionEnd: removed and pushed", r["change"] == "status" and latest_list(ANN) == [])
    r = await hook(client, ANN, "claude", claude("SessionEnd", reason="exit"))
    check("SessionEnd of an unknown session: nothing", r["change"] is None)
    r = await hook(client, ANN, "claude", claude("Stop", last_assistant_message="late"))
    check("a Stop for a session never seen still shows it finished", r["change"] == "status"
          and latest_list(ANN)[0]["status"] == "done")


async def test_users(client) -> None:
    PINGS.clear()
    await hook(client, BOB, "claude", claude("UserPromptSubmit", prompt="Bob's work"))
    check("isolation: Bob's push reaches only Bob's phone", [p["token"] for p in PINGS] == ["tok-b"], PINGS)
    r = await client.get("/api/agent-sessions", headers=auth(BOB))
    bob = (await r.json())["sessions"]
    r = await client.get("/api/agent-sessions", headers=auth(ANN))
    ann = (await r.json())["sessions"]
    check("isolation: the same session id under two users is two sessions",
          [x["title"] for x in bob] == ["Bob's work"] and all(x["title"] != "Bob's work" for x in ann)
          and all(x["title"] != "late" for x in bob), (bob, ann))
    check("isolation: Ann's list in her phone events holds none of Bob's",
          all(x["title"] != "Bob's work" for x in latest_list(ANN)))

    r = await client.delete("/api/agent-sessions/claude:s-1", headers=auth(CARL))
    check("no phone kind: 403 not_connected", r.status == 403 and (await r.json()).get("error") == "not_connected")
    r = await client.post("/api/agent-sessions/hook?agent=claude", headers=auth(CARL), json=claude("Stop"))
    check("no phone kind: hook 403", r.status == 403)
    r = await client.get("/api/agent-sessions", headers=auth(CARL))
    check("no phone kind: list 403", r.status == 403)

    r = await client.delete("/api/agent-sessions/claude:nope", headers=auth(BOB))
    check("hide: an unknown id → 404", r.status == 404)
    PINGS.clear()
    r = await client.delete("/api/agent-sessions/claude:s-1", headers=auth(BOB))
    check("hide: removes only the caller's session and pushes their list", r.status == 200
          and agent_sessions.snapshot(BOB) == [] and len(agent_sessions.snapshot(ANN)) == 1
          and [p["token"] for p in PINGS] == ["tok-b"])

    r = await client.post("/api/agent-sessions/hook?agent=gemini", headers=auth(ANN), json=claude("Stop"))
    check("bad agent → 400", r.status == 400)
    r = await client.post("/api/agent-sessions/hook?agent=claude", headers=auth(ANN), json={"hook_event_name": "Stop"})
    check("no session id → 400", r.status == 400)
    r = await client.post("/api/agent-sessions/hook?agent=claude", headers=auth(ANN), data="nope")
    check("not JSON → 400", r.status == 400)
    r = await client.post("/api/agent-sessions/hook?agent=claude", json=claude("Stop"))
    check("no token → 401", r.status == 401)


def entry(user, sid: str) -> Dict[str, Any]:
    return next((x for x in agent_sessions.snapshot(user) if x["id"] == sid), {})


def age(sid: str, seconds: float) -> None:
    """Make a row look last heard of ``seconds`` ago."""
    when = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)
    with get_db_connection() as conn:
        conn.execute("UPDATE agent_sessions SET updated_at = ? WHERE id = ?", (when.isoformat(timespec="seconds"), sid))
        conn.commit()


async def test_subagents(client) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM agent_sessions")
        conn.commit()
    url = "https://app.t3.codes/threads/env-1/root-1"
    t3 = {"open_url": url, "thread": "root-1", "title": "Data feature ideas"}

    # Claude Code's own subagent: same session id, its events carry agent_id.
    def main_(name: str, **f) -> Dict[str, Any]:
        return {"session_id": "p-1", "hook_event_name": name, "cwd": "/w/ganam-assistant", **f}

    await hook(client, ANN, "claude", main_("UserPromptSubmit", prompt="Delegated task node:delegated-task:x"), **t3)
    check("T3 thread title over the prompt", entry(ANN, "claude:p-1").get("title") == "Data feature ideas")
    await hook(client, ANN, "claude", main_("PreToolUse", tool_name="Agent",
                                            tool_input={"description": "Find the push code"}), **t3)
    r = await hook(client, ANN, "claude", main_("SubagentStart", agent_id="a1", agent_type="Explore"), **t3)
    await hook(client, ANN, "claude", main_("PreToolUse", agent_id="a1", agent_type="Explore", tool_name="Grep",
                                            tool_input={}), **t3)
    s = agent_sessions.snapshot(ANN)
    check("native subagent: inside its parent, not a session of its own",
          [x["id"] for x in s] == ["claude:p-1"] and [(a["title"], a["status"], a["activity"])
                                                       for a in s[0]["subagents"]] ==
          [("Explore", "running", "Searching the code")] and s[0]["activity"] == "Subagent: Find the push code", s)
    check("native subagent: its tool calls don't count as the parent's", s[0]["tools"] == 1, s)
    pushed = latest_list(ANN)
    check("native subagent: the pushed list nests it", r["change"] == "status" and len(pushed) == 1
          and pushed[0]["subagents"][0]["title"] == "Explore", pushed)

    rev = entry(ANN, "claude:p-1")["rev"]
    await hook(client, ANN, "claude", main_("PermissionRequest", agent_id="a1", tool_name="Bash",
                                            tool_input={"command": "rm -rf build"}), **t3)
    e = entry(ANN, "claude:p-1")
    check("a waiting subagent makes the group wait and raises the parent's rev",
          e["status"] == "waiting" and e["rev"] == rev + 1 and e["activity"].startswith("Explore: Needs permission"), e)
    await hook(client, ANN, "claude", main_("PostToolUse", agent_id="a1", tool_name="Bash", tool_input={}), **t3)
    await hook(client, ANN, "claude", main_("SubagentStop", agent_id="a1", agent_type="Explore",
                                            last_assistant_message="Found it in push.py"), **t3)
    e = entry(ANN, "claude:p-1")
    check("subagent done: listed done with its summary, the running parent stays running",
          e["status"] == "running" and e["subagents"][0]["status"] == "done"
          and e["subagents"][0]["summary"] == "Found it in push.py", e)

    # T3 Code delegated children: their own sessions, the root thread as parent.
    child = {"open_url": url, "thread": "thread:delegated-task:abc", "parent_thread": "root-1", "title": "Gmail kind"}
    await hook(client, ANN, "claude", main_("Stop", last_assistant_message="Started the delegates."), **t3)
    await hook(client, ANN, "claude", {"session_id": "k-1", "hook_event_name": "UserPromptSubmit",
                                       "cwd": "/t3/worktrees/t3-dcbbd5ef", "prompt": "Act as the sub-agent"}, **child)
    await hook(client, ANN, "claude", {"session_id": "k-1", "hook_event_name": "PreToolUse", "tool_name": "Edit",
                                       "tool_input": {"file_path": "/x/kind.py"}}, **child)
    s = agent_sessions.snapshot(ANN)
    check("delegated child: inside the session of its root thread, titled by its thread",
          [x["id"] for x in s] == ["claude:p-1"] and [a["title"] for a in s[0]["subagents"]] == ["Explore", "Gmail kind"],
          s)
    check("parent stopped, a subagent runs: the group runs and says what it waits for",
          s[0]["status"] == "running" and s[0]["activity"] == "Waiting for Gmail kind", s[0])

    age("claude:p-1", agent_sessions.DONE_KEEP + 60)
    check("a done parent with running subagents isn't pruned", entry(ANN, "claude:p-1").get("status") == "running")

    await hook(client, ANN, "claude", {"session_id": "k-1", "hook_event_name": "Stop",
                                       "last_assistant_message": "Report: gmail kind"}, **child)
    e = entry(ANN, "claude:p-1")
    check("a subagent finished after the parent stopped: running for the grace",
          e["status"] == "running" and e["activity"] == "Reading the subagents' results", e)
    age("claude:k-1", agent_sessions.GRACE + 5)
    age("claude:p-1/a1", agent_sessions.GRACE + 30)
    with get_db_connection() as conn:
        conn.execute("UPDATE agent_sessions SET updated_at = ? WHERE id = 'claude:p-1'",
                     ((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=agent_sessions.GRACE + 10))
                      .isoformat(timespec="seconds"),))
        conn.commit()
    e = entry(ANN, "claude:p-1")
    check("after the grace the group is done", e.get("status") == "done", e)

    await hook(client, ANN, "claude", main_("UserPromptSubmit", prompt="Delegated task finished"), **t3)
    await hook(client, ANN, "claude", main_("Stop", last_assistant_message="Both kinds are in."), **t3)
    e = entry(ANN, "claude:p-1")
    check("the parent going on and stopping: done with its own summary", e["status"] == "done"
          and e["summary"] == "Both kinds are in." and len(e["subagents"]) == 2, e)

    for sid in ("claude:p-1", "claude:p-1/a1", "claude:k-1"):
        age(sid, agent_sessions.DONE_KEEP + 5)
    check("a finished group leaves as one", agent_sessions.snapshot(ANN) == []
          and not get_db_connection().execute("SELECT 1 FROM agent_sessions WHERE user_id = ?", (ANN.id,)).fetchone())

    # Orphans show on their own; Hide and SessionEnd take the subagents.
    await hook(client, ANN, "claude", {"session_id": "k-2", "hook_event_name": "UserPromptSubmit", "prompt": "go"},
               **{**child, "parent_thread": "root-gone"})
    e = agent_sessions.snapshot(ANN)
    check("a delegated child whose parent isn't listed shows on its own, opening the root thread",
          [x["id"] for x in e] == ["claude:k-2"] and e[0]["open_url"] == url and e[0]["subagents"] == [], e)
    await hook(client, ANN, "claude", main_("UserPromptSubmit", prompt="x"), **t3)
    await hook(client, ANN, "claude", main_("SubagentStart", agent_id="a2", agent_type="Plan"), **t3)
    await hook(client, ANN, "claude", {"session_id": "k-3", "hook_event_name": "UserPromptSubmit", "prompt": "go"},
               **child)
    r = await client.delete("/api/agent-sessions/claude:p-1", headers=auth(ANN))
    check("hide: the parent and its subagents go, others stay", r.status == 200
          and [x["id"] for x in agent_sessions.snapshot(ANN)] == ["claude:k-2"], agent_sessions.snapshot(ANN))
    await hook(client, ANN, "claude", main_("UserPromptSubmit", prompt="x"), **t3)
    await hook(client, ANN, "claude", main_("SubagentStart", agent_id="a3", agent_type="Plan"), **t3)
    await hook(client, ANN, "claude", main_("SessionEnd", reason="exit"), **t3)
    check("SessionEnd: Claude's own subagents go with the session",
          not get_db_connection().execute("SELECT 1 FROM agent_sessions WHERE id LIKE 'claude:p-1%'").fetchone())
    check("subagent rows stay the user's own", all(not x["subagents"] for x in agent_sessions.snapshot(BOB)))


async def main() -> None:
    app = web.Application()
    setup_auth(app)
    phone_api.setup(app)
    async with TestClient(TestServer(app)) as client:
        await test_flow(client)
        await test_users(client)
        await test_subagents(client)


unit_activity()
asyncio.run(main())
passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
