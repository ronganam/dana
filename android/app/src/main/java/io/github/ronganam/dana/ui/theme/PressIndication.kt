package io.github.ronganam.dana.ui.theme

import androidx.compose.animation.core.Animatable
import androidx.compose.foundation.IndicationNodeFactory
import androidx.compose.foundation.interaction.InteractionSource
import androidx.compose.foundation.interaction.PressInteraction
import androidx.compose.material3.ripple
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.drawscope.ContentDrawScope
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.node.DelegatableNode
import androidx.compose.ui.node.DelegatingNode
import androidx.compose.ui.node.DrawModifierNode
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

/**
 * Every clickable's press feedback (the app's `LocalIndication`, set by
 * [DanaTheme]): the theme's ripple gives the slight tint (and the focus
 * state), and the pressed thing squishes to 0.96, fast, then springs back
 * on release with a hair of overshoot ([Motion.bouncy]), so a tap feels
 * tactile.
 *
 * The shrink is draw-phase only: a scale of the canvas around the content
 * (ripple included), no layer, no recomposition. Big targets shrink by at
 * most ~8dp a side so a whole pane never visibly jumps.
 */
object PressIndication : IndicationNodeFactory {
    private val tint = ripple()
    override fun create(interactionSource: InteractionSource): DelegatableNode = PressNode(interactionSource, tint.create(interactionSource))
    override fun hashCode(): Int = -1
    override fun equals(other: Any?) = other === this
}

/**
 * Holds the two as delegates. It must not draw itself: a delegating node that
 * is a draw node itself has its delegates' draws skipped (the ripple would vanish).
 */
private class PressNode(source: InteractionSource, tint: DelegatableNode) : DelegatingNode() {
    init {
        delegate(ShrinkNode(source))
        delegate(tint)
    }
}

private class ShrinkNode(private val source: InteractionSource) : Modifier.Node(), DrawModifierNode {
    private val pressed = Animatable(0f)

    override fun onAttach() {
        coroutineScope.launch {
            var presses = 0
            source.interactions.collect { i ->
                if (i !is PressInteraction) return@collect
                presses = if (i is PressInteraction.Press) presses + 1 else (presses - 1).coerceAtLeast(0)
                val target = if (presses > 0) 1f else 0f
                launch {
                    // A quick tap still shows the press: reach it before letting go.
                    if (target == 0f && pressed.targetValue == 1f) pressed.animateTo(1f, Motion.fast())
                    // Let go: spring back, passing rest a little (the scale goes just above 1).
                    pressed.animateTo(target, if (target == 0f) Motion.bouncy() else Motion.fast())
                }
            }
        }
    }

    override fun ContentDrawScope.draw() {
        // Read in the draw: a press redraws this node only.
        val p = pressed.value
        if (p == 0f) {
            drawContent()
        } else {
            val longest = maxOf(size.width, size.height).coerceAtLeast(1f)
            val most = (16.dp.toPx() / longest).coerceAtMost(1f - Motion.PRESS_SCALE)
            // p < 0 while the release overshoots: a touch larger than at rest.
            scale(1f - most * p) { this@draw.drawContent() }
        }
    }
}
