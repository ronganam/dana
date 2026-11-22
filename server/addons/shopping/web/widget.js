/* The shopping addon's dashboard widget (server/docs/dashboard.md): the active list, live. Its data comes from
 * shopping_widget (addons/shopping/hooks.py); the list's stream (GET /api/shopping/events) says when it changed,
 * and the widget reads its data again. Registered with Dana.widget (static/dana.js). */
(() => {
  "use strict";
  const rtl = (text) => /[֐-׿؀-ۿ]/.test(text || "");

  function draw(el, w, api) {
    const d = w.data || {};
    const items = d.items || [];
    const head = `<div class="sw-head"><span class="dw-big">${api.esc(String(d.to_buy || 0))}</span>`
      + `<span class="dw-sub">to buy${d.list && d.list.name ? ` · ${api.esc(d.list.name)}` : ""}</span></div>`;
    const list = items.length
      ? `<ul class="dw-list sw-list">${items.map((i) => `<li class="dw-item${rtl(i.name) ? " dw-rtl" : ""}">`
          + `<span class="dw-grow">${api.esc(i.name)}</span>`
          + (i.quantity ? `<span class="dw-end">${api.esc(i.quantity)}</span>` : "") + `</li>`).join("")}</ul>`
        + (d.to_buy > items.length ? `<div class="dw-sub dw-mute">+${d.to_buy - items.length} more</div>` : "")
      : `<div class="dw-empty">The list is empty</div>`;
    el.innerHTML = head + list;
  }

  Dana.widget("shopping", {
    style: "widget.css",
    mount(el, w, api) {
      draw(el, w, api);
      let version = null;
      el._stream = api.stream("/api/shopping/events", {
        changed: (msg) => {
          if (version !== null && msg && msg.version !== version) api.refresh();
          version = msg ? msg.version : version;
        },
        lists: () => api.refresh(),
      });
    },
    update(el, w, api) { draw(el, w, api); },
    unmount(el) { if (el._stream) el._stream.close(); },
  });
})();
