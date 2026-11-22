/* Devices — the signed-in user's devices (phones, watches, tablets, TVs, browsers) and pairing a new one.
 *
 *   GET    /api/devices                          mine; `current` marks the one making the request
 *   PATCH  /api/devices/{id} {name, room, role}  rename, the room, a tablet's or TV's household role
 *   DELETE /api/devices/{id}                     remove: its token stops working at once
 *   POST   /api/devices/pairings {kind, role?, room?} → {id, code, expires_at, qr}
 *   GET    /api/devices/pairings/{id}            polled while the code shows: pending | paired | expired
 *   DELETE /api/devices/pairings/{id}            the dialog closed before the device paired
 *
 * Pairing: choose the kind (and, for a tablet or a TV, its room and role), then the QR code (what the app
 * scans: `qr`, drawn with the vendored qrcode-generator) and the typed code with a five-minute countdown; the
 * dialog follows the pairing and says when the device has paired. A browser gets no QR: it types the code on
 * /login. Server side: core/devices_api.py, server/docs/devices.md.
 *
 * Two parts: DanaDevicesCore (pure: labels, icons, times, the code's display, the QR drawing; tested by
 * test_web_devices.mjs; static/household.js reuses it for invites)
 * and the controller below it (window.DanaDevices = {show, hide}; app-nav.js calls them on tab switches).
 */
(() => {
  "use strict";

  const KINDS = [
    { id: "phone", label: "Phone", icon: "smartphone" },
    { id: "watch", label: "Watch", icon: "watch" },
    { id: "tablet", label: "Tablet", icon: "tablet" },
    { id: "tv", label: "TV", icon: "tv" },
    { id: "browser", label: "Browser", icon: "globe" },
    { id: "other", label: "Other", icon: "cpu" },
  ];
  const ROLES = [
    { id: "", label: "Personal" },
    { id: "kitchen", label: "Kitchen" },
    { id: "bedroom", label: "Bedroom" },
    { id: "hall", label: "Hall" },
    { id: "living_room", label: "Living room" },
  ];
  const ROLE_KINDS = new Set(["tablet", "tv"]);
  const POLL_MS = 2000;

  const kindOf = (id) => KINDS.find((k) => k.id === id) || KINDS[KINDS.length - 1];
  const roleLabel = (id) => (ROLES.find((r) => r.id === id) || { label: id }).label;

  /** "4 min ago" from an ISO time and now (ms); "never" without one. */
  function ago(iso, now) {
    const t = Date.parse(iso || "");
    if (!Number.isFinite(t)) return "never";
    const s = Math.max(0, Math.round((now - t) / 1000));
    if (s < 90) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 36 * 3600) return `${Math.round(s / 3600)} h ago`;
    if (s < 30 * 86400) return `${Math.round(s / 86400)} days ago`;
    return new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }

  /** The line under a device's name: kind, room / role, when it was last seen (or that it is not paired). */
  function metaLine(d, now) {
    const parts = [kindOf(d.kind).label];
    const place = [d.room, d.role ? roleLabel(d.role) : ""].filter(Boolean);
    if (place.length) parts.push([...new Set(place)].join(" · "));
    parts.push(d.paired ? (d.current ? "this device" : `seen ${ago(d.last_seen_at, now)}`) : "not paired: pair it again");
    return parts.join(" · ");
  }

  /** "ABCD EFGH": the code in two groups of four, as it is read out. */
  const showCode = (code) => String(code || "").replace(/^(.{4})(.{4})$/, "$1 $2");

  /** "4:59" left until an ISO time (0:00 once past). */
  function countdown(iso, now) {
    const left = Math.max(0, Math.floor((Date.parse(iso || "") - now) / 1000)) || 0;
    return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  }

  /** The request body for a new pairing; a role (and room) only for a tablet or a TV. */
  function pairingBody(kind, role, room) {
    const body = { kind };
    if (ROLE_KINDS.has(kind)) {
      if (role) body.role = role;
      if (room && room.trim()) body.room = room.trim();
    }
    return body;
  }

  /** The pairing QR code as SVG (the vendored qrcode-generator); "" without it. Household invites draw it too. */
  function qrSvg(text) {
    const make = typeof window !== "undefined" ? window.qrcode : undefined;
    if (typeof make !== "function") return "";
    const qr = make(0, "M");
    qr.addData(text);
    qr.make();
    return qr.createSvgTag({ cellSize: 5, margin: 2, scalable: true, alt: "QR code for pairing" });
  }

  const core = { KINDS, ROLES, ROLE_KINDS, kindOf, roleLabel, ago, metaLine, showCode, countdown, pairingBody, qrSvg };
  (typeof window !== "undefined" ? window : globalThis).DanaDevicesCore = core;
  if (typeof document === "undefined" || !document.getElementById("panel-devices")) return;

  // ---------------------------------------------------------------- controller

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (id) => document.getElementById(id);
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };

  const S = { devices: [], loading: false, error: "", visible: false, pairing: null, poll: null, tick: null };

  async function api(method, path, body) {
    const r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {},
                                  body: body ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) throw Object.assign(new Error((data && (data.detail || data.error)) || `HTTP ${r.status}`), { status: r.status, data });
    return data || {};
  }

  async function load() {
    S.loading = true;
    renderList();
    try {
      S.devices = (await api("GET", "/api/devices")).devices || [];
      S.error = "";
    } catch (e) {
      S.error = e.message;
    }
    S.loading = false;
    renderList();
  }

  function renderList() {
    const el = $("dev-list");
    if (!el) return;
    if (S.loading && !S.devices.length) { el.innerHTML = `<div class="dev-skel"></div>`; return; }
    if (S.error) { el.innerHTML = `<div class="ibx-empty"><strong>Devices could not be loaded</strong><span>${esc(S.error)}</span></div>`; return; }
    if (!S.devices.length) {
      el.innerHTML = `<div class="ibx-empty"><i data-lucide="smartphone"></i><strong>No devices yet</strong>
        <span>Pair your phone, a tablet or a TV to use Dana there.</span></div>`;
      icons();
      return;
    }
    const now = Date.now();
    el.innerHTML = S.devices.map((d) => `
      <div class="dev-row${d.paired ? "" : " unpaired"}" data-id="${esc(d.id)}">
        <span class="dev-icon"><i data-lucide="${kindOf(d.kind).icon}"></i></span>
        <div class="dev-text">
          <div class="dev-name">${esc(d.name)}${d.current ? `<span class="dev-chip">This device</span>` : ""}</div>
          <div class="dev-meta">${esc(metaLine(d, now))}</div>
        </div>
        <div class="dev-acts">
          <button class="icon-btn" data-act="edit" title="Rename" aria-label="Rename ${esc(d.name)}"><i data-lucide="pencil"></i></button>
          <button class="icon-btn destructive" data-act="remove" title="Remove" aria-label="Remove ${esc(d.name)}"><i data-lucide="trash-2"></i></button>
        </div>
      </div>`).join("");
    icons();
  }

  // ---------------------------------------------------------------- dialogs

  let opener = null;
  function openModal(html) {
    opener = document.activeElement;
    const m = $("dev-modal");
    m.innerHTML = html;
    m.classList.remove("hidden");
    icons();
    (m.querySelector("[autofocus]") || m.querySelector("button, input, select"))?.focus();
  }
  function closeModal() {
    stopPairing(true);
    const m = $("dev-modal");
    m.classList.add("hidden");
    m.innerHTML = "";
    opener?.focus?.();
  }

  function roleFields(kind, role, room) {
    if (!ROLE_KINDS.has(kind)) return "";
    return `
      <label class="settings-label" for="dev-role">Who it is for</label>
      <select class="settings-input" id="dev-role">${ROLES.map((r) => `<option value="${r.id}"${r.id === (role || "") ? " selected" : ""}>${esc(r.id ? `${r.label} (household)` : r.label)}</option>`).join("")}</select>
      <label class="settings-label" for="dev-room">Room</label>
      <input class="settings-input" id="dev-room" maxlength="40" value="${esc(room || "")}" placeholder="e.g. Kitchen">`;
  }

  function editDevice(d) {
    openModal(`<div class="ibx-modal-card dev-dialog" role="dialog" aria-modal="true" aria-labelledby="dev-edit-title">
      <h3 id="dev-edit-title">Rename ${esc(kindOf(d.kind).label.toLowerCase())}</h3>
      <form class="dev-form" id="dev-edit-form">
        <label class="settings-label" for="dev-name">Name</label>
        <input class="settings-input" id="dev-name" maxlength="60" value="${esc(d.name)}" autofocus>
        ${roleFields(d.kind, d.role, d.room)}
        <div class="dev-error" role="alert"></div>
        <div class="ibx-btns dev-end"><button type="button" class="btn-secondary" data-close>Cancel</button>
          <button type="submit" class="btn-primary">Save</button></div>
      </form></div>`);
    $("dev-edit-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = { name: $("dev-name").value };
      if (ROLE_KINDS.has(d.kind)) { body.role = $("dev-role").value; body.room = $("dev-room").value; }
      try {
        await api("PATCH", `/api/devices/${encodeURIComponent(d.id)}`, body);
        closeModal();
        load();
      } catch (err) {
        $("dev-modal").querySelector(".dev-error").textContent = err.message;
      }
    });
  }

  function removeDevice(d) {
    const what = d.current ? "This is the device you are using: you will be signed out here."
      : "It is signed out at once and has to be paired again to use Dana.";
    openModal(`<div class="ibx-modal-card dev-dialog" role="alertdialog" aria-modal="true" aria-labelledby="dev-rm-title">
      <h3 id="dev-rm-title">Remove ${esc(d.name)}?</h3>
      <p class="dev-note">${esc(what)}</p>
      <div class="ibx-btns dev-end"><button type="button" class="btn-secondary" data-close autofocus>Cancel</button>
        <button type="button" class="btn-primary dev-danger" data-confirm>Remove</button></div></div>`);
    $("dev-modal").querySelector("[data-confirm]").addEventListener("click", async () => {
      try {
        await api("DELETE", `/api/devices/${encodeURIComponent(d.id)}`);
        closeModal();
        if (d.current) { location.href = "/login"; return; }
        load();
      } catch (err) {
        $("dev-modal").querySelector(".dev-note").textContent = err.message;
      }
    });
  }

  // ---------------------------------------------------------------- pairing

  function pairDialog() {
    openModal(`<div class="ibx-modal-card dev-dialog" role="dialog" aria-modal="true" aria-labelledby="dev-pair-title">
      <h3 id="dev-pair-title">Pair a device</h3>
      <div id="dev-pair-body"></div></div>`);
    renderChoose("phone", "", "");
  }

  function renderChoose(kind, role, room) {
    const body = $("dev-pair-body");
    if (!body) return;
    body.innerHTML = `
      <div class="dev-kinds" role="radiogroup" aria-label="Kind of device">${KINDS.map((k) => `
        <button type="button" class="dev-kind${k.id === kind ? " active" : ""}" role="radio" aria-checked="${k.id === kind}" data-kind="${k.id}">
          <i data-lucide="${k.icon}"></i><span>${esc(k.label)}</span></button>`).join("")}</div>
      <div class="dev-form">${roleFields(kind, role, room)}</div>
      <div class="dev-error" role="alert"></div>
      <div class="ibx-btns dev-end"><button type="button" class="btn-secondary" data-close>Cancel</button>
        <button type="button" class="btn-primary" data-start>Show the code</button></div>`;
    icons();
    body.querySelectorAll("[data-kind]").forEach((b) => b.addEventListener("click", () =>
      renderChoose(b.dataset.kind, $("dev-role")?.value || "", $("dev-room")?.value || "")));
    body.querySelector("[data-start]").addEventListener("click", async () => {
      try {
        const p = await api("POST", "/api/devices/pairings", pairingBody(kind, $("dev-role")?.value || "", $("dev-room")?.value || ""));
        startPairing(p);
      } catch (err) {
        body.querySelector(".dev-error").textContent = err.message;
      }
    });
  }

  function startPairing(p) {
    stopPairing(false);
    S.pairing = { ...p, status: "pending" };
    const body = $("dev-pair-body");
    const browser = p.kind === "browser";
    const how = browser
      ? `On the new browser, open <strong>${esc(location.origin)}/login</strong> and type this code.`
      : "In the Dana app, choose <strong>Pair with a code</strong>, then scan this or type the code.";
    body.innerHTML = `
      <p class="dev-note">${how}</p>
      ${browser ? "" : `<div class="dev-qr">${qrSvg(p.qr)}</div>`}
      <div class="dev-code" aria-label="Pairing code ${esc(p.code.split("").join(" "))}">${esc(showCode(p.code))}</div>
      <div class="dev-status" role="status" aria-live="polite"><span id="dev-countdown"></span></div>
      <div class="ibx-btns dev-end"><button type="button" class="btn-secondary" data-close>Close</button></div>`;
    tickPairing();
    S.tick = setInterval(tickPairing, 1000);
    S.poll = setInterval(pollPairing, POLL_MS);
  }

  function tickPairing() {
    const p = S.pairing;
    const el = $("dev-countdown");
    if (!p || !el || p.status !== "pending") return;
    const left = countdown(p.expires_at, Date.now());
    el.textContent = `Waiting for the device · ${left}`;
    if (left === "0:00") finishPairing({ status: "expired" });
  }

  async function pollPairing() {
    const p = S.pairing;
    if (!p || p.status !== "pending") return;
    try {
      const st = await api("GET", `/api/devices/pairings/${encodeURIComponent(p.id)}`);
      if (st.status !== "pending") finishPairing(st);
    } catch (_) { /* the next poll tries again */ }
  }

  function finishPairing(st) {
    const p = S.pairing;
    if (!p) return;
    p.status = st.status;
    stopPairing(false);
    const body = $("dev-pair-body");
    if (!body) return;
    if (st.status === "paired") {
      body.innerHTML = `<div class="dev-done"><i data-lucide="check-circle-2"></i>
          <strong>${esc(st.device?.name || "The device")} is paired</strong>
          <span>It can use Dana now.</span></div>
        <div class="ibx-btns dev-end"><button type="button" class="btn-primary" data-close autofocus>Done</button></div>`;
      load();
    } else {
      body.innerHTML = `<p class="dev-note">The code expired before a device used it.</p>
        <div class="ibx-btns dev-end"><button type="button" class="btn-secondary" data-close>Close</button>
          <button type="button" class="btn-primary" data-again autofocus>New code</button></div>`;
      body.querySelector("[data-again]").addEventListener("click", () => renderChoose(p.kind, p.role, p.room));
    }
    icons();
  }

  function stopPairing(cancel) {
    clearInterval(S.poll);
    clearInterval(S.tick);
    S.poll = S.tick = null;
    const p = S.pairing;
    if (cancel && p && p.status === "pending") {
      api("DELETE", `/api/devices/pairings/${encodeURIComponent(p.id)}`).catch(() => {});
    }
    if (cancel) S.pairing = null;
  }

  // ---------------------------------------------------------------- wiring

  $("dev-pair")?.addEventListener("click", pairDialog);
  $("dev-refresh")?.addEventListener("click", load);
  $("dev-list")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    const row = e.target.closest("[data-id]");
    const d = row && S.devices.find((x) => x.id === row.dataset.id);
    if (!b || !d) return;
    if (b.dataset.act === "edit") editDevice(d); else removeDevice(d);
  });
  $("dev-modal")?.addEventListener("click", (e) => {
    if (e.target.id === "dev-modal" || e.target.closest("[data-close]")) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("dev-modal")?.classList.contains("hidden")) closeModal();
  });

  window.DanaDevices = {
    show() { S.visible = true; load(); },
    hide() { S.visible = false; if (!$("dev-modal")?.classList.contains("hidden")) closeModal(); },
  };
  if (location.hash === "#devices") setTimeout(() => window.danaSwitchTab?.("devices"), 0);
})();
