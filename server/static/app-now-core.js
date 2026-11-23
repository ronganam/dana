/* The web app — the Now screen's pure part (static/app.js says how the app's scripts fit together;
 * the DOM is static/app-now.js). Sets globalThis.DanaNow; no DOM here, tested by tests/test_web_now.mjs.
 *
 * Everything comes from one GET /api/now payload (server/docs/now.md): the header (the date, the lede or a
 * calm line), the day track and the day's items, the hue of a card by its kind, what an action sends, what a client
 * action opens, an action applied optimistically (and undone on an error), a refresh reconciled with what
 * is on screen (only new cards rise in), the notifications line and the "Updated … ago" note.
 * A card is rendered from its fields and blocks only: no addon is named, a kind only picks a hue; the one
 * exception is a notification's app icon, served by the phone's /api/phone/app-icons (docs/notifications.md).
 */
(() => {
  "use strict";

  const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                  "October", "November", "December"];

  // A card's hue by its kind (web-design.md, the hues); a kind Dana does not know (an addon's own) is sky.
  const KIND_HUES = { approval: "ember", notification: "teal", suggestion: "plum", reminder: "amber",
                      money: "sage", check: "rose", reconnect: "rose", review: "sky" };

  /** The hue of a Now card: by kind; a call's suggestion is sage, a charge that stands out rose. */
  function hueOf(item) {
    const kind = item && item.kind;
    const ref = (item && item.ref) || {};
    if (kind === "suggestion" && ref.type === "call") return "sage";
    if (ref.type === "transaction") return "rose";   // money out
    return Object.prototype.hasOwnProperty.call(KIND_HUES, kind) ? KIND_HUES[kind] : "sky";
  }

  /** The hue of an item of the day strip: events sky, reminders amber, anything else sky. */
  function dayHue(it) {
    return it && it.kind === "reminder" ? "amber" : "sky";
  }

  /** {y, mo, d, h, mi} of a local ISO time as the server wrote it ("2026-11-22T14:58:55+02:00"): the
   *  user's own clock, whatever zone the browser is in. Null when it does not read. */
  function parts(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(iso || ""));
    return m ? { y: +m[1], mo: +m[2], d: +m[3], h: +m[4], mi: +m[5] } : null;
  }

  /** "SUNDAY · 22 NOVEMBER" for the payload's `now`; "" when it does not read. */
  function dateLine(iso) {
    const p = parts(iso);
    if (!p) return "";
    const wd = new Date(Date.UTC(p.y, p.mo - 1, p.d)).getUTCDay();
    return `${DAYS[wd]} · ${p.d} ${MONTHS[p.mo - 1]}`.toUpperCase();
  }

  /** Minutes since midnight on the user's clock: the payload's `now` plus the time since it was fetched. */
  function minutesNow(iso, fetchedAt, at) {
    const p = parts(iso);
    if (!p) return null;
    const passed = Math.max(0, Math.floor(((at || 0) - (fetchedAt || 0)) / 60000));
    return Math.min(24 * 60 - 1, p.h * 60 + p.mi + passed);
  }

  /** "HH:MM" of minutes since midnight. */
  function clock(minutes) {
    const m = Math.max(0, Math.floor(minutes || 0));
    return `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  }

  /** The header's line: the server's lede; without one a calm line ("Nothing needs you right now." only
   *  when nothing waits, else one from the time of day). */
  function ledeLine(payload) {
    const lede = payload && typeof payload.lede === "string" ? payload.lede.trim() : "";
    if (lede) return lede;
    const items = (payload && payload.items) || [];
    if (!items.length) return "Nothing needs you right now.";
    const p = parts(payload && payload.now);
    const h = p ? p.h : 12;
    if (h < 5) return "Still up.";
    if (h < 12) return "Good morning.";
    if (h < 18) return "Good afternoon.";
    return "Good evening.";
  }

  const toMinutes = (t) => {
    const m = /^(\d{1,2}):(\d{2})$/.exec(String(t || ""));
    return m ? +m[1] * 60 + +m[2] : null;
  };

  /** What a day item refers to: its first `<type>_id` field ({type: "task", id} for `task_id`), or null. */
  function dayRef(it) {
    for (const [k, v] of Object.entries(it || {})) {
      const m = /^([a-z][a-z0-9]*)_id$/.exec(k);
      if (m && v !== null && v !== undefined && v !== "") return { type: m[1], id: String(v) };
    }
    return null;
  }

  const isHour = (slot) => /^\d{1,2}$/.test(String(slot && slot.key));
  const hourLabel = (key) => `${String(key).padStart(2, "0")}:00`;

  /** The day's items: {show, calendarNote, cells}. Cells, in order: the hour slots with items, then "At home" /
   *  "On waking" when they hold something (empty slots are left out: the track shows the free hours). Each item
   *  gets its hue, its `ref`, `past` (its time has gone by on the user's clock) and `next` (the first one still
   *  to come). Nothing in any slot: hidden (`show` false); the calendar note still shows when the calendar
   *  couldn't be read. */
  function dayCells(day, minutes) {
    const slots = (day && Array.isArray(day.slots)) ? day.slots : [];
    const calendarNote = Boolean(day && day.calendar === false);
    const cells = [];
    for (const s of slots) {
      const items = s && Array.isArray(s.items) ? s.items : [];
      if (!items.length) continue;
      cells.push({
        key: String(s.key), type: isHour(s) ? "hour" : String(s.key), label: s.label || hourLabel(s.key),
        items: items.map(it => {
          const t = toMinutes(it && it.time);
          return { ...it, hue: dayHue(it), ref: dayRef(it), past: t !== null && minutes !== null && t < minutes, next: false };
        }),
      });
    }
    if (!cells.length) return { show: false, calendarNote, cells: [] };
    // The next item: the first timed one still to come, else the first one of all.
    const all = cells.flatMap(c => c.items);
    const next = all.find(it => it.time && !it.past) || all.find(it => !it.past);
    if (next) next.next = true;
    return { show: true, calendarNote, cells };
  }

  const DAY_FIRST_HOUR = 6;    // the track's hours: the waking day the server plans (server/docs/now.md, Day slots)
  const DAY_LAST_HOUR = 23;

  /** The day track under the lede: a 4 px segment per hour of the waking day (06–23), {hours, mark, now}.
   *  An hour that has begun on the user's clock is `past`; a later one is `busy` when its slot holds something.
   *  `mark` is where the now-mark stands (the number of past hours; −1 when the time does not read), `now` the
   *  time as "HH:MM". */
  function dayTrack(day, minutes) {
    const slots = (day && Array.isArray(day.slots)) ? day.slots : [];
    const busy = new Set(slots.filter(s => s && isHour(s) && Array.isArray(s.items) && s.items.length).map(s => Number(s.key)));
    const known = minutes !== null && minutes !== undefined;
    const hours = [];
    for (let h = DAY_FIRST_HOUR; h <= DAY_LAST_HOUR; h++) {
      const past = known && h * 60 <= minutes;
      hours.push({ hour: h, past, busy: !past && busy.has(h) });
    }
    return { hours, mark: known ? hours.filter(h => h.past).length : -1, now: known ? clock(minutes) : "" };
  }

  /** The selected option of a card's `choices` block (its index in `form.choices[key]`, else the one the
   *  server marked selected, else the first). */
  function chosen(block, form) {
    const opts = (block && block.options) || [];
    const picked = form && form.choices && form.choices[block.key];
    if (Number.isInteger(picked) && picked >= 0 && picked < opts.length) return picked;
    const i = opts.findIndex(o => o && o.selected);
    return i >= 0 ? i : 0;
  }

  /** Whether option `i` of a `checks` block is checked (`form.checks[key]`: a list of indexes, else the
   *  server's `checked`). */
  function isChecked(block, i, form) {
    const picked = form && form.checks && form.checks[block.key];
    if (Array.isArray(picked)) return picked.includes(i);
    return Boolean(block.options && block.options[i] && block.options[i].checked);
  }

  const blocksOf = (item) => (item && Array.isArray(item.blocks) ? item.blocks : []);

  /** The primary button's label: the chosen option's `action_label` when a `choices` block offers one. */
  function primaryLabel(item, form) {
    const primary = (item && item.primary) || {};
    for (const b of blocksOf(item)) {
      if (b.type !== "choices" || !Array.isArray(b.options) || !b.options.length) continue;
      const o = b.options[chosen(b, form)];
      if (o && o.action_label) return o.action_label;
    }
    return primary.label || primary.action || "";
  }

  /** The confirmation the chosen option promises (its `message`), or "". */
  function chosenMessage(item, form) {
    for (const b of blocksOf(item)) {
      if (b.type !== "choices" || !Array.isArray(b.options) || !b.options.length) continue;
      const o = b.options[chosen(b, form)];
      if (o && o.message) return o.message;
    }
    return "";
  }

  /** The body of POST /api/now/act for `action` of `item`: {id, action, args?}. The primary action carries
   *  what the blocks contribute (the chosen `choices` value, the checked `checks` values) over its own args. */
  function actBody(item, action, form, isPrimary) {
    const args = { ...((action && action.args) || {}) };
    if (isPrimary) {
      for (const b of blocksOf(item)) {
        if (!b.key || !Array.isArray(b.options)) continue;
        if (b.type === "choices" && b.options.length) args[b.key] = b.options[chosen(b, form)].value;
        if (b.type === "checks") args[b.key] = b.options.filter((_, i) => isChecked(b, i, form)).map(o => o.value);
      }
    }
    const body = { id: item.id, action: action.action };
    if (Object.keys(args).length) body.args = args;
    return body;
  }

  // Context chats that stand for a ref the web has no page for (POST /api/chat/context kinds).
  const CONTEXT_REFS = ["transaction", "saving"];

  /** What a client action (`client: true`) does on the web: {type, …}.
   *  - `reply`: {type: "reply"} — the web cannot reply through the phone's shade (drawn disabled).
   *  - `open`: the thread of an approval → {type: "session", id}; a review → {type: "screen", id: "review"};
   *    a charge or a saving ("Ask Dana") → {type: "context", kind, id}; a card with blocks still folded →
   *    {type: "expand"}; a page that opens the ref's type (`opens(type)` → page id) → {type: "page", id, arg};
   *    else blocks to fold back → {type: "expand"}; else {type: "none"}. A self-check's card (ref
   *    `check`) opens Settings → Needs attention once its blocks are unfolded: {type: "screen", id: "attention", arg}.
   *  - a check's fix (server/docs/now.md, "Fixes"): `connect` → {type: "connect", addon} (the addon's connect
   *    flow on the Addons page); `screen` → {type: "screen", id}; `url` → {type: "url", url} (https only, else
   *    {type: "none"}). */
  function clientTarget(item, action, ctx) {
    const c = ctx || {};
    const name = action && action.action;
    const args = (action && action.args) || {};
    if (name === "reply") return { type: "reply" };
    if (name === "connect") return args.addon ? { type: "connect", addon: String(args.addon) } : { type: "none" };
    if (name === "screen") return args.screen ? { type: "screen", id: String(args.screen) } : { type: "none" };
    if (name === "url") return /^https:\/\//.test(String(args.url || "")) ? { type: "url", url: String(args.url) } : { type: "none" };
    const ref = (item && item.ref) || {};
    if (ref.type === "thread" && ref.id) return { type: "session", id: String(ref.id) };
    if (ref.type === "review") return { type: "screen", id: "review" };
    if (CONTEXT_REFS.includes(ref.type) && ref.id) return { type: "context", kind: ref.type, id: String(ref.id) };
    const hasBlocks = blocksOf(item).length > 0;
    if (hasBlocks && !c.expanded) return { type: "expand" };
    if (ref.type === "check" && ref.id) return { type: "screen", id: "attention", arg: String(ref.id) };
    const page = ref.type && typeof c.opens === "function" ? c.opens(ref.type) : null;
    if (page) return { type: "page", id: page, arg: ref.id === undefined ? null : String(ref.id) };
    return hasBlocks ? { type: "expand" } : { type: "none" };
  }

  /** Whether the web can do `action` at all (a reply through the phone's shade it cannot). */
  const canDo = (action) => Boolean(action) && !(action.client && action.action === "reply");

  /** Acting optimistically: the list without the card `id`, and what restores it on an error. */
  function removeItem(items, id) {
    const list = items || [];
    const index = list.findIndex(i => i && i.id === id);
    if (index < 0) return { items: list.slice(), removed: null, index: -1 };
    return { items: [...list.slice(0, index), ...list.slice(index + 1)], removed: list[index], index };
  }

  /** The list with `item` back at `index` (or the end), unless a refresh brought it back already. */
  function restoreItem(items, item, index) {
    const list = (items || []).slice();
    if (!item || list.some(i => i && i.id === item.id)) return list;
    list.splice(Math.max(0, Math.min(index, list.length)), 0, item);
    return list;
  }

  const signature = (item) => JSON.stringify(item);

  /** A refresh against what is on screen: {items, added, changed, removed} (ids). Cards being acted on
   *  (`pending`) stay out until the server answers; cards already shown keep their element (no arrival
   *  again), changed ones are drawn again in place, only `added` ones rise in. */
  function reconcile(prev, next, pending) {
    const wait = pending instanceof Set ? pending : new Set(pending || []);
    const before = new Map((prev || []).map(i => [i.id, signature(i)]));
    const items = (next || []).filter(i => i && i.id && !wait.has(i.id));
    const now = new Set(items.map(i => i.id));
    return {
      items,
      added: items.filter(i => !before.has(i.id)).map(i => i.id),
      changed: items.filter(i => before.has(i.id) && before.get(i.id) !== signature(i)).map(i => i.id),
      removed: [...before.keys()].filter(id => !now.has(id) && !wait.has(id)),
    };
  }

  /** "17 notifications · 1 needs you · 11 noise" (zero parts left out), or "" when there are none. */
  function notificationsLine(n) {
    const total = n && Number(n.total) > 0 ? Number(n.total) : 0;
    if (!total) return "";
    const out = [`${total} notification${total === 1 ? "" : "s"}`];
    if (Number(n.needs_you) > 0) out.push(`${n.needs_you} need${Number(n.needs_you) === 1 ? "s" : ""} you`);
    if (Number(n.noise) > 0) out.push(`${n.noise} noise`);
    return out.join(" · ");
  }

  /** "Updated just now" / "Updated 2 min ago" / "Updated 3 h ago" since `lastOk` (ms). */
  function updatedAgo(lastOk, at) {
    const s = Math.max(0, Math.floor(((at || 0) - (lastOk || 0)) / 1000));
    if (s < 60) return "Updated just now";
    if (s < 3600) return `Updated ${Math.floor(s / 60)} min ago`;
    return `Updated ${Math.floor(s / 3600)} h ago`;
  }

  /** The card the keyboard moves to: `delta` from `index` among `count`, kept inside (−1 = none yet). */
  function step(index, delta, count) {
    if (!count) return -1;
    if (index < 0) return delta < 0 ? count - 1 : 0;
    return Math.max(0, Math.min(count - 1, index + delta));
  }

  // An Android package name, as the server accepts it for an app icon.
  const PACKAGE_RE = /^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$/;

  /** A notification card's app icon: {package, src, letter} (src "" when the package cannot have one; the
   *  letter tile is the fallback), or null for any other card (it keeps its dot). */
  function appIcon(item) {
    const n = item && item.kind === "notification" ? item.notification : null;
    const pkg = n ? String(n.package || "") : "";
    if (!pkg) return null;
    const name = String(n.app || "").trim() || pkg.split(".").pop();
    const ok = pkg.length <= 200 && PACKAGE_RE.test(pkg);
    return { package: pkg, src: ok ? `/api/phone/app-icons/${pkg}.png` : "", letter: (Array.from(name)[0] || "?").toUpperCase() };
  }

  /** A text has right-to-left letters (Hebrew, Arabic). */
  const isRtl = (s) => /[֐-׿؀-ۿיִ-﷿ﹰ-﻿]/.test(String(s || ""));

  globalThis.DanaNow = {
    KIND_HUES, hueOf, dayHue, parts, dateLine, minutesNow, clock, ledeLine, dayRef, dayCells, dayTrack,
    chosen, isChecked, primaryLabel, chosenMessage, actBody, clientTarget, canDo,
    removeItem, restoreItem, reconcile, notificationsLine, updatedAgo, step, isRtl, appIcon,
  };
})();
