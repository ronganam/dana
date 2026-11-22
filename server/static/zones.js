/* Zones — the signed-in user's places (Home and the others), on the Settings screen, and the place picker the
 * wizard's Home step shares (static/setup.js).
 *
 *   GET    /api/zones                     [{name, entity_id, id, latitude, longitude, radius_m, is_home}]
 *   POST   /api/zones {name, latitude, longitude, radius_m, is_home}   PATCH /api/zones/{id}   DELETE /api/zones/{id}
 *   GET    /api/places?q=                 {places: [{name, label, latitude, longitude, timezone, country_code, currency}]}
 *   POST   /api/ha/zones/import           copy Home Assistant's zones (only with the homeassistant addon on)
 *
 * Location reminders name one of these zones. Server side: core/zones.py, core/zones_api.py, server/docs/zones.md.
 *
 * Three parts: DanaZonesCore (pure: the form's checks, labels; tested by test_web_zones.mjs), DanaPlacePicker
 * (search a place by name or "Use my location"; works on any page), and the Settings card's controller.
 */
(() => {
  "use strict";

  const RADIUS_MIN = 10;
  const RADIUS_MAX = 50000;

  /** The request body of a zone from the form's raw values, or {error: {field, detail}}. */
  function zoneBody({ name = "", latitude = "", longitude = "", radius = "", isHome = false } = {}) {
    const n = String(name).replace(/\s+/g, " ").trim();
    if (!n) return { error: { field: "name", detail: "A zone needs a name." } };
    if (n.length > 60) return { error: { field: "name", detail: "At most 60 characters." } };
    const num = (v) => (String(v).trim() === "" ? NaN : Number(String(v).trim().replace(",", ".")));
    const lat = num(latitude);
    const lon = num(longitude);
    if (!Number.isFinite(lat) || lat < -90 || lat > 90) return { error: { field: "latitude", detail: "Between -90 and 90." } };
    if (!Number.isFinite(lon) || lon < -180 || lon > 180) return { error: { field: "longitude", detail: "Between -180 and 180." } };
    const r = String(radius).trim() === "" ? 100 : Math.round(num(radius));
    if (!Number.isFinite(r) || r < RADIUS_MIN || r > RADIUS_MAX) {
      return { error: { field: "radius_m", detail: `Between ${RADIUS_MIN} and ${RADIUS_MAX} metres.` } };
    }
    return { body: { name: n, latitude: lat, longitude: lon, radius_m: r, is_home: Boolean(isHome) } };
  }

  /** "32.08530, 34.78180": a centre with five decimals (about a metre). */
  const coords = (z) => `${Number(z.latitude).toFixed(5)}, ${Number(z.longitude).toFixed(5)}`;

  /** "150 m" / "1.5 km". */
  function radiusLabel(m) {
    const n = Number(m) || 0;
    return n >= 1000 ? `${(n / 1000).toFixed(n % 1000 ? 1 : 0)} km` : `${Math.round(n)} m`;
  }

  /** The line under a zone's name: Home, the radius, the centre. */
  const metaLine = (z) => [z.is_home ? "Home" : "", `within ${radiusLabel(z.radius_m)}`, coords(z)].filter(Boolean).join(" · ");

  /** A place the browser's location gave: the coordinates and this browser's timezone. */
  function hereAsPlace(position, timezone) {
    const c = position && position.coords ? position.coords : {};
    return { name: "Your location", label: "Your location", latitude: Number(c.latitude), longitude: Number(c.longitude),
             timezone: timezone || "", country_code: "", currency: "" };
  }

  const core = { RADIUS_MIN, RADIUS_MAX, zoneBody, coords, radiusLabel, metaLine, hereAsPlace };
  const G = typeof window !== "undefined" ? window : globalThis;
  G.DanaZonesCore = core;
  if (typeof document === "undefined") return;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };

  async function api(method, path, body) {
    const r = await fetch(path, { method, cache: "no-store", credentials: "same-origin",
                                  headers: body ? { "Content-Type": "application/json" } : {},
                                  body: body ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) throw Object.assign(new Error((data && (data.detail || data.error)) || `HTTP ${r.status}`), { status: r.status, data });
    return data;
  }

  // ---------------------------------------------------------------- the place picker

  /** Draws a place search (and "Use my location") into `el`; onPick(place) gets the chosen place. */
  function mountPicker(el, onPick) {
    el.innerHTML = `
      <div class="zn-picker">
        <div class="zn-search-row">
          <input class="settings-input" type="search" data-q placeholder="Search a town or place" autocomplete="off">
          <button type="button" class="btn-secondary" data-here><i data-lucide="locate-fixed"></i> Use my location</button>
        </div>
        <div class="zn-results" data-results role="listbox"></div>
        <div class="zn-note" data-note role="status" aria-live="polite"></div>
      </div>`;
    icons();
    const q = el.querySelector("[data-q]");
    const results = el.querySelector("[data-results]");
    const note = el.querySelector("[data-note]");
    let places = [];
    let timer = null;
    let seq = 0;
    const tz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (_) { return ""; } })();

    async function search() {
      const text = q.value.trim();
      const mine = ++seq;
      if (text.length < 2) { results.innerHTML = ""; note.textContent = ""; return; }
      note.textContent = "Searching…";
      let data = null;
      try { data = await api("GET", `/api/places?q=${encodeURIComponent(text)}`); } catch (_) { /* below */ }
      if (mine !== seq) return;
      places = (data && data.places) || [];
      note.textContent = places.length ? "" : "Nothing found. Use your location, or type the coordinates.";
      results.innerHTML = places.map((p, i) => `<button type="button" class="zn-result" role="option" data-i="${i}">
        <i data-lucide="map-pin"></i><span>${esc(p.label || p.name)}</span></button>`).join("");
      icons();
    }
    q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(search, 350); });
    results.addEventListener("click", (e) => {
      const b = e.target.closest("[data-i]");
      if (!b) return;
      const p = places[Number(b.dataset.i)];
      results.innerHTML = "";
      note.textContent = p.label || p.name;
      onPick(p);
    });
    el.querySelector("[data-here]").addEventListener("click", () => {
      if (!navigator.geolocation) { note.textContent = "This browser cannot share its location."; return; }
      note.textContent = "Asking the browser for your location…";
      navigator.geolocation.getCurrentPosition(
        (pos) => { note.textContent = "Your location"; onPick(hereAsPlace(pos, tz)); },
        () => { note.textContent = "The location was not shared. Search a place instead."; },
        { enableHighAccuracy: true, timeout: 15000 });
    });
  }
  G.DanaPlacePicker = { mount: mountPicker };

  // ---------------------------------------------------------------- the Settings card

  const $ = (id) => document.getElementById(id);
  if (!$("zones-card")) return;

  const S = { zones: [], error: "", ha: false };

  async function load() {
    try {
      const [list, me] = await Promise.all([api("GET", "/api/zones"), api("GET", "/api/me").catch(() => null)]);
      S.zones = Array.isArray(list) ? list : [];
      S.ha = Boolean(me && (me.addons || []).includes("homeassistant"));
      S.error = "";
    } catch (e) {
      S.error = e.message;
    }
    render();
  }

  function render() {
    const el = $("zones-list");
    $("zones-import").classList.toggle("hidden", !S.ha);
    if (S.error) { el.innerHTML = `<p class="profile-hint bad">Zones could not be loaded: ${esc(S.error)}</p>`; return; }
    if (!S.zones.length) {
      el.innerHTML = `<p class="profile-hint">No zones yet. Add Home and the places you go to, for reminders when you arrive or leave.</p>`;
      return;
    }
    el.innerHTML = S.zones.map((z) => `
      <div class="zn-row" data-id="${esc(z.id)}">
        <span class="zn-icon"><i data-lucide="${z.is_home ? "house" : "map-pin"}"></i></span>
        <div class="zn-text"><div class="zn-name">${esc(z.name)}</div><div class="zn-meta">${esc(metaLine(z))}</div></div>
        <div class="zn-acts">
          <button class="icon-btn" data-act="edit" title="Edit" aria-label="Edit ${esc(z.name)}"><i data-lucide="pencil"></i></button>
          <button class="icon-btn destructive" data-act="remove" title="Remove" aria-label="Remove ${esc(z.name)}"><i data-lucide="trash-2"></i></button>
        </div>
      </div>`).join("");
    icons();
  }

  let opener = null;
  function openModal(html) {
    opener = document.activeElement;
    const m = $("zones-modal");
    m.innerHTML = html;
    m.classList.remove("hidden");
    icons();
    (m.querySelector("[autofocus]") || m.querySelector("input, button"))?.focus();
  }
  function closeModal() {
    const m = $("zones-modal");
    m.classList.add("hidden");
    m.innerHTML = "";
    opener?.focus?.();
  }

  function editor(z) {
    const isNew = !z;
    z = z || { name: S.zones.length ? "" : "Home", latitude: "", longitude: "", radius_m: 100, is_home: !S.zones.length };
    openModal(`<div class="ibx-modal-card zn-dialog" role="dialog" aria-modal="true" aria-labelledby="zn-title">
      <h3 id="zn-title">${isNew ? "Add a zone" : `Edit ${esc(z.name)}`}</h3>
      <form class="zn-form" id="zn-form" novalidate>
        <label class="settings-label" for="zn-name">Name</label>
        <input class="settings-input" id="zn-name" maxlength="60" value="${esc(z.name)}" placeholder="e.g. Work, Gym" autofocus>
        <span class="settings-label">Where</span>
        <div id="zn-picker"></div>
        <div class="zn-grid">
          <label><span class="settings-label">Latitude</span><input class="settings-input" id="zn-lat" inputmode="decimal" value="${esc(z.latitude)}"></label>
          <label><span class="settings-label">Longitude</span><input class="settings-input" id="zn-lon" inputmode="decimal" value="${esc(z.longitude)}"></label>
          <label><span class="settings-label">Radius (m)</span><input class="settings-input" id="zn-radius" inputmode="numeric" value="${esc(z.radius_m)}"></label>
        </div>
        <label class="zn-check"><input type="checkbox" id="zn-home" ${z.is_home ? "checked" : ""}> This is my Home</label>
        <div class="dev-error" role="alert"></div>
        <div class="ibx-btns dev-end"><button type="button" class="btn-secondary" data-close>Cancel</button>
          <button type="submit" class="btn-primary">Save</button></div>
      </form></div>`);
    mountPicker($("zn-picker"), (p) => {
      $("zn-lat").value = p.latitude.toFixed(5);
      $("zn-lon").value = p.longitude.toFixed(5);
      if (!$("zn-name").value.trim() && p.name && p.name !== "Your location") $("zn-name").value = p.name;
    });
    $("zn-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const out = zoneBody({ name: $("zn-name").value, latitude: $("zn-lat").value, longitude: $("zn-lon").value,
                             radius: $("zn-radius").value, isHome: $("zn-home").checked });
      const error = $("zones-modal").querySelector(".dev-error");
      if (out.error) { error.textContent = out.error.detail; return; }
      try {
        if (isNew) await api("POST", "/api/zones", out.body);
        else await api("PATCH", `/api/zones/${encodeURIComponent(z.id)}`, out.body);
        closeModal();
        load();
      } catch (err) {
        error.textContent = err.message;
      }
    });
  }

  function remove(z) {
    openModal(`<div class="ibx-modal-card zn-dialog" role="alertdialog" aria-modal="true" aria-labelledby="zn-rm-title">
      <h3 id="zn-rm-title">Remove ${esc(z.name)}?</h3>
      <p class="dev-note">Reminders for arriving at or leaving ${esc(z.name)} stop firing until a zone has that name again.</p>
      <div class="ibx-btns dev-end"><button type="button" class="btn-secondary" data-close autofocus>Cancel</button>
        <button type="button" class="btn-primary dev-danger" data-confirm>Remove</button></div></div>`);
    $("zones-modal").querySelector("[data-confirm]").addEventListener("click", async () => {
      try {
        await api("DELETE", `/api/zones/${encodeURIComponent(z.id)}`);
        closeModal();
        load();
      } catch (err) {
        $("zones-modal").querySelector(".dev-note").textContent = err.message;
      }
    });
  }

  async function importHa() {
    const status = $("zones-status");
    status.textContent = "Copying Home Assistant's zones…";
    try {
      const r = await api("POST", "/api/ha/zones/import");
      const added = r.added.length;
      status.textContent = added ? `Added ${added} zone${added === 1 ? "" : "s"} from Home Assistant.`
        : "Nothing new: you already have every Home Assistant zone.";
      load();
    } catch (e) {
      status.textContent = e.message;
    }
  }

  $("zones-add").addEventListener("click", () => editor(null));
  $("zones-import").addEventListener("click", importHa);
  $("zones-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    const row = e.target.closest("[data-id]");
    const z = row && S.zones.find((x) => x.id === row.dataset.id);
    if (!b || !z) return;
    if (b.dataset.act === "edit") editor(z); else remove(z);
  });
  $("zones-modal").addEventListener("click", (e) => {
    if (e.target.id === "zones-modal" || e.target.closest("[data-close]")) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("zones-modal").classList.contains("hidden")) closeModal();
  });
  load();
})();
