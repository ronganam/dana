/* The Tasks page's pure part (globalThis.DanaTasksCore): what the page shows from GET /api/tasks,
 * /api/task-groups and /api/zones, and the triggers it sends. No DOM; tested by
 * addons/tasks/tests/test_web_tasks.mjs.
 */
(() => {
  "use strict";
  const DAY = 86400000;
  const TRIGGERS = ["time", "location", "car", "wake"];

  /** A task's triggers as an object (the API gives a JSON string; {} when there are none or it is broken). */
  function triggersOf(task) {
    const raw = task && task.triggers;
    if (raw && typeof raw === "object") return raw;
    try { const t = JSON.parse(raw || "{}"); return t && typeof t === "object" ? t : {}; } catch (_) { return {}; }
  }

  /** The id of a task's group ("general" when it has none). */
  const groupOf = (task) => (task && (task.groupId || task.group_id)) || "general";
  const isDone = (task) => task.status === "completed";
  const matches = (task, q) => !q || (task.title || "").toLowerCase().includes(q) || (task.description || "").toLowerCase().includes(q);

  /** Counts of the open tasks: all of them and by trigger ({all, time, location, car, wake}). */
  function triggerCounts(tasks) {
    const open = (tasks || []).filter((t) => !isDone(t));
    const out = { all: open.length };
    for (const kind of TRIGGERS) out[kind] = open.filter((t) => Boolean(triggersOf(t)[kind])).length;
    return out;
  }

  /** Open tasks per group id ({general: n, <id>: n}). */
  function groupCounts(tasks) {
    const out = {};
    for (const t of tasks || []) if (!isDone(t)) out[groupOf(t)] = (out[groupOf(t)] || 0) + 1;
    return out;
  }

  /** The tasks the filters let through: {open, done}. `group`: a group id or null (all); `trigger`: a
   *  trigger kind or null; `q`: search text in the title or description. Done tasks follow the group and
   *  the search, not the trigger. */
  function filterTasks(tasks, { group = null, trigger = null, q = "" } = {}) {
    const query = String(q || "").toLowerCase().trim();
    const inGroup = (t) => group === null || groupOf(t) === group;
    const list = tasks || [];
    return {
      open: list.filter((t) => !isDone(t) && inGroup(t) && (!trigger || Boolean(triggersOf(t)[trigger])) && matches(t, query)),
      done: list.filter((t) => isDone(t) && inGroup(t) && matches(t, query)),
    };
  }

  /** When a task is due, relative to `now`: overdue / today / tomorrow / thisweek / later / nodate. */
  function dueBucket(task, now) {
    const raw = task.dueDate || task.due_date;
    if (!raw) return "nodate";
    const d = new Date(raw);
    if (isNaN(d.getTime())) return "nodate";
    d.setHours(0, 0, 0, 0);
    const today = new Date(now);
    today.setHours(0, 0, 0, 0);
    const days = Math.round((d.getTime() - today.getTime()) / DAY);
    if (days < 0) return "overdue";
    if (days === 0) return "today";
    if (days === 1) return "tomorrow";
    return days < 7 ? "thisweek" : "later";
  }

  const PRIORITY = [["high", "🔴 High Priority"], ["medium", "🟡 Medium Priority"], ["low", "🟢 Low Priority"]];
  const DUE = [["overdue", "🔴 Overdue"], ["today", "📅 Today"], ["tomorrow", "📅 Tomorrow"], ["thisweek", "📅 This Week"],
               ["later", "📅 Later"], ["nodate", "📋 No Due Date"]];

  /** The list's sections for a sort mode ("group" / "priority" / "due"), in order, empty ones left out:
   *  [{key, title, tasks}]. Titles are plain text (escape them to show). */
  function sections(open, mode, groups, now = new Date()) {
    let order;
    let keyOf;
    if (mode === "priority") {
      order = PRIORITY;
      keyOf = (t) => (t.priority || "medium").toLowerCase();
    } else if (mode === "due") {
      order = DUE;
      keyOf = (t) => dueBucket(t, now);
    } else {
      order = [["general", "📁 General"], ...(groups || []).filter((g) => g.id !== "general").map((g) => [g.id, `${g.emoji || "📁"} ${g.name}`])];
      keyOf = groupOf;
    }
    const byKey = new Map(order.map(([key, title]) => [key, { key, title, tasks: [] }]));
    for (const t of open || []) {
      const key = keyOf(t);
      if (!byKey.has(key)) byKey.set(key, { key, title: key, tasks: [] });
      byKey.get(key).tasks.push(t);
    }
    return [...byKey.values()].filter((s) => s.tasks.length);
  }

  /** An ISO time as an <input type="datetime-local"> value in local time ("" for none or a bad one). */
  function localDatetime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  /** The zone names a location trigger offers (Home and Work while Home Assistant gives none). */
  function zoneNames(zones) {
    const list = zones && zones.length ? zones : [{ name: "Home" }, { name: "Work" }];
    return list.map((z) => (z && typeof z === "object" && z.name != null ? String(z.name) : String(z)));
  }

  /** The `triggers` a save sends from the form's choices: {time?, location?: {location, event}, car?, wake?}. */
  function triggersFrom({ time = "", zone = null, event = "arrive", car = null, wake = false } = {}) {
    const out = {};
    if (time) out.time = time;
    if (zone) out.location = { location: zone, event: event || "arrive" };
    if (car) out.car = car;
    if (wake) out.wake = true;
    return out;
  }

  globalThis.DanaTasksCore = { triggersOf, groupOf, triggerCounts, groupCounts, filterTasks, dueBucket, sections,
                               localDatetime, zoneNames, triggersFrom };
})();
