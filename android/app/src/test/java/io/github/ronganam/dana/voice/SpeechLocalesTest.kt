package io.github.ronganam.dana.voice

import io.github.ronganam.dana.data.voice.SpeechLocales
import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.Locale

class SpeechLocalesTest {
    private val usPhone = Locale.US
    private val swissGermanPhone = Locale.forLanguageTag("de-CH")

    @Test fun `the user's languages in order, each with its usual region`() {
        assertEquals(listOf("de-DE", "en-US"), SpeechLocales.forRecognizer(listOf("de", "en"), usPhone))
        assertEquals(listOf("he-IL", "en-US"), SpeechLocales.forRecognizer(listOf("he", "en"), usPhone))
    }

    @Test fun `the phone's region wins when it speaks the same language`() {
        assertEquals(listOf("de-CH", "en-US"), SpeechLocales.forRecognizer(listOf("de", "en"), swissGermanPhone))
    }

    @Test fun `a region in the code is kept, duplicates and blanks dropped`() {
        assertEquals(listOf("pt-BR", "es-ES"), SpeechLocales.forRecognizer(listOf("pt-br", " ", "es", "es"), usPhone))
    }

    @Test fun `a language without a known region stays bare`() {
        assertEquals(listOf("sw"), SpeechLocales.forRecognizer(listOf("sw"), usPhone))
    }

    @Test fun `no languages of the user's own - the phone's locale`() {
        assertEquals(listOf("de-CH"), SpeechLocales.forRecognizer(emptyList(), swissGermanPhone))
        assertEquals(listOf("en-US"), SpeechLocales.forRecognizer(emptyList(), usPhone))
    }

    @Test fun `read aloud - the user's language in the text's script`() {
        assertEquals("de-DE", SpeechLocales.forText("Guten Morgen, Robin", listOf("de", "en"), usPhone).toLanguageTag())
        assertEquals("he-IL", SpeechLocales.forText("בוקר טוב", listOf("de", "he"), usPhone).toLanguageTag())
    }

    @Test fun `read aloud - another script falls back to its usual language, then the phone`() {
        assertEquals("ar-EG", SpeechLocales.forText("صباح الخير", listOf("en"), usPhone).toLanguageTag())
        assertEquals("ja-JP", SpeechLocales.forText("おはようございます", listOf("en"), usPhone).toLanguageTag())
        assertEquals("ka", SpeechLocales.forText("დილა მშვიდობისა", listOf("en"), Locale.forLanguageTag("ka")).toLanguageTag())
        assertEquals("en-US", SpeechLocales.forText("Good morning", emptyList(), Locale.forLanguageTag("ja-JP")).toLanguageTag())
    }

    @Test fun `the main script is the one most letters are in`() {
        assertEquals(Character.UnicodeScript.HEBREW, SpeechLocales.mainScript("נועה sent the PR לבדיקה היום"))
        assertEquals(Character.UnicodeScript.LATIN, SpeechLocales.mainScript("12:30 — 4"))
    }
}
