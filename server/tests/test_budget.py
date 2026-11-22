"""The monthly budget: setting it (an admin can, a member cannot; bad limits refused), the budget in
/api/usage (amounts for admins, the state only for members), the 80 % Now card (admins only, dismissed
for the month), the pause at 100 % (a background purpose refused in complete / complete_json / transcribe
without calling the model, chat and user-started calls kept, an agent run or a user_started block let
through, background loops skip quietly, a job pass that hits it counts as done), the budget self-check,
and raising or removing the budget resuming background work.

LiteLLM runs with ``mock_response`` (no network).

Run: bin/test test_budget
"""
import asyncio
import contextvars
import datetime
import os
import sys
from types import SimpleNamespace

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("budget_test")
os.environ["API_TOKEN"] = "budget-test-env-token"  # a test value: the first admin's token (a headless install)
os.environ["ANTHROPIC_API_KEY"] = "test-key"  # a test value: LiteLLM only ever runs its offline mock here
os.environ["TRANSCRIBE_MODEL"] = "gemini/gemini-3.1-flash-lite"
os.environ["TIMEZONE"] = "Europe/London"

import litellm
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users
from addons import bootstrap as addons_bootstrap

db.migrate()
users.bootstrap()
addons_bootstrap.bootstrap()
from core import budget, checks, clock, llm, now_api, runs, usage, usage_api
from core.addons import scheduler
from core.addons.hooks import Job
from core.auth import setup_auth
from core.db import get_db_connection

ROBIN = users.first_admin()
GAL = users.create("Gal")
MAYA = users.create("Maya", "admin")
ROBIN_TOKEN, _ = users.issue_token(ROBIN.id, "test")
GAL_TOKEN, _ = users.issue_token(GAL.id, "test")
MAYA_TOKEN, _ = users.issue_token(MAYA.id, "test")

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def auth(token):
    return {"Authorization": f"Bearer {token}"}


CALLS = []


async def fake_acompletion(**kw):
    CALLS.append(kw.get("litellm_metadata", {}).get("usage", {}).get("purpose"))
    if kw.get("response_format"):
        kw["mock_response"] = '{"ok": true}'
    else:
        kw["mock_response"] = "fine"
    return await _real_acompletion(**kw)


_real_acompletion = litellm.acompletion
litellm.acompletion = fake_acompletion


def spend(amount):
    """Record a call of ``amount`` USD this month (made for nobody: the budget counts everyone's)."""
    usage.record(kind="llm", purpose="chat", model="zz-test", cost=amount, user_id="")
    usage.flush_sync()
    budget.forget_cache()


def reset_usage():
    usage._buffer.clear()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM usage_events")
        conn.execute("DELETE FROM usage_daily")
        conn.commit()
    budget.forget_cache()


def build_app():
    app = web.Application()
    setup_auth(app)
    usage_api.setup(app)
    now_api.setup(app)
    return app


async def test_setting(client):
    r = await client.put("/api/usage/budget", json={"limit": 10}, headers=auth(GAL_TOKEN))
    body = await r.json()
    check("set: a member cannot set the budget (403 forbidden)", r.status == 403 and body.get("needs") == "manage_server", body)
    check("set: nothing was stored by the member", budget.limit() is None)
    for bad in (-1, "ten", True, {"x": 1}):
        r = await client.put("/api/usage/budget", json={"limit": bad}, headers=auth(ROBIN_TOKEN))
        check(f"set: limit {bad!r} refused (400)", r.status == 400 and budget.limit() is None, await r.text())
    r = await client.put("/api/usage/budget", json={}, headers=auth(ROBIN_TOKEN))
    check("set: a body without limit is refused", r.status == 400)
    r = await client.put("/api/usage/budget", json={"limit": 10}, headers=auth(ROBIN_TOKEN))
    body = await r.json()
    check("set: an admin sets $10", r.status == 200 and body["limit"] == 10 and body["state"] == "ok"
          and body["can_edit"] is True and budget.limit() == 10, body)
    month = clock.now().strftime("%Y-%m")
    check("set: the answer names this month and the day it resets",
          body["month"] == month and body["resets"] > clock.now().date().isoformat(), body)

    r = await client.get("/api/usage", headers=auth(ROBIN_TOKEN))
    b = (await r.json())["budget"]
    check("usage: an admin gets {limit, spent, forecast, state, can_edit}",
          b["limit"] == 10 and b["spent"] == 0 and b["forecast"] == 0 and b["state"] == "ok" and b["can_edit"], b)
    r = await client.get("/api/usage", headers=auth(MAYA_TOKEN))
    check("usage: another admin sees the same budget", (await r.json())["budget"]["limit"] == 10)
    r = await client.get("/api/usage", headers=auth(GAL_TOKEN))
    b = (await r.json())["budget"]
    check("usage: a member gets the state only, no amounts", b == {"state": "ok", "can_edit": False}, b)


async def test_warn(client):
    reset_usage()
    spend(8.5)
    st = budget.state()
    check("warn: 85% of the budget is warn", st["state"] == "warn" and st["spent"] == 8.5, st)
    days = (datetime.date.fromisoformat(st["resets"]) - clock.now().date().replace(day=1)).days
    check("warn: the forecast carries the daily rate to the month's end",
          abs(st["forecast"] - 8.5 / clock.now().day * days) < 0.001, st)
    check("warn: background work still runs at 80%", not budget.holds("people_brief"))

    month = st["month"]
    r = await client.get("/api/now", headers=auth(ROBIN_TOKEN))
    items = [i for i in (await r.json())["items"] if i["kind"] == "budget"]
    check("warn: the admin's Now has the budget card", len(items) == 1 and items[0]["id"] == f"budget:{month}"
          and "85%" in items[0]["title"] and items[0]["secondary"][0]["action"] == "dismiss", items)
    r = await client.get("/api/now", headers=auth(MAYA_TOKEN))
    check("warn: the other admin has it too", any(i["kind"] == "budget" for i in (await r.json())["items"]))
    r = await client.get("/api/now", headers=auth(GAL_TOKEN))
    check("warn: a member's Now has no budget card", not any(i["kind"] == "budget" for i in (await r.json())["items"]))
    r = await client.post("/api/now/act", json={"id": f"budget:{month}", "action": "dismiss"}, headers=auth(GAL_TOKEN))
    check("warn: a member cannot act on it", r.status == 400, await r.text())
    r = await client.post("/api/now/act", json={"id": "budget:2001-01", "action": "dismiss"}, headers=auth(ROBIN_TOKEN))
    check("warn: a past month's card is 409", r.status == 409, await r.text())
    r = await client.post("/api/now/act", json={"id": f"budget:{month}", "action": "dismiss"}, headers=auth(ROBIN_TOKEN))
    body = await r.json()
    check("warn: dismissed, with an undo", r.status == 200 and body.get("undo_id"), body)
    r = await client.get("/api/now", headers=auth(ROBIN_TOKEN))
    check("warn: dismissed for the month: gone from the admin's Now",
          not any(i["kind"] == "budget" for i in (await r.json())["items"]))
    r = await client.get("/api/now", headers=auth(MAYA_TOKEN))
    check("warn: still on the other admin's Now (dismissing is per user)",
          any(i["kind"] == "budget" for i in (await r.json())["items"]))
    c = {x["key"]: x for x in checks.for_user(ROBIN)}
    check("warn: the budget self-check is still ok", c["budget"]["ok"], c.get("budget"))


async def test_paused(client):
    spend(2.0)
    st = budget.state()
    check("paused: $10.50 of $10 is paused", st["state"] == "paused", st)
    r = await client.get("/api/usage", headers=auth(GAL_TOKEN))
    check("paused: a member sees the paused state", (await r.json())["budget"] == {"state": "paused", "can_edit": False})
    r = await client.get("/api/now", headers=auth(MAYA_TOKEN))
    items = (await r.json())["items"]
    check("paused: the warn card gives way to the budget check",
          not any(i["kind"] == "budget" for i in items) and any(i["id"] == "check:budget" for i in items), items)

    CALLS.clear()
    try:
        with users.acting_as(ROBIN):
            await llm.complete([{"role": "user", "content": "x"}], purpose="people_brief")
        check("paused: complete refuses a background purpose", False)
    except budget.Paused:
        check("paused: complete refuses a background purpose without calling the model", CALLS == [], CALLS)
    try:
        await llm.complete_json("x", {"type": "object"}, purpose="notification_summary", attempts=3)
        check("paused: complete_json refuses a background purpose", False)
    except budget.Paused as e:
        check("paused: complete_json refuses at once (no retries)", CALLS == [] and not llm.retryable(e), CALLS)
    try:
        await llm.transcribe(b"\x00" * 50, "audio/ogg", purpose="call_transcript")
        check("paused: transcribe refuses a background purpose", False)
    except budget.Paused:
        check("paused: transcribe refuses a background purpose", CALLS == [], CALLS)

    with users.acting_as(GAL):
        reply = await llm.complete([{"role": "user", "content": "hi"}], purpose="chat")
    check("paused: chat keeps working", reply == "fine" and CALLS == ["chat"], CALLS)
    text = await llm.transcribe(b"\x00" * 50, "audio/ogg", purpose="voice_transcript")
    check("paused: a voice turn's transcription keeps working", text == "fine" and CALLS[-1] == "voice_transcript", CALLS)
    data = await llm.complete_json("x", {"type": "object"}, purpose="task_emoji")
    check("paused: a user-started addon purpose keeps working", data == {"ok": True}, data)

    with budget.user_started():
        await llm.complete([{"role": "user", "content": "x"}], purpose="people_brief")
    check("paused: a user_started block lets a background purpose through", CALLS[-1] == "people_brief", CALLS)

    async def in_run():
        runs._current_run.set(SimpleNamespace(session_id="main", id="r1"))
        return await llm.complete_json("x", {"type": "object"}, purpose="money_classify")
    data = await asyncio.get_running_loop().create_task(in_run(), context=contextvars.copy_context())
    check("paused: inside an agent run a background purpose goes through", data == {"ok": True}
          and CALLS[-1] == "money_classify", CALLS)
    check("paused: outside the run the pause holds again", budget.holds("money_classify"))

    check("paused: holds() names background work only",
          budget.holds("about_learner") and budget.holds("wake_briefing") and not budget.holds("chat")
          and not budget.holds("voice_transcript") and not budget.holds("no_such_purpose"))

    seen = []

    async def job_func(user):
        seen.append(user.id)
        await llm.complete([{"role": "user", "content": "x"}], purpose="people_brief")

    job = Job("people", "zz_budget_job", job_func)
    out = await scheduler.run_once(job)
    mark = scheduler._read_mark("job:people.zz_budget_job")
    with get_db_connection() as conn:
        ok = conn.execute("SELECT ok FROM system_marks WHERE key = 'job:people.zz_budget_job'").fetchone()
    check("paused: a job pass that hits the pause counts as done, not failed",
          seen and set(out) == set(seen) and mark and ok and ok["ok"] == 1, (seen, out, dict(ok) if ok else None))

    c = {x["key"]: x for x in checks.for_user(ROBIN)}
    check("paused: the budget self-check says paused by the budget",
          not c["budget"]["ok"] and c["budget"]["detail"] == "paused by the budget", c.get("budget"))
    check("paused: a member has no budget check", "budget" not in {x["key"] for x in checks.for_user(GAL)})


async def test_resume(client):
    r = await client.put("/api/usage/budget", json={"limit": 50}, headers=auth(MAYA_TOKEN))
    body = await r.json()
    check("resume: raising the budget to $50 is ok again", r.status == 200 and body["state"] == "ok", body)
    CALLS.clear()
    await llm.complete([{"role": "user", "content": "x"}], purpose="people_brief")
    check("resume: background purposes run again", CALLS == ["people_brief"], CALLS)
    c = {x["key"]: x for x in checks.for_user(ROBIN)}
    check("resume: the self-check is ok", c["budget"]["ok"], c.get("budget"))

    await client.put("/api/usage/budget", json={"limit": 5}, headers=auth(ROBIN_TOKEN))
    check("remove: lowered under the spend pauses again", budget.holds("people_brief"))
    r = await client.put("/api/usage/budget", json={"limit": None}, headers=auth(ROBIN_TOKEN))
    body = await r.json()
    check("remove: null removes the budget", r.status == 200 and body["limit"] is None and body["state"] == "ok"
          and budget.limit() is None, body)
    check("remove: no budget never pauses", not budget.holds("people_brief"))
    r = await client.get("/api/usage", headers=auth(ROBIN_TOKEN))
    b = (await r.json())["budget"]
    check("remove: the month's spend and forecast still show", b["limit"] is None and 10.5 <= b["spent"] < 10.6 and b["forecast"] >= b["spent"], b)


async def main():
    client = TestClient(TestServer(build_app()))
    await client.start_server()
    try:
        await test_setting(client)
        await test_warn(client)
        await test_paused(client)
        await test_resume(client)
    finally:
        await client.close()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
    sys.exit(1 if failed else 0)


asyncio.run(main())
