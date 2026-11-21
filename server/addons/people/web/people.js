/* People — who the user talks to (calls + messages), from the web.
 *
 *   GET    /api/people?sort=&q=&hidden=        list (+ stats, open task count)
 *   GET    /api/people/{id}                     detail: brief, timeline, tasks, suggestions
 *   POST   /api/people  · PUT/DELETE /api/people/{id}
 *   POST   /api/people/{id}/merge {other_id}    · GET /api/people/merge-candidates
 *   POST   /api/people/{id}/brief               rewrite the AI brief now
 *   POST   /api/chat/context {kind: "person"}   "Ask Dana" about them
 * The people addon's page (Dana.panel, static/dana.js); opened with a person's id (api.open("people", id))
 * it shows that person.
 */
(() => {
  "use strict";

  const SORTS = [
    { id: "recent", label: "Recent" },
    { id: "pinned", label: "Pinned first" },
    { id: "drifting", label: "Drifting" },
    { id: "name", label: "A–Z" },
  ];
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (_) { return null; } };

  const S = { sort: store("ppl.sort") || "recent", q: "", hidden: false, items: [], pairs: [], openId: null, person: null,
              loading: false, visible: false, briefBusy: false };
  let $ = {};
  let A = null;          // the page's Dana api (static/dana.js)
  let searchTimer = null;
  let toastTimer = null;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };
  // A steady named hue for a name (styles.css tokens; ember is the accent, left out).
  const HUES = ["amber", "sage", "teal", "sky", "plum", "rose"];
  const hue = (s) => { let h = 0; for (const c of String(s || "")) h = (h * 31 + c.charCodeAt(0)) % 997; return HUES[h % HUES.length]; };
  const rtl = (s) => /[֐-׿]/.test(String(s || "")) ? ' dir="auto"' : "";

  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.body !== undefined) { init.method = opts.method || "POST"; init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    const r = await fetch(path, init);
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
    return data;
  }

  function toast(msg) {
    if (!$.toast) return;
    $.toast.textContent = msg;
    $.toast.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $.toast.classList.add("hidden"), 3200);
  }

  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return String(iso).slice(0, 10);
    const now = new Date();
    const sameYear = d.getFullYear() === now.getFullYear();
    return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) });
  }
  const fmtTime = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); };
  const fmtPhone = (p) => (p ? "+" + p : "");
  const dur = (s) => (!s ? "" : s < 60 ? `${s}s` : `${Math.round(s / 60)} min`);

  function avatar(p, size = "") {
    return `<span class="ppl-avatar ${size}" style="--av:var(--${hue(p.name)}-tint);--av-text:var(--${hue(p.name)}-text)">${esc(p.initials || "?")}</span>`;
  }

  // ---------- list ----------
  async function loadList() {
    S.loading = true;
    try {
      const qs = new URLSearchParams({ sort: S.sort, q: S.q, ...(S.hidden ? { hidden: "1" } : {}) });
      const [list, pairs] = await Promise.all([api(`/api/people?${qs}`), S.pairs.length || S.q ? null : api("/api/people/merge-candidates").catch(() => null)]);
      S.items = list.people || [];
      if (pairs) {
        const skipped = new Set(JSON.parse(store("ppl.skip") || "[]"));  // pairs the user said aren't the same person
        S.pairs = (pairs.pairs || []).filter((x) => !skipped.has(`${x.a.id}|${x.b.id}`));
      }
    } catch (e) {
      $.list.innerHTML = `<div class="ibx-empty"><strong>Couldn't load people</strong><span>${esc(e.message)}</span></div>`;
      return;
    } finally { S.loading = false; }
    renderList();
  }

  function renderSorts() {
    $.sorts.innerHTML = SORTS.map((s) => `<button class="chip ${S.sort === s.id ? "active" : ""}" data-sort="${s.id}">${s.label}</button>`).join("")
      + `<button class="chip ${S.hidden ? "active" : ""}" data-hidden title="Include numbers without a saved name">Unnamed</button>`;
  }

  function rowHtml(p) {
    const st = p.stats || {};
    const meta = [];
    if (p.relation) meta.push(esc(p.relation));
    meta.push(st.last_contact_ago ? `${st.last_contact_kind === "call" ? "spoke" : "messaged"} ${esc(st.last_contact_ago)}` : "no contact yet");
    const flags = [
      p.pinned ? `<i data-lucide="pin" class="ppl-flag" title="Pinned"></i>` : "",
      st.drifting ? `<span class="ppl-tag warn" title="Usually every ~${st.typical_gap_days} days">${st.days_since}d</span>` : "",
      p.birthday_in_days != null ? `<span class="ppl-tag" title="Birthday">🎂 ${p.birthday_in_days === 0 ? "today" : p.birthday_in_days + "d"}</span>` : "",
      p.open_tasks ? `<span class="ppl-tag" title="Open tasks">${p.open_tasks} open</span>` : "",
    ].join("");
    return `<button class="ppl-row dn-row ${S.openId === p.id ? "active" : ""}" data-id="${p.id}">
      ${avatar(p)}
      <span class="ppl-row-main"><span class="ppl-row-name"${rtl(p.name)}>${esc(p.name)}</span><span class="ppl-row-meta">${meta.join(" · ")}</span></span>
      <span class="ppl-row-flags">${flags}</span></button>`;
  }

  function renderList() {
    renderSorts();
    const pairs = S.pairs.length && !S.q ? `<div class="ppl-dupes">
        <div class="ppl-dupes-head"><i data-lucide="users"></i> Possible duplicates</div>
        ${S.pairs.slice(0, 4).map((x) => `<div class="ppl-dupe"><span${rtl(x.a.name + x.b.name)}>${esc(x.a.name)} · ${esc(x.b.name)}</span>
          <button class="ibx-link" data-merge="${x.a.id}|${x.b.id}">Merge</button><button class="ibx-link" data-skip="${x.a.id}|${x.b.id}">Not the same</button></div>`).join("")}
      </div>` : "";
    if (!S.items.length) {
      $.list.innerHTML = pairs + `<div class="ibx-empty"><i data-lucide="users"></i><strong>${S.sort === "drifting" ? "Nobody's drifting" : "No people yet"}</strong>
        <span>${S.sort === "drifting" ? "You're in touch with everyone you usually talk to." : "People appear here from your calls and messages."}</span></div>`;
    } else {
      $.list.innerHTML = pairs + S.items.map(rowHtml).join("");
    }
    icons();
  }

  // ---------- detail ----------
  async function open(id) {
    S.openId = id;
    $.root.classList.add("detail-open");
    $.list.querySelectorAll(".ppl-row").forEach((r) => r.classList.toggle("active", r.dataset.id === id));
    $.detail.innerHTML = `<div class="ppl-detail"><div class="ppl-skel dn-shimmer"></div></div>`;
    try {
      S.person = await api(`/api/people/${id}`);
    } catch (e) {
      $.detail.innerHTML = `<div class="ibx-empty"><strong>Couldn't load</strong><span>${esc(e.message)}</span></div>`;
      return;
    }
    renderDetail();
  }

  function stat(label, value) {
    return value ? `<div class="ppl-stat"><span class="v">${esc(value)}</span><span class="l">${label}</span></div>` : "";
  }

  function timelineHtml(items) {
    if (!items.length) return `<p class="ppl-muted">No calls or messages on record.</p>`;
    let lastDay = "";
    return items.map((e) => {
      const day = fmtDate(e.at);
      const head = day !== lastDay ? `<div class="ppl-day">${esc(day)}</div>` : "";
      lastDay = day;
      if (e.type === "call") {
        const dir = e.direction === "outgoing" ? "phone-outgoing" : e.direction === "missed" ? "phone-missed" : "phone-incoming";
        return `${head}<div class="ppl-ev"><i data-lucide="${dir}"></i><div class="ppl-ev-body">
          <div class="ppl-ev-title"${rtl(e.title)}>${esc(e.title || "Call")} <span class="ppl-muted">${fmtTime(e.at)}${e.duration_seconds ? " · " + dur(e.duration_seconds) : ""}</span></div>
          ${e.summary ? `<div class="ppl-ev-text"${rtl(e.summary)}>${esc(e.summary)}</div>` : ""}
          <button class="ibx-link" data-call="${e.id}">Open call</button></div></div>`;
      }
      return `${head}<div class="ppl-ev"><i data-lucide="message-circle"></i><div class="ppl-ev-body">
        <div class="ppl-ev-title">${e.count} message${e.count === 1 ? "" : "s"} <span class="ppl-muted">${esc(e.app || "")}${e.chat ? " · " + esc(e.chat) : ""}</span></div>
        ${(e.texts || []).map((t) => `<div class="ppl-ev-text quote"${rtl(t)}>${esc(t)}</div>`).join("")}</div></div>`;
    }).join("");
  }

  function renderDetail() {
    const p = S.person;
    if (!p) return;
    const st = p.stats || {};
    const b = p.brief || { points: [], open: [] };
    const briefBody = (b.points.length || b.open.length)
      ? `${b.points.length ? `<ul class="ppl-points">${b.points.map((x) => `<li${rtl(x)}>${esc(x)}</li>`).join("")}</ul>` : ""}
         ${b.open.length ? `<div class="ppl-sub">Open between you</div><ul class="ppl-points open">${b.open.map((x) => `<li${rtl(x)}>${esc(x)}</li>`).join("")}</ul>` : ""}`
      : `<p class="ppl-muted">${p.brief_stale ? "Writing a brief…" : "Not enough history for a brief yet."}</p>`;
    $.detail.innerHTML = `<div class="ppl-detail">
      <div class="ppl-head">
        <button class="icon-btn ibx-close" data-close title="Back"><i data-lucide="arrow-left"></i></button>
        ${avatar(p, "lg")}
        <div class="ppl-head-main">
          <h2${rtl(p.name)}>${esc(p.name)}</h2>
          <div class="ppl-head-meta">${[p.relation && esc(p.relation), ...p.phones.map((x) => `<a href="tel:+${esc(x)}">${esc(fmtPhone(x))}</a>`)].filter(Boolean).join(" · ") || "<span class='ppl-muted'>No details yet</span>"}</div>
          ${p.aliases.length ? `<div class="ppl-aliases"${rtl(p.aliases.join(" "))}>Also: ${p.aliases.map(esc).join(", ")}</div>` : ""}
        </div>
        <div class="ppl-head-actions">
          <button class="icon-btn" data-pin title="${p.pinned ? "Unpin" : "Pin"}"><i data-lucide="${p.pinned ? "pin-off" : "pin"}"></i></button>
          <button class="icon-btn" data-edit title="Edit"><i data-lucide="pencil"></i></button>
          <button class="btn-primary" data-ask><i data-lucide="sparkles" style="width:15px;height:15px;"></i><span>Ask Dana</span></button>
        </div>
      </div>
      <div class="ppl-stats">
        ${stat("last contact", st.last_contact_ago || "never")}
        ${stat("usually every", st.typical_gap_days ? `${Math.round(st.typical_gap_days)} days` : "")}
        ${stat("calls", st.calls ? String(st.calls) : "")}
        ${stat("messages (30d)", st.messages ? String(st.messages) : "")}
        ${p.birthday ? stat("birthday", p.birthday_in_days != null ? (p.birthday_in_days === 0 ? "today 🎂" : `in ${p.birthday_in_days} days`) : p.birthday) : ""}
      </div>
      ${st.drifting ? `<div class="ppl-nudge"><i data-lucide="hourglass"></i> It's been ${st.days_since} days — you usually talk every ~${Math.round(st.typical_gap_days)}.</div>` : ""}
      <section class="ppl-card">
        <div class="ppl-card-head"><span>Brief</span>
          <button class="ibx-link" data-brief ${S.briefBusy ? "disabled" : ""}>${S.briefBusy ? "Writing…" : "Refresh"}</button></div>
        ${briefBody}
      </section>
      ${p.notes ? `<section class="ppl-card"><div class="ppl-card-head"><span>Notes</span></div><p class="ppl-notes"${rtl(p.notes)}>${esc(p.notes)}</p></section>` : ""}
      ${p.tasks.length || p.suggestions.length ? `<section class="ppl-card"><div class="ppl-card-head"><span>Open tasks</span></div>
        ${p.tasks.map((t) => `<div class="ppl-task"><i data-lucide="circle"></i><span${rtl(t.title)}>${esc(t.title)}</span>${t.due_date ? `<span class="ppl-muted">${esc(t.due_date)}</span>` : ""}</div>`).join("")}
        ${p.suggestions.map((s) => `<div class="ppl-task sugg"><i data-lucide="lightbulb"></i><span${rtl(s.title)}>${esc(s.title)}</span><span class="ppl-muted">suggested</span></div>`).join("")}
      </section>` : ""}
      <section class="ppl-card"><div class="ppl-card-head"><span>Timeline</span></div>${timelineHtml(p.timeline || [])}</section>
    </div>`;
    icons();
    if (p.brief_stale && !S.briefBusy) setTimeout(() => { if (S.openId === p.id && S.visible) refreshDetailQuietly(p.id); }, 6000);
  }

  async function refreshDetailQuietly(id) {
    try {
      const fresh = await api(`/api/people/${id}`);
      if (S.openId !== id) return;
      const changed = JSON.stringify(fresh.brief) !== JSON.stringify(S.person?.brief);
      S.person = fresh;
      if (changed || !fresh.brief_stale) renderDetail();
    } catch (_) { /* ignore */ }
  }

  // ---------- actions ----------
  async function save(changes) {
    try {
      await api(`/api/people/${S.openId}`, { method: "PUT", body: changes });
      await Promise.all([open(S.openId), loadList()]);
    } catch (e) { toast(e.message); }
  }

  function editModal(p) {
    const isNew = !p;
    p = p || { name: "", relation: "", phones: [], aliases: [], birthday: "", notes: "", hidden: false };
    $.modal.innerHTML = `<div class="ibx-modal-card"><h3>${isNew ? "Add person" : "Edit " + esc(p.name)}</h3>
      <label class="ibx-field">Name<input class="form-control" name="name" value="${esc(p.name)}" required></label>
      <div class="ibx-row2">
        <label class="ibx-field">Relation<input class="form-control" name="relation" value="${esc(p.relation)}" placeholder="mother, colleague…"></label>
        <label class="ibx-field">Birthday<input class="form-control" name="birthday" value="${esc(p.birthday)}" placeholder="YYYY-MM-DD or MM-DD"></label>
      </div>
      <label class="ibx-field">Phone numbers (comma separated)<input class="form-control" name="phones" value="${esc(p.phones.map(fmtPhone).join(", "))}"></label>
      <label class="ibx-field">Other names (comma separated — Hebrew/English spellings, nicknames)<input class="form-control" name="aliases" value="${esc(p.aliases.join(", "))}"></label>
      <label class="ibx-field">Notes<textarea class="form-control" name="notes" rows="4">${esc(p.notes)}</textarea></label>
      ${isNew ? "" : `<label class="ppl-check"><input type="checkbox" name="hidden" ${p.hidden ? "checked" : ""}> Hide from the list (spam, services)</label>`}
      <div class="ppl-modal-actions">
        ${isNew ? "" : `<button class="btn-secondary danger" data-delete>Delete</button>`}
        <span class="grow"></span>
        <button class="btn-secondary" data-cancel>Cancel</button>
        <button class="btn-primary" data-save>${isNew ? "Add" : "Save"}</button>
      </div></div>`;
    $.modal.classList.remove("hidden");
    $.modal.querySelector("input[name=name]").focus();
    const val = (n) => $.modal.querySelector(`[name=${n}]`);
    const split = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
    $.modal.onclick = async (ev) => {
      if (ev.target === $.modal || ev.target.closest("[data-cancel]")) { $.modal.classList.add("hidden"); return; }
      if (ev.target.closest("[data-delete]")) {
        if (!confirm(`Delete ${p.name}? Their calls and messages stay, unlinked.`)) return;
        try { await api(`/api/people/${p.id}`, { method: "DELETE" }); } catch (e) { toast(e.message); return; }
        $.modal.classList.add("hidden");
        S.openId = null; S.person = null; $.root.classList.remove("detail-open");
        $.detail.innerHTML = emptyDetail();
        loadList();
        return;
      }
      if (!ev.target.closest("[data-save]")) return;
      const body = { name: val("name").value.trim(), relation: val("relation").value.trim(), birthday: val("birthday").value.trim(),
                     phones: split(val("phones").value), aliases: split(val("aliases").value), notes: val("notes").value };
      if (!isNew) body.hidden = val("hidden").checked;
      if (!body.name) { toast("A name is required"); return; }
      try {
        const res = isNew ? await api("/api/people", { body }) : await api(`/api/people/${p.id}`, { method: "PUT", body });
        $.modal.classList.add("hidden");
        await loadList();
        open(res.id);
      } catch (e) { toast(e.message); }
    };
  }

  async function ask() {
    try {
      const res = await api("/api/chat/context", { body: { kind: "person", id: S.openId } });
      A.openSession(res.session_id);
    } catch (e) { toast(e.message); }
  }

  async function refreshBrief() {
    S.briefBusy = true;
    renderDetail();
    try {
      const res = await api(`/api/people/${S.openId}/brief`, { body: {} });
      S.person = res.person;
    } catch (e) { toast(`Brief failed: ${e.message}`); }
    S.briefBusy = false;
    renderDetail();
  }

  async function merge(a, b) {
    const pa = S.items.find((x) => x.id === a) || { name: "first" };
    const pb = S.items.find((x) => x.id === b) || { name: "second" };
    if (!confirm(`Merge “${pb.name}” into “${pa.name}”? Numbers, names, calls and messages move over.`)) return;
    try {
      await api(`/api/people/${a}/merge`, { body: { other_id: b } });
      S.pairs = S.pairs.filter((x) => ![x.a.id, x.b.id].includes(b));
      await loadList();
      open(a);
    } catch (e) { toast(e.message); }
  }

  function skipPair(key) {
    const skipped = JSON.parse(store("ppl.skip") || "[]");
    skipped.push(key);
    store("ppl.skip", JSON.stringify(skipped.slice(-200)));
    S.pairs = S.pairs.filter((x) => `${x.a.id}|${x.b.id}` !== key);
    renderList();
  }

  const emptyDetail = () => `<div class="ibx-empty"><i data-lucide="user-round"></i><strong>Pick someone</strong><span>Their brief, open items and every call and message.</span></div>`;

  // ---------- wiring ----------
  function init() {
    $ = {
      root: document.getElementById("ppl-root"), list: document.getElementById("ppl-list"), detail: document.getElementById("ppl-detail"),
      sorts: document.getElementById("ppl-sorts"), search: document.getElementById("ppl-search"), modal: document.getElementById("ppl-modal"),
      toast: document.getElementById("ppl-toast"),
    };
    if (!$.root) return;
    $.detail.innerHTML = emptyDetail();
    $.sorts.addEventListener("click", (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      if (b.hasAttribute("data-hidden")) S.hidden = !S.hidden;
      else { S.sort = b.dataset.sort; store("ppl.sort", S.sort); }
      loadList();
    });
    $.search.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { S.q = $.search.value.trim(); loadList(); }, 220);
    });
    $.list.addEventListener("click", (ev) => {
      const m = ev.target.closest("[data-merge]");
      if (m) { const [a, b] = m.dataset.merge.split("|"); merge(a, b); return; }
      const sk = ev.target.closest("[data-skip]");
      if (sk) { skipPair(sk.dataset.skip); return; }
      const row = ev.target.closest(".ppl-row");
      if (row) open(row.dataset.id);
    });
    $.detail.addEventListener("click", (ev) => {
      const t = ev.target;
      if (t.closest("[data-close]")) { $.root.classList.remove("detail-open"); S.openId = null; renderList(); return; }
      if (t.closest("[data-pin]")) { save({ pinned: !S.person.pinned }); return; }
      if (t.closest("[data-edit]")) { editModal(S.person); return; }
      if (t.closest("[data-ask]")) { ask(); return; }
      if (t.closest("[data-brief]")) { refreshBrief(); return; }
      const c = t.closest("[data-call]");
      if (c) A.open("calls", c.dataset.call || null);
    });
    document.getElementById("ppl-add")?.addEventListener("click", () => editModal(null));
    document.getElementById("ppl-refresh")?.addEventListener("click", () => { S.pairs = []; loadList(); if (S.openId) open(S.openId); });
    document.addEventListener("keydown", (ev) => {
      if (!S.visible || ev.target.matches("input, textarea")) return;
      if (ev.key === "/") { ev.preventDefault(); $.search.focus(); }
      if (ev.key === "Escape") $.modal.classList.add("hidden");
    });
  }

  async function show() {
    S.visible = true;
    await loadList();
  }

  const TEMPLATE = `
    <header class="top-bar">
        <span class="top-bar-title">People</span>
        <div class="top-bar-actions">
            <button class="icon-btn" id="ppl-refresh" title="Refresh"><i data-lucide="refresh-cw"></i></button>
            <button class="btn-primary" id="ppl-add"><i data-lucide="user-plus" style="width:16px;height:16px;"></i><span>Add</span></button>
        </div>
    </header>
    <div class="ibx ppl" id="ppl-root">
        <div class="ibx-list-pane">
            <div class="ibx-toolbar">
                <div class="ibx-views" id="ppl-sorts"></div>
                <div class="ibx-search">
                    <i data-lucide="search"></i>
                    <input type="search" id="ppl-search" placeholder="Name, relation or number   /" autocomplete="off">
                </div>
            </div>
            <div class="ibx-scroll" id="ppl-list"></div>
        </div>
        <div class="ibx-detail-pane" id="ppl-detail"></div>
    </div>
    <div class="ibx-toast hidden" id="ppl-toast" role="status" aria-live="polite"></div>
    <div class="ibx-modal hidden" id="ppl-modal"></div>

  `;

  Dana.panel({
    async mount(el, api, personId) {
      A = api;
      if (!el.firstChild) { el.innerHTML = TEMPLATE; init(); }
      await show();
      if (personId) await open(personId);
    },
    unmount() { S.visible = false; },
  });
})();
