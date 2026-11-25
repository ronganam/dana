"""Coding-agent sessions on the phone (addons/phone/agent_sessions.py): hook events → session state → live activities.
Run: bin/test test_agent_sessions

Two users with the ``phone`` addon (Ann, Bob) and one without (Carl), on the
real handlers with FCM faked at httpx. Checked: Claude Code and Codex hook
events become running / waiting / done / failed with the activity, plan steps,
title and summary; ``rev`` rises only on a status change; each listed session
is one live activity of the phone add-on (title, text, context, chip, tone,
progress, since, Hide, open, ends_at), a wait alerts once per ``rev``, a
finished one ends with its final (once), one leaving the list ends without;
Hide (live's stop) forgets the session through the phone's ``live_action``;
stale and old finished sessions drop out; ``SessionEnd`` removes one;
sessions and activities are each user's own (the same session id under two
users stays two sessions); no addon → 403 ``not_connected``; bad input → 400.
Subagents (Claude Code's own, T3 Code delegated children) show inside their
parent with the group's status: a waiting subagent alerts through the parent's
rev, the grace after one finishes, a done parent kept while its subagents run,
a finished group pruned as one, orphans on their own, Hide and SessionEnd take
the subagents; a T3 thread title wins over the prompt.
"""

import asyncio
import datetime
import os
import sys
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("agent_sessions_test")
os.environ["API_TOKEN"] = "agent-sessions-test-env-token"  # a test value: the first admin's token (a headless install)
os.environ["FCM_SERVICE_ACCOUNT_JSON"] = '{"project_id": "dana-test"}'
from cryptography.fernet import Fernet  # noqa: E402
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()

import httpx  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import db, devices, live, users  # noqa: E402

db.migrate()
users.bootstrap()

from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.addons import store  # noqa: E402
from addons.phone import agent_sessions, api as phone_api, hooks as phone_hooks, push  # noqa: E402, F401
from addons.phone.addon import addon as PHONE  # noqa: E402

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
TOKENS = {u.id: devices.create(u, "phone")[0] for u in (ANN, BOB, CARL)}  # each one's paired phone
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


def act(user, sid: str):
    """The running activity of session ``sid`` (a live.Activity), or None."""
    c = live.get(user, PHONE, agent_sessions._key(sid))
    return c.activity if c else None


def final(user, sid: str):
    """The final of session ``sid``'s ended activity, or None."""
    key = agent_sessions._key(sid)
    return next((c.final for c in live.listed(user) if c.addon == "phone" and c.key == key and c.final), None)


def live_pings(token: str = "") -> int:
    return sum(1 for p in PINGS if p["type"] == "live" and (not token or p["token"] == token))


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)
    pending = list(phone_api._hook_syncs)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    await live.drain()


async def hook(client, user, agent: str, event: Dict[str, Any], host: str = "laptop", open_url: str = "",
               **params: str) -> Dict[str, Any]:
    r = await client.post("/api/agent-sessions/hook", params={"agent": agent, "host": host, "open_url": open_url, **params},
                          headers=auth(user), json=event)
    body = await r.json()
    await settle()
    return {"status": r.status, **body}


def claude(name: str, **fields) -> Dict[str, Any]:
    return {"session_id": "s-1", "hook_event_name": name, "cwd": "/home/x/projects/dana",
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


def unit_mapping() -> None:
    """Statuses → live activity fields, and the final, on hand-made entries."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    t = now.isoformat()

    def entry(**f) -> Dict[str, Any]:
        e = {"id": "claude:x", "agent": "claude", "host": "laptop", "project": "dana", "title": "Fix the retry",
             "status": "running", "activity": "Editing push.py", "summary": "", "steps_done": 0, "steps_total": 0,
             "tools": 0, "rev": 1, "started_at": t, "updated_at": t, "open_url": "", "subagents": []}
        e.update(f)
        return e

    def sub(status: str, title: str = "Explore", summary: str = "") -> Dict[str, Any]:
        return {"id": "claude:x/a", "title": title, "status": status, "activity": "", "summary": summary,
                "steps_done": 0, "steps_total": 0, "started_at": t, "updated_at": t}

    a = agent_sessions.activity_of(entry())
    check("running: title, activity, agent · project, bot, neutral, since the prompt, Hide, no alert",
          a.title == "Fix the retry" and a.text == "Editing push.py" and a.context == "Claude Code · dana"
          and a.icon == "bot" and a.tone == "neutral" and a.since == now and a.chip == "" and a.alert == ""
          and a.actions == (live.Action("stop", "Hide", kind="stop"),) and a.open == "", a)
    check("running with neither plan nor subagents: an indeterminate bar",
          a.progress == live.Progress(indeterminate=True), a.progress)
    check("ends_at: an hour after it was last heard of (to the minute)",
          a.ends_at == now.replace(second=0) + datetime.timedelta(hours=1), a.ends_at)

    a = agent_sessions.activity_of(entry(steps_done=3, steps_total=7, open_url="https://app.t3.codes/t/1"))
    check("plan: step in the text, 3/7 chip, one segment per step, value = done, open_url",
          a.text == "Editing push.py · step 3/7" and a.chip == "3/7" and a.progress is not None
          and a.progress.segments == (live.Seg(1, "neutral"),) * 7 and a.progress.value == 3
          and a.open == "https://app.t3.codes/t/1", a)

    a = agent_sessions.activity_of(entry(status="waiting", activity="Needs permission: Running rm", rev=4,
                                         steps_done=1, steps_total=2))
    check("waiting: Needs you title, warn, the alert names the rev, the plan bar in warn",
          a.title == "Needs you · Fix the retry" and a.tone == "warn" and a.alert == "waiting:4"
          and a.chip == "Waiting" and a.progress is not None
          and a.progress.segments == (live.Seg(1, "warn"),) * 2, a)
    check("waiting with neither: no bar", agent_sessions.activity_of(entry(status="waiting")).progress is None)

    a = agent_sessions.activity_of(entry(subagents=[sub("running", "Plan"), sub("done"), sub("failed"),
                                                    sub("waiting")]))
    check("subagents: finished first, coloured by status, value = finished, 2/4 in text and chip",
          a.progress is not None and [s.tone for s in a.progress.segments] == ["good", "bad", "neutral", "warn"]
          and a.progress.value == 2 and a.text == "Editing push.py · 2/4 subagents done" and a.chip == "2/4", a)

    a = agent_sessions.activity_of(entry(title="", project="", agent="codex"))
    check("no title or project: the agent's name", a.title == "Codex" and a.context == "Codex", a)
    a = agent_sessions.activity_of(entry(title="x" * 120, status="waiting", steps_total=500, steps_done=250))
    check("long titles are cut, many steps make a plain bar",
          len(a.title) <= live.MAX_TITLE and a.progress is not None and a.progress.segments == (live.Seg(100, "warn"),)
          and a.progress.value == 50, a)

    f = agent_sessions.final_of(entry(status="done", summary="Fixed it.", open_url="https://app.t3.codes/t/1",
                                      subagents=[sub("done", "Explore", "Found push.py"), sub("failed", "Plan")]))
    check("done: the final has the summary and the subagents' results, good, open_url",
          f == live.Final(title="Done · Fix the retry", text="Fixed it.\n✓ Explore: Found push.py\n✗ Plan: failed",
                          tone="good", open="https://app.t3.codes/t/1"), f)
    f = agent_sessions.final_of(entry(status="failed", summary="rate_limit"))
    check("failed: bad", f.title == "Failed · Fix the retry" and f.tone == "bad" and f.text == "rate_limit", f)

    check("key: a session id that fits is the key", agent_sessions._key("claude:s-1") == "claude:s-1")
    k = agent_sessions._key("claude:p-1/a1")
    check("key: one that doesn't is a stable hash in the charset", k == agent_sessions._key("claude:p-1/a1")
          and live.KEY.match(k) and k != agent_sessions._key("claude:p-1/a2"), k)


async def test_flow(client) -> None:
    await client.post("/api/phone/register", headers=auth(ANN), json={"transport": "fcm", "token": "tok-a"})
    await client.post("/api/phone/register", headers=auth(BOB), json={"transport": "fcm", "token": "tok-b"})
    PINGS.clear()
    r = await hook(client, ANN, "claude", claude("SessionStart", source="startup"))
    check("SessionStart alone shows nothing", r["status"] == 200 and r["change"] is None
          and agent_sessions.snapshot(ANN) == [] and live.active(ANN) == [], r)

    r = await hook(client, ANN, "claude", claude("UserPromptSubmit", prompt="\n  Fix the   push retry\nmore text"))
    s = agent_sessions.snapshot(ANN)
    a = act(ANN, "claude:s-1")
    check("prompt: the session runs, titled by the prompt's first line",
          r["change"] == "status" and len(s) == 1 and s[0]["status"] == "running"
          and s[0]["title"] == "Fix the push retry" and s[0]["project"] == "dana"
          and s[0]["agent"] == "claude" and s[0]["host"] == "laptop" and s[0]["id"] == "claude:s-1"
          and s[0]["rev"] == 1, (r, s))
    check("prompt: its live activity starts, pushed at once", a is not None and a.title == "Fix the push retry"
          and a.text == "Thinking" and a.context == "Claude Code · dana" and live_pings("tok-a") == 1, (a, PINGS))

    r = await hook(client, ANN, "claude", claude("PreToolUse", tool_name="TodoWrite", tool_input={"todos": [
        {"content": "Read", "activeForm": "Reading push.py", "status": "completed"},
        {"content": "Fix", "activeForm": "Fixing the retry", "status": "in_progress"},
        {"content": "Test", "activeForm": "Testing", "status": "pending"}]}))
    row = agent_sessions.snapshot(ANN)[0]
    a = act(ANN, "claude:s-1")
    check("plan: steps done / total and the current step", r["change"] == "activity" and row["steps_done"] == 1
          and row["steps_total"] == 3 and row["activity"] == "Fixing the retry" and row["rev"] == 1, row)
    check("plan: the activity follows, its push batched by live", a.text == "Fixing the retry · step 1/3"
          and a.chip == "1/3" and live_pings("tok-a") == 1, (a, PINGS))

    r = await hook(client, ANN, "claude", claude("PermissionRequest", tool_name="Bash",
                                                 tool_input={"command": "rm -rf build"}))
    s = agent_sessions.snapshot(ANN)
    a = act(ANN, "claude:s-1")
    check("permission: waiting, rev up, the activity alerts at once", r["change"] == "status"
          and s[0]["status"] == "waiting" and s[0]["activity"] == "Needs permission: Running rm -rf build"
          and s[0]["rev"] == 2 and a.alert == "waiting:2" and a.tone == "warn"
          and a.title == "Needs you · Fix the push retry" and live_pings("tok-a") == 2, (s, a, PINGS))
    await hook(client, ANN, "claude", claude("Notification", notification_type="permission_prompt",
                                             message="Claude needs your permission to use Bash"))
    check("still waiting: the same alert, no new push", act(ANN, "claude:s-1").alert == "waiting:2"
          and live_pings("tok-a") == 2, PINGS)
    r = await hook(client, ANN, "claude", claude("PostToolUse", tool_name="Bash", tool_input={"command": "rm -rf build"}))
    check("permission granted: running again, no alert", r["change"] == "status"
          and agent_sessions.snapshot(ANN)[0]["rev"] == 3 and act(ANN, "claude:s-1").alert == "")
    await hook(client, ANN, "claude", claude("PreToolUse", tool_name="AskUserQuestion", tool_input={}))
    check("a new wait alerts again (a new rev)", act(ANN, "claude:s-1").alert == "waiting:4"
          and live_pings("tok-a") == 3, PINGS)
    await hook(client, ANN, "claude", claude("PostToolUse", tool_name="AskUserQuestion", tool_input={}))

    url = "https://app.t3.codes/threads/env-1/thread-1"
    r = await hook(client, ANN, "claude", claude("PostToolUse", tool_name="Read", tool_input={}), open_url=url)
    check("open_url: an https link is kept and opens the activity", r["change"] == "activity"
          and agent_sessions.snapshot(ANN)[0]["open_url"] == url and act(ANN, "claude:s-1").open == url)
    await hook(client, ANN, "claude", claude("PostToolUse", tool_name="Read", tool_input={}),
               open_url="javascript:alert(1)")
    check("open_url: a non-https link is refused, the old one kept", agent_sessions.snapshot(ANN)[0]["open_url"] == url)
    r = await hook(client, ANN, "claude", claude("Notification", notification_type="idle_prompt",
                                                 message="Claude is waiting for your input"))
    check("idle notification changes nothing", r["change"] is None)

    n = live_pings("tok-a")
    r = await hook(client, ANN, "claude", claude("Stop", stop_hook_active=False,
                                                 last_assistant_message="Fixed the retry.\nAll tests pass."))
    s = agent_sessions.snapshot(ANN)
    f = final(ANN, "claude:s-1")
    check("stop: done with the start of the last message", r["change"] == "status" and s[0]["status"] == "done"
          and s[0]["summary"] == "Fixed the retry." and s[0]["rev"] == 6, s)
    check("stop: the activity ends with its final, pushed at once", act(ANN, "claude:s-1") is None
          and f == live.Final(title="Done · Fix the push retry", text="Fixed the retry.", tone="good", open=url)
          and live_pings("tok-a") == n + 1, (f, PINGS))

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
    check("codex: its own activity", act(ANN, "codex:c-9").context == "Codex · homelab")
    await hook(client, ANN, "claude", claude("UserPromptSubmit", prompt="Once more"))
    check("a new prompt after done starts the activity again", act(ANN, "claude:s-1").title == "Once more")
    await hook(client, ANN, "claude", claude("StopFailure", error="rate_limit"))
    row = next(x for x in agent_sessions.snapshot(ANN) if x["id"] == "claude:s-1")
    f = final(ANN, "claude:s-1")
    check("StopFailure: failed with the error, a bad final", row["status"] == "failed"
          and row["summary"] == "rate_limit" and f is not None and f.tone == "bad"
          and f.title == "Failed · Once more", (row, f))
    n = live_pings("tok-a")
    await agent_sessions.sync(ANN)
    await live.drain()
    check("a finished session's final is shown once (a later sync neither restarts nor ends it again)",
          act(ANN, "claude:s-1") is None and final(ANN, "claude:s-1").tone == "bad" and live_pings("tok-a") == n,
          live_pings("tok-a") - n)

    # Old rows drop out; a silent running one ends without a final.
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
    await agent_sessions.sync(ANN)
    await live.drain()
    check("silent running sessions leave after STALE_AFTER, their activity ends without a final",
          agent_sessions.snapshot(ANN) == [] and act(ANN, "codex:c-9") is None and final(ANN, "codex:c-9") is None)

    await hook(client, ANN, "claude", claude("UserPromptSubmit", prompt="again"))
    r = await hook(client, ANN, "claude", claude("SessionEnd", reason="exit"))
    check("SessionEnd: removed, its activity ends without a final", r["change"] == "status"
          and agent_sessions.snapshot(ANN) == [] and act(ANN, "claude:s-1") is None
          and final(ANN, "claude:s-1") is None)
    r = await hook(client, ANN, "claude", claude("SessionEnd", reason="exit"))
    check("SessionEnd of an unknown session: nothing", r["change"] is None)
    r = await hook(client, ANN, "claude", claude("Stop", last_assistant_message="late"))
    check("a Stop for a session never seen is listed done but shows no activity", r["change"] == "status"
          and agent_sessions.snapshot(ANN)[0]["status"] == "done" and act(ANN, "claude:s-1") is None)


async def test_users(client) -> None:
    PINGS.clear()
    await hook(client, BOB, "claude", claude("UserPromptSubmit", prompt="Bob's work"))
    check("isolation: Bob's activity reaches only Bob's phone", [p["token"] for p in PINGS] == ["tok-b"], PINGS)
    bob, ann = agent_sessions.snapshot(BOB), agent_sessions.snapshot(ANN)
    check("isolation: the same session id under two users is two sessions",
          [x["title"] for x in bob] == ["Bob's work"] and all(x["title"] != "Bob's work" for x in ann)
          and all(x["title"] != "late" for x in bob), (bob, ann))
    check("isolation: each sees only their own activities", act(BOB, "claude:s-1").title == "Bob's work"
          and all(c.activity.title != "Bob's work" for c in live.active(ANN)))

    await hook(client, ANN, "claude", claude("UserPromptSubmit", prompt="Ann's work"))
    check("hide: another user's id is not theirs", not await live.stop(BOB, "phone:nope"))
    PINGS.clear()
    ok = await live.stop(BOB, f"phone:{agent_sessions._key('claude:s-1')}")
    await live.drain()
    check("hide: live's stop forgets only the caller's session", ok and agent_sessions.snapshot(BOB) == []
          and act(BOB, "claude:s-1") is None and act(ANN, "claude:s-1").title == "Ann's work"
          and len(agent_sessions.snapshot(ANN)) == 1 and [p["token"] for p in PINGS] == ["tok-b"],
          (agent_sessions.snapshot(BOB), PINGS))

    r = await client.post("/api/agent-sessions/hook?agent=claude", headers=auth(CARL), json=claude("Stop"))
    check("no phone addon: hook 403 not_connected", r.status == 403
          and (await r.json()).get("error") == "not_connected")
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
    await agent_sessions.sync(ANN)
    url = "https://app.t3.codes/threads/env-1/root-1"
    t3 = {"open_url": url, "thread": "root-1", "title": "Data feature ideas"}

    # Claude Code's own subagent: same session id, its events carry agent_id.
    def main_(name: str, **f) -> Dict[str, Any]:
        return {"session_id": "p-1", "hook_event_name": name, "cwd": "/w/dana", **f}

    await hook(client, ANN, "claude", main_("UserPromptSubmit", prompt="Delegated task node:delegated-task:x"), **t3)
    check("T3 thread title over the prompt", entry(ANN, "claude:p-1").get("title") == "Data feature ideas"
          and act(ANN, "claude:p-1").title == "Data feature ideas")
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
    a = act(ANN, "claude:p-1")
    check("native subagent: on the parent's activity, no activity of its own", r["change"] == "status"
          and a.text == "Subagent: Find the push code · 0/1 subagent done" and a.chip == "0/1"
          and [c.key for c in live.active(ANN) if c.addon == "phone"] == ["claude:p-1"], (a, live.active(ANN)))

    rev = entry(ANN, "claude:p-1")["rev"]
    await hook(client, ANN, "claude", main_("PermissionRequest", agent_id="a1", tool_name="Bash",
                                            tool_input={"command": "rm -rf build"}), **t3)
    e = entry(ANN, "claude:p-1")
    check("a waiting subagent makes the group wait and raises the parent's rev (an alert)",
          e["status"] == "waiting" and e["rev"] == rev + 1 and e["activity"].startswith("Explore: Needs permission")
          and act(ANN, "claude:p-1").alert == f"waiting:{rev + 1}", e)
    await hook(client, ANN, "claude", main_("PostToolUse", agent_id="a1", tool_name="Bash", tool_input={}), **t3)
    await hook(client, ANN, "claude", main_("SubagentStop", agent_id="a1", agent_type="Explore",
                                            last_assistant_message="Found it in push.py"), **t3)
    e = entry(ANN, "claude:p-1")
    check("subagent done: listed done with its summary, the running parent stays running",
          e["status"] == "running" and e["subagents"][0]["status"] == "done"
          and e["subagents"][0]["summary"] == "Found it in push.py"
          and act(ANN, "claude:p-1").progress.segments == (live.Seg(1, "good"),), e)

    # T3 Code delegated children: their own sessions, the root thread as parent.
    child = {"open_url": url, "thread": "thread:delegated-task:abc", "parent_thread": "root-1", "title": "Gmail addon"}
    await hook(client, ANN, "claude", main_("Stop", last_assistant_message="Started the delegates."), **t3)
    await hook(client, ANN, "claude", {"session_id": "k-1", "hook_event_name": "UserPromptSubmit",
                                       "cwd": "/t3/worktrees/t3-dcbbd5ef", "prompt": "Act as the sub-agent"}, **child)
    await hook(client, ANN, "claude", {"session_id": "k-1", "hook_event_name": "PreToolUse", "tool_name": "Edit",
                                       "tool_input": {"file_path": "/x/kind.py"}}, **child)
    s = agent_sessions.snapshot(ANN)
    check("delegated child: inside the session of its root thread, titled by its thread",
          [x["id"] for x in s] == ["claude:p-1"] and [a["title"] for a in s[0]["subagents"]] == ["Explore", "Gmail addon"],
          s)
    check("parent stopped, a subagent runs: the group runs and says what it waits for",
          s[0]["status"] == "running" and s[0]["activity"] == "Waiting for Gmail addon"
          and act(ANN, "claude:p-1") is not None and final(ANN, "claude:p-1") is None, s[0])

    age("claude:p-1", agent_sessions.DONE_KEEP + 60)
    check("a done parent with running subagents isn't pruned", entry(ANN, "claude:p-1").get("status") == "running")

    await hook(client, ANN, "claude", {"session_id": "k-1", "hook_event_name": "Stop",
                                       "last_assistant_message": "Report: gmail addon"}, **child)
    e = entry(ANN, "claude:p-1")
    check("a subagent finished after the parent stopped: running for the grace",
          e["status"] == "running" and e["activity"] == "Reading the subagents' results"
          and act(ANN, "claude:p-1") is not None, e)
    check("the grace schedules a sync for when it ends", ANN.id in agent_sessions._grace)
    age("claude:k-1", agent_sessions.GRACE + 5)
    age("claude:p-1/a1", agent_sessions.GRACE + 30)
    age("claude:p-1", agent_sessions.GRACE + 10)
    await agent_sessions.sync(ANN)
    await live.drain()
    e = entry(ANN, "claude:p-1")
    f = final(ANN, "claude:p-1")
    check("after the grace the group is done: its final lists the subagents' results",
          e.get("status") == "done" and act(ANN, "claude:p-1") is None and f is not None
          and f.text == "Started the delegates.\n✓ Explore: Found it in push.py\n✓ Gmail addon: Report: gmail addon",
          (e, f))

    await hook(client, ANN, "claude", main_("UserPromptSubmit", prompt="Delegated task finished"), **t3)
    await hook(client, ANN, "claude", main_("Stop", last_assistant_message="Both kinds are in."), **t3)
    e = entry(ANN, "claude:p-1")
    check("the parent going on and stopping: done with its own summary", e["status"] == "done"
          and e["summary"] == "Both kinds are in." and len(e["subagents"]) == 2
          and final(ANN, "claude:p-1").text.startswith("Both kinds are in."), e)

    for sid in ("claude:p-1", "claude:p-1/a1", "claude:k-1"):
        age(sid, agent_sessions.DONE_KEEP + 5)
    check("a finished group leaves as one", agent_sessions.snapshot(ANN) == []
          and not get_db_connection().execute("SELECT 1 FROM agent_sessions WHERE user_id = ?", (ANN.id,)).fetchone())

    # Orphans show on their own; Hide and SessionEnd take the subagents.
    await hook(client, ANN, "claude", {"session_id": "k-2", "hook_event_name": "UserPromptSubmit", "prompt": "go"},
               **{**child, "parent_thread": "root-gone"})
    e = agent_sessions.snapshot(ANN)
    check("a delegated child whose parent isn't listed shows on its own, opening the root thread",
          [x["id"] for x in e] == ["claude:k-2"] and e[0]["open_url"] == url and e[0]["subagents"] == []
          and act(ANN, "claude:k-2").open == url, e)
    await hook(client, ANN, "claude", main_("UserPromptSubmit", prompt="x"), **t3)
    await hook(client, ANN, "claude", main_("SubagentStart", agent_id="a2", agent_type="Plan"), **t3)
    await hook(client, ANN, "claude", {"session_id": "k-3", "hook_event_name": "UserPromptSubmit", "prompt": "go"},
               **child)
    ok = await live.stop(ANN, "phone:claude:p-1")
    await live.drain()
    check("hide: the parent and its subagents go, others stay", ok
          and [x["id"] for x in agent_sessions.snapshot(ANN)] == ["claude:k-2"] and act(ANN, "claude:p-1") is None
          and act(ANN, "claude:k-2") is not None, agent_sessions.snapshot(ANN))
    await hook(client, ANN, "claude", main_("UserPromptSubmit", prompt="x"), **t3)
    check("hidden, then a new event: shown again", act(ANN, "claude:p-1") is not None)
    await hook(client, ANN, "claude", main_("SubagentStart", agent_id="a3", agent_type="Plan"), **t3)
    await hook(client, ANN, "claude", main_("SessionEnd", reason="exit"), **t3)
    check("SessionEnd: Claude's own subagents go with the session",
          not get_db_connection().execute("SELECT 1 FROM agent_sessions WHERE id LIKE 'claude:p-1%'").fetchone()
          and act(ANN, "claude:p-1") is None)
    check("subagent rows stay the user's own", all(not x["subagents"] for x in agent_sessions.snapshot(BOB)))
    for task in list(agent_sessions._grace.values()):
        task.cancel()


async def main() -> None:
    app = web.Application()
    setup_auth(app)
    phone_api.setup(app)
    async with TestClient(TestServer(app)) as client:
        await test_flow(client)
        await test_users(client)
        await test_subagents(client)


unit_activity()
unit_mapping()
asyncio.run(main())
passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
sys.exit(0 if passed == len(results) else 1)
