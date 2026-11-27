package io.github.ronganam.dana.ui.screens.now

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.hideFromAccessibility
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.models.DaySlotInfo
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.theme.Hue
import io.github.ronganam.dana.ui.theme.hues
import java.time.LocalTime

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
    val next: Boolean = false
)

/** One hour of the day strip: [past] before the current hour, [busy] when something happens in it. */
data class DaySegment(val hour: Int, val past: Boolean, val busy: Boolean)

/*
 * Pure rules of Now's day (tested in NowScreenTest): the strip's hours (which
 * are past, which hold something) and the list of what is left today.
 */
object NowDay {

    /** The strip starts at 07:00 (earlier when it is earlier) and runs to 23:00 (later when a slot does). */
    private const val FIRST_HOUR = 7
    private const val LAST_HOUR = 23

    /** "19:30" → minutes since midnight; null when it isn't a time. */
    fun minutesOf(hhmm: String?): Int? {
        val m = Regex("""^(\d{1,2}):(\d{2})""").find(hhmm?.trim() ?: return null) ?: return null
        val h = m.groupValues[1].toInt()
        val min = m.groupValues[2].toInt()
        return if (h in 0..23 && min in 0..59) h * 60 + min else null
    }

    private fun hourOf(slot: DaySlotInfo): Int? = slot.key.toIntOrNull()?.takeIf { it in 0..23 }

    /**
     * The day strip, one segment per hour: the past hours (before the current
     * one), then the current hour and the rest. An hour is busy when a slot
     * item falls in it (by its own time, else by its slot). The server lists
     * only the hours still ahead, so past hours are never busy.
     */
    fun segments(slots: List<DaySlotInfo>, now: LocalTime): List<DaySegment> {
        val busy = HashSet<Int>()
        slots.forEach { slot ->
            val hour = hourOf(slot) ?: return@forEach
            slot.items.forEach { item -> busy += (minutesOf(item.time)?.div(60) ?: hour) }
        }
        val last = maxOf(LAST_HOUR, slots.mapNotNull(::hourOf).maxOrNull() ?: 0)
        val first = minOf(FIRST_HOUR, now.hour)
        return (first..last).map { h -> DaySegment(h, past = h < now.hour, busy = h >= now.hour && h in busy) }
    }

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
                val e = DayEntry(item.title, item.kind, item.time, item.taskId, item.eventId, place = if (hour == null) slot.label else null)
                if (hour == null) places += e
                else {
                    val at = minutesOf(item.time) ?: (hour * 60)
                    if (at >= nowMin) timed += at to e
                }
            }
        }
        val ordered = timed.sortedBy { it.first }.mapIndexed { i, (_, e) -> if (i == 0) e.copy(next = true) else e }
        return ordered + places
    }
}

/** Up to this many of the day's entries show under the strip; the rest are "+N later". */
private const val UPCOMING_SHOWN = 3

/**
 * Today in one line: 4dp segments, one per hour, sky where something happens
 * and a hairline where nothing does, past hours at 30 %, and a 2dp ember
 * now-mark before the current hour. Static and decorative (the list under it
 * says the same in words).
 */
@Composable
fun NowDayStrip(segments: List<DaySegment>, modifier: Modifier = Modifier) {
    val accent = MaterialTheme.colorScheme.primary
    val sky = hues.of(Hue.SKY)
    val idle = MaterialTheme.colorScheme.outlineVariant
    val mark = segments.count { it.past }
    Row(
        modifier.widthIn(max = 300.dp).fillMaxWidth().height(18.dp).semantics { hideFromAccessibility() },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp)
    ) {
        segments.forEachIndexed { i, s ->
            if (i == mark) NowMark(accent)
            val color = if (s.busy) sky else idle
            Box(Modifier.weight(1f).height(4.dp).clip(RoundedCornerShape(2.dp)).background(if (s.past) color.copy(alpha = color.alpha * 0.3f) else color))
        }
        if (mark == segments.size) NowMark(accent)
    }
}

@Composable
private fun NowMark(color: Color) {
    Box(Modifier.width(2.dp).height(18.dp).clip(RoundedCornerShape(1.dp)).background(color))
}

/**
 * The strip and what is left today under it as a dot list (a dot in the
 * kind's hue, the title, its time or place in mono): the first three, then
 * "+N later". An entry with a reminder behind it opens it. [centred] is the
 * all-clear layout: the strip and the list's block centred, lines at their start.
 */
@Composable
fun NowDayBlock(slots: List<DaySlotInfo>, now: LocalTime, onOpen: (DayEntry) -> Unit, centred: Boolean, modifier: Modifier = Modifier) {
    val segments = remember(slots, now.hour) { NowDay.segments(slots, now) }
    val upcoming = remember(slots, now.hour, now.minute) { NowDay.upcoming(slots, now) }
    Column(modifier.fillMaxWidth(), horizontalAlignment = if (centred) Alignment.CenterHorizontally else Alignment.Start) {
        NowDayStrip(segments)
        if (upcoming.isNotEmpty()) Column(Modifier.padding(top = 12.dp).then(if (centred) Modifier.width(IntrinsicSize.Max) else Modifier)) {
            upcoming.take(UPCOMING_SHOWN).forEach { e -> DayEntryLine(e, onOpen) }
            if (upcoming.size > UPCOMING_SHOWN) MetaLabel("+${upcoming.size - UPCOMING_SHOWN} later", modifier = Modifier.padding(start = 16.dp, top = 2.dp))
        }
    }
}

@Composable
private fun DayEntryLine(e: DayEntry, onOpen: (DayEntry) -> Unit) {
    val hue = hues.of(NowModel.dayHue(e.kind))
    Row(
        Modifier
            .clip(RoundedCornerShape(6.dp))
            .then(if (e.taskId != null) Modifier.clickable(onClickLabel = "Open") { onOpen(e) } else Modifier)
            .semantics(mergeDescendants = true) {
                contentDescription = listOfNotNull(e.title, e.time ?: e.place, if (e.next) "next" else null).joinToString(", ")
            }
            .heightIn(min = 30.dp)
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(hue))
        Spacer(Modifier.width(8.dp))
        Text(
            e.title,
            style = MaterialTheme.typography.bodyMedium.copy(
                fontSize = 13.5.sp, textDirection = TextDirection.Content,
                fontWeight = if (e.next) FontWeight.Medium else FontWeight.Normal
            ),
            color = if (e.next) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f, fill = false)
        )
        (e.time ?: e.place)?.let { MetaLabel(it, modifier = Modifier.padding(start = 8.dp)) }
    }
}
