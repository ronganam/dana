package io.github.ronganam.dana.ui.components.assistant

import io.github.ronganam.dana.ui.theme.motionOff
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.State
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.sin

enum class VoiceDialMode { IDLE, LISTENING, THINKING }

/**
 * The assistant's one voice control: a vermilion button wrapped in a ring of ticks.
 * Ticks breathe like a level meter while listening, with a gentle pulse ring
 * leaving the button (stops the moment listening stops), a bright sweep circles
 * while thinking, and they rest as a quiet dial when idle. Replaces the old glow
 * sphere. Everything animated is read in the Canvas: frames redraw, never recompose.
 */
@Composable
fun VoiceDial(
    mode: VoiceDialMode,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    size: Dp = 76.dp,
    ticks: Int = 40
) {
    // Nothing loops when the system turned animations off.
    val active = mode != VoiceDialMode.IDLE && !motionOff()
    // Animated values are read only inside the Canvas below, so each frame just
    // redraws the ticks instead of recomposing the dial (that matters on the
    // tablet and the TV, where the dial runs for the whole conversation).
    val phase: State<Float>? = if (active) {
        val transition = rememberInfiniteTransition(label = "dial")
        transition.animateFloat(
            initialValue = 0f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(
                animation = tween(if (mode == VoiceDialMode.THINKING) 1400 else 2600, easing = LinearEasing),
                repeatMode = RepeatMode.Restart
            ),
            label = "dial_t"
        )
    } else null

    val energyState = animateFloatAsState(
        targetValue = if (mode == VoiceDialMode.LISTENING) 1f else 0f,
        animationSpec = tween(300),
        label = "dial_energy"
    )

    val accent = MaterialTheme.colorScheme.primary
    val rest = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.45f)
    val onAccent = MaterialTheme.colorScheme.onPrimary
    val label = when (mode) {
        VoiceDialMode.IDLE -> "Start listening"
        VoiceDialMode.LISTENING -> "Stop listening and send"
        VoiceDialMode.THINKING -> "Working"
    }

    Box(
        modifier = modifier
            .size(size)
            .semantics { contentDescription = label },
        contentAlignment = Alignment.Center
    ) {
        Canvas(modifier = Modifier.size(size)) {
            val t = phase?.value ?: 0f
            val energy = energyState.value
            val c = center
            val outer = this.size.minDimension / 2f
            val maxLen = outer * 0.24f
            val minLen = outer * 0.08f
            val stroke = 2.2.dp.toPx()
            if (mode == VoiceDialMode.LISTENING && phase != null) {
                // Pulse ring: from the button's edge out to the ticks, fading as it goes (twice per cycle).
                val core = outer * 0.56f
                val ring = (t * 2f) % 1f
                drawCircle(
                    color = accent.copy(alpha = 0.45f * (1f - ring) * energy),
                    radius = core + (outer - minLen - core) * ring,
                    center = c,
                    style = androidx.compose.ui.graphics.drawscope.Stroke(width = 2.dp.toPx() * (1f - ring * 0.5f))
                )
            }
            for (i in 0 until ticks) {
                val frac = i.toFloat() / ticks
                val ang = (frac * 2f * PI).toFloat() - (PI / 2).toFloat()
                // Pseudo-level: two travelling sines give an organic, non-repeating shimmer.
                val wave = (sin(frac * 2f * PI * 3 + t * 2f * PI * 2) + sin(frac * 2f * PI * 7 - t * 2f * PI * 3)) / 2f
                val level = ((wave.toFloat() + 1f) / 2f)
                val resting = if (i % 5 == 0) 0.55f else 0.25f
                var len = minLen + (maxLen - minLen) * (resting * (1f - energy) + level * energy)
                var color = rest
                if (mode == VoiceDialMode.LISTENING) {
                    color = accent.copy(alpha = 0.55f + 0.45f * level)
                }
                if (mode == VoiceDialMode.THINKING) {
                    var d = t - frac
                    if (d < 0f) d += 1f
                    val glow = max(0f, 1f - d * 3.2f)
                    len = minLen + (maxLen - minLen) * (resting * 0.6f + glow * 0.9f)
                    color = if (glow > 0.02f) accent.copy(alpha = 0.3f + 0.7f * glow) else rest
                }
                val r1 = outer - len
                val r2 = outer
                drawLine(
                    color = color,
                    start = Offset(c.x + cos(ang) * r1, c.y + sin(ang) * r1),
                    end = Offset(c.x + cos(ang) * r2, c.y + sin(ang) * r2),
                    strokeWidth = stroke,
                    cap = StrokeCap.Round
                )
            }
        }
        // Core button
        val core = size * 0.56f
        Box(
            modifier = Modifier
                .size(core)
                .clip(CircleShape)
                .background(accent)
                .clickable(role = Role.Button, onClick = onClick),
            contentAlignment = Alignment.Center
        ) {
            if (mode == VoiceDialMode.LISTENING) {
                // Stop glyph: rounded square
                Box(
                    modifier = Modifier
                        .size(core * 0.34f)
                        .clip(RoundedCornerShape(3.dp))
                        .background(onAccent)
                )
            } else {
                Icon(
                    Icons.Filled.Mic,
                    contentDescription = null,
                    tint = onAccent,
                    modifier = Modifier.size(core * 0.5f)
                )
            }
        }
    }
}
