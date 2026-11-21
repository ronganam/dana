package app.ganam.assistant.ui.components

import android.content.Context
import android.speech.tts.TextToSpeech
import java.util.Locale

/**
 * Speaks a reply when it is tapped. The engine is bound on the first tap, not
 * when the sheet opens: binding wakes the TTS service, which on the tablet and
 * the TV costs CPU right when the sheet is animating in, for a feature that is
 * rarely used.
 */
class TapToSpeech(private val context: Context) {
    private var tts: TextToSpeech? = null
    private var ready = false
    private var pending: String? = null

    fun speak(text: String) {
        val clean = text.replace(Regex("[*#`_\\-]"), "").trim()
        if (clean.isEmpty()) return
        val engine = tts
        if (engine == null) {
            pending = clean
            tts = TextToSpeech(context.applicationContext) { status ->
                ready = status == TextToSpeech.SUCCESS
                pending?.let { if (ready) say(it) }
                pending = null
            }
        } else if (ready) {
            say(clean)
        } else {
            pending = clean
        }
    }

    private fun say(text: String) {
        val engine = tts ?: return
        engine.language = if (text.any { it in '֐'..'׿' }) Locale.forLanguageTag("he-IL") else Locale.US
        engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, null)
    }

    fun shutdown() {
        tts?.run { stop(); shutdown() }
        tts = null
        ready = false
        pending = null
    }
}
