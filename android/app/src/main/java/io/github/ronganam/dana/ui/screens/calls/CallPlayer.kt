package io.github.ronganam.dana.ui.screens.calls

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.PhoneCall
import io.github.ronganam.dana.ui.components.MetaLabel

/** Deterministic pseudo-waveform (the server exposes no peaks), so every call has its own stable "signature". */
private fun waveformPeaks(seed: String, count: Int = 96): FloatArray {
    var s = seed.hashCode().toLong() and 0xFFFFFFFFL
    fun next(): Float {
        s = (s * 1664525L + 1013904223L) and 0xFFFFFFFFL
        return (s ushr 8).toFloat() / (1 shl 24).toFloat()
    }
    val raw = FloatArray(count) { next() }
    return FloatArray(count) { i ->
        val envelope = 0.55f + 0.45f * kotlin.math.abs(kotlin.math.sin(i * 0.19f + (seed.length % 7)))
        val smooth = (raw[i] + raw[(i + 1) % count] * 0.5f + raw[(i + count - 1) % count] * 0.5f) / 2f
        (0.18f + 0.82f * smooth * envelope).coerceIn(0.12f, 1f)
    }
}

/** Tap or scrub the waveform to start playback or seek. Played bars are vermilion, the rest hairline. */
@Composable
internal fun Waveform(
    seed: String,
    progress: Float,
    onFraction: (Float) -> Unit,
    modifier: Modifier = Modifier
) {
    val peaks = remember(seed) { waveformPeaks(seed) }
    val played = MaterialTheme.colorScheme.primary
    val rest = MaterialTheme.colorScheme.outline.copy(alpha = 0.55f)
    Canvas(
        modifier = modifier
            .semantics { contentDescription = "Audio waveform. Tap or drag to seek." }
            .pointerInput(Unit) { detectTapGestures { onFraction((it.x / size.width).coerceIn(0f, 1f)) } }
            .pointerInput(Unit) {
                detectHorizontalDragGestures { change, _ ->
                    onFraction((change.position.x / size.width).coerceIn(0f, 1f))
                }
            }
    ) {
        val barW = 3.dp.toPx()
        val gap = 2.dp.toPx()
        val count = ((size.width + gap) / (barW + gap)).toInt().coerceAtLeast(1)
        val cr = CornerRadius(barW / 2f)
        for (i in 0 until count) {
            val amp = peaks[(i * peaks.size / count).coerceIn(0, peaks.size - 1)]
            val h = (size.height * amp).coerceAtLeast(4.dp.toPx())
            val x = i * (barW + gap)
            val isPlayed = (i + 0.5f) / count <= progress
            drawRoundRect(
                color = if (isPlayed) played else rest,
                topLeft = Offset(x, (size.height - h) / 2f),
                size = Size(barW, h),
                cornerRadius = cr
            )
        }
    }
}

/** Play/pause + waveform + mono timecode for one call. */
@Composable
internal fun CallPlayer(call: PhoneCall, player: CallAudioPlayer, modifier: Modifier = Modifier) {
    if (!call.audioAvailable) {
        androidx.compose.material3.Text(
            if (call.audioDeleted) "The recording was removed after 90 days. Transcript and summary are kept."
            else "No recording on the server for this call.",
            style = MaterialTheme.typography.bodyMedium,
            fontStyle = androidx.compose.ui.text.font.FontStyle.Italic,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = modifier.padding(vertical = 8.dp)
        )
        return
    }
    val isThis = player.currentPlayingCallId.value == call.id
    val playing = isThis && player.isPlaying.value
    val totalMs = if (isThis && player.duration.intValue > 0) player.duration.intValue else call.durationSeconds * 1000
    val posMs = if (isThis) player.currentPosition.floatValue.toInt() else 0
    val progress = if (totalMs > 0) (posMs.toFloat() / totalMs).coerceIn(0f, 1f) else 0f

    Row(modifier = modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        IconButton(
            onClick = { player.togglePlay(call.id) },
            modifier = Modifier.size(52.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primary)
        ) {
            Icon(
                if (playing) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                contentDescription = if (playing) "Pause" else "Play call audio",
                tint = MaterialTheme.colorScheme.onPrimary,
                modifier = Modifier.size(26.dp)
            )
        }
        Spacer(Modifier.width(14.dp))
        Column(modifier = Modifier.weight(1f)) {
            Waveform(
                seed = call.id,
                progress = progress,
                onFraction = { f ->
                    if (isThis && totalMs > 0) player.seekTo(f * totalMs) else player.togglePlay(call.id)
                },
                modifier = Modifier.fillMaxWidth().height(44.dp)
            )
            Spacer(Modifier.height(4.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                MetaLabel(formatMsec(posMs))
                MetaLabel(if (totalMs > 0) formatMsec(totalMs) else "--:--")
            }
        }
    }
}
