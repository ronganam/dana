package io.github.ronganam.dana.ui.screens.projects

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.projects.ProjectsCore
import io.github.ronganam.dana.ui.theme.DmMono

/** A block of the small Markdown the summaries and details use (Dana writes TL;DRs, lists and comparison tables). */
internal sealed class MdBlock {
    data class Heading(val text: String, val level: Int) : MdBlock()
    data class Paragraph(val text: String) : MdBlock()
    /** A list item: [marker] "•", "1." or a checkbox ☐ / ☑. */
    data class Item(val text: String, val marker: String, val depth: Int = 0) : MdBlock()
    data class Code(val content: String) : MdBlock()
    data class Quote(val content: String) : MdBlock()
    data class Table(val header: List<String>, val rows: List<List<String>>) : MdBlock()
    object Rule : MdBlock()
}

private val headingRe = Regex("^(#{1,6})\\s+(.*)$")
private val checkRe = Regex("^[-*+]\\s*\\[([ xX])]\\s*(.*)$")
private val bulletRe = Regex("^[-*+]\\s+(.*)$")
private val numberRe = Regex("^(\\d+)[.)]\\s+(.*)$")
private val tableSepRe = Regex("^\\|?\\s*:?-{2,}:?\\s*(\\|\\s*:?-{2,}:?\\s*)*\\|?$")
private val ruleRe = Regex("^(-{3,}|\\*{3,}|_{3,})$")

private fun cells(line: String): List<String> =
    line.trim().removePrefix("|").removeSuffix("|").split("|").map { it.trim() }

/** Markdown → blocks: headings, paragraphs, bullet / numbered / check items, code, quotes, pipe tables, rules. */
internal fun parseMarkdownBlocks(content: String): List<MdBlock> {
    val lines = content.replace("\r\n", "\n").split("\n")
    val out = mutableListOf<MdBlock>()
    var i = 0
    fun startsBlock(t: String) = t.startsWith("#") || t.startsWith("```") || t.startsWith(">") ||
        bulletRe.matches(t) || numberRe.matches(t) || t.startsWith("|") || ruleRe.matches(t)
    while (i < lines.size) {
        val raw = lines[i]
        val t = raw.trim()
        when {
            t.isEmpty() -> i++
            t.startsWith("```") -> {
                val code = mutableListOf<String>()
                i++
                while (i < lines.size && !lines[i].trim().startsWith("```")) code += lines[i++]
                out += MdBlock.Code(code.joinToString("\n"))
                i++
            }
            ruleRe.matches(t) -> { out += MdBlock.Rule; i++ }
            headingRe.matches(t) -> {
                val m = headingRe.find(t)!!
                out += MdBlock.Heading(m.groupValues[2].trim().trimEnd('#').trim(), m.groupValues[1].length)
                i++
            }
            t.startsWith(">") -> {
                val q = mutableListOf<String>()
                while (i < lines.size && lines[i].trimStart().startsWith(">")) q += lines[i++].trimStart().removePrefix(">").trim()
                out += MdBlock.Quote(q.joinToString(" "))
            }
            t.contains("|") && i + 1 < lines.size && tableSepRe.matches(lines[i + 1].trim()) -> {
                val header = cells(t)
                i += 2
                val rows = mutableListOf<List<String>>()
                while (i < lines.size && lines[i].trim().contains("|") && lines[i].isNotBlank()) {
                    val r = cells(lines[i++])
                    rows += List(header.size) { r.getOrElse(it) { "" } }
                }
                out += MdBlock.Table(header, rows)
            }
            checkRe.matches(t) -> {
                val m = checkRe.find(t)!!
                out += MdBlock.Item(m.groupValues[2].trim(), if (m.groupValues[1].isBlank()) "☐" else "☑", depthOf(raw)); i++
            }
            bulletRe.matches(t) -> { out += MdBlock.Item(bulletRe.find(t)!!.groupValues[1].trim(), "•", depthOf(raw)); i++ }
            numberRe.matches(t) -> {
                val m = numberRe.find(t)!!
                out += MdBlock.Item(m.groupValues[2].trim(), "${m.groupValues[1]}.", depthOf(raw)); i++
            }
            else -> {
                val p = mutableListOf<String>()
                while (i < lines.size) {
                    val l = lines[i].trim()
                    if (l.isEmpty() || (p.isNotEmpty() && startsBlock(l))) break
                    p += l
                    i++
                }
                out += MdBlock.Paragraph(p.joinToString(" "))
            }
        }
    }
    return out
}

private fun depthOf(raw: String): Int = (raw.length - raw.trimStart().length) / 2

/** **bold**, *italic* / _italic_, `code` and [text](url) (shown as its text) inline. */
internal fun inlineMarkdown(content: String, codeColor: androidx.compose.ui.graphics.Color, codeBg: androidx.compose.ui.graphics.Color): AnnotatedString {
    val b = AnnotatedString.Builder()
    val re = Regex("\\*\\*(.+?)\\*\\*|__(.+?)__|`([^`]+)`|\\[([^]]+)]\\(([^)]+)\\)|(?<![\\w*])\\*(?!\\s)(.+?)(?<!\\s)\\*(?![\\w*])|(?<!\\w)_(?!\\s)(.+?)(?<!\\s)_(?!\\w)")
    var last = 0
    for (m in re.findAll(content)) {
        if (m.range.first > last) b.append(content.substring(last, m.range.first))
        val g = m.groupValues
        when {
            g[1].isNotEmpty() -> b.append(AnnotatedString(g[1], SpanStyle(fontWeight = FontWeight.SemiBold)))
            g[2].isNotEmpty() -> b.append(AnnotatedString(g[2], SpanStyle(fontWeight = FontWeight.SemiBold)))
            g[3].isNotEmpty() -> b.append(AnnotatedString(g[3], SpanStyle(fontFamily = DmMono, color = codeColor, background = codeBg)))
            g[4].isNotEmpty() -> b.append(AnnotatedString(g[4], SpanStyle(color = codeColor)))
            g[6].isNotEmpty() -> b.append(AnnotatedString(g[6], SpanStyle(fontStyle = FontStyle.Italic)))
            g[7].isNotEmpty() -> b.append(AnnotatedString(g[7], SpanStyle(fontStyle = FontStyle.Italic)))
        }
        last = m.range.last + 1
    }
    if (last < content.length) b.append(content.substring(last))
    return b.toAnnotatedString()
}

/** The direction a line's own script asks for (Hebrew right to left), whatever the screen's direction. */
@Composable
private fun ByScript(text: String, content: @Composable () -> Unit) {
    CompositionLocalProvider(
        LocalLayoutDirection provides if (ProjectsCore.isRtl(text)) LayoutDirection.Rtl else LayoutDirection.Ltr,
        content = content
    )
}

/** Renders Markdown in Ink & Paper: serif headings, accent markers, hairline tables (scroll sideways when wide). */
@Composable
internal fun MarkdownBody(content: String, modifier: Modifier = Modifier) {
    val blocks = remember(content) { parseMarkdownBlocks(content) }
    val accent = MaterialTheme.colorScheme.primary
    val codeBg = accent.copy(alpha = 0.12f)
    val body = MaterialTheme.typography.bodyLarge.copy(lineHeight = 24.sp, textDirection = TextDirection.Content)
    val ink = MaterialTheme.colorScheme.onSurface
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        blocks.forEach { block ->
            when (block) {
                is MdBlock.Heading -> ByScript(block.text) {
                    Text(
                        inlineMarkdown(block.text, accent, codeBg),
                        style = when {
                            block.level <= 1 -> MaterialTheme.typography.headlineMedium
                            block.level == 2 -> MaterialTheme.typography.headlineSmall
                            else -> MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold)
                        }.copy(textDirection = TextDirection.Content),
                        color = ink,
                        modifier = Modifier.fillMaxWidth().padding(top = if (block.level <= 2) 6.dp else 2.dp)
                    )
                }
                is MdBlock.Paragraph -> ByScript(block.text) {
                    Text(inlineMarkdown(block.text, accent, codeBg), style = body, color = ink, modifier = Modifier.fillMaxWidth())
                }
                is MdBlock.Item -> ByScript(block.text) {
                    Row(Modifier.fillMaxWidth().padding(start = (block.depth * 16).dp)) {
                        Text(
                            block.marker, style = body, color = accent,
                            modifier = Modifier.widthIn(min = 20.dp).padding(end = 6.dp)
                        )
                        Text(inlineMarkdown(block.text, accent, codeBg), style = body, color = ink, modifier = Modifier.weight(1f))
                    }
                }
                is MdBlock.Code -> Column(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(6.dp))
                        .background(MaterialTheme.colorScheme.surfaceContainerHigh)
                        .horizontalScroll(rememberScrollState()).padding(horizontal = 10.dp, vertical = 8.dp)
                ) {
                    Text(block.content, style = MaterialTheme.typography.bodySmall.copy(fontFamily = DmMono), color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                is MdBlock.Quote -> ByScript(block.content) {
                    Text(
                        inlineMarkdown(block.content, accent, codeBg),
                        style = quietText(18).copy(textDirection = TextDirection.Content),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.fillMaxWidth()
                            .drawBehind { drawRect(accent, Offset.Zero, Size(2.dp.toPx(), size.height)) }
                            .padding(start = 14.dp)
                    )
                }
                is MdBlock.Table -> MdTable(block, accent, codeBg)
                MdBlock.Rule -> io.github.ronganam.dana.ui.components.Rule()
            }
        }
    }
}

@Composable
private fun MdTable(t: MdBlock.Table, accent: androidx.compose.ui.graphics.Color, codeBg: androidx.compose.ui.graphics.Color) {
    val line = MaterialTheme.colorScheme.outlineVariant
    val rtl = ProjectsCore.isRtl(t.header.joinToString(" ") + t.rows.flatten().joinToString(" "))
    CompositionLocalProvider(LocalLayoutDirection provides if (rtl) LayoutDirection.Rtl else LayoutDirection.Ltr) {
        // Every row shares the column widths: each column is as wide as its widest cell (72–260dp).
        val measurer = androidx.compose.ui.text.rememberTextMeasurer()
        val density = androidx.compose.ui.platform.LocalDensity.current
        val headStyle = MaterialTheme.typography.labelMedium
        val bodyStyle = MaterialTheme.typography.bodyMedium
        val cols = maxOf(t.header.size, t.rows.maxOfOrNull { it.size } ?: 0)
        val widths = remember(t, headStyle, bodyStyle, density) {
            List(cols) { i ->
                val cells = listOf(t.header.getOrElse(i) { "" }.uppercase() to headStyle) +
                    t.rows.map { (it.getOrElse(i) { "" }) to bodyStyle }
                val px = cells.maxOf { (text, style) ->
                    measurer.measure(inlineMarkdown(text, accent, codeBg), style, softWrap = false, maxLines = 1).size.width
                }
                with(density) { (px.toDp() + 21.dp).coerceIn(72.dp, 260.dp) }
            }
        }
        Box(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())) {
            Column(Modifier.width(IntrinsicSize.Max)) {
                @Composable
                fun row(cellsOf: List<String>, head: Boolean) {
                    Row(
                        Modifier.height(IntrinsicSize.Min).drawBehind {
                            val w = 1.dp.toPx()
                            drawLine(line, Offset(0f, size.height - w / 2), Offset(size.width, size.height - w / 2), w)
                        }
                    ) {
                        List(cols) { cellsOf.getOrElse(it) { "" } }.forEachIndexed { i, c ->
                            Text(
                                if (head) AnnotatedString(c.uppercase()) else inlineMarkdown(c, accent, codeBg),
                                style = if (head) headStyle else bodyStyle.copy(textDirection = TextDirection.Content),
                                color = if (head) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface,
                                modifier = Modifier.width(widths[i]).fillMaxHeight()
                                    .padding(horizontal = 10.dp, vertical = 8.dp)
                            )
                        }
                    }
                }
                row(t.header, head = true)
                t.rows.forEach { row(it, head = false) }
            }
        }
    }
}
