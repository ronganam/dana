package io.github.ronganam.dana.data.voice

import java.util.Locale

/**
 * The locales Dana listens and speaks in, from the user's languages in
 * `/api/me` (language codes, the first one theirs): push-to-talk gets them as
 * BCP-47 tags with a region ("de" → "de-DE"), the read-aloud voices pick one
 * by the script of the text. With no languages of the user's own, the phone's
 * locale is used. Pure, so the choice is unit-tested.
 */
object SpeechLocales {
    /** The usual region of a language when the phone's locale doesn't give one. */
    private val REGIONS = mapOf(
        "en" to "US", "he" to "IL", "ar" to "EG", "ru" to "RU", "es" to "ES", "fr" to "FR", "de" to "DE",
        "it" to "IT", "pt" to "PT", "nl" to "NL", "pl" to "PL", "tr" to "TR", "fa" to "IR", "uk" to "UA",
        "zh" to "CN", "ja" to "JP", "ko" to "KR", "hi" to "IN", "am" to "ET", "ro" to "RO", "el" to "GR",
        "sv" to "SE", "da" to "DK", "no" to "NO", "nb" to "NO", "fi" to "FI", "cs" to "CZ", "hu" to "HU",
        "th" to "TH", "vi" to "VN", "id" to "ID", "yi" to "US",
        // The old ISO codes some Android versions still report for Hebrew, Yiddish and Indonesian.
        "iw" to "IL", "ji" to "US", "in" to "ID",
    )

    /** The language a script's text is read in when none of the user's languages uses that script. */
    private val SCRIPT_LANGUAGE = mapOf(
        Character.UnicodeScript.HEBREW to "he", Character.UnicodeScript.ARABIC to "ar",
        Character.UnicodeScript.CYRILLIC to "ru", Character.UnicodeScript.GREEK to "el",
        Character.UnicodeScript.HAN to "zh", Character.UnicodeScript.HIRAGANA to "ja",
        Character.UnicodeScript.KATAKANA to "ja", Character.UnicodeScript.HANGUL to "ko",
        Character.UnicodeScript.DEVANAGARI to "hi", Character.UnicodeScript.THAI to "th",
        Character.UnicodeScript.ETHIOPIC to "am",
    )

    /** The script each language is written in (Latin when not listed). */
    private val LANGUAGE_SCRIPT = mapOf(
        "he" to Character.UnicodeScript.HEBREW, "yi" to Character.UnicodeScript.HEBREW,
        "iw" to Character.UnicodeScript.HEBREW, "ji" to Character.UnicodeScript.HEBREW,
        "ar" to Character.UnicodeScript.ARABIC, "fa" to Character.UnicodeScript.ARABIC,
        "ru" to Character.UnicodeScript.CYRILLIC, "uk" to Character.UnicodeScript.CYRILLIC,
        "el" to Character.UnicodeScript.GREEK, "zh" to Character.UnicodeScript.HAN,
        "ja" to Character.UnicodeScript.HIRAGANA, "ko" to Character.UnicodeScript.HANGUL,
        "hi" to Character.UnicodeScript.DEVANAGARI, "th" to Character.UnicodeScript.THAI,
        "am" to Character.UnicodeScript.ETHIOPIC,
    )

    /**
     * BCP-47 tags for the speech recognizer, the first one the main language:
     * each of [languages] with a region (the phone's when it speaks the same
     * language, else the usual one; a region in the code is kept), or the
     * phone's own locale when [languages] is empty.
     */
    fun forRecognizer(languages: List<String>, device: Locale): List<String> {
        val tags = languages.mapNotNull { code -> code.trim().takeIf { it.isNotEmpty() }?.let { tag(it, device) } }.distinct()
        return tags.ifEmpty { listOf(device.toLanguageTag()) }
    }

    /** One language code as a BCP-47 tag with a region ("de" → "de-DE", "pt-br" → "pt-BR"). */
    fun tag(code: String, device: Locale): String {
        val given = Locale.forLanguageTag(code.replace('_', '-'))
        val language = given.language.ifEmpty { return device.toLanguageTag() }
        val region = given.country.ifEmpty {
            if (device.language == language && device.country.isNotEmpty()) device.country else REGIONS[language].orEmpty()
        }
        return Locale.Builder().setLanguage(language).setRegion(region).build().toLanguageTag()
    }

    /**
     * The locale to read [text] aloud in: the first of the user's
     * [languages] written in the text's main script, else the script's usual
     * language (Arabic letters → Arabic), else the phone's locale. Latin text
     * with no Latin language of the user's own reads in English when the
     * phone's locale isn't Latin either.
     */
    fun forText(text: String, languages: List<String>, device: Locale): Locale {
        val script = mainScript(text)
        val own = languages.firstOrNull { scriptOf(it) == script }
        if (own != null) return Locale.forLanguageTag(tag(own, device))
        if (scriptOf(device.language) == script) return device
        val usual = SCRIPT_LANGUAGE[script] ?: if (script == Character.UnicodeScript.LATIN) "en" else null
        return usual?.let { Locale.forLanguageTag(tag(it, device)) } ?: device
    }

    /** The script most of [text]'s letters are in (Latin for text without letters). */
    fun mainScript(text: String): Character.UnicodeScript {
        val counts = HashMap<Character.UnicodeScript, Int>()
        var i = 0
        while (i < text.length) {
            val cp = text.codePointAt(i)
            if (Character.isLetter(cp)) {
                val script = Character.UnicodeScript.of(cp).let {
                    if (it == Character.UnicodeScript.KATAKANA) Character.UnicodeScript.HIRAGANA else it
                }
                counts[script] = (counts[script] ?: 0) + 1
            }
            i += Character.charCount(cp)
        }
        return counts.maxByOrNull { it.value }?.key ?: Character.UnicodeScript.LATIN
    }

    private fun scriptOf(code: String): Character.UnicodeScript =
        LANGUAGE_SCRIPT[code.substringBefore('-').substringBefore('_').lowercase()] ?: Character.UnicodeScript.LATIN
}
