/* The web app — the Settings screen's form (static/app.js says how the app's scripts fit together).
 *
 * The theme of this browser: System (the default: the device's light or dark), Ink or Paper (`dana_theme`;
 * index.html applies it before the first paint). The browser signs in with its session cookie (/login), so the
 * web keeps no server address or token.
 */

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
