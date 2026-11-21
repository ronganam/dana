/* Money — bank and card transactions, investment accounts (docs/money.md "Money tab"). The pure part is
 * static/money-core.js; the Overview / Spending / Cash flow / Save / Investments views are drawn by
 * static/money-dash.js (window.DanaMoneyDash).
 *
 * Views: Overview · Spending · Cash flow · Save · Investments (only with an investment account) ·
 * Transactions; the header's month switcher hides on Save and Investments (not tied to a month). Each view
 * loads what it shows (cached per URL; a change elsewhere refetches the view on screen and marks the rest
 * stale):
 *   GET    /api/money/overview?month=           tiles, banners, sources' states (every view)
 *   GET    /api/money/categories?period=        the category taxonomy (the picker, labels)
 *   GET    /api/money/insights?month=           Overview: insight cards
 *   GET    /api/money/pace?month=               Overview: everyday pace
 *   GET    /api/money/balance?days=90|180|365   Overview: coming up · Cash flow: bank balance, plans
 *   GET    /api/money/cashflow?n=12&end=        Overview: typical net · Cash flow: in / out
 *   GET    /api/money/trends?month=             Spending: where it went, movers, big purchases, Everyday bars
 *   GET    /api/money/months?n=12               Spending: the Everything bars
 *   GET    /api/money/merchants?period=YYYY-MM  Spending: top merchants
 *   GET    /api/money/recurring                 Cash flow: fixed costs and regular income
 *   GET    /api/money/portfolio?days=365        Investments: value, history, allocation, holdings, activity
 *   GET    /api/money/savings                   Save: totals, ways to save, spending to watch
 *   GET    /api/money/savings/{key}             one item: polled every 3 s (up to 90 s) while its price check runs
 *   PATCH  /api/money/savings/{key}             {status, snooze_days} · {answers} · {target} · {subitem} → {item, undo_id}
 *   POST   /api/money/savings/{key}/check       "Check prices" (only when pressed) → 202 · 400 needs_answer · 409 · 429 · 503
 *   GET    /api/money/transactions?…            Transactions: the list (filters, search, next_cursor)
 *   GET    /api/money/transactions/{id}         the detail
 *   PATCH  /api/money/transactions/{id}         {category, for_merchant} · {note} · {flow} · {one_off} · {yearly} → undo_id
 *   POST   /api/undo/{id}                       the toast's Undo
 *   GET    /api/money/sources                   logins, their accounts, a pending code_request
 *   GET    /api/money/companies                 the login form's fields
 *   POST / PATCH / DELETE /api/money/sources…   add, update login, remove; …/{id}/sync "Sync now"
 *   POST   /api/money/code-requests/{id}        the SMS sign-in code {code}
 *   GET    /api/money/changes?since=            polled every 30 s while shown (5 s while a sync runs)
 *   POST   /api/chat/context {kind: "transaction", id}   "Ask Dana"
 *
 * window.DanaMoney = {show, hide}; app-nav.js calls them on tab switches. The rail / bottom-nav buttons
 * ([data-money-nav]) appear only for a user with `money` connected (GET /api/me → addons). The page's
 * state (view, month, filters, the accounts view) lives in the hash: #money/spending?month=2026-10&category=food.
 * Login details go only into the add / update request bodies: never the URL, the console or storage.
 */
(() => {
  "use strict";
  const C = window.DanaMoneyCore;
  const $ = (id) => document.getElementById(id);
  const esc = C.esc;
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };
  const icon = (name, cls = "") => `<i data-lucide="${name}"${cls ? ` class="${cls}"` : ""}></i>`;
  const wide = () => window.matchMedia("(min-width: 1180px)").matches;

  const POLL_MS = 30000, FAST_MS = 5000, PAGE = 50, FAST_FOR_MS = 4 * 60000;
  const CHECK_POLL_MS = 3000, CHECK_POLL_FOR_MS = 90000;
  const GROUP_ICONS = {
    food: "utensils", housing: "house", transport: "car", bills: "receipt", health: "heart-pulse", shopping: "shopping-bag",
    leisure: "ticket", family: "baby", people: "users", finance: "landmark", other: "circle-dashed", income: "arrow-down-left",
  };

  const S = {
    enabled: null, visible: false, wired: false,
    month: C.monthOf(), view: "page", tab: "overview", f: { ...C.EMPTY_FILTERS },
    d: {}, stale: new Set(), sources: null, companies: null, taxonomy: null,
    error: "", loaded: false,
    txns: [], next: null, txLoading: false, txError: "", txLoaded: false, txDirty: true,
    openId: null, detail: null, detailError: "", noteDraft: {}, noteBusy: false,
    expanded: new Set(), openBills: new Set(), horizon: 90, invDays: 365, spendMode: "everyday",
    // Save: the open card, the card whose target field is open (and what is typed in it), writes in
    // flight, a price check's last answer in words, the running polls, collapsed sections.
    saveOpen: null, saveTarget: null, saveDraft: {}, saveTargetError: "", saveBusy: {}, checkNote: {}, savePolls: {}, pollGaveUp: new Set(), saveKeys: false,
    saveCollapsed: new Set(),
    cursor: null, fastUntil: 0,
    sheet: null,
    codeDrafts: {}, codeBusy: {}, codeError: {}, codeSent: {},
    syncBusy: {},
  };
  let txSeq = 0, detailSeq = 0;
  const fetchSeq = {};
  let pollTimer = null, tickTimer = null, searchTimer = null, toastTimer = null;

  async function request(method, url, body) {
    let r;
    try {
      r = await fetch(url, { method, cache: "no-store", headers: body !== undefined ? { "Content-Type": "application/json" } : {},
                             body: body !== undefined ? JSON.stringify(body) : undefined });
    } catch (_) {
      return { status: 0, body: null };
    }
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    return { status: r.status, body: data };
  }

  // ---------- lookups ----------

  function accounts() {
    const map = {};
    (S.sources || []).forEach((s) => (s.accounts || []).forEach((a) => { map[a.id] = { ...a, company: s.company, company_name: s.company_name }; }));
    const ov = lastOverview();
    if (ov) [...(ov.cards || []).map((a) => ({ ...a, kind: "card" })), ...(ov.banks || []).map((a) => ({ ...a, kind: "bank" }))]
      .forEach((a) => { map[a.id] = { ...(map[a.id] || {}), ...a }; });
    return map;
  }
  const acctOf = (id) => accounts()[id];
  /** Whether the Investments view has anything: an investment account in the overview or a login's accounts. */
  const hasInvestments = () => ((lastOverview() || {}).investments || []).length > 0
    || Object.values(accounts()).some((a) => a.kind === "investment");
  /** Whether the Meal card view has anything: a meal card in the overview or a login's accounts. */
  const hasMeals = () => ((lastOverview() || {}).meals || []).length > 0
    || (S.sources || []).some((s) => (s.accounts || []).some((a) => a.kind === "meal"));
  /** A login's kind for its icon: its accounts', else its company's (bank, card, investment). */
  const sourceKind = (s) => ((s.accounts || [])[0] || {}).kind
    || ((S.companies || []).find((c) => c.id === s.company) || {}).kind || (s.company === "hapoalim" ? "bank" : "card");
  const acctName = (t) => C.accountName(acctOf(t.account_id)) || t.account_label || "";
  const companyOf = (t) => (acctOf(t.account_id) || {}).company_name || "";
  const catLabel = (key) => C.categoryLabel(S.taxonomy, key);
  const today = () => C.todayIso();
  const thisMonth = () => C.monthOf();
  const isCurrent = () => S.month === thisMonth();
  const lastOverview = () => (S.d.overview ? S.d.overview.body : null);  // any month's: accounts and attention are the same
  const seriesCls = (group) => (group === "income" ? "g-income" : `g-${C.seriesOf(group)}`);
  function rowIcon(t) {
    if (!t.category && t.flow === "card_bill") return ["credit-card", "g-none"];
    if (!t.category && t.flow === "own_transfer") return ["repeat", "g-none"];
    if (!t.group) return ["circle-help", "g-uncategorized"];
    return [GROUP_ICONS[t.group] || "circle", seriesCls(t.group)];
  }

  // ---------- loading ----------

  // What each view shows: a name → its URL for the page's month (and horizon); got(name) is its answer
  // only while that URL is still the one wanted, so a month switch shows skeletons, never the old month.
  const URLS = {
    overview: () => `/api/money/overview?month=${S.month}`,
    sources: () => "/api/money/sources",
    categories: () => `/api/money/categories?period=${S.month}`,
    insights: () => `/api/money/insights?month=${S.month}`,
    pace: () => `/api/money/pace?month=${S.month}`,
    balance: () => `/api/money/balance?days=${S.horizon}`,
    cashflow: () => `/api/money/cashflow?n=12&end=${S.month}`,
    trends: () => `/api/money/trends?month=${S.month}`,
    months: () => `/api/money/months?n=12&end=${S.month}`,
    merchants: () => `/api/money/merchants?period=${S.month}&limit=12`,
    recurring: () => "/api/money/recurring",
    portfolio: () => "/api/money/portfolio?days=365&trades=50",
    meals: () => "/api/money/meals?months=12",
    savings: () => "/api/money/savings",
  };
  const VIEW_NEEDS = {
    overview: ["insights", "pace", "cashflow"], spending: ["trends", "months", "merchants"],
    cashflow: ["cashflow", "balance", "recurring"], save: ["savings"], investments: ["portfolio"], meals: ["meals"], transactions: [],
  };
  function wanted() {
    const names = ["overview", "sources", "categories", ...VIEW_NEEDS[S.tab]];
    if (S.tab === "overview" && isCurrent()) names.push("balance");
    return [...new Set(names)];
  }
  function got(name) {
    const e = S.d[name];
    return e && e.url === URLS[name]() ? e.body : null;
  }
  const D = window.DanaMoneyDash({ S, C, got, esc, icon, icons, today, isCurrent, seriesCls, acctOf, catLabel });

  async function fetchOne(name) {
    const url = URLS[name]();
    const mine = (fetchSeq[name] = (fetchSeq[name] || 0) + 1);
    const r = await request("GET", url);
    if (fetchSeq[name] !== mine) return null;  // a newer fetch of it is on its way
    if (r.status === 200) {
      S.d[name] = { url, body: r.body };
      S.stale.delete(name);
      if (name === "sources") S.sources = r.body.sources;
      if (name === "categories") S.taxonomy = r.body.taxonomy;
    }
    return r;
  }

  async function loadAll() {
    const ch = await request("GET", "/api/money/changes");
    if (ch.status === 200) S.cursor = ch.body.cursor;
    S.stale = new Set(Object.keys(S.d));
    await Promise.all([loadPage(), S.tab === "transactions" ? loadTxns() : null]);
  }

  /** What the view on screen needs and doesn't have (or has stale): fetched together, then one render.
   *  The screen keeps what it shows meanwhile. */
  async function loadPage() {
    const names = wanted().filter((n) => !got(n) || S.stale.has(n));
    if (!names.length) { render(); return; }
    const results = await Promise.all(names.map(async (n) => [n, await fetchOne(n)]));
    if (results.some(([, r]) => r && r.status === 403 && r.body && r.body.error === "not_connected")) { setEnabled(false); return; }
    const ov = results.find(([n]) => n === "overview");
    if (ov && ov[1] && ov[1].status !== 200) {
      S.error = C.errorText(ov[1].status, ov[1].body);
      if (!S.loaded) { render(); return; }
      toast(S.error);
    } else if (ov && ov[1]) S.error = "";
    if (got("overview")) S.loaded = true;
    render();
    schedulePoll();
  }

  async function loadTxns({ more = false, keep = false } = {}) {
    const mine = ++txSeq;
    S.txDirty = false;
    const limit = keep ? Math.min(200, Math.max(PAGE, S.txns.length)) : PAGE;
    const query = C.filtersToQuery(S.f, S.month, { limit, cursor: more ? S.next : "" });
    S.txLoading = true;
    if (!more && !keep) { S.txns = []; S.next = null; S.txLoaded = false; renderTxns(); }
    else if (more) renderMore();
    const r = await request("GET", `/api/money/transactions?${query}`);
    if (mine !== txSeq) return;
    S.txLoading = false;
    if (r.status === 200) {
      S.txns = more ? C.mergeTxns(S.txns, r.body.transactions) : r.body.transactions;
      S.next = r.body.next_cursor;
      S.txError = "";
      S.txLoaded = true;
    } else S.txError = C.errorText(r.status, r.body);
    renderTxns();
  }

  async function loadDetail(id) {
    const mine = ++detailSeq;
    const r = await request("GET", `/api/money/transactions/${encodeURIComponent(id)}`);
    if (mine !== detailSeq || S.openId !== id) return;
    if (r.status === 200) { S.detail = r.body; S.detailError = ""; }
    else if (r.status === 404) { closeDetail(); toast("That transaction isn't there any more."); return; }
    else S.detailError = C.errorText(r.status, r.body);
    renderDetail();
  }

  async function loadSources() {
    const r = await fetchOne("sources");
    if (r && r.status === 200) render();
  }

  async function loadCompanies() {
    if (S.companies) return S.companies;
    const r = await request("GET", "/api/money/companies");
    if (r.status === 200) S.companies = r.body.companies;
    return S.companies;
  }

  /** What changed elsewhere (a sync, the phone, Dana): refetch what is on screen, keeping its place. */
  function refresh() {
    S.stale = new Set(Object.keys(S.d));
    loadPage();
    if (S.tab === "transactions") loadTxns({ keep: true });
    else S.txDirty = true;
    if (S.openId) loadDetail(S.openId);
  }

  // ---------- live ----------

  const anyBusy = () => (S.sources || []).some(C.busy) || Object.keys(S.syncBusy).length > 0;
  function schedulePoll() {
    clearTimeout(pollTimer);
    if (!S.visible || S.enabled !== true) return;
    const fast = anyBusy() || Date.now() < S.fastUntil;
    pollTimer = setTimeout(poll, fast ? FAST_MS : POLL_MS);
  }
  async function poll() {
    if (!S.visible || S.enabled !== true) return;
    if (document.hidden) { schedulePoll(); return; }
    const r = await request("GET", `/api/money/changes${S.cursor ? `?since=${encodeURIComponent(S.cursor)}` : ""}`);
    if (r.status === 200) {
      const changed = r.body.changed && S.cursor !== null;
      S.cursor = r.body.cursor;
      if (changed) { S.syncBusy = {}; refresh(); }
      else renderTop();  // "Synced 2 h ago" moves on
    }
    schedulePoll();
  }
  const goFast = () => { S.fastUntil = Date.now() + FAST_FOR_MS; schedulePoll(); };

  /** Once a second while a code request is open: the countdowns (only their text, so a typed code stays). */
  function tick() {
    document.querySelectorAll("#panel-money [data-countdown]").forEach((n) => {
      const left = C.countdown(n.dataset.countdown, Date.now());
      n.textContent = left;
      if (left === "0:00" && !n.dataset.over) { n.dataset.over = "1"; setTimeout(loadSources, 1500); }
    });
  }

  // ---------- state ----------

  function syncHash() {
    if (!S.visible) return;
    const h = C.stateToHash(S, thisMonth());
    if (location.hash !== h) history.replaceState(history.state, "", location.pathname + location.search + h);
  }
  function setMonth(m) {
    if (!C.isMonth(m) || m > thisMonth() || m === S.month) return;
    S.month = m;
    if (S.sheet && S.sheet.type === "detail") S.sheet = null;
    S.openId = null; S.detail = null;
    S.expanded.clear(); S.openBills.clear();
    S.txns = []; S.next = null; S.txLoaded = false; S.txDirty = true;
    syncHash();
    render();
    loadPage();
    if (S.tab === "transactions") loadTxns();
  }
  function setFilter(patch) {
    S.f = C.cleanFilters({ ...S.f, ...patch });
    syncHash();
    if (S.tab === "transactions") { renderTxTools(); loadTxns(); }
    else { S.txDirty = true; renderView(); }
  }
  /** One of the views (C.TABS). `anchor`: an element id to scroll to once it is drawn. */
  function setTab(tab, { anchor = null } = {}) {
    if (!C.TABS.includes(tab)) return;
    const same = tab === S.tab;
    S.tab = tab;
    syncHash();
    if (!same && !wide() && S.sheet && S.sheet.type === "detail") { S.sheet = null; S.openId = null; renderSheet(); }
    render();
    const sc = $("mny-scroll");
    if (anchor) setTimeout(() => $(anchor)?.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
    else if (sc && !same) sc.scrollTop = 0;
    loadPage();
    if (tab === "transactions" && (S.txDirty || !S.txLoaded)) loadTxns();
  }
  function setView(v) {
    S.view = v;
    syncHash();
    render();
    const sc = $("mny-scroll");
    if (sc) sc.scrollTop = 0;
    if (v === "sources") loadSources();
    else loadPage();
  }
  function setHorizon(days) {
    if (S.horizon === days) return;
    S.horizon = days;
    renderView();
    loadPage();
  }
  /** An insight's link: a view, with a category to focus Spending on or a transaction to open. */
  function followLink(view, category, id) {
    if (category) S.f = C.cleanFilters({ ...S.f, category, all: false });
    if (id) { openTxn(id); return; }
    setTab(view || "overview");
  }
  /** A transaction from a dashboard view: the Transactions view's pane on a wide screen, a sheet in place on a narrow one. */
  function openTxn(id) {
    if (wide()) { if (S.tab !== "transactions") setTab("transactions"); }
    openDetail(id);
  }
  function setEnabled(on) {
    S.enabled = on;
    document.querySelectorAll("[data-money-nav]").forEach((b) => b.classList.toggle("hidden", !on));
    render();
  }

  // ---------- render: frame ----------

  function render() {
    if (!$("mny-root")) return;
    renderTop();
    const root = $("mny-root");
    if (S.enabled === false) {
      root.innerHTML = `<div class="mny-off mny-card"><span class="mny-off-icon">${icon("wallet")}</span><h2>Money is off</h2>
        <p>Turn on Money in Addons to bring in your bank and card transactions.</p>
        <button class="btn-primary" data-act="addons">${icon("plug")}<span>Open Addons</span></button></div>`;
      icons();
      return;
    }
    if (S.error && !S.loaded) {
      root.innerHTML = `<div class="mny-off mny-card"><h2>Couldn't load Money</h2><p>${esc(S.error)}</p>
        <button class="btn-secondary" data-act="retry">${icon("refresh-cw")}<span>Try again</span></button></div>`;
      icons();
      return;
    }
    ensureFrame();
    root.classList.toggle("view-sources", S.view === "sources");
    root.dataset.tab = S.tab;
    $("mny-page").classList.toggle("hidden", S.view !== "page");
    $("mny-sources").classList.toggle("hidden", S.view !== "sources");
    renderBanners();
    if (S.view === "sources") renderSources();
    else renderPageBody();
    // A background refresh redraws only the detail sheet: a form or picker keeps what is typed and its scroll.
    if (S.sheet && S.sheet.type === "detail") renderSheet();
    icons();
  }

  function ensureFrame() {
    const root = $("mny-root");
    if ($("mny-page")) return;
    root.innerHTML = `
      <div id="mny-banners" class="mny-banners"></div>
      <div id="mny-page">
        <div id="mny-welcome"></div>
        <div id="mny-body">
          <nav class="mny-tabs" id="mny-tabs" role="tablist" aria-label="Money views">${C.TABS.map((t) =>
            `<button role="tab" data-tab-go="${t}" id="mny-tab-${t}" aria-controls="mny-view-${t}">${esc(C.TAB_LABELS[t])}</button>`).join("")}</nav>
          <div class="mny-view" id="mny-view-overview" role="tabpanel" aria-labelledby="mny-tab-overview"></div>
          <div class="mny-view" id="mny-view-spending" role="tabpanel" aria-labelledby="mny-tab-spending"></div>
          <div class="mny-view" id="mny-view-cashflow" role="tabpanel" aria-labelledby="mny-tab-cashflow"></div>
          <div class="mny-view" id="mny-view-save" role="tabpanel" aria-labelledby="mny-tab-save"></div>
          <div class="mny-view" id="mny-view-investments" role="tabpanel" aria-labelledby="mny-tab-investments"></div>
          <div class="mny-view" id="mny-view-meals" role="tabpanel" aria-labelledby="mny-tab-meals"></div>
          <div class="mny-view" id="mny-view-transactions" role="tabpanel" aria-labelledby="mny-tab-transactions">
            <div class="mny-tx">
              <section class="mny-card mny-tx-list" aria-label="Transactions"><div id="mny-txtools"></div><div id="mny-txns"></div></section>
              <aside class="mny-tx-detail" id="mny-detail" aria-label="Transaction details"></aside>
            </div>
          </div>
        </div>
      </div>
      <div id="mny-sources" class="hidden"></div>`;
  }

  function renderTop() {
    const back = $("mny-back"), title = $("mny-title"), month = $("mny-month"), status = $("mny-status"), add = $("mny-add-top"), src = $("mny-sources-btn");
    if (!title) return;
    const sources = S.view === "sources";
    back.classList.toggle("hidden", !sources);
    title.textContent = sources ? "Banks & cards" : "Money";
    add.classList.toggle("hidden", !sources || S.enabled !== true);
    src.classList.toggle("hidden", sources || S.enabled !== true);
    month.classList.toggle("hidden", sources || S.enabled !== true || !C.usesMonth(S.tab));
    const attention = (S.sources || []).some((s) => ["needs_attention", "needs_code", "failing"].includes(s.state));
    src.querySelector(".mny-dot")?.classList.toggle("hidden", !attention);
    if (S.enabled === true && !sources) {
      const m = S.month;
      const next = C.shiftMonth(m, 1);
      month.innerHTML = `<button class="mny-mbtn" data-month="${C.shiftMonth(m, -1)}" aria-label="Previous month" title="${esc(C.monthLabel(C.shiftMonth(m, -1)))}">${icon("chevron-left")}</button>
        <button class="mny-mlabel${isCurrent() ? "" : " past"}" data-act="this-month" title="${isCurrent() ? "This month" : "Back to this month"}">
          <span class="long">${esc(C.monthLabel(m))}</span><span class="short">${esc(C.monthLabel(m, { short: true }))}</span></button>
        <button class="mny-mbtn" data-month="${next}" aria-label="Next month" ${next > thisMonth() ? "disabled" : ""} title="${esc(C.monthLabel(next))}">${icon("chevron-right")}</button>`;
    }
    const hs = C.headerStatus(S.sources, Date.now());
    status.innerHTML = S.enabled === true && hs.text
      ? `<span class="mny-state tone-${hs.tone}">${hs.tone === "busy" ? `<span class="mny-spin"></span>` : `<span class="mny-sdot"></span>`}<span class="mny-state-t">${esc(hs.text)}</span></span>` : "";
  }

  function renderPageBody() {
    const ov = got("overview");
    const welcome = $("mny-welcome"), body = $("mny-body");
    const sources = S.sources || [];
    const cf = got("cashflow");
    const anything = (ov && ((ov.spent && ov.spent.count) || (ov.income && ov.income.count))) || (cf && cf.months.some((m) => m.income || m.spent)) || S.txns.length;
    if (S.loaded && ov && !sources.length && !anything) {
      welcome.innerHTML = welcomeHtml();
      body.classList.add("hidden");
      return;
    }
    const firstSync = S.loaded && sources.length && sources.every((s) => !s.last_ok_at) && !anything;
    welcome.innerHTML = firstSync ? firstSyncHtml() : "";
    body.classList.toggle("hidden", Boolean(firstSync));
    if (firstSync) return;
    renderTabs();
    renderView();
  }

  function renderTabs() {
    const shown = C.visibleTabs({ investments: hasInvestments(), meals: hasMeals() }, S.tab);
    C.TABS.forEach((t) => {
      const on = t === S.tab;
      const b = $(`mny-tab-${t}`);
      if (b) { b.classList.toggle("on", on); b.classList.toggle("hidden", !shown.includes(t)); b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; }
      $(`mny-view-${t}`)?.classList.toggle("hidden", !on);
    });
    // A narrow screen scrolls the strip of views: keep the one on screen in sight.
    const nav = $("mny-tabs"), cur = $(`mny-tab-${S.tab}`);
    if (nav && cur && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = Math.max(0, cur.offsetLeft - (nav.clientWidth - cur.offsetWidth) / 2);
  }

  /** The view on screen, from what is loaded (skeletons for the rest). */
  function renderView() {
    if (!$("mny-body") || S.view !== "page") return;
    const el = $(`mny-view-${S.tab}`);
    if (S.tab === "overview") D.overview(el);
    else if (S.tab === "spending") D.spending(el);
    else if (S.tab === "cashflow") D.cashflow(el);
    else if (S.tab === "investments") D.investments(el);
    else if (S.tab === "meals") D.meals(el);
    else if (S.tab === "save") { keepFocus(() => D.save(el)); watchChecks(); }
    else { renderTxTools(); renderTxns(); renderDetail(); }
    icons();
  }

  function welcomeHtml() {
    return `<div class="mny-welcome mny-card">
      <span class="mny-off-icon">${icon("wallet")}</span>
      <h2>Bring in your bank and cards</h2>
      <p>Dana signs in for you twice a day, reads the transactions, sorts them into categories and keeps an eye out for
         anything odd. Your login details are stored encrypted and are never shown again.</p>
      <div class="mny-welcome-btns"><button class="btn-primary" data-act="add-source">${icon("plus")}<span>Add a bank or card</span></button></div>
    </div>`;
  }
  function firstSyncHtml() {
    const rows = (S.sources || []).map((s) => {
      const st = C.sourceStatus(s, Date.now());
      return `<li><span class="mny-state tone-${st.tone}">${st.tone === "busy" ? `<span class="mny-spin"></span>` : `<span class="mny-sdot"></span>`}<strong>${esc(s.company_name)}</strong>&nbsp;· ${esc(st.label)}</span>
        ${st.detail && !/first sync reads/.test(st.detail) ? `<div class="mny-muted">${esc(st.detail)}</div>` : ""}</li>`;
    }).join("");
    return `<div class="mny-welcome mny-card">
      <span class="mny-off-icon">${icon("loader")}</span>
      <h2>Getting your transactions</h2>
      <p>The first sync reads a year back and takes a few minutes. This page fills in by itself.</p>
      <ul class="mny-firstsync">${rows}</ul>
      <div class="mny-welcome-btns"><button class="btn-secondary" data-act="view-sources">${icon("landmark")}<span>Banks &amp; cards</span></button></div>
    </div>`;
  }

  // ---------- banners ----------

  function renderBanners() {
    const el = $("mny-banners");
    if (!el) return;
    const out = [];
    const sources = S.sources || [];
    sources.filter((s) => s.code_request).forEach((s) => {
      const req = s.code_request, id = req.id;
      const sent = S.codeSent[id];
      out.push(`<div class="mny-banner code" data-banner="code-${esc(id)}">
        <span class="mny-banner-icon">${icon("message-square-lock")}</span>
        <div class="mny-banner-body">
          <strong>${esc(s.company_name)} sent you a sign-in code</strong>
          <span>${sent ? "Thanks — signing in…" : "The phone usually hands it over by itself. If it doesn't, type it here."}
            ${sent ? "" : `<span class="mny-countdown" title="Time left">${icon("timer")}<span data-countdown="${esc(req.expires_at)}">${C.countdown(req.expires_at, Date.now())}</span></span>`}</span>
          ${sent ? "" : `<form class="mny-code" data-code-form="${esc(id)}">
            <input class="form-control" name="code" data-keep="code-${esc(id)}" data-code-input="${esc(id)}" inputmode="numeric" autocomplete="one-time-code"
                   pattern="[0-9]{4,8}" maxlength="8" placeholder="Code" aria-label="${esc(s.company_name)} sign-in code" value="${esc(S.codeDrafts[id] || "")}">
            <button class="btn-primary" type="submit" ${S.codeBusy[id] ? "disabled" : ""}>${S.codeBusy[id] ? "Sending…" : "Send"}</button></form>
            ${S.codeError[id] ? `<span class="mny-err" role="alert">${esc(S.codeError[id])}</span>` : ""}`}
        </div></div>`);
    });
    const coded = new Set(sources.filter((s) => s.code_request).map((s) => s.id));
    const lov = lastOverview();
    const attention = (lov && lov.attention) || sources.filter((s) => ["needs_attention", "failing", "needs_code"].includes(s.state))
      .map((s) => ({ source_id: s.id, name: s.company_name, state: s.state, reason: s.state_reason }));
    attention.filter((a) => !coded.has(a.source_id)).forEach((a) => {
      const bad = a.state === "needs_attention";
      const src = sources.find((s) => s.id === a.source_id);
      const st = src ? src.state : a.state;
      if (st === "syncing" || st === "ok") return;
      const syncBtn = `<button class="btn-secondary" data-sync="${esc(a.source_id)}" ${S.syncBusy[a.source_id] ? "disabled" : ""}>${icon("refresh-cw")}<span>Sync now</span></button>`;
      const editBtn = `<button class="btn-secondary" data-edit-source="${esc(a.source_id)}">Update login</button>`;
      const byHand = bad && Boolean(src && src.can_sync);
      out.push(`<div class="mny-banner ${bad ? "bad" : "warn"}">
        <span class="mny-banner-icon">${icon(bad ? "key-round" : "triangle-alert")}</span>
        <div class="mny-banner-body"><strong>${esc(a.name)}: ${byHand ? "Dana stopped signing in" : bad ? "the login needs fixing" : st === "needs_code" ? "waiting for a sign-in code" : "the last sync failed"}</strong>
          <span>${esc(a.reason || "")}</span></div>
        <div class="mny-banner-act">${byHand ? syncBtn + editBtn : bad ? editBtn : syncBtn}</div>
      </div>`);
    });
    keepFocus(() => { el.innerHTML = out.join(""); });
    clearInterval(tickTimer);
    if (sources.some((s) => s.code_request)) tickTimer = setInterval(tick, 1000);
  }

  // ---------- transactions ----------

  function renderTxTools() {
    const el = $("mny-txtools");
    if (!el) return;
    const f = S.f;
    const chip = (key, label, value, iconName) => value
      ? `<span class="mny-fchip on"><button data-open="${key}">${icon(iconName)}<span>${esc(value)}</span></button><button class="x" data-clear="${key}" aria-label="Clear ${esc(label)}">${icon("x")}</button></span>`
      : `<span class="mny-fchip"><button data-open="${key}">${icon(iconName)}<span>${esc(label)}</span>${icon("chevron-down", "mny-dd")}</button></span>`;
    const chips = C.filterChips(f, { categoryLabel: catLabel, accountLabel: (id) => C.accountName(acctOf(id)) });
    const val = (k) => (chips.find((c) => c.key === k) || {}).label || "";
    const monthName = C.monthLabel(S.month, { short: true });
    keepFocus(() => {
      el.innerHTML = `<div class="mny-tx-head">
        <div class="mny-card-head"><span>Transactions</span>${C.hasFilters(f) ? `<button class="mny-link" data-act="clear-filters">Clear filters</button>` : ""}</div>
        <div class="mny-search">${icon("search")}<input type="search" id="mny-q" data-keep="mny-q" value="${esc(f.q)}" autocomplete="off" dir="auto"
          placeholder="Search merchant, memo, note or amount" aria-label="${f.all ? "Search every transaction" : `Search ${esc(C.monthLabel(S.month))}`}"><kbd>/</kbd></div>
        <div class="mny-fchips">
          <span class="mny-seg" role="radiogroup" aria-label="Which months">
            <button role="radio" aria-checked="${!f.all}" class="${f.all ? "" : "on"}" data-scope="month">${esc(monthName)}</button>
            <button role="radio" aria-checked="${f.all}" class="${f.all ? "on" : ""}" data-scope="all">All time</button></span>
          ${f.category === "uncategorized" ? "" : chip("category", "Category", val("category"), "tag")}
          ${chip("account", "Account", val("account"), "credit-card")}
          ${chip("amount", "Amount", val("amount"), "arrow-up-down")}
          <button class="mny-fchip-toggle${f.category === "uncategorized" ? " on" : ""}" data-act="toggle-uncat" aria-pressed="${f.category === "uncategorized"}">${icon("circle-help")}<span>Only uncategorized</span></button>
        </div></div>`;
    });
    icons();
  }

  function rowHtml(t) {
    const [ic, cls] = rowIcon(t);
    const flow = C.flowChip(t);
    const tone = C.amountTone(t);
    const kb = C.kindBadge(t);
    const badges = C.badges(t, companyOf(t)).map((b) => `<span class="mny-badge b-${b.type}" title="${esc(b.title)}">${b.type === "provisional" ? icon("zap") : ""}${esc(b.text)}</span>`).join("")
      + (kb ? `<span class="mny-badge b-${kb.type}" title="${esc(kb.title)}">${esc(kb.text)}</span>` : "");
    const time = C.timeOf(t.occurred_at);
    const catBtn = flow
      ? `<span class="mny-catchip flow" title="${esc(C.statusExplain(t, companyOf(t)))}">${esc(flow)}</span>`
      : `<button class="mny-catchip${t.category ? "" : " none"}" data-cat-for="${esc(t.id)}" title="${esc(t.category ? `${t.category_label}: change the category` : "Pick a category")}">${t.group ? `<span class="mny-sw ${seriesCls(t.group)}"></span>` : ""}<span class="mny-catchip-t">${esc(t.category ? t.category_label : "Uncategorized")}</span></button>`;
    return `<div class="mny-row${S.openId === t.id ? " open" : ""}${t.status === "unmatched" ? " unmatched" : ""}" role="button" tabindex="0" data-open-txn="${esc(t.id)}">
      <span class="mny-row-icon ${cls}">${icon(ic)}</span>
      <span class="mny-row-main">
        <span class="mny-row-top"><bdi dir="auto" class="mny-merchant">${esc(t.merchant || t.description || "—")}</bdi>${badges}</span>
        <span class="mny-row-sub">${catBtn}<span class="mny-row-meta">${esc(acctName(t))}${time ? ` · ${time}` : ""}${t.note ? ` · ${icon("sticky-note", "mny-note-i")}` : ""}</span></span>
      </span>
      <span class="mny-row-amt ${tone}">${C.moneyHtml(t.amount, { sign: tone === "in" ? "plus" : "minus" })}</span>
    </div>`;
  }

  function renderTxns() {
    const el = $("mny-txns");
    if (!el) return;
    if (!S.txLoaded && !S.txError) { el.innerHTML = `<div class="mny-day-head skel"></div>` + `<div class="mny-row-skel tall"></div>`.repeat(6); return; }
    if (S.txError && !S.txns.length) { el.innerHTML = `<div class="mny-empty">${esc(S.txError)} <button class="mny-link" data-act="retry-tx">Try again</button></div>`; return; }
    const shown = S.f.category === "uncategorized" ? S.txns.filter(C.needsCategory) : S.txns;
    if (!shown.length && !S.next) {
      const where = S.f.all ? "" : ` in ${C.monthLabel(S.month)}`;
      el.innerHTML = `<div class="mny-empty big">${icon(C.hasFilters(S.f) ? "search-x" : "inbox")}<span>${C.hasFilters(S.f) ? `Nothing matches${esc(where)}.` : `Nothing${esc(where)} yet.`}</span>
        ${C.hasFilters(S.f) ? `<button class="mny-link" data-act="clear-filters">Clear filters</button>` : ""}${!S.f.all && C.hasFilters(S.f) ? `<button class="mny-link" data-scope="all">Search all time</button>` : ""}</div>`;
      icons();
      return;
    }
    const days = C.groupByDay(shown);
    el.innerHTML = days.map((d) => `<div class="mny-day">
        <div class="mny-day-head"><span title="${esc(C.longDate(d.date))}">${esc(C.dayLabel(d.date, today()))}</span>
          <span class="mny-day-sum">${d.income ? `<span class="in" title="Money in">${C.moneyHtml(d.income, { sign: "plus" })}</span>` : ""}${d.spent > 0 ? `<span title="Spent">${C.moneyHtml(d.spent, { sign: "none" })}</span>`
            : d.spent < 0 ? `<span class="in" title="Refunds, more than was spent">${C.moneyHtml(-d.spent, { sign: "plus" })}</span>` : ""}</span></div>
        ${d.items.map(rowHtml).join("")}</div>`).join("") + `<div id="mny-more-slot">${moreHtml()}</div>`;
    icons();
    observeMore();
  }
  function moreHtml() {
    if (S.next) return `<button class="chip mny-more-chip" data-more ${S.txLoading ? "disabled" : ""}>${S.txLoading ? "Loading…" : "Show more"}</button>`;
    return S.txns.length > 8 ? `<div class="mny-end">That's everything${S.f.all ? "" : ` in ${esc(C.monthLabel(S.month))}`}.</div>` : "";
  }
  function renderMore() { const slot = $("mny-more-slot"); if (slot) slot.innerHTML = moreHtml(); }
  let moreObserver = null;
  function observeMore() {
    if (!("IntersectionObserver" in window)) return;
    moreObserver?.disconnect();
    const btn = document.querySelector("#mny-more-slot [data-more]");
    if (!btn) return;
    moreObserver = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && S.next && !S.txLoading) loadTxns({ more: true });
    }, { root: $("mny-scroll"), rootMargin: "300px" });
    moreObserver.observe(btn);
  }

  // ---------- detail ----------

  function openDetail(id) {
    S.openId = id;
    S.detail = null;
    S.detailError = "";
    document.querySelectorAll("#mny-txns .mny-row.open").forEach((n) => n.classList.remove("open"));
    document.querySelector(`#mny-txns .mny-row[data-open-txn="${CSS.escape(id)}"]`)?.classList.add("open");
    if (!wide()) S.sheet = { type: "detail" };
    renderDetail();
    renderSheet();
    loadDetail(id);
  }
  function closeDetail() {
    S.openId = null; S.detail = null;
    document.querySelectorAll("#mny-txns .mny-row.open").forEach((n) => n.classList.remove("open"));
    if (S.sheet && S.sheet.type === "detail") S.sheet = null;
    renderDetail();
    renderSheet();
  }
  const currentTxn = () => (S.detail && S.detail.id === S.openId ? S.detail : S.txns.find((t) => t.id === S.openId) || null);

  function detailHtml(inSheet) {
    const t = currentTxn();
    if (!t) return `<div class="mny-detail-empty">${S.detailError ? esc(S.detailError) : "Loading…"}</div>`;
    const full = S.detail && S.detail.id === t.id ? S.detail : null;
    const tone = C.amountTone(t);
    const company = companyOf(t);
    const explain = C.statusExplain(t, company);
    const flow = C.flowChip(t);
    const time = C.timeOf(t.occurred_at);
    const facts = [];
    if (t.description && t.description !== t.merchant) facts.push(["As the bank wrote it", `<bdi dir="auto">${esc(t.description)}</bdi>`]);
    else if (full) facts.push(["Description", `<bdi dir="auto">${esc(t.description || "—")}</bdi>`]);
    if (t.memo) facts.push(["Memo", `<bdi dir="auto">${esc(t.memo)}</bdi>`]);
    facts.push(["Bought", `${esc(C.longDate(t.purchase_date))}${time ? `, ${time}` : ""}`]);
    if (t.charge_date && t.charge_date !== t.purchase_date) facts.push(["Charged", esc(C.longDate(t.charge_date))]);
    facts.push(["Account", esc(acctName(t))]);
    if (full && full.installment) {
      const i = full.installment;
      facts.push(["Installments", `Payment ${i.number} of ${i.total}${i.purchase_amount ? ` · ${C.moneyHtml(i.purchase_amount, { sign: "none" })} in all` : ""}`]);
    }
    if (full && full.foreign && full.foreign.currency !== "ILS") facts.push(["Paid in", `<bdi dir="ltr">${esc(C.foreignText(full.foreign.amount, full.foreign.currency))}</bdi> → ${C.moneyHtml(t.amount, { sign: "none" })}`]);
    if (full && full.bank_category) facts.push([`${company || "Card"}'s category`, `<bdi dir="auto">${esc(full.bank_category)}</bdi>`]);
    if (full && full.same_merchant && full.same_merchant.count > 1) {
      const sm = full.same_merchant;
      facts.push(["History", `${sm.count} charges, ${C.moneyHtml(Math.abs(sm.total), { sign: "none" })} since ${esc(C.shortDate(sm.first_date, today()))}
        <button class="mny-link" data-merchant="${esc(t.merchant)}">See them</button>`]);
    } else if (full && full.same_merchant && full.same_merchant.count === 1) facts.push(["History", "The first charge from this merchant"]);
    if (full && full.meal_cycle) {
      const mc = full.meal_cycle;
      facts.push(["Meal card", `What the budget did not cover, ${esc(C.cycleLabel(mc, today()))}: ${mc.meals.length} ${mc.meals.length === 1 ? "meal" : "meals"}
        (${mc.meals.slice(0, 3).map((x) => `<bdi dir="auto">${esc(x.place || "—")}</bdi>`).join(", ")}${mc.meals.length > 3 ? ", …" : ""})
        <button class="mny-link" data-tab-go="meals">See the meal card</button>`]);
    }
    const draft = S.noteDraft[t.id];
    const note = draft !== undefined ? draft : t.note || "";
    const dirty = draft !== undefined && draft !== (t.note || "");
    return `<div class="mny-detail">
      <div class="mny-d-top">
        <span class="mny-row-icon big ${rowIcon(t)[1]}">${icon(rowIcon(t)[0])}</span>
        <button class="icon-btn mny-d-close" data-act="close-detail" aria-label="Close" title="Close (Esc)">${icon("x")}</button>
      </div>
      <h3 class="mny-d-merchant"><bdi dir="auto">${esc(t.merchant || t.description || "—")}</bdi></h3>
      <div class="mny-d-amount ${tone}">${C.moneyHtml(t.amount, { sign: tone === "in" ? "plus" : "minus" })}</div>
      <div class="mny-d-when">${esc(C.longDate(t.purchase_date))}${time ? ` · ${time}` : ""} · ${esc(acctName(t))}</div>
      <div class="mny-d-badges">${C.badges(t, company).map((b) => `<span class="mny-badge b-${b.type}" title="${esc(b.title)}">${b.type === "provisional" ? icon("zap") : ""}${esc(b.text)}</span>`).join("")}</div>
      ${explain ? `<div class="mny-d-explain">${icon("info")}<span>${esc(explain)}</span></div>` : ""}
      <div class="mny-d-section"><div class="mny-d-label">Category</div>
        <div class="mny-d-cat">${flow ? `<span class="mny-catchip flow big">${esc(flow)}</span>`
          : `<button class="mny-catchip big${t.category ? "" : " none"}" data-cat-for="${esc(t.id)}">${t.group ? `<span class="mny-sw ${seriesCls(t.group)}"></span>` : ""}${esc(t.category ? t.category_label : "Pick a category")}${icon("pencil", "mny-edit-i")}</button>`}
          ${t.category_source ? `<span class="mny-muted">${esc(C.categorySourceLabel(t.category_source))}</span>` : ""}</div></div>
      ${full ? countsHtml(full) : ""}
      <dl class="mny-facts">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>
      <div class="mny-d-section"><label class="mny-d-label" for="mny-note-${esc(t.id)}">Note</label>
        <textarea class="form-control mny-note" id="mny-note-${esc(t.id)}" data-keep="note-${inSheet ? "s" : "p"}-${esc(t.id)}" data-note-for="${esc(t.id)}" rows="2" dir="auto"
          placeholder="Add a note — who it was with, what it was for">${esc(note)}</textarea>
        <div class="mny-note-btns${dirty ? "" : " hidden"}"><button class="btn-primary" data-act="save-note" ${S.noteBusy ? "disabled" : ""}>${S.noteBusy ? "Saving…" : "Save note"}</button>
          <button class="btn-secondary" data-act="cancel-note">Cancel</button></div></div>
      <div class="mny-d-actions"><button class="btn-secondary" data-act="ask">${icon("sparkles")}<span>Ask Dana</span></button></div>
    </div>`;
  }

  /** Counts as (the flow), One-off and Yearly (yes / no / Dana decides): each a PATCH with Undo. A card
   *  bill's bank line keeps its flow (the card's rows are the spending); the marks only for spending. */
  function countsHtml(t) {
    const flowCtl = t.flow === "card_bill" ? "" : `<div class="mny-d-row"><label class="mny-d-label" for="mny-flow-${esc(t.id)}">Counts as</label>
        <select class="form-control mny-flow" id="mny-flow-${esc(t.id)}" data-flow-for="${esc(t.id)}">
          ${C.FLOW_LABELS[t.flow] ? "" : `<option value="" selected disabled>Not set</option>`}
          ${C.flowChoices(t).map((o) => `<option value="${esc(o.flow)}"${o.flow === t.flow ? " selected" : ""}>${esc(o.label)}</option>`).join("")}
        </select></div>`;
    const tri = (mark, label) => {
      const st = C.markState(t[mark]);
      const btn = (v, text) => `<button role="radio" aria-checked="${st === v}" class="${st === v ? "on" : ""}" data-mark="${mark}" data-mark-value="${v}" data-mark-for="${esc(t.id)}">${text}</button>`;
      return `<div class="mny-d-row"><span class="mny-d-label" id="mny-${mark}-l">${label}</span>
        <span class="mny-seg mny-tri" role="radiogroup" aria-labelledby="mny-${mark}-l">${btn("yes", "Yes")}${btn("no", "No")}${btn("auto", "Automatic")}</span></div>`;
    };
    const marks = t.spending || t.kind ? `${tri("one_off", "One-off")}${tri("yearly", "Yearly")}
      <div class="mny-hint">${esc(C.kindText(t.kind))} One-offs are left out of everyday spending and the forecast; yearly charges count a twelfth a month in fixed costs.</div>` : "";
    if (!flowCtl && !marks) return "";
    return `<div class="mny-d-section mny-counts">${flowCtl}${marks}</div>`;
  }

  function renderDetail() {
    const el = $("mny-detail");
    if (!el) return;
    if (wide()) {
      if (S.openId) keepFocus(() => { el.innerHTML = detailHtml(false); });
      else el.innerHTML = `<div class="mny-detail-idle"><div class="mny-detail-hint">${icon("mouse-pointer-click")}<span>Pick a transaction to see everything about it, change its category or add a note.</span></div></div>`;
    } else el.innerHTML = "";
    if (S.sheet && S.sheet.type === "detail") renderSheet();
    icons();
  }

  // ---------- sources ----------

  function renderSources() {
    const el = $("mny-sources");
    const list = S.sources;
    if (!list) { el.innerHTML = `<div class="mny-card mny-skel" style="height:180px"></div>`; return; }
    const now = Date.now();
    const cards = list.map((s) => {
      const st = C.sourceStatus(s, now);
      const accts = (s.accounts || []).map((a) => {
        const name = C.accountName({ ...a, company_name: s.company_name });
        const right = a.kind === "meal"
          ? (a.balance != null ? `${C.moneyHtml(a.balance, { cents: false })}<em>left, as of ${esc(C.when(a.balance_at, now))}</em>` : "")
          : a.kind === "bank" || a.kind === "investment"
          ? (a.balance != null ? `${C.moneyHtml(a.balance, a.kind === "investment" ? { cents: false } : {})}<em>${a.kind === "investment" ? "worth, " : ""}as of ${esc(C.when(a.balance_at, now))}</em>` : "")
          : (a.next_charge ? `${C.moneyHtml(a.next_charge, { sign: "none" })}<em>bill on ${esc(C.shortDate(a.next_charge_date, today()))}</em>` : `<em>no bill coming</em>`);
        return `<li><span class="mny-acct-name">${icon(C.kindIcon(a.kind))}<span>${esc(name)}</span></span><span class="mny-acct-num">${right}</span></li>`;
      }).join("");
      const times = [s.last_ok_at ? `Last good sync ${esc(C.when(s.last_ok_at, now))}` : "Never synced yet",
                     s.next_sync_at && !["syncing", "needs_attention"].includes(s.state) ? `next ${esc(C.when(s.next_sync_at, now))} (${esc(C.until(s.next_sync_at, now))})` : ""].filter(Boolean).join(" · ");
      const busy = s.state === "syncing" || s.state === "new" || Boolean(S.syncBusy[s.id]);
      const noSync = busy || s.state === "needs_code" || (s.state === "needs_attention" && !s.can_sync);
      return `<section class="mny-card mny-src">
        <div class="mny-src-top"><span class="mny-src-icon">${icon(C.kindIcon(sourceKind(s)))}</span>
          <div class="mny-src-titles"><div class="mny-src-name">${esc(s.company_name)}${s.label ? ` <span class="mny-muted">· <bdi dir="auto">${esc(s.label)}</bdi></span>` : ""}</div>
            <div class="mny-state tone-${busy ? "busy" : st.tone}">${busy ? `<span class="mny-spin"></span>` : `<span class="mny-sdot"></span>`}${esc(busy && st.tone !== "busy" ? "Syncing…" : st.label)}</div></div></div>
        ${st.detail ? `<p class="mny-src-reason">${esc(st.detail)}</p>` : ""}
        <div class="mny-src-times">${times}</div>
        ${accts ? `<ul class="mny-src-accts">${accts}</ul>` : `<div class="mny-muted mny-src-none">No accounts yet — they appear after the first sync.</div>`}
        <div class="ibx-btns mny-src-btns">
          <button class="btn-secondary" data-sync="${esc(s.id)}" ${noSync ? "disabled" : ""} title="${s.state === "needs_attention" && !s.can_sync ? "Update the login first" : s.state === "needs_code" ? "Signing in: waiting for the code" : busy ? "Syncing now" : "Sync now"}">${icon("refresh-cw")}<span>Sync now</span></button>
          <button class="btn-secondary" data-edit-source="${esc(s.id)}">${icon("key-round")}<span>Update login</span></button>
          <button class="btn-ghost-danger" data-remove-source="${esc(s.id)}">Remove</button>
        </div></section>`;
    }).join("");
    el.innerHTML = `<div class="mny-src-intro"><p>Dana signs in to each of these twice a day and when your phone sees a payment. Login details are stored encrypted and never shown again.</p></div>
      ${cards || welcomeHtml()}
      ${list.length ? `<button class="mny-add-card" data-act="add-source">${icon("plus")}<span>Add a bank or card</span></button>` : ""}`;
    icons();
  }

  // ---------- sheets: pickers, forms, the phone's detail ----------

  function openSheet(sheet) { S.sheet = sheet; renderSheet(); setTimeout(() => $("mny-sheet")?.querySelector("[data-autofocus]")?.focus(), 30); }
  function closeSheet() {
    const back = S.sheet && S.sheet.back;
    S.sheet = back || null;
    if (!S.sheet && S.openId && !wide()) S.openId = null;
    renderSheet();
    if (!S.sheet) document.querySelectorAll("#mny-txns .mny-row.open").forEach((n) => { if (n.dataset.openTxn !== S.openId) n.classList.remove("open"); });
  }

  function renderSheet() {
    const sheet = $("mny-sheet"), scrim = $("mny-scrim");
    if (!sheet) return;
    const s = S.sheet;
    if (!s || !S.visible) { sheet.classList.add("hidden"); scrim.classList.add("hidden"); sheet.innerHTML = ""; return; }
    let html = "";
    if (s.type === "detail") html = detailHtml(true);
    else if (s.type === "picker" || s.type === "filter-category") html = pickerHtml(s);
    else if (s.type === "account") html = accountSheetHtml();
    else if (s.type === "amount") html = amountSheetHtml();
    else if (s.type === "companies") html = companiesHtml(s);
    else if (s.type === "source-form") html = sourceFormHtml(s);
    else if (s.type === "remove") html = removeHtml(s);
    sheet.className = `mny-sheet t-${s.type}`;
    keepFocus(() => { sheet.innerHTML = html; });
    scrim.classList.remove("hidden");
    icons();
  }

  function sheetHead(title, sub) {
    return `<div class="mny-sheet-head"><div><h3>${title}</h3>${sub ? `<div class="mny-sheet-sub">${sub}</div>` : ""}</div>
      <button class="icon-btn" data-act="sheet-close" aria-label="Close">${icon("x")}</button></div>`;
  }

  function pickerHtml(s) {
    const t = s.type === "picker" ? (S.txns.find((x) => x.id === s.txnId) || (S.detail && S.detail.id === s.txnId ? S.detail : null)) : null;
    const filter = s.type === "filter-category";
    const head = filter ? sheetHead("Show a category")
      : sheetHead("Category", t ? `<bdi dir="auto">${esc(t.merchant)}</bdi> · ${C.moneyHtml(t.amount)} · ${esc(C.shortDate(t.purchase_date, today()))}` : "");
    const scope = !filter && t ? `<div class="mny-seg wide" role="radiogroup" aria-label="Apply to">
        <button role="radio" aria-checked="${s.forMerchant}" class="${s.forMerchant ? "on" : ""}" data-pscope="merchant">Always for <bdi dir="auto">${esc(t.merchant)}</bdi></button>
        <button role="radio" aria-checked="${!s.forMerchant}" class="${s.forMerchant ? "" : "on"}" data-pscope="one">Only this one</button></div>
        <div class="mny-hint">${s.forMerchant ? "Its other charges change too (unless you set them by hand), and new ones get it as they arrive." : "Only this transaction changes."}</div>` : "";
    return `${head}${scope}
      <div class="mny-search in-sheet">${icon("search")}<input type="search" data-keep="picker-q" data-picker-q value="${esc(s.q || "")}" placeholder="Search — English or עברית" autocomplete="off" dir="auto" data-autofocus></div>
      <div class="mny-pick-list" id="mny-pick-list">${pickerListHtml(s, t)}</div>`;
  }
  function pickerListHtml(s, t) {
    const filter = s.type === "filter-category";
    const groups = C.pickerGroups(S.taxonomy, s.q, { income: t ? t.amount > 0 : false });
    const current = filter ? S.f.category : t && t.category;
    if (!S.taxonomy) return `<div class="mny-empty">Loading…</div>`;
    const out = groups.map((g) => {
      const cls = seriesCls(g.income ? "income" : g.key);
      const head = filter
        ? `<button class="mny-pick ghead${current === g.key ? " on" : ""}" data-pick="${esc(g.key)}"><span class="mny-sw ${cls}"></span><span class="mny-pick-en">${esc(g.label)}</span><span class="mny-pick-he" dir="rtl" lang="he">${esc(g.label_he)}</span><span class="mny-pick-all">all</span></button>`
        : `<div class="mny-pick-ghead"><span class="mny-sw ${cls}"></span><span>${esc(g.label)}</span><span class="mny-pick-he" dir="rtl" lang="he">${esc(g.label_he)}</span></div>`;
      return `<div class="mny-pick-group">${head}${g.categories.map((c) => `<button class="mny-pick${current === c.key ? " on" : ""}" data-pick="${esc(c.key)}">
        <span class="mny-pick-en">${esc(c.label)}</span><span class="mny-pick-he" dir="rtl" lang="he">${esc(c.label_he)}</span>${current === c.key ? icon("check", "mny-pick-ok") : ""}</button>`).join("")}</div>`;
    }).join("");
    const uncat = filter && (!s.q || "uncategorized".includes(String(s.q).toLowerCase()))
      ? `<div class="mny-pick-group"><button class="mny-pick ghead${current === "uncategorized" ? " on" : ""}" data-pick="uncategorized"><span class="mny-sw g-uncategorized"></span><span class="mny-pick-en">Uncategorized</span></button></div>` : "";
    return uncat + out || `<div class="mny-empty">No category matches “${esc(s.q)}”.</div>`;
  }

  function accountSheetHtml() {
    const list = Object.values(accounts()).filter((a) => a.kind !== "investment").sort((a, b) => (a.kind === b.kind ? 0 : a.kind === "bank" ? -1 : 1));
    return `${sheetHead("Show one account")}
      <div class="mny-pick-list">
        <button class="mny-pick${S.f.account ? "" : " on"}" data-pick-account=""><span class="mny-pick-en">Every account</span></button>
        ${list.map((a) => `<button class="mny-pick${S.f.account === a.id ? " on" : ""}" data-pick-account="${esc(a.id)}">${icon(C.kindIcon(a.kind), "mny-pick-i")}<span class="mny-pick-en">${esc(C.accountName(a))}</span>${S.f.account === a.id ? icon("check", "mny-pick-ok") : ""}</button>`).join("")}
      </div>`;
  }
  function amountSheetHtml() {
    return `${sheetHead("Amount", "Absolute shekels: money out and in alike")}
      <form class="mny-form" data-form="amount">
        <div class="ibx-row2">
          <label class="ibx-field">At least (₪)<input class="form-control" name="min" type="number" min="0" step="1" inputmode="decimal" value="${esc(S.f.min)}" data-autofocus></label>
          <label class="ibx-field">At most (₪)<input class="form-control" name="max" type="number" min="0" step="1" inputmode="decimal" value="${esc(S.f.max)}"></label>
        </div>
        <div class="mny-form-btns"><button class="btn-secondary" type="button" data-clear="amount">Clear</button><button class="btn-primary" type="submit">Show</button></div>
      </form>`;
  }

  function companiesHtml(s) {
    const list = S.companies;
    return `${sheetHead("Add a bank or card")}
      ${!list ? `<div class="mny-empty">Loading…</div>` : `<div class="mny-pick-list">${list.map((c) => `<button class="mny-pick company" data-company="${esc(c.id)}">
        ${icon(C.kindIcon(c.kind), "mny-pick-i")}<span class="mny-pick-en">${esc(c.name)}</span><span class="mny-muted">${esc(C.kindWord(c.kind))}</span>${icon("chevron-right", "mny-chev")}</button>`).join("")}</div>`}
      ${s.error ? `<div class="mny-err" role="alert">${esc(s.error)}</div>` : ""}`;
  }

  function sourceFormHtml(s) {
    const company = (S.companies || []).find((c) => c.id === s.company);
    if (!company) return `${sheetHead("Login")}<div class="mny-empty">Loading…</div>`;
    const source = s.sourceId ? (S.sources || []).find((x) => x.id === s.sourceId) : null;
    const fields = company.fields.map((f, i) => `<label class="ibx-field">${esc(f.label)}
        <input class="form-control" name="cred:${esc(f.name)}" type="${f.secret ? "password" : "text"}" autocomplete="${f.secret ? "new-password" : "off"}"
               autocapitalize="off" spellcheck="false" ${i === 0 ? "data-autofocus" : ""} ${s.sourceId ? "" : "required"} dir="ltr">
        <span class="mny-err" data-field-error="${esc(f.name)}"></span></label>`).join("");
    const title = s.sourceId ? `Update the ${esc(company.name)} login` : `Add ${esc(company.name)}`;
    return `${sheetHead(title, s.sourceId ? "Type the login again: saved details are never shown." : `The same details you use on ${esc(company.name)}'s site.`)}
      <form class="mny-form" data-form="source" autocomplete="off">
        ${fields}
        <label class="ibx-field">Name (optional)<input class="form-control" name="label" type="text" value="${esc(source ? source.label || "" : "")}" placeholder="e.g. Joint account" dir="auto"></label>
        ${company.id === "hapoalim" && !s.sourceId ? `<div class="mny-hint">${icon("message-square-lock")}<span>The bank sends an SMS code the first time Dana signs in. Your phone hands it over by itself; if not, a box for it shows up here.</span></div>` : ""}
        <div class="mny-err" role="alert" data-form-error>${esc(s.error || "")}</div>
        <div class="mny-form-btns">${s.sourceId ? "" : `<button class="btn-secondary" type="button" data-act="back-companies">Back</button>`}
          <button class="btn-primary" type="submit" data-form-submit>${s.sourceId ? "Save and sync" : "Add and sync"}</button></div>
        <p class="mny-fine">${icon("lock")}<span>Stored encrypted on your Dana server and used only to sign in for you.</span></p>
      </form>`;
  }

  function removeHtml(s) {
    const src = (S.sources || []).find((x) => x.id === s.sourceId);
    if (!src) return `${sheetHead("Remove")}<div class="mny-empty">It's already gone.</div>`;
    const n = (src.accounts || []).length;
    return `${sheetHead(`Remove ${esc(src.company_name)}?`)}
      <form class="mny-form" data-form="remove">
        <p class="mny-body-text">Dana stops signing in to ${esc(src.company_name)} and forgets the login.</p>
        <label class="mny-check"><input type="checkbox" name="transactions"><span>Also delete ${n ? `its ${n} account${n === 1 ? "" : "s"} and their transactions` : "its transactions"}</span></label>
        <div class="mny-hint">Otherwise they stay here, without new ones.</div>
        ${s.error ? `<div class="mny-err" role="alert">${esc(s.error)}</div>` : ""}
        <div class="mny-form-btns"><button class="btn-secondary" type="button" data-act="sheet-close">Cancel</button>
          <button class="btn-destructive-outline" type="submit" ${s.busy ? "disabled" : ""}>${s.busy ? "Removing…" : "Remove"}</button></div>
      </form>`;
  }

  // ---------- actions ----------

  async function setCategory(txnId, category, forMerchant) {
    const r = await request("PATCH", `/api/money/transactions/${encodeURIComponent(txnId)}`, { category, for_merchant: forMerchant });
    if (r.status !== 200) { toast(C.errorText(r.status, r.body)); return; }
    const t = r.body.transaction;
    S.txns = S.txns.map((x) => (x.id === t.id ? { ...x, ...pickItem(t) } : x));
    if (S.detail && S.detail.id === t.id) S.detail = t;
    renderTxns();
    renderDetail();
    toast(C.undoText(r.body, t.category_label, forMerchant), r.body.undo_id);
    refresh();
  }
  /** PATCH {flow} / {one_off} / {yearly}: the row and the detail take the answer, a toast offers Undo,
   *  and the views refetch (what counts as spending moved). */
  async function patchTxn(txnId, body, words) {
    const r = await request("PATCH", `/api/money/transactions/${encodeURIComponent(txnId)}`, body);
    if (r.status !== 200) { toast(C.errorText(r.status, r.body)); renderDetail(); return; }
    const t = r.body.transaction;
    S.txns = S.txns.map((x) => (x.id === t.id ? { ...x, ...pickItem(t) } : x));
    if (S.detail && S.detail.id === t.id) S.detail = t;
    if (S.tab === "transactions") renderTxns();
    renderDetail();
    toast(words(r.body), r.body.undo_id);
    refresh();
  }
  function setMark(txnId, mark, state) {
    const value = state === "yes" ? true : state === "no" ? false : null;
    patchTxn(txnId, { [mark]: value }, (b) => C.markText(b, mark, value));
  }
  const pickItem = (t) => { const { memo, source, notification_id, installment, foreign, bank_category, created_at, updated_at, same_merchant, meal_cycle, ...item } = t; return { ...item, memo }; };

  async function saveNote() {
    const id = S.openId;
    const note = S.noteDraft[id];
    if (note === undefined) return;
    S.noteBusy = true; renderDetail();
    const r = await request("PATCH", `/api/money/transactions/${encodeURIComponent(id)}`, { note });
    S.noteBusy = false;
    if (r.status !== 200) { toast(C.errorText(r.status, r.body)); renderDetail(); return; }
    delete S.noteDraft[id];
    const t = r.body.transaction;
    S.txns = S.txns.map((x) => (x.id === t.id ? { ...x, note: t.note } : x));
    if (S.detail && S.detail.id === t.id) S.detail = t;
    renderDetail();
    renderTxns();
    toast(C.undoText(r.body, null), r.body.undo_id);
  }

  async function undo(id) {
    const r = await request("POST", `/api/undo/${encodeURIComponent(id)}`);
    if (r.status === 200) { toast("Undone"); refresh(); }
    else toast(C.errorText(r.status, r.body));
  }

  async function askDana() {
    const id = S.openId;
    if (!id) return;
    const r = await request("POST", "/api/chat/context", { kind: "transaction", id });
    if (r.status === 200 && r.body && r.body.session_id) { S.sheet = null; renderSheet(); window.danaOpenSession?.(r.body.session_id); }
    else toast(C.errorText(r.status, r.body));
  }

  // ---------- save: ways to save, spending to watch ----------

  const savingItem = (key) => ((got("savings") || {}).items || []).find((it) => it.key === key) || null;
  /** An item's newer copy (a PATCH's answer, a poll) in the loaded list, so the card changes in place. */
  function putSaving(item) {
    const sv = got("savings");
    if (!sv || !item) return;
    sv.items = sv.items.map((it) => (it.key === item.key ? item : it));
  }
  const savingUrl = (key) => `/api/money/savings/${encodeURIComponent(key)}`;
  /** After a redraw: the card in view, and the keyboard's focus back on its head (S.saveKeys: the last
   *  press came from the keyboard; a mouse click leaves no focus ring behind). */
  function focusSaving(key) {
    const head = document.querySelector(`#mny-view-save [data-save-toggle="${CSS.escape(key)}"]`);
    if (!head) return;
    if (S.saveKeys) head.focus({ preventScroll: true });
    head.closest(".mny-sv")?.scrollIntoView({ block: "nearest" });
  }
  function toggleSaving(key) {
    S.saveOpen = S.saveOpen === key ? null : key;
    if (S.saveTarget && S.saveTarget !== S.saveOpen) { S.saveTarget = null; S.saveTargetError = ""; }
    renderView();
    focusSaving(key);
  }
  /** One write (status, answers, target, a subscription line): the card takes the answer at once, the
   *  toast offers Undo, and the page refetches (the totals move). A card stays open only while it is
   *  being worked on (doing, a target, an answer, a line); one that moves to another section closes. */
  async function patchSaving(key, body) {
    if (S.saveBusy[key]) return;
    S.saveBusy[key] = true;
    renderView();
    const r = await request("PATCH", savingUrl(key), body);
    delete S.saveBusy[key];
    if (r.status !== 200 || !r.body || !r.body.item) { toast(C.errorText(r.status, r.body)); renderView(); return; }
    const item = r.body.item;
    putSaving(item);
    if ("target" in body || body.status) { S.saveTarget = null; S.saveTargetError = ""; delete S.saveDraft[key]; }
    if (body.status && body.status !== "doing" && S.saveOpen === key) S.saveOpen = null;
    renderView();
    if (S.saveOpen === key) focusSaving(key);
    toast(C.saveToast(item, body), r.body.undo_id);
    refresh();
  }
  /** "Check prices": only ever on this press. 202 (or 409, one already runs) → poll the item. */
  async function startCheck(key) {
    if (S.saveBusy[key]) return;
    delete S.checkNote[key];
    S.pollGaveUp.delete(key);
    S.saveBusy[key] = true;
    renderView();
    const r = await request("POST", `${savingUrl(key)}/check`);
    delete S.saveBusy[key];
    const reply = C.checkReply(r.status, r.body);
    const it = savingItem(key);
    if (reply.kind === "checking") {
      if (it && it.market) putSaving({ ...it, market: { ...it.market, state: "checking", error: null } });
      pollCheck(key);
    } else {
      // A question the check needs shows in the Prices block itself; anything else is said in words there.
      if (reply.kind === "question" && reply.question && it && it.market) {
        const has = (it.questions || []).some((q) => q.id === reply.question.id);
        putSaving({ ...it, market: { ...it.market, needs_answer: reply.question.id }, questions: has ? it.questions : [...(it.questions || []), reply.question] });
      } else S.checkNote[key] = reply;
    }
    renderView();
  }
  /** GET the item every 3 s while its check runs, for up to 90 s; then the list refetches. */
  function pollCheck(key) {
    if (S.savePolls[key]) return;
    const started = Date.now();
    const step = async () => {
      if (!S.visible || S.enabled !== true) { delete S.savePolls[key]; return; }
      const r = await request("GET", savingUrl(key));
      if (r.status === 200 && r.body) {
        putSaving(r.body);
        if (!r.body.market || r.body.market.state !== "checking") { delete S.savePolls[key]; renderView(); refresh(); return; }
      } else if (r.status === 404 || r.status === 403) { delete S.savePolls[key]; refresh(); return; }
      if (Date.now() - started >= CHECK_POLL_FOR_MS) {
        delete S.savePolls[key];
        S.pollGaveUp.add(key);
        S.checkNote[key] = { kind: "wait", text: "Still checking. The offers show up here when it's done: come back in a few minutes." };
        renderView();
        return;
      }
      S.savePolls[key] = setTimeout(step, CHECK_POLL_MS);
    };
    S.savePolls[key] = setTimeout(step, CHECK_POLL_MS);
  }
  /** A check already running when the list arrives (started earlier or elsewhere) is followed the same way. */
  function watchChecks() {
    ((got("savings") || {}).items || []).forEach((it) => {
      if (it.market && it.market.state === "checking" && !S.savePolls[it.key] && !S.pollGaveUp.has(it.key)) pollCheck(it.key);
    });
  }
  function stopChecks() {
    Object.values(S.savePolls).forEach((t) => clearTimeout(t));
    S.savePolls = {};
  }
  async function copyScript(key) {
    const it = savingItem(key);
    const text = it && it.script && it.script.text;
    if (!text) return;
    try { await navigator.clipboard.writeText(text); toast("Copied"); }
    catch (_) { toast("Couldn't copy here: select the text and copy it yourself."); }
  }
  function followSavingLink(key) {
    const it = savingItem(key);
    const l = it && C.savingLink(it.evidence && it.evidence.link);
    if (!l) return;
    if (l.f) { S.f = l.f; S.txDirty = true; }
    setTab(l.tab);
  }
  function openTarget(key) {
    S.saveOpen = key;
    S.saveTarget = key;
    S.saveTargetError = "";
    renderView();
    const input = document.querySelector(`#mny-view-save [data-save-target-form="${CSS.escape(key)}"] input`);
    if (input) { input.focus(); input.select(); input.closest(".mny-sv")?.scrollIntoView({ block: "nearest" }); }
  }
  function closeTarget() {
    const key = S.saveTarget;
    S.saveTarget = null; S.saveTargetError = "";
    if (key) delete S.saveDraft[key];
    renderView();
    if (key) focusSaving(key);
  }

  async function syncNow(id) {
    S.syncBusy[id] = true;
    render();
    const r = await request("POST", `/api/money/sources/${encodeURIComponent(id)}/sync`);
    if (r.status === 202) { toast("Syncing — new transactions show up here by themselves."); goFast(); setTimeout(loadSources, 1200); return; }
    delete S.syncBusy[id];
    toast(C.errorText(r.status, r.body));
    render();
  }

  async function sendCode(id, form) {
    const code = String(form.code.value || "").replace(/\s+/g, "");
    if (!/^\d{4,8}$/.test(code)) { S.codeError[id] = "The code is 4 to 8 digits."; renderBanners(); icons(); return; }
    S.codeBusy[id] = true; S.codeError[id] = ""; renderBanners(); icons();
    const r = await request("POST", `/api/money/code-requests/${encodeURIComponent(id)}`, { code });
    S.codeBusy[id] = false;
    if (r.status === 200) { S.codeSent[id] = true; delete S.codeDrafts[id]; goFast(); }
    else S.codeError[id] = C.errorText(r.status, r.body);
    renderBanners(); icons();
    if (r.status === 200 || r.status === 409) setTimeout(loadSources, 1500);
  }

  async function openAdd() {
    openSheet({ type: "companies" });
    await loadCompanies();
    if (S.sheet && S.sheet.type === "companies") { if (!S.companies) S.sheet.error = "Couldn't load the list of banks and cards."; renderSheet(); }
  }
  async function openEdit(sourceId) {
    const src = (S.sources || []).find((s) => s.id === sourceId);
    if (!src) return;
    openSheet({ type: "source-form", company: src.company, sourceId });
    await loadCompanies();
    if (S.sheet && S.sheet.type === "source-form") renderSheet();
  }

  /** The login form's state, set in place: a re-render would wipe what was typed. */
  function formState(form, { busy = false, error = "", field = null } = {}) {
    const btn = form.querySelector("[data-form-submit]");
    if (btn) { btn.disabled = busy; btn.textContent = busy ? "Saving…" : S.sheet && S.sheet.sourceId ? "Save and sync" : "Add and sync"; }
    const err = form.querySelector("[data-form-error]");
    if (err) err.textContent = error;
    form.querySelectorAll("[data-field-error]").forEach((n) => { n.textContent = field && n.dataset.fieldError === field.field ? field.message : ""; });
  }

  async function submitSource(form) {
    const s = S.sheet;
    const company = (S.companies || []).find((c) => c.id === s.company);
    if (!company) return;
    const credentials = {};
    company.fields.forEach((f) => { credentials[f.name] = form.elements[`cred:${f.name}`].value; });
    const filled = company.fields.filter((f) => credentials[f.name] !== "");
    const label = form.elements.label.value.trim();
    let body;
    if (s.sourceId) {
      if (filled.length && filled.length < company.fields.length) { formState(form, { error: "Fill in every field to change the login." }); return; }
      body = filled.length ? { credentials, label } : { label };
    } else {
      if (filled.length < company.fields.length) { formState(form, { error: "Fill in every field." }); return; }
      body = { company: company.id, credentials, ...(label ? { label } : {}) };
    }
    formState(form, { busy: true });
    const r = s.sourceId ? await request("PATCH", `/api/money/sources/${encodeURIComponent(s.sourceId)}`, body)
                         : await request("POST", "/api/money/sources", body);
    if (r.status === 200 || r.status === 201) {
      form.reset();  // the typed login leaves the page
      if (S.sheet === s) { S.sheet = null; renderSheet(); }
      toast(s.sourceId ? (filled.length ? `${company.name}: login updated, syncing now` : "Saved") : `${company.name} added — the first sync has started`);
      goFast();
      if (!s.sourceId && S.view !== "sources") setView("sources");
      loadSources();
      loadPage();
      return;
    }
    formState(form, r.status === 400 && r.body && r.body.field ? { field: { field: r.body.field, message: r.body.message } }
                                                               : { error: C.errorText(r.status, r.body) });
  }

  async function submitRemove(form) {
    const s = S.sheet;
    s.busy = true; renderSheet();
    const r = await request("DELETE", `/api/money/sources/${encodeURIComponent(s.sourceId)}${form.transactions.checked ? "?transactions=1" : ""}`);
    s.busy = false;
    if (r.status === 200) { S.sheet = null; renderSheet(); toast("Removed"); refresh(); return; }
    s.error = C.errorText(r.status, r.body);
    renderSheet();
  }

  // ---------- toast, focus ----------

  function toast(text, undoId) {
    const el = $("mny-toast");
    if (!el) return;
    clearTimeout(toastTimer);
    el.innerHTML = `<span>${esc(text)}</span>${undoId ? `<button data-undo="${esc(undoId)}">Undo</button>` : ""}`;
    el.classList.remove("hidden");
    toastTimer = setTimeout(() => el.classList.add("hidden"), undoId ? 7000 : 3500);
  }

  /** Re-render without losing what is typed: the focused [data-keep] field keeps its value, focus and caret. */
  function keepFocus(fn) {
    const a = document.activeElement;
    const key = a && a.dataset ? a.dataset.keep : null;
    const val = key ? a.value : null;
    let s0 = null, s1 = null;
    try { if (key) { s0 = a.selectionStart; s1 = a.selectionEnd; } } catch (_) { /* not a text field */ }
    fn();
    if (!key) return;
    const n = document.querySelector(`[data-keep="${CSS.escape(key)}"]`);
    if (n && n !== a) { n.value = val; n.focus(); try { if (s0 !== null) n.setSelectionRange(s0, s1); } catch (_) { /* ignore */ } }
  }

  function focusRow(dir) {
    const rows = [...document.querySelectorAll("#mny-txns .mny-row")];
    if (!rows.length) return;
    const i = rows.indexOf(document.activeElement);
    const next = rows[Math.max(0, Math.min(rows.length - 1, i < 0 ? 0 : i + dir))];
    next.focus();
    next.scrollIntoView({ block: "nearest" });
  }

  // ---------- events ----------

  function onClick(ev) {
    const t = ev.target;
    let n;
    if ((n = t.closest("[data-undo]"))) { $("mny-toast").classList.add("hidden"); undo(n.dataset.undo); return; }
    if (t.closest("#mny-view-save") && onSaveClick(t, ev)) return;
    if ((n = t.closest("[data-month]"))) { setMonth(n.dataset.month); return; }
    if ((n = t.closest("[data-cat-for]"))) {
      ev.stopPropagation();
      const back = S.sheet && S.sheet.type === "detail" ? S.sheet : null;
      openSheet({ type: "picker", txnId: n.dataset.catFor, q: "", forMerchant: true, back });
      return;
    }
    if ((n = t.closest("[data-pick]"))) {
      const s = S.sheet;
      const key = n.dataset.pick;
      if (s.type === "filter-category") { S.sheet = null; renderSheet(); setFilter({ category: key }); }
      else { const { txnId, forMerchant, back } = s; S.sheet = back || null; renderSheet(); setCategory(txnId, key, forMerchant); }
      return;
    }
    if ((n = t.closest("[data-pscope]"))) { S.sheet.forMerchant = n.dataset.pscope === "merchant"; renderSheet(); return; }
    if ((n = t.closest("[data-pick-account]"))) { S.sheet = null; renderSheet(); setFilter({ account: n.dataset.pickAccount }); return; }
    if ((n = t.closest("[data-company]"))) { openSheet({ type: "source-form", company: n.dataset.company }); return; }
    if ((n = t.closest("[data-open]"))) {
      const k = n.dataset.open;
      openSheet(k === "category" ? { type: "filter-category", q: "" } : { type: k });
      return;
    }
    if ((n = t.closest("[data-clear]"))) {
      const k = n.dataset.clear;
      if (S.sheet && S.sheet.type === "amount") { S.sheet = null; renderSheet(); }
      setFilter(k === "amount" ? { min: "", max: "" } : { [k]: "" });
      return;
    }
    if ((n = t.closest("[data-scope]"))) { setFilter({ all: n.dataset.scope === "all" }); return; }
    if ((n = t.closest("[data-tab-go]"))) { setTab(n.dataset.tabGo); return; }
    if ((n = t.closest("[data-go]"))) { setTab(n.dataset.go, { anchor: n.dataset.anchor || null }); return; }
    if ((n = t.closest("[data-link-view]"))) { followLink(n.dataset.linkView, n.dataset.linkCategory || "", n.dataset.linkId || ""); return; }
    if ((n = t.closest("[data-focus]"))) { setFilter({ category: n.dataset.focus, all: false }); return; }
    if ((n = t.closest("[data-expand]"))) {
      const k = n.dataset.expand;
      if (S.expanded.has(k)) S.expanded.delete(k); else S.expanded.add(k);
      renderView();
      return;
    }
    if ((n = t.closest("[data-bill]"))) {
      const k = n.dataset.bill;
      if (S.openBills.has(k)) S.openBills.delete(k); else S.openBills.add(k);
      renderView();
      return;
    }
    if ((n = t.closest("[data-horizon]"))) { setHorizon(Number(n.dataset.horizon)); return; }
    if ((n = t.closest("[data-inv-range]"))) { S.invDays = Number(n.dataset.invRange); renderView(); return; }
    if ((n = t.closest("[data-spend-mode]"))) { S.spendMode = n.dataset.spendMode; renderView(); return; }
    if ((n = t.closest("[data-month-bar]"))) { setMonth(n.dataset.monthBar); return; }
    if ((n = t.closest("[data-oneoff-no]"))) { const id = n.dataset.oneoffNo; n.disabled = true; patchTxn(id, { one_off: false }, (b) => C.markText(b, "one_off", false)); return; }
    if ((n = t.closest("[data-mark]"))) { setMark(n.dataset.markFor, n.dataset.mark, n.dataset.markValue); return; }
    if ((n = t.closest("[data-txn-go]"))) { openTxn(n.dataset.txnGo); return; }
    if ((n = t.closest("[data-merchant]"))) {
      S.sheet = null; renderSheet();
      if (!wide()) closeDetail();
      S.f = C.cleanFilters({ q: n.dataset.merchant, all: true });
      S.txDirty = true;
      setTab("transactions");
      return;
    }
    if (t.closest("[data-more]")) { loadTxns({ more: true }); return; }
    if ((n = t.closest("[data-sync]"))) { syncNow(n.dataset.sync); return; }
    if ((n = t.closest("[data-edit-source]"))) { openEdit(n.dataset.editSource); return; }
    if ((n = t.closest("[data-remove-source]"))) { openSheet({ type: "remove", sourceId: n.dataset.removeSource }); return; }
    if ((n = t.closest("[data-open-txn]"))) {
      if (t.closest("button, a, textarea, input") && !t.closest("button[data-open-txn]")) return;
      const id = n.dataset.openTxn;
      if (S.openId === id && wide()) closeDetail(); else openDetail(id);
      return;
    }
    if ((n = t.closest("[data-act]"))) act(n.dataset.act, n);
  }

  /** The Save view's clicks; true when one was handled. */
  function onSaveClick(t, ev) {
    let n;
    S.saveKeys = ev.detail === 0;  // a click from Enter / Space
    if ((n = t.closest("[data-save-toggle]"))) { toggleSaving(n.dataset.saveToggle); return true; }
    if ((n = t.closest("[data-save-section]"))) {
      const id = n.dataset.saveSection;
      if (S.saveCollapsed.has(id)) S.saveCollapsed.delete(id); else S.saveCollapsed.add(id);
      renderView();
      document.querySelector(`#mny-view-save [data-save-section="${CSS.escape(id)}"]`)?.focus();
      return true;
    }
    if ((n = t.closest("[data-save-act]"))) {
      const it = savingItem(n.dataset.saveAct);
      const a = it && C.statusActions(it)[Number(n.dataset.actI)];
      if (a && a.body) patchSaving(it.key, a.body);
      return true;
    }
    if ((n = t.closest("[data-save-target]"))) { openTarget(n.dataset.saveTarget); return true; }
    if (t.closest("[data-save-target-cancel]")) { closeTarget(); return true; }
    if ((n = t.closest("[data-save-answer]"))) {
      const it = savingItem(n.dataset.saveAnswer);
      const q = it && (it.questions || []).find((x) => x.id === n.dataset.q);
      const o = q && (q.options || [])[Number(n.dataset.i)];
      if (o && String(o.value) !== String(q.answer)) { delete S.checkNote[it.key]; patchSaving(it.key, { answers: { [q.id]: o.value } }); }
      return true;
    }
    if ((n = t.closest("[data-save-sub]"))) {
      const it = savingItem(n.dataset.saveSub);
      const sub = it && (it.subitems || [])[Number(n.dataset.subI)];
      if (sub) patchSaving(it.key, { subitem: { key: sub.key, choice: sub.choice === n.dataset.choice ? null : n.dataset.choice } });
      return true;
    }
    if ((n = t.closest("[data-save-check]"))) { startCheck(n.dataset.saveCheck); return true; }
    if ((n = t.closest("[data-save-copy]"))) { copyScript(n.dataset.saveCopy); return true; }
    if ((n = t.closest("[data-save-link]"))) { followSavingLink(n.dataset.saveLink); return true; }
    return false;
  }

  function act(a, n) {
    switch (a) {
      case "addons": window.danaSwitchTab?.("addons"); break;
      case "retry": S.error = ""; loadAll(); break;
      case "retry-tx": loadTxns(); break;
      case "this-month": setMonth(thisMonth()); break;
      case "goto-uncat": S.f = C.cleanFilters({ category: "uncategorized" }); S.txDirty = true; setTab("transactions"); break;
      case "focus-tx": S.f = C.cleanFilters({ category: S.f.category }); S.txDirty = true; setTab("transactions"); break;
      case "toggle-uncat": setFilter({ category: S.f.category === "uncategorized" ? "" : "uncategorized" }); break;
      case "clear-filters": setFilter({ ...C.EMPTY_FILTERS }); break;
      case "close-detail": closeDetail(); break;
      case "save-note": saveNote(); break;
      case "cancel-note": delete S.noteDraft[S.openId]; renderDetail(); break;
      case "ask": askDana(); break;
      case "sheet-close": closeSheet(); break;
      case "add-source": openAdd(); break;
      case "back-companies": openSheet({ type: "companies" }); break;
      case "view-sources": setView("sources"); break;
      default: break;
    }
  }
  /** The flow picker in the detail ("Counts as"). */
  function onChange(ev) {
    const t = ev.target;
    if (t.dataset && t.dataset.flowFor && t.value) patchTxn(t.dataset.flowFor, { flow: t.value }, C.flowText);
  }

  function onInput(ev) {
    const t = ev.target;
    if (t.id === "mny-q") {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        S.f = C.cleanFilters({ ...S.f, q: t.value });
        syncHash();
        loadTxns();
      }, 280);
      return;
    }
    if (t.dataset.pickerQ !== undefined && S.sheet) {
      S.sheet.q = t.value;
      const list = $("mny-pick-list");
      const txn = S.sheet.type === "picker" ? (S.txns.find((x) => x.id === S.sheet.txnId) || S.detail) : null;
      if (list) { list.innerHTML = pickerListHtml(S.sheet, txn); icons(); }
      return;
    }
    if (t.dataset.codeInput) { S.codeDrafts[t.dataset.codeInput] = t.value; return; }
    if (t.dataset.saveDraft) { S.saveDraft[t.dataset.saveDraft] = t.value; return; }
    if (t.dataset.noteFor) {
      S.noteDraft[t.dataset.noteFor] = t.value;
      const tx = currentTxn();
      const dirty = t.value !== ((tx && tx.note) || "");
      t.closest(".mny-d-section")?.querySelector(".mny-note-btns")?.classList.toggle("hidden", !dirty);
    }
  }

  function onSubmit(ev) {
    const form = ev.target;
    if (form.dataset.codeForm) { ev.preventDefault(); sendCode(form.dataset.codeForm, form); return; }
    if (form.dataset.saveTargetForm) {
      ev.preventDefault();
      const key = form.dataset.saveTargetForm;
      const target = C.targetAgorot(form.elements.amount.value);
      if (target == null) { S.saveTargetError = "Type the most to spend a month in whole shekels, for example 650."; renderView(); return; }
      S.saveTargetError = "";
      patchSaving(key, { target });
      return;
    }
    if (form.dataset.form === "amount") {
      ev.preventDefault();
      const min = form.min.value, max = form.max.value;
      S.sheet = null; renderSheet();
      setFilter({ min, max });
      return;
    }
    if (form.dataset.form === "source") { ev.preventDefault(); submitSource(form); return; }
    if (form.dataset.form === "remove") { ev.preventDefault(); submitRemove(form); }
  }

  function onKey(ev) {
    if (!S.visible) return;
    const typing = ev.target.matches?.("input, textarea, select, [contenteditable]");
    if (ev.key === "Escape") {
      if (S.sheet) { ev.preventDefault(); closeSheet(); return; }
      if (typing && ev.target.id === "mny-q" && ev.target.value) return;
      if (S.tab === "save" && S.view === "page" && (S.saveTarget || S.saveOpen)) {
        ev.preventDefault();
        S.saveKeys = true;
        if (S.saveTarget) closeTarget();
        else { const key = S.saveOpen; S.saveOpen = null; renderView(); focusSaving(key); }
        return;
      }
      if (S.openId) { ev.preventDefault(); closeDetail(); }
      return;
    }
    if (ev.target.dataset && ev.target.dataset.noteFor && ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); saveNote(); return; }
    if (typing || ev.ctrlKey || ev.metaKey || ev.altKey || S.sheet) return;
    if (ev.key === "/") { ev.preventDefault(); if (S.tab !== "transactions") setTab("transactions"); setTimeout(() => $("mny-q")?.focus(), 0); return; }
    if (S.tab !== "transactions" || S.view !== "page") return;
    const row = ev.target.closest?.(".mny-row");
    if (row && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); openDetail(row.dataset.openTxn); return; }
    if (ev.key === "ArrowDown" || ev.key === "j") { if (row || ev.key === "j") { ev.preventDefault(); focusRow(1); } return; }
    if (ev.key === "ArrowUp" || ev.key === "k") { if (row || ev.key === "k") { ev.preventDefault(); focusRow(-1); } }
  }

  function wire() {
    const panel = $("panel-money");
    panel.addEventListener("click", onClick);
    panel.addEventListener("input", onInput);
    panel.addEventListener("submit", onSubmit);
    panel.addEventListener("change", onChange);
    $("mny-scrim").addEventListener("click", closeSheet);
    $("mny-back").addEventListener("click", () => setView("page"));
    $("mny-sources-btn").addEventListener("click", () => setView("sources"));
    $("mny-add-top").addEventListener("click", openAdd);
    $("mny-refresh").addEventListener("click", () => { if (S.enabled) { refresh(); if (S.view === "sources") loadSources(); } });
    document.addEventListener("keydown", onKey);
    document.addEventListener("visibilitychange", () => { if (S.visible && !document.hidden && S.enabled) poll(); });
    window.addEventListener("focus", () => { if (S.visible && S.enabled) poll(); });
    let resizeTimer = null, wasWide = wide();
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (!S.visible || S.view !== "page" || !S.loaded) return;
        const nowWide = wide();
        if (nowWide !== wasWide) {
          wasWide = nowWide;
          if (S.sheet && S.sheet.type === "detail" && nowWide) S.sheet = null;
          else if (S.openId && !nowWide && !S.sheet) S.sheet = { type: "detail" };
          renderDetail(); renderSheet();
        }
        D.redraw();
      }, 150);
    });
  }

  // ---------- start ----------

  window.DanaMoney = {
    show() {
      if (!S.wired && $("panel-money")) { wire(); S.wired = true; }
      const was = S.visible;
      S.visible = true;
      syncHash();
      if (S.enabled === true) { if (!was) loadAll(); }
      else render();
    },
    hide() {
      if (!S.visible) return;
      S.visible = false;
      clearTimeout(pollTimer);
      clearInterval(tickTimer);
      stopChecks();
      S.sheet = null;
      renderSheet();
      $("mny-toast")?.classList.add("hidden");
      if (location.hash.startsWith("#money")) history.replaceState(history.state, "", location.pathname + location.search);
    },
  };

  async function init() {
    const fromHash = C.stateFromHash(location.hash);
    if (fromHash) {
      if (fromHash.month && fromHash.month <= thisMonth()) S.month = fromHash.month;
      S.view = fromHash.view;
      S.tab = fromHash.tab;
      S.f = fromHash.f;
      window.danaSwitchTab?.("money");
    }
    const r = await request("GET", "/api/me");
    if (r.status !== 200) { if (S.visible) { S.error = C.errorText(r.status, r.body); render(); } return; }
    setEnabled((r.body.addons || []).includes("money"));
    if (S.enabled && S.visible) loadAll();
  }
  // After the app's scripts (static/app.js; they load before this one), which wire the tabs and open Chat.
  if (document.readyState === "complete") init();
  else document.addEventListener("DOMContentLoaded", init);
})();
