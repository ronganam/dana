/* Weekly review + the Today strip (the morning briefing and the weekly-review banner).
 *
 *   GET  /api/review/latest · POST /api/review/generate · POST /api/review/{id}/apply | /dismiss
 *   GET  /api/briefing/today
 *   POST /api/chat/context {kind: "review"}
 * Exposes window.DanaReview = {show, hide} and window.DanaToday = {show(el)}. The Today strip is a core
 * piece: an addon page hosts it with api.piece("today", el) (static/dana.js; the tasks page does).
 */
(() => {
  "use strict";

  const SECTIONS = [
    { why: "overdue", label: "Overdue" },
    { why: "snoozed_often", label: "Snoozed again and again" },
    { why: "stale", label: "Untouched for weeks" },
    { why: "old_suggestion", label: "Old suggestions" },
    { why: "drifting", label: "People to reach out to" },
  ];
  const LABELS = {
    complete: "Mark done", reschedule: "Reschedule", delete: "Delete", move_group: "Move to group", keep: "Keep as is",
    accept: "Accept", dismiss: "Dismiss", reach_out: "Remind me to reach out",
  };
  const ACTIONS = { task: ["reschedule", "complete", "move_group", "delete", "keep"], suggestion: ["accept", "dismiss", "keep"], person: ["reach_out", "keep"] };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };
  const rtl = (s) => /[֐-׿]/.test(String(s || "")) ? ' dir="auto"' : "";
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (_) { return null; } };

  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.body !== undefined) { init.method = "POST"; init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    const r = await fetch(path, init);
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
    return data;
  }

  // ======================= Weekly review =======================
  const R = { review: null, edits: {}, busy: false, visible: false };
  let $r = {};
  let toastTimer = null;

  function toast(msg) {
    $r.toast.textContent = msg;
    $r.toast.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $r.toast.classList.add("hidden"), 3500);
  }

  const item = (it) => ({ ...it, ...(R.edits[it.ref] || {}) });
  const today = () => new Date().toISOString().slice(0, 10);

  function itemHtml(raw) {
    const it = item(raw);
    const acts = ACTIONS[it.type] || ["keep"];
    const dateCtl = ["reschedule", "reach_out"].includes(it.action)
      ? `<input type="date" data-f="due_date" value="${esc(it.due_date || "")}" min="${today()}">` : "";
    const groupCtl = it.action === "move_group"
      ? `<select data-f="group">${(R.review.groups || []).map((g) => `<option ${g === it.group ? "selected" : ""}>${esc(g)}</option>`).join("")}</select>` : "";
    return `<div class="rv-item ${it.enabled ? "" : "off"}" data-ref="${esc(it.ref)}">
      <input type="checkbox" data-f="enabled" ${it.enabled ? "checked" : ""} aria-label="Apply this change">
      <span class="t"${rtl(it.title)}>${esc(it.title)}</span>
      <span class="ctl"><select data-f="action">${acts.map((a) => `<option value="${a}" ${a === it.action ? "selected" : ""}>${LABELS[a]}</option>`).join("")}</select>${dateCtl}${groupCtl}</span>
      <span class="f">${esc((it.facts || []).join(" · "))}</span>
      ${it.reason ? `<span class="r"${rtl(it.reason)}>${esc(it.reason)}</span>` : ""}
    </div>`;
  }

  function render() {
    const rv = R.review;
    if (!rv) {
      $r.kicker.textContent = "";
      $r.root.innerHTML = `<div class="rv-wrap"><div class="rv-done"><strong>No review yet</strong>
        Dana makes one every Saturday evening.<br><br><button class="btn-primary" data-generate>Make one now</button></div></div>`;
      return;
    }
    $r.kicker.textContent = `${rv.week} · ${rv.status}`;
    if (rv.status !== "pending") {
      const res = rv.result;
      $r.root.innerHTML = `<div class="rv-wrap"><div class="rv-done"><strong>${rv.status === "applied" ? "Review applied ✓" : "Review dismissed"}</strong>
        ${esc(rv.headline)}
        ${res ? `<div class="rv-result">${res.applied} change(s) made${res.failed ? `, <span class="bad">${res.failed} failed</span>` : ""}.
          ${(res.items || []).filter((x) => !x.ok).map((x) => `<div class="bad">${esc(x.ref)}: ${esc(x.error)}</div>`).join("")}</div>` : ""}
        <br><button class="btn-secondary" data-generate>Make a new one</button></div></div>`;
      return;
    }
    const groups = SECTIONS.map((s) => ({ ...s, items: rv.items.filter((i) => i.why === s.why) })).filter((s) => s.items.length);
    const on = rv.items.map(item).filter((i) => i.enabled && i.action !== "keep").length;
    const st = rv.stats || {};
    const nw = rv.next_week;
    $r.root.innerHTML = `<div class="rv-wrap">
      <div class="rv-hero"><h2>${esc(rv.headline)}</h2>
        <ul>${(rv.summary || []).map((s) => `<li${rtl(s)}>${esc(s)}</li>`).join("")}
          ${nw && nw.busiest ? `<li>Next week: ${nw.events} events, busiest ${esc(nw.busiest.weekday)} (${nw.busiest.count})</li>` : ""}
          ${st.done_titles && st.done_titles.length ? `<li${rtl(st.done_titles.join(" "))}>Done: ${st.done_titles.slice(0, 5).map(esc).join(", ")}${st.done > 5 ? "…" : ""}</li>` : ""}</ul></div>
      ${rv.items.length ? groups.map((g) => `<div class="rv-section">${g.label} <span>${g.items.length}</span></div>${g.items.map(itemHtml).join("")}`).join("")
        : `<div class="rv-done"><strong>Nothing to decide</strong>Your list is in good shape.</div>`}
    </div>
    ${rv.items.length ? `<div class="rv-bar"><span class="grow">${on} change${on === 1 ? "" : "s"} selected</span>
      <button class="btn-secondary" data-dismiss>Skip this week</button>
      <button class="btn-primary" data-apply ${R.busy || !on ? "disabled" : ""}>${R.busy ? "Applying…" : `Apply ${on}`}</button></div>` : ""}`;
    icons();
  }

  async function load() {
    try {
      R.review = (await api("/api/review/latest")).review;
      R.edits = {};
    } catch (e) { toast(e.message); }
    render();
  }

  async function generate() {
    $r.root.innerHTML = `<div class="rv-wrap"><div class="rv-done"><strong>Looking at your week…</strong>This takes a few seconds.</div></div>`;
    try {
      R.review = (await api("/api/review/generate", { body: {} })).review;
      R.edits = {};
    } catch (e) { toast(e.message); }
    render();
  }

  async function apply() {
    R.busy = true;
    render();
    const items = R.review.items.map((i) => {
      const x = item(i);
      return { ref: x.ref, action: x.action, due_date: x.due_date, group: x.group, enabled: !!x.enabled };
    });
    try {
      const res = await api(`/api/review/${R.review.id}/apply`, { body: { items } });
      toast(`${res.applied} change(s) made${res.failed ? `, ${res.failed} failed` : ""}`);
      window.DanaToday?.show();
    } catch (e) { toast(e.message); }
    R.busy = false;
    await load();
  }

  function initReview() {
    $r = { root: document.getElementById("rv-root"), kicker: document.getElementById("rv-kicker"), toast: document.getElementById("rv-toast") };
    if (!$r.root) return;
    $r.root.addEventListener("change", (ev) => {
      const row = ev.target.closest(".rv-item");
      const f = ev.target.dataset.f;
      if (!row || !f) return;
      const e = (R.edits[row.dataset.ref] = R.edits[row.dataset.ref] || {});
      e[f] = f === "enabled" ? ev.target.checked : ev.target.value;
      if (f === "action") {
        e.enabled = ev.target.value !== "keep";
        const base = R.review.items.find((i) => i.ref === row.dataset.ref);
        if (["reschedule", "reach_out"].includes(e.action) && !(e.due_date || base.due_date)) {
          const d = new Date(); d.setDate(d.getDate() + ((7 - d.getDay()) % 7 || 7)); e.due_date = d.toISOString().slice(0, 10);
        }
        if (e.action === "move_group" && !e.group) e.group = (R.review.groups || [])[0];
      }
      render();
    });
    $r.root.addEventListener("click", async (ev) => {
      if (ev.target.closest("[data-generate]")) generate();
      else if (ev.target.closest("[data-apply]")) apply();
      else if (ev.target.closest("[data-dismiss]")) {
        try { await api(`/api/review/${R.review.id}/dismiss`, { body: {} }); } catch (e) { toast(e.message); }
        load();
        window.DanaToday?.show();
      }
    });
    document.getElementById("rv-back")?.addEventListener("click", () => window.danaBack?.());
    document.getElementById("rv-generate")?.addEventListener("click", () => { if (confirm("Make a fresh review now? Unapplied choices are replaced.")) generate(); });
    document.getElementById("rv-ask")?.addEventListener("click", async () => {
      if (!R.review) return;
      try { window.danaOpenSession?.((await api("/api/chat/context", { body: { kind: "review", id: R.review.id } })).session_id); }
      catch (e) { toast(e.message); }
    });
    if (location.hash === "#review") setTimeout(() => window.danaSwitchTab?.("review"), 0);
  }

  window.DanaReview = { show() { R.visible = true; load(); }, hide() { R.visible = false; } };

  // ======================= Today strip (a core piece an addon page hosts) =======================
  let todayHost = null;

  /** Draws the strip into `el` (and keeps drawing there); no `el`: draws again where it was. */
  async function showToday(el) {
    if (el && el !== todayHost) {
      todayHost = el;
      el.classList.add("today-strip");
      el.addEventListener("click", (ev) => {
        const h = ev.target.closest("[data-hide-brief]");
        if (h) { store("today.hidden", h.dataset.hideBrief); showToday(); return; }
        if (ev.target.closest("[data-open-review]")) window.danaSwitchTab?.("review");
      });
    }
    const host = todayHost;
    if (!host) return;
    const [b, r] = await Promise.all([api("/api/briefing/today").catch(() => null), api("/api/review/latest").catch(() => null)]);
    const cards = [];
    const brief = b && b.briefing;
    const hour = new Date().getHours();
    if (brief && hour < 14 && store("today.hidden") !== brief.date) {
      cards.push(`<div class="today-card"><div class="main"><div class="dn-eyebrow">This morning</div><div class="head"${rtl(brief.headline)}>${esc(brief.headline)}</div>
        <div class="pts">${brief.points.map((p) => `<span${rtl(p)}>${esc(p)}</span>`).join("")}</div></div>
        <div class="acts"><button class="dn-overflow" data-hide-brief="${esc(brief.date)}" title="Hide for today" aria-label="Hide the briefing for today"><i data-lucide="x"></i></button></div></div>`);
    }
    const rv = r && r.review;
    if (rv && rv.status === "pending" && rv.items.length) {
      cards.push(`<div class="today-card"><div class="main"><div class="dn-eyebrow">Weekly review</div><div class="head">Your week is ready to review</div>
        <div class="pts"><span${rtl(rv.headline)}>${esc(rv.headline)}</span><span>${rv.items.length} items to decide</span></div></div>
        <div class="acts"><button type="button" class="dn-pill" data-open-review>Review</button></div></div>`);
    }
    host.innerHTML = cards.join("");
    host.classList.toggle("hidden", !cards.length);
    icons();
  }

  window.DanaToday = { show: showToday };

  function init() { initReview(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
