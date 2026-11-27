package io.github.ronganam.dana.ui.screens.chat

import io.github.ronganam.dana.ui.components.rememberCopyText
import android.speech.tts.TextToSpeech
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.ui.components.ReasoningBlock
import io.github.ronganam.dana.ui.components.ThinkingCaption
import io.github.ronganam.dana.ui.components.serverImageRequest
import io.github.ronganam.dana.ui.screens.MarkdownText
import io.github.ronganam.dana.ui.screens.TypingIndicator
import coil.compose.AsyncImage
import io.github.ronganam.dana.data.voice.useVoiceFor
import io.github.ronganam.dana.R

/** A TextToSpeech for "tap a reply to hear it", shut down with the composable. */
@Composable
fun rememberTts(): TextToSpeech? {
    val context = LocalContext.current
    var tts by remember { mutableStateOf<TextToSpeech?>(null) }
    DisposableEffect(context) {
        val instance = TextToSpeech(context) { }
        tts = instance
        onDispose {
            instance.stop()
            instance.shutdown()
        }
    }
    return tts
}

/** Your bubbles' violet (R6: #3A2F55 on night, a lavender on paper). */
@Composable
fun userBubbleColor(): Color =
    if (MaterialTheme.colorScheme.background.luminance() < 0.5f) Color(0xFF3A2F55) else Color(0xFFE2D8FA)

/**
 * One message: yours in a violet bubble at the end, Dana's on a tonal card
 * at the start (or, [bare], without its own card: inside the card of the
 * steps that led to it). With the typing indicator, streamed thoughts,
 * images and markdown; a message in Hebrew keeps its own direction. Tap
 * speaks it, long-press copies it. Shared by the old chat screen, the
 * thread, the power-button sheet and kiosk / TV.
 */
@Composable
fun ChatBubble(
    msg: ChatMessage,
    tts: TextToSpeech?,
    wide: Boolean,
    serverUrl: String,
    apiToken: String,
    modifier: Modifier = Modifier,
    bare: Boolean = false
) {
    val context = LocalContext.current
    val copy = rememberCopyText()
    val isUser = msg.isUser
    val isHebrew = remember(msg.message) { msg.message.any { it in '\u0590'..'\u05FF' } }
    val layoutDirection = if (isHebrew) LayoutDirection.Rtl else LayoutDirection.Ltr
    val userColor = userBubbleColor()
    val cardColor = MaterialTheme.colorScheme.surfaceContainerLow
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start
    ) {
        CompositionLocalProvider(LocalLayoutDirection provides layoutDirection) {
            Box(
                modifier = Modifier
                    .then(
                        when {
                            isUser -> Modifier
                                .widthIn(max = if (wide) 480.dp else 300.dp)
                                .clip(RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp, bottomStart = 20.dp, bottomEnd = 6.dp))
                                .background(userColor)
                                .padding(horizontal = 16.dp, vertical = 12.dp)
                            bare -> Modifier.fillMaxWidth()
                            else -> Modifier
                                .widthIn(max = CARD_MAX)
                                .fillMaxWidth()
                                .clip(RoundedCornerShape(20.dp))
                                .background(cardColor)
                                .padding(horizontal = 16.dp, vertical = 12.dp)
                        }
                    )
                    .pointerInput(msg.id) {
                        detectTapGestures(
                            onTap = {
                                if (!msg.isThinking) {
                                    val cleanText = msg.message.replace(Regex("[*#`_\\-]"), "").trim()
                                    tts?.useVoiceFor(cleanText, context)
                                    tts?.speak(cleanText, TextToSpeech.QUEUE_FLUSH, null, null)
                                }
                            },
                            onLongPress = {
                                if (!msg.isThinking) {
                                    copy(msg.message)
                                    Toast.makeText(context, R.string.toast_copied_to_clipboard, Toast.LENGTH_SHORT).show()
                                }
                            }
                        )
                    }
            ) {
                if (msg.isThinking) {
                    Column {
                        TypingIndicator()
                        ThinkingCaption(msg.thinkingLabel)
                    }
                } else if (msg.isStreaming && msg.message.isEmpty() && msg.reasoning.isEmpty()) {
                    TypingIndicator()
                } else Column {
                    if (msg.reasoning.isNotEmpty()) {
                        ReasoningBlock(
                            text = msg.reasoning,
                            thinking = msg.isStreaming && msg.message.isEmpty()
                        )
                    }
                    if (msg.screen.isNotEmpty()) {
                        // Sent from the power-button sheet with the screen of the app underneath.
                        io.github.ronganam.dana.ui.components.assistant.ScreenCaption(msg, Modifier.padding(bottom = 6.dp))
                    }
                    if (msg.images.isNotEmpty()) {
                        Column {
                            msg.images.forEach { imageUrl ->
                                AsyncImage(
                                    model = serverImageRequest(context, imageUrl, serverUrl, apiToken),
                                    contentDescription = if (msg.screen.isNotEmpty()) "Screen sent with the message" else "Attached image",
                                    modifier = if (msg.screen.isNotEmpty()) Modifier
                                        .padding(bottom = if (msg.message.isNotEmpty()) 8.dp else 0.dp)
                                        .height(160.dp)
                                        .clip(RoundedCornerShape(4.dp))
                                    else Modifier
                                        .fillMaxWidth()
                                        .clip(RoundedCornerShape(4.dp))
                                        .padding(bottom = if (msg.message.isNotEmpty()) 8.dp else 0.dp),
                                    contentScale = if (msg.screen.isNotEmpty()) ContentScale.FillHeight else ContentScale.FillWidth
                                )
                            }
                            if (msg.message.isNotEmpty()) {
                                MarkdownText(
                                    text = msg.message,
                                    color = MaterialTheme.colorScheme.onSurface,
                                    style = MaterialTheme.typography.bodyLarge
                                )
                            }
                        }
                    } else if (msg.message.isNotEmpty()) {
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
