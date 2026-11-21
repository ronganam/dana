/* Calendar — the coming 30 days of events (GET /api/calendar/events), the calendar addon's page
 * (Dana.panel, static/dana.js). The full-screen TV view is /calendar (static/calendar-tv.html).
 */
(() => {
  "use strict";

  const TEMPLATE = `
    <header class="top-bar">
        <span class="top-bar-title">Calendar Hub</span>
        <div class="top-bar-actions">
            <button class="icon-btn" data-refresh title="Refresh">
                <i data-lucide="refresh-cw"></i>
            </button>
        </div>
    </header>
    <div style="flex:1;overflow-y:auto;padding:24px;" data-events></div>
  `;

  function when(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (isNaN(date.getTime())) return iso;
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  async function load(el, api) {
    const cont = el.querySelector("[data-events]");
    cont.innerHTML = `<span style="font-size:12px;color:var(--mute);">Loading calendar events...</span>`;
    const start = new Date().toISOString();
    const end = new Date(Date.now() + 30 * 86400000).toISOString();
    const r = await api.json("GET", `/api/calendar/events?start_date=${encodeURIComponent(start)}&end_date=${encodeURIComponent(end)}`);
    if (r.status !== 200) {
      cont.innerHTML = `<span style="font-size:12px;color:var(--destructive);">Error loading calendar.</span>`;
    } else if (!r.body || r.body.length === 0) {
      cont.innerHTML = `
        <div class="pane-empty-state">
            <i data-lucide="calendar" style="width:40px;height:40px;opacity:0.4;"></i>
            <span>No upcoming calendar events.</span>
        </div>`;
    } else {
      cont.innerHTML = r.body.map((ev) => `
        <div class="task-card" style="margin-bottom:8px;">
            <div style="flex:1;">
                <strong style="font-size:14px;color:var(--ink);">${api.esc(ev.summary || "Event")}</strong>
                <div style="display:flex;gap:8px;font-size:11.5px;color:var(--mute);margin-top:4px;">
                    <span>📅 ${api.esc(when(ev.start?.dateTime || ev.start?.date))}</span>
                    ${ev.location ? `<span>📍 ${api.esc(ev.location)}</span>` : ""}
                </div>
            </div>
        </div>`).join("");
    }
    api.icons();
  }

  Dana.panel({
    mount(el, api) {
      if (!el.firstChild) {
        el.innerHTML = TEMPLATE;
        el.querySelector("[data-refresh]").addEventListener("click", () => load(el, api));
      }
      load(el, api);
    },
  });
})();
