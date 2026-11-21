/* The web app — addons' pages (static/app.js says how the app's scripts fit together).
 *
 * The shell knows no addon by name. At start it asks GET /api/me and GET /api/addons, puts a navigation
 * button for each page the user has (`panelEntries`: addons on for them whose manifest declares
 * `web=Web(panel=…)`), and loads each page's script, which registers itself with Dana.panel
 * (static/dana.js; may start background work such as a badge). Opening a page loads its style the
 * first time, gives it its own <section id="panel-<id>"> and calls its mount; leaving calls unmount.
 * `/<id>` and `#<id>…` open a page when the app loads. The Addons page announces changes with the
 * `dana:addons-changed` event and the navigation is built again.
 * The pure part (`panelEntries`) is tested by tests/test_web_panels.mjs.
 */

// ══════════════════════════════════════════════════════════════════════
// ADDON PAGES
// ══════════════════════════════════════════════════════════════════════

/** The pages of a GET /api/addons payload, in navigation order: [{addon, label, icon, order, nav, panel, style}]. */
function panelEntries(payload) {
    const out = [];
    for (const a of (payload && payload.addons) || []) {
        const web = a && a.state === "on" ? a.web : null;
        if (!web || !web.panel) continue;
        const nav = web.nav || null;
        out.push({
            addon: a.name,
            label: (nav && nav.label) || a.title || a.name,
            icon: (nav && nav.icon) || "puzzle",
            order: nav && Number.isFinite(nav.order) ? nav.order : 100,
            nav: Boolean(nav),
            panel: web.panel,
            style: web.style || null,
        });
    }
    return out.sort((x, y) => x.order - y.order || x.label.localeCompare(y.label) || x.addon.localeCompare(y.addon));
}

/** The page a URL asks for: "/weather" → "weather", "#weather/week" → "weather", else null. */
function routeOf(pathname, hash) {
    const fromHash = /^#([a-z][a-z0-9_-]*)/.exec(hash || "");
    if (fromHash) return fromHash[1];
    const fromPath = /^\/([a-z][a-z0-9_-]*)\/?$/.exec(pathname || "");
    return fromPath ? fromPath[1] : null;
}

const panelHost = {
    me: null,
    entries: [],            // panelEntries of the last /api/addons
    byId: new Map(),        // page id → addon name (once registered)
    current: null,          // the addon whose page is on screen
    styles: new Map(),      // style URL → Promise
    ready: null,            // every listed page's script has run (or failed)
};

function panelNavButton(entry, cls) {
    const b = document.createElement("button");
    b.className = cls;
    b.dataset.addon = entry.addon;
    b.title = entry.label;
    b.innerHTML = `<i data-lucide="${escapeHtml(entry.icon)}"></i><span>${escapeHtml(entry.label)}</span>`
        + `<span class="nav-badge hidden" data-badge="${escapeHtml(entry.addon)}"></span>`;
    const reg = window.Dana.registered(entry.addon);
    if (reg) b.dataset.tab = reg.def.id;
    b.addEventListener("click", () => openAddonPanel(entry.addon));
    return b;
}

function renderPanelNav() {
    const shown = panelHost.entries.filter(e => e.nav);
    const rail = document.getElementById("rail-addons");
    const bottom = document.getElementById("bottom-addons");
    if (rail) rail.replaceChildren(...shown.map(e => panelNavButton(e, "rail-btn")));
    if (bottom) bottom.replaceChildren(...shown.map(e => panelNavButton(e, "bottom-nav-btn")));
    highlightNav(activeTab);
    paintBadges();
    refreshIcons();
}

function loadPanelStyle(url) {
    if (!url) return Promise.resolve();
    if (!panelHost.styles.has(url)) {
        panelHost.styles.set(url, new Promise(resolve => {
            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = url;
            link.onload = link.onerror = () => resolve();
            document.head.appendChild(link);
            setTimeout(resolve, 4000);   // never hold a page back on a slow stylesheet
        }));
    }
    return panelHost.styles.get(url);
}

/** Loads the page of `entry` (its script); resolves with the registration, or null when it failed. */
function loadPanel(entry) {
    return window.Dana.load(entry.addon, entry.panel).then(reg => {
        const holder = panelHost.byId.get(reg.def.id);
        if (CORE_SCREENS.includes(reg.def.id) || (holder && holder !== entry.addon)) {
            throw new Error(`its page id "${reg.def.id}" is taken by ${holder || "the shell"}`);
        }
        panelHost.byId.set(reg.def.id, entry.addon);
        document.querySelectorAll(`[data-addon="${entry.addon}"]`).forEach(b => { b.dataset.tab = reg.def.id; });
        return reg;
    }).catch(err => {
        console.error(`[dana] the ${entry.addon} page did not load`, err);
        return null;
    });
}

function panelSection(reg) {
    const id = `panel-${reg.def.id}`;
    let el = document.getElementById(id);
    if (!el) {
        el = document.createElement("section");
        el.className = "screen-panel";
        el.id = id;
        el.dataset.addon = reg.addon;
        document.getElementById("main-viewport").appendChild(el);
    }
    return el;
}

/** Opens `addon`'s page (`arg` reaches its mount). */
async function openAddonPanel(addon, arg = null) {
    const entry = panelHost.entries.find(e => e.addon === addon);
    if (!entry) return false;
    const reg = await loadPanel(entry);
    if (!reg) { showAppToast(`${entry.label} could not be opened`); return false; }
    await Promise.all([reg.ready, loadPanelStyle(entry.style)]);
    panelSection(reg);
    switchTab(reg.def.id, arg);
    return true;
}

/** Opens the page whose id is `id`, once the pages have loaded; false when there is none. */
async function openPanelById(id, arg = null) {
    if (panelHost.ready) await panelHost.ready;
    const addon = panelHost.byId.get(id);
    return addon && panelHost.entries.some(e => e.addon === addon) ? openAddonPanel(addon, arg) : false;
}

/** switchTab's part for pages: unmount the one leaving, mount the one arriving. */
function switchPanels(tabId, arg) {
    const arriving = panelHost.byId.get(tabId) || null;
    if (panelHost.current && panelHost.current !== arriving) {
        const leaving = window.Dana.registered(panelHost.current);
        try { leaving?.def.unmount?.(); } catch (e) { console.error("[dana] unmount failed", e); }
    }
    panelHost.current = arriving;
    if (!arriving) return;
    const reg = window.Dana.registered(arriving);
    try { reg.def.mount(panelSection(reg), reg.api, arg === undefined ? null : arg); }
    catch (e) { console.error(`[dana] the ${arriving} page failed to open`, e); }
}

/** The text of a navigation badge: "" hides it. */
function badgeText(value) {
    if (value === null || value === undefined || value === 0 || value === "") return "";
    return typeof value === "number" ? (value > 99 ? "99+" : String(value)) : String(value);
}

const panelBadges = new Map();   // addon → {text, tone}: kept so a rebuilt navigation shows them again
function setPanelBadge(addon, value, tone = "") {
    panelBadges.set(addon, { text: badgeText(value), tone });
    paintBadges();
}

function paintBadges() {
    document.querySelectorAll("[data-badge]").forEach(b => {
        const { text, tone } = panelBadges.get(b.dataset.badge) || { text: "", tone: "" };
        b.textContent = text;
        b.dataset.tone = tone;
        b.classList.toggle("hidden", !text);
    });
}

let appToastTimer = null;
function showAppToast(text) {
    const el = document.getElementById("app-toast");
    if (!el) return;
    el.textContent = text;
    el.classList.remove("hidden");
    clearTimeout(appToastTimer);
    appToastTimer = setTimeout(() => el.classList.add("hidden"), 4000);
}

async function fetchJson(path) {
    try {
        const r = await fetch(path, { cache: "no-store", credentials: "same-origin" });
        return r.ok ? await r.json() : null;
    } catch (_) {
        return null;
    }
}

/** Asks the server for the user's pages and builds the navigation; loads every listed page's script.
 *  `panelHost.ready` settles once that is done (openPanelById waits for it). */
function refreshPanels() {
    panelHost.ready = (async () => {
        const [me, addons] = await Promise.all([fetchJson("/api/me"), fetchJson("/api/addons")]);
        if (me) panelHost.me = me;
        if (!addons) return;
        panelHost.entries = panelEntries(addons);
        renderPanelNav();
        await Promise.all(panelHost.entries.map(loadPanel));
        // A page the user no longer has stays registered (scripts cannot be unloaded) but leaves the screen.
        if (panelHost.current && !panelHost.entries.some(e => e.addon === panelHost.current)) switchTab("chat");
    })();
    return panelHost.ready;
}

window.Dana.attach({
    me: () => panelHost.me,
    toast: showAppToast,
    open: (id, arg) => openScreen(id, arg),
    openSession: (sid) => window.danaOpenSession(sid),
    badge: setPanelBadge,
    visible: (addon) => panelHost.current === addon,
});

document.addEventListener("dana:addons-changed", () => { refreshPanels(); });

async function startPanels() {
    await refreshPanels();
    const wanted = routeOf(location.pathname, location.hash);
    if (wanted && !CORE_SCREENS.includes(wanted)) await openPanelById(wanted);
}
