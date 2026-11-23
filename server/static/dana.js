/* Dana — the web SDK an addon's page is written against (server/docs/writing-an-addon.md, "Pages").
 *
 * An addon declares its pages in its manifest (`web=(Page("weather", panel="x.js", style="x.css",
 * nav=Nav(...)), …)`: the page id names the screen, its element is #panel-<id>, `#<id>` / `/<id>` open it);
 * the shell (static/app-panels.js) lists the user's pages from GET /api/addons, loads each page's script,
 * and the script registers itself here (the SDK knows which page a script is from the URL it loaded):
 *
 *   Dana.panel({
 *     scripts: ["weather-core.js"], optional: files of the same web/ folder loaded first, in order
 *     start(api) {},                optional: once, as soon as the page is registered (background work: a badge)
 *     mount(el, api, arg) {},       each time the page is opened (`arg`: what api.open passed, else null);
 *                                   `el` is the page's empty <section> the first time, then the same element
 *     unmount() {},                 optional: each time the user leaves it
 *     opens: ["task"],              optional: the kinds of things this page opens (a Now card's or day
 *                                   item's ref type); the shell opens the page with the thing's id as `arg`
 *   });
 *
 * `api` is the same object every time: { addon, page, me, base, fetch, json, call, stream, toast, open,
 * openSession, badge, visible, piece, icons, esc, store, motion, menu } (each described where it is built, `makeApi`;
 * `motion` is `Dana.motion`, below).
 * Nothing else of the shell is a page's business: no globals, no other page's elements.
 *
 * A dashboard widget (`@addon.widget("<key>", script="widget.js", …)`, server/docs/dashboard.md) is drawn by a
 * script of the addon's web/ folder that the dashboard page (static/dashboard.js) loads; it registers each widget
 * it draws:
 *
 *   Dana.widget("<key>", {
 *     style: "widget.css",          optional: a stylesheet of the same web/ folder, loaded once
 *     bare: true,                   optional: no title line above it (the clock)
 *     mount(el, w, api) {},         draws it into its empty element; w = {id, widget, size, settings, data}
 *     update(el, w, api) {},        optional: new data (w.data) for it; default: emptied and mounted again
 *     unmount(el, api) {},          optional: before it leaves the page (stop timers, close streams)
 *   });
 *
 * Its `api` is a page's (above) plus: size, settings, refresh() (reads its data again and calls update),
 * night() (whether the dashboard is in night mode), kiosk (window.DanaKiosk inside the kiosk app, else null),
 * setValue(el, text) (sets a value; one that changed crossfades in: the dashboard's only motion).
 * Works in Node too (no DOM until a page mounts): tests/test_web_panels.mjs.
 */
(() => {
  "use strict";
  const root = typeof window !== "undefined" ? window : globalThis;

  const registered = new Map();   // page id → {addon, page, def, api, ready: Promise}
  const owners = new Map();       // a panel script's URL (no query) → {addon, page}
  let shell = null;               // what the shell lends pages (Dana.attach)

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const bare = (url) => String(url || "").split(/[?#]/)[0];

  /** localStorage that never throws: get(key, fallback), set(key, value), remove(key). Pages get it as
   *  api.store; the shell keeps its own per-browser choices with it too (the navigation's places). */
  const store = {
    get(k, d) { try { const v = root.localStorage.getItem(k); return v === null ? (d === undefined ? null : d) : v; } catch (_) { return d === undefined ? null : d; } },
    set(k, v) { try { root.localStorage.setItem(k, String(v)); } catch (_) { /* ignore */ } },
    remove(k) { try { root.localStorage.removeItem(k); } catch (_) { /* ignore */ } },
  };

  /** The absolute URL of `url` (relative to the page). */
  function absolute(url) {
    try { return new URL(url, root.location ? root.location.href : "http://localhost/").href; } catch (_) { return String(url); }
  }

  /** `file` next to `panelUrl`, with the panel URL's query (its version) carried over. */
  function sibling(panelUrl, file) {
    const query = String(panelUrl).includes("?") ? "?" + String(panelUrl).split("?")[1] : "";
    return absolute(bare(panelUrl).replace(/[^/]*$/, "") + file + query);
  }

  /** Loads a classic script once; resolves when it has run. */
  const loadedScripts = new Map();
  function loadScript(url) {
    if (!loadedScripts.has(url)) {
      loadedScripts.set(url, new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = url;
        s.onload = () => resolve();
        s.onerror = () => { loadedScripts.delete(url); reject(new Error(`could not load ${url}`)); };
        document.head.appendChild(s);
      }));
    }
    return loadedScripts.get(url);
  }

  async function request(method, path, body, extra) {
    const init = { method, cache: "no-store", credentials: "same-origin", headers: {}, ...(extra || {}) };
    if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    let r;
    try { r = await root.fetch(path, init); } catch (_) { return { status: 0, body: null }; }
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    return { status: r.status, body: data };
  }

  /* Motion the shell and pages share (server/docs/web-design.md): the CSS lives in styles.css (.dn-*), on
   * opacity and transform only; with reduced motion each one is instant. */
  // Nothing animates where nobody sees it: a hidden page (a background tab) gets every change at once.
  const hidden = () => Boolean(root.document && root.document.hidden);
  const reduced = () => hidden() || Boolean(root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const later = (fn, ms) => root.setTimeout(fn, ms);
  const frame = (fn) => (root.requestAnimationFrame && !hidden() ? root.requestAnimationFrame(fn) : later(fn, 16));
  const motion = {
    /** Whether motion is off: the user asked for reduced motion, or the page is hidden. */
    reduced,
    /** Rows arriving: elements added inside `el` during the next `ms` rise in, the first eight staggered.
     *  Call it on a screen's first load only, never on a refresh. */
    arrive(el, ms = 1200) {
      if (!el || !el.classList || reduced()) return;
      el.classList.add("dn-arrive");
      later(() => el.classList.remove("dn-arrive"), ms);
    },
    /** Data that just updated: a brief accent tint over `el` that fades out (~600 ms). */
    flash(el) {
      if (!el || !el.classList || reduced()) return;
      el.classList.remove("dn-flash");
      frame(() => frame(() => {
        el.classList.add("dn-flash");
        later(() => el.classList.remove("dn-flash"), 700);
      }));
    },
    /** Completing a row: `.dn-completing` (the page draws its check and strike-through), then the row fades
     *  and collapses; resolves when it is gone (~400 ms), at once with reduced motion. */
    complete(el) {
      if (!el || !el.classList || reduced()) return Promise.resolve();
      el.classList.add("dn-completing");
      return new Promise((resolve) => {
        later(() => {
          el.style.height = `${el.offsetHeight}px`;
          frame(() => { el.classList.add("dn-collapse"); later(resolve, 200); });
        }, 220);
      });
    },
    /** A number that changed: sets `el`'s text and rolls the new value in (no counting loop). */
    number(el, text) {
      if (!el) return;
      const next = String(text);
      if (el.textContent === next) return;
      el.textContent = next;
      if (reduced() || !el.classList) return;
      el.classList.remove("dn-roll");
      frame(() => frame(() => el.classList.add("dn-roll")));
    },
  };

  /* The overflow menu ("⋯"): a row shows one action and keeps the rest here (server/docs/web-design.md, "One
   * visible action"). A popup of buttons anchored to the button that opened it: role="menu", arrows / Home /
   * End move, Enter or Space picks, Escape, Tab or a click outside closes, and the keyboard goes back to the
   * button. One menu at a time. */
  let openMenu = null;   // {el, anchor, close}

  /** Opens the menu of `items` ({label, onSelect, disabled, hint, danger, icon}; null draws a line) under (or
   *  above, when there is no room) `anchor`, end-aligned; again on the same anchor closes it. */
  function menu(anchor, items, opts) {
    const doc = root.document;
    if (!doc || !anchor) return;
    const wasOpen = openMenu && openMenu.anchor === anchor;
    if (openMenu) openMenu.close(false);
    if (wasOpen) return;
    const o = opts || {};
    const el = doc.createElement("div");
    el.className = "dn-menu";
    el.setAttribute("role", "menu");
    if (o.label) el.setAttribute("aria-label", o.label);
    const buttons = [];
    for (const item of items || []) {
      if (!item) { const hr = doc.createElement("div"); hr.className = "dn-menu-sep"; hr.setAttribute("role", "separator"); el.appendChild(hr); continue; }
      const b = doc.createElement("button");
      b.type = "button";
      b.className = "dn-menu-item" + (item.danger ? " danger" : "");
      b.setAttribute("role", "menuitem");
      b.tabIndex = -1;
      if (item.disabled) b.setAttribute("aria-disabled", "true");
      b.innerHTML = (item.icon ? `<i data-lucide="${esc(item.icon)}" aria-hidden="true"></i>` : "")
        + `<span class="dn-menu-label" dir="auto">${esc(item.label)}</span>`
        + (item.hint ? `<span class="dn-menu-hint">${esc(item.hint)}</span>` : "");
      b.addEventListener("click", () => {
        if (item.disabled) return;
        close(true);
        if (typeof item.onSelect === "function") item.onSelect();
      });
      el.appendChild(b);
      buttons.push(b);
    }
    if (!buttons.length) return;
    doc.body.appendChild(el);
    try { root.lucide?.createIcons(); } catch (_) { /* ignore */ }
    // Place it: under the anchor, its end edge on the anchor's; above when it would leave the window.
    const a = anchor.getBoundingClientRect();
    const m = el.getBoundingClientRect();
    const rtl = root.getComputedStyle(anchor).direction === "rtl";
    const vw = root.innerWidth, vh = root.innerHeight;
    let left = rtl ? a.left : a.right - m.width;
    left = Math.max(8, Math.min(left, vw - m.width - 8));
    let top = a.bottom + 4;
    if (top + m.height > vh - 8 && a.top - m.height - 4 >= 8) top = a.top - m.height - 4;
    el.style.left = `${Math.round(left)}px`;
    el.style.top = `${Math.round(Math.max(8, top))}px`;
    if (!reduced()) el.classList.add("dn-menu-in");
    anchor.setAttribute("aria-expanded", "true");
    const focusAt = (i) => { const n = buttons.length; buttons[((i % n) + n) % n].focus(); };
    focusAt(0);
    const onKey = (e) => {
      const i = buttons.indexOf(doc.activeElement);
      if (e.key === "ArrowDown") focusAt(i + 1);
      else if (e.key === "ArrowUp") focusAt(i < 0 ? -1 : i - 1);
      else if (e.key === "Home") focusAt(0);
      else if (e.key === "End") focusAt(-1);
      else if (e.key === "Escape") close(true);
      else if (e.key === "Tab") { close(false); return; }
      else if (e.key !== "Enter" && e.key !== " ") { e.stopPropagation(); return; }
      else return;   // Enter / Space: the button's own click
      e.preventDefault();
      e.stopPropagation();
    };
    const onDown = (e) => { if (!el.contains(e.target) && !anchor.contains(e.target)) close(false); };
    const onAway = (e) => { if (!e || !e.target || !el.contains(e.target)) close(false); };
    el.addEventListener("keydown", onKey);
    doc.addEventListener("pointerdown", onDown, true);
    root.addEventListener("resize", onAway);
    root.addEventListener("scroll", onAway, true);
    function close(refocus) {
      if (!openMenu || openMenu.el !== el) return;
      openMenu = null;
      el.remove();
      doc.removeEventListener("pointerdown", onDown, true);
      root.removeEventListener("resize", onAway);
      root.removeEventListener("scroll", onAway, true);
      anchor.setAttribute("aria-expanded", "false");
      if (refocus && anchor.isConnected) anchor.focus();
    }
    openMenu = { el, anchor, close };
  }

  function makeApi(addon, page, panelUrl) {
    return {
      /** The addon this page belongs to. */
      addon,
      /** This page's id (the manifest's Page id). */
      page,
      /** Who is signed in (GET /api/me: id, name, access, capabilities, addons, languages, currency). */
      get me() { return shell ? shell.me() : null; },
      /** The URL of the page's web/ folder (ends with "/"). */
      base: bare(panelUrl).replace(/[^/]*$/, ""),
      /** fetch(), signed in like the page (the session cookie), never answered from a cache. */
      fetch: (path, init) => root.fetch(path, { cache: "no-store", credentials: "same-origin", ...(init || {}) }),
      /** A JSON request that never throws: {status, body} (status 0 = no answer). */
      json: (method, path, body) => request(method, path, body),
      /** A JSON request that throws Error(error code or "HTTP n") with `.status` and `.body` when not 2xx. */
      async call(path, opts) {
        const o = opts || {};
        const method = o.method || (o.body !== undefined ? "POST" : "GET");
        const r = await request(method, path, o.body);
        if (r.status < 200 || r.status >= 300) {
          const e = new Error((r.body && (r.body.message || r.body.error)) || (r.status ? `HTTP ${r.status}` : "No connection"));
          e.status = r.status; e.body = r.body;
          throw e;
        }
        return r.body;
      },
      /** A live stream (Server-Sent Events): {event: (data, ev) => …}; returns {close()}. */
      stream(path, handlers) {
        const es = new root.EventSource(path);
        for (const [name, fn] of Object.entries(handlers || {})) {
          es.addEventListener(name, (ev) => { let d = null; try { d = JSON.parse(ev.data); } catch (_) { d = ev.data; } fn(d, ev); });
        }
        return { close: () => es.close(), source: es };
      },
      /** A short message at the bottom of the screen. */
      toast: (text) => shell && shell.toast(text),
      /** Opens a screen: a core one ("now", "chat", "settings", "addons", "review", "usage") or a page by its id
       *  (its own addon's or another's); `arg` reaches that page's mount. Resolves false when there is none. */
      open: (id, arg) => shell && shell.open(id, arg === undefined ? null : arg),
      /** Opens a chat session in Chat. */
      openSession: (sid) => shell && shell.openSession(sid),
      /** The page's badge in the navigation: a count (0 / null hides it; above 99 shows "99+") or a short
       *  text; tone "quiet" draws a count that is not an alert (the default is the alert colour). */
      badge: (value, tone) => shell && shell.badge(page, value, tone || ""),
      /** Whether this page is the one on screen. */
      visible: () => Boolean(shell && shell.visible(page)),
      /** Draws a core piece into an element of the page, which keeps it up to date there: "today" (the
       *  morning briefing and the weekly-review banner). False for a piece this shell does not have. */
      piece: (name, el) => Boolean(shell && shell.piece(name, el)),
      /** Draws the icons (`<i data-lucide="name">`) added since. */
      icons() { try { root.lucide?.createIcons(); } catch (_) { /* ignore */ } },
      /** HTML-escapes text for a template. */
      esc,
      /** localStorage that never throws: get(key, fallback), set(key, value), remove(key). */
      store,
      /** Shared motion: arrive(el), flash(el), complete(el) → Promise, number(el, text), reduced(). */
      motion,
      /** The overflow menu: menu(button, [{label, onSelect, disabled, hint, danger, icon} | null]) opens the
       *  row's other actions under its "⋯" button (give the button aria-haspopup="menu" aria-expanded="false"). */
      menu,
    };
  }

  /** Registers a page (see the top of this file). Called by a panel script while it runs. */
  function panel(def) {
    if (!def || typeof def.mount !== "function") throw new Error("Dana.panel: mount(el, api, arg) is required");
    const src = (typeof document !== "undefined" && document.currentScript && document.currentScript.src) || "";
    const owner = owners.get(bare(src));
    if (!owner) throw new Error("Dana.panel: not loaded by the shell from an addon's web/ folder");
    if (registered.has(owner.page)) throw new Error(`Dana.panel: the ${owner.page} page is already registered`);
    const api = makeApi(owner.addon, owner.page, src);
    const ready = (async () => {
      for (const file of def.scripts || []) await loadScript(sibling(src, file));
      if (typeof def.start === "function") {
        try { await def.start(api); } catch (e) { console.error(`[dana] ${owner.page}: start failed`, e); }
      }
    })();
    const entry = { addon: owner.addon, page: owner.page, def, api, ready };
    registered.set(owner.page, entry);
    return entry;
  }

  /** The shell: loads page `page` of `addon` from `url`; resolves with its registration ({def, api, ready}). */
  function load(addon, page, url) {
    if (registered.has(page)) return Promise.resolve(registered.get(page));
    const abs = absolute(url);
    owners.set(bare(abs), { addon, page });
    return loadScript(abs).then(() => {
      if (!registered.has(page)) throw new Error(`${url} ran but did not call Dana.panel`);
      return registered.get(page);
    });
  }

  const widgets = new Map();      // widget key → {addon, def, url}

  /** Registers a dashboard widget's renderer (see the top of this file). Called by a widget script while it runs. */
  function widget(key, def) {
    if (!def || typeof def.mount !== "function") throw new Error("Dana.widget: mount(el, w, api) is required");
    const src = (typeof document !== "undefined" && document.currentScript && document.currentScript.src) || "";
    const found = /\/addons\/([A-Za-z0-9_]+)\/web\//.exec(bare(src));
    if (!found) throw new Error("Dana.widget: not loaded from an addon's web/ folder");
    if (widgets.has(key)) throw new Error(`Dana.widget: the ${key} widget is already registered`);
    widgets.set(key, { addon: found[1], def, url: src });
  }

  root.Dana = {
    panel,
    load,
    widget,
    /** A widget's registration ({addon, def, url}), or null. */
    widgetDef: (key) => widgets.get(key) || null,
    /** The api a widget of `addon` (its script at `url`) gets: a page's, plus `extra`. */
    widgetApi: (addon, url, extra) => Object.assign(makeApi(addon, "dashboard", url), extra || {}),
    /** Loads a classic script once; resolves when it has run. */
    loadScript,
    /** The shell lends pages its services: {me(), toast(text), open(id, arg), openSession(sid),
     *  badge(page, value, tone), visible(page), piece(name, el)}. */
    attach(services) { shell = services; },
    /** The registration of the page `page`, or null. */
    registered: (page) => registered.get(page) || null,
    /** A test or the shell names the page a script URL belongs to before running it. */
    expect(url, addon, page) { owners.set(bare(absolute(url)), { addon, page }); },
    sibling,
    motion,
    menu,
    store,
  };
})();
