package io.github.ronganam.dana.ui.screens.now

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.models.DaySlotInfo
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.startBar
import io.github.ronganam.dana.ui.theme.DanaSerif
import io.github.ronganam.dana.ui.theme.hues
import java.time.LocalTime
import java.time.format.DateTimeFormatter

/** One thing on today's timeline (from `day.slots`). [past] is dimmed, [next] emphasised. */
data class DayEntry(
    val title: String,
    val kind: String,
    val time: String?,
    val taskId: String?,
    val eventId: String?,
    val past: Boolean = false,
    val next: Boolean = false
)

/** A column of the timeline: the now marker's, an hour with something in it, or a place slot ("At home", "On waking"). */
data class DayColumn(val key: String, val label: String, val type: Type, val entries: List<DayEntry>) {
    enum class Type { NOW, HOUR, PLACE }
}

/*
 * Pure rules of Now's day timeline (tested in NowDayTest): which column an
 * item lands in, which items are past and which one is next.
 */
object NowDay {

    /** "19:30" → minutes since midnight; null when it isn't a time. */
    fun minutesOf(hhmm: String?): Int? {
        val m = Regex("""^(\d{1,2}):(\d{2})""").find(hhmm?.trim() ?: return null) ?: return null
        val h = m.groupValues[1].toInt()
        val min = m.groupValues[2].toInt()
        return if (h in 0..23 && min in 0..59) h * 60 + min else null
    }

    /**
     * The timeline's columns: NOW first (the marker, with anything timed
     * before the first hour slot — the server lists those in the first slot
     * with their own time), then the hour slots that have items, then the
     * place slots that have items. Empty when nothing happens today (the
     * strip is hidden).
     */
    fun columns(slots: List<DaySlotInfo>, now: LocalTime): List<DayColumn> {
        val nowMin = now.hour * 60 + now.minute
        val early = mutableListOf<DayEntry>()
        val hours = mutableListOf<DayColumn>()
        val places = mutableListOf<DayColumn>()
        slots.forEach { slot ->
            val hour = slot.key.toIntOrNull()?.takeIf { it in 0..23 }
            val entries = slot.items.map { DayEntry(it.title, it.kind, it.time, it.taskId, it.eventId) }
            if (hour == null) {
                if (entries.isNotEmpty()) places += DayColumn(slot.key, slot.label, DayColumn.Type.PLACE, entries)
                return@forEach
            }
            val (before, inHour) = entries.partition { e -> minutesOf(e.time)?.let { it < hour * 60 } == true }
            early += before
            if (inHour.isNotEmpty()) hours += DayColumn(slot.key, slot.label, DayColumn.Type.HOUR, inHour)
        }
        if (early.isEmpty() && hours.isEmpty() && places.isEmpty()) return emptyList()

        val nowCol = DayColumn("now", "Now", DayColumn.Type.NOW, early.sortedBy { minutesOf(it.time) ?: 0 })
        // Past = timed earlier than now (an answer from a little while ago); next = the first one that isn't.
        var nextFound = false
        fun mark(col: DayColumn): DayColumn = col.copy(entries = col.entries.map { e ->
            val m = minutesOf(e.time)
            val past = m != null && m < nowMin
            val next = !past && !nextFound
            if (next) nextFound = true
            e.copy(past = past, next = next)
        })
        return listOf(mark(nowCol)) + hours.map(::mark) + places
    }
}

private val HM = DateTimeFormatter.ofPattern("HH:mm")
private val COLUMN = 128.dp

/**
 * Today as a horizontal timeline: the now marker (an ember line with "NOW"
 * and the time), the hours with something in them, then "At home" / "On
 * waking". Past items are dimmed, the next one is emphasised; tapping an item
 * with a reminder behind it opens it. Static: nothing animates here.
 */
@Composable
fun NowDayTimeline(slots: List<DaySlotInfo>, now: LocalTime, onOpen: (DayEntry) -> Unit, modifier: Modifier = Modifier) {
    val columns = remember(slots, now.hour, now.minute) { NowDay.columns(slots, now) }
    if (columns.isNotEmpty()) {
        val accent = MaterialTheme.colorScheme.primary
        val rule = MaterialTheme.colorScheme.outlineVariant
        Row(
            modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = NOW_GUTTER)
                .height(IntrinsicSize.Min)
        ) {
            columns.forEachIndexed { i, col ->
                val isNow = col.type == DayColumn.Type.NOW
                // A place slot after the hours starts after a little gap.
                if (col.type == DayColumn.Type.PLACE && columns.getOrNull(i - 1)?.type != DayColumn.Type.PLACE) Spacer(Modifier.width(10.dp))
                Column(
                    Modifier
                        .width(if (isNow && col.entries.isEmpty()) 104.dp else COLUMN)
                        .fillMaxHeight()
                        .drawBehind {
                            // The axis under the labels, and the now marker across the strip.
                            val y = 22.dp.toPx()
                            drawLine(rule, Offset(0f, y), Offset(size.width, y), 1.dp.toPx())
                            if (isNow) {
                                val x = if (layoutDirection == LayoutDirection.Rtl) size.width - 1.dp.toPx() else 1.dp.toPx()
                                drawLine(accent, Offset(x, 0f), Offset(x, size.height), 2.dp.toPx())
                                drawCircle(accent, 3.5.dp.toPx(), Offset(x, y))
                            }
                        }
                        .padding(start = if (isNow) 10.dp else 0.dp, end = 10.dp, bottom = 4.dp)
                ) {
                    Row(Modifier.height(18.dp), verticalAlignment = Alignment.CenterVertically) {
                        if (isNow) {
                            MonoLabel("Now", color = accent)
                            Text(
                                " " + now.format(HM),
                                style = MaterialTheme.typography.bodySmall.copy(fontFamily = DanaSerif, fontStyle = FontStyle.Italic, fontSize = 14.sp),
                                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1
                            )
                        } else MonoLabel(col.label)
                    }
                    Spacer(Modifier.height(10.dp))
                    col.entries.forEach { e -> DayEntryLine(e, onOpen) }
                }
            }
        }
    }
}

@Composable
private fun DayEntryLine(e: DayEntry, onOpen: (DayEntry) -> Unit) {
    val h = hues
    val hue = h.of(NowModel.dayHue(e.kind))
    val opens = e.taskId != null
    val style = MaterialTheme.typography.bodySmall.copy(
        fontSize = 12.5.sp, lineHeight = 15.sp, textDirection = TextDirection.Content,
        fontWeight = if (e.next) FontWeight.SemiBold else FontWeight.Normal
    )
    val color = if (e.next) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant
    Column(
        Modifier
            .padding(bottom = 6.dp)
            .fillMaxWidth()
            .alpha(if (e.past) 0.45f else 1f)
            .clip(RoundedCornerShape(6.dp))
            .then(if (e.kind == "event") Modifier.background(h.tint(hue)).startBar(hue, 2.dp) else Modifier)
            .then(if (opens) Modifier.clickable { onOpen(e) } else Modifier)
            .semantics { contentDescription = listOfNotNull(e.time, e.title, if (e.next) "next" else null).joinToString(", ") }
            .padding(start = if (e.kind == "event") 8.dp else 0.dp, end = 4.dp, top = 3.dp, bottom = 3.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (e.kind != "event") Box(Modifier.padding(end = 5.dp).size(5.dp).clip(CircleShape).background(hue))
            e.time?.let { MonoLabel(it, color = if (e.next) h.text(hue) else MaterialTheme.colorScheme.onSurfaceVariant) }
        }
        Text(e.title, style = style, color = color, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
}
