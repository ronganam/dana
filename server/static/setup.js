/* The setup page (/setup): a fresh server's first steps (server/docs/devices.md, "Setup mode").
 *
 *   GET  /api/setup              {setup_required, version}
 *   POST /api/setup/admin        {code, name} → {user, device, token}; the browser is signed in
 *
 * STEPS is the whole flow, in order. Each step is {id, kicker, title, needed(state), render(el, state, done)}:
 * the page shows, one after another, the steps whose needed(state) is true when their turn comes; render draws
 * the step into `el` and calls done(changes) when it is finished (changes are merged into state). After the
 * last step the app opens. A new step is one more entry: it runs as the signed-in admin once `admin` is done,
 * using the ordinary API.
 *
 * state: {setup_required, version, code (from the link, then taken out of the address bar), user?}
 */
(() => {
  "use strict";

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const root = document.getElementById("setup");

  async function api(method, path, body) {
    const r = await fetch(path, {
      method, credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    return { status: r.status, data: data || {} };
  }

  const STEPS = [
    {
      id: "admin",
      kicker: "Welcome",
      title: "Let's set up Dana",
      needed: (s) => s.setup_required,
      render(el, s, done) {
        if (!s.code) {
          el.innerHTML = `<p class="setup-lede">Open the setup link the server wrote to its log when it started:
            it ends in <code>/setup?code=…</code>.</p>`;
          return;
        }
        el.innerHTML = `
          <p class="setup-lede">You will be this server's admin. Others can be added later.</p>
          <form class="setup-form" novalidate>
            <label class="settings-label" for="setup-name">Your name</label>
            <input class="settings-input" id="setup-name" name="name" maxlength="80" autocomplete="name" required autofocus>
            <div class="setup-error" role="alert"></div>
            <button class="btn-primary" type="submit">Create my account</button>
          </form>`;
        const form = el.querySelector("form");
        const error = el.querySelector(".setup-error");
        form.addEventListener("submit", async (e) => {
          e.preventDefault();
          const name = form.name.value.trim();
          if (!name) { error.textContent = "Your name is needed."; form.name.focus(); return; }
          const btn = form.querySelector("button");
          btn.disabled = true;
          error.textContent = "";
          const r = await api("POST", "/api/setup/admin", { code: s.code, name });
          btn.disabled = false;
          if (r.status === 201) { done({ user: r.data.user, setup_required: false }); return; }
          if (r.status === 409) { done({ setup_required: false }); return; }
          error.textContent = r.status === 429
            ? `Too many tries. Wait ${r.data.retry_after || 60} seconds and try again.`
            : r.data.detail || "That did not work. Try again.";
        });
      },
    },
  ];

  const state = { setup_required: false, version: "", code: "" };

  function frame(step, index, total) {
    root.innerHTML = `
      <div class="setup-steps" aria-hidden="true">${Array.from({ length: total }, (_, i) => `<span class="${i <= index ? "on" : ""}"></span>`).join("")}</div>
      <span class="setup-kicker">${esc(step.kicker)}</span>
      <h1 class="setup-title">${esc(step.title)}</h1>
      <div data-step></div>`;
    return root.querySelector("[data-step]");
  }

  function next(from) {
    for (let i = from; i < STEPS.length; i++) {
      const step = STEPS[i];
      if (!step.needed(state)) continue;
      const el = frame(step, i, STEPS.length);
      step.render(el, state, (changes) => { Object.assign(state, changes || {}); next(i + 1); });
      return;
    }
    location.replace("/");
  }

  async function start() {
    const params = new URLSearchParams(location.search);
    state.code = params.get("code") || "";
    if (state.code) history.replaceState(null, "", location.pathname);  // the code stays out of history and screenshots
    const r = await api("GET", "/api/setup");
    Object.assign(state, { setup_required: Boolean(r.data.setup_required), version: r.data.version || "" });
    next(0);
  }

  start().catch(() => { root.innerHTML = `<p class="setup-lede">Dana could not be reached. Reload the page to try again.</p>`; });
})();
