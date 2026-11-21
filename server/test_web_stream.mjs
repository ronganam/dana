/**
 * Contract test for the web assistant stream client.
 *
 * Feeds the *actual bytes* emitted by the Python server (captured in
 * test_fixtures/golden.sse by `WRITE_GOLDEN=test_fixtures/golden.sse python
 * test_run_stream.py`) through the real `readSseStream` / `applyAssistantEvent`
 * / `applyAssistantState` of static/app-stream.js (its run model is pure: the
 * file loads in Node as is, with no DOM), and asserts the resulting chatMessages
 * model. Also covers reattach de-duplication, retry, approval, selection,
 * cancellation and parser edge cases with synthetic events.
 *
 * Run: node test_web_stream.mjs [path-to-sse-fixture]
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const GOLDEN = process.argv[2] || path.join(here, "test_fixtures", "golden.sse");
const raw = fs.readFileSync(GOLDEN, "utf8");

const src = fs.readFileSync(path.join(here, "static/app-stream.js"), "utf8");

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
    if (cond) { pass++; console.log(`[PASS] ${name}`); }
    else { failures.push(name); console.log(`[FAIL] ${name}${detail ? " — " + detail : ""}`); }
}

// A sandbox standing in for the app's shared globals the stream client uses (chat state, rendering).
function makeSandbox() {
    const sb = {
        console, JSON, TextDecoder, AbortController, Date, Map, Set, Object, Array,
        String, Number, Math, Error, Promise, URLSearchParams,
        isRtlText: () => false,
        escapeHtml: (s) => String(s),
        chatMessages: [],
        renderCount: 0,
        patchCount: 0,
        currentSessionId: "gold",
        assistantStreamAbort: null,
        chatSendBtn: null,
        chatMessagesEl: null,
        requestAnimationFrame: (fn) => { fn(); return 0; },
        cancelAnimationFrame() {},
        setTimeout: (fn) => { fn(); return 0; },
        renderChatMessages() { sb.renderCount++; },
        patchBubble() { sb.patchCount++; return true; },
        refreshIcons() {},
        async fetch() { throw new Error("fetch should not be called in this test"); },
    };
    sb.globalThis = sb;
    vm.createContext(sb);
    vm.runInContext(src, sb, { filename: "static/app-stream.js" });
    return sb;
}

function fakeResponse(bodyText, chunkSize = 37) {
    const bytes = Buffer.from(bodyText, "utf8");
    let pos = 0;
    return {
        body: {
            getReader() {
                return {
                    async read() {
                        if (pos >= bytes.length) return { done: true, value: undefined };
                        const slice = bytes.subarray(pos, pos + chunkSize);
                        pos += slice.length;
                        return { done: false, value: new Uint8Array(slice) };
                    },
                };
            },
        },
    };
}

function feed(sb, ctx, payloads) {
    for (const p of payloads) {
        if (p.type === "state") sb.applyAssistantState(ctx, p.state);
        else sb.applyAssistantEvent(ctx, p);
    }
}

// ── Golden stream from the real server ──────────────────────────────────
const sb = makeSandbox();
const ctx = sb.newRunContext();
const payloads = [];
await sb.readSseStream(fakeResponse(raw), (p) => payloads.push(p));
feed(sb, ctx, payloads);

const seen = payloads.map((p) => p.type);
check("parser: id: lines tolerated, every frame parsed", seen.length === raw.split("\n\n").filter((b) => b.includes("data:")).length);
check("parser: stream opens with run_start and closes with run_end + state",
    seen[0] === "run_start" && seen.slice(-2).join() === "run_end,state", JSON.stringify(seen));

const msgs = sb.chatMessages;
const byRole = (r) => msgs.filter((m) => m.role === r);
const assistants = byRole("assistant");
check("model: no thinking indicator left behind", assistants.every((m) => !m.isThinking), JSON.stringify(assistants));

const reasoningBubble = assistants.find((m) => m.reasoning);
check("model: reasoning streamed into a bubble",
    reasoningBubble && reasoningBubble.reasoning === "Thinking about which lookups to run.",
    reasoningBubble && reasoningBubble.reasoning);
check("model: reasoning collapses once tools start", reasoningBubble && reasoningBubble.reasoningOpen === false);

const tools = byRole("tool");
check("model: one card per tool call (tool_call + start + end merged)", tools.length === 2, `got ${tools.length}`);
check("model: tool cards keep the tool_call_id", tools.map((t) => t.toolCallId).join() === "call_a,call_b");
check("model: tools finished with results",
    tools.every((t) => !t.isRunning && !t.isPreparing && t.response.startsWith("result for")),
    JSON.stringify(tools.map((t) => [t.isRunning, t.isPreparing, t.response])));
check("model: tool args pretty-printed", tools[0] && tools[0].parameters.includes('"query"'));

const answer = assistants[assistants.length - 1];
check("model: answer text assembled from deltas", answer && answer.content === "Both lookups done.", answer && answer.content);
check("model: answer comes after the tool cards", msgs.indexOf(answer) > msgs.indexOf(tools[1]));
check("model: final reuses the streamed bubble (no duplicate answer)",
    assistants.filter((m) => m.content === "Both lookups done.").length === 1);
check("model: bubbles stop streaming at the end", assistants.every((m) => !m.streaming));
check("ctx: tracks run id and last seq",
    ctx.runId === payloads[0].run_id && ctx.lastSeq === payloads[payloads.length - 2].seq);
{
    // Text deltas return the bubble id so the scheduler patches just that bubble.
    const s = makeSandbox();
    const c = s.newRunContext();
    for (const p of payloads) {
        if (p.type === "state") continue;
        const id = s.applyAssistantEvent(c, p);
        s.scheduleRender(typeof id === "string" ? id : null);
    }
    const deltas = payloads.filter((p) => p.type === "text_delta" || p.type === "reasoning_delta").length;
    check("render: deltas patch one bubble instead of re-rendering the list",
        s.patchCount === deltas && s.renderCount === payloads.length - 1 - deltas,
        `patches=${s.patchCount} deltas=${deltas} renders=${s.renderCount}`);
}

// ── Reattach: replayed events must not render twice ─────────────────────
{
    const s = makeSandbox();
    const c = s.newRunContext();
    const half = payloads.slice(0, 10);
    feed(s, c, half);
    // Reconnect asks for after=lastSeq but a proxy may still resend a frame.
    feed(s, c, payloads.slice(8));
    const t = s.chatMessages.filter((m) => m.role === "tool");
    const a = s.chatMessages.filter((m) => m.role === "assistant" && m.content);
    check("reattach: overlapping replay is de-duplicated by seq",
        t.length === 2 && a.length === 1 && a[0].content === "Both lookups done.",
        JSON.stringify(s.chatMessages.map((m) => [m.role, m.content || m.toolName])));
}

// ── Retry discards the step's partial text ───────────────────────────────
{
    const s = makeSandbox();
    const c = s.newRunContext();
    let seq = 0;
    const ev = (o) => Object.assign({ run_id: "r1", seq: ++seq }, o);
    feed(s, c, [
        ev({ type: "run_start", model: { label: "Fast" } }),
        ev({ type: "step_start", iteration: 1, max_iterations: 10 }),
        ev({ type: "text_delta", iteration: 1, delta: "Half an ans" }),
        ev({ type: "retry", iteration: 1, attempt: 1, message: "boom" }),
        ev({ type: "text_delta", iteration: 1, delta: "Clean answer." }),
        ev({ type: "final", content: "Clean answer." }),
        ev({ type: "run_end", status: "idle" }),
    ]);
    const a = s.chatMessages.filter((m) => m.role === "assistant");
    check("retry: partial text dropped, only the clean answer remains",
        a.length === 1 && a[0].content === "Clean answer.", JSON.stringify(a));
}

// ── Approval: the pending tool card is expanded with buttons ─────────────
{
    const s = makeSandbox();
    const c = s.newRunContext();
    let seq = 0;
    const ev = (o) => Object.assign({ run_id: "r2", seq: ++seq }, o);
    feed(s, c, [
        ev({ type: "run_start" }),
        ev({ type: "step_start", iteration: 1, max_iterations: 10 }),
        ev({ type: "tool_call", id: "w1", name: "add_task", iteration: 1 }),
        ev({ type: "tool_start", id: "w1", name: "add_task", args: { title: "x" }, iteration: 1 }),
        ev({ type: "approval_required", id: "w1", name: "add_task", args: { title: "x" } }),
        ev({ type: "run_end", status: "waiting_approval" }),
        { type: "state", state: { status: "waiting_approval", pending_approval: { tool_call_id: "w1", tool_name: "add_task", tool_args: { title: "x" } } } },
    ]);
    const t = s.chatMessages.filter((m) => m.role === "tool");
    check("approval: exactly one card, needing approval, expanded, not running",
        t.length === 1 && t[0].requiresApproval && !t[0].isCollapsed && !t[0].isRunning, JSON.stringify(t));
}

// ── Selection: the tool card is replaced by a selection card ─────────────
{
    const s = makeSandbox();
    const c = s.newRunContext();
    let seq = 0;
    const ev = (o) => Object.assign({ run_id: "r3", seq: ++seq }, o);
    feed(s, c, [
        ev({ type: "tool_call", id: "s1", name: "request_user_selection", iteration: 1 }),
        ev({ type: "tool_start", id: "s1", name: "request_user_selection", args: {}, iteration: 1 }),
        ev({ type: "selection_required", id: "s1", prompt: "Which?", options: [{ label: "A", value: "a" }], multiple: false }),
        { type: "state", state: { status: "awaiting_selection", selection_request: { prompt: "Which?", options: [] } } },
    ]);
    check("selection: one selection card, no tool card",
        s.chatMessages.filter((m) => m.role === "selection").length === 1 &&
        s.chatMessages.filter((m) => m.role === "tool").length === 0,
        JSON.stringify(s.chatMessages.map((m) => m.role)));
}

// ── Iteration limit: event + state yield exactly one card ────────────────
{
    const s = makeSandbox();
    const c = s.newRunContext();
    s.applyAssistantEvent(c, { type: "iterations_exceeded", summary: "Reached 10 steps" });
    s.applyAssistantState(c, { status: "iterations_exceeded", extend_summary: "Reached 10 steps" });
    const cards = s.chatMessages.filter((m) => m.role === "iteration");
    check("iteration: exactly one card carrying the summary",
        cards.length === 1 && cards[0].summary === "Reached 10 steps", JSON.stringify(cards));
}

// ── Cancellation leaves a note and no spinner ────────────────────────────
{
    const s = makeSandbox();
    const c = s.newRunContext();
    s.startThinking(c, "Thinking…");
    feed(s, c, [
        { type: "run_start", run_id: "r4", seq: 1 },
        { type: "text_delta", run_id: "r4", seq: 2, iteration: 1, delta: "Partial" },
        { type: "cancelled", run_id: "r4", seq: 3 },
        { type: "run_end", run_id: "r4", seq: 4, status: "cancelled" },
    ]);
    check("cancel: 'Stopped.' note, nothing thinking or streaming",
        s.chatMessages.some((m) => m.isNote && m.content === "Stopped.") &&
        s.chatMessages.every((m) => !m.isThinking && !m.streaming),
        JSON.stringify(s.chatMessages));
}

// ── Parser edge cases ────────────────────────────────────────────────────
{
    const s = makeSandbox();
    const hb = "id: 1\ndata: {\"type\":\"step_start\",\"iteration\":1}\n\n: heartbeat\n\n: heartbeat\n\nid: 2\ndata: {\"type\":\"final\",\"content\":\"ok\"}\n\ndata: {\"type\":\"state\",\"state\":{\"status\":\"idle\"}}\n\n";
    const types = [];
    await s.readSseStream(fakeResponse(hb, 11), (p) => types.push(p.type));
    check("heartbeat: comment frames skipped, data frames kept",
        JSON.stringify(types) === JSON.stringify(["step_start", "final", "state"]), JSON.stringify(types));

    const hebrew = "id: 9\ndata: {\"type\":\"final\",\"content\":\"שלום עולם\"}\n\n";
    let got = null;
    await s.readSseStream(fakeResponse(hebrew, 5), (p) => { got = p.content; });
    check("utf8: multi-byte text survives being split across network chunks", got === "שלום עולם", String(got));
}

console.log("\n" + "=".repeat(60));
console.log(`${pass}/${pass + failures.length} checks passed`);
if (failures.length) {
    console.log("FAILED:");
    failures.forEach((f) => console.log("  - " + f));
    process.exit(1);
}
console.log("Web streaming client checks passed.");
