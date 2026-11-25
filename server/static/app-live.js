/* The web app — the Live strip at the top of Now (static/app.js says how the app's scripts fit together).
 *
 * The user's live activities (server/docs/live.md): one compact card each, drawn by DanaLive.cardHtml
 * (static/app-live-core.js, the pure part): the icon, title, text, context, the timer ticking every second
 * here, the chip, the progress bar and the actions. Next / Earlier switch the shown choice at once and then
 * POST /api/live/{id}/select; a server action POSTs /api/live/{id}/act; Stop (and the × of an ended one kept
 * for its final) DELETEs /api/live/{id}; a tap on the card goes where `open` says. From GET /api/live when Now
 * opens, every 20 s while Now is on screen and the tab visible, and when the window comes back (the web has no
 * live channel for these: the push goes to the phone). Hidden when there is none. server/docs/web-design.md,
 * "Now" ("Live").
 */

// ══════════════════════════════════════════════════════════════════════
// LIVE ACTIVITIES (on Now)
// ══════════════════════════════════════════════════════════════════════

const LIVE_REFRESH_MS = 20000;

const liveState = {
    items: [],          // the activities listed, newest-updated first (the server's order)
    picks: {},          // id → the choice picked here while its select request runs
    busy: {},           // id → Set of server action ids in flight
    gone: new Set(),    // ids being stopped: kept off screen until the server answers
    loaded: false,
    loading: null,
    poll: null,
    tick: null,
    zeroed: new Set(),  // "<id>|<to>" countdowns that reached zero (one refresh each)
};

// `{id}` is `<addon>:<key>`: ":" and "@" stay readable in the path.
const livePath = (id, tail) => `/api/live/${encodeURIComponent(id).replace(/%3A/gi, ":").replace(/%40/g, "@")}${tail || ""}`;
const liveFind = (id) => liveState.items.find(a => a.id === id);

function liveShow() {
    liveRefresh();
    clearInterval(liveState.poll);
    liveState.poll = setInterval(() => { if (activeTab === "now" && !document.hidden) liveRefresh(); }, LIVE_REFRESH_MS);
}

function liveHide() {
    clearInterval(liveState.poll);
    liveState.poll = null;
    liveTicking(false);
}

window.addEventListener("focus", () => { if (activeTab === "now") liveRefresh(); });
document.addEventListener("visibilitychange", () => {
    if (activeTab !== "now") return;
    if (document.hidden) liveTicking(false); else liveRefresh();
});

function liveRefresh() {
    if (liveState.loading) return liveState.loading;
    liveState.loading = (async () => {
        const r = await nowRequest("GET", "/api/live");
        liveState.loading = null;
        if (r.status !== 200 || !r.body) return;     // keep what is on screen (an older server has no /api/live)
        liveState.items = window.DanaLive.parse(r.body);
        liveState.loaded = true;
        liveDraw();
    })();
    return liveState.loading;
}

// ── Drawing ────────────────────────────────────────────────────────────

/** The strip: the cards in server order; a card is drawn again only when its activity, its pick or its
 *  busy actions changed (its timer ticks in place), new ones rise in, the focus stays where it was. */
function liveDraw() {
    const L = window.DanaLive;
    const strip = document.getElementById("now-live");
    if (!strip) return;
    const shown = liveState.items.filter(a => !liveState.gone.has(a.id));
    strip.hidden = !shown.length;
    if (!shown.length) { strip.replaceChildren(); liveTicking(false); return; }
    let listEl = strip.querySelector(".lv-list");
    const first = !listEl;
    if (first) {
        strip.innerHTML = `<h2 class="now-section-h lv-h" id="now-live-h"><span class="lv-h-dot" aria-hidden="true"></span>Live</h2><div class="lv-list"></div>`;
        listEl = strip.querySelector(".lv-list");
    }
    const have = new Map([...listEl.children].map(el => [el.dataset.liveId, el]));
    const focused = document.activeElement && listEl.contains(document.activeElement) ? document.activeElement : null;
    const focusCard = focused ? focused.closest(".lv-card") : null;
    const focusSel = focused ? liveFocusSelector(focused) : null;
    const now = Date.now();
    let refocus = null;
    const els = shown.map(a => {
        const busy = liveState.busy[a.id];
        const sig = L.signature(a, liveState.picks[a.id], busy);
        let el = have.get(a.id);
        if (el && el.dataset.sig === sig) return el;
        const tpl = document.createElement("template");
        tpl.innerHTML = L.cardHtml(a, { key: liveState.picks[a.id], busy, now });
        const fresh = tpl.content.firstElementChild;
        fresh.dataset.sig = sig;
        fresh.tabIndex = -1;
        if (el) {
            if (el === focusCard) refocus = fresh;
            el.replaceWith(fresh);
        } else if (!first && liveState.loaded && !window.Dana.motion.reduced()) {
            fresh.classList.add("dn-new");
            fresh.addEventListener("animationend", () => fresh.classList.remove("dn-new"), { once: true });
        }
        return fresh;
    });
    const keep = new Set(els);
    for (const el of [...listEl.children]) if (!keep.has(el)) el.remove();
    let cursor = listEl.firstChild;
    for (const el of els) {
        if (el === cursor) cursor = cursor.nextSibling;
        else listEl.insertBefore(el, cursor);
    }
    refreshIcons();
    if (refocus) {
        const target = (focusSel && refocus.querySelector(focusSel)) || null;
        (target && !target.disabled ? target : refocus.querySelector(".lv-act:not(:disabled)") || refocus).focus();
    }
    liveTicking(L.ticking(shown, liveState.picks));
}

function liveFocusSelector(el) {
    const kind = el.dataset.liveAct;
    if (kind === "server") return `[data-live-action="${CSS.escape(el.dataset.liveAction || "")}"]`;
    if (kind) return `[data-live-act="${kind}"]`;
    if (el.classList.contains("lv-open")) return ".lv-open";
    return null;
}

// ── The timers: one tick a second while Now shows a running timer ───────

function liveTicking(on) {
    if (on && !liveState.tick && !document.hidden) liveState.tick = setInterval(liveTick, 1000);
    if (!on && liveState.tick) { clearInterval(liveState.tick); liveState.tick = null; }
}

function liveTick() {
    const L = window.DanaLive;
    const now = Date.now();
    let due = false;
    for (const el of document.querySelectorAll("#now-live .lv-timer[data-to], #now-live .lv-timer[data-since]")) {
        const v = el.dataset.to ? { countdown: { to: el.dataset.to, label: el.dataset.label || "" } } : { since: el.dataset.since };
        const t = L.timer(v, now);
        if (!t) continue;
        const label = el.querySelector(".lv-timer-label");
        const value = el.querySelector(".lv-timer-value");
        if (label && label.textContent !== t.label) label.textContent = t.label;
        if (value && value.textContent !== t.value) value.textContent = t.value;
        el.classList.toggle("past", t.past);
        if (t.past) {
            const card = el.closest(".lv-card");
            const mark = `${card ? card.dataset.liveId : ""}|${el.dataset.to}`;
            if (!liveState.zeroed.has(mark)) { liveState.zeroed.add(mark); due = true; }
        }
    }
    if (due) liveRefresh();   // the add-on may have moved on (the train left): ask once
}

// ── Acting ─────────────────────────────────────────────────────────────

/** Next / Earlier: the card shows the other choice at once; the server stores it. */
async function liveStep(id, dir) {
    const a = liveFind(id);
    if (!a) return;
    const key = window.DanaLive.stepChoice(a, liveState.picks[id], dir);
    if (key === null) return;
    liveState.picks[id] = key;
    liveDraw();
    const r = await nowRequest("POST", livePath(id, "/select"), { choice: key });
    if (liveState.picks[id] !== key) return;    // a later pick is on its way: its answer decides
    delete liveState.picks[id];
    if (r.status === 200 && r.body) liveState.items = window.DanaLive.applyReply(liveState.items, id, r.body.activity);
    else nowToast(liveError(r, "Couldn't switch"));
    liveDraw();
    if (r.status === 404) liveRefresh();
}

/** A server action: the button waits, the card redraws from the answer. */
async function liveAct(id, actionId) {
    const a = liveFind(id);
    if (!a) return;
    const busy = liveState.busy[id] || (liveState.busy[id] = new Set());
    if (busy.has(actionId)) return;
    const choice = window.DanaLive.view(a, liveState.picks[id]).key;
    busy.add(actionId);
    liveDraw();
    const r = await nowRequest("POST", livePath(id, "/act"), choice ? { action: actionId, choice } : { action: actionId });
    busy.delete(actionId);
    if (!busy.size) delete liveState.busy[id];
    if (r.status === 200 && r.body) liveState.items = window.DanaLive.applyReply(liveState.items, id, r.body.activity);
    else nowToast(liveError(r, "Couldn't do that"));
    liveDraw();
    if (r.status === 404) liveRefresh();
}

/** Stop (or dismiss an ended one): the card leaves at once and comes back if the server says no. */
async function liveStop(id) {
    const el = document.getElementById(window.DanaLive.domId(id));
    if (!liveFind(id) || liveState.gone.has(id)) return;
    liveState.gone.add(id);
    const hadFocus = el && el.contains(document.activeElement);
    const neighbour = el && (el.nextElementSibling || el.previousElementSibling);
    const request = nowRequest("DELETE", livePath(id));
    if (el) await window.Dana.motion.complete(el);
    liveDraw();
    if (hadFocus) (neighbour && neighbour.isConnected ? neighbour : document.getElementById("now-lede"))?.focus?.();
    const r = await request;
    liveState.gone.delete(id);
    if ((r.status === 200 && r.body) || r.status === 404) liveState.items = liveState.items.filter(a => a.id !== id);
    else nowToast(liveError(r, "Couldn't stop it"));
    liveDraw();
}

function liveError(r, fallback) {
    const body = r.body || {};
    if (body.message) return body.message;
    if (r.status === 404) return "That has already ended";
    if (!r.status) return "No connection: nothing changed";
    return `${fallback} (${body.error || r.status})`;
}

function liveOpen(id) {
    const a = liveFind(id);
    if (!a) return;
    const t = window.DanaLive.openTarget(window.DanaLive.view(a, liveState.picks[id]).open);
    if (!t) return;
    if (t.type === "url") window.open(t.url, "_blank", "noopener");
    else if (t.id !== "now") openScreen(t.id, t.arg);
}

// ── Events ─────────────────────────────────────────────────────────────

document.getElementById("panel-now")?.addEventListener("click", (e) => {
    const card = e.target.closest(".lv-card");
    if (!card) return;
    const id = card.dataset.liveId;
    const btn = e.target.closest("[data-live-act]");
    if (btn) {
        if (btn.disabled) return;
        const kind = btn.dataset.liveAct;
        if (kind === "next" || kind === "prev") liveStep(id, kind === "next" ? 1 : -1);
        else if (kind === "server") liveAct(id, btn.dataset.liveAction);
        else if (kind === "stop") liveStop(id);
        return;
    }
    if (e.target.closest("a.lv-open")) return;                       // an https link opens itself (a new tab)
    if (e.target.closest("button, a, input")) { if (e.target.closest("[data-live-open]")) liveOpen(id); return; }
    // Anywhere else on a card that goes somewhere (not while text is being selected).
    if (!card.classList.contains("can-open") || String(window.getSelection?.() || "")) return;
    const link = card.querySelector("a.lv-open");
    if (link) link.click(); else liveOpen(id);
});
