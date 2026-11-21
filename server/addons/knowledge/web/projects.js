/* Projects — the Projects panel (docs/knowledge.md), the knowledge addon's page. The pure part is
 * projects-core.js (loaded first, Dana.panel `scripts`).
 *
 *   GET    /api/projects                         the list (with date, budget, decisions made, committed)
 *   GET    /api/projects/{id}/items              {project, sections, entries (+ files), tasks}
 *   POST   /api/projects · PUT|DELETE /api/projects/{id}        (change / delete: the owner)
 *   POST   /api/projects/{id}/sections · PUT|DELETE /api/sections/{id}
 *   POST   /api/entries · PUT|DELETE /api/entries/{id}
 *   POST   /api/entries/{id}/files (multipart) · DELETE /api/entries/{id}/files/{file_id}
 *
 * Levels: the project list (the side column; the whole screen on a phone) → a project (its date, budget,
 * one card per decision, notes, loose items) → a section (its summary, then its options compared side by
 * side or as cards, or its notes). An item opens in the sheet: decision, call / WhatsApp, the column
 * values edited in place, details, files. Registered with Dana.panel (static/dana.js).
 */
(() => {
  "use strict";
  let C = null;          // DanaProjectsCore (projects-core.js), once loaded
  let esc = null;
  const $ = (id) => document.getElementById(id);
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };
  const store = {
    get(k, d) { try { return localStorage.getItem(k) ?? d; } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* ignore */ } },
  };
  const md = (text) => {
    const src = String(text || "");
    if (window.marked && window.DOMPurify) return DOMPurify.sanitize(marked.parse(src));
    return `<p>${esc(src).replace(/\n/g, "<br>")}</p>`;
  };

  const S = {
    visible: false,
    list: null,            // GET /api/projects
    listError: "",
    projectId: store.get("dana_project", "") || null,
    sectionId: null,
    data: null,            // GET /api/projects/{id}/items
    loading: false,
    showArchived: false,
    view: store.get("dana_project_view", ""),   // "compare" | "cards" ("" = by width)
    sheet: null,           // {type: "entry", id, edit} | {type: "project", draft} | {type: "section", draft}
    summaryOpen: false,
    summaryEdit: null,     // draft text while editing the section summary
    busy: false,
  };
  let loadSeq = 0;

  async function api(method, url, body) {
    let r;
    try {
      const isForm = body instanceof FormData;
      r = await fetch(url, { method, cache: "no-store",
                             headers: body !== undefined && !isForm ? { "Content-Type": "application/json" } : {},
                             body: body === undefined ? undefined : (isForm ? body : JSON.stringify(body)) });
    } catch (_) {
      return { status: 0, body: null };
    }
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    return { status: r.status, body: data };
  }

  function toast(text) {
    const el = $("prj-toast");
    if (!el) return;
    el.textContent = text;
    el.classList.remove("hidden");
    clearTimeout(toast.t);
    toast.t = setTimeout(() => el.classList.add("hidden"), 3200);
  }
  const failed = (r, what) => toast(r.body?.message || r.body?.error || `Couldn't ${what}${r.status ? "" : " — offline?"}`);

  // ── Derived ───────────────────────────────────────────────────────────────────────
  const project = () => S.data?.project || null;
  const sections = () => S.data?.sections || [];
  const entries = () => S.data?.entries || [];
  const section = (id = S.sectionId) => sections().find((s) => s.id === id) || null;
  const entry = (id) => entries().find((e) => e.id === id) || null;
  const canEdit = () => !!project() && project().access !== "read";
  const isOwner = () => !!project() && !project().access;
  const projectDir = () => {
    const p = project();
    return p && C.isRtl(p.title + " " + sections().map((s) => s.title).join(" ")) ? "rtl" : "auto";
  };
  // A Hebrew project speaks Hebrew inside: its labels, counts and decisions (the app's frame stays English).
  const lang = () => (projectDir() === "rtl" ? "he" : "en");
  const t = (en, he) => (lang() === "he" ? he : en);
  const narrow = () => window.matchMedia?.("(max-width: 899px)").matches;
  const viewMode = (sec) => {
    const n = entries().filter((e) => e.section_id === sec.id).length;
    if (S.view) return S.view;
    return narrow() || n < 2 ? "cards" : "compare";
  };

  // ── Loading ───────────────────────────────────────────────────────────────────────
  async function loadList() {
    const first = S.list === null;
    const r = await api("GET", "/api/projects");
    if (r.status === 200 && Array.isArray(r.body)) { S.list = r.body; S.listError = ""; }
    else S.listError = r.status === 403 ? "Projects aren't connected for you." : "Couldn't load projects.";
    if (S.projectId && S.list && !S.list.some((p) => p.id === S.projectId)) { S.projectId = null; S.data = null; }
    // On a wide screen the first visit opens a project right away (the list stays beside it).
    if (first && !S.projectId && S.list?.length && !narrow()) S.projectId = C.orderProjects(S.list)[0].id;
  }

  async function loadProject() {
    if (!S.projectId) { S.data = null; return; }
    const seq = ++loadSeq;
    const r = await api("GET", `/api/projects/${encodeURIComponent(S.projectId)}/items`);
    if (seq !== loadSeq) return;
    if (r.status === 200 && r.body) {
      S.data = r.body;
      C.setCurrency(S.data.project?.currency);
      if (S.sectionId && !section()) S.sectionId = null;
    } else { S.data = null; S.projectId = null; }
  }

  async function refresh({ list = true } = {}) {
    if (list) await loadList();
    await loadProject();
    render();
  }

  async function openProject(id) {
    S.projectId = id; S.sectionId = null; S.sheet = null; S.summaryEdit = null; S.summaryOpen = false;
    store.set("dana_project", id || "");
    S.data = null;
    render();
    await loadProject();
    render();
    $("prj-main")?.scrollTo?.(0, 0);
  }

  function openSection(id) {
    S.sectionId = id; S.summaryEdit = null; S.summaryOpen = false;
    render();
    $("prj-main")?.scrollTo?.(0, 0);
  }

  // ── Render: frame ─────────────────────────────────────────────────────────────────
  function render() {
    const panel = $("panel-projects");
    if (!panel) return;
    panel.dataset.level = S.sectionId ? "section" : S.projectId ? "project" : "home";
    renderTop();
    renderSide();
    renderMain();
    renderSheet();
    icons();
  }

  function renderTop() {
    const crumbs = $("prj-crumbs");
    const actions = $("prj-actions");
    const p = project();
    const sec = section();
    const parts = [];
    if (S.projectId) parts.push(`<button class="icon-btn prj-back" data-act="back" aria-label="Back"><i data-lucide="arrow-left"></i></button>`);
    parts.push(`<button class="prj-crumb ${S.projectId ? "" : "current"}" data-act="home">Projects</button>`);
    if (p) {
      parts.push(`<i data-lucide="chevron-right" class="prj-crumb-sep"></i>`);
      parts.push(`<button class="prj-crumb ${sec ? "" : "current"}" data-act="project-home" dir="auto">${esc(p.icon || "📁")} ${esc(p.title)}</button>`);
    }
    if (sec) {
      parts.push(`<i data-lucide="chevron-right" class="prj-crumb-sep"></i>`);
      parts.push(`<span class="prj-crumb current" dir="auto">${sec.icon ? esc(sec.icon) + " " : ""}${esc(sec.title)}</span>`);
    }
    crumbs.innerHTML = parts.join("");
    const btns = [];
    if (p && canEdit() && !sec) btns.push(`<button class="chip" data-act="new-section"><i data-lucide="plus"></i><span>Section</span></button>`);
    if (p && isOwner() && !sec) btns.push(`<button class="icon-btn" data-act="edit-project" aria-label="Edit project" title="Edit project"><i data-lucide="pencil"></i></button>`);
    if (!S.projectId) btns.push(`<button class="btn-primary" data-act="new-project"><i data-lucide="plus"></i><span>New project</span></button>`);
    actions.innerHTML = btns.join("");
  }

  function renderSide() {
    const side = $("prj-side");
    if (!side) return;
    if (S.listError) { side.innerHTML = `<div class="prj-empty small">${esc(S.listError)}</div>`; return; }
    if (!S.list) { side.innerHTML = `<div class="prj-empty small">Loading…</div>`; return; }
    const all = C.orderProjects(S.list);
    const shown = all.filter((p) => S.showArchived || p.status !== "archived");
    const archived = all.length - all.filter((p) => p.status !== "archived").length;
    const rows = shown.map((p) => {
      const he = C.isRtl(p.title);
      const cd = C.countdown(p.target_date, undefined, he ? "he" : "en");
      const sub = [cd && cd.days >= 0 ? cd.label : "", p.decisions ? `${p.decided}/${p.decisions} ${he ? "הוחלטו" : "decided"}` : "",
                   !p.decisions ? (he ? C.heCount(p.entry_count || 0, "פריט אחד", "פריטים") : C.plural(p.entry_count || 0, "item")) : ""].filter(Boolean).join(" · ");
      return `<button class="prj-side-item ${p.id === S.projectId ? "active" : ""} ${p.status === "archived" ? "archived" : ""}" data-act="open-project" data-id="${esc(p.id)}">
        <span class="prj-side-icon">${esc(p.icon || "📁")}</span>
        <span class="prj-side-text"><span class="prj-side-title" dir="auto">${esc(p.title)}</span><span class="prj-side-sub" dir="auto">${esc(sub)}${p.shared_by ? ` · shared by ${esc(p.shared_by)}` : ""}</span></span>
        ${p.decisions ? `<span class="prj-ring" style="--pct:${Math.round((p.decided / p.decisions) * 100)}" aria-hidden="true"></span>` : ""}
      </button>`;
    }).join("");
    side.innerHTML = `
      <div class="prj-side-head"><span>Projects</span><button class="icon-btn" data-act="new-project" aria-label="New project" title="New project"><i data-lucide="plus"></i></button></div>
      <div class="prj-side-list">${rows || `<div class="prj-empty small">No projects yet.<br><button class="btn-primary" data-act="new-project">Create one</button></div>`}</div>
      ${archived ? `<button class="prj-side-toggle" data-act="toggle-archived">${S.showArchived ? "Hide" : "Show"} archived (${archived})</button>` : ""}`;
  }

  function renderMain() {
    const main = $("prj-main");
    if (!main) return;
    if (!S.projectId) {
      main.innerHTML = S.list && !S.list.length ? `
        <div class="prj-empty big">
          <div class="prj-empty-icon">🗂️</div>
          <h2>Keep a project in one place</h2>
          <p>A wedding, a trip, a flat hunt: decisions with their options side by side, notes, links and the files people send.</p>
          <button class="btn-primary" data-act="new-project"><i data-lucide="plus"></i> New project</button>
        </div>` : `<div class="prj-empty">Pick a project.</div>`;
      return;
    }
    if (!S.data) { main.innerHTML = `<div class="prj-empty">Loading…</div>`; return; }
    main.innerHTML = S.sectionId ? sectionHtml(section()) : projectHtml();
  }

  // ── Render: project ───────────────────────────────────────────────────────────────
  function projectHtml() {
    const p = project();
    const secs = sections();
    const decisions = secs.filter((s) => s.kind === "options");
    const notes = secs.filter((s) => s.kind !== "options");
    const loose = entries().filter((e) => !e.section_id);
    const cd = C.countdown(p.target_date, undefined, lang());
    const budget = C.budgetInfo(p, secs, entries());
    const decided = decisions.filter((s) => C.sectionState(s, entries()).decided).length;
    const decidedText = t(`${decided} of ${decisions.length} decided`, `${decided} מתוך ${decisions.length} הוחלטו`);
    const tasks = S.data.tasks || [];

    const hero = `
      <section class="prj-hero">
        <div class="prj-hero-icon">${esc(p.icon || "📁")}</div>
        <div class="prj-hero-text">
          <h1 dir="auto">${esc(p.title)}</h1>
          ${p.description ? `<p dir="auto">${esc(p.description)}</p>` : ""}
          <div class="prj-hero-chips">
            ${cd ? `<span class="prj-chip ${cd.days >= 0 && cd.days <= 30 ? "soon" : ""}"><i data-lucide="calendar-heart"></i><span dir="ltr">${esc(cd.date)}</span><em>${esc(cd.label)}</em></span>` : ""}
            ${decisions.length ? `<span class="prj-chip"><i data-lucide="list-checks"></i><span>${decidedText}</span></span>` : ""}
            ${p.shared_by ? `<span class="prj-chip"><i data-lucide="users"></i><span>${t("Shared by", "שותף על ידי")} ${esc(p.shared_by)}${p.access === "read" ? t(" · view only", " · צפייה בלבד") : ""}</span></span>` : ""}
            ${p.status === "archived" ? `<span class="prj-chip"><i data-lucide="archive"></i>${t("Archived", "בארכיון")}</span>` : ""}
          </div>
        </div>
      </section>`;

    const budgetCard = budget ? `
      <section class="prj-budget">
        <div class="prj-budget-row"><span>${t("Committed", "סגור")}</span><strong class="prj-num">${C.money(budget.committed)}</strong>
          ${budget.budget ? `<span class="prj-budget-of">${t("of", "מתוך")} ${C.money(budget.budget)}</span>` : ""}</div>
        ${budget.budget ? `<div class="prj-bar" role="progressbar" aria-valuenow="${budget.pct}" aria-valuemin="0" aria-valuemax="100"><span style="width:${budget.pct}%"></span></div>
        <div class="prj-budget-left">${budget.left >= 0 ? `${t("Left:", "נשאר:")} ${C.money(budget.left)}` : `${t("Over budget:", "חריגה:")} ${C.money(-budget.left)}`}</div>` : ""}
      </section>` : "";

    const decisionCards = decisions.map((s) => sectionCard(s)).join("");
    const noteCards = notes.map((s) => sectionCard(s)).join("");
    const addCard = (kind, label) => canEdit()
      ? `<button class="prj-card prj-card-add" data-act="new-section" data-kind="${kind}"><i data-lucide="plus"></i><span>${label}</span></button>` : "";

    return `<div class="prj-wrap" dir="${projectDir()}">
      ${hero}${budgetCard}
      <div class="prj-h2"><h2>${t("Decisions", "החלטות")}</h2>${decisions.length ? `<span>${decidedText}</span>` : ""}</div>
      <div class="prj-grid">${decisionCards}${addCard("options", t("Add a decision", "החלטה חדשה"))}</div>
      ${notes.length || canEdit() ? `<div class="prj-h2"><h2>${t("Notes", "הערות")}</h2></div><div class="prj-grid">${noteCards}${addCard("notes", t("Add notes", "הערות חדשות"))}</div>` : ""}
      ${loose.length ? `<div class="prj-h2"><h2>${t("Other items", "פריטים נוספים")}</h2><span>${loose.length}</span></div><div class="prj-items">${loose.map(itemRow).join("")}</div>` : ""}
      ${tasks.length ? `<div class="prj-h2"><h2>${t("Tasks", "משימות")}</h2><span>${tasks.length}</span></div><div class="prj-items">${tasks.map((t) =>
        `<div class="prj-item prj-task ${t.status === "done" ? "done" : ""}"><i data-lucide="${t.status === "done" ? "circle-check" : "circle"}"></i><span dir="auto">${esc(t.title)}</span></div>`).join("")}</div>` : ""}
    </div>`;
  }

  function sectionCard(s) {
    const st = C.sectionState(s, entries(), lang());
    const mine = C.orderOptions(entries().filter((e) => e.section_id === s.id));
    const files = mine.reduce((n, e) => n + (e.files?.length || 0), 0);
    const price = st.price ? (st.price.min === st.price.max ? C.shortMoney(st.price.min) : `${C.shortMoney(st.price.min)}–${C.shortMoney(st.price.max)}`) : "";
    const chips = s.kind === "options" ? mine.slice(0, 5).map((e) =>
      `<span class="prj-mini ${e.decision || "option"}" dir="auto">${e.decision === "chosen" ? "✓ " : ""}${esc(e.title)}</span>`).join("") : "";
    return `<button class="prj-card prj-sec-card ${st.decided ? "decided" : ""}" data-act="open-section" data-id="${esc(s.id)}">
      <div class="prj-card-top">
        <span class="prj-card-icon">${esc(s.icon || (s.kind === "options" ? "⚖️" : "📝"))}</span>
        <span class="prj-card-title" dir="auto">${esc(s.title)}</span>
        ${st.decided ? `<span class="prj-done-badge" title="${t("Decided", "הוחלט")}"><i data-lucide="check"></i></span>` : ""}
      </div>
      <div class="prj-card-line" dir="auto">${esc(st.line)}</div>
      ${chips ? `<div class="prj-minis">${chips}</div>` : s.kind !== "options" && mine.length ? `<div class="prj-card-preview" dir="auto">${mine.slice(0, 3).map((e) => esc(e.title)).join(" · ")}</div>` : ""}
      <div class="prj-card-meta">
        ${price ? `<span><i data-lucide="banknote"></i>${esc(price)}</span>` : ""}
        ${files ? `<span><i data-lucide="paperclip"></i>${files}</span>` : ""}
        ${s.summary ? `<span><i data-lucide="sparkles"></i>${t("Summary", "סיכום")}</span>` : ""}
      </div>
    </button>`;
  }

  function itemRow(e) {
    const files = e.files?.length || 0;
    const sub = e.url ? C.host(e.url) : C.preview(e.summary || e.body, 90);
    return `<button class="prj-item" data-act="open-entry" data-id="${esc(e.id)}">
      <i data-lucide="${e.url ? "link" : e.kind === "record" ? "id-card" : "sticky-note"}"></i>
      <span class="prj-item-text"><span class="prj-item-title" dir="auto">${esc(e.title || t("Untitled", "ללא שם"))}</span>${sub ? `<span class="prj-item-sub" dir="auto">${esc(sub)}</span>` : ""}</span>
      ${files ? `<span class="prj-item-files"><i data-lucide="paperclip"></i>${files}</span>` : ""}
    </button>`;
  }

  // ── Render: section ───────────────────────────────────────────────────────────────
  function sectionHtml(s) {
    if (!s) return `<div class="prj-empty">This section is gone.</div>`;
    const mine = C.orderOptions(entries().filter((e) => e.section_id === s.id));
    const isOptions = s.kind === "options";
    const mode = isOptions ? viewMode(s) : "list";
    const head = `
      <div class="prj-sec-head">
        <div class="prj-sec-title"><span class="prj-sec-icon">${esc(s.icon || (isOptions ? "⚖️" : "📝"))}</span>
          <h1 dir="auto">${esc(s.title)}</h1><span class="prj-kind-pill">${isOptions ? t("Decision", "החלטה") : t("Notes", "הערות")}</span></div>
        <div class="prj-sec-actions">
          ${isOptions && mine.length ? `<div class="prj-seg" role="tablist" aria-label="View">
            <button role="tab" aria-selected="${mode === "compare"}" class="${mode === "compare" ? "on" : ""}" data-act="view" data-view="compare"><i data-lucide="columns-3"></i><span>${t("Compare", "השוואה")}</span></button>
            <button role="tab" aria-selected="${mode === "cards"}" class="${mode === "cards" ? "on" : ""}" data-act="view" data-view="cards"><i data-lucide="layout-grid"></i><span>${t("Cards", "כרטיסים")}</span></button></div>` : ""}
          ${canEdit() ? `<button class="icon-btn" data-act="edit-section" aria-label="Edit section" title="Edit section"><i data-lucide="settings-2"></i></button>` : ""}
        </div>
      </div>`;
    const body = !mine.length ? `<div class="prj-empty small">${isOptions ? t("No options yet. Add the first one below, or ask Dana to collect them.", "אין עדיין אפשרויות. אפשר להוסיף כאן למטה, או לבקש מדנה לאסוף אותן.") : t("Nothing here yet.", "עדיין ריק.")}</div>`
      : mode === "compare" ? compareHtml(s, mine) : mode === "cards" ? `<div class="prj-opt-grid">${mine.map((e) => optionCard(s, e)).join("")}</div>`
      : `<div class="prj-items">${mine.map(itemRow).join("")}</div>`;
    const add = canEdit() ? `
      <form class="prj-add" data-form="add-entry">
        <i data-lucide="plus"></i>
        <input name="title" placeholder="${isOptions ? t("Add an option…", "אפשרות חדשה…") : t("Add a note…", "הערה חדשה…")}" autocomplete="off" dir="auto" maxlength="120" aria-label="${isOptions ? "New option name" : "New note title"}">
        <button class="btn-primary" type="submit">${t("Add", "הוספה")}</button>
      </form>` : "";
    return `<div class="prj-wrap" dir="${projectDir()}">${head}${summaryHtml(s)}${body}${add}</div>`;
  }

  function summaryHtml(s) {
    if (S.summaryEdit !== null) {
      return `<section class="prj-summary editing">
        <div class="prj-summary-label"><i data-lucide="sparkles"></i>${t("Summary", "סיכום")}</div>
        <textarea class="prj-textarea" id="prj-summary-text" rows="12" dir="auto" placeholder="TL;DR, a comparison table, the recommendation… (Markdown)">${esc(S.summaryEdit)}</textarea>
        <div class="prj-row-end"><button class="btn-secondary" data-act="summary-cancel">${t("Cancel", "ביטול")}</button><button class="btn-primary" data-act="summary-save">${t("Save", "שמירה")}</button></div>
      </section>`;
    }
    if (!s.summary) {
      return canEdit() ? `<section class="prj-summary empty"><i data-lucide="sparkles"></i>
        <span>${t(`No summary yet. Ask Dana to compare ${s.kind === "options" ? "the options" : "these"} and write the TL;DR, or `, "אין עדיין סיכום. אפשר לבקש מדנה להשוות ולכתוב תקציר, או ")}<button class="prj-linkbtn" data-act="summary-edit">${t("write one", "לכתוב אחד")}</button>.</span></section>` : "";
    }
    const long = s.summary.length > 700;
    return `<section class="prj-summary ${long && !S.summaryOpen ? "clamped" : ""}">
      <div class="prj-summary-label"><i data-lucide="sparkles"></i>${t("Summary", "סיכום")}
        ${canEdit() ? `<button class="icon-btn" data-act="summary-edit" aria-label="Edit summary" title="Edit summary"><i data-lucide="pencil"></i></button>` : ""}</div>
      <div class="markdown-body prj-md" dir="auto">${md(s.summary)}</div>
      ${long ? `<button class="prj-linkbtn prj-more" data-act="summary-toggle">${S.summaryOpen ? t("Show less", "פחות") : t("Show all", "הצג הכול")}</button>` : ""}
    </section>`;
  }

  function decisionPill(e) {
    const d = e.decision || "";
    return `<span class="prj-pill ${d || "option"}">${d === "chosen" ? "✓ " : ""}${esc(C.decisionLabel(d, lang()))}</span>`;
  }

  function compareHtml(s, mine) {
    const best = C.bestIds(s, mine);
    const cols = s.columns || [];
    const head = mine.map((e) => `<th scope="col" class="prj-cmp-opt ${e.decision || "option"}">
        <button data-act="open-entry" data-id="${esc(e.id)}"><span class="prj-cmp-name" dir="auto">${esc(e.title)}</span>${decisionPill(e)}</button></th>`).join("");
    const rows = cols.map((col) => `<tr><th scope="row" dir="auto">${esc(col.name)}${col.role === "price" ? ` <i data-lucide="banknote" class="prj-th-icon" title="${t("Counts toward the budget", "נספר בתקציב")}"></i>` : ""}</th>${
      mine.map((e) => {
        const isBest = best[col.key]?.has(e.id);
        return `<td class="${e.decision || "option"} ${isBest ? "best" : ""}" data-act="open-entry" data-id="${esc(e.id)}">${C.valueHtml(col, C.valueOf(e, col), lang())}${isBest ? `<span class="prj-best">${C.bestWay(col) === "low" ? t("Lowest", "הכי זול") : t("Best", "הכי טוב")}</span>` : ""}</td>`;
      }).join("")}</tr>`).join("");
    const files = `<tr class="prj-cmp-files"><th scope="row">${t("Files", "קבצים")}</th>${mine.map((e) => `<td class="${e.decision || "option"}" data-act="open-entry" data-id="${esc(e.id)}">${
      (e.files || []).length ? (e.files || []).map((f) => `<i data-lucide="${C.FILE_ICONS[C.fileKind(f.mime)]}" title="${esc(f.name)}"></i>`).join("") : `<span class="prj-none">—</span>`}</td>`).join("")}</tr>`;
    const notes = `<tr class="prj-cmp-notes"><th scope="row">${t("Notes", "הערות")}</th>${mine.map((e) => `<td class="${e.decision || "option"}" data-act="open-entry" data-id="${esc(e.id)}"><span dir="auto">${esc(C.preview(e.body, 110)) || `<span class="prj-none">—</span>`}</span></td>`).join("")}</tr>`;
    return `<div class="prj-cmp-wrap"><table class="prj-cmp">
      <thead><tr><th class="prj-cmp-corner" scope="col"></th>${head}</tr></thead>
      <tbody>${rows}${files}${notes}</tbody></table></div>
      ${!cols.length && canEdit() ? `<div class="prj-hint">${t("Add columns (price, phone…) to compare the options: ", "כדי להשוות, הוסיפו עמודות (מחיר, טלפון…): ")}<button class="prj-linkbtn" data-act="edit-section">${t("edit the section", "עריכת החלק")}</button>.</div>` : ""}`;
  }

  function optionCard(s, e) {
    const best = C.bestIds(s, entries().filter((x) => x.section_id === s.id));
    const vals = (s.columns || []).slice(0, 5).map((col) =>
      `<div class="prj-kv ${best[col.key]?.has(e.id) ? "best" : ""}"><dt dir="auto">${esc(col.name)}</dt><dd>${C.valueHtml(col, C.valueOf(e, col), lang())}</dd></div>`).join("");
    const files = e.files?.length || 0;
    return `<button class="prj-card prj-opt-card ${e.decision || "option"}" data-act="open-entry" data-id="${esc(e.id)}">
      <div class="prj-card-top"><span class="prj-card-title" dir="auto">${esc(e.title)}</span>${decisionPill(e)}</div>
      ${vals ? `<dl class="prj-kvs">${vals}</dl>` : `<div class="prj-card-preview" dir="auto">${esc(C.preview(e.body))}</div>`}
      <div class="prj-card-meta">${files ? `<span><i data-lucide="paperclip"></i>${t(C.plural(files, "file"), C.heCount(files, "קובץ אחד", "קבצים"))}</span>` : ""}${e.body ? `<span><i data-lucide="file-text"></i>${t("Details", "פרטים")}</span>` : ""}</div>
    </button>`;
  }

  // ── Sheet ─────────────────────────────────────────────────────────────────────────
  function renderSheet() {
    const sheet = $("prj-sheet");
    const backdrop = $("prj-sheet-backdrop");
    if (!sheet) return;
    const open = !!S.sheet && !!(S.sheet.type !== "entry" || entry(S.sheet.id));
    sheet.classList.toggle("hidden", !open);
    backdrop.classList.toggle("hidden", !open);
    if (!open) { sheet.innerHTML = ""; return; }
    sheet.setAttribute("dir", S.sheet.type === "project" && !project() ? "auto" : projectDir());
    sheet.innerHTML = S.sheet.type === "entry" ? entrySheet(entry(S.sheet.id))
      : S.sheet.type === "project" ? projectSheet(S.sheet.draft) : sectionSheet(S.sheet.draft);
  }

  function entrySheet(e) {
    const s = section(e.section_id) || null;
    const isOption = s?.kind === "options";
    const edit = canEdit();
    const phone = C.firstPhone(e, s);
    const link = C.safeUrl(e.url);
    const extras = C.extraFields(e, s);
    const editing = S.sheet.edit;   // "title" | "body" | "col:<key>" | "extra:<i>" | null
    const valueCell = (col, v, editKey) => {
      if (editing === editKey) return `<dd>${valueInput(col, v)}</dd>`;
      return `<dd ${edit ? `data-act="edit-value" data-key="${esc(editKey)}" tabindex="0" role="button" aria-label="Edit ${esc(col.name)}"` : ""} class="${edit ? "editable" : ""}">${C.valueHtml(col, v, lang())}</dd>`;
    };
    const values = (s?.columns || []).map((col) => `<div class="prj-kv"><dt dir="auto">${esc(col.name)}</dt>${valueCell(col, C.valueOf(e, col), `col:${col.key}`)}</div>`).join("")
      + extras.map((f, i) => `<div class="prj-kv extra"><dt dir="auto">${esc(f.name)}</dt>${valueCell({ name: f.name, type: f.type }, f.value, `extra:${i}`)}</div>`).join("");
    const title = editing === "title"
      ? `<input class="prj-title-input" id="prj-edit-title" value="${esc(e.title)}" dir="auto" maxlength="120" aria-label="Title">`
      : `<h2 dir="auto" ${edit ? `data-act="edit-title" class="editable" tabindex="0" role="button" title="Rename"` : ""}>${esc(e.title || t("Untitled", "ללא שם"))}</h2>`;
    const body = editing === "body"
      ? `<textarea class="prj-textarea" id="prj-edit-body" rows="10" dir="auto" placeholder="${t("Details (Markdown)", "פרטים (Markdown)")}">${esc(e.body || "")}</textarea>
         <div class="prj-row-end"><button class="btn-secondary" data-act="cancel-edit">${t("Cancel", "ביטול")}</button><button class="btn-primary" data-act="save-body">${t("Save", "שמירה")}</button></div>`
      : e.body ? `<div class="markdown-body prj-md" dir="auto">${md(e.body)}</div>`
      : edit ? `<button class="prj-linkbtn" data-act="edit-body">${t("Add details", "הוספת פרטים")}</button>` : `<span class="prj-none">—</span>`;
    const files = (e.files || []).map((f) => fileHtml(e, f)).join("");
    const moveOptions = [`<option value="">${t("No section", "ללא חלק")}</option>`, ...sections().map((x) =>
      `<option value="${esc(x.id)}" ${x.id === e.section_id ? "selected" : ""}>${esc(x.title)}</option>`)].join("");
    return `
      <header class="prj-sheet-head">
        <div class="prj-sheet-title">${title}${e.shared_by ? `<span class="prj-sheet-sub">${t(`Added to ${esc(e.shared_by)}'s project`, `בפרויקט של ${esc(e.shared_by)}`)}</span>` : ""}</div>
        <button class="icon-btn" data-act="close-sheet" aria-label="Close"><i data-lucide="x"></i></button>
      </header>
      <div class="prj-sheet-body">
        ${isOption ? `<div class="prj-decide" role="radiogroup" aria-label="Decision">${C.DECISIONS.map((d) =>
          `<button role="radio" aria-checked="${(e.decision || "") === d}" class="${d || "option"} ${(e.decision || "") === d ? "on" : ""}" ${edit ? `data-act="decide" data-decision="${d}"` : "disabled"}>${d === "chosen" ? "✓ " : ""}${C.decisionLabel(d, lang())}</button>`).join("")}</div>` : ""}
        ${phone || link ? `<div class="prj-quick">
          ${phone ? `<a class="chip" href="tel:${esc(phone.tel)}"><i data-lucide="phone"></i><span dir="ltr">${esc(phone.value)}</span></a>
                     <a class="chip" href="https://wa.me/${esc(phone.wa)}" target="_blank" rel="noopener noreferrer"><i data-lucide="message-circle"></i><span>WhatsApp</span></a>` : ""}
          ${link ? `<a class="chip" href="${esc(link)}" target="_blank" rel="noopener noreferrer"><i data-lucide="external-link"></i><span>${esc(C.host(link))}</span></a>` : ""}
        </div>` : ""}
        ${values ? `<section class="prj-block"><dl class="prj-kvs wide">${values}</dl></section>` : ""}
        ${e.summary ? `<section class="prj-block"><h3><i data-lucide="sparkles"></i>${t("From the link", "מהקישור")}</h3><p dir="auto" class="prj-p">${esc(e.summary)}</p></section>` : ""}
        <section class="prj-block"><h3>${t("Details", "פרטים")}${edit && editing !== "body" && e.body ? ` <button class="icon-btn" data-act="edit-body" aria-label="Edit details"><i data-lucide="pencil"></i></button>` : ""}</h3>${body}</section>
        <section class="prj-block prj-files-block" data-drop="${edit ? "files" : ""}">
          <h3>${t("Files", "קבצים")}${(e.files || []).length ? ` <span class="prj-count">${e.files.length}</span>` : ""}</h3>
          <div class="prj-files">${files || `<span class="prj-none">${t("No files.", "אין קבצים.")}</span>`}</div>
          ${edit ? `<label class="prj-upload"><input type="file" multiple data-upload="${esc(e.id)}" hidden><i data-lucide="upload"></i><span>${t("Add files", "הוספת קבצים")}</span><em>${t("or drop them here", "או לגרור לכאן")}</em></label>` : ""}
        </section>
        ${edit ? `<section class="prj-block prj-sheet-foot">
          <label class="prj-field inline"><span>${t("Section", "חלק")}</span><select class="settings-input" data-act-change="move" aria-label="Section">${moveOptions}</select></label>
          <button class="btn-destructive-outline" data-act="delete-entry"><i data-lucide="trash-2"></i>${t("Delete", "מחיקה")}</button>
        </section>` : ""}
      </div>`;
  }

  function valueInput(col, v) {
    const val = C.isEmpty(v) ? "" : v;
    if (col.type === "checkbox") {
      return `<div class="prj-inline-edit"><select class="settings-input" id="prj-edit-value"><option value="true" ${v === true ? "selected" : ""}>${t("Yes", "כן")}</option><option value="false" ${v !== true ? "selected" : ""}>${t("No", "לא")}</option></select>${saveBtns()}</div>`;
    }
    if (col.type === "select" && col.options?.length) {
      return `<div class="prj-inline-edit"><select class="settings-input" id="prj-edit-value"><option value=""></option>${col.options.map((o) =>
        `<option ${o === val ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>${saveBtns()}</div>`;
    }
    const type = col.type === "date" ? "date" : col.type === "url" ? "url" : col.type === "phone" ? "tel" : "text";
    const mode = col.type === "money" || col.type === "number" || col.type === "rating" ? ` inputmode="decimal"` : "";
    return `<div class="prj-inline-edit"><input class="settings-input" id="prj-edit-value" type="${type}"${mode} value="${esc(val)}" dir="auto">${saveBtns()}</div>`;
  }
  const saveBtns = () => `<button class="icon-btn" data-act="save-value" aria-label="Save"><i data-lucide="check"></i></button><button class="icon-btn" data-act="cancel-edit" aria-label="Cancel"><i data-lucide="x"></i></button>`;

  function fileHtml(e, f) {
    const kind = C.fileKind(f.mime);
    const media = kind === "audio" ? `<audio controls preload="none" src="${esc(f.url)}"></audio>`
      : kind === "video" ? `<video controls preload="metadata" src="${esc(f.url)}"></video>`
      : kind === "image" ? `<a href="${esc(f.url)}" target="_blank" rel="noopener"><img src="${esc(f.url)}" alt="${esc(f.name)}" loading="lazy"></a>` : "";
    return `<div class="prj-file ${kind}">
      <div class="prj-file-row">
        <i data-lucide="${C.FILE_ICONS[kind]}"></i>
        <a class="prj-file-name" href="${esc(f.url)}" target="_blank" rel="noopener" dir="auto">${esc(f.name)}</a>
        <span class="prj-file-meta">${esc(C.fileSize(f.size))}${f.source === "whatsapp" ? " · WhatsApp" : ""}</span>
        <a class="icon-btn" href="${esc(f.url)}?download=1" aria-label="Download ${esc(f.name)}" title="Download"><i data-lucide="download"></i></a>
        ${canEdit() ? `<button class="icon-btn" data-act="delete-file" data-id="${esc(f.id)}" aria-label="Remove ${esc(f.name)}" title="Remove"><i data-lucide="trash-2"></i></button>` : ""}
      </div>
      ${f.note ? `<div class="prj-file-note" dir="auto">${esc(f.note)}</div>` : ""}
      ${media}
      ${f.transcript ? `<details class="prj-transcript"><summary>${t("Transcript", "תמלול")}</summary><p dir="auto">${esc(f.transcript)}</p></details>` : ""}
    </div>`;
  }

  function projectSheet(d) {
    const isNew = !d.id;
    // An existing project's budget is in its owner's currency; a new one's in mine (that of a project of my own).
    const sym = C.currencySymbol(isNew ? (S.list || []).find((p) => !p.shared_by)?.currency : d.currency).trim();
    return `
      <header class="prj-sheet-head"><div class="prj-sheet-title"><h2>${isNew ? "New project" : "Edit project"}</h2></div>
        <button class="icon-btn" data-act="close-sheet" aria-label="Close"><i data-lucide="x"></i></button></header>
      <form class="prj-sheet-body prj-form" data-form="project">
        <div class="prj-form-row">
          <label class="prj-field icon"><span>Icon</span><input class="settings-input" name="icon" value="${esc(d.icon || "📁")}" maxlength="4" aria-label="Icon (emoji)"></label>
          <label class="prj-field grow"><span>Name</span><input class="settings-input" name="title" value="${esc(d.title || "")}" dir="auto" required maxlength="80" placeholder="Our wedding" autofocus></label>
        </div>
        <label class="prj-field"><span>Description</span><input class="settings-input" name="description" value="${esc(d.description || "")}" dir="auto" maxlength="200" placeholder="One line"></label>
        <div class="prj-form-row">
          <label class="prj-field grow"><span>Date <em>(optional — shows a countdown)</em></span><input class="settings-input" type="date" name="target_date" value="${esc(d.target_date || "")}"></label>
          <label class="prj-field grow"><span>Budget${sym ? ` ${esc(sym)}` : ""} <em>(optional)</em></span><input class="settings-input" name="budget" inputmode="decimal" value="${d.budget ? esc(d.budget) : ""}" placeholder="150000"></label>
        </div>
        <div class="prj-row-end">
          ${!isNew ? `<button type="button" class="btn-secondary" data-act="archive-project">${d.status === "archived" ? "Restore" : "Archive"}</button>
                      <button type="button" class="btn-destructive-outline" data-act="delete-project"><i data-lucide="trash-2"></i>Delete</button><span class="prj-spacer"></span>` : ""}
          <button type="button" class="btn-secondary" data-act="close-sheet">Cancel</button>
          <button type="submit" class="btn-primary">${isNew ? "Create" : "Save"}</button>
        </div>
      </form>`;
  }

  function sectionSheet(d) {
    const isNew = !d.id;
    const cols = d.columns.map((c, i) => `
      <div class="prj-col-row" data-i="${i}">
        <input class="settings-input" data-col="name" value="${esc(c.name)}" dir="auto" placeholder="Column name" aria-label="Column name" maxlength="40">
        <select class="settings-input" data-col="type" aria-label="Type">${C.TYPES.filter((t) => t !== "select" || c.type === "select").map((t) => `<option value="${t}" ${t === c.type ? "selected" : ""}>${C.TYPE_LABELS[t]}</option>`).join("")}</select>
        <label class="prj-col-price" title="Counts toward the budget when chosen"><input type="checkbox" data-col="price" ${c.role === "price" ? "checked" : ""}><span>Budget</span></label>
        <button type="button" class="icon-btn" data-act="col-up" data-i="${i}" aria-label="Move up" ${i === 0 ? "disabled" : ""}><i data-lucide="arrow-up"></i></button>
        <button type="button" class="icon-btn" data-act="col-remove" data-i="${i}" aria-label="Remove column"><i data-lucide="x"></i></button>
      </div>`).join("");
    return `
      <header class="prj-sheet-head"><div class="prj-sheet-title"><h2>${isNew ? (d.kind === "options" ? "New decision" : "New notes") : "Edit section"}</h2></div>
        <button class="icon-btn" data-act="close-sheet" aria-label="Close"><i data-lucide="x"></i></button></header>
      <form class="prj-sheet-body prj-form" data-form="section">
        <div class="prj-form-row">
          <label class="prj-field icon"><span>Icon</span><input class="settings-input" name="icon" value="${esc(d.icon || "")}" maxlength="4" placeholder="${d.kind === "options" ? "⚖️" : "📝"}"></label>
          <label class="prj-field grow"><span>Name</span><input class="settings-input" name="title" value="${esc(d.title || "")}" dir="auto" required maxlength="60" placeholder="${d.kind === "options" ? "Photographers" : "Ideas"}" autofocus></label>
        </div>
        <div class="prj-field"><span>Kind</span><div class="prj-seg">
          <button type="button" class="${d.kind === "options" ? "on" : ""}" data-act="section-kind" data-kind="options"><i data-lucide="scale"></i><span>Decision — compare options</span></button>
          <button type="button" class="${d.kind !== "options" ? "on" : ""}" data-act="section-kind" data-kind="notes"><i data-lucide="sticky-note"></i><span>Notes</span></button></div></div>
        ${d.kind === "options" ? `<div class="prj-field"><span>Columns <em>— what every option is compared on</em></span>
          <div class="prj-cols">${cols}</div>
          <button type="button" class="prj-linkbtn" data-act="col-add"><i data-lucide="plus"></i> Add column</button></div>` : ""}
        <div class="prj-row-end">
          ${!isNew ? `<button type="button" class="btn-destructive-outline" data-act="delete-section"><i data-lucide="trash-2"></i>Delete</button><span class="prj-spacer"></span>` : ""}
          <button type="button" class="btn-secondary" data-act="close-sheet">Cancel</button>
          <button type="submit" class="btn-primary">${isNew ? "Create" : "Save"}</button>
        </div>
      </form>`;
  }

  /** Read the section editor's column rows back into the draft (before re-rendering it). */
  function syncColumns() {
    const d = S.sheet?.draft;
    if (!d || S.sheet.type !== "section") return;
    document.querySelectorAll("#prj-sheet .prj-col-row").forEach((row) => {
      const c = d.columns[Number(row.dataset.i)];
      if (!c) return;
      c.name = row.querySelector('[data-col="name"]').value;
      c.type = row.querySelector('[data-col="type"]').value;
      c.role = row.querySelector('[data-col="price"]').checked ? "price" : "";
    });
    const f = document.querySelector('#prj-sheet [data-form="section"]');
    if (f) { d.title = f.title.value; d.icon = f.icon.value; }
  }

  function defaultColumns() {
    const he = projectDir() === "rtl";
    return he ? [{ name: "מחיר", type: "money", role: "price" }, { name: "טלפון", type: "phone" }, { name: "דירוג", type: "rating" }]
              : [{ name: "Price", type: "money", role: "price" }, { name: "Phone", type: "phone" }, { name: "Rating", type: "rating" }];
  }

  // ── Actions ───────────────────────────────────────────────────────────────────────
  async function putEntry(e, changes) {
    const r = await api("PUT", `/api/entries/${encodeURIComponent(e.id)}`, changes);
    if (r.status === 200 && r.body?.entry) {
      const i = S.data.entries.findIndex((x) => x.id === e.id);
      if (i >= 0) S.data.entries[i] = r.body.entry;
      return true;
    }
    failed(r, "save");
    return false;
  }

  async function saveValue() {
    const e = entry(S.sheet.id);
    const s = section(e.section_id);
    const key = S.sheet.edit || "";
    const input = $("prj-edit-value");
    if (!e || !input) return;
    let col;
    if (key.startsWith("col:")) col = (s?.columns || []).find((c) => c.key === key.slice(4));
    else if (key.startsWith("extra:")) { const f = C.extraFields(e, s)[Number(key.slice(6))]; col = f && { name: f.name, type: f.type || "text" }; }
    if (!col) return;
    const raw = col.type === "checkbox" ? input.value === "true" : input.value;
    S.sheet.edit = null;
    if (await putEntry(e, { fields: C.setField(e.fields, col, raw) })) { render(); refreshListQuietly(); } else render();
  }

  function refreshListQuietly() { loadList().then(renderSide).then(icons); }

  async function upload(entryId, fileList) {
    const list = [...(fileList || [])];
    if (!list.length) return;
    toast(list.length === 1 ? `Uploading ${list[0].name}…` : `Uploading ${list.length} files…`);
    let ok = 0;
    for (const f of list) {
      const form = new FormData();
      form.append("file", f, f.name);
      const r = await api("POST", `/api/entries/${encodeURIComponent(entryId)}/files`, form);
      if (r.status === 200) ok++; else failed(r, `upload ${f.name}`);
    }
    if (ok) toast(ok === 1 ? "File added" : `${ok} files added`);
    await refresh({ list: false });
  }

  async function onClick(ev) {
    const el = ev.target.closest("[data-act]");
    if (!el || !$("panel-projects").contains(el)) return;
    const act = el.dataset.act;
    const id = el.dataset.id;
    switch (act) {
      case "home": S.sectionId = null; S.projectId = null; S.data = null; S.sheet = null; store.set("dana_project", ""); render(); return;
      case "back": if (S.sectionId) openSection(null); else { S.projectId = null; S.data = null; store.set("dana_project", ""); render(); } return;
      case "project-home": openSection(null); return;
      case "open-project": await openProject(id); return;
      case "open-section": openSection(id); return;
      case "open-entry": S.sheet = { type: "entry", id, edit: null }; renderSheet(); icons(); focusSheet(); return;
      case "close-sheet": closeSheet(); return;
      case "toggle-archived": S.showArchived = !S.showArchived; renderSide(); icons(); return;
      case "view": S.view = el.dataset.view; store.set("dana_project_view", S.view); render(); return;
      case "new-project": S.sheet = { type: "project", draft: { icon: "📁" } }; renderSheet(); icons(); focusSheet(); return;
      case "edit-project": S.sheet = { type: "project", draft: { ...project() } }; renderSheet(); icons(); focusSheet(); return;
      case "new-section": {
        const kind = el.dataset.kind || "options";
        S.sheet = { type: "section", draft: { kind, title: "", icon: "", columns: kind === "options" ? defaultColumns() : [] } };
        renderSheet(); icons(); focusSheet(); return;
      }
      case "edit-section": {
        const s = section();
        S.sheet = { type: "section", draft: { ...s, columns: (s.columns || []).map((c) => ({ ...c })) } };
        renderSheet(); icons(); focusSheet(); return;
      }
      case "section-kind": syncColumns(); S.sheet.draft.kind = el.dataset.kind;
        if (el.dataset.kind === "options" && !S.sheet.draft.columns.length) S.sheet.draft.columns = defaultColumns();
        renderSheet(); icons(); return;
      case "col-add": syncColumns(); S.sheet.draft.columns.push({ name: "", type: "text" }); renderSheet(); icons();
        [...document.querySelectorAll('#prj-sheet [data-col="name"]')].pop()?.focus(); return;
      case "col-remove": syncColumns(); S.sheet.draft.columns.splice(Number(el.dataset.i), 1); renderSheet(); icons(); return;
      case "col-up": { syncColumns(); const i = Number(el.dataset.i); const c = S.sheet.draft.columns; [c[i - 1], c[i]] = [c[i], c[i - 1]]; renderSheet(); icons(); return; }
      case "summary-edit": S.summaryEdit = section()?.summary || ""; renderMain(); icons(); $("prj-summary-text")?.focus(); return;
      case "summary-cancel": S.summaryEdit = null; renderMain(); icons(); return;
      case "summary-toggle": S.summaryOpen = !S.summaryOpen; renderMain(); icons(); return;
      case "summary-save": {
        const text = $("prj-summary-text")?.value ?? "";
        const r = await api("PUT", `/api/sections/${encodeURIComponent(S.sectionId)}`, { summary: text });
        if (r.status === 200) { S.summaryEdit = null; await refresh({ list: false }); } else failed(r, "save the summary");
        return;
      }
      case "decide": {
        const e = entry(S.sheet.id);
        const d = el.dataset.decision || "";
        if (e && (e.decision || "") !== d && await putEntry(e, { decision: d })) {
          if (d === "chosen") toast(`Chosen: ${e.title}`);
          render(); refreshListQuietly();
        }
        return;
      }
      case "edit-title": S.sheet.edit = "title"; renderSheet(); icons(); $("prj-edit-title")?.focus(); $("prj-edit-title")?.select(); return;
      case "edit-body": S.sheet.edit = "body"; renderSheet(); icons(); $("prj-edit-body")?.focus(); return;
      case "edit-value": S.sheet.edit = el.dataset.key; renderSheet(); icons(); $("prj-edit-value")?.focus(); return;
      case "cancel-edit": S.sheet.edit = null; renderSheet(); icons(); return;
      case "save-value": await saveValue(); return;
      case "save-body": {
        const e = entry(S.sheet.id);
        const text = $("prj-edit-body")?.value ?? "";
        S.sheet.edit = null;
        await putEntry(e, { body: text });
        render();
        return;
      }
      case "delete-entry": {
        const e = entry(S.sheet.id);
        if (!e || !confirm(`Delete “${e.title}”${e.files?.length ? ` and its ${C.plural(e.files.length, "file")}` : ""}?`)) return;
        const r = await api("DELETE", `/api/entries/${encodeURIComponent(e.id)}`);
        if (r.status === 200) { S.sheet = null; await refresh(); } else failed(r, "delete");
        return;
      }
      case "delete-file": {
        const e = entry(S.sheet.id);
        const f = (e?.files || []).find((x) => x.id === id);
        if (!f || !confirm(`Remove “${f.name}”?`)) return;
        const r = await api("DELETE", f.url);
        if (r.status === 200) await refresh({ list: false }); else failed(r, "remove the file");
        return;
      }
      case "archive-project": {
        const p = S.sheet.draft;
        const r = await api("PUT", `/api/projects/${encodeURIComponent(p.id)}`, { status: p.status === "archived" ? "active" : "archived" });
        if (r.status === 200) { S.sheet = null; await refresh(); } else failed(r, "archive");
        return;
      }
      case "delete-project": {
        const p = S.sheet.draft;
        if (!confirm(`Delete “${p.title}” with everything in it?`)) return;
        const r = await api("DELETE", `/api/projects/${encodeURIComponent(p.id)}`);
        if (r.status === 200) { S.sheet = null; S.projectId = null; S.sectionId = null; S.data = null; await refresh(); } else failed(r, "delete");
        return;
      }
      case "delete-section": {
        const s = S.sheet.draft;
        const n = entries().filter((e) => e.section_id === s.id).length;
        if (!confirm(n ? `Delete the section “${s.title}”? Its ${C.plural(n, "item")} stay in the project.` : `Delete the section “${s.title}”?`)) return;
        const r = await api("DELETE", `/api/sections/${encodeURIComponent(s.id)}`);
        if (r.status === 200) { S.sheet = null; S.sectionId = null; await refresh(); } else failed(r, "delete");
        return;
      }
      default:
    }
  }

  async function onSubmit(ev) {
    const form = ev.target.closest("[data-form]");
    if (!form || !$("panel-projects").contains(form)) return;
    ev.preventDefault();
    if (S.busy) return;
    S.busy = true;
    try {
      if (form.dataset.form === "add-entry") {
        const title = form.title.value.trim();
        if (!title) return;
        const s = section();
        const r = await api("POST", "/api/entries", { project_id: S.projectId, section_id: s?.id || null, title,
                                                       kind: s?.kind === "options" ? "record" : "note" });
        if (r.status === 200) {
          form.title.value = "";
          await refresh();
          if (s?.kind === "options") { S.sheet = { type: "entry", id: r.body.id, edit: null }; renderSheet(); icons(); }
        } else failed(r, "add");
      } else if (form.dataset.form === "project") {
        const d = S.sheet.draft;
        const body = { title: form.title.value.trim(), icon: form.icon.value.trim() || "📁", description: form.description.value.trim(),
                       target_date: form.target_date.value || "", budget: form.budget.value.trim() };
        if (!body.title) return;
        const r = d.id ? await api("PUT", `/api/projects/${encodeURIComponent(d.id)}`, body) : await api("POST", "/api/projects", body);
        if (r.status !== 200) { failed(r, "save"); return; }
        S.sheet = null;
        if (!d.id) { await loadList(); await openProject(r.body.id); } else await refresh();
      } else if (form.dataset.form === "section") {
        syncColumns();
        const d = S.sheet.draft;
        const body = { title: d.title.trim(), icon: d.icon.trim(), kind: d.kind,
                       columns: d.kind === "options" ? C.columnsPayload(d.columns) : [] };
        if (!body.title) return;
        if (d.id && d.kind !== "options") delete body.columns;
        const r = d.id ? await api("PUT", `/api/sections/${encodeURIComponent(d.id)}`, body)
                       : await api("POST", `/api/projects/${encodeURIComponent(S.projectId)}/sections`, body);
        if (r.status !== 200) { failed(r, "save"); return; }
        S.sheet = null;
        if (!d.id) S.sectionId = r.body.id;
        await refresh();
      }
    } finally {
      S.busy = false;
    }
  }

  async function onKey(ev) {
    if (!S.visible) return;
    if (ev.key === "Escape") {
      if (S.sheet?.edit) { S.sheet.edit = null; renderSheet(); icons(); ev.preventDefault(); return; }
      if (S.sheet) { closeSheet(); ev.preventDefault(); }
      return;
    }
    const t = ev.target;
    if (ev.key === "Enter" && t?.id === "prj-edit-value") { ev.preventDefault(); await saveValue(); return; }
    if (ev.key === "Enter" && t?.id === "prj-edit-title" && S.sheet?.edit === "title") {
      ev.preventDefault();
      const e = entry(S.sheet.id);
      const title = t.value.trim();
      S.sheet.edit = null;
      if (title && title !== e.title) { await putEntry(e, { title }); render(); } else renderSheet();
      icons();
      return;
    }
    if ((ev.key === "Enter" || ev.key === " ") && t?.matches?.("[data-act][role=button]")) { ev.preventDefault(); t.click(); }
  }

  async function onChange(ev) {
    const t = ev.target;
    if (t?.dataset?.upload) { await upload(t.dataset.upload, t.files); t.value = ""; return; }
    if (t?.dataset?.actChange === "move") {
      const e = entry(S.sheet?.id);
      if (e && await putEntry(e, { section_id: t.value })) { toast("Moved"); render(); refreshListQuietly(); }
    }
  }

  function onBlur(ev) {
    // Leaving the title input saves it (like Enter).
    if (ev.target?.id === "prj-edit-title") onKey({ key: "Enter", target: ev.target, preventDefault() {} });
  }

  function closeSheet() { S.sheet = null; renderSheet(); }
  function focusSheet() { setTimeout(() => $("prj-sheet")?.querySelector("[autofocus], input, button")?.focus(), 30); }

  function bindDrop() {
    const sheet = $("prj-sheet");
    sheet.addEventListener("dragover", (ev) => {
      const zone = ev.target.closest('[data-drop="files"]');
      if (!zone) return;
      ev.preventDefault();
      zone.classList.add("drag");
    });
    sheet.addEventListener("dragleave", (ev) => ev.target.closest('[data-drop="files"]')?.classList.remove("drag"));
    sheet.addEventListener("drop", (ev) => {
      const zone = ev.target.closest('[data-drop="files"]');
      if (!zone || S.sheet?.type !== "entry") return;
      ev.preventDefault();
      zone.classList.remove("drag");
      upload(S.sheet.id, ev.dataTransfer?.files);
    });
  }

  // ── Wiring ────────────────────────────────────────────────────────────────────────
  function init() {
    const panel = $("panel-projects");
    if (!panel) return;
    panel.addEventListener("click", onClick);
    panel.addEventListener("submit", onSubmit);
    panel.addEventListener("change", onChange);
    panel.addEventListener("focusout", onBlur);
    document.addEventListener("keydown", onKey);
    $("prj-sheet-backdrop")?.addEventListener("click", closeSheet);
    bindDrop();
    window.addEventListener("resize", () => { if (S.visible && S.sectionId && !S.view) renderMain(); });
  }

  async function show() {
    S.visible = true;
    render();
    await refresh();
  }
  function hide() { S.visible = false; }

  const TEMPLATE = `
    <header class="top-bar prj-top">
        <nav class="prj-crumbs" id="prj-crumbs" aria-label="Where you are"><span class="top-bar-title">Projects</span></nav>
        <div class="top-bar-actions" id="prj-actions"></div>
    </header>
    <div class="prj-body">
        <aside class="prj-side" id="prj-side" aria-label="Projects"></aside>
        <main class="prj-main" id="prj-main"></main>
    </div>
    <div class="prj-sheet-backdrop hidden" id="prj-sheet-backdrop"></div>
    <aside class="prj-sheet hidden" id="prj-sheet" role="dialog" aria-modal="true" aria-label="Details"></aside>
    <div class="prj-toast hidden" id="prj-toast" role="status" aria-live="polite"></div>

  `;

  Dana.panel({
    scripts: ["projects-core.js"],
    start() { C = window.DanaProjectsCore; esc = C.esc; },
    mount(el) {
      if (!el.firstChild) { el.dataset.level = "home"; el.innerHTML = TEMPLATE; init(); }
      show();
    },
    unmount: hide,
  });
})();
