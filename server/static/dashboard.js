/* Dana — the dashboard of a tablet or TV (/dashboard; server/docs/dashboard.md, server/docs/web-design.md).
 *
 * Reads GET /api/dashboard (?device=<id>, or none: the requesting tablet / TV's own) and draws its widgets in
 * order, each by the renderer its addon's script registers with Dana.widget (static/dana.js). Each widget's data
 * is read again every `refresh` seconds (GET /api/dashboard/widget), the whole layout every LAYOUT_MS (an edit
 * made on the web reaches the wall within minutes). Without a device to show, it lists the user's tablets and TVs.
 *
 * The page itself: ?zoom= (20-300, the kiosk's scale), night mode (inside the kiosk app the tablet's light sensor
 * decides and dims the real backlight; elsewhere the 22-7 schedule and a black overlay; ?night=23-6 | off,
 * ?nightdim=0.55, ?nightlux=5, ?backlight=6), the Ask Dana button (kiosk only), burn-in drift.
 */
(() => {
    "use strict";
    const params = new URLSearchParams(window.location.search);
    const DEVICE = params.get("device") || "";
    const LAYOUT_MS = 5 * 60 * 1000;
    const grid = document.getElementById("dash-grid");
    const message = document.getElementById("dash-message");
    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const query = (extra) => {
        const q = new URLSearchParams(extra || {});
        if (DEVICE) q.set("device", DEVICE);
        const s = q.toString();
        return s ? `?${s}` : "";
    };

    // ── Page zoom (?zoom=) ──
    const URL_ZOOM = (() => {
        const z = parseInt(params.get("zoom"), 10);
        return !isNaN(z) && z >= 20 && z <= 300 ? z : 100;
    })();
    function applyZoom() {
        const z = URL_ZOOM / 100;
        document.body.style.zoom = z;
        document.body.style.setProperty("--zoom-scale", String(z));
        const w = window.innerWidth / z, h = window.innerHeight / z;
        document.documentElement.dataset.layout = w < 760 ? "narrow" : (w < 1150 || h < 660 ? "compact" : "wide");
    }
    applyZoom();
    window.addEventListener("resize", applyZoom);

    // ── Night mode ──
    const KIOSK = window.DanaKiosk && typeof window.DanaKiosk.lux === "function"
        && typeof window.DanaKiosk.setBacklight === "function" ? window.DanaKiosk : null;
    const NIGHT_WAKE_MS = 2 * 60 * 1000;
    const NIGHT_PARAM = params.get("night") || "";
    const NIGHT_OFF = NIGHT_PARAM === "off";
    const NIGHT_FIXED = /^\d{1,2}-\d{1,2}$/.test(NIGHT_PARAM);
    const USE_LIGHT_SENSOR = !!KIOSK && !NIGHT_OFF && !NIGHT_FIXED;
    const NIGHT_HOURS = (() => {
        const m = /^(\d{1,2})-(\d{1,2})$/.exec(NIGHT_FIXED ? NIGHT_PARAM : "22-7");
        return { start: +m[1] % 24, end: +m[2] % 24 };
    })();
    const num = (name, fallback, lo, hi) => {
        const v = parseFloat(params.get(name));
        return isNaN(v) ? fallback : Math.min(hi, Math.max(lo, v));
    };
    const NIGHT_LUX = num("nightlux", 5, 0.01, 10000);
    const NIGHT_DIM = num("nightdim", 0.55, 0, 0.95);
    const NIGHT_BACKLIGHT = Math.round(num("backlight", 6, 0, 255));
    let luxDark = null, luxPending = 0, luxFails = 0, nightWakeUntil = 0;
    function computeNight() {
        if (NIGHT_OFF || Date.now() < nightWakeUntil) return false;
        if (USE_LIGHT_SENSOR && luxDark !== null) return luxDark;
        const h = new Date().getHours(), { start, end } = NIGHT_HOURS;
        return start === end ? false : (start > end ? (h >= start || h < end) : (h >= start && h < end));
    }
    let nightActive = computeNight();
    document.documentElement.toggleAttribute("data-night", nightActive);
    document.documentElement.style.setProperty("--night-dim", KIOSK ? 0 : NIGHT_DIM);
    function syncBacklight() { if (KIOSK) KIOSK.setBacklight(nightActive ? NIGHT_BACKLIGHT : -1); }
    function applyNight() {
        const night = computeNight();
        if (night === nightActive) return;
        nightActive = night;
        syncBacklight();
        document.documentElement.toggleAttribute("data-night", night);
    }
    function refreshLight() {
        try {
            const lux = KIOSK.lux();            // < 0: no sensor / no reading yet
            if (!(lux >= 0)) throw new Error("no reading");
            luxFails = 0;
            const want = lux < NIGHT_LUX ? true : (lux >= NIGHT_LUX * 3 ? false : null);   // null: in the band
            if (want === null) luxPending = 0;
            else if (luxDark === null) luxDark = want;
            else if (want !== luxDark) { if (++luxPending >= 2) { luxDark = want; luxPending = 0; } }
            else luxPending = 0;
        } catch (_) {
            if (++luxFails >= 5) luxDark = null;   // no reading for ~50 s: back to the schedule
        }
        applyNight();
    }
    setInterval(applyNight, 5000);
    syncBacklight();
    if (USE_LIGHT_SENSOR) { refreshLight(); setInterval(refreshLight, 10000); }
    // A tap at night restores full brightness for NIGHT_WAKE_MS.
    document.addEventListener("pointerdown", () => {
        if (!nightActive) return;
        nightWakeUntil = Date.now() + NIGHT_WAKE_MS;
        applyNight();
    }, true);

    // Burn-in protection: nudge the layout a few px every 3 minutes.
    setInterval(() => {
        const r = () => Math.round((Math.random() * 2 - 1) * 4);
        document.documentElement.style.setProperty("--shift-x", r() + "px");
        document.documentElement.style.setProperty("--shift-y", r() + "px");
    }, 3 * 60 * 1000);

    // ── Ask Dana (the Dana Kiosk app exposes window.DanaKiosk) ──
    if (window.DanaKiosk && typeof window.DanaKiosk.openAssistant === "function") {
        const ask = document.getElementById("ask-dana");
        ask.hidden = false;
        ask.addEventListener("click", () => window.DanaKiosk.openAssistant());
    }

    // ── Widgets ──
    /** Sets `el`'s text; a value that changed after its first fill crossfades in (.dash-changed). */
    function setValue(el, text) {
        if (!el) return;
        text = String(text);
        if (el.textContent === text) return;
        const first = !el.dataset.filled;
        el.textContent = text;
        el.dataset.filled = "1";
        if (first) return;
        el.classList.remove("dash-changed");
        void el.offsetWidth;   // restart the animation
        el.classList.add("dash-changed");
    }
    const NOTES = {
        not_connected: (w) => `Turn on ${esc(w.addon || "its add-on")} on the Addons page to see this.`,
        private: () => "Only the owner of this dashboard sees its data.",
        unavailable: () => "This widget is not available on this server.",
        failed: () => "Could not load this just now.",
    };
    const mounted = new Map();   // widget id → {w, el, body, def, api, timer}
    let signature = "";
    const styles = new Set();

    async function api(path) {
        try {
            const r = await fetch(path, { cache: "no-store", credentials: "same-origin" });
            let body = null;
            try { body = await r.json(); } catch (_) { /* empty */ }
            return { status: r.status, body };
        } catch (_) {
            return { status: 0, body: null };
        }
    }

    function layoutSignature(payload) {
        return JSON.stringify(payload.widgets.map((w) => [w.id, w.widget, w.size, w.settings, w.script, w.error || ""]));
    }

    async function loadRenderer(w) {
        if (!w.script) return null;
        await window.Dana.loadScript(new URL(w.script, location.href).href).catch((e) => console.error(e));
        const reg = window.Dana.widgetDef(w.widget);
        if (!reg || reg.addon !== w.addon) return null;
        if (reg.def.style && !styles.has(reg.addon + "/" + reg.def.style)) {
            styles.add(reg.addon + "/" + reg.def.style);
            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = window.Dana.sibling(w.script, reg.def.style);
            document.head.appendChild(link);
        }
        return reg;
    }

    function note(m, text) {
        m.body.innerHTML = `<div class="dw-note">${text}</div>`;
        m.el.classList.add("dw-quiet");
    }

    function draw(m) {
        const { w } = m;
        if (w.error && w.error !== "failed") return note(m, (NOTES[w.error] || NOTES.failed)(w));
        if (!m.def) return note(m, NOTES.unavailable(w));
        if (w.error === "failed") {   // keep what is drawn, greyed; nothing drawn yet: say so
            if (m.drawn) { m.el.classList.add("dw-stale"); return; }
            return note(m, NOTES.failed(w));
        }
        m.el.classList.remove("dw-quiet", "dw-stale");
        try {
            if (m.drawn && typeof m.def.update === "function") m.def.update(m.body, w, m.api);
            else {
                m.body.innerHTML = "";
                m.def.mount(m.body, w, m.api);
            }
            m.drawn = true;
            if (window.lucide) window.lucide.createIcons();
        } catch (e) {
            console.error(`[dashboard] ${w.widget}:`, e);
            note(m, NOTES.failed(w));
        }
    }

    async function refreshOne(id) {
        const m = mounted.get(id);
        if (!m) return;
        const r = await api(`/api/dashboard/widget${query({ id })}`);
        if (r.status === 404) return reload();   // the layout changed under it
        if (r.status !== 200 || !r.body) {
            if (r.status === 403 && r.body && r.body.error === "not_connected") {
                m.w = { ...m.w, data: null, error: "not_connected", addon: r.body.addon };
            } else {
                m.w = { ...m.w, error: "failed" };
            }
        } else {
            m.w = { ...m.w, data: r.body.data, error: r.body.error || null };
        }
        draw(m);
    }

    function unmountAll() {
        for (const m of mounted.values()) {
            clearInterval(m.timer);
            if (m.def && typeof m.def.unmount === "function") {
                try { m.def.unmount(m.body, m.api); } catch (e) { console.error(e); }
            }
        }
        mounted.clear();
        grid.innerHTML = "";
    }

    async function render(payload) {
        unmountAll();
        message.hidden = true;
        grid.hidden = false;
        document.title = `Dana — ${payload.device.name || "Dashboard"}`;
        const regs = await Promise.all(payload.widgets.map(loadRenderer));
        payload.widgets.forEach((w, i) => {
            const reg = regs[i];
            const el = document.createElement("section");
            el.className = `dw dw-${w.size}`;
            el.dataset.widget = w.widget;
            const bare = reg && reg.def.bare;
            el.innerHTML = (bare ? "" : `<div class="dw-head">${esc(w.title)}</div>`) + `<div class="dw-body"></div>`;
            grid.appendChild(el);
            const m = { w, el, body: el.querySelector(".dw-body"), def: reg ? reg.def : null, drawn: false, timer: null };
            m.api = reg ? window.Dana.widgetApi(reg.addon, w.script, {
                size: w.size,
                settings: w.settings,
                refresh: () => refreshOne(w.id),
                night: () => nightActive,
                kiosk: window.DanaKiosk || null,
                setValue,
            }) : null;
            mounted.set(w.id, m);
            draw(m);
            if (w.refresh > 0 && !w.error) {
                m.timer = setInterval(() => { if (!document.hidden) refreshOne(w.id); }, w.refresh * 1000);
            }
        });
        signature = layoutSignature(payload);
    }

    async function showPicker() {
        grid.hidden = true;
        message.hidden = false;
        const r = await api("/api/devices");
        const list = ((r.body && r.body.devices) || []).filter((d) => d.kind === "tablet" || d.kind === "tv");
        message.innerHTML = list.length
            ? `<h1>Dashboards</h1><p>Choose a tablet or TV to show its dashboard.</p><ul>${list.map((d) =>
                `<li><a href="/dashboard?device=${encodeURIComponent(d.id)}">${esc(d.name)}<span>${esc(d.room || d.role.replace("_", " ") || d.kind)}</span></a></li>`).join("")}</ul>`
            : `<h1>No dashboards yet</h1><p>Pair a tablet or TV on the Devices page; its dashboard shows here.</p><p><a href="/devices">Open Devices</a></p>`;
    }

    function showMessage(title, text) {
        unmountAll();
        grid.hidden = true;
        message.hidden = false;
        message.innerHTML = `<h1>${esc(title)}</h1><p>${esc(text)}</p>`;
    }

    async function reload() {
        const r = await api(`/api/dashboard${query()}`);
        if (r.status === 400 && r.body && r.body.error === "device_required") return showPicker();
        if (r.status === 404) return showMessage("No such dashboard", "This tablet or TV is not one you can see.");
        if (r.status !== 200 || !r.body) return;   // keep what is shown; the next round tries again
        if (layoutSignature(r.body) !== signature) return render(r.body);
        for (const w of r.body.widgets) {
            const m = mounted.get(w.id);
            if (m) { m.w = w; draw(m); }
        }
    }

    reload();
    setInterval(() => { if (!document.hidden) reload(); }, LAYOUT_MS);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) reload(); });
})();
