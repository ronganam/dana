package io.github.ronganam.dana.wake

import android.content.Context
import android.content.SharedPreferences
import android.media.MediaRecorder
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow

/** Shared state between the wake-word service and the activity hosting the assistant (same process). */
object WakeBus {
    /** Detection runs only while the assistant is closed. */
    @Volatile var detectionEnabled = true

    /** Set when detection should restart from a clean slate. */
    @Volatile var resetRequested = false

    @Volatile var threshold = WakePrefs.DEFAULT_THRESHOLD

    /** When the assistant last closed; a new conversation starts after a pause (see [AssistantSession]). */
    @Volatile var lastClosedAt = 0L

    /** Emits the mic frame index at which "Hey Dana" was heard. */
    private val _wake = MutableSharedFlow<Long>(extraBufferCapacity = 4)
    val wake: SharedFlow<Long> = _wake.asSharedFlow()
    fun emitWake(frame: Long) { _wake.tryEmit(frame) }

    /** Highest score over the last ~2 s, for the settings screen. */
    private val _score = MutableStateFlow(0f)
    val score: StateFlow<Float> = _score.asStateFlow()
    fun setScore(v: Float) { _score.value = v }

    private val _status = MutableStateFlow("Starting…")
    val status: StateFlow<String> = _status.asStateFlow()
    fun setStatus(v: String) { _status.value = v }

    private val _listening = MutableStateFlow(false)
    /** True while the detector is loaded and fed by the mic. */
    val listening: StateFlow<Boolean> = _listening.asStateFlow()
    fun setListening(v: Boolean) { _listening.value = v }
}

/** Wake-word settings, in the app's usual `DanaPrefs` file (server_url / api_token are shared with ApiService). */
object WakePrefs {
    const val MODEL_ASSET = "wakeword/hey_dana.onnx"
    const val DEFAULT_THRESHOLD = 0.5f
    val DEFAULT_SERVER: String = io.github.ronganam.dana.BuildConfig.DEFAULT_SERVER_URL

    // Keys predate the TV build (kiosk_*); kept so existing tablets keep their settings.
    private const val KEY_THRESHOLD = "kiosk_wake_threshold"
    private const val KEY_SPEAK = "kiosk_speak_replies"
    private const val KEY_FOLLOW_UP = "kiosk_follow_up"
    private const val KEY_MIC_SOURCE = "wake_mic_source"
    private const val KEY_AEC = "wake_echo_cancel"

    /** Mic sources offered in settings: (AudioSource, label). */
    val MIC_SOURCES = listOf(
        MediaRecorder.AudioSource.VOICE_RECOGNITION to "Voice recognition (raw)",
        MediaRecorder.AudioSource.VOICE_COMMUNICATION to "Voice call (echo-cancelled)",
        MediaRecorder.AudioSource.MIC to "Plain mic",
    )

    fun prefs(context: Context): SharedPreferences =
        context.getSharedPreferences("DanaPrefs", Context.MODE_PRIVATE)

    fun serverUrl(context: Context): String =
        prefs(context).getString("server_url", "").orEmpty().trim().trimEnd('/').ifEmpty { DEFAULT_SERVER }

    fun apiToken(context: Context): String = prefs(context).getString("api_token", "").orEmpty()

    fun threshold(context: Context): Float = prefs(context).getFloat(KEY_THRESHOLD, DEFAULT_THRESHOLD)

    fun speakReplies(context: Context): Boolean = prefs(context).getBoolean(KEY_SPEAK, true)

    /** Listen again after Dana answers, so a follow-up doesn't need "Hey Dana". */
    fun followUp(context: Context): Boolean = prefs(context).getBoolean(KEY_FOLLOW_UP, true)

    fun micSource(context: Context): Int =
        prefs(context).getInt(KEY_MIC_SOURCE, MediaRecorder.AudioSource.VOICE_RECOGNITION)

    /** Platform acoustic echo canceller on the mic session (removes the device's own speaker output). */
    fun echoCancel(context: Context): Boolean = prefs(context).getBoolean(KEY_AEC, false)

    fun saveServer(context: Context, serverUrl: String, apiToken: String) {
        prefs(context).edit()
            .putString("server_url", serverUrl.trim().trimEnd('/'))
            .putString("api_token", apiToken.trim())
            .apply()
    }

    fun saveVoice(
        context: Context,
        threshold: Float = threshold(context),
        speak: Boolean = speakReplies(context),
        followUp: Boolean = followUp(context),
        micSource: Int = micSource(context),
        echoCancel: Boolean = echoCancel(context),
    ) {
        val micChanged = micSource != micSource(context) || echoCancel != echoCancel(context)
        prefs(context).edit()
            .putFloat(KEY_THRESHOLD, threshold)
            .putBoolean(KEY_SPEAK, speak)
            .putBoolean(KEY_FOLLOW_UP, followUp)
            .putInt(KEY_MIC_SOURCE, micSource)
            .putBoolean(KEY_AEC, echoCancel)
            .apply()
        WakeBus.threshold = threshold
        if (micChanged) MicHub.configure(micSource, echoCancel, reopen = true)
    }
}
