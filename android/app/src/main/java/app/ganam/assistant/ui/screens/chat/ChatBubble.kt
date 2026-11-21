package app.ganam.assistant.ui.screens.chat

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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import app.ganam.assistant.data.models.ChatMessage
import app.ganam.assistant.ui.components.ReasoningBlock
import app.ganam.assistant.ui.components.ThinkingCaption
import app.ganam.assistant.ui.components.serverImageRequest
import app.ganam.assistant.ui.screens.MarkdownText
import app.ganam.assistant.ui.screens.TypingIndicator
import coil.compose.AsyncImage
import java.util.Locale

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

/**
 * One chat bubble: the user's (boxed) or Dana's (vermilion rule), with the
 * typing indicator, streamed thoughts, images and markdown. Direction follows
 * the message's script. Tap speaks it, long-press copies it. Shared by the
 * old chat screen and the thread.
 */
@Composable
fun ChatBubble(
    msg: ChatMessage,
    tts: TextToSpeech?,
    wide: Boolean,
    serverUrl: String,
    apiToken: String,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    val clipboardManager = LocalClipboardManager.current
    val isUser = msg.isUser
    val isHebrew = remember(msg.message) { msg.message.any { it in '\u0590'..'\u05FF' } }
    val layoutDirection = if (isHebrew) LayoutDirection.Rtl else LayoutDirection.Ltr
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser || isHebrew) Arrangement.End else Arrangement.Start
    ) {
        CompositionLocalProvider(LocalLayoutDirection provides layoutDirection) {
            Box(
                modifier = Modifier
                    .then(
                        if (isUser) Modifier
                            .clip(RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp, bottomStart = 16.dp, bottomEnd = 4.dp))
                            .background(MaterialTheme.colorScheme.surfaceContainerLow)
                            .border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp, bottomStart = 16.dp, bottomEnd = 4.dp))
                        else {
                            val rule = MaterialTheme.colorScheme.primary
                            Modifier.drawBehind {
                                val x = if (layoutDirection == LayoutDirection.Ltr) 1.dp.toPx() else size.width - 1.dp.toPx()
                                drawLine(rule, Offset(x, 2.dp.toPx()), Offset(x, size.height - 2.dp.toPx()), 2.dp.toPx())
                            }
                        }
                    )
                    .pointerInput(msg.id) {
                        detectTapGestures(
                            onTap = {
                                if (!msg.isThinking) {
                                    val cleanText = msg.message.replace(Regex("[*#`_\\-]"), "").trim()
                                    val textHebrew = cleanText.any { it in '\u0590'..'\u05FF' }
                                    tts?.language = if (textHebrew) Locale.forLanguageTag("he-IL") else Locale.US
                                    tts?.speak(cleanText, TextToSpeech.QUEUE_FLUSH, null, null)
                                }
                            },
                            onLongPress = {
                                if (!msg.isThinking) {
                                    clipboardManager.setText(AnnotatedString(msg.message))
                                    Toast.makeText(context, "Copied to clipboard", Toast.LENGTH_SHORT).show()
                                }
                            }
                        )
                    }
                    .padding(horizontal = if (isUser) 14.dp else 14.dp, vertical = if (isUser) 10.dp else 2.dp)
                    .widthIn(max = if (wide) 640.dp else 320.dp)
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
                        app.ganam.assistant.ui.components.assistant.ScreenCaption(msg, Modifier.padding(bottom = 6.dp))
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
                                    style = MaterialTheme.typography.bodyMedium
                                )
                            }
                        }
                    } else if (msg.message.isNotEmpty()) {
                        MarkdownText(
                            text = msg.message,
                            color = MaterialTheme.colorScheme.onSurface,
                            style = MaterialTheme.typography.bodyMedium
                        )
                    }
                }
            }
        }
    }
}
