"""End-to-end check of live assistant streaming.

Drives the real aiohttp handlers, the real session runner and the real ReAct
loop. Only the LLM is faked: ``litellm.acompletion`` returns a scripted stream
of genuine LiteLLM chunk objects, so ``stream_chunk_builder`` and the agent's
delta handling run for real.

Covers: event order and seq numbering, reasoning/text deltas, tool_call
announcements, parallel read-only tools, model selection (and that resumes keep
the chosen model), reattach after a dropped connection, busy sessions (409),
approval of a confirm tool, a changing tool that runs at once with its undo id
on tool_end and in the closing state (and the Undo endpoint reversing it),
mid-stream retry, cancellation, the voice WebSocket, and the model catalog
endpoint.

Run: bin/test test_run_stream
"""

import asyncio
import json
import os
import sys
import time

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("run_stream_test")
os.environ["DANA_INSECURE_DEV"] = "1"  # no API_TOKEN: local development, a request without credentials is the first admin

import litellm
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from litellm.types.utils import (ChatCompletionDeltaToolCall, Delta, Function,
                                 ModelResponseStream, StreamingChoices)

from core import db, session_runner, users
db.migrate()
users.bootstrap()
from core import assistant_api, sessions_api
from core.agent import AgentOrchestrator
from core.auth import setup_auth
from core.runs import runs as _runs
from core.session import get_history
from addons.essentials.addon import addon as essentials_addon  # every user has it: the test tools go there

OWNER = users.first_admin()  # no API_TOKEN: a request without credentials is the first admin


class _OwnerRuns:
    """The first admin's view of the run manager (runs are per user)."""

    def is_running(self, session_id):
        return _runs.is_running(OWNER.id, session_id)


runs = _OwnerRuns()

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


# --- Test tools ---------------------------------------------------------------

TOOL_DELAY = 0.3
TOOL_SPANS: list = []   # (start, end) of each zz_slow_lookup call, for the parallelism check


@essentials_addon.tool(name="zz_slow_lookup")
async def zz_slow_lookup(conn, query: str) -> str:
    """Look something up slowly.

    Args:
        query: What to look up.
    """
    start = time.monotonic()
    await asyncio.sleep(TOOL_DELAY)
    TOOL_SPANS.append((start, time.monotonic()))
    return f"result for {query}"


@essentials_addon.tool(name="zz_write_thing", is_write=True, confirm=True)
async def zz_write_thing(conn, value: str) -> str:
    """Send something to someone (asks first).

    Args:
        value: The value to write.
    """
    return f"wrote {value}"


CHANGED = {"value": None}


@essentials_addon.tool(name="zz_change_thing", is_write=True)
async def zz_change_thing(conn, value: str) -> str:
    """Change something reversibly (runs at once, registers an undo).

    Args:
        value: The new value.
    """
    from core import undo
    previous = CHANGED["value"]
    CHANGED["value"] = value
    undo.record(f"Set the thing to {value}", lambda: CHANGED.update(value=previous), source="zz_change_thing")
    return f"set {value}"


# --- Fake LLM -----------------------------------------------------------------

def text_chunk(text=None, reasoning=None, finish=None):
    return ModelResponseStream(model="fake", choices=[StreamingChoices(
        index=0, delta=Delta(content=text, reasoning_content=reasoning), finish_reason=finish)])


def tool_chunk(index, call_id, name, args, finish=None):
    return ModelResponseStream(model="fake", choices=[StreamingChoices(
        index=0,
        delta=Delta(content=None, tool_calls=[ChatCompletionDeltaToolCall(
            id=call_id, index=index, type="function", function=Function(name=name, arguments=json.dumps(args)))]),
        finish_reason=finish)])


class FakeLLM:
    """Each acompletion() call pops the next script: a list of chunks, where a
    float means "sleep this long" and an Exception instance means "raise"."""

    def __init__(self):
        self.scripts = []
        self.calls = []

    def load(self, *scripts):
        self.scripts = list(scripts)
        self.calls = []

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs.get("stream") is True, "agent must stream"
        script = self.scripts.pop(0)

        async def gen():
            for item in script:
                if isinstance(item, (int, float)):
                    await asyncio.sleep(item)
                elif isinstance(item, Exception):
                    raise item
                else:
                    yield item
        return gen()


fake = FakeLLM()
litellm.acompletion = fake.acompletion


def answer_script(text_parts, gap=0.0):
    out = []
    for i, part in enumerate(text_parts):
        if gap:
            out.append(gap)
        out.append(text_chunk(part, finish="stop" if i == len(text_parts) - 1 else None))
    return out


TOOLS_THEN_ANSWER = [
    [
        text_chunk(reasoning="Thinking about "),
        text_chunk(reasoning="which lookups to run."),
        # Claude's signed thinking block: sent back within the turn, never stored.
        ModelResponseStream(model="fake", choices=[StreamingChoices(index=0, delta=Delta(
            content=None, thinking_blocks=[{"type": "thinking", "thinking": "plan", "signature": "sig-turn-1"}]))]),
        tool_chunk(0, "call_a", "zz_slow_lookup", {"query": "a"}),
        tool_chunk(1, "call_b", "zz_slow_lookup", {"query": "b"}, finish="tool_calls"),
    ],
    answer_script(["Both ", "lookups ", "done."]),
]


# --- Harness ------------------------------------------------------------------

class _OwnerHistories:
    def __getitem__(self, session_id):
        return get_history(OWNER.id, session_id)


class StubBot:
    """The core's agent for these runs, and the first admin's stored histories."""

    def __init__(self):
        session_runner.set_agent(AgentOrchestrator(model="gemini/server-default"))
        self.user_histories = _OwnerHistories()


def build_app(bot):
    app = web.Application()
    setup_auth(app)
    app.router.add_get("/api/assistant/models", assistant_api.assistant_models_api)
    app.router.add_post("/api/assistant/message", assistant_api.assistant_message_api)
    app.router.add_post("/api/assistant/approve", assistant_api.assistant_approve_api)
    app.router.add_get("/api/assistant/voice_stream", assistant_api.assistant_voice_stream_api)
    app.router.add_get("/api/sessions/{session_id}", sessions_api.get_session_detail_api)
    app.router.add_get("/api/sessions/{session_id}/stream", sessions_api.session_stream_api)
    from core import actions_api
    actions_api.setup(app)  # POST /api/undo/{id}
    return app


async def read_sse(response, limit=None, max_seconds=20.0, raw_sink=None):
    """Collect SSE payloads; stop after `limit` payloads or at end of stream."""
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
        if raw_sink is not None:
            raw_sink.append(chunk)
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            data = [ln[5:].strip() for ln in block.splitlines() if ln.startswith("data:")]
            if data:
                frames.append(json.loads("".join(data)))
                if limit and len(frames) >= limit:
                    return frames
    return frames


def types(frames):
    return [f["type"] for f in frames]


def seqs_ok(frames):
    seqs = [f["seq"] for f in frames if "seq" in f]
    return seqs == list(range(seqs[0], seqs[0] + len(seqs))) if seqs else False


async def wait_idle(session_id, timeout=5.0):
    end = time.monotonic() + timeout
    while runs.is_running(session_id) and time.monotonic() < end:
        await asyncio.sleep(0.02)


# --- Tests --------------------------------------------------------------------

async def test_models(client):
    res = await client.get("/api/assistant/models")
    body = await res.json()
    ids = [m["id"] for m in body["models"]]
    check("models: catalog lists fast/balanced/deep", ids == ["fast", "balanced", "deep"], str(ids))
    check("models: fast is the default", body["default"] == "fast")


async def test_full_stream(client):
    fake.load(*[list(s) for s in TOOLS_THEN_ANSWER])
    TOOL_SPANS.clear()
    started = time.monotonic()
    res = await client.post("/api/assistant/message",
                            json={"session_id": "s_full", "message": "look up a and b", "model": "balanced"})
    raw = []
    frames = await read_sse(res, raw_sink=raw)
    t = types(frames)
    if os.environ.get("WRITE_GOLDEN"):
        # Fixture for test_web_stream.mjs: real bytes from a real run.
        with open(os.environ["WRITE_GOLDEN"], "wb") as f:
            f.write(b"".join(raw))

    check("stream: starts with run_start", t[0] == "run_start", str(t[:3]))
    check("stream: run_start names the chosen model", frames[0]["model"]["id"] == "balanced")
    check("stream: LLM called with the balanced profile",
          fake.calls[0]["model"] == "anthropic/claude-sonnet-5-5" and fake.calls[0].get("reasoning_effort") == "high",
          str({k: fake.calls[0].get(k) for k in ("model", "reasoning_effort")}))
    check("stream: reasoning streamed as deltas", t.count("reasoning_delta") == 2)
    check("stream: tool_call announced before tool_start",
          t.index("tool_call") < t.index("tool_start"))
    check("stream: both tools announced", t.count("tool_call") == 2 and t.count("tool_start") == 2)
    # The two calls overlap in time when they ran side by side (whatever the
    # request's own overhead on a slow machine).
    overlapped = len(TOOL_SPANS) == 2 and max(s for s, _ in TOOL_SPANS) < min(e for _, e in TOOL_SPANS)
    check("stream: read-only tools ran in parallel", overlapped,
          f"tool spans {[(round(s - started, 2), round(e - started, 2)) for s, e in TOOL_SPANS]}")
    check("stream: answer text streamed in pieces",
          "".join(f["delta"] for f in frames if f["type"] == "text_delta") == "Both lookups done.")
    check("stream: final carries the full reply", any(f["type"] == "final" and f["content"] == "Both lookups done." for f in frames))
    check("stream: run_end then state close the stream", t[-2:] == ["run_end", "state"], str(t[-3:]))
    check("stream: seq numbers are contiguous", seqs_ok(frames))
    state = frames[-1]["state"]
    check("stream: state is idle with no active run", state["status"] == "idle" and state["active_run"] is None)
    msgs = state["messages"]
    check("stream: history has the tool cards and the answer",
          sum(1 for m in msgs if m["toolCall"]) == 2 and msgs[-1]["message"] == "Both lookups done.")
    check("stream: reasoning not stored in history",
          "reasoning_content" not in json.dumps(StubBot().user_histories["s_full"]))
    sent_back = [m for m in fake.calls[1]["messages"] if m.get("role") == "assistant" and m.get("tool_calls")]
    check("stream: the thinking block goes back to the model within the turn",
          bool(sent_back) and "sig-turn-1" in json.dumps(sent_back[-1].get("thinking_blocks")), str(sent_back)[:300])
    check("stream: thinking blocks not stored in history (Claude binds them to this turn's prompt)",
          "sig-turn-1" not in json.dumps(StubBot().user_histories["s_full"]))
    check("stream: read-only tool_end carries an empty undos list",
          all(f.get("undos") == [] for f in frames if f["type"] == "tool_end"))
    check("stream: state carries undos [] and shortcut false",
          state.get("undos") == [] and state.get("shortcut") is False, str({k: state.get(k) for k in ("undos", "shortcut")}))


async def test_reattach(client):
    fake.load([tool_chunk(0, "call_r", "zz_slow_lookup", {"query": "r"}, finish="tool_calls")],
              answer_script(["one ", "two ", "three"], gap=0.15))
    res = await client.post("/api/assistant/message", json={"session_id": "s_re", "message": "go"})
    first = await read_sse(res, limit=3)
    run_id, last = first[0]["run_id"], first[-1]["seq"]
    res.close()  # client drops mid-run
    check("reattach: the run survives the disconnect", runs.is_running("s_re"))

    detail = await (await client.get("/api/sessions/s_re")).json()
    check("reattach: session detail advertises the active run",
          detail["status"] == "running" and detail["active_run"]["run_id"] == run_id)

    res2 = await client.get(f"/api/sessions/s_re/stream?run_id={run_id}&after={last}")
    rest = await read_sse(res2)
    check("reattach: resumes right after the last seen seq", rest[0].get("seq") == last + 1, str(rest[:1]))
    joined = first + rest
    check("reattach: no gaps or duplicates across both connections", seqs_ok(joined))
    check("reattach: ends with run_end + state", types(rest)[-2:] == ["run_end", "state"])

    res3 = await client.get(f"/api/sessions/s_re/stream?run_id={run_id}&after={rest[-2]['seq']}")
    tail = await read_sse(res3)
    check("reattach: a finished run replays only the state frame", types(tail) == ["state"], str(types(tail)))

    res4 = await client.get("/api/sessions/never_ran/stream")
    check("reattach: no run → just the state frame", types(await read_sse(res4)) == ["state"])


async def test_busy(client):
    fake.load(answer_script(["slow ", "answer"], gap=0.4))
    res = await client.post("/api/assistant/message", json={"session_id": "s_busy", "message": "one"})
    await read_sse(res, limit=1)
    res2 = await client.post("/api/assistant/message", json={"session_id": "s_busy", "message": "two"})
    body = await res2.json()
    check("busy: second message while running → 409", res2.status == 409 and body["code"] == "session_busy")
    history = StubBot().user_histories["s_busy"]
    check("busy: rejected message is not added to history",
          [m["content"] for m in history if m["role"] == "user"] == ["one"])
    await read_sse(res)
    await wait_idle("s_busy")


async def test_approval_keeps_model(client):
    fake.load([tool_chunk(0, "call_w", "zz_write_thing", {"value": "x"}, finish="tool_calls")],
              answer_script(["Written."]))
    res = await client.post("/api/assistant/message",
                            json={"session_id": "s_appr", "message": "write x", "model": "deep"})
    frames = await read_sse(res)
    t = types(frames)
    check("approval: suspends with approval_required", "approval_required" in t and "final" not in t)
    run_end = next(f for f in frames if f["type"] == "run_end")
    check("approval: run_end reports waiting_approval", run_end["status"] == "waiting_approval", run_end["status"])

    res2 = await client.post("/api/assistant/approve", json={"session_id": "s_appr", "action": "approve"})
    frames2 = await read_sse(res2)
    t2 = types(frames2)
    check("approval: resume is a new run", frames2[0]["run_id"] != frames[0]["run_id"])
    check("approval: approved tool_end then final",
          "tool_end" in t2 and t2.index("tool_end") < t2.index("final"))
    check("approval: resumed LLM call keeps the deep profile",
          fake.calls[-1]["model"] == "anthropic/claude-opus-5-5", fake.calls[-1]["model"])


async def test_change_runs_at_once_with_undo(client):
    CHANGED["value"] = "old"
    fake.load([tool_chunk(0, "call_c", "zz_change_thing", {"value": "new"}, finish="tool_calls")],
              answer_script(["Set."]))
    res = await client.post("/api/assistant/message", json={"session_id": "s_change", "message": "set it"})
    frames = await read_sse(res)
    t = types(frames)
    check("undo: a changing tool runs without approval", "approval_required" not in t and "final" in t, str(t))
    end = next(f for f in frames if f["type"] == "tool_end")
    check("undo: tool_end carries the undo id and label",
          len(end.get("undos") or []) == 1 and end["undos"][0]["label"] == "Set the thing to new", str(end.get("undos")))
    state = frames[-1]["state"]
    check("undo: closing state lists the run's undo", state.get("undos") == end["undos"], str(state.get("undos")))
    check("undo: the change happened", CHANGED["value"] == "new")
    uid = end["undos"][0]["id"]
    r = await client.post(f"/api/undo/{uid}")
    check("undo: POST /api/undo/{id} reverses it", r.status == 200 and CHANGED["value"] == "old")
    detail = await (await client.get("/api/sessions/s_change")).json()
    check("undo: a used undo is no longer offered in the state", detail.get("undos") == [], str(detail.get("undos")))
    r2 = await client.post(f"/api/undo/{uid}")
    check("undo: a second press is 404", r2.status == 404)


async def test_retry(client):
    fake.load([text_chunk("Partial "), RuntimeError("stream broke")], answer_script(["Recovered."]))
    res = await client.post("/api/assistant/message", json={"session_id": "s_retry", "message": "go"})
    frames = await read_sse(res)
    t = types(frames)
    check("retry: a broken stream emits retry and recovers",
          "retry" in t and t.index("retry") < t.index("final"))
    final = next(f for f in frames if f["type"] == "final")
    check("retry: final has only the recovered text", final["content"] == "Recovered.")


async def test_missing_key(client):
    """An authentication error (no key / a refused key) is not retried: one short
    message saying which variable to set, as the error and the stored answer."""
    from core import llm
    error = litellm.AuthenticationError(message="x-api-key header is required", llm_provider="anthropic",
                                        model="claude-sonnet-5-5")
    fake.load([error], answer_script(["never used"]))
    res = await client.post("/api/assistant/message",
                            json={"session_id": "s_nokey", "message": "hi", "model": "balanced"})
    frames = await read_sse(res)
    t = types(frames)
    errors = [f for f in frames if f["type"] == "error"]
    check("missing key: no retry, one error naming the variable to set",
          "retry" not in t and len(fake.calls) == 1 and len(errors) == 1
          and errors[0]["message"] == "No API key for anthropic: add one in Settings → The model (or set ANTHROPIC_API_KEY)", str(errors or t))
    fake.scripts.clear()
    await wait_idle("s_nokey")
    check("missing key: the message is the stored answer",
          (get_history(OWNER.id, "s_nokey") or [{}])[-1].get("content") == errors[0]["message"] if errors else False)
    os.environ["ANTHROPIC_API_KEY"] = ""
    os.environ["GEMINI_API_KEY"] = "test-gemini-key"
    check("missing key: an empty ANTHROPIC_API_KEY counts as missing (startup warning)",
          llm._missing_keys("anthropic/claude-sonnet-5-5") == ["ANTHROPIC_API_KEY"]
          and llm._missing_keys("gemini/gemini-3.1-flash-lite") == [])
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
    check("missing key: with a key set, a refusal says to check it",
          llm._missing_keys("anthropic/claude-sonnet-5-5") == []
          and llm.missing_key_message("anthropic/claude-sonnet-5-5")
          == "The anthropic API key was refused: enter it again in Settings → The model (or check ANTHROPIC_API_KEY)")
    os.environ.pop("ANTHROPIC_API_KEY")
    os.environ.pop("GEMINI_API_KEY")


async def test_cancel(client):
    fake.load(answer_script(["never ", "finishes"], gap=2.0))
    res = await client.post("/api/assistant/message", json={"session_id": "s_cancel", "message": "go"})
    await read_sse(res, limit=2)
    cres = await client.post("/api/assistant/approve", json={"session_id": "s_cancel", "action": "cancel"})
    check("cancel: endpoint reports cancelled", (await cres.json())["status"] == "cancelled")
    rest = await read_sse(res)
    t = types(rest)
    check("cancel: stream gets cancelled + run_end", "cancelled" in t and t[-2:] == ["run_end", "state"], str(t))
    check("cancel: session is idle afterwards", rest[-1]["state"]["status"] == "idle")


async def test_voice_ws(client):
    async def fake_transcribe(audio, mime, **_):
        return "what's up"
    assistant_api.llm.transcribe = fake_transcribe
    fake.load(answer_script(["Not ", "much."]))

    ws = await client.ws_connect("/api/assistant/voice_stream?session_id=s_voice&model=fast")
    await ws.send_bytes(b"\x00\x01" * 1600)
    await ws.send_str(json.dumps({"event": "stop", "sample_rate": 16000}))
    frames = []
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout=10)
        frames.append(json.loads(msg.data))
        if frames[-1]["type"] == "state":
            break
    t = types(frames)
    check("voice ws: run events stream over the socket",
          t[0] == "run_start" and "transcription" in t and "text_delta" in t and "final" in t, str(t))
    check("voice ws: ends with state carrying the transcription",
          frames[-1]["state"].get("transcription") == "what's up")
    check("voice ws: uses the fast profile", fake.calls[-1]["model"] == "anthropic/claude-haiku-5-5")

    async def silent(audio, mime, **_):
        return ""
    assistant_api.llm.transcribe = silent
    await ws.send_bytes(b"\x00" * 100)
    await ws.send_str(json.dumps({"event": "stop"}))
    msg = await asyncio.wait_for(ws.receive(), timeout=5)
    check("voice ws: silence → no_speech on the same socket", json.loads(msg.data)["type"] == "no_speech")
    await ws.close()


async def main():
    client = TestClient(TestServer(build_app(StubBot())))
    await client.start_server()
    try:
        await test_models(client)
        await test_full_stream(client)
        await test_reattach(client)
        await test_busy(client)
        await test_approval_keeps_model(client)
        await test_change_runs_at_once_with_undo(client)
        await test_retry(client)
        await test_missing_key(client)
        await test_cancel(client)
        await test_voice_ws(client)
    finally:
        await client.close()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
