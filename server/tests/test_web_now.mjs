/**
 * The web Now screen's pure part (static/app-now-core.js, DanaNow): hues by kind (unknown kinds sky), the date line and the lede's fallback, the day strip's cells (gaps, past / next, refs, hidden when empty, the calendar note), the primary's label and body with choices / checks, what client actions open, acting optimistically and restoring, reconciling a refresh (only new cards are added), the notifications line, "Updated … ago", keyboard steps.
 *
 * Run: bin/test test_web_now
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(here, "..");

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
    if (cond) { pass++; console.log(`[PASS] ${name}`); }
    else { failures.push(name); console.log(`[FAIL] ${name}${detail ? " — " + JSON.stringify(detail) : ""}`); }
}
const plain = (v) => JSON.parse(JSON.stringify(v));
const eq = (a, b) => JSON.stringify(plain(a)) === JSON.stringify(b);

const sb = { console };
vm.createContext(sb);
vm.runInContext(fs.readFileSync(path.join(SERVER, "static/app-now-core.js"), "utf8"), sb);
const N = sb.DanaNow;
check("app-now-core.js sets globalThis.DanaNow", Boolean(N && N.dayCells && N.reconcile));

// --- Hues -----------------------------------------------------------------------------------------
const hue = (kind, ref) => N.hueOf({ kind, ref });
check("approval ember, notification teal, reminder amber, check rose, review sky",
      [hue("approval"), hue("notification"), hue("reminder"), hue("check"), hue("review")].join() === "ember,teal,amber,rose,sky");
check("a suggestion is plum, a call's suggestion sage",
      hue("suggestion", { type: "notification", id: "n1" }) === "plum" && hue("suggestion", { type: "call", id: "c1" }) === "sage");
check("money: a charge that stands out rose, a saving sage",
      hue("money", { type: "transaction", id: "1" }) === "rose" && hue("money", { type: "saving", id: "x" }) === "sage");
check("a kind Dana does not know (an addon's own, e.g. trains) is sky", hue("israel_rail") === "sky" && hue(undefined) === "sky");
check("day items: reminders amber, events and anything else sky",
      N.dayHue({ kind: "reminder" }) === "amber" && N.dayHue({ kind: "event" }) === "sky" && N.dayHue({ kind: "train" }) === "sky");

// --- Header ---------------------------------------------------------------------------------------
check("the date line in caps from the server's own clock", N.dateLine("2026-11-22T14:58:55+02:00") === "SUNDAY · 22 NOVEMBER", N.dateLine("2026-11-22T14:58:55+02:00"));
check("the date line ignores the browser's zone (late at night, a +14 offset)", N.dateLine("2026-01-01T23:59:00+14:00") === "THURSDAY · 1 JANUARY");
check("an unreadable time gives no date line", N.dateLine("soon") === "" && N.dateLine(null) === "");
check("the lede is the server's when it has one", N.ledeLine({ lede: "Noa at 19:30.", items: [] }) === "Noa at 19:30.");
check("no lede and nothing waiting: \"Nothing needs you right now.\"",
      N.ledeLine({ lede: null, items: [], now: "2026-11-22T09:00:00+02:00" }) === "Nothing needs you right now.");
check("no lede but cards waiting: a line from the time of day, never \"Nothing needs you\"",
      N.ledeLine({ lede: null, items: [{ id: "x" }], now: "2026-11-22T09:00:00+02:00" }) === "Good morning."
      && N.ledeLine({ lede: "  ", items: [{ id: "x" }], now: "2026-11-22T20:00:00+02:00" }) === "Good evening.");
check("the clock runs on from the payload's now", N.minutesNow("2026-11-22T14:58:00+02:00", 1000, 1000 + 5 * 60000) === 15 * 60 + 3
      && N.clock(15 * 60 + 3) === "15:03");

// --- Day strip ------------------------------------------------------------------------------------
const slot = (key, items = []) => ({ key, label: /^\d+$/.test(key) ? `${key.padStart(2, "0")}:00` : key, items });
const DAY = { calendar: true, slots: [
    slot("15", [{ title: "Call the plumber", kind: "reminder", task_id: "t9", time: "15:10" }]),
    slot("16"), slot("17"), slot("18"),
    slot("19", [{ title: "Dinner with Noa", kind: "event", time: "19:30", event_id: "e1" }, { title: "Train to Haifa", kind: "train", time: "19:45" }]),
    slot("20"), slot("21"),
    slot("home", [{ title: "Take out the trash", kind: "reminder", task_id: "t2" }]),
    slot("wake"),
] };
const day = N.dayCells(DAY, 15 * 60 + 20);
check("cells: the hours with items, one gap cell for the empty run between, At home; no trailing gap, no empty On waking",
      eq(day.cells.map(c => c.type), ["hour", "gap", "hour", "home"]), plain(day.cells.map(c => c.type)));
check("the gap says the hours it stands for", day.cells[1].label === "16:00–18:00" && day.cells[1].hours === 3);
check("an item whose time has gone by on the user's clock is past", day.cells[0].items[0].past === true);
check("the next one is the first timed item still to come", day.cells[2].items[0].next === true
      && day.cells.flatMap(c => c.items).filter(i => i.next).length === 1);
check("refs come from <type>_id fields; none without one",
      eq(day.cells[2].items[0].ref, { type: "event", id: "e1" }) && day.cells[2].items[1].ref === null
      && eq(day.cells[3].items[0].ref, { type: "task", id: "t2" }));
check("items carry their hue", day.cells[0].items[0].hue === "amber" && day.cells[2].items[0].hue === "sky");
const lead = N.dayCells({ slots: [slot("15"), slot("16", [{ title: "x", kind: "event", time: "16:00" }])] }, 14 * 60);
check("a run of empty hours before the first item is a gap too (how far away it is)", eq(lead.cells.map(c => c.type), ["gap", "hour"]) && lead.cells[0].label === "15:00");
const empty = N.dayCells({ calendar: true, slots: [slot("15"), slot("home"), slot("wake")] }, 900);
check("nothing in any slot: the strip hides", empty.show === false && empty.cells.length === 0 && empty.calendarNote === false);
check("the calendar couldn't be read: the note shows even with an empty day", N.dayCells({ calendar: false, slots: [] }, 900).calendarNote === true);
check("no day at all is an empty strip", N.dayCells(undefined, null).show === false);

// --- Actions --------------------------------------------------------------------------------------
const SUG = {
    id: "sug:cs1", kind: "suggestion", title: "Bring the ladder", ref: { type: "call", id: "c1" },
    primary: { action: "accept", label: "Add to reminders", args: { triggers: {} } },
    secondary: [{ action: "dismiss", label: "Dismiss" }],
    blocks: [{ type: "text", label: "From the call", text: "…" },
             { type: "choices", label: "Remind me", key: "triggers", options: [
                 { label: "No reminder", value: {}, selected: true, action_label: "Add to reminders", message: "Added" },
                 { label: "Tomorrow 09:00", value: { time: "2026-11-23 09:00" }, selected: false, action_label: "Remind me tomorrow 09:00", message: "Reminder set · tomorrow 09:00" }] }],
};
check("the primary's label follows the server's selected choice", N.primaryLabel(SUG, null) === "Add to reminders");
const picked = { choices: { triggers: 1 }, checks: {} };
check("picking another choice changes the primary's label and the promised message",
      N.primaryLabel(SUG, picked) === "Remind me tomorrow 09:00" && N.chosenMessage(SUG, picked) === "Reminder set · tomorrow 09:00");
check("the primary sends the chosen value as args[key], over its own args",
      eq(N.actBody(SUG, SUG.primary, picked, true), { id: "sug:cs1", action: "accept", args: { triggers: { time: "2026-11-23 09:00" } } }));
check("a secondary action sends only its own args", eq(N.actBody(SUG, SUG.secondary[0], picked, false), { id: "sug:cs1", action: "dismiss" }));
check("snooze args are carried", eq(N.actBody({ id: "task:t1" }, { action: "snooze", args: { minutes: 60 } }, null, false),
      { id: "task:t1", action: "snooze", args: { minutes: 60 } }));
const REVIEW = { id: "review:3", kind: "review", primary: { action: "apply", label: "Apply" }, ref: { type: "review", id: "3" },
                 blocks: [{ type: "checks", label: "Proposed", key: "items", options: [
                     { label: "a", value: "task:1", checked: true }, { label: "b", value: "task:2", checked: false }, { label: "c", value: "task:3", checked: true }] }] };
check("checks: the server's checked values by default", eq(N.actBody(REVIEW, REVIEW.primary, null, true).args, { items: ["task:1", "task:3"] }));
check("checks: the user's own ticks", eq(N.actBody(REVIEW, REVIEW.primary, { checks: { items: [1] } }, true).args, { items: ["task:2"] }));

const opens = (type) => ({ task: "tasks", call: "calls" }[type] || null);
const target = (item, action, expanded) => plain(N.clientTarget(item, action, { expanded, opens }));
check("reply cannot be done on the web", target({ id: "notif:1", ref: { type: "notification" } }, { action: "reply", client: true }).type === "reply"
      && N.canDo({ action: "reply", client: true }) === false && N.canDo({ action: "dismiss" }) === true);
check("an approval's open goes to its thread", eq(target({ ref: { type: "thread", id: "main" } }, { action: "open", client: true }), { type: "session", id: "main" }));
check("a review's open goes to the review screen", target({ ref: { type: "review", id: "3" }, blocks: [{}] }, { action: "open" }).id === "review");
check("Ask Dana on a charge or a saving starts its context chat",
      eq(target({ ref: { type: "transaction", id: 7 }, blocks: [{}] }, { action: "open" }), { type: "context", kind: "transaction", id: "7" })
      && target({ ref: { type: "saving", id: "phone_plan" } }, { action: "open" }).kind === "saving");
check("open on a folded card with blocks expands it first", target({ ref: { type: "check", id: "wake" }, blocks: [{}] }, { action: "open" }, false).type === "expand");
check("open on an expanded card goes to the page that opens its ref, with the id",
      eq(target({ ref: { type: "task", id: "t1" }, blocks: [{}] }, { action: "open" }, true), { type: "page", id: "tasks", arg: "t1" }));
check("open with no page for the ref folds an expanded card back, else does nothing",
      target({ ref: { type: "check", id: "wake" }, blocks: [{}] }, { action: "open" }, true).type === "expand"
      && target({ ref: { type: "check", id: "wake" } }, { action: "open" }, false).type === "none");

// --- Optimistic acting, refreshes ---------------------------------------------------------------------
const A = { id: "a", title: "A" }, B = { id: "b", title: "B" }, Cc = { id: "c", title: "C" };
const out = N.removeItem([A, B, Cc], "b");
check("acting removes the card at once and remembers where it was", eq(out.items.map(i => i.id), ["a", "c"]) && out.index === 1 && out.removed.id === "b");
check("removing a card that is not there changes nothing", N.removeItem([A], "zz").index === -1 && N.removeItem([A], "zz").items.length === 1);
check("an error puts it back where it was", eq(N.restoreItem(out.items, out.removed, out.index).map(i => i.id), ["a", "b", "c"]));
check("…unless a refresh brought it back already", eq(N.restoreItem([A, B, Cc], B, 0).map(i => i.id), ["a", "b", "c"]));
check("…and past the end it goes last", eq(N.restoreItem([A], B, 9).map(i => i.id), ["a", "b"]));
const rec = N.reconcile([A, B], [A, { id: "b", title: "B2" }, Cc], new Set());
check("a refresh: new cards are added, changed ones redrawn, unchanged ones kept",
      eq(rec.added, ["c"]) && eq(rec.changed, ["b"]) && eq(rec.removed, []) && rec.items.length === 3);
const rec2 = N.reconcile([A, B, Cc], [A, B, Cc, { id: "d" }], ["b"]);
check("a card being acted on stays off screen even if the server still has it, and is not \"removed\"",
      eq(rec2.items.map(i => i.id), ["a", "c", "d"]) && eq(rec2.added, ["d"]) && eq(rec2.removed, []));
check("a card the server dropped is removed", eq(N.reconcile([A, B], [A], []).removed, ["b"]));
check("the same payload twice: nothing added or changed (no arrival again)",
      (() => { const r = N.reconcile([A, B], [A, B], []); return !r.added.length && !r.changed.length && !r.removed.length; })());

// --- The rest -----------------------------------------------------------------------------------------
check("the notifications line", N.notificationsLine({ total: 17, needs_you: 1, noise: 11 }) === "17 notifications · 1 needs you · 11 noise"
      && N.notificationsLine({ total: 1, needs_you: 0, noise: 0 }) === "1 notification"
      && N.notificationsLine({ total: 3, needs_you: 2, noise: 0 }) === "3 notifications · 2 need you");
check("no notifications: no line", N.notificationsLine({ total: 0 }) === "" && N.notificationsLine(undefined) === "");
check("Updated … ago", N.updatedAgo(0, 20000) === "Updated just now" && N.updatedAgo(0, 125000) === "Updated 2 min ago"
      && N.updatedAgo(0, 3 * 3600000 + 5) === "Updated 3 h ago");
check("keyboard steps stay inside the list", N.step(-1, 1, 3) === 0 && N.step(-1, -1, 3) === 2 && N.step(2, 1, 3) === 2
      && N.step(0, -1, 3) === 0 && N.step(1, 1, 3) === 2 && N.step(0, 1, 0) === -1);
check("Hebrew is right-to-left", N.isRtl("להזמין טכנאי") === true && N.isRtl("Call Sam") === false);

// --- The shell names no addon page in the Now files ----------------------------------------------------
const src = ["app-now-core.js", "app-now.js"].map(f => fs.readFileSync(path.join(SERVER, "static", f), "utf8")).join("\n");
const named = ["tasks", "inbox", "calls", "calendar", "money", "people", "shopping", "projects", "memory"].filter(p => src.includes(`"${p}"`));
check("the Now screen names no addon page (it opens pages by the ref types they declare)", !named.length, named);

console.log(`\n${failures.length ? `${failures.length} check(s) failed` : "All checks passed"} (${pass} passed)`);
process.exit(failures.length ? 1 : 0);
