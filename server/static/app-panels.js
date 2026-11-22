/* The web app — addons' pages (static/app.js says how the app's scripts fit together).
 *
 * The shell knows no addon by name. At start it asks GET /api/me and GET /api/addons, gives the navigation
 * (app-nav.js) a place for each page the user has (`panelEntries`: every `Page` with a `Nav` in the
 * `web=(…)` of the addons on for them), and loads each page's script, which registers itself with Dana.panel (static/dana.js; may start
 * background work such as a badge). Opening a page loads its style the first time, gives it its own
 * <section id="panel-<id>"> and calls its mount; leaving calls unmount. `/<id>` and `#<id>…` open a page
 * when the app loads. The Addons page announces changes with the `dana:addons-changed` event and the
 * navigation is built again.
 * The pure part (`panelEntries`, `routeOf`, `badgeText`) is tested by tests/test_web_panels.mjs.
 */

// ══════════════════════════════════════════════════════════════════════
// ADDON PAGES
// ══════════════════════════════════════════════════════════════════════

/** The pages of a GET /api/addons payload, in navigation order: [{addon, id, label, icon, order, nav, primary, panel, style}]. */
function panelEntries(payload) {
    const out = [];
    for (const a of (payload && payload.addons) || []) {
        if (!a || a.state !== "on" || !Array.isArray(a.web)) continue;
        for (const page of a.web) {
            if (!page || !page.id || !page.panel) continue;
            const nav = page.nav || null;
            out.push({
                addon: a.name,
                id: page.id,
                label: (nav && nav.label) || a.title || a.name,
                icon: (nav && nav.icon) || "puzzle",
                order: nav && Number.isFinite(nav.order) ? nav.order : 100,
                nav: Boolean(nav),
                primary: Boolean(nav && nav.primary),
                panel: page.panel,
                style: page.style || null,
            });
        }
    }
    return out.sort((x, y) => x.order - y.order || x.label.localeCompare(y.label) || x.id.localeCompare(y.id));
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
    current: null,          // the id of the page on screen
    styles: new Map(),      // style URL → Promise
    ready: null,            // every listed page's script has run (or failed)
};

const pageEntry = (id) => panelHost.entries.find(e => e.id === id) || null;

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
    return window.Dana.load(entry.addon, entry.id, entry.panel).catch(err => {
        console.error(`[dana] the ${entry.id} page (${entry.addon}) did not load`, err);
        return null;
    });
}

function panelSection(entry) {
    const id = `panel-${entry.id}`;
    let el = document.getElementById(id);
    if (!el) {
        el = document.createElement("section");
        el.className = "screen-panel";
        el.id = id;
        el.dataset.addon = entry.addon;
        document.getElementById("main-viewport").appendChild(el);
    }
    return el;
}

/** Opens the page `id` (`arg` reaches its mount); false when the user has no such page. */
async function openPage(id, arg = null) {
    const entry = pageEntry(id);
    if (!entry) return false;
    const reg = await loadPanel(entry);
    if (!reg) { showAppToast(`${entry.label} could not be opened`); return false; }
    await Promise.all([reg.ready, loadPanelStyle(entry.style)]);
    panelSection(entry);
    switchTab(id, arg);
    return true;
}

/** Opens the page whose id is `id`, once the pages have loaded; false when there is none. */
async function openPanelById(id, arg = null) {
    if (panelHost.ready) await panelHost.ready;
    return openPage(id, arg);
}

/** switchTab's part for pages: unmount the one leaving, mount the one arriving. */
function switchPanels(tabId, arg) {
    const reg = pageEntry(tabId) ? window.Dana.registered(tabId) : null;
    const arriving = reg ? tabId : null;
    if (panelHost.current && panelHost.current !== arriving) {
        const leaving = window.Dana.registered(panelHost.current);
        try { leaving?.def.unmount?.(); } catch (e) { console.error("[dana] unmount failed", e); }
    }
    panelHost.current = arriving;
    if (!reg) return;
    try { reg.def.mount(panelSection(pageEntry(tabId)), reg.api, arg === undefined ? null : arg); }
    catch (e) { console.error(`[dana] the ${tabId} page failed to open`, e); }
}

/** The text of a navigation badge: "" hides it. */
function badgeText(value) {
    if (value === null || value === undefined || value === 0 || value === "") return "";
    return typeof value === "number" ? (value > 99 ? "99+" : String(value)) : String(value);
}

const panelBadges = new Map();   // page id → {text, tone}: kept so a rebuilt navigation shows them again
function setPanelBadge(page, value, tone = "") {
    panelBadges.set(page, { text: badgeText(value), tone });
    paintBadges();
}

function paintBadges() {
    document.querySelectorAll("[data-badge]").forEach(b => {
        const { text, tone } = panelBadges.get(b.dataset.badge) || { text: "", tone: "" };
        window.Dana.motion.number(b, text);   // a changed count rolls in
        b.dataset.tone = tone;
        b.classList.toggle("hidden", !text);
    });
    paintMoreBadges(panelBadges);   // the More buttons add up the badges under them (app-nav.js)
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
        if (addons) panelHost.entries = panelEntries(addons);
        setNavPlaces(panelHost.entries, panelHost.me);   // the navigation's places (app-nav.js)
        if (!addons) return;
        await Promise.all(panelHost.entries.map(loadPanel));
        // A page the user no longer has stays registered (scripts cannot be unloaded) but leaves the screen.
        if (panelHost.current && !pageEntry(panelHost.current)) switchTab("chat");
    })();
    return panelHost.ready;
}

window.Dana.attach({
    me: () => panelHost.me,
    toast: showAppToast,
    open: (id, arg) => openScreen(id, arg),
    openSession: (sid) => window.danaOpenSession(sid),
    badge: setPanelBadge,
    visible: (page) => panelHost.current === page,
    piece: (name, el) => {
        const piece = Object.prototype.hasOwnProperty.call(CORE_PIECES, name) ? CORE_PIECES[name] : null;
        if (!piece || !el) return false;
        piece(el);
        return true;
    },
});

document.addEventListener("dana:addons-changed", () => { refreshPanels(); });

async function startPanels() {
    await refreshPanels();
    const wanted = routeOf(location.pathname, location.hash);
    if (wanted && !CORE_SCREENS.includes(wanted)) await openPanelById(wanted);
}
