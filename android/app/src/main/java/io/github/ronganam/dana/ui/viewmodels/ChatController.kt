package io.github.ronganam.dana.ui.viewmodels

import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import android.util.Log
import io.github.ronganam.dana.data.chat.ChatHistoryStore
import io.github.ronganam.dana.data.chat.ThreadModel
import io.github.ronganam.dana.data.chat.UndoItem
import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.data.network.ChatApi
import io.github.ronganam.dana.data.network.RunAction
import io.github.ronganam.dana.data.network.RunEvent
import io.github.ronganam.dana.data.network.SessionItem
import io.github.ronganam.dana.data.network.SessionState
import io.github.ronganam.dana.data.network.pretty
import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.models.ModelOption
import io.github.ronganam.dana.data.models.PendingExtension
import io.github.ronganam.dana.data.models.SelectionRequest
import io.github.ronganam.dana.data.models.ToolCallEntry
import io.github.ronganam.dana.data.network.Reachability
import io.github.ronganam.dana.data.network.ServerStatusMonitor
import io.github.ronganam.dana.data.network.SessionBusyException
import io.github.ronganam.dana.data.voice.DeviceSpeech
import io.github.ronganam.dana.data.screen.OutgoingScreen
import io.github.ronganam.dana.data.screen.ScreenAttachments
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.IOException
import java.util.UUID

data class PendingApproval(
    val toolName: String,
    val toolArgs: String,
    val toolCallId: String
)

/**
 * The conversation: it orchestrates the run stream ([RunStreamClient]),
 * push-to-talk voice ([VoiceSocket] fed by [MicCapture], or the phone's own
 * recognizer), the Undo row ([ChatUndos]) and the local transcript cache
 * ([ChatCache]), and maps every run event onto the messages shown.
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
    private val cache = ChatCache(ChatHistoryStore(appContext, ctx.scope), prefs, ctx.scope, { _chatMessages.value }, { sessionId to inThreadNow() })
    private val store get() = cache.store
    private val undoRow = ChatUndos(ctx, onThingsChanged)
    private val runs = RunStreamClient { ctx.api.chat }
    private val run: RunTracker get() = runs.run
    private val mic = MicCapture()
    private val voice = VoiceSocket(onEvent = ::handleVoiceEvent, onFailure = { t -> ctx.scope.launch { onVoiceFailure(t) } })

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

    private val _sessionContext = MutableStateFlow<io.github.ronganam.dana.data.models.ChatContext?>(null)
    /** What the current chat was opened from (call suggestion / call / task), if anything. */
    val sessionContext: StateFlow<io.github.ronganam.dana.data.models.ChatContext?> = _sessionContext.asStateFlow()

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

    /** Whether this controller talks in the shared thread (from [initialize] on). */
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

    /** Undo buttons of the latest run (and the outcome of pressed ones). */
    val undos: StateFlow<List<UndoItem>> = undoRow.items

    /** What is on screen while the owner asks (set by the main screen; null elsewhere). */
    @Volatile var screenProvider: () -> io.github.ronganam.dana.ui.viewmodels.ScreenRef? = { null }

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

    private var recordingJob: Job? = null
    private var voiceGateJob: Job? = null
    private var voiceVerifyJob: Job? = null
    /** Words were heard in the current utterance (the phone's recognizer; the server path asks [mic]). */
    private var hasSpokenInCurrentSession = false

    /** Live on-device transcription for the current utterance, if that path is in use. */
    private var deviceSpeech: DeviceSpeech? = null
    /** The user bubble that shows words as they are recognised. */
    private var liveBubbleId: String? = null
    private var deviceStopJob: Job? = null

    fun initialize() {
        store.migrateFromPrefs(prefs)
        initialized = true
        threadMode = true
        sessionId = ThreadModel.MAIN
        _currentSessionId.value = sessionId
        _inThread.value = inThreadNow()
        store.load(ThreadModel.MAIN)?.let { _chatMessages.value = it }
        loadActiveSessionHistory()
        fetchSessions()
        fetchModels()
    }

    /** Back to the shared thread after looking at another conversation. */
    fun returnToThread() {
        if (threadMode && sessionId != ThreadModel.MAIN) loadSession(ThreadModel.MAIN)
    }

    // --- Model selection ---

    fun fetchModels() {
        ctx.scope.launch {
            val catalog = ctx.load("Models") { ctx.api.chat.models() } ?: return@launch
            val options = catalog.models
            _models.value = options
            if (options.none { it.id == _chatModel.value }) {
                _chatModel.value = catalog.default.ifEmpty { options.firstOrNull()?.id.orEmpty() }
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

    private fun persistMessages(list: List<ChatMessage>) = cache.save(list)

    private fun persistMessagesThrottled() = cache.saveSoon()

    private fun flushPendingPersist() = cache.flush()

    // --- Sessions ---

    fun fetchSessions() {
        // The shared thread has no session list.
        if (threadMode) return
        ctx.scope.launch {
            val fromServer = ctx.load("Sessions") { ctx.api.chat.sessions() }
            _sessions.value = fromServer ?: withContext(Dispatchers.IO) {
                // Offline: offer the transcripts cached on this device.
                store.listSessions().map {
                    SessionItem(it.id, it.lastModified, it.snippet, status = "idle", title = "Offline copy")
                }
            }
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
            undoRow.clear()
            _chatStatusText.value = ""
            _sessionContext.value = null
            _hasOlder.value = false
            viewFloorMs = System.currentTimeMillis() - VIEW_FLOOR_MARGIN_MS
            cache.markActive()
            onSessionChanged()
            return
        }
        detachFromSession()
        sessionId = UUID.randomUUID().toString()
        _currentSessionId.value = sessionId
        prefs.edit()
            .putString(KEY_SESSION_ID, sessionId)
            .putLong(ChatCache.KEY_LAST_ACTIVE, System.currentTimeMillis())
            .apply()
        _chatMessages.value = emptyList()
        _chatStatusText.value = ""
        _sessionContext.value = null

        val newId = sessionId
        ctx.scope.launch {
            ctx.load("CreateSession") { ctx.api.chat.createSession(newId, "New Chat") }
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
        undoRow.clear()
        _chatMessages.value = if (inThreadNow()) store.load(ThreadModel.MAIN).orEmpty() else emptyList()
        _sessionContext.value = null
        loadActiveSessionHistory()
        onSessionChanged()
    }

    /** One GET of what is shown: the newest thread page, or the whole session. */
    private suspend fun fetchShown(id: String): SessionState =
        if (threadMode && id == ThreadModel.MAIN) ctx.api.chat.thread(THREAD_PAGE) else ctx.api.chat.session(id)

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

    private fun parseAndSetSessionDetail(state: SessionState) {
        if (state.hasContext) _sessionContext.value = state.context
        val serverMessages = state.messages

        if (inThreadNow()) {
            applyThreadPage(state, serverMessages)
        } else if (serverMessages.isNotEmpty() || _chatMessages.value.isEmpty()) {
            // Only replace local messages if the server returned some. POST-style
            // responses carry no messages array and must not wipe the chat.
            _chatMessages.value = serverMessages
        }
        state.undos?.let(::reconcileUndos)

        val pending = state.pendingApproval
        _pendingApproval.value = pending?.let { PendingApproval(it.toolName.orEmpty(), it.toolArgs.orEmpty(), it.toolCallId) }
        if (pending != null && pending.reason.isNotEmpty() && pending.toolCallId.isNotEmpty()) {
            updateToolCall(pending.toolCallId) { it.copy(approvalReason = pending.reason) }
        }

        val selection = state.selection
        if (selection != null) {
            _pendingApproval.value = null
            _selectionRequest.value = selection
        } else {
            _selectionRequest.value = null
        }

        val activeRun = state.activeRunId
        if (state.status == "running" && activeRun != null) {
            // A run is in flight but this client isn't attached to its stream
            // (reload, app resumed, session switch): replay it and follow live.
            if (!_isStreaming.value) attachToRun(activeRun)
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
    private fun applyThreadPage(state: SessionState, page: List<ChatMessage>) {
        _memory.value = state.memory
        state.gapMinutes?.let { _gapMinutes.value = it }
        val floor = viewFloorMs
        val visible = if (floor > 0) page.filter { (it.timestamp ?: Long.MAX_VALUE) >= floor } else page
        val current = _chatMessages.value.filter { !it.isThinking }
        var merged = ThreadModel.mergeNewest(current, visible)
        if (state.shortcut) {
            val i = merged.indexOfLast { !it.isUser && it.toolCall == null && it.message.isNotBlank() }
            if (i >= 0 && !merged[i].shortcut) merged = merged.toMutableList().also { it[i] = it[i].copy(shortcut = true) }
        }
        // The cursor belongs to this page; keep ours when older pages are already shown.
        if (floor > 0) {
            _hasOlder.value = false
        } else if (visible.isNotEmpty() && merged.firstOrNull()?.id == visible.first().id) {
            _hasOlder.value = state.hasMore
            olderCursor = state.before
        }
        val thinking = _chatMessages.value.filter { it.isThinking }
        _chatMessages.value = merged + thinking
        if (merged.isNotEmpty()) store.save(ThreadModel.MAIN, merged.takeLast(ChatCache.THREAD_CACHE_MESSAGES))
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
                val state = ctx.api.chat.clearThread(device)
                if (!inThreadNow()) return@launch
                parseAndSetSessionDetail(state)
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

    private fun reconcileUndos(server: List<io.github.ronganam.dana.data.chat.UndoAction>) {
        undoRow.reconcile(server, _chatMessages.value.lastOrNull { it.isUser }?.timestamp)
    }

    /** Scrolled to the top of the thread: fetch the page before the oldest one shown. */
    fun loadOlder() {
        if (!inThreadNow() || !_hasOlder.value || _loadingOlder.value) return
        val cursor = olderCursor ?: return
        _loadingOlder.value = true
        ctx.scope.launch {
            try {
                val page = ctx.load("ThreadOlder") { ctx.api.chat.thread(THREAD_PAGE, cursor) } ?: return@launch
                if (!inThreadNow()) return@launch
                val older = page.messages
                _chatMessages.value = ThreadModel.prependOlder(_chatMessages.value, older)
                _hasOlder.value = page.hasMore && older.isNotEmpty()
                olderCursor = page.before
            } finally {
                _loadingOlder.value = false
            }
        }
    }

    /** Press an Undo under Dana's reply ([ChatUndos.press]). */
    fun undo(id: String) = undoRow.press(id)

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
            val state = ctx.load("ResyncSession") { fetchShown(id) } ?: return@launch
            if (sessionId == id && !_isStreaming.value) parseAndSetSessionDetail(state)
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
        block: RunRequest
    ) {
        streamJob?.cancel()
        if (resetTracker) runs.reset()
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
            ctx.api.chat.attach(id, runId, afterSeq, onEvent)
        }
    }

    private suspend fun followRun(id: String, sent: SentMessage?, first: RunRequest) =
        runs.follow(id, first, object : RunStreamClient.Listener {
            override fun onEvent(event: RunEvent) = handleAssistantEvent(event)

            override fun onBusy(): Boolean {
                if (sent == null || !inThreadNow()) return false
                // Another device's (or an earlier) request is still running in the
                // thread: nothing was sent. Put the text back and show that run.
                refuseBusy(sent)
                return true
            }

            override fun onFollowingBusy() {
                appendMessage("Assistant", "Still working on your previous request — following it…", false)
            }

            override fun onReconnecting() {
                _chatStatusText.value = "Connection lost — reconnecting…"
                showThinkingIndicator("Reconnecting…")
            }

            override fun onFailed(e: Exception) = reportStreamFailure(e)

            override fun onCancelled() = removeThinkingIndicator()

            override fun stillShowing(): Boolean = sessionId == id
        })

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
            ctx.load("CancelAssistant") { ctx.api.chat.cancel(id) }
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
    private fun handleAssistantEvent(event: RunEvent) {
        if (!runs.accept(event)) return  // already rendered (replayed on reattach)

        when (event.type) {
            "run_start" -> {
                run.modelLabel = event.model?.label
            }

            "transcription" -> {
                val text = event.text
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
                val iteration = event.iteration
                val max = event.maxIterations
                val label = if (iteration > 1 && max > 0) "Step $iteration of $max" else "Thinking…"
                _chatStatusText.value = label
                showThinkingIndicator(label)
            }

            "reasoning_delta" -> {
                val delta = event.delta
                updateStepBubble(event.iteration) { it.copy(reasoning = it.reasoning + delta, isStreaming = true) }
                _chatStatusText.value = "Thinking…"
            }

            "text_delta" -> {
                val delta = event.delta
                updateStepBubble(event.iteration) { it.copy(message = it.message + delta, isStreaming = true) }
                _chatStatusText.value = "Writing…"
            }

            "retry" -> {
                dropStepBubble(event.iteration)
                showThinkingIndicator("Connection hiccup — retrying…")
            }

            "tool_call" -> {
                val name = event.name
                upsertToolCall(event.id, name) { it.copy(toolName = name, isPreparing = true) }
                _chatStatusText.value = "Preparing $name…"
            }

            "tool_start" -> {
                val name = event.name
                upsertToolCall(event.id, name) {
                    it.copy(toolName = name, parameters = event.args.pretty(), isPreparing = false, isRunning = true)
                }
                _chatStatusText.value = "Running $name…"
            }

            "tool_end" -> {
                runChanged = true
                undoRow.add(event.undos)
                upsertToolCall(event.id, event.name) {
                    it.copy(
                        response = event.result.pretty(),
                        isPreparing = false,
                        isRunning = false,
                        succeeded = event.ok,
                        durationMs = event.durationMs
                    )
                }
                if (!anyToolBusy()) {
                    showThinkingIndicator("Reading the results…")
                    _chatStatusText.value = "Thinking…"
                }
            }

            "final" -> {
                removeThinkingIndicator()
                val content = event.content
                val shortcut = event.shortcut
                val lastStep = run.stepBubbles.keys.maxOrNull()
                val bubbleId = lastStep?.let { run.stepBubbles[it] }
                val list = _chatMessages.value
                val bubbleIdx = list.indexOfFirst { it.id == bubbleId }
                val toolAfter = bubbleIdx >= 0 && list.drop(bubbleIdx + 1).any { it.toolCall != null }
                if (lastStep != null && bubbleIdx >= 0 && !toolAfter) {
                    // The reply already streamed into this bubble; the final
                    // text is authoritative in case a delta went missing.
                    updateStepBubble(lastStep) { it.copy(message = content.ifEmpty { it.message }, isStreaming = false, shortcut = shortcut) }
                } else if (content.isNotEmpty()) {
                    val updated = _chatMessages.value + ChatMessage(sender = "Assistant", message = content, isUser = false, shortcut = shortcut)
                    _chatMessages.value = updated
                    persistMessages(updated)
                }
                settleBubbles()
            }

            "error" -> {
                removeThinkingIndicator()
                appendMessage("Assistant", "Error: ${event.message}", false)
            }

            "approval_required" -> {
                val id = event.id
                val name = event.name
                _pendingApproval.value = PendingApproval(toolName = name, toolArgs = event.argsText, toolCallId = id)
                upsertToolCall(id, name) {
                    it.copy(
                        requiresApproval = true,
                        isRunning = false,
                        isPreparing = false,
                        parameters = it.parameters.ifEmpty { event.args.pretty() },
                        toolArgs = event.argsText,
                        isCollapsed = false,
                        approvalReason = event.reason.orEmpty()
                    )
                }
                removeThinkingIndicator()
                _chatStatusText.value = "Waiting for your approval"
            }

            "selection_required" -> {
                // The selection prompt is shown as its own card, not a tool run.
                val id = event.id
                if (id.isNotEmpty()) _chatMessages.value = _chatMessages.value.filter { it.toolCall?.toolCallId != id }
                _selectionRequest.value = event.selection
                removeThinkingIndicator()
                _chatStatusText.value = "Waiting for your choice"
            }

            "iterations_exceeded" -> {
                _pendingExtension.value = PendingExtension(event.summary)
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
                event.state?.let { applyTerminalState(it) }
            }
        }
    }

    /**
     * The authoritative session state that closes every stream. Live events
     * already rendered the content, so this only reconciles the prompts the
     * user still has to answer and clears stale ones.
     */
    private fun applyTerminalState(state: SessionState) {
        val status = state.status
        // Refreshed every turn: a suggestion accepted in this chat shows as done.
        if (state.hasContext) _sessionContext.value = state.context
        removeThinkingIndicator()
        _chatStatusText.value = ""
        if (inThreadNow()) {
            _memory.value = state.memory
            // The stored turn (stable ids, times, devices) replaces the live bubbles.
            if (state.messages.isNotEmpty()) applyThreadPage(state, state.messages)
        }
        state.undos?.let(::reconcileUndos)

        val pending = state.pendingApproval
        // The server stores "waiting_approval" while an approval is pending
        // (server/core/session_runner.py), so accept both spellings. Clear only when
        // the state carries no pending approval at all.
        if ((status == "pending_approval" || status == "waiting_approval") && pending != null) {
            val id = pending.toolCallId
            _pendingApproval.value = PendingApproval(
                toolName = pending.toolName ?: "Tool Execution",
                toolArgs = pending.toolArgs ?: "null",
                toolCallId = id
            )
            updateToolCall(id) {
                it.copy(requiresApproval = true, isRunning = false, isCollapsed = false, approvalReason = pending.reason.ifEmpty { it.approvalReason })
            }
        } else if (pending == null && _isStreaming.value) {
            _pendingApproval.value = null
        }

        val selection = state.selection
        if (status == "awaiting_selection" && selection != null) {
            _selectionRequest.value = selection
        } else if (_isStreaming.value) {
            _selectionRequest.value = null
        }

        if (status == "iterations_exceeded") {
            _pendingExtension.value = PendingExtension(state.extendSummary)
        } else if (_isStreaming.value) {
            _pendingExtension.value = null
        }

        // The error event usually said it already; a state frame adds a line only when it has news.
        val stateError = state.error
        if (status == "error" && stateError != null && _chatMessages.value.lastOrNull()?.message?.contains(stateError) != true) {
            appendMessage("Assistant", "Error: $stateError", false)
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
                ctx.api.chat.sendWithImage(message, id, selectedModel(), imageBytes, mimeType, onEvent, dev, screen)
            } else {
                ctx.api.chat.send(message, id, selectedModel(), onEvent, dev, screen)
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
                ctx.api.chat.sendWithImage(message, id, selectedModel(), jpeg, "image/jpeg", onEvent, dev, screen, outgoing.screen)
            } else {
                ctx.api.chat.send(message, id, selectedModel(), onEvent, dev, screen, outgoing.screen)
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
    private fun currentScreen(): io.github.ronganam.dana.ui.viewmodels.ScreenRef? =
        if (inThreadNow()) runCatching { screenProvider() }.getOrNull() else null

    /** A new turn starts: the previous turn's Undo row (and its outcomes) goes. */
    private fun newTurn() = undoRow.clear()

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
     * Content shared from another app: put it in the thread's input, ready to
     * send, so the user can add "summarize this" / "remind me" first.
     */
    fun startChatFromShare(text: String?, imageUri: Uri?) {
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

        streamAction(if (approved) "Executing..." else "Cancelling...", if (approved) "approve" else "deny", toolCallId = pending.toolCallId.ifEmpty { null })
    }

    fun sendSelection(indices: List<Int>) {
        if (_selectionRequest.value == null) return
        if (!ensureCanReachServer("Selection not sent")) return
        _selectionRequest.value = null
        streamAction(if (indices.isEmpty()) "Cancelling..." else "Submitting selection...", "select", selection = indices)
    }

    fun cancelSelection() = sendSelection(emptyList())

    /** Grant more iterations to a run that hit the step limit. */
    fun extendIterations() {
        if (_pendingExtension.value == null) return
        if (!ensureCanReachServer("Couldn't continue")) return
        _pendingExtension.value = null
        streamAction("Continuing with more steps...", "extend_iterations")
    }

    /** Stop an over-budget run and settle the session. */
    fun declineIterations() {
        if (_pendingExtension.value == null) return
        if (!ensureCanReachServer("Couldn't stop the run")) return
        _pendingExtension.value = null
        streamAction("Wrapping up...", "decline_iterations")
    }

    private fun streamAction(statusText: String, action: String, toolCallId: String? = null, selection: List<Int>? = null) {
        val payload = RunAction(sessionId, action, deviceParam(), toolCallId, selection)
        runAssistantStream(statusText) { onEvent -> ctx.api.chat.act(payload, onEvent) }
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
        val socketAtStart = voice.current()
        voiceVerifyJob?.cancel()
        voiceVerifyJob = ctx.scope.launch {
            if (!ServerStatusMonitor.ensureReachable() && voice.current() === socketAtStart && _isRecording.value) {
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
            ctx.api.chat.send(text, id, selectedModel(), onEvent, dev, screen)
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

    private fun beginServerRecording() {
        if (_isRecording.value) return
        _isRecording.value = true
        _chatStatusText.value = "Listening..."
        mic.reset()

        voice.open(ctx.api.chat.voiceRequest(serverUrl(), apiToken(), sessionId, selectedModel(), deviceParam()))

        recordingJob = ctx.scope.launch(Dispatchers.IO) {
            try {
                val silent = mic.capture(keepGoing = { _isRecording.value }, onChunk = voice::send)
                if (silent) withContext(Dispatchers.Main) { stopVoiceRecording() }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.e(TAG, "Voice recording failed", e)
                withContext(Dispatchers.Main) { abortVoice("Microphone error") }
            }
        }
    }

    /** The voice socket failed (it is already closed). */
    private fun onVoiceFailure(t: Throwable) {
        val runId = run.runId
        if (runId != null && !run.gotState) {
            // The run started on the server; keep following it over SSE.
            attachToRun(runId, run.lastSeq, resetTracker = false)
            return
        }
        val reason = if (t is IOException) describeError(t) else (t.message ?: "connection lost")
        abortVoice("Voice failed: $reason")
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
        mic.release()

        if (!mic.heardSpeech) {
            Log.d(TAG, "No speech detected; cancelling voice stream.")
            _chatStatusText.value = ""
            removeThinkingIndicator()
            closeVoiceSocket("No speech detected")
            return
        }

        runs.reset()
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
        voice.send(ChatApi.voiceStop(sessionId, MicCapture.SAMPLE_RATE, screen, outgoing?.screen))
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
        mic.release()
        voice.cancel()
        removeThinkingIndicator()
        _isStreaming.value = false
        _chatStatusText.value = statusText
    }

    private fun closeVoiceSocket(reason: String) = voice.close(reason)

    /**
     * One frame from the voice socket: the same events as the SSE stream
     * (`transcription`, the run's events, then a closing `state`), or
     * `no_speech` when nothing intelligible was said.
     */
    private fun handleVoiceEvent(event: RunEvent) {
        when (event.type) {
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
                handleAssistantEvent(event)
                _isStreaming.value = false
                settleBubbles()
                flushPendingPersist()
                closeVoiceSocket("Response received")
                finishRun(sessionId)
            }
            "error" -> if (event.code == "session_busy" && inThreadNow()) {
                voiceScreen?.let { screenAttachments?.putBack(it) }
                voiceScreen = null
                refuseBusy(null)
            } else handleAssistantEvent(event)
            else -> handleAssistantEvent(event)
        }
    }

    /** Release the mic and sockets; the host is going away. */
    fun release() {
        voiceGateJob?.cancel()
        voiceVerifyJob?.cancel()
        recordingJob?.cancel()
        cancelDeviceRecognition()
        _isRecording.value = false
        mic.release()
        voice.cancel()
        flushPendingPersist()
    }

    companion object {
        private const val TAG = "ChatController"
        /** After the user taps stop, how long to wait for the recognizer's final words. */
        private const val DEVICE_FINAL_TIMEOUT_MS = 2_500L
        private const val KEY_SESSION_ID = "chat_session_id"
        private const val DEFAULT_GAP_MINUTES = 20.0
        private const val BUSY_CLEAR = "Dana is still answering — try again when the reply is done."
        /** Thread messages per page (the server's own default for the state frame). */
        private const val THREAD_PAGE = 60
        /** A fresh view still shows a message stamped this much before it (clock skew). */
        private const val VIEW_FLOOR_MARGIN_MS = 5_000L
        private const val KEY_CHAT_MODEL = "chat_model"
        /** A successful check this recent is trusted without re-probing. */
        private const val VOICE_STATUS_FRESH_MS = 30_000L
    }
}
