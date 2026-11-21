package app.ganam.assistant.data.voice

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log

/**
 * Push-to-talk transcription with the phone's own speech recognizer (on the
 * Fold this is "Speech Services by Google"). Words stream back while the user
 * is still talking and the recognizer detects the end of speech itself, so the
 * reply can start without the server-side transcription round trip.
 *
 * Hebrew is recognised online (Google offers no offline Hebrew model), with
 * automatic Hebrew/English switching so mixed sentences come out right.
 *
 * Must be created and driven on the main thread. One instance per utterance.
 */
class DeviceSpeech(
    private val context: Context,
    private val listener: Listener
) {
    interface Listener {
        fun onPartial(text: String)
        /** The utterance ended; [text] may be empty when nothing was understood. */
        fun onFinal(text: String)
        /**
         * Recognition failed. [heardSomething] is false when the failure came
         * before any words, so the caller can retry another way without the
         * user having to repeat themselves.
         */
        fun onError(code: Int, heardSomething: Boolean)
    }

    private var recognizer: SpeechRecognizer? = null
    private var lastPartial = ""
    private var heardSpeech = false
    private var done = false

    fun start() {
        val r = SpeechRecognizer.createSpeechRecognizer(context)
        recognizer = r
        r.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() { heardSpeech = true }
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onEvent(eventType: Int, params: Bundle?) {}

            override fun onPartialResults(partialResults: Bundle?) {
                val text = partialResults.firstResult()
                if (text.isNotBlank() && text != lastPartial) {
                    lastPartial = text
                    listener.onPartial(text)
                }
            }

            override fun onResults(results: Bundle?) {
                finish { listener.onFinal(results.firstResult().ifBlank { lastPartial }) }
            }

            override fun onError(error: Int) {
                Log.w(TAG, "Recognizer error $error (heard=${lastPartial.isNotEmpty()})")
                finish {
                    when {
                        // Words already reached the screen: keep them rather than fail.
                        lastPartial.isNotBlank() -> listener.onFinal(lastPartial)
                        error == SpeechRecognizer.ERROR_NO_MATCH ||
                            error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> listener.onFinal("")
                        else -> listener.onError(error, heardSpeech)
                    }
                }
            }
        })
        r.startListening(buildIntent())
    }

    /** The user tapped stop: end the utterance now; the final result follows. */
    fun stop() {
        if (!done) recognizer?.stopListening()
    }

    /** Words recognised so far (used if the final result never arrives). */
    val partialText: String get() = lastPartial

    /** Drop the utterance without delivering anything. */
    fun cancel() {
        done = true
        release()
    }

    private inline fun finish(deliver: () -> Unit) {
        if (done) return
        done = true
        release()
        deliver()
    }

    private fun release() {
        val r = recognizer ?: return
        recognizer = null
        try {
            r.cancel()
            r.destroy()
        } catch (e: Exception) {
            Log.w(TAG, "Error releasing recognizer", e)
        }
    }

    private fun buildIntent() = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
        putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        putExtra(RecognizerIntent.EXTRA_LANGUAGE, PRIMARY_LANGUAGE)
        putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
        putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, false)
        putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.packageName)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            putExtra(RecognizerIntent.EXTRA_ENABLE_LANGUAGE_DETECTION, true)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_DETECTION_ALLOWED_LANGUAGES, LANGUAGES)
            putExtra(RecognizerIntent.EXTRA_ENABLE_LANGUAGE_SWITCH, RecognizerIntent.LANGUAGE_SWITCH_BALANCED)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_SWITCH_ALLOWED_LANGUAGES, LANGUAGES)
        }
    }

    companion object {
        private const val TAG = "DeviceSpeech"
        const val PREF_KEY = "live_transcription"
        private const val PRIMARY_LANGUAGE = "he-IL"
        private val LANGUAGES = arrayListOf("he-IL", "en-US")

        /**
         * Errors that mean "this recognizer can't serve us right now" (no
         * network, service busy, language missing). When they happen before any
         * words, the caller falls back to server-side transcription.
         */
        val FALLBACK_ERRORS = setOf(
            SpeechRecognizer.ERROR_NETWORK_TIMEOUT,
            SpeechRecognizer.ERROR_NETWORK,
            SpeechRecognizer.ERROR_AUDIO,
            SpeechRecognizer.ERROR_SERVER,
            SpeechRecognizer.ERROR_CLIENT,
            SpeechRecognizer.ERROR_RECOGNIZER_BUSY,
            SpeechRecognizer.ERROR_TOO_MANY_REQUESTS,
            SpeechRecognizer.ERROR_SERVER_DISCONNECTED,
            SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED,
            SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE,
        )

        /** The kiosk and TV always stream to the server: Gemini handles Hebrew + English far-field speech. */
        fun isEnabled(context: Context): Boolean =
            !app.ganam.assistant.BuildConfig.KIOSK && !app.ganam.assistant.BuildConfig.TV &&
                context.getSharedPreferences("GanamPrefs", Context.MODE_PRIVATE).getBoolean(PREF_KEY, true)

        fun isAvailable(context: Context): Boolean = SpeechRecognizer.isRecognitionAvailable(context)

        private fun Bundle?.firstResult(): String =
            this?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.trim().orEmpty()
    }
}
