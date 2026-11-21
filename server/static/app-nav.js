/* The web app — navigation (static/app.js says how the app's scripts fit together).
 *
 * The rail and the bottom nav, switchTab (which panel shows, the panel scripts' show / hide, each screen's
 * load) and the hooks the panel scripts call: window.danaSwitchTab, window.danaOpenSession and
 * window.danaOpenCall.
 */

// ══════════════════════════════════════════════════════════════════════
// 3. NAVIGATION CONTROLLER (Rail + Bottom Nav)
// ══════════════════════════════════════════════════════════════════════

const railButtons = document.querySelectorAll(".rail-btn[data-tab]");
const bottomNavButtons = document.querySelectorAll(".bottom-nav-btn[data-tab]");
const screenPanels = document.querySelectorAll(".screen-panel");

function switchTab(tabId) {
    activeTab = tabId;
    // Integrations is a page of Settings: Settings stays the highlighted tab.
    const navTab = tabId === "integrations" ? "settings" : tabId;
    railButtons.forEach(btn => {
        btn.classList.toggle("active", btn.getAttribute("data-tab") === navTab);
    });
    bottomNavButtons.forEach(btn => {
        btn.classList.toggle("active", btn.getAttribute("data-tab") === navTab);
    });
    screenPanels.forEach(panel => {
        panel.classList.toggle("active", panel.id === `panel-${tabId}`);
    });

    // Trigger screen data loading
    if (tabId !== "debug") stopDebugPolling();
    if (tabId === "inbox") window.DanaInbox?.show(); else window.DanaInbox?.hide();
    if (tabId === "people") window.DanaPeople?.show(); else window.DanaPeople?.hide();
    if (tabId === "review") window.DanaReview?.show(); else window.DanaReview?.hide();
    if (tabId === "usage") window.DanaUsage?.show(); else window.DanaUsage?.hide();
    if (tabId === "integrations") window.DanaIntegrations?.show(); else window.DanaIntegrations?.hide();
    if (tabId === "shopping") window.DanaShopping?.show(); else window.DanaShopping?.hide();
    if (tabId === "money") window.DanaMoney?.show(); else window.DanaMoney?.hide();
    if (tabId === "projects") window.DanaProjects?.show(); else window.DanaProjects?.hide();
    if (tabId === "tasks") window.DanaToday?.show();
    if (tabId === "tasks") loadTasks();
    else if (tabId === "memory") loadMemories();
    else if (tabId === "calls") loadCalls();
    else if (tabId === "calendar") loadCalendar();
    else if (tabId === "debug") { loadDebugData(); startDebugPolling(); }
    else if (tabId === "chat") {
        // Scroll to bottom of chat
        const chatMessagesEl = document.getElementById("chat-messages");
        if (chatMessagesEl) chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
    }

    refreshIcons();
}

// The Inbox (static/inbox.js) opens a chat about a notification here.
window.danaOpenSession = (sid) => { switchTab("chat"); loadSession(sid); };
window.danaSwitchTab = switchTab;
// People (static/people.js) opens a call from a person's timeline.
window.danaOpenCall = async (id) => {
    switchTab("calls");
    await loadCalls();
    selectedCallId = id;
    renderCallsMasterList();
    renderCallDetail();
    return true;
};

railButtons.forEach(btn => btn.addEventListener("click", () => switchTab(btn.getAttribute("data-tab"))));
bottomNavButtons.forEach(btn => btn.addEventListener("click", () => switchTab(btn.getAttribute("data-tab"))));

document.getElementById("rail-brand")?.addEventListener("click", () => switchTab("chat"));
document.getElementById("btn-open-debug-tab")?.addEventListener("click", () => switchTab("debug"));
document.getElementById("btn-open-usage-tab")?.addEventListener("click", () => switchTab("usage"));
document.getElementById("btn-open-integrations-tab")?.addEventListener("click", () => switchTab("integrations"));
