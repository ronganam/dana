package io.github.ronganam.dana.ui.screens.calls

import io.github.ronganam.dana.ui.components.EmptyState
import androidx.compose.material.icons.filled.Phone
import io.github.ronganam.dana.ui.components.ScreenTopBar
import io.github.ronganam.dana.ui.theme.Danger
import io.github.ronganam.dana.ui.theme.Success
import io.github.ronganam.dana.ui.theme.Warning
import io.github.ronganam.dana.ui.theme.Info
import io.github.ronganam.dana.ui.theme.CategoryPalette
import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.OpenableColumns
import android.util.Log
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.border
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.PhoneInTalk
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import io.github.ronganam.dana.data.models.PhoneCall
import io.github.ronganam.dana.ui.components.GlassCard
import io.github.ronganam.dana.ui.theme.isWideLayout
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.PendingRecordingFile
import io.github.ronganam.dana.ui.viewmodels.UploadFormState

@Composable
fun UploadOptionsSheet(
    onPickFile: () -> Unit,
    onSelectRecording: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 32.dp)
    ) {
        Text(
            text = "Upload Audio",
            style = MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(horizontal = 24.dp, vertical = 16.dp)
        )

        Surface(
            onClick = onPickFile,
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 4.dp),
            shape = RoundedCornerShape(8.dp),
            color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.04f)
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    modifier = Modifier
                        .size(48.dp)
                        .clip(RoundedCornerShape(6.dp))
                        .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        Icons.Filled.MusicNote,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.size(24.dp)
                    )
                }
                Spacer(Modifier.width(16.dp))
                Column {
                    Text(
                        text = "Pick audio file",
                        style = MaterialTheme.typography.bodyLarge,
                        fontWeight = FontWeight.Medium
                    )
                    Text(
                        text = "Choose from device storage",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f)
                    )
                }
            }
        }

        Surface(
            onClick = onSelectRecording,
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 4.dp),
            shape = RoundedCornerShape(8.dp),
            color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.04f)
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    modifier = Modifier
                        .size(48.dp)
                        .clip(RoundedCornerShape(6.dp))
                        .background(MaterialTheme.colorScheme.tertiary.copy(alpha = 0.12f)),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        Icons.Filled.PhoneInTalk,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.tertiary,
                        modifier = Modifier.size(24.dp)
                    )
                }
                Spacer(Modifier.width(16.dp))
                Column {
                    Text(
                        text = "Select from recent recordings",
                        style = MaterialTheme.typography.bodyLarge,
                        fontWeight = FontWeight.Medium
                    )
                    Text(
                        text = "Choose a detected call recording",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f)
                    )
                }
            }
        }
    }
}

@Composable
fun UploadFormSheet(
    formState: UploadFormState,
    onCallerChange: (String) -> Unit,
    onReceiverChange: (String) -> Unit,
    onTimestampChange: (String) -> Unit,
    onInstructionsChange: (String) -> Unit,
    onSubmit: () -> Unit,
    onDismiss: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 32.dp)
    ) {
        Text(
            text = "Upload Details",
            style = MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(horizontal = 24.dp, vertical = 16.dp)
        )

        // File name
        GlassCard(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
            cornerRadius = 8.dp
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(12.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    Icons.Filled.MusicNote,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.size(20.dp)
                )
                Spacer(Modifier.width(10.dp))
                Text(
                    text = formState.displayFileName,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.7f),
                    modifier = Modifier.weight(1f)
                )
            }
        }

        Spacer(Modifier.height(16.dp))

        // Caller field
        OutlinedTextField(
            value = formState.caller,
            onValueChange = onCallerChange,
            label = { Text("Caller") },
            placeholder = { Text("Name of the caller") },
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
            shape = RoundedCornerShape(8.dp),
            singleLine = true,
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = MaterialTheme.colorScheme.primary,
                unfocusedBorderColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.12f)
            )
        )

        Spacer(Modifier.height(12.dp))

        // Receiver field
        OutlinedTextField(
            value = formState.receiver,
            onValueChange = onReceiverChange,
            label = { Text("Receiver") },
            placeholder = { Text("Who received the call") },
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
            shape = RoundedCornerShape(8.dp),
            singleLine = true,
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = MaterialTheme.colorScheme.primary,
                unfocusedBorderColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.12f)
            )
        )

        Spacer(Modifier.height(12.dp))

        // Timestamp field
        OutlinedTextField(
            value = formState.timestamp,
            onValueChange = onTimestampChange,
            label = { Text("Call Timestamp") },
            placeholder = { Text("YYYY-MM-DDTHH:mm:ss") },
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
            shape = RoundedCornerShape(8.dp),
            singleLine = true,
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = MaterialTheme.colorScheme.primary,
                unfocusedBorderColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.12f)
            )
        )

        Spacer(Modifier.height(12.dp))

        // Custom instructions field
        OutlinedTextField(
            value = formState.instructions,
            onValueChange = onInstructionsChange,
            label = { Text("Custom Instructions (optional)") },
            placeholder = { Text("e.g. focus on action items, summarize in Hebrew...") },
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
            shape = RoundedCornerShape(8.dp),
            minLines = 2,
            maxLines = 4,
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = MaterialTheme.colorScheme.primary,
                unfocusedBorderColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.12f)
            )
        )

        Spacer(Modifier.height(24.dp))

        // Action buttons
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            OutlinedButton(
                onClick = onDismiss,
                modifier = Modifier.weight(1f),
                shape = RoundedCornerShape(8.dp),
                enabled = !formState.isSubmitting
            ) {
                Text("Cancel")
            }
            Button(
                onClick = onSubmit,
                modifier = Modifier.weight(1f),
                shape = RoundedCornerShape(8.dp),
                enabled = !formState.isSubmitting
            ) {
                if (formState.isSubmitting) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(20.dp),
                        strokeWidth = 2.dp,
                        color = MaterialTheme.colorScheme.onPrimary
                    )
                    Spacer(Modifier.width(8.dp))
                }
                Text("Upload")
            }
        }
    }
}

@Composable
fun RecordingSelectionSheet(
    recordings: List<PendingRecordingFile>,
    isScanning: Boolean,
    onSelect: (PendingRecordingFile) -> Unit
) {
    val handler = remember { Handler(Looper.getMainLooper()) }
    val mediaPlayer = remember { mutableStateOf<MediaPlayer?>(null) }
    val currentlyPlayingPath = remember { mutableStateOf<String?>(null) }
    val isPlaying = remember { mutableStateOf(false) }
    val currentPosition = remember { mutableStateOf(0f) }
    val currentDuration = remember { mutableStateOf(0) }

    val progressRunnable = remember {
        object : Runnable {
            override fun run() {
                mediaPlayer.value?.let { mp ->
                    if (mp.isPlaying) {
                        currentPosition.value = mp.currentPosition.toFloat()
                        handler.postDelayed(this, 250)
                    }
                }
            }
        }
    }

    fun playFile(path: String) {
        if (currentlyPlayingPath.value == path && isPlaying.value) {
            mediaPlayer.value?.pause()
            isPlaying.value = false
            handler.removeCallbacks(progressRunnable)
            return
        }
        mediaPlayer.value?.apply {
            stop()
            release()
        }
        mediaPlayer.value = null
        currentlyPlayingPath.value = null

        try {
            val mp = MediaPlayer().apply {
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .build()
                )
                setDataSource(path)
                prepare()
                start()
                currentDuration.value = duration
                currentPosition.value = 0f
                setOnCompletionListener {
                    isPlaying.value = false
                    currentPosition.value = 0f
                    handler.removeCallbacks(progressRunnable)
                }
            }
            mediaPlayer.value = mp
            currentlyPlayingPath.value = path
            isPlaying.value = true
            handler.post(progressRunnable)
        } catch (e: Exception) {
            Log.e("RecordingSelectionSheet", "Error playing file", e)
        }
    }

    fun stopPlayback() {
        mediaPlayer.value?.apply {
            stop()
            release()
        }
        mediaPlayer.value = null
        currentlyPlayingPath.value = null
        isPlaying.value = false
        currentPosition.value = 0f
        handler.removeCallbacks(progressRunnable)
    }

    DisposableEffect(Unit) {
        onDispose {
            mediaPlayer.value?.apply {
                stop()
                release()
            }
            handler.removeCallbacks(progressRunnable)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 32.dp)
    ) {
        Text(
            text = "Select Recording",
            style = MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(horizontal = 24.dp, vertical = 16.dp)
        )

        if (recordings.isEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(120.dp),
                contentAlignment = Alignment.Center
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = if (isScanning) "Scanning recordings..." else "No recordings found",
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f)
                    )
                    if (isScanning) {
                        Spacer(Modifier.height(12.dp))
                        CircularProgressIndicator(modifier = Modifier.size(24.dp), strokeWidth = 2.dp)
                    }
                }
            }
        } else {
            LazyColumn(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(max = 460.dp),
                contentPadding = PaddingValues(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                items(recordings) { recording ->
                    val isThisPlaying = currentlyPlayingPath.value == recording.file.absolutePath

                    Surface(
                        shape = RoundedCornerShape(8.dp),
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.04f)
                    ) {
                        Column {
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(14.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                IconButton(
                                    onClick = { playFile(recording.file.absolutePath) },
                                    modifier = Modifier
                                        .size(40.dp)
                                        .clip(CircleShape)
                                        .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.12f))
                                ) {
                                    Icon(
                                        painter = painterResource(
                                            id = if (isThisPlaying && isPlaying.value)
                                                android.R.drawable.ic_media_pause
                                            else
                                                android.R.drawable.ic_media_play
                                        ),
                                        contentDescription = if (isThisPlaying) "Pause" else "Play",
                                        tint = MaterialTheme.colorScheme.primary,
                                        modifier = Modifier.size(20.dp)
                                    )
                                }

                                Spacer(Modifier.width(12.dp))

                                Column(modifier = Modifier.weight(1f)) {
                                    Text(
                                        text = recording.contactName,
                                        style = MaterialTheme.typography.bodyLarge,
                                        fontWeight = FontWeight.Medium
                                    )
                                    Row {
                                        Text(
                                            text = formatCallTimestamp(recording.timestamp),
                                            style = MaterialTheme.typography.bodySmall,
                                            color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f)
                                        )
                                        if (recording.durationSeconds > 0) {
                                            Text(
                                                text = " · ${formatDuration(recording.durationSeconds)}",
                                                style = MaterialTheme.typography.bodySmall,
                                                color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.4f)
                                            )
                                        }
                                    }
                                }

                                IconButton(
                                    onClick = {
                                        stopPlayback()
                                        onSelect(recording)
                                    },
                                    modifier = Modifier
                                        .size(36.dp)
                                        .clip(CircleShape)
                                        .background(MaterialTheme.colorScheme.primary)
                                ) {
                                    Icon(
                                        Icons.AutoMirrored.Filled.KeyboardArrowRight,
                                        contentDescription = "Select",
                                        tint = MaterialTheme.colorScheme.onPrimary,
                                        modifier = Modifier.size(22.dp)
                                    )
                                }
                            }

                            // Seek bar when this file is playing
                            if (isThisPlaying && currentDuration.value > 0) {
                                Row(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(start = 14.dp, end = 14.dp, bottom = 8.dp),
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Text(
                                        text = formatMsec(currentPosition.value.toInt()),
                                        fontSize = 10.sp,
                                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f),
                                        modifier = Modifier.width(32.dp)
                                    )
                                    Slider(
                                        value = currentPosition.value,
                                        onValueChange = {
                                            currentPosition.value = it
                                            mediaPlayer.value?.seekTo(it.toInt())
                                        },
                                        valueRange = 0f..currentDuration.value.toFloat().coerceAtLeast(1f),
                                        colors = SliderDefaults.colors(
                                            thumbColor = MaterialTheme.colorScheme.primary,
                                            activeTrackColor = MaterialTheme.colorScheme.primary
                                        ),
                                        modifier = Modifier
                                            .weight(1f)
                                            .height(18.dp)
                                    )
                                    Text(
                                        text = formatMsec(currentDuration.value),
                                        fontSize = 10.sp,
                                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f),
                                        modifier = Modifier.width(32.dp)
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
