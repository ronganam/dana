/* The web app — the Now screen (static/app.js says how the app's scripts fit together).
 *
 * The screen the app opens on: what's happening today and what needs a decision, from GET /api/now
 * (server/docs/now.md; a core composer, so the shell names it). The header (the date, the lede), the day
 * strip, the needs-you cards (a hue by kind, blocks folded behind More, the primary and the quiet actions,
 * acted on optimistically with Undo), "Can wait", the notifications line, All clear. Refreshed when it
 * opens, when the window comes back, and every minute while it is on screen (never in the background);
 * a refresh keeps the cards on screen as they are and only new ones rise in. Keyboard: j / k (or the
 * arrows on a card) between cards, Enter = its primary action, e = More.
 * The pure part is DanaNow (static/app-now-core.js). server/docs/web-design.md, "Now".
 */

// ══════════════════════════════════════════════════════════════════════
// NOW
// ══════════════════════════════════════════════════════════════════════

const NOW_REFRESH_MS = 60000;

const nowState = {
    data: null,            // the last payload that answered
    fetchedAt: 0,          // when it was fetched (ms): the day strip's clock runs on from its `now`
    lastOk: 0,
    failed: false,         // the last refresh failed: the "Updated … ago" note shows
    items: [],             // the needs-you cards on screen (the payload's, without the ones being acted on)
    quiet: [],
    pending: new Set(),    // ids being acted on: kept off screen until the server answers
    expanded: new Set(),   // cards with their blocks open
    forms: {},             // id → {choices: {key: index}, checks: {key: [index]}}
    errors: {},            // id → the message of a failed action, shown on the card
    quietOpen: false,
    drawn: false,          // the first data is on screen (rows rise in only then)
    loading: null,
    timer: null,
};

const nowEl = (id) => document.getElementById(id);
// User text lays out in its own direction (the first strong letter decides: "Call · אמא · 20 min ago" stays LTR).
const nowRtl = () => ' dir="auto"';
// A source line ("Call · אמא · 20 min ago"): each part isolated, so a Hebrew name never reorders its neighbours.
const nowSource = (text) => String(text || "").split(" · ").map(part => `<bdi>${escapeHtml(part)}</bdi>`).join(" · ");

async function nowRequest(method, path, body) {
    const init = { method, cache: "no-store", credentials: "same-origin", headers: {} };
    if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    let r;
    try { r = await fetch(path, init); } catch (_) { return { status: 0, body: null }; }
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    return { status: r.status, body: data };
}

// ── Showing, refreshing ────────────────────────────────────────────────

function showNow() {
    nowSkeleton();
    refreshNow();
    clearInterval(nowState.timer);
    nowState.timer = setInterval(() => { if (activeTab === "now" && !document.hidden) refreshNow(); }, NOW_REFRESH_MS);
}

function hideNow() {
    clearInterval(nowState.timer);
    nowState.timer = null;
}

window.addEventListener("focus", () => { if (activeTab === "now") refreshNow(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden && activeTab === "now") refreshNow(); });

function refreshNow() {
    if (nowState.loading) return nowState.loading;
    nowState.loading = (async () => {
        const r = await nowRequest("GET", "/api/now");
        nowState.loading = null;
        if (r.status !== 200 || !r.body) {
            nowState.failed = true;
            if (!nowState.data) nowFailedFirst();
            else paintStale();
            return;
        }
        nowState.failed = false;
        nowState.data = r.body;
        nowState.fetchedAt = nowState.lastOk = Date.now();
        drawNow();
    })();
    return nowState.loading;
}

function nowSkeleton() {
    const root = nowEl("now-root");
    if (!root || nowState.data || root.dataset.state === "skeleton") return;
    root.dataset.state = "skeleton";
    root.innerHTML = `<div class="now-skel" aria-hidden="true"><div class="now-skel-line short dn-shimmer"></div>
        <div class="now-skel-line wide dn-shimmer"></div><div class="now-skel-card dn-shimmer"></div>
        <div class="now-skel-card dn-shimmer"></div></div><p class="nav-sr-only" role="status">Loading Now</p>`;
}

function nowFailedFirst() {
    const root = nowEl("now-root");
    if (!root) return;
    root.dataset.state = "error";
    root.innerHTML = `<div class="now-offline"><p class="now-allclear">Now couldn't be loaded.</p>
        <p class="now-muted">Check the connection; Dana keeps trying while this screen is open.</p>
        <button type="button" class="btn-secondary" data-now-retry>Try again</button></div>`;
}

/** The screen's frame, drawn once; each refresh fills its parts. */
function nowFrame() {
    const root = nowEl("now-root");
    if (root.dataset.state === "ready") return root;
    root.dataset.state = "ready";
    root.innerHTML = `
        <header class="now-head">
            <div class="now-date-row">
                <span class="now-date" id="now-date"></span>
                <span class="now-stale" id="now-stale" hidden></span>
                <button type="button" class="icon-btn now-refresh" id="now-refresh" aria-label="Refresh" title="Refresh"><i data-lucide="rotate-cw" aria-hidden="true"></i></button>
            </div>
            <h1 class="now-lede" id="now-lede"></h1>
        </header>
        <div class="now-clear" id="now-clear" hidden><span class="now-mark" aria-hidden="true"></span><p class="now-allclear">All clear.</p></div>
        <section class="now-day" id="now-day" aria-label="Today" hidden></section>
        <section class="now-needs" id="now-needs" aria-labelledby="now-needs-h">
            <h2 class="now-section-h" id="now-needs-h">Needs you <span class="now-count" id="now-count"></span></h2>
            <div class="now-list" id="now-list"></div>
        </section>
        <section class="now-quiet" id="now-quiet" hidden>
            <div class="now-quiet-head">
                <button type="button" class="now-quiet-toggle" id="now-quiet-toggle" aria-expanded="false" aria-controls="now-quiet-list">
                    <i data-lucide="chevron-right" aria-hidden="true"></i><span>Can wait</span><span class="now-quiet-n" id="now-quiet-n"></span>
                </button>
                <button type="button" class="ibx-link" id="now-quiet-all" hidden>Dismiss all</button>
            </div>
            <div class="now-quiet-list" id="now-quiet-list" hidden></div>
        </section>
        <button type="button" class="now-notifs" id="now-notifs" hidden><span class="now-notifs-dot" aria-hidden="true"></span><span id="now-notifs-text"></span><i data-lucide="chevron-right" aria-hidden="true"></i></button>`;
    return root;
}

function drawNow() {
    const C = window.DanaNow;
    const d = nowState.data;
    const first = !nowState.drawn;
    const root = nowFrame();
    if (first) window.Dana.motion.arrive(root);

    nowEl("now-date").textContent = C.dateLine(d.now);
    const lede = C.ledeLine(d);
    const ledeEl = nowEl("now-lede");
    if (ledeEl.textContent !== lede) {
        ledeEl.textContent = lede;
        ledeEl.setAttribute("dir", "auto");
        ledeEl.classList.toggle("script", C.isRtl(lede));   // the serif is Latin only: Hebrew / Arabic in Inter
        if (!first) window.Dana.motion.flash(ledeEl);
    }
    paintStale();

    const rec = C.reconcile(nowState.items, d.items || [], nowState.pending);
    nowState.items = rec.items;
    for (const id of rec.removed) { nowState.expanded.delete(id); delete nowState.forms[id]; delete nowState.errors[id]; }
    drawCards(rec, first);
    nowState.quiet = (d.quiet || []).filter(q => !nowState.pending.has(q.id));
    drawQuiet();
    drawDay();
    drawNotifs();
    nowState.drawn = true;
    refreshIcons();
}

function paintStale() {
    const el = nowEl("now-stale");
    if (!el) return;
    el.hidden = !nowState.failed;
    if (nowState.failed) el.textContent = window.DanaNow.updatedAgo(nowState.lastOk, Date.now());
}

// ── The day strip ──────────────────────────────────────────────────────

function drawDay() {
    const C = window.DanaNow;
    const d = nowState.data;
    const el = nowEl("now-day");
    const minutes = C.minutesNow(d.now, nowState.fetchedAt, Date.now());
    const day = C.dayCells(d.day, minutes);
    el.hidden = !day.show && !day.calendarNote;
    if (el.hidden) { el.replaceChildren(); return; }
    const item = (it) => {
        const page = it.ref ? panelOpening(it.ref.type) : null;
        const cls = `now-day-item${it.past ? " past" : ""}${it.next ? " next" : ""}`;
        const inner = `<span class="now-day-dot" style="--hue: var(--${it.hue})" aria-hidden="true"></span>`
            + (it.time ? `<span class="now-day-time">${escapeHtml(it.time)}</span>` : "")
            + `<span class="now-day-title"${nowRtl(it.title)}>${escapeHtml(it.title)}</span>`;
        return page
            ? `<button type="button" class="${cls}" data-day-page="${escapeHtml(page)}" data-day-arg="${escapeHtml(it.ref.id)}"${it.next ? ' aria-current="time"' : ""}>${inner}</button>`
            : `<div class="${cls}"${it.next ? ' aria-current="time"' : ""}>${inner}</div>`;
    };
    const cells = day.cells.map(c => c.type === "gap"
        ? `<li class="now-cell gap" style="--hours: ${c.hours}"><span class="now-cell-label">${escapeHtml(c.label)}</span></li>`
        : `<li class="now-cell ${c.type === "hour" ? "hour" : "slot"}"><span class="now-cell-label">${escapeHtml(c.label)}</span>${c.items.map(item).join("")}</li>`);
    el.innerHTML = (day.show
        ? `<h2 class="now-section-h">Today</h2><ol class="now-strip" aria-label="Today's timeline">`
            + `<li class="now-cell nowcell" aria-label="Now, ${escapeHtml(C.clock(minutes))}"><span class="now-cell-label">Now</span><span class="now-cell-time">${escapeHtml(C.clock(minutes))}</span></li>`
            + cells.join("") + `</ol>`
        : "")
        + (day.calendarNote ? `<p class="now-muted now-cal-note"><i data-lucide="calendar-x" aria-hidden="true"></i>Calendar couldn't be read</p>` : "");
}

// ── Cards ──────────────────────────────────────────────────────────────

const cardDomId = (id) => "now-c-" + String(id).replace(/[^a-zA-Z0-9_-]/g, "_");

function blockHtml(b, item, bi) {
    const C = window.DanaNow;
    const form = nowState.forms[item.id];
    const label = b.label ? `<div class="now-block-label">${escapeHtml(b.label)}</div>` : "";
    switch (b.type) {
        case "lines":
            return `<div class="now-block lines">${label}<div class="now-lines">${(b.lines || []).map(l =>
                `<p class="now-line${l.hit ? " hit" : ""}"${nowRtl(l.text)}>${l.who ? `<span class="now-who">${escapeHtml(l.who)}</span> ` : ""}${escapeHtml(l.text)}</p>`).join("")}</div></div>`;
        case "chat":
            return `<div class="now-block chat">${label}<div class="now-chat">${(b.messages || []).map(m =>
                `<div class="now-msg${m.mine ? " mine" : ""}"${nowRtl(m.text)}>${m.who ? `<span class="now-who">${escapeHtml(m.who)}</span>` : ""}<span>${escapeHtml(m.text)}</span></div>`).join("")}</div></div>`;
        case "facts":
            return `<div class="now-block facts">${label}<ul class="now-facts">${(b.items || []).map(f =>
                `<li${nowRtl(f)}>${escapeHtml(f)}</li>`).join("")}</ul></div>`;
        case "text":
            return `<div class="now-block text">${label}<p class="now-text"${nowRtl(b.text)}>${escapeHtml(b.text)}</p></div>`;
        case "week":
            return `<div class="now-block week">${label}<ol class="now-week">${(b.cells || []).map(c =>
                `<li class="now-week-cell ${c.ok === true ? "ok" : c.ok === false ? "bad" : "unknown"}" title="${escapeHtml(`${c.label}: ${c.value ?? ""}`)}">`
                + `<span class="now-week-mark" aria-hidden="true"></span><span class="now-week-label">${escapeHtml(c.label)}</span>`
                + `<span class="now-week-value">${escapeHtml(c.value ?? "")}</span>`
                + `<span class="nav-sr-only">${c.ok === true ? "fine" : c.ok === false ? "missed" : "not known yet"}</span></li>`).join("")}</ol></div>`;
        case "choices": {
            const sel = C.chosen(b, form);
            const name = `${cardDomId(item.id)}-b${bi}`;
            return `<fieldset class="now-block choices"><legend class="now-block-label">${escapeHtml(b.label || "Choose")}</legend><div class="now-choices">${(b.options || []).map((o, i) =>
                `<label class="now-choice${i === sel ? " on" : ""}"><input type="radio" name="${name}" data-choice="${bi}" value="${i}"${i === sel ? " checked" : ""}><span${nowRtl(o.label)}>${escapeHtml(o.label)}</span></label>`).join("")}</div></fieldset>`;
        }
        case "checks":
            return `<fieldset class="now-block checks"><legend class="now-block-label">${escapeHtml(b.label || "Choose")}</legend><div class="now-checks">${(b.options || []).map((o, i) =>
                `<label class="now-check"><input type="checkbox" data-check="${bi}" value="${i}"${C.isChecked(b, i, form) ? " checked" : ""}><span${nowRtl(o.label)}>${escapeHtml(o.label)}</span></label>`).join("")}</div></fieldset>`;
        default:
            // A block type this screen does not know yet: its label and any text it carries.
            return b.text ? `<div class="now-block text">${label}<p class="now-text"${nowRtl(b.text)}>${escapeHtml(b.text)}</p></div>` : "";
    }
}

function cardEl(item) {
    const C = window.DanaNow;
    const dom = cardDomId(item.id);
    const blocks = Array.isArray(item.blocks) ? item.blocks : [];
    const open = nowState.expanded.has(item.id);
    const primary = item.primary;
    const el = document.createElement("article");
    el.className = "now-card dn-row" + (open ? " open" : "");
    el.id = dom;
    el.dataset.id = item.id;
    el.tabIndex = -1;
    el.setAttribute("role", "region");
    el.setAttribute("aria-labelledby", `${dom}-t`);
    el.style.setProperty("--hue", `var(--${C.hueOf(item)})`);
    el.style.setProperty("--hue-text", `var(--${C.hueOf(item)}-text)`);
    const btn = (action, which, cls) => {
        if (!C.canDo(action)) {
            return `<button type="button" class="btn-secondary now-btn" disabled title="Reply from your phone" aria-label="${escapeHtml(action.label || "Reply")} — from your phone only">`
                + `<i data-lucide="smartphone" aria-hidden="true"></i>${escapeHtml(action.label || "Reply")} on phone</button>`;
        }
        const label = which === "p" ? C.primaryLabel(item, nowState.forms[item.id]) : (action.label || action.action);
        return `<button type="button" class="${cls}" data-act="${which}">${escapeHtml(label)}</button>`;
    };
    const err = nowState.errors[item.id];
    el.innerHTML = `
        <div class="now-src"><span class="now-dot" aria-hidden="true"></span><span class="now-src-text"${nowRtl(item.source)}>${nowSource(item.source || item.kind || "")}</span></div>
        <h3 class="now-title" id="${dom}-t"${nowRtl(item.title)}>${escapeHtml(item.title || "")}</h3>
        ${item.why ? `<p class="now-why"${nowRtl(item.why)}>${escapeHtml(item.why)}</p>` : ""}
        ${blocks.length ? `<div class="now-blocks" id="${dom}-b"${open ? "" : " hidden"}>${blocks.map((b, i) => blockHtml(b, item, i)).join("")}</div>` : ""}
        <div class="now-acts">
            ${primary ? btn(primary, "p", "btn-primary now-btn") : ""}
            ${(item.secondary || []).map((a, i) => btn(a, String(i), "btn-secondary now-btn quiet")).join("")}
            ${blocks.length ? `<button type="button" class="now-more" data-more aria-expanded="${open}" aria-controls="${dom}-b">${open ? "Less" : "More"}<i data-lucide="chevron-down" aria-hidden="true"></i></button>` : ""}
        </div>
        <p class="now-err" role="alert"${err ? "" : " hidden"}>${escapeHtml(err || "")}</p>`;
    return el;
}

/** The cards in server order: an element kept as it is unless its card changed; new ones rise in
 *  (except on the first load, where the whole screen arrives). */
function drawCards(rec, first) {
    const list = nowEl("now-list");
    const have = new Map([...list.children].map(el => [el.dataset.id, el]));
    const changed = new Set(rec.changed);
    const added = new Set(rec.added);
    const els = nowState.items.map(item => {
        let el = have.get(item.id);
        if (!el || changed.has(item.id)) {
            const fresh = cardEl(item);
            if (el) { el.replaceWith(fresh); if (!first) window.Dana.motion.flash(fresh); }
            else if (!first && added.has(item.id) && !window.Dana.motion.reduced()) {
                fresh.classList.add("dn-new");
                fresh.addEventListener("animationend", () => fresh.classList.remove("dn-new"), { once: true });
            }
            el = fresh;
        }
        return el;
    });
    const keep = new Set(els);
    for (const el of [...list.children]) if (!keep.has(el)) el.remove();
    let cursor = list.firstChild;
    for (const el of els) {
        if (el === cursor) cursor = cursor.nextSibling;
        else list.insertBefore(el, cursor);
    }
    paintNeedsCount();
}

function paintNeedsCount() {
    const n = nowState.items.length;
    window.Dana.motion.number(nowEl("now-count"), n ? String(n) : "");
    nowEl("now-needs").hidden = !n;
    nowEl("now-clear").hidden = Boolean(n) || !nowState.data;
}

function redrawCard(id) {
    const item = nowState.items.find(i => i.id === id);
    const old = nowEl(cardDomId(id));
    if (!item || !old) return null;
    const focused = document.activeElement && old.contains(document.activeElement) ? document.activeElement : null;
    const sel = focused ? (focused.dataset.act ? `[data-act="${focused.dataset.act}"]` : focused.hasAttribute("data-more") ? "[data-more]" : null) : null;
    const fresh = cardEl(item);
    old.replaceWith(fresh);
    refreshIcons();
    if (focused) (sel && fresh.querySelector(sel) || fresh).focus();
    return fresh;
}

function toggleCard(id, open) {
    const want = open === undefined ? !nowState.expanded.has(id) : open;
    if (want) nowState.expanded.add(id); else nowState.expanded.delete(id);
    const el = nowEl(cardDomId(id));
    if (!el) return;
    el.classList.toggle("open", want);
    const blocks = el.querySelector(".now-blocks");
    if (blocks) {
        blocks.hidden = !want;
        if (want && !window.Dana.motion.reduced()) {
            blocks.classList.remove("dn-new");
            void blocks.offsetWidth;
            blocks.classList.add("dn-new");
        }
    }
    const more = el.querySelector("[data-more]");
    if (more) {
        more.setAttribute("aria-expanded", String(want));
        more.firstChild.textContent = want ? "Less" : "More";
    }
}

// ── Acting ─────────────────────────────────────────────────────────────

let nowToastTimer = null;
function nowToast(text, undoId) {
    const el = nowEl("now-toast");
    if (!el) return;
    el.innerHTML = `<span${nowRtl(text)}>${escapeHtml(text)}</span>` + (undoId ? `<button type="button" data-undo="${escapeHtml(undoId)}">Undo</button>` : "");
    el.classList.remove("hidden");
    clearTimeout(nowToastTimer);
    nowToastTimer = setTimeout(() => el.classList.add("hidden"), undoId ? 8000 : 4000);
}

async function nowUndo(undoId) {
    nowEl("now-toast")?.classList.add("hidden");
    const r = await nowRequest("POST", `/api/undo/${encodeURIComponent(undoId)}`);
    if (r.status === 200) nowToast((r.body && r.body.message) || "Undone");
    else nowToast(r.status === 404 ? "Too late to undo" : (r.body && r.body.error) || "Couldn't undo");
    refreshNow();
}

/** Opens what a client action points at (DanaNow.clientTarget). */
async function nowClient(item, action) {
    const t = window.DanaNow.clientTarget(item, action, { expanded: nowState.expanded.has(item.id), opens: panelOpening });
    if (t.type === "expand") { toggleCard(item.id); return; }
    if (t.type === "session") { window.danaOpenSession(t.id); return; }
    if (t.type === "screen" || t.type === "page") { openScreen(t.id, t.arg ?? null); return; }
    if (t.type === "connect") { window.DanaAddons?.openAddon(t.addon); return; }
    if (t.type === "url") { window.open(t.url, "_blank", "noopener"); return; }
    if (t.type === "context") {
        const r = await nowRequest("POST", "/api/chat/context", { kind: t.kind, id: t.id });
        if (r.status === 200 && r.body && r.body.session_id) window.danaOpenSession(r.body.session_id);
        else nowToast((r.body && (r.body.message || r.body.error)) || "Couldn't open the chat");
    }
}

async function nowAct(id, which) {
    const C = window.DanaNow;
    const inQuiet = !nowState.items.some(i => i.id === id);
    const item = (inQuiet ? nowState.quiet : nowState.items).find(i => i.id === id);
    if (!item || nowState.pending.has(id)) return;
    const action = which === "p" ? item.primary : (item.secondary || [])[Number(which)];
    if (!action) return;
    if (action.client) { nowClient(item, action); return; }
    const body = C.actBody(item, action, nowState.forms[id], which === "p");
    nowState.pending.add(id);
    delete nowState.errors[id];
    const el = inQuiet ? document.querySelector(`.now-qrow[data-id="${CSS.escape(id)}"]`) : nowEl(cardDomId(id));
    // Keep the keyboard on the list: the next card (or the one before) takes focus once this one is gone.
    const hadFocus = el && el.contains(document.activeElement);
    const neighbour = el && (el.nextElementSibling || el.previousElementSibling);
    const request = nowRequest("POST", "/api/now/act", body);
    await window.Dana.motion.complete(el);
    const out = C.removeItem(inQuiet ? nowState.quiet : nowState.items, id);
    if (inQuiet) nowState.quiet = out.items; else nowState.items = out.items;
    el?.remove();
    if (hadFocus) (neighbour && neighbour.isConnected ? neighbour : nowEl("now-lede"))?.focus?.();
    if (inQuiet) paintQuietCount(); else paintNeedsCount();
    const r = await request;
    nowState.pending.delete(id);
    if (r.status === 200 && r.body && r.body.ok !== false) {
        nowToast(r.body.message || C.chosenMessage(item, nowState.forms[id]) || "Done", r.body.undo_id || null);
        refreshNow();
        return;
    }
    // The card comes back with the message.
    const msg = (r.body && (r.body.error || r.body.message)) || (r.status ? `Couldn't do that (${r.status})` : "No connection: nothing changed");
    if (inQuiet) {
        nowState.quiet = C.restoreItem(nowState.quiet, item, out.index);
        drawQuiet();
        nowToast(msg);
    } else {
        nowState.errors[id] = msg;
        nowState.items = C.restoreItem(nowState.items, item, out.index);
        drawCards({ items: nowState.items, added: [], changed: [id], removed: [] }, true);
        refreshIcons();
        window.Dana.motion.flash(nowEl(cardDomId(id)));
    }
    if (r.status === 404 || r.status === 409) refreshNow();   // the card's object is gone or already handled
}

async function nowDismissAllQuiet() {
    const rows = [...document.querySelectorAll(".now-qrow")];
    const ids = nowState.quiet.map(q => q.id);
    ids.forEach(id => nowState.pending.add(id));
    const request = nowRequest("POST", "/api/now/act", { id: "quiet:all", action: "dismiss" });
    await Promise.all(rows.map(r => window.Dana.motion.complete(r)));
    const before = nowState.quiet;
    nowState.quiet = [];
    drawQuiet();
    const r = await request;
    ids.forEach(id => nowState.pending.delete(id));
    if (r.status === 200 && r.body && r.body.ok !== false) {
        nowToast(r.body.message || "Dismissed", r.body.undo_id || null);
    } else {
        nowState.quiet = before;
        drawQuiet();
        nowToast((r.body && r.body.error) || "Couldn't dismiss them");
    }
    refreshNow();
}

// ── Can wait, the notifications line ───────────────────────────────────

function paintQuietCount() {
    const d = nowState.data || {};
    const shown = nowState.quiet.length;
    const total = Math.max(shown, Number(d.quiet_total) || 0) - ((d.quiet || []).length - shown);
    window.Dana.motion.number(nowEl("now-quiet-n"), total > 0 ? `· ${total}` : "");
    nowEl("now-quiet").hidden = total <= 0;
}

function drawQuiet() {
    const C = window.DanaNow;
    const list = nowEl("now-quiet-list");
    const toggle = nowEl("now-quiet-toggle");
    toggle.setAttribute("aria-expanded", String(nowState.quietOpen));
    nowEl("now-quiet").classList.toggle("open", nowState.quietOpen);
    list.hidden = !nowState.quietOpen;
    nowEl("now-quiet-all").hidden = !nowState.quietOpen || nowState.quiet.length < 2;
    list.innerHTML = nowState.quiet.map(q => {
        const btn = (a, which, cls) => C.canDo(a) ? `<button type="button" class="${cls}" data-act="${which}">${escapeHtml(a.label || a.action)}</button>` : "";
        return `<div class="now-qrow" data-id="${escapeHtml(q.id)}" style="--hue: var(--${C.hueOf(q)})">`
            + `<span class="now-dot" aria-hidden="true"></span><div class="now-qtext"><span class="now-qtitle"${nowRtl(q.title)}>${escapeHtml(q.title || "")}</span>`
            + (q.source ? `<span class="now-qsrc"${nowRtl(q.source)}>${nowSource(q.source)}</span>` : "") + `</div>`
            + `<div class="now-qacts">${q.primary ? btn(q.primary, "p", "chip") : ""}${(q.secondary || []).map((a, i) => btn(a, String(i), "chip quiet")).join("")}</div></div>`;
    }).join("");
    paintQuietCount();
}

function drawNotifs() {
    const n = (nowState.data && nowState.data.notifications) || {};
    const text = window.DanaNow.notificationsLine(n);
    const el = nowEl("now-notifs");
    const page = panelOpening("notification");
    el.hidden = !text;
    el.disabled = !page;
    el.classList.toggle("loud", Number(n.needs_you) > 0);
    window.Dana.motion.number(nowEl("now-notifs-text"), text);
    if (page) el.dataset.page = page; else delete el.dataset.page;
}

// ── Events ─────────────────────────────────────────────────────────────

(() => {
    const panel = nowEl("panel-now");
    if (!panel) return;
    panel.addEventListener("click", (e) => {
        const t = e.target;
        if (t.closest("[data-now-retry]")) { refreshNow(); return; }
        if (t.closest("#now-refresh")) { refreshNow(); return; }
        const undo = t.closest("[data-undo]");
        if (undo) { nowUndo(undo.dataset.undo); return; }
        const act = t.closest("[data-act]");
        const holder = t.closest("[data-id]");
        if (act && holder && !act.disabled) { nowAct(holder.dataset.id, act.dataset.act); return; }
        if (t.closest("[data-more]") && holder) { toggleCard(holder.dataset.id); return; }
        const day = t.closest("[data-day-page]");
        if (day) { openScreen(day.dataset.dayPage, day.dataset.dayArg || null); return; }
        if (t.closest("#now-quiet-toggle")) { nowState.quietOpen = !nowState.quietOpen; drawQuiet(); refreshIcons(); return; }
        if (t.closest("#now-quiet-all")) { nowDismissAllQuiet(); return; }
        const notifs = t.closest("#now-notifs");
        if (notifs && notifs.dataset.page) openScreen(notifs.dataset.page);
    });
    panel.addEventListener("change", (e) => {
        const input = e.target;
        const card = input.closest(".now-card");
        if (!card) return;
        const id = card.dataset.id;
        const item = nowState.items.find(i => i.id === id);
        if (!item) return;
        const form = nowState.forms[id] || (nowState.forms[id] = { choices: {}, checks: {} });
        if (input.dataset.choice !== undefined) {
            const b = item.blocks[Number(input.dataset.choice)];
            form.choices[b.key] = Number(input.value);
            card.querySelectorAll(`[data-choice="${input.dataset.choice}"]`).forEach(r => r.closest(".now-choice")?.classList.toggle("on", r.checked));
            const p = card.querySelector('[data-act="p"]');
            if (p) p.textContent = window.DanaNow.primaryLabel(item, form);
        } else if (input.dataset.check !== undefined) {
            const b = item.blocks[Number(input.dataset.check)];
            form.checks[b.key] = [...card.querySelectorAll(`[data-check="${input.dataset.check}"]`)].filter(c => c.checked).map(c => Number(c.value));
        }
    });
})();

// The keyboard: j / k between cards (the arrows too, on a card), Enter = the card's primary, e = More.
document.addEventListener("keydown", (e) => {
    if (activeTab !== "now" || e.altKey || e.ctrlKey || e.metaKey) return;
    const t = e.target;
    if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
    if (document.querySelector("#nav-more:not([hidden])")) return;
    const cards = [...document.querySelectorAll("#now-list .now-card")];
    const current = t && t.closest ? t.closest(".now-card") : null;
    const onCard = current && t === current;
    const index = current ? cards.indexOf(current) : -1;
    const down = e.key === "j" || (onCard && e.key === "ArrowDown");
    const up = e.key === "k" || (onCard && e.key === "ArrowUp");
    if (down || up) {
        const i = window.DanaNow.step(index, down ? 1 : -1, cards.length);
        if (i >= 0) { e.preventDefault(); cards[i].focus(); cards[i].scrollIntoView({ block: "nearest" }); }
        return;
    }
    if (!current) return;
    if (e.key === "e") { e.preventDefault(); toggleCard(current.dataset.id); return; }
    if (e.key === "Enter" && onCard) {
        e.preventDefault();
        const p = current.querySelector('[data-act="p"]:not(:disabled)');
        if (p) p.click(); else toggleCard(current.dataset.id);
    }
});
