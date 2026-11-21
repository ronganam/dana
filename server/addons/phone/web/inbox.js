/* Inbox — phone notifications, managed from the web.
 *
 * Talks to the same endpoints as the Android Inbox:
 *   GET  /api/notifications?status=&bucket=&q=     (+ counts)
 *   POST /api/notifications/status | /snooze       (dismiss / restore / snooze)
 *   POST /api/notifications/{id}/act               (press a button on the phone)
 *   GET  /api/notifications/requests/{rid}         (did the phone do it?)
 *   GET  /api/notifications/summaries | /apps      (AI summaries, per-app rules)
 *   GET  /api/suggestions?source=all               (tasks / events to create)
 * The phone addon's page (Dana.panel, static/dana.js): mounted when opened, the needs-you count on its
 * navigation button kept fresh in the background.
 */
(() => {
  "use strict";

  const VIEWS = [
    { id: "needs", label: "Needs you", q: { status: "active", bucket: "needs_you" }, count: "needs_you" },
    { id: "all", label: "Everything", q: { status: "active" }, count: "active" },
    { id: "dismiss", label: "Suggested to dismiss", q: { status: "active", bucket: "dismiss" }, count: "dismiss" },
    { id: "snoozed", label: "Snoozed", q: { status: "snoozed" }, count: "snoozed" },
    { id: "history", label: "Dismissed", q: { status: "dismissed" } },
  ];
  const RULES = [
    { id: "none", label: "Normal" },
    { id: "important", label: "Always needs me" },
    { id: "alert", label: "Alert me" },
    { id: "mute", label: "Suggest dismissing" },
  ];
  const POLL_MS = 20000;
  const BADGE_MS = 60000;

  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (_) { return null; } };

  const S = {
    view: store("ibx.view") || "needs",
    q: "",
    group: store("ibx.group") === "1",
    items: [],
    counts: {},
    suggestions: [],
    summaries: null,
    apps: null,          // {package: {rule, ...}}
    rules: [],
    openId: null,
    cursor: -1,
    selected: new Set(),
    anchor: null,
    collapsed: new Set(),
    suggOpen: store("ibx.suggOpen") !== "0",
    suggAll: false,
    visible: false,
    loading: false,
    loadedOnce: false,
    actions: {},         // notification id → {state, label, error}
  };

  let $ = {};
  let A = null;          // the page's Dana api (static/dana.js)
  let pollTimer = null;
  let searchTimer = null;
  let toastTimer = null;

  // ---------- helpers ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };
  const hue = (s) => { let h = 0; for (const c of String(s || "")) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
  const initial = (s) => (String(s || "?").trim()[0] || "?").toUpperCase();

  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.body !== undefined) { init.method = opts.method || "POST"; init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    const r = await fetch(path, init);
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) { const e = new Error((data && data.error) || `HTTP ${r.status}`); e.status = r.status; throw e; }
    return data;
  }

  function age(iso) {
    if (!iso) return "";
    const t = new Date(iso).getTime();
    if (isNaN(t)) return "";
    const m = Math.round((Date.now() - t) / 60000);
    if (m < 1) return "now";
    if (m < 60) return `${m}m`;
    const h = Math.round(m / 60);
    if (h < 24) return `${h}h`;
    const d = Math.round(h / 24);
    if (d < 7) return `${d}d`;
    return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  function when(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const today = new Date(); const tmr = new Date(); tmr.setDate(today.getDate() + 1);
    const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === today.toDateString()) return `today ${time}`;
    if (d.toDateString() === tmr.toDateString()) return `tomorrow ${time}`;
    return `${d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })} ${time}`;
  }
  const appName = (n) => n.app_name || (n.package || "").split(".").pop() || "App";
  const who = (n) => [n.sender, n.conversation && n.conversation !== n.sender ? n.conversation : null].filter(Boolean).join(" · ");
  const prioClass = (n) => (n.priority === "urgent" ? "prio-urgent" : n.priority === "important" ? "prio-important" : "");
  const pendingSuggs = (n) => (n.suggestions || []).filter((s) => s.status === "pending");
  const viewDef = () => VIEWS.find((v) => v.id === S.view) || VIEWS[0];
  const isActiveView = () => ["needs", "all", "dismiss"].includes(S.view);

  // ---------- data ----------
  async function load({ quiet = false } = {}) {
    if (S.loading) return;
    S.loading = true;
    if (!quiet && !S.loadedOnce) renderList();
    try {
      const params = new URLSearchParams({ ...viewDef().q, limit: "300" });
      if (S.q) params.set("q", S.q);
      const wantSugg = S.view === "needs" || S.view === "all";
      const [list, sugg] = await Promise.all([
        api(`/api/notifications?${params}`),
        wantSugg ? api("/api/suggestions?status=pending&source=all").catch(() => null) : Promise.resolve(null),
      ]);
      S.items = list.items || [];
      S.counts = list.counts || {};
      S.suggestions = Array.isArray(sugg) ? sugg : sugg ? (sugg.items || sugg.suggestions || []) : [];
      const ids = new Set(S.items.map((n) => n.id));
      for (const id of [...S.selected]) if (!ids.has(id)) S.selected.delete(id);
      S.loadedOnce = true;
      setBadge(S.counts);
    } catch (e) {
      if (!quiet) toast(`Couldn't load the Inbox: ${e.message}`);
    } finally {
      S.loading = false;
    }
    renderAll();
    if (isActiveView()) loadSummaries();
  }

  async function loadSummaries(refresh = false) {
    try {
      S.summaries = await api(`/api/notifications/summaries${refresh ? "?refresh=1" : ""}`);
      renderList({ keepScroll: true });
      if (S.summaries.refreshing && S.visible) setTimeout(() => S.visible && loadSummaries(), 5000);
    } catch (_) { /* summaries are optional */ }
  }

  async function loadApps() {
    try {
      const d = await api("/api/notifications/apps");
      S.apps = {};
      for (const a of d.apps || []) S.apps[a.package] = a;
      S.rules = d.rules || [];
    } catch (_) { S.apps = S.apps || {}; }
  }

  function ruleFor(pkg) {
    const r = (S.rules || []).find((x) => x.package === pkg && !x.sender);
    if (r) return r.action;
    const a = S.apps && S.apps[pkg];
    return (a && (a.rule?.action || a.rule)) || "none";
  }

  function setBadge(counts) {
    A?.badge((counts.needs_you || 0) + (counts.pending_suggestions || 0));
  }
  async function pollBadge() {
    if (S.visible || document.hidden) return;
    try { setBadge((await api("/api/notifications?status=active&bucket=needs_you&limit=1")).counts || {}); } catch (_) { /* offline */ }
  }

  // ---------- list ----------
  function rows() {
    // The order the list shows them in (used by j/k and shift-select).
    if (!S.group) return S.items;
    return groups().flatMap((g) => (S.collapsed.has(g.pkg) ? [] : g.items));
  }
  function groups() {
    const m = new Map();
    for (const n of S.items) {
      if (!m.has(n.package)) m.set(n.package, { pkg: n.package, name: appName(n), items: [] });
      m.get(n.package).items.push(n);
    }
    return [...m.values()].sort((a, b) => b.items.length - a.items.length);
  }

  function renderAll() {
    renderViews();
    renderKicker();
    renderList({ keepScroll: true });
    renderBulk();
    renderDetail();
  }

  function renderKicker() {
    const c = S.counts;
    const bits = [];
    if (c.needs_you) bits.push(`${c.needs_you} need you`);
    if (c.pending_suggestions) bits.push(`${c.pending_suggestions} to review`);
    if (c.active) bits.push(`${c.active} active`);
    $.kicker.textContent = bits.join(" · ");
  }

  function renderViews() {
    $.views.innerHTML = VIEWS.map((v) => {
      const n = v.count ? S.counts[v.count] : null;
      return `<button class="chip${v.id === S.view ? " active" : ""}" role="tab" aria-selected="${v.id === S.view}" data-view="${v.id}">${esc(v.label)}${n ? ` <span class="n">${n}</span>` : ""}</button>`;
    }).join("");
  }

  function summaryPoints(entry, cls = "") {
    const pts = (entry && (entry.points || (entry.summary || "").split("\n"))) || [];
    const clean = pts.map((p) => String(p).replace(/^[-•*]\s*/, "").trim()).filter(Boolean);
    if (!clean.length) return "";
    return `<ul class="ibx-points ${cls}${entry.stale ? " stale" : ""}">${clean.map((p) => `<li dir="auto">${esc(p)}</li>`).join("")}</ul>`;
  }

  function rowHtml(n, indent = false) {
    const sel = S.selected.has(n.id);
    const sug = pendingSuggs(n)[0];
    const meta = [];
    if (n.priority === "urgent") meta.push('<span class="urgent">Urgent</span>');
    else if (n.priority === "important") meta.push('<span class="important">Important</span>');
    if (!indent || who(n)) meta.push(esc(indent ? who(n) : [appName(n), who(n)].filter(Boolean).join(" · ")));
    if (n.snoozed_until) meta.push(`until ${esc(when(n.snoozed_until))}`);
    if (S.view === "history" && n.removed_reason) meta.push(esc(n.removed_reason));
    const title = n.title || n.sender || appName(n);
    const actions = isActiveView()
      ? `<button class="icon-btn" data-act="dismiss" title="Dismiss (e)"><i data-lucide="check"></i></button>
         <button class="icon-btn" data-act="snooze" title="Snooze (s)"><i data-lucide="clock"></i></button>`
      : S.view === "snoozed"
        ? `<button class="icon-btn" data-act="unsnooze" title="Wake now"><i data-lucide="bell-ring"></i></button>`
        : `<button class="icon-btn" data-act="restore" title="Restore"><i data-lucide="undo-2"></i></button>`;
    return `<div class="ibx-row ${prioClass(n)}${indent ? " indent" : ""}${sel ? " selected" : ""}${S.openId === n.id ? " open" : ""}" data-id="${esc(n.id)}" role="option" aria-selected="${sel}">
      <span class="ibx-check" data-act="select" role="checkbox" aria-checked="${sel}" title="Select (x)"></span>
      ${indent ? "" : `<span class="ibx-avatar" style="--h:${hue(n.package)}">${esc(initial(appName(n)))}<span class="emo">${esc(n.category_emoji || "")}</span></span>`}
      <div class="ibx-main">
        <div class="ibx-line1"><span class="ibx-title" dir="auto">${esc(title)}</span><span class="ibx-age">${esc(age(n.posted_at))}</span></div>
        ${meta.length ? `<div class="ibx-meta" dir="auto">${meta.join(" · ")}</div>` : ""}
        ${n.text ? `<div class="ibx-text" dir="auto">${esc(n.text)}</div>` : ""}
        ${sug ? `<span class="ibx-sugg-chip" dir="auto">✨ ${esc(sug.payload?.title || "Suggestion")}</span>` : ""}
      </div>
      <div class="ibx-row-actions">${actions}</div>
    </div>`;
  }

  function suggestionsHtml() {
    if (!S.suggestions.length || S.q) return "";
    const shown = S.suggAll ? S.suggestions : S.suggestions.slice(0, 3);
    const more = S.suggestions.length - shown.length;
    const list = S.suggOpen ? shown.map(suggHtml).join("") + (more > 0 ? `<div style="padding:0 16px 10px"><button class="ibx-link" data-act="sugg-more">Show ${more} more</button></div>` : "") : "";
    return `<div class="ibx-section"><button class="ibx-fold" data-act="fold-sugg"><i data-lucide="${S.suggOpen ? "chevron-down" : "chevron-right"}"></i>Suggestions · ${S.suggestions.length}</button><span class="grow"></span>
      ${S.suggestions.length > 1 ? '<button class="ibx-link" data-act="sugg-dismiss-all">Dismiss all</button>' : ""}</div>${list}`;
  }

  function trigList(p) {
    const t = p.triggers || {};
    const out = [];
    if (p.due_date) out.push(`📅 due ${p.due_date}`);
    if (t.time) out.push(`⏰ ${t.time}`);
    if (t.location) out.push(`📍 ${t.location.event === "leave" ? "leaving" : "arriving"} ${t.location.location}`);
    if (t.car) out.push(`🚗 ${t.car === "leave" ? "leaving" : "getting in"} the car`);
    if (t.wake) out.push("🌅 when I wake up");
    if (p.start) out.push(`🗓 ${when(p.start)}`);
    if (p.location) out.push(`📍 ${p.location}`);
    return out;
  }

  function suggHtml(s) {
    const p = s.payload || {};
    const src = s.notification ? [s.notification.app_name, s.notification.sender || s.notification.title].filter(Boolean).join(" · ")
      : s.call ? `Call · ${s.call.contact || ""}` : "";
    const kind = s.kind === "event" ? "Event" : s.kind === "alert" ? "Alert" : "Task";
    const trigs = trigList(p);
    return `<div class="ibx-sugg" data-sugg="${esc(s.id)}">
      <div class="ibx-sugg-kicker" dir="auto">${esc(kind)}${src ? ` · from ${esc(src)}` : ""}</div>
      <div class="ibx-sugg-title" dir="auto">${esc(p.title || "(untitled)")}</div>
      ${trigs.length ? `<div class="ibx-trigs">${trigs.map((t) => `<span class="ibx-trig" dir="auto">${esc(t)}</span>`).join("")}</div>` : ""}
      ${p.evidence ? `<div class="ibx-sugg-ev" dir="auto">“${esc(p.evidence)}”</div>` : ""}
      <div class="ibx-btns">
        <button class="btn-primary" data-act="sugg-accept"><i data-lucide="plus"></i>${s.kind === "event" ? "Add to calendar" : "Create task"}</button>
        <button class="btn-secondary" data-act="sugg-edit"><i data-lucide="pencil"></i>Edit</button>
        <button class="btn-secondary" data-act="sugg-ask"><i data-lucide="sparkles"></i>Ask Dana</button>
        <button class="btn-ghost-danger" data-act="sugg-dismiss">Dismiss</button>
      </div>
    </div>`;
  }

  function emptyHtml() {
    if (!S.loadedOnce) return `<div class="ibx-empty"><i data-lucide="loader"></i>Loading…</div>`;
    if (S.q) return `<div class="ibx-empty"><i data-lucide="search-x"></i><strong>Nothing matches “${esc(S.q)}”</strong>Try another word, or look in Everything / Dismissed.</div>`;
    const msg = {
      needs: ["Nothing needs you", "Everything important is handled."],
      all: ["Inbox zero", "No notifications on your phone right now."],
      dismiss: ["Nothing to clean up", "Dana hasn't flagged anything as safe to dismiss."],
      snoozed: ["Nothing snoozed", "Snooze a notification and it comes back here."],
      history: ["No dismissed notifications", ""],
    }[S.view];
    return `<div class="ibx-empty"><i data-lucide="inbox"></i><strong>${msg[0]}</strong>${msg[1]}</div>`;
  }

  function renderList({ keepScroll = false } = {}) {
    if (!$.list) return;
    const top = $.list.scrollTop;
    let html = "";
    const sums = S.summaries;
    if (isActiveView() && !S.q && sums && sums.overall && (sums.overall.points?.length || sums.overall.summary)) {
      html += `<div class="ibx-card"><div class="ibx-card-label"><span class="spark">✦</span>At a glance${sums.refreshing ? " · updating…" : ""}</div>${summaryPoints(sums.overall)}</div>`;
    }
    if (S.view === "dismiss" && S.items.length) {
      html += `<div class="ibx-section">Dana thinks these are safe to clear<span class="grow"></span><button class="ibx-link" data-act="dismiss-bucket">Dismiss all ${S.items.length}</button></div>`;
    }
    if (!S.items.length) {
      html += emptyHtml();
    } else if (S.group) {
      for (const g of groups()) {
        const coll = S.collapsed.has(g.pkg);
        const urgent = g.items.filter((n) => n.priority === "urgent" || n.priority === "important").length;
        const sum = isActiveView() && !S.q ? sums?.apps?.[g.pkg] : null;
        html += `<div class="ibx-group" data-group="${esc(g.pkg)}">
          <span class="ibx-avatar" style="--h:${hue(g.pkg)}">${esc(initial(g.name))}</span>
          <div class="ibx-main">
            <div class="ibx-group-name"><i data-lucide="${coll ? "chevron-right" : "chevron-down"}" style="width:14px;height:14px;color:var(--mute)"></i>${esc(g.name)}
              <span class="ibx-pill">${g.items.length}</span>${urgent ? `<span class="ibx-pill warn">${urgent} important</span>` : ""}</div>
            ${sum ? summaryPoints(sum, "small") : ""}
          </div>
          <div class="tools">
            ${isActiveView() ? `<button class="icon-btn" data-act="group-dismiss" title="Dismiss all from ${esc(g.name)}"><i data-lucide="check-check"></i></button>` : ""}
          </div>
        </div>`;
        if (!coll) html += g.items.map((n) => rowHtml(n, true)).join("");
      }
    } else {
      if (S.view === "needs" || S.view === "all") html += `<div class="ibx-section">${S.view === "needs" ? "Needs you" : "Notifications"} · ${S.items.length}</div>`;
      html += S.items.map((n) => rowHtml(n)).join("");
    }
    if (S.view === "needs" || S.view === "all") html += suggestionsHtml();
    $.list.innerHTML = html;
    $.root.classList.toggle("selecting", S.selected.size > 0);
    $.group.classList.toggle("active", S.group);
    if (keepScroll) $.list.scrollTop = top;
    markCursor();
    icons();
  }

  function markCursor() {
    const list = rows();
    const id = list[S.cursor]?.id;
    $.list.querySelectorAll(".ibx-row.cursor").forEach((r) => r.classList.remove("cursor"));
    if (id) $.list.querySelector(`.ibx-row[data-id="${CSS.escape(id)}"]`)?.classList.add("cursor");
  }

  function renderBulk() {
    const n = S.selected.size;
    $.bulk.classList.toggle("hidden", n === 0);
    $.root.classList.toggle("selecting", n > 0);
    if (!n) { $.bulk.innerHTML = ""; return; }
    const act = isActiveView();
    $.bulk.innerHTML = `<span class="count">${n} selected</span>
      ${act ? `<button class="btn-secondary" data-act="bulk-dismiss"><i data-lucide="check"></i>Dismiss</button>
      <span class="ibx-menu-wrap"><button class="btn-secondary" data-act="bulk-snooze"><i data-lucide="clock"></i>Snooze</button></span>` : ""}
      ${S.view === "snoozed" ? `<button class="btn-secondary" data-act="bulk-unsnooze"><i data-lucide="bell-ring"></i>Wake now</button>` : ""}
      ${S.view === "history" ? `<button class="btn-secondary" data-act="bulk-restore"><i data-lucide="undo-2"></i>Restore</button>` : ""}
      <button class="chip" data-act="select-all">Select all</button>
      <button class="icon-btn" data-act="select-clear" title="Clear selection (Esc)"><i data-lucide="x"></i></button>`;
    icons();
  }

  // ---------- detail ----------
  function current() { return S.items.find((n) => n.id === S.openId) || null; }

  function renderDetail() {
    const n = current();
    $.root.classList.toggle("detail-open", !!n);
    if (!n) {
      const c = S.counts;
      $.detail.innerHTML = `<div class="ibx-empty" style="padding-top:18vh"><i data-lucide="mouse-pointer-click"></i>
        <strong>Pick a notification</strong>
        ${c.active ? `${c.active} active${c.needs_you ? `, ${c.needs_you} need you` : ""}.` : ""}
        <span style="margin-top:6px">Press <kbd style="font-family:var(--font-mono)">?</kbd> for keyboard shortcuts</span></div>`;
      icons();
      return;
    }
    const sug = pendingSuggs(n);
    const sig = n.signals || {};
    const tags = [];
    if (n.priority === "urgent") tags.push('<span class="ibx-tag urgent">Urgent</span>');
    else if (n.priority === "important") tags.push('<span class="ibx-tag important">Important</span>');
    if (n.category) tags.push(`<span class="ibx-tag">${esc(n.category_emoji || "")} ${esc(n.category.replace(/_/g, " "))}</span>`);
    tags.push(`<span class="ibx-tag">${esc({ needs_you: "Needs you", dismiss: "Suggested to dismiss", normal: "Normal" }[n.bucket] || n.bucket)}</span>`);
    if (n.escalated) tags.push('<span class="ibx-tag urgent">Alerted on phone</span>');
    if (n.is_group) tags.push('<span class="ibx-tag">Group chat</span>');
    if (n.snoozed_until) tags.push(`<span class="ibx-tag">Snoozed until ${esc(when(n.snoozed_until))}</span>`);
    if (n.triage_status === "failed") tags.push('<span class="ibx-tag urgent">Triage failed</span>');
    else if (n.triage_status && n.triage_status !== "done") tags.push(`<span class="ibx-tag">Triage ${esc(n.triage_status)}</span>`);

    const rule = ruleFor(n.package);
    const statusBtns = isActiveView()
      ? `<button class="btn-primary" data-act="dismiss"><i data-lucide="check"></i>Dismiss</button>
         <span class="ibx-menu-wrap"><button class="btn-secondary" data-act="snooze"><i data-lucide="clock"></i>Snooze</button></span>`
      : S.view === "snoozed"
        ? `<button class="btn-primary" data-act="unsnooze"><i data-lucide="bell-ring"></i>Wake now</button>
           <button class="btn-secondary" data-act="dismiss"><i data-lucide="check"></i>Dismiss</button>`
        : `<button class="btn-primary" data-act="restore"><i data-lucide="undo-2"></i>Restore to Inbox</button>`;

    $.detail.innerHTML = `<div class="ibx-detail" data-id="${esc(n.id)}">
      <div class="ibx-detail-head">
        <button class="icon-btn ibx-close" data-act="close" title="Back"><i data-lucide="arrow-left"></i></button>
        <span class="ibx-avatar lg" style="--h:${hue(n.package)}">${esc(initial(appName(n)))}<span class="emo">${esc(n.category_emoji || "")}</span></span>
        <div class="who"><div class="app">${esc(appName(n))}</div><div class="sub" dir="auto">${esc([who(n), when(n.posted_at)].filter(Boolean).join(" · "))}</div></div>
        <button class="btn-secondary" data-act="ask"><i data-lucide="sparkles"></i>Ask Dana</button>
      </div>
      <div class="ibx-tags">${tags.join("")}</div>
      ${n.title ? `<h2 dir="auto">${esc(n.title)}</h2>` : ""}
      ${n.text ? `<div class="ibx-body" dir="auto">${esc(n.text)}</div>` : ""}
      ${n.sub_text ? `<div class="ibx-note" dir="auto" style="margin-top:6px">${esc(n.sub_text)}</div>` : ""}

      <div class="ibx-block"><div class="ibx-btns">${statusBtns}</div></div>

      ${phoneHtml(n)}

      ${sug.length ? `<div class="ibx-block"><div class="ibx-block-label">Dana suggests</div>${sug.map(suggHtml).join("").replaceAll('class="ibx-sugg"', 'class="ibx-sugg" style="margin:0 0 8px"')}</div>` : ""}

      <div class="ibx-block"><div class="ibx-block-label">Notifications from ${esc(appName(n))}</div>
        <div class="ibx-seg">${RULES.map((r) => `<button class="chip${r.id === rule ? " active" : ""}" data-act="rule" data-rule="${r.id}">${esc(r.label)}</button>`).join("")}</div>
        <div class="ibx-note" style="margin-top:8px">${esc(ruleHint(rule))}</div>
      </div>

      ${Object.keys(sig).length ? `<div class="ibx-block"><div class="ibx-block-label">Why Dana sorted it this way</div><div class="ibx-signals">
        ${Object.entries(sig).map(([k, v]) => { const p = Math.round((Number(v) || 0) * 100); return `<span>${esc(k.replace(/_/g, " "))}</span><span class="ibx-meter"><i style="width:${p}%"></i></span><span class="v">${p}%</span>`; }).join("")}
      </div></div>` : ""}
    </div>`;
    icons();
  }

  function ruleHint(rule) {
    return {
      none: "Dana decides per notification.",
      important: "Always goes to Needs you.",
      alert: "Always goes to Needs you and opens the full-screen alert on your phone.",
      mute: "Goes straight to Suggested to dismiss, without asking the model.",
    }[rule] || "";
  }

  function phoneHtml(n) {
    const st = S.actions[n.id];
    const status = st ? `<div class="ibx-status ${st.state === "done" ? "ok" : st.state === "pending" ? "" : "err"}" role="status">
      ${st.state === "pending" ? '<span class="dot"></span>' : ""}${esc(st.text)}</div>` : "";
    if (!n.on_phone) {
      return `<div class="ibx-block"><div class="ibx-block-label">On your phone</div>
        <div class="ibx-note">${n.status === "active" && n.snoozed_until ? "Snoozed on the phone — wake it to use its buttons." : "No longer in the phone's notification shade, so its buttons can't be pressed from here."}</div>${status}</div>`;
    }
    const acts = n.actions || [];
    const replies = acts.filter((a) => a.reply);
    const buttons = acts.filter((a) => !a.reply);
    const busy = st && st.state === "pending";
    return `<div class="ibx-block"><div class="ibx-block-label"><i data-lucide="smartphone" style="width:13px;height:13px"></i>On your phone</div>
      <div class="ibx-phone">
        <div class="ibx-btns">
          <button class="btn-secondary" data-act="phone" data-action="open" ${busy ? "disabled" : ""}><i data-lucide="external-link"></i>Open on phone</button>
          ${buttons.map((a) => `<button class="btn-secondary" data-act="phone" data-action="${esc(a.title)}" ${busy ? "disabled" : ""} dir="auto">${esc(a.title)}</button>`).join("")}
        </div>
        ${replies.map((a) => `<form class="ibx-reply" data-act="reply" data-action="${esc(a.title)}">
          <input class="form-control" name="text" dir="auto" placeholder="${esc(a.title)} as you…" maxlength="2000" autocomplete="off" ${busy ? "disabled" : ""}>
          <button class="btn-primary" type="submit" ${busy ? "disabled" : ""}><i data-lucide="send"></i>Send</button></form>`).join("")}
        ${status || (acts.length ? "" : '<div class="ibx-note" style="margin-top:8px">No buttons on this one. Open shows it on your phone (unlock to see it).</div>')}
      </div></div>`;
  }

  // ---------- actions ----------
  function open(id, { focusList = false } = {}) {
    S.openId = id;
    const i = rows().findIndex((n) => n.id === id);
    if (i >= 0) S.cursor = i;
    $.list.querySelectorAll(".ibx-row.open").forEach((r) => r.classList.remove("open"));
    const el = id && $.list.querySelector(`.ibx-row[data-id="${CSS.escape(id)}"]`);
    el?.classList.add("open");
    el?.scrollIntoView({ block: "nearest" });
    markCursor();
    renderDetail();
    if (focusList) $.list.focus({ preventScroll: true });
    if (id && !S.apps) loadApps().then(() => S.openId === id && renderDetail());
  }

  function removeLocal(ids) {
    const set = new Set(ids);
    const list = rows();
    let next = null;
    if (S.openId && set.has(S.openId)) {
      const i = list.findIndex((n) => n.id === S.openId);
      next = list.slice(i + 1).find((n) => !set.has(n.id)) || list.slice(0, i).reverse().find((n) => !set.has(n.id)) || null;
    }
    S.items = S.items.filter((n) => !set.has(n.id));
    ids.forEach((id) => S.selected.delete(id));
    if (S.openId && set.has(S.openId)) S.openId = window.innerWidth > 900 && next ? next.id : null;
    S.cursor = Math.min(S.cursor, rows().length - 1);
    renderList({ keepScroll: true });
    renderBulk();
    renderDetail();
  }

  async function setStatus(ids, status, { undo = true } = {}) {
    if (!ids.length) return;
    const prev = S.items.filter((n) => ids.includes(n.id)).map((n) => n.status);
    removeLocal(ids);
    try {
      await api("/api/notifications/status", { body: { ids, status } });
      const word = status === "dismissed" ? "Dismissed" : status === "active" ? "Restored" : "Archived";
      toast(`${word} ${ids.length === 1 ? "1 notification" : `${ids.length} notifications`}`,
        undo && status !== "active" ? () => setStatus(ids, prev[0] === "dismissed" ? "dismissed" : "active", { undo: false }) : null);
    } catch (e) { toast(`Failed: ${e.message}`); }
    load({ quiet: true });
  }

  async function snooze(ids, opt) {
    if (!ids.length) return;
    removeLocal(ids);
    try {
      const r = await api("/api/notifications/snooze", { body: { ids, ...opt } });
      toast(opt.clear ? "Back in the Inbox" : `Snoozed until ${when(r.until)}`, opt.clear ? null : () => snooze(ids, { clear: true }));
    } catch (e) { toast(`Failed: ${e.message}`); }
    load({ quiet: true });
  }

  function snoozeChoices() {
    const at = (days, h) => { const d = new Date(); d.setDate(d.getDate() + days); d.setHours(h, 0, 0, 0); return d; };
    const now = new Date();
    const out = [
      { label: "15 minutes", opt: { minutes: 15 }, hint: "" },
      { label: "1 hour", opt: { minutes: 60 }, hint: "" },
      { label: "3 hours", opt: { minutes: 180 }, hint: "" },
    ];
    const eve = at(0, 20);
    if (eve - now > 30 * 60000) out.push({ label: "This evening", opt: { until: eve.toISOString() }, hint: "20:00" });
    const mor = at(1, 8);
    out.push({ label: "Tomorrow morning", opt: { until: mor.toISOString() }, hint: mor.toLocaleDateString(undefined, { weekday: "short" }) + " 08:00" });
    const mon = at(((8 - now.getDay()) % 7) || 7, 8);
    if (now.getDay() !== 0) out.push({ label: "Next week", opt: { until: mon.toISOString() }, hint: mon.toLocaleDateString(undefined, { weekday: "short", day: "numeric" }) });
    return out;
  }

  function menu(anchor, items, onPick) {
    closeMenus();
    const wrap = anchor.closest(".ibx-menu-wrap") || anchor.parentElement;
    const m = document.createElement("div");
    m.className = "ibx-menu";
    if (anchor.getBoundingClientRect().left > window.innerWidth - 220) m.classList.add("right");
    m.innerHTML = items.map((it, i) => `<button data-i="${i}">${esc(it.label)}<span class="hint">${esc(it.hint || "")}</span></button>`).join("");
    m.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-i]");
      if (!b) return;
      e.stopPropagation();
      closeMenus();
      onPick(items[+b.dataset.i]);
    });
    m.addEventListener("keydown", (e) => {
      const bs = [...m.querySelectorAll("button")];
      const i = bs.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); bs[(i + 1) % bs.length].focus(); }
      if (e.key === "ArrowUp") { e.preventDefault(); bs[(i - 1 + bs.length) % bs.length].focus(); }
      if (e.key === "Escape") { e.stopPropagation(); closeMenus(); anchor.focus(); }
      if (/^[1-9]$/.test(e.key) && bs[+e.key - 1]) { e.preventDefault(); bs[+e.key - 1].click(); }
    });
    wrap.appendChild(m);
    m.querySelector("button")?.focus();
  }
  function closeMenus() { document.querySelectorAll(".ibx-menu").forEach((m) => m.remove()); }

  function snoozeMenu(anchor, ids) {
    menu(anchor, snoozeChoices().map((c, i) => ({ ...c, hint: c.hint || String(i + 1) })), (c) => snooze(ids, c.opt));
  }

  async function phoneAction(n, action, text = "") {
    const label = action === "open" ? "Opening on your phone" : text ? `Sending “${text.length > 40 ? text.slice(0, 40) + "…" : text}”` : `Pressing “${action}”`;
    S.actions[n.id] = { state: "pending", text: `${label}…` };
    renderDetail();
    let req;
    try {
      req = await api(`/api/notifications/${encodeURIComponent(n.id)}/act`, { body: { action, text } });
    } catch (e) {
      S.actions[n.id] = { state: "failed", text: e.message };
      renderDetail();
      if (e.status === 400) load({ quiet: true });
      return;
    }
    const started = Date.now();
    while (req && req.status === "pending" && Date.now() - started < 130000) {
      await new Promise((r) => setTimeout(r, 1200));
      try { req = await api(`/api/notifications/requests/${encodeURIComponent(req.id)}`); } catch (_) { /* retry */ }
    }
    const done = action === "open" ? "Opened on your phone ✓" : text ? "Sent from your phone ✓" : `“${action}” pressed on your phone ✓`;
    S.actions[n.id] = req?.status === "done" ? { state: "done", text: done }
      : req?.status === "expired" ? { state: "failed", text: "Your phone didn't answer — is it online and is Dana running?" }
        : { state: "failed", text: `Your phone couldn't do it${req?.error ? `: ${req.error}` : ""}` };
    if (S.openId === n.id) renderDetail();
    if (req?.status === "done") setTimeout(() => load({ quiet: true }), 2500);
  }

  async function setRule(pkg, action) {
    try {
      await api("/api/notifications/rules", { body: { package: pkg, action } });
      S.rules = (S.rules || []).filter((r) => !(r.package === pkg && !r.sender));
      if (action !== "none") S.rules.push({ package: pkg, action });
      renderDetail();
      toast(`${RULES.find((r) => r.id === action)?.label}: applies to new notifications`);
      loadApps();
    } catch (e) { toast(`Failed: ${e.message}`); }
  }

  async function askDana(kind, id) {
    try {
      const r = await api("/api/chat/context", { body: { kind, id } });
      if (r && r.session_id) A.openSession(r.session_id);
    } catch (e) { toast(`Couldn't start a chat: ${e.message}`); }
  }

  function findSugg(id) {
    return S.suggestions.find((s) => s.id === id) || S.items.flatMap((n) => n.suggestions || []).find((s) => s.id === id);
  }

  async function acceptSugg(id, overrides = {}) {
    const s = findSugg(id);
    try {
      await api(`/api/suggestions/${encodeURIComponent(id)}/accept`, { body: overrides });
      toast(s?.kind === "event" ? "Added to your calendar" : "Task created");
    } catch (e) { toast(`Failed: ${e.message}`); }
    load({ quiet: true });
  }
  async function dismissSugg(ids) {
    S.suggestions = S.suggestions.filter((s) => !ids.includes(s.id));
    renderList({ keepScroll: true });
    try {
      if (ids.length === 1) await api(`/api/suggestions/${encodeURIComponent(ids[0])}/dismiss`, { body: {} });
      else await api("/api/suggestions/bulk", { body: { ids, action: "dismiss" } });
      toast(ids.length === 1 ? "Suggestion dismissed" : `${ids.length} suggestions dismissed`);
    } catch (e) { toast(`Failed: ${e.message}`); }
    load({ quiet: true });
  }

  function editSugg(id) {
    const s = findSugg(id);
    if (!s) return;
    const p = s.payload || {};
    const t = p.triggers || {};
    const local = (iso) => (iso ? String(iso).replace(" ", "T").slice(0, 16) : "");
    const isEvent = s.kind === "event";
    $.modal.innerHTML = `<form class="ibx-modal-card" id="ibx-edit">
      <h3>${isEvent ? "Add to calendar" : "Create task"}</h3>
      <label class="ibx-field">Title<input class="form-control" name="title" dir="auto" required value="${esc(p.title || "")}"></label>
      ${isEvent ? `
        <div class="ibx-row2"><label class="ibx-field">Starts<input class="form-control" type="datetime-local" name="start" value="${esc(local(p.start))}"></label>
        <label class="ibx-field">Ends<input class="form-control" type="datetime-local" name="end" value="${esc(local(p.end))}"></label></div>
        <label class="ibx-field">Location<input class="form-control" name="location" dir="auto" value="${esc(p.location || "")}"></label>`
      : `
        <label class="ibx-field">Notes<textarea class="form-control" name="description" dir="auto" rows="3">${esc(p.description || "")}</textarea></label>
        <div class="ibx-row2"><label class="ibx-field">Due date<input class="form-control" type="date" name="due_date" value="${esc((p.due_date || "").slice(0, 10))}"></label>
        <label class="ibx-field">Priority<select class="form-control" name="priority">${["low", "medium", "high"].map((x) => `<option${(p.priority || "medium") === x ? " selected" : ""}>${x}</option>`).join("")}</select></label></div>
        <label class="ibx-field">Remind me at<input class="form-control" type="datetime-local" name="remind" value="${esc(local(t.time))}"></label>
        ${t.location || t.car || t.wake ? `<div class="ibx-note" style="margin:-4px 0 12px">Other reminder kept: ${esc(trigList({ triggers: { ...t, time: null } }).join(", "))}</div>` : ""}`}
      <div class="ibx-btns" style="justify-content:flex-end;margin-top:6px">
        <button type="button" class="btn-secondary" data-act="modal-close">Cancel</button>
        <button type="submit" class="btn-primary"><i data-lucide="check"></i>${isEvent ? "Add" : "Create"}</button>
      </div></form>`;
    $.modal.classList.remove("hidden");
    icons();
    const f = $.modal.querySelector("form");
    f.querySelector("[name=title]").focus();
    f.addEventListener("submit", (e) => {
      e.preventDefault();
      const v = Object.fromEntries(new FormData(f));
      const o = { title: v.title.trim() };
      if (isEvent) {
        o.start = v.start ? v.start.replace("T", " ") : null;
        o.end = v.end ? v.end.replace("T", " ") : null;
        o.location = v.location.trim() || null;
      } else {
        o.description = v.description.trim() || null;
        o.due_date = v.due_date || null;
        o.priority = v.priority;
        o.triggers = { ...t, time: v.remind ? v.remind.replace("T", " ") : null };
        Object.keys(o.triggers).forEach((k) => o.triggers[k] == null && delete o.triggers[k]);
      }
      closeModal();
      acceptSugg(id, o);
    });
  }

  function help() {
    const keys = [["j / k", "Next / previous"], ["Enter", "Open"], ["e", "Dismiss"], ["s", "Snooze"], ["x", "Select"], ["Shift + click", "Select a range"],
      ["o", "Open on phone"], ["a", "Ask Dana"], ["/", "Search"], ["g", "Group by app"], ["1 – 5", "Switch view"], ["r", "Refresh"], ["Esc", "Close / clear"]];
    $.modal.innerHTML = `<div class="ibx-modal-card"><h3>Keyboard shortcuts</h3><div class="ibx-keys">${keys.map(([k, d]) => `<span><kbd>${k}</kbd></span><span>${d}</span>`).join("")}</div>
      <div class="ibx-btns" style="justify-content:flex-end;margin-top:16px"><button class="btn-secondary" data-act="modal-close">Close</button></div></div>`;
    $.modal.classList.remove("hidden");
  }
  function closeModal() { $.modal.classList.add("hidden"); $.modal.innerHTML = ""; }

  function toast(msg, undo) {
    clearTimeout(toastTimer);
    $.toast.innerHTML = `<span>${esc(msg)}</span>${undo ? '<button data-act="undo">Undo</button>' : ""}`;
    $.toast.classList.remove("hidden");
    $.toast.onclick = (e) => {
      if (e.target.closest("[data-act=undo]")) { $.toast.classList.add("hidden"); undo(); }
    };
    toastTimer = setTimeout(() => $.toast.classList.add("hidden"), undo ? 6000 : 3500);
  }

  function selectRow(id, range) {
    const list = rows();
    if (range && S.anchor) {
      const a = list.findIndex((n) => n.id === S.anchor);
      const b = list.findIndex((n) => n.id === id);
      if (a >= 0 && b >= 0) { for (let i = Math.min(a, b); i <= Math.max(a, b); i++) S.selected.add(list[i].id); }
    } else if (S.selected.has(id)) S.selected.delete(id);
    else S.selected.add(id);
    S.anchor = id;
    renderList({ keepScroll: true });
    renderBulk();
  }

  function targets() {
    if (S.selected.size) return [...S.selected];
    const n = current() || rows()[S.cursor];
    return n ? [n.id] : [];
  }

  function setView(id) {
    if (S.view === id) return;
    S.view = id;
    store("ibx.view", id);
    S.selected.clear();
    S.openId = null;
    S.cursor = -1;
    S.loadedOnce = false;
    S.items = [];
    renderAll();
    load();
  }

  // ---------- events ----------
  function onListClick(e) {
    const t = e.target;
    const act = t.closest("[data-act]")?.dataset.act;
    const row = t.closest(".ibx-row");
    const sugg = t.closest("[data-sugg]")?.dataset.sugg;
    const grp = t.closest(".ibx-group")?.dataset.group;
    if (act === "fold-sugg") { S.suggOpen = !S.suggOpen; store("ibx.suggOpen", S.suggOpen ? "1" : "0"); renderList({ keepScroll: true }); return; }
    if (act === "sugg-more") { S.suggAll = true; renderList({ keepScroll: true }); return; }
    if (act === "sugg-dismiss-all") { if (confirm(`Dismiss all ${S.suggestions.length} suggestions?`)) dismissSugg(S.suggestions.map((s) => s.id)); return; }
    if (sugg && act) return suggAction(act, sugg);
    if (act === "dismiss-bucket") { setStatus(S.items.map((n) => n.id), "dismissed"); return; }
    if (grp) {
      e.stopPropagation();
      if (act === "group-dismiss") {
        const g = groups().find((x) => x.pkg === grp);
        if (g) setStatus(g.items.map((n) => n.id), "dismissed");
        return;
      }
      if (S.collapsed.has(grp)) S.collapsed.delete(grp); else S.collapsed.add(grp);
      renderList({ keepScroll: true });
      return;
    }
    if (!row) return;
    const id = row.dataset.id;
    if (act === "select" || e.metaKey || e.ctrlKey || (e.shiftKey && S.anchor) || (S.selected.size && !act)) { e.preventDefault(); selectRow(id, e.shiftKey); return; }
    if (act === "dismiss") return setStatus([id], "dismissed");
    if (act === "restore") return setStatus([id], "active");
    if (act === "unsnooze") return snooze([id], { clear: true });
    if (act === "snooze") { const b = t.closest("[data-act]"); b.parentElement.classList.add("ibx-menu-wrap"); return snoozeMenu(b, [id]); }
    S.anchor = id;
    open(id);
  }

  function suggAction(act, id) {
    if (act === "sugg-accept") return acceptSugg(id);
    if (act === "sugg-edit") return editSugg(id);
    if (act === "sugg-dismiss") return dismissSugg([id]);
    if (act === "sugg-ask") return askDana("suggestion", id);
  }

  function onDetailClick(e) {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const n = current();
    const act = b.dataset.act;
    const sugg = b.closest("[data-sugg]")?.dataset.sugg;
    if (sugg) return suggAction(act, sugg);
    if (act === "close") { S.openId = null; renderList({ keepScroll: true }); renderDetail(); return; }
    if (!n) return;
    if (act === "dismiss") return setStatus([n.id], "dismissed");
    if (act === "restore") return setStatus([n.id], "active");
    if (act === "unsnooze") return snooze([n.id], { clear: true });
    if (act === "snooze") return snoozeMenu(b, [n.id]);
    if (act === "ask") return askDana("notification", n.id);
    if (act === "rule") return setRule(n.package, b.dataset.rule);
    if (act === "phone") return phoneAction(n, b.dataset.action);
  }

  function onDetailSubmit(e) {
    const f = e.target.closest("form[data-act=reply]");
    if (!f) return;
    e.preventDefault();
    const n = current();
    const text = f.querySelector("input").value.trim();
    if (!n || !text) return;
    phoneAction(n, f.dataset.action, text);
  }

  function onBulkClick(e) {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const ids = [...S.selected];
    switch (b.dataset.act) {
      case "bulk-dismiss": return setStatus(ids, "dismissed");
      case "bulk-restore": return setStatus(ids, "active");
      case "bulk-unsnooze": return snooze(ids, { clear: true });
      case "bulk-snooze": return snoozeMenu(b, ids);
      case "select-all": rows().forEach((n) => S.selected.add(n.id)); renderList({ keepScroll: true }); return renderBulk();
      case "select-clear": S.selected.clear(); renderList({ keepScroll: true }); return renderBulk();
    }
  }

  function onKey(e) {
    if (!S.visible) return;
    if (!$.modal.classList.contains("hidden")) {
      if (e.key === "Escape") closeModal();
      return;
    }
    const tag = (e.target.tagName || "").toLowerCase();
    const typing = tag === "input" || tag === "textarea" || tag === "select" || e.target.isContentEditable;
    if (typing) {
      if (e.key === "Escape" && e.target === $.search) { $.search.blur(); $.list.focus({ preventScroll: true }); }
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (document.querySelector(".ibx-menu")) return;
    const list = rows();
    const move = (d) => {
      if (!list.length) return;
      S.cursor = Math.max(0, Math.min(list.length - 1, (S.cursor < 0 ? (d > 0 ? -1 : list.length) : S.cursor) + d));
      if (window.innerWidth > 900 || S.openId) open(list[S.cursor].id);
      else { markCursor(); $.list.querySelector(".ibx-row.cursor")?.scrollIntoView({ block: "nearest" }); }
    };
    const k = e.key;
    let handled = true;
    if (k === "j" || k === "ArrowDown") move(1);
    else if (k === "k" || k === "ArrowUp") move(-1);
    else if (k === "Enter") { const n = list[S.cursor]; if (n) open(n.id); }
    else if (k === "e" || k === "#") { const ids = targets(); if (ids.length) (isActiveView() || S.view === "snoozed" ? setStatus(ids, "dismissed") : setStatus(ids, "active")); }
    else if (k === "s") {
      const ids = targets();
      if (ids.length && isActiveView()) {
        const anchor = S.selected.size ? $.bulk.querySelector("[data-act=bulk-snooze]") : ($.detail.querySelector("[data-act=snooze]") || $.list.querySelector(".ibx-row.cursor [data-act=snooze]"));
        if (anchor) snoozeMenu(anchor, ids);
      }
    }
    else if (k === "x") { const n = list[S.cursor] || current(); if (n) selectRow(n.id, e.shiftKey); }
    else if (k === "o") { const n = current(); if (n?.on_phone) phoneAction(n, "open"); }
    else if (k === "a") { const n = current(); if (n) askDana("notification", n.id); }
    else if (k === "/") $.search.focus();
    else if (k === "g") toggleGroup();
    else if (k === "r") { load(); loadSummaries(true); }
    else if (k === "?") help();
    else if (/^[1-5]$/.test(k)) setView(VIEWS[+k - 1].id);
    else if (k === "Escape") {
      if (S.selected.size) { S.selected.clear(); renderList({ keepScroll: true }); renderBulk(); }
      else if (S.openId) { S.openId = null; renderList({ keepScroll: true }); renderDetail(); }
      else handled = false;
    } else handled = false;
    if (handled) e.preventDefault();
  }

  function toggleGroup() {
    S.group = !S.group;
    store("ibx.group", S.group ? "1" : "0");
    S.cursor = -1;
    renderList();
  }

  // ---------- lifecycle ----------
  function init() {
    $ = {
      root: document.getElementById("ibx-root"),
      list: document.getElementById("ibx-list"),
      detail: document.getElementById("ibx-detail"),
      views: document.getElementById("ibx-views"),
      search: document.getElementById("ibx-search"),
      bulk: document.getElementById("ibx-bulkbar"),
      toast: document.getElementById("ibx-toast"),
      modal: document.getElementById("ibx-modal"),
      kicker: document.getElementById("ibx-kicker"),
      group: document.getElementById("ibx-group-toggle"),
    };
    if (!$.root) return;
    $.views.addEventListener("click", (e) => { const b = e.target.closest("[data-view]"); if (b) setView(b.dataset.view); });
    $.list.addEventListener("click", onListClick);
    $.detail.addEventListener("click", onDetailClick);
    $.detail.addEventListener("submit", onDetailSubmit);
    $.bulk.addEventListener("click", onBulkClick);
    $.group.addEventListener("click", toggleGroup);
    document.getElementById("ibx-refresh")?.addEventListener("click", () => { load(); loadSummaries(true); });
    document.getElementById("ibx-help-btn")?.addEventListener("click", help);
    $.modal.addEventListener("click", (e) => { if (e.target === $.modal || e.target.closest("[data-act=modal-close]")) closeModal(); });
    $.search.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { S.q = $.search.value.trim(); S.openId = null; S.cursor = -1; load(); }, 250);
    });
    document.addEventListener("click", (e) => { if (!e.target.closest(".ibx-menu, .ibx-menu-wrap")) closeMenus(); });
    document.addEventListener("keydown", onKey);
    document.addEventListener("visibilitychange", () => { if (!document.hidden && S.visible) load({ quiet: true }); });
    renderViews();
  }

  function show() {
    if (!$.root) return;
    S.visible = true;
    if (location.hash !== "#inbox") history.replaceState(null, "", "#inbox");
    load();
    if (!S.apps) loadApps();
    clearInterval(pollTimer);
    pollTimer = setInterval(() => {
      // Don't redraw under the user's hands (menus, typing a reply, a pending press).
      if (document.hidden || document.querySelector(".ibx-menu") || !$.modal.classList.contains("hidden")) return;
      if ($.detail.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;
      load({ quiet: true });
    }, POLL_MS);
  }
  function hide() {
    if (S.visible && location.hash === "#inbox") history.replaceState(null, "", location.pathname + location.search);
    S.visible = false;
    clearInterval(pollTimer);
    closeMenus();
  }

  const TEMPLATE = `
    <header class="top-bar">
        <div class="ibx-titles">
            <span class="top-bar-title">Inbox</span>
            <span class="ibx-kicker" id="ibx-kicker"></span>
        </div>
        <div class="top-bar-actions">
            <button class="chip" id="ibx-group-toggle" title="Group by app (g)">
                <i data-lucide="layers" style="width:14px;height:14px;"></i><span>By app</span>
            </button>
            <button class="icon-btn" id="ibx-help-btn" title="Keyboard shortcuts (?)"><i data-lucide="keyboard"></i></button>
            <button class="icon-btn" id="ibx-refresh" title="Refresh (r)"><i data-lucide="refresh-cw"></i></button>
        </div>
    </header>
    <div class="ibx" id="ibx-root">
        <div class="ibx-list-pane">
            <div class="ibx-toolbar">
                <div class="ibx-views" id="ibx-views" role="tablist"></div>
                <div class="ibx-search">
                    <i data-lucide="search"></i>
                    <input type="search" id="ibx-search" placeholder="Search app, person or text   /" autocomplete="off">
                </div>
            </div>
            <div class="ibx-bulkbar hidden" id="ibx-bulkbar"></div>
            <div class="ibx-scroll" id="ibx-list" tabindex="-1"></div>
        </div>
        <div class="ibx-detail-pane" id="ibx-detail"></div>
    </div>
    <div class="ibx-toast hidden" id="ibx-toast" role="status" aria-live="polite"></div>
    <div class="ibx-modal hidden" id="ibx-modal"></div>

  `;

  Dana.panel({
    start(api) { A = api; pollBadge(); setInterval(pollBadge, BADGE_MS); },
    mount(el) {
      if (!el.firstChild) { el.innerHTML = TEMPLATE; init(); }
      show();
    },
    unmount: hide,
  });
})();
