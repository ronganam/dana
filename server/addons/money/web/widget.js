/* The money addon's dashboard widget (server/docs/dashboard.md): the month so far and where it is heading, the
 * bank balance ahead; from money_widget (addons/money/hooks.py). Amounts arrive in minor units. Registered with
 * Dana.widget (static/dana.js). */
(() => {
  "use strict";
  function money(minor, currency) {
    if (minor == null) return "—";
    try {
      return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "USD", maximumFractionDigits: 0 }).format(minor / 100);
    } catch (_) {
      return `${Math.round(minor / 100)} ${currency || ""}`.trim();
    }
  }

  Dana.widget("money", {
    style: "widget.css",
    mount(el, w, api) {
      const d = w.data || {};
      const cur = d.currency;
      const over = d.on_track != null && d.typical && d.on_track > d.typical * 1.05;
      const heading = d.on_track != null
        ? `<div class="dw-sub">On track for <b class="${over ? "mw-over" : "mw-ok"}">${api.esc(money(d.on_track, cur))}</b>`
          + (d.typical ? ` · usually ${api.esc(money(d.typical, cur))}` : "") + `</div>` : "";
      const bank = d.balance != null && w.size !== "small"
        ? `<div class="mw-bank"><div class="dw-sub">In the bank <b>${api.esc(money(d.balance, cur))}</b></div>`
          + (d.low ? `<div class="dw-sub dw-mute">Lowest in ${d.low.days} days: <b class="${d.low.balance < 0 ? "mw-over" : ""}">`
            + `${api.esc(money(d.low.balance, cur))}</b> on ${api.esc(d.low.date)}</div>` : "") + `</div>` : "";
      el.innerHTML = `<div class="dw-sub dw-mute">${api.esc(d.month || "This month")}, spent so far</div>`
        + `<div class="dw-big mw-spent"></div>${heading}${bank}`;
      api.setValue(el.querySelector(".mw-spent"), money(d.spent, cur));
    },
  });
})();
