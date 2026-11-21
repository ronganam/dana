package app.ganam.assistant.ui.viewmodels

import android.annotation.SuppressLint
import android.content.Context
import android.content.SharedPreferences
import android.media.AudioFormat
import android.media.AudioRecord
import app.ganam.assistant.data.voice.PcmStream
import app.ganam.assistant.data.voice.VoiceInput
import android.media.MediaRecorder
import android.net.Uri
import android.util.Log
import app.ganam.assistant.data.chat.ChatHistoryCodec
import app.ganam.assistant.data.chat.ChatHistoryStore
import app.ganam.assistant.data.chat.ThreadModel
import app.ganam.assistant.data.chat.UndoItem
import app.ganam.assistant.data.network.ApiException
import app.ganam.assistant.data.network.ApiService
import app.ganam.assistant.data.models.ChatMessage
import app.ganam.assistant.data.models.ModelOption
import app.ganam.assistant.data.models.PendingExtension
import app.ganam.assistant.data.models.SelectionOption
import app.ganam.assistant.data.models.SelectionRequest
import app.ganam.assistant.data.models.ToolCallEntry
import app.ganam.assistant.data.network.HttpClients
import app.ganam.assistant.data.network.bearerAuth
import app.ganam.assistant.data.network.Reachability
import app.ganam.assistant.data.network.ServerStatusMonitor
import app.ganam.assistant.data.network.SessionBusyException
import app.ganam.assistant.data.voice.DeviceSpeech
import app.ganam.assistant.data.screen.OutgoingScreen
import app.ganam.assistant.data.screen.ScreenAttachments
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.UUID

data class SessionItem(
    val id: String,
    val timestamp: Long,
    val snippet: String,
    val status: String = "idle",
    val title: String = ""
)

data class PendingApproval(
    val toolName: String,
    val toolArgs: String,
    val toolCallId: String
)

/**
 * The conversation: the live SSE assistant stream, push-to-talk voice over
 * WebSocket, and the local transcript cache.
 *
 * **Thread mode** (the new main screen, the power-button sheet, kiosk and
 * TV): one conversation shared by every device, `session_id: "main"`
 * (server `docs/agent-actions.md`). It loads newest page first
 * (`GET /api/sessions/main`, older pages with [loadOlder]), stamps what it
 * sends with [device] and the on-screen item ([screenProvider]), keeps the
 * latest run's Undo buttons ([undos]) and marks replies that came from a
 * learned phrase. Settings → "Previous layout" turns it off: per-session
 * chats with a session list, as before.
 *
 * Sending and speaking check [ServerStatusMonitor] first, so the user isn't
 * left typing or talking into a dead connection while the VPN is off.
 */
class ChatController(
    private val ctx: ControllerContext,
    private val appContext: Context,
    private val prefs: SharedPreferences,
    private val serverUrl: () -> String,
    private val apiToken: () -> String,
    /** Called after the active session changes (new or switched). */
    private val onSessionChanged: () -> Unit,
    /** Ends a realtime call if one is running (voice and session switches need the mic/session). */
    private val endRealtimeCall: () -> Unit,
    /** What this app is in the shared thread: `phone`, `tablet`, `tv`. */
    private val device: String = "phone",
    /** A run that used tools, or an Undo, may have changed what other screens show. */
    private val onThingsChanged: () -> Unit = {}
) {
    private val store = ChatHistoryStore(appContext, ctx.scope)

    var sessionId: String = ""
        private set

    private val _currentSessionId = MutableStateFlow("")
    val currentSessionId: StateFlow<String> = _currentSessionId.asStateFlow()

    private val _chatMessages = MutableStateFlow<List<ChatMessage>>(emptyList())
    val chatMessages: StateFlow<List<ChatMessage>> = _chatMessages.asStateFlow()

    private val _sessions = MutableStateFlow<List<SessionItem>>(emptyList())
    val sessions: StateFlow<List<SessionItem>> = _sessions.asStateFlow()

    private val _pendingApproval = MutableStateFlow<PendingApproval?>(null)
    val pendingApproval: StateFlow<PendingApproval?> = _pendingApproval.asStateFlow()

    private val _selectionRequest = MutableStateFlow<SelectionRequest?>(null)
    val selectionRequest: StateFlow<SelectionRequest?> = _selectionRequest.asStateFlow()

    private val _pendingExtension = MutableStateFlow<PendingExtension?>(null)
    val pendingExtension: StateFlow<PendingExtension?> = _pendingExtension.asStateFlow()

    private val _sessionContext = MutableStateFlow<app.ganam.assistant.data.models.ChatContext?>(null)
    /** What the current chat was opened from (call suggestion / call / task), if anything. */
    val sessionContext: StateFlow<app.ganam.assistant.data.models.ChatContext?> = _sessionContext.asStateFlow()

    private val _isStreaming = MutableStateFlow(false)
    val isStreaming: StateFlow<Boolean> = _isStreaming.asStateFlow()

    private val _isRecording = MutableStateFlow(false)
    val isRecording: StateFlow<Boolean> = _isRecording.asStateFlow()

    private val _chatStatusText = MutableStateFlow("")
    val chatStatusText: StateFlow<String> = _chatStatusText.asStateFlow()

    private val _pendingImageUri = MutableStateFlow<Uri?>(null)
    val pendingImageUri: StateFlow<Uri?> = _pendingImageUri.asStateFlow()

    private val _models = MutableStateFlow<List<ModelOption>>(emptyList())
    /** Chat models the server offers (empty until fetched). */
    val models: StateFlow<List<ModelOption>> = _models.asStateFlow()

    private val _chatModel = MutableStateFlow(prefs.getString(KEY_CHAT_MODEL, "").orEmpty())
    /** Selected chat model id; empty means "server default". */
    val chatModel: StateFlow<String> = _chatModel.asStateFlow()

    private var streamJob: Job? = null
    private var loadHistoryJob: Job? = null

    // --- The shared thread ---

    private var initialized = false
    private var threadMode = false

    /** Whether this controller talks in the shared thread (false = "Previous layout" sessions). */
    val isThreadMode: Boolean get() = threadMode

    private val _inThread = MutableStateFlow(false)
    /** The shared thread is shown (false while another session is open, e.g. an approval from Telegram). */
    val inThread: StateFlow<Boolean> = _inThread.asStateFlow()

    private val _gapMinutes = MutableStateFlow(DEFAULT_GAP_MINUTES)
    /** Quiet longer than this starts a new conversation (the server's `gap_minutes`). */
    val gapMinutes: StateFlow<Double> = _gapMinutes.asStateFlow()

    private val _hasOlder = MutableStateFlow(false)
    /** The server has older thread messages than the ones shown. */
    val hasOlder: StateFlow<Boolean> = _hasOlder.asStateFlow()
    private var olderCursor: String? = null

    private val _loadingOlder = MutableStateFlow(false)
    val loadingOlder: StateFlow<Boolean> = _loadingOlder.asStateFlow()

    private val _memory = MutableStateFlow<ThreadModel.Memory>(ThreadModel.Memory.Unknown)
    /** Which thread messages Dana still has in mind (server `context_from_seq` / `context_expires_at`). */
    val memory: StateFlow<ThreadModel.Memory> = _memory.asStateFlow()

    private val _clearing = MutableStateFlow(false)
    /** "New topic" is on its way to the server. */
    val clearing: StateFlow<Boolean> = _clearing.asStateFlow()

    private val _undos = MutableStateFlow<List<UndoItem>>(emptyList())
    /** Undo buttons of the latest run (and the outcome of pressed ones). */
    val undos: StateFlow<List<UndoItem>> = _undos.asStateFlow()

    /** What is on screen while the owner asks (set by the main screen; null elsewhere). */
    @Volatile var screenProvider: () -> app.ganam.assistant.ui.viewmodels.ScreenRef? = { null }

    /**
     * The app under the power-button sheet, when the owner included it with
     * the "Screen" control (set by the sheet only; null in the app, kiosk, TV).
     * Taken once per message — typed, live-transcribed or server-transcribed.
     */
    @Volatile var screenAttachments: ScreenAttachments? = null

    /** The screen taken for the utterance on the voice socket, until its transcription arrives. */
    private var voiceScreen: OutgoingScreen? = null

    /**
     * Thread messages older than this (epoch ms) stay hidden: a "fresh" view
     * of the thread (the power-button sheet, the kiosk after a pause) shows
     * only what is said from now on, while the server keeps one thread.
     */
    private var viewFloorMs = 0L

    /** A tool ran in the current run: other screens may need a refresh when it ends. */
    @Volatile private var runChanged = false
    /** The message was refused because another run is going (409): follow that one after this stream ends. */
    @Volatile private var followBusyRun = false

    private fun inThreadNow(): Boolean = threadMode && sessionId == ThreadModel.MAIN

    /** `device` to send: only in thread mode (the old per-session chats never sent one). */
    private fun deviceParam(): String? = device.takeIf { threadMode }

    /**
     * Bookkeeping for the run being rendered. Events carry `run_id` + `seq`,
     * so a reattached stream that replays frames we already have is ignored.
     */
    private class RunTracker {
        var runId: String? = null
        var lastSeq = 0
        var modelLabel: String? = null
        var gotState = false
        /** LLM step (iteration) -> id of the live reply bubble for that step. */
        val stepBubbles = HashMap<Int, String>()
    }

    @Volatile private var run = RunTracker()

    private var audioRecord: AudioRecord? = null
    @Volatile private var pcmStream: PcmStream? = null
    private var recordingJob: Job? = null
    private var voiceGateJob: Job? = null
    private var voiceVerifyJob: Job? = null
    private var currentWebSocket: WebSocket? = null
    private var hasSpokenInCurrentSession = false

    /** Live on-device transcription for the current utterance, if that path is in use. */
    private var deviceSpeech: DeviceSpeech? = null
    /** The user bubble that shows words as they are recognised. */
    private var liveBubbleId: String? = null
    private var deviceStopJob: Job? = null

    fun initialize() {
        store.migrateFromPrefs(prefs)
        initialized = true
        threadMode = !prefs.getBoolean(KEY_PREVIOUS_LAYOUT, false)
        sessionId = if (threadMode) ThreadModel.MAIN else savedSessionId()
        _currentSessionId.value = sessionId
        _inThread.value = inThreadNow()
        if (threadMode) store.load(ThreadModel.MAIN)?.let { _chatMessages.value = it }
        loadActiveSessionHistory()
        fetchSessions()
        fetchModels()
    }

    /** The "Previous layout" chat's session id (kept apart from the thread). */
    private fun savedSessionId(): String {
        var id = prefs.getString(KEY_SESSION_ID, "").orEmpty()
        if (id.isEmpty() || id == ThreadModel.MAIN) {
            id = UUID.randomUUID().toString()
            prefs.edit().putString(KEY_SESSION_ID, id).apply()
        }
        return id
    }

    /**
     * Switch between the shared thread and the old per-session chats (Settings
     * → "Previous layout"). A no-op before [initialize], which reads the
     * setting itself.
     */
    fun setThreadMode(on: Boolean) {
        if (!initialized || on == threadMode) return
        detachFromSession()
        threadMode = on
        viewFloorMs = 0L
        sessionId = if (on) ThreadModel.MAIN else savedSessionId()
        _currentSessionId.value = sessionId
        _inThread.value = inThreadNow()
        _chatMessages.value = if (on) store.load(ThreadModel.MAIN).orEmpty() else emptyList()
        _undos.value = emptyList()
        _memory.value = ThreadModel.Memory.Unknown
        _sessionContext.value = null
        _chatStatusText.value = ""
        loadActiveSessionHistory()
        fetchSessions()
        onSessionChanged()
    }

    /** Back to the shared thread after looking at another conversation. */
    fun returnToThread() {
        if (threadMode && sessionId != ThreadModel.MAIN) loadSession(ThreadModel.MAIN)
    }

    // --- Model selection ---

    fun fetchModels() {
        ctx.scope.launch {
            val json = ctx.load("Models") { JSONObject(ctx.api.getAssistantModels()) } ?: return@launch
            val arr = json.optJSONArray("models") ?: return@launch
            val options = (0 until arr.length()).mapNotNull { i ->
                val o = arr.optJSONObject(i) ?: return@mapNotNull null
                ModelOption(
                    id = o.optString("id"),
                    label = o.optString("label", o.optString("id")),
                    description = o.optString("description"),
                    showsReasoning = o.optBoolean("shows_reasoning", false)
                )
            }.filter { it.id.isNotEmpty() }
            _models.value = options
            if (options.none { it.id == _chatModel.value }) {
                _chatModel.value = json.optString("default").ifEmpty { options.firstOrNull()?.id.orEmpty() }
            }
        }
    }

    fun setChatModel(id: String) {
        _chatModel.value = id
        prefs.edit().putString(KEY_CHAT_MODEL, id).apply()
    }

    private fun selectedModel(): String? = _chatModel.value.ifEmpty { null }

    fun setStatusText(text: String) {
        _chatStatusText.value = text
    }

    // --- Local history ---

    fun appendMessage(sender: String, message: String, isUser: Boolean, images: List<String> = emptyList()) {
        appendAndGetId(sender, message, isUser, images)
    }

    /** Append a message (user messages get this device's time + name, for the thread's separators). */
    private fun appendAndGetId(sender: String, message: String, isUser: Boolean, images: List<String> = emptyList()): String {
        val msg = ChatMessage(
            sender = sender, message = message, isUser = isUser, images = images,
            timestamp = if (isUser) System.currentTimeMillis() else null,
            device = if (isUser && threadMode) device else ""
        )
        val list = _chatMessages.value + msg
        _chatMessages.value = list
        persistMessages(list)
        return msg.id
    }

    private fun showThinkingIndicator(label: String = "") {
        val full = listOfNotNull(run.modelLabel, label.ifEmpty { null }).joinToString(" · ")
        val current = _chatMessages.value.filter { !it.isThinking }
        _chatMessages.value = current +
            ChatMessage(sender = "Assistant", message = "", isUser = false, isThinking = true, thinkingLabel = full)
    }

    private fun removeThinkingIndicator() {
        val current = _chatMessages.value
        if (current.any { it.isThinking }) _chatMessages.value = current.filter { !it.isThinking }
    }

    /** Add a tool-call card, collapsing the previous expanded one. */
    private fun addToolCall(entry: ToolCallEntry) {
        val current = _chatMessages.value.toMutableList()
        val lastToolIdx = current.indexOfLast { it.toolCall != null && !it.toolCall.isCollapsed }
        if (lastToolIdx >= 0) {
            val prev = current[lastToolIdx]
            current[lastToolIdx] = prev.copy(toolCall = prev.toolCall!!.copy(isCollapsed = true))
        }
        current.add(ChatMessage(sender = "Tool", message = "", isUser = false, toolCall = entry))
        _chatMessages.value = current
        persistMessagesThrottled()
    }

    /** Mutate a tool-call card in place (start -> end, approval granted, ...). */
    private fun updateToolCall(toolCallId: String, transform: (ToolCallEntry) -> ToolCallEntry) {
        if (toolCallId.isEmpty()) return
        val current = _chatMessages.value.toMutableList()
        val idx = current.indexOfLast { it.toolCall?.toolCallId == toolCallId }
        if (idx < 0) return
        val msg = current[idx]
        val existing = msg.toolCall ?: return
        current[idx] = msg.copy(toolCall = transform(existing))
        _chatMessages.value = current
        persistMessagesThrottled()
    }

    /** Clear the approval prompt on every card that is showing one. */
    private fun updateToolCallRequiringApproval(approved: Boolean) {
        val current = _chatMessages.value.toMutableList()
        var changed = false
        for (i in current.indices) {
            val tc = current[i].toolCall
            if (tc != null && tc.requiresApproval) {
                current[i] = current[i].copy(
                    toolCall = tc.copy(requiresApproval = false, isRunning = approved, succeeded = if (approved) null else false)
                )
                changed = true
            }
        }
        if (changed) {
            _chatMessages.value = current
            persistMessagesThrottled()
        }
    }

    fun toggleToolCallCollapse(messageId: String) {
        val current = _chatMessages.value.toMutableList()
        val idx = current.indexOfFirst { it.id == messageId }
        if (idx < 0) return
        val msg = current[idx]
        val tc = msg.toolCall ?: return
        current[idx] = msg.copy(toolCall = tc.copy(isCollapsed = !tc.isCollapsed))
        _chatMessages.value = current
        persistMessages(current)
    }

    private fun persistMessages(list: List<ChatMessage>) {
        // The thread can be long; the cache only needs the recent part.
        store.save(sessionId, if (inThreadNow()) list.takeLast(THREAD_CACHE_MESSAGES) else list)
        prefs.edit().putLong(KEY_LAST_ACTIVE, System.currentTimeMillis()).apply()
    }

    private var persistPending = false
    private var persistJob: Job? = null

    /** Coalesce streaming updates into at most one write per second. */
    private fun persistMessagesThrottled() {
        if (persistJob?.isActive == true) {
            persistPending = true
            return
        }
        persistMessages(_chatMessages.value)
        persistPending = false
        persistJob = ctx.scope.launch {
            while (persistPending) {
                delay(1000)
                persistPending = false
                persistMessages(_chatMessages.value)
            }
        }
    }

    /** Write any coalesced pending state immediately. */
    private fun flushPendingPersist() {
        if (persistPending) {
            persistPending = false
            persistJob?.cancel()
            persistJob = null
            persistMessages(_chatMessages.value)
        }
    }

    // --- Sessions ---

    fun fetchSessions() {
        // The shared thread has no session list.
        if (threadMode) return
        ctx.scope.launch {
            val fromServer = ctx.load("Sessions") { parseSessions(ctx.api.getSessions()) }
            _sessions.value = fromServer ?: withContext(Dispatchers.IO) {
                // Offline: offer the transcripts cached on this device.
                store.listSessions().map {
                    SessionItem(it.id, it.lastModified, it.snippet, status = "idle", title = "Offline copy")
                }
            }
        }
    }

    private fun parseSessions(json: String): List<SessionItem> {
        val arr = JSONArray(json)
        return (0 until arr.length()).map { i ->
            val obj = arr.getJSONObject(i)
            SessionItem(
                id = obj.optString("session_id"),
                timestamp = parseServerTimestamp(obj.optString("updated_at")) ?: 0L,
                snippet = obj.optString("snippet", "Empty chat"),
                status = obj.optString("status", "idle"),
                title = obj.optString("title", "New Chat")
            )
        }
    }

    /** Detach from everything tied to the current session before switching. */
    private fun detachFromSession() {
        endRealtimeCall()
        if (_isRecording.value || voiceGateJob?.isActive == true || deviceSpeech != null) {
            abortVoice(statusText = "")
        }
        loadHistoryJob?.cancel()
        loadHistoryJob = null
        // The server keeps running the task; we only stop rendering it.
        streamJob?.cancel()
        streamJob = null
        _isStreaming.value = false
        flushPendingPersist()
        _pendingApproval.value = null
        _selectionRequest.value = null
        _pendingExtension.value = null
    }

    /**
     * A new conversation. In thread mode there is only one, so this starts a
     * fresh *view* of it (the power-button sheet and the kiosk after a pause):
     * nothing older than now is shown, the server keeps the thread.
     */
    fun startNewSession() {
        if (threadMode) {
            detachFromSession()
            sessionId = ThreadModel.MAIN
            _currentSessionId.value = sessionId
            _inThread.value = true
            _chatMessages.value = emptyList()
            _undos.value = emptyList()
            _chatStatusText.value = ""
            _sessionContext.value = null
            _hasOlder.value = false
            viewFloorMs = System.currentTimeMillis() - VIEW_FLOOR_MARGIN_MS
            prefs.edit().putLong(KEY_LAST_ACTIVE, System.currentTimeMillis()).apply()
            onSessionChanged()
            return
        }
        detachFromSession()
        sessionId = UUID.randomUUID().toString()
        _currentSessionId.value = sessionId
        prefs.edit()
            .putString(KEY_SESSION_ID, sessionId)
            .putLong(KEY_LAST_ACTIVE, System.currentTimeMillis())
            .apply()
        _chatMessages.value = emptyList()
        _chatStatusText.value = ""
        _sessionContext.value = null

        val newId = sessionId
        ctx.scope.launch {
            ctx.load("CreateSession") { ctx.api.createSession(newId, "New Chat") }
            fetchSessions()
            onSessionChanged()
        }
    }

    /**
     * Show [targetSessionId]. In thread mode another id is only a visit (an
     * approval waiting in a Telegram chat, say): it isn't remembered, and
     * [returnToThread] goes back.
     */
    fun loadSession(targetSessionId: String) {
        detachFromSession()
        sessionId = targetSessionId
        _currentSessionId.value = sessionId
        _inThread.value = inThreadNow()
        if (!threadMode) prefs.edit().putString(KEY_SESSION_ID, sessionId).apply()
        viewFloorMs = 0L
        _undos.value = emptyList()
        _chatMessages.value = if (inThreadNow()) store.load(ThreadModel.MAIN).orEmpty() else emptyList()
        _sessionContext.value = null
        loadActiveSessionHistory()
        onSessionChanged()
    }

    /** One GET of what is shown: the newest thread page, or the whole session. */
    private suspend fun fetchShown(id: String): JSONObject =
        JSONObject(if (threadMode && id == ThreadModel.MAIN) ctx.api.getThread(THREAD_PAGE) else ctx.api.getSession(id))

    private fun loadActiveSessionHistory() {
        loadHistoryJob?.cancel()
        showThinkingIndicator()

        val id = sessionId
        loadHistoryJob = ctx.scope.launch {
            try {
                parseAndSetSessionDetail(fetchShown(id))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.w(TAG, "Loading session from server failed; using local copy: ${describeError(e)}")
                removeThinkingIndicator()
                val cached = withContext(Dispatchers.IO) { store.load(id) }
                if (cached != null && sessionId == id) _chatMessages.value = cached
            }
        }
    }

    private fun parseAndSetSessionDetail(json: JSONObject) {
        val status = json.optString("status", "idle")
        if (json.has("context")) _sessionContext.value = app.ganam.assistant.data.network.CallJson.chatContext(json.optJSONObject("context"))
        val serverMessages = ChatHistoryCodec.decode(json.optJSONArray("messages") ?: JSONArray())

        if (inThreadNow()) {
            applyThreadPage(json, serverMessages)
        } else if (serverMessages.isNotEmpty() || _chatMessages.value.isEmpty()) {
            // Only replace local messages if the server returned some. POST-style
            // responses carry no messages array and must not wipe the chat.
            _chatMessages.value = serverMessages
        }
        if (json.has("undos")) reconcileUndos(json)

        val pendingObj = json.optJSONObject("pending_approval")
        _pendingApproval.value = pendingObj?.let {
            PendingApproval(
                toolName = it.optString("tool_name"),
                toolArgs = it.optString("tool_args"),
                toolCallId = it.optString("tool_call_id")
            )
        }
        pendingObj?.let { p ->
            val reason = approvalReasonOf(p)
            val id = p.optString("tool_call_id")
            if (reason.isNotEmpty() && id.isNotEmpty()) updateToolCall(id) { it.copy(approvalReason = reason) }
        }

        val selectionObj = json.optJSONObject("selection_request")
        if (selectionObj != null) {
            _pendingApproval.value = null
            _selectionRequest.value = selectionObj.toSelectionRequest()
        } else {
            _selectionRequest.value = null
        }

        val activeRun = json.optJSONObject("active_run")
        if (status == "running" && activeRun != null) {
            // A run is in flight but this client isn't attached to its stream
            // (reload, app resumed, session switch): replay it and follow live.
            if (!_isStreaming.value) attachToRun(activeRun.optString("run_id"))
        } else if (!_isStreaming.value) {
            removeThinkingIndicator()
            _chatStatusText.value = ""
        }

        if (serverMessages.isNotEmpty() && !inThreadNow()) {
            store.save(sessionId, serverMessages)
        }
    }

    /**
     * The newest thread page (from a GET or a run's closing `state`) over
     * what is shown: stored messages replace live bubbles, older pages stay.
     */
    private fun applyThreadPage(json: JSONObject, page: List<ChatMessage>) {
        applyMemory(json)
        json.optDouble("gap_minutes").takeIf { !it.isNaN() && it > 0 }?.let { _gapMinutes.value = it }
        val floor = viewFloorMs
        val visible = if (floor > 0) page.filter { (it.timestamp ?: Long.MAX_VALUE) >= floor } else page
        val current = _chatMessages.value.filter { !it.isThinking }
        var merged = ThreadModel.mergeNewest(current, visible)
        if (json.optBoolean("shortcut", false)) {
            val i = merged.indexOfLast { !it.isUser && it.toolCall == null && it.message.isNotBlank() }
            if (i >= 0 && !merged[i].shortcut) merged = merged.toMutableList().also { it[i] = it[i].copy(shortcut = true) }
        }
        // The cursor belongs to this page; keep ours when older pages are already shown.
        if (floor > 0) {
            _hasOlder.value = false
        } else if (visible.isNotEmpty() && merged.firstOrNull()?.id == visible.first().id) {
            _hasOlder.value = json.optBoolean("has_more", false)
            olderCursor = json.opt("before")?.takeIf { it != JSONObject.NULL }?.toString()?.takeIf { it.isNotBlank() && it != "null" }
        }
        val thinking = _chatMessages.value.filter { it.isThinking }
        _chatMessages.value = merged + thinking
        if (merged.isNotEmpty()) store.save(ThreadModel.MAIN, merged.takeLast(THREAD_CACHE_MESSAGES))
    }

    /** The thread state's memory fields (absent on older servers: nothing is drawn). */
    private fun applyMemory(json: JSONObject) {
        _memory.value = ThreadModel.parseMemory(json)
    }

    /**
     * "New topic": Dana forgets the thread so far (`POST /api/sessions/main/clear`);
     * the messages stay shown, an open approval or choice counts as declined.
     * Refused calmly while a run is going (409 `session_busy`). [report] shows
     * the outcome (the app's Snackbar by default; the sheet uses a toast).
     */
    fun clearThread(report: (String) -> Unit = { ctx.messages.post(it) }) {
        if (!inThreadNow() || _clearing.value) return
        if (_isStreaming.value) {
            report(BUSY_CLEAR)
            return
        }
        _clearing.value = true
        ctx.scope.launch {
            try {
                val json = ctx.api.clearThread(device)
                if (!inThreadNow()) return@launch
                parseAndSetSessionDetail(json)
                // An older server's payload says nothing: Dana remembers nothing now.
                if (_memory.value == ThreadModel.Memory.Unknown) _memory.value = ThreadModel.Memory.Known(null, null)
                report("Dana forgot this conversation")
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                report(if (e.code == 409) BUSY_CLEAR else "Couldn't start a new topic: ${e.message}")
            } catch (e: Exception) {
                report("Couldn't start a new topic: ${describeError(e)}")
            } finally {
                _clearing.value = false
            }
        }
    }

    private fun reconcileUndos(state: JSONObject) {
        val server = ThreadModel.parseUndos(state.optJSONArray("undos"))
        val lastUserAt = _chatMessages.value.lastOrNull { it.isUser }?.timestamp
        _undos.update { ThreadModel.reconcileUndos(it, server, lastUserAt) }
    }

    /** Scrolled to the top of the thread: fetch the page before the oldest one shown. */
    fun loadOlder() {
        if (!inThreadNow() || !_hasOlder.value || _loadingOlder.value) return
        val cursor = olderCursor ?: return
        _loadingOlder.value = true
        ctx.scope.launch {
            try {
                val json = ctx.load("ThreadOlder") { JSONObject(ctx.api.getThread(THREAD_PAGE, cursor)) } ?: return@launch
                if (!inThreadNow()) return@launch
                val older = ChatHistoryCodec.decode(json.optJSONArray("messages") ?: JSONArray())
                _chatMessages.value = ThreadModel.prependOlder(_chatMessages.value, older)
                _hasOlder.value = json.optBoolean("has_more", false) && older.isNotEmpty()
                olderCursor = json.opt("before")?.takeIf { it != JSONObject.NULL }?.toString()?.takeIf { it.isNotBlank() && it != "null" }
            } finally {
                _loadingOlder.value = false
            }
        }
    }

    /**
     * Press an Undo under Dana's reply (`POST /api/undo/{id}`). The button
     * turns into what happened: "Undone.", too late (404, after 15 min), or
     * the server's reason when it refused (the thing changed since).
     */
    fun undo(id: String) {
        val item = _undos.value.firstOrNull { it.id == id } ?: return
        if (item.status != UndoItem.Status.READY && item.status != UndoItem.Status.FAILED) return
        setUndo(item) { it.copy(status = UndoItem.Status.BUSY, error = null) }
        ctx.scope.launch {
            val at = System.currentTimeMillis()
            try {
                val outcome = ctx.api.undo(id)
                setUndo(item) {
                    it.copy(status = if (outcome == ApiService.UndoOutcome.DONE) UndoItem.Status.DONE else UndoItem.Status.TOO_LATE, at = at)
                }
                if (outcome == ApiService.UndoOutcome.DONE) onThingsChanged()
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                setUndo(item) { it.copy(status = UndoItem.Status.FAILED, error = e.message?.removePrefix("Undo failed: "), at = at) }
            } catch (e: Exception) {
                setUndo(item) { it.copy(status = UndoItem.Status.READY) }
                ctx.messages.post("Couldn't undo: ${describeError(e)}")
            }
        }
    }

    private fun setUndo(item: UndoItem, transform: (UndoItem) -> UndoItem) {
        // Several presses can finish at once: update atomically.
        _undos.update { list ->
            if (list.any { it.id == item.id }) list.map { if (it.id == item.id) transform(it) else it } else list + transform(item)
        }
    }

    /**
     * Re-read the session after the app comes back to the foreground, so a
     * reply that finished in the background shows up without a reload.
     */
    fun resyncIfIdle() {
        if (_isStreaming.value || loadHistoryJob?.isActive == true) return
        if (_isRecording.value || voiceGateJob?.isActive == true || deviceSpeech != null) return
        val id = sessionId
        if (id.isEmpty()) return
        ctx.scope.launch {
            val json = ctx.load("ResyncSession") { fetchShown(id) } ?: return@launch
            if (sessionId == id && !_isStreaming.value) parseAndSetSessionDetail(json)
        }
        fetchSessions()
    }

    // --- Assistant streaming (Server-Sent Events) ---

    /**
     * Run an assistant request and render its run live. The server streams the
     * run's events (each with `run_id` + `seq`) and always closes with a `state`
     * frame, so every terminal case (approval, selection, step limit, error,
     * cancel) is handled here. If the connection drops mid-run we reattach to
     * `GET /api/sessions/{id}/stream` and resume after the last seen `seq`.
     */
    private fun runAssistantStream(
        statusText: String,
        resetTracker: Boolean = true,
        /** A new message: on a 409 in the thread its text goes back to the ask bar. */
        sent: SentMessage? = null,
        block: suspend (onEvent: (JSONObject) -> Unit) -> Unit
    ) {
        streamJob?.cancel()
        if (resetTracker) run = RunTracker()
        _isStreaming.value = true
        _chatStatusText.value = statusText
        showThinkingIndicator(statusText)
        flushPendingPersist()
        runChanged = false
        followBusyRun = false

        val id = sessionId
        streamJob = ctx.scope.launch {
            try {
                followRun(id, sent, block)
            } finally {
                if (sessionId == id) {
                    _isStreaming.value = false
                    settleBubbles()
                }
                flushPendingPersist()
                finishRun(id)
            }
        }
    }

    /** What was just sent, so a refused send (409) can be put back. */
    private class SentMessage(val text: String, val bubbleId: String, val image: Uri? = null, val screen: OutgoingScreen? = null)

    /** After a run: refresh other screens if it changed things; follow the run that refused ours. */
    private fun finishRun(id: String) {
        if (runChanged) {
            runChanged = false
            onThingsChanged()
        }
        if (followBusyRun && sessionId == id) {
            followBusyRun = false
            ctx.scope.launch { loadActiveSessionHistory() }
        }
    }

    /** Attach to a run that is already going (reload, resume, reconnect). */
    private fun attachToRun(runId: String?, afterSeq: Int = 0, resetTracker: Boolean = true) {
        val id = sessionId
        runAssistantStream("Dana is thinking…", resetTracker) { onEvent ->
            ctx.api.attachRunStream(id, runId, afterSeq, onEvent)
        }
    }

    private suspend fun followRun(id: String, sent: SentMessage?, first: suspend (onEvent: (JSONObject) -> Unit) -> Unit) {
        var request = first
        val startedAt = System.currentTimeMillis()
        var reconnects = 0
        var busyRedirects = 0
        while (true) {
            try {
                request { event -> handleAssistantEvent(event) }
                if (run.gotState) return
                throw IOException("Stream ended before the run finished")
            } catch (e: CancellationException) {
                Log.d(TAG, "Assistant stream cancelled")
                removeThinkingIndicator()
                throw e
            } catch (e: SessionBusyException) {
                if (sent != null && inThreadNow()) {
                    // Another device's (or an earlier) request is still running in the
                    // thread: nothing was sent. Put the text back and show that run.
                    refuseBusy(sent)
                    return
                }
                if (busyRedirects++ > 0) {
                    reportStreamFailure(e)
                    return
                }
                // An earlier run in this chat is still going: follow it.
                appendMessage("Assistant", "Still working on your previous request — following it…", false)
                run = RunTracker()
                val busyRun = e.runId
                request = { onEvent -> ctx.api.attachRunStream(id, busyRun, 0, onEvent) }
            } catch (e: Exception) {
                Log.w(TAG, "Assistant stream failed: ${describeError(e)}")
                // A stream that dies mid-read never reaches the interceptor.
                if (e is IOException) ServerStatusMonitor.checkNow()
                val runId = run.runId
                val canReattach = e is IOException && runId != null && sessionId == id &&
                    System.currentTimeMillis() - startedAt < REATTACH_GIVE_UP_MS
                if (!canReattach) {
                    reportStreamFailure(e)
                    return
                }
                // The server keeps the run going without us; reconnect and
                // resume right after the last event we rendered.
                reconnects++
                _chatStatusText.value = "Connection lost — reconnecting…"
                showThinkingIndicator("Reconnecting…")
                delay(minOf(REATTACH_BASE_DELAY_MS * reconnects, REATTACH_MAX_DELAY_MS))
                val after = run.lastSeq
                request = { onEvent -> ctx.api.attachRunStream(id, runId, after, onEvent) }
            }
        }
    }

    private fun refuseBusy(sent: SentMessage?) {
        removeThinkingIndicator()
        if (sent != null) {
            _chatMessages.value = _chatMessages.value.filter { it.id != sent.bubbleId }
            if (sent.image != null) _pendingImageUri.value = sent.image
            sent.screen?.let { screenAttachments?.putBack(it) }
            _draftText.value = sent.text
        }
        _chatStatusText.value = ""
        ctx.messages.post(
            if (sent != null) "Dana is still answering the last request. Your message is back in the box."
            else "Dana is still answering the last request. Say it again when she's done."
        )
        followBusyRun = true
    }

    private fun reportStreamFailure(e: Exception) {
        removeThinkingIndicator()
        val reason = if (e is SessionBusyException) e.message.orEmpty() else describeError(e)
        _chatStatusText.value = if (run.runId != null) "Lost the connection. Reopen this chat to see the reply." else "Connection error: $reason"
        if (run.runId == null) appendMessage("Assistant", "Couldn't get a reply: $reason", false)
    }

    fun stopAssistant() {
        if (!_isStreaming.value) return
        val id = sessionId
        streamJob?.cancel()
        closeVoiceSocket("Stopped")
        ctx.scope.launch {
            ctx.load("CancelAssistant") { ctx.api.cancelAssistant(id) }
        }
        removeThinkingIndicator()
        settleBubbles()
        _isStreaming.value = false
        _chatStatusText.value = "Stopped"
        _pendingApproval.value = null
        _selectionRequest.value = null
        _pendingExtension.value = null
    }

    // --- Live reply bubbles ---

    /** Mutate (creating on first use) the streaming reply bubble of one LLM step. */
    private fun updateStepBubble(iteration: Int, transform: (ChatMessage) -> ChatMessage) {
        val current = _chatMessages.value.filter { !it.isThinking }.toMutableList()
        val existingId = run.stepBubbles[iteration]
        val idx = if (existingId != null) current.indexOfFirst { it.id == existingId } else -1
        if (idx >= 0) {
            current[idx] = transform(current[idx])
        } else {
            val bubble = ChatMessage(sender = "Assistant", message = "", isUser = false, isStreaming = true)
            run.stepBubbles[iteration] = bubble.id
            current.add(transform(bubble))
        }
        _chatMessages.value = current
        persistMessagesThrottled()
    }

    private fun dropStepBubble(iteration: Int) {
        val id = run.stepBubbles.remove(iteration) ?: return
        _chatMessages.value = _chatMessages.value.filter { it.id != id }
    }

    /** Stop the blinking/streaming state on every bubble of this run. */
    private fun settleBubbles() {
        val ids = run.stepBubbles.values.toSet()
        if (ids.isEmpty()) return
        val current = _chatMessages.value
        if (current.none { it.id in ids && it.isStreaming }) return
        _chatMessages.value = current.map { if (it.id in ids && it.isStreaming) it.copy(isStreaming = false) else it }
    }

    /** Create or update the card for one tool call. */
    private fun upsertToolCall(toolCallId: String, name: String, transform: (ToolCallEntry) -> ToolCallEntry) {
        val exists = toolCallId.isNotEmpty() && _chatMessages.value.any { it.toolCall?.toolCallId == toolCallId }
        if (exists) {
            updateToolCall(toolCallId, transform)
        } else {
            removeThinkingIndicator()
            addToolCall(transform(ToolCallEntry(toolName = name, toolSummary = "", parameters = "", toolCallId = toolCallId)))
        }
    }

    private fun anyToolBusy(): Boolean =
        _chatMessages.value.any { it.toolCall?.let { tc -> tc.isRunning || tc.isPreparing } == true }

    /** Map one streamed run event onto the chat state. */
    private fun handleAssistantEvent(event: JSONObject) {
        val runId = event.optString("run_id")
        if (runId.isNotEmpty()) {
            if (run.runId != null && run.runId != runId) {
                // We're now following a different run than before (attached
                // after a newer one started): fresh bookkeeping.
                run = RunTracker().also { it.modelLabel = run.modelLabel }
            }
            run.runId = runId
        }
        if (event.has("seq")) {
            val seq = event.optInt("seq")
            if (seq <= run.lastSeq) return  // already rendered (replayed on reattach)
            run.lastSeq = seq
        }

        when (event.optString("type")) {
            "run_start" -> {
                run.modelLabel = event.optJSONObject("model")?.optString("label")?.takeIf { it.isNotEmpty() }
            }

            "transcription" -> {
                val text = event.optString("text")
                if (text.isNotEmpty()) {
                    removeThinkingIndicator()
                    newTurn()
                    val sentScreen = voiceScreen
                    voiceScreen = null
                    if (sentScreen != null) {
                        val m = ChatMessage(
                            sender = "You", message = text, isUser = true,
                            images = listOfNotNull(sentScreen.jpeg?.let { screenBubbleImage(it) }),
                            screen = sentScreen.label.ifBlank { "Screen" },
                            timestamp = System.currentTimeMillis(), device = if (threadMode) device else ""
                        )
                        _chatMessages.value = _chatMessages.value + m
                        persistMessagesThrottled()
                    } else {
                        appendMessage("You", text, true)
                    }
                    showThinkingIndicator("Thinking…")
                }
            }

            "step_start" -> {
                val iteration = event.optInt("iteration", 0)
                val max = event.optInt("max_iterations", 0)
                val label = if (iteration > 1 && max > 0) "Step $iteration of $max" else "Thinking…"
                _chatStatusText.value = label
                showThinkingIndicator(label)
            }

            "reasoning_delta" -> {
                val delta = event.optString("delta")
                updateStepBubble(event.optInt("iteration", 0)) { it.copy(reasoning = it.reasoning + delta, isStreaming = true) }
                _chatStatusText.value = "Thinking…"
            }

            "text_delta" -> {
                val delta = event.optString("delta")
                updateStepBubble(event.optInt("iteration", 0)) { it.copy(message = it.message + delta, isStreaming = true) }
                _chatStatusText.value = "Writing…"
            }

            "retry" -> {
                dropStepBubble(event.optInt("iteration", 0))
                showThinkingIndicator("Connection hiccup — retrying…")
            }

            "tool_call" -> {
                val name = event.optString("name", "Tool Execution")
                upsertToolCall(event.optString("id"), name) { it.copy(toolName = name, isPreparing = true) }
                _chatStatusText.value = "Preparing $name…"
            }

            "tool_start" -> {
                val name = event.optString("name", "Tool Execution")
                upsertToolCall(event.optString("id"), name) {
                    it.copy(
                        toolName = name,
                        parameters = prettyJson(event.opt("args")),
                        isPreparing = false,
                        isRunning = true
                    )
                }
                _chatStatusText.value = "Running $name…"
            }

            "tool_end" -> {
                runChanged = true
                val more = ThreadModel.parseUndos(event.optJSONArray("undos"))
                if (more.isNotEmpty()) _undos.update { ThreadModel.addUndos(it, more) }
                val ok = !event.has("ok") || event.optBoolean("ok", true)
                upsertToolCall(event.optString("id"), event.optString("name", "Tool Execution")) {
                    it.copy(
                        response = prettyJson(event.opt("result")),
                        isPreparing = false,
                        isRunning = false,
                        succeeded = ok,
                        durationMs = if (event.has("duration_ms")) event.optLong("duration_ms", 0L) else null
                    )
                }
                if (!anyToolBusy()) {
                    showThinkingIndicator("Reading the results…")
                    _chatStatusText.value = "Thinking…"
                }
            }

            "final" -> {
                removeThinkingIndicator()
                val content = event.optString("content")
                val shortcut = event.optBoolean("shortcut", false)
                val lastStep = run.stepBubbles.keys.maxOrNull()
                val bubbleId = lastStep?.let { run.stepBubbles[it] }
                val list = _chatMessages.value
                val bubbleIdx = list.indexOfFirst { it.id == bubbleId }
                val toolAfter = bubbleIdx >= 0 && list.drop(bubbleIdx + 1).any { it.toolCall != null }
                if (bubbleIdx >= 0 && !toolAfter) {
                    // The reply already streamed into this bubble; the final
                    // text is authoritative in case a delta went missing.
                    updateStepBubble(lastStep!!) { it.copy(message = content.ifEmpty { it.message }, isStreaming = false, shortcut = shortcut) }
                } else if (content.isNotEmpty()) {
                    val list = _chatMessages.value + ChatMessage(sender = "Assistant", message = content, isUser = false, shortcut = shortcut)
                    _chatMessages.value = list
                    persistMessages(list)
                }
                settleBubbles()
            }

            "error" -> {
                removeThinkingIndicator()
                appendMessage("Assistant", "Error: ${event.optString("message")}", false)
            }

            "approval_required" -> {
                val id = event.optString("id")
                val name = event.optString("name", "Tool Execution")
                _pendingApproval.value = PendingApproval(
                    toolName = name,
                    toolArgs = event.opt("args").toString(),
                    toolCallId = id
                )
                val reason = event.optString("reason").takeIf { it != "null" }.orEmpty()
                upsertToolCall(id, name) {
                    it.copy(
                        requiresApproval = true,
                        isRunning = false,
                        isPreparing = false,
                        parameters = it.parameters.ifEmpty { prettyJson(event.opt("args")) },
                        toolArgs = event.opt("args").toString(),
                        isCollapsed = false,
                        approvalReason = reason
                    )
                }
                removeThinkingIndicator()
                _chatStatusText.value = "Waiting for your approval"
            }

            "selection_required" -> {
                // The selection prompt is shown as its own card, not a tool run.
                val id = event.optString("id")
                if (id.isNotEmpty()) _chatMessages.value = _chatMessages.value.filter { it.toolCall?.toolCallId != id }
                _selectionRequest.value = event.toSelectionRequest()
                removeThinkingIndicator()
                _chatStatusText.value = "Waiting for your choice"
            }

            "iterations_exceeded" -> {
                _pendingExtension.value = PendingExtension(event.optString("summary"))
                removeThinkingIndicator()
                _chatStatusText.value = "Step limit reached"
            }

            "cancelled" -> {
                removeThinkingIndicator()
                appendMessage("Assistant", "Stopped.", false)
            }

            "run_end" -> {
                removeThinkingIndicator()
                settleBubbles()
            }

            "state" -> {
                run.gotState = true
                event.optJSONObject("state")?.let { applyTerminalState(it) }
            }
        }
    }

    /**
     * The authoritative session state that closes every stream. Live events
     * already rendered the content, so this only reconciles the prompts the
     * user still has to answer and clears stale ones.
     */
    private fun applyTerminalState(state: JSONObject) {
        val status = state.optString("status", "idle")
        // Refreshed every turn: a suggestion accepted in this chat shows as done.
        if (state.has("context")) _sessionContext.value = app.ganam.assistant.data.network.CallJson.chatContext(state.optJSONObject("context"))
        removeThinkingIndicator()
        _chatStatusText.value = ""
        if (inThreadNow()) {
            applyMemory(state)
            // The stored turn (stable ids, times, devices) replaces the live bubbles.
            val stored = ChatHistoryCodec.decode(state.optJSONArray("messages") ?: JSONArray())
            if (stored.isNotEmpty()) applyThreadPage(state, stored)
        }
        if (state.has("undos")) reconcileUndos(state)

        val pendingObj = state.optJSONObject("pending_approval")
        // The server stores "waiting_approval" while an approval is pending
        // (core/session_runner.py), so accept both spellings. Clear only when
        // the state carries no pending approval at all.
        val awaitingApproval =
            (status == "pending_approval" || status == "waiting_approval") && pendingObj != null
        if (awaitingApproval) {
            val id = pendingObj!!.optString("tool_call_id")
            _pendingApproval.value = PendingApproval(
                toolName = pendingObj.optString("tool_name", "Tool Execution"),
                toolArgs = pendingObj.opt("tool_args").toString(),
                toolCallId = id
            )
            val reason = approvalReasonOf(pendingObj)
            updateToolCall(id) { it.copy(requiresApproval = true, isRunning = false, isCollapsed = false, approvalReason = reason.ifEmpty { it.approvalReason }) }
        } else if (pendingObj == null && _isStreaming.value) {
            _pendingApproval.value = null
        }

        val selectionObj = state.optJSONObject("selection_request")
        if (status == "awaiting_selection" && selectionObj != null) {
            _selectionRequest.value = selectionObj.toSelectionRequest()
        } else if (_isStreaming.value) {
            _selectionRequest.value = null
        }

        if (status == "iterations_exceeded") {
            _pendingExtension.value = PendingExtension(state.optString("extend_summary"))
        } else if (_isStreaming.value) {
            _pendingExtension.value = null
        }

        // The error event usually said it already; a state frame adds a line only when it has news.
        val stateError = state.optString("error").takeIf { it.isNotBlank() && it != "null" }
        if (status == "error" && stateError != null && _chatMessages.value.lastOrNull()?.message?.contains(stateError) != true) {
            appendMessage("Assistant", "Error: $stateError", false)
        }
    }

    /** The pending payload's reason for asking ("device_screen" when only the shared screen made it ask). */
    private fun approvalReasonOf(pending: JSONObject): String =
        pending.optString("approval_reason").takeIf { it.isNotBlank() && it != "null" }
            ?: if (pending.optBoolean("untrusted_content", false)) "device_screen" else ""

    private fun JSONObject.toSelectionRequest(): SelectionRequest {
        val optionsArr = optJSONArray("options") ?: JSONArray()
        val options = (0 until optionsArr.length()).mapNotNull { i ->
            val opt = optionsArr.optJSONObject(i) ?: return@mapNotNull null
            SelectionOption(label = opt.optString("label"), value = opt.optString("value"))
        }
        return SelectionRequest(prompt = optString("prompt"), options = options, multiple = optBoolean("multiple", false))
    }

    /** Pretty-print a tool argument/result blob, tolerating plain strings. */
    private fun prettyJson(value: Any?): String {
        if (value == null || value === JSONObject.NULL) return ""
        val text = value.toString()
        if (!text.startsWith("{") && !text.startsWith("[")) return text
        return try {
            if (text.startsWith("{")) JSONObject(text).toString(2) else JSONArray(text).toString(2)
        } catch (e: Exception) {
            text
        }
    }

    // --- Sending ---

    /**
     * Returns false (and keeps nothing) when the message wasn't sent because the
     * server is known to be unreachable. The caller should then keep the user's
     * text in the input box.
     */
    fun sendChatMessage(message: String): Boolean {
        if (!ensureCanReachServer("Message not sent")) return false

        val outgoing = screenAttachments?.take()
        if (outgoing != null) {
            sendWithScreen(message, outgoing, bubbleId = null)
            return true
        }
        val imageUri = _pendingImageUri.value
        _pendingImageUri.value = null
        newTurn()
        val bubbleId = appendAndGetId("You", message, true, listOfNotNull(imageUri?.toString()))

        val id = sessionId
        val screen = currentScreen()
        val dev = deviceParam()
        runAssistantStream("Sending…", sent = SentMessage(message, bubbleId, imageUri)) { onEvent ->
            val imageBytes = imageUri?.let { uri ->
                // Reading a multi-MB image must not block the main dispatcher.
                withContext(Dispatchers.IO) {
                    appContext.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                }
            }
            if (imageUri != null && imageBytes != null) {
                val mimeType = appContext.contentResolver.getType(imageUri)
                    ?: android.webkit.MimeTypeMap.getSingleton().getMimeTypeFromExtension(
                        android.webkit.MimeTypeMap.getFileExtensionFromUrl(imageUri.toString()).lowercase()
                    )
                    ?: "image/jpeg"
                ctx.api.streamMessageWithImage(message, id, selectedModel(), imageBytes, mimeType, onEvent, dev, screen)
            } else {
                ctx.api.streamMessage(message, id, selectedModel(), onEvent, dev, screen)
            }
        }
        return true
    }

    /**
     * A message with the screen of the app under the sheet: the screenshot
     * goes as the message's image (multipart), the description as
     * `device_screen`; with no screenshot (the app blocks it) the text goes
     * alone on the JSON path. [bubbleId]: the live-transcript bubble to settle
     * instead of adding one. A picked image waits for the next message.
     */
    private fun sendWithScreen(message: String, outgoing: OutgoingScreen, bubbleId: String?) {
        newTurn()
        val dev = deviceParam()
        val images = listOfNotNull(outgoing.jpeg?.let { screenBubbleImage(it) })
        val label = outgoing.label.ifBlank { "Screen" }
        val shownId = if (bubbleId != null && _chatMessages.value.any { it.id == bubbleId }) {
            val now = System.currentTimeMillis()
            _chatMessages.value = _chatMessages.value.map {
                if (it.id == bubbleId) it.copy(message = message, isStreaming = false, timestamp = now, device = dev.orEmpty(), images = images, screen = label) else it
            }
            bubbleId
        } else {
            val m = ChatMessage(
                sender = "You", message = message, isUser = true, images = images, screen = label,
                timestamp = System.currentTimeMillis(), device = if (threadMode) device else ""
            )
            _chatMessages.value = _chatMessages.value + m
            m.id
        }
        persistMessages(_chatMessages.value)

        val id = sessionId
        val screen = currentScreen()
        runAssistantStream("Sending…", sent = SentMessage(message, shownId, screen = outgoing)) { onEvent ->
            val jpeg = outgoing.jpeg
            if (jpeg != null) {
                ctx.api.streamMessageWithImage(message, id, selectedModel(), jpeg, "image/jpeg", onEvent, dev, screen, outgoing.screen)
            } else {
                ctx.api.streamMessage(message, id, selectedModel(), onEvent, dev, screen, outgoing.screen)
            }
        }
    }

    /**
     * The sent screenshot as a local file for the message bubble until the
     * stored copy (a server image) replaces it. Written only once it is sent;
     * older ones are cleared.
     */
    private fun screenBubbleImage(jpeg: ByteArray): String? = runCatching {
        val dir = java.io.File(appContext.cacheDir, "sent_screens").apply { mkdirs() }
        val cutoff = System.currentTimeMillis() - 60 * 60_000L
        dir.listFiles()?.filter { it.lastModified() < cutoff }?.forEach { it.delete() }
        val f = java.io.File(dir, "${UUID.randomUUID()}.jpg")
        f.writeBytes(jpeg)
        "file://" + f.absolutePath
    }.onFailure { Log.w(TAG, "Couldn't keep the sent screenshot for the bubble", it) }.getOrNull()

    /** The item on screen to send with this message (thread only). */
    private fun currentScreen(): app.ganam.assistant.ui.viewmodels.ScreenRef? =
        if (inThreadNow()) runCatching { screenProvider() }.getOrNull() else null

    /** A new turn starts: the previous turn's Undo row (and its outcomes) goes. */
    private fun newTurn() {
        _undos.value = emptyList()
    }

    /**
     * Refuse to start work while the server is known to be unreachable, and
     * re-check right away so the banner clears as soon as the VPN is back.
     */
    private fun ensureCanReachServer(action: String): Boolean {
        val status = ServerStatusMonitor.status.value
        return when (status.reachability) {
            Reachability.NOT_CONFIGURED -> {
                ctx.messages.post("$action: set the server URL in Settings first")
                false
            }
            Reachability.OFFLINE -> {
                ServerStatusMonitor.checkNow()
                ctx.messages.post("$action: ${status.detail ?: "server unreachable"}")
                false
            }
            else -> true
        }
    }

    fun clearPendingImage() {
        _pendingImageUri.value = null
    }

    private val _draftText = MutableStateFlow<String?>(null)
    /** Text to put in the input box (from a share); the screen consumes it. */
    val draftText: StateFlow<String?> = _draftText.asStateFlow()

    fun consumeDraft() {
        _draftText.value = null
    }

    /**
     * Content shared from another app: open a fresh chat with it ready to
     * send, so the user can add "summarize this" / "remind me" first.
     */
    /** Shared text/image into the input. [newSession]: the old layout starts a fresh chat; the shell keeps its one thread. */
    fun startChatFromShare(text: String?, imageUri: Uri?, newSession: Boolean = true) {
        if (newSession) startNewSession()
        _pendingImageUri.value = imageUri
        _draftText.value = text?.trim()?.takeIf { it.isNotEmpty() }
    }

    fun setPendingImage(uri: Uri?) {
        _pendingImageUri.value = uri
    }

    fun approveAction(approved: Boolean) {
        // The visible "Needs Approval" card is authoritative. If the prompt
        // state drifted (e.g. a terminal state frame racing the approval
        // event), rebuild it from the card instead of dropping the click.
        val pending = _pendingApproval.value ?: run {
            val card = _chatMessages.value.lastOrNull { it.toolCall?.requiresApproval == true }?.toolCall ?: return
            PendingApproval(card.toolName, card.toolArgs.ifEmpty { card.parameters }, card.toolCallId)
        }
        if (!ensureCanReachServer(if (approved) "Couldn't approve" else "Couldn't deny")) return
        _pendingApproval.value = null
        updateToolCallRequiringApproval(approved)

        streamAction(if (approved) "Executing..." else "Cancelling...") {
            put("action", if (approved) "approve" else "deny")
            if (pending.toolCallId.isNotEmpty()) put("tool_call_id", pending.toolCallId)
        }
    }

    fun sendSelection(indices: List<Int>) {
        if (_selectionRequest.value == null) return
        if (!ensureCanReachServer("Selection not sent")) return
        _selectionRequest.value = null
        streamAction(if (indices.isEmpty()) "Cancelling..." else "Submitting selection...") {
            put("action", "select")
            put("selection", JSONArray(indices))
        }
    }

    fun cancelSelection() = sendSelection(emptyList())

    /** Grant more iterations to a run that hit the step limit. */
    fun extendIterations() {
        if (_pendingExtension.value == null) return
        if (!ensureCanReachServer("Couldn't continue")) return
        _pendingExtension.value = null
        streamAction("Continuing with more steps...") { put("action", "extend_iterations") }
    }

    /** Stop an over-budget run and settle the session. */
    fun declineIterations() {
        if (_pendingExtension.value == null) return
        if (!ensureCanReachServer("Couldn't stop the run")) return
        _pendingExtension.value = null
        streamAction("Wrapping up...") { put("action", "decline_iterations") }
    }

    private fun streamAction(statusText: String, fill: JSONObject.() -> Unit) {
        val payload = JSONObject().apply {
            put("session_id", sessionId)
            deviceParam()?.let { put("device", it) }
            fill()
        }
        runAssistantStream(statusText) { onEvent -> ctx.api.streamApproveAction(payload, onEvent) }
    }

    // --- Voice (push-to-talk over WebSocket) ---

    /**
     * Start listening. When the server is already known to be unreachable we
     * re-check first and only open the mic if it answers. When reachability is
     * unknown or stale we start right away and abort if a probe fails, so the
     * user isn't left talking to nothing.
     */
    fun startVoiceRecording() {
        endRealtimeCall()
        if (_isRecording.value || voiceGateJob?.isActive == true || deviceSpeech != null) return

        val status = ServerStatusMonitor.status.value
        when (status.reachability) {
            Reachability.NOT_CONFIGURED -> {
                _chatStatusText.value = "Set the server URL in Settings first"
                return
            }
            Reachability.OFFLINE -> {
                _chatStatusText.value = "Checking server…"
                voiceGateJob = ctx.scope.launch {
                    if (ServerStatusMonitor.ensureReachable()) {
                        beginVoiceRecording()
                    } else {
                        _chatStatusText.value = offlineText()
                    }
                }
                return
            }
            else -> {
                beginVoiceRecording()
                val stale = System.currentTimeMillis() - status.lastCheckedAt > VOICE_STATUS_FRESH_MS
                if (status.reachability != Reachability.ONLINE || stale) verifyWhileRecording()
            }
        }
    }

    private fun verifyWhileRecording() {
        val socketAtStart = currentWebSocket
        voiceVerifyJob?.cancel()
        voiceVerifyJob = ctx.scope.launch {
            if (!ServerStatusMonitor.ensureReachable() && currentWebSocket === socketAtStart && _isRecording.value) {
                abortVoice(offlineText())
            }
        }
    }

    private fun offlineText(): String =
        ServerStatusMonitor.status.value.detail ?: "Server unreachable — is the VPN on?"

    /**
     * Listen with the phone's speech recognizer when it's available (live words,
     * its own end-of-speech detection), otherwise stream PCM to the server.
     */
    private fun beginVoiceRecording() {
        if (_isRecording.value) return
        if (DeviceSpeech.isEnabled(appContext) && DeviceSpeech.isAvailable(appContext)) {
            beginDeviceRecognition()
        } else {
            beginServerRecording()
        }
    }

    // --- Voice: on-device recognition, then a normal text message ---

    private fun beginDeviceRecognition() {
        _isRecording.value = true
        _chatStatusText.value = "Listening..."
        hasSpokenInCurrentSession = false
        liveBubbleId = null

        lateinit var speech: DeviceSpeech
        speech = DeviceSpeech(appContext, object : DeviceSpeech.Listener {
            override fun onPartial(text: String) {
                if (deviceSpeech !== speech) return
                hasSpokenInCurrentSession = true
                showLiveTranscript(text)
            }

            override fun onFinal(text: String) {
                if (deviceSpeech !== speech) return
                finishDeviceRecognition(text)
            }

            override fun onError(code: Int, heardSomething: Boolean) {
                if (deviceSpeech !== speech) return
                // Only fall back while the user is still holding the turn open;
                // after they tapped stop, starting a new recording would surprise them.
                val stillListening = _isRecording.value
                deviceSpeech = null
                deviceStopJob?.cancel()
                removeLiveTranscript()
                _isRecording.value = false
                if (!stillListening) {
                    _chatStatusText.value = ""
                } else if (!heardSomething && code in DeviceSpeech.FALLBACK_ERRORS) {
                    Log.w(TAG, "Device speech unavailable (error $code); using server transcription")
                    beginServerRecording()
                } else {
                    _chatStatusText.value = "Didn't catch that — try again"
                }
            }
        })
        deviceSpeech = speech
        try {
            speech.start()
        } catch (e: Exception) {
            Log.e(TAG, "Could not start device speech; using server transcription", e)
            deviceSpeech = null
            _isRecording.value = false
            beginServerRecording()
        }
    }

    /** User tapped stop: end the utterance now and wait briefly for the final words. */
    private fun stopDeviceRecognition(speech: DeviceSpeech) {
        _isRecording.value = false
        if (!hasSpokenInCurrentSession) _chatStatusText.value = "Transcribing…"
        speech.stop()
        deviceStopJob?.cancel()
        deviceStopJob = ctx.scope.launch {
            delay(DEVICE_FINAL_TIMEOUT_MS)
            // The final result never came; go with what was already on screen.
            if (deviceSpeech === speech) {
                speech.cancel()
                finishDeviceRecognition(speech.partialText)
            }
        }
    }

    private fun finishDeviceRecognition(text: String) {
        deviceSpeech = null
        deviceStopJob?.cancel()
        _isRecording.value = false
        val bubbleId = liveBubbleId
        liveBubbleId = null
        if (text.isBlank()) {
            removeLiveTranscript(bubbleId)
            _chatStatusText.value = ""
            return
        }
        if (!ensureCanReachServer("Message not sent")) {
            removeLiveTranscript(bubbleId)
            _chatStatusText.value = offlineText()
            return
        }
        val outgoing = screenAttachments?.take()
        if (outgoing != null) {
            sendWithScreen(text, outgoing, bubbleId)
            return
        }
        // Settle the live bubble into the user's message, then ask the assistant.
        val current = _chatMessages.value
        newTurn()
        val now = System.currentTimeMillis()
        val dev = deviceParam()
        val settled = ChatMessage(sender = "You", message = text, isUser = true, timestamp = now, device = dev.orEmpty())
        val settledId = if (bubbleId != null && current.any { it.id == bubbleId }) bubbleId else settled.id
        _chatMessages.value = if (bubbleId != null && current.any { it.id == bubbleId }) {
            current.map { if (it.id == bubbleId) it.copy(message = text, isStreaming = false, timestamp = now, device = dev.orEmpty()) else it }
        } else {
            current + settled
        }
        persistMessages(_chatMessages.value)

        val id = sessionId
        val screen = currentScreen()
        runAssistantStream("Sending…", sent = SentMessage(text, settledId)) { onEvent ->
            ctx.api.streamMessage(text, id, selectedModel(), onEvent, dev, screen)
        }
    }

    private fun showLiveTranscript(text: String) {
        val current = _chatMessages.value
        val id = liveBubbleId
        if (id != null && current.any { it.id == id }) {
            _chatMessages.value = current.map { if (it.id == id) it.copy(message = text) else it }
        } else {
            val bubble = ChatMessage(
                sender = "You", message = text, isUser = true, isStreaming = true,
                timestamp = System.currentTimeMillis(), device = deviceParam().orEmpty()
            )
            liveBubbleId = bubble.id
            _chatMessages.value = current + bubble
        }
    }

    private fun removeLiveTranscript(id: String? = liveBubbleId) {
        liveBubbleId = null
        if (id == null) return
        val current = _chatMessages.value
        if (current.any { it.id == id }) _chatMessages.value = current.filter { it.id != id }
    }

    private fun cancelDeviceRecognition() {
        deviceStopJob?.cancel()
        deviceSpeech?.cancel()
        deviceSpeech = null
        removeLiveTranscript()
    }

    // --- Voice: PCM over WebSocket, transcribed by the server ---

    @SuppressLint("MissingPermission")
    private fun beginServerRecording() {
        if (_isRecording.value) return
        _isRecording.value = true
        _chatStatusText.value = "Listening..."
        hasSpokenInCurrentSession = false

        val model = selectedModel()?.let { "&model=" + java.net.URLEncoder.encode(it, "UTF-8") }.orEmpty()
        val dev = deviceParam()?.let { "&device=$it" }.orEmpty()
        val wsUrl = serverUrl().replaceFirst("http", "ws") + "/api/assistant/voice_stream?session_id=$sessionId$model$dev"
        val request = Request.Builder().url(wsUrl).bearerAuth(apiToken()).build()

        val socket = HttpClients.webSocket.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                // OkHttp delivers frames in order on one thread; handle them
                // synchronously so the run's events keep that order.
                handleWebSocketVoiceEvent(webSocket, text)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e(TAG, "Voice WebSocket failure: ${t::class.simpleName}: ${t.message}", t)
                ctx.scope.launch {
                    // Ignore late failures from a socket we already moved on from.
                    if (currentWebSocket !== webSocket) return@launch
                    currentWebSocket = null
                    val runId = run.runId
                    if (runId != null && !run.gotState) {
                        // The run started on the server; keep following it over SSE.
                        attachToRun(runId, run.lastSeq, resetTracker = false)
                        return@launch
                    }
                    val reason = if (t is IOException) describeError(t) else (t.message ?: "connection lost")
                    abortVoice("Voice failed: $reason")
                }
            }
        })
        currentWebSocket = socket

        recordingJob = ctx.scope.launch(Dispatchers.IO) {
            try {
                captureAudio(socket)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.e(TAG, "Voice recording failed", e)
                withContext(Dispatchers.Main) { abortVoice("Microphone error") }
            }
        }
    }

    @SuppressLint("MissingPermission")
    private suspend fun captureAudio(socket: WebSocket) {
        val stream = VoiceInput.source?.invoke() ?: openAudioRecordStream()
        pcmStream = stream
        try {
            val buffer = ByteArray(2560) // 80 ms at 16 kHz PCM16

            var lastSpokenTime = System.currentTimeMillis()
            val silenceThreshold = VoiceInput.silenceRms
            val maxSilenceDuration = VoiceInput.endOfSpeechMs
            // Speech starts after a run of loud frames, not one: a single loud frame
            // (a click, the chime's tail, boosted room noise on the TV) sent near-silence
            // to the server, and the transcriber made up a sentence from it.
            var loudRun = 0

            while (kotlin.coroutines.coroutineContext.isActive && _isRecording.value) {
                val read = stream.read(buffer)
                if (read <= 0) {
                    if (pcmStream !== stream) break
                    continue
                }
                socket.send(buffer.copyOfRange(0, read).toByteString())

                var sum = 0.0
                val shortsCount = read / 2
                for (i in 0 until shortsCount) {
                    val low = buffer[i * 2].toInt() and 0xFF
                    val high = buffer[i * 2 + 1].toInt()
                    val sample = ((high shl 8) or low).toShort()
                    sum += sample * sample
                }
                val rms = if (shortsCount > 0) Math.sqrt(sum / shortsCount) else 0.0

                val now = System.currentTimeMillis()
                if (now < VoiceInput.speechGateUntil) {
                    lastSpokenTime = now
                } else if (rms > silenceThreshold) {
                    loudRun++
                    if (hasSpokenInCurrentSession || loudRun >= VoiceInput.speechStartFrames) {
                        lastSpokenTime = now
                        hasSpokenInCurrentSession = true
                    }
                } else {
                    loudRun = 0
                    val limit = if (hasSpokenInCurrentSession) maxSilenceDuration else VoiceInput.noSpeechTimeoutMs
                    if (now - lastSpokenTime > limit) {
                        Log.d(TAG, "Silence detected, auto-stopping voice recording.")
                        withContext(Dispatchers.Main) { stopVoiceRecording() }
                        break
                    }
                }
            }
        } finally {
            // Also covers a stop that raced ahead of `pcmStream = stream`.
            if (pcmStream === stream) pcmStream = null
            runCatching { stream.close() }
        }
    }

    @SuppressLint("MissingPermission")
    private fun openAudioRecordStream(): PcmStream {
        val sampleRate = 16000
        val channelConfig = AudioFormat.CHANNEL_IN_MONO
        val audioFormat = AudioFormat.ENCODING_PCM_16BIT
        val minBufferSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)
        val recorder = AudioRecord(MediaRecorder.AudioSource.MIC, sampleRate, channelConfig, audioFormat, maxOf(minBufferSize, 2560))
        audioRecord = recorder
        recorder.startRecording()
        return object : PcmStream {
            override fun read(buffer: ByteArray): Int = recorder.read(buffer, 0, buffer.size)
            override fun close() = Unit // released by releaseAudioRecord()
        }
    }

    private fun releaseAudioRecord() {
        pcmStream?.let { stream ->
            pcmStream = null
            try { stream.close() } catch (e: Exception) { Log.w(TAG, "Error closing PCM stream", e) }
        }
        try {
            audioRecord?.stop()
            audioRecord?.release()
        } catch (e: Exception) {
            Log.e(TAG, "Error releasing AudioRecord", e)
        } finally {
            audioRecord = null
        }
    }

    fun stopVoiceRecording() {
        if (voiceGateJob?.isActive == true) {
            // Still waiting to learn whether the server is back; give up.
            voiceGateJob?.cancel()
            voiceGateJob = null
            _chatStatusText.value = ""
            return
        }
        deviceSpeech?.let { speech ->
            voiceVerifyJob?.cancel()
            if (_isRecording.value) stopDeviceRecognition(speech)
            return
        }
        if (!_isRecording.value) return
        _isRecording.value = false
        recordingJob?.cancel()
        voiceVerifyJob?.cancel()
        releaseAudioRecord()

        if (!hasSpokenInCurrentSession) {
            Log.d(TAG, "No speech detected; cancelling voice stream.")
            _chatStatusText.value = ""
            removeThinkingIndicator()
            closeVoiceSocket("No speech detected")
            return
        }

        run = RunTracker()
        runChanged = false
        followBusyRun = false
        _isStreaming.value = true
        _chatStatusText.value = "Transcribing…"
        showThinkingIndicator("Transcribing…")
        val screen = currentScreen()
        // The screen goes with this utterance as `device_screen` (its text).
        // The socket carries no image (server contract), so the screenshot
        // stays on the phone on this path; the sent message says "text only".
        val outgoing = screenAttachments?.take()?.let { OutgoingScreen(it.screen, null, it.label) }
        voiceScreen = outgoing
        val stopMsg = JSONObject().apply {
            put("event", "stop")
            put("session_id", sessionId)
            put("sample_rate", 16000)
            screen?.let { put("screen", JSONObject().put("kind", it.kind).put("id", it.id)) }
            outgoing?.screen?.let { put("device_screen", it.toJson()) }
        }
        currentWebSocket?.send(stopMsg.toString())
    }

    /**
     * Tear down a voice turn that can't complete (server unreachable, socket
     * failure, mic error), whether still recording or waiting for the reply.
     * Unlike [stopVoiceRecording] it never sends "stop" or shows "Processing".
     */
    private fun abortVoice(statusText: String) {
        // The utterance never became a message: its screen stays included.
        voiceScreen?.let { screenAttachments?.putBack(it) }
        voiceScreen = null
        voiceGateJob?.cancel()
        voiceGateJob = null
        voiceVerifyJob?.cancel()
        voiceVerifyJob = null
        cancelDeviceRecognition()
        _isRecording.value = false
        recordingJob?.cancel()
        releaseAudioRecord()
        currentWebSocket?.cancel()
        currentWebSocket = null
        removeThinkingIndicator()
        _isStreaming.value = false
        _chatStatusText.value = statusText
    }

    private fun closeVoiceSocket(reason: String) {
        try {
            currentWebSocket?.close(1000, reason)
        } catch (e: Exception) {
            Log.w(TAG, "Error closing voice socket", e)
        }
        currentWebSocket = null
    }

    /**
     * One frame from the voice socket: the same events as the SSE stream
     * (`transcription`, the run's events, then a closing `state`), or
     * `no_speech` when nothing intelligible was said.
     */
    private fun handleWebSocketVoiceEvent(socket: WebSocket, text: String) {
        if (currentWebSocket !== socket) return
        val json = try {
            JSONObject(text)
        } catch (e: Exception) {
            Log.w(TAG, "Ignoring unparseable voice frame", e)
            return
        }
        when (json.optString("type")) {
            "no_speech" -> {
                // Nothing was sent: the screen goes with the next try.
                voiceScreen?.let { screenAttachments?.putBack(it) }
                voiceScreen = null
                removeThinkingIndicator()
                _isStreaming.value = false
                _chatStatusText.value = ""
                closeVoiceSocket("No speech")
            }
            "state" -> {
                handleAssistantEvent(json)
                _isStreaming.value = false
                settleBubbles()
                flushPendingPersist()
                closeVoiceSocket("Response received")
                finishRun(sessionId)
            }
            "error" -> if (json.optString("code") == "session_busy" && inThreadNow()) {
                voiceScreen?.let { screenAttachments?.putBack(it) }
                voiceScreen = null
                refuseBusy(null)
            } else handleAssistantEvent(json)
            else -> handleAssistantEvent(json)
        }
    }

    /** Release the mic and sockets; the host is going away. */
    fun release() {
        voiceGateJob?.cancel()
        voiceVerifyJob?.cancel()
        recordingJob?.cancel()
        cancelDeviceRecognition()
        _isRecording.value = false
        releaseAudioRecord()
        currentWebSocket?.cancel()
        currentWebSocket = null
        flushPendingPersist()
    }

    companion object {
        private const val TAG = "ChatController"
        private const val REATTACH_BASE_DELAY_MS = 1_000L
        /** After the user taps stop, how long to wait for the recognizer's final words. */
        private const val DEVICE_FINAL_TIMEOUT_MS = 2_500L
        private const val REATTACH_MAX_DELAY_MS = 10_000L
        /** Stop trying to reattach to a dropped run after this long. */
        private const val REATTACH_GIVE_UP_MS = 10 * 60_000L

        /**
         * Parse a session's `updated_at`. SQLite's CURRENT_TIMESTAMP writes
         * "YYYY-MM-DD HH:MM:SS" in UTC; Python writes ISO-8601, with or
         * without fractions and offset. Returns epoch millis, or null.
         */
        internal fun parseServerTimestamp(raw: String?): Long? {
            val text = raw?.trim().orEmpty()
            if (text.isEmpty() || text == "null") return null
            text.toLongOrNull()?.let { n -> return if (n < 100_000_000_000L) n * 1000 else n }
            var t = text.replace(' ', 'T')
            // Drop fractional seconds; SimpleDateFormat can't take micros.
            t = t.replace(Regex("""(T\d{2}:\d{2}:\d{2})\.\d+"""), "$1")
            val hasZone = t.endsWith("Z") || Regex("""[+-]\d{2}:?\d{2}$""").containsMatchIn(t.substringAfter('T', ""))
            if (!hasZone) t += "Z"
            t = t.replace(Regex("Z$"), "+0000").replace(Regex("""([+-]\d{2}):(\d{2})$"""), "$1$2")
            for (pattern in listOf("yyyy-MM-dd'T'HH:mm:ssZ", "yyyy-MM-dd'T'HH:mmZ")) {
                try {
                    val fmt = java.text.SimpleDateFormat(pattern, java.util.Locale.US)
                    fmt.isLenient = false
                    return fmt.parse(t)?.time
                } catch (_: java.text.ParseException) {
                }
            }
            return null
        }
        private const val KEY_SESSION_ID = "chat_session_id"
        /** Settings → "Previous layout" (`LayoutSettings`): per-session chats instead of the thread. */
        private const val KEY_PREVIOUS_LAYOUT = "previous_layout"
        private const val DEFAULT_GAP_MINUTES = 20.0
        private const val BUSY_CLEAR = "Dana is still answering — try again when the reply is done."
        /** Thread messages per page (the server's own default for the state frame). */
        private const val THREAD_PAGE = 60
        /** Newest thread messages kept in the offline cache. */
        private const val THREAD_CACHE_MESSAGES = 120
        /** A fresh view still shows a message stamped this much before it (clock skew). */
        private const val VIEW_FLOOR_MARGIN_MS = 5_000L
        private const val KEY_LAST_ACTIVE = "last_active_timestamp"
        private const val KEY_CHAT_MODEL = "chat_model"
        /** A successful check this recent is trusted without re-probing. */
        private const val VOICE_STATUS_FRESH_MS = 30_000L
    }
}
