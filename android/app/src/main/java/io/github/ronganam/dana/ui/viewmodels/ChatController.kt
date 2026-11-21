package io.github.ronganam.dana.ui.viewmodels

import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import android.util.Log
import androidx.core.content.edit
import io.github.ronganam.dana.R
import io.github.ronganam.dana.data.chat.ChatHistoryStore
import io.github.ronganam.dana.data.chat.ThreadModel
import io.github.ronganam.dana.data.chat.UndoItem
import io.github.ronganam.dana.data.models.ChatContext
import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.models.ModelOption
import io.github.ronganam.dana.data.models.PendingExtension
import io.github.ronganam.dana.data.models.SelectionRequest
import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.data.network.ChatApi
import io.github.ronganam.dana.data.network.Reachability
import io.github.ronganam.dana.data.network.RunAction
import io.github.ronganam.dana.data.network.RunEvent
import io.github.ronganam.dana.data.network.ServerStatusMonitor
import io.github.ronganam.dana.data.network.SessionBusyException
import io.github.ronganam.dana.data.network.SessionItem
import io.github.ronganam.dana.data.network.SessionState
import io.github.ronganam.dana.data.screen.OutgoingScreen
import io.github.ronganam.dana.data.screen.ScreenAttachments
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Request
import java.util.UUID

/**
 * The conversation the screens show. It owns which session is shown and
 * every request that starts or follows a run; the parts it composes each do
 * one job: [ChatTranscript] (the messages shown), [ChatRunRenderer] (run
 * events → what is shown), [ChatPrompts] (approval, choice, step limit),
 * [ThreadView] (the shared thread's pages and memory), [RunStreamClient]
 * (the stream and reattaching to it), [ChatVoice] (push-to-talk),
 * [ChatUndos] (the Undo row), [ChatCache] (the on-device copy),
 * [ChatModels] and [ChatComposer] (the input box).
 *
 * **Thread mode** (the main screen, the power-button sheet, kiosk and TV):
 * one conversation shared by every device, `session_id: "main"` (server
 * `server/docs/agent-actions.md`). It loads newest page first
 * (`GET /api/sessions/main`, older pages with [loadOlder]), stamps what it
 * sends with [device] and the on-screen item ([screenProvider]), keeps the
 * latest run's Undo buttons ([undos]) and marks replies that came from a
 * learned phrase. Before [initialize] it works with per-session chats and a
 * session list (another session can still be visited from the thread, e.g.
 * an approval waiting in a Telegram chat).
 *
 * Sending checks [ServerStatusMonitor] first, so the user isn't left typing
 * into a dead connection while the VPN is off.
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
    private val texts = ctx.texts
    private val transcript: ChatTranscript = ChatTranscript(persist = { cache.save(it) }, persistSoon = { cache.saveSoon() })
    private val cache: ChatCache = ChatCache(ChatHistoryStore(appContext, ctx.scope), prefs, ctx.scope, { transcript.value }, { sessionId to inThreadNow() })
    private val store get() = cache.store
    private val undoRow = ChatUndos(ctx, onThingsChanged)
    private val prompts = ChatPrompts(transcript)
    private val thread = ThreadView(transcript) { store.save(ThreadModel.MAIN, it.takeLast(ChatCache.THREAD_CACHE_MESSAGES)) }
    private val runs = RunStreamClient { ctx.api.chat }
    private val run: RunTracker get() = runs.run
    private val chatModels = ChatModels(ctx, prefs)
    private val composer = ChatComposer()

    private val _chatStatusText = MutableStateFlow("")
    val chatStatusText: StateFlow<String> = _chatStatusText.asStateFlow()

    private val _sessionContext = MutableStateFlow<ChatContext?>(null)
    /** What the current chat was opened from (call suggestion / call / task), if anything. */
    val sessionContext: StateFlow<ChatContext?> = _sessionContext.asStateFlow()

    private val _isStreaming = MutableStateFlow(false)
    val isStreaming: StateFlow<Boolean> = _isStreaming.asStateFlow()

    private val renderer = ChatRunRenderer(
        runs, transcript, prompts, thread, undoRow, texts, _chatStatusText, _sessionContext,
        inThread = ::inThreadNow,
        streaming = { _isStreaming.value },
        onUtterance = ::showUtterance,
        onToolEnded = { runChanged = true }
    )
    private val voice = ChatVoice(appContext, ctx.scope, texts, transcript, _chatStatusText, VoiceHost())

    var sessionId: String = ""
        private set

    private val _currentSessionId = MutableStateFlow("")
    val currentSessionId: StateFlow<String> = _currentSessionId.asStateFlow()

    val chatMessages: StateFlow<List<ChatMessage>> = transcript.messages

    private val _sessions = MutableStateFlow<List<SessionItem>>(emptyList())
    val sessions: StateFlow<List<SessionItem>> = _sessions.asStateFlow()

    val pendingApproval: StateFlow<PendingApproval?> = prompts.approval
    val selectionRequest: StateFlow<SelectionRequest?> = prompts.selection
    val pendingExtension: StateFlow<PendingExtension?> = prompts.extension

    val isRecording: StateFlow<Boolean> = voice.isRecording

    val pendingImageUri: StateFlow<Uri?> = composer.image
    /** Text to put in the input box (from a share, or a refused message given back); the screen consumes it. */
    val draftText: StateFlow<String?> = composer.draft

    /** Chat models the server offers (empty until fetched). */
    val models: StateFlow<List<ModelOption>> = chatModels.models
    /** Selected chat model id; empty means "server default". */
    val chatModel: StateFlow<String> = chatModels.selected

    private var streamJob: Job? = null
    private var loadHistoryJob: Job? = null

    // --- The shared thread ---

    private var threadMode = false

    /** Whether this controller talks in the shared thread (from [initialize] on). */
    val isThreadMode: Boolean get() = threadMode

    private val _inThread = MutableStateFlow(false)
    /** The shared thread is shown (false while another session is open, e.g. an approval from Telegram). */
    val inThread: StateFlow<Boolean> = _inThread.asStateFlow()

    /** Quiet longer than this starts a new conversation (the server's `gap_minutes`). */
    val gapMinutes: StateFlow<Double> = thread.gapMinutes
    /** The server has older thread messages than the ones shown. */
    val hasOlder: StateFlow<Boolean> = thread.hasOlder
    val loadingOlder: StateFlow<Boolean> = thread.loadingOlder
    /** Which thread messages Dana still has in mind (server `context_from_seq` / `context_expires_at`). */
    val memory: StateFlow<ThreadModel.Memory> = thread.memory

    private val _clearing = MutableStateFlow(false)
    /** "New topic" is on its way to the server. */
    val clearing: StateFlow<Boolean> = _clearing.asStateFlow()

    /** Undo buttons of the latest run (and the outcome of pressed ones). */
    val undos: StateFlow<List<UndoItem>> = undoRow.items

    /** What is on screen while the owner asks (set by the main screen; null elsewhere). */
    @Volatile var screenProvider: () -> ScreenRef? = { null }

    /**
     * The app under the power-button sheet, when the owner included it with
     * the "Screen" control (set by the sheet only; null in the app, kiosk, TV).
     * Taken once per message — typed, live-transcribed or server-transcribed.
     */
    @Volatile var screenAttachments: ScreenAttachments? = null

    /** A tool ran in the current run: other screens may need a refresh when it ends. */
    @Volatile private var runChanged = false
    /** The message was refused because another run is going (409): follow that one after this stream ends. */
    @Volatile private var followBusyRun = false

    private fun inThreadNow(): Boolean = threadMode && sessionId == ThreadModel.MAIN

    /** `device` to send: only in thread mode (the per-session chats never send one). */
    private fun deviceParam(): String? = device.takeIf { threadMode }

    fun initialize() {
        store.migrateFromPrefs(prefs)
        threadMode = true
        sessionId = ThreadModel.MAIN
        _currentSessionId.value = sessionId
        _inThread.value = inThreadNow()
        store.load(ThreadModel.MAIN)?.let { transcript.value = it }
        loadActiveSessionHistory()
        fetchSessions()
        fetchModels()
    }

    /** Back to the shared thread after looking at another conversation. */
    fun returnToThread() {
        if (threadMode && sessionId != ThreadModel.MAIN) loadSession(ThreadModel.MAIN)
    }

    // --- Model selection ---

    fun fetchModels() = chatModels.fetch()

    fun setChatModel(id: String) = chatModels.select(id)

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
        transcript.append(msg)
        return msg.id
    }

    private fun assistantLine(text: String) = appendMessage(ChatTranscript.ASSISTANT, text, false)

    fun toggleToolCallCollapse(messageId: String) = transcript.toggleCollapse(messageId)

    // --- Sessions ---

    fun fetchSessions() {
        // The shared thread has no session list.
        if (threadMode) return
        ctx.scope.launch {
            val fromServer = ctx.load("Sessions") { ctx.api.chat.sessions() }
            _sessions.value = fromServer ?: withContext(Dispatchers.IO) {
                // Offline: offer the transcripts cached on this device.
                val offline = texts.get(R.string.chat_offline_copy)
                store.listSessions().map {
                    SessionItem(it.id, it.lastModified, it.snippet, status = "idle", title = offline)
                }
            }
        }
    }

    /** Detach from everything tied to the current session before switching. */
    private fun detachFromSession() {
        endRealtimeCall()
        if (voice.busy) voice.abort(statusText = "")
        loadHistoryJob?.cancel()
        loadHistoryJob = null
        // The server keeps running the task; we only stop rendering it.
        streamJob?.cancel()
        streamJob = null
        _isStreaming.value = false
        cache.flush()
        prompts.clear()
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
            transcript.value = emptyList()
            undoRow.clear()
            _chatStatusText.value = ""
            _sessionContext.value = null
            thread.startFresh()
            cache.markActive()
            onSessionChanged()
            return
        }
        detachFromSession()
        sessionId = UUID.randomUUID().toString()
        _currentSessionId.value = sessionId
        prefs.edit {
            putString(KEY_SESSION_ID, sessionId)
            putLong(ChatCache.KEY_LAST_ACTIVE, System.currentTimeMillis())
        }
        transcript.value = emptyList()
        _chatStatusText.value = ""
        _sessionContext.value = null

        val newId = sessionId
        val title = texts.get(R.string.chat_new_chat)
        ctx.scope.launch {
            ctx.load("CreateSession") { ctx.api.chat.createSession(newId, title) }
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
        if (!threadMode) prefs.edit { putString(KEY_SESSION_ID, sessionId) }
        thread.showAll()
        undoRow.clear()
        transcript.value = if (inThreadNow()) store.load(ThreadModel.MAIN).orEmpty() else emptyList()
        _sessionContext.value = null
        loadActiveSessionHistory()
        onSessionChanged()
    }

    /** One GET of what is shown: the newest thread page, or the whole session. */
    private suspend fun fetchShown(id: String): SessionState =
        if (threadMode && id == ThreadModel.MAIN) ctx.api.chat.thread(ThreadView.PAGE) else ctx.api.chat.session(id)

    private fun loadActiveSessionHistory() {
        loadHistoryJob?.cancel()
        renderer.thinking()

        val id = sessionId
        loadHistoryJob = ctx.scope.launch {
            try {
                showSession(fetchShown(id))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.w(TAG, "Loading session from server failed; using local copy: $e")
                transcript.removeThinking()
                val cached = withContext(Dispatchers.IO) { store.load(id) }
                if (cached != null && sessionId == id) transcript.value = cached
            }
        }
    }

    /** A loaded session over what is shown; follows its run when one is in flight. */
    private fun showSession(state: SessionState) {
        if (state.hasContext) _sessionContext.value = state.context
        val serverMessages = state.messages

        if (inThreadNow()) {
            thread.applyPage(state, serverMessages)
        } else if (serverMessages.isNotEmpty() || transcript.value.isEmpty()) {
            // Only replace local messages if the server returned some. POST-style
            // responses carry no messages array and must not wipe the chat.
            transcript.value = serverMessages
        }
        renderer.reconcileUndos(state)
        prompts.fromSession(state)

        val activeRun = state.activeRunId
        if (state.status == "running" && activeRun != null) {
            // A run is in flight but this client isn't attached to its stream
            // (reload, app resumed, session switch): replay it and follow live.
            if (!_isStreaming.value) attachToRun(activeRun)
        } else if (!_isStreaming.value) {
            transcript.removeThinking()
            _chatStatusText.value = ""
        }

        if (serverMessages.isNotEmpty() && !inThreadNow()) {
            store.save(sessionId, serverMessages)
        }
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
            report(texts.get(R.string.chat_busy_clear))
            return
        }
        _clearing.value = true
        ctx.scope.launch {
            try {
                val state = ctx.api.chat.clearThread(device)
                if (!inThreadNow()) return@launch
                showSession(state)
                // An older server's payload says nothing: Dana remembers nothing now.
                if (thread.memory.value == ThreadModel.Memory.Unknown) thread.setMemory(ThreadModel.Memory.Known(null, null))
                report(texts.get(R.string.chat_forgot))
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                report(if (e.code == 409) texts.get(R.string.chat_busy_clear) else texts.get(R.string.chat_new_topic_failed, e.message.orEmpty()))
            } catch (e: Exception) {
                report(texts.get(R.string.chat_new_topic_failed, texts.reason(e)))
            } finally {
                _clearing.value = false
            }
        }
    }

    /** Scrolled to the top of the thread: fetch the page before the oldest one shown. */
    fun loadOlder() {
        if (!inThreadNow()) return
        val cursor = thread.olderToLoad() ?: return
        thread.setLoadingOlder(true)
        ctx.scope.launch {
            try {
                val page = ctx.load("ThreadOlder") { ctx.api.chat.thread(ThreadView.PAGE, cursor) } ?: return@launch
                if (!inThreadNow()) return@launch
                thread.prependOlder(page)
            } finally {
                thread.setLoadingOlder(false)
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
        if (voice.busy) return
        val id = sessionId
        if (id.isEmpty()) return
        ctx.scope.launch {
            val state = ctx.load("ResyncSession") { fetchShown(id) } ?: return@launch
            if (sessionId == id && !_isStreaming.value) showSession(state)
        }
        fetchSessions()
    }

    // --- Following a run (Server-Sent Events) ---

    /** The run's bookkeeping starts over, nothing has changed yet, no busy run to follow. */
    private fun beginRun(resetTracker: Boolean = true) {
        if (resetTracker) runs.reset()
        runChanged = false
        followBusyRun = false
        _isStreaming.value = true
    }

    /**
     * Run an assistant request and render its run live. The server streams the
     * run's events (each with `run_id` + `seq`) and always closes with a `state`
     * frame, so every terminal case (approval, selection, step limit, error,
     * cancel) is handled by [ChatRunRenderer]. If the connection drops mid-run
     * [RunStreamClient] reattaches and resumes after the last seen `seq`.
     */
    private fun runAssistantStream(
        statusText: String,
        resetTracker: Boolean = true,
        /** A new message: on a 409 in the thread its text goes back to the ask bar. */
        sent: SentMessage? = null,
        block: RunRequest
    ) {
        streamJob?.cancel()
        beginRun(resetTracker)
        _chatStatusText.value = statusText
        renderer.thinking(statusText)
        cache.flush()

        val id = sessionId
        streamJob = ctx.scope.launch {
            try {
                followRun(id, sent, block)
            } finally {
                if (sessionId == id) {
                    _isStreaming.value = false
                    renderer.settleBubbles()
                }
                cache.flush()
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
        runAssistantStream(texts.get(R.string.chat_dana_thinking), resetTracker) { onEvent ->
            ctx.api.chat.attach(id, runId, afterSeq, onEvent)
        }
    }

    private suspend fun followRun(id: String, sent: SentMessage?, first: RunRequest) =
        runs.follow(id, first, object : RunStreamClient.Listener {
            override fun onEvent(event: RunEvent) = renderer.render(event)

            override fun onBusy(): Boolean {
                if (sent == null || !inThreadNow()) return false
                // Another device's (or an earlier) request is still running in the
                // thread: nothing was sent. Put the text back and show that run.
                refuseBusy(sent)
                return true
            }

            override fun onFollowingBusy() = assistantLine(texts.get(R.string.chat_following_busy))

            override fun onReconnecting() {
                _chatStatusText.value = texts.get(R.string.chat_reconnecting_status)
                renderer.thinking(texts.get(R.string.chat_reconnecting))
            }

            override fun onFailed(e: Exception) = reportStreamFailure(e)

            override fun onCancelled() = transcript.removeThinking()

            override fun stillShowing(): Boolean = sessionId == id
        })

    private fun refuseBusy(sent: SentMessage?) {
        transcript.removeThinking()
        if (sent != null) {
            transcript.remove(sent.bubbleId)
            if (sent.image != null) composer.setImage(sent.image)
            sent.screen?.let { screenAttachments?.putBack(it) }
            composer.setDraft(sent.text)
        }
        _chatStatusText.value = ""
        ctx.say(if (sent != null) R.string.chat_busy_message_back else R.string.chat_busy_say_again)
        followBusyRun = true
    }

    private fun reportStreamFailure(e: Exception) {
        transcript.removeThinking()
        val reason = if (e is SessionBusyException) e.message.orEmpty() else texts.reason(e)
        _chatStatusText.value = if (run.runId != null) texts.get(R.string.chat_lost_connection) else texts.get(R.string.chat_connection_error, reason)
        if (run.runId == null) assistantLine(texts.get(R.string.chat_no_reply, reason))
    }

    fun stopAssistant() {
        if (!_isStreaming.value) return
        val id = sessionId
        streamJob?.cancel()
        voice.closeSocket("Stopped")
        ctx.scope.launch {
            ctx.load("CancelAssistant") { ctx.api.chat.cancel(id) }
        }
        transcript.removeThinking()
        renderer.settleBubbles()
        _isStreaming.value = false
        _chatStatusText.value = texts.get(R.string.chat_stopped_status)
        prompts.clear()
    }

    // --- Sending ---

    /**
     * Returns false (and keeps nothing) when the message wasn't sent because the
     * server is known to be unreachable. The caller should then keep the user's
     * text in the input box.
     */
    fun sendChatMessage(message: String): Boolean {
        if (!ensureCanReachServer(R.string.chat_not_sent)) return false

        val outgoing = screenAttachments?.take()
        if (outgoing != null) {
            sendWithScreen(message, outgoing, bubbleId = null)
            return true
        }
        val imageUri = composer.takeImage()
        newTurn()
        val bubbleId = appendAndGetId(ChatTranscript.YOU, message, true, listOfNotNull(imageUri?.toString()))

        val id = sessionId
        val screen = currentScreen()
        val dev = deviceParam()
        runAssistantStream(texts.get(R.string.chat_sending), sent = SentMessage(message, bubbleId, imageUri)) { onEvent ->
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
                ctx.api.chat.sendWithImage(message, id, chatModels.forRequest(), imageBytes, mimeType, onEvent, dev, screen)
            } else {
                ctx.api.chat.send(message, id, chatModels.forRequest(), onEvent, dev, screen)
            }
        }
        return true
    }

    /** The user bubble of a message sent with a screen ([outgoing]): its screenshot and app name. */
    private fun screenBubble(message: String, outgoing: OutgoingScreen?): ChatMessage = ChatMessage(
        sender = ChatTranscript.YOU, message = message, isUser = true,
        images = listOfNotNull(outgoing?.jpeg?.let { screenBubbleImage(it) }),
        screen = outgoing?.label.orEmpty().ifBlank { texts.get(R.string.chat_screen) },
        timestamp = System.currentTimeMillis(), device = deviceParam().orEmpty()
    )

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
        val shownId = transcript.settleUser(bubbleId, screenBubble(message, outgoing))

        val id = sessionId
        val screen = currentScreen()
        runAssistantStream(texts.get(R.string.chat_sending), sent = SentMessage(message, shownId, screen = outgoing)) { onEvent ->
            val jpeg = outgoing.jpeg
            if (jpeg != null) {
                ctx.api.chat.sendWithImage(message, id, chatModels.forRequest(), jpeg, "image/jpeg", onEvent, dev, screen, outgoing.screen)
            } else {
                ctx.api.chat.send(message, id, chatModels.forRequest(), onEvent, dev, screen, outgoing.screen)
            }
        }
    }

    /**
     * Words recognised on the phone: settle the live bubble [bubbleId] into
     * the user's message and ask Dana (with the sheet's screen when it is
     * included). False when the server is known to be unreachable.
     */
    private fun sendSpoken(text: String, bubbleId: String?): Boolean {
        if (!ensureCanReachServer(R.string.chat_not_sent)) return false
        val outgoing = screenAttachments?.take()
        if (outgoing != null) {
            sendWithScreen(text, outgoing, bubbleId)
            return true
        }
        newTurn()
        val dev = deviceParam()
        val settled = ChatMessage(sender = ChatTranscript.YOU, message = text, isUser = true, timestamp = System.currentTimeMillis(), device = dev.orEmpty())
        val settledId = transcript.settleUser(bubbleId, settled)

        val id = sessionId
        val screen = currentScreen()
        runAssistantStream(texts.get(R.string.chat_sending), sent = SentMessage(text, settledId)) { onEvent ->
            ctx.api.chat.send(text, id, chatModels.forRequest(), onEvent, dev, screen)
        }
        return true
    }

    /** The server transcribed an utterance from the voice socket: the user's bubble (with the screen sent with it). */
    private fun showUtterance(text: String) {
        val sentScreen = voice.takeSentScreen()
        if (sentScreen != null) {
            transcript.appendSoon(screenBubble(text, sentScreen))
        } else {
            appendMessage(ChatTranscript.YOU, text, true)
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
    private fun currentScreen(): ScreenRef? =
        if (inThreadNow()) runCatching { screenProvider() }.getOrNull() else null

    /** A new turn starts: the previous turn's Undo row (and its outcomes) goes. */
    private fun newTurn() = undoRow.clear()

    /**
     * Refuse to start work while the server is known to be unreachable, and
     * re-check right away so the banner clears as soon as the VPN is back.
     * [action] names what didn't happen ("Message not sent").
     */
    private fun ensureCanReachServer(action: Int): Boolean {
        val status = ServerStatusMonitor.status.value
        return when (status.reachability) {
            Reachability.NOT_CONFIGURED -> {
                ctx.say(R.string.chat_set_server_first, texts.get(action))
                false
            }
            Reachability.OFFLINE -> {
                ServerStatusMonitor.checkNow()
                ctx.say(R.string.error_with_reason, texts.get(action), status.detail ?: texts.get(R.string.server_unreachable))
                false
            }
            else -> true
        }
    }

    fun clearPendingImage() = composer.setImage(null)

    fun consumeDraft() = composer.consumeDraft()

    /**
     * Content shared from another app: put it in the thread's input, ready to
     * send, so the user can add "summarize this" / "remind me" first.
     */
    fun startChatFromShare(text: String?, imageUri: Uri?) {
        composer.setImage(imageUri)
        composer.setDraft(text?.trim()?.takeIf { it.isNotEmpty() })
    }

    fun setPendingImage(uri: Uri?) = composer.setImage(uri)

    // --- Answers to what the run asks ---

    fun approveAction(approved: Boolean) {
        val pending = prompts.currentApproval() ?: return
        if (!ensureCanReachServer(if (approved) R.string.chat_couldnt_approve else R.string.chat_couldnt_deny)) return
        prompts.answerApproval(approved)
        streamAction(
            texts.get(if (approved) R.string.chat_executing else R.string.chat_cancelling),
            if (approved) "approve" else "deny",
            toolCallId = pending.toolCallId.ifEmpty { null }
        )
    }

    fun sendSelection(indices: List<Int>) {
        if (!prompts.hasSelection()) return
        if (!ensureCanReachServer(R.string.chat_selection_not_sent)) return
        prompts.takeSelection()
        streamAction(texts.get(if (indices.isEmpty()) R.string.chat_cancelling else R.string.chat_submitting_selection), "select", selection = indices)
    }

    fun cancelSelection() = sendSelection(emptyList())

    /** Grant more iterations to a run that hit the step limit. */
    fun extendIterations() {
        if (!prompts.hasExtension()) return
        if (!ensureCanReachServer(R.string.chat_couldnt_continue)) return
        prompts.clearExtension()
        streamAction(texts.get(R.string.chat_continuing), "extend_iterations")
    }

    /** Stop an over-budget run and settle the session. */
    fun declineIterations() {
        if (!prompts.hasExtension()) return
        if (!ensureCanReachServer(R.string.chat_couldnt_stop_run)) return
        prompts.clearExtension()
        streamAction(texts.get(R.string.chat_wrapping_up), "decline_iterations")
    }

    private fun streamAction(statusText: String, action: String, toolCallId: String? = null, selection: List<Int>? = null) {
        val payload = RunAction(sessionId, action, deviceParam(), toolCallId, selection)
        runAssistantStream(statusText) { onEvent -> ctx.api.chat.act(payload, onEvent) }
    }

    // --- Voice (push-to-talk, [ChatVoice]) ---

    fun startVoiceRecording() = voice.start()

    fun stopVoiceRecording() = voice.stop()

    private inner class VoiceHost : ChatVoice.Host {
        override fun endRealtimeCall() = this@ChatController.endRealtimeCall()
        override fun screens(): ScreenAttachments? = screenAttachments
        override fun inThread(): Boolean = inThreadNow()
        override fun device(): String = deviceParam().orEmpty()
        override fun sendSpoken(text: String, bubbleId: String?): Boolean = this@ChatController.sendSpoken(text, bubbleId)
        override fun voiceRequest(): Request =
            ctx.api.chat.voiceRequest(serverUrl(), apiToken(), sessionId, chatModels.forRequest(), deviceParam())

        override fun startVoiceRun(screen: OutgoingScreen?): String {
            beginRun()
            val transcribing = texts.get(R.string.voice_transcribing)
            _chatStatusText.value = transcribing
            renderer.thinking(transcribing)
            return ChatApi.voiceStop(sessionId, MicCapture.SAMPLE_RATE, currentScreen(), screen?.screen)
        }

        override fun followDroppedRun(): Boolean {
            val runId = run.runId
            if (runId == null || run.gotState) return false
            attachToRun(runId, run.lastSeq, resetTracker = false)
            return true
        }

        override fun onVoiceEvent(event: RunEvent) = renderer.render(event)

        override fun voiceRunEnded() {
            _isStreaming.value = false
            renderer.settleBubbles()
            cache.flush()
            finishRun(sessionId)
        }

        override fun voiceRefusedBusy() = refuseBusy(null)

        override fun voiceIdle() {
            transcript.removeThinking()
            _isStreaming.value = false
        }
    }

    /** Release the mic and sockets; the host is going away. */
    fun release() {
        voice.release()
        cache.flush()
    }

    companion object {
        private const val TAG = "ChatController"
        private const val KEY_SESSION_ID = "chat_session_id"
    }
}
