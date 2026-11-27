package io.github.ronganam.dana.ui.components.circuit

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.State
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.graphics.takeOrElse
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.rememberVectorPainter
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.theme.CircuitColors
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.reducedMotion

/*
 * Circuit's node: the one shape vocabulary every line uses (a watch's circuit,
 * a run's steps, Now's timeline, a reminder's moments, a conversation's tool
 * steps). The shape says what a node is; the state says what happened there.
 */

/** What a node is. The same everywhere. */
enum class NodeShape {
    /** A source or a moment. */
    Circle,
    /** A rule. */
    Slot,
    /** An AI check. */
    Diamond,
    /** An action, or done. */
    RoundedSquare
}

/** What happened at a node. */
enum class NodeState {
    /** Nothing yet (or nothing today). */
    Idle,
    /** Something went through it. */
    Passed,
    /** Something stopped here (a check said no): the line after it dims. */
    Dropped,
    /** Live now: filled accent, the line's pulse travels into it. */
    Firing,
    /** Waiting for the owner: filled warm, and it breathes. */
    Waiting,
    /** Finished: tonal accent. */
    Done,
    /** Turned off or asleep: a dashed outline. */
    Paused
}

/**
 * A node's geometry. [box] is the square every shape of this size fits in
 * (rows and columns align on it); [glyph] the icon size inside a circle or a
 * rounded square.
 */
@Immutable
class NodeSize(
    val box: Dp,
    val circle: Dp,
    val slotWidth: Dp,
    val slotHeight: Dp,
    /** Half the diamond's diagonal. */
    val diamond: Dp,
    val square: Dp,
    val squareRadius: Dp,
    val stroke: Dp,
    val glyph: Dp,
    /** How far a waiting node's breath grows past its shape. */
    val breath: Dp
) {
    companion object {
        /** A watch's circuit in a list. */
        val Small = NodeSize(box = 22.dp, circle = 22.dp, slotWidth = 6.dp, slotHeight = 16.dp, diamond = 8.dp, square = 16.dp, squareRadius = 4.dp, stroke = 1.5.dp, glyph = 12.dp, breath = 7.dp)
        /** One watch, drawn large. */
        val Large = NodeSize(box = 44.dp, circle = 44.dp, slotWidth = 10.dp, slotHeight = 32.dp, diamond = 15.dp, square = 30.dp, squareRadius = 8.dp, stroke = 2.dp, glyph = 22.dp, breath = 9.dp)
        /** A row's leading node (32dp, the same column as a row's icon). */
        val Leading = NodeSize(box = 32.dp, circle = 30.dp, slotWidth = 8.dp, slotHeight = 22.dp, diamond = 11.dp, square = 24.dp, squareRadius = 7.dp, stroke = 1.5.dp, glyph = 16.dp, breath = 5.dp)
        /** A timeline's node with a glyph (a place, a wake-up). */
        val Timeline = NodeSize(box = 24.dp, circle = 24.dp, slotWidth = 6.dp, slotHeight = 16.dp, diamond = 8.dp, square = 18.dp, squareRadius = 5.dp, stroke = 1.5.dp, glyph = 12.dp, breath = 4.dp)
        /** A timeline's plain node (a time). */
        val Dot = NodeSize(box = 24.dp, circle = 11.dp, slotWidth = 5.dp, slotHeight = 12.dp, diamond = 6.dp, square = 11.dp, squareRadius = 3.dp, stroke = 1.5.dp, glyph = 0.dp, breath = 4.dp)
        /** A run's step. */
        val Step = NodeSize(box = 20.dp, circle = 10.dp, slotWidth = 4.dp, slotHeight = 10.dp, diamond = 5.dp, square = 10.dp, squareRadius = 3.dp, stroke = 1.5.dp, glyph = 0.dp, breath = 4.dp)
    }

    /** How far a [shape] reaches from its centre along a horizontal line. */
    internal fun halfWidth(shape: NodeShape): Dp = when (shape) {
        NodeShape.Circle -> circle / 2
        NodeShape.Slot -> slotWidth / 2
        NodeShape.Diamond -> diamond
        NodeShape.RoundedSquare -> square / 2
    }

    /** How far a [shape] reaches from its centre along a vertical line. */
    internal fun halfHeight(shape: NodeShape): Dp = when (shape) {
        NodeShape.Circle -> circle / 2
        NodeShape.Slot -> slotHeight / 2
        NodeShape.Diamond -> diamond
        NodeShape.RoundedSquare -> square / 2
    }
}

/**
 * The surface nodes sit on: a node's fill punches the wire out behind it, so
 * it must match what is under it. [GroupedSection] sets it for its rows;
 * otherwise the canvas.
 */
val LocalNodeSurface = compositionLocalOf { Color.Unspecified }

@Composable
internal fun nodeSurface(): Color = LocalNodeSurface.current.takeOrElse { MaterialTheme.colorScheme.background }

/** How one node is drawn. */
@Immutable
internal class NodePaint(
    val stroke: Color,
    val fill: Color,
    val glyph: Color,
    val dashed: Boolean = false,
    /** Breathes in this colour (a waiting node). */
    val breath: Color? = null,
    /** A spinner arc around it (a run's current step). */
    val spinner: Color? = null
)

/** [state]'s colours. [warmLine]: the line holds a waiting node, so what passed is drawn warm too. */
internal fun nodePaint(state: NodeState, c: CircuitColors, surface: Color, warmLine: Boolean = false): NodePaint = when (state) {
    NodeState.Idle -> NodePaint(c.node, surface, c.inkSoft)
    NodeState.Passed -> if (warmLine) NodePaint(c.warm, surface, c.warm) else NodePaint(c.accent, surface, c.accent)
    NodeState.Dropped -> NodePaint(c.nodeDim, surface, c.nodeDim)
    NodeState.Firing -> NodePaint(c.accent, c.accent, c.onAccentFilled)
    NodeState.Waiting -> NodePaint(c.warm, c.warm, c.onWarm, breath = c.warm)
    NodeState.Done -> NodePaint(c.accent, c.accentTonal, c.onAccentTonal)
    NodeState.Paused -> NodePaint(c.nodeDim, surface, c.nodeDim, dashed = true)
}

/**
 * Draws one node centred on [center]. [breath] is the waiting node's breath
 * (0..1, null when still); [spin] the spinner's angle in degrees.
 */
internal fun DrawScope.drawNode(
    shape: NodeShape,
    center: Offset,
    size: NodeSize,
    paint: NodePaint,
    glyph: Painter? = null,
    breath: Float? = null,
    spin: Float? = null
) {
    val sw = size.stroke.toPx()
    val dash = if (paint.dashed) PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 3.dp.toPx())) else null
    val stroke = Stroke(width = sw, join = StrokeJoin.Round, pathEffect = dash)

    if (paint.breath != null && breath != null) {
        val grow = size.breath.toPx()
        val half = maxOf(size.halfWidth(shape), size.halfHeight(shape)).toPx().coerceAtLeast(1f)
        val factor = 1f + breath * grow / half
        scale(factor, factor, pivot = center) {
            outline(shape, center, size, paint.breath.copy(alpha = 0.7f * (1f - breath)), Stroke(width = sw / factor))
        }
    }
    outline(shape, center, size, paint.fill, null)
    outline(shape, center, size, paint.stroke, stroke)

    val g = size.glyph.toPx()
    if (glyph != null && g > 0f && (shape == NodeShape.Circle || shape == NodeShape.RoundedSquare)) {
        translate(center.x - g / 2f, center.y - g / 2f) {
            with(glyph) { draw(Size(g, g), colorFilter = ColorFilter.tint(paint.glyph)) }
        }
    }
    if (paint.spinner != null && spin != null) {
        val r = size.box.toPx() / 2f
        rotate(spin, pivot = center) {
            drawArc(
                paint.spinner, startAngle = -90f, sweepAngle = 100f, useCenter = false,
                topLeft = Offset(center.x - r, center.y - r), size = Size(r * 2, r * 2),
                style = Stroke(width = sw, cap = StrokeCap.Round)
            )
        }
    }
}

/** The shape's fill ([stroke] null) or outline. */
private fun DrawScope.outline(shape: NodeShape, c: Offset, size: NodeSize, color: Color, stroke: Stroke?) {
    val style = stroke ?: androidx.compose.ui.graphics.drawscope.Fill
    when (shape) {
        NodeShape.Circle -> drawCircle(color, radius = size.circle.toPx() / 2f, center = c, style = style)
        NodeShape.Slot -> {
            val w = size.slotWidth.toPx()
            val h = size.slotHeight.toPx()
            drawRoundRect(color, Offset(c.x - w / 2, c.y - h / 2), Size(w, h), CornerRadius(w / 2), style = style)
        }
        NodeShape.Diamond -> {
            val d = size.diamond.toPx()
            val path = Path().apply {
                moveTo(c.x, c.y - d); lineTo(c.x + d, c.y); lineTo(c.x, c.y + d); lineTo(c.x - d, c.y); close()
            }
            drawPath(path, color, style = style)
        }
        NodeShape.RoundedSquare -> {
            val q = size.square.toPx()
            drawRoundRect(color, Offset(c.x - q / 2, c.y - q / 2), Size(q, q), CornerRadius(size.squareRadius.toPx()), style = style)
        }
    }
}

/** A waiting node's breath: 0 → 1 every 2.4 s while [on] and motion is allowed; null otherwise. */
@Composable
internal fun rememberBreath(on: Boolean): State<Float>? {
    // No early return in a composable (AGENTS.md): branch instead.
    val live = on && !reducedMotion()
    return if (live) rememberInfiniteTransition(label = "breath").animateFloat(0f, 1f, infiniteRepeatable(tween(2400, easing = LinearEasing), RepeatMode.Restart), label = "breath") else null
}

/** A spinner's angle (one turn per 1.4 s) while [on] and motion is allowed; null otherwise. */
@Composable
internal fun rememberSpin(on: Boolean): State<Float>? {
    // No early return in a composable (AGENTS.md): branch instead.
    val live = on && !reducedMotion()
    return if (live) rememberInfiniteTransition(label = "spin").animateFloat(0f, 360f, infiniteRepeatable(tween(1400, easing = LinearEasing), RepeatMode.Restart), label = "spin") else null
}

/** A travelling pulse's progress along a segment (0 → 1 every [millis]) while [on]; null otherwise. */
@Composable
internal fun rememberPulse(on: Boolean, millis: Int = 2200): State<Float>? {
    // No early return in a composable (AGENTS.md): branch instead.
    val live = on && !reducedMotion()
    return if (live) rememberInfiniteTransition(label = "pulse").animateFloat(0f, 1f, infiniteRepeatable(tween(millis, easing = LinearEasing), RepeatMode.Restart), label = "pulse") else null
}

/**
 * One node on its own: a row's leading node, a timeline's node, a legend.
 * Fills [NodeSize.box]; a waiting node breathes past it (not clipped). The
 * fill matches [surface] so a line drawn behind it stops at its edge.
 */
@Composable
fun CircuitNode(
    shape: NodeShape,
    state: NodeState,
    modifier: Modifier = Modifier,
    size: NodeSize = NodeSize.Small,
    icon: ImageVector? = null,
    surface: Color = nodeSurface(),
    contentDescription: String? = null
) {
    val paint = nodePaint(state, circuitColors, surface)
    val glyph = icon?.let { rememberVectorPainter(it) }
    val breath = rememberBreath(state == NodeState.Waiting)
    Box(
        modifier
            .size(size.box)
            .then(if (contentDescription != null) Modifier.semantics { this.contentDescription = contentDescription } else Modifier)
            .drawBehind {
                drawNode(shape, Offset(this.size.width / 2f, this.size.height / 2f), size, paint, glyph, breath?.value)
            }
    )
}
