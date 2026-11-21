package io.github.ronganam.dana.ui.screens.now

import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.Animatable
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.gestures.draggable
import androidx.compose.foundation.gestures.rememberDraggableState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.models.NowAction
import io.github.ronganam.dana.data.models.NowItem
import io.github.ronganam.dana.data.notifications.PhoneNotificationActions
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.shell.DaySlot
import io.github.ronganam.dana.ui.shell.dragToDay
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

internal val NOW_GUTTER = 18.dp

/** Everything a Now row or the stage can do; built once by the destination. */
class NowHandlers(
    /** Run an action (server or client) on a card. */
    val perform: (item: NowItem, action: NowAction, isPrimary: Boolean) -> Unit,
    /** Row tapped: expand / collapse it (cover), select it (unfolded). */
    val toggle: (NowItem) -> Unit,
    /** "Ask Dana about this". */
    val ask: (NowItem) -> Unit,
    /** Send a reply to a notification item through the shade. */
    val reply: (NowItem, String) -> Unit,
    val choice: (NowItem, key: String, index: Int) -> Unit,
    val check: (NowItem, key: String, index: Int) -> Unit,
    /** Dropped on the day strip. */
    val schedule: (NowItem, DaySlot) -> Unit
)

/**
 * A row of the queue: where it came from (mono), the title, why it is here,
 * the primary and one or two others. On the cover screen it swipes (right =
 * the primary, left = dismiss, the action showing underneath as it moves) and
 * a tap opens its evidence in place; unfolded, the selected row is
 * highlighted (its evidence is on the stage) and a schedulable row can be
 * long-pressed onto the day strip.
 */
@Composable
fun NowRow(
    item: NowItem,
    draft: ItemDraft?,
    expanded: Boolean,
    selected: Boolean,
    wide: Boolean,
    handlers: NowHandlers
) {
    val actions = remember(item) { NowModel.rowActions(item) }
    val accent = MaterialTheme.colorScheme.primary
    val canvas = MaterialTheme.colorScheme.background
    val dismiss = actions.dismiss
    val density = LocalDensity.current
    val threshold = with(density) { 96.dp.toPx() }
    val offset = remember(item.id) { Animatable(0f) }
    val scope = rememberCoroutineScope()
    val primaryLabel = NowModel.primaryLabel(item, draft)

    Column(Modifier.fillMaxWidth()) {
        Box(Modifier.fillMaxWidth().clipToBounds()) {
            // What a swipe will do, revealed under the row as it moves (cover only).
            if (!wide && offset.value != 0f) {
                Row(
                    Modifier.matchParentSize().padding(horizontal = 14.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    if (offset.value > 0f) Text(
                        "✓ " + (primaryLabel ?: "Open"),
                        style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold, textDirection = TextDirection.Content),
                        color = accent
                    )
                    Spacer(Modifier.weight(1f))
                    if (offset.value < 0f && dismiss != null) Text(
                        dismiss.label + " ✕",
                        style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
            Column(
                Modifier
                    .fillMaxWidth()
                    .offset { IntOffset(offset.value.roundToInt(), 0) }
                    .then(
                        when {
                            wide && selected -> Modifier.background(canvas).drawBehind {
                                drawLine(accent, Offset(1.5.dp.toPx(), 0f), Offset(1.5.dp.toPx(), size.height), 3.dp.toPx())
                            }
                            wide -> Modifier
                            else -> Modifier.background(canvas)
                        }
                    )
                    .then(
                        if (wide) Modifier.then(if (item.schedulable) Modifier.dragToDay(item.title) { slot -> handlers.schedule(item, slot) } else Modifier)
                        else Modifier.draggable(
                            orientation = Orientation.Horizontal,
                            state = rememberDraggableState { d ->
                                scope.launch {
                                    val next = offset.value + d
                                    // Without a dismiss there is nothing to the left; it only gives a little.
                                    offset.snapTo(if (dismiss == null) next.coerceAtLeast(-threshold / 4) else next)
                                }
                            },
                            onDragStopped = {
                                when {
                                    offset.value > threshold -> {
                                        offset.animateTo(0f)
                                        val a = item.primary
                                        if (a != null) handlers.perform(item, a, true)
                                    }
                                    offset.value < -threshold && dismiss != null -> {
                                        handlers.perform(item, dismiss, dismiss === item.primary)
                                        offset.snapTo(0f)
                                    }
                                    else -> offset.animateTo(0f)
                                }
                            }
                        )
                    )
                    .clickable { handlers.toggle(item) }
                    .padding(horizontal = NOW_GUTTER)
                    .padding(top = 13.dp, bottom = 12.dp)
            ) {
                Row(Modifier.fillMaxWidth()) {
                    // Where it came from at a glance; the text column carries the words.
                    NowTile(item, modifier = Modifier.padding(top = 1.dp))
                    Spacer(Modifier.width(NOW_TILE_GAP))
                    Column(Modifier.weight(1f)) {
                        if (item.source.isNotBlank()) Text(
                            io.github.ronganam.dana.ui.screens.common.isolatedParts(item.source.uppercase()),
                            style = MaterialTheme.typography.labelMedium.copy(textDirection = TextDirection.Ltr),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis
                        )
                        Text(
                            item.title,
                            style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.SemiBold, fontSize = 15.5.sp, lineHeight = 20.sp, textDirection = TextDirection.Content),
                            color = MaterialTheme.colorScheme.onSurface,
                            modifier = Modifier.fillMaxWidth().padding(top = 5.dp)
                        )
                        if (item.why.isNotBlank() && (!wide || selected)) Text(
                            item.why,
                            style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.fillMaxWidth().padding(top = 2.dp)
                        )
                    }
                }
                ActionButtons(item, draft, actions, handlers, Modifier.padding(start = NOW_TILE + NOW_TILE_GAP, top = 10.dp))
                if (expanded && !wide) Expanded(item, draft, actions.more, handlers)
            }
        }
        Rule(Modifier.padding(horizontal = if (wide) NOW_GUTTER else 0.dp))
    }
}

/** Primary (accent outline) + the inline others. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ActionButtons(
    item: NowItem,
    draft: ItemDraft?,
    actions: RowActions,
    handlers: NowHandlers,
    modifier: Modifier = Modifier,
    solidPrimary: Boolean = false,
    includeMore: Boolean = false
) {
    FlowRow(modifier, horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        actions.primary?.let { p ->
            InkButton(NowModel.primaryLabel(item, draft) ?: p.label, emphasis = if (solidPrimary) InkEmphasis.SOLID else InkEmphasis.PRIMARY) {
                handlers.perform(item, p, true)
            }
        }
        (actions.inline + if (includeMore) actions.more else emptyList()).forEach { a ->
            InkButton(a.label) { handlers.perform(item, a, false) }
        }
    }
}

/** The cover screen's open row: evidence, choices / checks, the other actions, the reply box, Ask Dana. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Expanded(item: NowItem, draft: ItemDraft?, more: List<NowAction>, handlers: NowHandlers) {
    Column(
        Modifier
            .padding(top = 12.dp)
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .padding(horizontal = 12.dp)
            .padding(bottom = 10.dp)
            .animateContentSize()
    ) {
        item.blocks.filterNot { it.isDecide() }.forEach { EvidenceBlock(it) }
        item.blocks.filter { it.isDecide() }.forEach { b ->
            DecideBlock(b, draft, { k, i -> handlers.choice(item, k, i) }, { k, i -> handlers.check(item, k, i) })
        }
        if (more.isNotEmpty()) {
            MoreActions(item, more, handlers)
        }
        ReplyBox(item, handlers)
        InkButton("Ask Dana about this →", emphasis = InkEmphasis.BARE, modifier = Modifier.padding(top = 8.dp)) { handlers.ask(item) }
    }
}

/** The actions that didn't fit the row ("Later" when they are snoozes). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun MoreActions(item: NowItem, more: List<NowAction>, handlers: NowHandlers) {
    Column(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(top = 14.dp, bottom = 6.dp)) { MonoLabel(if (more.all { it.action == "snooze" }) "Later" else "More") }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            more.forEach { a -> InkButton(a.label) { handlers.perform(item, a, false) } }
        }
    }
}

/**
 * Reply to a notification item right here, through its own reply button in
 * the shade: the app's quick replies (if it offers any), a field, Send.
 * Shown only while the notification can still be answered from the phone.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ReplyBox(item: NowItem, handlers: NowHandlers) {
    val n = item.notification
    val key = n?.key
    // Read live: what the shade can do now (the server's can_reply may be minutes old).
    val button = remember(key, item) { if (n == null) null else PhoneNotificationActions.replyButton(key) }
    val quick = remember(key, item) { if (button == null) emptyList() else PhoneNotificationActions.quickReplies(key) }
    var text by rememberSaveable(item.id) { mutableStateOf("") }
    if (n != null && button != null) {
        val who = item.source.split(" · ").takeIf { it.size >= 3 }?.get(1)
        Column(Modifier.fillMaxWidth()) {
            Row(Modifier.padding(top = 14.dp, bottom = 6.dp)) { MonoLabel(if (who != null) "Reply to $who" else "Reply") }
            if (quick.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                quick.forEach { q -> InkButton(q) { text = q } }
            }
            val shape = RoundedCornerShape(10.dp)
            BasicTextField(
                value = text,
                onValueChange = { text = it },
                textStyle = MaterialTheme.typography.bodyMedium.copy(color = MaterialTheme.colorScheme.onSurface, textDirection = TextDirection.Content),
                cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                maxLines = 4,
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { if (text.isNotBlank()) { handlers.reply(item, text.trim()); text = "" } }),
                modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                decorationBox = { inner ->
                    Box(
                        Modifier.fillMaxWidth().clip(shape).background(MaterialTheme.colorScheme.background)
                            .border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape).padding(horizontal = 11.dp, vertical = 9.dp)
                    ) {
                        if (text.isEmpty()) Text("Write a reply…", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        inner()
                    }
                }
            )
            InkButton("Send", emphasis = InkEmphasis.SOLID, enabled = text.isNotBlank(), modifier = Modifier.padding(top = 8.dp)) {
                handlers.reply(item, text.trim())
                text = ""
            }
        }
    }
}
