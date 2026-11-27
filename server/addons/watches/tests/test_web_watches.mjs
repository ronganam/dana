/**
 * The Watches page's pure part (addons/watches/web/watches-core.js, DanaWatchesCore): a spec in words, the list's
 * groups and search, the editor's helpers, validation and the server's errors on their fields, the dry run in
 * words, events in a line, and the live pipeline (counters, feed, dot moves, the diagram's geometry).
 * Made-up data only.
 *
 * Run: bin/test test_web_watches
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(here, "..", "..", "..");
const sandbox = { console, JSON, Object, Array, String, Number, Boolean, Math, Date, Map, Set, isNaN, parseFloat, parseInt, Infinity };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(SERVER, "addons/watches/web/watches-core.js"), "utf8"), sandbox);
const C = sandbox.DanaWatchesCore;

let pass = 0;
const failures = [];
function check(name, cond, detail) {
  if (cond) { pass++; console.log(`[PASS] ${name}`); }
  else { failures.push(name); console.log(`[FAIL] ${name}${detail !== undefined ? " — " + JSON.stringify(detail) : ""}`); }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// The page files exist and the page registers itself with its core.
const page = fs.readFileSync(path.join(SERVER, "addons/watches/web/watches.js"), "utf8");
const css = fs.readFileSync(path.join(SERVER, "addons/watches/web/watches.css"), "utf8");
check("the page registers with Dana.panel and loads its core first", /Dana\.panel\(\{\s*scripts: \["watches-core\.js"\]/.test(page));
check("the stylesheet hardcodes no colour (tokens only)", !/#[0-9a-fA-F]{3,8}\b/.test(css.replace(/\/\*[\s\S]*?\*\//g, "")) && !/rgba?\(\s*\d/.test(css));

const NOW = new Date(2026, 10, 18, 9, 30);   // Wednesday 18 Nov 2026, local time

const catalog = {
  event_types: [
    { name: "phone.notification.received", about: "A phone notification", fields: { app: "the app", sender: "who sent it", text: "the text", amount: "ILS amount or null" } },
    { name: "money.transaction.added", about: "A new transaction", fields: { amount: "ILS, signed", counterparty: "who", counterparty_kind: "person/business/bank/unknown", direction: "in/out" } },
    { name: "core.signal.car", about: "Car", fields: { action: "enter/leave" } },
  ],
  ops: C.OPS, action_types: C.ACTION_TYPES, tools: ["calendar_add_event", "whatsapp_send"],
};

const transfer = {
  title: "Transfer about 2k", kind: "watch", task_title: "", task_id: null,
  triggers: [{ id: "t1", kind: "event", event_type: "money.transaction.added",
    where: [{ field: "amount", op: "approx", value: "2000", tolerance_pct: 10 }, { field: "counterparty_kind", op: "eq", value: "person" }],
    keywords: [], during: "", semantic: "" }],
  window_start: "2026-11-18 09:00", window_end: "2026-11-19 23:59", repeat: "once", cooldown_minutes: 10,
  on_match: [{ type: "notify", text: "The transfer arrived", urgency: "now" }], on_deadline: [],
  evidence_event_types: [], evidence_keywords: [], evidence_question: "", unsupported: "",
};
const reminder = {
  title: "Call Maya", kind: "reminder", task_title: "Call Maya", task_id: "task-1",
  triggers: [{ id: "t1", kind: "time", at: "2026-11-18 20:30", every: null },
    { id: "t2", kind: "time", relative_to: "t1", offset_minutes: -30 },
    { id: "t3", kind: "event", event_type: "core.signal.car", where: [{ field: "action", op: "eq", value: "enter" }], keywords: [], semantic: "" }],
  window_start: "2026-11-18 09:00", window_end: "2026-11-18 23:00", repeat: "once",
  on_match: [{ type: "notify", text: "Call Maya", urgency: "now" }], on_deadline: [],
  evidence_event_types: ["phone.notification.received"], evidence_keywords: ["Maya"], evidence_question: "Did they talk?",
};
const agentSpec = {
  title: "Dates from Gal", kind: "watch",
  triggers: [{ id: "t1", kind: "event", event_type: "phone.notification.received", where: [{ field: "sender", op: "contains", value: "Gal" }],
    keywords: ["date", "meet"], during: "08:00-22:00", semantic: "Does the message propose a date?" }],
  repeat: "each", cooldown_minutes: 10,
  on_match: [{ type: "agent", instructions: "Add it to my calendar", autonomy: "ask_first" }, { type: "notify", text: "Added", urgency: "glance" }],
  on_deadline: [],
};

// ---------- words ----------

check("conditions in words: approx with its tolerance, eq quoted, a list, between, exists, a number",
  C.conditionWords(transfer.triggers[0].where[0]) === "amount is about 2,000 (±10%)"
  && C.conditionWords(transfer.triggers[0].where[1]) === "counterparty kind is “person”"
  && C.conditionWords({ field: "sender", op: "in", value: "Gal | Maya" }) === "sender is one of “Gal” / “Maya”"
  && C.conditionWords({ field: "amount", op: "between", value: "100", value2: "2500" }) === "amount is between 100 and 2,500"
  && C.conditionWords({ field: "amount", op: "exists" }) === "amount is present"
  && C.conditionWords({ field: "amount", op: "gt", value: "1000" }) === "amount is more than 1,000"
  && C.conditionWords({ field: "sender", op: "contains", value: "" }) === "sender contains …"
  && C.conditionWords({ field: "amount", op: "between", value: "5" }) === "amount is between 5 and …",
  [C.conditionWords(transfer.triggers[0].where[0]), C.conditionWords({ field: "sender", op: "in", value: "Gal | Maya" })]);

check("an event trigger in words: label, conditions, keywords, hours",
  C.triggerWords(agentSpec.triggers[0], agentSpec, NOW) === "Notification: sender contains “Gal” · mentions “date” or “meet” · 08:00–22:00",
  C.triggerWords(agentSpec.triggers[0], agentSpec, NOW));
check("time triggers in words: today, relative to another (before / after, hours), repeating",
  C.triggerWords(reminder.triggers[0], reminder, NOW) === "today 20:30"
  && C.triggerWords(reminder.triggers[1], reminder, NOW) === "30 min before today 20:30"
  && C.triggerWords({ id: "x", kind: "time", relative_to: "t1", offset_minutes: 120 }, reminder, NOW) === "2 h after today 20:30"
  && C.triggerWords({ id: "d", kind: "time", at: "2026-11-20 08:00", every: "daily" }, {}, NOW) === "every day at 08:00"
  && C.triggerWords({ id: "w", kind: "time", at: "2026-11-20 08:00", every: "weekly" }, {}, NOW) === "every Friday at 08:00"
  && C.triggerWords({ id: "m", kind: "time", at: "2026-11-22 08:00", every: "monthly" }, {}, NOW) === "monthly on the 22nd at 08:00",
  [C.triggerWords(reminder.triggers[1], reminder, NOW), C.triggerWords({ id: "w", kind: "time", at: "2026-11-20 08:00", every: "weekly" }, {}, NOW)]);

check("actions in words: tell me quoted, an agent that asks first, a tool that just acts",
  C.actionWords(transfer.on_match[0]) === "Tell me “The transfer arrived”"
  && C.actionWords(agentSpec.on_match[0]) === "Agent: Add it to my calendar (asks you first)"
  && C.actionWords({ type: "tool", tool: "whatsapp_send", autonomy: "act_and_tell" }) === "Run whatsapp_send");

const chips = C.triggerChips(agentSpec, NOW);
check("chips: an event trigger with its lane's icon and hue, the AI check flagged; time chips amber",
  chips.length === 1 && chips[0].label === "Notification" && chips[0].icon === "bell" && chips[0].hue === "teal" && chips[0].ai === true
  && C.triggerChips(reminder, NOW)[0].kind === "time" && C.triggerChips(reminder, NOW)[0].hue === "amber" && C.triggerChips(reminder, NOW)[0].ai === false, chips);
check("action chips: an agent marked, a tool named",
  eq(C.actionChips(agentSpec).map((a) => [a.label, a.ai]), [["Agent", true], ["Tell me", false]])
  && C.actionChips({ on_match: [{ type: "tool", tool: "whatsapp_send" }] })[0].label === "Run whatsapp_send");

check("lanes: known event types, the clock for none, an unknown type by its name",
  C.laneOf("money.transaction.added").key === "money" && C.laneOf("phone.email.received").key === "email"
  && C.laneOf("phone.call.ended").key === "calls" && C.laneOf("core.signal.location").key === "places"
  && C.laneOf("core.user.woke").key === "wake" && C.laneOf("tasks.task.completed").key === "tasks"
  && C.laneOf(null).key === "clock" && C.laneOf("garden.sprinkler.started").key === "garden.sprinkler.started"
  && C.laneOf("garden.sprinkler.started").label === "Sprinkler");

check("dates: today / tomorrow / yesterday / a weekday; ago; the datetime-local value and back",
  C.whenWords("2026-11-19 08:00", NOW) === "tomorrow 08:00" && C.whenWords("2026-11-17 21:15", NOW) === "yesterday 21:15"
  && C.whenWords("2026-11-24 20:30", NOW) === "Tue 24 Nov 20:30"
  && C.agoWords(new Date(2026, 10, 18, 9, 25), NOW) === "5 min ago" && C.agoWords(new Date(2026, 10, 18, 7, 30), NOW) === "2 h ago"
  && C.agoWords(new Date(2026, 10, 15, 9, 0), NOW) === "3 days ago"
  && C.toInput("2026-11-18 20:30") === "2026-11-18T20:30" && C.fromInput("2026-11-18T20:30") === "2026-11-18 20:30" && C.toInput("") === ""
  && eq(C.splitDuring("8:00-22:00"), ["08:00", "22:00"]) && eq(C.splitDuring(""), ["", ""]) && C.joinDuring("08:00", "") === "");

// ---------- the list ----------

const watches = [
  { id: "w1", title: "Transfer about 2k", kind: "watch", status: "active", request: "tell me when about 2k arrives from a person", spec: transfer, created_at: "2026-11-17T10:00:00", last_fired_at: null, next_time: null },
  { id: "w2", title: "Call Maya", kind: "reminder", status: "active", request: "remind me at 20:30 to call Maya", spec: reminder, created_at: "2026-11-18T08:00:00", next_time: "2026-11-18 20:00" },
  { id: "w3", title: "Dates from Gal", kind: "watch", status: "paused", request: "when Gal sends a date put it in my calendar", spec: agentSpec, created_at: "2026-11-10T08:00:00" },
  { id: "w4", title: "Old one", kind: "watch", status: "expired", request: "", spec: transfer, created_at: "2026-11-01T08:00:00", last_fired_at: "2026-11-02T08:00:00" },
  { id: "w5", title: "Pay rent", kind: "reminder", status: "active", request: "", spec: reminder, created_at: "2026-11-18T08:00:00", next_time: "2026-11-18 12:00" },
  { id: "w6", title: "Done one", kind: "reminder", status: "done", spec: reminder, created_at: "2026-11-05T08:00:00", last_fired_at: "2026-11-06T08:00:00" },
];
const groups = C.groupWatches(watches);
check("groups: active reminders (next first), active watches, paused, done and expired (latest first)",
  eq(groups.map((g) => [g.key, g.items.map((w) => w.id)]), [["reminders", ["w5", "w2"]], ["watches", ["w1"]], ["paused", ["w3"]], ["done", ["w6", "w4"]]]),
  groups.map((g) => [g.key, g.items.map((w) => w.id)]));
check("search: the title, the user's words or the spec, any case; empty groups left out",
  eq(C.groupWatches(watches, "CALENDAR").map((g) => g.items.map((w) => w.id)), [["w3"]])
  && eq(C.groupWatches(watches, "counterparty").map((g) => g.key), ["watches", "done"])
  && C.groupWatches(watches, "nothing like this").length === 0);
check("row status: next, paused, expired, done, watching",
  C.rowStatus(watches[1], NOW) === "Next today 20:00" && C.rowStatus(watches[2], NOW) === "Paused" && C.rowStatus(watches[3], NOW) === "Expired"
  && C.rowStatus(watches[0], NOW) === "Watching" && C.rowStatus({ status: "active", last_fired_at: "2026-11-18T09:00:00" }, NOW) === "Fired 30 min ago");
check("row hue: reminders amber, watches sky, an agent plum, paused mute",
  C.rowHue(watches[1]) === "amber" && C.rowHue(watches[0]) === "sky" && C.rowHue({ status: "active", spec: agentSpec }) === "plum" && C.rowHue(watches[2]) === "mute");
check("kicker: active and paused counts", C.kicker(watches) === "3 active · 1 paused" && C.kicker([]) === "none active");

// ---------- editing ----------

const s0 = C.clone(transfer);
const s1 = C.addTrigger(s0, "event", catalog);
check("add an event trigger: a new id, the catalog's first type, nothing changed in place",
  s1.triggers.length === 2 && s1.triggers[1].id === "t2" && s1.triggers[1].event_type === "phone.notification.received" && s0.triggers.length === 1);
const s2 = C.addTrigger(C.addTrigger(s0, "time", catalog, NOW), "relative", catalog, NOW);
check("add a time (tomorrow 09:00) then one relative to it (30 min before)",
  s2.triggers[1].at === "2026-11-19 09:00" && s2.triggers[2].relative_to === "t2" && s2.triggers[2].offset_minutes === -30);
check("remove a timed trigger: the ones relative to it go too", C.removeTrigger(s2, 1).triggers.map((t) => t.id).join() === "t1");
const s3 = C.setTrigger(C.addCondition(s0, 0, catalog), 0, "event_type", "core.signal.car", catalog);
check("a new event type keeps only conditions on fields it has", s3.triggers[0].where.length === 0 && s3.triggers[0].event_type === "core.signal.car");
check("add a condition: the type's first field, contains, empty", eq(C.addCondition(s0, 0, catalog).triggers[0].where[2], { field: "amount", op: "contains", value: "" }));
const between = C.setCondition(s0, 0, 0, "op", "between");
check("an op change drops what it does not use (approx's tolerance) and between gets a second value",
  !("tolerance_pct" in between.triggers[0].where[0]) && C.setCondition(between, 0, 0, "value2", "2500").triggers[0].where[0].value2 === "2500"
  && !("value2" in C.setCondition(C.setCondition(between, 0, 0, "value2", "9"), 0, 0, "op", "gt").triggers[0].where[0])
  && C.setCondition(s0, 0, 1, "op", "approx").triggers[0].where[1].tolerance_pct === 10);
check("setting a trigger field: offset as a number, every '' → null, an empty optional field removed",
  C.setTrigger(reminder, 1, "offset_minutes", "-45").triggers[1].offset_minutes === -45
  && C.setTrigger(reminder, 0, "every", "").triggers[0].every === null
  && !("semantic" in C.setTrigger(agentSpec, 0, "semantic", "").triggers[0]));
const kw = C.addKeyword(C.addKeyword(C.addKeyword(s0, 0, " Rent "), 0, "rent"), 0, "");
check("keywords: trimmed, no repeats in any case, blanks ignored; removable; evidence keywords too",
  eq(kw.triggers[0].keywords, ["Rent"]) && eq(C.removeKeyword(kw, 0, 0).triggers[0].keywords, [])
  && eq(C.addKeyword(reminder, "evidence", "call").evidence_keywords, ["Maya", "call"]) && eq(C.removeKeyword(reminder, "evidence", 0).evidence_keywords, []));
check("evidence types toggle on and off",
  eq(C.toggleEvidenceType(reminder, "core.signal.car").evidence_event_types, ["phone.notification.received", "core.signal.car"])
  && eq(C.toggleEvidenceType(reminder, "phone.notification.received").evidence_event_types, []));
const a1 = C.addAction(s0, "on_deadline", "notify");
check("actions: add to a list, change type keeping the words, remove",
  eq(a1.on_deadline, [{ type: "notify", text: "", urgency: "now" }])
  && eq(C.setAction(transfer, "on_match", 0, "type", "agent").on_match[0], { type: "agent", instructions: "The transfer arrived", autonomy: "ask_first" })
  && C.setAction(agentSpec, "on_match", 0, "autonomy", "act_and_tell").on_match[0].autonomy === "act_and_tell"
  && C.removeAction(agentSpec, "on_match", 0).on_match.length === 1);
check("top-level fields: cooldown as a number, never negative", C.setSpec(s0, "cooldown_minutes", "15").cooldown_minutes === 15 && C.setSpec(s0, "cooldown_minutes", "-3").cooldown_minutes === 0);
check("dirty: same / changed", C.same(transfer, C.clone(transfer)) && !C.same(transfer, C.setSpec(transfer, "title", "x")));

// ---------- validation ----------

check("a good spec has no errors", eq(C.validate(transfer, catalog), []) && eq(C.validate(reminder, catalog), []) && eq(C.validate(agentSpec, catalog), []),
  [C.validate(transfer, catalog), C.validate(reminder, catalog)]);
const broken = {
  title: " ", triggers: [
    { id: "t1", kind: "event", event_type: "money.transaction.added", where: [{ field: "amount", op: "gt", value: "lots" }, { field: "amount", op: "between", value: "1" }] },
    { id: "t2", kind: "time", at: "" }, { id: "t3", kind: "time", relative_to: "t9", offset_minutes: 5 },
    { id: "t4", kind: "event", event_type: "garden.sprinkler.started", where: [], keywords: [] },
  ],
  window_start: "2026-11-19 10:00", window_end: "2026-11-18 10:00",
  on_match: [{ type: "tool", tool: "x", args_json: "[1]" }, { type: "notify", text: "" }],
};
const errs = C.validate(broken, catalog).map((e) => e.path);
check("validation: each problem on its field",
  eq(errs, ["title", "triggers.0.where.0.value", "triggers.0.where.1.value2", "triggers.1.at", "triggers.2.relative_to", "triggers.3.event_type", "triggers.3.where",
    "window_end", "on_match.0.args_json", "on_match.1.text"]), errs);
check("validation: no triggers, no actions", eq(C.validate({ title: "x" }, catalog).map((e) => e.path), ["triggers", "on_match"]));

const server = C.normalizeErrors({ error: "invalid", errors: ["triggers[0].where[1].value: needs a number", "spec.on_match.0.tool: unknown tool", "The budget is exceeded", { path: ["triggers", 2, "semantic"], message: "too broad" }] });
check("server errors: paths read out of strings and objects, the rest left loose",
  eq(server, [{ path: "triggers.0.where.1.value", message: "needs a number" }, { path: "on_match.0.tool", message: "unknown tool" }, { path: "", message: "The budget is exceeded" }, { path: "triggers.2.semantic", message: "too broad" }]), server);
check("server errors: a lone error message", eq(C.normalizeErrors({ error: "Couldn't understand that" }), [{ path: "", message: "Couldn't understand that" }]) && eq(C.normalizeErrors(null), []));
check("errors at a place and the ones left for the Save bar",
  C.errorsAt(server, "triggers.0").length === 1 && C.errorsAt(server, "triggers").length === 2
  && eq(C.errorsElsewhere(server, ["triggers", "on_match"]).map((e) => e.message), ["The budget is exceeded"]));

// ---------- dry run ----------

check("load in words: matches and rate, none, an AI check, over budget, too often without one",
  C.loadWords({ matches: 12, per_day: 0.857, over_budget: false, sample: [] }, false).line === "Last 2 weeks: 12 matches (0.9/day)"
  && C.loadWords({ matches: 0, per_day: 0 }, false).line === "Last 2 weeks: no matches"
  && C.loadWords({ matches: 1, per_day: 0.07 }, true).line === "Last 2 weeks: 1 match (0.1/day) · each gets an AI check"
  && C.loadWords({ matches_14d: 420, per_day: 30, semantic: true, over_budget: true }).tone === "rose"
  && /budget/.test(C.loadWords({ matches_14d: 420, per_day: 30, semantic: true, over_budget: true }).warn)
  && C.loadWords({ matches: 140, per_day: 10 }, false).tone === "amber"
  && C.loadWords(null) === null);

check("events in a line: money signed with the shekel, a notification, a call, car and place",
  C.eventSummary("money.transaction.added", { amount: 2150, counterparty: "Robin", category: "Transfers" }) === "+2,150 ₪ · Robin · Transfers"
  && C.eventSummary("money.transaction.added", { amount: -42.5, counterparty: "Cafe" }) === "−42.5 ₪ · Cafe"
  && C.eventSummary("phone.notification.received", { app: "WhatsApp", sender: "Gal", text: "dinner Friday?" }) === "WhatsApp · Gal: dinner Friday?"
  && C.eventSummary("phone.call.ended", { person: "Maya", direction: "incoming", title: "Weekend plans" }) === "Maya · incoming · Weekend plans"
  && C.eventSummary("core.signal.car", { action: "enter" }) === "Got in the car"
  && C.eventSummary("core.signal.location", { zone: "Gym", action: "arrive" }) === "Arrived at Gym");
const fa = C.fireActions({ actions: [{ type: "agent", status: "held", detail: "", run_id: "r1" }, { type: "notify", status: "done" }, { type: "tool", status: "failed", detail: "boom" }] });
check("a fire's actions: their words, status and hue, the run kept",
  eq(fa.map((a) => [a.label, a.statusLabel, a.hue, a.run_id]), [["Agent", "Waiting for your OK", "amber", "r1"], ["Tell me", "Done", "sage", null], ["Run a tool", "Failed", "rose", null]]));

// ---------- the live pipeline ----------

const snapshot = {
  stats: { since: "2026-11-18T08:00:00", events_in: 10, by_type: { "money.transaction.added": 3, "phone.notification.received": 7 }, filter_pass: 2,
    semantic_checks: 1, semantic_yes: 0, fires: 1, actions: { notify: 1 }, avg_filter_us: 40, cost_usd: 0.0003 },
  recent: [
    { seq: 1, at: "2026-11-18T09:00:00", stage: "event", event_type: "phone.notification.received", event_id: "n1", summary: "WhatsApp · Gal", watch_id: null, watch_title: null, trigger_id: null, result: null },
    { seq: 2, at: "2026-11-18T09:00:00", stage: "filter", event_type: "phone.notification.received", event_id: "n1", summary: "WhatsApp · Gal", result: "drop", candidates: 0, us: 30 },
  ],
  watches_active: 3, triggers_active: 5,
};
const st = C.pipelineInit(snapshot, catalog);
check("init: the server's counters as they are; lanes from the catalog and the seen types, the clock last; the feed from recent",
  st.stats.events_in === 10 && st.lastSeq === 2 && eq(C.lanes(st).map((l) => l.key), ["notifications", "money", "car", "clock"])
  && st.feed.length === 1 && st.feed[0].dropped === true, [C.lanes(st).map((l) => l.key), st.stats.events_in]);
check("a drop in words: quiet, no watch matched", eq(C.feedWords(st.feed[0]), { head: "WhatsApp · Gal", steps: ["no watch matched"], tone: "mute", quiet: true }));

const evs = [
  { seq: 3, at: "2026-11-18T09:31:00", stage: "event", event_type: "money.transaction.added", event_id: "m7", summary: "Money · +2,050 ₪ from a person" },
  { seq: 4, at: "2026-11-18T09:31:00", stage: "filter", event_type: "money.transaction.added", event_id: "m7", result: "pass", candidates: 1, us: 60 },
  { seq: 5, at: "2026-11-18T09:31:00", stage: "fire", event_type: "money.transaction.added", event_id: "m7", watch_id: "w1", watch_title: "Transfer about 2k", trigger_id: "t1" },
  { seq: 6, at: "2026-11-18T09:31:00", stage: "action", event_type: "money.transaction.added", event_id: "m7", watch_id: "w1", watch_title: "Transfer about 2k", trigger_id: "t1", result: "done", action_type: "notify", summary: "Tell me" },
];
const moves = evs.map((e) => C.pipelineApply(st, e));
check("an event: its dot leaves its lane for the filter, counted in and by type",
  eq(moves[0], [{ key: "m7", parent: null, from: "lane:money", to: "filter", tone: "sage", end: null }])
  && st.stats.events_in === 11 && st.stats.by_type["money.transaction.added"] === 4, moves[0]);
check("the filter passes it (counted, the average time moves)", moves[1][0].end === "pass" && st.stats.filter_pass === 3 && st.stats.avg_filter_us > 40);
check("it fires: from the filter to Fire, glowing, waiting for the filter's step",
  eq(moves[2], [{ key: "m7|w1|t1", parent: "m7", from: "filter", to: "fire", tone: "ember", end: "glow" }]) && st.stats.fires === 2, moves[2]);
check("its action lands in Tell me, after the fire, in sage when done",
  moves[3][0].from === "fire" && moves[3][0].to === "sink:notify" && moves[3][0].tone === "sage" && moves[3][0].parent === "m7|w1|t1" && st.stats.actions.notify === 2);
check("the feed in plain words", eq(C.feedWords(st.feed[0]), { head: "Money · +2,050 ₪ from a person", steps: ["matched “Transfer about 2k”", "told you"], tone: "ember", quiet: false }), C.feedWords(st.feed[0]));
check("a step seen again changes nothing", C.pipelineApply(st, evs[1]).length === 0 && st.stats.filter_pass === 3);

const sem = [
  { seq: 7, at: "2026-11-18T09:40:00", stage: "event", event_type: "phone.notification.received", event_id: "n9", summary: "WhatsApp · Gal: Friday 8pm?" },
  { seq: 8, at: "2026-11-18T09:40:00", stage: "filter", event_type: "phone.notification.received", event_id: "n9", result: "pass", candidates: 1, us: 20 },
  { seq: 9, at: "2026-11-18T09:40:01", stage: "semantic", event_type: "phone.notification.received", event_id: "n9", watch_id: "w3", watch_title: "Dates from Gal", trigger_id: "t1", result: "yes", cost_usd: 0.0002 },
  { seq: 10, at: "2026-11-18T09:40:01", stage: "fire", event_type: "phone.notification.received", event_id: "n9", watch_id: "w3", watch_title: "Dates from Gal", trigger_id: "t1" },
  { seq: 11, at: "2026-11-18T09:40:02", stage: "action", event_type: "phone.notification.received", event_id: "n9", watch_id: "w3", trigger_id: "t1", result: "held", action_type: "agent", summary: "Agent" },
];
const sm = sem.map((e) => C.pipelineApply(st, e));
check("an AI check: plum from the filter to the AI node, counted with its cost",
  eq(sm[2], [{ key: "n9|w3|t1", parent: "n9", from: "filter", to: "ai", tone: "plum", end: "pass" }]) && st.stats.semantic_checks === 2 && st.stats.semantic_yes === 1
  && Math.abs(st.stats.cost_usd - 0.0005) < 1e-9, sm[2]);
check("after a yes it fires from the AI node; the held agent lands amber in Agent",
  sm[3][0].from === "ai" && sm[3][0].parent === null && sm[4][0].to === "sink:agent" && sm[4][0].tone === "amber");
check("the feed: AI yes, matched, waiting for your OK",
  eq(C.feedWords(st.feed[0]).steps, ["AI check: yes for “Dates from Gal”", "matched “Dates from Gal”", "the agent is waiting for your OK"]) && C.feedWords(st.feed[0]).tone === "amber");
const no = C.pipelineApply(st, { seq: 12, at: "2026-11-18T09:41:00", stage: "semantic", event_type: "phone.notification.received", event_id: "n9b", watch_id: "w3", watch_title: "Dates from Gal", trigger_id: "t1", result: "no" });
check("an AI no drops the dot at the AI node", no[0].end === "drop" && C.feedWords(st.feed[0]).steps[0] === "AI check said no to “Dates from Gal”");

const tm = [
  { seq: 13, at: "2026-11-18T20:00:00", stage: "time", watch_id: "w2", watch_title: "Call Maya", trigger_id: "t2", summary: "Call Maya" },
  { seq: 14, at: "2026-11-18T20:00:00", stage: "fire", watch_id: "w2", watch_title: "Call Maya", trigger_id: "t2" },
  { seq: 15, at: "2026-11-18T20:00:00", stage: "action", watch_id: "w2", trigger_id: "t2", result: "done", action_type: "notify" },
];
const tmv = tm.map((e) => C.pipelineApply(st, e));
check("a time trigger: the clock pulses, the dot goes straight from the clock to Fire, then to Tell me",
  tmv[0][0].end === "pulse" && tmv[0][0].from === "lane:clock" && tmv[1][0].from === "lane:clock" && tmv[1][0].to === "fire"
  && tmv[2][0].to === "sink:notify" && tmv[2][0].parent === tmv[1][0].key && st.stats.clock === 1);
check("the time in words", eq(C.feedWords(st.feed[0]).steps, ["“Call Maya” is due", "told you"]), C.feedWords(st.feed[0]));
const dl = C.pipelineApply(st, { seq: 16, at: "2026-11-18T23:00:00", stage: "deadline", watch_id: "w1", watch_title: "Transfer about 2k", summary: "Transfer about 2k" });
check("a deadline: the clock, then Fire in rose", dl.length === 2 && dl[1].to === "fire" && dl[1].tone === "rose" && C.feedWords(st.feed[0]).steps[0] === "deadline passed, nothing matched");
const evd = C.pipelineApply(st, { seq: 17, at: "2026-11-18T21:00:00", stage: "evidence", event_type: "phone.notification.received", event_id: "n20", watch_id: "w2", watch_title: "Call Maya", result: "yes" });
check("evidence: to the AI node, then to Task when it looks done",
  evd.length === 2 && evd[0].to === "ai" && evd[1].to === "sink:task" && C.feedWords(st.feed[0]).steps[0] === "looks done: “Call Maya” → offered to close it");
check("a new kind of event adds its lane", C.pipelineApply(st, { seq: 18, at: "2026-11-18T21:05:00", stage: "event", event_type: "phone.call.ended", event_id: "c1", summary: "Call · Maya" })[0].from === "lane:calls"
  && C.lanes(st).map((l) => l.key).join() === "notifications,money,calls,car,clock");
check("an action without its type: read from the summary", C.actionTypeOf({ summary: "Agent run started" }) === "agent" && C.actionTypeOf({ summary: "Told you" }) === "notify" && C.sinkOf("suggest_complete") === "task");

const cnt = C.counters(st);
check("counters: by lane, the filter's in / passed / time, the AI's checks and cost, fires, actions by node",
  cnt.lanes.money === 4 && cnt.lanes.notifications === 8 && cnt.lanes.clock === 2 && cnt.filter.in === 13 && cnt.filter.passed === 4
  && cnt.ai.checks === 3 && cnt.fire === 4 && cnt.sinks.notify === 3 && cnt.sinks.agent === 1 && cnt.sinks.tool === 0, cnt);
check("µs and cost in words", C.usWords(38.4) === "38 µs" && C.usWords(4.25) === "4.3 µs" && C.usWords(1500) === "1.5 ms"
  && C.costWords(0) === "$0" && C.costWords(0.00042) === "$0.0004" && C.costWords(1.2) === "$1.20");

const big = C.pipelineInit({}, null);
for (let i = 1; i <= 60; i++) C.pipelineApply(big, { seq: i, stage: "event", event_type: "core.user.woke", event_id: `x${i}`, summary: "Woke up" });
check("the feed keeps the latest 40, newest first", big.feed.length === 40 && big.feed[0].key === "x60" && !big.chains.x1);

// ---------- geometry ----------

const ls = C.lanes(st);
const g = C.layout(ls, 1000, 420);
const N = g.nodes;
check("layout: sources left, then filter, AI (above the line), fire, actions right; lanes top to bottom with the clock last",
  N["lane:notifications"].x < N.filter.x && N.filter.x < N.ai.x && N.ai.x < N.fire.x && N.fire.x < N["sink:notify"].x
  && N.ai.y < N.filter.y && N.filter.y === N.fire.y && N["lane:notifications"].y < N["lane:clock"].y && N["lane:clock"].y === 420 - 34
  && !g.compact, N);
check("layout: an edge per lane (the clock straight to Fire), the AI detour, one per action",
  g.edges.some((e) => e.id === "clock-fire") && g.edges.some((e) => e.id === "money-filter") && g.edges.filter((e) => e.from === "fire").length === 4
  && g.edges.some((e) => e.id === "filter-ai") && g.edges.some((e) => e.id === "ai-fire"));
check("layout: compact on a phone, everything inside the stage", (() => {
  const c = C.layout(ls, 360, 400);
  return c.compact && Object.values(c.nodes).every((p) => p.x > 0 && p.x < 360 && p.y > 0 && p.y < 400);
})());
const cv = C.curve({ x: 0, y: 0 }, { x: 100, y: 50 });
check("curves: they start and end on the nodes, the middle is between; ease is 0 → 1",
  eq(C.pointAt(cv, 0), { x: 0, y: 0 }) && eq(C.pointAt(cv, 1), { x: 100, y: 50 }) && Math.abs(C.pointAt(cv, 0.5).x - 50) < 1e-9 && Math.abs(C.pointAt(cv, 0.5).y - 25) < 1e-9
  && C.ease(0) === 0 && C.ease(1) === 1 && C.ease(0.5) === 0.5 && C.pathD(cv) === "M0,0 C50,0 50,50 100,50");
check("the edge a move travels", C.edgeOf("lane:money", "filter") === "money-filter" && C.edgeOf("fire", "sink:agent") === "fire-agent" && C.edgeOf("filter", "filter") === null);

// ---------- recent checks (the trace) ----------

check("trace query: the limit, and the filter unless it is all",
  C.traceQuery("all") === "?limit=100" && C.traceQuery("near", 50) === "?limit=50&outcome=near"
  && C.traceQuery("fired", 9999) === "?limit=500&outcome=fired" && C.traceQuery("bogus") === "?limit=100");
const drop = { id: 3, at: "2026-11-18T09:20:00+02:00", trigger_id: "self", event_type: "phone.email.received", event_id: "m1",
  summary: "Email received · me", outcome: "drop", reason: 'from contains_any "tester@example.com" ← "me"', near: true,
  data: { from: "me", subject: "list", amount: null, text: "" } };
const v = C.traceWords(drop);
check("trace row in words: what, outcome, hue, the reason with the value seen",
  v.what === "Email received · me" && v.outcome === "No match" && v.hue === "mute" && v.reason.endsWith('← "me"') && v.near && v.trigger === "self", v);
check("trace outcomes: AI no / fired / unknown",
  C.traceWords({ outcome: "semantic_no", reason: "a newsletter" }).outcome === "AI said no"
  && C.traceWords({ outcome: "fired" }).hue === "ember" && C.traceWords({ outcome: "new_kind" }).outcome === "new kind");
check("trace row without a summary falls back to the event in a line",
  C.traceWords({ outcome: "drop", event_type: "money.transaction.added", data: { counterparty: "Maya Cohen", amount: 2000 } }).what !== "");
check("trace data lines: empty values out, long ones cut",
  eq(C.traceDataLines(drop.data), [["from", "me"], ["subject", "list"]])
  && C.traceDataLines({ text: "x".repeat(400) })[0][1].length === 300 && eq(C.traceDataLines({ n: 2, ok: false }), [["n", "2"], ["ok", "false"]]));
check("the page shows Recent checks with its filter", page.includes("Recent checks") && page.includes('data-act="trace-filter"'));

console.log(`\n${failures.length ? `${failures.length} check(s) failed` : "All checks passed"} (${pass} passed)`);
process.exit(failures.length ? 1 : 0);
