package io.github.ronganam.dana.ui.screens.now

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.WbSunny
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.DaySlotInfo
import io.github.ronganam.dana.ui.components.circuit.CircuitNode
import io.github.ronganam.dana.ui.components.circuit.GroupRow
import io.github.ronganam.dana.ui.components.circuit.GroupedSection
import io.github.ronganam.dana.ui.components.circuit.LineNode
import io.github.ronganam.dana.ui.components.circuit.LocalNodeSurface
import io.github.ronganam.dana.ui.components.circuit.NodeLine
import io.github.ronganam.dana.ui.components.circuit.NodeShape
import io.github.ronganam.dana.ui.components.circuit.NodeSize
import io.github.ronganam.dana.ui.components.circuit.NodeState
import io.github.ronganam.dana.ui.components.circuit.SectionHeader
import io.github.ronganam.dana.ui.components.circuit.SegmentStyle
import io.github.ronganam.dana.ui.components.circuit.StepLineMini
import io.github.ronganam.dana.ui.components.circuit.TimelineRow
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.tabular
import kotlinx.coroutines.delay
import java.time.LocalTime
import java.time.format.DateTimeFormatter

/*
 * Now's sections besides "Needs you": Going on (live activities), Next (the
 * rest of today as a timeline), Earlier today (reminders done), and the calm
 * card that stands in for "Needs you" when nothing does.
 */

/** "Going on": each running live activity as a row of one group: its node, what it is and does now, its steps / timer / time. */
@Composable
fun GoingOnSection(rows: List<NowLiveRow>, handlers: LiveHandlers, modifier: Modifier = Modifier) {
    val c = circuitColors
    val nowMs = rememberMinuteClock()
    GroupedSection(modifier, title = "Going on") {
        rows.forEach { r ->
            item(key = r.id) {
                GroupRow(
                    title = r.title,
                    subtitle = r.line.ifBlank { null },
                    leading = {
                        CircuitNode(NodeShape.Circle, NodeState.Idle, size = NodeSize.Leading, icon = r.glyph.vector(), strokeColor = c.accent, glyphColor = c.inkSoft)
                    },
                    trailing = { LiveTrailingView(r.trailing, nowMs) },
                    onClick = { handlers.open(r) }
                )
            }
        }
    }
}

@Composable
private fun LiveTrailingView(t: LiveTrailing, nowMs: Long) {
    val c = circuitColors
    val style = MaterialTheme.typography.bodyMedium.tabular()
    when (t) {
        is LiveTrailing.Steps -> Column(horizontalAlignment = Alignment.End) {
            // Steps done, as the server writes them in its line ("step 2/4").
            Text("${t.done}/${t.total}", style = style, color = c.inkSoft)
            StepLineMini(t.done, t.total, Modifier.padding(top = 2.dp), width = (14 * t.total).coerceIn(40, 72).dp)
        }
        is LiveTrailing.Since -> Text(NowLive.elapsed(t.sinceMs, nowMs), style = style, color = c.inkSoft)
        is LiveTrailing.At -> Text(NowLive.clock(t.toMs), style = MaterialTheme.typography.titleMedium.tabular(), color = c.ink)
        LiveTrailing.None -> Unit
    }
}

/** The wall clock in ms, ticking once a minute (for "14 min"). */
@Composable
private fun rememberMinuteClock(): Long {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(60_000L - now % 60_000L)
            now = System.currentTimeMillis()
        }
    }
    return now
}

/** A place slot's short name in the time column ("Wake", "Home"); an addon's own label as it is. */
internal fun placeLabel(e: DayEntry): String = when (e.placeKey) {
    "wake" -> "Wake"
    "home" -> "Home"
    else -> e.place.orEmpty()
}

/**
 * "Next": what is left today as a timeline (time | node | title), timed
 * ones first, then the place moments (wake, home) with their glyph. A row
 * with a reminder behind it opens it. [calendarOk] false adds a note.
 */
@Composable
fun NextSection(slots: List<DaySlotInfo>, now: LocalTime, calendarOk: Boolean, onOpen: (DayEntry) -> Unit, modifier: Modifier = Modifier) {
    val c = circuitColors
    val upcoming = remember(slots, now.hour, now.minute) { NowDay.upcoming(slots, now) }
    Column(modifier.fillMaxWidth()) {
        SectionHeader("Next", meta = "Today")
        if (upcoming.isEmpty()) Text(
            "Nothing else today.",
            style = MaterialTheme.typography.bodyLarge,
            color = c.inkSoft,
            modifier = Modifier.padding(vertical = 4.dp)
        )
        upcoming.forEachIndexed { i, e ->
            val place = e.place != null
            val icon = when (e.placeKey) {
                "wake" -> Icons.Outlined.WbSunny
                "home" -> Icons.Outlined.Home
                else -> null
            }
            TimelineRow(
                time = if (place) placeLabel(e) else e.time,
                node = LineNode(NodeShape.Circle, NodeState.Idle, icon = icon),
                title = e.title,
                timeIsPlace = place,
                first = i == 0,
                last = i == upcoming.lastIndex,
                timeColumn = 52.dp,
                onClick = e.taskId?.let { { onOpen(e) } }
            )
        }
        if (!calendarOk) Text(
            "Your calendar couldn't be read.",
            style = MaterialTheme.typography.bodyMedium,
            color = c.inkSoft,
            modifier = Modifier.padding(top = 4.dp)
        )
    }
}

private val HM = DateTimeFormatter.ofPattern("HH:mm")

/** "Earlier today": the reminders done today, a done node each, the time at the end. */
@Composable
fun EarlierSection(done: List<DoneEntry>, onOpen: (DoneEntry) -> Unit, modifier: Modifier = Modifier) {
    val c = circuitColors
    GroupedSection(modifier, title = "Earlier today") {
        done.forEach { d ->
            item(key = d.taskId) {
                GroupRow(
                    title = d.title,
                    subtitle = "Done",
                    leading = { CircuitNode(NodeShape.RoundedSquare, NodeState.Done, size = NodeSize.Leading, icon = Icons.Outlined.Check) },
                    trailing = { Text(d.at.format(HM), style = MaterialTheme.typography.bodyMedium.tabular(), color = c.inkSoft) },
                    onClick = { onOpen(d) }
                )
            }
        }
    }
}

/**
 * Nothing needs you: a card that says so, one line from what the server
 * knows ([line]: its lede, else what Dana keeps quiet), and the day as a
 * line: reminders done today (squares), now, and what is still ahead
 * today (circles), by time. Only real points; no line when there are none.
 */
@Composable
fun CalmCard(line: String?, done: List<DoneEntry>, slots: List<DaySlotInfo>, now: LocalTime, modifier: Modifier = Modifier) {
    val c = circuitColors
    val surface = MaterialTheme.colorScheme.surfaceContainerLow
    val nowMin = now.hour * 60 + now.minute
    val ahead = remember(slots, now.hour, now.minute) {
        NowDay.upcoming(slots, now).mapNotNull { NowDay.minutesOf(it.time) }
    }
    val doneMin = done.map { it.at.hour * 60 + it.at.minute }
    val nodes = remember(doneMin, ahead, nowMin) { NowDay.dayLine(doneMin, ahead, nowMin) }
    Column(
        modifier.fillMaxWidth().padding(horizontal = NOW_GUTTER).clip(RoundedCornerShape(24.dp)).background(surface)
            .padding(start = 20.dp, end = 20.dp, top = 20.dp, bottom = 16.dp)
    ) {
        CompositionLocalProvider(LocalNodeSurface provides surface) {
            Row(verticalAlignment = Alignment.Top) {
                CircuitNode(NodeShape.RoundedSquare, NodeState.Done, size = NodeSize.Leading, icon = Icons.Outlined.Check, modifier = Modifier.padding(top = 2.dp))
                Spacer(Modifier.width(16.dp))
                Column(Modifier.weight(1f)) {
                    Text("Nothing needs you", style = MaterialTheme.typography.titleLarge, color = c.ink)
                    if (line != null) Text(
                        line,
                        style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
                        color = c.inkSoft,
                        modifier = Modifier.padding(top = 4.dp)
                    )
                }
            }
            if (nodes.isNotEmpty()) BoxWithConstraints(Modifier.fillMaxWidth().padding(top = 20.dp)) {
                val pad = 18.dp
                val span = maxWidth - pad * 2
                val nowIndex = nodes.indexOfFirst { it.kind == DayLineNode.Kind.NOW }
                NodeLine(
                    nodes = nodes.map { n ->
                        when (n.kind) {
                            DayLineNode.Kind.START -> LineNode(NodeShape.Circle, NodeState.Passed, n.label, description = "From ${n.label ?: ""}".trim())
                            DayLineNode.Kind.DONE -> LineNode(NodeShape.RoundedSquare, NodeState.Passed, n.label, description = "Done at ${n.label ?: ""}".trim())
                            DayLineNode.Kind.NOW -> LineNode(NodeShape.Circle, NodeState.Done, n.label, description = "Now")
                            DayLineNode.Kind.NEXT -> LineNode(NodeShape.Circle, NodeState.Idle, n.label, description = "Later today")
                        }
                    },
                    positions = nodes.map { pad + span * it.fraction },
                    segments = nodes.indices.drop(1).map { i -> if (i > nowIndex) SegmentStyle.Dashed else SegmentStyle.Auto },
                    pulseSegment = null,
                    size = NodeSize.Step,
                    labelStyle = MaterialTheme.typography.bodySmall.tabular(),
                    modifier = Modifier.fillMaxWidth().semantics { contentDescription = "Today: ${done.size} done, ${ahead.count { it >= nowMin }} still ahead" }
                )
            }
        }
    }
}

/** The calm card's line: the server's lede, else how many notifications Dana keeps quiet; null when neither. */
fun calmLine(lede: String?, noise: Int): String? =
    lede?.trim()?.takeIf { it.isNotEmpty() }
        ?: if (noise > 0) "Dana is keeping $noise notification${if (noise == 1) "" else "s"} quiet." else null

/** "Can wait": the quieter suggestions, one group; each row's primary at its end. */
@Composable
fun CanWaitSection(quiet: List<io.github.ronganam.dana.data.models.QuietEntry>, total: Int, act: (io.github.ronganam.dana.data.models.QuietEntry) -> Unit, modifier: Modifier = Modifier) {
    val c = circuitColors
    GroupedSection(modifier, title = "Can wait", meta = total.toString()) {
        quiet.forEach { q ->
            item(key = q.id) {
                GroupRow(
                    title = q.title,
                    subtitle = q.source.ifBlank { null },
                    trailing = q.primary?.let { p -> { TextAction(p.label, c.accent) { act(q) } } }
                )
            }
        }
    }
}
