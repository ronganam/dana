package io.github.ronganam.dana.data.voice

import android.content.Context
import android.speech.tts.TextToSpeech
import io.github.ronganam.dana.data.network.Me
import java.util.Locale

/**
 * Sets this engine's voice for reading [text]: the locale [SpeechLocales.forText]
 * picks from the text's script and the user's languages (`/api/me`), or the
 * engine's default voice when it has none for that locale. Never throws.
 */
fun TextToSpeech.useVoiceFor(text: String, context: Context) {
    val locale = SpeechLocales.forText(text, Me.cached(context)?.languages.orEmpty(), Locale.getDefault())
    runCatching {
        if (isLanguageAvailable(locale) >= TextToSpeech.LANG_AVAILABLE) language = locale
        else defaultVoice?.let { voice = it }
    }.onFailure { android.util.Log.w("ReadAloud", "Couldn't set a voice for $locale: ${it.message}") }
}
