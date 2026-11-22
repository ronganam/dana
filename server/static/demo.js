/* The demo household's banner on the Now screen (admins: manage_users).
 *
 *   GET    /api/demo        {loaded, users: [{id, name}], viewing}   (403 for anyone else: no banner)
 *   POST   /api/demo/view   this browser shows the app as the first demo user (the page reloads)
 *   DELETE /api/demo/view   back to the admin's own account (reloads)
 *   DELETE /api/demo        removes the sample household and all its data (reloads when it was being viewed)
 *
 * While the sample data is loaded the banner says so with "View it" and "Remove"; while it is being viewed,
 * "Viewing the sample household as <name>" with "Back to my account" and "Remove". Loading it is the setup
 * wizard's ("Show me with sample data": POST /api/demo). Server side: core/demo.py, core/demo_api.py.
 *
 * Two parts: DanaDemoCore (pure: what the banner says and offers; tested by test_web_household.mjs) and the
 * controller (window.DanaDemo = {refresh}; app-nav.js calls it when Now opens).
 */
(() => {
  "use strict";

  /** The banner for a GET /api/demo answer: null (none) or {text, actions: [view | back | remove]}. */
  function banner(state) {
    if (!state || !state.loaded) return null;
    const viewed = (state.users || []).find((u) => u.id === state.viewing);
    if (viewed) return { text: `Viewing the sample household as ${viewed.name}. Nothing you do here touches your own data.`,
                         actions: ["back", "remove"], viewing: true };
    return { text: "Sample data is loaded: a made-up household to look around in.", actions: ["view", "remove"], viewing: false };
  }

  const LABELS = { view: "View it", back: "Back to my account", remove: "Remove" };

  const core = { banner, LABELS };
  (typeof window !== "undefined" ? window : globalThis).DanaDemoCore = core;
  if (typeof document === "undefined" || !document.getElementById("demo-banner")) return;

  const el = document.getElementById("demo-banner");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let current = null;

  async function request(method, path) {
    try {
      const r = await fetch(path, { method, cache: "no-store" });
      let body = null;
      try { body = await r.json(); } catch (_) { /* empty */ }
      return { status: r.status, body };
    } catch (_) {
      return { status: 0, body: null };
    }
  }

  function draw(b, confirming = false) {
    current = b;
    el.classList.toggle("hidden", !b);
    if (!b) { el.innerHTML = ""; return; }
    if (confirming) {
      el.innerHTML = `<span class="demo-text">Remove the sample household? Its made-up people and data go; yours stay.</span>
        <span class="demo-acts"><button type="button" class="chip" data-demo="cancel">Keep it</button>
        <button type="button" class="chip demo-danger" data-demo="confirm">Remove</button></span>`;
      return;
    }
    el.innerHTML = `<span class="demo-text">${esc(b.text)}</span><span class="demo-acts">${b.actions.map((a) =>
      `<button type="button" class="chip${a === "remove" ? " demo-danger" : ""}" data-demo="${a}">${esc(LABELS[a])}</button>`).join("")}</span>`;
  }

  async function refresh() {
    const r = await request("GET", "/api/demo");
    draw(r.status === 200 ? banner(r.body) : null);
  }

  el.addEventListener("click", async (e) => {
    const act = e.target.closest("[data-demo]")?.dataset.demo;
    if (!act) return;
    if (act === "remove") { draw(current, true); return; }
    if (act === "cancel") { draw(current); return; }
    if (act === "view") { if ((await request("POST", "/api/demo/view")).status === 200) location.reload(); return; }
    if (act === "back") { await request("DELETE", "/api/demo/view"); location.reload(); return; }
    if (act === "confirm") {
      const viewing = current?.viewing;
      const r = await request("DELETE", "/api/demo");
      if (r.status === 200 && viewing) { location.reload(); return; }
      refresh();
    }
  });

  window.DanaDemo = { refresh };
  refresh();
})();
