package io.github.ronganam.dana.ui.viewmodels

import android.os.Build
import android.media.AudioDeviceInfo
import android.content.Context
import android.content.SharedPreferences
import android.media.AudioManager
import android.util.Log
import io.github.ronganam.dana.data.network.HttpClients
import io.github.ronganam.dana.data.realtime.RealtimeCallEngine
import io.github.ronganam.dana.data.realtime.RealtimeCallStatus
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import androidx.core.content.edit

/** Continuous realtime voice call (OpenAI Realtime relayed by the server). */
class RealtimeCallController(
    private val ctx: ControllerContext,
    private val appContext: Context,
    private val prefs: SharedPreferences,
    private val serverUrl: () -> String,
    private val apiToken: () -> String,
    private val sessionId: () -> String,
    private val onTranscript: (sender: String, text: String, isUser: Boolean) -> Unit,
    private val onStatusText: (String) -> Unit
) {
    // GA models per https://developers.openai.com/api/docs/guides/realtime
    private val _selectedRealtimeModel = MutableStateFlow(prefs.getString(KEY_MODEL, DEFAULT_MODEL) ?: DEFAULT_MODEL)
    val selectedRealtimeModel: StateFlow<String> = _selectedRealtimeModel.asStateFlow()

    private val _isCallActive = MutableStateFlow(false)
    val isCallActive: StateFlow<Boolean> = _isCallActive.asStateFlow()

    private val _isCallMuted = MutableStateFlow(false)
    val isCallMuted: StateFlow<Boolean> = _isCallMuted.asStateFlow()

    private val _isSpeakerOn = MutableStateFlow(true)
    val isSpeakerOn: StateFlow<Boolean> = _isSpeakerOn.asStateFlow()

    private val _callStatus = MutableStateFlow(RealtimeCallStatus.IDLE)
    val callStatus: StateFlow<RealtimeCallStatus> = _callStatus.asStateFlow()

    private val _currentLiveTranscript = MutableStateFlow("")
    val currentLiveTranscript: StateFlow<String> = _currentLiveTranscript.asStateFlow()

    private val _activeCallToolName = MutableStateFlow<String?>(null)
    val activeCallToolName: StateFlow<String?> = _activeCallToolName.asStateFlow()

    private var engine: RealtimeCallEngine? = null
    private val callJobs = mutableListOf<Job>()

    fun setRealtimeModel(model: String) {
        _selectedRealtimeModel.value = model
        prefs.edit { putString(KEY_MODEL, model) }
    }

    fun toggleCallMute() {
        val muted = !_isCallMuted.value
        _isCallMuted.value = muted
        engine?.setMicMuted(muted)
    }

    fun toggleSpeaker() {
        val on = !_isSpeakerOn.value
        _isSpeakerOn.value = on
        try {
            audioManager()?.let { setSpeaker(it, on) }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to toggle speaker: ${e.message}")
        }
    }

    fun startCall() {
        if (_isCallActive.value) return

        callJobs.forEach { it.cancel() }
        callJobs.clear()

        val newEngine = RealtimeCallEngine(HttpClients.webSocket, ctx.scope)
        engine = newEngine

        callJobs += ctx.scope.launch { newEngine.isCallActive.collect { _isCallActive.value = it } }
        callJobs += ctx.scope.launch { newEngine.callStatus.collect { _callStatus.value = it } }
        callJobs += ctx.scope.launch { newEngine.currentAssistantDelta.collect { _currentLiveTranscript.value = it } }
        callJobs += ctx.scope.launch { newEngine.activeToolName.collect { _activeCallToolName.value = it } }

        newEngine.onUserTranscriptDone = { transcript ->
            ctx.scope.launch { if (transcript.isNotBlank()) onTranscript("You", transcript, true) }
        }
        newEngine.onAssistantTranscriptDone = { transcript ->
            ctx.scope.launch {
                _currentLiveTranscript.value = ""
                if (transcript.isNotBlank()) onTranscript("Assistant", transcript, false)
            }
        }
        newEngine.onError = { errorMsg ->
            ctx.scope.launch { onStatusText("Call error: $errorMsg") }
        }
        newEngine.onCallEnded = {
            ctx.scope.launch {
                if (engine === newEngine) resetState()
            }
        }

        _isCallActive.value = true
        _callStatus.value = RealtimeCallStatus.CONNECTING
        _currentLiveTranscript.value = ""
        _isCallMuted.value = false
        applyCallAudioRoute()

        newEngine.startCall(
            serverUrl = serverUrl(),
            apiToken = apiToken(),
            sessionId = sessionId(),
            model = _selectedRealtimeModel.value
        )
    }

    /** End the call if one is running; a no-op otherwise. */
    fun endCall() {
        if (engine == null && !_isCallActive.value) return
        engine?.endCall()
        resetState()
    }

    private fun resetState() {
        callJobs.forEach { it.cancel() }
        callJobs.clear()
        engine = null
        _isCallActive.value = false
        _callStatus.value = RealtimeCallStatus.IDLE
        _currentLiveTranscript.value = ""
        _activeCallToolName.value = null
        _isCallMuted.value = false
        restoreAudioRoute()
    }

    private fun audioManager(): AudioManager? =
        appContext.getSystemService(Context.AUDIO_SERVICE) as? AudioManager

    /** Route call audio to the loudspeaker or back (the communication device on Android 12+). */
    private fun setSpeaker(am: AudioManager, on: Boolean) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            if (on) am.availableCommunicationDevices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }?.let { am.setCommunicationDevice(it) }
            else am.clearCommunicationDevice()
        } else {
            @Suppress("DEPRECATION")
            am.isSpeakerphoneOn = on
        }
    }

    private fun applyCallAudioRoute() {
        try {
            audioManager()?.let { am ->
                am.mode = AudioManager.MODE_IN_COMMUNICATION
                setSpeaker(am, _isSpeakerOn.value)
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to apply call audio route: ${e.message}")
        }
    }

    private fun restoreAudioRoute() {
        try {
            audioManager()?.let { am ->
                setSpeaker(am, false)
                if (am.mode == AudioManager.MODE_IN_COMMUNICATION) am.mode = AudioManager.MODE_NORMAL
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to restore audio route: ${e.message}")
        }
    }

    companion object {
        private const val TAG = "RealtimeCall"
        private const val KEY_MODEL = "realtime_model"
        private const val DEFAULT_MODEL = "gpt-realtime"
    }
}
