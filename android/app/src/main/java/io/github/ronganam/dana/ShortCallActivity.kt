package io.github.ronganam.dana

import android.media.MediaPlayer
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.sync.CallUploadSettings
import io.github.ronganam.dana.data.sync.CallUploadStore
import io.github.ronganam.dana.data.sync.CallUploads
import io.github.ronganam.dana.data.sync.CallUploads.readMeta
import io.github.ronganam.dana.data.sync.RecordingMeta
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.screens.calls.formatDuration
import io.github.ronganam.dana.ui.theme.DanaTheme
import io.github.ronganam.dana.ui.theme.ThemeSettings
import kotlinx.coroutines.delay

/**
 * "Short call — upload it?" as a bottom sheet over whatever is on screen.
 * Opened from the short-call notification: listen to it, then Upload, Skip,
 * or never ask again for this contact.
 */
class ShortCallActivity : ComponentActivity() {

    private var player: MediaPlayer? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        ThemeSettings.load(applicationContext)
        val meta = intent.readMeta()
        if (meta == null || !meta.file.exists()) {
            finish()
            return
        }
        setContent {
            DanaTheme {
                ShortCallSheet(
                    meta = meta,
                    playerFactory = ::playerFor,
                    onUpload = {
                        CallUploads.cancelNotification(this, meta.file.name)
                        CallUploads.enqueue(this, meta, replace = true)
                        finish()
                    },
                    onSkip = { always ->
                        CallUploads.cancelNotification(this, meta.file.name)
                        CallUploadStore.mark(this, meta.file.name, CallUploadStore.State.SKIPPED)
                        if (always) CallUploadSettings.addSkipped(this, meta.contact)
                        finish()
                    },
                    onClose = { finish() }
                )
            }
        }
    }

    private fun playerFor(meta: RecordingMeta): MediaPlayer? {
        player?.let { return it }
        return runCatching {
            MediaPlayer().apply { setDataSource(meta.file.absolutePath); prepare() }
        }.getOrNull()?.also { player = it }
    }

    override fun onDestroy() {
        player?.release()
        player = null
        super.onDestroy()
    }
}

@Composable
private fun ShortCallSheet(
    meta: RecordingMeta,
    playerFactory: (RecordingMeta) -> MediaPlayer?,
    onUpload: () -> Unit,
    onSkip: (always: Boolean) -> Unit,
    onClose: () -> Unit
) {
    var playing by remember { mutableStateOf(false) }
    var position by remember { mutableIntStateOf(0) }
    val total = meta.durationSeconds.coerceAtLeast(1)

    LaunchedEffect(playing) {
        while (playing) {
            val p = playerFactory(meta) ?: break
            position = p.currentPosition / 1000
            if (!p.isPlaying) { playing = false; position = 0 }
            delay(250)
        }
    }

    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black.copy(alpha = 0.45f))
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClose)
            .statusBarsPadding(),
        contentAlignment = Alignment.BottomCenter
    ) {
        // Capped width (centred on the unfolded screen), and scrollable so it
        // still fits a landscape cover / split-screen window.
        Column(
            Modifier
                .widthIn(max = 560.dp)
                .fillMaxWidth()
                .clip(RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp))
                .background(MaterialTheme.colorScheme.background)
                .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) {}
                .verticalScroll(rememberScrollState())
                .navigationBarsPadding()
                .padding(horizontal = 24.dp, vertical = 20.dp)
        ) {
            MonoLabel(
                buildString {
                    append("Short call · ").append(formatDuration(meta.durationSeconds))
                    meta.direction?.let { append(" · ").append(it) }
                },
                color = MaterialTheme.colorScheme.primary
            )
            Spacer(Modifier.height(6.dp))
            Text(
                meta.contact,
                style = MaterialTheme.typography.headlineMedium.copy(textDirection = TextDirection.Content),
                color = MaterialTheme.colorScheme.onBackground
            )
            Spacer(Modifier.height(4.dp))
            Text(
                "Under your auto-upload threshold. Send it to Dana anyway?",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Spacer(Modifier.height(18.dp))
            Rule()
            Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = {
                    val p = playerFactory(meta) ?: return@IconButton
                    if (p.isPlaying) { p.pause(); playing = false } else { p.start(); playing = true }
                }) {
                    Icon(
                        if (playing) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                        contentDescription = if (playing) "Pause" else "Play",
                        tint = MaterialTheme.colorScheme.primary
                    )
                }
                LinearProgressIndicator(
                    progress = { (position.toFloat() / total).coerceIn(0f, 1f) },
                    modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
                    trackColor = MaterialTheme.colorScheme.outlineVariant
                )
                MonoLabel("${formatDuration(position)} / ${formatDuration(meta.durationSeconds)}")
            }
            Rule()
            Spacer(Modifier.height(18.dp))
            Button(onClick = onUpload, modifier = Modifier.fillMaxWidth().height(48.dp)) { Text("Upload") }
            Spacer(Modifier.height(8.dp))
            OutlinedButton(onClick = { onSkip(false) }, modifier = Modifier.fillMaxWidth().height(48.dp)) { Text("Skip") }
            TextButton(onClick = { onSkip(true) }, modifier = Modifier.fillMaxWidth()) {
                Text("Never upload calls with ${meta.contact}", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
