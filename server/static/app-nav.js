/* The web app — navigation (static/app.js says how the app's scripts fit together).
 *
 * The rail and the bottom nav, switchTab (which screen shows, each core screen's load, the addon pages'
 * mount / unmount through app-panels.js) and the hooks the self-contained core screens call:
 * window.danaSwitchTab (a core screen or an addon page by id), window.danaOpenSession and
 * window.danaOpenCall. Addon pages reach the same through their Dana api (static/dana.js).
 */

// ══════════════════════════════════════════════════════════════════════
// 3. NAVIGATION CONTROLLER (Rail + Bottom Nav)
// ══════════════════════════════════════════════════════════════════════

// The shell's own screens; every other screen is an addon's page (app-panels.js).
const CORE_SCREENS = ["chat", "tasks", "calls", "settings", "addons", "usage", "debug", "review"];

function highlightNav(tabId) {
    // Addons is a page of Settings: Settings stays the highlighted tab.
    const navTab = tabId === "addons" ? "settings" : tabId;
    document.querySelectorAll(".rail-btn, .bottom-nav-btn").forEach(btn => {
        btn.classList.toggle("active", btn.getAttribute("data-tab") === navTab);
    });
}

function switchTab(tabId, arg = null) {
    activeTab = tabId;
    highlightNav(tabId);
    document.querySelectorAll(".screen-panel").forEach(panel => {
        panel.classList.toggle("active", panel.id === `panel-${tabId}`);
    });

    // Trigger screen data loading
    if (tabId !== "debug") stopDebugPolling();
    switchPanels(tabId, arg);
    if (tabId === "review") window.DanaReview?.show(); else window.DanaReview?.hide();
    if (tabId === "usage") window.DanaUsage?.show(); else window.DanaUsage?.hide();
    if (tabId === "addons") window.DanaAddons?.show(); else window.DanaAddons?.hide();
    if (tabId === "tasks") window.DanaToday?.show();
    if (tabId === "tasks") loadTasks();
    else if (tabId === "calls") loadCalls();
    else if (tabId === "debug") { loadDebugData(); startDebugPolling(); }
    else if (tabId === "chat") {
        // Scroll to bottom of chat
        const chatMessagesEl = document.getElementById("chat-messages");
        if (chatMessagesEl) chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
    }

    refreshIcons();
}

/** Opens a core screen or an addon page by id (`arg`: a call id for calls, a page's own argument). */
function openScreen(id, arg = null) {
    if (id === "calls" && arg) return window.danaOpenCall(arg);
    if (CORE_SCREENS.includes(id)) { switchTab(id, arg); return Promise.resolve(true); }
    return openPanelById(id, arg);
}

// A chat about something (a notification, a person, a review) opens here.
window.danaOpenSession = (sid) => { switchTab("chat"); loadSession(sid); };
window.danaSwitchTab = openScreen;
// A call opened from elsewhere (a person's timeline, the Usage screen).
window.danaOpenCall = async (id) => {
    switchTab("calls");
    await loadCalls();
    selectedCallId = id;
    renderCallsMasterList();
    renderCallDetail();
    return true;
};

document.querySelectorAll(".rail-btn[data-tab], .bottom-nav-btn[data-tab]").forEach(btn => {
    btn.addEventListener("click", () => switchTab(btn.getAttribute("data-tab")));
});

document.getElementById("rail-brand")?.addEventListener("click", () => switchTab("chat"));
document.getElementById("btn-open-debug-tab")?.addEventListener("click", () => switchTab("debug"));
document.getElementById("btn-open-usage-tab")?.addEventListener("click", () => switchTab("usage"));
document.getElementById("btn-open-addons-tab")?.addEventListener("click", () => switchTab("addons"));
