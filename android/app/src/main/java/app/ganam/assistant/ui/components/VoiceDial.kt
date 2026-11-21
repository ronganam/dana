package app.ganam.assistant.ui.components

import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * The assistant's voice mark: a dial of fine ticks around a solid disc. Idle it is a still, even ring;
 * while listening the ticks swell in a travelling wave. Deliberately not a glowing orb.
 */
@Composable
fun VoiceDial(listening: Boolean, modifier: Modifier = Modifier, content: @Composable () -> Unit = {}) {
    val transition = rememberInfiniteTransition(label = "dial")
    val phase by transition.animateFloat(
        initialValue = 0f,
        targetValue = (2 * PI).toFloat(),
        animationSpec = infiniteRepeatable(tween(2200, easing = LinearEasing)),
        label = "phase"
    )
    val energy by animateFloatAsState(if (listening) 1f else 0f, tween(400), label = "energy")
    val accent = MaterialTheme.colorScheme.primary
    val ringTrack = MaterialTheme.colorScheme.outline

    Box(modifier = modifier, contentAlignment = androidx.compose.ui.Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val c = Offset(size.width / 2, size.height / 2)
            val outer = size.minDimension / 2
            val disc = outer * 0.62f
            val ticks = 40
            val stroke = outer * 0.045f
            for (i in 0 until ticks) {
                val a = (2 * PI * i / ticks).toFloat()
                val wave = (sin(phase * 2 + i * 0.55f) * 0.5f + 0.5f) * (sin(phase + i * 0.21f) * 0.5f + 0.5f)
                val len = outer * (0.10f + 0.20f * energy * wave)
                val r0 = disc + outer * 0.08f
                val r1 = r0 + len
                drawLine(
                    color = if (energy > 0.05f) accent.copy(alpha = 0.35f + 0.65f * wave * energy) else ringTrack,
                    start = Offset(c.x + cos(a) * r0, c.y + sin(a) * r0),
                    end = Offset(c.x + cos(a) * r1, c.y + sin(a) * r1),
                    strokeWidth = stroke,
                    cap = StrokeCap.Round
                )
            }
            drawCircle(accent, radius = disc, center = c)
            drawCircle(
                color = accent.copy(alpha = 0.35f * energy),
                radius = disc + outer * 0.04f,
                center = c,
                style = Stroke(width = stroke)
            )
        }
        content()
    }
}
