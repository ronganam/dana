/* The web app — the Debug screen (static/app.js says how the app's scripts fit together).
 *
 * Server logs (polled while the screen is open), the sessions list and one session's history.
 */

// ── Debug & Logs controller state ──
let debugSessionCache = [];
let selectedDebugSessionId = null;
let activeDebugSessionData = null;
let debugView = "logs";
let debugLogsTimer = null;

function stopDebugPolling() {
    if (debugLogsTimer) {
        clearInterval(debugLogsTimer);
        debugLogsTimer = null;
    }
}

function startDebugPolling() {
    if (debugLogsTimer) return;
    loadDebugLogs();
    debugLogsTimer = setInterval(loadDebugLogs, 2000);
}

function setDebugView(view) {
    debugView = view;
    const toggle = document.getElementById("debug-view-toggle");
    toggle?.querySelectorAll(".segmented-btn").forEach(btn => {
        btn.classList.toggle("active", btn.getAttribute("data-view") === view);
    });
    const liveView = document.getElementById("debug-live-logs-view");
    const sessionView = document.getElementById("debug-session-view");
    if (liveView) liveView.classList.toggle("hidden", view !== "logs");
    if (sessionView) sessionView.classList.toggle("hidden", view !== "session");
    refreshIcons();
}

document.querySelectorAll("#debug-view-toggle .segmented-btn").forEach(btn => {
    btn.addEventListener("click", () => setDebugView(btn.getAttribute("data-view")));
});

async function loadDebugLogs() {
    const logsEl = document.getElementById("debug-live-logs");
    if (!logsEl) return;
    try {
        const res = await fetch("/api/debug/logs");
        if (!res.ok) return;
        const logs = await res.json();
        const lines = Array.isArray(logs)
            ? logs.map(l => (typeof l === "string" ? l : (l.formatted || l.message || JSON.stringify(l))))
            : [JSON.stringify(logs, null, 2)];
        const nearBottom = logsEl.scrollTop + logsEl.clientHeight >= logsEl.scrollHeight - 60;
        logsEl.textContent = lines.join("\n");
        if (nearBottom) logsEl.scrollTop = logsEl.scrollHeight;
        const indicator = document.getElementById("debug-live-indicator");
        if (indicator) indicator.textContent = "live · " + lines.length + " lines";
    } catch (e) {
        console.error(e);
    }
}

async function loadDebugData() {
    const sessList = document.getElementById("debug-sessions-list");
    try {
        const res = await fetch("/api/debug/sessions");
        if (res.ok && sessList) {
            const sessions = await res.json();
            debugSessionCache = sessions || [];
            renderDebugSessionsList();
        }
    } catch (e) {
        console.error(e);
    }
    await loadDebugLogs();
}

function renderDebugSessionsList() {
    const sessList = document.getElementById("debug-sessions-list");
    if (!sessList) return;

    if (debugSessionCache.length === 0) {
        sessList.innerHTML = `
                <div class="pane-empty-state">
                    <i data-lucide="terminal" style="width:36px;height:36px;opacity:0.4;"></i>
                    <span>No sessions recorded yet.</span>
                </div>
            `;
        refreshIcons();
        return;
    }

    sessList.innerHTML = debugSessionCache.map(s => {
        const isActive = s.session_id === selectedDebugSessionId;
        return `
                <div class="memory-row-item ${isActive ? "active" : ""}" data-sid="${escapeHtml(s.session_id)}">
                    <div style="flex:1;overflow:hidden;min-width:0;">
                        <strong style="font-size:12px;font-family:var(--font-mono);color:var(--ink);display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                            ${escapeHtml(s.session_id)}
                        </strong>
                        <div style="display:flex;gap:8px;font-size:10.5px;color:var(--mute);margin-top:2px;align-items:center;">
                            <span>${s.history_length ?? 0} msgs</span>
                            ${s.has_pending_approval ? `<span style="color:var(--connecting-amber);">⏸ pending</span>` : ""}
                            <span style="font-family:var(--font-mono);margin-left:auto;">${escapeHtml(formatTime(s.updated_at))}</span>
                        </div>
                    </div>
                </div>
            `;
    }).join("");

    sessList.querySelectorAll("[data-sid]").forEach(item => {
        item.addEventListener("click", () => selectDebugSession(item.getAttribute("data-sid")));
    });
    refreshIcons();
}

async function selectDebugSession(sid) {
    selectedDebugSessionId = sid;
    setDebugView("session");
    renderDebugSessionsList();

    const detailEl = document.getElementById("debug-session-detail");
    if (!detailEl) return;
    detailEl.innerHTML = `<span style="font-size:12px;color:var(--mute);">Loading session…</span>`;

    try {
        const res = await fetch(`/api/debug/sessions/${encodeURIComponent(sid)}`);
        if (!res.ok) throw new Error("Session not found");
        activeDebugSessionData = await res.json();
        renderDebugSessionDetail();
    } catch (e) {
        activeDebugSessionData = null;
        detailEl.innerHTML = `
                <div class="pane-empty-state" style="height:auto;">
                    <i data-lucide="alert-circle" style="width:36px;height:36px;opacity:0.4;"></i>
                    <span>Failed to load session.</span>
                </div>
            `;
    }
    refreshIcons();
}

function extractHistoryText(content) {
    if (content == null) return "";
    if (typeof content === "string") return content;
    if (Array.isArray(content)) {
        return content
            .filter(p => p && typeof p === "object" && p.type === "text" && p.text)
            .map(p => p.text)
            .join("\n");
    }
    return "";
}

function historyEntryHtml(entry, idx) {
    const role = entry.role || entry.sender || "unknown";
    const content = extractHistoryText(entry.content);
    const roleClass = role === "user" ? "role-user" : role === "tool" ? "role-tool" : "role-assistant";
    const roleLabel = role === "tool" ? "tool · " + (entry.name || "") : role;
    let body = "";
    if (content) body += `<div class="dbg-msg-content">${escapeHtml(typeof content === "string" ? content : String(content))}</div>`;
    if (Array.isArray(entry.tool_calls) && entry.tool_calls.length > 0) {
        entry.tool_calls.forEach((tc, ti) => {
            const fn = tc.function || {};
            body += `
                    <div class="dbg-toolcall" data-tc="${idx}_${ti}">
                        <div class="dbg-toolcall-head">
                            <span style="font-family:var(--font-mono);">${escapeHtml(fn.name || "tool_call")}</span>
                            <i data-lucide="chevron-down" class="dbg-chevron"></i>
                        </div>
                        <pre class="dbg-toolcall-args hidden">${escapeHtml(JSON.stringify(fn.arguments == null ? tc.arguments : fn.arguments, null, 2))}</pre>
                    </div>
                `;
        });
    }

    return `
            <div class="dbg-msg">
                <div class="dbg-msg-head">
                    <span class="dbg-role ${roleClass}">${escapeHtml(String(roleLabel))}</span>
                    <span class="dbg-msg-idx">#${idx + 1}</span>
                </div>
                ${body}
            </div>
        `;
}

function renderDebugSessionDetail() {
    const detailEl = document.getElementById("debug-session-detail");
    if (!detailEl) return;
    const d = activeDebugSessionData;
    if (!d) return;

    const history = Array.isArray(d.history) ? d.history : [];
    const lastUpdated = d.updated_at || d.timestamp || "";
    const envVars = (typeof d.environment_variables === "object" && d.environment_variables) ? d.environment_variables : null;

    let html = `
            <div class="dbg-session-head">
                <div>
                    <div class="dbg-sid">${escapeHtml(d.session_id || selectedDebugSessionId)}</div>
                    <div class="dbg-meta-row">
                        <span class="badge-tag">${escapeHtml(d.agent_model || "unknown model")}</span>
                        <span class="badge-tag">${history.length} messages</span>
                        ${d.pending_approval ? `<span class="badge-tag" style="background:rgba(255,179,0,0.15);color:var(--connecting-amber);">pending approval</span>` : ""}
                        ${lastUpdated ? `<span class="badge-tag">${escapeHtml(formatTime(lastUpdated))}</span>` : ""}
                    </div>
                </div>
            </div>
        `;

    if (d.error_context) {
        html += `
                <div class="dbg-error-box">
                    <span class="filter-sidebar-title">Error Context</span>
                    <pre>${escapeHtml(d.error_context)}</pre>
                </div>
            `;
    }

    if (d.pending_approval) {
        html += `
                <div class="dbg-box">
                    <span class="filter-sidebar-title">Pending Approval</span>
                    <pre>${escapeHtml(JSON.stringify(d.pending_approval, null, 2))}</pre>
                </div>
            `;
    }

    html += `
            <div class="dbg-box">
                <div class="dbg-box-head">
                    <span class="filter-sidebar-title">History</span>
                </div>
                ${history.length ? history.map(historyEntryHtml).join("") : `<span style="font-size:12px;color:var(--mute);">No messages in history.</span>`}
            </div>
        `;

    if (envVars) {
        const envRows = Object.entries(envVars)
            .slice(0, 120)
            .map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td class="dbg-env-val">${escapeHtml(String(v))}</td></tr>`)
            .join("");
        html += `
                <div class="dbg-box">
                    <span class="filter-sidebar-title">Environment Variables</span>
                    <table class="dbg-env-table">
                        <tbody>${envRows || '<tr><td style="color:var(--mute);">None</td></tr>'}</tbody>
                    </table>
                </div>
            `;
    }

    html += `
            <div class="dbg-box">
                <span class="filter-sidebar-title">Raw JSON</span>
                <div class="tool-json-box dbg-raw-json">${escapeHtml(JSON.stringify(d, null, 2))}</div>
            </div>
        `;

    detailEl.innerHTML = html;

    // Collapsible tool calls
    detailEl.querySelectorAll(".dbg-toolcall-head").forEach(head => {
        head.addEventListener("click", () => {
            const isOpen = head.parentElement.classList.toggle("open");
            head.querySelector(".dbg-chevron")?.classList.toggle("open", isOpen);
            head.parentElement.querySelector(".dbg-toolcall-args")?.classList.toggle("hidden", !isOpen);
        });
    });
    refreshIcons();
}

document.getElementById("btn-refresh-debug")?.addEventListener("click", loadDebugData);
document.getElementById("btn-clear-logs")?.addEventListener("click", async () => {
    if (confirm("Clear live logs?")) {
        await fetch("/api/debug/logs", { method: "DELETE" });
        loadDebugData();
    }
});
document.getElementById("btn-debug-delete-session")?.addEventListener("click", async () => {
    if (!selectedDebugSessionId) return;
    if (confirm(`Delete debug session ${selectedDebugSessionId}?`)) {
        await fetch(`/api/debug/sessions/${encodeURIComponent(selectedDebugSessionId)}`, { method: "DELETE" });
        selectedDebugSessionId = null;
        activeDebugSessionData = null;
        setDebugView("logs");
        loadDebugData();
    }
});
