/* The web app — navigation (static/app.js says how the app's scripts fit together).
 *
 * The rail and the bottom nav, switchTab (which screen shows, each core screen's load, the addon pages'
 * mount / unmount through app-panels.js) and the hooks the self-contained core screens call:
 * window.danaSwitchTab (a core screen or an addon page by id), window.danaBack (the screen before) and
 * window.danaOpenSession. Addon pages reach the same through their Dana api (static/dana.js).
 */

// ══════════════════════════════════════════════════════════════════════
// NAVIGATION CONTROLLER (Rail + Bottom Nav)
// ══════════════════════════════════════════════════════════════════════

// The shell's own screens (core/addons/hooks.py, CORE_SCREENS: no addon page may take one of these ids);
// every other screen is an addon's page (app-panels.js).
const CORE_SCREENS = ["chat", "settings", "addons", "usage", "debug", "review"];

// Core pieces an addon page can host in an element of its own (api.piece(name, el), static/dana.js).
const CORE_PIECES = {
    // The morning briefing and the weekly-review banner (static/review.js).
    today: (el) => window.DanaToday?.show(el),
};

let previousTab = null;
const visitedScreens = new Set();   // screens opened once: their rows rise in only the first time

/** A screen's place in the navigation (core and addon buttons in their order); -1 when it has none. */
function navIndex(tabId) {
    const navTab = tabId === "addons" ? "settings" : tabId;
    return [...document.querySelectorAll(".rail-btn[data-tab]")].findIndex(b => b.getAttribute("data-tab") === navTab);
}

function highlightNav(tabId) {
    // Addons is a page of Settings: Settings stays the highlighted tab.
    const navTab = tabId === "addons" ? "settings" : tabId;
    document.querySelectorAll(".rail-btn, .bottom-nav-btn").forEach(btn => {
        btn.classList.toggle("active", btn.getAttribute("data-tab") === navTab);
    });
}

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
    if (tabId === "addons") window.DanaAddons?.show(); else window.DanaAddons?.hide();
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
window.danaBack = () => openScreen(previousTab && previousTab !== activeTab ? previousTab : "chat");

document.querySelectorAll(".rail-btn[data-tab], .bottom-nav-btn[data-tab]").forEach(btn => {
    btn.addEventListener("click", () => switchTab(btn.getAttribute("data-tab")));
});

document.getElementById("rail-brand")?.addEventListener("click", () => switchTab("chat"));
document.getElementById("btn-open-debug-tab")?.addEventListener("click", () => switchTab("debug"));
document.getElementById("btn-open-usage-tab")?.addEventListener("click", () => switchTab("usage"));
document.getElementById("btn-open-addons-tab")?.addEventListener("click", () => switchTab("addons"));
