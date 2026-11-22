/* The model — which LLM provider Dana uses and its key (server-wide, admins only), on the Settings screen and in the
 * wizard's Model step (static/setup.js).
 *
 *   GET /api/llm   {provider, providers: [{id, label, key_env, models, key_url, experimental, local}],
 *                   key: {set, from_env, last4}, base_url, model, models}
 *   PUT /api/llm   {provider, key?, base_url?, model?}: tested on the spot, then stored (the key encrypted)
 *
 * The key is never shown again: only "set" and its last four characters. Server side: core/llm_api.py,
 * core/llm_provider.py, server/docs/configuration.md ("Models").
 *
 * Two parts: DanaLlmCore (pure: the request body, the key's line; tested by test_web_llm.mjs) and DanaModelPicker
 * (the form, on any page), mounted into the Settings card for admins.
 */
(() => {
  "use strict";

  /** The PUT body from the form: a key only when one was typed; a local model takes an address and a name. */
  function requestBody(provider, { key = "", baseUrl = "", model = "" } = {}) {
    if (!provider) return { error: { field: "provider", detail: "Choose a provider." } };
    if (provider.local) return { body: { provider: provider.id, base_url: baseUrl.trim(), model: model.trim() } };
    const k = key.trim();
    if (k && /\s/.test(k)) return { error: { field: "key", detail: "A key has no spaces." } };
    return { body: k ? { provider: provider.id, key: k } : { provider: provider.id } };
  }

  /** What the page says about the key: never the key itself. */
  function keyLine(status, provider) {
    if (!provider || provider.local) return "";
    const k = (status && status.key) || {};
    if (!k.set || (status.provider && status.provider !== provider.id)) return "No key yet.";
    if (k.from_env) return `Set on the server (${provider.key_env}).`;
    return k.last4 ? `A key is set, ending in ${k.last4}.` : "A key is set.";
  }

  const core = { requestBody, keyLine };
  const G = typeof window !== "undefined" ? window : globalThis;
  G.DanaLlmCore = core;
  if (typeof document === "undefined") return;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(method, path, body) {
    const r = await fetch(path, { method, cache: "no-store", credentials: "same-origin",
                                  headers: body ? { "Content-Type": "application/json" } : {},
                                  body: body ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) throw Object.assign(new Error((data && (data.detail || data.error)) || `HTTP ${r.status}`), { status: r.status, data });
    return data;
  }

  /** Draws the provider choice and its key into `el`; onSaved(status) after a key passed its test and was stored. */
  async function mount(el, onSaved) {
    let status;
    try { status = await api("GET", "/api/llm"); } catch (e) {
      el.innerHTML = `<p class="profile-hint bad">${esc(e.status === 403 ? "Only an admin chooses the model." : e.message)}</p>`;
      return;
    }
    let chosen = status.provider || "claude";

    function draw() {
      const p = status.providers.find((x) => x.id === chosen) || status.providers[0];
      el.innerHTML = `
        <div class="segmented-control llm-providers" role="radiogroup" aria-label="Provider">${status.providers.map((x) => `
          <button type="button" class="segmented-btn${x.id === p.id ? " active" : ""}" role="radio" aria-checked="${x.id === p.id}" data-p="${esc(x.id)}">${esc(x.label)}</button>`).join("")}</div>
        ${p.experimental ? `<p class="profile-hint">Experimental: local models often get tool use wrong (reminders, the home, messages).</p>` : ""}
        <form class="llm-form" novalidate>
          ${p.local ? `
            <label class="settings-label" for="llm-base">Ollama's address</label>
            <input class="settings-input" id="llm-base" value="${esc(status.base_url)}" placeholder="http://localhost:11434" autocomplete="off">
            <label class="settings-label" for="llm-model">Model</label>
            <input class="settings-input" id="llm-model" value="${esc(status.model)}" placeholder="qwen3" autocomplete="off">` : `
            <label class="settings-label" for="llm-key">${esc(p.label)} API key</label>
            <input class="settings-input" id="llm-key" type="password" autocomplete="off" spellcheck="false" placeholder="Paste the key">
            <span class="profile-hint">${esc(keyLine(status, p))}${p.key_url ? ` <a href="${esc(p.key_url)}" target="_blank" rel="noopener">Get a key</a>` : ""}</span>`}
          <span class="profile-hint">Chat: ${esc((p.local ? status.models : p.models).balanced || "your model")}; background jobs: ${esc((p.local ? status.models : p.models).fast || "your model")}.</span>
          <div class="setup-error llm-error" role="alert"></div>
          <button type="submit" class="btn-primary">Test and save</button>
          <span class="profile-hint llm-status" role="status" aria-live="polite"></span>
        </form>`;
      el.querySelectorAll("[data-p]").forEach((b) => b.addEventListener("click", () => { chosen = b.dataset.p; draw(); }));
      const form = el.querySelector("form");
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const out = requestBody(p, { key: el.querySelector("#llm-key")?.value || "",
                                     baseUrl: el.querySelector("#llm-base")?.value || "",
                                     model: el.querySelector("#llm-model")?.value || "" });
        const error = el.querySelector(".llm-error");
        const note = el.querySelector(".llm-status");
        error.textContent = "";
        if (out.error) { error.textContent = out.error.detail; return; }
        const btn = form.querySelector("button[type=submit]");
        btn.disabled = true;
        note.textContent = "Testing…";
        try {
          status = await api("PUT", "/api/llm", out.body);
          chosen = status.provider;
          draw();
          el.querySelector(".llm-status").textContent = "Saved: the key works.";
          onSaved?.(status);
        } catch (err) {
          btn.disabled = false;
          note.textContent = "";
          error.textContent = err.message;
        }
      });
    }
    draw();
  }
  G.DanaModelPicker = { mount };

  const card = document.getElementById("llm-card");
  if (!card) return;
  card.classList.add("hidden");
  fetch("/api/me", { cache: "no-store", credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null)).then((me) => {
    if (!me || !(me.capabilities || []).includes("manage_server")) return;
    card.classList.remove("hidden");
    mount(document.getElementById("llm-box"));
  }).catch(() => {});
})();
