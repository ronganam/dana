package io.github.ronganam.dana.ui.components

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.drawscope.ContentDrawScope
import androidx.compose.ui.graphics.layer.CompositingStrategy as LayerCompositing
import androidx.compose.ui.graphics.layer.GraphicsLayer
import androidx.compose.ui.graphics.layer.drawLayer
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
import androidx.compose.ui.node.requireGraphicsContext
import androidx.compose.ui.platform.InspectorInfo
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.theme.LocalRichMotion
import io.github.ronganam.dana.ui.theme.Motion
import io.github.ronganam.dana.ui.theme.motionOff
import io.github.ronganam.dana.ui.theme.richMotion
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/*
 * Ember's motion patterns as cheap modifiers: each animates only graphicsLayer
 * alpha / translation or a draw, from a Modifier.Node coroutine, so a frame
 * redraws or re-places one node and never recomposes the screen.
 */

// ---------------------------------------------------------------- rows arriving

/**
 * Rows arriving on a screen's first load: fade + 8dp rise, 30ms apart, the
 * first [Motion.STAGGER_ROWS] rows only, once per screen per process (never on
 * a refresh, a poll, a scroll or when one item changes). Phone only.
 */
object Arrivals {
    private val played = HashSet<String>()
    private val windows = HashMap<String, Long>()
    private const val WINDOW_MS = 700L

    /** True when row [index] of [screen] should play its arrival now. */
    internal fun claim(screen: String, index: Int): Boolean {
        if (index >= Motion.STAGGER_ROWS || screen in played) return false
        val now = System.currentTimeMillis()
        val start = windows.getOrPut(screen) { now }
        if (now - start > WINDOW_MS) {
            played += screen
            return false
        }
        return true
    }
}

/** Plays this row's arrival (see [Arrivals]) when [screen] first shows its rows. */
fun Modifier.arrive(screen: String, index: Int): Modifier = this then ArriveElement(screen, index)

/** [LocalRichMotion] is fixed per flavour, so a one-shot animation reads it once, when it attaches. */
private fun CompositionLocalConsumerModifierNode.richMotionOnce(): Boolean = currentValueOf(LocalRichMotion)

private data class ArriveElement(val screen: String, val index: Int) : ModifierNodeElement<ArriveNode>() {
    override fun create() = ArriveNode(screen, index)
    override fun update(node: ArriveNode) = Unit
    override fun InspectorInfo.inspectableProperties() { name = "arrive" }
}

private class ArriveNode(val screen: String, val index: Int) : Modifier.Node(), LayoutModifierNode, CompositionLocalConsumerModifierNode {
    private var progress: Animatable<Float, *>? = null

    override fun onAttach() {
        if (!richMotionOnce() || !Arrivals.claim(screen, index)) return
        if (coroutineScope.coroutineContext.motionOff()) return
        val a = Animatable(0f)
        progress = a
        coroutineScope.launch {
            delay(index * Motion.STAGGER.toLong())
            a.animateTo(1f, Motion.arrive(Motion.SLOW))
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
                val v = a.value
                alpha = v
                // Fade each draw instead of an offscreen buffer the size of the content.
                compositingStrategy = CompositingStrategy.ModulateAlpha
                translationY = (1f - v) * Motion.Rise.toPx()
            }
        }
    }
}

// ---------------------------------------------------------------- entering on change

/**
 * Plays an entrance each time [key] changes (not on first composition): fade
 * in + slide [distance] from the side of travel ([direction] -1 from the
 * start, +1 from the end, 0 from below). The screen/pane/tab switch pattern;
 * phone only (the TV and the wall tablet just switch).
 */
fun Modifier.enterOnChange(key: Any?, direction: Int = 1, distance: Dp = Motion.Slide): Modifier =
    this then EnterElement(key, direction, distance)

private data class EnterElement(val key: Any?, val direction: Int, val distance: Dp) : ModifierNodeElement<EnterNode>() {
    override fun create() = EnterNode(key, direction, distance)
    override fun update(node: EnterNode) = node.update(key, direction, distance)
    override fun InspectorInfo.inspectableProperties() { name = "enterOnChange" }
}

private class EnterNode(var key: Any?, var direction: Int, var distance: Dp) : Modifier.Node(), LayoutModifierNode, CompositionLocalConsumerModifierNode {
    private var progress: Animatable<Float, *>? = null

    fun update(newKey: Any?, newDirection: Int, newDistance: Dp) {
        direction = newDirection
        distance = newDistance
        if (newKey == key) return
        key = newKey
        if (!richMotionOnce() || coroutineScope.coroutineContext.motionOff()) return
        val a = progress ?: Animatable(0f).also { progress = it }
        coroutineScope.launch {
            a.snapTo(0f)
            invalidatePlacement()
            a.animateTo(1f, Motion.arrive(Motion.BASE))
            progress = null
            invalidatePlacement()
        }
    }

    override fun MeasureScope.measure(measurable: Measurable, constraints: Constraints): MeasureResult {
        val p = measurable.measure(constraints)
        return layout(p.width, p.height) {
            val a = progress
            if (a == null) p.place(0, 0)
            else p.placeRelativeWithLayer(0, 0) {
                val v = a.value
                alpha = v
                // Fade each draw instead of an offscreen buffer the size of the content.
                compositingStrategy = CompositingStrategy.ModulateAlpha
                val d = (1f - v) * distance.toPx()
                if (direction == 0) translationY = d else translationX = d * direction
            }
        }
    }
}

// ---------------------------------------------------------------- just updated

/**
 * "This just changed": when [key] changes after the first composition, a tint
 * of [color] over the element fades out over [Motion.FLASH] ms.
 */
fun Modifier.flashOnChange(key: Any?, color: Color): Modifier = this then FlashElement(key, color)

private data class FlashElement(val key: Any?, val color: Color) : ModifierNodeElement<FlashNode>() {
    override fun create() = FlashNode(key, color)
    override fun update(node: FlashNode) = node.update(key, color)
    override fun InspectorInfo.inspectableProperties() { name = "flashOnChange" }
}

private class FlashNode(var key: Any?, var color: Color) : Modifier.Node(), DrawModifierNode {
    private val level = Animatable(0f)

    fun update(newKey: Any?, newColor: Color) {
        color = newColor
        if (newKey == key) return
        key = newKey
        coroutineScope.launch {
            level.snapTo(1f)
            level.animateTo(0f, tween(Motion.FLASH, easing = LinearEasing)) { invalidateDraw() }
        }
    }

    override fun ContentDrawScope.draw() {
        val v = level.value
        if (v > 0f) drawRect(color.copy(alpha = color.alpha * v))
        drawContent()
    }
}

// ---------------------------------------------------------------- skeleton

/**
 * A loading placeholder block: [base] fill with one shimmer sweep across it
 * (phone only; elsewhere the block stays still), then it rests.
 */
@Composable
fun Modifier.skeleton(base: Color = MaterialTheme.colorScheme.surfaceContainerHigh): Modifier {
    val shine = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.06f)
    return this then SkeletonElement(base, shine, sweep = richMotion)
}

private data class SkeletonElement(val base: Color, val shine: Color, val sweep: Boolean) : ModifierNodeElement<SkeletonNode>() {
    override fun create() = SkeletonNode(base, shine, sweep)
    override fun update(node: SkeletonNode) { node.base = base; node.shine = shine; node.invalidateDraw() }
    override fun InspectorInfo.inspectableProperties() { name = "skeleton" }
}

private class SkeletonNode(var base: Color, var shine: Color, val sweep: Boolean) : Modifier.Node(), DrawModifierNode {
    private val x = Animatable(-1f)

    override fun onAttach() {
        if (!sweep || coroutineScope.coroutineContext.motionOff()) return
        coroutineScope.launch { x.animateTo(2f, tween(1100, easing = LinearEasing)) { invalidateDraw() } }
    }

    override fun ContentDrawScope.draw() {
        drawRect(base)
        val v = x.value
        if (v > -1f && v < 2f) {
            val band = size.width * 0.5f
            val cx = v * size.width
            drawRect(Brush.horizontalGradient(listOf(Color.Transparent, shine, Color.Transparent), startX = cx - band, endX = cx + band))
        }
        drawContent()
    }
}

// ---------------------------------------------------------------- changing numbers

/**
 * A number or short count that rolls when it changes (the old value leaves
 * upwards, the new one rises in), fast; never a counting loop.
 */
@Composable
fun RollingText(
    text: String,
    modifier: Modifier = Modifier,
    style: TextStyle = LocalTextStyle.current,
    color: Color = Color.Unspecified
) {
    AnimatedContent(
        targetState = text,
        transitionSpec = {
            (slideInVertically(Motion.arrive(Motion.FAST + 60)) { it / 2 } + fadeIn(Motion.fast()))
                .togetherWith(slideOutVertically(Motion.leave()) { -it / 2 } + fadeOut(Motion.leave()))
        },
        modifier = modifier.clipToBounds(),
        label = "rolling"
    ) { t -> Text(t, style = style, color = color, maxLines = 1, softWrap = false) }
}

// ---------------------------------------------------------------- canvas glow

/**
 * The faint, static ember glow at the top of the main canvas (accent at ~7%),
 * drawn behind the content. Phone only: [enabled] false on other flavours.
 */
fun Modifier.emberGlow(accent: Color, enabled: Boolean = true): Modifier =
    if (!enabled) this else this then GlowElement(accent)

private data class GlowElement(val accent: Color) : ModifierNodeElement<GlowNode>() {
    override fun create() = GlowNode(accent)
    override fun update(node: GlowNode) { node.accent = accent; node.invalidateDraw() }
    override fun InspectorInfo.inspectableProperties() { name = "emberGlow" }
}

private class GlowNode(var accent: Color) : Modifier.Node(), DrawModifierNode {
    // Recorded once per size / colour into its own offscreen layer: the root
    // re-records often, and evaluating the gradient over the canvas each frame
    // costs whole frames where the GPU is weak. Each frame only composites it.
    private var layer: GraphicsLayer? = null
    private var recorded: Pair<Size, Color>? = null

    override fun onAttach() {
        layer = requireGraphicsContext().createGraphicsLayer().apply { compositingStrategy = LayerCompositing.Offscreen }
    }

    override fun onDetach() {
        layer?.let { requireGraphicsContext().releaseGraphicsLayer(it) }
        layer = null
        recorded = null
    }

    override fun ContentDrawScope.draw() {
        val l = layer
        if (l != null) {
            val r = maxOf(size.width * 0.9f, 360.dp.toPx())
            val area = size.copy(height = minOf(size.height, r))
            if (recorded != area to accent) {
                l.record(IntSize(area.width.toInt(), area.height.toInt())) {
                    drawRect(
                        Brush.radialGradient(
                            colors = listOf(accent.copy(alpha = 0.075f), accent.copy(alpha = 0.03f), Color.Transparent),
                            center = Offset(area.width * 0.28f, -r * 0.18f),
                            radius = r
                        )
                    )
                }
                recorded = area to accent
            }
            drawLayer(l)
        }
        drawContent()
    }
}

/** A row's start bar in [color] (3dp), drawn behind; for reminder groups and tinted rows. */
fun Modifier.startBar(color: Color, width: Dp = 3.dp): Modifier = this then StartBarElement(color, width)

private data class StartBarElement(val color: Color, val width: Dp) : ModifierNodeElement<StartBarNode>() {
    override fun create() = StartBarNode(color, width)
    override fun update(node: StartBarNode) { node.color = color; node.width = width; node.invalidateDraw() }
    override fun InspectorInfo.inspectableProperties() { name = "startBar" }
}

private class StartBarNode(var color: Color, var width: Dp) : Modifier.Node(), DrawModifierNode {
    override fun ContentDrawScope.draw() {
        drawContent()
        val w = width.toPx()
        val x = if (layoutDirection == androidx.compose.ui.unit.LayoutDirection.Rtl) size.width - w else 0f
        drawRect(color, topLeft = Offset(x, 0f), size = size.copy(width = w))
    }
}

// ---------------------------------------------------------------- a new message

/**
 * A chat message arriving: when [play] is true as the element first appears,
 * it rises [Motion.Rise] and fades in (decelerating, base). Later updates
 * (a streaming reply growing) don't replay it. Phone only.
 */
fun Modifier.riseIn(play: Boolean): Modifier = this then RiseElement(play)

// Always in the chain (play only decides at creation), so a later update can't cut the entrance short.
private data class RiseElement(val play: Boolean) : ModifierNodeElement<RiseNode>() {
    override fun create() = RiseNode(play)
    override fun update(node: RiseNode) = Unit
    override fun InspectorInfo.inspectableProperties() { name = "riseIn" }
}

private class RiseNode(private val play: Boolean) : Modifier.Node(), LayoutModifierNode, CompositionLocalConsumerModifierNode {
    private var progress: Animatable<Float, *>? = null

    override fun onAttach() {
        if (!play || !richMotionOnce() || coroutineScope.coroutineContext.motionOff()) return
        val a = Animatable(0f)
        progress = a
        coroutineScope.launch {
            a.animateTo(1f, Motion.arrive(Motion.BASE + 60))
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
                val v = a.value
                alpha = v
                // Fade each draw instead of an offscreen buffer the size of the content.
                compositingStrategy = CompositingStrategy.ModulateAlpha
                translationY = (1f - v) * Motion.Rise.toPx()
            }
        }
    }
}

/**
 * Which rows of a list are new at its end since the last look: the first look
 * (the thread opening, the cache) and older pages loaded on top are not.
 */
class TailArrivals {
    private var lastKey: Any? = null
    private var started = false

    /** The keys appended after the previous last row; call once per composition with the current keys. */
    fun fresh(keys: List<Any>): Set<Any> {
        if (keys.isEmpty()) return emptySet()
        val prev = lastKey
        lastKey = keys.last()
        if (!started) { started = true; return emptySet() }
        val at = keys.lastIndexOf(prev)
        return if (at < 0 || at == keys.size - 1) emptySet() else keys.subList(at + 1, keys.size).toHashSet()
    }
}

// ---------------------------------------------------------------- listening

/**
 * A gentle ring leaving the element's edge while [active] (listening): it
 * grows by [reach] and fades, every 1.3 s, and stops as soon as [active] goes
 * false. Draw-only, behind the element; nothing when animations are off.
 */
fun Modifier.pulseRing(active: Boolean, color: Color, reach: Dp = 10.dp): Modifier = this then PulseElement(active, color, reach)

private data class PulseElement(val active: Boolean, val color: Color, val reach: Dp) : ModifierNodeElement<PulseNode>() {
    override fun create() = PulseNode(active, color, reach)
    override fun update(node: PulseNode) = node.update(active, color, reach)
    override fun InspectorInfo.inspectableProperties() { name = "pulseRing" }
}

private class PulseNode(var active: Boolean, var color: Color, var reach: Dp) : Modifier.Node(), DrawModifierNode {
    private val t = Animatable(0f)
    private var job: kotlinx.coroutines.Job? = null

    override fun onAttach() { if (active) start() }

    fun update(newActive: Boolean, newColor: Color, newReach: Dp) {
        color = newColor
        reach = newReach
        if (newActive == active) return
        active = newActive
        if (active) start() else {
            job?.cancel()
            job = null
            coroutineScope.launch { t.snapTo(0f); invalidateDraw() }
        }
    }

    private fun start() {
        if (coroutineScope.coroutineContext.motionOff()) return
        job?.cancel()
        job = coroutineScope.launch {
            while (true) {
                t.snapTo(0f)
                t.animateTo(1f, tween(1300, easing = Motion.Decelerate)) { invalidateDraw() }
            }
        }
    }

    override fun ContentDrawScope.draw() {
        val v = t.value
        if (active && v > 0f) {
            val r = size.minDimension / 2f + reach.toPx() * v
            drawCircle(color.copy(alpha = 0.5f * (1f - v)), r, style = androidx.compose.ui.graphics.drawscope.Stroke(2.dp.toPx()))
        }
        drawContent()
    }
}
