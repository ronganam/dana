package io.github.ronganam.dana.ui.screens.now

import io.github.ronganam.dana.ui.theme.hues
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.isTraversalGroup
import androidx.compose.ui.semantics.semantics
import io.github.ronganam.dana.ui.components.startBar
import io.github.ronganam.dana.ui.theme.Hue
import io.github.ronganam.dana.ui.theme.Motion
import kotlinx.coroutines.delay
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
import io.github.ronganam.dana.ui.components.ActionPill
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.MenuEntry
import io.github.ronganam.dana.ui.components.OverflowMenu
import androidx.compose.ui.text.style.TextOverflow
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.shell.DaySlot
import io.github.ronganam.dana.ui.shell.dragToDay
import kotlinx.coroutines.launch
import androidx.compose.foundation.Image
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import io.github.ronganam.dana.data.notifications.AppIcons
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlin.math.roundToInt

internal val NOW_GUTTER = 16.dp

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
 * A row of the queue, on the canvas: its lead ([NowModel.rowLead]: a
 * notification item's app icon, else an 8dp dot in its kind's hue), the title, one muted line (source · why), the one
 * visible action as a quiet pill (an ember fill for approvals) and a ⋯ menu
 * with every other action and "Ask Dana about this". On the cover screen a
 * tap opens the row in place (evidence, choices / checks, the reply box) and
 * it swipes: towards the start = dismiss, towards the end = the card's own
 * snooze (each only when the card offers it; the revealed side says which;
 * both answer with Undo). Unfolded, the selected row is highlighted (its
 * evidence is on the stage) and a schedulable one can be long-pressed onto
 * the day strip. Swipe, open and the actions are also accessibility actions.
 */
@Composable
fun NowRow(
    item: NowItem,
    draft: ItemDraft?,
    expanded: Boolean,
    selected: Boolean,
    wide: Boolean,
    handlers: NowHandlers,
    modifier: Modifier = Modifier
) {
    val actions = remember(item) { NowModel.rowActions(item) }
    val accent = MaterialTheme.colorScheme.primary
    val canvas = MaterialTheme.colorScheme.background
    val dismiss = actions.dismiss
    val snooze = actions.snooze
    val density = LocalDensity.current
    val threshold = with(density) { 96.dp.toPx() }
    val offset = remember(item.id) { Animatable(0f) }
    val scope = rememberCoroutineScope()
    val h = hues
    val lead = remember(item) { NowModel.rowLead(item) }
    val hasMore = item.blocks.isNotEmpty() || item.notification != null

    /** Swiped past the threshold: slide out, do it; snap back later only if the card is still here (failed / undone). */
    fun swipeOut(action: NowAction, direction: Float, width: Float) {
        scope.launch {
            offset.animateTo(direction * width, Motion.leave())
            handlers.perform(item, action, action === item.primary)
            delay(Motion.COMPLETE + 200L)
            offset.snapTo(0f)
        }
    }

    Column(modifier.fillMaxWidth()) {
        BoxWithConstraints(Modifier.fillMaxWidth().clipToBounds()) {
            val width = constraints.maxWidth.toFloat()
            // What a swipe will do, revealed under the row as it moves (cover only).
            if (!wide && offset.value != 0f) {
                Row(
                    Modifier.matchParentSize().padding(horizontal = NOW_GUTTER),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    if (offset.value > 0f && snooze != null) Text(
                        "Snooze · " + snooze.label,
                        style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold, textDirection = TextDirection.Content),
                        color = h.text(Hue.AMBER)
                    )
                    Spacer(Modifier.weight(1f))
                    if (offset.value < 0f && dismiss != null) Text(
                        dismiss.label,
                        style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold, textDirection = TextDirection.Content),
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
            Row(
                Modifier
                    .fillMaxWidth()
                    .offset { IntOffset(offset.value.roundToInt(), 0) }
                    .then(
                        when {
                            wide && selected -> Modifier.background(canvas).startBar(accent)
                            wide -> Modifier
                            // Canvas only while swiping (to cover what the swipe reveals), so the canvas glow shows through at rest.
                            else -> Modifier.drawBehind { if (offset.value != 0f) drawRect(canvas) }
                        }
                    )
                    .then(
                        if (wide) Modifier.then(if (item.schedulable) Modifier.dragToDay(item.title) { slot -> handlers.schedule(item, slot) } else Modifier)
                        else Modifier.draggable(
                            orientation = Orientation.Horizontal,
                            state = rememberDraggableState { d ->
                                scope.launch {
                                    val next = offset.value + d
                                    // A side with nothing behind it only gives a little.
                                    val min = if (dismiss == null) -threshold / 4 else -width
                                    val max = if (snooze == null) threshold / 4 else width
                                    offset.snapTo(next.coerceIn(min, max))
                                }
                            },
                            onDragStopped = {
                                when {
                                    offset.value > threshold && snooze != null -> swipeOut(snooze, 1f, width)
                                    offset.value < -threshold && dismiss != null -> swipeOut(dismiss, -1f, width)
                                    else -> offset.animateTo(0f, Motion.base())
                                }
                            }
                        )
                    )
                    .semantics {
                        isTraversalGroup = true
                        customActions = buildList {
                            if (hasMore && !wide) add(CustomAccessibilityAction(if (expanded) "Less" else "More") { handlers.toggle(item); true })
                            if (snooze != null) add(CustomAccessibilityAction("Snooze · ${snooze.label}") { handlers.perform(item, snooze, false); true })
                            if (dismiss != null) add(CustomAccessibilityAction(dismiss.label) { handlers.perform(item, dismiss, dismiss === item.primary); true })
                        }
                    }
                    .clickable(onClickLabel = if (wide) "Open" else if (expanded) "Less" else "More") { handlers.toggle(item) }
                    .padding(start = NOW_GUTTER, end = NOW_GUTTER - 10.dp)
                    .padding(top = 16.dp, bottom = 16.dp),
                verticalAlignment = Alignment.Top
            ) {
                // The lead sits on the title's first line (22sp line): the app icon (20dp) or the dot (8dp).
                when (lead) {
                    is RowLead.App -> RowAppIcon(lead, Modifier.padding(top = 1.dp))
                    is RowLead.Dot -> Box(Modifier.padding(top = 7.dp).size(8.dp).clip(CircleShape).background(h.of(lead.hue)))
                }
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(
                        item.title,
                        style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.Medium, fontSize = 16.sp, lineHeight = 22.sp, textDirection = TextDirection.Content),
                        color = MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.fillMaxWidth().semantics { heading() }
                    )
                    val meta = remember(item) { NowModel.metaLine(item.source, item.why) }
                    if (meta.isNotEmpty()) Text(
                        io.github.ronganam.dana.ui.screens.common.isolatedParts(meta),
                        style = MaterialTheme.typography.bodySmall.copy(fontSize = 13.sp, lineHeight = 18.sp, textDirection = TextDirection.Content),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = if (expanded) Int.MAX_VALUE else 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.fillMaxWidth().padding(top = 3.dp)
                    )
                    // Open: the evidence and the choices come before the decision.
                    if (expanded && !wide) Expanded(item, draft, handlers)
                    actions.primary?.let { p ->
                        ActionPill(
                            NowModel.primaryLabel(item, draft) ?: p.label,
                            solid = NowModel.solidPrimary(item),
                            modifier = Modifier.padding(top = if (expanded && !wide) 16.dp else 12.dp)
                        ) { handlers.perform(item, p, true) }
                    }
                    if (expanded && !wide) ReplyBox(item, handlers)
                }
                Spacer(Modifier.width(4.dp))
                // Centred on the title's first line.
                ItemOverflow(item, actions, handlers, Modifier.offset(y = (-9).dp))
            }
        }
        Rule(Modifier.padding(horizontal = NOW_GUTTER))
    }
}

/** A row's lead is this big when it is an app icon. */
internal val ROW_ICON = 20.dp

/**
 * A notification item's app icon (saved by [AppIcons]), 20dp with 5dp
 * corners; until there is one, the app's letter on the teal tint (as the
 * Inbox's tiles). Decorative: the row's source line names the app.
 */
@Composable
private fun RowAppIcon(lead: RowLead.App, modifier: Modifier = Modifier) {
    val ctx = LocalContext.current.applicationContext
    val icon by produceState<ImageBitmap?>(AppIcons.cached(lead.pkg)?.asImageBitmap(), lead.pkg) {
        if (value == null) value = withContext(Dispatchers.IO) { AppIcons.load(ctx, lead.pkg) }?.asImageBitmap()
    }
    val h = hues
    val bmp = icon
    Box(
        modifier.size(ROW_ICON).clip(RoundedCornerShape(5.dp)).background(if (bmp == null) h.tint(h.of(Hue.TEAL)) else Color.Transparent),
        contentAlignment = Alignment.Center
    ) {
        if (bmp != null) Image(bmp, contentDescription = null, modifier = Modifier.size(ROW_ICON))
        else Text(lead.letter, style = MaterialTheme.typography.labelMedium.copy(fontSize = 11.sp, lineHeight = 11.sp), color = h.text(h.of(Hue.TEAL)))
    }
}

/** The ⋯ of a row or the stage: every secondary in server order, then "Ask Dana about this". */
@Composable
fun ItemOverflow(item: NowItem, actions: RowActions, handlers: NowHandlers, modifier: Modifier = Modifier) {
    val entries = actions.overflow.map { a -> MenuEntry(a.label) { handlers.perform(item, a, false) } } +
        MenuEntry("Ask Dana about this") { handlers.ask(item) }
    OverflowMenu(entries, modifier, divideBefore = actions.overflow.size)
}

/** The cover screen's open row, above its primary: evidence, choices / checks and their confirmation (the reply box follows the primary). */
@Composable
private fun Expanded(item: NowItem, draft: ItemDraft?, handlers: NowHandlers) {
    Column(Modifier.padding(top = 4.dp).fillMaxWidth()) {
        item.blocks.filterNot { it.isDecide() }.forEach { EvidenceBlock(it) }
        item.blocks.filter { it.isDecide() }.forEach { b ->
            DecideBlock(b, draft, { k, i -> handlers.choice(item, k, i) }, { k, i -> handlers.check(item, k, i) })
        }
        // The chosen option's confirmation, so the primary says what will happen.
        NowModel.choiceBlocks(item).firstNotNullOfOrNull { b -> b.options.getOrNull(NowModel.chosenIndex(b, draft))?.message }?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 8.dp))
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
            Row(Modifier.padding(top = 14.dp, bottom = 6.dp)) { MetaLabel(if (who != null) "Reply to $who" else "Reply") }
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
