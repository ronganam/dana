package app.ganam.assistant.data.shopping

import java.text.Normalizer
import java.util.Locale

/**
 * Item names exactly as the server reads them (server repo
 * `integrations/shopping/names.py`): the comparison key, and typed text split
 * into items with their quantities. The phone splits typed text itself so
 * every item can carry a client id (a retried add never adds twice); this has
 * to stay in step with the server's parser.
 */
object ShoppingText {
    const val MAX_ITEMS = 30
    const val MAX_NAME = 200
    const val MAX_QUANTITY = 40
    const val MAX_NOTE = 200

    /** The key two names are compared by (the same item when equal). */
    fun nameKey(name: String): String {
        val folded = Normalizer.normalize(name, Normalizer.Form.NFKC).uppercase(Locale.ROOT).lowercase(Locale.ROOT)
        val out = StringBuilder()
        var i = 0
        while (i < folded.length) {
            val cp = folded.codePointAt(i)
            i += Character.charCount(cp)
            val type = Character.getType(cp)
            val mark = type == Character.NON_SPACING_MARK.toInt() || type == Character.ENCLOSING_MARK.toInt() ||
                type == Character.COMBINING_SPACING_MARK.toInt()
            if (mark && cp in 0x0591..0x05C7) continue
            when {
                type == Character.DASH_PUNCTUATION.toInt() || cp == '/'.code -> out.append(' ')
                type in PUNCTUATION -> Unit
                else -> out.appendCodePoint(cp)
            }
        }
        return out.toString().replace(SPACES, " ").trim()
    }

    private val PUNCTUATION = setOf(
        Character.CONNECTOR_PUNCTUATION, Character.DASH_PUNCTUATION, Character.START_PUNCTUATION,
        Character.END_PUNCTUATION, Character.INITIAL_QUOTE_PUNCTUATION, Character.FINAL_QUOTE_PUNCTUATION,
        Character.OTHER_PUNCTUATION
    ).map { it.toInt() }.toSet()

    private val SPACES = Regex("[\\s\\p{Z}]+")

    /** One part of typed text: a name and the quantity found in it. */
    data class Part(val name: String, val quantity: String = "")

    private val UNITS = listOf(
        "kg", "kgs", "kilo", "kilos", "g", "gr", "gram", "grams", "l", "lt", "ltr", "liter", "liters", "litre",
        "litres", "ml", "lb", "lbs", "oz", "pack", "packs", "pk", "box", "boxes", "bottle", "bottles", "can", "cans",
        "bag", "bags", "dozen", "jar", "jars", "pcs", "pieces", "piece", "units", "unit", "rolls", "roll", "tray",
        "trays", "bunch", "bunches", "loaf", "loaves",
        "ק\"ג", "ק״ג", "קג", "קילו", "גרם", "גר'", "גר", "ליטר", "ליטרים", "ל'", "מ\"ל", "מ״ל", "מל", "יחידות", "יחידה",
        "יח'", "חבילה", "חבילות", "בקבוק", "בקבוקים", "קופסה", "קופסאות", "קופסא", "שקית", "שקיות", "מגש", "מגשים",
        "תריסר", "זוג", "זוגות", "צרור", "צרורות", "פחית", "פחיות", "גביע", "גביעים", "כיכר", "כיכרות", "רול", "גלילים"
    )
    private const val NUMBER = "\\p{Nd}+(?:[.,]\\p{Nd}+)?|½|¼|¾"
    private val UNIT_RE = UNITS.sortedByDescending { it.length }.joinToString("|") { Regex.escape(it) }
    private val FLAGS = setOf(RegexOption.IGNORE_CASE)
    private val LEADING_AMOUNT = Regex("^($NUMBER)[\\s\\p{Z}]*($UNIT_RE)(?=[\\s\\p{Z}])[\\s\\p{Z}]+(.+)$", FLAGS)
    private val LEADING_COUNT = Regex("^($NUMBER)[\\s\\p{Z}]*(?:[x×][\\s\\p{Z}]*)?[\\s\\p{Z}](?=[^\\s\\p{Z}])(.+)$|^($NUMBER)[\\s\\p{Z}]*[x×][\\s\\p{Z}]*([^\\s\\p{Z}].*)$", FLAGS)
    private val TRAILING_COUNT = Regex("^(.*?[^\\s\\p{Z}])[\\s\\p{Z}]*(?:[\\s\\p{Z}][x×]|×)[\\s\\p{Z}]*($NUMBER)$", FLAGS)
    private val BULLET = Regex("^[\\s\\p{Z}\\-*•·▪›>]+")
    private val NUMBERED = Regex("^[\\s\\p{Z}]*\\p{Nd}+[.)][\\s\\p{Z}]+")
    private val SPLIT = Regex("[\\n\\r,،]+")

    /** [text] as one trimmed line of at most [limit] characters. */
    fun clip(text: String, limit: Int): String = text.trim().split(SPACES).filter { it.isNotEmpty() }.joinToString(" ").take(limit)

    /** One typed line as an item (null: nothing in it). */
    fun parseLine(line: String): Part? {
        var text = BULLET.replaceFirst(NUMBERED.replaceFirst(line, ""), "").trim()
        if (text.isEmpty()) return null
        var quantity = ""
        val amount = LEADING_AMOUNT.find(text)
        if (amount != null) {
            quantity = "${amount.groupValues[1]} ${amount.groupValues[2]}"
            text = amount.groupValues[3]
        } else {
            val count = LEADING_COUNT.find(text)
            if (count != null) {
                val g = count.groupValues
                if (g[1].isNotEmpty()) { quantity = g[1]; text = g[2] } else { quantity = g[3]; text = g[4] }
            } else {
                TRAILING_COUNT.find(text)?.let { quantity = it.groupValues[2]; text = it.groupValues[1] }
            }
        }
        val name = clip(text, MAX_NAME)
        if (name.isEmpty()) return null
        return Part(name, clip(quantity, MAX_QUANTITY))
    }

    /** Typed text as items: split on line breaks and commas (empty parts skipped), names with a key only. */
    fun parse(text: String): List<Part> =
        text.split(SPLIT).mapNotNull(::parseLine).filter { nameKey(it.name).isNotEmpty() }

    /** Pasted lines become one comma-separated line in the add field. */
    fun pasteAsLine(text: String): String =
        if ('\n' in text || '\r' in text) text.split(Regex("[\\r\\n]+")).map { it.trim() }.filter { it.isNotEmpty() }.joinToString(", ")
        else text

    /** "Robin Cohen" → "RC", "Maya" → "M". */
    fun initials(name: String): String {
        val words = name.trim().split(SPACES).filter { it.isNotEmpty() }
        if (words.isEmpty()) return "?"
        val picked = if (words.size > 1) listOf(words.first(), words.last()) else listOf(words.first())
        return picked.joinToString("") { w -> String(Character.toChars(w.codePointAt(0))) }.uppercase(Locale.ROOT)
    }

    fun firstName(name: String?): String = name?.trim()?.split(SPACES)?.firstOrNull()?.takeIf { it.isNotEmpty() } ?: "Someone"
}
