package io.github.ronganam.dana.ui.shell

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isShiftPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.screens.VoiceWaveformIndicator
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import coil.compose.AsyncImage

/**
 * The ask bar, always on screen: what you type or say goes to Dana's thread.
 * On the cover screen and unfolded portrait a one-line peek of the last
 * message sits above it and opens / closes the thread sheet; sending or
 * speaking opens the sheet too. Unfolded, "On screen · …" above the field
 * names what goes with the next message (× drops it). The button is the mic while the field is
 * empty, send once there is text, stop while Dana is answering.
 */
@Composable
fun AskBar(viewModel: MainViewModel, modifier: Modifier = Modifier) {
    val shell = viewModel.shellController
    val layout = LocalShellLayout.current
    val sheet = layout != ShellLayout.FULL
    val threadOpen by shell.threadOpen.collectAsState()
    val screen by shell.screen.collectAsState()
    val messages by viewModel.chatController.chatMessages.collectAsState()
    val isRecording by viewModel.chatController.isRecording.collectAsState()
    val isStreaming by viewModel.chatController.isStreaming.collectAsState()
    val isCallActive by viewModel.callController.isCallActive.collectAsState()
    val pendingImage by viewModel.chatController.pendingImageUri.collectAsState()
    val serverStatus by viewModel.serverStatus.collectAsState()
    val context = LocalContext.current
    val draft = shell.askDraft

    val micPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            viewModel.chatController.startVoiceRecording()
            if (sheet) shell.openThread()
        }
    }
    val imagePicker = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) viewModel.chatController.setPendingImage(uri)
    }

    fun send() {
        val text = draft.text.trim()
        if (text.isEmpty() && pendingImage == null) return
        // Open first: on the cover that pins what is open, so the message carries it.
        if (sheet) shell.openThread()
        // Keep the text when the server is unreachable so it can be sent again.
        if (viewModel.chatController.sendChatMessage(text)) shell.askDraft = TextFieldValue("")
    }

    fun mic() {
        when {
            isCallActive -> viewModel.callController.endCall()
            isRecording -> viewModel.chatController.stopVoiceRecording()
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED ->
                micPermission.launch(Manifest.permission.RECORD_AUDIO)
            else -> {
                viewModel.chatController.startVoiceRecording()
                if (sheet) shell.openThread()
            }
        }
    }

    Column(
        modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .edgeRule(top = true)
            .padding(start = 14.dp, end = 14.dp, top = 4.dp, bottom = 10.dp)
    ) {
        if (sheet) {
            val last = messages.lastOrNull { !it.isThinking && it.toolCall == null && it.message.isNotBlank() }
            val inThread by viewModel.chatController.inThread.collectAsState()
            Row(
                Modifier.fillMaxWidth().clickable { shell.toggleThread() }.heightIn(min = 36.dp).padding(horizontal = 2.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                MonoLabel(if (inThread) "Thread" else "Conversation", modifier = Modifier.padding(end = 8.dp))
                Text(
                    last?.message?.lineSequence()?.firstOrNull { it.isNotBlank() }?.trim() ?: if (inThread) "empty" else "nothing yet",
                    style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Left,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f)
                )
                MonoLabel(if (threadOpen) "close ↓" else "open ↑", modifier = Modifier.padding(start = 10.dp))
            }
        } else {
            Spacer(Modifier.height(6.dp))
        }
        // Unfolded: what Dana sees with the next message (on the cover it is at the top of the sheet).
        val onScreen = screen
        if (layout != ShellLayout.STACKED && onScreen != null) {
            OnScreenLine(onScreen, onDrop = { shell.dropScreen() }, modifier = Modifier.padding(bottom = 4.dp))
        }

        pendingImage?.let { uri ->
            Row(Modifier.padding(bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                AsyncImage(
                    model = uri,
                    contentDescription = "Image to send",
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.size(56.dp).clip(RoundedCornerShape(8.dp))
                )
                IconButton(onClick = { viewModel.chatController.clearPendingImage() }) {
                    Icon(Icons.Filled.Close, contentDescription = "Remove the image", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }

        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            IconButton(
                onClick = { imagePicker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) },
                modifier = Modifier.size(40.dp)
            ) {
                Icon(Icons.Outlined.Image, contentDescription = "Attach an image", tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            val fieldShape = RoundedCornerShape(14.dp)
            BasicTextField(
                value = draft,
                onValueChange = { shell.askDraft = it },
                textStyle = MaterialTheme.typography.bodyLarge.copy(
                    color = MaterialTheme.colorScheme.onSurface,
                    textDirection = TextDirection.Content,
                    fontSize = 15.sp
                ),
                cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                maxLines = 4,
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { send() }),
                // A hardware Enter sends too (Shift+Enter keeps a new line).
                modifier = Modifier.weight(1f).onPreviewKeyEvent { e ->
                    if (e.key == Key.Enter && !e.isShiftPressed) {
                        if (e.type == KeyEventType.KeyDown) send()
                        true
                    } else false
                },
                decorationBox = { inner ->
                    Box(
                        Modifier
                            .heightIn(min = 44.dp)
                            .clip(fieldShape)
                            .background(MaterialTheme.colorScheme.background)
                            .border(1.dp, MaterialTheme.colorScheme.outlineVariant, fieldShape)
                            .padding(horizontal = 14.dp, vertical = 11.dp),
                        contentAlignment = Alignment.CenterStart
                    ) {
                        if (draft.text.isEmpty()) {
                            if (isRecording || isCallActive) Row(verticalAlignment = Alignment.CenterVertically) {
                                VoiceWaveformIndicator()
                                Spacer(Modifier.width(8.dp))
                                Text("Listening…", color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.bodyLarge)
                            } else Text(
                                "Ask or tell Dana…",
                                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
                                style = MaterialTheme.typography.bodyLarge
                            )
                        }
                        // Full width, so a Hebrew line starts at the right.
                        Box(Modifier.fillMaxWidth()) { inner() }
                    }
                }
            )
            val hasText = draft.text.isNotBlank() || pendingImage != null
            val (icon, label) = when {
                isRecording || isCallActive -> Icons.Filled.Stop to "Stop listening"
                hasText -> Icons.Filled.ArrowUpward to "Send"
                isStreaming -> Icons.Filled.Stop to "Stop Dana"
                else -> Icons.Filled.Mic to "Speak"
            }
            Box(
                Modifier
                    .size(44.dp)
                    .clip(CircleShape)
                    .background(MaterialTheme.colorScheme.primary)
                    // Dimmed while the server is unreachable (sending refuses and re-checks).
                    .alpha(if (serverStatus.isOffline && !isRecording) 0.5f else 1f)
                    .clickable {
                        when {
                            isRecording || isCallActive -> mic()
                            hasText -> send()
                            isStreaming -> viewModel.chatController.stopAssistant()
                            else -> mic()
                        }
                    },
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    icon,
                    contentDescription = label,
                    tint = MaterialTheme.colorScheme.onPrimary,
                    modifier = Modifier.size(20.dp)
                )
            }
        }
    }
}
