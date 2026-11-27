package io.github.ronganam.dana.ui.screens.reminders

import io.github.ronganam.dana.data.models.CalendarEvent
import io.github.ronganam.dana.data.models.Task
import java.time.Duration
import java.time.LocalDateTime

/*
 * The day view at the top of Reminders: the hours from now as one track,
 * with the calendar's events as bars and each reminder that fires then as a
 * point on it, then the two moments that are not a time (waking, home).
 * No Compose here (unit-tested in DayTimelineTest).
 */

/** Something on the track: a calendar event (a bar from [start] to [end]) or a reminder (a point at [start]). */
data class TrackItem(
    val key: String,
    val title: String,
    val start: LocalDateTime,
    val end: LocalDateTime?,
    val isEvent: Boolean,
    val task: Task? = null
)

/** The track from [start] (now) to [end], what is on it, and what waits for waking / home. */
data class DayTrack(
    val start: LocalDateTime,
    val end: LocalDateTime,
    val items: List<TrackItem>,
    val allDay: List<String>,
    val waking: List<Task>,
    val home: List<Task>
) {
    val minutes: Long get() = Duration.between(start, end).toMinutes().coerceAtLeast(1)

    /** Where [t] sits along the track, 0 at now, 1 at its end (clamped). */
    fun fraction(t: LocalDateTime): Float =
        (Duration.between(start, t).toMinutes().toFloat() / minutes).coerceIn(0f, 1f)

    /** The full hours on the track (their ticks and labels). */
    fun hours(): List<LocalDateTime> {
        val out = mutableListOf<LocalDateTime>()
        var h = start.withMinute(0).withSecond(0).withNano(0).plusHours(1)
        while (!h.isAfter(end)) { out += h; h = h.plusHours(1) }
        return out
    }

    /**
     * The time a drop at [fraction] sets: the nearest half hour, never before
     * now (then the next half hour after now).
     */
    fun timeAt(fraction: Float): LocalDateTime {
        val raw = start.plusMinutes((fraction.coerceIn(0f, 1f) * minutes).toLong())
        val base = raw.withSecond(0).withNano(0)
        val m = base.minute
        val snapped = when {
            m < 15 -> base.withMinute(0)
            m < 45 -> base.withMinute(30)
            else -> base.withMinute(0).plusHours(1)
        }
        return if (snapped.isAfter(start)) snapped else nextHalfHour(start)
    }
}

/** The first :00 or :30 strictly after [t]. */
fun nextHalfHour(t: LocalDateTime): LocalDateTime {
    val base = t.withSecond(0).withNano(0)
    return if (base.minute < 30) base.withMinute(30) else base.withMinute(0).plusHours(1)
}

/**
 * How many hours the track spans: as many as fit [widthDp] at about 64dp an
 * hour, 3 to 18, and never past 24 hours.
 */
fun trackHours(widthDp: Float): Int = (widthDp / 64f).toInt().coerceIn(3, 18)

/**
 * The day track from [now] for [hours] hours: timed events that overlap it
 * (one that started before now begins at now), all-day events of today, and
 * every open reminder whose time falls on it; reminders that wait for waking
 * and for arriving home.
 */
fun dayTrack(events: List<CalendarEvent>, tasks: List<Task>, now: LocalDateTime, hours: Int): DayTrack {
    val end = now.plusHours(hours.toLong())
    val today = now.toLocalDate()
    val items = mutableListOf<TrackItem>()
    val allDay = mutableListOf<String>()
    events.forEach { e ->
        val s = parseTriggerTime(e.start) ?: return@forEach
        val en = parseTriggerTime(e.end)
        if (e.allDay) {
            if (!s.toLocalDate().isAfter(today) && (en == null || en.toLocalDate().isAfter(today))) allDay += e.title
            return@forEach
        }
        val stop = en ?: s.plusMinutes(30)
        if (!stop.isAfter(now) || !s.isBefore(end)) return@forEach
        items += TrackItem("e_${e.id}_$s", e.title, if (s.isBefore(now)) now else s, stop, isEvent = true)
    }
    val open = tasks.filter(::isOpen)
    open.forEach { t ->
        val at = remindWhens(t).filterIsInstance<RemindWhen.At>().firstOrNull()?.time ?: return@forEach
        if (at.isBefore(now) || at.isAfter(end)) return@forEach
        items += TrackItem("t_${t.id}", t.title, at, null, isEvent = false, task = t)
    }
    items.sortWith(compareBy({ it.start }, { !it.isEvent }, { it.title.lowercase() }))
    return DayTrack(
        start = now,
        end = end,
        items = items,
        allDay = allDay,
        waking = open.filter { t -> remindWhens(t).any { it == RemindWhen.Wake } },
        home = open.filter { t -> remindWhens(t).any { it is RemindWhen.Place && !it.leave && isHomeZone(it.zone) } }
    )
}

/**
 * Lanes for labels laid along a line: each span (start, end) goes in the
 * first of [lanes] lanes where it starts at least [gap] after the previous
 * one ends; null when none has room (the item then shows as a point only).
 * Spans must be sorted by start.
 */
fun assignLanes(spans: List<Pair<Float, Float>>, lanes: Int, gap: Float): List<Int?> {
    val ends = FloatArray(lanes) { Float.NEGATIVE_INFINITY }
    return spans.map { (s, e) ->
        val lane = (0 until lanes).firstOrNull { ends[it] + gap <= s }
        if (lane != null) ends[lane] = e
        lane
    }
}
