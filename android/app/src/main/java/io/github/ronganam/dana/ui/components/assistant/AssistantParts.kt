package io.github.ronganam.dana.ui.components.assistant

import android.content.Context
import android.widget.Toast
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.IntrinsicSize.Min
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.OpenInFull
import androidx.compose.material.icons.filled.RestartAlt
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.serverImageRequest
import io.github.ronganam.dana.ui.screens.MarkdownText
import io.github.ronganam.dana.ui.screens.TypingIndicator
import io.github.ronganam.dana.ui.components.ReasoningBlock
import io.github.ronganam.dana.ui.components.ThinkingCaption
import io.github.ronganam.dana.ui.theme.DanaSerif
import coil.compose.AsyncImage
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Header: live state kicker on the left, four sheet actions on the right. */
@Composable
internal fun AssistantHeader(
    stateLabel: String,
    live: Boolean,
    /** Empty in the shared thread (no session to name). */
    sessionId: String,
    /** Null in the shared thread: there is one conversation, nothing to start. */
    onNewSession: (() -> Unit)?,
    onMinimize: () -> Unit,
    onMoveToApp: (() -> Unit)?,
    onDismiss: () -> Unit,
    onCopySession: () -> Unit,
    modifier: Modifier = Modifier,
    /** "New topic" in the shared thread (Dana forgets it so far); null while Dana remembers nothing. */
    onNewTopic: (() -> Unit)? = null,
    newTopicEnabled: Boolean = true
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(start = 20.dp, end = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                // Pulse through the layer's alpha: a redraw per frame, no recomposition.
                val pulse = if (live) {
                    rememberInfiniteTransition(label = "dot").animateFloat(
                        initialValue = 0.35f,
                        targetValue = 1f,
                        animationSpec = infiniteRepeatable(tween(700), RepeatMode.Reverse),
                        label = "dot_a"
                    )
                } else null
                Box(
                    modifier = Modifier
                        .size(7.dp)
                        .graphicsLayer { alpha = pulse?.value ?: 1f }
                        .clip(CircleShape)
                        .background(
                            if (live) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f)
                        )
                )
                Spacer(Modifier.width(8.dp))
                MonoLabel(stateLabel, color = if (live) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (sessionId.isNotEmpty()) {
                MonoLabel(
                    "#${sessionId.take(8)}",
                    color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                    modifier = Modifier
                        .padding(start = 15.dp)
                        .clickable { onCopySession() }
                )
            }
        }
        if (onNewSession != null) HeaderIcon(Icons.Default.Add, "New conversation", onNewSession)
        if (onNewTopic != null) {
            HeaderIcon(Icons.Default.RestartAlt, "New topic — Dana forgets this conversation", onNewTopic, enabled = newTopicEnabled)
        }
        if (onMoveToApp != null) {
            HeaderIcon(Icons.Default.OpenInFull, "Continue in app", onMoveToApp, tint = MaterialTheme.colorScheme.primary)
        }
        HeaderIcon(Icons.Default.KeyboardArrowDown, "Minimise to bubble", onMinimize)
        HeaderIcon(Icons.Default.Close, "Close", onDismiss)
    }
}

@Composable
private fun HeaderIcon(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    description: String,
    onClick: () -> Unit,
    tint: Color = MaterialTheme.colorScheme.onSurfaceVariant,
    enabled: Boolean = true
) {
    IconButton(onClick = onClick, enabled = enabled, modifier = Modifier.size(44.dp)) {
        Icon(icon, contentDescription = description, tint = tint.copy(alpha = if (enabled) tint.alpha else 0.4f), modifier = Modifier.size(20.dp))
    }
}

/** Shown before the first exchange: the date as a kicker and one big serif statement. */
@Composable
internal fun AssistantStage(isRecording: Boolean, modifier: Modifier = Modifier) {
    val date = remember { SimpleDateFormat("EEEE d MMM", Locale.getDefault()).format(Date()) }
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 24.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)
    ) {
        MonoLabel(date, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(
            text = if (isRecording) "I'm listening." else "Say it, or type it.",
            style = MaterialTheme.typography.displaySmall,
            color = MaterialTheme.colorScheme.onSurface
        )
        Text(
            text = if (isRecording) "Tap the dial when you're done." else "Tap the dial to talk.",
            style = MaterialTheme.typography.bodyMedium,
            fontStyle = FontStyle.Italic,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

/**
 * One conversation turn, set editorially: the user's words as a large serif line,
 * the assistant's reply as ruled body text. No chat bubbles. RTL-aware per message.
 */
@Composable
internal fun AssistantTurn(
    msg: ChatMessage,
    context: Context,
    copy: (String) -> Unit,
    tapToSpeech: io.github.ronganam.dana.ui.components.TapToSpeech,
    serverUrl: String,
    apiToken: String,
    modifier: Modifier = Modifier
) {
    val isHebrew = remember(msg.message) { msg.message.any { it in '֐'..'׿' } }
    val direction = if (isHebrew) LayoutDirection.Rtl else LayoutDirection.Ltr
    val isUser = msg.isUser

    CompositionLocalProvider(LocalLayoutDirection provides direction) {
        Column(
            modifier = modifier
                .fillMaxWidth()
                .pointerInput(msg.id) {
                    detectTapGestures(
                        onTap = {
                            if (!msg.isThinking && !isUser) tapToSpeech.speak(msg.message)
                        },
                        onLongPress = {
                            if (!msg.isThinking) {
                                copy(msg.message)
                                Toast.makeText(context, "Copied to clipboard", Toast.LENGTH_SHORT).show()
                            }
                        }
                    )
                }
                .padding(horizontal = 24.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            if (isUser) {
                MonoLabel("You")
                if (msg.images.isNotEmpty()) {
                    msg.images.forEach { url ->
                        AsyncImage(
                            model = serverImageRequest(context, url, serverUrl, apiToken),
                            contentDescription = if (msg.screen.isNotEmpty()) "Screen sent with the message" else "Attached image",
                            // A sent screen is a small portrait thumbnail, not a full-width picture.
                            modifier = if (msg.screen.isNotEmpty()) Modifier
                                .height(120.dp)
                                .clip(RoundedCornerShape(6.dp))
                                .border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(6.dp))
                            else Modifier
                                .widthIn(max = 220.dp)
                                .clip(RoundedCornerShape(10.dp)),
                            contentScale = if (msg.screen.isNotEmpty()) ContentScale.FillHeight else ContentScale.FillWidth
                        )
                    }
                }
                if (msg.screen.isNotEmpty()) ScreenCaption(msg)
                if (msg.message.isNotBlank()) {
                    Text(
                        text = msg.message,
                        style = MaterialTheme.typography.headlineSmall,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                }
            } else {
                // Ruled reply: a 2dp vermilion rule on the reading-start edge.
                Row(modifier = Modifier.height(IntrinsicSize.Min)) {
                    Box(
                        modifier = Modifier
                            .width(2.dp)
                            .fillMaxHeight()
                            .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.8f))
                    )
                    Spacer(Modifier.width(12.dp))
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        // A learned phrase ran without the model: said quietly next to the name.
                        MonoLabel(if (msg.shortcut) "Dana · learned phrase" else "Dana", color = MaterialTheme.colorScheme.primary)
                        if (msg.isThinking) {
                            TypingIndicator()
                            ThinkingCaption(msg.thinkingLabel)
                        } else if (msg.isStreaming && msg.message.isEmpty() && msg.reasoning.isEmpty()) {
                            TypingIndicator()
                        } else {
                            if (msg.reasoning.isNotEmpty()) {
                                ReasoningBlock(text = msg.reasoning, thinking = msg.isStreaming && msg.message.isEmpty())
                            }
                            if (msg.images.isNotEmpty()) {
                                msg.images.forEach { url ->
                                    AsyncImage(
                                        model = serverImageRequest(context, url, serverUrl, apiToken),
                                        contentDescription = "Attached image",
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .clip(RoundedCornerShape(10.dp)),
                                        contentScale = ContentScale.FillWidth
                                    )
                                }
                            }
                            if (msg.message.isNotEmpty()) {
                                MarkdownText(
                                    text = msg.message,
                                    color = MaterialTheme.colorScheme.onSurface,
                                    style = MaterialTheme.typography.bodyLarge
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

/** Under a message sent with the screen: "Screen · Chrome" (and "text only" without a screenshot). */
@Composable
internal fun ScreenCaption(msg: ChatMessage, modifier: Modifier = Modifier) {
    val parts = listOfNotNull("Screen", msg.screen.takeIf { it != "Screen" }, if (msg.images.isEmpty()) "text only" else null)
    MonoLabel(
        io.github.ronganam.dana.ui.screens.common.isolatedParts(parts.joinToString(" · ")),
        color = MaterialTheme.colorScheme.primary,
        modifier = modifier
    )
}

/** One-tap starters. Items with [prefill] drop text into the field for completion; others send right away. */
internal data class QuickAction(val label: String, val send: String? = null, val prefill: String? = null)

internal val DefaultQuickActions = listOf(
    QuickAction("What's on today?", send = "What's on my agenda today?"),
    QuickAction("Remind me…", prefill = "Remind me to "),
    QuickAction("Remember…", prefill = "Remember that "),
    QuickAction("Last call summary", send = "Summarise my last call")
)

@Composable
internal fun QuickActionRow(
    actions: List<QuickAction>,
    onAction: (QuickAction) -> Unit,
    modifier: Modifier = Modifier
) {
    LazyRow(
        modifier = modifier.fillMaxWidth(),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 20.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        items(actions, key = { it.label }) { action ->
            Surface(
                onClick = { onAction(action) },
                shape = RoundedCornerShape(50),
                color = Color.Transparent,
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.55f))
            ) {
                Text(
                    text = action.label,
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 9.dp)
                )
            }
        }
    }
}

/**
 * Thumb-zone dock: attach (or [leading] in its place: the power-button sheet's
 * "Screen" control), text field, and the voice dial; [above] sits over the row.
 */
@Composable
internal fun AssistantDock(
    inputText: String,
    onInputTextChange: (String) -> Unit,
    mode: VoiceDialMode,
    pendingImageUri: android.net.Uri?,
    onSend: () -> Unit,
    onVoiceToggle: () -> Unit,
    onPickImage: () -> Unit,
    onClearImage: () -> Unit,
    modifier: Modifier = Modifier,
    leading: (@Composable () -> Unit)? = null,
    above: (@Composable () -> Unit)? = null
) {
    Column(modifier = modifier.fillMaxWidth().padding(horizontal = 14.dp)) {
        above?.invoke()
        if (pendingImageUri != null) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(start = 6.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                AsyncImage(
                    model = pendingImageUri,
                    contentDescription = "Selected image",
                    modifier = Modifier.size(52.dp).clip(RoundedCornerShape(10.dp)),
                    contentScale = ContentScale.Crop
                )
                IconButton(onClick = onClearImage, modifier = Modifier.size(36.dp)) {
                    Icon(
                        Icons.Default.Close,
                        contentDescription = "Remove image",
                        tint = MaterialTheme.colorScheme.error,
                        modifier = Modifier.size(18.dp)
                    )
                }
            }
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            if (leading != null) {
                leading()
                Spacer(Modifier.width(6.dp))
            } else {
                IconButton(onClick = onPickImage, modifier = Modifier.size(44.dp)) {
                    Icon(
                        Icons.Outlined.Image,
                        contentDescription = "Attach image",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(22.dp)
                    )
                }
            }
            OutlinedTextField(
                value = inputText,
                onValueChange = onInputTextChange,
                placeholder = {
                    when (mode) {
                        VoiceDialMode.LISTENING -> MonoLabel("Listening…", color = MaterialTheme.colorScheme.primary)
                        VoiceDialMode.THINKING -> MonoLabel("Working on it…")
                        VoiceDialMode.IDLE -> Text("Type instead…", style = MaterialTheme.typography.bodyMedium)
                    }
                },
                modifier = Modifier.weight(1f).heightIn(min = 52.dp),
                shape = RoundedCornerShape(26.dp),
                // A Hebrew request starts at the right.
                textStyle = MaterialTheme.typography.bodyLarge.copy(textDirection = androidx.compose.ui.text.style.TextDirection.Content),
                maxLines = 3,
                colors = OutlinedTextFieldDefaults.colors(
                    focusedContainerColor = Color.Transparent,
                    unfocusedContainerColor = Color.Transparent,
                    focusedBorderColor = MaterialTheme.colorScheme.primary,
                    unfocusedBorderColor = MaterialTheme.colorScheme.outline.copy(alpha = 0.55f)
                ),
                trailingIcon = {
                    if (inputText.isNotBlank() || pendingImageUri != null) {
                        IconButton(onClick = onSend, modifier = Modifier.size(40.dp)) {
                            Icon(
                                Icons.AutoMirrored.Filled.Send,
                                contentDescription = "Send",
                                tint = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.size(20.dp)
                            )
                        }
                    }
                }
            )
            Spacer(Modifier.width(6.dp))
            VoiceDial(mode = mode, onClick = onVoiceToggle, size = 80.dp)
        }
    }
}
