package io.github.ronganam.dana.ui.screens.watching

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.outlined.AccountBalanceWallet
import androidx.compose.material.icons.outlined.Call
import androidx.compose.material.icons.outlined.DirectionsCar
import androidx.compose.material.icons.outlined.Email
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material.icons.outlined.WbSunny
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.watches.Circuit
import io.github.ronganam.dana.data.watches.SourceKind
import io.github.ronganam.dana.data.watches.TodayCounts
import io.github.ronganam.dana.data.watches.Watch
import io.github.ronganam.dana.data.watches.WatchRun
import io.github.ronganam.dana.ui.components.circuit.CircuitColumns
import io.github.ronganam.dana.ui.components.circuit.LineNode
import io.github.ronganam.dana.ui.components.circuit.NodeLine
import io.github.ronganam.dana.ui.components.circuit.NodeShape
import io.github.ronganam.dana.ui.components.circuit.NodeSize
import io.github.ronganam.dana.ui.components.circuit.NodeState
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.tabular

/** The glyph in a watch's source node. */
fun SourceKind.icon(): ImageVector = when (this) {
    SourceKind.Message -> Icons.AutoMirrored.Outlined.Chat
    SourceKind.Email -> Icons.Outlined.Email
    SourceKind.Notification -> Icons.Outlined.Notifications
    SourceKind.Place -> Icons.Outlined.Place
    SourceKind.Car -> Icons.Outlined.DirectionsCar
    SourceKind.Money -> Icons.Outlined.AccountBalanceWallet
    SourceKind.Time -> Icons.Outlined.Schedule
    SourceKind.Task -> Icons.Outlined.TaskAlt
    SourceKind.Call -> Icons.Outlined.Call
    SourceKind.Wake -> Icons.Outlined.WbSunny
}

/** The circuit columns of the list's rows: [CircuitColumns.Default], narrowed in a narrow pane ([fitColumns]). */
val LocalCircuitColumns = androidx.compose.runtime.compositionLocalOf { CircuitColumns.Default }

/**
 * The list's circuit columns for a list [width] wide: the default ones, or
 * squeezed to the row when its state word (about 88dp) no longer fits beside
 * the circuit (the side pane next to an open watch); the word then shows on
 * the title line ([narrowRow]).
 */
fun fitColumns(width: Dp): CircuitColumns {
    val d = CircuitColumns.Default
    if (width - 64.dp - 88.dp >= d.width) return d
    // Narrow: the state word moves up to the title line, the circuit takes the row.
    val f = ((width - 64.dp) / d.width).coerceIn(0.5f, 1f)
    return CircuitColumns(source = d.source, rule = d.source + (d.rule - d.source) * f, ai = d.source + (d.ai - d.source) * f,
        action = d.source + (d.action - d.source) * f, width = d.source + (d.width - d.source) * f)
}

/** Rows on squeezed columns put their state word on the title line. */
@Composable
fun narrowRow(): Boolean = LocalCircuitColumns.current !== CircuitColumns.Default

/** A watch's four nodes, in states. */
@Immutable
data class CircuitNodes(val source: LineNode, val rule: LineNode, val ai: LineNode?, val action: LineNode) {
    val list: List<LineNode> get() = listOfNotNull(source, rule, ai, action)
}

/** Where a watch is right now, for its line. */
enum class WatchLive { Off, Closed, Idle, FiredToday, Pulse, Running, Waiting }

/** How a watch's line is drawn now. */
fun watchLive(w: Watch, run: WatchRun?, firedToday: Boolean, pulsing: Boolean): WatchLive = when {
    run?.waiting == true -> WatchLive.Waiting
    run != null -> WatchLive.Running
    w.isPaused -> WatchLive.Off
    w.isClosed -> WatchLive.Closed
    pulsing -> WatchLive.Pulse
    firedToday -> WatchLive.FiredToday
    else -> WatchLive.Idle
}

/**
 * The nodes of [circuit] for [live]: a waiting run fills the action warm
 * (what passed warm too), a running one fills it accent, a step of the
 * engine just now sends a pulse into the rule, a fire today leaves the action
 * tonal, a paused watch is dashed. [labels]: the words under each node.
 */
fun circuitNodes(circuit: Circuit, live: WatchLive, labels: Boolean = false): CircuitNodes {
    val s: NodeState
    val r: NodeState
    val a: NodeState
    val t: NodeState
    when (live) {
        WatchLive.Waiting -> { s = NodeState.Passed; r = NodeState.Passed; a = NodeState.Passed; t = NodeState.Waiting }
        WatchLive.Running -> { s = NodeState.Passed; r = NodeState.Passed; a = NodeState.Passed; t = NodeState.Firing }
        WatchLive.Pulse -> { s = NodeState.Passed; r = NodeState.Firing; a = NodeState.Idle; t = NodeState.Idle }
        WatchLive.FiredToday -> { s = NodeState.Idle; r = NodeState.Idle; a = NodeState.Idle; t = NodeState.Done }
        WatchLive.Off -> { s = NodeState.Paused; r = NodeState.Paused; a = NodeState.Paused; t = NodeState.Paused }
        WatchLive.Closed -> { s = NodeState.Dropped; r = NodeState.Dropped; a = NodeState.Dropped; t = NodeState.Dropped }
        WatchLive.Idle -> { s = NodeState.Idle; r = NodeState.Idle; a = NodeState.Idle; t = NodeState.Idle }
    }
    return CircuitNodes(
        source = LineNode(NodeShape.Circle, s, label = circuit.source.takeIf { labels }, icon = circuit.sourceKind.icon(), description = "When: ${circuit.source}"),
        rule = LineNode(NodeShape.Slot, r, label = circuit.rule.takeIf { labels }, description = "Rule: ${circuit.rule}"),
        ai = circuit.ai?.let { LineNode(NodeShape.Diamond, a, label = it.takeIf { labels }, description = "AI check: $it") },
        action = LineNode(NodeShape.RoundedSquare, t, label = circuit.action.takeIf { labels }, description = "Then: ${circuit.action}"),
    )
}

/** The trailing state word of a row and its tone. */
data class StateWord(val text: String, val color: Color)

@Composable
fun stateWord(live: WatchLive, quiet: String?): StateWord {
    val c = circuitColors
    return when (live) {
        WatchLive.Waiting -> StateWord("Needs you", c.warm)
        WatchLive.Running -> StateWord("Running", c.accent)
        WatchLive.Off -> StateWord("Paused", c.mute)
        WatchLive.Closed -> StateWord("Ended", c.mute)
        else -> StateWord(quiet ?: "On", c.mute)
    }
}

/**
 * One watch's circuit drawn large, across the width it is given: today's
 * counts over the wires (events in → past the rule → fired), the nodes, then
 * each column's name and words, each column as wide as its share of the row
 * so long words stay whole at large font sizes.
 */
@Composable
fun LargeCircuit(circuit: Circuit, nodes: CircuitNodes, counts: TodayCounts?, modifier: Modifier = Modifier) {
    val c = circuitColors
    BoxWithConstraints(modifier.widthIn(max = 480.dp).fillMaxWidth()) {
        val n = if (circuit.ai != null) 4 else 3
        val step = maxWidth / n
        val xs = List(n) { step * it + step / 2 }
        val nums: List<String?> = if (counts == null) xs.map { null } else if (circuit.ai != null)
            listOf(null, counts.checks.toString(), counts.passedRule.toString(), counts.fired.toString())
        else listOf(null, counts.checks.toString(), counts.fired.toString())
        // Each count sits over the wire into its node.
        val numXs = xs.mapIndexed { i, x -> if (i == 0) x else (xs[i - 1] + x) / 2 }
        val room = step - 4.dp
        Column(Modifier.fillMaxWidth()) {
            PlacedTexts(numXs, nums, MaterialTheme.typography.titleMedium.tabular(), c.ink, room = room)
            NodeLine(nodes.list, size = NodeSize.Large, positions = xs, modifier = Modifier.padding(top = 4.dp))
            PlacedTexts(xs, listOfNotNull("When", "Rule", if (circuit.ai != null) "AI check" else null, "Then"), MaterialTheme.typography.bodyMedium, c.mute, room = room, modifier = Modifier.padding(top = 8.dp))
            PlacedTexts(xs, listOfNotNull(circuit.source, circuit.rule, circuit.ai, circuit.action), MaterialTheme.typography.bodyMedium, c.ink, room = room, maxLines = 4, modifier = Modifier.padding(top = 2.dp))
        }
    }
}

/** Texts each centred on its x (from the start), [room] wide at most. */
@Composable
fun PlacedTexts(
    xs: List<Dp>,
    texts: List<String?>,
    style: TextStyle,
    color: Color,
    room: Dp,
    modifier: Modifier = Modifier,
    maxLines: Int = 1,
) {
    Layout(
        content = {
            texts.forEach { t ->
                Box { if (t != null) Text(t, style = style, color = color, textAlign = TextAlign.Center, maxLines = maxLines, overflow = TextOverflow.Ellipsis) }
            }
        },
        modifier = modifier.fillMaxWidth()
    ) { ms, constraints ->
        val roomPx = room.roundToPx()
        val ps = ms.map { it.measure(Constraints(maxWidth = roomPx)) }
        val h = ps.maxOfOrNull { it.height } ?: 0
        layout(constraints.maxWidth, h) {
            ps.forEachIndexed { i, p ->
                val x = (xs.getOrNull(i) ?: 0.dp).roundToPx() - p.width / 2
                p.placeRelative(x.coerceIn(0, (constraints.maxWidth - p.width).coerceAtLeast(0)), 0)
            }
        }
    }
}
