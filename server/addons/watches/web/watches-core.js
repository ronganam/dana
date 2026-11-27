/* The Watches page's pure part (globalThis.DanaWatchesCore): a watch spec in words (triggers, conditions,
 * actions), the list's groups and search, the editor's helpers (every edit returns a new spec), validation and
 * the server's errors placed on the field they name, the dry run in words, and the live pipeline: the counters,
 * the feed in plain words, where each step's dot travels, and the diagram's geometry. No DOM; tested by
 * addons/watches/tests/test_web_watches.mjs.
 *
 * Payloads: GET /api/watches (Watch), GET /api/watches/{id} (fires), POST /api/watches/dry-run,
 * GET /api/watches/catalog, GET /api/watches/pipeline and its stream (PipelineEvent), GET /api/watches/{id}/trace
 * (the recent checks: the filter in its query, each row in words, its event data as lines).
 */
(() => {
  "use strict";

  // ---------- words ----------

  const OPS = ["eq", "ne", "in", "contains", "contains_any", "not_contains", "gt", "gte", "lt", "lte", "between", "approx", "exists"];
  const OP_WORDS = {
    eq: "is", ne: "is not", in: "is one of", contains: "contains", contains_any: "contains any of",
    not_contains: "doesn't contain", gt: "is more than", gte: "is at least", lt: "is less than", lte: "is at most",
    between: "is between", approx: "is about", exists: "is present",
  };
  const NUMERIC_OPS = new Set(["gt", "gte", "lt", "lte", "between", "approx"]);
  const LIST_OPS = new Set(["in", "contains_any"]);
  const EVERY = ["daily", "weekdays", "weekly", "monthly"];
  const EVERY_WORDS = { "": "Once", daily: "Every day", weekdays: "Weekdays", weekly: "Every week", monthly: "Every month" };
  const ACTION_TYPES = ["notify", "tool", "agent", "create_task", "complete_task", "suggest_complete"];
  const ACTION_WORDS = {
    notify: "Tell me", tool: "Run a tool", agent: "Agent", create_task: "Add a task",
    complete_task: "Complete the task", suggest_complete: "Suggest closing",
  };
  const URGENCY_WORDS = { now: "Now", glance: "At a glance", later: "Later" };
  const STATUS_WORDS = { active: "Active", paused: "Paused", done: "Done", expired: "Expired" };
  const FIRE_STATUS = {
    done: { label: "Done", hue: "sage" }, held: { label: "Waiting for your OK", hue: "amber" },
    failed: { label: "Failed", hue: "rose" }, skipped: { label: "Skipped", hue: "mute" },
  };

  /** Where an event type comes from: its lane in the diagram, a short label, a lucide icon and a hue. */
  const LANES = [
    { key: "notifications", test: (t) => t === "phone.notification.received" || /notification/.test(t), label: "Notifications", icon: "bell", hue: "teal" },
    { key: "email", test: (t) => /email/.test(t), label: "Email", icon: "mail", hue: "sky" },
    { key: "money", test: (t) => /^money\./.test(t), label: "Money", icon: "wallet", hue: "sage" },
    { key: "calls", test: (t) => /call/.test(t), label: "Calls", icon: "phone", hue: "sage" },
    { key: "car", test: (t) => /\.car$|signal\.car/.test(t), label: "Car", icon: "car", hue: "amber" },
    { key: "places", test: (t) => /location|place|zone/.test(t), label: "Places", icon: "map-pin", hue: "sky" },
    { key: "wake", test: (t) => /woke|wake/.test(t), label: "Waking", icon: "sunrise", hue: "amber" },
    { key: "tasks", test: (t) => /^tasks\./.test(t), label: "Tasks", icon: "list-checks", hue: "amber" },
  ];
  const CLOCK = { key: "clock", label: "Clock", icon: "clock", hue: "amber" };
  const EVENT_WORDS = {
    "phone.notification.received": "Notification", "phone.email.received": "Email", "phone.call.ended": "Call ended",
    "money.transaction.added": "Money", "core.signal.car": "Car", "core.signal.location": "Place",
    "core.user.woke": "Woke up", "tasks.task.created": "Task added", "tasks.task.completed": "Task done",
  };

  const words = (s) => String(s == null ? "" : s).replace(/[_.]+/g, " ").trim();
  const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

  /** The lane an event type flows in: {key, label, icon, hue}; null type → the clock. */
  function laneOf(type) {
    if (!type) return CLOCK;
    const t = String(type);
    const known = LANES.find((l) => l.test(t));
    if (known) return { key: known.key, label: known.label, icon: known.icon, hue: known.hue };
    const parts = t.split(".");
    return { key: t, label: cap(words(parts.length > 2 ? parts[parts.length - 2] : parts[parts.length - 1])), icon: "radio", hue: "sky" };
  }

  /** An event type's name for people ("Money", "Notification"); unknown ones from the name itself. */
  function eventLabel(type) {
    if (!type) return "Time";
    if (EVENT_WORDS[type]) return EVENT_WORDS[type];
    const parts = String(type).split(".");
    return cap(words(parts.slice(-2).join(" ")));
  }

  const fieldWords = (f) => words(f) || "field";

  function num(v) {
    if (v === null || v === undefined || v === "") return null;
    const n = parseFloat(String(v).replace(/,/g, ""));
    return Number.isFinite(n) ? n : null;
  }
  function fmtNum(v) {
    const n = num(v);
    if (n === null) return String(v == null ? "" : v);
    return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }
  const quote = (s) => `“${s}”`;

  /** One field condition in words: "amount is about 22,000 (±10%)", "sender is one of “Gil” / “Maya”". */
  function conditionWords(c) {
    if (!c) return "";
    const f = fieldWords(c.field);
    const op = c.op || "eq";
    const v = c.value == null ? "" : String(c.value);
    if (op === "exists") return `${f} ${OP_WORDS.exists}`;
    if (!v.trim()) return `${f} ${OP_WORDS[op] || op} …`;
    if (op === "between") return `${f} is between ${fmtNum(v)} and ${c.value2 == null || c.value2 === "" ? "…" : fmtNum(c.value2)}`;
    if (op === "approx") return `${f} is about ${fmtNum(v)} (±${c.tolerance_pct || 10}%)`;
    if (NUMERIC_OPS.has(op)) return `${f} ${OP_WORDS[op]} ${fmtNum(v)}`;
    if (LIST_OPS.has(op)) return `${f} ${OP_WORDS[op]} ${v.split("|").map((x) => x.trim()).filter(Boolean).map(quote).join(" / ")}`;
    return `${f} ${OP_WORDS[op] || op} ${quote(v)}`;
  }

  // ---------- dates ----------

  const pad = (n) => String(n).padStart(2, "0");
  /** A Date from "YYYY-MM-DD HH:MM" (local), "YYYY-MM-DDTHH:MM" or an ISO string; null when it is not one. */
  function parseWhen(s) {
    if (!s) return null;
    if (s instanceof Date) return Number.isNaN(s.getTime()) ? null : s;
    const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$/.exec(String(s).trim());
    if (m) return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
    const d = new Date(s);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const localStr = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const hm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const LONG_DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  /** "today 20:30", "tomorrow 08:00", "yesterday 09:10", "Tue 24 Nov 20:30"; "" for nothing. */
  function whenWords(s, now = new Date()) {
    const d = parseWhen(s);
    if (!d) return s ? String(s) : "";
    const tm = new Date(now); tm.setDate(tm.getDate() + 1);
    const ys = new Date(now); ys.setDate(ys.getDate() - 1);
    if (sameDay(d, now)) return `today ${hm(d)}`;
    if (sameDay(d, tm)) return `tomorrow ${hm(d)}`;
    if (sameDay(d, ys)) return `yesterday ${hm(d)}`;
    return `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]} ${hm(d)}`;
  }

  /** "just now", "5 min ago", "3 h ago", "yesterday", "4 days ago", else the date. */
  function agoWords(s, now = new Date()) {
    const d = parseWhen(s);
    if (!d) return "";
    const min = Math.round((now - d) / 60000);
    if (min < 1) return "just now";
    if (min < 60) return `${min} min ago`;
    if (min < 24 * 60 && sameDay(d, now)) return `${Math.round(min / 60)} h ago`;
    const ys = new Date(now); ys.setDate(ys.getDate() - 1);
    if (sameDay(d, ys)) return "yesterday";
    const days = Math.round(min / 1440);
    if (days < 7) return `${days} days ago`;
    return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  }

  /** The spec's "YYYY-MM-DD HH:MM" as an <input type="datetime-local"> value and back. */
  const toInput = (s) => { const d = parseWhen(s); return d ? localStr(d).replace(" ", "T") : ""; };
  const fromInput = (v) => (v ? String(v).replace("T", " ").slice(0, 16) : "");

  /** "HH:MM-HH:MM" → ["HH:MM", "HH:MM"]; ["", ""] when unset. */
  function splitDuring(s) {
    const m = /^\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$/.exec(s || "");
    return m ? [m[1].padStart(5, "0"), m[2].padStart(5, "0")] : ["", ""];
  }
  const joinDuring = (a, b) => (a && b ? `${a}-${b}` : "");

  // ---------- a spec in words ----------

  const triggersOf = (spec) => (spec && Array.isArray(spec.triggers) ? spec.triggers : []);
  const actionsOf = (spec, list = "on_match") => (spec && Array.isArray(spec[list]) ? spec[list] : []);

  function everyWords(every, d) {
    if (!every) return "";
    if (every === "daily") return "every day";
    if (every === "weekdays") return "on weekdays";
    if (every === "weekly") return d ? `every ${LONG_DAYS[d.getDay()]}` : "every week";
    if (every === "monthly") return d ? `monthly on the ${d.getDate()}${ordinal(d.getDate())}` : "every month";
    return String(every);
  }
  function ordinal(n) {
    if (n % 100 >= 11 && n % 100 <= 13) return "th";
    return { 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th";
  }

  /** A trigger in words. Time: "today 20:30", "every day at 20:30", "30 min before 20:30". Event: "Money:
   *  amount is about 22,000 (±10%), counterparty kind is “person” · mentions “rent” · 08:00–20:00". */
  function triggerWords(t, spec, now = new Date()) {
    if (!t) return "";
    if (t.kind === "time") {
      if (t.relative_to) {
        const m = Number(t.offset_minutes) || 0;
        const base = triggersOf(spec).find((x) => x.id === t.relative_to);
        const baseWords = base && base.at ? (base.every ? hm(parseWhen(base.at) || new Date()) : whenWords(base.at, now)) : t.relative_to;
        if (!m) return `at ${baseWords}`;
        const amount = Math.abs(m) >= 60 && Math.abs(m) % 60 === 0 ? `${Math.abs(m) / 60} h` : `${Math.abs(m)} min`;
        return `${amount} ${m < 0 ? "before" : "after"} ${baseWords}`;
      }
      const d = parseWhen(t.at);
      if (t.every) return `${everyWords(t.every, d)}${d ? ` at ${hm(d)}` : ""}`;
      return whenWords(t.at, now) || "a time";
    }
    const parts = (t.where || []).map(conditionWords).filter(Boolean);
    const kw = (t.keywords || []).filter((k) => String(k).trim());
    let out = eventLabel(t.event_type);
    if (parts.length) out += `: ${parts.join(", ")}`;
    if (kw.length) out += ` · mentions ${kw.map(quote).join(" or ")}`;
    const [a, b] = splitDuring(t.during);
    if (a) out += ` · ${a}–${b}`;
    return out;
  }

  /** An action in words: "Tell me “Rent arrived”", "Run ha.call_service", "Agent: … (asks you first)". */
  function actionWords(a) {
    if (!a) return "";
    const asks = (a.autonomy || "ask_first") === "ask_first";
    switch (a.type) {
      case "notify": return a.text ? `Tell me ${quote(a.text)}` : "Tell me";
      case "tool": return `Run ${a.tool || "a tool"}${asks ? " (asks you first)" : ""}`;
      case "agent": return `Agent${a.instructions ? `: ${a.instructions}` : ""}${asks ? " (asks you first)" : ""}`;
      case "create_task": return a.text ? `Add the task ${quote(a.text)}` : "Add a task";
      case "complete_task": return "Complete the task";
      case "suggest_complete": return "Suggest closing the task";
      default: return ACTION_WORDS[a.type] || words(a.type);
    }
  }

  /** The list row's chips: triggers [{kind: "time"|"event", label, title, icon, hue, ai}] then actions. */
  function triggerChips(spec, now = new Date()) {
    return triggersOf(spec).map((t) => {
      if (t.kind === "time") return { kind: "time", label: triggerWords(t, spec, now), title: triggerWords(t, spec, now), icon: "clock", hue: "amber", ai: false };
      const lane = laneOf(t.event_type);
      return { kind: "event", label: eventLabel(t.event_type), title: triggerWords(t, spec, now), icon: lane.icon, hue: lane.hue, ai: Boolean(t.semantic && String(t.semantic).trim()) };
    });
  }
  function actionChips(spec) {
    return actionsOf(spec).map((a) => ({
      type: a.type, label: a.type === "tool" && a.tool ? `Run ${a.tool}` : (ACTION_WORDS[a.type] || words(a.type)),
      title: actionWords(a), ai: a.type === "agent",
    }));
  }

  // ---------- the list ----------

  const isActive = (w) => (w.status || "active") === "active";
  const kindOf = (w) => (w.kind || (w.spec && w.spec.kind) || "watch");

  /** Whether a watch matches the search: its title, the user's words, or anything in its spec (any case). */
  function matchesQuery(w, q) {
    const s = String(q || "").trim().toLowerCase();
    if (!s) return true;
    return [w.title, w.request, JSON.stringify(w.spec || {})].some((x) => String(x || "").toLowerCase().includes(s));
  }

  /** The list's groups, empty ones left out: Active reminders (next first), Active watches (newest first),
   *  Paused, Done and expired (latest fire first). [{key, title, items}] */
  function groupWatches(watches, q = "") {
    const xs = (watches || []).filter((w) => matchesQuery(w, q));
    const t = (s) => { const d = parseWhen(s); return d ? d.getTime() : 0; };
    const out = [
      { key: "reminders", title: "Active reminders", items: xs.filter((w) => isActive(w) && kindOf(w) === "reminder").sort((a, b) => (t(a.next_time) || Infinity) - (t(b.next_time) || Infinity)) },
      { key: "watches", title: "Active watches", items: xs.filter((w) => isActive(w) && kindOf(w) !== "reminder").sort((a, b) => t(b.created_at) - t(a.created_at)) },
      { key: "paused", title: "Paused", items: xs.filter((w) => w.status === "paused").sort((a, b) => t(b.created_at) - t(a.created_at)) },
      { key: "done", title: "Done and expired", items: xs.filter((w) => w.status === "done" || w.status === "expired").sort((a, b) => (t(b.last_fired_at) || t(b.created_at)) - (t(a.last_fired_at) || t(a.created_at))) },
    ];
    return out.filter((g) => g.items.length);
  }

  /** The row's quiet end line: "Fired 2 h ago", "Next tomorrow 08:00", "Paused", "Expired". */
  function rowStatus(w, now = new Date()) {
    if (w.status === "paused") return "Paused";
    if (w.status === "expired") return "Expired";
    if (w.status === "done") return w.last_fired_at ? `Done · ${agoWords(w.last_fired_at, now)}` : "Done";
    if (w.next_time) return `Next ${whenWords(w.next_time, now)}`;
    if (w.last_fired_at) return `Fired ${agoWords(w.last_fired_at, now)}`;
    return "Watching";
  }

  /** The row's dot hue: reminders amber, watches sky, anything with an agent plum, paused / done mute. */
  function rowHue(w) {
    if (!isActive(w)) return "mute";
    if (actionsOf(w.spec).some((a) => a.type === "agent")) return "plum";
    return kindOf(w) === "reminder" ? "amber" : "sky";
  }

  /** The page's kicker: "4 active · 1 paused". */
  function kicker(watches) {
    const xs = watches || [];
    const a = xs.filter(isActive).length, p = xs.filter((w) => w.status === "paused").length;
    return [a ? `${a} active` : "none active", p ? `${p} paused` : ""].filter(Boolean).join(" · ");
  }

  // ---------- editing (each returns a new spec) ----------

  const clone = (x) => JSON.parse(JSON.stringify(x == null ? {} : x));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  function nextTriggerId(spec) {
    const used = new Set(triggersOf(spec).map((t) => t.id));
    let i = triggersOf(spec).length + 1;
    while (used.has(`t${i}`)) i++;
    return `t${i}`;
  }
  const fieldsOf = (catalog, type) => {
    const et = ((catalog && catalog.event_types) || []).find((e) => e.name === type);
    return et && et.fields ? Object.keys(et.fields) : [];
  };
  function firstEventType(catalog) {
    const ets = (catalog && catalog.event_types) || [];
    return ets.length ? ets[0].name : "phone.notification.received";
  }

  /** Adds a trigger: "event" (the catalog's first type, no conditions), "time" (tomorrow 09:00) or "relative"
   *  (30 min before the first timed trigger). */
  function addTrigger(spec, kind, catalog, now = new Date()) {
    const s = clone(spec);
    s.triggers = triggersOf(s);
    const id = nextTriggerId(s);
    if (kind === "time") {
      const d = new Date(now); d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0);
      s.triggers.push({ id, kind: "time", at: localStr(d), every: null });
    } else if (kind === "relative") {
      const base = s.triggers.find((t) => t.kind === "time" && t.at);
      s.triggers.push({ id, kind: "time", relative_to: base ? base.id : "", offset_minutes: -30 });
    } else {
      s.triggers.push({ id, kind: "event", event_type: firstEventType(catalog), where: [], keywords: [] });
    }
    return s;
  }
  /** Removes trigger `i`; time triggers relative to it go too. */
  function removeTrigger(spec, i) {
    const s = clone(spec);
    const gone = triggersOf(s)[i];
    s.triggers = triggersOf(s).filter((t, j) => j !== i && !(gone && t.relative_to && t.relative_to === gone.id));
    return s;
  }
  /** Sets one of trigger `i`'s fields; "" clears an optional one. A new event type drops conditions on fields it
   *  does not have. */
  function setTrigger(spec, i, key, value, catalog) {
    const s = clone(spec);
    const t = triggersOf(s)[i];
    if (!t) return s;
    if (key === "offset_minutes") t.offset_minutes = parseInt(value, 10) || 0;
    else if (key === "every") t.every = value || null;
    else if (value === "" && key !== "at") delete t[key];
    else t[key] = value;
    if (key === "event_type") {
      const fields = fieldsOf(catalog, value);
      if (fields.length) t.where = (t.where || []).filter((c) => fields.includes(c.field));
    }
    return s;
  }
  function addCondition(spec, i, catalog) {
    const s = clone(spec);
    const t = triggersOf(s)[i];
    if (!t) return s;
    const fields = fieldsOf(catalog, t.event_type);
    (t.where = t.where || []).push({ field: fields[0] || "text", op: "contains", value: "" });
    return s;
  }
  function removeCondition(spec, i, j) {
    const s = clone(spec);
    const t = triggersOf(s)[i];
    if (t && t.where) t.where.splice(j, 1);
    return s;
  }
  /** Sets a condition's field / op / value / value2 / tolerance_pct; extra keys an op does not use are dropped. */
  function setCondition(spec, i, j, key, value) {
    const s = clone(spec);
    const c = triggersOf(s)[i] && (triggersOf(s)[i].where || [])[j];
    if (!c) return s;
    c[key] = key === "tolerance_pct" ? (parseFloat(value) || 10) : value;
    if (c.op !== "between") delete c.value2;
    if (c.op !== "approx") delete c.tolerance_pct;
    else if (!c.tolerance_pct) c.tolerance_pct = 10;
    if (c.op === "exists") c.value = "";
    return s;
  }
  /** Adds a keyword to trigger `i` (or, with i === "evidence", to the evidence keywords); blanks and repeats
   *  (any case) are ignored. */
  function addKeyword(spec, i, word) {
    const s = clone(spec);
    const w = String(word || "").trim();
    if (!w) return s;
    const holder = i === "evidence" ? s : triggersOf(s)[i];
    if (!holder) return s;
    const key = i === "evidence" ? "evidence_keywords" : "keywords";
    const list = holder[key] = Array.isArray(holder[key]) ? holder[key] : [];
    if (!list.some((k) => String(k).toLowerCase() === w.toLowerCase())) list.push(w);
    return s;
  }
  function removeKeyword(spec, i, j) {
    const s = clone(spec);
    const holder = i === "evidence" ? s : triggersOf(s)[i];
    const key = i === "evidence" ? "evidence_keywords" : "keywords";
    if (holder && Array.isArray(holder[key])) holder[key].splice(j, 1);
    return s;
  }
  /** Turns an evidence event type on or off. */
  function toggleEvidenceType(spec, type) {
    const s = clone(spec);
    const list = Array.isArray(s.evidence_event_types) ? s.evidence_event_types : [];
    s.evidence_event_types = list.includes(type) ? list.filter((x) => x !== type) : list.concat([type]);
    return s;
  }

  const ACTION_DEFAULTS = {
    notify: () => ({ type: "notify", text: "", urgency: "now" }),
    tool: () => ({ type: "tool", tool: "", args_json: "{}", autonomy: "ask_first" }),
    agent: () => ({ type: "agent", instructions: "", autonomy: "ask_first" }),
    create_task: () => ({ type: "create_task", text: "" }),
    complete_task: () => ({ type: "complete_task" }),
    suggest_complete: () => ({ type: "suggest_complete" }),
  };
  function addAction(spec, list = "on_match", type = "notify") {
    const s = clone(spec);
    s[list] = actionsOf(s, list);
    s[list].push((ACTION_DEFAULTS[type] || ACTION_DEFAULTS.notify)());
    return s;
  }
  function removeAction(spec, list, i) {
    const s = clone(spec);
    s[list] = actionsOf(s, list).filter((_, j) => j !== i);
    return s;
  }
  /** Sets one of an action's fields. A new type starts from that type's defaults, keeping the words typed
   *  (text ↔ instructions). */
  function setAction(spec, list, i, key, value) {
    const s = clone(spec);
    const a = actionsOf(s, list)[i];
    if (!a) return s;
    if (key === "type") {
      const keep = a.text || a.instructions || "";
      const fresh = (ACTION_DEFAULTS[value] || ACTION_DEFAULTS.notify)();
      if ("text" in fresh) fresh.text = keep;
      if ("instructions" in fresh) fresh.instructions = keep;
      s[list][i] = fresh;
      return s;
    }
    a[key] = value;
    return s;
  }
  /** Sets a top-level field ("" clears window and note fields to empty strings, cooldown becomes a number). */
  function setSpec(spec, key, value) {
    const s = clone(spec);
    if (key === "cooldown_minutes") s.cooldown_minutes = Math.max(0, parseInt(value, 10) || 0);
    else s[key] = value;
    return s;
  }

  // ---------- validation ----------

  /** What is wrong with a spec before it is sent: [{path, message}] (path like "triggers.0.where.1.value",
   *  "on_match.0.text", "title"). The server checks again; this catches the obvious. */
  function validate(spec, catalog) {
    const errs = [];
    const add = (path, message) => errs.push({ path, message });
    const s = spec || {};
    if (!String(s.title || "").trim()) add("title", "Give it a title");
    const trig = triggersOf(s);
    if (!trig.length) add("triggers", "Add at least one trigger");
    const known = new Set(((catalog && catalog.event_types) || []).map((e) => e.name));
    const ids = new Map(trig.map((t) => [t.id, t]));
    trig.forEach((t, i) => {
      const p = `triggers.${i}`;
      if (t.kind === "time") {
        if (t.relative_to) {
          const base = ids.get(t.relative_to);
          if (!base || base.kind !== "time" || !base.at) add(`${p}.relative_to`, "Pick the time this one is relative to");
        } else if (!parseWhen(t.at)) add(`${p}.at`, "Pick a date and time");
        return;
      }
      if (!t.event_type) add(`${p}.event_type`, "Pick what to watch");
      else if (known.size && !known.has(t.event_type)) add(`${p}.event_type`, `${eventLabel(t.event_type)} isn't available to you`);
      (t.where || []).forEach((c, j) => {
        const cp = `${p}.where.${j}`;
        if (!c.field) add(`${cp}.field`, "Pick a field");
        if (!OPS.includes(c.op)) add(`${cp}.op`, "Pick how to compare");
        if (c.op !== "exists" && !String(c.value == null ? "" : c.value).trim()) add(`${cp}.value`, "Fill in a value");
        else if (NUMERIC_OPS.has(c.op) && num(c.value) === null) add(`${cp}.value`, "Needs a number");
        if (c.op === "between" && num(c.value2) === null) add(`${cp}.value2`, "Needs a second number");
      });
      if (t.during && !splitDuring(t.during)[0]) add(`${p}.during`, "Use HH:MM-HH:MM");
      if (!(t.where || []).length && !(t.keywords || []).length && !String(t.semantic || "").trim()) {
        add(`${p}.where`, "Without a condition, keyword or AI check this fires on every event");
      }
    });
    const ws = parseWhen(s.window_start), we = parseWhen(s.window_end);
    if (ws && we && we < ws) add("window_end", "Ends before it starts");
    if (!actionsOf(s).length) add("on_match", "Add something to do");
    for (const list of ["on_match", "on_deadline"]) {
      actionsOf(s, list).forEach((a, i) => {
        const p = `${list}.${i}`;
        if (!ACTION_TYPES.includes(a.type)) add(`${p}.type`, "Pick what to do");
        if (a.type === "notify" && !String(a.text || "").trim()) add(`${p}.text`, "Say what to tell you");
        if (a.type === "tool" && !String(a.tool || "").trim()) add(`${p}.tool`, "Pick a tool");
        if (a.type === "tool" && String(a.args_json || "").trim()) {
          let ok = false;
          try { const v = JSON.parse(a.args_json); ok = v && typeof v === "object" && !Array.isArray(v); } catch (_) { ok = false; }
          if (!ok) add(`${p}.args_json`, "Arguments must be a JSON object");
        }
        if (a.type === "agent" && !String(a.instructions || "").trim()) add(`${p}.instructions`, "Tell the agent what to do");
      });
    }
    return errs;
  }

  /** The server's 400 body ({error, errors: [string | {path|field, message}]}) as [{path, message}]: a path
   *  at the start of a string ("triggers[0].where[1].value: …", "triggers.0: …") is read out of it. */
  function normalizeErrors(body) {
    const b = body || {};
    const list = Array.isArray(b.errors) && b.errors.length ? b.errors : (b.error || b.message ? [b.message || b.error] : []);
    return list.map((e) => {
      if (e && typeof e === "object") {
        return { path: normPath(e.path || e.field || e.loc || ""), message: String(e.message || e.msg || e.error || "") };
      }
      const s = String(e);
      const m = /^\s*((?:spec\.)?[a-z_]+(?:\[\d+\]|\.\d+|\.[a-z_0-9]+)*)\s*[:—-]\s+(.+)$/i.exec(s);
      if (m && /^(?:spec\.)?(title|kind|triggers|window_start|window_end|repeat|cooldown_minutes|on_match|on_deadline|evidence|task_title)/.test(m[1])) {
        return { path: normPath(m[1]), message: m[2] };
      }
      return { path: "", message: s };
    });
  }
  function normPath(p) {
    if (Array.isArray(p)) p = p.join(".");
    return String(p || "").replace(/^spec\./, "").replace(/\[(\d+)\]/g, ".$1").replace(/^\./, "");
  }
  /** The errors for one place: those whose path is `prefix` or under it. */
  const errorsAt = (errs, prefix) => (errs || []).filter((e) => e.path === prefix || e.path.startsWith(`${prefix}.`));
  /** The errors not under any of `placed` prefixes (shown by the Save button). */
  const errorsElsewhere = (errs, placed) => (errs || []).filter((e) => !e.path || !placed.some((p) => e.path === p || e.path.startsWith(`${p}.`)));

  // ---------- dry run and load ----------

  const BUDGET = 20;   // AI checks a day a semantic trigger may cost

  /** One trigger's load in words, from a dry run ({matches, per_day, over_budget}) or the watch's `load`
   *  ({matches_14d, per_day, semantic, over_budget}): {line, warn, tone}. */
  function loadWords(r, semantic) {
    if (!r) return null;
    const n = r.matches != null ? r.matches : r.matches_14d;
    if (n == null) return null;
    const perDay = r.per_day != null ? Number(r.per_day) : n / 14;
    const rate = perDay >= 10 ? Math.round(perDay) : perDay.toFixed(1).replace(/\.0$/, "");
    const line = n === 0 ? "Last 2 weeks: no matches" : `Last 2 weeks: ${n} match${n === 1 ? "" : "es"} (${rate}/day)`;
    const sem = semantic != null ? semantic : r.semantic;
    let warn = "";
    if (r.over_budget) warn = `Over the AI-check budget (${BUDGET} a day): add a condition or keyword to narrow it`;
    else if (!sem && perDay > 5) warn = "This would tell you often: narrow it, or add an AI check";
    return { line: sem && n ? `${line} · each gets an AI check` : line, warn, tone: r.over_budget ? "rose" : (warn ? "amber" : "") };
  }

  // ---------- events in words ----------

  function money(amount) {
    const n = num(amount);
    if (n === null) return "";
    const s = Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: Math.abs(n) >= 100 ? 0 : 2 });
    return `${n > 0 ? "+" : n < 0 ? "−" : ""}${s} ₪`;
  }

  /** An event's data in a short line ("+21,800 ₪ · Robin", "WhatsApp · Gil: are you home?"). */
  function eventSummary(type, data) {
    const d = data || {};
    const t = String(type || "");
    const join = (...xs) => xs.filter((x) => x !== undefined && x !== null && String(x).trim()).join(" · ");
    if (/^money\./.test(t)) return join(money(d.amount), d.counterparty, d.category);
    if (/email/.test(t)) return join(d.from, d.subject || d.text);
    if (/notification/.test(t)) {
      const who = d.sender || d.title;
      const text = d.text && d.text !== who ? d.text : "";
      return join(d.app, who && text ? `${who}: ${text}` : (who || text));
    }
    if (/call/.test(t)) return join(d.person, d.direction, d.title || d.summary);
    if (/car/.test(t)) return d.action === "leave" ? "Left the car" : "Got in the car";
    if (/location/.test(t)) return `${d.action === "leave" ? "Left" : "Arrived at"} ${d.zone || "a place"}`;
    if (/woke/.test(t)) return join("Woke up", d.why);
    if (/^tasks\./.test(t)) return join(/completed/.test(t) ? "Done" : "Added", d.title);
    return join(...Object.values(d).filter((v) => typeof v === "string").slice(0, 3));
  }

  /** A fire's actions with their status words and hue. */
  function fireActions(fire) {
    return ((fire && fire.actions) || []).map((a) => ({
      label: ACTION_WORDS[a.type] || words(a.type), detail: a.detail || "", run_id: a.run_id || null, session_id: a.session_id || null,
      status: a.status, statusLabel: (FIRE_STATUS[a.status] || { label: words(a.status) }).label, hue: (FIRE_STATUS[a.status] || { hue: "mute" }).hue,
    }));
  }

  // ---------- recent checks (GET /api/watches/{id}/trace) ----------

  const TRACE_FILTERS = [["all", "All"], ["near", "Near misses"], ["fired", "Fires"]];
  const TRACE_OUTCOMES = {
    drop: { label: "No match", hue: "mute" }, during: { label: "Outside its hours", hue: "mute" },
    outside_window: { label: "Outside its window", hue: "mute" }, semantic_no: { label: "AI said no", hue: "plum" },
    semantic_yes: { label: "AI said yes", hue: "plum" }, semantic_failed: { label: "AI check failed", hue: "rose" },
    fired: { label: "Fired", hue: "ember" }, cooldown: { label: "Cooldown", hue: "amber" },
    skipped: { label: "Skipped", hue: "amber" }, unstored: { label: "Not stored", hue: "mute" },
  };
  /** The query string of a trace request: "?limit=100" plus the filter when it is not "all". */
  function traceQuery(filter, limit = 100) {
    const f = TRACE_FILTERS.some(([k]) => k === filter) ? filter : "all";
    return `?limit=${Math.max(1, Math.min(500, Number(limit) || 100))}${f === "all" ? "" : `&outcome=${f}`}`;
  }
  /** One trace row in words: {what, outcome, hue, reason, near, trigger}. */
  function traceWords(row) {
    const r = row || {};
    const o = TRACE_OUTCOMES[r.outcome] || { label: words(r.outcome || "?"), hue: "mute" };
    const what = r.summary || (r.event_type ? eventSummary(r.event_type, r.data || {}) : "");
    return { what, outcome: o.label, hue: o.hue, reason: r.reason || "", near: Boolean(r.near), trigger: r.trigger_id || "" };
  }
  /** An event's data as [field, value] lines for the expanded row (empty values left out, long ones cut). */
  function traceDataLines(data) {
    const out = [];
    for (const [k, v] of Object.entries(data || {})) {
      if (v === null || v === undefined || v === "") continue;
      const s = typeof v === "string" ? v : JSON.stringify(v);
      out.push([k, s.length > 300 ? `${s.slice(0, 299)}…` : s]);
    }
    return out;
  }

  // ---------- the live pipeline ----------

  const SINKS = [
    { key: "notify", label: "Tell me", icon: "bell-ring" },
    { key: "tool", label: "Tool", icon: "wrench" },
    { key: "agent", label: "Agent", icon: "sparkles" },
    { key: "task", label: "Task", icon: "list-checks" },
  ];
  /** The action node an action type lands in. */
  function sinkOf(type) {
    if (type === "tool") return "tool";
    if (type === "agent") return "agent";
    if (type === "create_task" || type === "complete_task" || type === "suggest_complete") return "task";
    return "notify";
  }
  /** An action step's type: `action_type` when the server sends it, else read from its summary's words. */
  function actionTypeOf(ev) {
    if (ev.action_type) return ev.action_type;
    if (ev.action) return ev.action;
    const s = String(ev.summary || "").toLowerCase();
    if (/agent/.test(s)) return "agent";
    if (/task/.test(s)) return "create_task";
    if (/tool|ran |run /.test(s)) return "tool";
    return "notify";
  }

  function emptyStats() {
    return { since: null, events_in: 0, by_type: {}, filter_pass: 0, semantic_checks: 0, semantic_yes: 0, fires: 0, actions: {}, avg_filter_us: 0, cost_usd: 0, clock: 0, filtered: 0 };
  }

  /** The live state from GET /api/watches/pipeline: the server's counters, the lanes to draw (the catalog's
   *  types, the ones seen, then the clock), the feed rebuilt from `recent` (no dots for those). */
  function pipelineInit(snapshot, catalog) {
    const snap = snapshot || {};
    const stats = Object.assign(emptyStats(), clone(snap.stats || {}));
    stats.by_type = stats.by_type || {};
    stats.actions = stats.actions || {};
    const state = {
      stats, lastSeq: 0, feed: [], chains: {}, at: {}, byWatch: {}, laneKeys: [],
      watches_active: snap.watches_active || 0, triggers_active: snap.triggers_active || 0,
    };
    for (const et of (catalog && catalog.event_types) || []) addLane(state, et.name);
    for (const t of Object.keys(stats.by_type)) addLane(state, t);
    stats.filtered = stats.filtered || stats.events_in || 0;
    for (const ev of snap.recent || []) pipelineApply(state, ev, { count: false });
    return state;
  }
  function addLane(state, type) {
    const lane = laneOf(type);
    if (lane.key !== "clock" && !state.laneKeys.includes(lane.key)) state.laneKeys.push(lane.key);
    return lane;
  }
  /** The lanes in drawing order (known ones in their fixed order, then the others, then the clock). */
  function lanes(state) {
    const order = LANES.map((l) => l.key);
    const keys = state.laneKeys.slice().sort((a, b) => {
      const ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    const all = keys.map((k) => {
      const known = LANES.find((l) => l.key === k);
      return known ? { key: k, label: known.label, icon: known.icon, hue: known.hue } : laneOf(k);
    });
    return all.concat([CLOCK]);
  }
  /** How many events came in on a lane (summed over its event types). */
  function laneCount(state, key) {
    if (key === "clock") return state.stats.clock || 0;
    let n = 0;
    for (const [t, c] of Object.entries(state.stats.by_type || {})) if (laneOf(t).key === key) n += c;
    return n;
  }
  const actionCount = (state, sink) => Object.entries(state.stats.actions || {}).reduce((n, [t, c]) => n + (sinkOf(t) === sink ? c : 0), 0);

  const FEED_MAX = 40;
  function feedEntry(state, key, ev, kind) {
    let e = state.chains[key];
    if (!e) {
      const lane = kind === "time" || kind === "deadline" ? CLOCK : laneOf(ev.event_type);
      e = { key, kind, at: ev.at, seq: ev.seq, lane: lane.key, hue: lane.hue, icon: lane.icon, summary: ev.summary || "", event_type: ev.event_type || null, dropped: false, candidates: null, watches: [] };
      state.chains[key] = e;
      state.feed.unshift(e);
      if (state.feed.length > FEED_MAX) {
        for (const old of state.feed.splice(FEED_MAX)) delete state.chains[old.key];
      }
    }
    return e;
  }
  function feedWatch(e, ev) {
    if (!ev.watch_id) return null;
    let w = e.watches.find((x) => x.id === ev.watch_id && (!ev.trigger_id || !x.trigger_id || x.trigger_id === ev.trigger_id));
    if (!w) { w = { id: ev.watch_id, title: ev.watch_title || "", trigger_id: ev.trigger_id || null, ai: null, fired: false, actions: [] }; e.watches.push(w); }
    if (ev.watch_title) w.title = ev.watch_title;
    return w;
  }

  /** Applies one PipelineEvent: the counters (unless `count: false`), the feed, and where its dot goes.
   *  Returns the moves [{key, parent, from, to, tone, end}] — `from`/`to` are node ids ("lane:money",
   *  "filter", "ai", "fire", "sink:notify", "lane:clock"); `end` is "pass" | "drop" | "glow" | "land" | "pulse".
   *  A step already seen (seq not above the last one) changes nothing. */
  function pipelineApply(state, ev, opts) {
    const count = !opts || opts.count !== false;
    if (!ev || typeof ev !== "object") return [];
    if (typeof ev.seq === "number") {
      if (ev.seq <= state.lastSeq) return [];
      state.lastSeq = ev.seq;
    }
    const st = state.stats;
    const moves = [];
    const wkey = (chain) => `${chain}|${ev.watch_id || ""}|${ev.trigger_id || ""}`;
    switch (ev.stage) {
      case "event": {
        const lane = addLane(state, ev.event_type);
        const chain = ev.event_id || `e${ev.seq}`;
        feedEntry(state, chain, ev, "event");
        if (count) { st.events_in++; st.by_type[ev.event_type] = (st.by_type[ev.event_type] || 0) + 1; }
        state.at[chain] = "filter";
        moves.push({ key: chain, parent: null, from: `lane:${lane.key}`, to: "filter", tone: lane.hue, end: null });
        break;
      }
      case "filter": {
        const chain = ev.event_id || `e${ev.seq}`;
        const e = feedEntry(state, chain, ev, "event");
        e.candidates = ev.candidates == null ? null : ev.candidates;
        if (ev.result === "drop") e.dropped = true;
        if (count) {
          st.filtered = (st.filtered || 0) + 1;
          if (ev.result === "pass") st.filter_pass++;
          if (typeof ev.us === "number") st.avg_filter_us = st.avg_filter_us + (ev.us - st.avg_filter_us) / st.filtered;
        }
        moves.push({ key: chain, parent: null, from: "filter", to: "filter", tone: ev.result === "drop" ? "mute" : "ink", end: ev.result === "drop" ? "drop" : "pass" });
        break;
      }
      case "semantic": {
        const chain = ev.event_id || `e${ev.seq}`;
        const e = feedEntry(state, chain, ev, "event");
        const w = feedWatch(e, ev);
        if (w) w.ai = ev.result === "yes" ? "yes" : "no";
        if (count) { st.semantic_checks++; if (ev.result === "yes") st.semantic_yes++; st.cost_usd += Number(ev.cost_usd) || 0; }
        const key = wkey(chain);
        state.at[key] = "ai";
        moves.push({ key, parent: chain, from: "filter", to: "ai", tone: "plum", end: ev.result === "yes" ? "pass" : "drop" });
        break;
      }
      case "time":
      case "deadline": {
        const chain = `${ev.stage}:${ev.seq}`;
        const e = feedEntry(state, chain, ev, ev.stage);
        feedWatch(e, ev);
        if (count) st.clock = (st.clock || 0) + 1;
        if (ev.watch_id) state.byWatch[ev.watch_id] = chain;
        state.at[wkey(chain)] = "lane:clock";
        moves.push({ key: wkey(chain), parent: null, from: "lane:clock", to: "lane:clock", tone: ev.stage === "deadline" ? "rose" : "amber", end: "pulse" });
        if (ev.stage === "deadline") {
          state.at[wkey(chain)] = "fire";
          moves.push({ key: wkey(chain), parent: null, from: "lane:clock", to: "fire", tone: "rose", end: "glow" });
        }
        break;
      }
      case "fire": {
        const chain = ev.event_id || (ev.watch_id && state.byWatch[ev.watch_id]) || `f${ev.seq}`;
        const e = feedEntry(state, chain, ev, ev.event_id ? "event" : "time");
        const w = feedWatch(e, ev);
        if (w) w.fired = true;
        e.dropped = false;
        if (count) st.fires++;
        if (ev.watch_id) state.byWatch[ev.watch_id] = chain;
        const key = wkey(chain);
        const from = state.at[key] || (ev.event_id ? "filter" : "lane:clock");
        state.at[key] = "fire";
        moves.push({ key, parent: from === "filter" ? chain : null, from, to: "fire", tone: "ember", end: "glow" });
        break;
      }
      case "action": {
        const chain = ev.event_id || (ev.watch_id && state.byWatch[ev.watch_id]) || `a${ev.seq}`;
        const e = feedEntry(state, chain, ev, ev.event_id ? "event" : "time");
        const type = actionTypeOf(ev);
        const w = feedWatch(e, ev) || (e.watches[e.watches.length - 1] || null);
        if (w) w.actions.push({ type, result: ev.result || "done", summary: ev.summary || "" });
        if (count) st.actions[type] = (st.actions[type] || 0) + 1;
        const tone = ev.result === "held" ? "amber" : ev.result === "failed" ? "rose" : "sage";
        moves.push({ key: `${wkey(chain)}|${ev.seq}`, parent: wkey(chain), from: "fire", to: `sink:${sinkOf(type)}`, tone, end: "land" });
        break;
      }
      case "evidence": {
        const chain = ev.event_id || `v${ev.seq}`;
        const e = feedEntry(state, chain, ev, "evidence");
        const w = feedWatch(e, ev);
        if (w) { w.evidence = ev.result || null; }
        const key = `${wkey(chain)}|ev`;
        const start = state.at[chain] || `lane:${laneOf(ev.event_type).key}`;
        moves.push({ key, parent: state.at[chain] ? chain : null, from: start, to: "ai", tone: "plum", end: ev.result === "yes" ? "pass" : "drop" });
        if (ev.result === "yes") moves.push({ key, parent: null, from: "ai", to: "sink:task", tone: "sage", end: "land" });
        break;
      }
      default:
        break;
    }
    return moves;
  }

  const DONE_WORDS = { notify: "told you", tool: "ran the tool", agent: "the agent did it", create_task: "added a task", complete_task: "closed the task", suggest_complete: "suggested closing it" };
  const HELD_WORDS = { tool: "waiting for your OK to run it", agent: "the agent is waiting for your OK" };

  /** A feed entry in plain words: {head, steps: [string], tone, quiet}. "Money · +21,800 ₪ from a person" →
   *  ["matched “Transfer ~22k”", "AI check: yes", "told you"]. */
  function feedWords(e) {
    const head = e.summary || (e.kind === "time" ? "Time" : e.kind === "deadline" ? "Deadline" : eventLabel(e.event_type));
    const steps = [];
    let tone = e.hue || "sky";
    if (e.kind === "deadline") steps.push("deadline passed, nothing matched");
    if (e.dropped && !e.watches.some((w) => w.fired || w.ai)) {
      return { head, steps: ["no watch matched"], tone: "mute", quiet: true };
    }
    for (const w of e.watches) {
      const name = w.title ? quote(w.title) : "a watch";
      if (e.kind === "evidence") { steps.push(w.evidence === "yes" ? `looks done: ${name} → offered to close it` : `checked ${name}: not done yet`); continue; }
      if (w.ai === "no") { steps.push(`AI check said no to ${name}`); tone = "plum"; continue; }
      if (w.ai === "yes") steps.push(`AI check: yes for ${name}`);
      if (w.fired) { steps.push(e.kind === "event" ? `matched ${name}` : `${name} is due`); tone = "ember"; }
      for (const a of w.actions) {
        if (a.result === "held") { steps.push(HELD_WORDS[a.type] || "waiting for your OK"); tone = "amber"; }
        else if (a.result === "failed") { steps.push(`${(ACTION_WORDS[a.type] || "the action").toLowerCase()} failed`); tone = "rose"; }
        else steps.push(DONE_WORDS[a.type] || "done");
      }
    }
    if (!steps.length && e.candidates) steps.push(`passed the filter for ${e.candidates} watch${e.candidates === 1 ? "" : "es"}`);
    return { head, steps, tone, quiet: false };
  }

  // ---------- the diagram's geometry ----------

  /** Where the nodes sit in a stage of `width` × `height` px for `laneList` (lanes(state)): {nodes: {id: {x, y}},
   *  edges: [{id, from, to}], compact}. Sources on the left, then Filter, AI check (above the line), Fire and the
   *  actions on the right; the clock is the last lane and its edge goes straight to Fire. */
  function layout(laneList, width, height) {
    const w = Math.max(280, width || 0), h = Math.max(200, height || 0);
    const compact = w < 640;
    const nodes = {};
    const padY = 34;
    const n = laneList.length;
    const laneX = compact ? 52 : Math.min(210, w * 0.22);
    laneList.forEach((l, i) => {
      const y = n === 1 ? h / 2 : padY + (i * (h - padY * 2)) / (n - 1);
      nodes[`lane:${l.key}`] = { x: laneX, y };
    });
    const cy = h / 2;
    const sinkX = compact ? w - 52 : w - Math.min(170, w * 0.18);
    const span = sinkX - laneX;
    nodes.filter = { x: laneX + span * 0.34, y: cy };
    nodes.fire = { x: laneX + span * 0.78, y: cy };
    nodes.ai = { x: laneX + span * 0.56, y: cy - Math.min(110, h * 0.26) };
    const sinkGap = Math.min(70, (h - padY * 2) / (SINKS.length - 1 || 1));
    SINKS.forEach((s, i) => { nodes[`sink:${s.key}`] = { x: sinkX, y: cy + (i - (SINKS.length - 1) / 2) * sinkGap }; });
    const edges = [];
    for (const l of laneList) {
      if (l.key === "clock") edges.push({ id: "clock-fire", from: "lane:clock", to: "fire" });
      else edges.push({ id: `${l.key}-filter`, from: `lane:${l.key}`, to: "filter" });
    }
    edges.push({ id: "filter-fire", from: "filter", to: "fire" }, { id: "filter-ai", from: "filter", to: "ai" }, { id: "ai-fire", from: "ai", to: "fire" });
    for (const s of SINKS) edges.push({ id: `fire-${s.key}`, from: "fire", to: `sink:${s.key}` });
    return { nodes, edges, compact, width: w, height: h };
  }

  /** The curve between two points: a horizontal cubic Bézier [p0, p1, p2, p3] (a point when they coincide). */
  function curve(a, b) {
    const dx = Math.max(24, Math.abs(b.x - a.x) * 0.5);
    return [{ x: a.x, y: a.y }, { x: a.x + dx, y: a.y }, { x: b.x - dx, y: b.y }, { x: b.x, y: b.y }];
  }
  /** The point at `t` (0..1) along a curve. */
  function pointAt(c, t) {
    const u = 1 - t;
    const a = u * u * u, b = 3 * u * u * t, d = 3 * u * t * t, e = t * t * t;
    return { x: a * c[0].x + b * c[1].x + d * c[2].x + e * c[3].x, y: a * c[0].y + b * c[1].y + d * c[2].y + e * c[3].y };
  }
  /** An SVG path for a curve. */
  const pathD = (c) => `M${r1(c[0].x)},${r1(c[0].y)} C${r1(c[1].x)},${r1(c[1].y)} ${r1(c[2].x)},${r1(c[2].y)} ${r1(c[3].x)},${r1(c[3].y)}`;
  const r1 = (v) => Math.round(v * 10) / 10;
  /** Ease in-out for a dot's travel. */
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

  /** The edge a move travels ("filter-ai"), or null when it stays on one node. */
  function edgeOf(from, to) {
    if (from === to) return null;
    const f = from.replace(/^lane:/, "").replace(/^sink:/, "");
    const t = to.replace(/^lane:/, "").replace(/^sink:/, "");
    return `${f}-${t}`;
  }

  /** Counters for the nodes: {lanes: {key: n}, filter: {in, passed, us}, ai: {checks, yes, cost}, fire, sinks}. */
  function counters(state) {
    const st = state.stats;
    const out = { lanes: {}, filter: { in: st.events_in || 0, passed: st.filter_pass || 0, us: st.avg_filter_us || 0 }, ai: { checks: st.semantic_checks || 0, yes: st.semantic_yes || 0, cost: st.cost_usd || 0 }, fire: st.fires || 0, sinks: {} };
    for (const l of lanes(state)) out.lanes[l.key] = laneCount(state, l.key);
    for (const s of SINKS) out.sinks[s.key] = actionCount(state, s.key);
    return out;
  }
  /** "38 µs", "1.2 ms". */
  function usWords(us) {
    const n = Number(us) || 0;
    if (n >= 1000) return `${(n / 1000).toFixed(1)} ms`;
    return `${n >= 10 ? Math.round(n) : n.toFixed(1)} µs`;
  }
  /** "$0.0042", "$1.20", "$0". */
  function costWords(c) {
    const n = Number(c) || 0;
    if (!n) return "$0";
    return n < 0.1 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
  }

  globalThis.DanaWatchesCore = {
    OPS, OP_WORDS, NUMERIC_OPS: [...NUMERIC_OPS], EVERY, EVERY_WORDS, ACTION_TYPES, ACTION_WORDS, URGENCY_WORDS, STATUS_WORDS, SINKS, BUDGET,
    laneOf, eventLabel, fieldWords, conditionWords, triggerWords, actionWords, triggerChips, actionChips,
    parseWhen, whenWords, agoWords, toInput, fromInput, splitDuring, joinDuring,
    matchesQuery, groupWatches, rowStatus, rowHue, kicker,
    clone, same, nextTriggerId, fieldsOf, addTrigger, removeTrigger, setTrigger, addCondition, removeCondition, setCondition,
    addKeyword, removeKeyword, toggleEvidenceType, addAction, removeAction, setAction, setSpec,
    validate, normalizeErrors, errorsAt, errorsElsewhere, loadWords, eventSummary, money, fireActions,
    TRACE_FILTERS, TRACE_OUTCOMES, traceQuery, traceWords, traceDataLines,
    sinkOf, actionTypeOf, pipelineInit, pipelineApply, lanes, laneCount, counters, feedWords,
    layout, curve, pointAt, pathD, ease, edgeOf, usWords, costWords,
  };
})();
