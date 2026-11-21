/* The web app — the Chat screen (static/app.js says how the app's scripts fit together).
 *
 * Its elements, the session badge and New chat, the realtime model toggle, image attachment, the model
 * picker, sending, rendering the messages (replies, tool cards, approvals, selections, iteration
 * prompts), the session history, and restoring the open chat on page load. Runs stream through
 * app-stream.js.
 */

// ══════════════════════════════════════════════════════════════════════
// 4. SCREEN 1: CHAT & ASSISTANT CONTROLLER
// ══════════════════════════════════════════════════════════════════════

const chatMessagesEl = document.getElementById("chat-messages");
const chatTextInput = document.getElementById("chat-text-input");
const chatSendBtn = document.getElementById("btn-chat-send");
const chatImgBtn = document.getElementById("btn-chat-img");
const chatImgInput = document.getElementById("chat-img-input");
const chatAttachedPreview = document.getElementById("chat-attached-preview");
const chatPreviewImg = document.getElementById("chat-preview-img");
const chatPreviewName = document.getElementById("chat-preview-name");
const chatPreviewRemove = document.getElementById("btn-chat-preview-remove");
const chatSessionIdLabel = document.getElementById("chat-session-id-label");
const chatSessionBadge = document.getElementById("chat-session-badge");
const chatInputWrapper = document.getElementById("chat-input-wrapper");
const btnChatNewSession = document.getElementById("btn-chat-new-session");
const btnChatHistory = document.getElementById("btn-chat-history");
const chatLayout = document.querySelector(".chat-layout");
const chatSidebarList = document.getElementById("chat-sessions-list");
const btnChatNewTopic = document.getElementById("btn-chat-new-topic");
const btnChatCall = document.getElementById("btn-chat-call");
const chatCallIcon = document.getElementById("chat-call-icon");
const chatCallBanner = document.getElementById("chat-call-banner");
const callBannerStatus = document.getElementById("call-banner-status");
const callPulseOrb = document.getElementById("call-pulse-orb");
const btnCallHangup = document.getElementById("btn-call-hangup");
const btnCallMute = document.getElementById("btn-call-mute");
const callMuteIcon = document.getElementById("call-mute-icon");
const chatModelBtn = document.getElementById("chat-model-btn");
const chatModelLabel = document.getElementById("chat-model-label");

// The top bar under "Dana": which chat is open.
function updateSessionBadge() {
    const shared = currentSessionId === THREAD_ID;
    if (chatSessionIdLabel) {
        chatSessionIdLabel.textContent = shared ? "Shared chat" : "#" + currentSessionId.substring(0, 8);
    }
    if (chatSessionBadge) {
        chatSessionBadge.title = shared ? "The conversation your phone, watch and tablet share" : "Click to copy session ID";
        const prefix = chatSessionBadge.firstChild;
        if (prefix && prefix.nodeType === 3) prefix.textContent = shared ? "" : "Session: ";
    }
    btnChatNewTopic?.classList.toggle("hidden", !shared);
    markActiveSidebarItem();
}
updateSessionBadge();

chatSessionBadge?.addEventListener("click", () => {
    if (currentSessionId === THREAD_ID) return;
    navigator.clipboard.writeText(currentSessionId).then(() => {
        chatSessionIdLabel.textContent = "Copied!";
        setTimeout(updateSessionBadge, 1500);
    });
});

btnChatNewSession?.addEventListener("click", () => {
    detachChatStream();
    currentSessionId = "web_" + Math.random().toString(36).substring(2, 10);
    localStorage.setItem("dana_session_id", currentSessionId);
    threadInfo = null;
    updateSessionBadge();
    chatMessages = [];
    renderChatMessages();
    closeSidebarOnPhone();
});

// "New topic" in the shared conversation: Dana forgets it so far, the messages stay.
btnChatNewTopic?.addEventListener("click", async () => {
    if (currentSessionId !== THREAD_ID) return;
    btnChatNewTopic.disabled = true;
    try {
        const res = await fetch(`/api/sessions/${THREAD_ID}/clear`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ device: "web" })
        });
        const data = await res.json().catch(() => ({}));
        if (res.status === 409) {
            pushChatNote("Dana is still answering. Try again when she is done.");
        } else if (res.ok) {
            applyThreadInfo(data);
            // An open approval or choice counts as declined: drop its buttons.
            chatMessages.forEach(m => { if (m.requiresApproval) m.requiresApproval = false; });
            chatMessages = chatMessages.filter(m => m.role !== "selection" && m.role !== "iteration");
        } else {
            pushChatNote("Couldn't start a new topic: " + (data.error || res.status));
        }
    } catch (e) {
        pushChatNote("Couldn't start a new topic: " + e.message);
    } finally {
        btnChatNewTopic.disabled = false;
        renderChatMessages();
    }
});

/** A small note in the chat (not stored). */
function pushChatNote(text) {
    chatMessages.push({ id: uid("note"), role: "assistant", content: text, isNote: true, timestamp: new Date().toISOString() });
    renderChatMessages();
}

// Toggle Realtime Model
chatModelBtn?.addEventListener("click", () => {
    if (selectedRealtimeModel === "gpt-realtime") {
        selectedRealtimeModel = "gpt-realtime-mini";
        chatModelLabel.textContent = "Mini";
    } else {
        selectedRealtimeModel = "gpt-realtime";
        chatModelLabel.textContent = "Realtime";
    }
});

// Image Attachment
chatImgBtn?.addEventListener("click", () => chatImgInput?.click());
chatImgInput?.addEventListener("change", (e) => {
    const file = e.target.files?.[0];
    if (file) {
        pendingImageFile = file;
        chatPreviewName.textContent = file.name;
        const reader = new FileReader();
        reader.onload = (re) => {
            chatPreviewImg.src = re.target.result;
            chatAttachedPreview.classList.remove("hidden");
        };
        reader.readAsDataURL(file);
    }
});
chatPreviewRemove?.addEventListener("click", () => {
    pendingImageFile = null;
    chatImgInput.value = "";
    chatAttachedPreview.classList.add("hidden");
});

// --- Model picker -------------------------------------------------------

const chatLlmSelect = document.getElementById("chat-llm-select");
let selectedLlm = null;
try { selectedLlm = localStorage.getItem("dana_chat_model"); } catch (e) {}

async function loadModelCatalog() {
    if (!chatLlmSelect) return;
    try {
        const res = await fetch("/api/assistant/models");
        if (!res.ok) return;
        const catalog = await res.json();
        const ids = catalog.models.map(m => m.id);
        if (!selectedLlm || !ids.includes(selectedLlm)) selectedLlm = catalog.default;
        chatLlmSelect.innerHTML = catalog.models.map(m =>
            `<option value="${escapeHtml(m.id)}" title="${escapeHtml(m.description || "")}">${escapeHtml(m.label)}</option>`
        ).join("");
        chatLlmSelect.value = selectedLlm;
        chatLlmSelect.classList.remove("hidden");
    } catch (e) {
        console.warn("Could not load the model catalog", e);
    }
}
chatLlmSelect?.addEventListener("change", () => {
    selectedLlm = chatLlmSelect.value;
    try { localStorage.setItem("dana_chat_model", selectedLlm); } catch (e) {}
});
loadModelCatalog();

// ══════════════════════════════════════════════════════════════════════
// SEND MESSAGE
// ══════════════════════════════════════════════════════════════════════
async function sendChatMessage() {
    const text = chatTextInput.value.trim();
    if (!text && !pendingImageFile) return;

    chatTextInput.value = "";
    const imageToSend = pendingImageFile;
    pendingImageFile = null;
    if (chatAttachedPreview) chatAttachedPreview.classList.add("hidden");
    if (chatImgInput) chatImgInput.value = "";

    // Add user message to UI
    chatMessages.push({
        id: "msg_" + Date.now(),
        role: "user",
        content: text,
        imageUrl: imageToSend ? chatPreviewImg.src : null,
        timestamp: new Date().toISOString()
    });

    const ctx = newRunContext();
    startThinking(ctx, "Sending…");
    flushRender();

    if (imageToSend) {
        const fd = new FormData();
        fd.append("message", text);
        fd.append("image", imageToSend);
        const q = new URLSearchParams({ session_id: currentSessionId, device: "web" });
        if (selectedLlm) q.set("model", selectedLlm);
        await runAssistantStream(`/api/assistant/message_with_image?${q}`, { method: "POST", body: fd }, ctx);
    } else {
        await runAssistantStream("/api/assistant/message", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text, session_id: currentSessionId, device: "web", model: selectedLlm || undefined })
        }, ctx);
    }
}

chatSendBtn?.addEventListener("click", () => {
    if (assistantStreamAbort) {
        stopCurrentAssistantRun();
        return;
    }
    sendChatMessage();
});
chatTextInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (assistantStreamAbort) {
            stopCurrentAssistantRun();
            return;
        }
        sendChatMessage();
    }
});

// How many messages of this session are on screen: only one past them rises in (styles.css .dn-new), so a
// streaming reply that changes its id (thinking → live → saved) rises once; a session's first draw and
// older pages added on top come in quietly.
let drawnCount = 0;
let drawnSession = null;
let drawQuietOnce = false;
function startDrawing() {
    const quiet = drawQuietOnce || drawnSession !== currentSessionId;
    drawnSession = currentSessionId;
    drawQuietOnce = false;
    const from = quiet ? Infinity : drawnCount;
    drawnCount = chatMessages.length;
    return from;
}

// Render Chat Messages
function renderChatMessages() {
    if (!chatMessagesEl) return;
    const newFrom = startDrawing();
    if (chatMessages.length === 0) {
        chatMessagesEl.innerHTML = `
                <div class="pane-empty-state" style="margin-top:auto;margin-bottom:auto;">
                    <strong style="color:var(--ink);font-family:var(--font-serif);font-style:italic;font-weight:400;font-size:30px;line-height:1.1;">Nothing said yet<span style="color:var(--accent, var(--ember));">.</span></strong>
                    <span style="font-size:13px;max-width:320px;">Ask about your day, your reminders and calls, or the house.</span>
                </div>
            `;
        return;
    }

    const stickToBottom = isNearBottom();
    chatMessagesEl.innerHTML = "";

    // The shared conversation: "Show earlier" and the line where Dana's memory starts.
    const shared = currentSessionId === THREAD_ID;
    const divider = shared ? threadDivider(chatMessages, threadInfo, Date.now() / 1000) : null;
    if (shared && threadInfo?.hasMore && threadInfo.before != null) {
        const more = document.createElement("button");
        more.className = "chip chat-load-earlier";
        more.textContent = "Show earlier messages";
        more.addEventListener("click", loadEarlierThreadMessages);
        chatMessagesEl.appendChild(more);
    }
    const memoryLine = (text, title) => {
        const line = document.createElement("div");
        line.className = "chat-memory-line";
        line.textContent = text;
        line.title = title;
        chatMessagesEl.appendChild(line);
    };

    chatMessages.forEach((msg, idx) => {
        if (divider && !divider.fresh && idx === divider.index) {
            memoryLine("Dana remembers from here", "Dana doesn't see the messages above. Pausing 20 minutes or tapping New topic starts fresh.");
        }
        const firstChild = chatMessagesEl.children.length;
        renderChatMessage(msg);
        if (idx >= newFrom) {
            for (let i = firstChild; i < chatMessagesEl.children.length; i++) chatMessagesEl.children[i].classList.add("dn-new");
        }
        if (divider && idx < divider.index) {
            for (let i = firstChild; i < chatMessagesEl.children.length; i++) chatMessagesEl.children[i].classList.add("chat-forgotten");
        }
    });
    if (divider && divider.fresh) {
        memoryLine("New topic — Dana starts fresh", "Dana doesn't see the messages above; your next message starts a new conversation.");
    }
    if (shared) scheduleMemoryLineRefresh();

    if (stickToBottom) chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
    refreshIcons();
}

/** Append one message's element (bubble, tool card, selection or step-limit prompt). */
function renderChatMessage(msg) {
    if (msg.role === "tool") {
        // Render ToolCallCard
        const card = document.createElement("div");
        card.className = "tool-call-card";
        const statusBadge = msg.isPreparing
            ? `<span class="tool-call-running"><span class="tool-spinner"></span> Preparing</span>`
            : msg.isRunning
            ? `<span class="tool-call-running"><span class="tool-spinner"></span> Running</span>`
            : (msg.ok === false
                ? `<span class="tool-approval-badge">Failed</span>`
                : (msg.approved === true ? `<span class="tool-call-time">Approved</span>` : ""));
        card.innerHTML = `
                <div class="tool-call-header" data-toggle="${msg.id}">
                    <span class="tool-icon-badge">⚙</span>
                    <span class="tool-call-name" title="${escapeHtml(msg.toolName)}">${escapeHtml(msg.toolName)}</span>
                    ${statusBadge}
                    ${msg.requiresApproval ? `<span class="tool-approval-badge">Needs Approval</span>` : ""}
                    ${msg.durationMs !== null && msg.durationMs !== undefined && !msg.isRunning
                    ? `<span class="tool-call-time">${msg.durationMs}ms</span>` : ""}
                    <span class="tool-call-time">${new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                    <i data-lucide="chevron-down" class="tool-call-chevron ${msg.isCollapsed ? "" : "open"}" style="width:16px;height:16px;"></i>
                </div>
                <div class="tool-call-body ${msg.isCollapsed ? "hidden" : ""}">
                    ${msg.parameters ? `
                        <div class="tool-json-section">
                            <span class="tool-section-label">Parameters</span>
                            <div class="tool-json-box">${escapeHtml(msg.parameters)}</div>
                        </div>
                    ` : ""}
                    ${msg.response ? `
                        <div class="tool-json-section">
                            <span class="tool-section-label">Response</span>
                            <div class="tool-json-box">${escapeHtml(msg.response)}</div>
                        </div>
                    ` : ""}
                    ${msg.isRunning ? `
                        <div class="tool-approval-box">
                            <span class="tool-approval-msg">Running…</span>
                        </div>
                    ` : ""}
                    ${msg.requiresApproval ? `
                        <div class="tool-approval-box">
                            <span class="tool-approval-title">⚠ Action Required</span>
                            <span class="tool-approval-msg">The assistant wants to execute the above tool. Do you allow it?</span>
                            <div class="tool-approval-buttons">
                                <button class="btn-destructive-outline btn-deny" data-id="${msg.id}" style="flex:1;">Deny</button>
                                <button class="btn-primary btn-approve" data-id="${msg.id}" style="flex:1;">Approve</button>
                            </div>
                        </div>
                    ` : ""}
                </div>
            `;

        // Event listener to collapse/expand
        card.querySelector(".tool-call-header").addEventListener("click", () => {
            msg.isCollapsed = !msg.isCollapsed;
            renderChatMessages();
        });

        // Approve / Deny click
        card.querySelector(".btn-approve")?.addEventListener("click", () => handleToolApproval(msg.id, true));
        card.querySelector(".btn-deny")?.addEventListener("click", () => handleToolApproval(msg.id, false));

        chatMessagesEl.appendChild(card);
    } else if (msg.role === "selection") {
        // Render SelectionCard
        const card = document.createElement("div");
        card.className = "selection-card";
        let selectedIdxs = [];

        card.innerHTML = `
                <div class="selection-prompt">${escapeHtml(msg.prompt)}</div>
                <div class="selection-options">
                    ${msg.options.map((opt, idx) => `
                        <div class="selection-option-row" data-idx="${idx}">
                            <input type="${msg.multiple ? "checkbox" : "radio"}" name="sel_${msg.id}" ${msg.multiple ? "" : "name='opt'"} style="accent-color:var(--accent);">
                            <span style="font-size:13.5px;">${escapeHtml(opt.label || opt)}</span>
                        </div>
                    `).join("")}
                </div>
                <button class="btn-primary btn-submit-selection" style="margin-top:4px;">Submit Selection</button>
            `;

        const optRows = card.querySelectorAll(".selection-option-row");
        optRows.forEach(row => {
            row.addEventListener("click", () => {
                const idx = parseInt(row.getAttribute("data-idx"));
                const inp = row.querySelector("input");
                if (msg.multiple) {
                    inp.checked = !inp.checked;
                    row.classList.toggle("selected", inp.checked);
                    if (inp.checked) selectedIdxs.push(idx);
                    else selectedIdxs = selectedIdxs.filter(i => i !== idx);
                } else {
                    optRows.forEach(r => {
                        r.classList.remove("selected");
                        r.querySelector("input").checked = false;
                    });
                    inp.checked = true;
                    row.classList.add("selected");
                    selectedIdxs = [idx];
                }
            });
        });

        card.querySelector(".btn-submit-selection").addEventListener("click", () => {
            if (!selectedIdxs.length) return;
            msg.resolved = true;
            chatMessages = chatMessages.filter(m => m !== msg);
            sendSelectionChoice(msg, selectedIdxs.slice());
        });

        chatMessagesEl.appendChild(card);
    } else if (msg.role === "iteration") {
        // Iteration budget exhausted — offer more steps or stop here.
        const card = document.createElement("div");
        card.className = "selection-card iteration-card";
        card.innerHTML = `
                <div class="selection-prompt">⚠ This task is taking a lot of steps</div>
                <div class="iteration-summary">${escapeHtml(msg.summary || "The assistant reached its step limit before finishing.")}</div>
                <div class="tool-approval-buttons">
                    <button class="btn-destructive-outline btn-iter-stop" data-id="${msg.id}" style="flex:1;">Stop here</button>
                    <button class="btn-primary btn-iter-extend" data-id="${msg.id}" style="flex:1;">Give it more steps</button>
                </div>
            `;
        card.querySelector(".btn-iter-extend")?.addEventListener("click", () => {
            msg.resolved = true;
            card.remove();
            startIterationDecision("extend_iterations");
        });
        card.querySelector(".btn-iter-stop")?.addEventListener("click", () => {
            msg.resolved = true;
            card.remove();
            startIterationDecision("decline_iterations");
        });
        chatMessagesEl.appendChild(card);
    } else {
        // Regular chat bubble
        const row = document.createElement("div");
        row.className = `chat-bubble-row ${msg.role === "user" ? "user" : "assistant"}`;
        const bubble = document.createElement("div");
        bubble.dataset.msgId = msg.id;
        fillBubble(bubble, msg);
        row.appendChild(bubble);
        if (msg.role === "user" && msg.device && msg.device !== "web" && currentSessionId === THREAD_ID) {
            const wrap = document.createElement("div");
            wrap.style.cssText = "display:flex;flex-direction:column;align-items:flex-end;max-width:85%;";
            bubble.style.maxWidth = "100%";
            wrap.appendChild(bubble);
            const tag = document.createElement("div");
            tag.className = "chat-device-tag";
            tag.textContent = "from " + msg.device;
            wrap.appendChild(tag);
            row.appendChild(wrap);
        }
        chatMessagesEl.appendChild(row);
    }
}

/** True when the user is reading the latest messages (not scrolled up). */
function isNearBottom() {
    if (!chatMessagesEl) return true;
    return chatMessagesEl.scrollHeight - chatMessagesEl.scrollTop - chatMessagesEl.clientHeight < 120;
}

function renderMarkdown(text) {
    if (window.marked && window.DOMPurify) {
        return `<div class="markdown-body">${DOMPurify.sanitize(marked.parse(text || ""))}</div>`;
    }
    return `<p>${escapeHtml(text || "")}</p>`;
}

/** (Re)build one chat bubble's contents from its message. */
function fillBubble(bubble, msg) {
    const role = msg.role === "user" ? "user" : "assistant";
    bubble.className = `chat-bubble ${role}${msg.streaming ? " streaming" : ""}${msg.isError ? " error" : ""}${msg.isNote ? " note" : ""}`;
    const text = msg.content || msg.reasoning || "";
    if (isRtlText(text)) bubble.setAttribute("dir", "rtl");
    else bubble.removeAttribute("dir");

    if (msg.isThinking) {
        bubble.innerHTML = `
                <div class="typing-dots">
                    <span class="typing-dot"></span>
                    <span class="typing-dot"></span>
                    <span class="typing-dot"></span>
                </div>
                ${msg.thinkingLabel ? `<div class="thinking-label">${escapeHtml(msg.thinkingLabel)}</div>` : ""}
            `;
        return;
    }

    let html = "";
    if (msg.imageUrl) {
        html += `<img src="${msg.imageUrl}" style="max-width:100%;border-radius:4px;margin-bottom:8px;display:block;">`;
    }
    if (msg.reasoning) {
        html += `
                <details class="reasoning-block" ${msg.reasoningOpen ? "open" : ""}>
                    <summary>${msg.streaming && !msg.content ? "Thinking…" : "Thought process"}</summary>
                    <div class="reasoning-text">${renderMarkdown(msg.reasoning)}</div>
                </details>`;
    }
    if (msg.content) {
        html += renderMarkdown(msg.content);
    }
    if (msg.streaming) {
        html += `<span class="stream-caret" aria-hidden="true"></span>`;
    } else if (role === "assistant" && msg.content && !msg.isNote) {
        html += `
                <div class="chat-bubble-actions">
                    <button class="chat-bubble-act-btn btn-copy" title="Copy text"><i data-lucide="copy" style="width:12px;height:12px;"></i> Copy</button>
                    <button class="chat-bubble-act-btn btn-tts" title="Read aloud"><i data-lucide="volume-2" style="width:12px;height:12px;"></i> Speak</button>
                </div>
            `;
    }
    bubble.innerHTML = html;

    bubble.querySelector(".reasoning-block")?.addEventListener("toggle", (e) => {
        msg.reasoningOpen = e.target.open;
    });
    bubble.querySelector(".btn-copy")?.addEventListener("click", () => {
        navigator.clipboard.writeText(msg.content);
    });
    bubble.querySelector(".btn-tts")?.addEventListener("click", () => {
        if ("speechSynthesis" in window) {
            window.speechSynthesis.cancel();
            const cleanText = msg.content.replace(/[*#`_\-]/g, "");
            const utter = new SpeechSynthesisUtterance(cleanText);
            if (isRtlText(cleanText)) utter.lang = "he-IL";
            window.speechSynthesis.speak(utter);
        }
    });
}

/** Update a single streaming bubble in place. Returns false if it isn't on screen. */
function patchBubble(id) {
    const msg = chatMessages.find(m => m.id === id);
    const bubble = chatMessagesEl?.querySelector(`[data-msg-id="${CSS.escape(id)}"]`);
    if (!msg || !bubble) return false;
    const stick = isNearBottom();
    fillBubble(bubble, msg);
    if (stick) chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
    return true;
}

/**
 * Approve or deny a pending write tool, streaming whatever the agent does
 * next. Note the server expects `action`, not `approved`.
 */
async function handleToolApproval(toolId, approved) {
    const tc = chatMessages.find(m => m.id === toolId);
    if (tc) {
        tc.requiresApproval = false;
        tc.isRunning = approved;
        renderChatMessages();
    }

    const ctx = newRunContext();
    if (tc) ctx.toolCardIds.set(tc.toolCallId || tc.id, tc.id);
    startThinking(ctx, approved ? "Applying your change…" : "Cancelling…");
    flushRender();

    await runAssistantStream("/api/assistant/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            session_id: currentSessionId,
            device: "web",
            action: approved ? "approve" : "deny"
        })
    }, ctx);
}

/** Extend or end an over-budget run. */
async function startIterationDecision(action) {
    const ctx = newRunContext();
    startThinking(ctx, action === "extend_iterations"
        ? "Continuing with more steps…"
        : "Wrapping up…");
    flushRender();

    await runAssistantStream("/api/assistant/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: currentSessionId, device: "web", action })
    }, ctx);
}

/**
 * Answer a selection prompt: the server resumes the paused run with the
 * chosen option indices (an empty list cancels).
 */
async function sendSelectionChoice(card, indices) {
    const labels = indices.map(i => card.options[i]?.label || card.options[i]?.value || String(card.options[i]));
    if (labels.length) {
        chatMessages.push({ id: uid("msg"), role: "user", content: labels.join(", "), timestamp: new Date().toISOString() });
    }
    const ctx = newRunContext();
    startThinking(ctx, "Continuing…");
    flushRender();

    await runAssistantStream("/api/assistant/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: currentSessionId, device: "web", action: "select", selection: indices })
    }, ctx);
}

// ── Chats sidebar: the shared conversation pinned on top, then recent chats ──

const SIDEBAR_HIDDEN_KEY = "dana_chat_sidebar_hidden";
const isPhoneWidth = () => window.matchMedia("(max-width: 900px)").matches;

try {
    if (localStorage.getItem(SIDEBAR_HIDDEN_KEY) === "1") chatLayout?.classList.add("sidebar-hidden");
} catch (e) {}

btnChatHistory?.addEventListener("click", () => {
    if (!chatLayout) return;
    if (isPhoneWidth()) {
        chatLayout.classList.toggle("sidebar-open");
        if (chatLayout.classList.contains("sidebar-open")) refreshChatSidebar();
    } else {
        const hidden = chatLayout.classList.toggle("sidebar-hidden");
        try { localStorage.setItem(SIDEBAR_HIDDEN_KEY, hidden ? "1" : "0"); } catch (e) {}
        if (!hidden) refreshChatSidebar();
    }
});
document.getElementById("chat-sidebar-backdrop")?.addEventListener("click", closeSidebarOnPhone);
document.getElementById("chat-sidebar-shared")?.addEventListener("click", () => {
    loadSession(THREAD_ID);
    closeSidebarOnPhone();
});

function closeSidebarOnPhone() {
    chatLayout?.classList.remove("sidebar-open");
}

function markActiveSidebarItem() {
    document.querySelectorAll(".chat-sidebar-item").forEach(el => {
        el.classList.toggle("active", el.getAttribute("data-session-id") === currentSessionId);
    });
}

function relativeTime(value) {
    if (!value) return "";
    const d = new Date(String(value).includes("T") || String(value).endsWith("Z") ? value : String(value).replace(" ", "T") + "Z");
    if (isNaN(d)) return "";
    const mins = Math.round((Date.now() - d.getTime()) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    if (mins < 24 * 60) return `${Math.round(mins / 60)} h ago`;
    return d.toLocaleDateString([], { day: "numeric", month: "short" });
}

/** Reload the recent chats (GET /api/sessions never lists the shared one; it is pinned above). */
async function refreshChatSidebar() {
    if (!chatSidebarList) return;
    try {
        const res = await fetch("/api/sessions");
        if (!res.ok) throw new Error(res.status);
        const list = (await res.json()) || [];
        if (!list.length) {
            chatSidebarList.innerHTML = `<span class="chat-sidebar-empty">No other chats yet.</span>`;
            return;
        }
        chatSidebarList.innerHTML = list.map(s => {
            const sid = s.session_id || s.id;
            const title = s.title && s.title !== "New Chat" ? s.title : (s.snippet || "Chat " + String(sid).slice(0, 8));
            return `
                <div class="chat-sidebar-item" data-session-id="${escapeHtml(sid)}" tabindex="0">
                    <div class="chat-sidebar-text">
                        <strong${isRtlText(title) ? ' dir="rtl"' : ""}>${escapeHtml(title)}</strong>
                        <span>${escapeHtml(relativeTime(s.updated_at || s.timestamp))}</span>
                    </div>
                    <button class="icon-btn chat-sidebar-del" title="Delete chat" aria-label="Delete chat">
                        <i data-lucide="trash-2" style="width:13px;height:13px;"></i>
                    </button>
                </div>`;
        }).join("");
        chatSidebarList.querySelectorAll(".chat-sidebar-item").forEach(item => {
            const sid = item.getAttribute("data-session-id");
            item.addEventListener("click", () => { loadSession(sid); closeSidebarOnPhone(); });
            item.addEventListener("keydown", (e) => { if (e.key === "Enter") { loadSession(sid); closeSidebarOnPhone(); } });
            item.querySelector(".chat-sidebar-del")?.addEventListener("click", async (e) => {
                e.stopPropagation();
                if (!confirm("Delete this chat?")) return;
                const r = await fetch(`/api/sessions/${encodeURIComponent(sid)}`, { method: "DELETE" });
                if (r.ok && sid === currentSessionId) btnChatNewSession?.click();
                refreshChatSidebar();
            });
        });
        markActiveSidebarItem();
        refreshIcons();
    } catch (e) {
        chatSidebarList.innerHTML = `<span class="chat-sidebar-empty">Couldn't load chats.</span>`;
    }
}

/** Stop rendering a stream into the view. The server keeps the run going; its result is stored either way. */
function detachChatStream() {
    if (assistantStreamAbort) {
        try { assistantStreamAbort.abort(); } catch (e) {}
        assistantStreamAbort = null;
    }
}

/** Server messages (client format) → the chat's message model. */
function mapServerMessages(msgs) {
    return (msgs || []).map(item => {
        if (item.sender === "Tool" || item.toolCall) {
            const tc = item.toolCall || {};
            return {
                id: item.id || ("tc_" + Math.random()),
                role: "tool",
                toolName: tc.toolName || tc.tool_name || "Tool Execution",
                toolCallId: tc.toolCallId || "",
                parameters: tc.parameters || "",
                response: tc.response || "",
                requiresApproval: tc.requiresApproval || false,
                isCollapsed: tc.isCollapsed !== undefined ? tc.isCollapsed : true,
                timestamp: item.timestamp || new Date().toISOString()
            };
        }
        const isUsr = item.isUser !== undefined ? item.isUser : (item.role === "user");
        return {
            id: item.id || ("msg_" + Math.random()),
            role: isUsr ? "user" : "assistant",
            content: item.message || item.content || "",
            imageUrl: (item.images && item.images.length > 0) ? item.images[0] : null,
            device: item.device || "",
            timestamp: item.timestamp || new Date().toISOString()
        };
    });
}

async function loadSession(sid) {
    detachChatStream();
    currentSessionId = sid;
    localStorage.setItem("dana_session_id", sid);
    threadInfo = null;
    updateSessionBadge();
    chatMessages = [];
    renderChatMessages();

    try {
        const res = await fetch(`/api/sessions/${encodeURIComponent(sid)}`);
        if (res.ok && currentSessionId === sid) {
            const sessionData = await res.json();
            applyThreadInfo(sessionData, { page: true });
            const msgs = sessionData.messages || sessionData.history;
            if (msgs && Array.isArray(msgs)) {
                chatMessages = mapServerMessages(msgs);
                drawQuietOnce = true;   // the session's history comes in at once, not message by message
                renderChatMessages();
                if (chatMessagesEl) chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
            }
            if (sessionData.active_run) {
                attachToActiveRun(sessionData.active_run);
            } else if (sessionData.status === "awaiting_selection" || sessionData.status === "iterations_exceeded") {
                const ctx = newRunContext();
                applyAssistantState(ctx, sessionData);
                renderChatMessages();
            }
        }
    } catch (e) {
        console.error(e);
    }
}

/** The shared conversation: prepend the next older page. */
async function loadEarlierThreadMessages() {
    if (currentSessionId !== THREAD_ID || !threadInfo?.before) return;
    const before = threadInfo.before;
    try {
        const res = await fetch(`/api/sessions/${THREAD_ID}?before=${encodeURIComponent(before)}&limit=60`);
        if (!res.ok || currentSessionId !== THREAD_ID) return;
        const data = await res.json();
        const older = mapServerMessages(data.messages);
        const known = new Set(chatMessages.map(m => m.id));
        const height = chatMessagesEl ? chatMessagesEl.scrollHeight : 0;
        chatMessages = older.filter(m => !known.has(m.id)).concat(chatMessages);
        threadInfo.hasMore = !!data.has_more;
        threadInfo.before = data.before ?? null;
        drawQuietOnce = true;
        renderChatMessages();
        // Keep the reader where they were.
        if (chatMessagesEl) chatMessagesEl.scrollTop += chatMessagesEl.scrollHeight - height;
    } catch (e) {
        console.error(e);
    }
}

// Re-draw the memory line when the quiet gap runs out.
let memoryLineTimer = null;
function scheduleMemoryLineRefresh() {
    clearTimeout(memoryLineTimer);
    const at = threadInfo?.expiresAt;
    if (at == null) return;
    const ms = at * 1000 - Date.now();
    if (ms > 0 && ms < 2 ** 31 - 1) memoryLineTimer = setTimeout(renderChatMessages, ms + 500);
}

// Restore the open chat on page load; reattaches if a reply is still running.
if (currentSessionId) loadSession(currentSessionId);
refreshChatSidebar();
