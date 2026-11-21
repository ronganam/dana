package io.github.ronganam.dana.wear.ui

import io.github.ronganam.dana.protocol.WearPaths
import io.github.ronganam.dana.protocol.WearReply
import io.github.ronganam.dana.protocol.WearRunAction
import io.github.ronganam.dana.protocol.WearSendMessage
import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import android.app.Application
import androidx.annotation.StringRes
import io.github.ronganam.dana.wear.R
import androidx.lifecycle.viewModelScope
import android.os.SystemClock
import io.github.ronganam.dana.wear.data.PhoneNode
import io.github.ronganam.dana.wear.data.WearPrefs
import io.github.ronganam.dana.wear.perf.WearPerf
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import com.google.android.gms.wearable.MessageClient
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.util.UUID

data class WearToolApproval(
    val toolName: String,
    val summary: String,
    val parameters: String
)

data class WearChatMessage(
    val id: String,
    val text: String,
    val isUser: Boolean,
    val isToolCall: Boolean = false,
    val toolName: String? = null,
    val toolArgs: String? = null
)

data class WearSelectionOption(
    val label: String,
    val value: String
)

data class WearSelectionRequest(
    val prompt: String,
    val options: List<WearSelectionOption>,
    val multiple: Boolean = false
)

/** A run that hit its step limit and is waiting on the user's decision. */
data class WearPendingExtension(
    val summary: String
)

class WearViewModel(app: Application) : AndroidViewModel(app), MessageClient.OnMessageReceivedListener {
    private fun str(@StringRes id: Int): String = getApplication<Application>().getString(id)


    private lateinit var appContext: Context
    private lateinit var prefs: WearPrefs

    private val _statusText = MutableStateFlow(str(R.string.status_ready))
    val statusText: StateFlow<String> = _statusText.asStateFlow()

    private val _isRecording = MutableStateFlow(false)
    val isRecording: StateFlow<Boolean> = _isRecording.asStateFlow()

    private val _lastAssistantResponse = MutableStateFlow(str(R.string.tap_mic_to_speak))
    val lastAssistantResponse: StateFlow<String> = _lastAssistantResponse.asStateFlow()

    private val _chatMessages = MutableStateFlow<List<WearChatMessage>>(
        listOf(WearChatMessage(UUID.randomUUID().toString(), str(R.string.tap_mic_to_start), false))
    )
    val chatMessages: StateFlow<List<WearChatMessage>> = _chatMessages.asStateFlow()

    private val _pendingApproval = MutableStateFlow<WearToolApproval?>(null)
    val pendingApproval: StateFlow<WearToolApproval?> = _pendingApproval.asStateFlow()

    private val _pendingSelection = MutableStateFlow<WearSelectionRequest?>(null)
    val pendingSelection: StateFlow<WearSelectionRequest?> = _pendingSelection.asStateFlow()

    private val _pendingExtension = MutableStateFlow<WearPendingExtension?>(null)
    val pendingExtension: StateFlow<WearPendingExtension?> = _pendingExtension.asStateFlow()

    private val _currentSessionId = MutableStateFlow("")
    val currentSessionId: StateFlow<String> = _currentSessionId.asStateFlow()

    private var initialized = false

    private var awaitingReplySince = 0L

    /**
     * A request is with the phone/server and its reply hasn't arrived yet.
     * Goes stale after [REPLY_STALE_MS] so a lost reply can't block
     * auto-listening forever.
     */
    private var awaitingReply: Boolean
        get() = awaitingReplySince > 0 && System.currentTimeMillis() - awaitingReplySince < REPLY_STALE_MS
        set(value) {
            awaitingReplySince = if (value) System.currentTimeMillis() else 0L
        }

    /**
     * The conversation: always Dana's one shared thread (`main`). The phone
     * bridge sends it with `device: "watch"`, so the phone, the Mac and the
     * watch all read the same conversation.
     */
    private val sessionId: String get() = THREAD_ID

    fun initialize(context: Context) {
        if (initialized) return
        initialized = true
        appContext = context.applicationContext
        prefs = WearPrefs(appContext)

        _chatMessages.value = listOf(
            WearChatMessage(UUID.randomUUID().toString(), str(R.string.tap_mic_to_start), false)
        )
        _lastAssistantResponse.value = str(R.string.tap_mic_to_speak)
        _statusText.value = str(R.string.status_ready)

        // Register message listener to receive phone replies
        Wearable.getMessageClient(appContext).addListener(this)
        PhoneNode.prefetch(appContext)

        // Tell the phone which watch-app version we run (for update
        // awareness), off the press-to-listen path.
        viewModelScope.launch {
            delay(5_000)
            io.github.ronganam.dana.wear.data.WearVersionPublisher.publish(appContext)
        }
    }

    private fun markActivity() {
        prefs.lastConversationActivityAt = System.currentTimeMillis()
    }

    /**
     * Whether the user is in the middle of a conversation: speaking, waiting
     * for a reply, answering a prompt, or the last exchange was moments ago.
     */
    private fun isMidConversation(): Boolean {
        if (_isRecording.value || awaitingReply) return true
        if (_pendingApproval.value != null || _pendingSelection.value != null || _pendingExtension.value != null) return true
        val idleFor = System.currentTimeMillis() - prefs.lastConversationActivityAt
        return prefs.lastConversationActivityAt > 0 && idleFor < CONVERSATION_IDLE_MS
    }

    /**
     * The app was opened (launched or brought back). Start listening in a new
     * conversation right away, unless the user is in the middle of one; then
     * keep it (and reload it if the app process was restarted).
     */
    fun onAppOpened(canListen: Boolean) {
        if (!initialized) return
        if (isMidConversation()) {
            if (_currentSessionId.value.isEmpty()) {
                // Fresh process: pick the conversation back up.
                _currentSessionId.value = THREAD_ID
                _statusText.value = str(R.string.status_loading_chat)
                loadSessionHistory(THREAD_ID)
            }
            return
        }
        if (canListen) {
            startNewSessionAndListen(handsFree = true)
        } else {
            startNewChat()
        }
    }

    fun reloadConfiguration() {
        // Only refresh config from prefs — do NOT change session or load history
        _statusText.value = str(R.string.status_ready)
    }

    private fun getPhoneNodeId(action: (String) -> Unit) {
        PhoneNode.get(appContext) { nodeId ->
            if (nodeId != null) action(nodeId) else _statusText.value = str(R.string.phone_not_found)
        }
    }

    /** Sends [path] to the phone; a failed send drops the cached node id. */
    private fun sendToPhone(path: String, data: ByteArray?) {
        getPhoneNodeId { phoneId ->
            Wearable.getMessageClient(appContext).sendMessage(phoneId, path, data)
                .addOnFailureListener {
                    Log.w("WearViewModel", "Send $path failed", it)
                    PhoneNode.invalidate()
                }
        }
    }

    private fun loadSessionHistory(sessionId: String) {
        sendToPhone(WearPaths.GET_SESSION_DETAIL, sessionId.toByteArray())
    }

    /** Show the latest turns of the shared thread (from the phone, any device). */
    fun showThread() {
        _currentSessionId.value = THREAD_ID
        markActivity()
        _pendingApproval.value = null
        _statusText.value = str(R.string.status_loading_chat)
        loadSessionHistory(THREAD_ID)
    }

    /** A clean screen; the thread itself goes on (the server separates conversations by time). */
    fun startNewChat() {
        _currentSessionId.value = THREAD_ID
        resetConversationState()
        _lastAssistantResponse.value = str(R.string.tap_mic_to_speak)
        _statusText.value = str(R.string.status_ready)
    }

    /**
     * Start listening with a clean screen. Nothing is created on the phone or
     * the server: every turn goes to the shared thread.
     *
     * @param handsFree the turn was started by opening the app or the assist
     *   button rather than a tap on the mic: it ends by itself (see
     *   [startVoiceRecording]).
     */
    fun startNewSessionAndListen(handsFree: Boolean = true) {
        if (_isRecording.value) return // already listening (button pressed twice)
        // Mic first: every millisecond before the recorder runs loses words.
        _currentSessionId.value = THREAD_ID
        if (awaitingReply) {
            _pendingApproval.value = null
            _pendingSelection.value = null
            _pendingExtension.value = null
        } else {
            resetConversationState()
        }
        startVoiceRecording(handsFree)
    }

    private fun resetConversationState() {
        awaitingReply = false
        _pendingApproval.value = null
        _pendingSelection.value = null
        _pendingExtension.value = null
        _lastAssistantResponse.value = str(R.string.say_something)
        _chatMessages.value = emptyList()
    }

    /** One push-to-talk utterance: mic capture → buffered chunks → phone. */
    private class VoiceTurn(val sessionId: String, val handsFree: Boolean) {
        /** PCM batches waiting for the phone; filled from the first mic frame. */
        val chunks = Channel<ByteArray>(Channel.UNLIMITED)
        @Volatile var stopRequested = false
        @Volatile var heardSpeech = false
        /** [WearPaths.VOICE_STOP] (send what was said) or [WearPaths.VOICE_CANCEL]. */
        @Volatile var endPath: String? = null
    }

    private var voiceTurn: VoiceTurn? = null

    /**
     * Starts the mic at once and streams to the phone as soon as its node id
     * is known; audio captured before that (node lookup, phone wake-up) is
     * buffered, not lost. Capture runs on its own urgent-audio thread, so the
     * main thread only flips state.
     *
     * @param handsFree the turn ends by itself: cancelled without sending if
     *   nothing loud enough is heard within [NO_SPEECH_TIMEOUT_MS] (opening
     *   the app just to read doesn't leave the mic streaming), and sent after
     *   [END_SILENCE_MS] of quiet following speech. A tap on the mic starts a
     *   turn that runs until the user taps Stop.
     */
    @SuppressLint("MissingPermission")
    fun startVoiceRecording(handsFree: Boolean = false) {
        if (_isRecording.value) return
        if (!handsFree) WearPerf.start("mic tap")
        _isRecording.value = true
        markActivity()
        _statusText.value = str(R.string.status_listening)

        val turn = VoiceTurn(sessionId, handsFree)
        voiceTurn = turn
        Thread({ captureAudio(turn) }, "dana-mic").start()
        tick() // feedback at the press, while the mic opens
        viewModelScope.launch(Dispatchers.IO) { streamToPhone(turn) }
    }

    @SuppressLint("MissingPermission")
    private fun captureAudio(turn: VoiceTurn) {
        android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_AUDIO)
        var recorder: AudioRecord? = null
        try {
            val minBuffer = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            // Room for ~0.5 s so a busy moment can't overrun the recorder.
            recorder = AudioRecord(
                MediaRecorder.AudioSource.MIC,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minBuffer, SAMPLE_RATE)
            )
            check(recorder.state == AudioRecord.STATE_INITIALIZED) { "AudioRecord not initialized" }
            recorder.startRecording()
            WearPerf.mark("mic opened")

            val frame = ByteArray(FRAME_BYTES)
            var batch = java.io.ByteArrayOutputStream(BATCH_BYTES)
            val startedAt = SystemClock.uptimeMillis()
            var loudRun = 0
            var quietMs = 0L
            var first = true
            var firstFrameAt = 0L
            val levels = ArrayList<Int>(512)

            while (!turn.stopRequested) {
                val read = recorder.read(frame, 0, frame.size)
                if (read <= 0) {
                    if (read < 0) throw IllegalStateException("AudioRecord.read = $read")
                    continue
                }
                if (first) {
                    first = false
                    firstFrameAt = SystemClock.uptimeMillis()
                    WearPerf.mark("first audio frame")
                }
                batch.write(frame, 0, read)
                if (batch.size() >= BATCH_BYTES) {
                    turn.chunks.trySend(batch.toByteArray())
                    batch = java.io.ByteArrayOutputStream(BATCH_BYTES)
                }

                if (!turn.handsFree) continue
                val rms = pcmRms(frame, read)
                if (levels.size < 4000) levels += rms.toInt()
                // The haptic tick and the recorder's start-up transient
                // aren't speech.
                if (SystemClock.uptimeMillis() - firstFrameAt < SPEECH_GATE_MS) continue
                val loud = rms > SPEECH_RMS
                if (loud) {
                    loudRun++
                    quietMs = 0
                    if (!turn.heardSpeech && loudRun >= SPEECH_MIN_LOUD_FRAMES) {
                        turn.heardSpeech = true
                        WearPerf.mark("speech heard")
                    }
                } else {
                    loudRun = 0
                    if (turn.heardSpeech) quietMs += read * 1000L / (SAMPLE_RATE * 2)
                }
                val heardSpeech = turn.heardSpeech
                val elapsed = SystemClock.uptimeMillis() - startedAt
                when {
                    !heardSpeech && elapsed > NO_SPEECH_TIMEOUT_MS -> {
                        viewModelScope.launch { cancelVoiceRecording(str(R.string.heard_nothing)) }
                        break
                    }
                    heardSpeech && quietMs >= END_SILENCE_MS -> {
                        WearPerf.mark("end of speech")
                        viewModelScope.launch { stopVoiceRecording() }
                        break
                    }
                    elapsed > AUTO_LISTEN_MAX_MS -> {
                        viewModelScope.launch { stopVoiceRecording() }
                        break
                    }
                }
            }
            if (batch.size() > 0) turn.chunks.trySend(batch.toByteArray())
            if (levels.isNotEmpty()) {
                val sorted = levels.sorted()
                fun pct(p: Int) = sorted[(sorted.size - 1) * p / 100]
                WearPerf.note("voice turn levels: p10=${pct(10)} p50=${pct(50)} p90=${pct(90)} max=${sorted.last()} threshold=${SPEECH_RMS.toInt()} speech=${turn.heardSpeech} frames=${sorted.size} first=${levels.take(50).joinToString(",")}")
            }
        } catch (e: Exception) {
            Log.e("WearViewModel", "Voice recording error", e)
            turn.endPath = WearPaths.VOICE_CANCEL
            viewModelScope.launch {
                if (voiceTurn === turn) {
                    _statusText.value = str(R.string.status_mic_error)
                    _isRecording.value = false
                }
            }
        } finally {
            try {
                recorder?.stop()
            } catch (e: Exception) {
                android.util.Log.w("WearViewModel", "Recorder stop failed (nothing recorded?)", e)
            }
            recorder?.release()
            turn.chunks.close()
        }
    }

    /** Sends the turn's audio to the phone, in order, then how it ended. */
    private suspend fun streamToPhone(turn: VoiceTurn) {
        val client = Wearable.getMessageClient(appContext)
        val phoneId = PhoneNode.await(appContext)
        if (phoneId == null) {
            withContext(Dispatchers.Main) {
                if (voiceTurn === turn && _isRecording.value) cancelVoiceRecording(str(R.string.phone_not_found))
            }
            return
        }
        WearPerf.mark("phone node")
        try {
            client.sendMessage(phoneId, WearPaths.VOICE_START, turn.sessionId.toByteArray()).addOnFailureListener { PhoneNode.invalidate() }
            var sentAny = false
            for (chunk in turn.chunks) {
                if (turn.endPath == WearPaths.VOICE_CANCEL) continue // drain without sending
                client.sendMessage(phoneId, WearPaths.VOICE_CHUNK, chunk)
                if (!sentAny) {
                    sentAny = true
                    WearPerf.mark("first audio sent")
                }
            }
            client.sendMessage(phoneId, turn.endPath ?: WearPaths.VOICE_CANCEL, turn.sessionId.toByteArray())
        } catch (e: Exception) {
            Log.e("WearViewModel", "Streaming voice to phone failed", e)
        }
    }

    /** A short haptic tick when listening starts. */
    private fun tick() {
        try {
            appContext.getSystemService(android.os.Vibrator::class.java)
                ?.vibrate(android.os.VibrationEffect.createPredefined(android.os.VibrationEffect.EFFECT_TICK))
        } catch (e: Exception) {
            android.util.Log.w("WearViewModel", "No haptic tick", e)
        }
    }

    /**
     * The app left the screen. A recording app in the background only gets
     * silence, and an old turn left running kept the next open from
     * listening at all (it counted as "mid-conversation"): send what was
     * said, or drop a turn that heard nothing.
     */
    fun onAppHidden() {
        val turn = voiceTurn ?: return
        if (!_isRecording.value) return
        if (turn.heardSpeech || !turn.handsFree) stopVoiceRecording()
        else cancelVoiceRecording(str(R.string.tap_mic_to_talk))
    }

    /** Stop listening without sending anything to the assistant. */
    private fun cancelVoiceRecording(status: String) {
        if (!_isRecording.value) return
        _isRecording.value = false
        voiceTurn?.let {
            it.endPath = WearPaths.VOICE_CANCEL
            it.stopRequested = true
        }
        _statusText.value = status
    }

    fun stopVoiceRecording() {
        if (!_isRecording.value) return
        WearPerf.mark("stop")
        _isRecording.value = false
        awaitingReply = true
        markActivity()
        voiceTurn?.let {
            it.endPath = WearPaths.VOICE_STOP
            it.stopRequested = true
        }
        _statusText.value = str(R.string.status_processing_voice)
    }

    fun sendTextMessage(text: String) {
        if (text.isBlank()) return
        _statusText.value = str(R.string.status_thinking)
        awaitingReply = true
        markActivity()

        val userMsg = WearChatMessage(UUID.randomUUID().toString(), text, true)
        _chatMessages.value = _chatMessages.value + userMsg

        sendToPhone(WearPaths.SEND_MESSAGE, WearSendMessage(sessionId, text).encode().toByteArray())
    }

    fun approveAction(approved: Boolean) {
        _statusText.value = if (approved) str(R.string.status_executing) else str(R.string.status_denying)
        _pendingApproval.value = null
        awaitingReply = true
        markActivity()

        sendAction(WearRunAction(sessionId, if (approved) "approve" else "deny"))
    }

    override fun onMessageReceived(messageEvent: MessageEvent) {
        val path = messageEvent.path
        val data = messageEvent.data
        val jsonString = String(data)

        viewModelScope.launch {
            try {
                when (path) {
                    WearPaths.response(WearPaths.GET_SESSION_DETAIL) -> {
                        showReply(reply(jsonString) ?: return@launch)
                        _statusText.value = str(R.string.status_ready)
                    }
                    WearPaths.response(WearPaths.CREATE_SESSION) -> {
                        // Arrives while listening has often already started;
                        // don't wipe a transcript or the "Listening" status.
                        if (!_isRecording.value && !awaitingReply) {
                            _lastAssistantResponse.value = str(R.string.new_conversation_ready)
                            _statusText.value = str(R.string.status_ready)
                        }
                    }
                    WearPaths.response(WearPaths.SEND_MESSAGE), WearPaths.response(WearPaths.APPROVE_ACTION), WearPaths.RESPONSE_VOICE_STREAM -> {
                        val json = reply(jsonString) ?: return@launch
                        if (json.error != null) {
                            awaitingReply = false
                            _statusText.value = getApplication<Application>().getString(R.string.error_detail, json.error)
                            return@launch
                        }
                        // Live frames from the phone bridge: the transcript as
                        // soon as the server has it, then the reply as it streams.
                        json.partialResponse?.let {
                            if (awaitingReply) showPartialReply(it)
                            return@launch
                        }
                        showReply(json)
                        // A bare transcription frame can precede the answer.
                        val isFinal = json.status != null || json.response != null || json.messages != null
                        if (!isFinal && !json.transcription.isNullOrEmpty()) {
                            _statusText.value = str(R.string.status_thinking)
                        }
                        if (isFinal) {
                            if (!json.response.isNullOrEmpty()) WearPerf.mark("reply text shown")
                            awaitingReply = false
                            markActivity()
                            _statusText.value = str(R.string.status_ready)
                        }
                    }
                    WearPaths.RESPONSE_ERROR -> {
                        awaitingReply = false
                        val error = WearReply.decode(jsonString)?.error ?: str(R.string.failed)
                        _statusText.value = getApplication<Application>().getString(R.string.error_detail, error)
                    }
                }
            } catch (e: Exception) {
                Log.e("WearViewModel", "Error parsing response for path: $path", e)
            }
        }
    }

    /** The frame in [text]; null (logged) when it doesn't read. */
    private fun reply(text: String): WearReply? =
        WearReply.decode(text) ?: null.also { Log.e("WearViewModel", "Unreadable reply") }

    private fun showReply(reply: WearReply) {
        val thread = reply.messages.orEmpty().mapNotNull { m ->
            val id = m.id ?: UUID.randomUUID().toString()
            val toolCall = m.toolCall
            when {
                toolCall != null -> toolCard(id, toolCall.toolName ?: str(R.string.tool))
                m.message.isNotEmpty() -> WearChatMessage(id = id, text = m.message, isUser = m.isUser)
                else -> null
            }
        }
        if (thread.isNotEmpty()) _chatMessages.value = thread

        // The live transcription (voice)
        val transcription = reply.transcription.orEmpty()
        if (transcription.isNotEmpty()) {
            val currentList = _chatMessages.value
            // Only add if user's last message is not already matching this transcription
            WearPerf.mark("transcript shown")
            if (currentList.none { it.isUser && it.text == transcription }) {
                _chatMessages.value = currentList + WearChatMessage(
                    id = UUID.randomUUID().toString(),
                    text = transcription,
                    isUser = true
                )
            }
        }

        // The assistant's reply
        val respText = reply.response.orEmpty()
        if (respText.isNotEmpty()) {
            _lastAssistantResponse.value = respText
            val currentList = _chatMessages.value.filter { it.id != STREAMING_ID }
            if (currentList.isEmpty() || currentList.last().text != respText) {
                _chatMessages.value = currentList + WearChatMessage(
                    id = UUID.randomUUID().toString(),
                    text = respText,
                    isUser = false
                )
            }
        }

        // The tools the run used
        val toolCalls = reply.toolCalls.orEmpty()
        if (toolCalls.isNotEmpty()) {
            _chatMessages.value = _chatMessages.value + toolCalls.map { toolCard(UUID.randomUUID().toString(), it.toolName ?: str(R.string.tool)) }
        }

        // The phone bridge forwards prompts the run is blocked on; tool cards carry no approval.
        _pendingApproval.value = reply.pendingApproval?.let { WearToolApproval(it.toolName ?: str(R.string.action), "", "") }
        reply.selection?.let { sel ->
            _pendingSelection.value = WearSelectionRequest(
                prompt = sel.prompt ?: str(R.string.choose_an_option),
                options = sel.options.map { WearSelectionOption(label = it.label, value = it.value) },
                multiple = sel.multiple
            )
        } ?: run {
            if (reply.status != null) _pendingSelection.value = null
        }

        reply.extension?.let { ext ->
            _pendingExtension.value = WearPendingExtension(summary = ext.summary)
        } ?: run {
            if (reply.status != null) _pendingExtension.value = null
        }
    }

    private fun toolCard(id: String, name: String) =
        WearChatMessage(id = id, text = name, isUser = false, isToolCall = true, toolName = name, toolArgs = name)

    private fun sendAction(action: WearRunAction) {
        sendToPhone(WearPaths.APPROVE_ACTION, action.encode().toByteArray())
    }

    /**
     * Grant more iterations to a run that hit the step limit. Routed through
     * the phone bridge like every other assistant action.
     */
    fun extendIterations() {
        _pendingExtension.value = null
        awaitingReply = true
        markActivity()
        _statusText.value = str(R.string.status_continuing)

        sendAction(WearRunAction(sessionId, "extend_iterations"))
    }

    /** Stop an over-budget run and settle the session. */
    fun declineIterations() {
        _pendingExtension.value = null
        awaitingReply = true
        markActivity()
        _statusText.value = str(R.string.status_wrapping_up)

        sendAction(WearRunAction(sessionId, "decline_iterations"))
    }

    fun submitSelection(indices: List<Int>) {
        val request = _pendingSelection.value ?: return
        _pendingSelection.value = null
        _statusText.value = str(R.string.status_submitting)
        awaitingReply = true
        markActivity()

        sendAction(WearRunAction(sessionId, "select", selection = indices))
    }

    /** Grows (or starts) the reply bubble while the answer streams in. */
    private fun showPartialReply(text: String) {
        if (text.isBlank()) return
        WearPerf.mark("first reply text shown")
        val list = _chatMessages.value
        val bubble = WearChatMessage(STREAMING_ID, text, isUser = false)
        _chatMessages.value = if (list.lastOrNull()?.id == STREAMING_ID) list.dropLast(1) + bubble
        else list.filter { it.id != STREAMING_ID } + bubble
    }

    override fun onCleared() {
        super.onCleared()
        if (initialized) Wearable.getMessageClient(appContext).removeListener(this)
    }

    private fun pcmRms(buffer: ByteArray, length: Int): Double {
        var sum = 0.0
        var count = 0
        var i = 0
        while (i + 1 < length) {
            val sample = ((buffer[i + 1].toInt() shl 8) or (buffer[i].toInt() and 0xFF)).toShort().toDouble()
            sum += sample * sample
            count++
            i += 2
        }
        return if (count == 0) 0.0 else kotlin.math.sqrt(sum / count)
    }

    private companion object {
        /** Same idle window the phone's voice session uses. */
        const val CONVERSATION_IDLE_MS = 2 * 60_000L
        const val REPLY_STALE_MS = 3 * 60_000L
        /** Hands-free listening gives up if nothing is said this long after opening. */
        const val NO_SPEECH_TIMEOUT_MS = 8_000L
        /** Hands-free listening sends what it has after this long. */
        const val AUTO_LISTEN_MAX_MS = 90_000L
        /** Hands-free listening sends once it has been quiet this long after speech. */
        const val END_SILENCE_MS = 1_500L
        /** 16-bit PCM RMS clearly above a quiet room; speech near the watch is far louder. */
        const val SPEECH_RMS = 700.0
        /** 100 ms of loud frames in a row counts as speech. */
        const val SPEECH_MIN_LOUD_FRAMES = 5
        /**
         * Frames this soon after the mic opens never count as speech: on the
         * OnePlus Watch 2 every turn starts with a burst (RMS 1200–3200,
         * ~340–640 ms after the first frame, whether or not the haptic tick
         * fires then), which alone used to count as speech.
         */
        const val SPEECH_GATE_MS = 800L
        const val SAMPLE_RATE = 16_000
        /** 20 ms mic frames for level tracking. */
        const val FRAME_BYTES = SAMPLE_RATE * 2 / 50
        /** 100 ms per Data Layer message (was one per ~40 ms read). */
        const val BATCH_BYTES = SAMPLE_RATE * 2 / 10
        const val STREAMING_ID = "streaming-reply"
        /** Dana's shared thread (server `session_id: "main"`). */
        const val THREAD_ID = "main"
    }
}
