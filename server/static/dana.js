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
 *   });
 *
 * `api` is the same object every time: { addon, page, me, base, fetch, json, call, stream, toast, open,
 * openSession, badge, visible, piece, icons, esc, store, motion } (each described where it is built, `makeApi`;
 * `motion` is `Dana.motion`, below).
 * Nothing else of the shell is a page's business: no globals, no other page's elements.
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
      /** Opens a screen: a core one ("chat", "settings", "addons", "review", "usage") or a page by its id
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

  root.Dana = {
    panel,
    load,
    /** The shell lends pages its services: {me(), toast(text), open(id, arg), openSession(sid),
     *  badge(page, value, tone), visible(page), piece(name, el)}. */
    attach(services) { shell = services; },
    /** The registration of the page `page`, or null. */
    registered: (page) => registered.get(page) || null,
    /** A test or the shell names the page a script URL belongs to before running it. */
    expect(url, addon, page) { owners.set(bare(absolute(url)), { addon, page }); },
    sibling,
    motion,
    store,
  };
})();
