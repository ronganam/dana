package io.github.ronganam.dana.ui.screens.watching

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.watches.MarkKind
import io.github.ronganam.dana.data.watches.TraceRow
import io.github.ronganam.dana.data.watches.TrackMark
import io.github.ronganam.dana.data.watches.clockWords
import io.github.ronganam.dana.data.watches.trackMarks
import io.github.ronganam.dana.ui.components.circuit.CircuitColumns
import io.github.ronganam.dana.ui.components.circuit.NodeLine
import io.github.ronganam.dana.ui.components.circuit.NodeSize
import io.github.ronganam.dana.ui.theme.CircuitColors
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.tabular
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit

/** Where every track of the list starts (epoch ms): 06:00, or midnight when something came earlier. */
val LocalTrackStart = compositionLocalOf { 0L }

private val STATE_COLUMN = 96.dp
private val TRACK_GAP = 16.dp

/**
 * An unfolded watch row: its name and time, then today's track running into
 * its circuit (on the shared columns) and its state at the end.
 */
@Composable
fun TrackedRow(
    title: String,
    time: String?,
    nodes: CircuitNodes,
    state: StateWord,
    rows: List<TraceRow>,
    now: ZonedDateTime,
    dimmed: Boolean,
    heldSince: Long? = null,
    onClick: () -> Unit,
) {
    val c = circuitColors
    val cols = CircuitColumns.Default
    val marks = trackMarks(rows, heldSince)
    Column(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                title,
                style = MaterialTheme.typography.titleMedium.copy(fontWeight = if (dimmed) FontWeight.Normal else FontWeight.Medium),
                color = if (dimmed) c.mute else c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f)
            )
            if (time != null) Text(time, style = MaterialTheme.typography.bodyMedium.tabular(), color = if (dimmed) c.mute else c.inkSoft)
        }
        Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Track(marks, LocalTrackStart.current, now.toInstant().toEpochMilli(), dimmed, Modifier.weight(1f).height(NodeSize.Small.box))
            Spacer(Modifier.width(TRACK_GAP - cols.source + NodeSize.Small.circle / 2))
            Box(Modifier.width(cols.width - cols.source + NodeSize.Small.circle / 2)) {
                NodeLine(nodes.list, size = NodeSize.Small, positions = shifted(cols, nodes.ai != null))
            }
            Text(
                state.text,
                style = MaterialTheme.typography.bodyMedium.copy(fontWeight = if (state.color == c.warm || state.color == c.accent) FontWeight.SemiBold else FontWeight.Normal),
                color = state.color, textAlign = TextAlign.End, maxLines = 1,
                modifier = Modifier.width(STATE_COLUMN)
            )
        }
    }
}

/** The circuit's columns moved so the source node touches the track's end. */
private fun shifted(cols: CircuitColumns, ai: Boolean): List<Dp> {
    val start = NodeSize.Small.circle / 2
    val d = cols.source - start
    return listOfNotNull(start, cols.rule - d, (cols.ai - d).takeIf { ai }, cols.action - d)
}

/** Today's events as marks on a wire, from [startMs] to [nowMs]. */
@Composable
private fun Track(marks: List<TrackMark>, startMs: Long, nowMs: Long, dimmed: Boolean, modifier: Modifier = Modifier) {
    val c = circuitColors
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    val desc = if (marks.isEmpty()) "Nothing today" else "${marks.size} events today, ${marks.count { it.kind == MarkKind.Fired || it.kind == MarkKind.Held }} fired"
    Canvas(modifier.semantics { contentDescription = desc }) {
        val y = size.height / 2
        drawLine(if (dimmed) c.wireDim else c.wire, Offset(0f, y), Offset(size.width, y), strokeWidth = 1.5.dp.toPx())
        val span = (nowMs - startMs).coerceAtLeast(1)
        for (m in marks) {
            val f = ((m.atMs - startMs).toFloat() / span).coerceIn(0f, 1f)
            val x = (4.dp.toPx() + f * (size.width - 8.dp.toPx())).let { if (rtl) size.width - it else it }
            drawMark(m.kind, Offset(x, y), c, if (dimmed) 0.5f else 1f)
        }
    }
}

private fun DrawScope.drawMark(kind: MarkKind, at: Offset, c: CircuitColors, alpha: Float) {
    when (kind) {
        MarkKind.Dropped -> drawCircle(c.mute.copy(alpha = alpha), radius = 2.dp.toPx(), center = at)
        MarkKind.AiNo -> {
            val d = 4.dp.toPx()
            val p = Path().apply { moveTo(at.x, at.y - d); lineTo(at.x + d, at.y); lineTo(at.x, at.y + d); lineTo(at.x - d, at.y); close() }
            drawPath(p, c.inkSoft.copy(alpha = alpha), style = Stroke(1.2.dp.toPx()))
        }
        MarkKind.Fired, MarkKind.Held -> {
            val q = 7.dp.toPx()
            drawRoundRect((if (kind == MarkKind.Held) c.warm else c.accent).copy(alpha = alpha), Offset(at.x - q / 2, at.y - q / 2), Size(q, q), CornerRadius(1.5.dp.toPx()))
        }
    }
}

/** The unfolded list's header: the track's hours and "Now", then When · Rule · AI check · Then, then "Last fired". */
@Composable
fun TrackHeader(modifier: Modifier = Modifier) {
    val c = circuitColors
    val cols = CircuitColumns.Default
    val style = MaterialTheme.typography.labelMedium
    val start = LocalTrackStart.current
    val now = System.currentTimeMillis()
    Row(modifier.fillMaxWidth().padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
        BoxWithConstraints(Modifier.weight(1f)) {
            val w = maxWidth
            val hours = trackHours(start, now, maxLabels = (w / 56.dp).toInt() - 1)
            val span = (now - start).coerceAtLeast(1).toFloat()
            val xs = hours.map { 4.dp + (w - 8.dp) * ((it - start) / span) } + (w - 4.dp)
            val labels = hours.map { clockWords(it, ZoneId.systemDefault()) } + "Now"
            PlacedTexts(xs, labels, style, c.mute, room = 48.dp)
        }
        Spacer(Modifier.width(TRACK_GAP - cols.source + NodeSize.Small.circle / 2))
        Box(Modifier.width(cols.width - cols.source + NodeSize.Small.circle / 2)) {
            PlacedTexts(shifted(cols, true), listOf("When", "Rule", "AI check", "Then"), style, c.mute, room = 64.dp)
        }
        Text("Last fired", style = style, color = c.mute, textAlign = TextAlign.End, modifier = Modifier.width(STATE_COLUMN))
    }
}

/** Full hours between [start] and [now] (every 1, 2, 3, 4 or 6 h: at most [maxLabels]), leaving room before "Now". */
internal fun trackHours(start: Long, now: Long, maxLabels: Int): List<Long> {
    val zone = ZoneId.systemDefault()
    var t = Instant.ofEpochMilli(start).atZone(zone).truncatedTo(ChronoUnit.HOURS)
    if (t.toInstant().toEpochMilli() < start) t = t.plusHours(1)
    val out = mutableListOf<Long>()
    val hours = (now - start) / 3_600_000L
    val step = listOf(1L, 2L, 3L, 4L, 6L).firstOrNull { hours / it < maxLabels.coerceAtLeast(1) } ?: 12L
    if (maxLabels < 1) return emptyList()
    while (t.toInstant().toEpochMilli() < now - 45 * 60_000L) {
        out += t.toInstant().toEpochMilli()
        t = t.plusHours(step)
    }
    return out
}

/** What the track's marks mean. */
@Composable
fun TrackLegend(modifier: Modifier = Modifier) {
    val c = circuitColors
    Row(modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
        LegendItem("The rule dropped it") { drawMark(MarkKind.Dropped, Offset(size.width / 2, size.height / 2), c, 1f) }
        LegendItem("The AI said no") { drawMark(MarkKind.AiNo, Offset(size.width / 2, size.height / 2), c, 1f) }
        LegendItem("Fired") { drawMark(MarkKind.Fired, Offset(size.width / 2, size.height / 2), c, 1f) }
        LegendItem("Held for you") { drawMark(MarkKind.Held, Offset(size.width / 2, size.height / 2), c, 1f) }
    }
}

@Composable
private fun LegendItem(text: String, mark: DrawScope.() -> Unit) {
    Canvas(Modifier.size(12.dp), onDraw = mark)
    Text(text, style = MaterialTheme.typography.bodySmall, color = circuitColors.inkSoft, modifier = Modifier.padding(start = 6.dp, end = 20.dp))
}
