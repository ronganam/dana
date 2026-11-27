package io.github.ronganam.dana.ui.components.circuit

import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.ui.theme.CircuitColors
import io.github.ronganam.dana.ui.theme.circuitColors

/** Where a run is with one of its steps. */
enum class StepState { Done, Current, Next, Failed }

/** One step of a run ("AC off", "Tell you"). */
@Immutable
data class Step(val label: String, val state: StepState)

private fun stepPaint(state: StepState, c: CircuitColors, surface: Color): NodePaint = when (state) {
    StepState.Done -> NodePaint(c.accent, c.accent, c.onAccentFilled)
    StepState.Current -> NodePaint(c.accent, surface, c.accent, spinner = c.accent)
    StepState.Next -> NodePaint(c.node, surface, c.inkSoft)
    StepState.Failed -> NodePaint(c.warm, c.warm, c.onWarm)
}

private fun stepSegment(a: StepState, b: StepState, c: CircuitColors): SegmentPaint = when {
    a == StepState.Done && (b == StepState.Done || b == StepState.Current) -> SegmentPaint(c.accent, dashed = false)
    b == StepState.Failed -> SegmentPaint(c.warm, dashed = false)
    else -> SegmentPaint(c.wireRaised, dashed = true)
}

/**
 * A run's steps on a line: done steps filled, the current one hollow with a
 * spinner and a pulse travelling into it, what's next hollow on a dashed
 * line; the current step's label in ink. Labels under the nodes, each step
 * in an equal share of the width. Motion stops under reduced motion.
 */
@Composable
fun StepLine(
    steps: List<Step>,
    modifier: Modifier = Modifier,
    showLabels: Boolean = true,
    labelStyle: TextStyle = MaterialTheme.typography.bodyMedium.copy(fontSize = 13.sp, lineHeight = 18.sp),
    surface: Color = nodeSurface()
) {
    val c = circuitColors
    val paints = steps.map { stepPaint(it.state, c, surface) }
    val segs = (0 until (steps.size - 1).coerceAtLeast(0)).map { stepSegment(steps[it].state, steps[it + 1].state, c) }
    val current = steps.indexOfFirst { it.state == StepState.Current }
    val spin = rememberSpin(current >= 0)
    val pulse = rememberPulse(current > 0, millis = 1800)
    val size = NodeSize.Step
    LineLayout(
        count = steps.size,
        labels = if (showLabels) steps.map { it.label } else steps.map { null },
        emphasized = steps.map { it.state == StepState.Current },
        orientation = Orientation.Horizontal,
        size = size,
        positions = null,
        rowGap = 0.dp,
        labelStyle = labelStyle,
        labelGap = 4.dp,
        modifier = modifier.semantics { contentDescription = stepsDescription(steps) }
    ) { centers ->
        drawSegments(centers, Orientation.Horizontal, segs, { NodeShape.RoundedSquare }, size, null, pulse, (current - 1).takeIf { current > 0 }, c.accent)
        centers.forEachIndexed { i, o -> drawNode(NodeShape.RoundedSquare, o, size, paints[i], spin = spin?.value) }
    }
}

/**
 * A run's steps as a tiny line for a row's trailing slot (Now's "Going on"):
 * [done] filled circles, the current one with a spinner, the rest hollow.
 */
@Composable
fun StepLineMini(done: Int, total: Int, modifier: Modifier = Modifier, width: Dp = 64.dp) {
    val c = circuitColors
    val surface = nodeSurface()
    val spin = rememberSpin(done < total)
    val pulse = rememberPulse(done in 1 until total, millis = 1800)
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    val dot = NodeSize(box = 14.dp, circle = 8.dp, slotWidth = 4.dp, slotHeight = 8.dp, diamond = 4.dp, square = 8.dp, squareRadius = 2.dp, stroke = 1.5.dp, glyph = 0.dp, breath = 0.dp)
    Box(
        modifier
            .size(width, 16.dp)
            .semantics { contentDescription = "Step ${(done + 1).coerceAtMost(total)} of $total" }
            .drawBehind {
                if (total <= 0) return@drawBehind
                val pad = 5.dp.toPx()
                val step = if (total > 1) (size.width - 2 * pad) / (total - 1) else 0f
                val centers = (0 until total).map { i ->
                    val x = pad + i * step
                    Offset(if (rtl) size.width - x else x, size.height / 2f)
                }
                val segs = (0 until total - 1).map { i ->
                    if (i + 1 <= done) SegmentPaint(c.accent, dashed = false) else SegmentPaint(c.wire, dashed = false)
                }
                drawSegments(centers, Orientation.Horizontal, segs, { NodeShape.Circle }, dot, null, pulse, (done - 1).takeIf { done in 1 until total }, c.accent)
                centers.forEachIndexed { i, o ->
                    val paint = when {
                        i < done -> NodePaint(c.accent, c.accent, c.onAccentFilled)
                        i == done -> NodePaint(c.accent, surface, c.accent, spinner = c.accent)
                        else -> NodePaint(c.node, surface, c.inkSoft)
                    }
                    drawNode(NodeShape.Circle, o, dot, paint, spin = if (i == done) spin?.value else null)
                }
            }
    )
}

private fun stepsDescription(steps: List<Step>): String {
    val current = steps.indexOfFirst { it.state == StepState.Current }
    val head = if (current >= 0) "Step ${current + 1} of ${steps.size}: ${steps[current].label}" else "${steps.count { it.state == StepState.Done }} of ${steps.size} steps done"
    return head + ". " + steps.joinToString(", ") { it.label }
}
