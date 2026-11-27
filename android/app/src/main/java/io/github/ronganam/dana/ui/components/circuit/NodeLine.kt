package io.github.ronganam.dana.ui.components.circuit

import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.layout.Box
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.State
import androidx.compose.runtime.key
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.rememberVectorPainter
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.layout.layoutId
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.theme.CircuitColors
import io.github.ronganam.dana.ui.theme.circuitColors
import kotlin.math.roundToInt

/** One node of a [NodeLine]. */
@Immutable
data class LineNode(
    val shape: NodeShape,
    val state: NodeState = NodeState.Idle,
    /** Under the node (horizontal) or beside it (vertical); null for none. */
    val label: String? = null,
    /** A glyph inside a circle or a rounded square (a source's kind, a done check). */
    val icon: ImageVector? = null,
    /** Read to a screen reader in place of [label]. */
    val description: String? = null
)

/** How a segment between two nodes is drawn; [Auto] follows the nodes' states. */
enum class SegmentStyle { Auto, Solid, Dashed, Hidden }

/** One segment, resolved: its colour, whether it is dashed, whether its dashes march. */
@Immutable
internal class SegmentPaint(val color: Color, val dashed: Boolean, val marching: Boolean = false, val hidden: Boolean = false)

private fun reached(s: NodeState) = s == NodeState.Passed || s == NodeState.Firing || s == NodeState.Done || s == NodeState.Waiting

/**
 * The segment from [a] to [b]: warm and marching into a waiting node, solid
 * accent between nodes something went through, dashed after the live node
 * (what comes next) and around a paused one, a plain wire otherwise.
 */
internal fun segmentPaint(a: NodeState, b: NodeState, c: CircuitColors, warmLine: Boolean, style: SegmentStyle): SegmentPaint {
    val auto = when {
        b == NodeState.Waiting -> SegmentPaint(c.warm, dashed = true, marching = true)
        a == NodeState.Paused || b == NodeState.Paused -> SegmentPaint(c.wireDim, dashed = true)
        a == NodeState.Dropped -> SegmentPaint(c.wireDim, dashed = false)
        reached(a) && reached(b) -> SegmentPaint(if (warmLine) c.warm else c.accent, dashed = false)
        a == NodeState.Firing -> SegmentPaint(c.wire, dashed = true)
        else -> SegmentPaint(c.wire, dashed = false)
    }
    return when (style) {
        SegmentStyle.Auto -> auto
        SegmentStyle.Solid -> SegmentPaint(auto.color, dashed = false)
        SegmentStyle.Dashed -> SegmentPaint(auto.color, dashed = true, marching = auto.marching)
        SegmentStyle.Hidden -> SegmentPaint(auto.color, dashed = false, hidden = true)
    }
}

/** The segment a running line's pulse travels: into its first live node. */
internal fun autoPulseSegment(nodes: List<LineNode>): Int? =
    nodes.indexOfFirst { it.state == NodeState.Firing }.takeIf { it > 0 }?.minus(1)

/**
 * A line of nodes with labels: a watch's circuit, a run's steps laid out
 * large, a reminder's moments. Horizontal by default (labels under the
 * nodes); vertical puts each label beside its node.
 *
 * - [positions]: each node's centre from the start edge (horizontal: x,
 *   vertical: y); by default spread evenly over the width (horizontal) or
 *   [rowGap] apart (vertical). Mirrored in RTL.
 * - [segments]: per segment (nodes − 1), overriding [SegmentStyle.Auto].
 * - [pulseSegment]: the segment a pulse travels (default: into the first
 *   [NodeState.Firing] node); null for none. Off under reduced motion.
 * - A line holding a [NodeState.Waiting] node draws what passed in warm.
 */
@Composable
fun NodeLine(
    nodes: List<LineNode>,
    modifier: Modifier = Modifier,
    orientation: Orientation = Orientation.Horizontal,
    size: NodeSize = NodeSize.Small,
    positions: List<Dp>? = null,
    segments: List<SegmentStyle>? = null,
    pulseSegment: Int? = autoPulseSegment(nodes),
    rowGap: Dp = 16.dp,
    labelStyle: TextStyle = MaterialTheme.typography.bodyMedium,
    surface: Color = nodeSurface()
) {
    val c = circuitColors
    val warmLine = nodes.any { it.state == NodeState.Waiting }
    val paints = nodes.map { nodePaint(it.state, c, surface, warmLine) }
    val segPaints = (0 until (nodes.size - 1).coerceAtLeast(0)).map { i ->
        segmentPaint(nodes[i].state, nodes[i + 1].state, c, warmLine, segments?.getOrNull(i) ?: SegmentStyle.Auto)
    }
    val glyphs: List<Painter?> = nodes.mapIndexed { i, n -> key(i) { n.icon?.let { rememberVectorPainter(it) } } }
    val breath = rememberBreath(nodes.any { it.state == NodeState.Waiting })
    val marching = rememberPulse(segPaints.any { it.marching }, millis = 2400)
    val pulse = rememberPulse(pulseSegment != null && pulseSegment in segPaints.indices)
    LineLayout(
        count = nodes.size,
        labels = nodes.map { it.label },
        emphasized = nodes.map { it.state == NodeState.Firing || it.state == NodeState.Waiting },
        orientation = orientation,
        size = size,
        positions = positions,
        rowGap = rowGap,
        labelStyle = labelStyle,
        modifier = modifier.semantics {
            contentDescription = nodes.mapNotNull { it.description ?: it.label }.joinToString(", ")
        }
    ) { centers ->
        drawSegments(centers, orientation, segPaints, { i -> nodes[i].shape }, size, marching, pulse, pulseSegment, c.accent)
        centers.forEachIndexed { i, o -> drawNode(nodes[i].shape, o, size, paints[i], glyphs[i], breath?.value) }
    }
}

/**
 * The wires between [centers], each stopping at its nodes' edges, then the
 * travelling pulse on [pulseSegment].
 */
internal fun DrawScope.drawSegments(
    centers: List<Offset>,
    orientation: Orientation,
    paints: List<SegmentPaint>,
    shapeOf: (Int) -> NodeShape,
    size: NodeSize,
    marching: State<Float>?,
    pulse: State<Float>?,
    pulseSegment: Int?,
    pulseColor: Color
) {
    val horizontal = orientation == Orientation.Horizontal
    val sw = size.stroke.toPx()
    for (i in 0 until centers.size - 1) {
        val p = paints.getOrNull(i) ?: continue
        if (p.hidden) continue
        val (start, end) = segmentEnds(centers[i], centers[i + 1], shapeOf(i), shapeOf(i + 1), size, horizontal)
        val effect = when {
            p.marching -> PathEffect.dashPathEffect(floatArrayOf(2.dp.toPx(), 5.dp.toPx()), -(marching?.value ?: 0f) * 7.dp.toPx() * 2f)
            p.dashed -> PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 4.dp.toPx()))
            else -> null
        }
        drawLine(p.color, start, end, strokeWidth = if (p.marching) sw + 0.5.dp.toPx() else sw, cap = if (p.marching) StrokeCap.Round else StrokeCap.Butt, pathEffect = effect)
    }
    val seg = pulseSegment
    val v = pulse?.value
    if (seg != null && v != null && seg in 0 until centers.size - 1) {
        val (start, end) = segmentEnds(centers[seg], centers[seg + 1], shapeOf(seg), shapeOf(seg + 1), size, horizontal)
        val alpha = when {
            v < 0.15f -> v / 0.15f
            v > 0.8f -> (1f - v) / 0.2f
            else -> 1f
        }
        drawCircle(pulseColor.copy(alpha = alpha), radius = 2.5.dp.toPx(), center = start + (end - start) * v)
    }
}

private fun DrawScope.segmentEnds(a: Offset, b: Offset, sa: NodeShape, sb: NodeShape, size: NodeSize, horizontal: Boolean): Pair<Offset, Offset> {
    val forward = if (horizontal) (if (b.x >= a.x) 1f else -1f) else 1f
    return if (horizontal) {
        Offset(a.x + forward * size.halfWidth(sa).toPx(), a.y) to Offset(b.x - forward * size.halfWidth(sb).toPx(), b.y)
    } else {
        Offset(a.x, a.y + size.halfHeight(sa).toPx()) to Offset(b.x, b.y - size.halfHeight(sb).toPx())
    }
}

/**
 * Lays out a line's labels and hands [draw] the nodes' centres (mirrored in
 * RTL). Horizontal: the node band ([NodeSize.box] tall) on top, each label
 * centred under its node within its share of the width. Vertical: a node
 * column, each label beside its node, its first line centred on it.
 */
@Composable
internal fun LineLayout(
    count: Int,
    labels: List<String?>,
    emphasized: List<Boolean>,
    orientation: Orientation,
    size: NodeSize,
    positions: List<Dp>?,
    rowGap: Dp,
    labelStyle: TextStyle,
    modifier: Modifier = Modifier,
    labelGap: Dp = 6.dp,
    draw: DrawScope.(centers: List<Offset>) -> Unit
) {
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    // Written by the layout pass, read by the draw pass of the same frame.
    val centers = remember { CenterHolder() }
    val ink = MaterialTheme.colorScheme.onSurface
    val soft = MaterialTheme.colorScheme.onSurfaceVariant
    val horizontal = orientation == Orientation.Horizontal
    Layout(
        content = {
            for (i in 0 until count) {
                val label = labels.getOrNull(i)
                Box(Modifier.layoutId(i)) {
                    if (label != null) Text(
                        label,
                        style = if (emphasized.getOrNull(i) == true) labelStyle.copy(fontWeight = FontWeight.SemiBold) else labelStyle,
                        color = if (emphasized.getOrNull(i) == true) ink else soft,
                        textAlign = if (horizontal) TextAlign.Center else TextAlign.Start,
                        maxLines = if (horizontal) 2 else 3,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
        },
        modifier = modifier.drawBehind {
            val list = centers.points.map { if (rtl) Offset(this.size.width - it.x, it.y) else it }
            draw(list)
        }
    ) { measurables, constraints ->
        val box = size.box.roundToPx()
        val gap = labelGap.roundToPx()
        if (horizontal) {
            val given = positions?.map { it.roundToPx() }
            val width = when {
                constraints.hasBoundedWidth -> constraints.maxWidth
                given != null -> (given.maxOrNull() ?: 0) + box
                else -> box * count * 3
            }
            val xs = given ?: (0 until count).map { ((2 * it + 1) * width / (2f * count.coerceAtLeast(1))).roundToInt() }
            // Each label gets the room to its neighbours' midpoints.
            val placeables = measurables.map { m ->
                val i = m.layoutId as Int
                val left = if (i == 0) xs[i] * 2 else xs[i] - xs[i - 1]
                val right = if (i == count - 1) (width - xs[i]) * 2 else xs[i + 1] - xs[i]
                val room = minOf(left, right).coerceAtLeast(box)
                m.measure(Constraints(maxWidth = room))
            }
            val labelH = placeables.maxOfOrNull { it.height } ?: 0
            val height = box + if (labelH > 0) gap + labelH else 0
            centers.points = xs.map { Offset(it.toFloat(), box / 2f) }
            layout(width, height.coerceAtLeast(constraints.minHeight)) {
                placeables.forEachIndexed { i, p ->
                    if (p.width > 0) p.placeRelative(xs[i] - p.width / 2, box + gap)
                }
            }
        } else {
            val labelX = box + 12.dp.roundToPx()
            val width = if (constraints.hasBoundedWidth) constraints.maxWidth else labelX + 240.dp.roundToPx()
            val placeables = measurables.map { it.measure(Constraints(maxWidth = (width - labelX).coerceAtLeast(0))) }
            val line = 20.dp.roundToPx()
            val given = positions?.map { it.roundToPx() }
            val ys = ArrayList<Int>(count)
            var y = 0
            for (i in 0 until count) {
                val rowH = maxOf(box, placeables[i].height)
                val cy = given?.getOrNull(i) ?: (y + maxOf(box, line) / 2)
                ys += cy
                y = if (given != null) cy + rowH / 2 else y + rowH + rowGap.roundToPx()
            }
            val height = if (given != null) (ys.lastOrNull() ?: 0) + maxOf(box, placeables.lastOrNull()?.height ?: 0) / 2
            else (y - rowGap.roundToPx()).coerceAtLeast(0)
            centers.points = ys.map { Offset(box / 2f, it.toFloat()) }
            layout(width, height.coerceAtLeast(constraints.minHeight)) {
                placeables.forEachIndexed { i, p -> p.placeRelative(labelX, ys[i] - line / 2) }
            }
        }
    }
}

private class CenterHolder {
    var points: List<Offset> = emptyList()
}
