/* The web app — the shared conversation's memory line (static/app.js says how the app's scripts fit together).
 *
 * The shared thread (session `main`: phone, watch, tablet, web) tells clients where Dana's memory of it
 * starts (`context_from_seq`, `context_expires_at` in its state; docs/agent-actions.md). This file keeps
 * that and works out where the chat draws its "Dana remembers from here" line. Pure, no DOM: it loads in
 * Node as is (test_web_thread.mjs).
 */

const THREAD_ID = "main";

/** What the open chat knows about the shared thread; null for an ordinary chat. */
let threadInfo = null;

/** The stored `seq` in a thread message id (`msg_user_12`, `msg_assistant_12`, `msg_tool_12_<call>`), or null. */
function threadSeqOf(id) {
    const m = /^msg_(?:user|assistant|tool)_(\d+)(?:_|$)/.exec(String(id || ""));
    return m ? Number(m[1]) : null;
}

/** Keep the memory fields of a thread state payload (GET /api/sessions/main or a run's final state). */
function applyThreadInfo(state, { page = false } = {}) {
    if (!state || state.thread !== true) {
        if (page) threadInfo = null;
        return;
    }
    const prev = threadInfo || {};
    threadInfo = {
        fromSeq: state.context_from_seq ?? null,
        expiresAt: state.context_expires_at ?? null,
        // The paging cursor comes from the page the chat loaded, not from a run's newest-60 state.
        hasMore: page ? !!state.has_more : !!prev.hasMore,
        before: page ? (state.before ?? null) : (prev.before ?? null),
        known: "context_from_seq" in state,
    };
}

/**
 * Where the memory line goes in `messages`, or null for none.
 *
 * Returns `{index, fresh}`: the line goes before `messages[index]`, everything above it is no longer
 * remembered. `fresh` = Dana remembers nothing (just cleared, or the quiet gap ran out): the line goes
 * after the last message. Messages sent in this page view (no stored seq yet) are newer than any stored one.
 */
function threadDivider(messages, info, nowSec) {
    if (!info || !info.known || !messages || !messages.length) return null;
    const expired = info.expiresAt != null && nowSec >= info.expiresAt;
    if (info.fromSeq == null || expired) return { index: messages.length, fresh: true };
    let index = messages.findIndex(m => threadSeqOf(m.id) === info.fromSeq);
    if (index < 0) {
        const maxSeq = Math.max(-1, ...messages.map(m => threadSeqOf(m.id) ?? -1));
        if (info.fromSeq > maxSeq) {
            // It starts at a message sent from this page: the first one without a stored seq.
            index = messages.findIndex(m => m.role === "user" && threadSeqOf(m.id) == null);
            if (index < 0) index = messages.length;
        } else {
            // It starts above the loaded page: everything shown is remembered.
            index = 0;
        }
    }
    return index > 0 ? { index, fresh: false } : null;
}
