/* The Plants page (Dana.panel, static/dana.js): the user's plants and the messages the channel kept,
 * from GET /api/showcase/plants. */
// --- reference: page ---
(() => {
  "use strict";
  Dana.panel({
    async mount(el, api) {
      if (!el.firstChild) {
        el.innerHTML = `<header class="top-bar"><span class="top-bar-title">Plants</span></header>
          <div class="plants-body"><ul data-plants></ul><ul data-inbox></ul></div>`;
      }
      const { plants, inbox } = await api.call("/api/showcase/plants");
      el.querySelector("[data-plants]").innerHTML = plants.length
        ? plants.map((p) => `<li class="dn-row">${api.esc(p.name)}</li>`).join("")
        : "<li>No plants yet.</li>";
      el.querySelector("[data-inbox]").innerHTML = inbox.map((t) => `<li>${api.esc(t)}</li>`).join("");
    },
  });
})();
// --- end ---
