package app.ganam.assistant.ui.screens.calls

import app.ganam.assistant.data.models.PhoneCall
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.format.TextStyle
import java.util.Locale

/** The other party: the caller unless the call was made by "Me". */
internal val PhoneCall.contactName: String
    get() = if (caller == "Me") receiver else caller

internal fun PhoneCall.displayTitle(): String =
    title?.takeIf { it.isNotEmpty() }
        ?: (if (caller == "Me") "Call to $receiver" else "Call from $caller")

internal fun parseCallTime(iso: String): LocalDateTime? {
    runCatching { return LocalDateTime.parse(iso) }
    runCatching { return OffsetDateTime.parse(iso).toLocalDateTime() }
    runCatching { return LocalDateTime.parse(iso.take(19)) }
    return null
}

private fun two(n: Int) = n.toString().padStart(2, '0')

/** "14:32" — falls back to the raw timestamp when it can't be parsed. */
internal fun PhoneCall.timeLabel(): String =
    parseCallTime(callTimestamp)?.let { "${two(it.hour)}:${two(it.minute)}" } ?: formatCallTimestamp(callTimestamp)

/** "Fri 6 Nov · 14:32" */
internal fun PhoneCall.fullDateLabel(): String {
    val t = parseCallTime(callTimestamp) ?: return formatCallTimestamp(callTimestamp)
    val dow = t.dayOfWeek.getDisplayName(TextStyle.SHORT, Locale.getDefault())
    val mon = t.month.getDisplayName(TextStyle.SHORT, Locale.getDefault())
    return "$dow ${t.dayOfMonth} $mon · ${two(t.hour)}:${two(t.minute)}"
}

internal data class CallDay(val label: String, val calls: List<PhoneCall>)

internal fun dayLabel(date: LocalDate, today: LocalDate): String {
    val diff = java.time.temporal.ChronoUnit.DAYS.between(date, today)
    return when {
        diff == 0L -> "Today"
        diff == 1L -> "Yesterday"
        diff in 2..6 -> date.dayOfWeek.getDisplayName(TextStyle.FULL, Locale.getDefault())
        date.year == today.year ->
            "${date.dayOfMonth} ${date.month.getDisplayName(TextStyle.SHORT, Locale.getDefault())}"
        else ->
            "${date.dayOfMonth} ${date.month.getDisplayName(TextStyle.SHORT, Locale.getDefault())} ${date.year}"
    }
}

/** Newest first, grouped by calendar day (unparseable timestamps land under "Earlier"). */
internal fun groupCallsByDay(calls: List<PhoneCall>, today: LocalDate = LocalDate.now()): List<CallDay> {
    val sorted = calls.sortedByDescending { parseCallTime(it.callTimestamp) ?: LocalDateTime.MIN }
    val out = LinkedHashMap<String, MutableList<PhoneCall>>()
    for (c in sorted) {
        val d = parseCallTime(c.callTimestamp)?.toLocalDate()
        val label = if (d == null) "Earlier" else dayLabel(d, today)
        out.getOrPut(label) { mutableListOf() }.add(c)
    }
    return out.map { CallDay(it.key, it.value) }
}

internal fun countCallsToday(calls: List<PhoneCall>, today: LocalDate = LocalDate.now()): Int =
    calls.count { parseCallTime(it.callTimestamp)?.toLocalDate() == today }

internal fun monogram(name: String): String =
    name.trim().firstOrNull { it.isLetterOrDigit() }?.uppercase() ?: "?"

internal data class TranscriptTurn(val speaker: String?, val text: String)

private val speakerLine =
    Regex("""^(?:\[?\d{1,2}:\d{2}(?::\d{2})?]?\s*)?\[?([^\]:：\n]{1,28}?)]?\s*[:：]\s*(.*)$""")

/** Splits "Speaker: text" lines into dialogue turns; unlabeled lines continue the previous turn. */
internal fun parseTranscript(transcript: String): List<TranscriptTurn> {
    val turns = ArrayList<TranscriptTurn>()
    for (raw in transcript.lines()) {
        val line = raw.trim()
        if (line.isEmpty()) continue
        val m = speakerLine.matchEntire(line)
        val speaker = m?.groupValues?.get(1)?.trim()
        if (m != null && speaker != null && speaker.any { it.isLetter() }) {
            turns += TranscriptTurn(speaker, m.groupValues[2].trim())
        } else if (turns.isNotEmpty()) {
            val last = turns.removeAt(turns.lastIndex)
            turns += last.copy(text = (last.text + "\n" + line).trim())
        } else {
            turns += TranscriptTurn(null, line)
        }
    }
    return turns
}

private val bulletLine = Regex("""^\s*(?:[-*•▪‣–]|\d{1,2}[.)])\s+(.+)$""")

/** Bullet / numbered lines from the AI summary — treated as follow-ups / key points. */
internal fun extractKeyPoints(summary: String): List<String> =
    summary.lines()
        .mapNotNull { bulletLine.matchEntire(it)?.groupValues?.get(1)?.replace("**", "")?.trim() }
        .filter { it.isNotEmpty() }
