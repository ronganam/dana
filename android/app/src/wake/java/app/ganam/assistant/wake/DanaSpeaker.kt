package app.ganam.assistant.wake

import android.content.Context
import android.media.AudioAttributes
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.Locale
import java.util.concurrent.atomic.AtomicInteger

/**
 * Speaks Dana's replies, choosing a Hebrew or English voice per reply by its
 * script. Markdown is flattened first so "**bold**" isn't read out as symbols.
 */
class DanaSpeaker(context: Context) {
    private val pending = AtomicInteger(0)
    private val _speaking = MutableStateFlow(false)
    val speaking: StateFlow<Boolean> = _speaking.asStateFlow()

    @Volatile var hebrewAvailable = false
        private set
    @Volatile private var ready = false
    private var seq = 0

    // Prefer Google's engine (it has Hebrew); fall back to the system default.
    private val tts: TextToSpeech = TextToSpeech(context.applicationContext, { status ->
        if (status == TextToSpeech.SUCCESS) onReady() else Log.e(TAG, "TTS init failed: $status")
    }, GOOGLE_TTS.takeIf { isInstalled(context, it) })

    private fun onReady() {
        ready = true
        tts.setAudioAttributes(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build()
        )
        hebrewAvailable = HEBREW.any { tts.isLanguageAvailable(it) >= TextToSpeech.LANG_AVAILABLE }
        Log.i(TAG, "TTS engine=${tts.defaultEngine} hebrew=$hebrewAvailable")
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {}
            override fun onDone(utteranceId: String?) = finished()
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) = finished()
            override fun onError(utteranceId: String?, errorCode: Int) = finished()
            override fun onStop(utteranceId: String?, interrupted: Boolean) = finished()
        })
    }

    private fun finished() {
        if (pending.decrementAndGet() <= 0) {
            pending.set(0)
            _speaking.value = false
        }
    }

    fun speak(raw: String) {
        if (!ready) return
        val text = flatten(raw)
        if (text.isBlank()) return
        val hebrew = isMostlyHebrew(text)
        if (hebrew && !hebrewAvailable) {
            Log.w(TAG, "No Hebrew TTS voice installed; showing the reply without speaking it")
            return
        }
        val locale = if (hebrew) HEBREW.first { tts.isLanguageAvailable(it) >= TextToSpeech.LANG_AVAILABLE } else Locale.US
        tts.language = locale
        // Long replies: the engine limit is ~4000 chars; split on sentences.
        for (chunk in chunks(text, 3500)) {
            pending.incrementAndGet()
            _speaking.value = true
            val result = tts.speak(chunk, TextToSpeech.QUEUE_ADD, Bundle(), "dana_${seq++}")
            if (result != TextToSpeech.SUCCESS) finished()
        }
    }

    fun stop() {
        if (ready) tts.stop()
        pending.set(0)
        _speaking.value = false
    }

    fun shutdown() {
        stop()
        tts.shutdown()
    }

    companion object {
        private const val TAG = "DanaSpeaker"
        private const val GOOGLE_TTS = "com.google.android.tts"

        private fun isInstalled(context: Context, pkg: String): Boolean =
            runCatching { context.packageManager.getPackageInfo(pkg, 0) }.isSuccess

        private val HEBREW = listOf(Locale("he", "IL"), Locale("iw", "IL"), Locale("he"), Locale("iw"))

        fun isMostlyHebrew(text: String): Boolean {
            var he = 0
            var latin = 0
            for (c in text) {
                if (c in '֐'..'׿') he++
                else if (c in 'a'..'z' || c in 'A'..'Z') latin++
            }
            return he > latin
        }

        fun flatten(md: String): String = md
            .replace(Regex("```[\\s\\S]*?```"), " ")
            .replace(Regex("`([^`]*)`"), "$1")
            .replace(Regex("!?\\[([^\\]]*)]\\([^)]*\\)"), "$1")
            .replace(Regex("https?://\\S+"), " ")
            .replace(Regex("(?m)^\\s{0,3}#{1,6}\\s*"), "")
            .replace(Regex("(?m)^\\s*[-*+]\\s+"), "")
            .replace(Regex("[*_~|>]+"), "")
            .replace(Regex("\\s+"), " ")
            .trim()

        private fun chunks(text: String, max: Int): List<String> {
            if (text.length <= max) return listOf(text)
            val out = mutableListOf<String>()
            var rest = text
            while (rest.length > max) {
                val cut = rest.lastIndexOfAny(charArrayOf('.', '!', '?', '\n'), max).takeIf { it > max / 2 } ?: max
                out += rest.substring(0, cut + 1)
                rest = rest.substring(cut + 1)
            }
            if (rest.isNotBlank()) out += rest
            return out
        }
    }
}
