package app.ganam.assistant.ui.screens.now

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.ganam.assistant.data.models.NowBlock
import app.ganam.assistant.ui.components.InkChoice
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.components.Rule
import app.ganam.assistant.ui.theme.GanamSerif
import app.ganam.assistant.ui.theme.Warning

/*
 * The evidence of a Now card, drawn from the server's generic blocks. A block
 * type this app doesn't know draws nothing, so the server can add kinds of
 * cards without an app release.
 */

private val bodyStyle @Composable get() = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content)

/** One evidence block (not choices / checks: those are [DecideBlock]). */
@Composable
fun EvidenceBlock(block: NowBlock, modifier: Modifier = Modifier) {
    when (block) {
        is NowBlock.Lines -> LinesBlock(block, modifier)
        is NowBlock.Chat -> ChatBlock(block, modifier)
        is NowBlock.Facts -> FactsBlock(block, modifier)
        is NowBlock.Text -> TextBlock(block, modifier)
        is NowBlock.Week -> WeekBlock(block, modifier)
        is NowBlock.Choices, is NowBlock.Checks, is NowBlock.Unknown -> Unit
    }
}

@Composable
private fun BlockLabel(label: String?, trailing: String? = null) {
    if (label != null || trailing != null) {
        Row(Modifier.fillMaxWidth().padding(top = 14.dp, bottom = 6.dp)) {
            MonoLabel(label.orEmpty(), modifier = Modifier.weight(1f))
            if (trailing != null) MonoLabel(trailing)
        }
    }
}

/** Transcript lines; the one(s) the item came from are tinted with an accent bar. */
@Composable
private fun LinesBlock(b: NowBlock.Lines, modifier: Modifier) {
    val accent = MaterialTheme.colorScheme.primary
    val tint = MaterialTheme.colorScheme.surfaceContainerHigh
    Column(modifier.fillMaxWidth()) {
        BlockLabel(b.label)
        val anyWho = b.lines.any { it.who != null }
        b.lines.forEach { line ->
            Row(
                Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(8.dp))
                    .then(
                        if (line.hit) Modifier.background(tint).drawBehind {
                            drawLine(accent, Offset(1.dp.toPx(), 0f), Offset(1.dp.toPx(), size.height), 2.dp.toPx())
                        } else Modifier
                    )
                    .padding(horizontal = 8.dp, vertical = 6.dp),
                verticalAlignment = Alignment.Top
            ) {
                if (anyWho) Box(Modifier.width(52.dp).padding(top = 2.dp)) { MonoLabel(line.who.orEmpty()) }
                Text(line.text, style = bodyStyle, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f))
            }
        }
    }
}

/** The last messages of a chat: theirs on a surface, the owner's ("mine") outlined on the other side. */
@Composable
private fun ChatBlock(b: NowBlock.Chat, modifier: Modifier) {
    Column(modifier.fillMaxWidth()) {
        BlockLabel(b.label)
        b.messages.forEach { m ->
            val shape = RoundedCornerShape(12.dp)
            Box(Modifier.fillMaxWidth().padding(vertical = 3.dp), contentAlignment = if (m.mine) Alignment.CenterEnd else Alignment.CenterStart) {
                Column(
                    Modifier
                        .fillMaxWidth(0.82f)
                        .wrapContentWidth(if (m.mine) Alignment.End else Alignment.Start)
                        .clip(shape)
                        .then(
                            if (m.mine) Modifier.border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape)
                            else Modifier.background(MaterialTheme.colorScheme.surfaceContainerHigh)
                        )
                        .padding(horizontal = 11.dp, vertical = 7.dp)
                ) {
                    if (m.who != null) Text(
                        m.who,
                        style = MaterialTheme.typography.labelMedium.copy(textDirection = TextDirection.Content),
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Text(m.text, style = bodyStyle, color = MaterialTheme.colorScheme.onSurface)
                }
            }
        }
    }
}

@Composable
private fun FactsBlock(b: NowBlock.Facts, modifier: Modifier) {
    Column(modifier.fillMaxWidth()) {
        BlockLabel(b.label)
        Rule()
        b.items.forEach { f ->
            Text(f, style = bodyStyle, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.fillMaxWidth().padding(vertical = 9.dp))
            Rule()
        }
    }
}

@Composable
private fun TextBlock(b: NowBlock.Text, modifier: Modifier) {
    if (b.text.isNotBlank()) Column(modifier.fillMaxWidth()) {
        BlockLabel(b.label)
        Text(b.text, style = bodyStyle, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.fillMaxWidth())
    }
}

/** Seven cells, oldest first: a missed day in the warning colour, today (not known yet) faint. */
@Composable
private fun WeekBlock(b: NowBlock.Week, modifier: Modifier) {
    Column(modifier.fillMaxWidth()) {
        BlockLabel(b.label)
        Row(Modifier.fillMaxWidth().padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            b.cells.forEach { c ->
                val shape = RoundedCornerShape(10.dp)
                Column(
                    Modifier.weight(1f).border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape).padding(vertical = 8.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    MonoLabel(c.label)
                    Text(
                        c.value,
                        style = MaterialTheme.typography.titleMedium.copy(fontFamily = GanamSerif, fontSize = 18.sp),
                        color = when (c.ok) {
                            false -> Warning
                            null -> MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f)
                            true -> MaterialTheme.colorScheme.onSurface
                        },
                        textAlign = TextAlign.Center,
                        maxLines = 1
                    )
                }
            }
        }
    }
}

/** Choices (pick one: changes the primary) and checks (tick several: sent with the primary). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun DecideBlock(
    block: NowBlock,
    draft: ItemDraft?,
    onChoice: (key: String, index: Int) -> Unit,
    onCheck: (key: String, index: Int) -> Unit,
    modifier: Modifier = Modifier
) {
    when (block) {
        is NowBlock.Choices -> Column(modifier.fillMaxWidth()) {
            BlockLabel(block.label)
            val chosen = NowModel.chosenIndex(block, draft)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                block.options.forEachIndexed { i, o ->
                    InkChoice(o.label, selected = i == chosen) { onChoice(block.key, i) }
                }
            }
        }
        is NowBlock.Checks -> Column(modifier.fillMaxWidth()) {
            BlockLabel(block.label)
            val checked = NowModel.checkedIndexes(block, draft)
            block.options.forEachIndexed { i, o ->
                // The box sits at the start of its line's own direction (right of a Hebrew line).
                val dir = if (app.ganam.assistant.ui.screens.common.mostlyRtl(o.label)) LayoutDirection.Rtl else LayoutDirection.Ltr
                CompositionLocalProvider(LocalLayoutDirection provides dir) {
                    Row(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).clickable { onCheck(block.key, i) }.padding(vertical = 2.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Checkbox(
                            checked = i in checked,
                            onCheckedChange = { onCheck(block.key, i) },
                            colors = CheckboxDefaults.colors(checkedColor = MaterialTheme.colorScheme.primary)
                        )
                        Text(o.label, style = bodyStyle, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f))
                    }
                }
            }
        }
        else -> Unit
    }
}

fun NowBlock.isDecide(): Boolean = this is NowBlock.Choices || this is NowBlock.Checks
