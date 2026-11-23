package io.github.ronganam.dana.data.sync

import java.time.LocalDateTime
import java.time.ZoneId
import kotlin.math.abs

/** What a recording's file name says: who (a name and / or a number) and when the recording started. */
data class RecordingName(val name: String?, val number: String?, val start: LocalDateTime?)

/**
 * Reads the file names call recorders write. Every recorder has its own
 * pattern, so this looks for the parts rather than one format:
 * - Samsung: `Call<name or number>_<yyMMdd>_<HHmmss>.m4a` (bidi marks around the name)
 * - Xiaomi: `<name>(<number>)_<yyyyMMddHHmmss>.mp3`
 * - OnePlus / Oppo and others: `<name or number> <yyyy-MM-dd HH-mm-ss>.m4a`,
 *   `Call_<number>_<yyyyMMdd-HHmmss>_IN.m4a`, …
 * A name with neither a time nor a number in it (`Voice 001.m4a`) says nothing.
 */
object RecordingNames {
    private val BIDI = Regex("[\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069]")
    private val EXTENSION = Regex("\\.[A-Za-z0-9]{2,5}$")

    // A four-digit year, then month, day, hour, minute and (optionally) second, any separators.
    private val LONG_TIME = Regex("(?<!\\d)(20\\d{2})[-_.]?(\\d{2})[-_.]?(\\d{2})[ _T.-]?(\\d{2})[-_.:h]?(\\d{2})(?:[-_.:m]?(\\d{2}))?(?!\\d)")
    // Samsung: _yyMMdd_HHmmss.
    private val SHORT_TIME = Regex("(?<!\\d)(\\d{2})(\\d{2})(\\d{2})_(\\d{2})(\\d{2})(\\d{2})(?!\\d)")
    // A phone number: at least seven digits, maybe a leading +, spaces, dashes, dots or brackets inside.
    private val NUMBER = Regex("\\+?\\d[\\d \\-.()]{5,}\\d")
    private val DIRECTION = Regex("(?i)(?:^|[\\s_\\-])(in|out|incoming|outgoing)$")
    private val GENERIC_PREFIX = Regex("(?i)^(?:call\\s*recording|recording|record|rec)(?=$|[\\s_\\-])")

    /** The parts of [fileName] (no path). */
    fun parse(fileName: String): RecordingName {
        var rest = EXTENSION.replace(BIDI.replace(fileName, " "), "").trim()
        var start: LocalDateTime? = null
        // Samsung's six-and-six digits first: read as a four-digit year they could pass for another date.
        SHORT_TIME.find(rest)?.let { m ->
            val g = m.groupValues
            start = dateTime(2000 + g[1].toInt(), g[2], g[3], g[4], g[5], g[6])
            if (start != null) rest = rest.removeRange(m.range)
        }
        if (start == null) {
            LONG_TIME.find(rest)?.let { m ->
                val g = m.groupValues
                start = dateTime(g[1].toInt(), g[2], g[3], g[4], g[5], g[6].ifEmpty { "00" })
                if (start != null) rest = rest.removeRange(m.range)
            }
        }
        rest = stripCallPrefix(rest)
        var number: String? = null
        NUMBER.findAll(rest).firstOrNull { m -> m.value.count(Char::isDigit) >= 7 }?.let { m ->
            number = m.value.filter { it.isDigit() || it == '+' }
            rest = rest.removeRange(m.range)
        }
        if (start == null && number == null) return RecordingName(null, null, null)
        rest = rest.replace("()", " ").replace(Regex("[()\\[\\]]"), " ")
        rest = cleanEdges(rest)
        rest = cleanEdges(DIRECTION.replace(rest, ""))
        val name = rest.takeIf { it.any(Char::isLetter) }
        return RecordingName(name, number, start)
    }

    /** "Call" / "Call recording" in front of the rest (Samsung writes "Call" right before the name). */
    private fun stripCallPrefix(text: String): String {
        val t = text.trim()
        GENERIC_PREFIX.find(t)?.let { return t.substring(it.range.last + 1) }
        if (t.length > 4 && t.startsWith("Call") && (t[4].isUpperCase() || t[4] == '+' || t[4].isDigit() || t[4] in " _-")) {
            return t.substring(4)
        }
        return t
    }

    private fun cleanEdges(text: String): String =
        text.replace(Regex("[\\s_]+"), " ").trim { it.isWhitespace() || it in "_-.,;:" }

    private fun dateTime(year: Int, month: String, day: String, hour: String, minute: String, second: String): LocalDateTime? =
        runCatching { LocalDateTime.of(year, month.toInt(), day.toInt(), hour.toInt(), minute.toInt(), second.toInt()) }.getOrNull()
}

/** One call-log entry ([startMs] epoch ms, [direction] incoming / outgoing / missed). */
data class LoggedCall(
    val startMs: Long,
    val durationSeconds: Int,
    val number: String?,
    val name: String?,
    val direction: String
) {
    val endMs: Long get() = startMs + durationSeconds * 1000L
}

/**
 * Which call a recording is of. With a number in the file name, the
 * answered call with that number closest in time (within 12 h). Otherwise by
 * time alone: the recording's start from the file name inside the call
 * (from 3 min before its start to 2 min after its end), closest to the call's
 * start; without a time in the name, the file's last change (the recorder
 * stops writing when the call ends) 2 min before to 10 min after the call's
 * end, closest to that end. Missed calls (0 s) have no recording.
 */
object CallMatcher {
    private const val NUMBER_WINDOW_MS = 12L * 3600 * 1000
    private const val BEFORE_START_MS = 3L * 60 * 1000
    private const val AFTER_END_MS = 2L * 60 * 1000
    private const val FILE_BEFORE_END_MS = 2L * 60 * 1000
    private const val FILE_AFTER_END_MS = 10L * 60 * 1000

    /** The time the call log is read around: the name's start, else the file's last change. */
    fun referenceMs(rec: RecordingName, fileModifiedMs: Long, zone: ZoneId): Long =
        rec.start?.atZone(zone)?.toInstant()?.toEpochMilli() ?: fileModifiedMs

    fun match(rec: RecordingName, fileModifiedMs: Long, calls: List<LoggedCall>, zone: ZoneId): LoggedCall? {
        val answered = calls.filter { it.durationSeconds > 0 }
        val startMs = rec.start?.atZone(zone)?.toInstant()?.toEpochMilli()
        rec.number?.let { number ->
            val byNumber = answered.filter { sameNumber(it.number, number) }
            val ref = startMs ?: fileModifiedMs.takeIf { it > 0 }
            val best = if (ref == null) byNumber.maxByOrNull { it.startMs }
            else byNumber.filter { abs(distance(it, startMs, ref)) <= NUMBER_WINDOW_MS }.minByOrNull { abs(distance(it, startMs, ref)) }
            if (best != null) return best
        }
        if (startMs != null) {
            return answered
                .filter { startMs >= it.startMs - BEFORE_START_MS && startMs <= it.endMs + AFTER_END_MS }
                .minByOrNull { abs(startMs - it.startMs) }
        }
        if (fileModifiedMs <= 0) return null
        return answered
            .filter { fileModifiedMs >= it.endMs - FILE_BEFORE_END_MS && fileModifiedMs <= it.endMs + FILE_AFTER_END_MS }
            .minByOrNull { abs(fileModifiedMs - it.endMs) }
    }

    private fun distance(call: LoggedCall, startMs: Long?, ref: Long): Long =
        if (startMs != null) startMs - call.startMs else ref - call.endMs

    /** The same phone number however it is written (the last nine digits, or all of a shorter one). */
    fun sameNumber(a: String?, b: String?): Boolean {
        val da = a.orEmpty().filter(Char::isDigit)
        val db = b.orEmpty().filter(Char::isDigit)
        if (da.length < 7 || db.length < 7) return false
        return if (da.length >= 9 && db.length >= 9) da.takeLast(9) == db.takeLast(9) else da == db
    }
}
