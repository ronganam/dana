/* The web app — the assistant stream client (static/app.js says how the app's scripts fit together).
 *
 * The run model (readSseStream … applyAssistantState) is pure: no DOM, no network. It turns one run's
 * events (core/runs.py) into the chat model `chatMessages` (app-core.js). test_web_stream.mjs loads this
 * file in Node and feeds it the server's captured stream (tests/fixtures/golden.sse). Below the model, the
 * render cadence and the transport (POST or reattach, resume after a drop, stop) call into the Chat
 * screen (app-chat.js) while a run is followed. Loading the file only declares.
 */

// ══════════════════════════════════════════════════════════════════════
// ASSISTANT STREAMING (Server-Sent Events)
// ══════════════════════════════════════════════════════════════════════
//
// Every assistant request streams the events of one server-side "run"
// (see core/runs.py). Each event carries run_id + seq, so a dropped
// connection reattaches via GET /api/sessions/{id}/stream and resumes
// exactly where it left off. The stream always closes with a `state`
// frame (no seq) holding the authoritative session state.

/**
 * Consume an SSE response body, invoking onEvent for every `data:` frame.
 * `id:` lines are ignored (seq is inside the payload) and comment lines
 * (`: heartbeat`) only keep intermediaries from timing out.
 */
async function readSseStream(response, onEvent) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    const dispatch = (block) => {
        const data = block.split("\n")
            .filter(line => line.startsWith("data:"))
            .map(line => line.slice(5).trim())
            .join("");
        if (!data || data === "[DONE]") return;
        let payload;
        try {
            payload = JSON.parse(data);
        } catch (e) {
            console.warn("Unparseable SSE frame", data);
            return;
        }
        onEvent(payload);
    };

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sep;
        while ((sep = buffer.indexOf("\n\n")) !== -1) {
            const block = buffer.slice(0, sep);
            buffer = buffer.slice(sep + 2);
            dispatch(block);
        }
    }
    if (buffer.trim()) dispatch(buffer);
}

/** Pretty-print a tool result/args blob, tolerating plain strings. */
function formatToolPayload(value) {
    if (value === undefined || value === null || value === "") return "";
    if (typeof value === "string") return value;
    try {
        return JSON.stringify(value, null, 2);
    } catch (e) {
        return String(value);
    }
}

function uid(prefix) {
    return prefix + "_" + Date.now() + "_" + Math.random().toString(36).slice(2, 7);
}

/** Per-run bookkeeping for one rendered stream. */
function newRunContext(label) {
    return {
        runId: null,
        lastSeq: 0,
        done: false,             // run_end seen
        gotState: false,         // closing state frame seen
        modelLabel: null,
        thinkingId: null,
        thinkingLabel: label || null,
        stepBubbles: new Map(),  // iteration -> chat message id
        toolCardIds: new Map(),  // tool_call_id -> chat message id
        sawSelection: false,
        sawIteration: false,
        sawApproval: false,
        transcriptionShown: false
    };
}

function endThinking(ctx) {
    if (ctx.thinkingId) {
        chatMessages = chatMessages.filter(m => m.id !== ctx.thinkingId);
        ctx.thinkingId = null;
    }
}

function startThinking(ctx, label) {
    endThinking(ctx);
    ctx.thinkingId = uid("thinking");
    const parts = [ctx.modelLabel, label || ctx.thinkingLabel].filter(Boolean);
    chatMessages.push({
        id: ctx.thinkingId,
        role: "assistant",
        isThinking: true,
        thinkingLabel: parts.join(" · ") || null
    });
}

function upsertToolCard(ctx, toolCallId, patch) {
    let cardId = ctx.toolCardIds.get(toolCallId);
    const existing = cardId && chatMessages.find(m => m.id === cardId);
    if (existing) {
        Object.assign(existing, patch);
        return existing;
    }
    cardId = "tc_" + (toolCallId || uid("x"));
    ctx.toolCardIds.set(toolCallId, cardId);
    const msg = Object.assign({
        id: cardId,
        toolCallId: toolCallId,
        role: "tool",
        toolName: "Tool Execution",
        parameters: "",
        response: "",
        isCollapsed: true,
        isPreparing: false,
        isRunning: false,
        timestamp: new Date().toISOString()
    }, patch);
    endThinking(ctx);
    chatMessages.push(msg);
    return msg;
}

function anyToolBusy(ctx) {
    for (const cardId of ctx.toolCardIds.values()) {
        const m = chatMessages.find(x => x.id === cardId);
        if (m && (m.isRunning || m.isPreparing)) return true;
    }
    return false;
}

/** The live reply bubble for one LLM step, created on its first delta. */
function stepBubble(ctx, iteration) {
    const key = iteration || 0;
    let id = ctx.stepBubbles.get(key);
    let msg = id && chatMessages.find(m => m.id === id);
    if (!msg) {
        endThinking(ctx);
        id = uid("live");
        ctx.stepBubbles.set(key, id);
        msg = {
            id,
            role: "assistant",
            content: "",
            reasoning: "",
            reasoningOpen: true,
            streaming: true,
            timestamp: new Date().toISOString()
        };
        chatMessages.push(msg);
    }
    return msg;
}

function addSelectionCard(ctx, payload) {
    const existing = chatMessages.find(m => m.role === "selection" && !m.resolved);
    if (existing) {
        existing.prompt = payload.prompt || "Please select an option:";
        existing.options = payload.options || [];
        existing.multiple = !!payload.multiple;
        return existing;
    }
    const card = {
        id: uid("sel"),
        role: "selection",
        prompt: payload.prompt || "Please select an option:",
        options: payload.options || [],
        multiple: !!payload.multiple,
        resolved: false,
        timestamp: new Date().toISOString()
    };
    chatMessages.push(card);
    return card;
}

function addIterationCard(ctx, summary) {
    const existing = chatMessages.find(m => m.role === "iteration" && !m.resolved);
    if (existing) {
        existing.summary = summary || existing.summary;
        return existing;
    }
    const card = {
        id: uid("iter"),
        role: "iteration",
        summary: summary || "",
        resolved: false,
        timestamp: new Date().toISOString()
    };
    chatMessages.push(card);
    return card;
}

function pushAssistantText(content, extra) {
    if (!content) return;
    chatMessages.push(Object.assign({
        id: uid("msg"),
        role: "assistant",
        content: content,
        timestamp: new Date().toISOString()
    }, extra || {}));
}

function settleBubbles(ctx) {
    for (const id of ctx.stepBubbles.values()) {
        const m = chatMessages.find(x => x.id === id);
        if (m) {
            m.streaming = false;
            m.reasoningOpen = false;
        }
    }
}

/**
 * Render one streamed run event. Returns true when only a single bubble's
 * text changed, so the caller can patch that bubble instead of
 * re-rendering the whole list.
 */
function applyAssistantEvent(ctx, ev) {
    if (ev.run_id) {
        if (ctx.runId && ev.run_id !== ctx.runId) {
            // A different run than we were following (we reattached after a
            // newer one started): start its bookkeeping fresh.
            ctx.stepBubbles = new Map();
            ctx.lastSeq = 0;
        }
        ctx.runId = ev.run_id;
    }
    if (typeof ev.seq === "number") {
        if (ev.seq <= ctx.lastSeq) return false; // already rendered
        ctx.lastSeq = ev.seq;
    }

    switch (ev.type) {
        case "run_start":
            ctx.done = false;
            ctx.modelLabel = ev.model && ev.model.label ? ev.model.label : null;
            if (ctx.thinkingId) startThinking(ctx);
            break;

        case "transcription":
            if (!ctx.transcriptionShown && ev.text) {
                ctx.transcriptionShown = true;
                const thinking = ctx.thinkingId;
                endThinking(ctx);
                chatMessages.push({
                    id: uid("user"),
                    role: "user",
                    content: ev.text,
                    timestamp: new Date().toISOString()
                });
                if (thinking) startThinking(ctx, "Thinking…");
            }
            break;

        case "step_start":
            startThinking(ctx, ev.iteration > 1
                ? `Step ${ev.iteration} of ${ev.max_iterations || "?"}`
                : "Thinking…");
            break;

        case "reasoning_delta": {
            const b = stepBubble(ctx, ev.iteration);
            b.reasoning += ev.delta || "";
            return b.id;
        }

        case "text_delta": {
            const b = stepBubble(ctx, ev.iteration);
            b.content += ev.delta || "";
            b.reasoningOpen = false;
            return b.id;
        }

        case "retry": {
            const id = ctx.stepBubbles.get(ev.iteration || 0);
            if (id) {
                chatMessages = chatMessages.filter(m => m.id !== id);
                ctx.stepBubbles.delete(ev.iteration || 0);
            }
            startThinking(ctx, "Connection hiccup — retrying…");
            break;
        }

        case "tool_call": {
            const b = chatMessages.find(m => m.id === ctx.stepBubbles.get(ev.iteration || 0));
            if (b) b.reasoningOpen = false;
            upsertToolCard(ctx, ev.id, {
                toolName: ev.name || "Tool Execution",
                isPreparing: true
            });
            break;
        }

        case "tool_start":
            upsertToolCard(ctx, ev.id, {
                toolName: ev.name || "Tool Execution",
                parameters: formatToolPayload(ev.args),
                isPreparing: false,
                isRunning: true,
                requiresApproval: false,
                ok: null,
                durationMs: null
            });
            break;

        case "tool_end":
            upsertToolCard(ctx, ev.id, {
                toolName: ev.name || "Tool Execution",
                isPreparing: false,
                isRunning: false,
                ok: ev.ok !== false,
                response: formatToolPayload(ev.result),
                durationMs: typeof ev.duration_ms === "number" ? ev.duration_ms : null,
                approved: ev.approved
            });
            if (!anyToolBusy(ctx)) startThinking(ctx, "Reading the results…");
            break;

        case "final": {
            endThinking(ctx);
            // Reuse the bubble the reply streamed into; the final text is
            // authoritative in case a delta was dropped.
            const lastKey = Math.max(-1, ...ctx.stepBubbles.keys());
            const b = lastKey >= 0 ? chatMessages.find(m => m.id === ctx.stepBubbles.get(lastKey)) : null;
            if (b && !anyToolCardAfter(b)) {
                b.content = ev.content || b.content;
            } else {
                pushAssistantText(ev.content);
            }
            settleBubbles(ctx);
            break;
        }

        case "error":
            endThinking(ctx);
            pushAssistantText("Error: " + (ev.message || "The assistant run failed."), { isError: true });
            break;

        case "approval_required":
            ctx.sawApproval = true;
            upsertToolCard(ctx, ev.id, {
                toolName: ev.name || "Tool Execution",
                parameters: formatToolPayload(ev.args),
                isPreparing: false,
                isRunning: false,
                requiresApproval: true,
                isCollapsed: false
            });
            endThinking(ctx);
            break;

        case "selection_required":
            ctx.sawSelection = true;
            endThinking(ctx);
            // The selection tool is shown as a card, not as a tool run.
            if (ev.id && ctx.toolCardIds.has(ev.id)) {
                const cardId = ctx.toolCardIds.get(ev.id);
                chatMessages = chatMessages.filter(m => m.id !== cardId);
                ctx.toolCardIds.delete(ev.id);
            }
            addSelectionCard(ctx, ev);
            break;

        case "iterations_exceeded":
            ctx.sawIteration = true;
            endThinking(ctx);
            addIterationCard(ctx, ev.summary || "");
            break;

        case "cancelled":
            endThinking(ctx);
            pushAssistantText("Stopped.", { isNote: true });
            break;

        case "run_end":
            ctx.done = true;
            endThinking(ctx);
            settleBubbles(ctx);
            break;
    }
    return false;
}

function anyToolCardAfter(bubble) {
    const idx = chatMessages.indexOf(bubble);
    return chatMessages.slice(idx + 1).some(m => m.role === "tool");
}

/**
 * Apply the authoritative session state that closes every stream. Live
 * events are preferred for content; this makes sure a prompt the user
 * must answer is always present.
 */
function applyAssistantState(ctx, state) {
    if (!state) return;
    ctx.gotState = true;
    // The shared conversation: where Dana's memory starts now (app-thread.js); other chats: titles in the sidebar.
    if (state.thread === true && typeof applyThreadInfo === "function") applyThreadInfo(state);
    else if (state.status !== "running" && typeof refreshChatSidebar === "function") refreshChatSidebar();
    endThinking(ctx);
    settleBubbles(ctx);

    if (state.status === "error" && state.error) {
        pushAssistantText("Error: " + state.error, { isError: true });
    }

    const awaitingApproval = state.status === "pending_approval" || state.status === "waiting_approval";
    if (awaitingApproval && state.pending_approval && !ctx.sawApproval) {
        const pending = state.pending_approval;
        ctx.sawApproval = true;
        upsertToolCard(ctx, pending.tool_call_id || "pending", {
            toolName: pending.tool_name || "Tool Execution",
            parameters: formatToolPayload(pending.tool_args),
            isRunning: false,
            requiresApproval: true,
            isCollapsed: false
        });
    }

    if (state.selection_request && !ctx.sawSelection) {
        ctx.sawSelection = true;
        addSelectionCard(ctx, state.selection_request);
    }

    if (state.status === "iterations_exceeded" && !ctx.sawIteration) {
        ctx.sawIteration = true;
        addIterationCard(ctx, state.extend_summary || "");
    }
}

// --- Rendering cadence -------------------------------------------------

let pendingBubblePatches = new Set();
let pendingFullRender = false;
let renderFrame = null;

/** Coalesce stream updates to one paint per animation frame. */
function scheduleRender(bubbleId) {
    if (bubbleId) pendingBubblePatches.add(bubbleId);
    else pendingFullRender = true;
    if (renderFrame) return;
    renderFrame = requestAnimationFrame(() => {
        renderFrame = null;
        const full = pendingFullRender;
        const patches = pendingBubblePatches;
        pendingFullRender = false;
        pendingBubblePatches = new Set();
        if (full) {
            renderChatMessages();
            return;
        }
        for (const id of patches) {
            if (!patchBubble(id)) {
                renderChatMessages();
                return;
            }
        }
    });
}

function flushRender() {
    if (renderFrame) {
        cancelAnimationFrame(renderFrame);
        renderFrame = null;
    }
    pendingFullRender = false;
    pendingBubblePatches = new Set();
    renderChatMessages();
}

// --- Transport ----------------------------------------------------------

const MAX_REATTACH_ATTEMPTS = 6;

/**
 * Follow one run to completion: POST the request (or GET the reattach
 * endpoint), render its events and, if the connection drops before the
 * closing state frame, reattach and resume from the last seq.
 */
async function runAssistantStream(url, init, ctx) {
    if (assistantStreamAbort) {
        try { assistantStreamAbort.abort(); } catch (e) {}
    }
    const controller = new AbortController();
    assistantStreamAbort = controller;
    updateSendButton();
    const sessionAtStart = currentSessionId;

    const onPayload = (payload) => {
        if (payload.type === "state") {
            applyAssistantState(ctx, payload.state);
            scheduleRender();
            return;
        }
        const patchId = applyAssistantEvent(ctx, payload);
        scheduleRender(typeof patchId === "string" ? patchId : null);
    };

    let attempt = 0;
    let request = { url, init };
    try {
        while (true) {
            try {
                const res = await fetch(request.url, Object.assign({}, request.init, { signal: controller.signal }));
                if (res.status === 409) {
                    // The session is still busy with an earlier run: follow it.
                    const body = await res.json().catch(() => ({}));
                    const active = body.active_run || {};
                    endThinking(ctx);
                    pushAssistantText("Still working on your previous request — following it…", { isNote: true });
                    scheduleRender();
                    request = { url: attachUrl(sessionAtStart, active.run_id, 0), init: {} };
                    continue;
                }
                if (!res.ok || !res.body) {
                    const errText = await res.text().catch(() => "");
                    let message = errText;
                    try { message = JSON.parse(errText).error || errText; } catch (e) {}
                    endThinking(ctx);
                    pushAssistantText("Error: " + (message || `Request failed (${res.status})`), { isError: true });
                    return;
                }
                await readSseStream(res, onPayload);
                if (ctx.gotState) return;
                throw new Error("stream ended early");
            } catch (err) {
                if (err.name === "AbortError") return;
                // Nothing streamed yet and no run known: the request itself failed.
                if (!ctx.runId || attempt >= MAX_REATTACH_ATTEMPTS || currentSessionId !== sessionAtStart) {
                    endThinking(ctx);
                    pushAssistantText("Network error: " + err.message, { isError: true });
                    return;
                }
                attempt += 1;
                startThinking(ctx, "Connection lost — reconnecting…");
                scheduleRender();
                await new Promise(r => setTimeout(r, Math.min(1000 * attempt, 5000)));
                request = { url: attachUrl(sessionAtStart, ctx.runId, ctx.lastSeq), init: {} };
            }
        }
    } finally {
        if (assistantStreamAbort === controller) assistantStreamAbort = null;
        endThinking(ctx);
        flushRender();
        updateSendButton();
    }
}

function attachUrl(sessionId, runId, after) {
    const q = new URLSearchParams();
    if (runId) q.set("run_id", runId);
    q.set("after", String(after || 0));
    return `/api/sessions/${encodeURIComponent(sessionId)}/stream?${q}`;
}

/** Re-follow a run that is still going (page reload, session switch). */
function attachToActiveRun(activeRun) {
    const ctx = newRunContext();
    startThinking(ctx, "Thinking…");
    flushRender();
    runAssistantStream(attachUrl(currentSessionId, activeRun.run_id, 0), {}, ctx);
}

/** Stop the run this view is following (server-side cancel). */
async function stopCurrentAssistantRun() {
    try {
        await fetch("/api/assistant/approve", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: currentSessionId, action: "cancel" })
        });
    } catch (e) {
        // The run_end/cancelled events arrive on the stream; abort only if
        // the cancel request itself failed.
        if (assistantStreamAbort) {
            try { assistantStreamAbort.abort(); } catch (e2) {}
        }
    }
}

function updateSendButton() {
    if (!chatSendBtn) return;
    const busy = !!assistantStreamAbort;
    chatSendBtn.title = busy ? "Stop" : "Send Message";
    chatSendBtn.innerHTML = `<i data-lucide="${busy ? "square" : "send"}"></i>`;
    refreshIcons();
}
