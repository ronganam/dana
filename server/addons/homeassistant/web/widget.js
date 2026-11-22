/* The Home Assistant addon's dashboard widgets (server/docs/dashboard.md): Climate, Lights, Home Assistant
 * (chosen entities) and Batteries, from addons/homeassistant/widgets.py. Controls call POST /api/ha/service
 * (the dashboard owner's Home Assistant), then read the widget again. Registered with Dana.widget (static/dana.js). */
(() => {
  "use strict";
  const MODE_ICONS = { off: "power", cool: "snowflake", heat: "flame", fan_only: "fan", dry: "droplet", auto: "refresh-cw", heat_cool: "thermometer" };
  const FAN_ICONS = { auto: "gauge", low: "chevrons-down", mid: "minus", medium: "minus", middle: "minus", high: "chevrons-up" };
  const SHOWN_MODES = ["off", "cool", "heat", "fan_only", "dry", "auto", "heat_cool"];
  const fmt = (n) => (n == null ? "--" : (Math.round(n * 10) / 10).toString());

  async function service(api, domain, name, entity, data) {
    try {
      await api.call("/api/ha/service", { body: { domain, service: name, entity_id: entity, service_data: data || {} } });
    } catch (e) {
      console.warn("[dashboard] Home Assistant:", e.message);
    }
    setTimeout(() => api.refresh(), 800);
  }

  Dana.widget("ha_climate", {
    style: "widget.css",
    mount(el, w, api) {
      el.innerHTML = `<div class="hw-climate"><div class="hw-temps"><span class="dw-big hw-cur"></span>`
        + `<div class="hw-target"><button class="dw-pill hw-down" title="Lower">&minus;</button>`
        + `<span class="hw-set"></span><button class="dw-pill hw-up" title="Raise">+</button></div></div>`
        + `<div class="dw-sub hw-name"></div><div class="hw-modes"></div><div class="hw-fans"></div></div>`;
      const step = (dir) => {
        const d = el._data;
        if (!d || !d.entity_id || d.target == null) return;
        const next = Math.min(d.max ?? 32, Math.max(d.min ?? 16, d.target + dir * (d.step || 0.5)));
        d.target = next;
        api.setValue(el.querySelector(".hw-set"), `${fmt(next)}°`);
        clearTimeout(el._send);   // several presses in a row: one call with the last value
        el._send = setTimeout(() => service(api, "climate", "set_temperature", d.entity_id, { temperature: next }), 600);
      };
      el.querySelector(".hw-up").addEventListener("click", () => step(1));
      el.querySelector(".hw-down").addEventListener("click", () => step(-1));
      this.update(el, w, api);
    },
    update(el, w, api) {
      const d = w.data || {};
      el._data = d;
      const box = el.querySelector(".hw-climate");
      box.classList.toggle("hw-missing", !d.entity_id);
      if (!d.entity_id) {
        el.querySelector(".hw-name").textContent = d.wanted ? `${d.wanted} was not found` : "No climate device in Home Assistant";
        return;
      }
      api.setValue(el.querySelector(".hw-cur"), `${fmt(d.current)}°`);
      api.setValue(el.querySelector(".hw-set"), `${fmt(d.target)}°`);
      el.querySelector(".hw-name").textContent = d.name + (d.humidity != null ? ` · ${fmt(d.humidity)}% humidity` : "");
      const modes = SHOWN_MODES.filter((m) => (d.modes || []).includes(m));
      el.querySelector(".hw-modes").innerHTML = modes.map((m) =>
        `<button class="dw-pill${d.mode === m ? " active" : ""}" data-mode="${api.esc(m)}" title="${api.esc(m.replace("_", " "))}">`
        + `<i data-lucide="${MODE_ICONS[m] || "circle"}"></i></button>`).join("");
      el.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => {
        const m = b.dataset.mode;
        service(api, "climate", m === "off" ? "turn_off" : "set_hvac_mode", d.entity_id, m === "off" ? {} : { hvac_mode: m });
      }));
      const fans = d.mode === "off" ? [] : (d.fan_modes || []).slice(0, 5);
      el.querySelector(".hw-fans").innerHTML = d.mode === "off" ? `<span class="dw-sub dw-mute">Off</span>` : fans.map((f) =>
        `<button class="dw-pill${d.fan_mode === f ? " active" : ""}" data-fan="${api.esc(f)}" title="Fan: ${api.esc(f)}">`
        + (FAN_ICONS[f] ? `<i data-lucide="${FAN_ICONS[f]}"></i>` : api.esc(f)) + `</button>`).join("");
      el.querySelectorAll("[data-fan]").forEach((b) => b.addEventListener("click", () =>
        service(api, "climate", "set_fan_mode", d.entity_id, { fan_mode: b.dataset.fan })));
      api.icons();
    },
    unmount(el) { clearTimeout(el._send); },
  });

  Dana.widget("ha_lights", {
    style: "widget.css",
    mount(el, w, api) {
      const lights = (w.data && w.data.lights) || [];
      if (!lights.length) { el.innerHTML = `<div class="dw-empty">No lights</div>`; return; }
      const on = lights.filter((l) => l.on).length;
      el.innerHTML = `<div class="dw-sub dw-mute">${on ? `${on} on` : "All off"}</div><div class="hw-grid">`
        + lights.map((l) => `<button class="hw-tile dw-press${l.on ? " hw-on" : ""}" data-entity="${api.esc(l.entity_id)}">`
          + `<i data-lucide="lightbulb"></i><span>${api.esc(l.name)}</span>`
          + (l.on && l.brightness != null ? `<small>${l.brightness}%</small>` : "") + `</button>`).join("") + `</div>`;
      el.querySelectorAll("[data-entity]").forEach((b) => b.addEventListener("click", () => {
        b.classList.toggle("hw-on");
        service(api, "light", "toggle", b.dataset.entity);
      }));
    },
  });

  Dana.widget("ha_entities", {
    style: "widget.css",
    mount(el, w, api) {
      const list = (w.data && w.data.entities) || [];
      if (!list.length) { el.innerHTML = `<div class="dw-empty">Choose entities in the dashboard editor</div>`; return; }
      el.innerHTML = `<ul class="dw-list">${list.map((e) => `<li class="dw-item">`
        + `<span class="dw-grow">${api.esc(e.name)}</span>`
        + (e.toggles
          ? `<button class="dw-pill${e.state === "on" ? " active" : ""}" data-entity="${api.esc(e.entity_id)}">${e.state === "on" ? "On" : "Off"}</button>`
          : `<span class="dw-end">${api.esc(e.state)}${e.unit ? " " + api.esc(e.unit) : ""}</span>`) + `</li>`).join("")}</ul>`;
      el.querySelectorAll("[data-entity]").forEach((b) => b.addEventListener("click", () =>
        service(api, b.dataset.entity.split(".")[0], "toggle", b.dataset.entity)));
    },
  });

  const battColor = (pct) => (pct > 50 ? "var(--sage)" : pct > 20 ? "var(--amber)" : "var(--rose)");
  Dana.widget("ha_batteries", {
    style: "widget.css",
    mount(el, w, api) {
      const list = (w.data && w.data.batteries) || [];
      if (!list.length) { el.innerHTML = `<div class="dw-empty">Choose battery sensors in the dashboard editor</div>`; return; }
      el.innerHTML = list.map((b) => {
        const pct = b.level;
        const color = pct == null ? "var(--mute)" : battColor(pct);
        return `<div class="hw-batt"><div class="hw-batt-top"><span>${api.esc(b.name)}</span>`
          + `<b style="color:${color}">${pct == null ? "--" : pct}%</b></div>`
          + `<div class="dw-bar"><span style="width:${pct || 0}%;background:${color}"></span></div></div>`;
      }).join("");
    },
  });
})();
