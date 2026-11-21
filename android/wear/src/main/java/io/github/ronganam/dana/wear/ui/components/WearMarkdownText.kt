package io.github.ronganam.dana.wear.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.wear.compose.material.Text

sealed class WearMarkdownBlock {
    data class Paragraph(val text: AnnotatedString) : WearMarkdownBlock()
    data class Heading(val text: AnnotatedString, val level: Int) : WearMarkdownBlock()
    data class BulletItem(val text: AnnotatedString) : WearMarkdownBlock()
    data class CodeBlock(val code: String, val language: String? = null) : WearMarkdownBlock()
}

@Composable
fun WearMarkdownText(
    text: String,
    color: Color,
    style: TextStyle,
    modifier: Modifier = Modifier
) {
    val isHebrew = remember(text) { text.any { it in '\u0590'..'\u05FF' } }
    val layoutDirection = if (isHebrew) LayoutDirection.Rtl else LayoutDirection.Ltr

    val blocks = remember(text) { parseWearMarkdown(text) }

    CompositionLocalProvider(LocalLayoutDirection provides layoutDirection) {
        Column(
            modifier = modifier.fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(4.dp),
            horizontalAlignment = Alignment.Start
        ) {
            blocks.forEach { block ->
                when (block) {
                    is WearMarkdownBlock.Heading -> {
                        Text(
                            text = block.text,
                            color = color,
                            fontWeight = FontWeight.Bold,
                            style = style.copy(fontSize = style.fontSize * (if (block.level == 1) 1.25f else 1.1f)),
                            textAlign = if (isHebrew) TextAlign.Right else TextAlign.Left,
                            modifier = Modifier.padding(top = 4.dp, bottom = 2.dp)
                        )
                    }
                    is WearMarkdownBlock.BulletItem -> {
                        Row(
                            modifier = Modifier.fillMaxWidth().padding(start = 4.dp),
                            horizontalArrangement = Arrangement.spacedBy(4.dp)
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
                                textAlign = if (isHebrew) TextAlign.Right else TextAlign.Left,
                                modifier = Modifier.weight(1f)
                            )
                        }
                    }
                    is WearMarkdownBlock.CodeBlock -> {
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .background(Color.Black.copy(alpha = 0.25f), RoundedCornerShape(6.dp))
                                .padding(6.dp)
                        ) {
                            Text(
                                text = block.code,
                                color = color.copy(alpha = 0.9f),
                                style = style.copy(fontFamily = FontFamily.Monospace, fontSize = style.fontSize * 0.9f),
                                textAlign = TextAlign.Left
                            )
                        }
                    }
                    is WearMarkdownBlock.Paragraph -> {
                        Text(
                            text = block.text,
                            color = color,
                            style = style,
                            textAlign = if (isHebrew) TextAlign.Right else TextAlign.Left,
                            modifier = Modifier.fillMaxWidth()
                        )
                    }
                }
            }
        }
    }
}

fun parseWearMarkdown(text: String): List<WearMarkdownBlock> {
    val lines = text.split("\n")
    val blocks = mutableListOf<WearMarkdownBlock>()

    var inCodeBlock = false
    val codeContent = StringBuilder()
    var codeLanguage: String? = null

    for (line in lines) {
        val trimmed = line.trim()
        if (trimmed.startsWith("```")) {
            if (inCodeBlock) {
                blocks.add(WearMarkdownBlock.CodeBlock(codeContent.toString().trimEnd(), codeLanguage))
                codeContent.setLength(0)
                codeLanguage = null
                inCodeBlock = false
            } else {
                inCodeBlock = true
                val lang = trimmed.substring(3).trim()
                codeLanguage = if (lang.isNotEmpty()) lang else null
            }
            continue
        }

        if (inCodeBlock) {
            codeContent.append(line).append("\n")
            continue
        }

        if (trimmed.isEmpty()) continue

        if (trimmed.startsWith("#")) {
            val hashCount = trimmed.takeWhile { it == '#' }.length
            if (hashCount in 1..6 && trimmed.getOrNull(hashCount) == ' ') {
                val headingText = trimmed.substring(hashCount + 1).trim()
                blocks.add(WearMarkdownBlock.Heading(parseInlineWearMarkdown(headingText), hashCount))
                continue
            }
        }

        if (trimmed.startsWith("- ") || trimmed.startsWith("* ") || trimmed.startsWith("• ")) {
            val itemText = trimmed.substring(2).trim()
            blocks.add(WearMarkdownBlock.BulletItem(parseInlineWearMarkdown(itemText)))
            continue
        }

        blocks.add(WearMarkdownBlock.Paragraph(parseInlineWearMarkdown(line)))
    }

    if (inCodeBlock && codeContent.isNotEmpty()) {
        blocks.add(WearMarkdownBlock.CodeBlock(codeContent.toString().trimEnd(), codeLanguage))
    }

    return blocks
}

fun parseInlineWearMarkdown(text: String): AnnotatedString {
    val builder = AnnotatedString.Builder()
    var i = 0
    val n = text.length

    while (i < n) {
        // Inline code: `code`
        if (text[i] == '`') {
            val nextBacktick = text.indexOf('`', i + 1)
            if (nextBacktick != -1) {
                builder.pushStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = Color(0x33FFFFFF)))
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
                builder.append(parseInlineWearMarkdown(text.substring(i + 2, nextBold)))
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
                builder.append(parseInlineWearMarkdown(text.substring(i + 1, nextItalic)))
                builder.pop()
                i = nextItalic + 1
                continue
            }
        }

        builder.append(text[i].toString())
        i++
    }

    return builder.toAnnotatedString()
}
