/* Profile — the signed-in user's languages and currency, on the Settings screen.
 *
 *   GET /api/profile   {languages, currency, own: {languages, currency}, defaults: {languages, currency}}
 *   PUT /api/profile   {languages: "he, en" | [], currency: "ILS" | ""}  ("" / []: back to the server's)
 * Server side: core/profile_api.py, core/user_locale.py (docs/access.md → User). Self-contained.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  async function request(method, body) {
    let r;
    try {
      r = await fetch("/api/profile", { method, cache: "no-store",
        headers: body !== undefined ? { "Content-Type": "application/json" } : {},
        body: body !== undefined ? JSON.stringify(body) : undefined });
    } catch (_) {
      return { status: 0, body: null };
    }
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    return { status: r.status, body: data };
  }

  function status(text, bad) {
    const el = $("profile-status");
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("bad", !!bad);
  }

  function fill(p) {
    $("profile-languages").value = (p.own.languages || []).join(", ");
    $("profile-languages").placeholder = (p.defaults.languages || []).join(", ");
    $("profile-currency").value = p.own.currency || "";
    $("profile-currency").placeholder = p.defaults.currency || "";
    $("profile-languages-hint").textContent =
      `Dana answers in ${p.languages.join(", ")}; the first is the default. Empty: the server's (${p.defaults.languages.join(", ")}).`;
    $("profile-currency-hint").textContent =
      `Money values mean ${p.currency} where nothing else says. Empty: the server's (${p.defaults.currency}).`;
  }

  async function load() {
    const r = await request("GET");
    if (r.status === 200 && r.body) fill(r.body);
    else status("Couldn't load your profile.", true);
  }

  async function save() {
    status("Saving…");
    const r = await request("PUT", { languages: $("profile-languages").value, currency: $("profile-currency").value });
    if (r.status === 200 && r.body) { fill(r.body); status("Saved."); return; }
    status((r.body && r.body.detail) || "Couldn't save your profile.", true);
  }

  function start() {
    if (!$("profile-card")) return;
    $("btn-save-profile").addEventListener("click", save);
    load();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
