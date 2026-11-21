/* Dana — the web SDK an addon's page is written against (server/docs/writing-an-addon.md, "A web page").
 *
 * An addon declares its page in its manifest (`web=Web(panel="x.js", style="x.css", nav=Nav(...))`); the
 * shell (static/app-panels.js) lists the user's pages from GET /api/addons, loads each page's script,
 * and the script registers itself here:
 *
 *   Dana.panel({
 *     id: "weather",                the screen's name: its element is #panel-<id>, `#<id>` / `/<id>` open it
 *     title: "Weather", icon: "cloud-sun",  used where the manifest gives no Nav
 *     scripts: ["weather-core.js"], optional: files of the same web/ folder loaded first, in order
 *     start(api) {},                optional: once, as soon as the page is registered (background work: a badge)
 *     mount(el, api, arg) {},       each time the page is opened (`arg`: what api.open passed, else null);
 *                                   `el` is the page's empty <section> the first time, then the same element
 *     unmount() {},                 optional: each time the user leaves it
 *   });
 *
 * `api` is the same object every time: { addon, me, base, fetch, json, call, stream, toast, open,
 * openSession, badge, visible, icons, esc, store } (each described where it is built, `makeApi`).
 * Nothing else of the shell is a page's business: no globals, no other page's elements.
 * Works in Node too (no DOM until a page mounts): tests/test_web_panels.mjs.
 */
(() => {
  "use strict";
  const root = typeof window !== "undefined" ? window : globalThis;

  const registered = new Map();   // addon name → {addon, def, api, ready: Promise}
  const owners = new Map();       // a panel script's URL (no query) → addon name
  let shell = null;               // what the shell lends pages (Dana.attach)

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const bare = (url) => String(url || "").split(/[?#]/)[0];

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

  function makeApi(addon, panelUrl) {
    return {
      /** The addon this page belongs to. */
      addon,
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
      /** Opens a screen: a core one ("chat", "tasks", "calls", "settings", "addons", "review", "usage")
       *  or another page by its id; `arg` reaches that page's mount (calls: a call id). */
      open: (id, arg) => shell && shell.open(id, arg === undefined ? null : arg),
      /** Opens a chat session in Chat. */
      openSession: (sid) => shell && shell.openSession(sid),
      /** The page's badge in the navigation: a count (0 / null hides it; above 99 shows "99+") or a short
       *  text; tone "quiet" draws a count that is not an alert (the default is the alert colour). */
      badge: (value, tone) => shell && shell.badge(addon, value, tone || ""),
      /** Whether this page is the one on screen. */
      visible: () => Boolean(shell && shell.visible(addon)),
      /** Draws the icons (`<i data-lucide="name">`) added since. */
      icons() { try { root.lucide?.createIcons(); } catch (_) { /* ignore */ } },
      /** HTML-escapes text for a template. */
      esc,
      /** localStorage that never throws: get(key, fallback), set(key, value), remove(key). */
      store: {
        get(k, d) { try { const v = root.localStorage.getItem(k); return v === null ? (d === undefined ? null : d) : v; } catch (_) { return d === undefined ? null : d; } },
        set(k, v) { try { root.localStorage.setItem(k, String(v)); } catch (_) { /* ignore */ } },
        remove(k) { try { root.localStorage.removeItem(k); } catch (_) { /* ignore */ } },
      },
    };
  }

  /** Registers a page (see the top of this file). Called by a panel script while it runs. */
  function panel(def) {
    if (!def || typeof def.id !== "string" || !/^[a-z][a-z0-9_-]*$/.test(def.id)) throw new Error("Dana.panel: an id of lowercase letters, digits, _ or -");
    if (typeof def.mount !== "function") throw new Error(`Dana.panel(${def.id}): mount(el, api) is required`);
    const src = (typeof document !== "undefined" && document.currentScript && document.currentScript.src) || "";
    const addon = owners.get(bare(src));
    if (!addon) throw new Error(`Dana.panel(${def.id}): not loaded by the shell from an addon's web/ folder`);
    if (registered.has(addon)) throw new Error(`Dana.panel(${def.id}): ${addon} already has a page`);
    const api = makeApi(addon, src);
    const ready = (async () => {
      for (const file of def.scripts || []) await loadScript(sibling(src, file));
      if (typeof def.start === "function") {
        try { await def.start(api); } catch (e) { console.error(`[dana] ${def.id}: start failed`, e); }
      }
    })();
    const entry = { addon, def, api, ready };
    registered.set(addon, entry);
    return entry;
  }

  /** The shell: loads `addon`'s page from `url`; resolves with its registration ({def, api, ready}). */
  function load(addon, url) {
    if (registered.has(addon)) return Promise.resolve(registered.get(addon));
    const abs = absolute(url);
    owners.set(bare(abs), addon);
    return loadScript(abs).then(() => {
      if (!registered.has(addon)) throw new Error(`${url} ran but did not call Dana.panel`);
      return registered.get(addon);
    });
  }

  root.Dana = {
    panel,
    load,
    /** The shell lends pages its services: {me(), toast(text), open(id, arg), openSession(sid), badge(addon, value, tone), visible(addon)}. */
    attach(services) { shell = services; },
    /** The registration of `addon`'s page, or null. */
    registered: (addon) => registered.get(addon) || null,
    /** A test or the shell names the addon a script URL belongs to before running it. */
    expect(url, addon) { owners.set(bare(absolute(url)), addon); },
    sibling,
  };
})();
