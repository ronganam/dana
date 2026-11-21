/* Calls — the phone addon's second page (Dana.panel, static/dana.js): call recordings, the list, the
 * detail with the player, the summary and the transcript, and the upload and regenerate modals. Opened
 * with a call's id (api.open("calls", id): a person's timeline, the Usage screen) it shows that call.
 *
 *   GET  /api/calls · GET /api/calls/{id}/audio · DELETE /api/calls/{id}
 *   POST /api/calls/upload (form: audio, caller, receiver, instructions)
 *   POST /api/calls/{id}/reanalyze {instructions}
 *
 * The pure part is calls-core.js (DanaCallsCore; tests/test_web_calls.mjs).
 */
(() => {
  "use strict";
  let A = null;
  let C = null;
  let root = null;
  const S = { calls: [], selected: null, playing: null };
  const audio = new Audio();
  const $ = (id) => root.querySelector(`#${id}`);
  const esc = (s) => A.esc(s);
  const when = (iso) => {
    const d = new Date(iso || "");
    return isNaN(d.getTime()) ? String(iso || "") : d.toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };

  const TEMPLATE = `
    <header class="top-bar">
      <span class="top-bar-title">Call Assistant</span>
      <div class="top-bar-actions">
        <button class="icon-btn" id="btn-refresh-calls" title="Refresh"><i data-lucide="refresh-cw"></i></button>
        <button class="btn-primary" data-open="call-upload-modal"><i data-lucide="upload" style="width:16px;height:16px;"></i><span>Upload Audio</span></button>
      </div>
    </header>
    <div class="two-pane-container" id="calls-two-pane">
      <div class="pane-master">
        <div class="pane-master-header"><input type="text" class="form-control" id="calls-search-input" placeholder="Search calls, transcripts..."></div>
        <div class="pane-master-list" id="calls-master-list"></div>
      </div>
      <div class="pane-detail" id="calls-detail-pane"></div>
    </div>
    <button class="fab-btn" data-open="call-upload-modal" title="Upload Audio"><i data-lucide="upload"></i></button>

    <div class="modal-overlay" id="call-upload-modal">
      <div class="modal-sheet">
        <div class="modal-header"><h3 class="modal-title">Upload Call Audio</h3><button class="icon-btn" data-close="call-upload-modal"><i data-lucide="x"></i></button></div>
        <form id="call-upload-form" class="modal-body">
          <div class="form-group"><label class="form-label" for="call-file">Audio File</label><input type="file" class="form-control" id="call-file" accept="audio/*" required></div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
            <div class="form-group"><label class="form-label" for="call-caller">Caller</label><input type="text" class="form-control" id="call-caller" placeholder="Name or number"></div>
            <div class="form-group"><label class="form-label" for="call-receiver">Receiver</label><input type="text" class="form-control" id="call-receiver" placeholder="Me"></div>
          </div>
          <div class="form-group"><label class="form-label" for="call-instructions">Custom Instructions (Optional)</label>
            <textarea class="form-control" id="call-instructions" rows="2" placeholder="e.g. summarize in Hebrew, focus on action items..."></textarea></div>
        </form>
        <div class="modal-footer"><button class="btn-secondary" data-close="call-upload-modal">Cancel</button><button class="btn-primary" id="btn-call-upload-submit">Upload & Analyze</button></div>
      </div>
    </div>

    <div class="modal-overlay" id="call-reanalyze-modal">
      <div class="modal-sheet">
        <div class="modal-header"><h3 class="modal-title">Regenerate Call Summary</h3><button class="icon-btn" data-close="call-reanalyze-modal"><i data-lucide="x"></i></button></div>
        <div class="modal-body">
          <p id="reanalyze-call-label" style="font-size:13px;color:var(--body);"></p>
          <div class="form-group"><label class="form-label" for="reanalyze-instructions">Custom Instructions (Optional)</label>
            <textarea class="form-control" id="reanalyze-instructions" rows="3" placeholder="e.g. focus on dates and prices..."></textarea></div>
        </div>
        <div class="modal-footer"><button class="btn-secondary" data-close="call-reanalyze-modal">Cancel</button><button class="btn-primary" id="btn-reanalyze-submit">Regenerate</button></div>
      </div>
    </div>`;

  async function load() {
    const r = await A.json("GET", "/api/calls");
    if (r.status === 200 && Array.isArray(r.body)) S.calls = r.body;
    render();
  }

  function render() { renderList(); renderDetail(); }

  function avatar(contact, cls = "") {
    const color = C.avatarColor(contact);
    return `<div class="call-avatar ${cls}" style="background:var(--${color}-tint);color:var(--${color}-text);">${esc(contact.charAt(0).toUpperCase())}</div>`;
  }

  function renderList() {
    const list = $("calls-master-list");
    const shown = C.filterCalls(S.calls, $("calls-search-input").value);
    if (!shown.length) { list.innerHTML = `<span style="font-size:12px;color:var(--mute);padding:12px;">No calls found.</span>`; return; }
    if (!S.selected && window.innerWidth >= 768) S.selected = shown[0].id;
    list.innerHTML = shown.map((c) => {
      const contact = C.contactOf(c);
      return `
        <div class="call-compact-row dn-row ${c.id === S.selected ? "active" : ""}" data-id="${esc(c.id)}">
          ${avatar(contact)}
          <div style="flex:1;overflow:hidden;display:flex;flex-direction:column;gap:2px;">
            <div style="display:flex;align-items:center;justify-content:space-between;">
              <strong style="font-size:13px;color:var(--ink);">${esc(contact)}</strong>
              <span style="font-size:10.5px;color:var(--mute);">${esc(when(c.call_timestamp))}</span>
            </div>
            <span style="font-size:11.5px;color:var(--body);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(c.title || "Phone Call")}</span>
          </div>
          <button class="icon-btn" data-play="${esc(c.id)}" style="width:30px;height:30px;"><i data-lucide="${S.playing === c.id ? "pause" : "play"}" style="width:14px;height:14px;"></i></button>
        </div>`;
    }).join("");
    A.icons();
  }

  function renderDetail() {
    const pane = $("calls-detail-pane");
    const call = S.calls.find((c) => c.id === S.selected);
    if (!call) {
      pane.innerHTML = `<div class="pane-empty-state"><i data-lucide="phone" style="width:40px;height:40px;opacity:0.4;"></i><span>Select a call to view recording details</span></div>`;
      A.icons();
      return;
    }
    const contact = C.contactOf(call);
    const dir = (text) => (C.isRtl(text) ? ' dir="rtl"' : "");
    const summary = window.marked && window.DOMPurify ? window.DOMPurify.sanitize(window.marked.parse(call.summary || "")) : `<p>${esc(call.summary)}</p>`;
    pane.innerHTML = `
      <div style="display:flex;align-items:center;gap:14px;margin-bottom:16px;">
        ${avatar(contact, "large")}
        <div style="flex:1;">
          <h2 style="font-size:20px;font-weight:600;color:var(--ink);">${esc(contact)}</h2>
          <span style="font-size:12px;color:var(--mute);">${esc(when(call.call_timestamp))} · ${C.duration(call.duration_seconds)}</span>
        </div>
        <button class="icon-btn destructive" id="btn-delete-call" title="Delete Call"><i data-lucide="trash-2"></i></button>
      </div>
      <h3 style="font-size:16px;font-weight:600;color:var(--ink);margin-bottom:12px;">${esc(call.title || "Phone Call")}</h3>
      <div class="audio-player-bar">
        <button class="icon-btn primary" data-play="${esc(call.id)}" style="width:36px;height:36px;background:var(--ink);color:var(--on-primary);">
          <i data-lucide="${S.playing === call.id ? "pause" : "play"}" style="width:16px;height:16px;"></i>
        </button>
        <input type="range" class="audio-slider" id="call-audio-seek" min="0" max="100" value="0">
        <span id="call-audio-time" style="font-size:11px;font-family:var(--font-mono);color:var(--mute);">0:00</span>
      </div>
      ${call.summary ? `
        <div class="ai-summary-box">
          <div style="display:flex;align-items:center;justify-content:space-between;">
            <span class="title">✦ AI Summary</span>
            <button class="btn-secondary" id="btn-reanalyze-call" style="padding:3px 8px;font-size:11px;"><i data-lucide="refresh-cw" style="width:12px;height:12px;"></i> Regenerate</button>
          </div>
          <div class="markdown-body" style="font-size:13.5px;color:var(--body-strong);line-height:1.55;margin-top:6px;"${dir(call.summary)}>${summary}</div>
        </div>` : ""}
      ${call.transcript ? `
        <div style="margin-top:16px;">
          <div style="display:flex;align-items:center;justify-content:space-between;cursor:pointer;padding:6px 0;" id="call-toggle-transcript">
            <strong style="font-size:13px;color:var(--body-strong);">Full Transcript</strong>
            <i data-lucide="chevron-down" style="width:16px;height:16px;color:var(--mute);"></i>
          </div>
          <div class="tool-json-box hidden" id="call-transcript-content" style="margin-top:8px;max-height:300px;font-family:var(--font-inter);font-size:13px;line-height:1.6;"${dir(call.transcript)}>${esc(call.transcript)}</div>
        </div>` : ""}`;
    A.icons();
  }

  function togglePlay(id) {
    if (S.playing === id) { audio.pause(); S.playing = null; }
    else { audio.src = `/api/calls/${encodeURIComponent(id)}/audio`; audio.play().catch(() => { S.playing = null; render(); }); S.playing = id; }
    render();
  }

  audio.onended = () => { S.playing = null; if (root) render(); };
  audio.ontimeupdate = () => {
    if (!root) return;
    const seek = $("call-audio-seek");
    const time = $("call-audio-time");
    if (seek && audio.duration) seek.value = (audio.currentTime / audio.duration) * 100;
    if (time) time.textContent = C.duration(audio.currentTime);
  };

  async function upload() {
    const file = $("call-file").files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("audio", file);
    fd.append("caller", $("call-caller").value.trim() || "Unknown");
    fd.append("receiver", $("call-receiver").value.trim() || "Me");
    fd.append("instructions", $("call-instructions").value.trim());
    const btn = $("btn-call-upload-submit");
    btn.disabled = true;
    btn.textContent = "Uploading...";
    try {
      const r = await A.fetch("/api/calls/upload", { method: "POST", body: fd });
      if (r.ok) { $("call-upload-modal").classList.remove("open"); load(); } else A.toast("The upload failed");
    } catch (_) {
      A.toast("The upload failed");
    } finally {
      btn.disabled = false;
      btn.textContent = "Upload & Analyze";
    }
  }

  async function reanalyze() {
    const call = S.calls.find((c) => c.id === S.selected);
    if (!call) return;
    const btn = $("btn-reanalyze-submit");
    btn.disabled = true;
    btn.textContent = "Regenerating...";
    const r = await A.json("POST", `/api/calls/${encodeURIComponent(call.id)}/reanalyze`, { instructions: $("reanalyze-instructions").value.trim() || null });
    btn.disabled = false;
    btn.textContent = "Regenerate";
    if (r.status >= 200 && r.status < 300) { $("call-reanalyze-modal").classList.remove("open"); load(); } else A.toast("Could not regenerate the summary");
  }

  function wire() {
    root.addEventListener("click", async (ev) => {
      const t = ev.target;
      const close = t.closest("[data-close]");
      if (close) { $(close.dataset.close).classList.remove("open"); return; }
      const open = t.closest("[data-open]");
      if (open) { $(open.dataset.open).classList.add("open"); return; }
      const play = t.closest("[data-play]");
      if (play) { togglePlay(play.dataset.play); return; }
      const row = t.closest(".call-compact-row");
      if (row) { S.selected = row.dataset.id; render(); return; }
      if (t.closest("#btn-refresh-calls")) { load(); return; }
      if (t.closest("#btn-call-upload-submit")) { upload(); return; }
      if (t.closest("#btn-reanalyze-submit")) { reanalyze(); return; }
      if (t.closest("#btn-reanalyze-call")) {
        const call = S.calls.find((c) => c.id === S.selected);
        $("reanalyze-call-label").textContent = call ? `Call with ${C.contactOf(call)}` : "";
        $("call-reanalyze-modal").classList.add("open");
        return;
      }
      if (t.closest("#btn-delete-call")) {
        if (!confirm("Delete this call recording permanently?")) return;
        await A.json("DELETE", `/api/calls/${encodeURIComponent(S.selected)}`);
        S.selected = null;
        load();
        return;
      }
      if (t.closest("#call-toggle-transcript")) $("call-transcript-content")?.classList.toggle("hidden");
    });
    $("calls-search-input").addEventListener("input", renderList);
  }

  Dana.panel({
    scripts: ["calls-core.js"],
    start(api) { A = api; C = window.DanaCallsCore; },
    async mount(el, api, callId) {
      if (!root) { root = el; el.innerHTML = TEMPLATE; wire(); }
      if (callId) S.selected = String(callId);
      await load();
    },
  });
})();
