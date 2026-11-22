/* The tasks addon's dashboard widget (server/docs/dashboard.md): Reminders, from reminders_widget in
 * addons/tasks/hooks.py. Registered with Dana.widget (static/dana.js). */
(() => {
  "use strict";
  const rtl = (text) => /[֐-׿؀-ۿ]/.test(text || "");
  const ROWS = { small: 3, medium: 6, large: 12 };

  Dana.widget("reminders", {
    style: "widget.css",
    mount(el, w, api) {
      const d = w.data || {};
      const rows = [];
      for (const r of d.reminders_today || []) rows.push({ title: r.title, end: r.time, tone: "timed" });
      for (const t of d.due || []) rows.push({ title: t.title, end: t.overdue ? "Overdue" : "Today", tone: t.overdue ? "overdue" : t.priority || "" });
      for (const t of d.high_priority || []) rows.push({ title: t.title, end: "", tone: "high" });
      const shown = rows.slice(0, ROWS[w.size] || 6);
      const brief = d.briefing && d.briefing.headline
        ? `<div class="tw-brief${rtl(d.briefing.headline + d.briefing.points.join(""))? " dw-rtl" : ""}">`
          + `<div class="tw-brief-head">${api.esc(d.briefing.headline)}</div>`
          + d.briefing.points.slice(0, w.size === "small" ? 1 : 3).map((p) => `<div class="dw-sub">${api.esc(p)}</div>`).join("")
          + `</div>` : "";
      const list = shown.length
        ? `<ul class="dw-list">${shown.map((r) => `<li class="dw-item${rtl(r.title) ? " dw-rtl" : ""}">`
            + `<span class="dw-dot tw-${api.esc(r.tone)}"></span><span class="dw-grow">${api.esc(r.title)}</span>`
            + (r.end ? `<span class="dw-end tw-end-${api.esc(r.tone)}">${api.esc(r.end)}</span>` : "") + `</li>`).join("")}</ul>`
          + (rows.length > shown.length ? `<div class="dw-sub dw-mute">+${rows.length - shown.length} more</div>` : "")
        : (brief ? "" : `<div class="dw-empty">Nothing due today</div>`);
      el.innerHTML = brief + list;
    },
  });
})();
