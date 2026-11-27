package io.github.ronganam.dana.ui.shell

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.WbSunny
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.ui.components.RollingText
import io.github.ronganam.dana.ui.components.arrive
import io.github.ronganam.dana.ui.screens.reminders.DayTrack
import io.github.ronganam.dana.ui.screens.reminders.TrackItem
import io.github.ronganam.dana.ui.screens.reminders.assignLanes
import io.github.ronganam.dana.ui.screens.reminders.dayTrack
import io.github.ronganam.dana.ui.screens.reminders.reminderHue
import io.github.ronganam.dana.ui.screens.reminders.remindWhenFor
import io.github.ronganam.dana.ui.screens.reminders.trackHours
import io.github.ronganam.dana.ui.theme.Motion
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.hues
import io.github.ronganam.dana.ui.theme.motionOff
import io.github.ronganam.dana.ui.theme.tabular
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import kotlinx.coroutines.delay
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.roundToInt

/** The current time, updated on the minute. */
@Composable
fun rememberNow(): State<LocalDateTime> = produceState(LocalDateTime.now()) {
    while (true) {
        val now = LocalDateTime.now()
        value = now
        delay((60 - now.second) * 1000L - now.nano / 1_000_000 + 50)
    }
}

private val HM = DateTimeFormatter.ofPattern("HH:mm")

/** "Fri 27 Nov" (off the composition: the locale is read here, not in a composable). */
private fun dateLabel(t: LocalDateTime): String = t.format(DateTimeFormatter.ofPattern("EEE d MMM", Locale.getDefault()))

/** An hour tick's label: "15:00", or the day's short name at midnight. */
private fun hourLabel(t: LocalDateTime): String =
    if (t.hour == 0) t.format(DateTimeFormatter.ofPattern("EEE", Locale.getDefault())) else t.format(HM)

/**
 * The day view at the top of Reminders: Now (the time, the date, today's
 * all-day events) | the coming hours as one track — calendar events as sky
 * bars (their time also marked on the line), each reminder as a point in its
 * list's colour with its title above — | On waking and Home, the two
 * moments that are not a time. Every part takes a drop: a reminder dragged
 * onto the track is set to the nearest half hour under the finger (shown on
 * the line while dragging), onto On waking / Home to that moment. A
 * reminder's chip opens it ([onOpenReminder]) and can be dragged along the
 * track. [compact]: the cover screen (fewer lanes, the moments as glyphs).
 */
@Composable
fun DayView(
    viewModel: MainViewModel,
    onNow: () -> Unit,
    onOpenReminder: (String) -> Unit,
    modifier: Modifier = Modifier,
    compact: Boolean = false
) {
    val events by viewModel.dayController.events.collectAsState()
    val tasks by viewModel.tasksController.tasks.collectAsState()
    val zones by viewModel.tasksController.zones.collectAsState()
    val now by rememberNow()
    LaunchedEffect(now.toLocalDate()) { viewModel.dayController.refreshIfStale() }
    val drop = LocalDayDrop.current

    BoxWithConstraints(
        modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = if (compact) 8.dp else 12.dp)
    ) {
        val nowW = if (compact) 90.dp else 112.dp
        val momentsW = if (compact) 52.dp else 140.dp
        val trackW = maxWidth - nowW - momentsW - 24.dp
        val hours = trackHours(trackW.value)
        val track = remember(events, tasks, now, hours) { dayTrack(events, tasks, now, hours) }
        Row(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(MaterialTheme.colorScheme.surfaceContainerLow)
                .padding(start = 16.dp, end = 10.dp, top = 12.dp, bottom = 10.dp),
            verticalAlignment = Alignment.Top
        ) {
            NowBlock(now, track.allDay, onNow, Modifier.width(nowW - 16.dp), compact)
            Spacer(Modifier.width(8.dp))
            Track(
                track = track,
                lanes = if (compact) 2 else 3,
                hovered = (drop?.hovered as? DaySlot.At)?.time,
                dragging = drop?.dragging != null,
                onOpen = onOpenReminder,
                onMove = { item, slot ->
                    item.task?.let { t -> viewModel.tasksController.setTriggers(t.id, remindWhenFor(slot, LocalDateTime.now(), zones).toTriggers(), dropWords(slot)) }
                },
                modifier = Modifier.weight(1f)
            )
            Spacer(Modifier.width(8.dp))
            Column(Modifier.width(momentsW - 10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                MomentTarget(DaySlot.OnWaking, Icons.Outlined.WbSunny, "On waking", track.waking.size, drop?.hovered == DaySlot.OnWaking, drop?.dragging != null, compact)
                MomentTarget(DaySlot.AtHome, Icons.Outlined.Home, "Home", track.home.size, drop?.hovered == DaySlot.AtHome, drop?.dragging != null, compact)
            }
        }
    }
}

/** The undo message after a drop on the day view ("Set to 18:30", "Set to waking", "Set to arriving home"). */
private fun dropWords(slot: DaySlot): String = when (slot) {
    is DaySlot.At -> "Set to " + slot.time.format(HM)
    is DaySlot.Hour -> "Set to %02d:00".format(slot.hour)
    DaySlot.OnWaking -> "Set to waking"
    DaySlot.AtHome -> "Set to arriving home"
    is DaySlot.Moment -> "Moved"
}

@Composable
private fun NowBlock(now: LocalDateTime, allDay: List<String>, onNow: () -> Unit, modifier: Modifier, compact: Boolean) {
    val c = circuitColors
    Column(modifier.clip(RoundedCornerShape(12.dp)).clickable(onClick = onNow).padding(vertical = 2.dp)) {
        Text("Now", style = MaterialTheme.typography.labelLarge, color = c.accent)
        RollingText(
            now.format(HM),
            // Sized so "23:59" never clips in its column.
            style = MaterialTheme.typography.headlineMedium.copy(
                fontSize = if (compact) 22.sp else 26.sp, lineHeight = if (compact) 26.sp else 30.sp, fontWeight = FontWeight.SemiBold
            ).tabular(),
            color = c.ink
        )
        Text(
            dateLabel(now),
            style = MaterialTheme.typography.bodySmall,
            color = c.inkSoft,
            maxLines = 1
        )
        allDay.take(2).forEach {
            Text(
                it,
                style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content),
                color = hues.text(hues.sky),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(top = 4.dp)
            )
        }
    }
}

private val LANE_H = 24.dp
private val LANE_GAP = 4.dp

/**
 * The hours as a line: the lanes of chips above it, hour ticks and labels
 * under it. Drawn once per change; while dragging, the half hour under the
 * finger is marked on the line with its time.
 */
@Composable
private fun Track(
    track: DayTrack,
    lanes: Int,
    hovered: LocalDateTime?,
    dragging: Boolean,
    onOpen: (String) -> Unit,
    onMove: (TrackItem, DaySlot) -> Unit,
    modifier: Modifier
) {
    val c = circuitColors
    val h = hues
    val density = LocalDensity.current
    val measurer = rememberTextMeasurer()
    val chipStyle = MaterialTheme.typography.bodySmall.copy(fontSize = 12.sp, lineHeight = 16.sp)
    val labelStyle = MaterialTheme.typography.labelSmall.tabular()
    val lineY = LANE_H * lanes + LANE_GAP * (lanes - 1) + 14.dp
    val totalH = lineY + 26.dp
    val skyLine = h.sky
    val accent = c.accent
    val wire = c.wire
    // One soft pulse from the Now point each minute (none with animations off).
    val pulse = remember { Animatable(1f) }
    val animate = !motionOff()
    LaunchedEffect(track.start) {
        if (animate) { pulse.snapTo(0f); pulse.animateTo(1f, tween(1400, easing = Motion.Decelerate)) }
    }

    val widthPx = remember { floatArrayOf(1f) }
    BoxWithConstraints(
        modifier.height(totalH)
            .onSizeChanged { widthPx[0] = it.width.coerceAtLeast(1).toFloat() }
            .dayDropArea("day_track") { p -> DaySlot.At(track.timeAt(p.x / widthPx[0])) }
    ) {
        val wPx = with(density) { maxWidth.toPx() }
        val laneHPx = with(density) { (LANE_H + LANE_GAP).toPx() }
        val padPx = with(density) { 20.dp.toPx() }
        val maxChipPx = with(density) { 168.dp.toPx() }
        // Where each item sits and how wide its chip is, then its lane.
        val placed = remember(track, wPx) {
            val spans = track.items.map { item ->
                val x = track.fraction(item.start) * wPx
                val text = measurer.measure(item.title, chipStyle, maxLines = 1).size.width + padPx
                val barEnd = item.end?.let { track.fraction(it) * wPx } ?: x
                val w = maxOf(text, barEnd - x).coerceAtMost(maxChipPx).coerceAtMost(wPx)
                val start = minOf(x - if (item.isEvent) 0f else with(density) { 6.dp.toPx() }, wPx - w).coerceAtLeast(0f)
                Triple(item, start, w)
            }
            val laneOf = assignLanes(spans.map { it.second to it.second + it.third }, lanes, with(density) { 4.dp.toPx() })
            spans.mapIndexed { i, s -> Placed(s.first, s.second, s.third, laneOf[i]) }
        }
        val hourTicks = remember(track) { track.hours() }

        // The line, the busy stretches, the ticks, the points, Now and the drop mark.
        Box(Modifier.matchParentSizeCompat().drawBehind {
            val y = lineY.toPx()
            val sw = 2.dp.toPx()
            drawLine(wire, Offset(0f, y), Offset(size.width, y), sw, cap = StrokeCap.Round)
            // The first hour glows from Now into the line.
            drawLine(
                Brush.horizontalGradient(listOf(accent, accent.copy(alpha = 0f)), startX = 0f, endX = (size.width / track.minutes * 60f).coerceAtMost(size.width)),
                Offset(0f, y), Offset((size.width / track.minutes * 60f).coerceAtMost(size.width), y), sw, cap = StrokeCap.Round
            )
            track.items.filter { it.isEvent }.forEach { e ->
                val x0 = track.fraction(e.start) * size.width
                val x1 = track.fraction(e.end ?: e.start) * size.width
                if (x1 > x0) drawLine(skyLine, Offset(x0, y), Offset(x1, y), 4.dp.toPx(), cap = StrokeCap.Round)
            }
            hourTicks.forEach { t ->
                val x = track.fraction(t) * size.width
                drawLine(wire, Offset(x, y - 4.dp.toPx()), Offset(x, y + 4.dp.toPx()), 1.dp.toPx())
            }
            // Now: a filled point and a ring that leaves it once a minute.
            val p = pulse.value
            if (p < 1f) drawCircle(accent.copy(alpha = 0.45f * (1f - p)), radius = 5.dp.toPx() + 14.dp.toPx() * p, center = Offset(5.dp.toPx(), y))
            drawCircle(accent, radius = 5.dp.toPx(), center = Offset(5.dp.toPx(), y))
            if (hovered != null) {
                val x = track.fraction(hovered) * size.width
                drawLine(accent, Offset(x, 0f), Offset(x, y), 1.5.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(4.dp.toPx(), 4.dp.toPx())))
                drawCircle(accent, radius = 7.dp.toPx(), center = Offset(x, y))
            } else if (dragging) {
                drawRoundRect(
                    accent.copy(alpha = 0.10f), topLeft = Offset(0f, 0f), size = Size(size.width, y + 12.dp.toPx()),
                    cornerRadius = CornerRadius(12.dp.toPx())
                )
            }
        })

        // Points on the line for each reminder (also those without room for a chip).
        placed.filter { !it.item.isEvent }.forEach { pl ->
            val x = track.fraction(pl.item.start) * wPx
            val hue = pl.item.task?.let { reminderHue(it) } ?: h.amber
            Box(
                Modifier.offset { IntOffset((x - 6.dp.toPx()).roundToInt(), (lineY - 6.dp).roundToPx()) }
                    .size(12.dp).clip(CircleShape).background(MaterialTheme.colorScheme.surfaceContainerLow)
                    .drawBehind { drawCircle(hue, radius = size.minDimension / 2 - 1.dp.toPx(), style = Stroke(2.5.dp.toPx())) }
            )
        }

        // Hour labels under the line; midnight says the day.
        hourTicks.forEach { t ->
            val x = track.fraction(t) * wPx
            val text = hourLabel(t)
            val tw = measurer.measure(text, labelStyle).size.width
            if (x - tw / 2f > with(density) { 14.dp.toPx() } && x + tw / 2f <= wPx) Text(
                text,
                style = labelStyle,
                color = if (t.hour == 0) c.accent else c.mute,
                modifier = Modifier.offset { IntOffset((x - tw / 2f).roundToInt(), (lineY + 8.dp).roundToPx()) }
            )
        }

        // The chips, lane by lane above the line.
        placed.forEachIndexed { i, pl ->
            val lane = pl.lane ?: return@forEachIndexed
            val y = with(density) { (lineY - 10.dp).toPx() } - laneHPx * (lane + 1) + with(density) { LANE_GAP.toPx() }
            Chip(
                item = pl.item,
                style = chipStyle,
                modifier = Modifier
                    .offset { IntOffset(pl.x.roundToInt(), y.roundToInt()) }
                    .width(with(density) { pl.w.toDp() })
                    .arrive("day", i),
                onOpen = onOpen,
                onMove = onMove
            )
        }

        // A clear stretch says so.
        if (placed.isEmpty() && hovered == null) Text(
            if (dragging) "Drop on a time" else "Nothing timed until ${track.end.format(HM)}",
            style = MaterialTheme.typography.bodyMedium,
            color = c.mute,
            modifier = Modifier.offset { IntOffset(18.dp.roundToPx(), (lineY - 34.dp).roundToPx()) }
        )

        // The time a drop sets, over the mark.
        if (hovered != null) {
            val x = track.fraction(hovered) * wPx
            val text = hovered.format(HM)
            val tw = measurer.measure(text, MaterialTheme.typography.labelLarge).size.width + with(density) { 16.dp.toPx() }
            Text(
                text,
                style = MaterialTheme.typography.labelLarge.tabular(),
                color = c.onAccentFilled,
                modifier = Modifier
                    .offset { IntOffset((x - tw / 2f).coerceIn(0f, wPx - tw).roundToInt(), 0) }
                    .clip(RoundedCornerShape(10.dp)).background(c.accentFilled)
                    .padding(horizontal = 8.dp, vertical = 3.dp)
            )
        }
    }
}

private class Placed(val item: TrackItem, val x: Float, val w: Float, val lane: Int?)

/** An event (a sky bar with a start bar) or a reminder (its colour's dot, the title; opens and drags). */
@Composable
private fun Chip(item: TrackItem, style: TextStyle, modifier: Modifier, onOpen: (String) -> Unit, onMove: (TrackItem, DaySlot) -> Unit) {
    val c = circuitColors
    val h = hues
    if (item.isEvent) {
        Text(
            item.title,
            style = style.copy(textDirection = TextDirection.Content),
            color = c.ink,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = modifier.height(LANE_H).clip(RoundedCornerShape(8.dp)).background(h.tint(h.sky))
                .drawBehind { drawRect(h.sky, size = Size(3.dp.toPx(), size.height)) }
                .padding(start = 9.dp, end = 6.dp, top = 4.dp)
        )
    } else {
        val task = item.task ?: return
        val hue = reminderHue(task)
        Row(
            modifier.height(LANE_H).clip(RoundedCornerShape(12.dp))
                .background(MaterialTheme.colorScheme.surfaceContainerHighest)
                .clickable { onOpen(task.id) }
                .dragToDay(task.title) { slot -> onMove(item, slot) }
                .semantics { contentDescription = "${task.title}, ${item.start.format(HM)}" }
                .padding(start = 6.dp, end = 8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(Modifier.size(7.dp).clip(CircleShape).background(hue))
            Spacer(Modifier.width(5.dp))
            Text(
                task.title,
                style = style.copy(textDirection = TextDirection.Content, fontWeight = FontWeight.Medium),
                color = c.ink,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
    }
}

/** On waking / Home: a drop target with its glyph and how many reminders wait for it. */
@Composable
private fun MomentTarget(slot: DaySlot, icon: ImageVector, label: String, count: Int, hovered: Boolean, dragging: Boolean, compact: Boolean) {
    val c = circuitColors
    val scale by animateFloatAsState(if (hovered) 1.06f else 1f, Motion.base(), label = "moment")
    val bg = when {
        hovered -> c.accentTonal
        else -> MaterialTheme.colorScheme.surfaceContainerHigh
    }
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 44.dp)
            .graphicsLayer { scaleX = scale; scaleY = scale }
            .clip(RoundedCornerShape(14.dp))
            .background(bg)
            .then(if (dragging && !hovered) Modifier.drawBehind {
                drawRoundRect(
                    c.accent.copy(alpha = 0.7f), cornerRadius = CornerRadius(14.dp.toPx()),
                    style = Stroke(1.5.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(5.dp.toPx(), 4.dp.toPx())))
                )
            } else Modifier)
            .dayDropTarget(slot)
            .semantics { contentDescription = "$label, $count" }
            .padding(horizontal = if (compact) 6.dp else 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = if (compact) Arrangement.Center else Arrangement.Start
    ) {
        Icon(icon, contentDescription = null, tint = if (hovered) c.onAccentTonal else hues.amber, modifier = Modifier.size(18.dp))
        if (!compact) {
            Spacer(Modifier.width(8.dp))
            Text(label, style = MaterialTheme.typography.labelLarge, color = if (hovered) c.onAccentTonal else c.ink, maxLines = 1, modifier = Modifier.weight(1f))
        }
        if (count > 0) Text(
            count.toString(),
            style = MaterialTheme.typography.labelMedium.tabular(),
            color = if (hovered) c.onAccentTonal else c.inkSoft,
            modifier = Modifier.padding(start = 4.dp)
        )
    }
}

/** Fills the parent Box (BoxWithConstraints has no matchParentSize in its receiver's scope here). */
private fun Modifier.matchParentSizeCompat(): Modifier = this.fillMaxWidth().fillMaxHeight()
