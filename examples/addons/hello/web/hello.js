/* The hello page (Dana.panel, static/dana.js): the user's greeting and who they still have to greet,
 * from GET /api/hello. */
(() => {
  "use strict";
  Dana.panel({
    async mount(el, api) {
      if (!el.firstChild) {
        el.innerHTML = `<header class="top-bar"><span class="top-bar-title">Hello</span></header>
          <div class="hello-body"><p data-greeting></p><ul data-pending></ul></div>`;
      }
      const { greeting, pending } = await api.call("/api/hello");
      el.querySelector("[data-greeting]").textContent = `${greeting}, ${api.me ? api.me.name : "you"}!`;
      el.querySelector("[data-pending]").innerHTML = pending.length
        ? pending.map((name) => `<li>Greet ${api.esc(name)}</li>`).join("")
        : "<li>Nobody left to greet.</li>";
    },
  });
})();
