package io.github.ronganam.dana.data.realtime

import android.annotation.SuppressLint
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.util.Base64
import android.util.Log
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import io.github.ronganam.dana.data.network.bearerAuth
import okhttp3.*
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.concurrent.TimeUnit

enum class RealtimeCallStatus {
    IDLE,
    CONNECTING,
    LISTENING,
    SPEAKING,
    TOOL_RUNNING,
    ERROR;

    fun toDisplayString(): String = when (this) {
        IDLE -> "Idle"
        CONNECTING -> "Connecting..."
        LISTENING -> "Listening..."
        SPEAKING -> "Dana is speaking…"
        TOOL_RUNNING -> "Executing tool..."
        ERROR -> "Call error"
    }
}

class RealtimeCallEngine(
    private val okHttpClient: OkHttpClient,
    private val scope: CoroutineScope
) {
    companion object {
        private const val TAG = "RealtimeCallEngine"
        private const val SAMPLE_RATE = 24000
        private const val CHUNK_SIZE_BYTES = 2400 // 50ms of 16-bit mono PCM at 24kHz (1200 samples)
        private const val STALE_AUDIO_GUARD_MS = 300L
    }

    private var webSocket: WebSocket? = null
    private var audioRecord: AudioRecord? = null
    private var audioTrack: AudioTrack? = null

    private var recordingJob: Job? = null
    private var playbackJob: Job? = null

    private val playbackChannel = Channel<ByteArray>(capacity = Channel.UNLIMITED)
    private var interruptedTimestamp: Long = 0L

    private val _isCallActive = MutableStateFlow(false)
    val isCallActive: StateFlow<Boolean> = _isCallActive.asStateFlow()

    private val _callStatus = MutableStateFlow(RealtimeCallStatus.IDLE)
    val callStatus: StateFlow<RealtimeCallStatus> = _callStatus.asStateFlow()

    private val _currentAssistantDelta = MutableStateFlow("")
    val currentAssistantDelta: StateFlow<String> = _currentAssistantDelta.asStateFlow()

    private val _activeToolName = MutableStateFlow<String?>(null)
    val activeToolName: StateFlow<String?> = _activeToolName.asStateFlow()

    // Callbacks for ViewModel integration
    var onUserTranscriptDone: ((String) -> Unit)? = null
    var onAssistantTranscriptDelta: ((String) -> Unit)? = null
    var onAssistantTranscriptDone: ((String) -> Unit)? = null
    var onToolStart: ((String) -> Unit)? = null
    var onToolDone: ((toolName: String, status: String) -> Unit)? = null
    var onError: ((String) -> Unit)? = null
    var onCallEnded: (() -> Unit)? = null

    @Volatile
    private var isMicMuted: Boolean = false

    fun setMicMuted(muted: Boolean) {
        isMicMuted = muted
        Log.d(TAG, "Mic muted: $muted")
    }

    @SuppressLint("MissingPermission")
    fun startCall(
        serverUrl: String,
        apiToken: String,
        sessionId: String,
        model: String
    ) {
        if (_isCallActive.value) {
            Log.w(TAG, "startCall called while call is already active")
            return
        }

        _isCallActive.value = true
        _callStatus.value = RealtimeCallStatus.CONNECTING
        _currentAssistantDelta.value = ""
        _activeToolName.value = null
        isMicMuted = false
        interruptedTimestamp = 0L

        cleanUpAudio()

        try {
            initAudioTrack()
            startPlaybackWorker()

            val wsBase = serverUrl.replaceFirst("http", "ws")
            val wsUrl = "$wsBase/api/assistant/realtime?model=$model&session_id=$sessionId"
            Log.d(TAG, "Connecting Realtime WebSocket: $wsUrl")

            val request = Request.Builder().url(wsUrl).bearerAuth(apiToken).build()

            val callClient = okHttpClient.newBuilder()
                .pingInterval(15, TimeUnit.SECONDS)
                .readTimeout(0, TimeUnit.MILLISECONDS)
                .build()

            webSocket = callClient.newWebSocket(request, createWebSocketListener())
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start realtime call: ${e.message}", e)
            _callStatus.value = RealtimeCallStatus.ERROR
            onError?.invoke("Failed to start call: ${e.message}")
            endCall()
        }
    }

    private fun createWebSocketListener() = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            Log.d(TAG, "Realtime WebSocket connected successfully")
            _callStatus.value = RealtimeCallStatus.LISTENING
            startAudioRecording()
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            val pcmData = bytes.toByteArray()
            if (pcmData.isNotEmpty()) {
                if (System.currentTimeMillis() - interruptedTimestamp < STALE_AUDIO_GUARD_MS) {
                    Log.d(TAG, "Discarding stale audio frame arrived post-interruption")
                    return
                }
                if (_callStatus.value != RealtimeCallStatus.TOOL_RUNNING) {
                    _callStatus.value = RealtimeCallStatus.SPEAKING
                }
                playbackChannel.trySend(pcmData)
            }
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            handleServerEvent(text)
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            Log.e(TAG, "Realtime WebSocket error: ${t.message}", t)
            _callStatus.value = RealtimeCallStatus.ERROR
            onError?.invoke(t.message ?: "Realtime connection failed")
            endCall()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            Log.d(TAG, "Realtime WebSocket closed: code=$code, reason=$reason")
            endCall()
        }
    }

    private fun handleServerEvent(jsonText: String) {
        try {
            val json = JSONObject(jsonText)
            val type = json.optString("type")

            when (type) {
                "session_ready" -> {
                    Log.d(TAG, "Session ready: ${json.optString("session_id")}")
                    _callStatus.value = RealtimeCallStatus.LISTENING
                }
                "interrupted", "input_audio_buffer.speech_started", "speech_started" -> {
                    Log.d(TAG, "Interruption signal received, flushing playback buffers")
                    flushAudio()
                }
                "transcript_delta" -> {
                    interruptedTimestamp = 0L
                    val delta = json.optString("delta", "")
                    if (delta.isNotEmpty()) {
                        _currentAssistantDelta.value += delta
                        if (_callStatus.value != RealtimeCallStatus.TOOL_RUNNING) {
                            _callStatus.value = RealtimeCallStatus.SPEAKING
                        }
                        onAssistantTranscriptDelta?.invoke(delta)
                    }
                }
                "transcript_done" -> {
                    val transcript = json.optString(
                        "transcript",
                        json.optString("text", _currentAssistantDelta.value)
                    )
                    Log.d(TAG, "Assistant transcript complete: $transcript")
                    _currentAssistantDelta.value = ""
                    _callStatus.value = RealtimeCallStatus.LISTENING
                    if (transcript.isNotBlank()) {
                        onAssistantTranscriptDone?.invoke(transcript)
                    }
                }
                "user_transcript_done", "conversation.item.input_audio_transcription.completed" -> {
                    val transcript = json.optString("transcript", json.optString("text", ""))
                    Log.d(TAG, "User transcript complete: $transcript")
                    if (transcript.isNotBlank()) {
                        onUserTranscriptDone?.invoke(transcript)
                    }
                }
                "tool_start" -> {
                    val name = json.optString("name", "")
                    Log.d(TAG, "Tool execution started: $name")
                    _activeToolName.value = name
                    _callStatus.value = RealtimeCallStatus.TOOL_RUNNING
                    onToolStart?.invoke(name)
                }
                "tool_done" -> {
                    val name = json.optString("name", "")
                    val status = json.optString("status", "")
                    Log.d(TAG, "Tool execution completed: $name (status=$status)")
                    _activeToolName.value = null
                    _callStatus.value = RealtimeCallStatus.SPEAKING
                    onToolDone?.invoke(name, status)
                }
                "response.audio.delta" -> {
                    val b64 = json.optString("delta", "")
                    if (b64.isNotEmpty()) {
                        if (System.currentTimeMillis() - interruptedTimestamp < STALE_AUDIO_GUARD_MS) {
                            Log.d(TAG, "Discarding stale base64 audio delta post-interruption")
                            return
                        }
                        try {
                            val pcm = Base64.decode(b64, Base64.DEFAULT)
                            if (_callStatus.value != RealtimeCallStatus.TOOL_RUNNING) {
                                _callStatus.value = RealtimeCallStatus.SPEAKING
                            }
                            playbackChannel.trySend(pcm)
                        } catch (e: Exception) {
                            Log.w(TAG, "Failed to decode base64 audio delta: ${e.message}")
                        }
                    }
                }
                "error" -> {
                    val message = json.optString("message", "Unknown realtime error")
                    Log.e(TAG, "Server error event: $message")
                    _callStatus.value = RealtimeCallStatus.ERROR
                    onError?.invoke(message)
                }
                "call_ended" -> {
                    Log.d(TAG, "Server ended call")
                    endCall()
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to parse server event: $jsonText", e)
        }
    }

    @SuppressLint("MissingPermission")
    private fun startAudioRecording() {
        recordingJob?.cancel()
        recordingJob = scope.launch(Dispatchers.IO) {
            try {
                val channelConfig = AudioFormat.CHANNEL_IN_MONO
                val audioFormat = AudioFormat.ENCODING_PCM_16BIT
                val minBufSize = AudioRecord.getMinBufferSize(SAMPLE_RATE, channelConfig, audioFormat)
                if (minBufSize <= 0) {
                    Log.e(TAG, "Invalid minBufferSize for AudioRecord: $minBufSize")
                    _callStatus.value = RealtimeCallStatus.ERROR
                    onError?.invoke("Audio capture not supported on this device")
                    endCall()
                    return@launch
                }
                val bufferSize = maxOf(minBufSize * 2, CHUNK_SIZE_BYTES * 2)

                val record = AudioRecord(
                    MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                    SAMPLE_RATE,
                    channelConfig,
                    audioFormat,
                    bufferSize
                )

                if (record.state != AudioRecord.STATE_INITIALIZED) {
                    Log.e(TAG, "AudioRecord initialization failed")
                    record.release()
                    _callStatus.value = RealtimeCallStatus.ERROR
                    onError?.invoke("Failed to initialize microphone")
                    endCall()
                    return@launch
                }

                audioRecord = record
                record.startRecording()
                Log.d(TAG, "AudioRecord started at $SAMPLE_RATE Hz (VOICE_COMMUNICATION)")

                val chunk = ByteArray(CHUNK_SIZE_BYTES)
                while (isActive && _isCallActive.value) {
                    val read = record.read(chunk, 0, chunk.size)
                    if (read > 0) {
                        // Drop frames while muted (keep recording so unmute is instant)
                        if (!isMicMuted) {
                            val sent = webSocket?.send(chunk.toByteString(0, read)) ?: false
                            if (!sent) {
                                Log.w(TAG, "Audio chunk send failed (WebSocket queue full or closed)")
                            }
                        }
                    } else if (read < 0) {
                        Log.w(TAG, "AudioRecord read returned error code $read")
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "Exception in audio recording loop", e)
                _callStatus.value = RealtimeCallStatus.ERROR
                onError?.invoke("Audio recording failed: ${e.message}")
                endCall()
            } finally {
                stopAudioRecord()
            }
        }
    }

    private fun initAudioTrack() {
        val minTrackBuf = AudioTrack.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        val trackBufferSize = maxOf(minTrackBuf * 2, SAMPLE_RATE * 2) // 1-second buffer

        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(SAMPLE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setBufferSizeInBytes(trackBufferSize)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()

        if (track.state != AudioTrack.STATE_INITIALIZED) {
            track.release()
            throw IllegalStateException("AudioTrack failed to initialize")
        }

        audioTrack = track
        track.play()
        Log.d(TAG, "AudioTrack initialized and playing in MODE_STREAM at $SAMPLE_RATE Hz")
    }

    private fun startPlaybackWorker() {
        playbackJob?.cancel()
        playbackJob = scope.launch(Dispatchers.IO) {
            for (pcm in playbackChannel) {
                if (!isActive || !_isCallActive.value) break
                val track = audioTrack ?: break
                if (track.state == AudioTrack.STATE_INITIALIZED) {
                    track.write(pcm, 0, pcm.size, AudioTrack.WRITE_BLOCKING)
                }
            }
        }
    }

    private fun flushAudio() {
        // Drain pending queue immediately
        while (playbackChannel.tryReceive().isSuccess) {
            // drop buffered audio
        }
        _currentAssistantDelta.value = ""
        interruptedTimestamp = System.currentTimeMillis()
        try {
            audioTrack?.let { track ->
                if (track.state == AudioTrack.STATE_INITIALIZED) {
                    track.pause()
                    track.flush()
                    track.play()
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error flushing AudioTrack: ${e.message}")
        }
        _callStatus.value = RealtimeCallStatus.LISTENING
    }

    fun sendTextMessage(text: String) {
        if (!_isCallActive.value) return
        try {
            val json = JSONObject().apply {
                put("type", "text_input")
                put("text", text)
            }
            webSocket?.send(json.toString())
        } catch (e: Exception) {
            Log.e(TAG, "Failed to send text frame over realtime socket", e)
        }
    }

    fun endCall() {
        if (!_isCallActive.value && _callStatus.value == RealtimeCallStatus.IDLE) return
        Log.d(TAG, "Ending realtime call session")

        _isCallActive.value = false
        _callStatus.value = RealtimeCallStatus.IDLE
        _activeToolName.value = null
        interruptedTimestamp = 0L

        // Send hangup frame to backend relay
        try {
            val hangupJson = JSONObject().put("type", "hangup").toString()
            webSocket?.send(hangupJson)
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't send the hangup frame", e)
        }

        recordingJob?.cancel()
        playbackJob?.cancel()

        stopAudioRecord()
        stopAudioTrack()

        try {
            webSocket?.close(1000, "Call ended by user")
        } catch (e: Exception) {
            Log.w(TAG, "Couldn't close the call socket", e)
        }
        webSocket = null

        // Drain channel
        while (playbackChannel.tryReceive().isSuccess) {}

        onCallEnded?.invoke()
    }

    private fun cleanUpAudio() {
        stopAudioRecord()
        stopAudioTrack()
        while (playbackChannel.tryReceive().isSuccess) {}
    }

    private fun stopAudioRecord() {
        try {
            audioRecord?.let { record ->
                if (record.recordingState == AudioRecord.RECORDSTATE_RECORDING) {
                    record.stop()
                }
                record.release()
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error stopping AudioRecord: ${e.message}")
        } finally {
            audioRecord = null
        }
    }

    private fun stopAudioTrack() {
        try {
            audioTrack?.let { track ->
                track.pause()
                track.flush()
                track.stop()
                track.release()
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error stopping AudioTrack: ${e.message}")
        } finally {
            audioTrack = null
        }
    }
}
