import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons as Commons
import "lib/Dana.js" as Dana

// The one long-lived owner of everything the plugin knows: the server and
// token, the shared conversation (`main`, the thread every device posts to),
// the run being followed, a screenshot waiting to be sent, and the Now queue.
// The overlay (Ask.qml) and the bar widget (BarWidget.qml) only draw it, so
// closing the overlay never stops anything: a run keeps streaming here and
// its answer arrives as a notification.
//
// Network: requests go only to the https origin in ~/.config/dana/omarchy.env
// with its bearer token. In the background, GET /api/now and GET /api/live
// every `refreshSeconds` (bar widget setting; 0 turns it off), and GET
// /api/live every 20 s while a live activity is running. Nothing else polls.
Item {
  id: root
  visible: false

  property var shell: null
  property string omarchyPath: Quickshell.env("OMARCHY_PATH") || ""
  property var manifest: null

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  // ---------- settings (pushed by the bar widget, which the shell hands them to) ----------
  property int refreshSeconds: 120
  property bool notifyWhenClosed: true

  function applySettings(values) {
    var v = values || ({})
    var r = parseInt(v.refreshSeconds, 10)
    root.refreshSeconds = isFinite(r) ? Math.max(0, Math.min(3600, r)) : 120
    root.notifyWhenClosed = v.notify !== false && v.notify !== "Off"
  }

  // ---------- configuration ----------
  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string configHome: Quickshell.env("XDG_CONFIG_HOME") || (home + "/.config")
  readonly property string envPath: configHome + "/dana/omarchy.env"
  readonly property string runtimeDir: (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/dana-omarchy"
  readonly property string glyph: String.fromCodePoint(0xF0AE2)

  property string url: ""
  property string token: ""
  property bool configFound: false
  readonly property bool ready: url !== "" && token !== ""
  // "" when ready, else what is wrong: missing | url | token.
  readonly property string setupProblem: !configFound ? "missing" : (url === "" ? "url" : (token === "" ? "token" : ""))

  FileView {
    id: envFile
    path: root.envPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      var env = Dana.parseEnv(text())
      root.configFound = true
      root.url = Dana.baseUrl(env.url)
      root.token = Dana.token(env.token)
    }
    onLoadFailed: { root.configFound = false; root.url = ""; root.token = "" }
  }

  // A file created after start isn't watched yet; look again now and then.
  Timer {
    interval: 10000
    repeat: true
    running: !root.ready
    onTriggered: envFile.reload()
  }

  // A new server or token: forget what the old one said and ask again.
  readonly property string connection: ready ? url + "\n" + token : ""
  onConnectionChanged: {
    abortStream()
    root.turns = []
    root.live = null
    root.pending = null
    root.now = null
    root.activities = []
    root.livePicks = ({})
    root.threadLoaded = false
    root.errorKind = ""
    root.nowErrorKind = ""
    if (ready) { loadThread(); refreshNow() }
  }

  // ---------- conversation state ----------
  property string sessionId: Dana.THREAD
  property var turns: []
  property var live: null          // the run being followed (Dana.newRun shape) or null
  property var pending: null       // what Dana waits for: approval | selection | iterations
  property string status: "idle"
  property string title: ""
  property string errorKind: ""    // auth | unreachable | busy | http | notFound | capture | captureTool | tooBig
  property real contextExpiresAt: 0
  property bool threadLoaded: false
  property double loadedAt: 0
  property double closedAt: 0
  property bool overlayOpen: false
  property string lastPrompt: ""
  readonly property bool working: live !== null && live.status === "running" && pending === null
  readonly property bool isThread: sessionId === Dana.THREAD

  onOverlayOpenChanged: if (!overlayOpen) closedAt = Date.now()

  // ---------- attachment ----------
  property string attachmentPath: ""
  property string attachmentData: ""
  property bool capturing: false

  // ---------- Now ----------
  property var now: null
  property bool nowLoading: false
  property string nowErrorKind: ""
  property string toastText: ""
  property string toastUndo: ""
  property bool toastError: false
  readonly property int needsYou: now ? now.items.length : 0

  // ---------- Live activities (server/docs/live.md) ----------
  property var activities: []       // Dana.parseLive shape, newest-updated first
  property var livePicks: ({})      // id → the choice picked here while its select request runs
  property var liveBusy: ({})       // id → true while a request about it runs
  property bool liveLoading: false
  readonly property bool liveRunning: activities.some(function(a) { return !a.ended })

  // ---------- HTTP ----------
  // Qt's XMLHttpRequest has no timeout: JSON requests are tracked and
  // aborted by the watchdog below. Streams are not; they end with the run.
  property var inflight: []
  property int inflightCount: 0

  function request(method, path, body, onOk, onFail) {
    if (!root.ready) { if (onFail) onFail("auth", null); return }
    var xhr = new XMLHttpRequest()
    var entry = { xhr: xhr, started: Date.now(), done: false, onFail: onFail }
    xhr.onreadystatechange = function() {
      if (xhr.readyState !== XMLHttpRequest.DONE || entry.done) return
      finish(entry)
      var text = xhr.responseText || ""
      var data = null
      try { data = text && text.length <= Dana.MAX_RESPONSE ? JSON.parse(text) : null } catch (e) { data = null }
      if (xhr.status >= 200 && xhr.status < 300) { if (onOk) onOk(data) }
      else if (onFail) onFail(Dana.errorKind(xhr.status), data)
    }
    xhr.open(method, root.url + path)
    xhr.setRequestHeader("Authorization", "Bearer " + root.token)
    xhr.setRequestHeader("Accept", "application/json")
    if (body !== null && body !== undefined) xhr.setRequestHeader("Content-Type", "application/json")
    inflight.push(entry)
    inflightCount = inflight.length
    xhr.send(body !== null && body !== undefined ? JSON.stringify(body) : null)
  }

  function finish(entry) {
    entry.done = true
    var i = inflight.indexOf(entry)
    if (i >= 0) inflight.splice(i, 1)
    inflightCount = inflight.length
  }

  Timer {
    interval: 1000
    repeat: true
    running: root.inflightCount > 0
    onTriggered: {
      var now = Date.now()
      var list = root.inflight.slice()
      for (var i = 0; i < list.length; i++) {
        if (now - list[i].started < 15000) continue
        root.finish(list[i])
        list[i].xhr.abort()
        if (list[i].onFail) list[i].onFail("unreachable", null)
      }
    }
  }

  // ---------- streams ----------
  property var streamXhr: null
  readonly property bool streaming: streamXhr !== null

  function abortStream() {
    var xhr = root.streamXhr
    root.streamXhr = null
    if (xhr) xhr.abort()
  }

  // Follow a run's events: POST a message or an answer, or GET the session's
  // stream to attach. Every frame goes through handleFrame; the closing
  // `state` frame replaces the live run with the stored conversation.
  function stream(method, path, body) {
    if (!root.ready) { root.errorKind = "auth"; return }
    abortStream()
    var xhr = new XMLHttpRequest()
    var reader = { pos: 0 }
    root.streamXhr = xhr
    xhr.onreadystatechange = function() {
      if (xhr !== root.streamXhr) return
      var st = xhr.readyState
      if ((st === XMLHttpRequest.LOADING || st === XMLHttpRequest.DONE) && xhr.status >= 200 && xhr.status < 300) {
        var frames = Dana.readFrames(xhr.responseText, reader)
        for (var i = 0; i < frames.length; i++) root.handleFrame(frames[i])
      }
      if (st !== XMLHttpRequest.DONE) return
      root.streamXhr = null
      if (xhr.status >= 200 && xhr.status < 300) {
        // The server closed without a state frame (a restart, a proxy): ask.
        if (root.live && root.live.status === "running") root.loadThread()
        return
      }
      if (xhr.status === 409) {
        // Still busy with an earlier request (maybe from the phone): follow that one.
        root.errorKind = "busy"
        root.live = null
        root.attach("", 0)
        return
      }
      root.live = null
      root.errorKind = Dana.errorKind(xhr.status)
    }
    xhr.open(method, root.url + path)
    xhr.setRequestHeader("Authorization", "Bearer " + root.token)
    xhr.setRequestHeader("Accept", "text/event-stream")
    if (body) xhr.setRequestHeader("Content-Type", "application/json")
    xhr.send(body ? JSON.stringify(body) : null)
  }

  function handleFrame(frame) {
    if (frame.type === "state") {
      var hadRun = root.live !== null
      root.live = null
      root.applyState(frame.state)
      if (hadRun) root.notifyIfAway()
      return
    }
    var run = root.live || Dana.newRun("")
    run = Dana.applyEvent(run, frame)
    root.live = run
    if (run.pending) root.pending = run.pending
  }

  property var lastState: null

  function applyState(state) {
    if (!state || typeof state !== "object") return
    if (state.session_id && String(state.session_id) !== root.sessionId) return
    root.lastState = state
    var turns = Dana.turnsFromState(state)
    root.turns = turns
    root.pending = Dana.pendingFromState(state, turns)
    root.status = String(state.status || "idle")
    root.title = Dana.oneLine(state.title, 120)
    root.contextExpiresAt = Number(state.context_expires_at) || 0
    root.threadLoaded = true
    root.loadedAt = Date.now()
    var run = state.active_run
    if (run && run.status === "running" && !root.streamXhr) root.attach(String(run.run_id || ""), 0)
  }

  // When the quiet gap runs out, the next message starts fresh: drop the
  // remembered turns then, not on the next reload.
  Timer {
    id: gapTimer
    readonly property real dueIn: root.contextExpiresAt > 0 ? root.contextExpiresAt * 1000 - Date.now() : -1
    interval: Math.max(1000, Math.min(dueIn + 500, 2147483000))
    running: root.contextExpiresAt > 0 && root.live === null && root.pending === null
    onTriggered: {
      var expired = root.lastState
      if (expired) root.turns = Dana.turnsFromState(expired)
      root.contextExpiresAt = 0
    }
  }

  function loadThread() {
    if (!root.ready || root.streamXhr) return
    var id = root.sessionId
    request("GET", "/api/sessions/" + encodeURIComponent(id) + (id === Dana.THREAD ? "?limit=60" : ""), null,
      function(data) { root.errorKind = ""; root.applyState(data) },
      function(kind) {
        if (kind === "notFound" && id !== Dana.THREAD) { root.openSession(Dana.THREAD); return }
        root.errorKind = kind
      })
  }

  // ---------- actions ----------
  function send(text) {
    var message = String(text || "").trim()
    if (!message && !root.attachmentData) return false
    if (!root.ready) { root.errorKind = "auth"; return false }
    if (root.working) { root.errorKind = "busy"; return false }
    if (!message) message = strings.t("ask.imageDefault")
    var body = { session_id: root.sessionId, message: message, device: Dana.DEVICE }
    if (root.attachmentData) body.images = [root.attachmentData]
    var run = Dana.newRun(message)
    run.image = !!root.attachmentData
    root.live = run
    root.pending = null
    root.errorKind = ""
    root.lastPrompt = message
    root.clearAttachment()
    stream("POST", "/api/assistant/message", body)
    return true
  }

  // approve | deny | extend_iterations | decline_iterations, or a list of
  // picked option indexes for a selection.
  function respond(answer) {
    if (!root.ready || !root.pending) return
    var body = { session_id: root.sessionId, device: Dana.DEVICE }
    if (Array.isArray(answer)) { body.action = "select"; body.selection = answer }
    else body.action = String(answer)
    root.live = Dana.newRun("")
    root.pending = null
    root.errorKind = ""
    stream("POST", "/api/assistant/approve", body)
  }

  function attach(runId, after) {
    var q = runId ? "?run_id=" + encodeURIComponent(runId) + "&after=" + (after || 0) : ""
    if (!root.live) root.live = Dana.newRun("")
    stream("GET", "/api/sessions/" + encodeURIComponent(root.sessionId) + "/stream" + q, null)
  }

  function stop() {
    if (!root.working) return
    request("POST", "/api/assistant/approve", { session_id: root.sessionId, action: "cancel" }, null,
      function(kind) { root.errorKind = kind })
  }

  // "New topic": Dana forgets the conversation so far (it stays on the phone
  // and the web). Off the thread, it goes back to the thread.
  function newTopic() {
    if (root.working) return
    root.clearAttachment()
    if (!root.isThread) { openSession(Dana.THREAD); return }
    request("POST", "/api/sessions/" + Dana.THREAD + "/clear", { device: Dana.DEVICE },
      function(data) { root.errorKind = ""; root.live = null; root.applyState(data) },
      function(kind) { root.errorKind = kind })
  }

  function openSession(id) {
    var target = String(id || Dana.THREAD)
    if (target === root.sessionId && root.threadLoaded) return
    abortStream()
    root.sessionId = target
    root.turns = []
    root.live = null
    root.pending = null
    root.title = ""
    root.threadLoaded = false
    root.errorKind = ""
    loadThread()
  }

  // Called when the overlay opens: come back to the thread after a while away
  // from a side conversation, and refresh what may have happened elsewhere.
  function ensureFresh() {
    if (!root.isThread && !root.working && !root.pending && root.closedAt > 0 && Date.now() - root.closedAt > 120000) {
      openSession(Dana.THREAD)
      return
    }
    if (!root.streamXhr && (!root.threadLoaded || Date.now() - root.loadedAt > 10000)) loadThread()
  }

  function openWeb() {
    if (root.url) Qt.openUrlExternally(root.url + (root.isThread ? "/#chat" : "/"))
  }

  // ---------- notifications ----------
  function notify(headline, body) {
    var bin = root.omarchyPath ? root.omarchyPath + "/bin/omarchy-notification-send" : "omarchy-notification-send"
    Quickshell.execDetached([bin, "--app-name", "Dana", "-g", root.glyph, "-t", "12000", headline, body,
                             "--exec", "omarchy-shell", "shell", "summon", "dana", "{}"])
  }

  function notifyIfAway() {
    if (root.overlayOpen || !root.notifyWhenClosed) return
    if (root.pending) {
      var what = root.pending.kind === "approval" && root.pending.title ? root.pending.title : ""
      notify(strings.t("notify.waiting"), what)
      return
    }
    var last = root.turns.length ? root.turns[root.turns.length - 1] : null
    if (last && last.answer) notify(strings.t("notify.answered"), Dana.summary(last.answer, 180))
  }

  // ---------- screenshot / clipboard image ----------
  function clearAttachment() {
    root.attachmentPath = ""
    root.attachmentData = ""
  }

  // Pick a region the way Omarchy's screenshots do (omarchy-capture-region:
  // the screen frozen, a click snaps to a window, a drag picks freely), or
  // with plain slurp where that isn't there, then grab it with grim.
  // stdin is /dev/null on purpose: slurp reads boxes from a stdin that isn't
  // a terminal until it closes, and the Process pipe never does, so the
  // picker would wait without ever showing.
  // Exit codes: 1 cancelled (nothing to say), 2 failed, 127 a tool missing.
  // `done(ok)` runs after the image is ready (or the pick was cancelled).
  function captureRegion(done) {
    if (root.capturing) return
    root.capturing = true
    root.errorKind = ""
    var path = root.runtimeDir + "/shot-" + Date.now() + ".jpg"
    captureProc.done = done || null
    captureProc.target = path
    captureProc.mime = "image/jpeg"
    captureProc.command = ["sh", "-c",
      "command -v grim >/dev/null || exit 127\n"
      + "mkdir -p \"${1%/*}\" && chmod 700 \"${1%/*}\" || exit 2\n"
      + "if command -v omarchy-capture-region >/dev/null; then g=$(omarchy-capture-region smart </dev/null 2>/dev/null)\n"
      + "elif command -v slurp >/dev/null; then g=$(slurp </dev/null 2>/dev/null)\n"
      + "else exit 127; fi\n"
      + "[ -n \"$g\" ] || exit 1\n"
      + "grim -g \"$g\" -t jpeg -q 85 \"$1\" </dev/null || exit 2", "sh", path]
    captureProc.running = true
  }

  // An image on the clipboard (a screenshot copied with Omarchy's capture).
  // Silent when there is none: the text field pastes text as usual.
  function pasteImage() {
    if (root.capturing) return
    root.capturing = true
    var path = root.runtimeDir + "/paste-" + Date.now()
    captureProc.done = null
    captureProc.target = path
    captureProc.mime = ""
    captureProc.command = ["sh", "-c",
      "command -v wl-paste >/dev/null || exit 3\n"
      + "t=$(wl-paste --list-types 2>/dev/null | grep -m1 -xE 'image/(png|jpeg)') || exit 3\n"
      + "mkdir -p \"${1%/*}\" && chmod 700 \"${1%/*}\" || exit 2\n"
      + "wl-paste -t \"$t\" > \"$1\" && printf %s \"$t\"", "sh", path]
    captureProc.running = true
  }

  Process {
    id: captureProc
    property var done: null
    property string target: ""
    property string mime: ""
    stdout: StdioCollector { id: captureOut }
    onExited: function(code) {
      var done = captureProc.done
      if (code === 0) {
        var mime = captureProc.mime || String(captureOut.text || "").trim()
        root.encode(captureProc.target, mime, done)
        return
      }
      root.capturing = false
      if (code === 127) root.errorKind = "captureTool"
      else if (code !== 1 && code !== 3) root.errorKind = "capture"
      if (done) done(false)
    }
  }

  function encode(path, mime, done) {
    encodeProc.done = done || null
    encodeProc.target = path
    encodeProc.mime = mime
    encodeProc.command = ["base64", "-w0", path]
    encodeProc.running = true
  }

  Process {
    id: encodeProc
    property var done: null
    property string target: ""
    property string mime: ""
    stdout: StdioCollector { id: encodeOut }
    onExited: function(code) {
      root.capturing = false
      var data = String(encodeOut.text || "").trim()
      var ok = code === 0 && data.length > 0 && data.length <= 12 * 1024 * 1024
      if (ok) {
        root.attachmentPath = encodeProc.target
        root.attachmentData = "data:" + encodeProc.mime + ";base64," + data
        root.errorKind = ""
      } else {
        root.errorKind = data.length > 12 * 1024 * 1024 ? "tooBig" : "capture"
      }
      if (encodeProc.done) encodeProc.done(ok)
    }
  }

  // ---------- clipboard ----------
  function copy(text) {
    var value = String(text || "")
    if (!value) return
    copyProc.pendingText = value
    copyProc.running = true
  }

  Process {
    id: copyProc
    property string pendingText: ""
    command: ["wl-copy"]
    stdinEnabled: true
    onStarted: { write(pendingText); stdinEnabled = false }
    onExited: { stdinEnabled = true }
  }

  // ---------- Now ----------
  Timer {
    interval: Math.max(30, root.refreshSeconds) * 1000
    repeat: true
    running: root.ready && root.refreshSeconds > 0
    onTriggered: root.refreshNow()
  }

  function refreshNow() {
    refreshLive()
    if (!root.ready || root.nowLoading) return
    root.nowLoading = true
    request("GET", "/api/now", null, function(data) {
      root.nowLoading = false
      var parsed = Dana.parseNow(data)
      if (parsed) { root.now = parsed; root.nowErrorKind = "" }
      else root.nowErrorKind = "http"
    }, function(kind) {
      root.nowLoading = false
      root.nowErrorKind = kind
    })
  }

  // ---------- Live ----------
  Timer {
    interval: 20000
    repeat: true
    running: root.ready && root.liveRunning
    onTriggered: root.refreshLive()
  }

  function refreshLive() {
    if (!root.ready || root.liveLoading) return
    root.liveLoading = true
    request("GET", "/api/live", null, function(data) {
      root.liveLoading = false
      var parsed = Dana.parseLive(data)
      if (parsed) root.activities = parsed
    }, function(kind) {
      root.liveLoading = false     // keep what is shown (a server before live activities answers 404)
    })
  }

  function setPick(id, key) {
    var picks = {}
    for (var k in root.livePicks) if (k !== id) picks[k] = root.livePicks[k]
    if (key) picks[id] = key
    root.livePicks = picks
  }

  function setBusy(id, on) {
    var busy = {}
    for (var k in root.liveBusy) if (k !== id) busy[k] = root.liveBusy[k]
    if (on) busy[id] = true
    root.liveBusy = busy
  }

  function liveReply(id, data) {
    root.activities = Dana.liveApply(root.activities, id, data ? data.activity : null)
  }

  function liveFailed(kind, data) {
    root.toast(Dana.oneLine(data && (data.message || data.error), 160) || strings.t("error." + kind), "", true)
    root.refreshLive()
  }

  // Next / Earlier: shown at once, then stored on the server.
  function liveSelect(a, dir) {
    if (!a) return
    var key = Dana.liveStep(a, root.livePicks[a.id], dir)
    if (!key) return
    setPick(a.id, key)
    request("POST", Dana.livePath(a.id, "/select"), { choice: key }, function(data) {
      if (root.livePicks[a.id] !== key) return
      root.liveReply(a.id, data)
      root.setPick(a.id, "")
    }, function(kind, data) {
      if (root.livePicks[a.id] !== key) return
      root.setPick(a.id, "")
      root.liveFailed(kind, data)
    })
  }

  function liveAct(a, actionId) {
    if (!a || root.liveBusy[a.id]) return
    var body = { action: actionId }
    var key = Dana.liveView(a, root.livePicks[a.id]).key
    if (key) body.choice = key
    setBusy(a.id, true)
    request("POST", Dana.livePath(a.id, "/act"), body, function(data) {
      root.setBusy(a.id, false)
      root.liveReply(a.id, data)
    }, function(kind, data) {
      root.setBusy(a.id, false)
      root.liveFailed(kind, data)
    })
  }

  // Stop (or dismiss an ended one kept for its final).
  function liveStop(a) {
    if (!a || root.liveBusy[a.id]) return
    setBusy(a.id, true)
    request("DELETE", Dana.livePath(a.id), null, function() {
      root.setBusy(a.id, false)
      root.liveReply(a.id, null)
    }, function(kind, data) {
      root.setBusy(a.id, false)
      if (kind === "notFound") root.liveReply(a.id, null)
      else root.liveFailed(kind, data)
    })
  }

  function liveOpen(a) {
    if (!a) return
    var target = Dana.liveOpenUrl(root.url, Dana.liveView(a, root.livePicks[a.id]).open)
    Qt.openUrlExternally(target || root.url + "/")
  }

  function act(item, action) {
    if (!item || !action || action.client) return
    var body = { id: item.id, action: action.action }
    if (action.args) body.args = action.args
    request("POST", "/api/now/act", body, function(data) {
      root.toast(Dana.oneLine(data && data.message, 160) || strings.t("now.done"), data && data.undo_id ? String(data.undo_id) : "", false)
      root.refreshNow()
    }, function(kind, data) {
      root.toast(Dana.oneLine(data && data.error, 160) || strings.t("error." + kind), "", true)
      root.refreshNow()
    })
  }

  function undo() {
    var id = root.toastUndo
    if (!id) return
    root.toastUndo = ""
    request("POST", "/api/undo/" + encodeURIComponent(id), null, function(data) {
      root.toast(strings.t("now.undone"), "", false)
      root.refreshNow()
    }, function(kind, data) {
      root.toast(Dana.oneLine(data && data.error, 160) || strings.t("now.undoFailed"), "", true)
    })
  }

  function toast(text, undoId, isError) {
    root.toastText = text
    root.toastUndo = undoId
    root.toastError = isError
    toastTimer.restart()
  }

  Timer {
    id: toastTimer
    interval: 9000
    onTriggered: { root.toastText = ""; root.toastUndo = ""; root.toastError = false }
  }
}
