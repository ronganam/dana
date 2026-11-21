package app.ganam.assistant.data.projects

import java.time.LocalDate
import java.time.temporal.ChronoUnit
import java.util.Locale
import kotlin.math.abs
import kotlin.math.roundToInt
import kotlin.math.roundToLong

/** The language a project speaks inside: Hebrew when its titles are Hebrew (the app's frame stays English). */
enum class Lang { EN, HE;
    fun t(en: String, he: String) = if (this == HE) he else en
}

/** How one value shows: [text] in a [style]; [stars] for ratings, [link] for a phone / url to open. */
data class ValueShown(val text: String, val style: Style, val stars: Int = 0, val link: String? = null) {
    enum class Style { NONE, PLAIN, NUMBER, YES, NO, STARS, LINK, DATE }
}

/** What a section card says (see [ProjectsCore.sectionState]). */
data class SectionState(
    val count: Int,
    val chosen: List<Entry>,
    val shortlisted: Int,
    val rejected: Int,
    val line: String,
    val priceMin: Double?,
    val priceMax: Double?
) {
    val decided: Boolean get() = chosen.isNotEmpty()
}

data class Countdown(val days: Long, val label: String, val date: String)

data class BudgetInfo(val budget: Double?, val committed: Double, val left: Double?, val pct: Int?)

data class PhoneLinks(val value: String, val tel: String, val wa: String)

/**
 * The pure part of the Projects screens, ported from the web panel's
 * `static/projects-core.js` (server repo): values by column type, the best
 * value per compared column, option order, section state lines, countdown,
 * budget, the fields a value edit sends, Hebrew words and counts.
 */
object ProjectsCore {

    val DECISIONS = listOf("", "shortlist", "chosen", "rejected")
    private val DECISION_ORDER = mapOf("chosen" to 0, "shortlist" to 1, "" to 2, "rejected" to 3)
    val TYPES = listOf("text", "money", "number", "phone", "url", "date", "checkbox", "rating", "select")
    val TYPE_LABELS = mapOf(
        "text" to "Text", "money" to "Price (₪)", "number" to "Number", "phone" to "Phone", "url" to "Link",
        "date" to "Date", "checkbox" to "Yes / no", "rating" to "Rating", "select" to "Choice"
    )

    fun decisionLabel(d: String, lang: Lang): String = when (d) {
        "shortlist" -> lang.t("Shortlist", "רשימה קצרה")
        "chosen" -> lang.t("Chosen", "נבחר")
        "rejected" -> lang.t("Rejected", "נפסל")
        else -> lang.t("Option", "אפשרות")
    }

    private val rtlChars = Regex("[֐-׿؀-ۿ]")
    fun isRtl(s: String?): Boolean = s != null && rtlChars.containsMatchIn(s)

    /** Hebrew when the project's title or any section title is Hebrew. */
    fun langOf(project: Project?, sections: List<Section> = emptyList()): Lang =
        if (isRtl((project?.title.orEmpty()) + " " + sections.joinToString(" ") { it.title })) Lang.HE else Lang.EN

    fun plural(n: Int, one: String, many: String = one + "s") = "$n ${if (n == 1) one else many}"
    fun plural(n: Long, one: String, many: String = one + "s") = "$n ${if (n == 1L) one else many}"
    /** "אפשרות אחת" / "3 אפשרויות" ([one] already holds the word "one"). */
    fun heCount(n: Int, one: String, many: String) = if (n == 1) one else "$n $many"

    private val numberNoise = Regex("[\\s,₪$€£]|ש[\"״]?ח|nis|ils", RegexOption.IGNORE_CASE)
    private val numberShape = Regex("-?\\d+(\\.\\d+)?")

    /** A number from 4500, "4,500", "₪ 13,400", "13400 ש״ח"; null for a range or text (as the server's as_number). */
    fun asNumber(v: Any?): Double? = when (v) {
        null, is Boolean -> null
        is Number -> v.toDouble().takeIf { it.isFinite() }
        else -> v.toString().replace(numberNoise, "").takeIf { numberShape.matches(it) }?.toDouble()
    }

    private fun grouped(n: Long): String = String.format(Locale.US, "%,d", n)
    fun money(n: Double): String = (if (n < 0) "-₪" else "₪") + grouped(abs(n).roundToLong())
    fun shortMoney(n: Double): String = if (abs(n) >= 1000) {
        val k = (n / 100).roundToLong() / 10.0
        "₪" + (if (k % 1.0 == 0.0) grouped(k.toLong()) else String.format(Locale.US, "%,.1f", k)) + "k"
    } else money(n)

    fun number(n: Double): String =
        if (n % 1.0 == 0.0) grouped(n.toLong()) else String.format(Locale.US, "%,.2f", n).trimEnd('0').trimEnd('.')

    fun field(entry: Entry, name: String): EntryField? = entry.fields.firstOrNull { it.name.equals(name, ignoreCase = true) }
    fun valueOf(entry: Entry, col: Column): Any? = field(entry, col.name)?.value
    fun isEmpty(v: Any?): Boolean = v == null || v == ""

    /** Israeli phone → tel / wa.me digits (972…), null when it holds no number. */
    fun phoneLinks(v: Any?): PhoneLinks? {
        val s = v?.toString().orEmpty()
        val digits = s.replace(Regex("[^\\d+]"), "")
        if (digits.replace(Regex("\\D"), "").length < 7) return null
        var intl = digits.removePrefix("+")
        if (intl.startsWith("0")) intl = "972" + intl.substring(1)
        return PhoneLinks(s, digits, intl.replace(Regex("\\D"), ""))
    }

    /** An http(s) link for [v] ("example.com" → "https://example.com"), "" when it is not one. */
    fun safeUrl(v: Any?): String {
        val s = v?.toString().orEmpty().trim()
        if (s.isEmpty()) return ""
        val u = if (Regex("^[a-z][a-z0-9+.-]*:", RegexOption.IGNORE_CASE).containsMatchIn(s)) s else "https://$s"
        return if (Regex("^https?://", RegexOption.IGNORE_CASE).containsMatchIn(u)) u else ""
    }

    fun host(u: String): String {
        val m = Regex("^[a-z][a-z0-9+.-]*://([^/?#]+)", RegexOption.IGNORE_CASE).find(u) ?: return u
        return m.groupValues[1].substringAfter('@').removePrefix("www.")
    }

    fun formatDate(v: Any?): String {
        val s = v?.toString().orEmpty()
        val m = Regex("^(\\d{4})-(\\d{2})-(\\d{2})").find(s) ?: return s
        return "${m.groupValues[3].toInt()}.${m.groupValues[2].toInt()}.${m.groupValues[1]}"
    }

    /** How a value shows for its column type (compact: tables and cards). */
    fun shown(col: Column?, v: Any?, lang: Lang): ValueShown {
        if (isEmpty(v)) return ValueShown("—", ValueShown.Style.NONE)
        val text = v.toString()
        return when (col?.type ?: "text") {
            "money", "number" -> asNumber(v)?.let { n ->
                ValueShown(if (col?.type == "money") money(n) else number(n), ValueShown.Style.NUMBER)
            } ?: ValueShown(text, ValueShown.Style.PLAIN)
            "checkbox" -> if (v == true) ValueShown("✓ " + lang.t("Yes", "כן"), ValueShown.Style.YES)
                else ValueShown("✕ " + lang.t("No", "לא"), ValueShown.Style.NO)
            "rating" -> {
                val n = (asNumber(v) ?: 0.0).roundToInt().coerceIn(0, 5)
                if (n > 0) ValueShown("$n of 5", ValueShown.Style.STARS, stars = n) else ValueShown(text, ValueShown.Style.PLAIN)
            }
            "phone" -> phoneLinks(v)?.let { ValueShown(text, ValueShown.Style.LINK, link = "tel:${it.tel}") }
                ?: ValueShown(text, ValueShown.Style.PLAIN)
            "url" -> safeUrl(v).takeIf { it.isNotEmpty() }?.let { ValueShown(host(it), ValueShown.Style.LINK, link = it) }
                ?: ValueShown(text, ValueShown.Style.PLAIN)
            "date" -> ValueShown(formatDate(v), ValueShown.Style.DATE)
            else -> ValueShown(text, ValueShown.Style.PLAIN)
        }
    }

    /** Which way a column's best value lies: "low" / "high" / "" (money: low, rating: high, unless set). */
    fun bestWay(col: Column): String = when {
        col.best.isNotEmpty() -> col.best
        col.type == "money" -> "low"
        col.type == "rating" -> "high"
        else -> ""
    }

    /** column key → ids of the options holding that column's best value (rejected left out; only when at
     *  least two options have a number and they are not all equal). */
    fun bestIds(section: Section, entries: List<Entry>): Map<String, Set<String>> {
        val out = mutableMapOf<String, Set<String>>()
        val live = entries.filter { it.decision != "rejected" }
        for (col in section.columns) {
            val way = bestWay(col)
            if (way.isEmpty()) continue
            val nums = live.mapNotNull { e -> asNumber(valueOf(e, col))?.let { e.id to it } }
            if (nums.size < 2) continue
            val pick = if (way == "low") nums.minOf { it.second } else nums.maxOf { it.second }
            if (nums.all { it.second == pick }) continue
            out[col.key] = nums.filter { it.second == pick }.map { it.first }.toSet()
        }
        return out
    }

    /** Options in the order shown: chosen, shortlisted, the rest, rejected; then oldest first. */
    fun orderOptions(entries: List<Entry>): List<Entry> = entries.sortedWith(
        compareBy<Entry> { DECISION_ORDER[it.decision] ?: 2 }.thenBy { it.createdAt.orEmpty() }
    )

    fun priceColumn(section: Section): Column? =
        section.columns.firstOrNull { it.role == "price" } ?: section.columns.firstOrNull { it.type == "money" }

    fun sectionState(section: Section, entries: List<Entry>, lang: Lang): SectionState {
        val mine = entries.filter { it.sectionId == section.id }
        val chosen = mine.filter { it.decision == "chosen" }
        val shortlisted = mine.count { it.decision == "shortlist" }
        val rejected = mine.count { it.decision == "rejected" }
        val col = priceColumn(section)
        val prices = if (col == null) emptyList()
            else mine.filter { it.decision != "rejected" }.mapNotNull { asNumber(valueOf(it, col)) }
        val he = lang == Lang.HE
        val open = mine.size - rejected
        val line = when {
            !section.isOptions -> if (mine.isNotEmpty()) (if (he) heCount(mine.size, "פריט אחד", "פריטים") else plural(mine.size, "item"))
                else lang.t("Nothing yet", "עדיין ריק")
            mine.isEmpty() -> lang.t("No options yet", "אין עדיין אפשרויות")
            chosen.isNotEmpty() -> "${lang.t("Chosen", "נבחר")}: ${chosen.joinToString(", ") { it.title }}"
            he -> listOf(heCount(open, "אפשרות אחת", "אפשרויות"), if (shortlisted > 0) "$shortlisted ברשימה הקצרה" else "", "טרם הוחלט")
                .filter { it.isNotEmpty() }.joinToString(" · ")
            else -> listOf(plural(open, "option"), if (shortlisted > 0) "$shortlisted shortlisted" else "", "not decided")
                .filter { it.isNotEmpty() }.joinToString(" · ")
        }
        return SectionState(mine.size, chosen, shortlisted, rejected, line, prices.minOrNull(), prices.maxOrNull())
    }

    /** "₪12k" or "₪12k–₪14k" for a section card, "" without prices. */
    fun priceRange(st: SectionState): String {
        val lo = st.priceMin ?: return ""
        val hi = st.priceMax ?: lo
        return if (lo == hi) shortMoney(lo) else "${shortMoney(lo)}–${shortMoney(hi)}"
    }

    fun countdown(iso: String?, today: LocalDate, lang: Lang): Countdown? {
        val m = Regex("^(\\d{4})-(\\d{2})-(\\d{2})").find(iso.orEmpty()) ?: return null
        val date = runCatching { LocalDate.of(m.groupValues[1].toInt(), m.groupValues[2].toInt(), m.groupValues[3].toInt()) }
            .getOrNull() ?: return null
        val days = ChronoUnit.DAYS.between(today, date)
        val label = if (lang == Lang.HE) when {
            days == 0L -> "היום"
            days == 1L -> "מחר"
            days > 0 -> "בעוד $days ימים"
            else -> "לפני ${-days} ימים"
        } else when {
            days == 0L -> "Today"
            days == 1L -> "Tomorrow"
            days > 0 -> "in ${plural(days, "day")}"
            else -> "${plural(-days, "day")} ago"
        }
        return Countdown(days, label, formatDate(iso))
    }

    /** Budget and what the chosen options' price columns commit (from the items payload when given). */
    fun budgetInfo(project: Project, sections: List<Section>? = null, entries: List<Entry>? = null): BudgetInfo? {
        val budget = project.budget?.takeIf { it != 0.0 }
        var committed = project.committed
        if (sections != null && entries != null) {
            committed = 0.0
            for (s in sections) {
                val col = s.columns.firstOrNull { it.role == "price" } ?: continue
                for (e in entries) if (e.sectionId == s.id && e.decision == "chosen") committed += asNumber(valueOf(e, col)) ?: 0.0
            }
        }
        if (budget == null && committed == 0.0) return null
        return BudgetInfo(
            budget, committed,
            left = budget?.let { it - committed },
            pct = budget?.let { minOf(100, ((committed / it) * 100).roundToInt()) }
        )
    }

    /** [fields] with column [col] set to [value], typed as the column; other fields kept in order. */
    fun setField(fields: List<EntryField>, col: Column, value: Any?): List<EntryField> {
        val v: Any? = when (col.type) {
            "money", "number", "rating" -> asNumber(value)?.let { if (it % 1.0 == 0.0) it.toLong() else it }
                ?: value?.toString()?.trim()?.ifEmpty { null }
            "checkbox" -> value == true || value == "true"
            else -> value?.toString()?.trim().orEmpty()
        }
        val i = fields.indexOfFirst { it.name.equals(col.name, ignoreCase = true) }
        return if (i >= 0) fields.toMutableList().also { it[i] = EntryField(fields[i].name, col.type, v) }
        else fields + EntryField(col.name, col.type, v)
    }

    /** Fields of an entry that are not columns of its section ("More details"). */
    fun extraFields(entry: Entry, section: Section?): List<EntryField> {
        val names = section?.columns.orEmpty().map { it.name.lowercase() }.toSet()
        return entry.fields.filter { it.name.isNotEmpty() && it.name.lowercase() !in names }
    }

    private val phoneName = Regex("phone|טלפון|נייד", RegexOption.IGNORE_CASE)

    /** The first phone among an entry's columns / fields. */
    fun firstPhone(entry: Entry, section: Section?): PhoneLinks? {
        for (c in section?.columns.orEmpty().filter { it.type == "phone" }) phoneLinks(valueOf(entry, c))?.let { return it }
        for (f in entry.fields) if (f.type == "phone" || phoneName.containsMatchIn(f.name)) phoneLinks(f.value)?.let { return it }
        return null
    }

    fun fileKind(mime: String): String = when {
        mime.startsWith("image/") -> "image"
        mime.startsWith("audio/") -> "audio"
        mime.startsWith("video/") -> "video"
        mime == "application/pdf" -> "pdf"
        else -> "file"
    }

    fun fileSize(n: Long): String = when {
        n >= 1024 * 1024 -> String.format(Locale.US, "%.1f MB", n / (1024.0 * 1024.0))
        n >= 1024 -> "${(n / 1024.0).roundToInt()} KB"
        else -> "$n B"
    }

    /** The editor's columns → what PUT sends: names trimmed, empty and repeated names dropped, keys kept. */
    fun columnsPayload(rows: List<Column>): List<Column> {
        val seen = mutableSetOf<String>()
        return rows.map { it.copy(name = it.name.trim()) }
            .filter { it.name.isNotEmpty() && seen.add(it.name.lowercase()) }
            .map { r ->
                r.copy(
                    type = if (r.type in TYPES) r.type else "text",
                    role = if (r.role == "price") "price" else "",
                    options = if (r.type == "select") r.options.map { it.trim() }.filter { it.isNotEmpty() } else emptyList()
                )
            }
    }

    /** Active first (in the server's order), archived last. */
    fun orderProjects(list: List<Project>): List<Project> = list.sortedBy { if (it.archived) 1 else 0 }

    /** Plain text of a Markdown body for previews. */
    fun preview(body: String?, n: Int = 140): String {
        val t = body.orEmpty().replace(Regex("[#*_>`|-]+"), " ").replace(Regex("\\s+"), " ").trim()
        return if (t.length > n) t.take(n - 1) + "…" else t
    }

    /** A project list row's line: countdown · decisions made (or items). */
    fun projectLine(p: Project, today: LocalDate): String {
        val lang = if (isRtl(p.title)) Lang.HE else Lang.EN
        val cd = countdown(p.targetDate, today, lang)
        return listOfNotNull(
            cd?.takeIf { it.days >= 0 }?.label,
            if (p.decisions > 0) "${p.decided}/${p.decisions} ${lang.t("decided", "הוחלטו")}" else null,
            if (p.decisions == 0) (if (lang == Lang.HE) heCount(p.entryCount, "פריט אחד", "פריטים") else plural(p.entryCount, "item")) else null
        ).joinToString(" · ")
    }

    /** Default columns of a new decision. */
    fun defaultColumns(lang: Lang): List<Column> = if (lang == Lang.HE)
        listOf(Column("", "מחיר", "money", role = "price"), Column("", "טלפון", "phone"), Column("", "דירוג", "rating"))
    else listOf(Column("", "Price", "money", role = "price"), Column("", "Phone", "phone"), Column("", "Rating", "rating"))
}
