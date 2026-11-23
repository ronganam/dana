// Run: node extensions/omarchy/tests/dana.test.js
// Loads lib/Dana.js the way QML does (a .pragma library script) and checks it
// against the server's shared fixtures (server/tests/fixtures/golden.sse), so
// a payload change that breaks the plugin fails here as it does for Android.
const fs = require("fs")
const path = require("path")
const assert = require("assert")

const root = path.join(__dirname, "..")
const src = fs.readFileSync(path.join(root, "lib", "Dana.js"), "utf8").replace(/^\.pragma library\s*$/m, "")
const names = ["cleanText", "oneLine", "expandHome", "parseEnv", "baseUrl", "token", "readFrames", "humanTool",
  "argLines", "newRun", "applyEvent", "messageSeq", "turnsFromState", "pendingFromState", "safeMarkdown",
  "safeLink", "summary", "parseNow", "approvalSession", "secondaryOf", "errorKind", "THREAD", "DEVICE"]
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
