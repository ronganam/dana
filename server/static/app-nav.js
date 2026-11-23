/* The web app — navigation (static/app.js says how the app's scripts fit together).
 *
 * The rail (wide) and the bottom bar (narrow), drawn from the places (app-places.js: which are primary,
 * the rest, the core group, what fits): the phone bar holds the primary places and More; the rail the
 * primary places, the rest while they fit, More when they do not, and the core group at its foot. More
 * opens #nav-more, a dialog (a bottom sheet on a phone, a flyout beside the rail) with the places under it
 * and Edit, where the user pins, unpins and orders the primary places (kept per browser and user with
 * Dana.store). Then switchTab (which screen shows, each core screen's load, the addon pages' mount /
 * unmount through app-panels.js) and the hooks the self-contained core screens call:
 * window.danaSwitchTab (a core screen or an addon page by id), window.danaBack (the screen before) and
 * window.danaOpenSession. Addon pages reach the same through their Dana api (static/dana.js).
 * server/docs/web-design.md, "Navigation".
 */

// ══════════════════════════════════════════════════════════════════════
// NAVIGATION CONTROLLER (Rail + Bottom Nav + More)
// ══════════════════════════════════════════════════════════════════════

// The shell's own screens (core/addons/hooks.py, CORE_SCREENS: no addon page may take one of these ids);
// every other screen is an addon's page (app-panels.js).
const CORE_SCREENS = ["now", "chat", "settings", "attention", "addons", "devices", "household", "usage", "debug", "review"];

// Core pieces an addon page can host in an element of its own (api.piece(name, el), static/dana.js).
const CORE_PIECES = {
    // The morning briefing and the weekly-review banner (static/review.js).
    today: (el) => window.DanaToday?.show(el),
};

let previousTab = null;
const visitedScreens = new Set();   // screens opened once: their rows rise in only the first time

// The lead places (Now, the screen the app opens on, then Chat), and the core group (the shell's screens
// and the dashboard link), quieter, last.
const NAV_LEAD = [{ id: "now", label: "Now", icon: "sunrise", order: -2 }, { id: "chat", label: "Chat", icon: "message-square", order: -1 }];
function coreNavPlaces(me) {
    const can = (c) => Boolean(me && Array.isArray(me.capabilities) && me.capabilities.includes(c));
    return [
        { id: "settings", label: "Settings", icon: "settings" },
        { id: "attention", label: "Needs attention", icon: "life-buoy" },
        { id: "addons", label: "Addons", icon: "plug" },
        { id: "devices", label: "Devices", icon: "smartphone" },
        ...(can("manage_users") ? [{ id: "household", label: "Household", icon: "users" }] : []),
        { id: "usage", label: "Usage", icon: "receipt" },
        ...(can("manage_server") ? [{ id: "debug", label: "Debug", icon: "terminal" }] : []),
        { id: "dash", label: "Dash", icon: "layout-dashboard", href: "/dashboard" },
    ];
}

// Rail row heights in px, as styles.css draws them (a row with its gap, a core row, More, Edit, the
// divider, the line between the primary places and the rest).
const RAIL_SIZE = { item: 56, coreItem: 44, more: 56, edit: 40, divider: 21, sep: 9 };

const nav = {
    me: null,
    places: navPlaces(NAV_LEAD, [], coreNavPlaces(null)),
    primary: [],                      // the primary ids
    layout: { primary: [], rest: [], core: [] },
    plan: { rest: 0, core: true, more: false },
    hidden: { phone: [], rail: [] },  // the ids under each More button
    order: [],                        // every id in navigation order (the direction a screen slides in)
    sheet: null,                      // the open More dialog: {mode: "phone" | "rail", editing, opener}
};

const navChoiceKey = () => "dana_nav_places" + (nav.me && nav.me.id ? `_${nav.me.id}` : "");
// A choice saved before Now was a place gets Now in front once (its last place moves to the rest when the
// bar is full); removed again afterwards, it stays removed.
function savedNavChoice() {
    const ids = parseChoice(window.Dana.store.get(navChoiceKey()));
    const met = navChoiceKey() + "_now";
    if (!ids || ids.includes("now") || window.Dana.store.get(met)) return ids;
    const next = ["now", ...ids].slice(0, NAV_MAX_PRIMARY);
    window.Dana.store.set(navChoiceKey(), JSON.stringify(next));
    window.Dana.store.set(met, "1");
    return next;
}
function saveNavChoice(ids) {
    if (ids) { window.Dana.store.set(navChoiceKey(), JSON.stringify(ids)); window.Dana.store.set(navChoiceKey() + "_now", "1"); }
    else window.Dana.store.remove(navChoiceKey());
}

/** The places change (the user's pages, who they are): draw the navigation again. */
function setNavPlaces(entries, me) {
    if (me) nav.me = me;
    nav.places = navPlaces(NAV_LEAD, entries, coreNavPlaces(nav.me));
    renderNav();
}

function renderNav() {
    nav.primary = applyChoice(nav.places.main, savedNavChoice());
    nav.layout = navLayout(nav.places, nav.primary);
    nav.order = [...nav.layout.primary, ...nav.layout.rest, ...nav.layout.core].map(p => p.id);
    renderBottomNav();
    renderRail();
    if (nav.sheet) renderNavSheet();
    afterNavDrawn();
}

function afterNavDrawn() {
    highlightNav(activeTab);
    if (typeof paintBadges === "function") paintBadges();
    refreshIcons();
}

function navButton(place, cls) {
    const el = document.createElement(place.href ? "a" : "button");
    el.className = cls;
    if (place.href) el.href = place.href;
    else { el.type = "button"; el.dataset.tab = place.id; }
    el.title = place.label;
    el.innerHTML = `<i data-lucide="${escapeHtml(place.icon)}" aria-hidden="true"></i>`
        + `<span class="nav-label">${escapeHtml(place.label)}</span>`
        + `<span class="nav-badge hidden" data-badge="${escapeHtml(place.id)}"></span>`;
    return el;
}

function moreButton(cls, mode) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `${cls} nav-more-btn`;
    b.id = mode === "phone" ? "bottom-more" : "rail-more";
    b.title = "More places";
    b.setAttribute("aria-haspopup", "dialog");
    b.setAttribute("aria-controls", "nav-more");
    b.setAttribute("aria-expanded", String(Boolean(nav.sheet && nav.sheet.mode === mode)));
    b.innerHTML = `<i data-lucide="layout-grid" aria-hidden="true"></i><span class="nav-label">More</span>`
        + `<span class="nav-badge hidden" data-more-badge></span>`;
    b.addEventListener("click", () => openNavSheet(mode, false));
    return b;
}

function renderBottomNav() {
    const bar = document.getElementById("bottom-nav");
    if (!bar) return;
    bar.replaceChildren(...nav.layout.primary.map(p => navButton(p, "bottom-nav-btn")), moreButton("bottom-nav-btn", "phone"));
}

/** The rail's height for places below the brand, or null while it is not shown (a phone). */
function railSpace() {
    const rail = document.getElementById("nav-rail");
    const list = document.getElementById("rail-places");
    if (!rail || !list || !rail.clientHeight || getComputedStyle(rail).display === "none") return null;
    return rail.clientHeight - list.offsetTop - parseFloat(getComputedStyle(rail).paddingBottom || "0") - RAIL_SIZE.sep;
}

/** Draws the rail; with `onlyIfChanged`, only when what fits changed. True when it drew. */
function renderRail(onlyIfChanged = false) {
    const list = document.getElementById("rail-places");
    const core = document.getElementById("rail-core");
    if (!list || !core) return false;
    const L = nav.layout;
    const avail = railSpace();
    const plan = avail === null ? { rest: L.rest.length, core: true, more: false }
        : railPlan({ primary: L.primary.length, rest: L.rest.length, core: L.core.length }, avail, RAIL_SIZE);
    if (onlyIfChanged && JSON.stringify(plan) === JSON.stringify(nav.plan)) return false;
    nav.plan = plan;
    nav.hidden = hiddenPlaces(L, nav.plan);
    const items = L.primary.map(p => navButton(p, "rail-btn"));
    const rest = L.rest.slice(0, nav.plan.rest);
    if (items.length && (rest.length || nav.plan.more)) {
        const sep = document.createElement("div");
        sep.className = "rail-sep";
        items.push(sep);
    }
    items.push(...rest.map(p => navButton(p, "rail-btn")));
    if (nav.plan.more) items.push(moreButton("rail-btn", "rail"));
    else {
        const edit = document.createElement("button");
        edit.type = "button";
        edit.className = "rail-btn rail-edit";
        edit.id = "rail-edit";
        edit.title = "Edit places";
        edit.setAttribute("aria-label", "Edit places");
        edit.setAttribute("aria-haspopup", "dialog");
        edit.setAttribute("aria-controls", "nav-more");
        edit.setAttribute("aria-expanded", String(Boolean(nav.sheet && nav.sheet.mode === "rail")));
        edit.innerHTML = `<i data-lucide="sliders-horizontal" aria-hidden="true"></i>`;
        edit.addEventListener("click", () => openNavSheet("rail", true));
        items.push(edit);
    }
    list.replaceChildren(...items);
    core.replaceChildren(...(nav.plan.core ? L.core.map(p => navButton(p, "rail-btn rail-core-btn")) : []));
    core.hidden = !nav.plan.core;
    const divider = document.querySelector("#nav-rail .rail-divider");
    if (divider) divider.hidden = !nav.plan.core;
    return true;
}

// The rail fits its places again when its height changes (a window resized, phone ↔ wide).
(() => {
    const rail = document.getElementById("nav-rail");
    if (!rail || typeof ResizeObserver !== "function") return;
    let pending = false, lastHeight = -1;
    new ResizeObserver(() => {
        if (pending || rail.clientHeight === lastHeight) return;
        pending = true;
        requestAnimationFrame(() => {
            pending = false;
            lastHeight = rail.clientHeight;
            if (!renderRail(true)) return;
            if (nav.sheet && nav.sheet.mode === "rail") closeNavSheet(false);
            afterNavDrawn();
        });
    }).observe(rail);
})();

/** A screen's place in the navigation; -1 when it has none. */
function navIndex(tabId) {
    return nav.order.indexOf(tabId);
}

function highlightNav(tabId) {
    document.querySelectorAll("#nav-rail [data-tab], #bottom-nav [data-tab], #nav-more [data-tab]").forEach(btn => {
        const on = btn.getAttribute("data-tab") === tabId;
        btn.classList.toggle("active", on);
        if (on) btn.setAttribute("aria-current", "page"); else btn.removeAttribute("aria-current");
    });
    // The current place lives under More: More shows it.
    document.getElementById("bottom-more")?.classList.toggle("active", nav.hidden.phone.includes(tabId));
    document.getElementById("rail-more")?.classList.toggle("active", nav.hidden.rail.includes(tabId));
}

/** The More buttons' badges: the badges of the places under each (`panelBadges`, app-panels.js). */
function paintMoreBadges(badges) {
    for (const [id, ids] of [["bottom-more", nav.hidden.phone], ["rail-more", nav.hidden.rail]]) {
        const el = document.getElementById(id)?.querySelector("[data-more-badge]");
        if (!el) continue;
        const m = moreBadge(ids, badges);
        window.Dana.motion.number(el, m.text);
        el.dataset.tone = m.tone;
        el.classList.toggle("dot", m.dot);
        el.classList.toggle("hidden", !m.text && !m.dot);
    }
}

// ── More: the sheet / flyout and Edit ──────────────────────────────────

const navSheetEl = () => document.getElementById("nav-more");
const navOpenerOf = (mode) => mode === "phone" ? document.getElementById("bottom-more")
    : (document.getElementById("rail-more") || document.getElementById("rail-edit"));

function openNavSheet(mode, editing) {
    const dlg = navSheetEl();
    if (!dlg) return;
    if (nav.sheet && nav.sheet.mode === mode && !editing) { closeNavSheet(); return; }
    nav.sheet = { mode, editing };
    dlg.hidden = false;
    dlg.classList.remove("closing");
    dlg.classList.toggle("pop", mode === "rail");
    const opener = navOpenerOf(mode);
    opener?.setAttribute("aria-expanded", "true");
    renderNavSheet();
    afterNavDrawn();
    const first = dlg.querySelector("#nav-more-body [aria-current='page']")
        || dlg.querySelector("#nav-more-body button:not(:disabled), #nav-more-body a")
        || document.getElementById("nav-more-edit");
    first?.focus();
}

/** The flyout beside the rail: its foot level with the button that opened it, moved up as far as it
 *  must to stay on screen (Edit is taller than the grid). */
function placeNavPop() {
    const dlg = navSheetEl();
    const card = dlg && dlg.querySelector(".nav-sheet-card");
    const opener = nav.sheet && nav.sheet.mode === "rail" ? navOpenerOf("rail") : null;
    if (!card || !opener) return;
    const wanted = Math.max(8, Math.round(window.innerHeight - opener.getBoundingClientRect().bottom));
    const room = window.innerHeight - card.offsetHeight - 8;
    dlg.style.setProperty("--pop-bottom", `${Math.max(8, Math.min(wanted, room))}px`);
}

function closeNavSheet(restoreFocus = true) {
    const s = nav.sheet;
    const dlg = navSheetEl();
    if (!s || !dlg) return;
    nav.sheet = null;
    dlg.classList.add("closing");
    const opener = navOpenerOf(s.mode);
    opener?.setAttribute("aria-expanded", "false");
    setTimeout(() => { if (!nav.sheet) { dlg.hidden = true; dlg.classList.remove("closing"); } },
        window.Dana.motion.reduced() ? 0 : 200);
    if (restoreFocus) opener?.focus();
}

function navSheetTile(place, quiet) {
    const el = navButton(place, quiet ? "nav-tile quiet" : "nav-tile");
    el.removeAttribute("title");
    return el;
}

function renderNavSheet() {
    const s = nav.sheet;
    const body = document.getElementById("nav-more-body");
    const title = document.getElementById("nav-more-title");
    const edit = document.getElementById("nav-more-edit");
    if (!s || !body) return;
    const where = s.mode === "phone" ? "bar" : "rail";
    title.textContent = s.editing ? "Edit places" : "More";
    edit.textContent = s.editing ? "Done" : "Edit";
    edit.setAttribute("aria-pressed", String(s.editing));
    if (!s.editing) {
        const hidden = new Set(nav.hidden[s.mode]);
        const main = nav.layout.rest.filter(p => hidden.has(p.id));
        const core = nav.layout.core.filter(p => hidden.has(p.id));
        const parts = [];
        if (main.length) {
            const grid = document.createElement("div");
            grid.className = "nav-grid";
            grid.setAttribute("role", "group");
            grid.setAttribute("aria-label", "Places");
            grid.append(...main.map(p => navSheetTile(p, false)));
            parts.push(grid);
        }
        if (core.length) {
            const label = document.createElement("div");
            label.className = "nav-group-label";
            label.id = "nav-more-core-label";
            label.textContent = "Settings and tools";
            const grid = document.createElement("div");
            grid.className = "nav-grid quiet";
            grid.setAttribute("role", "group");
            grid.setAttribute("aria-labelledby", label.id);
            grid.append(...core.map(p => navSheetTile(p, true)));
            parts.push(label, grid);
        }
        body.replaceChildren(...parts);
        placeNavPop();
        return;
    }
    const primary = nav.layout.primary;
    const rest = nav.layout.rest;
    const full = primary.length >= NAV_MAX_PRIMARY;
    const row = (p, buttons) => `<li class="nav-edit-row" data-id="${escapeHtml(p.id)}">`
        + `<i data-lucide="${escapeHtml(p.icon)}" aria-hidden="true"></i><span class="nav-edit-label">${escapeHtml(p.label)}</span>`
        + `<span class="nav-edit-acts">${buttons}</span></li>`;
    const act = (name, icon, label, disabled) =>
        `<button type="button" class="icon-btn" data-act="${name}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}"${disabled ? " disabled" : ""}>`
        + `<i data-lucide="${icon}" aria-hidden="true"></i></button>`;
    body.innerHTML = `<p class="nav-edit-hint">${s.mode === "phone"
            ? `Up to ${NAV_MAX_PRIMARY} places sit in the bar; the others stay under More.`
            : `Up to ${NAV_MAX_PRIMARY} places sit at the top of the rail; the others follow, under More when the rail is full.`}</p>`
        + `<div class="nav-group-label" id="nav-edit-primary">${where === "bar" ? "In the bar" : "At the top"} · ${primary.length} of ${NAV_MAX_PRIMARY}</div>`
        + `<ol class="nav-edit-list" aria-labelledby="nav-edit-primary">${primary.map((p, i) => row(p,
            act("up", "chevron-up", `Move ${p.label} up`, i === 0)
            + act("down", "chevron-down", `Move ${p.label} down`, i === primary.length - 1)
            + act("unpin", "circle-minus", `Remove ${p.label} from the ${where === "bar" ? "bar" : "top"}`, primary.length <= 1))).join("")}</ol>`
        + (rest.length ? `<div class="nav-group-label" id="nav-edit-rest">Other places</div>`
            + `<ul class="nav-edit-list" aria-labelledby="nav-edit-rest">${rest.map(p => row(p,
                act("pin", "circle-plus", full ? `${p.label}: the ${where === "bar" ? "bar" : "top"} is full` : `Add ${p.label} to the ${where === "bar" ? "bar" : "top"}`, full))).join("")}</ul>`
            : "")
        + `<button type="button" class="chip nav-edit-reset" data-act="reset"><i data-lucide="rotate-ccw" aria-hidden="true"></i>Default places</button>`;
    placeNavPop();
}

function navEditAction(act, id) {
    let ids = nav.primary.slice();
    const label = (nav.places.main.find(p => p.id === id) || {}).label || "";
    let said = "";
    if (act === "up") { ids = movePlace(ids, id, -1); said = `${label} moved up`; }
    else if (act === "down") { ids = movePlace(ids, id, 1); said = `${label} moved down`; }
    else if (act === "pin") { ids = pinPlace(ids, id); said = `${label} added`; }
    else if (act === "unpin") { ids = unpinPlace(ids, id); said = `${label} removed`; }
    else if (act === "reset") { ids = null; said = "Default places"; }
    saveNavChoice(ids);
    renderNav();
    const status = document.getElementById("nav-more-status");
    if (status) status.textContent = said;
    // Keep the keyboard where it was: the same button on the row, else another one of the row.
    const body = document.getElementById("nav-more-body");
    const rowEl = id ? body.querySelector(`.nav-edit-row[data-id="${CSS.escape(id)}"]`) : null;
    const target = (rowEl && (rowEl.querySelector(`[data-act="${act}"]:not(:disabled)`) || rowEl.querySelector("button:not(:disabled)")))
        || body.querySelector(`[data-act="${act}"]`) || document.getElementById("nav-more-edit");
    target?.focus();
}

(() => {
    const dlg = navSheetEl();
    if (!dlg) return;
    dlg.addEventListener("click", (e) => {
        if (e.target.closest("[data-close]")) { closeNavSheet(); return; }
        const place = e.target.closest("[data-tab]");
        if (place) { closeNavSheet(); openScreen(place.dataset.tab); return; }
        const actBtn = e.target.closest("[data-act]");
        if (actBtn && !actBtn.disabled) navEditAction(actBtn.dataset.act, actBtn.closest(".nav-edit-row")?.dataset.id || "");
    });
    document.getElementById("nav-more-close")?.addEventListener("click", () => closeNavSheet());
    document.getElementById("nav-more-edit")?.addEventListener("click", () => {
        if (!nav.sheet) return;
        nav.sheet.editing = !nav.sheet.editing;
        // Done with nothing under More (a rail with room for everything): there is no grid to go back to.
        if (!nav.sheet.editing && !nav.hidden[nav.sheet.mode].length) { closeNavSheet(); return; }
        renderNavSheet();
        afterNavDrawn();
        document.getElementById("nav-more-edit")?.focus();
    });
    dlg.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { e.preventDefault(); closeNavSheet(); return; }
        if (e.key !== "Tab") return;
        // Focus stays in the dialog.
        const items = [...dlg.querySelectorAll(".nav-sheet-card button:not(:disabled), .nav-sheet-card a[href]")]
            .filter(el => el.offsetParent !== null);
        if (!items.length) return;
        const first = items[0], last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
})();

// ── Switching screens ──────────────────────────────────────────────────

function switchTab(tabId, arg = null) {
    // The screen arriving slides in from the side it lies on in the navigation (styles.css, Motion).
    const dir = navIndex(tabId) < navIndex(activeTab) ? -1 : 1;
    if (tabId !== activeTab) previousTab = activeTab;
    activeTab = tabId;
    highlightNav(tabId);
    document.querySelectorAll(".screen-panel").forEach(panel => {
        const on = panel.id === `panel-${tabId}`;
        if (on) panel.style.setProperty("--dn-dir", dir);
        panel.classList.toggle("active", on);
    });
    if (!visitedScreens.has(tabId)) {
        visitedScreens.add(tabId);
        window.Dana?.motion.arrive(document.getElementById(`panel-${tabId}`));
    }

    // Trigger screen data loading
    if (tabId !== "debug") stopDebugPolling();
    switchPanels(tabId, arg);
    if (tabId === "review") window.DanaReview?.show(); else window.DanaReview?.hide();
    if (tabId === "usage") window.DanaUsage?.show(); else window.DanaUsage?.hide();
    if (tabId === "attention") window.DanaAttention?.show(arg); else window.DanaAttention?.hide();
    if (tabId === "addons") window.DanaAddons?.show(); else window.DanaAddons?.hide();
    if (tabId === "devices") window.DanaDevices?.show(); else window.DanaDevices?.hide();
    if (tabId === "household") window.DanaHousehold?.show(); else window.DanaHousehold?.hide();
    if (tabId === "now") { showNow(); window.DanaDemo?.refresh(); } else hideNow();
    if (tabId === "debug") { loadDebugData(); startDebugPolling(); }
    else if (tabId === "chat") {
        // Scroll to bottom of chat
        const chatMessagesEl = document.getElementById("chat-messages");
        if (chatMessagesEl) chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
    }

    refreshIcons();
}

/** Opens a core screen or an addon page by id (`arg` reaches the page's mount). */
function openScreen(id, arg = null) {
    if (CORE_SCREENS.includes(id)) { switchTab(id, arg); return Promise.resolve(true); }
    return openPanelById(id, arg);
}

// A chat about something (a notification, a person, a review) opens here.
window.danaOpenSession = (sid) => { switchTab("chat"); loadSession(sid); };
window.danaSwitchTab = openScreen;
// Back to the screen shown before this one (the weekly review's back button).
window.danaBack = () => openScreen(previousTab && previousTab !== activeTab ? previousTab : "now");

// A place in the rail or the bar opens its screen (the buttons are drawn again and again: one listener each).
for (const id of ["nav-rail", "bottom-nav"]) {
    document.getElementById(id)?.addEventListener("click", (e) => {
        const btn = e.target.closest("[data-tab]");
        if (btn) openScreen(btn.getAttribute("data-tab"));
    });
}

document.getElementById("rail-brand")?.addEventListener("click", () => switchTab("now"));
document.getElementById("btn-open-debug-tab")?.addEventListener("click", () => switchTab("debug"));
document.getElementById("btn-open-usage-tab")?.addEventListener("click", () => switchTab("usage"));
document.getElementById("btn-open-attention-tab")?.addEventListener("click", () => switchTab("attention"));
document.getElementById("btn-open-addons-tab")?.addEventListener("click", () => switchTab("addons"));
document.getElementById("btn-open-devices-tab")?.addEventListener("click", () => switchTab("devices"));
document.getElementById("btn-open-household-tab")?.addEventListener("click", () => switchTab("household"));

renderNav();
