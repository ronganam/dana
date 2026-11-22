/* The dashboard editor on the Devices page: a tablet's or TV's dashboard (docs/dashboard.md) as a plain list.
 *
 *   GET /api/dashboard/catalog?device=<id>  the presets, the widgets the device's owner can place, the sizes
 *   GET /api/dashboard?device=<id>          the current layout (its data is ignored here)
 *   PUT /api/dashboard?device=<id> {preset, widgets: [...] | null}  save (null: the preset's own widgets)
 *
 * Choose a preset (it replaces the list with the preset's widgets), then add, remove, reorder (up / down),
 * size and set each widget's settings; Save sends the list, or null when it is still the preset's own.
 * "Open" shows the dashboard itself (/dashboard?device=<id>). A widget placed earlier whose addon the owner
 * no longer has stays listed, marked, until it is removed.
 *
 * Two parts: DanaDashboardEditorCore (pure: list edits, the save body, labels; tested by
 * test_web_dashboard_editor.mjs) and the controller (window.DanaDashboardEditor = {open}; static/devices.js
 * calls it from a tablet's or TV's row with its own modal helpers).
 */
(() => {
  "use strict";

  const SIZE_LABELS = { small: "Small", medium: "Medium", large: "Large" };

  /** The catalog entry of a widget key, or null (its addon is no longer the owner's). */
  const infoOf = (catalog, key) => (catalog || []).find((c) => c.key === key) || null;

  /** An editable copy of a placed widget: id, widget, size, and every setting it declares (the catalog's
   *  default where it has none), as strings. */
  function entry(w, catalog) {
    const settings = {};
    for (const f of (infoOf(catalog, w.widget) || {}).settings || []) settings[f.name] = String(f.default ?? "");
    for (const [k, v] of Object.entries(w.settings || {})) settings[k] = String(v ?? "");
    return { id: w.id || "", widget: w.widget, size: w.size, settings };
  }

  /** A widget from the catalog, at its first size, its settings at their defaults; no id (the server gives one). */
  const fresh = (info) => entry({ widget: info.key, size: (info.sizes || ["medium"])[0] }, [info]);

  /** The list with the widget at `index` moved by `delta` (-1 up, +1 down); unchanged at an end. */
  function move(list, index, delta) {
    const to = index + delta;
    if (index < 0 || index >= list.length || to < 0 || to >= list.length) return list.slice();
    const out = list.slice();
    const [w] = out.splice(index, 1);
    out.splice(to, 0, w);
    return out;
  }

  const remove = (list, index) => list.filter((_, i) => i !== index);
  const add = (list, info) => [...list, fresh(info)];
  const withSize = (list, index, size) => list.map((w, i) => (i === index ? { ...w, size } : w));
  const withSetting = (list, index, name, value) =>
    list.map((w, i) => (i === index ? { ...w, settings: { ...w.settings, [name]: String(value) } } : w));

  /** The preset's own widgets as an editable list. */
  function presetList(presets, key, catalog) {
    const p = (presets || []).find((x) => x.key === key);
    return p ? p.widgets.map((w) => entry(w, catalog)) : [];
  }

  const comparable = (list) => JSON.stringify(list.map((w) => [w.widget, w.size,
    Object.keys(w.settings).sort().map((k) => [k, w.settings[k]])]));

  /** True when the list is still the preset's own widgets (same order, sizes and settings). */
  function isPreset(list, presets, key, catalog) {
    return comparable(list) === comparable(presetList(presets, key, catalog));
  }

  /** The PUT body: the preset, and the widgets (null when they are the preset's own). */
  function saveBody(preset, list, presets, catalog) {
    return { preset, widgets: isPreset(list, presets, preset, catalog) ? null : list.map((w) => {
      const out = { widget: w.widget, size: w.size, settings: { ...w.settings } };
      if (w.id) out.id = w.id;
      return out;
    }) };
  }

  /** The catalog's widgets not yet on the list first, then the rest (a widget may be placed twice). */
  function addable(catalog, list) {
    const placed = new Set(list.map((w) => w.widget));
    return [...(catalog || []).filter((c) => !placed.has(c.key)), ...(catalog || []).filter((c) => placed.has(c.key))];
  }

  const sizeLabel = (s) => SIZE_LABELS[s] || s;

  const core = { entry, fresh, move, remove, add, withSize, withSetting, presetList, isPreset, saveBody, infoOf,
                 addable, sizeLabel };
  (typeof window !== "undefined" ? window : globalThis).DanaDashboardEditorCore = core;
  if (typeof document === "undefined") return;

  // ---------------------------------------------------------------- controller

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };

  async function api(method, path, body) {
    const r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {},
                                  body: body ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) throw new Error((data && (data.message || data.error)) || `HTTP ${r.status}`);
    return data || {};
  }

  /** Open the editor for a tablet or TV in the page's modal: `ui` = {openModal(html) → card root, closeModal()}. */
  async function open(device, ui) {
    const q = `?device=${encodeURIComponent(device.id)}`;
    const root = ui.openModal(`<div class="ibx-modal-card dev-dialog dde" role="dialog" aria-modal="true" aria-labelledby="dde-title">
      <div class="dde-head"><h3 id="dde-title">Dashboard · ${esc(device.name)}</h3>
        <a class="btn-secondary dde-open" href="/dashboard${q}" target="_blank" rel="noopener"><i data-lucide="external-link"></i>Open</a></div>
      <div class="dde-body"><div class="dev-skel"></div></div></div>`);
    const body = root.querySelector(".dde-body");
    let catalog, list, preset;
    try {
      const [cat, layout] = await Promise.all([api("GET", `/api/dashboard/catalog${q}`), api("GET", `/api/dashboard${q}`)]);
      catalog = cat;
      preset = layout.preset;
      list = layout.widgets.map((w) => entry(w, cat.widgets));
    } catch (e) {
      body.innerHTML = `<p class="dev-error">${esc(e.message)}</p>
        <div class="ibx-btns dev-end"><button type="button" class="btn-secondary" data-close>Close</button></div>`;
      return;
    }
    let open_ = -1;   // the widget whose settings are shown

    function settingsFields(w, i, info) {
      if (!info || !(info.settings || []).length) return "";
      return `<div class="dde-settings">${info.settings.map((f) => `
        <label class="settings-label" for="dde-s-${i}-${esc(f.name)}">${esc(f.label || f.name)}</label>
        <input class="settings-input" id="dde-s-${i}-${esc(f.name)}" data-setting="${esc(f.name)}" data-i="${i}"
          maxlength="300" value="${esc(w.settings[f.name] ?? "")}" placeholder="${esc(f.placeholder || "")}">
        ${f.help ? `<span class="dde-help">${esc(f.help)}</span>` : ""}`).join("")}</div>`;
    }

    function draw() {
      const presets = catalog.presets;
      const rows = list.map((w, i) => {
        const info = infoOf(catalog.widgets, w.widget);
        const sizes = info ? info.sizes : [w.size];
        const hasSettings = info && (info.settings || []).length;
        return `<li class="dde-row${info ? "" : " gone"}">
          <div class="dde-line">
            <span class="dde-icon"><i data-lucide="${esc((info && info.icon) || "square")}"></i></span>
            <div class="dde-text"><div class="dde-name">${esc(info ? info.title : w.widget)}</div>
              <div class="dde-meta">${esc(info ? info.description : "Its add-on is not connected: it shows nothing")}</div></div>
            <select class="settings-input dde-size" data-size="${i}" aria-label="Size">${sizes.map((s) =>
              `<option value="${esc(s)}"${s === w.size ? " selected" : ""}>${esc(sizeLabel(s))}</option>`).join("")}</select>
            <div class="dev-acts">
              ${hasSettings ? `<button class="icon-btn${open_ === i ? " active" : ""}" data-act="settings" data-i="${i}" title="Settings" aria-label="Settings" aria-expanded="${open_ === i}"><i data-lucide="sliders-horizontal"></i></button>` : ""}
              <button class="icon-btn" data-act="up" data-i="${i}" title="Move up" aria-label="Move up"${i === 0 ? " disabled" : ""}><i data-lucide="arrow-up"></i></button>
              <button class="icon-btn" data-act="down" data-i="${i}" title="Move down" aria-label="Move down"${i === list.length - 1 ? " disabled" : ""}><i data-lucide="arrow-down"></i></button>
              <button class="icon-btn destructive" data-act="remove" data-i="${i}" title="Remove" aria-label="Remove"><i data-lucide="x"></i></button>
            </div>
          </div>
          ${open_ === i ? settingsFields(w, i, info) : ""}
        </li>`;
      }).join("");
      const choices = addable(catalog.widgets, list);
      body.innerHTML = `
        <div class="dev-form">
          <label class="settings-label" for="dde-preset">Preset</label>
          <select class="settings-input" id="dde-preset">${presets.map((p) =>
            `<option value="${esc(p.key)}"${p.key === preset ? " selected" : ""}>${esc(p.title)}</option>`).join("")}</select>
          <span class="dde-help" id="dde-state"></span>
        </div>
        ${list.length ? `<ol class="dde-list">${rows}</ol>` : `<p class="dev-note">No widgets: the dashboard is empty.</p>`}
        <div class="dde-add">
          <select class="settings-input" id="dde-add" aria-label="Widget to add">${choices.map((c) =>
            `<option value="${esc(c.key)}">${esc(c.title)}</option>`).join("")}</select>
          <button type="button" class="btn-secondary" data-act="add"${choices.length ? "" : " disabled"}><i data-lucide="plus"></i>Add</button>
        </div>
        <div class="dev-error" role="alert"></div>
        <div class="ibx-btns dev-end">
          <button type="button" class="btn-secondary" data-act="reset" id="dde-reset">Reset</button>
          <button type="button" class="btn-secondary" data-close>Cancel</button>
          <button type="button" class="btn-primary" data-act="save">Save</button></div>`;
      icons();
      state();
    }

    /** Whether the list is still the preset's own: the line under the preset and the Reset button. */
    function state() {
      const own = isPreset(list, catalog.presets, preset, catalog.widgets);
      body.querySelector("#dde-state").textContent = own ? "The preset's own widgets."
        : "Edited: Reset brings back the preset's own widgets.";
      body.querySelector("#dde-reset").disabled = own;
    }

    body.addEventListener("change", (e) => {
      const t = e.target;
      if (t.id === "dde-preset") { preset = t.value; list = presetList(catalog.presets, preset, catalog.widgets); open_ = -1; draw(); }
      else if (t.dataset.size !== undefined) { list = withSize(list, Number(t.dataset.size), t.value); state(); }
    });
    body.addEventListener("input", (e) => {
      const t = e.target;
      if (t.dataset.setting) { list = withSetting(list, Number(t.dataset.i), t.dataset.setting, t.value); state(); }
    });
    body.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-act]");
      if (!b || b.disabled) return;
      const i = Number(b.dataset.i);
      const act = b.dataset.act;
      if (act === "up" || act === "down") { list = move(list, i, act === "up" ? -1 : 1); open_ = -1; }
      else if (act === "remove") { list = remove(list, i); open_ = -1; }
      else if (act === "settings") { open_ = open_ === i ? -1 : i; }
      else if (act === "add") {
        const info = infoOf(catalog.widgets, body.querySelector("#dde-add").value);
        if (info) { list = add(list, info); open_ = (info.settings || []).length ? list.length - 1 : -1; }
      } else if (act === "reset") { list = presetList(catalog.presets, preset, catalog.widgets); open_ = -1; }
      else if (act === "save") {
        b.disabled = true;
        try {
          await api("PUT", `/api/dashboard${q}`, saveBody(preset, list, catalog.presets, catalog.widgets));
          ui.closeModal();
        } catch (err) {
          body.querySelector(".dev-error").textContent = err.message;
          b.disabled = false;
        }
        return;
      }
      draw();
    });
    draw();
  }

  window.DanaDashboardEditor = { open };
})();
