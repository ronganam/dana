// Run: node extensions/omarchy/tests/dana.test.js
// Loads lib/Dana.js the way QML does (a .pragma library script) and checks it
// against the server's shared fixtures (server/tests/fixtures/golden.sse, live_web.json), so
// a payload change that breaks the plugin fails here as it does for Android.
const fs = require("fs")
const path = require("path")
const assert = require("assert")

const root = path.join(__dirname, "..")
const src = fs.readFileSync(path.join(root, "lib", "Dana.js"), "utf8").replace(/^\.pragma library\s*$/m, "")
const names = ["cleanText", "oneLine", "expandHome", "parseEnv", "baseUrl", "token", "readFrames", "humanTool",
  "argLines", "newRun", "applyEvent", "messageSeq", "turnsFromState", "pendingFromState", "safeMarkdown",
  "safeLink", "summary", "parseNow", "approvalSession", "secondaryOf", "errorKind", "THREAD", "DEVICE",
  "parseLive", "liveActivity", "liveView", "liveStep", "liveDuration", "liveTimer", "soonestLive", "liveBarText",
  "liveApply", "livePath", "liveOpenUrl", "liveGlyph"]
const D = new Function(src + "\nreturn {" + names.join(",") + "}")()
const fixture = (name) => fs.readFileSync(path.join(root, "..", "..", "server", "tests", "fixtures", name), "utf8")

let passed = 0
function test(name, fn) {
  try { fn(); passed++ } catch (e) { console.error("FAIL " + name); throw e }
}

test("env file: DANA_URL and DANA_TOKEN, quotes and comments", () => {
  const env = D.parseEnv("# Dana\nDANA_URL=\"https://dana.example.com/\"\nexport DANA_TOKEN='abcdefghijklmnop1234'\nOTHER=x\n")
  assert.deepStrictEqual(env, { url: "https://dana.example.com/", token: "abcdefghijklmnop1234" })
  assert.deepStrictEqual(D.parseEnv(""), { url: "", token: "" })
  assert.strictEqual(D.parseEnv("DANA_TOKEN=$(rm -rf ~)").token, "$(rm -rf ~)") // read, never evaluated…
  assert.strictEqual(D.token("$(rm -rf ~)"), "")                                   // …and refused as a token
})

test("base url: https origins, plain http only on this machine", () => {
  assert.strictEqual(D.baseUrl("https://dana.example.com/"), "https://dana.example.com")
  assert.strictEqual(D.baseUrl("https://dana.example.com:8443"), "https://dana.example.com:8443")
  assert.strictEqual(D.baseUrl("http://localhost:18080"), "http://localhost:18080")
  assert.strictEqual(D.baseUrl("http://dana.example.com"), "")
  assert.strictEqual(D.baseUrl("https://user:pw@dana.example.com"), "")
  assert.strictEqual(D.baseUrl("https://dana.example.com/api?x"), "")
  assert.strictEqual(D.baseUrl("javascript:alert(1)"), "")
})

test("token shape", () => {
  assert.strictEqual(D.token("  abc.def-ghi_jkl.mnop\n"), "abc.def-ghi_jkl.mnop")
  assert.strictEqual(D.token("short"), "")
  assert.strictEqual(D.token("abc def ghi jkl mno pqr"), "")
})

test("SSE reader: frames across chunks, heartbeats, CRLF, bad JSON", () => {
  const reader = { pos: 0 }
  let text = ": heartbeat\n\ndata: {\"type\":\"text_delta\",\"delta\":\"he"
  assert.deepStrictEqual(D.readFrames(text, reader), [])
  text += "llo\"}\n\ndata: not json\n\nid: 3\r\ndata: {\"type\":\"run_end\",\r\ndata: \"status\":\"idle\"}\r\n\r\n"
  const frames = D.readFrames(text, reader)
  assert.deepStrictEqual(frames.map((f) => f.type), ["text_delta", "run_end"])
  assert.strictEqual(frames[0].delta, "hello")
  assert.strictEqual(frames[1].status, "idle")
  assert.deepStrictEqual(D.readFrames(text, reader), [])
})

test("golden.sse replays into the final answer and state", () => {
  const frames = D.readFrames(fixture("golden.sse"), { pos: 0 })
  assert.ok(frames.length > 10)
  let run = D.newRun("look up a and b")
  let state = null
  const activities = []
  for (const f of frames) {
    if (f.type === "state") { state = f.state; continue }
    run = D.applyEvent(run, f)
    if (run.activity) activities.push(run.activity)
  }
  assert.strictEqual(run.answer, "Both lookups done.")
  assert.strictEqual(run.status, "idle")
  assert.strictEqual(run.runId, "68c7d1f0de43435c")
  assert.strictEqual(run.lastSeq, 16)
  assert.ok(activities.includes("Slow lookup"))
  assert.deepStrictEqual(run.tools.map((t) => t.state), ["done", "done"])
  assert.ok(state)
  const turns = D.turnsFromState(state)
  assert.strictEqual(turns.length, 1)
  assert.strictEqual(turns[0].prompt, "look up a and b")
  assert.strictEqual(turns[0].answer, "Both lookups done.")
  assert.strictEqual(turns[0].tools.length, 2)
  assert.strictEqual(D.pendingFromState(state, turns), null)
})

test("reducer: text across steps, retry, approval, selection, errors", () => {
  let run = D.newRun("hi")
  run = D.applyEvent(run, { type: "step_start" })
  run = D.applyEvent(run, { type: "text_delta", delta: "Looking." })
  run = D.applyEvent(run, { type: "step_start" })
  run = D.applyEvent(run, { type: "text_delta", delta: "Half" })
  run = D.applyEvent(run, { type: "retry" })
  run = D.applyEvent(run, { type: "text_delta", delta: "Done" })
  assert.strictEqual(run.answer, "Looking.\n\nDone")
  run = D.applyEvent(run, { type: "approval_required", id: "c1", name: "send_whatsapp", args: { to: "Gal", message: "On my way" } })
  assert.strictEqual(run.pending.kind, "approval")
  assert.strictEqual(run.pending.title, "Send whatsapp")
  assert.deepStrictEqual(run.pending.lines, ["To: Gal", "Message: On my way"])
  run = D.applyEvent(run, { type: "selection_required", prompt: "Which Gal?", options: ["Gal A", "Gal B"], multiple: false })
  assert.deepStrictEqual(run.pending, { kind: "selection", prompt: "Which Gal?", options: ["Gal A", "Gal B"], multiple: false })
  run = D.applyEvent(run, { type: "error", message: "boom" })
  assert.strictEqual(run.error, "boom")
})

test("thread state: only the remembered window, pending approval", () => {
  const state = {
    thread: true, status: "waiting_approval", context_from_seq: 12, pending_approval: { status: "pending_approval" },
    messages: [
      { id: "msg_user_3", isUser: true, message: "old question" },
      { id: "msg_assistant_4", isUser: false, message: "old answer" },
      { id: "msg_user_12", isUser: true, message: "Tell Gal I'm late" },
      { id: "msg_tool_13_c1", isUser: false, message: "", toolCall: { toolCallId: "c1", toolName: "send_whatsapp", toolArgs: "{\"to\":\"Gal\"}", requiresApproval: true } },
    ],
  }
  const turns = D.turnsFromState(state)
  assert.strictEqual(turns.length, 1)
  assert.strictEqual(turns[0].prompt, "Tell Gal I'm late")
  const pending = D.pendingFromState(state, turns)
  assert.strictEqual(pending.kind, "approval")
  assert.deepStrictEqual(pending.lines, ["To: Gal"])
  assert.deepStrictEqual(D.turnsFromState({ thread: true, context_from_seq: null, messages: state.messages }), [])
  const quiet = { thread: true, context_from_seq: 3, context_expires_at: 1000, messages: state.messages.slice(0, 2) }
  assert.strictEqual(D.turnsFromState(quiet, 999).length, 1)   // still remembered
  assert.deepStrictEqual(D.turnsFromState(quiet, 1001), [])     // the gap ran out: a fresh start
  assert.strictEqual(D.messageSeq("msg_tool_13_c1"), 13)
  assert.strictEqual(D.messageSeq("live-1"), -1)
})

test("markdown: no HTML, no images, links only http(s)", () => {
  const evil = "Hi <img src=\"https://example.invalid/t\"> ![x](https://example.invalid/p.png) [ok](https://example.com)\n[r]: https://example.invalid"
  const safe = D.safeMarkdown(evil)
  assert.ok(!safe.includes("<img"))
  assert.ok(safe.includes("&lt;img"))
  assert.ok(!safe.includes("![x]"))
  assert.ok(!safe.includes("p.png"))
  assert.ok(safe.includes("[ok](https://example.com)"))
  assert.ok(!safe.includes("[r]:"))
  assert.strictEqual(D.safeLink("https://example.com/a"), "https://example.com/a")
  assert.strictEqual(D.safeLink("file:///etc/passwd"), "")
  assert.strictEqual(D.safeLink("javascript:alert(1)"), "")
})

test("notification summary: plain, short, no markup", () => {
  const s = D.summary("**Done.** Booked <b>Noa</b> for `19:30`.\n\n```\ncode\n```\nSee [the event](https://x.example).")
  assert.ok(s.startsWith("Done. Booked"), s)
  assert.ok(s.endsWith("for 19:30. See the event."), s)
  assert.ok(!/[<>*`]|code|https/.test(s), s)
  assert.ok(D.summary("x".repeat(500)).length <= 161)
})

test("Now: items validated and bounded, approval session, secondary verbs", () => {
  const now = D.parseNow(JSON.stringify({
    lede: "Noa at 19:30.",
    items: [
      { id: "approval:main", kind: "approval", title: "Send a WhatsApp message to Gal", primary: { action: "open", label: "Review", client: true }, ref: { type: "thread", id: "main" } },
      { id: "task:7", kind: "reminder", title: "<b>Call the plumber</b>", source: "Reminder · 2 h ago",
        primary: { action: "done", label: "Done" },
        secondary: [{ action: "snooze", label: "1 h", args: { minutes: 60 } }, { action: "dismiss", label: "Dismiss" }, { action: "x y", label: "bad" }] },
      { id: "bad id", kind: "reminder", title: "dropped" },
    ],
    notifications: { total: 17, needs_you: 1 },
  }))
  assert.strictEqual(now.items.length, 2)
  assert.strictEqual(now.lede, "Noa at 19:30.")
  assert.deepStrictEqual(now.notifications, { total: 17, needsYou: 1 })
  assert.strictEqual(D.approvalSession(now.items[0]), "main")
  assert.strictEqual(D.approvalSession(now.items[1]), "")
  assert.strictEqual(now.items[1].title, "<b>Call the plumber</b>") // shown as PlainText
  assert.strictEqual(now.items[1].secondary.length, 2)
  assert.deepStrictEqual(D.secondaryOf(now.items[1], "snooze").args, { minutes: 60 })
  assert.strictEqual(D.secondaryOf(now.items[1], "ignore"), null)
  assert.strictEqual(D.parseNow("not json"), null)
})

test("live: the server's live_web.json parses, bad entries out", () => {
  const list = D.parseLive(fixture("live_web.json"))
  assert.deepStrictEqual(list.map((a) => a.id), ["rail:watch-12", "phone:agent-claude-abc"])
  const [rail, agent] = list
  assert.strictEqual(rail.countdown.to, Date.parse("2026-11-25T17:46:00+02:00"))
  assert.deepStrictEqual(rail.actions.map((a) => a.kind), ["prev", "next", "stop"])
  assert.deepStrictEqual(rail.choices.map((c) => c.key), ["404", "406"])
  assert.strictEqual(agent.ended, true)
  assert.strictEqual(agent.final.title, "Done · Fix the parser")
  assert.strictEqual(D.parseLive({ activities: [null, { id: "bad id" }, { id: "a:b", title: "<b>x</b>\u0007" }] }).length, 1)
  assert.strictEqual(D.parseLive({ activities: [{ id: "a:b", title: "<b>x</b>\u0007" }] })[0].title, "<b>x</b>") // PlainText
  assert.strictEqual(D.parseLive("not json"), null)
  assert.strictEqual(D.parseLive({}), null)
})

test("live: the shown choice overrides the activity; next / earlier stop at the ends", () => {
  const [rail, agent] = D.parseLive(fixture("live_web.json"))
  const first = D.liveView(rail, "")
  assert.strictEqual(first.key, "404")
  assert.strictEqual(first.title, "Departs 17:46 (+4)")
  assert.strictEqual(first.context, "Central → Harbour · Train 404")
  const second = D.liveView(rail, "406")
  assert.strictEqual(second.title, "Departs 17:52")
  assert.strictEqual(second.tone, "good")
  assert.strictEqual(second.context, first.context)          // a field the choice leaves out stays
  assert.strictEqual(second.index, 1)
  assert.strictEqual(D.liveStep(rail, "", 1), "406")
  assert.strictEqual(D.liveStep(rail, "", -1), "")
  assert.strictEqual(D.liveStep(rail, "406", 1), "")
  assert.strictEqual(D.liveStep(rail, "406", -1), "404")
  assert.strictEqual(D.liveStep(agent, "", 1), "")
  const done = D.liveView(agent, "")
  assert.strictEqual(done.ended, true)
  assert.strictEqual(done.text, "All 42 tests pass")
  assert.strictEqual(done.countdown, null)
})

test("live: timers count down and up", () => {
  const [rail] = D.parseLive(fixture("live_web.json"))
  const at = (iso) => Date.parse(iso)
  assert.deepStrictEqual(D.liveTimer(D.liveView(rail, ""), at("2026-11-25T17:38:18+02:00")), { label: "Departs in", value: "7:42", past: false })
  assert.deepStrictEqual(D.liveTimer(D.liveView(rail, ""), at("2026-11-25T17:47:00+02:00")), { label: "Departs", value: "now", past: true })
  assert.strictEqual(D.liveTimer(D.liveView(rail, ""), at("2026-11-25T16:41:00+02:00")).value, "1 h 05")
  const since = { since: at("2026-11-25T17:01:00+02:00") }
  assert.deepStrictEqual(D.liveTimer(since, at("2026-11-25T17:29:14+02:00")), { label: "Elapsed", value: "28:14", past: false })
  assert.deepStrictEqual([0, 462, 3900, 90000].map(D.liveDuration), ["0:00", "7:42", "1 h 05", "1 d 1 h"])
  assert.strictEqual(D.liveTimer({ title: "x" }, 0), null)
})

test("live: the bar shows the soonest running one, its glyph and countdown", () => {
  const list = D.parseLive(fixture("live_web.json"))
  const at = Date.parse("2026-11-25T17:38:18+02:00")
  const later = D.liveActivity({ id: "w:1", title: "Water", tone: "good", icon: "droplets", countdown: { to: "2026-11-25T18:30:00+02:00" } })
  const timerless = D.liveActivity({ id: "w:2", title: "Cooking", tone: "neutral", chip: "3/7" })
  assert.strictEqual(D.soonestLive([later, ...list], {}, at).id, "rail:watch-12")
  // The user's pick shows the later train's countdown.
  assert.strictEqual(D.liveBarText(list[0], "406", at), "13:42")
  assert.strictEqual(D.liveBarText(list[0], "", at), "7:42")
  assert.strictEqual(D.soonestLive([timerless, later], {}, at).id, "w:1")
  assert.strictEqual(D.soonestLive([timerless], {}, at).id, "w:2")
  assert.strictEqual(D.liveBarText(timerless, "", at), "3/7")      // no timer: the chip
  assert.strictEqual(D.soonestLive([list[1]], {}, at), null)        // an ended one never takes the bar
  assert.strictEqual(D.soonestLive([], {}, at), null)
  assert.strictEqual(D.liveGlyph("train-front"), String.fromCodePoint(0xF052C))
  assert.strictEqual(D.liveGlyph("droplets"), String.fromCodePoint(0xF058C))
  assert.strictEqual(D.liveGlyph("nope"), String.fromCodePoint(0xF0430))
})

test("live: replies, paths, where a click goes", () => {
  const list = D.parseLive(fixture("live_web.json"))
  const raw = JSON.parse(fixture("live_web.json")).activities[0]
  const replaced = D.liveApply(list, "rail:watch-12", { ...raw, selected: "406" })
  assert.strictEqual(replaced.length, 2)
  assert.strictEqual(replaced[0].selected, "406")
  assert.deepStrictEqual(D.liveApply(list, "rail:watch-12", null).map((a) => a.id), ["phone:agent-claude-abc"])
  assert.strictEqual(D.liveApply([], "rail:watch-12", raw)[0].id, "rail:watch-12")
  assert.strictEqual(D.livePath("rail:watch-12", "/select"), "/api/live/rail:watch-12/select")
  assert.strictEqual(D.livePath("phone:agent@x y"), "/api/live/phone:agent@x%20y")
  assert.strictEqual(D.liveOpenUrl("https://dana.example.com", "/rail"), "https://dana.example.com/#rail")
  assert.strictEqual(D.liveOpenUrl("https://dana.example.com", "https://example.com/t"), "https://example.com/t")
  assert.strictEqual(D.liveOpenUrl("https://dana.example.com", "javascript:alert(1)"), "")
  assert.strictEqual(D.liveOpenUrl("https://dana.example.com", "//evil.example"), "")
})

test("http status kinds", () => {
  assert.strictEqual(D.errorKind(401), "auth")
  assert.strictEqual(D.errorKind(0), "unreachable")
  assert.strictEqual(D.errorKind(409), "busy")
  assert.strictEqual(D.errorKind(500), "http")
})

test("every i18n key the QML uses exists in en.json", () => {
  const en = JSON.parse(fs.readFileSync(path.join(root, "i18n", "en.json"), "utf8"))
  const used = new Set()
  for (const file of fs.readdirSync(root).filter((f) => f.endsWith(".qml"))) {
    const qml = fs.readFileSync(path.join(root, file), "utf8")
    for (const m of qml.matchAll(/strings\.tn?\("([^"]+)"/g)) used.add(m[1])
    for (const m of qml.matchAll(/"(help\.[A-Za-z]+)"/g)) used.add(m[1])
  }
  // `strings.t("error." + kind)`: every kind the service and the HTTP helper can set.
  used.delete("error.")
  for (const kind of ["auth", "unreachable", "busy", "http", "notFound", "capture", "captureTool", "tooBig"]) used.add("error." + kind)
  assert.ok(used.size > 10)
  for (const key of used) assert.ok(key in en, "missing i18n key: " + key)
  for (const key of Object.keys(en)) assert.ok(used.has(key), "unused i18n key: " + key)
})

console.log(passed + " tests passed")
