/* The web app — the Settings screen's form (static/app.js says how the app's scripts fit together).
 *
 * The server URL and API token kept in localStorage, and the theme of this browser: System (the default:
 * the device's light or dark), Ink or Paper (`dana_theme`; index.html applies it before the first paint).
 */

// ══════════════════════════════════════════════════════════════════════
// 9. SCREEN 6: SETTINGS CONTROLLER
// ══════════════════════════════════════════════════════════════════════

const settingsServerUrl = document.getElementById("settings-server-url");
const settingsApiToken = document.getElementById("settings-api-token");
const btnSaveSettings = document.getElementById("btn-save-settings");

if (settingsServerUrl) {
    settingsServerUrl.value = localStorage.getItem("dana_server_url") || window.location.origin;
}
if (settingsApiToken) {
    settingsApiToken.value = localStorage.getItem("dana_api_token") || "";
}

btnSaveSettings?.addEventListener("click", () => {
    localStorage.setItem("dana_server_url", settingsServerUrl.value.trim());
    localStorage.setItem("dana_api_token", settingsApiToken.value.trim());
    btnSaveSettings.textContent = "Settings Saved!";
    setTimeout(() => { btnSaveSettings.textContent = "Save Settings"; }, 1500);
});

// Theme: html[data-theme] overrides the system's choice (styles.css, section 1).
const themeChoices = document.getElementById("settings-theme");
function readTheme() {
    try { return localStorage.getItem("dana_theme") || "system"; } catch (_) { return "system"; }
}
function applyTheme(choice) {
    if (choice === "light" || choice === "dark") document.documentElement.dataset.theme = choice;
    else delete document.documentElement.dataset.theme;
    themeChoices?.querySelectorAll("[data-theme-choice]").forEach(b => {
        const on = b.dataset.themeChoice === choice;
        b.classList.toggle("active", on);
        b.setAttribute("aria-checked", String(on));
    });
    if (typeof wakeSphere === "function") wakeSphere();   // the orb reads the theme's colours
}
applyTheme(readTheme());
themeChoices?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-theme-choice]");
    if (!b) return;
    try {
        if (b.dataset.themeChoice === "system") localStorage.removeItem("dana_theme");
        else localStorage.setItem("dana_theme", b.dataset.themeChoice);
    } catch (_) { /* this browser keeps nothing: the choice lasts until the page reloads */ }
    applyTheme(b.dataset.themeChoice);
});
