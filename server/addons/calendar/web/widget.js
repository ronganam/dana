/* The calendar addon's dashboard widget (server/docs/dashboard.md): today and tomorrow, from calendar_widget
 * (addons/calendar/hooks.py). Registered with Dana.widget (static/dana.js). */
(() => {
  "use strict";
  const rtl = (text) => /[֐-׿؀-ۿ]/.test(text || "");
  const HUES = ["sky", "plum", "teal", "sage", "amber", "rose"];
  const hue = (id) => {
    let h = 0;
    for (const c of String(id || "")) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return HUES[h % HUES.length];
  };
  const time = (ev) => {
    if (ev.all_day) return "All day";
    const d = new Date(ev.start);
    return isNaN(d) ? "" : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  };
  const past = (ev) => !ev.all_day && ev.end && new Date(ev.end) < new Date();

  Dana.widget("calendar", {
    style: "widget.css",
    mount(el, w, api) {
      const days = (w.data && w.data.days) || [];
      const limit = w.size === "small" ? 3 : (w.size === "large" ? 10 : 5);
      el.innerHTML = days.map((day, i) => {
        if (w.size === "small" && i > 0) return "";
        const evs = day.events || [];
        const shown = evs.slice(0, limit);
        return `<div class="cw-day"><div class="cw-label${i === 0 ? " cw-today" : ""}">${api.esc(day.label)}</div>`
          + (shown.length ? `<ul class="dw-list">${shown.map((ev) => `<li class="dw-item${past(ev) ? " cw-past" : ""}${rtl(ev.title) ? " dw-rtl" : ""}">`
              + `<span class="dw-dot cw-${hue(ev.calendar_id)}"></span><span class="dw-grow">${api.esc(ev.title)}</span>`
              + `<span class="dw-end">${api.esc(time(ev))}</span></li>`).join("")}</ul>`
              + (evs.length > shown.length ? `<div class="dw-sub dw-mute">+${evs.length - shown.length} more</div>` : "")
            : `<div class="dw-empty">Nothing planned</div>`)
          + `</div>`;
      }).join("");
    },
  });
})();
