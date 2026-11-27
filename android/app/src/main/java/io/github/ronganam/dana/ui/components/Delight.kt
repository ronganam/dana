package io.github.ronganam.dana.ui.components

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.AnimationVector1D
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.tween
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.drawscope.ContentDrawScope
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.layout.Measurable
import androidx.compose.ui.layout.MeasureResult
import androidx.compose.ui.layout.MeasureScope
import androidx.compose.ui.node.CompositionLocalConsumerModifierNode
import androidx.compose.ui.node.DrawModifierNode
import androidx.compose.ui.node.LayoutModifierNode
import androidx.compose.ui.node.ModifierNodeElement
import androidx.compose.ui.node.currentValueOf
import androidx.compose.ui.node.invalidateDraw
import androidx.compose.ui.node.invalidatePlacement
import androidx.compose.ui.platform.InspectorInfo
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.theme.LocalRichMotion
import io.github.ronganam.dana.ui.theme.Motion
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.hues
import io.github.ronganam.dana.ui.theme.motionOff
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/*
 * Small moments of delight, built like Life.kt's patterns: each is a
 * Modifier.Node that animates a layer's scale or a draw, plays once (never
 * loops), and does nothing on the TV / wall tablet (no rich motion) or when
 * the system turned animations off.
 */

/** Rich motion is on for this node: the phone flavour and animations not turned off. */
private fun CompositionLocalConsumerModifierNode.mayPlay(node: Modifier.Node): Boolean =
    currentValueOf(LocalRichMotion) && !node.coroutineScope.coroutineContext.motionOff()

// ---------------------------------------------------------------- once per process

/**
 * One-shot moments that should play once per process for a key (the first
 * calm Now of the day), like [Arrivals] does for rows.
 */
object Celebrations {
    private val played = HashSet<String>()

    /** True the first time [key] is claimed in this process. */
    @Synchronized
    fun claim(key: String): Boolean = played.add(key)
}

// ---------------------------------------------------------------- popping in

/**
 * The element pops in as it first appears ([play]): from 40% scale, faded,
 * to its size on [Motion.bouncy] (a little past it, then back). For a mark
 * or node that means "this just became true" (the calm card's check).
 */
fun Modifier.popIn(play: Boolean = true): Modifier = this then PopInElement(play)

private data class PopInElement(val play: Boolean) : ModifierNodeElement<PopInNode>() {
    override fun create() = PopInNode(play)
    override fun update(node: PopInNode) = Unit
    override fun InspectorInfo.inspectableProperties() { name = "popIn" }
}

private class PopInNode(private val play: Boolean) : Modifier.Node(), LayoutModifierNode, CompositionLocalConsumerModifierNode {
    private var scale: Animatable<Float, AnimationVector1D>? = null

    override fun onAttach() {
        if (!play || !mayPlay(this)) return
        val a = Animatable(0.4f)
        scale = a
        coroutineScope.launch {
            a.animateTo(1f, Motion.bouncy())
            scale = null
            invalidatePlacement()
        }
    }

    override fun MeasureScope.measure(measurable: Measurable, constraints: Constraints): MeasureResult {
        val p = measurable.measure(constraints)
        return layout(p.width, p.height) {
            val a = scale
            if (a == null) p.place(0, 0)
            else p.placeWithLayer(0, 0) {
                val v = a.value
                scaleX = v
                scaleY = v
                alpha = ((v - 0.4f) / 0.5f).coerceIn(0f, 1f)
            }
        }
    }
}

// ---------------------------------------------------------------- a count rising

/**
 * Pops when [value] rises (a badge's count, a nav icon picked: pass 1 when
 * selected, 0 otherwise): it swells to [Motion.POP_SCALE] fast and springs
 * back. Going down never pops. [popOnAttach]: also pop, from 0, as it first
 * appears (a badge that just showed up; leave it false for one that was
 * there when the screen opened).
 */
fun Modifier.popOnRise(value: Int, popOnAttach: Boolean = false): Modifier = this then PopRiseElement(value, popOnAttach)

private data class PopRiseElement(val value: Int, val popOnAttach: Boolean) : ModifierNodeElement<PopRiseNode>() {
    override fun create() = PopRiseNode(value, popOnAttach)
    override fun update(node: PopRiseNode) = node.update(value)
    override fun InspectorInfo.inspectableProperties() { name = "popOnRise" }
}

private class PopRiseNode(private var value: Int, private val popOnAttach: Boolean) : Modifier.Node(), LayoutModifierNode, CompositionLocalConsumerModifierNode {
    // Starts at 0 when it pops in, so its first frame isn't already full size.
    private val scale = Animatable(if (popOnAttach) 0f else 1f)
    private var active = false

    override fun onAttach() { if (popOnAttach) pop(fromZero = true) }

    fun update(newValue: Int) {
        val rose = newValue > value
        value = newValue
        if (rose) pop(fromZero = false)
    }

    private fun pop(fromZero: Boolean) {
        if (!mayPlay(this)) return
        active = true
        invalidatePlacement()
        coroutineScope.launch {
            if (fromZero) scale.snapTo(0f)
            scale.animateTo(Motion.POP_SCALE, tween(if (fromZero) Motion.FAST + 40 else 90, easing = Motion.Decelerate))
            scale.animateTo(1f, Motion.bouncy())
            active = false
            invalidatePlacement()
        }
    }

    override fun MeasureScope.measure(measurable: Measurable, constraints: Constraints): MeasureResult {
        val p = measurable.measure(constraints)
        return layout(p.width, p.height) {
            if (!active) p.place(0, 0)
            else p.placeWithLayer(0, 0) {
                val v = scale.value
                scaleX = v
                scaleY = v
            }
        }
    }
}

// ---------------------------------------------------------------- growing in

/**
 * A short mark grows in from its start as it first appears (the empty
 * state's hue bar): scaleX 0 → 1 on [Motion.bouncy], mirrored in RTL.
 */
fun Modifier.growIn(play: Boolean = true): Modifier = this then GrowInElement(play)

private data class GrowInElement(val play: Boolean) : ModifierNodeElement<GrowInNode>() {
    override fun create() = GrowInNode(play)
    override fun update(node: GrowInNode) = Unit
    override fun InspectorInfo.inspectableProperties() { name = "growIn" }
}

private class GrowInNode(private val play: Boolean) : Modifier.Node(), LayoutModifierNode, CompositionLocalConsumerModifierNode {
    private var progress: Animatable<Float, AnimationVector1D>? = null

    override fun onAttach() {
        if (!play || !mayPlay(this)) return
        val a = Animatable(0f)
        progress = a
        coroutineScope.launch {
            delay(Motion.FAST.toLong())
            a.animateTo(1f, Motion.bouncy())
            progress = null
            invalidatePlacement()
        }
    }

    override fun MeasureScope.measure(measurable: Measurable, constraints: Constraints): MeasureResult {
        val p = measurable.measure(constraints)
        return layout(p.width, p.height) {
            val a = progress
            if (a == null) p.place(0, 0)
            else p.placeWithLayer(0, 0) {
                scaleX = a.value.coerceAtLeast(0f)
                transformOrigin = TransformOrigin(if (layoutDirection == LayoutDirection.Rtl) 1f else 0f, 0.5f)
            }
        }
    }
}

// ---------------------------------------------------------------- a small celebration

/**
 * A one-shot burst: when the element first appears with [play], 8 tiny dots
 * in [colors] leave its edge, fly out up to [reach] and fade over
 * [Motion.CELEBRATE] ms; then nothing is drawn again. Draw-only, over the
 * element, clipped by whatever clips it. For a moment worth a smile (Now's
 * "Nothing needs you"); give it [celebrationHues], never the warm one.
 */
fun Modifier.celebrate(play: Boolean, colors: List<Color>, reach: Dp = 26.dp): Modifier =
    this then CelebrateElement(play, colors, reach)

/** The hues a celebration flies in: the named hues and the accent, never warm (that one means "needs you"). */
@Composable
fun celebrationHues(): List<Color> {
    val h = hues
    return listOf(h.sky, h.sage, h.plum, h.amber, circuitColors.accent, h.teal, h.rose, h.sage)
}

private data class CelebrateElement(val play: Boolean, val colors: List<Color>, val reach: Dp) : ModifierNodeElement<CelebrateNode>() {
    override fun create() = CelebrateNode(play, colors, reach)
    override fun update(node: CelebrateNode) { node.colors = colors; node.reach = reach }
    override fun InspectorInfo.inspectableProperties() { name = "celebrate" }
}

private class CelebrateNode(private val play: Boolean, var colors: List<Color>, var reach: Dp) : Modifier.Node(), DrawModifierNode, CompositionLocalConsumerModifierNode {
    private val t = Animatable(0f)
    private var on = false

    override fun onAttach() {
        if (!play || colors.isEmpty() || !mayPlay(this)) return
        on = true
        coroutineScope.launch {
            // Let the thing it celebrates land first.
            delay(Motion.FAST.toLong())
            t.animateTo(1f, tween(Motion.CELEBRATE, easing = LinearEasing)) { invalidateDraw() }
            on = false
            invalidateDraw()
        }
    }

    override fun ContentDrawScope.draw() {
        drawContent()
        val v = t.value
        if (!on || v <= 0f || v >= 1f) return
        val travel = Motion.Decelerate.transform(v)
        val alpha = when {
            v < 0.1f -> v / 0.1f
            v > 0.55f -> 1f - (v - 0.55f) / 0.45f
            else -> 1f
        }
        val start = size.minDimension / 2f
        val far = reach.toPx()
        val dot = 2.5.dp.toPx()
        val n = DOTS
        for (i in 0 until n) {
            val angle = (i.toFloat() / n) * 2f * PI.toFloat() - PI.toFloat() / 2f + 0.25f
            // Every other dot a little shorter and smaller, so the burst isn't a perfect ring.
            val short = i % 2 == 1
            val dist = start + far * travel * (if (short) 0.7f else 1f)
            val r = dot * (if (short) 0.8f else 1f) * (1f - 0.45f * v)
            val color = colors[i % colors.size]
            drawCircle(
                color.copy(alpha = color.alpha * alpha),
                radius = r,
                center = center + Offset(cos(angle) * dist, sin(angle) * dist)
            )
        }
    }

    private companion object { const val DOTS = 8 }
}

// ---------------------------------------------------------------- hero surface

/**
 * A hero card's static fill: [base] with a soft tonal violet wash at its top
 * that has faded into [base] by [reach] of its height (so nodes punched out
 * on [base] lower down still match). A plain linear gradient, drawn with the
 * card (nothing offscreen, nothing animated).
 */
@Composable
fun heroBrush(base: Color = MaterialTheme.colorScheme.surfaceContainerLow, reach: Float = 0.6f): Brush {
    val top = lerp(base, circuitColors.accentTonal, 0.32f)
    return Brush.verticalGradient(0f to top, reach to base, 1f to base)
}
