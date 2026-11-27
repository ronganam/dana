package io.github.ronganam.dana.ui.screens.now

import io.github.ronganam.dana.data.models.DaySlotInfo
import io.github.ronganam.dana.data.models.Task
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset

/**
 * One thing left today (from `day.slots`): [time] for a timed one, [place]
 * ("At home", "On waking") for one tied to a place slot; [next] is the first
 * timed one still ahead.
 */
data class DayEntry(
    val title: String,
    val kind: String,
    val time: String?,
    val taskId: String?,
    val eventId: String?,
    val place: String? = null,
    val next: Boolean = false,
    /** The slot's key for a place entry: "home", "wake" (an addon's own otherwise). */
    val placeKey: String? = null
)

/** A reminder finished today ([at]: when, local), for "Earlier today" and the calm day line. */
data class DoneEntry(val title: String, val taskId: String, val at: LocalTime)

/** One node of the calm day line: [fraction] along it (0 = its start), what it is, and its label (null when it would crowd one). */
data class DayLineNode(val fraction: Float, val kind: Kind, val label: String?) {
    enum class Kind { DONE, NOW, NEXT }
}

/*
 * Pure rules of Now's day (tested in NowScreenTest): what is left today (the
 * Next timeline), the reminders done today and the calm state's day line.
 */
object NowDay {

    /** "19:30" → minutes since midnight; null when it isn't a time. */
    fun minutesOf(hhmm: String?): Int? {
        val m = Regex("""^(\d{1,2}):(\d{2})""").find(hhmm?.trim() ?: return null) ?: return null
        val h = m.groupValues[1].toInt()
        val min = m.groupValues[2].toInt()
        return if (h in 0..23 && min in 0..59) h * 60 + min else null
    }

    private fun hourOf(slot: DaySlotInfo): Int? = slot.key.toIntOrNull()?.takeIf { it in 0..23 }

    /**
     * What is left today, in order: timed items from now on (one timed before
     * now is gone), then the place slots' items. The first timed one is next.
     */
    fun upcoming(slots: List<DaySlotInfo>, now: LocalTime): List<DayEntry> {
        val nowMin = now.hour * 60 + now.minute
        val timed = mutableListOf<Pair<Int, DayEntry>>()
        val places = mutableListOf<DayEntry>()
        slots.forEach { slot ->
            val hour = hourOf(slot)
            slot.items.forEach { item ->
                val e = DayEntry(
                    item.title, item.kind, item.time, item.taskId, item.eventId,
                    place = if (hour == null) slot.label else null,
                    placeKey = if (hour == null) slot.key else null
                )
                if (hour == null) places += e
                else {
                    val at = minutesOf(item.time) ?: (hour * 60)
                    if (at >= nowMin) timed += at to e.copy(time = e.time ?: "%02d:00".format(hour))
                }
            }
        }
        val ordered = timed.sortedBy { it.first }.mapIndexed { i, (_, e) -> if (i == 0) e.copy(next = true) else e }
        return ordered + places
    }

    /**
     * The reminders done today, newest first: completed ones whose last
     * change (`updated_at`, the server's UTC "yyyy-MM-dd HH:mm:ss" or ISO
     * with an offset) falls on [today] in [zone].
     */
    fun doneToday(tasks: List<Task>, today: LocalDate, zone: ZoneId = ZoneId.systemDefault()): List<DoneEntry> =
        tasks.mapNotNull { t ->
            if (t.status != "completed") return@mapNotNull null
            val at = utcToLocal(t.updatedAt, zone) ?: return@mapNotNull null
            if (at.toLocalDate() != today) null else DoneEntry(t.title, t.id, at.toLocalTime())
        }.sortedByDescending { it.at }

    /** A server time ("2026-11-27 11:18:24" in UTC, or ISO with an offset) in [zone]; null when unreadable. */
    fun utcToLocal(raw: String?, zone: ZoneId): LocalDateTime? {
        val s = raw?.trim()?.takeIf { it.isNotEmpty() } ?: return null
        runCatching { return OffsetDateTime.parse(s).atZoneSameInstant(zone).toLocalDateTime() }
        return runCatching {
            LocalDateTime.parse(s.replace(' ', 'T').substringBefore('.')).atOffset(ZoneOffset.UTC).atZoneSameInstant(zone).toLocalDateTime()
        }.getOrNull()
    }

    /**
     * The calm state's day line: what was done today (squares), now, and
     * what is still ahead today (circles), placed by time on a line from
     * 08:00 (earlier when something was) to the last thing ahead (at least an
     * hour past now). Labels: "Now" always, the others' times only where
     * they keep [minGapFraction] from a label already placed. Empty when
     * there is nothing done and nothing ahead.
     */
    fun dayLine(done: List<Int>, ahead: List<Int>, nowMin: Int, minGapFraction: Float = 0.16f): List<DayLineNode> {
        val later = ahead.filter { it >= nowMin }
        if (done.isEmpty() && later.isEmpty()) return emptyList()
        val first = (listOf(8 * 60, nowMin) + done).min() / 60 * 60
        val lastAt = (later + (nowMin + 60)).max()
        val last = minOf(24 * 60, (lastAt + 59) / 60 * 60)
        val span = (last - first).coerceAtLeast(60).toFloat()
        fun f(m: Int) = ((m - first) / span).coerceIn(0f, 1f)
        fun hm(m: Int) = "%02d:%02d".format(m / 60 % 24, m % 60)
        val nodes = done.sorted().map { Triple(f(it), DayLineNode.Kind.DONE, hm(it)) } +
            Triple(f(nowMin), DayLineNode.Kind.NOW, "Now") +
            later.sorted().map { Triple(f(it), DayLineNode.Kind.NEXT, hm(it)) }
        val placed = mutableListOf(f(nowMin))
        return nodes.map { (x, kind, label) ->
            val shown = when {
                kind == DayLineNode.Kind.NOW -> label
                placed.all { kotlin.math.abs(it - x) >= minGapFraction } -> label.also { placed += x }
                else -> null
            }
            DayLineNode(x, kind, shown)
        }
    }
}
