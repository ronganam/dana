/* The Thirsty plants dashboard widget (Dana.widget, static/dana.js), drawing the data of
 * @addon.widget("plants_thirsty") in now.py. */
// --- reference: widget_web ---
(() => {
  "use strict";
  Dana.widget("plants_thirsty", {
    mount(el, w, api) {
      const { heading, plants } = w.data;
      el.innerHTML = `<div class="dw-sub">${api.esc(heading)}</div>` + (plants.length
        ? `<ul class="dw-list">${plants.map((n) => `<li class="dw-item">${api.esc(n)}</li>`).join("")}</ul>`
        : `<div class="dw-empty">All watered</div>`);
    },
  });
})();
// --- end ---
