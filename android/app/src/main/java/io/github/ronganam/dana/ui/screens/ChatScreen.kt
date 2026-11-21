package io.github.ronganam.dana.ui.screens

import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import io.github.ronganam.dana.ui.theme.Success
import io.github.ronganam.dana.ui.theme.Warning
import io.github.ronganam.dana.ui.theme.Info
import io.github.ronganam.dana.ui.theme.CategoryPalette

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.border
import androidx.compose.material.icons.outlined.ViewSidebar
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.draw.drawBehind
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.Phone
import androidx.compose.material.icons.filled.VolumeDown
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material.icons.automirrored.filled.ExitToApp
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material3.*
import io.github.ronganam.dana.data.realtime.RealtimeCallStatus
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.animation.core.*
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import io.github.ronganam.dana.ui.components.GlassCard
import io.github.ronganam.dana.ui.components.serverImageRequest
import io.github.ronganam.dana.ui.components.IterationPromptCard
import io.github.ronganam.dana.ui.components.SelectionCard
import io.github.ronganam.dana.ui.components.SiriGlowSphere
import io.github.ronganam.dana.ui.components.ToolCallCard
import io.github.ronganam.dana.ui.components.ModelPickerChip
import io.github.ronganam.dana.ui.components.ReasoningBlock
import io.github.ronganam.dana.ui.components.ThinkingCaption
import io.github.ronganam.dana.ui.theme.isWideLayout
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.*
import androidx.compose.foundation.BorderStroke
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import io.github.ronganam.dana.ui.theme.DmMono
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.unit.LayoutDirection
import android.speech.tts.TextToSpeech
import android.widget.Toast
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.size
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import coil.compose.AsyncImage

@Composable
fun VoiceWaveformIndicator(
    modifier: Modifier = Modifier
) {
    val infiniteTransition = rememberInfiniteTransition(label = "waveform")
    val numBars = 5
    val animations = (0 until numBars).map { index ->
        infiniteTransition.animateFloat(
            initialValue = 0.2f,
            targetValue = 1.0f,
            animationSpec = infiniteRepeatable(
                animation = tween(
                    durationMillis = when (index) {
                        0 -> 450
                        1 -> 350
                        2 -> 550
                        3 -> 400
                        else -> 500
                    },
                    easing = FastOutSlowInEasing
                ),
                repeatMode = RepeatMode.Reverse
            ),
            label = "bar_$index"
        )
    }

    Row(
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        animations.forEachIndexed { index, animValue ->
            val heightMultiplier = when (index) {
                0, 4 -> 0.6f
                1, 3 -> 0.8f
                else -> 1.0f
            }
            Box(
                modifier = Modifier
                    .size(width = 4.dp, height = 24.dp)
                    .graphicsLayer {
                        scaleY = animValue.value * heightMultiplier
                    }
                    .background(
                        brush = Brush.verticalGradient(
                            colors = listOf(
                                Color(0xFF00F0FF),
                                Color(0xFFD000FF)
                            )
                        ),
                        shape = RoundedCornerShape(2.dp)
                    )
            )
        }
    }
}

@Composable
fun TypingIndicator(
    modifier: Modifier = Modifier
) {
    val transition = rememberInfiniteTransition(label = "typing")

    val alpha1 by transition.animateFloat(
        initialValue = 0.2f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 600, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse,
            initialStartOffset = StartOffset(0)
        ),
        label = "dot_alpha1"
    )
    val alpha2 by transition.animateFloat(
        initialValue = 0.2f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 600, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse,
            initialStartOffset = StartOffset(200)
        ),
        label = "dot_alpha2"
    )
    val alpha3 by transition.animateFloat(
        initialValue = 0.2f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 600, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse,
            initialStartOffset = StartOffset(400)
        ),
        label = "dot_alpha3"
    )

    Row(
        modifier = modifier.padding(vertical = 4.dp, horizontal = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        val color = MaterialTheme.colorScheme.onSurfaceVariant
        Box(
            modifier = Modifier
                .size(8.dp)
                .graphicsLayer { alpha = alpha1 }
                .background(color, RoundedCornerShape(4.dp))
        )
        Box(
            modifier = Modifier
                .size(8.dp)
                .graphicsLayer { alpha = alpha2 }
                .background(color, RoundedCornerShape(4.dp))
        )
        Box(
            modifier = Modifier
                .size(8.dp)
                .graphicsLayer { alpha = alpha3 }
                .background(color, RoundedCornerShape(4.dp))
        )
    }
}

sealed class MarkdownBlock {
    data class Paragraph(val text: AnnotatedString) : MarkdownBlock()
    data class Heading(val text: AnnotatedString, val level: Int) : MarkdownBlock()
    data class BulletItem(val text: AnnotatedString) : MarkdownBlock()
    data class CodeBlock(val code: String, val language: String?) : MarkdownBlock()
}

fun parseInlineMarkdown(text: String): AnnotatedString {
    val builder = AnnotatedString.Builder()
    var i = 0
    val n = text.length
    while (i < n) {
        // Inline code: `code`
        if (text[i] == '`') {
            val nextBacktick = text.indexOf('`', i + 1)
            if (nextBacktick != -1) {
                builder.pushStyle(SpanStyle(
                    fontFamily = DmMono,
                    background = Color(0xFFF7F5F0).copy(alpha = 0.12f),
                    color = Color(0xFFC9C0AD)
                ))
                builder.append(text.substring(i + 1, nextBacktick))
                builder.pop()
                i = nextBacktick + 1
                continue
            }
        }
        
        // Bold: **text**
        if (i + 1 < n && text[i] == '*' && text[i + 1] == '*') {
            val nextBold = text.indexOf("**", i + 2)
            if (nextBold != -1) {
                builder.pushStyle(SpanStyle(fontWeight = FontWeight.Bold))
                builder.append(parseInlineMarkdown(text.substring(i + 2, nextBold)))
                builder.pop()
                i = nextBold + 2
                continue
            }
        }
        if (i + 1 < n && text[i] == '_' && text[i + 1] == '_') {
            val nextBold = text.indexOf("__", i + 2)
            if (nextBold != -1) {
                builder.pushStyle(SpanStyle(fontWeight = FontWeight.Bold))
                builder.append(parseInlineMarkdown(text.substring(i + 2, nextBold)))
                builder.pop()
                i = nextBold + 2
                continue
            }
        }
        
        // Italic: *text*
        if (text[i] == '*') {
            val nextItalic = text.indexOf('*', i + 1)
            if (nextItalic != -1) {
                builder.pushStyle(SpanStyle(fontStyle = FontStyle.Italic))
                builder.append(parseInlineMarkdown(text.substring(i + 1, nextItalic)))
                builder.pop()
                i = nextItalic + 1
                continue
            }
        }
        if (text[i] == '_') {
            val nextItalic = text.indexOf('_', i + 1)
            if (nextItalic != -1) {
                builder.pushStyle(SpanStyle(fontStyle = FontStyle.Italic))
                builder.append(parseInlineMarkdown(text.substring(i + 1, nextItalic)))
                builder.pop()
                i = nextItalic + 1
                continue
            }
        }
        
        // Otherwise, regular character
        builder.append(text[i].toString())
        i++
    }
    
    return builder.toAnnotatedString()
}

fun parseMarkdown(text: String): List<MarkdownBlock> {
    val lines = text.split("\n")
    val blocks = mutableListOf<MarkdownBlock>()
    
    var inCodeBlock = false
    val codeContent = StringBuilder()
    var codeLanguage: String? = null
    
    for (line in lines) {
        if (line.trim().startsWith("```")) {
            if (inCodeBlock) {
                blocks.add(MarkdownBlock.CodeBlock(codeContent.toString().trimEnd(), codeLanguage))
                codeContent.setLength(0)
                codeLanguage = null
                inCodeBlock = false
            } else {
                inCodeBlock = true
                val lang = line.trim().substring(3).trim()
                codeLanguage = if (lang.isNotEmpty()) lang else null
            }
            continue
        }
        
        if (inCodeBlock) {
            codeContent.append(line).append("\n")
            continue
        }
        
        val trimmed = line.trim()
        if (trimmed.isEmpty()) {
            continue
        }
        
        // Check for headings
        if (trimmed.startsWith("#")) {
            val hashCount = trimmed.takeWhile { it == '#' }.length
            if (hashCount in 1..6 && trimmed.getOrNull(hashCount) == ' ') {
                val headingText = trimmed.substring(hashCount + 1).trim()
                blocks.add(MarkdownBlock.Heading(parseInlineMarkdown(headingText), hashCount))
                continue
            }
        }
        
        // Check for bullet list items
        if (trimmed.startsWith("- ") || trimmed.startsWith("* ") || trimmed.startsWith("• ")) {
            val itemText = trimmed.substring(2).trim()
            blocks.add(MarkdownBlock.BulletItem(parseInlineMarkdown(itemText)))
            continue
        }
        
        // Default: paragraph
        blocks.add(MarkdownBlock.Paragraph(parseInlineMarkdown(line)))
    }
    
    if (inCodeBlock) {
        blocks.add(MarkdownBlock.CodeBlock(codeContent.toString().trimEnd(), codeLanguage))
    }
    
    return blocks
}

@Composable
fun MarkdownText(
    text: String,
    color: Color,
    style: TextStyle,
    modifier: Modifier = Modifier
) {
    val blocks = remember(text) { parseMarkdown(text) }
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        blocks.forEach { block ->
            when (block) {
                is MarkdownBlock.Heading -> {
                    val scale = when (block.level) {
                        1 -> 1.3f
                        2 -> 1.2f
                        3 -> 1.1f
                        else -> 1.0f
                    }
                    Text(
                        text = block.text,
                        color = color,
                        fontWeight = FontWeight.Bold,
                        style = style.copy(fontSize = style.fontSize * scale),
                        modifier = Modifier.padding(top = 6.dp, bottom = 2.dp)
                    )
                }
                is MarkdownBlock.BulletItem -> {
                    Row(
                        modifier = Modifier.padding(start = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        Text(
                            text = "•",
                            color = color,
                            style = style
                        )
                        Text(
                            text = block.text,
                            color = color,
                            style = style,
                            modifier = Modifier.weight(1f)
                        )
                    }
                }
                is MarkdownBlock.CodeBlock -> {
                    Surface(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 4.dp),
                        shape = RoundedCornerShape(4.dp),
                        color = Color.Black.copy(alpha = 0.15f),
                        border = BorderStroke(1.dp, Color.White.copy(alpha = 0.1f))
                    ) {
                        Column(modifier = Modifier.padding(12.dp)) {
                            block.language?.let {
                                Text(
                                    text = it.uppercase(),
                                    color = color.copy(alpha = 0.5f),
                                    style = MaterialTheme.typography.labelSmall,
                                    modifier = Modifier.padding(bottom = 4.dp)
                                )
                            }
                            Text(
                                text = block.code,
                                color = color.copy(alpha = 0.9f),
                                style = style.copy(
                                    fontFamily = DmMono,
                                    fontSize = style.fontSize * 0.9f
                                )
                            )
                        }
                    }
                }
                is MarkdownBlock.Paragraph -> {
                    Text(
                        text = block.text,
                        color = color,
                        style = style
                    )
                }
            }
        }
    }
}
