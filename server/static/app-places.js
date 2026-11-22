/* The web app — the navigation's places, pure (static/app.js says how the app's scripts fit together).
 *
 * A place is something the navigation opens: the shell's lead places (Now, then Chat) and the user's addon
 * pages with a Nav (the main places, in Nav order), and the shell's own screens (the core group: settings,
 * addons, usage, debug, the dashboard link), drawn quieter and last. The user pins up to NAV_MAX_PRIMARY main places as primary:
 * the phone's bottom bar (they plus More) and the top of the desktop rail. Everything else is "the rest",
 * under More on a phone, and on the rail below the primary ones until the rail is full (then under More).
 * The saved choice is a list of place ids; ids the user no longer has are skipped, new pages fall into the
 * rest by their Nav order. No DOM here: app-nav.js draws it; tests/test_web_places.mjs tests it.
 */

const NAV_MAX_PRIMARY = 4;   // the phone bar's places (a fifth slot is More)

/** {main, core}: the lead places first (one place or a list: Now, Chat), then the pages with a Nav in Nav
 *  order; `core` as given. Each place: {id, label, icon, order, primary, lead, href}. */
function navPlaces(lead, entries, core) {
    const place = (p, isLead) => ({ id: p.id, label: p.label, icon: p.icon, order: Number.isFinite(p.order) ? p.order : 100,
                                    primary: Boolean(p.primary), lead: isLead, href: p.href || null });
    const seen = new Set();
    const main = [];
    const leads = (Array.isArray(lead) ? lead : [lead]).filter(Boolean);
    const pages = (entries || []).filter(e => e && e.nav !== false)
        .sort((x, y) => (x.order ?? 100) - (y.order ?? 100) || String(x.label).localeCompare(String(y.label)));
    for (const [p, isLead] of [...leads.map(p => [p, true]), ...pages.map(p => [p, false])]) {
        if (!p || !p.id || seen.has(p.id)) continue;
        seen.add(p.id);
        main.push(place(p, isLead));
    }
    return { main, core: (core || []).filter(p => p && p.id && !seen.has(p.id)).map(place) };
}

/** The primary places when the user chose none: the lead places (Now, Chat; at least the first main
 *  place), then the pages whose Nav asks for it (`primary`), then the first others in Nav order, up to `max`. */
function defaultPrimary(main, max = NAV_MAX_PRIMARY) {
    const out = main.length ? [main[0].id] : [];
    for (const p of main.slice(1)) if (p.lead && out.length < max) out.push(p.id);
    for (const p of main.slice(1)) if (p.primary && !out.includes(p.id) && out.length < max) out.push(p.id);
    for (const p of main) if (out.length < max && !out.includes(p.id)) out.push(p.id);
    return out;
}

/** A saved choice (the stored text) as a list of ids, or null when there is none or it is unreadable. */
function parseChoice(text) {
    if (typeof text !== "string" || !text) return null;
    try {
        const v = JSON.parse(text);
        return Array.isArray(v) ? v.filter(x => typeof x === "string") : null;
    } catch (_) {
        return null;
    }
}

/** The primary ids: the saved choice's ids that are main places, once each, at most `max`; the defaults
 *  when there is no choice or none of its ids is a place any more. */
function applyChoice(main, saved, max = NAV_MAX_PRIMARY) {
    if (Array.isArray(saved)) {
        const ids = new Set(main.map(p => p.id));
        const out = [];
        for (const id of saved) if (ids.has(id) && !out.includes(id) && out.length < max) out.push(id);
        if (out.length) return out;
    }
    return defaultPrimary(main, max);
}

/** {primary, rest, core}: the primary places in the chosen order, the other main places in Nav order. */
function navLayout(places, primaryIds) {
    const byId = new Map(places.main.map(p => [p.id, p]));
    const primary = primaryIds.map(id => byId.get(id)).filter(Boolean);
    const chosen = new Set(primary.map(p => p.id));
    return { primary, rest: places.main.filter(p => !chosen.has(p.id)), core: places.core };
}

/** What fits on the desktop rail, `avail` pixels tall, given the sizes of a row (`item`), a core row
 *  (`coreItem`), the More row (`more`), the Edit row shown when nothing overflows (`edit`) and the divider:
 *  {rest: how many of the rest show, core: whether the core group shows, more: whether More shows}.
 *  Everything fits → no More. Else the rest fill what is left above More and the core group; when even
 *  the primary places, More and the core group do not fit, the core group goes under More too. */
function railPlan(counts, avail, size) {
    const { primary, rest, core } = counts;
    const coreH = core ? core * size.coreItem + size.divider : 0;
    if (primary * size.item + rest * size.item + size.edit + coreH <= avail) return { rest, core: true, more: false };
    let left = avail - primary * size.item - size.more - coreH;
    let showCore = true;
    if (left < 0) { showCore = false; left = avail - primary * size.item - size.more; }
    return { rest: Math.max(0, Math.min(rest, Math.floor(left / size.item))), core: showCore, more: true };
}

/** The places under More for each layout: the phone bar shows only the primary places; the rail shows
 *  `plan.rest` of the rest and, when `plan.core`, the core group. Lists of ids. */
function hiddenPlaces(layout, plan) {
    const coreIds = layout.core.map(p => p.id);
    return {
        phone: [...layout.rest.map(p => p.id), ...coreIds],
        rail: plan.more ? [...layout.rest.slice(plan.rest).map(p => p.id), ...(plan.core ? [] : coreIds)] : [],
    };
}

/** The More button's badge from the badges of the places under it (`badges`: id → {text, tone}, the
 *  text a page's badge shows: "3", "99+", or a short word): {text, dot, tone}. Counts add up (99+ past 99);
 *  a word with no count makes a dot; the tone is "quiet" only when every badge under More is quiet. */
function moreBadge(ids, badges) {
    let total = 0, word = false, loud = false, any = false;
    for (const id of ids) {
        const b = badges && (typeof badges.get === "function" ? badges.get(id) : badges[id]);
        const text = b && b.text ? String(b.text) : "";
        if (!text) continue;
        any = true;
        if (b.tone !== "quiet") loud = true;
        const n = /^(\d+)(\+?)$/.exec(text);
        if (n) total += Number(n[1]) + (n[2] ? 1 : 0); else word = true;
    }
    const text = total > 99 ? "99+" : total > 0 ? String(total) : "";
    return { text, dot: !text && word, tone: any && !loud ? "quiet" : "" };
}

/** `ids` with `id` moved `delta` places (−1 up, 1 down), kept inside the list. */
function movePlace(ids, id, delta) {
    const i = ids.indexOf(id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= ids.length) return ids.slice();
    const out = ids.slice();
    [out[i], out[j]] = [out[j], out[i]];
    return out;
}

/** `ids` with `id` added at the end, when there is room (at most `max`). */
function pinPlace(ids, id, max = NAV_MAX_PRIMARY) {
    return ids.includes(id) || ids.length >= max ? ids.slice() : [...ids, id];
}

/** `ids` without `id`; the last one stays (the bar always has a place besides More). */
function unpinPlace(ids, id) {
    return ids.length <= 1 ? ids.slice() : ids.filter(x => x !== id);
}
