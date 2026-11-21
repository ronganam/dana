package io.github.ronganam.dana.ui.screens.now

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.models.NowItem
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.shell.DayItem
import io.github.ronganam.dana.ui.theme.DanaSerif
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * Unfolded Now, the stage: the selected row's evidence and its decision
 * (choices / checks, every action, reply, Ask Dana); with nothing selected —
 * or nothing in the queue — the rest of today.
 */
@Composable
fun NowStage(
    item: NowItem?,
    draft: ItemDraft?,
    handlers: NowHandlers,
    now: LocalDateTime,
    later: List<Pair<String, DayItem>>,
    queueEmpty: Boolean = false
) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 26.dp, vertical = 22.dp)
            .widthIn(max = 720.dp)
    ) {
        if (item != null) ItemDetail(item, draft, handlers) else RestOfToday(now, later, hint = !queueEmpty)
    }
}

@Composable
private fun ItemDetail(item: NowItem, draft: ItemDraft?, handlers: NowHandlers) {
    val actions = remember(item) { NowModel.rowActions(item) }
    Row(Modifier.fillMaxWidth()) {
        NowTile(item, size = 38.dp, modifier = Modifier.padding(top = 1.dp))
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            if (item.source.isNotBlank()) Text(
                io.github.ronganam.dana.ui.screens.common.isolatedParts(item.source.uppercase()),
                style = MaterialTheme.typography.labelMedium.copy(textDirection = TextDirection.Ltr),
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Text(
                item.title,
                style = MaterialTheme.typography.titleLarge.copy(fontWeight = FontWeight.SemiBold, fontSize = 21.sp, lineHeight = 26.sp, textDirection = TextDirection.Content),
                modifier = Modifier.fillMaxWidth().padding(top = 6.dp, bottom = 4.dp)
            )
        }
    }
    if (item.why.isNotBlank()) Text(
        item.why,
        style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.fillMaxWidth()
    )
    item.blocks.filterNot { it.isDecide() }.forEach { EvidenceBlock(it, Modifier.padding(top = 4.dp)) }

    Row(Modifier.fillMaxWidth().padding(top = 22.dp, bottom = 8.dp)) {
        MonoLabel("Decide", modifier = Modifier.weight(1f))
        if (item.schedulable) MonoLabel("or drag it onto the day")
    }
    Rule()
    item.blocks.filter { it.isDecide() }.forEach { b ->
        DecideBlock(b, draft, { k, i -> handlers.choice(item, k, i) }, { k, i -> handlers.check(item, k, i) })
    }
    // The chosen option's confirmation, so the primary says what will happen.
    NowModel.choiceBlocks(item).firstNotNullOfOrNull { b -> b.options.getOrNull(NowModel.chosenIndex(b, draft))?.message }?.let {
        Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 8.dp))
    }
    ActionButtons(item, draft, actions, handlers, Modifier.padding(top = 14.dp), solidPrimary = true, includeMore = true)
    ReplyBox(item, handlers)
    InkButton("Ask Dana about this →", emphasis = InkEmphasis.BARE, modifier = Modifier.padding(top = 14.dp)) { handlers.ask(item) }
}

private val DAY_KICKER = DateTimeFormatter.ofPattern("EEE d MMM · 'from' HH:mm", Locale.ENGLISH)

@Composable
private fun RestOfToday(now: LocalDateTime, later: List<Pair<String, DayItem>>, hint: Boolean) {
    MonoLabel(now.format(DAY_KICKER))
    Text("The rest of today", style = MaterialTheme.typography.displaySmall, modifier = Modifier.padding(top = 2.dp, bottom = 10.dp))
    Rule()
    if (later.isEmpty()) Text(
        "Nothing else today.",
        style = MaterialTheme.typography.headlineSmall.copy(fontFamily = DanaSerif, fontStyle = FontStyle.Italic),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(vertical = 14.dp)
    )
    later.forEach { (at, item) -> LaterRow(at, item, gutter = 0.dp) }
    if (hint) Text(
        "Open a row on the left to see what it is about, or drag it onto the strip above.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 12.dp)
    )
}
