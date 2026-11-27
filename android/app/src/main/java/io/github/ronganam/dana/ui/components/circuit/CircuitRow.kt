package io.github.ronganam.dana.ui.components.circuit

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.takeOrElse
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.tabular

/**
 * The shared columns of every watch's circuit, from the start of a row's
 * content: When (source) · Rule · AI check · Then (action), so circuits in
 * any group line up and [CircuitColumnHeader] can name them. A circuit
 * without an AI check leaves its column empty; the action stays under Then.
 */
@Immutable
class CircuitColumns(
    val source: Dp = 12.dp,
    val rule: Dp = 84.dp,
    val ai: Dp = 156.dp,
    val action: Dp = 228.dp,
    /** The circuit's width (just past the action node). */
    val width: Dp = 248.dp
) {
    companion object {
        val Default = CircuitColumns()
        /** One watch, drawn large ([NodeSize.Large]). */
        val Large = CircuitColumns(source = 24.dp, rule = 112.dp, ai = 200.dp, action = 288.dp, width = 312.dp)
    }
}

/**
 * The column names over a list of [CircuitRow]s ("When · Rule · AI check ·
 * Then", and the trailing "Last fired"), at the same x as the nodes.
 * [startInset] / [endInset]: from the header's edges to a row's content
 * (screen margin + the group's padding).
 */
@Composable
fun CircuitColumnHeader(
    modifier: Modifier = Modifier,
    columns: CircuitColumns = CircuitColumns.Default,
    labels: List<String> = listOf("When", "Rule", "AI check", "Then"),
    trailing: String? = "Last fired",
    startInset: Dp = 32.dp,
    endInset: Dp = 32.dp
) {
    val style = MaterialTheme.typography.labelMedium
    val color = circuitColors.mute
    val xs = listOf(columns.source, columns.rule, columns.ai, columns.action)
    Layout(
        content = {
            labels.forEach { Text(it, style = style, color = color, maxLines = 1) }
            if (trailing != null) Text(trailing, style = style, color = color, maxLines = 1)
        },
        modifier = modifier.fillMaxWidth()
    ) { measurables, constraints ->
        val loose = Constraints(maxWidth = constraints.maxWidth)
        val ps = measurables.map { it.measure(loose) }
        val h = ps.maxOfOrNull { it.height } ?: 0
        val start = startInset.roundToPx()
        layout(constraints.maxWidth, h) {
            ps.take(labels.size).forEachIndexed { i, p ->
                val x = start + (xs.getOrNull(i) ?: 0.dp).roundToPx()
                p.placeRelative(x - p.width / 2, 0)
            }
            if (trailing != null) ps.last().let { p -> p.placeRelative(constraints.maxWidth - endInset.roundToPx() - p.width, 0) }
        }
    }
}

/**
 * One watch as a row: its name and trailing time on top, its circuit
 * (source → rule → AI check → action) on the shared [columns] under it, and
 * its state at the end ("Needs you" warm, "Running" accent, "On" / "Paused"
 * muted unless [stateColor] says otherwise). [dimmed]: a paused watch, its
 * name and time muted.
 */
@Composable
fun CircuitRow(
    title: String,
    source: LineNode,
    rule: LineNode,
    action: LineNode,
    modifier: Modifier = Modifier,
    ai: LineNode? = null,
    time: String? = null,
    state: String? = null,
    stateColor: Color = Color.Unspecified,
    dimmed: Boolean = false,
    columns: CircuitColumns = CircuitColumns.Default,
    size: NodeSize = NodeSize.Small,
    contentPadding: PaddingValues = PaddingValues(horizontal = 16.dp, vertical = 10.dp),
    onClick: (() -> Unit)? = null
) {
    val c = circuitColors
    val nodes = listOfNotNull(source, rule, ai, action)
    val positions = listOfNotNull(columns.source, columns.rule, columns.ai.takeIf { ai != null }, columns.action)
    val live = action.state == NodeState.Waiting || action.state == NodeState.Firing
    val stateTint = stateColor.takeOrElse {
        when (action.state) {
            NodeState.Waiting -> c.warm
            NodeState.Firing -> c.accent
            else -> c.mute
        }
    }
    Column(
        modifier
            .fillMaxWidth()
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(contentPadding)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                title,
                style = MaterialTheme.typography.titleMedium.copy(fontWeight = if (dimmed) FontWeight.Normal else FontWeight.Medium),
                color = if (dimmed) c.mute else c.ink,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f)
            )
            if (time != null) {
                Spacer(Modifier.width(12.dp))
                Text(time, style = MaterialTheme.typography.bodyMedium.tabular(), color = if (dimmed) c.mute else c.inkSoft, maxLines = 1)
            }
        }
        Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.width(columns.width)) {
                NodeLine(nodes, size = size, positions = positions, labelStyle = MaterialTheme.typography.bodySmall)
            }
            Spacer(Modifier.weight(1f))
            if (state != null) Text(
                state,
                style = MaterialTheme.typography.bodyMedium.copy(fontWeight = if (live) FontWeight.SemiBold else FontWeight.Normal),
                color = stateTint,
                maxLines = 1
            )
        }
    }
}
