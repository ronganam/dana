/* The web app — the Settings screen's form (static/app.js says how the app's scripts fit together).
 *
 * The server URL and API token kept in localStorage.
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
