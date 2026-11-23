.pragma library

// Pure helpers for the Dana plugin: configuration, the server's Server-Sent
// Events, the run event reducer, the shared thread's turns and the Now queue.
// Kept free of QML so tests/dana.test.js runs it under node against the
// server's own fixtures.

var MAX_RESPONSE = 2 * 1024 * 1024
var MAX_TEXT = 20000
var MAX_TITLE = 300
var MAX_ITEMS = 30
var MAX_TURNS = 30
var THREAD = "main"
var DEVICE = "desktop"

// Control characters out (newlines and tabs kept), length capped. Every
// display sink is PlainText or sanitized Markdown; this keeps a broken or
// hostile response from flooding the window.
function cleanText(value, max) {
  var text = value === undefined || value === null ? "" : String(value)
  text = text.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "")
  var cap = max || MAX_TEXT
  return text.length > cap ? text.slice(0, cap) + "…" : text
}

function oneLine(value, max) {
  return cleanText(value, max || MAX_TITLE).replace(/\s+/g, " ").trim()
}

// ---------------------------------------------------------------- config

function expandHome(path, home) {
  var p = String(path || "")
  if (p === "~") return home
  if (p.indexOf("~/") === 0) return home + p.slice(1)
  return p
}

// KEY=VALUE lines, as in ~/.config/dana/agent-hook.env: DANA_URL and
// DANA_TOKEN. Quotes around a value are dropped; comments and blank lines
// are skipped. Nothing is evaluated.
function parseEnv(text) {
  var out = { url: "", token: "" }
  var lines = String(text || "").split(/\r?\n/)
  for (var i = 0; i < lines.length; i++) {
    var m = /^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$/.exec(lines[i])
    if (!m) continue
    var value = m[2]
    if (value.length >= 2 && (value[0] === "\"" || value[0] === "'") && value[value.length - 1] === value[0])
      value = value.slice(1, -1)
    if (m[1] === "DANA_URL") out.url = value
    else if (m[1] === "DANA_TOKEN") out.token = value
  }
  return out
}

// An https origin (or plain http on this machine, for a local server), with
// no path, query or credentials. Anything else is refused so the token is
// never sent somewhere unexpected.
function baseUrl(raw) {
  var s = String(raw || "").trim().replace(/\/+$/, "")
  var m = /^(https?):\/\/([A-Za-z0-9.-]+)(:\d{1,5})?$/.exec(s)
  if (!m) return ""
  var local = m[2] === "localhost" || m[2] === "127.0.0.1"
  if (m[1] === "http" && !local) return ""
  return s
}

function token(raw) {
  var t = String(raw || "").trim()
  return /^[A-Za-z0-9._~+\/=-]{16,512}$/.test(t) ? t : ""
}

// ---------------------------------------------------------------- SSE

// Reads frames out of an XMLHttpRequest's growing responseText. `reader`
// keeps the offset between calls: { pos }. Returns the parsed frames; a
// heartbeat comment or a frame that is not JSON yields nothing.
function readFrames(text, reader) {
  var frames = []
  var all = String(text || "")
  for (;;) {
    var rest = all.slice(reader.pos)
    var m = /\r?\n\r?\n/.exec(rest)
    if (!m) break
    var block = rest.slice(0, m.index)
    reader.pos += m.index + m[0].length
    var data = []
    var lines = block.split(/\r?\n/)
    for (var i = 0; i < lines.length; i++) {
      if (lines[i].indexOf("data:") === 0) data.push(lines[i].slice(5).replace(/^ /, ""))
    }
    if (!data.length) continue
    try {
      var frame = JSON.parse(data.join("\n"))
      if (frame && typeof frame === "object" && typeof frame.type === "string") frames.push(frame)
    } catch (e) {}
  }
  return frames
}

// ---------------------------------------------------------------- runs

function humanTool(name) {
  var s = oneLine(name, 80).replace(/^zz_/, "").replace(/_/g, " ")
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : ""
}

function argsObject(args) {
  if (args && typeof args === "object") return args
  if (typeof args === "string") {
    try {
      var parsed = JSON.parse(args)
      if (parsed && typeof parsed === "object") return parsed
    } catch (e) {}
  }
  return null
}

// "Label: value" lines for an approval, bounded: what Dana is about to do,
// readable at a glance rather than as JSON.
function argLines(args, maxLines) {
  var obj = argsObject(args)
  if (!obj) return args ? [oneLine(args, 400)] : []
  var out = []
  var cap = maxLines || 8
  for (var key in obj) {
    if (out.length >= cap) { out.push("…"); break }
    var v = obj[key]
    if (v === null || v === undefined || v === "") continue
    var text = typeof v === "object" ? JSON.stringify(v) : String(v)
    out.push(humanTool(key) + ": " + oneLine(text, 400))
  }
  return out
}

function newRun(prompt) {
  return {
    runId: "", lastSeq: 0, prompt: oneLine(prompt, MAX_TEXT), committed: "", stepText: "",
    answer: "", tools: [], activity: "", status: "running", pending: null, error: ""
  }
}

function copyRun(run) {
  var next = {}
  for (var k in run) next[k] = run[k]
  next.tools = run.tools.slice()
  return next
}

// Fold one run event into the live state (pure). Mirrors the Raycast and
// Android reducers: text from earlier steps is kept, a retry drops the
// current step's text, `final` is authoritative.
function applyEvent(run, event) {
  var next = copyRun(run)
  if (event.run_id) next.runId = String(event.run_id)
  if (typeof event.seq === "number") next.lastSeq = event.seq
  function upsert(id, patch) {
    for (var i = 0; i < next.tools.length; i++) {
      if (next.tools[i].id === id) {
        var merged = {}
        for (var a in next.tools[i]) merged[a] = next.tools[i][a]
        for (var b in patch) merged[b] = patch[b]
        next.tools[i] = merged
        return merged
      }
    }
    var fresh = { id: id, name: "", state: "pending", args: null }
    for (var c in patch) fresh[c] = patch[c]
    next.tools.push(fresh)
    return fresh
  }
  switch (event.type) {
  case "transcription":
    if (!next.prompt) next.prompt = oneLine(event.text, MAX_TEXT)
    break
  case "step_start":
    if (next.stepText.trim()) next.committed = next.committed ? next.committed + "\n\n" + next.stepText.trim() : next.stepText.trim()
    next.stepText = ""
    next.activity = ""
    break
  case "text_delta":
    next.stepText += String(event.delta || "")
    next.activity = ""
    break
  case "retry":
    next.stepText = ""
    break
  case "tool_call":
    upsert(String(event.id), { name: String(event.name || ""), state: "pending" })
    break
  case "tool_start":
    upsert(String(event.id), { name: String(event.name || ""), args: event.args, state: "running" })
    next.activity = humanTool(event.name)
    break
  case "tool_end":
    upsert(String(event.id), { name: String(event.name || ""), state: event.ok ? "done" : "failed" })
    next.activity = ""
    break
  case "approval_required":
    var tool = upsert(String(event.id), { name: String(event.name || ""), args: event.args, state: "needs_approval" })
    next.pending = { kind: "approval", name: tool.name, title: humanTool(tool.name), lines: argLines(tool.args) }
    next.activity = ""
    break
  case "selection_required":
    next.pending = selectionPending(event)
    next.activity = ""
    break
  case "iterations_exceeded":
    next.pending = { kind: "iterations", summary: oneLine(event.summary, 600) }
    next.activity = ""
    break
  case "final":
    next.committed = String(event.content || "")
    next.stepText = ""
    break
  case "error":
    next.error = oneLine(event.message, 400)
    break
  case "cancelled":
    next.error = "cancelled"
    break
  case "run_end":
    next.status = String(event.status || "idle")
    next.activity = ""
    break
  }
  var parts = []
  if (next.committed) parts.push(next.committed)
  if (next.stepText.trim()) parts.push(next.stepText.trim())
  next.answer = cleanText(parts.join("\n\n"))
  return next
}

function selectionPending(req) {
  var options = Array.isArray(req && req.options) ? req.options.slice(0, 9).map(function(o) { return oneLine(o, 200) }) : []
  return { kind: "selection", prompt: oneLine(req && req.prompt, 400), options: options, multiple: !!(req && req.multiple) }
}

// ---------------------------------------------------------------- thread

// Thread message ids carry the message's seq: msg_user_12, msg_tool_13_call_a,
// msg_assistant_14 (core/session.py). -1 when there is none.
function messageSeq(id) {
  var m = /^msg_[a-z]+_(\d+)/.exec(String(id || ""))
  return m ? parseInt(m[1], 10) : -1
}

// The conversation Dana remembers now: the thread's messages from
// `context_from_seq` on, grouped into turns. Older messages stay on the
// phone and the web; the desktop shows only what the next message continues.
// Past `context_expires_at` (the quiet gap ran out) the next message starts
// fresh, so nothing is shown. `nowSeconds` defaults to the clock.
function turnsFromState(state, nowSeconds) {
  var messages = Array.isArray(state && state.messages) ? state.messages : []
  var from = state && state.thread ? state.context_from_seq : 0
  if (state && state.thread && (from === null || from === undefined)) return []
  var expires = state ? Number(state.context_expires_at) : 0
  var now = nowSeconds === undefined ? Date.now() / 1000 : nowSeconds
  if (state && state.thread && expires > 0 && expires <= now && !(state.active_run || state.pending_approval)) return []
  var turns = []
  var current = null
  for (var i = 0; i < messages.length; i++) {
    var msg = messages[i]
    if (!msg || typeof msg !== "object") continue
    if (from && messageSeq(msg.id) >= 0 && messageSeq(msg.id) < from) continue
    if (msg.isUser) {
      current = { id: String(msg.id), prompt: cleanText(msg.message), answer: "", tools: [], images: Array.isArray(msg.images) ? msg.images.length : 0 }
      turns.push(current)
    } else {
      if (!current) {
        current = { id: String(msg.id), prompt: "", answer: "", tools: [], images: 0 }
        turns.push(current)
      }
      if (msg.toolCall) {
        current.tools.push({ id: String(msg.toolCall.toolCallId || ""), name: String(msg.toolCall.toolName || ""),
                             args: msg.toolCall.toolArgs || msg.toolCall.parameters,
                             state: msg.toolCall.requiresApproval ? "needs_approval" : "done" })
      } else if (msg.message && String(msg.message).trim()) {
        current.answer = cleanText(current.answer ? current.answer + "\n\n" + msg.message : msg.message)
      }
    }
  }
  return turns.slice(-MAX_TURNS)
}

function pendingFromState(state, turns) {
  if (!state) return null
  if (state.status === "awaiting_selection" && state.selection_request) return selectionPending(state.selection_request)
  if (state.status === "iterations_exceeded")
    return { kind: "iterations", summary: oneLine(state.extend_summary || "", 600) }
  if (state.pending_approval) {
    for (var i = turns.length - 1; i >= 0; i--) {
      for (var j = turns[i].tools.length - 1; j >= 0; j--) {
        var t = turns[i].tools[j]
        if (t.state === "needs_approval") return { kind: "approval", name: t.name, title: humanTool(t.name), lines: argLines(t.args) }
      }
    }
    return { kind: "approval", name: "", title: "", lines: [] }
  }
  return null
}

// ---------------------------------------------------------------- display

// Markdown that Qt's MarkdownText may render: no raw HTML (a tag could load
// a remote resource or restyle the window) and no images (same reason);
// an image keeps its alt text. Links stay and open only through safeLink.
function safeMarkdown(text) {
  var s = cleanText(text)
  s = s.replace(/&/g, "&amp;").replace(/</g, "&lt;")
  s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
  s = s.replace(/!\[([^\]]*)\]\[[^\]]*\]/g, "$1")
  s = s.replace(/^\s*\[[^\]]+\]:\s*\S+.*$/gm, "")
  return s
}

function safeLink(url) {
  var s = String(url || "")
  return /^https?:\/\/[^\s]+$/i.test(s) ? s : ""
}

// The answer as one short plain line, for a desktop notification: Markdown
// marks and angle brackets out (the notification daemon may read markup).
function summary(text, max) {
  var s = cleanText(text, 4000)
  s = s.replace(/```[\s\S]*?```/g, " ")
  s = s.replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
  s = s.replace(/[*_`#>|~]+/g, "").replace(/[<>&]/g, " ")
  return oneLine(s, max || 160)
}

// ---------------------------------------------------------------- Now

var KINDS = ["approval", "notification", "suggestion", "reminder", "money", "check", "review"]

function action(raw) {
  if (!raw || typeof raw !== "object" || typeof raw.action !== "string") return null
  if (!/^[a-z_]{1,32}$/.test(raw.action)) return null
  return {
    action: raw.action,
    label: oneLine(raw.label || raw.action, 60),
    args: raw.args && typeof raw.args === "object" ? raw.args : null,
    client: raw.client === true
  }
}

function nowItem(raw) {
  if (!raw || typeof raw !== "object") return null
  var id = String(raw.id || "")
  if (!/^[a-z_]+:[^\s]{1,200}$/.test(id)) return null
  var kind = KINDS.indexOf(raw.kind) >= 0 ? raw.kind : "other"
  var secondary = Array.isArray(raw.secondary) ? raw.secondary.map(action).filter(function(a) { return !!a }).slice(0, 6) : []
  var ref = raw.ref && typeof raw.ref === "object" ? { type: oneLine(raw.ref.type, 32), id: oneLine(raw.ref.id, 200) } : null
  return {
    id: id, kind: kind, title: oneLine(raw.title, MAX_TITLE), source: oneLine(raw.source, 200),
    why: oneLine(raw.why, 200), primary: action(raw.primary), secondary: secondary, ref: ref
  }
}

function parseNow(text) {
  var data
  try { data = typeof text === "string" ? JSON.parse(text) : text } catch (e) { return null }
  if (!data || typeof data !== "object") return null
  var items = Array.isArray(data.items) ? data.items.map(nowItem).filter(function(x) { return !!x }).slice(0, MAX_ITEMS) : []
  var n = data.notifications && typeof data.notifications === "object" ? data.notifications : {}
  function count(v) { var x = parseInt(v, 10); return isFinite(x) && x > 0 ? Math.min(x, 99999) : 0 }
  return {
    lede: oneLine(data.lede, 300),
    items: items,
    notifications: { total: count(n.total), needsYou: count(n.needs_you) }
  }
}

// The session an approval card waits in: `approval:<session id>`.
function approvalSession(item) {
  if (!item || item.kind !== "approval") return ""
  if (item.ref && item.ref.type === "thread" && item.ref.id) return item.ref.id
  return item.id.indexOf("approval:") === 0 ? item.id.slice(9) : ""
}

// What a key does on a card: the first secondary action of that verb.
function secondaryOf(item, verb) {
  if (!item) return null
  for (var i = 0; i < item.secondary.length; i++)
    if (item.secondary[i].action === verb && !item.secondary[i].client) return item.secondary[i]
  return null
}

function errorKind(status) {
  if (status === 401 || status === 403) return "auth"
  if (status === 404) return "notFound"
  if (status === 409) return "busy"
  if (status === 0) return "unreachable"
  return "http"
}
