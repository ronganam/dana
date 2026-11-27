/* Watches — the watches addon's page (Dana.panel, static/dana.js). Two views under one header:
 *
 *   Watches: the list (Active reminders, Active watches, Paused, Done and expired; search; a "New watch" box that
 *            takes plain words) and the editor of one watch (title, window, once / every time, triggers with their
 *            field conditions, keywords, hours and AI check, actions, deadline actions, evidence for reminders,
 *            "Change in words", Save with the errors on their fields, a live dry run, the history of fires, the recent
 *            checks: each event it looked at and why it did or did not fire, all / near misses / fires).
 *   Live:    the pipeline drawn as it works: sources → Filter → AI check → Fire → actions, a dot per step of the
 *            stream, the counters on the nodes and a feed of the latest steps in plain words.
 *
 *   GET    /api/watches · GET /api/watches/{id} · GET /api/watches/{id}/trace · POST /api/watches {text} · PATCH /api/watches/{id}
 *          {spec} | {status} | {change} · DELETE /api/watches/{id} → {undo_id} · POST /api/undo/{id}
 *   POST   /api/watches/dry-run {spec, days} · GET /api/watches/catalog
 *   GET    /api/watches/pipeline · GET /api/watches/pipeline/stream (SSE, one PipelineEvent per message)
 *
 * The pure part is watches-core.js (DanaWatchesCore; tests/test_web_watches.mjs).
 */
(() => {
  "use strict";
  let A = null;           // the page's api
  let C = null;           // DanaWatchesCore
  let root = null;        // the page's <section>
  const S = {
    view: "list",         // "list" | "live"
    watches: [], loaded: false, loadError: "",
    catalog: { event_types: [], ops: [], action_types: [], tools: [] },
    q: "",
    selected: null,       // the watch in the editor
    draft: null, orig: null,
    errors: [],           // [{path, message}] shown on their fields
    fires: null,          // the selected watch's fires (GET /api/watches/{id})
    trace: null,          // its recent checks (GET /api/watches/{id}/trace)
    traceFilter: "all",   // "all" | "near" | "fired"
    traceUnstored: 0,
    dry: {},              // trigger id → dry-run result
    dryState: "",         // "" | "busy" | "error"
    openSamples: new Set(),
    saving: false, changing: false, changeError: "",
    creating: "", createError: "",
  };
  const $ = (id) => root.querySelector(`#${id}`);
  const esc = (s) => A.esc(s == null ? "" : String(s));
  const icon = (name, cls = "") => `<i data-lucide="${name}"${cls ? ` class="${cls}"` : ""} aria-hidden="true"></i>`;
  const narrow = () => window.matchMedia("(max-width: 900px)").matches;

  const TEMPLATE = `
    <header class="top-bar wch-top">
      <div class="ibx-titles">
        <span class="top-bar-title">Watches</span>
        <span class="ibx-kicker" id="wch-kicker" aria-live="polite"></span>
      </div>
      <div class="top-bar-actions">
        <div class="wch-switch" role="tablist" aria-label="View">
          <button type="button" role="tab" data-view="list" id="wch-tab-list">${icon("list")}<span>Watches</span></button>
          <button type="button" role="tab" data-view="live" id="wch-tab-live"><span class="wch-live-dot" aria-hidden="true"></span><span>Live</span></button>
        </div>
      </div>
    </header>
    <div class="ibx wch-list-view" id="wch-list-view">
      <div class="ibx-list-pane">
        <div class="ibx-toolbar wch-toolbar">
          <form class="wch-new" id="wch-new" autocomplete="off">
            <span class="wch-new-ico">${icon("plus")}</span>
            <textarea id="wch-new-text" rows="1" dir="auto" placeholder="Watch for something, or remind me…" aria-label="New watch, in your words"></textarea>
            <button type="submit" class="dn-pill small" id="wch-new-go">Add</button>
          </form>
          <div class="wch-new-err hidden" id="wch-new-err" role="alert"></div>
          <label class="ibx-search">${icon("search")}<input id="wch-q" type="search" placeholder="Search watches" aria-label="Search watches"></label>
        </div>
        <div class="ibx-scroll" id="wch-list"></div>
      </div>
      <div class="ibx-detail-pane wch-detail" id="wch-detail"></div>
    </div>
    <div class="wch-live hidden" id="wch-live-view">
      <div class="wch-live-head">
        <span class="wch-live-state" id="wch-live-state"><span class="wch-live-dot" aria-hidden="true"></span><span id="wch-live-status">Connecting…</span></span>
        <span class="dn-eyebrow" id="wch-live-since"></span>
      </div>
      <div class="wch-live-main">
        <div class="wch-stage" id="wch-stage" role="img" aria-label="The watch pipeline, live">
          <svg class="wch-svg" id="wch-svg" aria-hidden="true"><g id="wch-edges"></g><g id="wch-hot"></g><g id="wch-dots"></g></svg>
          <div class="wch-nodes" id="wch-nodes"></div>
        </div>
        <aside class="wch-feed" aria-label="Latest steps">
          <div class="wch-feed-head">
            <span class="dn-eyebrow">Latest</span>
            <div class="wch-feed-filter" role="group" aria-label="Show">
              <button type="button" class="chip active" data-feed="all">Everything</button>
              <button type="button" class="chip" data-feed="matches">Matches</button>
            </div>
          </div>
          <ol class="wch-feed-list" id="wch-feed" aria-live="polite"></ol>
        </aside>
      </div>
    </div>
    <div class="ibx-toast hidden" id="wch-toast" role="status" aria-live="polite"></div>`;

  // =====================================================================================================
  // data
  // =====================================================================================================

  async function loadList() {
    const r = await A.json("GET", "/api/watches");
    if (r.status === 200 && r.body && Array.isArray(r.body.watches)) {
      S.watches = r.body.watches;
      S.loadError = "";
    } else if (!S.loaded) {
      S.loadError = r.status === 403 ? "Watches isn't connected for you." : "Couldn't load your watches.";
    }
    S.loaded = true;
    renderKicker();
    renderList();
    if (S.selected && !S.watches.some((w) => w.id === S.selected)) select(null);
  }

  async function loadCatalog() {
    const r = await A.json("GET", "/api/watches/catalog");
    if (r.status === 200 && r.body) S.catalog = Object.assign({ event_types: [], ops: [], action_types: [], tools: [] }, r.body);
  }

  async function loadFires(id) {
    const r = await A.json("GET", `/api/watches/${encodeURIComponent(id)}`);
    if (S.selected !== id) return;
    if (r.status === 200 && r.body) {
      S.fires = r.body.fires || [];
      if (r.body.watch) replaceWatch(r.body.watch, { keepDraft: true });
    } else S.fires = [];
    renderHistory();
  }

  async function loadTrace(id) {
    const r = await A.json("GET", `/api/watches/${encodeURIComponent(id)}/trace${C.traceQuery(S.traceFilter)}`);
    if (S.selected !== id) return;
    S.trace = r.status === 200 && r.body ? r.body.trace || [] : [];
    S.traceUnstored = r.status === 200 && r.body ? Number(r.body.unstored) || 0 : 0;
    renderTrace();
  }

  const watchOf = (id) => S.watches.find((w) => w.id === id);
  function replaceWatch(w, { keepDraft = false } = {}) {
    const i = S.watches.findIndex((x) => x.id === w.id);
    if (i >= 0) S.watches[i] = w; else S.watches.unshift(w);
    if (S.selected === w.id && !keepDraft) { S.draft = C.clone(w.spec); S.orig = C.clone(w.spec); }
    if (S.selected === w.id && keepDraft && !dirty()) { S.draft = C.clone(w.spec); S.orig = C.clone(w.spec); }
  }
  const dirty = () => Boolean(S.draft && S.orig && !C.same(S.draft, S.orig));

  // =====================================================================================================
  // the list
  // =====================================================================================================

  function renderKicker() {
    const k = $("wch-kicker");
    if (k) k.textContent = S.loaded ? C.kicker(S.watches) : "";
  }

  function chipHtml(c) {
    return `<span class="wch-chip hue-${esc(c.hue)}" title="${esc(c.title)}">${icon(c.icon)}<span dir="auto">${esc(c.label)}</span></span>`
      + (c.ai ? `<span class="wch-chip hue-plum" title="A small model checks each match">✦ AI check</span>` : "");
  }

  function rowHtml(w) {
    const now = new Date();
    const trig = C.triggerChips(w.spec, now).map(chipHtml).join("");
    const acts = C.actionChips(w.spec).map((a) => `<span class="wch-chip quiet${a.ai ? " hue-plum" : ""}" title="${esc(a.title)}">${esc(a.label)}</span>`).join("");
    const sel = w.id === S.selected;
    return `
      <div class="wch-row dn-row${sel ? " selected" : ""}${w.status !== "active" ? " is-quiet" : ""}" data-id="${esc(w.id)}">
        <button type="button" class="wch-row-main" data-open="${esc(w.id)}"${sel ? ' aria-current="true"' : ""}>
          <span class="wch-dot hue-${C.rowHue(w)}" aria-hidden="true"></span>
          <span class="wch-row-text">
            <span class="wch-row-line"><span class="wch-row-title" dir="auto">${esc(w.title || (w.spec && w.spec.title) || "Untitled")}</span><span class="wch-row-end">${esc(C.rowStatus(w, now))}</span></span>
            ${w.request ? `<span class="wch-row-said" dir="auto">${esc(w.request)}</span>` : ""}
            <span class="wch-chips">${trig}${acts ? `<span class="wch-then" aria-label="then">→</span>${acts}` : ""}</span>
          </span>
        </button>
        <button type="button" class="dn-overflow" data-menu="${esc(w.id)}" aria-haspopup="menu" aria-expanded="false" aria-label="More for ${esc(w.title || "this watch")}">${icon("more-horizontal")}</button>
      </div>`;
  }

  const EXAMPLES = [
    "Tell me when a transfer of about 2,000 ₪ from a person arrives",
    "Remind me at 20:30 to call Maya, and 30 minutes before",
    "When Gal sends a date on WhatsApp, put it in my calendar",
  ];

  function renderList() {
    const list = $("wch-list");
    if (!list) return;
    const pending = S.creating ? `
      <div class="wch-row wch-pending dn-shimmer" aria-live="polite">
        <span class="wch-row-main"><span class="wch-dot hue-ember" aria-hidden="true"></span>
        <span class="wch-row-text"><span class="wch-row-title" dir="auto">${esc(S.creating)}</span><span class="wch-row-said">Building the watch…</span></span></span>
      </div>` : "";
    if (!S.loaded) { list.innerHTML = `<div class="wch-skel dn-shimmer"></div><div class="wch-skel dn-shimmer"></div><div class="wch-skel dn-shimmer"></div>`; return; }
    if (S.loadError) { list.innerHTML = `<div class="ibx-empty">${icon("eye-off")}<strong>${esc(S.loadError)}</strong></div>`; A.icons(); return; }
    const groups = C.groupWatches(S.watches, S.q);
    if (!groups.length && !S.creating) {
      list.innerHTML = S.q
        ? `<div class="ibx-empty">${icon("search-x")}<strong>No watches match.</strong></div>`
        : `<div class="wch-empty">
             <p class="wch-empty-title">Nothing watched yet.</p>
             <p class="wch-empty-line">Say what to look out for, in your words. Dana turns it into triggers, checks and actions.</p>
             <div class="wch-examples">${EXAMPLES.map((x) => `<button type="button" class="wch-example" data-example="${esc(x)}" dir="auto">${esc(x)}</button>`).join("")}</div>
           </div>`;
      A.icons();
      return;
    }
    list.innerHTML = pending + groups.map((g) => `
      <section class="wch-group" aria-label="${esc(g.title)}">
        <h3 class="wch-group-head dn-eyebrow">${esc(g.title)} · ${g.items.length}</h3>
        ${g.items.map(rowHtml).join("")}
      </section>`).join("");
    A.icons();
  }

  const rowEl = (id) => $("wch-list") && $("wch-list").querySelector(`.wch-row[data-id="${CSS.escape(id)}"]`);

  function openRowMenu(btn) {
    const w = watchOf(btn.dataset.menu);
    if (!w) return;
    const active = w.status === "active";
    const canToggle = w.status === "active" || w.status === "paused";
    A.menu(btn, [
      { label: "Open", icon: "pencil", onSelect: () => select(w.id) },
      canToggle ? { label: active ? "Pause" : "Resume", icon: active ? "pause" : "play", onSelect: () => setStatus(w.id, active ? "paused" : "active") } : null,
      null,
      { label: "Delete", icon: "trash-2", danger: true, onSelect: () => remove(w.id) },
    ].filter((x, i, xs) => x !== null || (i > 0 && xs[i - 1] !== null)), { label: `More for ${w.title}` });
  }

  async function createFromWords(text) {
    const t = String(text || "").trim();
    if (!t || S.creating) return;
    S.creating = t; S.createError = "";
    renderNewError();
    renderList();
    const r = await A.json("POST", "/api/watches", { text: t });
    S.creating = "";
    if ((r.status === 201 || r.status === 200) && r.body && r.body.watch) {
      $("wch-new-text").value = "";
      autosize($("wch-new-text"));
      replaceWatch(r.body.watch);
      renderKicker();
      renderList();
      select(r.body.watch.id, { force: true });
      A.motion.flash(rowEl(r.body.watch.id));
    } else {
      const errs = C.normalizeErrors(r.body);
      S.createError = errs.length ? errs.map((e) => e.message).join(" · ") : (r.status ? "Couldn't make that into a watch." : "No connection.");
      renderList();
    }
    renderNewError();
  }
  function renderNewError() {
    const el = $("wch-new-err");
    el.textContent = S.createError;
    el.classList.toggle("hidden", !S.createError);
    $("wch-new-go").disabled = Boolean(S.creating);
    $("wch-new-go").textContent = S.creating ? "Adding…" : "Add";
  }
  function autosize(ta) { if (!ta) return; ta.style.height = "auto"; ta.style.height = `${Math.min(ta.scrollHeight, 120)}px`; }

  async function setStatus(id, status) {
    const r = await A.json("PATCH", `/api/watches/${encodeURIComponent(id)}`, { status });
    if (r.status === 200 && r.body && r.body.watch) {
      replaceWatch(r.body.watch, { keepDraft: true });
      renderKicker(); renderList(); if (S.selected === id) renderDetail();
      A.motion.flash(rowEl(id));
      toast(status === "paused" ? "Paused" : "Watching again");
    } else toast(C.normalizeErrors(r.body)[0]?.message || "Couldn't change it");
  }

  async function remove(id) {
    const w = watchOf(id);
    if (!w) return;
    const row = rowEl(id);
    const leaving = row ? A.motion.complete(row) : Promise.resolve();
    const r = await A.json("DELETE", `/api/watches/${encodeURIComponent(id)}`);
    await leaving;
    if (r.status === 200 && r.body && r.body.ok) {
      S.watches = S.watches.filter((x) => x.id !== id);
      if (S.selected === id) select(null, { force: true });
      renderKicker(); renderList();
      toast(`Deleted “${w.title || "watch"}”`, r.body.undo_id || null);
    } else { renderList(); toast("Couldn't delete it"); }
  }

  async function undo(undoId) {
    $("wch-toast").classList.add("hidden");
    const r = await A.json("POST", `/api/undo/${encodeURIComponent(undoId)}`);
    if (r.status >= 200 && r.status < 300) { await loadList(); toast("Restored"); }
    else toast(r.status === 404 ? "Too late to undo" : "Couldn't undo");
  }

  let toastTimer = null;
  function toast(text, undoId) {
    const el = $("wch-toast");
    if (!el) return;
    clearTimeout(toastTimer);
    el.innerHTML = `<span dir="auto">${esc(text)}</span>${undoId ? `<button type="button" data-undo="${esc(undoId)}">Undo</button>` : ""}`;
    el.classList.remove("hidden");
    toastTimer = setTimeout(() => el.classList.add("hidden"), undoId ? 8000 : 3500);
  }

  // =====================================================================================================
  // the editor
  // =====================================================================================================

  function select(id, { force = false } = {}) {
    if (!force && id !== S.selected && dirty() && !window.confirm("Discard your changes to this watch?")) return;
    S.selected = id;
    const w = id ? watchOf(id) : null;
    S.draft = w ? C.clone(w.spec) : null;
    S.orig = w ? C.clone(w.spec) : null;
    S.errors = []; S.dry = {}; S.dryState = ""; S.fires = null; S.trace = null; S.changeError = ""; S.openSamples = new Set();
    $("wch-list-view").classList.toggle("detail-open", Boolean(id));
    root.querySelectorAll(".wch-row").forEach((r) => {
      const on = r.dataset.id === id;
      r.classList.toggle("selected", on);
      const b = r.querySelector(".wch-row-main");
      if (b) { if (on) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current"); }
    });
    renderDetail();
    if (id) { loadFires(id); loadTrace(id); $("wch-detail").scrollTop = 0; }
  }

  const sel = (path, options, value) => `<select class="form-control wch-in" data-path="${esc(path)}">${options.map(([v, l]) => `<option value="${esc(v)}"${String(v) === String(value == null ? "" : value) ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  const errAt = (path) => C.errorsAt(S.errors, path);
  const errHtml = (path, exact = false) => {
    const es = exact ? S.errors.filter((e) => e.path === path) : errAt(path);
    return es.length ? `<div class="wch-err" role="alert">${es.map((e) => esc(e.message)).join(" · ")}</div>` : "";
  };
  const bad = (path) => (S.errors.some((e) => e.path === path) ? ' aria-invalid="true"' : "");

  function eventTypeOptions(current) {
    const ets = S.catalog.event_types || [];
    const opts = ets.map((e) => [e.name, C.eventLabel(e.name)]);
    if (current && !ets.some((e) => e.name === current)) opts.unshift([current, `${C.eventLabel(current)} (not available)`]);
    return opts;
  }
  function fieldOptions(type, current) {
    const et = (S.catalog.event_types || []).find((e) => e.name === type);
    const fields = et && et.fields ? Object.keys(et.fields) : [];
    const opts = fields.map((f) => [f, C.fieldWords(f)]);
    if (current && !fields.includes(current)) opts.unshift([current, C.fieldWords(current)]);
    if (!opts.length) opts.push(["text", "text"]);
    return opts;
  }
  function fieldHint(type, field) {
    const et = (S.catalog.event_types || []).find((e) => e.name === type);
    return (et && et.fields && et.fields[field]) || "";
  }

  function conditionHtml(t, i, c, j) {
    const p = `triggers.${i}.where.${j}`;
    const opOpts = C.OPS.map((o) => [o, C.OP_WORDS[o]]);
    const list = c.op === "in" || c.op === "contains_any";
    const value = c.op === "exists" ? "" : `<input class="form-control wch-in wch-cond-val" dir="auto" data-path="${p}.value" value="${esc(c.value)}" placeholder="${list ? "a | b | c" : "value"}"${bad(`${p}.value`)}>`;
    const extra = c.op === "between"
      ? `<span class="wch-and">and</span><input class="form-control wch-in wch-cond-val" data-path="${p}.value2" value="${esc(c.value2)}" placeholder="value"${bad(`${p}.value2`)}>`
      : c.op === "approx" ? `<span class="wch-and">±</span><input class="form-control wch-in wch-cond-tol" type="number" min="1" max="100" data-path="${p}.tolerance_pct" value="${esc(c.tolerance_pct || 10)}" aria-label="Tolerance in percent"><span class="wch-and">%</span>` : "";
    const hint = fieldHint(t.event_type, c.field);
    return `
      <div class="wch-cond">
        ${sel(`${p}.field`, fieldOptions(t.event_type, c.field), c.field)}
        ${sel(`${p}.op`, opOpts, c.op)}
        ${value}${extra}
        <button type="button" class="icon-btn wch-x" data-act="rm-cond" data-i="${i}" data-j="${j}" aria-label="Remove this condition">${icon("x")}</button>
        ${hint ? `<div class="wch-cond-hint">${esc(hint)}</div>` : ""}
        ${errHtml(p)}
      </div>`;
  }

  function loadHtml(t) {
    if (t.kind === "time") return "";
    const sem = Boolean(String(t.semantic || "").trim());
    const r = S.dry[t.id] || (S.orig && C.same(S.draft, S.orig) && watchOf(S.selected) && (watchOf(S.selected).load || {})[t.id]) || null;
    const lw = C.loadWords(r, sem);
    const busy = S.dryState === "busy";
    if (!lw) return `<div class="wch-load${busy ? " is-busy" : ""}">${icon("activity")}<span>${busy ? "Checking the last 2 weeks…" : "Edit it to see how often it would have matched"}</span></div>`;
    const samples = (r && r.sample) || [];
    const open = S.openSamples.has(t.id);
    return `
      <div class="wch-load${busy ? " is-busy" : ""}${lw.tone ? ` tone-${lw.tone}` : ""}">
        ${icon("activity")}<span>${esc(lw.line)}</span>
        ${samples.length ? `<button type="button" class="ibx-link" data-act="samples" data-tid="${esc(t.id)}" aria-expanded="${open}">${open ? "Hide" : "Show"} examples</button>` : ""}
      </div>
      ${lw.warn ? `<div class="wch-warn tone-${lw.tone || "amber"}">${icon("alert-triangle")}<span>${esc(lw.warn)}</span></div>` : ""}
      ${open && samples.length ? `<ol class="wch-samples">${samples.slice().reverse().map((x) => `
        <li><span class="wch-s-when">${esc(C.whenWords(x.at))}</span><span class="wch-s-text" dir="auto">${esc(x.summary || C.eventSummary(x.event_type, x.data))}</span></li>`).join("")}</ol>` : ""}`;
  }

  function triggerHtml(t, i, spec) {
    const p = `triggers.${i}`;
    const head = (ico, hue, inner) => `
      <div class="wch-trig-head">
        <span class="wch-ico hue-${hue}">${icon(ico)}</span>${inner}
        <span class="wch-trig-id" title="Trigger id">${esc(t.id)}</span>
        <button type="button" class="icon-btn wch-x" data-act="rm-trig" data-i="${i}" aria-label="Remove this trigger">${icon("x")}</button>
      </div>`;
    if (t.kind === "time") {
      const bases = (spec.triggers || []).filter((x) => x.kind === "time" && x.at && x.id !== t.id);
      const body = t.relative_to !== undefined && t.relative_to !== null && !t.at
        ? `<div class="wch-line">
             <input class="form-control wch-in wch-num" type="number" min="0" data-role="off-abs" data-path="${p}.offset_minutes" value="${Math.abs(Number(t.offset_minutes) || 0)}" aria-label="Minutes">
             <span class="wch-and">min</span>
             <select class="form-control wch-in" data-role="off-dir" data-path="${p}.offset_minutes"><option value="before"${(Number(t.offset_minutes) || 0) < 0 ? " selected" : ""}>before</option><option value="after"${(Number(t.offset_minutes) || 0) >= 0 ? " selected" : ""}>after</option></select>
             ${sel(`${p}.relative_to`, (bases.length ? bases : [{ id: t.relative_to || "", at: "" }]).map((b) => [b.id, b.at ? C.triggerWords(b, spec) : (b.id || "pick a time")]), t.relative_to)}
           </div>${errHtml(`${p}.relative_to`)}`
        : `<div class="wch-line">
             <input class="form-control wch-in" type="datetime-local" data-path="${p}.at" value="${esc(C.toInput(t.at))}"${bad(`${p}.at`)} aria-label="Date and time">
             ${sel(`${p}.every`, [["", "Once"]].concat(C.EVERY.map((e) => [e, C.EVERY_WORDS[e]])), t.every || "")}
           </div>${errHtml(`${p}.at`)}`;
      return `
        <div class="wch-trig" data-trig="${i}">
          ${head("clock", "amber", `<span class="wch-trig-kind">${t.at ? "At a time" : "Relative to another time"}</span>`)}
          <p class="wch-words" data-words="${i}" dir="auto">${esc(C.triggerWords(t, spec))}</p>
          ${body}
        </div>`;
    }
    const lane = C.laneOf(t.event_type);
    const [from, to] = C.splitDuring(t.during);
    return `
      <div class="wch-trig" data-trig="${i}">
        ${head(lane.icon, lane.hue, `<select class="wch-plain-select" data-path="${p}.event_type" aria-label="What to watch">${eventTypeOptions(t.event_type).map(([v, l]) => `<option value="${esc(v)}"${v === t.event_type ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>`)}
        ${errHtml(`${p}.event_type`)}
        <p class="wch-words" data-words="${i}" dir="auto">${esc(C.triggerWords(t, spec))}</p>
        <div class="wch-sub">Only when all of these hold</div>
        <div class="wch-conds">${(t.where || []).map((c, j) => conditionHtml(t, i, c, j)).join("") || `<div class="wch-none">No field conditions</div>`}</div>
        ${S.errors.some((e) => e.path === `${p}.where`) ? errHtml(`${p}.where`, true) : ""}
        <button type="button" class="ibx-link wch-add" data-act="add-cond" data-i="${i}">${icon("plus")}Condition</button>
        <div class="wch-sub">Mentions any of</div>
        <div class="wch-kws">
          ${(t.keywords || []).map((k, j) => `<span class="wch-kw" dir="auto">${esc(k)}<button type="button" data-act="rm-kw" data-i="${i}" data-j="${j}" aria-label="Remove ${esc(k)}">${icon("x")}</button></span>`).join("")}
          <input class="wch-kw-in" data-kw="${i}" dir="auto" placeholder="${(t.keywords || []).length ? "add" : "a word, then Enter"}" aria-label="Add a keyword">
        </div>
        <div class="wch-sub">During</div>
        <div class="wch-line">
          <input class="form-control wch-in" type="time" data-role="during-from" data-path="${p}.during" value="${esc(from)}" aria-label="From"${bad(`${p}.during`)}>
          <span class="wch-and">to</span>
          <input class="form-control wch-in" type="time" data-role="during-to" data-path="${p}.during" value="${esc(to)}" aria-label="To">
          <span class="wch-mute">${from ? "" : "any time"}</span>
        </div>${errHtml(`${p}.during`)}
        <div class="wch-ai">
          <label class="wch-ai-label" for="wch-sem-${i}">✦ AI check</label>
          <textarea class="form-control wch-in" id="wch-sem-${i}" rows="2" dir="auto" data-path="${p}.semantic" placeholder="Optional: a yes / no question a small model answers for each event that passes the filters">${esc(t.semantic || "")}</textarea>
        </div>
        <div class="wch-load-wrap" data-load="${esc(t.id)}">${loadHtml(t)}</div>
      </div>`;
  }

  function actionHtml(a, i, list) {
    const p = `${list}.${i}`;
    const autonomy = (a.type === "agent" || a.type === "tool") ? `
      <div class="segmented-control wch-seg" role="group" aria-label="Before it acts">
        ${[["ask_first", "Ask me first"], ["act_and_tell", "Just do it"]].map(([k, l]) => `<button type="button" class="segmented-btn${(a.autonomy || "ask_first") === k ? " active" : ""}" data-set="${p}.autonomy" data-value="${k}" aria-pressed="${(a.autonomy || "ask_first") === k}">${l}</button>`).join("")}
      </div>` : "";
    let body = "";
    if (a.type === "notify") {
      body = `<textarea class="form-control wch-in" rows="2" dir="auto" data-path="${p}.text" placeholder="What to tell you"${bad(`${p}.text`)}>${esc(a.text || "")}</textarea>${errHtml(`${p}.text`)}
        <div class="segmented-control wch-seg" role="group" aria-label="How urgent">
          ${Object.entries(C.URGENCY_WORDS).map(([k, l]) => `<button type="button" class="segmented-btn${(a.urgency || "now") === k ? " active" : ""}" data-set="${p}.urgency" data-value="${k}" aria-pressed="${(a.urgency || "now") === k}">${l}</button>`).join("")}
        </div>`;
    } else if (a.type === "tool") {
      body = `<input class="form-control wch-in" list="wch-tools" data-path="${p}.tool" value="${esc(a.tool || "")}" placeholder="A Dana tool"${bad(`${p}.tool`)}>${errHtml(`${p}.tool`)}
        <textarea class="form-control wch-in wch-mono" rows="2" data-path="${p}.args_json" placeholder='{"key": "value"}' spellcheck="false"${bad(`${p}.args_json`)}>${esc(a.args_json || "")}</textarea>${errHtml(`${p}.args_json`)}${autonomy}`;
    } else if (a.type === "agent") {
      body = `<textarea class="form-control wch-in" rows="3" dir="auto" data-path="${p}.instructions" placeholder="What the agent should do with what happened"${bad(`${p}.instructions`)}>${esc(a.instructions || "")}</textarea>${errHtml(`${p}.instructions`)}
        <p class="wch-mute">It gets what happened and your Dana tools.</p>${autonomy}`;
    } else if (a.type === "create_task") {
      body = `<input class="form-control wch-in" dir="auto" data-path="${p}.text" value="${esc(a.text || "")}" placeholder="The task">`;
    } else if (a.type === "suggest_complete") {
      body = `<p class="wch-mute">Shows “Looks done. Close it?” — never closes it alone.</p>`;
    } else if (a.type === "complete_task") {
      body = `<p class="wch-mute">Marks the reminder's task done.</p>`;
    }
    return `
      <div class="wch-act" data-act-i="${i}" data-list="${list}">
        <div class="wch-act-head">
          <span class="wch-ico hue-${a.type === "agent" ? "plum" : a.type === "notify" ? "ember" : "sky"}">${icon(a.type === "agent" ? "sparkles" : a.type === "tool" ? "wrench" : a.type === "notify" ? "bell-ring" : "list-checks")}</span>
          <select class="wch-plain-select" data-path="${p}.type" aria-label="What to do">${C.ACTION_TYPES.map((k) => `<option value="${k}"${k === a.type ? " selected" : ""}>${esc(C.ACTION_WORDS[k])}</option>`).join("")}</select>
          <button type="button" class="icon-btn wch-x" data-act="rm-act" data-list="${list}" data-i="${i}" aria-label="Remove this action">${icon("x")}</button>
        </div>
        ${body}
      </div>`;
  }

  function evidenceHtml(s) {
    const types = S.catalog.event_types || [];
    const on = new Set(s.evidence_event_types || []);
    return `
      <section class="wch-sec">
        <h3 class="wch-sec-head dn-eyebrow">Done by itself?</h3>
        <p class="wch-mute">Dana looks for signs the task got done and offers to close it. It never closes it alone.</p>
        <div class="wch-evtypes">${types.map((e) => {
          const lane = C.laneOf(e.name);
          return `<button type="button" class="chip${on.has(e.name) ? " active" : ""}" data-act="ev-type" data-type="${esc(e.name)}" aria-pressed="${on.has(e.name)}">${icon(lane.icon)}${esc(C.eventLabel(e.name))}</button>`;
        }).join("")}</div>
        <div class="wch-sub">Mentions any of</div>
        <div class="wch-kws">
          ${(s.evidence_keywords || []).map((k, j) => `<span class="wch-kw" dir="auto">${esc(k)}<button type="button" data-act="rm-kw" data-i="evidence" data-j="${j}" aria-label="Remove ${esc(k)}">${icon("x")}</button></span>`).join("")}
          <input class="wch-kw-in" data-kw="evidence" dir="auto" placeholder="a word, then Enter" aria-label="Add an evidence keyword">
        </div>
        <div class="wch-ai">
          <label class="wch-ai-label" for="wch-evq">✦ AI check</label>
          <textarea class="form-control wch-in" id="wch-evq" rows="2" dir="auto" data-path="evidence_question" placeholder="Was it done? (asks whether it happened, not whether it was mentioned)">${esc(s.evidence_question || "")}</textarea>
        </div>
      </section>`;
  }

  function historyHtml() {
    if (S.fires === null) return `<div class="wch-skel dn-shimmer"></div>`;
    if (!S.fires.length) return `<p class="wch-mute">It hasn't fired yet.</p>`;
    const spec = S.draft || {};
    return `<ol class="wch-fires">${S.fires.map((f) => {
      const t = (spec.triggers || []).find((x) => x.id === f.trigger_id);
      const what = f.event_type ? C.eventSummary(f.event_type, f.event) : (t ? C.triggerWords(t, spec) : (f.trigger_id === "deadline" ? "Deadline passed" : "Time"));
      const lane = C.laneOf(f.event_type);
      return `
        <li class="wch-fire">
          <span class="wch-dot hue-${lane.hue}" aria-hidden="true"></span>
          <div class="wch-fire-text">
            <div class="wch-fire-line"><span class="wch-fire-what" dir="auto">${esc(what)}</span><span class="wch-fire-when">${esc(C.whenWords(f.at))}</span></div>
            <div class="wch-fire-acts">${C.fireActions(f).map((a) => `
              <span class="wch-status hue-${a.hue}" title="${esc(a.detail)}">${esc(a.label)} · ${esc(a.statusLabel)}</span>
              ${a.session_id || a.run_id ? `<button type="button" class="ibx-link" data-act="run" data-session="${esc(a.session_id || a.run_id)}">Open the agent's chat</button>` : ""}`).join("")}</div>
          </div>
        </li>`;
    }).join("")}</ol>`;
  }
  function traceHtml() {
    const filters = `<div class="segmented-control wch-seg" role="group" aria-label="Which checks">${C.TRACE_FILTERS.map(([k, l]) => `<button type="button" class="segmented-btn${S.traceFilter === k ? " active" : ""}" data-act="trace-filter" data-value="${k}" aria-pressed="${S.traceFilter === k}">${l}</button>`).join("")}</div>`;
    if (S.trace === null) return `${filters}<div class="wch-skel dn-shimmer"></div>`;
    const more = S.traceUnstored ? `<p class="wch-mute">${S.traceUnstored} more checks this hour were not stored (far misses over the hourly cap).</p>` : "";
    if (!S.trace.length) return `${filters}<p class="wch-mute">${S.traceFilter === "all" ? "No events of the kinds it watches have come in yet." : "None yet."}</p>${more}`;
    return `${filters}<ol class="wch-fires wch-checks">${S.trace.map((row) => {
      const v = C.traceWords(row);
      const lines = C.traceDataLines(row.data);
      return `
        <li class="wch-fire">
          <span class="wch-dot hue-${v.hue}" aria-hidden="true"></span>
          <div class="wch-fire-text">
            <div class="wch-fire-line"><span class="wch-fire-what" dir="auto">${esc(v.what || row.event_type || "")}</span><span class="wch-fire-when">${esc(C.whenWords(row.at))}</span></div>
            <div class="wch-fire-acts"><span class="wch-status hue-${v.hue}">${esc(v.outcome)}</span>${v.trigger ? `<span class="wch-mute">${esc(v.trigger)}</span>` : ""}${v.reason ? `<span class="wch-check-why" dir="auto">${esc(v.reason)}</span>` : ""}</div>
            ${lines.length ? `<details class="wch-check-data"><summary>Event data</summary><dl>${lines.map(([k, val]) => `<dt>${esc(k)}</dt><dd dir="auto">${esc(val)}</dd>`).join("")}</dl></details>` : ""}
          </div>
        </li>`;
    }).join("")}</ol>${more}`;
  }
  function renderTrace() {
    const el = $("wch-trace");
    if (el) { el.innerHTML = traceHtml(); A.icons(); }
  }
  function renderHistory() {
    const el = $("wch-history");
    if (el) { el.innerHTML = historyHtml(); A.icons(); }
  }

  function introHtml() {
    return `
      <div class="wch-intro">
        <p class="dn-eyebrow">How a watch works</p>
        <p class="wch-intro-title">Something happens. Dana checks. You hear about it.</p>
        <ol class="wch-intro-steps">
          <li><span class="wch-ico hue-teal">${icon("radio")}</span><span><strong>Events</strong> come in: notifications, email, money, calls, places, the clock.</span></li>
          <li><span class="wch-ico hue-sky">${icon("filter")}</span><span><strong>The filter</strong> checks each against your watches' conditions, in microseconds.</span></li>
          <li><span class="wch-ico hue-plum">${icon("sparkles")}</span><span><strong>An AI check</strong> answers a yes / no question when words alone can't tell.</span></li>
          <li><span class="wch-ico hue-ember">${icon("zap")}</span><span><strong>It fires</strong>: Dana tells you, runs a tool, or asks an agent to act.</span></li>
        </ol>
        <button type="button" class="dn-pill small" data-view="live">See it live</button>
      </div>`;
  }

  function renderDetail() {
    const pane = $("wch-detail");
    const w = S.selected ? watchOf(S.selected) : null;
    if (!w || !S.draft) { pane.innerHTML = S.loaded && !S.loadError ? introHtml() : ""; A.icons(); return; }
    keepFocus(() => {
      const s = S.draft;
      const kind = s.kind || w.kind || "watch";
      const placed = ["title", "window_start", "window_end", "triggers", "on_match", "on_deadline", "evidence_question"];
      const loose = C.errorsElsewhere(S.errors, placed);
      const active = w.status === "active";
      const meta = [
        w.created_at ? `Made ${C.agoWords(w.created_at)}` : "",
        w.fires_count ? `fired ${w.fires_count} time${w.fires_count === 1 ? "" : "s"}` : "not fired yet",
        w.last_fired_at ? `last ${C.agoWords(w.last_fired_at)}` : "",
        w.next_time && active ? `next ${C.whenWords(w.next_time)}` : "",
      ].filter(Boolean).join(" · ");
      const trig = (s.triggers || []).map((t, i) => triggerHtml(t, i, s)).join(`<div class="wch-or" aria-hidden="true"><span>or</span></div>`);
      pane.innerHTML = `
        <div class="wch-d">
          <div class="wch-d-head">
            <div class="wch-d-bar">
              <button type="button" class="icon-btn ibx-close" data-act="close" aria-label="Back to the list">${icon("arrow-left")}</button>
              <span class="dn-eyebrow">${esc(kind === "reminder" ? "Reminder" : "Watch")} · ${esc(C.STATUS_WORDS[w.status] || w.status)}</span>
              <span class="wch-grow"></span>
              ${w.status === "active" || w.status === "paused" ? `<button type="button" class="dn-pill small" data-act="toggle">${active ? "Pause" : "Resume"}</button>` : ""}
              <button type="button" class="dn-overflow" data-act="more" aria-haspopup="menu" aria-expanded="false" aria-label="More">${icon("more-horizontal")}</button>
            </div>
            <input class="wch-d-title" dir="auto" data-path="title" value="${esc(s.title || "")}" aria-label="Title"${bad("title")}>
            ${errHtml("title", true)}
            ${w.request ? `<p class="wch-d-said">You said <q dir="auto">${esc(w.request)}</q></p>` : ""}
            <p class="wch-d-meta">${esc(meta)}</p>
            ${s.unsupported ? `<div class="wch-warn tone-amber">${icon("info")}<span dir="auto">${esc(s.unsupported)}</span></div>` : ""}
          </div>

          <section class="wch-sec">
            <h3 class="wch-sec-head dn-eyebrow">When</h3>
            <div class="wch-window">
              <label class="wch-field"><span>From</span><input class="form-control wch-in" type="datetime-local" data-path="window_start" value="${esc(C.toInput(s.window_start))}"></label>
              <label class="wch-field"><span>Until</span><input class="form-control wch-in" type="datetime-local" data-path="window_end" value="${esc(C.toInput(s.window_end))}"${bad("window_end")}></label>
            </div>
            ${errHtml("window_end", true)}
            ${kind !== "reminder" ? `
            <div class="wch-line wch-repeat">
              <div class="segmented-control wch-seg" role="group" aria-label="How often">
                ${[["once", "First time only"], ["each", "Every time"]].map(([k, l]) => `<button type="button" class="segmented-btn${(s.repeat || "once") === k ? " active" : ""}" data-set="repeat" data-value="${k}" aria-pressed="${(s.repeat || "once") === k}">${l}</button>`).join("")}
              </div>
              ${(s.repeat || "once") === "each" ? `<label class="wch-mute wch-cool">at most once every <input class="form-control wch-in wch-num" type="number" min="0" data-path="cooldown_minutes" value="${esc(s.cooldown_minutes == null ? 10 : s.cooldown_minutes)}"> min per source</label>` : ""}
            </div>` : ""}
            <div class="wch-trigs">${trig || `<div class="wch-none">No triggers yet</div>`}</div>
            ${S.errors.some((e) => e.path === "triggers") ? errHtml("triggers", true) : ""}
            <button type="button" class="ibx-link wch-add" data-act="add-trig" aria-haspopup="menu" aria-expanded="false">${icon("plus")}Add a trigger</button>
          </section>

          <section class="wch-sec">
            <h3 class="wch-sec-head dn-eyebrow">Then</h3>
            <div class="wch-acts">${(s.on_match || []).map((a, i) => actionHtml(a, i, "on_match")).join("") || `<div class="wch-none">Nothing to do yet</div>`}</div>
            ${S.errors.some((e) => e.path === "on_match") ? errHtml("on_match", true) : ""}
            <button type="button" class="ibx-link wch-add" data-act="add-act" data-list="on_match">${icon("plus")}Add an action</button>
          </section>

          <section class="wch-sec">
            <h3 class="wch-sec-head dn-eyebrow">If nothing happens by the end</h3>
            <div class="wch-acts">${(s.on_deadline || []).map((a, i) => actionHtml(a, i, "on_deadline")).join("") || `<div class="wch-none">Nothing — it just ends</div>`}</div>
            <button type="button" class="ibx-link wch-add" data-act="add-act" data-list="on_deadline">${icon("plus")}Add an action</button>
          </section>

          ${kind === "reminder" ? evidenceHtml(s) : ""}

          <section class="wch-sec">
            <h3 class="wch-sec-head dn-eyebrow">Change in words</h3>
            <form class="wch-change" data-form="change" autocomplete="off">
              <input class="form-control" id="wch-change-text" dir="auto" placeholder="e.g. also on weekends, and only above 1,000 ₪" aria-label="What to change"${S.changing ? " disabled" : ""}>
              <button type="submit" class="dn-pill small"${S.changing ? " disabled" : ""}>${S.changing ? "Changing…" : "Change"}</button>
            </form>
            ${S.changeError ? `<div class="wch-err" role="alert">${esc(S.changeError)}</div>` : ""}
          </section>

          <section class="wch-sec">
            <h3 class="wch-sec-head dn-eyebrow">History</h3>
            <div id="wch-history">${historyHtml()}</div>
          </section>

          <section class="wch-sec">
            <h3 class="wch-sec-head dn-eyebrow">Recent checks</h3>
            <div id="wch-trace">${traceHtml()}</div>
          </section>
          <datalist id="wch-tools">${(S.catalog.tools || []).map((t) => `<option value="${esc(t)}">`).join("")}</datalist>
        </div>
        <div class="wch-savebar${dirty() || S.errors.length ? "" : " hidden"}" id="wch-savebar">
          <div class="wch-savebar-text">${loose.length ? `<span class="wch-err-inline" role="alert">${esc(loose.map((e) => e.message).join(" · "))}</span>` : S.errors.length ? `<span class="wch-err-inline" role="alert">Fix the marked fields</span>` : "<span>Unsaved changes</span>"}</div>
          <button type="button" class="btn-secondary" data-act="discard">Discard</button>
          <button type="button" class="btn-primary" data-act="save"${S.saving ? " disabled" : ""}>${S.saving ? "Saving…" : "Save"}</button>
        </div>`;
    });
    A.icons();
  }

  /** Re-render without losing what is typed: the focused field (by its data-path / data-kw / id) keeps focus
   *  and caret. */
  function keepFocus(fn) {
    const a = document.activeElement;
    const pane = $("wch-detail");
    let sel = null, s0 = null, s1 = null;
    if (a && pane.contains(a)) {
      if (a.dataset.path) sel = `[data-path="${CSS.escape(a.dataset.path)}"]${a.dataset.role ? `[data-role="${CSS.escape(a.dataset.role)}"]` : ""}`;
      else if (a.dataset.kw !== undefined) sel = `[data-kw="${CSS.escape(a.dataset.kw)}"]`;
      else if (a.id) sel = `#${CSS.escape(a.id)}`;
      try { s0 = a.selectionStart; s1 = a.selectionEnd; } catch (_) { /* not a text field */ }
    }
    const top = pane.scrollTop;
    fn();
    pane.scrollTop = top;
    if (sel) {
      const b = pane.querySelector(sel);
      if (b) { b.focus({ preventScroll: true }); try { if (s0 !== null) b.setSelectionRange(s0, s1); } catch (_) { /* ignore */ } }
    }
  }

  /** The draft after the field at `el` changed (its data-path says what it sets). */
  function applyField(el) {
    const path = el.dataset.path;
    const p = path.split(".");
    let d = S.draft;
    if (p[0] === "triggers") {
      const i = +p[1];
      const card = el.closest("[data-trig]");
      if (p[2] === "where") d = C.setCondition(d, i, +p[3], p[4], el.value);
      else if (p[2] === "at") d = C.setTrigger(d, i, "at", C.fromInput(el.value));
      else if (p[2] === "offset_minutes") {
        const abs = Math.abs(parseInt(card.querySelector('[data-role="off-abs"]').value, 10) || 0);
        const dir = card.querySelector('[data-role="off-dir"]').value;
        d = C.setTrigger(d, i, "offset_minutes", dir === "before" ? -abs : abs);
      } else if (p[2] === "during") {
        d = C.setTrigger(d, i, "during", C.joinDuring(card.querySelector('[data-role="during-from"]').value, card.querySelector('[data-role="during-to"]').value));
      } else d = C.setTrigger(d, i, p[2], el.value, S.catalog);
    } else if (p[0] === "on_match" || p[0] === "on_deadline") {
      d = C.setAction(d, p[0], +p[1], p[2], el.value);
    } else if (p[0] === "window_start" || p[0] === "window_end") {
      d = C.setSpec(d, p[0], C.fromInput(el.value));
    } else d = C.setSpec(d, p[0], el.value);
    return d;
  }

  /** After an edit: the words lines, the save bar, the errors (checked again once shown) and the dry run. */
  function edited({ rerender = false } = {}) {
    if (S.errors.length) S.errors = C.validate(S.draft, S.catalog);
    if (rerender) renderDetail();
    else {
      (S.draft.triggers || []).forEach((t, i) => {
        const el = $("wch-detail").querySelector(`[data-words="${i}"]`);
        if (el) el.textContent = C.triggerWords(t, S.draft);
      });
      const bar = $("wch-savebar");
      if (bar) bar.classList.toggle("hidden", !dirty() && !S.errors.length);
    }
    scheduleDry();
  }

  function setDraft(d, rerender = true) { S.draft = d; edited({ rerender }); }

  let dryTimer = null, dryReq = 0;
  function scheduleDry() {
    clearTimeout(dryTimer);
    if (!S.draft || !(S.draft.triggers || []).some((t) => t.kind === "event")) return;
    dryTimer = setTimeout(runDry, 700);
  }
  async function runDry() {
    const id = S.selected, n = ++dryReq;
    S.dryState = "busy"; paintLoads();
    const r = await A.json("POST", "/api/watches/dry-run", { spec: S.draft, days: 14 });
    if (n !== dryReq || id !== S.selected) return;
    if (r.status === 200 && r.body && r.body.triggers) { S.dry = r.body.triggers; S.dryState = ""; }
    else S.dryState = "error";
    paintLoads();
  }
  function paintLoads() {
    const pane = $("wch-detail");
    if (!pane || !S.draft) return;
    for (const t of S.draft.triggers || []) {
      const el = pane.querySelector(`[data-load="${CSS.escape(t.id)}"]`);
      if (el) el.innerHTML = loadHtml(t);
    }
    A.icons();
  }

  async function save() {
    if (!S.draft || S.saving) return;
    const errs = C.validate(S.draft, S.catalog);
    if (errs.length) { S.errors = errs; renderDetail(); focusFirstError(); return; }
    S.saving = true; renderDetail();
    const id = S.selected;
    const r = await A.json("PATCH", `/api/watches/${encodeURIComponent(id)}`, { spec: S.draft });
    S.saving = false;
    if (r.status === 200 && r.body && r.body.watch) {
      S.errors = [];
      replaceWatch(r.body.watch);
      renderKicker(); renderList(); renderDetail();
      A.motion.flash(rowEl(id));
      toast("Saved");
    } else {
      S.errors = C.normalizeErrors(r.body);
      if (!S.errors.length) S.errors = [{ path: "", message: r.status ? "Couldn't save it" : "No connection" }];
      renderDetail(); focusFirstError();
    }
  }
  function focusFirstError() {
    const el = $("wch-detail").querySelector('[aria-invalid="true"], .wch-err');
    if (el) { el.scrollIntoView({ block: "center", behavior: A.motion.reduced() ? "auto" : "smooth" }); if (el.focus) el.focus({ preventScroll: true }); }
  }

  async function changeInWords() {
    const input = $("wch-change-text");
    const text = input ? input.value.trim() : "";
    if (!text || S.changing) return;
    if (dirty() && !window.confirm("Changing it in words replaces your unsaved edits. Go on?")) return;
    S.changing = true; S.changeError = ""; renderDetail();
    const id = S.selected;
    const r = await A.json("PATCH", `/api/watches/${encodeURIComponent(id)}`, { change: text });
    S.changing = false;
    if (S.selected !== id) return;
    if (r.status === 200 && r.body && r.body.watch) {
      replaceWatch(r.body.watch);
      S.errors = []; S.dry = {};
      renderKicker(); renderList(); renderDetail();
      A.motion.flash($("wch-detail").querySelector(".wch-d-head"));
      toast("Changed");
    } else {
      S.changeError = C.normalizeErrors(r.body).map((e) => e.message).join(" · ") || "Couldn't change it";
      renderDetail();
      const again = $("wch-change-text");
      if (again) again.value = text;
    }
  }

  function addTriggerMenu(btn) {
    const hasTime = (S.draft.triggers || []).some((t) => t.kind === "time" && t.at);
    A.menu(btn, [
      { label: "When something happens", icon: "radio", onSelect: () => setDraft(C.addTrigger(S.draft, "event", S.catalog)) },
      { label: "At a time", icon: "clock", onSelect: () => setDraft(C.addTrigger(S.draft, "time", S.catalog)) },
      { label: "Before or after another time", icon: "timer", disabled: !hasTime, hint: hasTime ? "" : "needs a time", onSelect: () => setDraft(C.addTrigger(S.draft, "relative", S.catalog)) },
    ], { label: "Add a trigger" });
  }
  function detailMenu(btn) {
    const w = watchOf(S.selected);
    if (!w) return;
    A.menu(btn, [
      { label: "Copy the spec (JSON)", icon: "copy", onSelect: () => { navigator.clipboard?.writeText(JSON.stringify(S.draft, null, 2)).then(() => toast("Copied"), () => toast("Couldn't copy")); } },
      dirty() ? { label: "Discard changes", icon: "rotate-ccw", onSelect: discard } : null,
      null,
      { label: "Delete", icon: "trash-2", danger: true, onSelect: () => remove(w.id) },
    ].filter((x, i) => x !== null || i > 0), { label: "More" });
  }
  function discard() { S.draft = C.clone(S.orig); S.errors = []; S.dry = {}; renderDetail(); }

  // =====================================================================================================
  // live: the pipeline
  // =====================================================================================================

  const L = {
    state: null, stream: null, connected: false, geo: null, nodesDrawn: "", feedDrawn: new Set(), feed: "all",
    particles: [], ready: {}, raf: 0, ro: null, paintQueued: false,
  };
  const MAX_DOTS = 28;
  const HOP_MS = 900;

  const liveOn = () => S.view === "live" && A.visible() && !document.hidden;
  const motionOff = () => A.motion.reduced() || !liveOn();

  async function startLive() {
    if (L.stream || !liveOn()) return;
    await resync();
    if (!liveOn()) return;
    const s = A.stream("/api/watches/pipeline/stream", { message: (ev) => onPipelineEvent(ev) });
    L.stream = s;
    s.source.onopen = () => {
      const was = L.connected;
      L.connected = true; paintStatus();
      if (was === false && L.state && L.state._needsResync) { L.state._needsResync = false; resync(); }
    };
    s.source.onerror = () => { L.connected = false; if (L.state) L.state._needsResync = true; paintStatus(); };
  }
  function stopLive() {
    if (L.stream) { L.stream.close(); L.stream = null; }
    L.connected = false;
    clearDots();
    paintStatus();
  }
  async function resync() {
    const r = await A.json("GET", "/api/watches/pipeline");
    if (r.status === 200 && r.body) {
      L.state = C.pipelineInit(r.body, S.catalog);
      L.feedDrawn = new Set(L.state.feed.map((e) => e.key));
      layoutStage(true);
      paintFeed(true);
    } else if (!L.state) {
      L.state = C.pipelineInit({}, S.catalog);
      layoutStage(true);
      paintFeed(true);
    }
    paintStatus();
  }

  function onPipelineEvent(ev) {
    if (!L.state || !ev || typeof ev !== "object") return;
    const lanesBefore = L.state.laneKeys.length;
    const moves = C.pipelineApply(L.state, ev);
    if (L.state.laneKeys.length !== lanesBefore) layoutStage(true);
    queuePaint();
    if (motionOff()) { paintCounters(); return; }
    for (const m of moves) schedule(m);
  }

  function queuePaint() {
    if (L.paintQueued) return;
    L.paintQueued = true;
    setTimeout(() => { L.paintQueued = false; paintFeed(false); if (motionOff()) paintCounters(); paintStatus(); }, 120);
  }

  function paintStatus() {
    const st = $("wch-live-state");
    if (!st) return;
    st.classList.toggle("is-on", L.connected);
    $("wch-live-status").textContent = L.connected ? "Live" : (L.stream ? "Reconnecting…" : "Paused");
    const s = L.state;
    if (s) {
      const since = s.stats.since ? C.parseWhen(s.stats.since) : null;
      $("wch-live-since").textContent = [
        since ? `Since ${C.whenWords(since)}` : "",
        `${s.watches_active} watch${s.watches_active === 1 ? "" : "es"}`,
        `${s.triggers_active} trigger${s.triggers_active === 1 ? "" : "s"}`,
      ].filter(Boolean).join(" · ");
    }
  }

  // ---------- the stage ----------

  function stageSize() {
    const stage = $("wch-stage");
    const lanes = C.lanes(L.state);
    const w = stage.clientWidth || 800;
    const h = Math.max(380, lanes.length * 50 + 40);
    stage.style.height = `${h}px`;
    return { w, h, lanes };
  }

  function layoutStage(force) {
    if (!L.state || S.view !== "live") return;
    const { w, h, lanes } = stageSize();
    const sig = `${w}x${h}:${lanes.map((l) => l.key).join(",")}`;
    if (!force && sig === L.nodesDrawn) return;
    L.nodesDrawn = sig;
    clearDots();
    L.geo = C.layout(lanes, w, h);
    const svg = $("wch-svg");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("width", w); svg.setAttribute("height", h);
    const N = L.geo.nodes;
    const edgeClass = (e) => (e.from === "ai" || e.to === "ai" ? "wch-edge is-ai" : e.id === "clock-fire" ? "wch-edge is-clock" : "wch-edge");
    $("wch-edges").innerHTML = L.geo.edges.map((e) => `<path class="${edgeClass(e)}" d="${C.pathD(C.curve(N[e.from], N[e.to]))}"/>`).join("");
    $("wch-hot").innerHTML = L.geo.edges.map((e) => `<path class="wch-hot" data-edge="${e.id}" d="${C.pathD(C.curve(N[e.from], N[e.to]))}"/>`).join("");
    const at = (id) => `left:${N[id].x}px;top:${N[id].y}px`;
    const laneNodes = lanes.map((l) => `
      <div class="wch-node is-lane hue-${l.hue}" data-node="lane:${esc(l.key)}" style="${at(`lane:${l.key}`)}" title="${esc(l.label)}">
        <span class="wch-node-label">${esc(l.label)}</span>
        <span class="wch-node-n" data-n="lane:${esc(l.key)}">0</span>
        <span class="wch-node-ico">${icon(l.icon)}<span class="wch-ring" aria-hidden="true"></span></span>
      </div>`).join("");
    const stageNode = (id, ico, hue, label) => `
      <div class="wch-node is-stage hue-${hue}" data-node="${id}" style="${at(id)}">
        <span class="wch-node-disc">${icon(ico)}<span class="wch-ring" aria-hidden="true"></span></span>
        <span class="wch-node-label">${label}</span>
        <span class="wch-node-sub" data-n="${id}"></span>
      </div>`;
    const sinks = C.SINKS.map((s) => `
      <div class="wch-node is-sink hue-${s.key === "agent" ? "plum" : s.key === "notify" ? "ember" : s.key === "task" ? "amber" : "sky"}" data-node="sink:${s.key}" style="${at(`sink:${s.key}`)}" title="${esc(s.label)}">
        <span class="wch-node-ico">${icon(s.icon)}<span class="wch-ring" aria-hidden="true"></span></span>
        <span class="wch-node-label">${esc(s.label)}</span>
        <span class="wch-node-n" data-n="sink:${s.key}">0</span>
      </div>`).join("");
    $("wch-nodes").className = `wch-nodes${L.geo.compact ? " is-compact" : ""}`;
    $("wch-nodes").innerHTML = laneNodes + stageNode("filter", "filter", "sky", "Filter") + stageNode("ai", "sparkles", "plum", "AI check") + stageNode("fire", "zap", "ember", "Fire") + sinks;
    A.icons();
    paintCounters(true);
  }

  function paintCounters(initial) {
    if (!L.state || S.view !== "live") return;
    const c = C.counters(L.state);
    const set = (id, text) => {
      const el = $("wch-nodes").querySelector(`[data-n="${CSS.escape(id)}"]`);
      if (!el) return;
      if (initial) el.textContent = text; else A.motion.number(el, text);
    };
    for (const [k, n] of Object.entries(c.lanes)) set(`lane:${k}`, n);
    for (const [k, n] of Object.entries(c.sinks)) set(`sink:${k}`, n);
    const compact = L.geo && L.geo.compact;
    set("filter", compact ? `${c.filter.passed}/${c.filter.in} pass` : `${c.filter.passed} of ${c.filter.in} pass · ${C.usWords(c.filter.us)}`);
    set("ai", compact ? `${c.ai.yes}/${c.ai.checks} yes` : `${c.ai.checks} check${c.ai.checks === 1 ? "" : "s"} · ${c.ai.yes} yes · ${C.costWords(c.ai.cost)}`);
    set("fire", compact ? String(c.fire) : `${c.fire} fire${c.fire === 1 ? "" : "s"}`);
  }

  // ---------- dots ----------

  function schedule(m) {
    if (!L.geo || !L.geo.nodes[m.from] || !L.geo.nodes[m.to]) return;
    const now = performance.now();
    const start = Math.max(now, L.ready[m.key] || 0, m.parent ? (L.ready[m.parent] || 0) : 0);
    const travel = m.from !== m.to;
    const dur = travel ? HOP_MS : (m.end === "drop" ? 520 : 320);
    L.ready[m.key] = start + dur;
    const p = { m, start, dur, travel, el: null, curve: travel ? C.curve(L.geo.nodes[m.from], L.geo.nodes[m.to]) : null, hot: false };
    if (L.particles.length >= MAX_DOTS) {
      // Too busy: no dot, the node still answers when it would have arrived.
      setTimeout(() => arrive(p), Math.max(0, start + dur - now));
      return;
    }
    L.particles.push(p);
    if (!L.raf) L.raf = requestAnimationFrame(tick);
  }

  const SVGNS = "http://www.w3.org/2000/svg";
  function makeDot(p) {
    const g = document.createElementNS(SVGNS, "g");
    g.setAttribute("class", `wch-dotp tone-${p.m.tone}${p.m.end === "drop" && !p.travel ? " is-drop" : ""}`);
    const halo = document.createElementNS(SVGNS, "circle");
    halo.setAttribute("r", "9"); halo.setAttribute("class", "halo");
    const core = document.createElementNS(SVGNS, "circle");
    core.setAttribute("r", "4"); core.setAttribute("class", "core");
    g.appendChild(halo); g.appendChild(core);
    $("wch-dots").appendChild(g);
    return g;
  }

  function tick(t) {
    L.raf = 0;
    if (motionOff()) { clearDots(); paintCounters(); return; }
    const keep = [];
    for (const p of L.particles) {
      if (t < p.start) { keep.push(p); continue; }
      if (!p.el) { p.el = makeDot(p); if (p.travel) heat(edgeKey(p.m)); }
      const k = Math.min(1, (t - p.start) / p.dur);
      let x, y, o = 1, s = 1;
      if (p.travel) {
        const pt = C.pointAt(p.curve, C.ease(k));
        x = pt.x; y = pt.y;
        o = k < 0.12 ? k / 0.12 : 1;
      } else {
        const n = L.geo.nodes[p.m.to];
        x = n.x; y = n.y;
        if (p.m.end === "drop") { y += 14 * k; o = 1 - k; s = 1 - 0.4 * k; }
        else { o = 1 - k; s = 1 + 0.8 * k; }
      }
      p.el.setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)}) scale(${s.toFixed(2)})`);
      p.el.style.opacity = o.toFixed(2);
      if (k >= 1) { p.el.remove(); arrive(p); } else keep.push(p);
    }
    L.particles = keep;
    if (keep.length) L.raf = requestAnimationFrame(tick);
  }

  const edgeKey = (m) => C.edgeOf(m.from, m.to);
  function heat(edge) {
    if (!edge) return;
    const el = $("wch-hot").querySelector(`[data-edge="${CSS.escape(edge)}"]`);
    if (!el) return;
    el.classList.remove("on");
    // Two frames later the class comes back, which restarts the fade.
    requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add("on")));
  }

  /** A dot reached its node: the node's ring answers (a fire glows), the counters roll. */
  function arrive(p) {
    const end = p.m.end;
    if (end === "glow" || end === "land" || end === "pulse" || (end === "pass" && p.m.to !== "filter")) pulse(p.m.to, end === "glow" ? "is-glow" : "");
    paintCounters();
  }
  function pulse(nodeId, extra) {
    if (motionOff()) return;
    const node = $("wch-nodes").querySelector(`[data-node="${CSS.escape(nodeId)}"]`);
    if (!node) return;
    const ring = node.querySelector(".wch-ring");
    if (!ring) return;
    ring.className = "wch-ring";
    requestAnimationFrame(() => requestAnimationFrame(() => { ring.className = `wch-ring on ${extra || ""}`.trim(); }));
  }

  function clearDots() {
    if (L.raf) { cancelAnimationFrame(L.raf); L.raf = 0; }
    for (const p of L.particles) if (p.el) p.el.remove();
    L.particles = [];
    L.ready = {};
    const dots = $("wch-dots");
    if (dots) dots.innerHTML = "";
  }

  // ---------- the feed ----------

  function feedTime(at) {
    const d = C.parseWhen(at);
    return d ? `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}` : "";
  }
  function paintFeed(initial) {
    const list = $("wch-feed");
    if (!list || !L.state || S.view !== "live") return;
    const entries = L.state.feed.map((e) => ({ e, w: C.feedWords(e) })).filter((x) => L.feed === "all" || !x.w.quiet);
    if (!entries.length) {
      list.innerHTML = `<li class="wch-feed-empty">${L.feed === "all" ? "Waiting for the next event…" : "Nothing matched yet."}</li>`;
      return;
    }
    list.innerHTML = entries.map(({ e, w }) => {
      const fresh = !initial && !L.feedDrawn.has(e.key) && !A.motion.reduced();
      return `
        <li class="wch-f hue-${w.tone}${w.quiet ? " is-quiet" : ""}${fresh ? " dn-new" : ""}">
          <span class="wch-f-ico">${icon(e.icon)}</span>
          <div class="wch-f-text">
            <div class="wch-f-head"><span class="wch-f-sum" dir="auto">${esc(w.head)}</span><span class="wch-f-time">${esc(feedTime(e.at))}</span></div>
            ${w.steps.length ? `<div class="wch-f-steps" dir="auto">${w.steps.map((s) => `<span>→ ${esc(s)}</span>`).join(" ")}</div>` : ""}
          </div>
        </li>`;
    }).join("");
    for (const { e } of entries) L.feedDrawn.add(e.key);
    A.icons();
  }

  // =====================================================================================================
  // views and wiring
  // =====================================================================================================

  function setView(v) {
    S.view = v === "live" ? "live" : "list";
    A.store.set("dana_watches_view", S.view);
    $("wch-list-view").classList.toggle("hidden", S.view !== "list");
    $("wch-live-view").classList.toggle("hidden", S.view !== "live");
    for (const b of root.querySelectorAll(".wch-switch [data-view]")) {
      const on = b.dataset.view === S.view;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", String(on));
    }
    if (S.view === "live") { startLive(); requestAnimationFrame(() => layoutStage(true)); }
    else stopLive();
  }

  function wire() {
    root.addEventListener("click", (ev) => {
      const t = ev.target;
      let n;
      if ((n = t.closest("[data-view]"))) { setView(n.dataset.view); return; }
      if ((n = t.closest("[data-undo]"))) { undo(n.dataset.undo); return; }
      if ((n = t.closest("[data-example]"))) { const ta = $("wch-new-text"); ta.value = n.dataset.example; autosize(ta); ta.focus(); return; }
      if ((n = t.closest("[data-feed]"))) {
        L.feed = n.dataset.feed;
        root.querySelectorAll("[data-feed]").forEach((b) => b.classList.toggle("active", b === n));
        paintFeed(true);
        return;
      }
      if ((n = t.closest("[data-menu]"))) { openRowMenu(n); return; }
      if ((n = t.closest("[data-open]"))) { if (n.dataset.open !== S.selected) select(n.dataset.open); else if (narrow()) $("wch-list-view").classList.add("detail-open"); return; }
      if ((n = t.closest("[data-set]"))) {
        const path = n.dataset.set, p = path.split(".");
        S.draft = p.length === 1 ? C.setSpec(S.draft, path, n.dataset.value) : C.setAction(S.draft, p[0], +p[1], p[2], n.dataset.value);
        edited({ rerender: true });
        return;
      }
      if (!(n = t.closest("[data-act]"))) return;
      const act = n.dataset.act, i = n.dataset.i, j = +n.dataset.j;
      switch (act) {
        case "close": $("wch-list-view").classList.remove("detail-open"); break;
        case "toggle": { const w = watchOf(S.selected); if (w) setStatus(w.id, w.status === "active" ? "paused" : "active"); break; }
        case "more": detailMenu(n); break;
        case "add-trig": addTriggerMenu(n); break;
        case "rm-trig": setDraft(C.removeTrigger(S.draft, +i)); break;
        case "add-cond": setDraft(C.addCondition(S.draft, +i, S.catalog)); break;
        case "rm-cond": setDraft(C.removeCondition(S.draft, +i, j)); break;
        case "rm-kw": setDraft(C.removeKeyword(S.draft, i === "evidence" ? "evidence" : +i, j)); break;
        case "add-act": setDraft(C.addAction(S.draft, n.dataset.list, "notify")); break;
        case "rm-act": setDraft(C.removeAction(S.draft, n.dataset.list, +i)); break;
        case "ev-type": setDraft(C.toggleEvidenceType(S.draft, n.dataset.type)); break;
        case "samples": {
          const tid = n.dataset.tid;
          if (S.openSamples.has(tid)) S.openSamples.delete(tid); else S.openSamples.add(tid);
          paintLoads();
          break;
        }
        case "run": A.openSession(n.dataset.session); break;
        case "trace-filter":
          if (S.selected && n.dataset.value !== S.traceFilter) { S.traceFilter = n.dataset.value; S.trace = null; renderTrace(); loadTrace(S.selected); }
          break;
        case "save": save(); break;
        case "discard": discard(); break;
        default: break;
      }
    });

    root.addEventListener("input", (ev) => {
      const el = ev.target;
      if (el.id === "wch-q") { S.q = el.value; renderList(); return; }
      if (el.id === "wch-new-text") { autosize(el); if (S.createError) { S.createError = ""; renderNewError(); } return; }
      if (!el.dataset.path || !S.draft || el.tagName === "SELECT") return;
      S.draft = applyField(el);
      edited();
    });
    root.addEventListener("change", (ev) => {
      const el = ev.target;
      if (!el.dataset.path || !S.draft) return;
      S.draft = applyField(el);
      // A select can change the card's shape (an op with a second value, another event type's fields).
      edited({ rerender: el.tagName === "SELECT" || el.type === "number" });
    });
    root.addEventListener("keydown", (ev) => {
      const el = ev.target;
      if (el.id === "wch-new-text" && ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); createFromWords(el.value); return; }
      if (el.dataset && el.dataset.kw !== undefined) {
        if ((ev.key === "Enter" || ev.key === ",") && el.value.trim()) {
          ev.preventDefault();
          const i = el.dataset.kw === "evidence" ? "evidence" : +el.dataset.kw;
          setDraft(C.addKeyword(S.draft, i, el.value));
          const again = $("wch-detail").querySelector(`[data-kw="${CSS.escape(el.dataset.kw)}"]`);
          if (again) again.focus();
        } else if (ev.key === "Backspace" && !el.value) {
          const i = el.dataset.kw === "evidence" ? "evidence" : +el.dataset.kw;
          const list = i === "evidence" ? (S.draft.evidence_keywords || []) : ((S.draft.triggers[i] || {}).keywords || []);
          if (list.length) {
            setDraft(C.removeKeyword(S.draft, i, list.length - 1));
            const again = $("wch-detail").querySelector(`[data-kw="${CSS.escape(el.dataset.kw)}"]`);
            if (again) again.focus();
          }
        }
        return;
      }
      if ((ev.metaKey || ev.ctrlKey) && ev.key === "s" && S.draft && dirty()) { ev.preventDefault(); save(); }
    });
    root.addEventListener("submit", (ev) => {
      ev.preventDefault();
      if (ev.target.id === "wch-new") createFromWords($("wch-new-text").value);
      else if (ev.target.dataset.form === "change") changeInWords();
    });

    document.addEventListener("visibilitychange", () => {
      if (S.view !== "live") return;
      if (document.hidden) stopLive();
      else if (A.visible()) startLive();
    });
    if (window.ResizeObserver) {
      L.ro = new ResizeObserver(() => { if (S.view === "live") layoutStage(false); });
      L.ro.observe($("wch-stage"));
    }
  }

  Dana.panel({
    scripts: ["watches-core.js"],
    opens: ["watch"],
    start(api) { A = api; C = window.DanaWatchesCore; },
    async mount(el, _api, arg) {
      if (!root) {
        root = el;
        el.innerHTML = TEMPLATE;
        wire();
        renderList();
        await loadCatalog();
      }
      const wantLive = arg === "live";
      setView(wantLive ? "live" : (arg ? "list" : (A.store.get("dana_watches_view", "list") === "live" ? "live" : "list")));
      const first = !S.loaded;
      await loadList();
      if (first) A.motion.arrive($("wch-list"));
      if (arg && arg !== "live" && watchOf(arg)) select(arg, { force: true });
      else if (!S.selected) renderDetail();
    },
    unmount() { stopLive(); },
  });
})();
