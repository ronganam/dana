/* Household — who uses this Dana (Settings → Household; admins with manage_users).
 *
 *   GET    /api/household                     {members: [{id, name, level, pronoun, languages, devices, last_seen_at, you}], can_manage}
 *   POST   /api/household/members             {name, pronoun, languages, level} → 201 {member}
 *   PATCH  /api/household/members/{id}        {level} → {member}; 409 last_admin
 *   DELETE /api/household/members/{id}        → {ok}; 409 self
 *   POST   /api/devices/pairings {kind, user_id}  the invite: a phone QR + code and a browser sign-in code
 *   GET / DELETE /api/devices/pairings/{id}   followed while the invite shows; cancelled when it closes
 *
 * Invite shows the member's phone pairing (the QR the Dana app scans, drawn by DanaDevicesCore.qrSvg) and a
 * browser code they type on /login, both valid five minutes. Server side: core/household_api.py,
 * core/devices_api.py (server/docs/access.md → "Managing users", server/docs/devices.md).
 *
 * Two parts: DanaHouseholdCore (pure: the member's line, labels, the request bodies, who may be removed; tested
 * by test_web_household.mjs) and the controller (window.DanaHousehold = {show, hide}; app-nav.js calls them).
 */
(() => {
  "use strict";

  const LEVELS = [
    { id: "member", label: "Member" },
    { id: "admin", label: "Admin" },
  ];
  const PRONOUNS = [
    { id: "they", label: "They" },
    { id: "she", label: "She" },
    { id: "he", label: "He" },
  ];
  const POLL_MS = 2000;

  const levelLabel = (id) => (LEVELS.find((l) => l.id === id) || { label: id }).label;
  const devicesCore = () => (typeof window !== "undefined" ? window : globalThis).DanaDevicesCore;

  /** The line under a member's name: level, devices, when last seen. */
  function memberLine(m, now) {
    const n = Number(m.devices) || 0;
    const parts = [levelLabel(m.level), n === 1 ? "1 device" : `${n} devices`];
    const ago = devicesCore()?.ago;
    parts.push(m.last_seen_at ? `seen ${ago ? ago(m.last_seen_at, now) : m.last_seen_at}` : "not signed in yet");
    return parts.join(" · ");
  }

  /** How many admins the list holds. */
  const adminCount = (members) => (members || []).filter((m) => m.level === "admin").length;

  /** Whether the list lets a member be removed: never yourself, never the last admin. */
  function canRemove(m, members) {
    return !m.you && !(m.level === "admin" && adminCount(members) <= 1);
  }

  /** Whether a member's level may change to ``level`` (the last admin stays admin). */
  function canSetLevel(m, level, members) {
    return m.level === level || !(m.level === "admin" && adminCount(members) <= 1);
  }

  /** The body that adds a member (empty fields left out). */
  function newMemberBody(name, pronoun, languages, level) {
    const body = { name: String(name || "").trim(), pronoun: pronoun || "they", level: level || "member" };
    const langs = String(languages || "").trim();
    if (langs) body.languages = langs;
    return body;
  }

  /** The two invite requests for a member: their phone and a browser. */
  const inviteBodies = (userId) => [{ kind: "phone", user_id: userId }, { kind: "browser", user_id: userId }];

  const core = { LEVELS, PRONOUNS, levelLabel, memberLine, adminCount, canRemove, canSetLevel, newMemberBody, inviteBodies };
  (typeof window !== "undefined" ? window : globalThis).DanaHouseholdCore = core;
  if (typeof document === "undefined" || !document.getElementById("panel-household")) return;

  // ---------------------------------------------------------------- controller

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (id) => document.getElementById(id);
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };

  const S = { members: [], canManage: false, loading: false, error: "", invite: null, poll: null, tick: null };

  async function api(method, path, body) {
    const r = await fetch(path, { method, cache: "no-store", headers: body ? { "Content-Type": "application/json" } : {},
                                  body: body ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) throw Object.assign(new Error((data && (data.detail || data.error)) || `HTTP ${r.status}`), { status: r.status, data });
    return data || {};
  }

  async function load() {
    S.loading = true;
    render();
    try {
      const b = await api("GET", "/api/household");
      S.members = b.members || [];
      S.canManage = !!b.can_manage;
      S.error = "";
    } catch (e) {
      S.error = e.message;
    }
    S.loading = false;
    showTile();
    render();
  }

  // The Settings tile and the Add button only for who may manage the household.
  function showTile() {
    $("btn-open-household-tab")?.classList.toggle("hidden", !S.canManage);
    $("hh-add")?.classList.toggle("hidden", !S.canManage);
  }

  function render() {
    const el = $("hh-list");
    if (!el) return;
    if (S.loading && !S.members.length) { el.innerHTML = `<div class="dev-skel"></div>`; return; }
    if (S.error) { el.innerHTML = `<div class="ibx-empty"><strong>The household could not be loaded</strong><span>${esc(S.error)}</span></div>`; return; }
    const now = Date.now();
    el.innerHTML = S.members.map((m) => `
      <div class="dev-row" data-id="${esc(m.id)}">
        <span class="dev-icon"><i data-lucide="${m.level === "admin" ? "shield" : "user"}"></i></span>
        <div class="dev-text">
          <div class="dev-name">${esc(m.name)}${m.you ? `<span class="dev-chip">You</span>` : ""}</div>
          <div class="dev-meta">${esc(memberLine(m, now))}</div>
        </div>
        ${S.canManage ? `<div class="dev-acts">
          <button class="icon-btn" data-act="invite" title="Invite" aria-label="Invite ${esc(m.name)}"><i data-lucide="qr-code"></i></button>
          <button class="icon-btn" data-act="level" title="Change level" aria-label="Change the level of ${esc(m.name)}"><i data-lucide="key-round"></i></button>
          ${canRemove(m, S.members) ? `<button class="icon-btn destructive" data-act="remove" title="Remove" aria-label="Remove ${esc(m.name)}"><i data-lucide="trash-2"></i></button>` : ""}
        </div>` : ""}
      </div>`).join("");
    icons();
  }

  // ---------------------------------------------------------------- dialogs

  let opener = null;
  function openModal(html) {
    opener = document.activeElement;
    const m = $("hh-modal");
    m.innerHTML = html;
    m.classList.remove("hidden");
    icons();
    (m.querySelector("[autofocus]") || m.querySelector("button, input, select"))?.focus();
  }
  function closeModal() {
    stopInvite(true);
    const m = $("hh-modal");
    m.classList.add("hidden");
    m.innerHTML = "";
    opener?.focus?.();
  }
  const options = (list, chosen) => list.map((o) => `<option value="${o.id}"${o.id === chosen ? " selected" : ""}>${esc(o.label)}</option>`).join("");

  function addDialog() {
    openModal(`<div class="ibx-modal-card dev-dialog" role="dialog" aria-modal="true" aria-labelledby="hh-add-title">
      <h3 id="hh-add-title">Add someone</h3>
      <form class="dev-form" id="hh-add-form">
        <label class="settings-label" for="hh-name">Name</label>
        <input class="settings-input" id="hh-name" maxlength="80" autocomplete="off" autofocus>
        <label class="settings-label" for="hh-pronoun">Dana calls them</label>
        <select class="settings-input" id="hh-pronoun">${options(PRONOUNS, "they")}</select>
        <label class="settings-label" for="hh-languages">Languages</label>
        <input class="settings-input" id="hh-languages" placeholder="e.g. en, he (empty: the server's)" autocomplete="off" spellcheck="false">
        <label class="settings-label" for="hh-level">Level</label>
        <select class="settings-input" id="hh-level">${options(LEVELS, "member")}</select>
        <p class="dev-note">An admin can manage the household, the server and its addons.</p>
        <div class="dev-error" role="alert"></div>
        <div class="ibx-btns dev-end"><button type="button" class="btn-secondary" data-close>Cancel</button>
          <button type="submit" class="btn-primary">Add</button></div>
      </form></div>`);
    $("hh-add-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const b = await api("POST", "/api/household/members",
          newMemberBody($("hh-name").value, $("hh-pronoun").value, $("hh-languages").value, $("hh-level").value));
        await load();
        invite(b.member);
      } catch (err) {
        $("hh-modal").querySelector(".dev-error").textContent = err.message;
      }
    });
  }

  function levelDialog(m) {
    const locked = !canSetLevel(m, "member", S.members);
    openModal(`<div class="ibx-modal-card dev-dialog" role="dialog" aria-modal="true" aria-labelledby="hh-level-title">
      <h3 id="hh-level-title">${esc(m.name)}'s level</h3>
      <form class="dev-form" id="hh-level-form">
        <select class="settings-input" id="hh-level-choice" ${locked ? "disabled" : ""} autofocus>${options(LEVELS, m.level)}</select>
        <p class="dev-note">${locked ? "The last admin stays admin: make someone else admin first."
          : "An admin can manage the household, the server and its addons; a member only their own things."}</p>
        <div class="dev-error" role="alert"></div>
        <div class="ibx-btns dev-end"><button type="button" class="btn-secondary" data-close>Cancel</button>
          <button type="submit" class="btn-primary" ${locked ? "disabled" : ""}>Save</button></div>
      </form></div>`);
    $("hh-level-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("PATCH", `/api/household/members/${encodeURIComponent(m.id)}`, { level: $("hh-level-choice").value });
        closeModal();
        load();
      } catch (err) {
        $("hh-modal").querySelector(".dev-error").textContent = err.message;
      }
    });
  }

  function removeDialog(m) {
    openModal(`<div class="ibx-modal-card dev-dialog" role="alertdialog" aria-modal="true" aria-labelledby="hh-rm-title">
      <h3 id="hh-rm-title">Remove ${esc(m.name)}?</h3>
      <p class="dev-note">Everything that is theirs in Dana goes with them: their chats, tasks, lists, people, addons and
        devices. Their devices are signed out at once. This cannot be undone.</p>
      <div class="dev-error" role="alert"></div>
      <div class="ibx-btns dev-end"><button type="button" class="btn-secondary" data-close autofocus>Cancel</button>
        <button type="button" class="btn-primary dev-danger" data-confirm>Remove</button></div></div>`);
    $("hh-modal").querySelector("[data-confirm]").addEventListener("click", async () => {
      try {
        await api("DELETE", `/api/household/members/${encodeURIComponent(m.id)}`);
        closeModal();
        load();
      } catch (err) {
        $("hh-modal").querySelector(".dev-error").textContent = err.message;
      }
    });
  }

  // ---------------------------------------------------------------- invite

  async function invite(m) {
    stopInvite(true);
    openModal(`<div class="ibx-modal-card dev-dialog" role="dialog" aria-modal="true" aria-labelledby="hh-inv-title">
      <h3 id="hh-inv-title">Invite ${esc(m.name)}</h3>
      <div id="hh-inv-body"><div class="dev-skel"></div></div></div>`);
    try {
      const [phone, browser] = await Promise.all(inviteBodies(m.id).map((b) => api("POST", "/api/devices/pairings", b)));
      S.invite = { member: m, phone: { ...phone, status: "pending" }, browser: { ...browser, status: "pending" } };
    } catch (err) {
      const body = $("hh-inv-body");
      if (body) body.innerHTML = `<p class="dev-error">${esc(err.message)}</p>
        <div class="ibx-btns dev-end"><button type="button" class="btn-secondary" data-close>Close</button></div>`;
      return;
    }
    renderInvite();
    S.tick = setInterval(tickInvite, 1000);
    S.poll = setInterval(pollInvite, POLL_MS);
  }

  function renderInvite() {
    const inv = S.invite;
    const body = $("hh-inv-body");
    if (!inv || !body) return;
    const D = devicesCore();
    const part = (p, how, qr) => p.status === "paired"
      ? `<div class="dev-done"><i data-lucide="check-circle-2"></i><strong>${esc(p.device?.name || "Signed in")}</strong></div>`
      : `${how}${qr ? `<div class="dev-qr">${D.qrSvg(p.qr)}</div>` : ""}
         <div class="dev-code" aria-label="Code ${esc(p.code.split("").join(" "))}">${esc(D.showCode(p.code))}</div>`;
    body.innerHTML = `
      <div class="hh-invite">
        <section class="hh-invite-part">
          <span class="settings-label">Their phone</span>
          ${part(inv.phone, `<p class="dev-note">In the Dana app, choose <strong>Pair with a code</strong>, then scan this or type the code.</p>`, true)}
        </section>
        <section class="hh-invite-part">
          <span class="settings-label">A browser</span>
          ${part(inv.browser, `<p class="dev-note">Open <strong>${esc(location.origin)}/login</strong> and type this code.</p>`, false)}
        </section>
      </div>
      <div class="dev-status" role="status" aria-live="polite"><span id="hh-countdown"></span></div>
      <div class="ibx-btns dev-end"><button type="button" class="btn-secondary" data-close>Close</button></div>`;
    icons();
    tickInvite();
  }

  function tickInvite() {
    const inv = S.invite;
    const el = $("hh-countdown");
    if (!inv || !el) return;
    const waiting = [inv.phone, inv.browser].filter((p) => p.status === "pending");
    if (!waiting.length) { el.textContent = "Done"; stopInvite(false); return; }
    const left = devicesCore().countdown(waiting[0].expires_at, Date.now());
    el.textContent = left === "0:00" ? "The codes expired: close and invite again." : `Valid for ${left}`;
    if (left === "0:00") stopInvite(false);
  }

  async function pollInvite() {
    const inv = S.invite;
    if (!inv) return;
    let changed = false;
    for (const p of [inv.phone, inv.browser]) {
      if (p.status !== "pending") continue;
      try {
        const st = await api("GET", `/api/devices/pairings/${encodeURIComponent(p.id)}`);
        if (st.status !== "pending") { p.status = st.status; p.device = st.device; changed = true; }
      } catch (_) { /* the next poll tries again */ }
    }
    if (changed) { renderInvite(); load(); }
  }

  function stopInvite(cancel) {
    clearInterval(S.poll);
    clearInterval(S.tick);
    S.poll = S.tick = null;
    const inv = S.invite;
    if (cancel && inv) {
      for (const p of [inv.phone, inv.browser]) {
        if (p.status === "pending") api("DELETE", `/api/devices/pairings/${encodeURIComponent(p.id)}`).catch(() => {});
      }
      S.invite = null;
    }
  }

  // ---------------------------------------------------------------- wiring

  $("hh-add")?.addEventListener("click", addDialog);
  $("hh-refresh")?.addEventListener("click", load);
  $("hh-list")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    const row = e.target.closest("[data-id]");
    const m = row && S.members.find((x) => x.id === row.dataset.id);
    if (!b || !m) return;
    if (b.dataset.act === "invite") invite(m);
    else if (b.dataset.act === "level") levelDialog(m);
    else removeDialog(m);
  });
  $("hh-modal")?.addEventListener("click", (e) => {
    if (e.target.id === "hh-modal" || e.target.closest("[data-close]")) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("hh-modal")?.classList.contains("hidden")) closeModal();
  });

  window.DanaHousehold = {
    show() { load(); },
    hide() { if (!$("hh-modal")?.classList.contains("hidden")) closeModal(); },
  };
  load();
  if (location.hash === "#household") setTimeout(() => window.danaSwitchTab?.("household"), 0);
})();
