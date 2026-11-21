"""Usage and cost tracking: capture, storage, roll-up, reads, API.

Covers: LiteLLM calls recorded through the callback (plain, structured, streamed, failed,
unpriced model, audio sent to a chat model) with attribution (explicit purpose / ref /
source, scope, live run) and cost / tokens from LiteLLM's price table; the agent loop's
streamed call; TypeSafe requests (priced by env, unpriced, failed, no key = nothing
recorded); realtime pricing (text / audio / cached tokens) and its events; the buffer
(never touches SQLite on record, batch write, failed write keeps rows, cap, never raises);
the roll-up (totals identical before / after, raw rows gone, late rows add, idempotent);
summary (totals, previous period, filled days, breakdowns, filters, unpriced, days in the
user's zone) and events (order, cost sort, cursor, filters); the HTTP API; and that
recording is far cheaper than the calls it tracks.

LiteLLM runs with ``mock_response`` (no network, real price table).

Run: bin/test test_usage
"""
import asyncio
import datetime
import json
import os
import sys
import time

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("usage_test")
os.environ.setdefault("GEMINI_API_KEY", "test-key")  # transcription
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["TIMEZONE"] = "Asia/Jerusalem"
os.environ["TRANSCRIBE_MODEL"] = "gemini/gemini-3.1-flash-lite"
os.environ.pop("TYPESAFE_COST_PER_MTOK", None)

import httpx
import litellm
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import db, users
from addons.tasks.groups import ensure_default_groups_for_everyone

db.migrate()
users.bootstrap()
OWNER = users.first_admin()
ensure_default_groups_for_everyone()
from core import clock, llm, runs, usage, usage_api
from addons.phone.notifications import triage
from core.auth import setup_auth
from core.db import get_db_connection

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def reset():
    usage._buffer.clear()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM usage_events")
        conn.execute("DELETE FROM usage_daily")
        conn.commit()


def stored():
    usage.flush_sync()
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM usage_events ORDER BY id").fetchall()]


# Route every LiteLLM call through its offline mock, keeping the real callbacks and prices.
_real_acompletion = litellm.acompletion
MOCK = {"reply": "hello there", "fail": None}


async def mocked_acompletion(**kw):
    if MOCK["fail"]:
        kw["mock_response"] = MOCK["fail"]
    else:
        kw.setdefault("mock_response", MOCK["reply"])
    return await _real_acompletion(**kw)


litellm.acompletion = mocked_acompletion


async def settle():
    """LiteLLM runs its callbacks on a background worker; give it a moment."""
    for _ in range(40):
        await asyncio.sleep(0.05)
        if usage._buffer:
            await asyncio.sleep(0.1)
            return


async def test_capture():
    reset()
    text = await llm.complete([{"role": "user", "content": "hi"}], job="chat", purpose="task_emoji")
    await settle()
    rows = stored()
    check("a plain completion is recorded", len(rows) == 1 and text == "hello there", rows)
    r = rows[0]
    check("record has model, provider, kind, purpose", r["kind"] == "llm" and r["purpose"] == "task_emoji"
          and r["provider"] == "anthropic" and r["model"], r)
    check("tokens are recorded", r["input_tokens"] > 0 and r["output_tokens"] > 0, r)
    check("cost comes from LiteLLM's price table", r["cost"] is not None and r["cost"] > 0, r)
    check("latency and ok", r["ok"] == 1 and r["ms"] >= 0 and r["error"] == "", r)
    check("outside any run, the source is background", r["source"] == "background", r)
    check("the day is in the user's zone", r["day"] == clock.from_timestamp(r["ts"]).strftime("%Y-%m-%d"), r)

    reset()
    MOCK["reply"] = json.dumps({"a": 1})
    data = await llm.complete_json("x", {"type": "object"}, job="calls", purpose="call_analysis", ref="call:7",
                                   source="phone")
    await settle()
    r = stored()[0]
    check("structured call carries purpose, ref and source", data == {"a": 1} and r["purpose"] == "call_analysis"
          and r["ref"] == "call:7" and r["source"] == "phone", r)

    reset()
    await llm.complete_json("x", {"type": "object"}, job="briefs")
    await settle()
    check("no purpose given → the job name, so nothing is untagged", stored()[0]["purpose"] == "briefs")
    MOCK["reply"] = "hello there"

    reset()
    await llm.transcribe(b"\x00" * 100, "audio/mp3", source="telegram")
    await settle()
    r = stored()[0]
    check("audio sent to a chat model is a transcription", r["kind"] == "transcribe"
          and r["purpose"] == "voice_transcript" and r["source"] == "telegram", r)

    reset()
    with usage.scope(source="whatsapp", ref="whatsapp:abc"):
        with usage.scope(purpose="voice_transcript"):
            await llm.complete([{"role": "user", "content": "hi"}], purpose="")
    await settle()
    r = stored()[0]
    check("scope fills what the call leaves out, nested scopes merge",
          r["source"] == "whatsapp" and r["ref"] == "whatsapp:abc" and r["purpose"] == "chat", r)

    reset()

    async def in_run():
        await llm.complete([{"role": "user", "content": "hi"}], purpose="chat")

    run = runs.Run(OWNER, "main", "message")
    tok = runs._current_run.set(run)
    try:
        await in_run()
    finally:
        runs._current_run.reset(tok)
    await settle()
    r = stored()[0]
    check("inside an agent run: source app, ref is the session", r["source"] == "app" and r["ref"] == "session:main", r)

    reset()
    MOCK["fail"] = Exception("boom")
    try:
        await llm.complete([{"role": "user", "content": "hi"}], purpose="people_brief")
    except Exception:
        pass
    MOCK["fail"] = None
    await settle()
    rows = stored()
    check("a failed call is recorded with its error and no cost",
          len(rows) >= 1 and rows[0]["ok"] == 0 and rows[0]["cost"] is None and "boom" in rows[0]["error"], rows)

    reset()
    await litellm.acompletion(model="openai/some-model-nobody-priced", messages=[{"role": "user", "content": "hi"}],
                              litellm_metadata=usage.meta("chat"))
    await settle()
    r = stored()[0]
    check("a model without a price is unpriced (NULL), not free", r["ok"] == 1 and r["cost"] is None, r)


async def test_streaming_agent_call():
    reset()
    stream = await litellm.acompletion(model="gemini/gemini-flash-latest", mock_response="one two three",
                                       messages=[{"role": "user", "content": "hi"}], stream=True,
                                       litellm_metadata=usage.meta("chat"))
    async for _ in stream:
        pass
    await settle()
    rows = stored()
    check("a streamed call is recorded once with tokens and cost",
          len(rows) == 1 and rows[0]["output_tokens"] > 0 and rows[0]["cost"], rows)

    src = open(os.path.join(str(testenv.SERVER), "core", "agent.py")).read()
    check("the agent loop tags its stream as chat", '"litellm_metadata": usage.meta("chat")' in src)


async def test_decision_models():
    reset()
    answers = {q: {"noul": 0.5} for q in triage.questions("Robin")}

    def handler(request):
        return httpx.Response(200, json={"answers": answers})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await triage.ask({"x": 1}, me="Robin", api_key="k", client=client, ref="notification:5")
    rows = stored()
    r = rows[0]
    check("a TypeSafe request is recorded as a decision",
          len(rows) == 1 and r["kind"] == "decision" and r["purpose"] == "notification_triage"
          and r["ref"] == "notification:5" and r["provider"] == "typesafe" and r["ok"] == 1, r)
    want_tokens = usage.estimate_tokens({"state": {"x": 1}, "questions": triage.questions("Robin")})
    check("input tokens are estimated from the request (TypeSafe reports none)",
          r["input_tokens"] == want_tokens and want_tokens > 0 and r["output_tokens"] == 0, r)
    check("priced at Jev's published $0.042 per million input tokens",
          abs(r["cost"] - want_tokens * 0.042 / 1e6) < 1e-12, r)

    reset()
    os.environ["TYPESAFE_COST_PER_MTOK"] = "1.0"
    await triage.ask({"x": 1}, me="Robin", api_key="k", client=client, ref="notification:6")
    r = stored()[0]
    check("TYPESAFE_COST_PER_MTOK overrides the price", abs(r["cost"] - r["input_tokens"] / 1e6) < 1e-12, r)
    os.environ["TYPESAFE_COST_PER_MTOK"] = "oops"
    check("a bad price value is unpriced, not a crash", usage.decision_cost(100) is None)
    os.environ.pop("TYPESAFE_COST_PER_MTOK")
    check("Hebrew counts more tokens per character than English",
          usage.estimate_tokens("ש" * 300) > usage.estimate_tokens("a" * 300) * 2)

    reset()
    bad = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no")))
    try:
        await triage.ask({"x": 1}, me="Robin", api_key="k", client=bad, ref="notification:7")
    except triage.TriageError:
        pass
    r = stored()[0]
    check("a failed request is recorded as failed, no cost", r["ok"] == 0 and r["cost"] is None and "401" in r["error"], r)

    reset()
    os.environ.pop("TYPESAFE_API_KEY", None)
    try:
        await triage.ask({"x": 1}, me="Robin", client=client)
    except triage.TriageError:
        pass
    check("no key: nothing was sent, nothing is recorded", stored() == [])


def test_realtime():
    info = litellm.model_cost["gpt-realtime"]
    usage_obj = {"input_tokens": 1000, "output_tokens": 500,
                 "input_token_details": {"text_tokens": 600, "audio_tokens": 400, "cached_tokens": 200,
                                         "cached_tokens_details": {"text_tokens": 200, "audio_tokens": 0}},
                 "output_token_details": {"text_tokens": 100, "audio_tokens": 400}}
    want = (400 * info["input_cost_per_token"] + 200 * info["cache_read_input_token_cost"]
            + 400 * info["input_cost_per_audio_token"] + 100 * info["output_cost_per_token"]
            + 400 * info["output_cost_per_audio_token"])
    got = usage.realtime_cost("gpt-realtime", usage_obj)
    check("realtime cost: text, audio, cached input and audio output at their own rates",
          got is not None and abs(got - want) < 1e-12, (got, want))
    check("realtime cost: unknown model is unpriced", usage.realtime_cost("not-a-model", usage_obj) is None)
    plain = usage.realtime_cost("gpt-realtime", {"input_tokens": 10, "output_tokens": 10})
    check("realtime cost without token details counts text",
          abs(plain - (10 * info["input_cost_per_token"] + 10 * info["output_cost_per_token"])) < 1e-12, plain)

    reset()
    usage.record_realtime("gpt-realtime", usage_obj, session_id="abc")
    usage.record_realtime("gpt-realtime", {}, session_id="abc")
    usage.record_realtime("gpt-realtime", {"type": "duration", "seconds": 30.0}, session_id="abc", transcription=True)
    rows = stored()
    check("a response and a transcription are recorded, an empty usage is not", len(rows) == 2, rows)
    check("realtime rows: kind, ref, cached tokens", rows[0]["kind"] == "realtime" and rows[0]["ref"] == "session:abc"
          and rows[0]["cached_tokens"] == 200 and rows[0]["source"] == "app", rows[0])
    check("whisper transcription priced per second", rows[1]["model"] == "whisper-1"
          and abs(rows[1]["cost"] - 30 * litellm.model_cost["whisper-1"]["input_cost_per_second"]) < 1e-12, rows[1])


def test_buffer():
    reset()
    with get_db_connection() as conn:
        before = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
    for i in range(50):
        usage.record(kind="llm", purpose="chat", model="m", cost=0.001)
    with get_db_connection() as conn:
        mid = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
    check("record() only queues: nothing is written until the flush", before == mid == 0 and len(usage._buffer) == 50)
    check("the flush writes everything in one go", usage.flush_sync() == 50 and len(stored()) == 50)

    usage.record(kind="llm", purpose="chat", model="m", cost=0.001)
    real = usage._write
    usage._write = lambda rows: (_ for _ in ()).throw(RuntimeError("db locked"))
    try:
        usage.flush_sync()
        failed = False
    except RuntimeError:
        failed = True
    usage._write = real
    check("a failed write keeps the rows for the next flush", failed and len(usage._buffer) == 1)
    check("…which then writes them", usage.flush_sync() == 1)

    usage._buffer.clear()
    for i in range(usage.BUFFER_MAX + 10):
        usage._buffer.append({"i": i})
    check("the buffer is capped (oldest dropped)", len(usage._buffer) == usage.BUFFER_MAX)
    usage._buffer.clear()

    try:
        usage.record(kind="llm", purpose="chat", model="m", cost="not a number")
        ok = True
    except Exception:
        ok = False
    check("record() never raises", ok)
    usage._buffer.clear()

    usage._from_litellm({"standard_logging_object": None, "litellm_params": None}, object(), None, None, True)
    check("an odd LiteLLM payload never raises", True)
    usage._buffer.clear()


def put(day, kind="llm", purpose="chat", source="app", model="gemini-x", cost=0.01, ok=1, ref="", tin=100, tout=50,
        user_id=None):
    ts = datetime.datetime.fromisoformat(day + "T12:00:00").replace(tzinfo=clock.get_timezone()).timestamp()
    usage._buffer.append({"ts": ts, "day": day, "user_id": OWNER.id if user_id is None else user_id, "kind": kind, "purpose": purpose, "source": source, "ref": ref,
                          "provider": "p", "model": model, "input_tokens": tin, "output_tokens": tout,
                          "cached_tokens": 10, "reasoning_tokens": 0, "units": None, "cost": cost, "ms": 200,
                          "ok": ok, "error": "" if ok else "x"})


def day(offset):
    return (clock.now().date() - datetime.timedelta(days=offset)).isoformat()


def test_summary_and_rollup():
    reset()
    put(day(0), cost=0.10)
    put(day(0), kind="decision", purpose="notification_triage", source="phone", model="jev", cost=None)
    put(day(1), purpose="call_analysis", source="phone", cost=0.30, ref="call:1")
    put(day(1), cost=None, ok=0)
    put(day(3), kind="transcribe", purpose="call_transcript", source="phone", cost=0.05)
    put(day(10), cost=0.20)
    put(day(40), cost=1.00)
    usage.flush_sync()

    s = usage.summary("7d")
    check("totals over a range", s["totals"]["calls"] == 5 and abs(s["totals"]["cost"] - 0.45) < 1e-9, s["totals"])
    check("failures and unpriced are counted, not costed", s["totals"]["failures"] == 1 and s["totals"]["unpriced"] == 1,
          s["totals"])
    check("every day of the range is present, zero-filled", len(s["by_day"]) == 7 and s["by_day"][-1]["day"] == day(0)
          and s["by_day"][0]["day"] == day(6) and s["by_day"][2]["cost"] == 0, s["by_day"])
    check("previous period covers the 7 days before", s["previous"]["calls"] == 1
          and abs(s["previous"]["cost"] - 0.20) < 1e-9, s["previous"])
    purposes = {i["key"]: i for i in s["breakdown"]["purpose"]}
    check("breakdown by purpose has labels, sorted by cost",
          purposes["call_analysis"]["label"] == "Call analysis" and s["breakdown"]["purpose"][0]["key"] == "call_analysis",
          s["breakdown"]["purpose"])
    check("breakdown by kind and source", {i["key"] for i in s["breakdown"]["kind"]} == {"llm", "decision", "transcribe"}
          and {i["key"] for i in s["breakdown"]["source"]} == {"app", "phone"})

    f = usage.summary("30d", {"source": "phone"})
    check("a filter narrows totals and breakdowns", f["totals"]["calls"] == 3
          and {i["key"] for i in f["breakdown"]["source"]} == {"phone"}, f["totals"])
    check("unknown filter keys are ignored", usage.summary("7d", {"bogus": "x"})["totals"]["calls"] == 5)
    check("today is just today", usage.summary("today")["totals"]["calls"] == 2)
    try:
        usage.summary("forever")
        bad = False
    except ValueError:
        bad = True
    check("a bad range is refused", bad)

    before = {r: usage.summary(r)["totals"] for r in ("30d", "1y", "all")}
    before_breakdown = usage.summary("all")["breakdown"]["purpose"]
    folded = usage.rollup_sync(raw_days=30)
    check("roll-up folds only rows older than the window", folded == 1)
    with get_db_connection() as conn:
        raw = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
        daily = conn.execute("SELECT COUNT(*), SUM(calls) FROM usage_daily").fetchone()
    check("raw rows are gone, folded into daily", raw == 6 and daily[0] == 1 and daily[1] == 1, (raw, tuple(daily)))
    after = {r: usage.summary(r)["totals"] for r in ("30d", "1y", "all")}
    check("totals are identical before and after the roll-up", before == after, (before, after))
    check("breakdowns too", before_breakdown == usage.summary("all")["breakdown"]["purpose"])
    check("roll-up is idempotent", usage.rollup_sync(raw_days=30) == 0 and
          usage.summary("all")["totals"] == before["all"])

    put(day(40), cost=0.50)
    usage.flush_sync()
    usage.rollup_sync(raw_days=30)
    with get_db_connection() as conn:
        row = conn.execute("SELECT calls, cost FROM usage_daily WHERE day = ?", (day(40),)).fetchone()
    check("a late row for a folded day adds to it", row[0] == 2 and abs(row[1] - 1.5) < 1e-9, tuple(row))
    check("'all' starts at the oldest day", usage.summary("all")["from"] == day(40))

    reset()
    s = usage.summary("30d")
    check("an empty database gives zeros, not an error", s["totals"]["calls"] == 0 and s["totals"]["cost"] == 0
          and s["totals"]["avg_ms"] == 0 and len(s["by_day"]) == 30)


def test_events():
    reset()
    for i, c in enumerate([0.01, 0.50, None, 0.20, 0.05]):
        put(day(0), cost=c, ref=f"call:{i}", purpose="call_analysis" if i % 2 else "chat")
    put(day(0), cost=None, ok=0, ref="call:9")
    put(day(20), cost=9.0)
    usage.flush_sync()

    ev = usage.events(limit=3)
    ids = [e["id"] for e in ev["events"]]
    check("recent first, with a cursor", ids == sorted(ids, reverse=True) and ev["next"] == ids[-1] and len(ids) == 3)
    page2 = usage.events(limit=3, before=ev["next"])
    check("the cursor pages without overlap", not set(ids) & {e["id"] for e in page2["events"]}
          and len(page2["events"]) == 3 or len(page2["events"]) < 3)
    check("the last page has no cursor", usage.events(limit=50, rng="7d")["next"] is None)
    top = usage.events(limit=3, sort="cost", rng="all")["events"]
    check("most expensive first, unpriced last", [e["cost"] for e in top] == [9.0, 0.5, 0.2])
    check("the range limits events", all(e["cost"] != 9.0 for e in usage.events(rng="7d", limit=50)["events"]))
    check("filter by ref", [e["ref"] for e in usage.events(filters={"ref": "call:1"})["events"]] == ["call:1"])
    check("filter by failed", [e["ok"] for e in usage.events(filters={"ok": "0"})["events"]] == [False])
    check("filter by purpose", {e["purpose"] for e in usage.events(filters={"purpose": "chat"}, limit=50)["events"]} == {"chat"})
    e = usage.events(limit=1)["events"][0]
    check("events carry a readable purpose and drop internals", e["purpose_label"] and "day" not in e)
    check("limit is clamped", len(usage.events(limit=10_000)["events"]) <= 200)


async def test_user_column():
    """Rows carry the user the call was made for: the current user, or '' for none."""
    reset()
    with users.acting_as(OWNER):
        await llm.complete([{"role": "user", "content": "hi"}], purpose="chat")
    await settle()
    await llm.complete([{"role": "user", "content": "hi"}], purpose="people_brief")
    await settle()
    rows = stored()
    check("a model call made as a user is recorded for that user",
          [r["user_id"] for r in rows if r["purpose"] == "chat"] == [OWNER.id], rows)
    check("a call with no user context is recorded with no user",
          [r["user_id"] for r in rows if r["purpose"] == "people_brief"] == [""], rows)
    with users.acting_as(OWNER):
        usage.record(kind="realtime", purpose="voice", model="m", cost=0.01)
    usage.record(kind="realtime", purpose="voice", model="m", cost=0.01, user_id="")
    check("record() takes the current user unless told", [r["user_id"] for r in stored()[-2:]] == [OWNER.id, ""])

    reset()
    other = users.create("Usage Other")
    put(day(40), cost=1.0)
    put(day(40), cost=2.0, user_id=other.id)
    put(day(40), cost=4.0, user_id="")
    put(day(0), cost=0.5, user_id=other.id)
    usage.flush_sync()
    usage.rollup_sync(raw_days=30)
    with get_db_connection() as conn:
        daily = {r["user_id"]: r["cost"] for r in conn.execute("SELECT user_id, cost FROM usage_daily")}
    check("the daily roll-up keeps one row per user (no user included)",
          daily == {OWNER.id: 1.0, other.id: 2.0, "": 4.0}, daily)
    s_own = usage.summary("all", user_id=OWNER.id)["totals"]["cost"]
    s_other = usage.summary("all", user_id=other.id)["totals"]["cost"]
    s_all = usage.summary("all")["totals"]["cost"]
    check("summary for one user reads raw and rolled-up rows of that user only",
          abs(s_own - 1.0) < 1e-9 and abs(s_other - 2.5) < 1e-9 and abs(s_all - 7.5) < 1e-9, (s_own, s_other, s_all))
    ev = usage.events(rng="all", limit=50, user_id=other.id)["events"]
    check("events for one user, without a user_id field in the payload",
          len(ev) == 1 and ev[0]["cost"] == 0.5 and "user_id" not in ev[0], ev)
    reset()


async def test_api():
    reset()
    put(day(0), cost=0.25, ref="session:main")
    put(day(2), cost=0.10, purpose="call_analysis", source="phone")
    app = web.Application()
    setup_auth(app)  # no API_TOKEN: every request is the first admin
    usage_api.setup(app)
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/api/usage?range=7d")
        data = await r.json()
        check("GET /api/usage", r.status == 200 and data["totals"]["calls"] == 2
              and abs(data["totals"]["cost"] - 0.35) < 1e-9, data.get("totals"))
        check("the buffer is flushed before reading", not usage._buffer)
        r = await client.get("/api/usage?range=30d&source=phone")
        check("filters pass through", (await r.json())["totals"]["calls"] == 1)
        r = await client.get("/api/usage?range=weird")
        check("bad range → 400", r.status == 400)
        r = await client.get("/api/usage/events?sort=cost&limit=1")
        body = await r.json()
        check("GET /api/usage/events", r.status == 200 and len(body["events"]) == 1
              and body["events"][0]["cost"] == 0.25, body)
        r = await client.get("/api/usage/events?ref=session:main")
        check("events filter by ref", len((await r.json())["events"]) == 1)
        for bad in ("sort=x", "limit=abc", "ok=2", "before=x"):
            r = await client.get(f"/api/usage/events?{bad}")
            check(f"events: bad {bad.split('=')[0]} → 400", r.status == 400)


async def test_loop_and_cost_of_tracking():
    reset()
    usage.FLUSH_SECONDS = 0.05
    task = asyncio.create_task(usage.run_loop())
    usage.record(kind="llm", purpose="chat", model="m", cost=0.01)
    await asyncio.sleep(0.5)
    with get_db_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
    check("the background loop writes the buffer", n == 1 and not usage._buffer)
    usage.record(kind="llm", purpose="chat", model="m", cost=0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    with get_db_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
    check("cancelling it (shutdown) flushes what is left", n == 2)

    started = time.perf_counter()
    for _ in range(2000):
        usage.record(kind="llm", purpose="chat", model="m", cost=0.01, ref="session:main")
    per_call_us = (time.perf_counter() - started) / 2000 * 1e6
    check(f"queueing a record costs microseconds ({per_call_us:.0f} µs)", per_call_us < 200, per_call_us)
    usage._buffer.clear()

    reset()
    for i in range(30_000):
        put(day(i % 55), purpose=f"p{i % 7}", source=("app", "phone", "background")[i % 3], model=f"m{i % 4}")
    t = time.perf_counter()
    usage.flush_sync()
    write_s = time.perf_counter() - t
    t = time.perf_counter()
    usage.summary("90d")
    usage.events(limit=50)
    read_s = time.perf_counter() - t
    check(f"30k rows: one batch write ({write_s:.2f}s) and the whole screen's reads ({read_s * 1000:.0f} ms) stay fast",
          write_s < 5 and read_s < 1.0, (write_s, read_s))
    with get_db_connection() as conn:
        size = conn.execute("SELECT SUM(LENGTH(purpose) + LENGTH(source) + LENGTH(ref) + LENGTH(model) + 90) "
                            "FROM usage_events").fetchone()[0]
    print(f"      (≈{size / 1e6:.1f} MB for 30k raw rows; folded daily rows are a few KB a day)")


async def main():
    clock.get_timezone()
    await test_capture()
    await test_streaming_agent_call()
    await test_decision_models()
    test_realtime()
    test_buffer()
    test_summary_and_rollup()
    test_events()
    await test_user_column()
    await test_api()
    await test_loop_and_cost_of_tracking()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)


asyncio.run(main())
