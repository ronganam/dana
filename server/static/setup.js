/* The setup page (/setup): a fresh server's first admin, then the first-run wizard every user goes through
 * (server/docs/devices.md, "Setup mode" and "The wizard").
 *
 *   GET  /api/setup              {setup_required, version}
 *   POST /api/setup/admin        {code, name} → {user, device, token}; the browser is signed in
 *   GET  /api/wizard             {steps: [{id, status, available}], finished, next, demo, timezone}
 *   POST /api/wizard/steps/{id}  {status: done | skipped}       POST /api/wizard/finish
 *   PUT  /api/wizard/home        {latitude, longitude, timezone?, currency?} → the Home zone, the server's timezone
 *   GET|PUT /api/profile (You) · /api/llm (The model, static/llm.js) · POST /api/devices/pairings (Your phone)
 *   GET /api/addons, PUT /api/addons/{name} (Add-ons) · POST /api/demo (Sample data, when the server has it)
 *
 * STEPS is the whole flow, in order. Each step is {id, kicker, title, needed(state), render(el, state, done)}:
 * the page shows, one after another, the steps whose needed(state) is true when their turn comes; render draws
 * the step into `el` and calls done(changes) when it is finished (changes are merged into state). After the
 * last step the wizard is marked finished and the app opens. The wizard's steps run as the signed-in user with
 * the ordinary API; each has Skip, and what was done or skipped is stored, so the wizard resumes where it was
 * left. `/setup?again=1` (Settings → Setup wizard) shows every step again.
 *
 * Two parts: DanaSetupCore (pure: which steps show, the add-ons that are free to turn on; tested by
 * test_web_setup.mjs) and the page below it.
 *
 * state: {setup_required, version, code (from the link, then out of the address bar), again, user?, wizard?}
 */
(() => {
  "use strict";

  /** Whether wizard step `id` shows: available to the user, and not yet done or skipped (any, with `again`). */
  function wizardNeeded(state, id) {
    if (state.setup_required || !state.wizard) return false;
    const step = (state.wizard.steps || []).find((x) => x.id === id);
    return Boolean(step && step.available && (state.again || step.status === "pending"));
  }

  /** The add-ons to offer: suggested by the server, off, and free to turn on now (no required account,
   *  hardware or key: nothing to fill in). Returns [{name, title, description}]. */
  function freeAddons(payload) {
    const suggested = new Set(((payload && payload.welcome) || {}).suggested || []);
    const needsSomething = (a) => (a.needs || []).some((n) => !n.optional && n.kind !== "addon" && n.kind !== "app")
      || (a.credentials || []).some((c) => c.required && !c.set)
      || (a.settings || []).some((f) => f.required && (f.value === null || f.value === undefined || f.value === ""));
    return ((payload && payload.addons) || [])
      .filter((a) => suggested.has(a.name) && !a.connected && a.state === "available" && !needsSomething(a))
      .map((a) => ({ name: a.name, title: a.title || a.name, description: a.description || "" }));
  }

  const G = typeof window !== "undefined" ? window : globalThis;
  G.DanaSetupCore = { wizardNeeded, freeAddons };
  if (typeof document === "undefined") return;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const root = document.getElementById("setup");
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };

  async function api(method, path, body) {
    const r = await fetch(path, {
      method, credentials: "same-origin", cache: "no-store",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    return { status: r.status, data: data || {} };
  }

  /** Record a wizard step as done or skipped (the wizard state comes back). */
  async function mark(s, id, status) {
    const r = await api("POST", `/api/wizard/steps/${id}`, { status });
    if (r.status === 200) s.wizard = r.data;
  }

  /** The buttons under a wizard step: Skip (records it skipped) and, optionally, the main one. */
  function footer(el, s, id, done, main) {
    const bar = document.createElement("div");
    bar.className = "setup-actions";
    bar.innerHTML = `${main ? `<button class="btn-primary" type="button" data-main>${esc(main)}</button>` : ""}
      <button class="btn-secondary" type="button" data-skip>Skip</button>`;
    el.appendChild(bar);
    bar.querySelector("[data-skip]").addEventListener("click", async () => { await mark(s, id, "skipped"); done(); });
    return bar.querySelector("[data-main]");
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
    {
      id: "you",
      kicker: "You",
      title: "How should Dana talk to you?",
      needed: (s) => wizardNeeded(s, "you"),
      async render(el, s, done) {
        const p = (await api("GET", "/api/profile")).data;
        const langs = ((p.own && p.own.languages && p.own.languages.length ? p.own.languages : p.languages) || []).join(", ");
        el.innerHTML = `
          <form class="setup-form" novalidate>
            <label class="settings-label" for="w-name">Your name</label>
            <input class="settings-input" id="w-name" maxlength="80" value="${esc(p.name)}" autocomplete="name">
            <label class="settings-label" for="w-pronoun">Dana refers to you as</label>
            <select class="settings-input" id="w-pronoun">${[["she", "she / her"], ["he", "he / him"], ["they", "they / them"]]
              .map(([v, l]) => `<option value="${v}"${v === p.pronoun ? " selected" : ""}>${l}</option>`).join("")}</select>
            <label class="settings-label" for="w-langs">Languages you speak</label>
            <input class="settings-input" id="w-langs" value="${esc(langs)}" placeholder="en, he" autocomplete="off" spellcheck="false">
            <p class="setup-note">Language codes, the one Dana answers in first (en, he, fr, de…).</p>
            <div class="setup-error" role="alert"></div>
          </form>`;
        const save = footer(el, s, "you", done, "Continue");
        save.addEventListener("click", async () => {
          const error = el.querySelector(".setup-error");
          const r = await api("PUT", "/api/profile", { name: el.querySelector("#w-name").value,
            pronoun: el.querySelector("#w-pronoun").value, languages: el.querySelector("#w-langs").value });
          if (r.status !== 200) { error.textContent = r.data.detail || "That did not work."; return; }
          await mark(s, "you", "done");
          done();
        });
      },
    },
    {
      id: "home",
      kicker: "Home",
      title: "Where is home?",
      needed: (s) => wizardNeeded(s, "home"),
      render(el, s, done) {
        el.innerHTML = `
          <p class="setup-lede">For "remind me when I get home", and your timezone and currency. Only the place you choose is kept.</p>
          <div id="w-picker"></div>
          <p class="setup-note" id="w-home-note"></p>
          <div class="setup-error" role="alert"></div>`;
        let place = null;
        const save = footer(el, s, "home", done, "Set as home");
        save.disabled = true;
        window.DanaPlacePicker.mount(el.querySelector("#w-picker"), (p) => {
          place = p;
          save.disabled = false;
          el.querySelector("#w-home-note").textContent = [p.label, p.timezone, p.currency].filter(Boolean).join(" · ");
        });
        save.addEventListener("click", async () => {
          if (!place) return;
          const r = await api("PUT", "/api/wizard/home", { latitude: place.latitude, longitude: place.longitude,
            timezone: place.timezone || "", currency: place.currency || "" });
          if (r.status !== 200) { el.querySelector(".setup-error").textContent = r.data.detail || "That did not work."; return; }
          s.wizard = r.data.state;
          done();
        });
      },
    },
    {
      id: "model",
      kicker: "The model",
      title: "Connect a language model",
      needed: (s) => wizardNeeded(s, "model"),
      render(el, s, done) {
        el.innerHTML = `<p class="setup-lede">Dana thinks with a model from one provider. Paste one API key; it is tested now and kept encrypted.</p>
          <div id="w-model"></div>`;
        footer(el, s, "model", done);
        window.DanaModelPicker.mount(el.querySelector("#w-model"), async () => { await mark(s, "model", "done"); setTimeout(done, 600); });
      },
    },
    {
      id: "phone",
      kicker: "Your phone",
      title: "Pair your phone",
      needed: (s) => wizardNeeded(s, "phone"),
      render(el, s, done) {
        const C = window.DanaDevicesCore;
        el.innerHTML = `<p class="setup-lede">Install the Dana app, choose <strong>Pair with a code</strong>, then scan this or type the code.</p>
          <div class="setup-pair" id="w-pair"></div>`;
        const box = el.querySelector("#w-pair");
        let timers = [];
        const stop = () => { timers.forEach(clearInterval); timers = []; };
        const show = footer(el, s, "phone", () => { stop(); done(); }, "Show the code");
        show.addEventListener("click", async () => {
          stop();
          const r = await api("POST", "/api/devices/pairings", C.pairingBody("phone", "", ""));
          if (r.status !== 201) { box.innerHTML = `<p class="setup-error">${esc(r.data.detail || r.data.error || "No code")}</p>`; return; }
          const p = r.data;
          show.textContent = "New code";
          box.innerHTML = `<div class="dev-qr">${C.qrSvg(p.qr)}</div>
            <div class="dev-code">${esc(C.showCode(p.code))}</div><div class="dev-status" role="status" aria-live="polite"></div>`;
          const status = box.querySelector(".dev-status");
          const tick = () => { status.textContent = `Waiting for the phone · ${C.countdown(p.expires_at, Date.now())}`; };
          tick();
          timers.push(setInterval(tick, 1000));
          timers.push(setInterval(async () => {
            const st = await api("GET", `/api/devices/pairings/${encodeURIComponent(p.id)}`);
            if (st.data.status === "paired") {
              stop();
              box.innerHTML = `<p class="setup-lede">${esc((st.data.device && st.data.device.name) || "Your phone")} is paired.</p>`;
              await mark(s, "phone", "done");
              setTimeout(done, 900);
            } else if (st.data.status === "expired") {
              stop();
              status.textContent = "The code expired. Show a new one.";
            }
          }, 2000));
        });
      },
    },
    {
      id: "addons",
      kicker: "Add-ons",
      title: "Turn on what you need",
      needed: (s) => wizardNeeded(s, "addons"),
      async render(el, s, done) {
        const offer = freeAddons((await api("GET", "/api/addons")).data);
        el.innerHTML = offer.length ? `
          <p class="setup-lede">These work right away. Others (Home Assistant, Google, WhatsApp…) need an account: connect them later on the Addons page.</p>
          <div class="setup-form">${offer.map((a) => `
            <label class="setup-addon"><input type="checkbox" value="${esc(a.name)}" checked>
              <span><strong>${esc(a.title)}</strong><br><span class="setup-note">${esc(a.description)}</span></span></label>`).join("")}</div>
          <div class="setup-error" role="alert"></div>`
          : `<p class="setup-lede">Everything that works without an account is on. Connect the rest on the Addons page.</p>`;
        const go = footer(el, s, "addons", done, offer.length ? "Turn on" : "Continue");
        go.addEventListener("click", async () => {
          const names = [...el.querySelectorAll("input[type=checkbox]:checked")].map((x) => x.value);
          for (const name of names) {
            const r = await api("PUT", `/api/addons/${encodeURIComponent(name)}`, {});
            if (r.status !== 200) { el.querySelector(".setup-error").textContent = `${name}: ${r.data.message || r.data.error || "not turned on"}`; return; }
          }
          await mark(s, "addons", "done");
          done();
        });
      },
    },
    {
      id: "sample",
      kicker: "Try it",
      title: "See Dana with sample data",
      needed: (s) => wizardNeeded(s, "sample"),
      render(el, s, done) {
        el.innerHTML = `<p class="setup-lede">Fill Dana with a made-up week (reminders, a shopping list, notes) to see how it looks. You can remove it later.</p>
          <div class="setup-error" role="alert"></div>`;
        const go = footer(el, s, "sample", done, "Show me with sample data");
        go.addEventListener("click", async () => {
          go.disabled = true;
          const r = await api("POST", "/api/demo", {});
          if (r.status >= 300) { go.disabled = false; el.querySelector(".setup-error").textContent = r.data.detail || r.data.error || "That did not work."; return; }
          await mark(s, "sample", "done");
          done();
        });
      },
    },
  ];

  const state = { setup_required: false, version: "", code: "", again: false, wizard: null };

  function frame(step, index, total) {
    root.innerHTML = `
      <div class="setup-steps" aria-hidden="true">${Array.from({ length: total }, (_, i) => `<span class="${i <= index ? "on" : ""}"></span>`).join("")}</div>
      <span class="setup-kicker">${esc(step.kicker)}</span>
      <h1 class="setup-title">${esc(step.title)}</h1>
      <div data-step></div>`;
    return root.querySelector("[data-step]");
  }

  /** The wizard state for the signed-in user (null when nobody is signed in yet). */
  async function loadWizard() {
    const r = await api("GET", "/api/wizard");
    if (r.status === 401) { location.replace("/login"); return null; }
    return r.status === 200 ? r.data : null;
  }

  async function next(from) {
    if (!state.setup_required && !state.wizard) state.wizard = await loadWizard();
    for (let i = from; i < STEPS.length; i++) {
      const step = STEPS[i];
      if (!step.needed(state)) continue;
      const el = frame(step, i, STEPS.length);
      await step.render(el, state, (changes) => { Object.assign(state, changes || {}); next(i + 1); });
      icons();
      return;
    }
    if (state.wizard) await api("POST", "/api/wizard/finish");
    location.replace("/");
  }

  async function start() {
    const params = new URLSearchParams(location.search);
    state.code = params.get("code") || "";
    state.again = params.get("again") === "1";
    if (state.code || state.again) history.replaceState(null, "", location.pathname);  // the code stays out of history and screenshots
    const r = await api("GET", "/api/setup");
    Object.assign(state, { setup_required: Boolean(r.data.setup_required), version: r.data.version || "" });
    await next(0);
  }

  start().catch(() => { root.innerHTML = `<p class="setup-lede">Dana could not be reached. Reload the page to try again.</p>`; });
})();
