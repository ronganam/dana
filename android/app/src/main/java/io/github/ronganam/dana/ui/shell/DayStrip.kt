package io.github.ronganam.dana.ui.shell

import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.clip
import io.github.ronganam.dana.ui.components.startBar
import io.github.ronganam.dana.ui.theme.hues
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.models.CalendarEvent
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.screens.reminders.parseTriggerTime
import io.github.ronganam.dana.ui.screens.reminders.remindWhens
import io.github.ronganam.dana.ui.screens.reminders.RemindWhen
import io.github.ronganam.dana.ui.screens.reminders.slotOf
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import kotlinx.coroutines.delay
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/** One line in the day strip or "Later today": a calendar event or a reminder that fires then. */
data class DayItem(val title: String, val at: LocalDateTime?, val isEvent: Boolean, val taskId: String? = null)

/** The current time, updated on the minute. */
@Composable
fun rememberNow(): State<LocalDateTime> = produceState(LocalDateTime.now()) {
    while (true) {
        val now = LocalDateTime.now()
        value = now
        delay((60 - now.second) * 1000L - now.nano / 1_000_000 + 50)
    }
}

/**
 * What happens in each slot of the rest of today: timed events by their start
 * hour (all-day ones and anything in the current hour go to "now", key null),
 * reminders by [slotOf].
 */
fun dayItems(events: List<CalendarEvent>, tasks: List<Task>, now: LocalDateTime): Map<DaySlot?, List<DayItem>> {
    val out = LinkedHashMap<DaySlot?, MutableList<DayItem>>()
    val today = now.toLocalDate()
    events.forEach { e ->
        val start = parseTriggerTime(e.start) ?: return@forEach
        val end = parseTriggerTime(e.end)
        if (e.allDay) {
            if (!start.toLocalDate().isAfter(today) && (end == null || end.toLocalDate().isAfter(today))) {
                out.getOrPut(null) { mutableListOf() } += DayItem(e.title, null, true)
            }
            return@forEach
        }
        if (start.toLocalDate() != today) return@forEach
        if (start.hour < now.hour && (end == null || end.isBefore(now))) return@forEach
        val slot: DaySlot? = if (start.hour <= now.hour) null else DaySlot.Hour(start.hour)
        out.getOrPut(slot) { mutableListOf() } += DayItem(e.title, start, true)
    }
    tasks.forEach { t ->
        val slot = slotOf(t, now) ?: return@forEach
        val at = remindWhens(t).filterIsInstance<RemindWhen.At>().firstOrNull()?.time
        val key: DaySlot? = if (slot is DaySlot.Hour && slot.hour <= now.hour) null else slot
        out.getOrPut(key) { mutableListOf() } += DayItem(t.title, at, false, t.id)
    }
    out.values.forEach { list -> list.sortWith(compareBy({ it.at ?: LocalDateTime.MIN }, { !it.isEvent })) }
    return out
}

private val HM = DateTimeFormatter.ofPattern("HH:mm")

/**
 * Unfolded only: "Now HH:mm", the coming hours, "At home", "On waking", each
 * with today's events and the reminders that fire then. Every slot but "Now"
 * accepts drops ([dragToDay]). Tapping "Now" opens the Now place.
 */
@Composable
fun DayStrip(viewModel: MainViewModel, onNow: () -> Unit, modifier: Modifier = Modifier) {
    val events by viewModel.dayController.events.collectAsState()
    val tasks by viewModel.tasksController.tasks.collectAsState()
    val now by rememberNow()
    LaunchedEffect(now.toLocalDate()) { viewModel.dayController.refreshIfStale() }
    val items = remember(events, tasks, now) { dayItems(events, tasks, now) }
    val drop = LocalDayDrop.current

    BoxWithConstraints(modifier.fillMaxWidth()) {
        val hourCells = ((maxWidth.value - 84f - 2 * 92f) / 84f).toInt().coerceIn(2, 8)
        val hours = ((now.hour + 1)..23).take(hourCells)
        Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min).heightIn(min = 92.dp)) {
            Column(
                Modifier.width(84.dp).fillMaxHeight().clickable(onClick = onNow).edgeRule(end = true).padding(horizontal = 10.dp, vertical = 10.dp)
            ) {
                MetaLabel("Now", color = MaterialTheme.colorScheme.primary)
                Text(
                    now.format(HM),
                    style = MaterialTheme.typography.headlineMedium.copy(fontSize = 26.sp),
                    maxLines = 1
                )
                items[null].orEmpty().take(2).forEach { SlotItem(it) }
            }
            hours.forEach { h ->
                SlotCell(DaySlot.Hour(h), "%02d:00".format(h), items[DaySlot.Hour(h)].orEmpty(), drop?.hovered == DaySlot.Hour(h), Modifier.weight(1f))
            }
            SlotCell(DaySlot.AtHome, "At home", items[DaySlot.AtHome].orEmpty(), drop?.hovered == DaySlot.AtHome, Modifier.weight(1.1f))
            SlotCell(DaySlot.OnWaking, "On waking", items[DaySlot.OnWaking].orEmpty(), drop?.hovered == DaySlot.OnWaking, Modifier.weight(1.1f), last = true)
        }
    }
}

@Composable
private fun SlotCell(slot: DaySlot, label: String, items: List<DayItem>, hovered: Boolean, modifier: Modifier, last: Boolean = false) {
    val accent = MaterialTheme.colorScheme.primary
    Column(
        modifier
            .fillMaxHeight()
            .dayDropTarget(slot)
            .then(
                if (hovered) Modifier.background(hues.tint(accent)).border(1.dp, accent)
                else Modifier
            )
            .then(if (last) Modifier else Modifier.edgeRule(end = true))
            .padding(horizontal = 8.dp, vertical = 10.dp)
    ) {
        MetaLabel(label)
        val shown = items.take(3)
        shown.forEach { SlotItem(it) }
        if (items.size > shown.size) MetaLabel("+${items.size - shown.size}", modifier = Modifier.padding(top = 4.dp))
    }
}

@Composable
private fun SlotItem(item: DayItem) {
    val style = MaterialTheme.typography.bodySmall.copy(fontSize = 11.5.sp, lineHeight = 14.sp, textDirection = TextDirection.Content)
    // Events on a sky chip with a sky start bar, reminders with an amber dot (Ember's kinds).
    val h = hues
    if (item.isEvent) {
        Text(
            item.title,
            style = style,
            color = MaterialTheme.colorScheme.onSurface,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(top = 6.dp).fillMaxWidth()
                .clip(RoundedCornerShape(6.dp))
                .background(h.tint(h.sky))
                .startBar(h.sky, 2.dp)
                .padding(start = 8.dp, end = 6.dp, top = 3.dp, bottom = 3.dp)
        )
    } else {
        Row(Modifier.padding(top = 6.dp).fillMaxWidth(), verticalAlignment = Alignment.Top) {
            Box(Modifier.padding(top = 5.dp, end = 5.dp).size(5.dp).clip(CircleShape).background(h.amber))
            Text(
                item.title,
                style = style,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f)
            )
        }
    }
}
