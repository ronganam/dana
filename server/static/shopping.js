/* Shopping — the grocery list panel (docs/shopping.md). The pure part is static/shopping-core.js.
 *
 *   GET    /api/shopping[?list=]                the screen; every write answers with it (+ undo, results)
 *   POST   /api/shopping/items?list=            add {text} | {items: [{name}]}
 *   PATCH  /api/shopping/items/{id}?list=       tick / edit
 *   DELETE /api/shopping/items/{id}?list=       remove
 *   POST   /api/shopping/clear-bought?list=
 *   GET    /api/shopping/suggestions?q=&list=   while typing
 *   DELETE /api/shopping/suggestions?list=      {name}: don't suggest again
 *   GET    /api/shopping/events[?list=]         live: changed {list_id, version}, lists {}, end {}
 *   PUT    /api/shopping/active                 {list_id}
 *   POST   /api/shopping/invites/{list_id}      {action: use | dismiss, move_items}
 *   GET/PUT /api/sharing/shopping_list/{id}     the Share sheet (owner)
 *   POST   /api/undo/{id}                       the snackbar's Undo
 *
 * window.DanaShopping = {show, hide}; app-nav.js calls them on tab switches. The rail / bottom-nav buttons
 * ([data-shopping-nav]) appear only for a user with `shopping` connected (GET /api/me → kinds), with the
 * count to buy ([data-shopping-badge]). /shopping opens the app on this panel.
 */
(() => {
  "use strict";
  const C = window.DanaShoppingCore;
  const $ = (id) => document.getElementById(id);
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };
  const reduced = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const hoverDevice = () => window.matchMedia?.("(hover: hover) and (pointer: fine)").matches;
  const newId = () => (crypto.randomUUID ? crypto.randomUUID() : `t${Date.now()}${Math.random().toString(36).slice(2, 10)}`);

  const S = {
    me: null, enabled: false, visible: false,
    server: null,          // the last screen the server answered
    ops: [],               // changes on their way (shown on top of `server`)
    viewing: null,         // a list looked at with ?list= (a read-only share); null = the active list
    status: "loading",     // loading | ready | error | off
    error: "",
    linger: new Map(),     // id → timer: ticked rows that stay in place for a moment
    boughtOpen: false,
    reloadLater: false,
    invites: {},           // list_id → "" | "ask" | "busy"
    editor: null,          // {item, draft, error, busy}
    share: null,           // {model, error, busy}
    shareCache: null,      // the owner's sharing payload (decides whether Share is offered)
    menuOpen: false,
    sugg: { items: [], index: -1, seq: 0, timer: 0, text: "" },
    snack: null,           // {text, undo, timer}
    es: null,
    rendered: null,        // the list id last drawn
  };
  let loadSeq = 0;

  async function request(method, url, body) {
    let r;
    try {
      r = await fetch(url, { method, headers: body !== undefined ? { "Content-Type": "application/json" } : {},
                             body: body !== undefined ? JSON.stringify(body) : undefined, cache: "no-store" });
    } catch (_) {
      return { status: 0, body: null };
    }
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    return { status: r.status, body: data };
  }

  /** Send one change as the core says (writes name the list on screen). */
  function send(op) {
    const q = C.requestFor(op, S.server ? S.server.list.id : null);
    return request(q.method, q.url, q.body);
  }

  const shown = () => C.applyOps(S.server, S.ops, S.me);
  const listId = () => S.server && S.server.list.id;

  // ---------- loading ----------

  async function load() {
    const mine = ++loadSeq;
    const r = await request("GET", `/api/shopping${S.viewing ? `?list=${encodeURIComponent(S.viewing)}` : ""}`);
    if (mine !== loadSeq) return;
    if (r.status === 200) {
      if (C.accepts(S.server, r.body) || S.status !== "ready") S.server = r.body;
      S.status = "ready";
      S.reloadLater = false;
      setBadge(S.viewing ? null : S.server.counts.to_buy);
      if (S.server.list.access === "owner" && S.shareCache === null) loadShareCache();
    } else if (r.status === 404 && S.viewing) {
      S.viewing = null; openStream(); return load();
    } else if (r.status === 403 && r.body && r.body.error === "not_connected") {
      S.status = "off"; S.server = null;
    } else if (!S.server) {
      S.status = "error"; S.error = C.errorText(r.status, r.body);
    }
    render();
  }

  async function loadShareCache() {
    S.shareCache = false;  // asked
    const r = await request("GET", `/api/sharing/shopping_list/${encodeURIComponent(listId())}`);
    S.shareCache = r.status === 200 ? r.body : false;
    renderHeader();
  }

  // ---------- changes ----------

  async function run(op) {
    if (!S.server) return null;
    S.ops.push(op);
    render();
    const r = await send(op);
    S.ops = S.ops.filter((o) => o !== op);
    if (r.status === 200 && r.body && r.body.list) {
      if (C.accepts(S.server, r.body)) S.server = r.body;
      setBadge(S.viewing ? null : S.server.counts.to_buy);
    }
    if (S.reloadLater && !S.ops.length) load();
    render();
    return r;
  }

  async function change(op, words) {
    const r = await run(op);
    if (!r) return r;
    if (r.status === 200) {
      const said = words ? words(r.body) : null;
      if (r.body.undo || said) snack(said || C.undoLabel(r.body.undo.label), r.body.undo);
    } else {
      snack(C.errorText(r.status, r.body));
    }
    return r;
  }

  function addText(text) {
    const op = C.addOp(text, newId);
    if (!op) return false;
    if (op.error) { snack(op.error); return false; }
    const input = $("shp-add");
    change(op, (b) => C.addResultText(b.results).text).then((r) => {
      if (r && r.status !== 200 && input) input.value = op.text + (input.value.trim() ? `, ${input.value}` : "");  // never lose what was typed
    });
    return true;
  }

  function addName(name) {
    change(C.addNameOp(name, newId), (b) => C.addResultText(b.results).text);
  }

  function tick(id) {
    const s = shown();
    const it = s && s.items.find((i) => i.id === id);
    if (!it || it.pending) return;
    if (S.linger.has(id)) {  // a second tap while it is still in place: put it back
      clearTimeout(S.linger.get(id)); S.linger.delete(id);
      change({ type: "check", id, checked: false });
      return;
    }
    if (it.checked) { change({ type: "check", id, checked: false }); return; }
    S.linger.set(id, setTimeout(() => leave(id), C.LINGER_MS));
    change({ type: "check", id, checked: true });
  }

  function leave(id) {
    const el = document.querySelector(`#shp-list [data-key="r:${CSS.escape(id)}"]`);
    const done = () => { S.linger.delete(id); render(); };
    if (!el || reduced()) return done();
    el.style.height = `${el.offsetHeight}px`;
    requestAnimationFrame(() => { el.classList.add("shp-leaving"); el.style.height = "0px"; });
    setTimeout(done, 190);
  }

  function remove(id) { change({ type: "remove", id }); }
  function clearBought() { change({ type: "clear" }); }

  async function undoNow(undo) {
    hideSnack();
    const r = await send({ type: "undo", id: undo.id });
    const t = C.undoText(r.status, r.body);
    snack(t.text);
    load();
  }

  // ---------- invitations, lists, sharing ----------

  async function answerInvite(id, action, moveItems) {
    S.invites[id] = "busy"; renderInvites();
    const r = await send({ type: "invite", listId: id, action, moveItems });
    delete S.invites[id];
    if (r.status === 200) {
      S.viewing = null;
      S.server = r.body;
      openStream();
      if (r.body.undo) snack(C.undoLabel(r.body.undo.label), r.body.undo);
    } else snack(C.errorText(r.status, r.body));
    render();
  }

  async function switchTo(id) {
    closeMenu();
    const r = await send({ type: "active", listId: id });
    if (r.status === 200) {
      S.viewing = null; S.server = r.body; S.shareCache = null; openStream();
      if (r.body.undo) snack(C.undoLabel(r.body.undo.label), r.body.undo);
      render();
      if (S.server.list.access === "owner") loadShareCache();
    } else snack(C.errorText(r.status, r.body));
  }

  function viewList(id) {
    closeMenu();
    S.viewing = id; S.boughtOpen = false; S.shareCache = null;
    openStream(); load();
  }

  async function openShare() {
    S.share = { model: S.shareCache ? C.shareModel(S.shareCache) : null, error: "", busy: false };
    openSheet("share");
    const r = await request("GET", `/api/sharing/shopping_list/${encodeURIComponent(listId())}`);
    if (!S.share) return;
    if (r.status === 200) { S.shareCache = r.body; S.share.model = C.shareModel(r.body); }
    else S.share.error = C.errorText(r.status, r.body);
    renderSheet();
  }

  async function setShare(userId, level) {
    if (!S.share || S.share.busy) return;
    S.share.busy = true; S.share.error = ""; renderSheet();
    const r = await send({ type: "share", listId: listId(), userId, level });
    if (!S.share) return;
    S.share.busy = false;
    if (r.status === 200) { S.shareCache = r.body; S.share.model = C.shareModel(r.body); load(); }
    else S.share.error = C.errorText(r.status, r.body);
    renderSheet();
  }

  // ---------- the editor ----------

  function openEditor(id) {
    const s = shown();
    const it = s && s.items.find((i) => i.id === id);
    if (!it || it.pending || !s.list || s.list.access === "read") return;
    S.editor = { item: { ...it }, draft: C.editorDraft(it), error: "", busy: false };
    openSheet("editor");
  }

  async function saveEditor() {
    const ed = S.editor;
    if (!ed || ed.busy) return;
    const out = C.editChanges(ed.item, ed.draft);
    if (out.error) { ed.error = out.error; renderSheet(); return; }
    if (!Object.keys(out.changes).length) { closeSheet(); return; }
    ed.busy = true;
    const r = await run({ type: "edit", id: ed.item.id, changes: out.changes });
    if (S.editor !== ed) return;
    ed.busy = false;
    if (r && r.status === 200) {
      closeSheet();
      if (r.body.undo) snack(C.undoLabel(r.body.undo.label), r.body.undo);
    } else if (r) { ed.error = C.errorText(r.status, r.body); renderSheet(); }
  }

  function patchEditorControls() {
    const ed = S.editor;
    const sheet = $("shp-sheet");
    if (!ed || !sheet) return;
    const q = sheet.querySelector('[data-ed="quantity"]');
    if (q && q.value !== ed.draft.quantity) q.value = ed.draft.quantity;
    const count = C.isCount(ed.draft.quantity);
    sheet.querySelector('[data-act="qty-down"]').disabled = !count || ed.draft.quantity === "";
    sheet.querySelector('[data-act="qty-up"]').disabled = !count;
    sheet.querySelectorAll(".shp-aisle-chip").forEach((b) => {
      const on = b.dataset.key === ed.draft.category;
      b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on));
    });
  }

  // ---------- sheets, menu, snackbar ----------

  let sheetReturn = null;
  function openSheet(kind) {
    sheetReturn = document.activeElement;
    const sheet = $("shp-sheet");
    sheet.dataset.kind = kind;
    sheet.setAttribute("aria-labelledby", kind === "editor" ? "shp-editor-title" : "shp-share-title");
    renderSheet();
    $("shp-scrim").classList.remove("hidden");
    sheet.classList.remove("hidden");
    requestAnimationFrame(() => sheet.classList.add("open"));
    const first = kind === "editor" && hoverDevice() ? sheet.querySelector("#shp-ed-name") : sheet.querySelector("button, input");
    first?.focus({ preventScroll: true });
  }

  function closeSheet() {
    S.editor = null; S.share = null;
    const sheet = $("shp-sheet");
    sheet.classList.remove("open");
    sheet.classList.add("hidden");
    sheet.innerHTML = "";
    $("shp-scrim").classList.add("hidden");
    if (sheetReturn && document.contains(sheetReturn)) sheetReturn.focus({ preventScroll: true });
    sheetReturn = null;
  }

  function renderSheet() {
    const sheet = $("shp-sheet");
    if (!sheet) return;
    const active = document.activeElement;
    const keep = sheet.contains(active) && active.dataset ? active.dataset.ed : null;
    const caret = keep && active.selectionStart != null ? [active.selectionStart, active.selectionEnd] : null;
    if (S.editor) sheet.innerHTML = C.renderEditor(S.editor.draft, S.server ? S.server.categories : [], S.editor);
    else if (S.share) sheet.innerHTML = C.renderShare(S.share.model, S.share);
    icons();
    if (keep) {
      const el = sheet.querySelector(`[data-ed="${keep}"]`);
      if (el) { el.focus({ preventScroll: true }); if (caret) el.setSelectionRange(...caret); }
    }
  }

  function toggleMenu() {
    S.menuOpen = !S.menuOpen;
    renderHeader();
    if (S.menuOpen) $("shp-menu").querySelector("button")?.focus();
  }
  function closeMenu() { if (S.menuOpen) { S.menuOpen = false; renderHeader(); } }

  function snack(text, undo) {
    if (!text) return;
    if (S.snack) clearTimeout(S.snack.timer);
    const el = $("shp-snack");
    el.innerHTML = `<span class="shp-snack-text" dir="auto">${C.esc(text)}</span>${undo ? `<button type="button" data-act="undo">Undo</button>` : ""}`;
    el.classList.remove("hidden");
    S.snack = { text, undo, timer: setTimeout(hideSnack, C.SNACK_MS) };
  }
  function hideSnack() {
    if (S.snack) clearTimeout(S.snack.timer);
    S.snack = null;
    $("shp-snack")?.classList.add("hidden");
  }

  let usualPop = null;
  function openUsualMenu(chip, x, y) {
    usualPop = chip.dataset.name;
    const pop = $("shp-pop");
    pop.innerHTML = `<button type="button" role="menuitem" class="shp-menu-item" data-act="forget">${'<i data-lucide="eye-off" aria-hidden="true"></i>'}<span>Don't suggest again</span></button>`;
    pop.classList.remove("hidden");
    icons();
    const panel = $("panel-shopping").getBoundingClientRect();
    const w = pop.offsetWidth, h = pop.offsetHeight;
    pop.style.left = `${Math.max(8, Math.min(x - panel.left, panel.width - w - 8))}px`;
    pop.style.top = `${Math.max(8, Math.min(y - panel.top + 8, panel.height - h - 8))}px`;
    pop.querySelector("button").focus();
  }
  function closeUsualMenu() { usualPop = null; $("shp-pop")?.classList.add("hidden"); }

  // ---------- suggestions while typing ----------

  function onType() {
    const input = $("shp-add");
    const text = input.value;
    S.sugg.index = -1;
    clearTimeout(S.sugg.timer);
    const q = text.trim();
    if (!q || /[,\n]/.test(q)) { setSuggestions([]); return; }
    S.sugg.timer = setTimeout(async () => {
      const mine = ++S.sugg.seq;
      const r = await request("GET", `/api/shopping/suggestions?q=${encodeURIComponent(q)}${listId() ? `&list=${encodeURIComponent(listId())}` : ""}`);
      if (mine !== S.sugg.seq || $("shp-add").value.trim() !== q) return;
      setSuggestions(r.status === 200 ? (r.body.suggestions || []) : []);
    }, 140);
  }

  function setSuggestions(items) {
    S.sugg.items = items;
    if (S.sugg.index >= items.length) S.sugg.index = -1;
    const ul = $("shp-sugg"), input = $("shp-add");
    const open = items.length > 0 && document.activeElement === input;
    ul.innerHTML = open ? C.renderSuggestions(items, S.sugg.index) : "";
    ul.classList.toggle("hidden", !open);
    input.setAttribute("aria-expanded", String(open));
    if (S.sugg.index >= 0) input.setAttribute("aria-activedescendant", `shp-sugg-${S.sugg.index}`);
    else input.removeAttribute("aria-activedescendant");
    if (open) icons();
  }

  // ---------- rendering ----------

  function setBadge(n) {
    if (n === null || n === undefined) return;
    document.querySelectorAll("[data-shopping-badge]").forEach((b) => {
      b.textContent = n > 99 ? "99+" : String(n);
      b.classList.toggle("hidden", !n);
    });
  }

  function renderHeader() {
    const s = shown();
    const sub = $("shp-sub"), people = $("shp-people"), share = $("shp-share-btn"), menuBtn = $("shp-menu-btn"), menu = $("shp-menu");
    if (!s) {
      sub.textContent = ""; people.innerHTML = ""; share.classList.add("hidden"); menuBtn.classList.add("hidden"); menu.classList.add("hidden");
      return;
    }
    const v = C.view(s, { me: S.me, linger: new Set(S.linger.keys()), boughtOpen: S.boughtOpen });
    sub.textContent = v.subtitle;
    people.innerHTML = C.renderPeople(v);
    const canShare = v.isOwner && S.shareCache && ((S.shareCache.users || []).length || (S.shareCache.shared_with || []).length);
    share.classList.toggle("hidden", !canShare);
    const items = C.menuItems(s, S.viewing);
    menuBtn.classList.toggle("hidden", !items.length);
    menuBtn.setAttribute("aria-expanded", String(S.menuOpen && items.length > 0));
    menu.innerHTML = S.menuOpen ? C.renderMenu(items) : "";
    menu.classList.toggle("hidden", !(S.menuOpen && items.length));
    if (!items.length) S.menuOpen = false;
    icons();
  }

  function renderInvites() {
    const s = shown();
    $("shp-invites").innerHTML = s ? C.renderInvites(C.view(s, { me: S.me }), s, S.invites) : "";
    icons();
  }

  // Keyed update of the list: rows keep their DOM nodes (and focus) unless their HTML changed.
  function patchList(container, parts, flash) {
    const old = new Map([...container.children].map((el) => [el.dataset.key, el]));
    const active = document.activeElement;
    const focusKey = active && container.contains(active) ? active.dataset.focus : null;
    let prev = null, added = false;
    const fresh = [];
    for (const p of parts) {
      let el = old.get(p.key);
      if (!el || el._html !== p.html) {
        const t = document.createElement("template");
        t.innerHTML = p.html.trim();
        const nu = t.content.firstElementChild;
        nu.dataset.key = p.key;
        nu._html = p.html;
        if (el) el.replaceWith(nu);
        else if (flash && p.key.startsWith("r:") && !nu.classList.contains("is-pending")) fresh.push(nu);
        el = nu; added = true;
      }
      old.delete(p.key);
      const want = prev ? prev.nextSibling : container.firstChild;
      if (want !== el) container.insertBefore(el, want);
      prev = el;
    }
    for (const el of old.values()) el.remove();
    if (added) icons();
    if (focusKey && !container.contains(document.activeElement)) {
      container.querySelector(`[data-focus="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
    }
    if (!reduced()) fresh.forEach((el) => { el.classList.add("shp-fresh"); setTimeout(() => el.classList.remove("shp-fresh"), 1200); });
  }

  function render() {
    const root = $("panel-shopping");
    if (!root) return;
    const s = shown();
    const status = $("shp-status");
    root.classList.toggle("is-readonly", Boolean(s && s.list.access === "read"));
    root.dataset.state = S.status;
    if (S.status !== "ready" || !s) {
      status.innerHTML = S.status === "off"
        ? `<div class="shp-empty"><i data-lucide="shopping-cart"></i><strong>Shopping isn't turned on</strong><span>An admin can connect it for you in Settings → Integrations.</span></div>`
        : S.status === "error"
          ? `<div class="shp-empty"><i data-lucide="cloud-off"></i><strong>Couldn't load the list</strong><span>${C.esc(S.error)}</span><button class="btn-secondary" data-act="retry">Try again</button></div>`
          : `<div class="shp-skel"></div><div class="shp-skel short"></div>`;
      $("shp-list").innerHTML = ""; $("shp-usuals").innerHTML = ""; $("shp-empty").innerHTML = ""; $("shp-invites").innerHTML = "";
      renderHeader();
      icons();
      return;
    }
    status.innerHTML = "";
    const v = C.view(s, { me: S.me, linger: new Set(S.linger.keys()), boughtOpen: S.boughtOpen });
    renderHeader();
    renderInvites();
    const usuals = C.renderUsuals(v);
    const u = $("shp-usuals");
    if (u._html !== usuals) { const x = u.querySelector(".shp-usuals")?.scrollLeft || 0; u.innerHTML = usuals; u._html = usuals; const n = u.querySelector(".shp-usuals"); if (n) n.scrollLeft = x; icons(); }
    // New rows flash only when they arrive on the list already shown (not on the first draw or a switch).
    patchList($("shp-list"), C.listParts(v), S.rendered === v.listId);
    const empty = v.empty ? C.renderEmpty(v) : "";
    if ($("shp-empty")._html !== empty) { $("shp-empty").innerHTML = empty; $("shp-empty")._html = empty; icons(); }
    $("shp-addbar").classList.toggle("hidden", !v.canEdit);
    S.rendered = v.listId;
  }

  // ---------- the live stream ----------

  function openStream() {
    closeStream();
    if (!S.visible || !S.enabled) return;
    const es = new EventSource(`/api/shopping/events${S.viewing ? `?list=${encodeURIComponent(S.viewing)}` : ""}`);
    es.addEventListener("changed", (ev) => {
      let d; try { d = JSON.parse(ev.data); } catch (_) { return; }
      const what = C.onChanged(d, S.server, S.ops.length);
      if (what === "now") load();
      else if (what === "later") S.reloadLater = true;
    });
    es.addEventListener("lists", () => load());
    es.addEventListener("end", () => { S.viewing = null; openStream(); load(); });
    S.es = es;
  }
  function closeStream() { if (S.es) { S.es.close(); S.es = null; } }

  // ---------- events ----------

  function wire() {
    const root = $("panel-shopping");
    const input = $("shp-add");
    let pressTimer = 0, pressed = false;

    root.addEventListener("click", (ev) => {
      const t = ev.target.closest("[data-act]");
      if (!t) {
        if (S.menuOpen && !ev.target.closest("#shp-menu, #shp-menu-btn")) closeMenu();
        if (usualPop && !ev.target.closest("#shp-pop")) closeUsualMenu();
        return;
      }
      const act = t.dataset.act;
      const rowId = t.closest("[data-id]")?.dataset.id;
      if (act !== "menu" && S.menuOpen && !t.closest("#shp-menu")) closeMenu();
      switch (act) {
        case "tick": tick(rowId); break;
        case "edit": openEditor(rowId); break;
        case "bought": S.boughtOpen = !S.boughtOpen; render(); break;
        case "clear": case "menu-clear": closeMenu(); clearBought(); break;
        case "usual": if (pressed) { pressed = false; break; } addName(t.dataset.name); break;
        case "forget": { const name = usualPop; closeUsualMenu(); if (name) change({ type: "forget", name }, () => `Won't suggest ${name} again`); break; }
        case "undo": if (S.snack && S.snack.undo) undoNow(S.snack.undo); break;
        case "retry": S.status = "loading"; render(); load(); break;
        case "invite-use": {
          const m = C.inviteModel(shown().invites.find((i) => i.list_id === t.dataset.list), shown());
          if (m.askMove) { S.invites[t.dataset.list] = "ask"; renderInvites(); root.querySelector(`[data-act="invite-move"][data-list="${CSS.escape(t.dataset.list)}"]`)?.focus(); }
          else answerInvite(t.dataset.list, "use", false);
          break;
        }
        case "invite-move": answerInvite(t.dataset.list, "use", true); break;
        case "invite-keep": answerInvite(t.dataset.list, "use", false); break;
        case "invite-dismiss": answerInvite(t.dataset.list, "dismiss"); break;
        case "invite-view": viewList(t.dataset.list); break;
        case "menu-switch": t.closest(".current") ? closeMenu() : switchTo(t.dataset.list); break;
        case "menu-view": t.closest(".current") ? closeMenu() : viewList(t.dataset.list); break;
        case "suggest": break;  // handled on mousedown
        // the sheet
        case "editor-close": saveEditor(); break;
        case "sheet-close": closeSheet(); break;
        case "editor-remove": { const id = S.editor.item.id; closeSheet(); remove(id); break; }
        case "qty-down": case "qty-up": S.editor.draft.quantity = C.stepQuantity(S.editor.draft.quantity, act === "qty-up" ? 1 : -1); patchEditorControls(); break;
        case "aisle": S.editor.draft.category = t.dataset.key; patchEditorControls(); break;
        case "share-level": setShare(t.dataset.user, t.dataset.level); break;
      }
    });
    $("shp-menu-btn").addEventListener("click", (ev) => { ev.stopPropagation(); toggleMenu(); });
    $("shp-share-btn").addEventListener("click", () => openShare());
    $("shp-scrim").addEventListener("click", () => (S.editor ? saveEditor() : closeSheet()));

    // The usuals: long press / right click → "Don't suggest again".
    const usuals = $("shp-usuals");
    usuals.addEventListener("contextmenu", (ev) => {
      const chip = ev.target.closest(".shp-usual");
      if (!chip) return;
      ev.preventDefault();
      openUsualMenu(chip, ev.clientX, ev.clientY);
    });
    usuals.addEventListener("pointerdown", (ev) => {
      const chip = ev.target.closest(".shp-usual");
      if (!chip || ev.pointerType === "mouse") return;
      pressed = false;
      pressTimer = setTimeout(() => { pressed = true; const r = chip.getBoundingClientRect(); openUsualMenu(chip, r.left, r.bottom); }, 500);
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach((e) => usuals.addEventListener(e, () => clearTimeout(pressTimer)));
    usuals.addEventListener("scroll", () => clearTimeout(pressTimer), true);

    // The add bar.
    input.addEventListener("input", onType);
    input.addEventListener("focus", () => { if (S.sugg.items.length) setSuggestions(S.sugg.items); });
    input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input) setSuggestions(S.sugg.items); }, 0));
    input.addEventListener("paste", (ev) => {
      const text = (ev.clipboardData || window.clipboardData)?.getData("text") || "";
      if (!/[\r\n]/.test(text.trim())) return;
      ev.preventDefault();  // several lines become one comma-separated line: Enter adds them all
      const joined = text.split(/[\r\n]+/).map((l) => l.trim()).filter(Boolean).join(", ");
      const [a, b] = [input.selectionStart, input.selectionEnd];
      const before = input.value.slice(0, a).replace(/[,\s]*$/, ""), after = input.value.slice(b).replace(/^[,\s]*/, "");
      input.value = [before, joined, after].filter(Boolean).join(", ");
      onType();
    });
    input.addEventListener("keydown", (ev) => {
      const n = S.sugg.items.length;
      if (ev.key === "ArrowDown" && n) { ev.preventDefault(); S.sugg.index = (S.sugg.index + 1) % n; setSuggestions(S.sugg.items); }
      else if (ev.key === "ArrowUp" && n) { ev.preventDefault(); S.sugg.index = S.sugg.index <= 0 ? -1 : S.sugg.index - 1; setSuggestions(S.sugg.items); }
      else if (ev.key === "Escape" && n && !$("shp-sugg").classList.contains("hidden")) { ev.preventDefault(); ev.stopPropagation(); S.sugg.index = -1; setSuggestions([]); }
      else if (ev.key === "Enter" && !ev.isComposing) {
        ev.preventDefault();
        const chosen = S.sugg.index >= 0 ? S.sugg.items[S.sugg.index] : null;
        if (chosen) addName(chosen.name);
        else if (!addText(input.value)) return;
        input.value = "";
        S.sugg.index = -1; clearTimeout(S.sugg.timer); S.sugg.seq++; setSuggestions([]);
      }
    });
    $("shp-sugg").addEventListener("mousedown", (ev) => {
      const li = ev.target.closest("[data-name]");
      if (!li) return;
      ev.preventDefault();  // keep the focus in the input
      addName(li.dataset.name);
      input.value = ""; S.sugg.index = -1; S.sugg.seq++; setSuggestions([]);
    });

    // The editor's fields.
    $("shp-sheet").addEventListener("input", (ev) => {
      const f = ev.target.dataset.ed;
      if (!f || !S.editor) return;
      S.editor.draft[f] = ev.target.value;
      if (S.editor.error) { S.editor.error = ""; const e = $("shp-sheet").querySelector(".shp-ed-error"); if (e) e.remove(); }
      if (f === "quantity") patchEditorControls();
    });

    // Keys.
    root.addEventListener("keydown", (ev) => {
      if (ev.defaultPrevented) return;
      const sheetOpen = !$("shp-sheet").classList.contains("hidden");
      if (ev.key === "Escape") {
        if (sheetOpen) { ev.preventDefault(); closeSheet(); return; }
        if (usualPop) { closeUsualMenu(); return; }
        if (S.menuOpen) { closeMenu(); $("shp-menu-btn").focus(); return; }
        return;
      }
      if (sheetOpen) {
        if (ev.key === "Enter" && ev.target.matches("#shp-sheet input") && S.editor && !ev.isComposing) { ev.preventDefault(); saveEditor(); }
        else if (ev.key === "Tab") trapFocus(ev, $("shp-sheet"));
        return;
      }
      const typing = ev.target.matches("input, textarea, select, [contenteditable]");
      if (typing || ev.ctrlKey || ev.metaKey || ev.altKey) return;
      const main = ev.target.closest(".shp-main");
      if ((ev.key === "/" || ev.key === "a") && !$("shp-addbar").classList.contains("hidden")) { ev.preventDefault(); input.focus(); return; }
      if (!main) return;
      const id = main.closest("[data-id]").dataset.id;
      if (ev.key === "e") { ev.preventDefault(); openEditor(id); }
      else if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); focusNext(main, 1) || focusNext(main, -1); remove(id); }
      else if (ev.key === "ArrowDown" || ev.key === "j") { ev.preventDefault(); focusNext(main, 1); }
      else if (ev.key === "ArrowUp" || ev.key === "k") { ev.preventDefault(); focusNext(main, -1); }
    });
    document.addEventListener("keydown", (ev) => {
      // "/" from anywhere while the panel is shown (focus may be on the body).
      if (!S.visible || ev.defaultPrevented || ev.target.closest?.("#panel-shopping")) return;
      if (ev.target.matches?.("input, textarea, select, [contenteditable]") || ev.ctrlKey || ev.metaKey || ev.altKey) return;
      if ((ev.key === "/" || ev.key === "a") && S.server && S.server.list.access !== "read") { ev.preventDefault(); input.focus(); }
    });
    document.addEventListener("visibilitychange", () => {
      if (!S.enabled) return;
      if (document.hidden) return;
      if (S.visible) { load(); if (!S.es || S.es.readyState === 2) openStream(); }
      else refreshBadge();
    });
  }

  function focusNext(from, dir) {
    const all = [...document.querySelectorAll("#shp-list .shp-main")];
    const next = all[all.indexOf(from) + dir];
    if (next) next.focus();
    return Boolean(next);
  }

  function trapFocus(ev, box) {
    const f = [...box.querySelectorAll("button:not([disabled]), input")].filter((x) => x.offsetParent !== null);
    if (!f.length) return;
    if (ev.shiftKey && document.activeElement === f[0]) { ev.preventDefault(); f[f.length - 1].focus(); }
    else if (!ev.shiftKey && document.activeElement === f[f.length - 1]) { ev.preventDefault(); f[0].focus(); }
  }

  async function refreshBadge() {
    if (!S.enabled) return;
    const r = await request("GET", "/api/shopping/items");
    if (r.status === 200 && Array.isArray(r.body)) setBadge(r.body.filter((i) => !i.checked).length);
  }

  // ---------- start ----------

  let wired = false;
  window.DanaShopping = {
    show() {
      if (!wired && $("panel-shopping")) { wire(); wired = true; }
      S.visible = true;
      if (location.pathname === "/") history.replaceState(history.state, "", `/shopping${location.hash}`);
      if (S.enabled) { openStream(); load(); } else render();
    },
    hide() {
      if (!S.visible) return;
      S.visible = false;
      closeStream();
      if (S.editor) saveEditor();
      if (S.share) closeSheet();
      closeMenu(); closeUsualMenu(); hideSnack();
      if (location.pathname === "/shopping") history.replaceState(history.state, "", `/${location.hash}`);
    },
  };

  async function init() {
    const atShopping = location.pathname === "/shopping";
    if (atShopping) window.danaSwitchTab?.("shopping");
    const r = await request("GET", "/api/me");
    if (r.status !== 200) { if (atShopping) { S.status = "error"; S.error = C.errorText(r.status, r.body); render(); } return; }
    S.me = { id: r.body.id, name: r.body.name };
    S.enabled = (r.body.kinds || []).includes("shopping");
    document.querySelectorAll("[data-shopping-nav]").forEach((b) => b.classList.toggle("hidden", !S.enabled));
    if (!S.enabled) { S.status = "off"; render(); return; }
    if (S.visible) { openStream(); load(); } else refreshBadge();
    setInterval(() => { if (!S.visible && !document.hidden) refreshBadge(); }, 120000);
  }
  // After the app's scripts (static/app.js; they load before this one), which wire the tabs and open Chat.
  if (document.readyState === "complete") init();
  else document.addEventListener("DOMContentLoaded", init);
})();
