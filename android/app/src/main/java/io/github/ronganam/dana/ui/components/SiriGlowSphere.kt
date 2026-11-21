package io.github.ronganam.dana.ui.components

import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlin.math.sin

// Warm monochrome orb when idle; the multicolor voice identity only appears while listening.
private val OrbWarm = Color(0xFFEAE2D2)
private val OrbWarmCore = Color(0xFFF7F5F0)
private val OrbCyan = Color(0xFF00F0FF)
private val OrbMagenta = Color(0xFFD000FF)
private val OrbCoral = Color(0xFFFF3B30)

@Composable
fun SiriGlowSphere(
    modifier: Modifier = Modifier,
    isListening: Boolean = false,
    sphereRadius: Dp? = null
) {
    val infiniteTransition = rememberInfiniteTransition(label = "siri_glow")

    // Slow rotation phase when idle
    val idlePhase by infiniteTransition.animateFloat(
        initialValue = 0f,
        targetValue = (2 * Math.PI).toFloat(),
        animationSpec = infiniteRepeatable(
            animation = tween(4000, easing = LinearEasing),
            repeatMode = RepeatMode.Restart
        ),
        label = "idlePhase"
    )

    // Fast rotation phase when actively listening
    val listeningPhase by infiniteTransition.animateFloat(
        initialValue = 0f,
        targetValue = (2 * Math.PI).toFloat(),
        animationSpec = infiniteRepeatable(
            animation = tween(1200, easing = LinearEasing),
            repeatMode = RepeatMode.Restart
        ),
        label = "listeningPhase"
    )

    val phase = if (isListening) listeningPhase else idlePhase

    // Spring scale transitions when entering/exiting listening state
    val targetScale = if (isListening) 1.25f else 1.0f
    val scaleAnim by animateFloatAsState(
        targetValue = targetScale,
        animationSpec = spring(dampingRatio = 0.6f, stiffness = Spring.StiffnessLow),
        label = "scaleAnim"
    )

    // Continuous breathing/pulsing scale when listening
    val breathingScale by if (isListening) {
        infiniteTransition.animateFloat(
            initialValue = 1.0f,
            targetValue = 1.15f,
            animationSpec = infiniteRepeatable(
                animation = tween(1000, easing = FastOutSlowInEasing),
                repeatMode = RepeatMode.Reverse
            ),
            label = "breathingScale"
        )
    } else {
        remember { mutableStateOf(1.0f) }
    }

    val pulseScale = scaleAnim * breathingScale

    // Dual soundwave ripples that fade out as they expand
    val ripple1Radius by if (isListening) {
        infiniteTransition.animateFloat(
            initialValue = 0.35f,
            targetValue = 0.95f,
            animationSpec = infiniteRepeatable(
                animation = tween(1500, easing = LinearEasing),
                repeatMode = RepeatMode.Restart
            ),
            label = "ripple1Radius"
        )
    } else {
        remember { mutableStateOf(0f) }
    }

    val ripple1Alpha by if (isListening) {
        infiniteTransition.animateFloat(
            initialValue = 0.5f,
            targetValue = 0.0f,
            animationSpec = infiniteRepeatable(
                animation = tween(1500, easing = LinearEasing),
                repeatMode = RepeatMode.Restart
            ),
            label = "ripple1Alpha"
        )
    } else {
        remember { mutableStateOf(0f) }
    }

    val ripple2Radius by if (isListening) {
        infiniteTransition.animateFloat(
            initialValue = 0.35f,
            targetValue = 0.95f,
            animationSpec = infiniteRepeatable(
                animation = tween(1500, delayMillis = 750, easing = LinearEasing),
                repeatMode = RepeatMode.Restart
            ),
            label = "ripple2Radius"
        )
    } else {
        remember { mutableStateOf(0f) }
    }

    val ripple2Alpha by if (isListening) {
        infiniteTransition.animateFloat(
            initialValue = 0.5f,
            targetValue = 0.0f,
            animationSpec = infiniteRepeatable(
                animation = tween(1500, delayMillis = 750, easing = LinearEasing),
                repeatMode = RepeatMode.Restart
            ),
            label = "ripple2Alpha"
        )
    } else {
        remember { mutableStateOf(0f) }
    }

    Canvas(modifier = modifier.graphicsLayer(compositingStrategy = CompositingStrategy.Offscreen)) {
        val width = size.width
        val height = size.height
        val center = Offset(width / 2, height / 2)
        val maxDimension = if (sphereRadius != null) sphereRadius.toPx() / 0.35f else minOf(width, height)
        val baseRadius = maxDimension * 0.35f

        drawIntoCanvas {
            if (isListening) {
                // Draw expanding soundwave ripples in background
                if (ripple1Radius > 0f) {
                    drawCircle(
                        brush = Brush.radialGradient(
                            colors = listOf(
                                OrbCyan.copy(alpha = ripple1Alpha),
                                OrbMagenta.copy(alpha = ripple1Alpha * 0.3f),
                                Color.Transparent
                            ),
                            center = center,
                            radius = maxDimension * ripple1Radius
                        ),
                        radius = maxDimension * ripple1Radius,
                        center = center
                    )
                }
                if (ripple2Radius > 0f) {
                    drawCircle(
                        brush = Brush.radialGradient(
                            colors = listOf(
                                OrbMagenta.copy(alpha = ripple2Alpha),
                                OrbCoral.copy(alpha = ripple2Alpha * 0.3f),
                                Color.Transparent
                            ),
                            center = center,
                            radius = maxDimension * ripple2Radius
                        ),
                        radius = maxDimension * ripple2Radius,
                        center = center
                    )
                }

                // Orb 1: Cyan/Blue
                val offset1 = Offset(
                    center.x + sin(phase) * baseRadius * 0.3f,
                    center.y + sin(phase + 1f) * baseRadius * 0.2f
                )
                drawCircle(
                    brush = Brush.radialGradient(
                        colors = listOf(OrbCyan, Color.Transparent),
                        center = offset1,
                        radius = baseRadius * pulseScale * 1.1f
                    ),
                    radius = baseRadius * pulseScale * 1.1f,
                    center = offset1,
                    blendMode = BlendMode.Screen
                )

                // Orb 2: Purple/Magenta
                val offset2 = Offset(
                    center.x - sin(phase + 2f) * baseRadius * 0.35f,
                    center.y - sin(phase + 3f) * baseRadius * 0.25f
                )
                drawCircle(
                    brush = Brush.radialGradient(
                        colors = listOf(OrbMagenta, Color.Transparent),
                        center = offset2,
                        radius = baseRadius * pulseScale * 1.2f
                    ),
                    radius = baseRadius * pulseScale * 1.2f,
                    center = offset2,
                    blendMode = BlendMode.Screen
                )

                // Orb 3: Coral/Red
                val offset3 = Offset(
                    center.x + sin(phase + 4f) * baseRadius * 0.2f,
                    center.y - sin(phase + 5f) * baseRadius * 0.3f
                )
                drawCircle(
                    brush = Brush.radialGradient(
                        colors = listOf(OrbCoral, Color.Transparent),
                        center = offset3,
                        radius = baseRadius * pulseScale * 1.0f
                    ),
                    radius = baseRadius * pulseScale * 1.0f,
                    center = offset3,
                    blendMode = BlendMode.Screen
                )
            } else {
                // Idle: single warm monochrome orb — quiet, on-brand.
                drawCircle(
                    brush = Brush.radialGradient(
                        colors = listOf(OrbWarmCore.copy(alpha = 0.75f), OrbWarm.copy(alpha = 0.12f), Color.Transparent),
                        center = center,
                        radius = baseRadius
                    ),
                    radius = baseRadius,
                    center = center
                )
            }
        }
    }
}