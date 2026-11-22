/* The essentials addon's dashboard widgets (server/docs/dashboard.md): the clock with the weather, and Now.
 * Registered with Dana.widget (static/dana.js); the data comes from addons/essentials/hooks.py. */
(() => {
  "use strict";
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const pad = (n) => String(n).padStart(2, "0");
  const rtl = (text) => /[֐-׿؀-ۿ]/.test(text || "");

  function weatherLine(w) {
    if (!w || w.temperature == null) return "";
    const parts = [`${Math.round(w.temperature)}°`, w.condition ? w.condition.charAt(0).toUpperCase() + w.condition.slice(1) : ""];
    if (w.high != null && w.low != null) parts.push(`${Math.round(w.high)}° / ${Math.round(w.low)}°`);
    if (w.rain_chance) parts.push(`${w.rain_chance}% rain`);
    return parts.filter(Boolean).join(" · ");
  }

  // The time is the device's own clock; seconds only by day (dropped in night mode).
  Dana.widget("clock", {
    style: "widgets.css",
    bare: true,
    mount(el, w, api) {
      el.innerHTML = `<div class="ew-clock"><span class="ew-date"></span><span class="ew-time"></span>`
        + `<span class="ew-weather dw-sub"></span></div>`;
      const date = el.querySelector(".ew-date"), time = el.querySelector(".ew-time");
      const tick = () => {
        const now = new Date();
        date.textContent = `${DAYS[now.getDay()]}, ${pad(now.getDate())}/${pad(now.getMonth() + 1)}`;
        const hm = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
        time.textContent = api.night() || w.size === "small" ? hm : `${hm}:${pad(now.getSeconds())}`;
      };
      tick();
      el._tick = setInterval(tick, 1000);
      this.update(el, w, api);
    },
    update(el, w, api) {
      api.setValue(el.querySelector(".ew-weather"), weatherLine(w.data && w.data.weather));
    },
    unmount(el) { clearInterval(el._tick); },
  });

  Dana.widget("now", {
    style: "widgets.css",
    mount(el, w, api) {
      const d = w.data || {};
      const items = d.items || [];
      if (!items.length) {
        el.innerHTML = `<div class="dw-empty">${api.esc(d.lede || "Nothing needs you right now.")}</div>`;
        return;
      }
      const more = d.total > items.length ? `<div class="dw-sub dw-mute">+${d.total - items.length} more</div>` : "";
      el.innerHTML = (d.lede ? `<div class="dw-sub ew-lede">${api.esc(d.lede)}</div>` : "")
        + `<ul class="dw-list">${items.map((i) => `<li class="dw-item${rtl(i.title) ? " dw-rtl" : ""}">`
          + `<span class="dw-dot ew-kind-${api.esc(i.kind)}"></span><span class="dw-grow">${api.esc(i.title)}</span>`
          + `<span class="dw-end">${api.esc(i.source || "")}</span></li>`).join("")}</ul>${more}`;
    },
  });
})();
