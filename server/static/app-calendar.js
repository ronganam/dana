/* The web app — the Calendar screen (static/app.js says how the app's scripts fit together).
 *
 * The coming events (/api/calendar/events).
 */

// ══════════════════════════════════════════════════════════════════════
// 10. SCREEN 7: CALENDAR & SCREEN 8: DEBUG CONTROLLERS
// ══════════════════════════════════════════════════════════════════════

async function loadCalendar() {
    const cont = document.getElementById("calendar-events-container");
    if (!cont) return;
    cont.innerHTML = `<span style="font-size:12px;color:var(--mute);">Loading calendar events...</span>`;

    try {
        const startStr = new Date().toISOString();
        const endStr = new Date(Date.now() + 30 * 86400000).toISOString();
        const res = await fetch(`/api/calendar/events?start_date=${encodeURIComponent(startStr)}&end_date=${encodeURIComponent(endStr)}`);
        if (res.ok) {
            const events = await res.json();
            if (!events || events.length === 0) {
                cont.innerHTML = `
                        <div class="pane-empty-state">
                            <i data-lucide="calendar" style="width:40px;height:40px;opacity:0.4;"></i>
                            <span>No upcoming calendar events.</span>
                        </div>
                    `;
            } else {
                cont.innerHTML = events.map(ev => `
                        <div class="task-card" style="margin-bottom:8px;">
                            <div style="flex:1;">
                                <strong style="font-size:14px;color:var(--ink);">${escapeHtml(ev.summary || "Event")}</strong>
                                <div style="display:flex;gap:8px;font-size:11.5px;color:var(--mute);margin-top:4px;">
                                    <span>📅 ${formatTime(ev.start?.dateTime || ev.start?.date)}</span>
                                    ${ev.location ? `<span>📍 ${escapeHtml(ev.location)}</span>` : ""}
                                </div>
                            </div>
                        </div>
                    `).join("");
            }
        }
    } catch (e) {
        cont.innerHTML = `<span style="font-size:12px;color:var(--destructive);">Error loading calendar.</span>`;
    }
    refreshIcons();
}
