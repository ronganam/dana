package io.github.ronganam.dana.ui.shell

import io.github.ronganam.dana.ui.components.accentFill
import io.github.ronganam.dana.ui.components.pulseRing
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
import androidx.compose.material.icons.filled.Add
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import io.github.ronganam.dana.ui.theme.circuitColors
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
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.screens.VoiceWaveformIndicator
import io.github.ronganam.dana.ui.components.circuit.AskDanaButton
import io.github.ronganam.dana.ui.components.circuit.ConversationButton
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import coil.compose.AsyncImage

/**
 * The conversation's docked ask bar (it comes with the open thread, and sits
 * under the conversation pane unfolded): "+" (an image) in a tonal field,
 * then the talk button. Empty, the button is "Hold to talk" (hold and let go
 * to send what you said; a tap listens until you stop, or Dana hears the end);
 * with text it sends; while Dana answers it stops her. Beside the narrow
 * conversation pane it is a round mic. Unfolded, "On screen · …" above the
 * field names what goes with the next message (× drops it).
 */
@Composable
fun AskBar(viewModel: MainViewModel, modifier: Modifier = Modifier) {
    val shell = viewModel.shellController
    val layout = LocalShellLayout.current
    val sheet = layout != ShellLayout.FULL
    val screen by shell.screen.collectAsState()
    val isRecording by viewModel.chatController.isRecording.collectAsState()
    val isStreaming by viewModel.chatController.isStreaming.collectAsState()
    val isCallActive by viewModel.callController.isCallActive.collectAsState()
    val pendingImage by viewModel.chatController.pendingImageUri.collectAsState()
    val serverStatus by viewModel.serverStatus.collectAsState()
    val draft = shell.askDraft
    val c = circuitColors

    val mic = rememberVoiceToggle(viewModel)
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

    Column(
        modifier
            .fillMaxWidth()
            .padding(start = 16.dp, end = 16.dp, top = 6.dp, bottom = 12.dp)
    ) {
        // Unfolded: what Dana sees with the next message (on the cover it is at the top of the sheet).
        val onScreen = screen
        if (layout != ShellLayout.STACKED && onScreen != null) {
            OnScreenLine(onScreen, onDrop = { shell.dropScreen() }, modifier = Modifier.padding(bottom = 6.dp))
        }

        pendingImage?.let { uri ->
            Row(Modifier.padding(bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                AsyncImage(
                    model = uri,
                    contentDescription = "Image to send",
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.size(56.dp).clip(RoundedCornerShape(12.dp))
                )
                IconButton(onClick = { viewModel.chatController.clearPendingImage() }) {
                    Icon(Icons.Filled.Close, contentDescription = "Remove the image", tint = c.inkSoft)
                }
            }
        }

        val listening = isRecording || isCallActive
        val hasText = draft.text.isNotBlank() || pendingImage != null
        Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            BasicTextField(
                value = draft,
                onValueChange = { shell.askDraft = it },
                textStyle = MaterialTheme.typography.bodyLarge.copy(color = c.ink, textDirection = TextDirection.Content),
                cursorBrush = SolidColor(c.accent),
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
                    Row(
                        Modifier
                            .heightIn(min = 56.dp)
                            .clip(RoundedCornerShape(28.dp))
                            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
                            .padding(start = 4.dp, end = 16.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        IconButton(
                            onClick = { imagePicker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) },
                            modifier = Modifier.size(48.dp)
                        ) {
                            Icon(Icons.Filled.Add, contentDescription = "Attach an image", tint = c.inkSoft)
                        }
                        Box(Modifier.weight(1f).padding(vertical = 14.dp), contentAlignment = Alignment.CenterStart) {
                            if (draft.text.isEmpty()) {
                                if (listening) Row(verticalAlignment = Alignment.CenterVertically) {
                                    VoiceWaveformIndicator()
                                    Spacer(Modifier.width(8.dp))
                                    Text("Listening…", color = c.accent, style = MaterialTheme.typography.bodyLarge, maxLines = 1)
                                } else Text(
                                    "Message Dana",
                                    color = c.mute,
                                    style = MaterialTheme.typography.bodyLarge,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis
                                )
                            }
                            // Full width, so a Hebrew line starts at the right.
                            Box(Modifier.fillMaxWidth()) { inner() }
                        }
                    }
                }
            )
            TalkButton(
                round = layout == ShellLayout.FULL,
                listening = listening,
                hasText = hasText,
                streaming = isStreaming,
                offline = serverStatus.isOffline,
                onToggleMic = mic,
                onSend = { send() },
                onStop = { viewModel.chatController.stopAssistant() }
            )
        }
    }
}

/**
 * The ask bar's filled button: "Hold to talk" (press listens; a hold ends
 * when you let go, a tap keeps listening until tapped again or Dana hears the
 * end), Send with text, Stop while listening or while Dana answers. [round]:
 * just the icon (beside the narrow conversation pane).
 */
@Composable
private fun TalkButton(
    round: Boolean,
    listening: Boolean,
    hasText: Boolean,
    streaming: Boolean,
    offline: Boolean,
    onToggleMic: () -> Unit,
    onSend: () -> Unit,
    onStop: () -> Unit
) {
    val c = circuitColors
    val toggle by rememberUpdatedState(onToggleMic)
    val send by rememberUpdatedState(onSend)
    val stop by rememberUpdatedState(onStop)
    val isListening by rememberUpdatedState(listening)
    val withText by rememberUpdatedState(hasText)
    val answering by rememberUpdatedState(streaming)
    val (icon, label) = when {
        listening -> Icons.Filled.Stop to "Stop"
        hasText -> Icons.Filled.ArrowUpward to "Send"
        streaming -> Icons.Filled.Stop to "Stop"
        else -> Icons.Filled.Mic to "Hold to talk"
    }
    val showLabel = !round && !hasText
    Row(
        Modifier
            .height(56.dp)
            .then(if (showLabel) Modifier.widthIn(min = 56.dp) else Modifier.width(56.dp))
            // Listening: a gentle ring leaves the button until it stops.
            .pulseRing(listening, c.accent)
            .clip(RoundedCornerShape(28.dp))
            .background(c.accentFilled)
            // Dimmed while the server is unreachable (sending refuses and re-checks).
            .alpha(if (offline && !listening) 0.5f else 1f)
            .semantics { role = Role.Button; contentDescription = label }
            // Keyed on nothing: listening starts mid-press, and the hold must survive it.
            .pointerInput(Unit) {
                detectTapGestures(onPress = {
                    when {
                        isListening -> if (tryAwaitRelease()) toggle()
                        withText -> if (tryAwaitRelease()) send()
                        answering -> if (tryAwaitRelease()) stop()
                        else -> {
                            val down = System.currentTimeMillis()
                            toggle()
                            tryAwaitRelease()
                            // Held: letting go ends it (a tap keeps listening).
                            if (System.currentTimeMillis() - down > HOLD_MS && isListening) toggle()
                        }
                    }
                })
            }
            .padding(horizontal = if (showLabel) 20.dp else 0.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center
    ) {
        Icon(icon, contentDescription = null, tint = c.onAccentFilled, modifier = Modifier.size(22.dp))
        if (showLabel) {
            Spacer(Modifier.width(8.dp))
            Text(label, style = MaterialTheme.typography.titleMedium, color = c.onAccentFilled, maxLines = 1)
        }
    }
}

/** How long a press must last to count as a hold (let go = stop listening). */
private const val HOLD_MS = 500L

/**
 * Push-to-talk, the ask bar's and the header's: ends a call or a recording
 * that is on, else asks for the microphone if needed and starts listening,
 * opening the thread sheet where the thread is a sheet.
 */
@Composable
internal fun rememberVoiceToggle(viewModel: MainViewModel): () -> Unit {
    val shell = viewModel.shellController
    val sheet = LocalShellLayout.current != ShellLayout.FULL
    val context = LocalContext.current
    val isRecording by viewModel.chatController.isRecording.collectAsState()
    val isCallActive by viewModel.callController.isCallActive.collectAsState()
    val micPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            viewModel.chatController.startVoiceRecording()
            if (sheet) shell.openThread()
        }
    }
    return {
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
}

/**
 * Ask Dana from a place's header (the top-right slot on the cover, the top
 * of the rail unfolded): starts listening like the ask bar's mic, and stops
 * it while listening.
 */
@Composable
fun HeaderAsk(viewModel: MainViewModel, modifier: Modifier = Modifier, conversation: Boolean = false, vertical: Boolean = false) {
    val toggle = rememberVoiceToggle(viewModel)
    val isRecording by viewModel.chatController.isRecording.collectAsState()
    val isCallActive by viewModel.callController.isCallActive.collectAsState()
    val listening = isRecording || isCallActive
    val ask = @Composable {
        AskDanaButton(
            onClick = toggle,
            modifier = Modifier.pulseRing(listening, MaterialTheme.colorScheme.primary),
            label = if (listening) "Stop listening" else "Ask Dana"
        )
    }
    // No place docks the ask bar, so the thread opens from here too (the rail: under the mic).
    if (vertical) Column(modifier, horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        ask()
        if (conversation) ConversationButton(onClick = { viewModel.shellController.openThread() })
    } else Row(modifier, verticalAlignment = Alignment.CenterVertically) {
        if (conversation) ConversationButton(onClick = { viewModel.shellController.openThread() })
        ask()
    }
}
