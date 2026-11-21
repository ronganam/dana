package io.github.ronganam.dana.ui.viewmodels

import android.content.Context
import android.util.Log
import io.github.ronganam.dana.R
import io.github.ronganam.dana.data.network.Reachability
import io.github.ronganam.dana.data.network.RunEvent
import io.github.ronganam.dana.data.network.ServerStatusMonitor
import io.github.ronganam.dana.data.screen.OutgoingScreen
import io.github.ronganam.dana.data.screen.ScreenAttachments
import io.github.ronganam.dana.data.voice.DeviceSpeech
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Request
import java.io.IOException

/**
 * Push-to-talk. With the phone's speech recognizer (when it is on and
 * available) the words show live in the [transcript] and the utterance is
 * sent as a normal text message ([Host.sendSpoken]); otherwise PCM from the
 * mic ([MicCapture]) streams over the voice socket ([VoiceSocket]) and the
 * server transcribes it and runs the turn, whose events come back on the
 * socket ([Host.onVoiceEvent]).
 *
 * Listening checks [ServerStatusMonitor] first, so the user isn't left
 * talking into a dead connection while the VPN is off.
 */
internal class ChatVoice(
    private val appContext: Context,
    private val scope: CoroutineScope,
    private val texts: Texts,
    private val transcript: ChatTranscript,
    private val status: MutableStateFlow<String>,
    private val host: Host
) {
    /** What the voice turn needs from the conversation. */
    interface Host {
        /** Ends a realtime call if one is running (the mic is needed). */
        fun endRealtimeCall()
        /** The screen the sheet includes with the next message, if any. */
        fun screens(): ScreenAttachments?
        /** The shared thread is shown. */
        fun inThread(): Boolean
        /** `device` stamped on what this app says in the thread ("" outside it). */
        fun device(): String
        /** Send recognised [text] as a message, settling the live bubble [bubbleId]; false when the server is unreachable. */
        fun sendSpoken(text: String, bubbleId: String?): Boolean
        /** The voice socket's request for the session shown. */
        fun voiceRequest(): Request
        /** The utterance ended with speech: the run starts (streaming, "Transcribing…"); returns the `stop` frame to send. */
        fun startVoiceRun(screen: OutgoingScreen?): String
        /** The socket failed after the run started on the server: follow it over SSE; false when there is none. */
        fun followDroppedRun(): Boolean
        /** One frame of the run from the socket (no `no_speech`). */
        fun onVoiceEvent(event: RunEvent)
        /** The closing `state` frame was rendered: the run is over. */
        fun voiceRunEnded()
        /** The thread is busy with another run (409 on the socket). */
        fun voiceRefusedBusy()
        /** The turn went nowhere: no typing indicator, not streaming. */
        fun voiceIdle()
    }

    private val mic = MicCapture()
    private val socket = VoiceSocket(onEvent = ::handleEvent, onFailure = { t -> scope.launch { onSocketFailure(t) } })

    private val _isRecording = MutableStateFlow(false)
    val isRecording: StateFlow<Boolean> = _isRecording.asStateFlow()

    private var recordingJob: Job? = null
    private var gateJob: Job? = null
    private var verifyJob: Job? = null
    /** Words were heard in the current utterance (the phone's recognizer; the server path asks [mic]). */
    private var hasSpoken = false

    /** Live on-device transcription for the current utterance, if that path is in use. */
    private var deviceSpeech: DeviceSpeech? = null
    /** The user bubble that shows words as they are recognised. */
    private var liveBubbleId: String? = null
    private var deviceStopJob: Job? = null

    /** The screen taken for the utterance on the voice socket, until its transcription arrives. */
    private var sentScreen: OutgoingScreen? = null

    /** Listening, waiting to learn whether the server is back, or recognising on the phone. */
    val busy: Boolean get() = _isRecording.value || gateJob?.isActive == true || deviceSpeech != null

    /** The screen sent with the utterance whose transcription just arrived (once). */
    fun takeSentScreen(): OutgoingScreen? = sentScreen.also { sentScreen = null }

    /**
     * Start listening. When the server is already known to be unreachable we
     * re-check first and only open the mic if it answers. When reachability is
     * unknown or stale we start right away and abort if a probe fails, so the
     * user isn't left talking to nothing.
     */
    fun start() {
        host.endRealtimeCall()
        if (busy) return

        val serverStatus = ServerStatusMonitor.status.value
        when (serverStatus.reachability) {
            Reachability.NOT_CONFIGURED -> {
                status.value = texts.get(R.string.voice_set_server_first)
                return
            }
            Reachability.OFFLINE -> {
                status.value = texts.get(R.string.voice_checking_server)
                gateJob = scope.launch {
                    if (ServerStatusMonitor.ensureReachable()) {
                        begin()
                    } else {
                        status.value = offlineText()
                    }
                }
                return
            }
            else -> {
                begin()
                val stale = System.currentTimeMillis() - serverStatus.lastCheckedAt > STATUS_FRESH_MS
                if (serverStatus.reachability != Reachability.ONLINE || stale) verifyWhileRecording()
            }
        }
    }

    private fun verifyWhileRecording() {
        val socketAtStart = socket.current()
        verifyJob?.cancel()
        verifyJob = scope.launch {
            if (!ServerStatusMonitor.ensureReachable() && socket.current() === socketAtStart && _isRecording.value) {
                abort(offlineText())
            }
        }
    }

    /** Why the server can't be reached, for the status line. */
    fun offlineText(): String =
        ServerStatusMonitor.status.value.detail ?: texts.get(R.string.voice_unreachable)

    private fun begin() {
        if (_isRecording.value) return
        if (DeviceSpeech.isEnabled(appContext) && DeviceSpeech.isAvailable(appContext)) {
            beginDeviceRecognition()
        } else {
            beginServerRecording()
        }
    }

    // --- On-device recognition, then a normal text message ---

    private fun beginDeviceRecognition() {
        _isRecording.value = true
        status.value = texts.get(R.string.voice_listening)
        hasSpoken = false
        liveBubbleId = null

        lateinit var speech: DeviceSpeech
        speech = DeviceSpeech(appContext, object : DeviceSpeech.Listener {
            override fun onPartial(text: String) {
                if (deviceSpeech !== speech) return
                hasSpoken = true
                liveBubbleId = transcript.showLive(liveBubbleId, text, host.device())
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
                    status.value = ""
                } else if (!heardSomething && code in DeviceSpeech.FALLBACK_ERRORS) {
                    Log.w(TAG, "Device speech unavailable (error $code); using server transcription")
                    beginServerRecording()
                } else {
                    status.value = texts.get(R.string.voice_didnt_catch)
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
        if (!hasSpoken) status.value = texts.get(R.string.voice_transcribing)
        speech.stop()
        deviceStopJob?.cancel()
        deviceStopJob = scope.launch {
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
            status.value = ""
            return
        }
        if (!host.sendSpoken(text, bubbleId)) {
            removeLiveTranscript(bubbleId)
            status.value = offlineText()
        }
    }

    private fun removeLiveTranscript(id: String? = liveBubbleId) {
        liveBubbleId = null
        if (id != null && transcript.contains(id)) transcript.remove(id)
    }

    private fun cancelDeviceRecognition() {
        deviceStopJob?.cancel()
        deviceSpeech?.cancel()
        deviceSpeech = null
        removeLiveTranscript()
    }

    // --- PCM over the voice socket, transcribed by the server ---

    private fun beginServerRecording() {
        if (_isRecording.value) return
        _isRecording.value = true
        status.value = texts.get(R.string.voice_listening)
        mic.reset()

        socket.open(host.voiceRequest())

        recordingJob = scope.launch(Dispatchers.IO) {
            try {
                val silent = mic.capture(keepGoing = { _isRecording.value }, onChunk = socket::send)
                if (silent) withContext(Dispatchers.Main) { stop() }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.e(TAG, "Voice recording failed", e)
                withContext(Dispatchers.Main) { abort(texts.get(R.string.voice_mic_error)) }
            }
        }
    }

    /** The voice socket failed (it is already closed). */
    private fun onSocketFailure(t: Throwable) {
        // The run started on the server; keep following it over SSE.
        if (host.followDroppedRun()) return
        val reason = if (t is IOException) texts.reason(t) else (t.message ?: texts.get(R.string.voice_connection_lost))
        abort(texts.get(R.string.voice_failed, reason))
    }

    /** The user let go (or the mic heard silence): send what was said. */
    fun stop() {
        if (gateJob?.isActive == true) {
            // Still waiting to learn whether the server is back; give up.
            gateJob?.cancel()
            gateJob = null
            status.value = ""
            return
        }
        deviceSpeech?.let { speech ->
            verifyJob?.cancel()
            if (_isRecording.value) stopDeviceRecognition(speech)
            return
        }
        if (!_isRecording.value) return
        _isRecording.value = false
        recordingJob?.cancel()
        verifyJob?.cancel()
        mic.release()

        if (!mic.heardSpeech) {
            Log.d(TAG, "No speech detected; cancelling voice stream.")
            status.value = ""
            transcript.removeThinking()
            closeSocket("No speech detected")
            return
        }

        // The screen goes with this utterance as `device_screen` (its text).
        // The socket carries no image (server contract), so the screenshot
        // stays on the phone on this path; the sent message says "text only".
        val outgoing = host.screens()?.take()?.let { OutgoingScreen(it.screen, null, it.label) }
        val frame = host.startVoiceRun(outgoing)
        sentScreen = outgoing
        socket.send(frame)
    }

    /** The utterance never became a message: its screen stays included. */
    private fun putBackScreen() {
        sentScreen?.let { host.screens()?.putBack(it) }
        sentScreen = null
    }

    /**
     * Tear down a voice turn that can't complete (server unreachable, socket
     * failure, mic error, switching sessions), whether still recording or
     * waiting for the reply. Unlike [stop] it never sends "stop".
     */
    fun abort(statusText: String) {
        putBackScreen()
        gateJob?.cancel()
        gateJob = null
        verifyJob?.cancel()
        verifyJob = null
        cancelDeviceRecognition()
        _isRecording.value = false
        recordingJob?.cancel()
        mic.release()
        socket.cancel()
        host.voiceIdle()
        status.value = statusText
    }

    fun closeSocket(reason: String) = socket.close(reason)

    /**
     * One frame from the voice socket: the same events as the SSE stream
     * (`transcription`, the run's events, then a closing `state`), or
     * `no_speech` when nothing intelligible was said.
     */
    private fun handleEvent(event: RunEvent) {
        when (event.type) {
            "no_speech" -> {
                // Nothing was sent: the screen goes with the next try.
                putBackScreen()
                host.voiceIdle()
                status.value = ""
                closeSocket("No speech")
            }
            "state" -> {
                host.onVoiceEvent(event)
                host.voiceRunEnded()
                closeSocket("Response received")
            }
            "error" -> if (event.code == "session_busy" && host.inThread()) {
                putBackScreen()
                host.voiceRefusedBusy()
            } else host.onVoiceEvent(event)
            else -> host.onVoiceEvent(event)
        }
    }

    /** Release the mic and the socket; the host is going away. */
    fun release() {
        gateJob?.cancel()
        verifyJob?.cancel()
        recordingJob?.cancel()
        cancelDeviceRecognition()
        _isRecording.value = false
        mic.release()
        socket.cancel()
    }

    companion object {
        private const val TAG = "ChatVoice"
        /** After the user taps stop, how long to wait for the recognizer's final words. */
        private const val DEVICE_FINAL_TIMEOUT_MS = 2_500L
        /** A successful check this recent is trusted without re-probing. */
        private const val STATUS_FRESH_MS = 30_000L
    }
}
