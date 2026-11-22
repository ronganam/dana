/**
 * The Tasks page's pure part (addons/tasks/web/tasks-core.js, DanaTasksCore): triggers read from the
 * API's JSON string, the filter counts, the group / trigger / search filters, the sections of each sort
 * mode, due-date buckets, the datetime-local value, zone names and the triggers a save sends.
 *
 * Run: bin/test test_web_tasks
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(here, "..", "..", "..");
const sandbox = { console, JSON, Object, Array, String, Number, Boolean, Math, Date, Map, isNaN };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(SERVER, "addons/tasks/web/tasks-core.js"), "utf8"), sandbox);
const C = sandbox.DanaTasksCore;

let pass = 0;
const failures = [];
function check(name, cond, detail) {
  if (cond) { pass++; console.log(`[PASS] ${name}`); }
  else { failures.push(name); console.log(`[FAIL] ${name}${detail !== undefined ? " — " + JSON.stringify(detail) : ""}`); }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const NOW = new Date(2026, 10, 18, 9, 30);   // a Wednesday, local time
const day = (n) => { const d = new Date(NOW); d.setDate(d.getDate() + n); return d.toISOString(); };
const groups = [{ id: "general", name: "General" }, { id: "g-home", name: "Home", emoji: "🏠" }, { id: "g-work", name: "Work" }];
const tasks = [
  { id: "t1", title: "Buy milk", status: "needs_action", priority: "high", group_id: "g-home", triggers: '{"time": "2026-11-18T18:00"}', due_date: day(0) },
  { id: "t2", title: "Call the plumber", description: "about the sink", status: "needs_action", priority: "low", triggers: '{"location": {"location": "Home", "event": "arrive"}}', due_date: day(-2) },
  { id: "t3", title: "Send the report", status: "needs_action", groupId: "g-work", triggers: '{"car": "enter", "wake": true}', due_date: day(1) },
  { id: "t4", title: "Plan the trip", status: "needs_action", priority: "medium", group_id: "g-home", triggers: "not json", due_date: day(30) },
  { id: "t5", title: "Old chore", status: "completed", group_id: "g-home", triggers: '{"time": "2026-11-01T08:00"}' },
  { id: "t6", title: "Water the plants", status: "needs_action", group_id: "gone-group", triggers: null },
];

check("triggers: from the API's JSON string, an object as is, {} for broken or none",
  eq(C.triggersOf(tasks[0]), { time: "2026-11-18T18:00" }) && eq(C.triggersOf({ triggers: { car: "leave" } }), { car: "leave" })
  && eq(C.triggersOf(tasks[3]), {}) && eq(C.triggersOf(tasks[5]), {}));
check("group: group_id or groupId, else general", C.groupOf(tasks[0]) === "g-home" && C.groupOf(tasks[2]) === "g-work" && C.groupOf(tasks[1]) === "general");

check("counts: open tasks only, by trigger", eq(C.triggerCounts(tasks), { all: 5, time: 1, location: 1, car: 1, wake: 1 }), C.triggerCounts(tasks));
check("counts: open tasks per group", eq(C.groupCounts(tasks), { "g-home": 2, general: 1, "g-work": 1, "gone-group": 1 }), C.groupCounts(tasks));

const all = C.filterTasks(tasks);
check("no filter: every open task, the done ones apart", eq(all.open.map((t) => t.id), ["t1", "t2", "t3", "t4", "t6"]) && eq(all.done.map((t) => t.id), ["t5"]));
const home = C.filterTasks(tasks, { group: "g-home" });
check("a group: its open and done tasks", eq(home.open.map((t) => t.id), ["t1", "t4"]) && eq(home.done.map((t) => t.id), ["t5"]));
check("general: the tasks without a group", eq(C.filterTasks(tasks, { group: "general" }).open.map((t) => t.id), ["t2"]));
const timed = C.filterTasks(tasks, { trigger: "time" });
check("a trigger filters open tasks; done ones follow only the group and the search", eq(timed.open.map((t) => t.id), ["t1"]) && eq(timed.done.map((t) => t.id), ["t5"]));
check("search: title or description, any case", eq(C.filterTasks(tasks, { q: "  SINK " }).open.map((t) => t.id), ["t2"])
  && eq(C.filterTasks(tasks, { q: "trip" }).open.map((t) => t.id), ["t4"]));

const open = all.open;
const byGroup = C.sections(open, "group", groups, NOW);
check("sort by group: General first, then the groups in order, then a group no longer listed; empty ones left out",
  eq(byGroup.map((s) => [s.key, s.title, s.tasks.map((t) => t.id)]),
    [["general", "📁 General", ["t2"]], ["g-home", "🏠 Home", ["t1", "t4"]], ["g-work", "📁 Work", ["t3"]], ["gone-group", "gone-group", ["t6"]]]), byGroup);
const byPrio = C.sections(open, "priority", groups, NOW);
check("sort by priority: high, medium (the default), low",
  eq(byPrio.map((s) => [s.key, s.tasks.map((t) => t.id)]), [["high", ["t1"]], ["medium", ["t3", "t4", "t6"]], ["low", ["t2"]]]), byPrio);
const byDue = C.sections(open, "due", groups, NOW);
check("sort by due date: overdue, today, tomorrow, later, no date",
  eq(byDue.map((s) => [s.key, s.tasks.map((t) => t.id)]), [["overdue", ["t2"]], ["today", ["t1"]], ["tomorrow", ["t3"]], ["later", ["t4"]], ["nodate", ["t6"]]]), byDue);
check("due buckets: this week, a bad date", C.dueBucket({ due_date: day(4) }, NOW) === "thisweek" && C.dueBucket({ due_date: "soon" }, NOW) === "nodate"
  && C.dueBucket({ dueDate: day(6) }, NOW) === "thisweek" && C.dueBucket({ due_date: day(7) }, NOW) === "later");

const local = new Date(2026, 0, 5, 7, 4).toISOString();
check("datetime-local: local time with leading zeros; empty for none or a bad one",
  C.localDatetime(local) === "2026-01-05T07:04" && C.localDatetime("") === "" && C.localDatetime("nope") === "");
check("zones: the user's names, plain strings too; none without any",
  eq(C.zoneNames([{ name: "Gym" }, "Office"]), ["Gym", "Office"]) && eq(C.zoneNames([]), []) && eq(C.zoneNames(null), []));
const plain = (s) => String(s);
check("zone picker: the stored zone selected in any case; a disabled hint without zones",
  C.zoneOptions([{ name: "Home" }, { name: "Gym" }], "gym", plain)
    === `<option value="Home">Home</option><option value="Gym" selected>Gym</option>`
  && C.zoneOptions([], "", plain).includes("disabled") && C.zoneOptions([], "", plain).includes("Settings → Zones"));
check("triggers sent: only the ones chosen, a location with its event",
  eq(C.triggersFrom({ time: "2026-11-20T08:00", zone: "Home", event: "leave", car: "enter", wake: true }),
    { time: "2026-11-20T08:00", location: { location: "Home", event: "leave" }, car: "enter", wake: true })
  && eq(C.triggersFrom({ zone: "Work", event: "" }), { location: { location: "Work", event: "arrive" } }) && eq(C.triggersFrom({}), {}));

console.log(`\n${failures.length ? `${failures.length} check(s) failed` : "All checks passed"} (${pass} passed)`);
process.exit(failures.length ? 1 : 0);
