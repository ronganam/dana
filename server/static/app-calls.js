/* The web app — the Calls screen (static/app.js says how the app's scripts fit together).
 *
 * Call recordings: the list, the detail with the audio player, and the upload and reanalyze modals.
 */

// ══════════════════════════════════════════════════════════════════════
// 8. SCREEN 5: CALLS CONTROLLER
// ══════════════════════════════════════════════════════════════════════

const callsMasterList = document.getElementById("calls-master-list");
const callsDetailPane = document.getElementById("calls-detail-pane");
const callsSearchInput = document.getElementById("calls-search-input");
const btnRefreshCalls = document.getElementById("btn-refresh-calls");
const btnUploadCallTop = document.getElementById("btn-upload-call-top");
const btnUploadCallFab = document.getElementById("btn-upload-call-fab");
const callUploadModal = document.getElementById("call-upload-modal");
const callUploadClose = document.getElementById("call-upload-close");
const btnCallUploadCancel = document.getElementById("btn-call-upload-cancel");
const btnCallUploadSubmit = document.getElementById("btn-call-upload-submit");
const callReanalyzeModal = document.getElementById("call-reanalyze-modal");
const callReanalyzeClose = document.getElementById("call-reanalyze-close");
const btnReanalyzeCancel = document.getElementById("btn-reanalyze-cancel");
const btnReanalyzeSubmit = document.getElementById("btn-reanalyze-submit");

async function loadCalls() {
    try {
        const res = await fetch("/api/calls");
        if (res.ok) {
            calls = await res.json();
            renderCallsMasterList();
        }
    } catch (e) {
        console.error(e);
    }
}

btnRefreshCalls?.addEventListener("click", loadCalls);

function getContactAvatarColor(name) {
    let hash = 0;
    for (let i = 0; i < (name || "").length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
    const colors = ["#ff2d55", "#007aff", "#34c759", "#af52de", "#ff9500", "#5ac8fa"];
    return colors[Math.abs(hash) % colors.length];
}

function renderCallsMasterList() {
    if (!callsMasterList) return;
    const q = (callsSearchInput?.value || "").toLowerCase().trim();
    const filtered = calls.filter(c => {
        if (!q) return true;
        return (c.caller || "").toLowerCase().includes(q) ||
               (c.receiver || "").toLowerCase().includes(q) ||
               (c.title || "").toLowerCase().includes(q) ||
               (c.summary || "").toLowerCase().includes(q);
    });

    if (filtered.length === 0) {
        callsMasterList.innerHTML = `<span style="font-size:12px;color:var(--mute);padding:12px;">No calls found.</span>`;
        return;
    }

    callsMasterList.innerHTML = filtered.map(c => {
        const contact = c.caller === "Me" ? (c.receiver || "Unknown") : (c.caller || "Unknown");
        const color = getContactAvatarColor(contact);
        const isSelected = c.id === selectedCallId;
        const isPlaying = isCallAudioPlaying && currentPlayingCallId === c.id;

        return `
                <div class="call-compact-row ${isSelected ? "active" : ""}" data-id="${c.id}">
                    <div class="call-avatar" style="background:${color}25;color:${color};">
                        ${contact.charAt(0).toUpperCase()}
                    </div>
                    <div style="flex:1;overflow:hidden;display:flex;flex-direction:column;gap:2px;">
                        <div style="display:flex;align-items:center;justify-content:space-between;">
                            <strong style="font-size:13px;color:var(--ink);">${escapeHtml(contact)}</strong>
                            <span style="font-size:10.5px;color:var(--mute);">${formatTime(c.call_timestamp)}</span>
                        </div>
                        <span style="font-size:11.5px;color:var(--body);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                            ${escapeHtml(c.title || "Phone Call")}
                        </span>
                    </div>
                    <button class="icon-btn btn-mini-play" data-id="${c.id}" style="width:30px;height:30px;">
                        <i data-lucide="${isPlaying ? "pause" : "play"}" style="width:14px;height:14px;"></i>
                    </button>
                </div>
            `;
    }).join("");

    callsMasterList.querySelectorAll(".call-compact-row").forEach(row => {
        row.addEventListener("click", (e) => {
            if (e.target.closest(".btn-mini-play")) return;
            selectedCallId = row.getAttribute("data-id");
            renderCallsMasterList();
            renderCallDetail();
        });
    });

    callsMasterList.querySelectorAll(".btn-mini-play").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const cid = btn.getAttribute("data-id");
            toggleCallPlay(cid);
        });
    });

    if (!selectedCallId && filtered.length > 0 && window.innerWidth >= 768) {
        selectedCallId = filtered[0].id;
        renderCallsMasterList();
        renderCallDetail();
    }

    refreshIcons();
}

callsSearchInput?.addEventListener("input", renderCallsMasterList);

function toggleCallPlay(callId) {
    if (currentPlayingCallId === callId && isCallAudioPlaying) {
        callAudio.pause();
        isCallAudioPlaying = false;
    } else {
        callAudio.src = `/api/calls/${encodeURIComponent(callId)}/audio`;
        callAudio.play();
        isCallAudioPlaying = true;
        currentPlayingCallId = callId;
    }
    renderCallsMasterList();
    renderCallDetail();
}

callAudio.onended = () => {
    isCallAudioPlaying = false;
    renderCallsMasterList();
    renderCallDetail();
};

function renderCallDetail() {
    if (!callsDetailPane) return;
    const call = calls.find(x => x.id === selectedCallId);
    if (!call) {
        callsDetailPane.innerHTML = `
                <div class="pane-empty-state">
                    <i data-lucide="phone" style="width:40px;height:40px;opacity:0.4;"></i>
                    <span>Select a call to view recording details</span>
                </div>
            `;
        refreshIcons();
        return;
    }

    const contact = call.caller === "Me" ? (call.receiver || "Unknown") : (call.caller || "Unknown");
    const color = getContactAvatarColor(contact);
    const isPlaying = isCallAudioPlaying && currentPlayingCallId === call.id;

    callsDetailPane.innerHTML = `
            <div style="display:flex;align-items:center;gap:14px;margin-bottom:16px;">
                <div class="call-avatar large" style="background:${color}25;color:${color};">
                    ${contact.charAt(0).toUpperCase()}
                </div>
                <div style="flex:1;">
                    <h2 style="font-size:20px;font-weight:600;color:var(--ink);">${escapeHtml(contact)}</h2>
                    <span style="font-size:12px;color:var(--mute);">${formatTime(call.call_timestamp)} · ${formatDuration(call.duration_seconds)}</span>
                </div>
                <button class="icon-btn destructive" id="btn-delete-call" title="Delete Call">
                    <i data-lucide="trash-2"></i>
                </button>
            </div>

            <h3 style="font-size:16px;font-weight:600;color:var(--ink);margin-bottom:12px;">
                ${escapeHtml(call.title || "Phone Call")}
            </h3>

            <!-- Audio Player Bar -->
            <div class="audio-player-bar">
                <button class="icon-btn primary" id="btn-play-call" style="width:36px;height:36px;background:var(--ink);color:var(--on-primary);">
                    <i data-lucide="${isPlaying ? "pause" : "play"}" style="width:16px;height:16px;"></i>
                </button>
                <input type="range" class="audio-slider" id="call-audio-seek" min="0" max="100" value="0">
                <span id="call-audio-time" style="font-size:11px;font-family:var(--font-mono);color:var(--mute);">0:00</span>
            </div>

            <!-- AI Summary Block -->
            ${call.summary ? `
                <div class="ai-summary-box">
                    <div style="display:flex;align-items:center;justify-content:space-between;">
                        <span class="title">✦ AI Summary</span>
                        <button class="btn-secondary" id="btn-reanalyze-call" style="padding:3px 8px;font-size:11px;">
                            <i data-lucide="refresh-cw" style="width:12px;height:12px;"></i> Regenerate
                        </button>
                    </div>
                    <div class="markdown-body" style="font-size:13.5px;color:var(--body-strong);line-height:1.55;margin-top:6px;" ${isRtlText(call.summary) ? 'dir="rtl"' : ""}>
                        ${window.marked && window.DOMPurify ? DOMPurify.sanitize(marked.parse(call.summary)) : `<p>${escapeHtml(call.summary)}</p>`}
                    </div>
                </div>
            ` : ""}

            <!-- Transcript Block -->
            ${call.transcript ? `
                <div style="margin-top:16px;">
                    <div style="display:flex;align-items:center;justify-content:space-between;cursor:pointer;padding:6px 0;" id="call-toggle-transcript">
                        <strong style="font-size:13px;color:var(--body-strong);">Full Transcript</strong>
                        <i data-lucide="chevron-down" id="transcript-chevron" style="width:16px;height:16px;color:var(--mute);"></i>
                    </div>
                    <div class="tool-json-box hidden" id="call-transcript-content" style="margin-top:8px;max-height:300px;font-family:var(--font-inter);font-size:13px;line-height:1.6;" ${isRtlText(call.transcript) ? 'dir="rtl"' : ""}>
                        ${escapeHtml(call.transcript)}
                    </div>
                </div>
            ` : ""}
        `;

    // Bind audio controls
    document.getElementById("btn-play-call")?.addEventListener("click", () => toggleCallPlay(call.id));
    document.getElementById("btn-reanalyze-call")?.addEventListener("click", () => {
        reanalyzeTargetCall = call;
        document.getElementById("reanalyze-call-label").textContent = `Call with ${contact}`;
        callReanalyzeModal.classList.add("open");
    });
    document.getElementById("btn-delete-call")?.addEventListener("click", async () => {
        if (confirm("Delete this call recording permanently?")) {
            await fetch(`/api/calls/${encodeURIComponent(call.id)}`, { method: "DELETE" });
            selectedCallId = null;
            loadCalls();
        }
    });

    // Transcript accordion toggle
    document.getElementById("call-toggle-transcript")?.addEventListener("click", () => {
        const tr = document.getElementById("call-transcript-content");
        const ch = document.getElementById("transcript-chevron");
        if (tr) tr.classList.toggle("hidden");
        if (ch) ch.classList.toggle("open");
    });

    refreshIcons();
}

// Audio seek updates
callAudio.ontimeupdate = () => {
    const seek = document.getElementById("call-audio-seek");
    const timeEl = document.getElementById("call-audio-time");
    if (seek && callAudio.duration) {
        seek.value = (callAudio.currentTime / callAudio.duration) * 100;
    }
    if (timeEl) {
        timeEl.textContent = formatDuration(callAudio.currentTime);
    }
};

// Upload audio modal
btnUploadCallTop?.addEventListener("click", () => callUploadModal.classList.add("open"));
btnUploadCallFab?.addEventListener("click", () => callUploadModal.classList.add("open"));
callUploadClose?.addEventListener("click", () => callUploadModal.classList.remove("open"));
btnCallUploadCancel?.addEventListener("click", () => callUploadModal.classList.remove("open"));

btnCallUploadSubmit?.addEventListener("click", async () => {
    const fileInput = document.getElementById("call-file");
    if (!fileInput.files?.[0]) return;

    const fd = new FormData();
    fd.append("audio", fileInput.files[0]);
    fd.append("caller", document.getElementById("call-caller").value.trim() || "Unknown");
    fd.append("receiver", document.getElementById("call-receiver").value.trim() || "Me");
    fd.append("instructions", document.getElementById("call-instructions").value.trim());

    btnCallUploadSubmit.disabled = true;
    btnCallUploadSubmit.textContent = "Uploading...";

    try {
        const res = await fetch("/api/calls/upload", { method: "POST", body: fd });
        if (res.ok) {
            callUploadModal.classList.remove("open");
            loadCalls();
        }
    } catch (e) {
        console.error(e);
    } finally {
        btnCallUploadSubmit.disabled = false;
        btnCallUploadSubmit.textContent = "Upload & Analyze";
    }
});

// Reanalyze modal
callReanalyzeClose?.addEventListener("click", () => callReanalyzeModal.classList.remove("open"));
btnReanalyzeCancel?.addEventListener("click", () => callReanalyzeModal.classList.remove("open"));

btnReanalyzeSubmit?.addEventListener("click", async () => {
    if (!reanalyzeTargetCall) return;
    const inst = document.getElementById("reanalyze-instructions").value.trim();
    btnReanalyzeSubmit.disabled = true;
    btnReanalyzeSubmit.textContent = "Regenerating...";

    try {
        const res = await fetch(`/api/calls/${encodeURIComponent(reanalyzeTargetCall.id)}/reanalyze`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ instructions: inst || null })
        });
        if (res.ok) {
            callReanalyzeModal.classList.remove("open");
            loadCalls();
        }
    } catch (e) {
        console.error(e);
    } finally {
        btnReanalyzeSubmit.disabled = false;
        btnReanalyzeSubmit.textContent = "Regenerate";
    }
});
