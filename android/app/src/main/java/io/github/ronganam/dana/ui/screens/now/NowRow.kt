package io.github.ronganam.dana.ui.screens.now

import androidx.compose.animation.core.Animatable
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.gestures.draggable
import androidx.compose.foundation.gestures.rememberDraggableState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AutoMode
import androidx.compose.material.icons.outlined.DirectionsBus
import androidx.compose.material.icons.outlined.DirectionsCar
import androidx.compose.material.icons.outlined.Laptop
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material.icons.outlined.Timer
import androidx.compose.material.icons.outlined.Train
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.isTraversalGroup
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.NowAction
import io.github.ronganam.dana.data.models.NowItem
import io.github.ronganam.dana.data.notifications.AppIcons
import io.github.ronganam.dana.data.notifications.PhoneNotificationActions
import io.github.ronganam.dana.ui.components.MenuEntry
import io.github.ronganam.dana.ui.components.OverflowMenu
import io.github.ronganam.dana.ui.components.circuit.CircuitNode
import io.github.ronganam.dana.ui.components.circuit.LocalNodeSurface
import io.github.ronganam.dana.ui.components.circuit.NodeShape
import io.github.ronganam.dana.ui.components.circuit.NodeSize
import io.github.ronganam.dana.ui.components.circuit.NodeState
import io.github.ronganam.dana.ui.screens.common.isolatedParts
import io.github.ronganam.dana.ui.shell.DaySlot
import io.github.ronganam.dana.ui.theme.Motion
import io.github.ronganam.dana.ui.theme.circuitColors
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.math.roundToInt

internal val NOW_GUTTER = 16.dp

/** Everything a Now row can do; built once by the destination. */
class NowHandlers(
    /** Run an action (server or client) on a card. */
    val perform: (item: NowItem, action: NowAction, isPrimary: Boolean) -> Unit,
    /** Row tapped: open / close it in place. */
    val toggle: (NowItem) -> Unit,
    /** "Ask Dana about this". */
    val ask: (NowItem) -> Unit,
    /** Send a reply to a notification item through the shade. */
    val reply: (NowItem, String) -> Unit,
    val choice: (NowItem, key: String, index: Int) -> Unit,
    val check: (NowItem, key: String, index: Int) -> Unit,
    /** Kept for the server's slot actions (no drag target on the redesigned Now). */
    val schedule: (NowItem, DaySlot) -> Unit
)

/** What the live rows can do. */
class LiveHandlers(
    /** A `server` button (Approve / Deny). */
    val act: (NowLiveRow, String) -> Unit,
    /** The row tapped: where its `open` leads. */
    val open: (NowLiveRow) -> Unit
)

/** Whether a kind is something that waits on the owner (its node wears the warm ring). */
internal fun needsRing(item: NowItem): Boolean = item.kind in setOf("approval", "notification", "reminder")

/**
 * A row of "Needs you", one of a tonal group (its [shape] joins it to the
 * rows around it). Folded or [featured]: a card on the warm surface with
 * the evidence and a button row (the primary filled, two more as text, the
 * rest and "Ask Dana about this" under ⋯). Otherwise a compact row: the
 * node, the title and one line, its primary as a text action at the end; a
 * tap opens it into the card. Swipes: towards the start = dismiss, towards
 * the end = the card's own snooze (each only when the card offers it; both
 * answer with Undo). Swipe, open and the actions are accessibility actions too.
 */
@Composable
fun NeedsRow(
    item: NowItem,
    draft: ItemDraft?,
    expanded: Boolean,
    featured: Boolean,
    shape: Shape,
    handlers: NowHandlers,
    modifier: Modifier = Modifier
) {
    val c = circuitColors
    val actions = remember(item) { NowModel.rowActions(item) }
    val dismiss = actions.dismiss
    val snooze = actions.snooze
    val density = LocalDensity.current
    val threshold = with(density) { 96.dp.toPx() }
    val offset = remember(item.id) { Animatable(0f) }
    val scope = rememberCoroutineScope()
    val open = expanded || featured
    val surface = if (featured && needsRing(item)) c.warmSurface else MaterialTheme.colorScheme.surfaceContainerLow

    fun swipeOut(action: NowAction, direction: Float, width: Float) {
        scope.launch {
            offset.animateTo(direction * width, Motion.leave())
            handlers.perform(item, action, action === item.primary)
            delay(Motion.COMPLETE + 200L)
            offset.snapTo(0f)
        }
    }

    BoxWithConstraints(modifier.fillMaxWidth().padding(horizontal = NOW_GUTTER).clipToBounds()) {
        val width = constraints.maxWidth.toFloat()
        // What a swipe will do, revealed beside the row as it moves.
        if (offset.value != 0f) Row(Modifier.matchParentSize().padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            if (offset.value > 0f && snooze != null) Text(
                "Snooze · " + snooze.label,
                style = MaterialTheme.typography.labelLarge.copy(textDirection = TextDirection.Content),
                color = c.accent
            )
            Spacer(Modifier.weight(1f))
            if (offset.value < 0f && dismiss != null) Text(
                dismiss.label,
                style = MaterialTheme.typography.labelLarge.copy(textDirection = TextDirection.Content),
                color = c.inkSoft
            )
        }
        Column(
            Modifier
                .fillMaxWidth()
                .offset { IntOffset(offset.value.roundToInt(), 0) }
                .clip(shape)
                .background(surface)
                .draggable(
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
                .semantics {
                    isTraversalGroup = true
                    customActions = buildList {
                        if (!featured) add(CustomAccessibilityAction(if (expanded) "Less" else "More") { handlers.toggle(item); true })
                        if (snooze != null) add(CustomAccessibilityAction("Snooze · ${snooze.label}") { handlers.perform(item, snooze, false); true })
                        if (dismiss != null) add(CustomAccessibilityAction(dismiss.label) { handlers.perform(item, dismiss, dismiss === item.primary); true })
                    }
                }
        ) {
            CompositionLocalProvider(LocalNodeSurface provides surface) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .then(if (featured) Modifier else Modifier.clickable(onClickLabel = if (expanded) "Less" else "More") { handlers.toggle(item) })
                        .heightIn(min = 56.dp)
                        .padding(start = 16.dp, end = 4.dp, top = 6.dp, bottom = 6.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    ItemNode(item)
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f).padding(vertical = 4.dp)) {
                        Text(
                            item.title,
                            style = MaterialTheme.typography.titleMedium.copy(
                                fontWeight = if (open) FontWeight.SemiBold else FontWeight.Medium, textDirection = TextDirection.Content
                            ),
                            color = c.ink,
                            maxLines = if (open) 3 else 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.fillMaxWidth().semantics { heading() }
                        )
                        val meta = remember(item) { NowModel.metaLine(item.source, item.why) }
                        if (meta.isNotEmpty()) Text(
                            isolatedParts(meta),
                            style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
                            color = c.inkSoft,
                            maxLines = if (open) 3 else 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.fillMaxWidth()
                        )
                    }
                    if (!open) actions.primary?.let { p ->
                        Spacer(Modifier.width(4.dp))
                        TextAction(NowModel.primaryLabel(item, draft) ?: p.label, c.accent) { handlers.perform(item, p, true) }
                    } ?: Spacer(Modifier.width(12.dp))
                }
                if (open) OpenCard(item, draft, actions, handlers, warm = surface == c.warmSurface)
            }
        }
    }
}

/** The open part of a card, under its title row, indented to the text: evidence, decisions, reply, the button row. */
@Composable
private fun OpenCard(item: NowItem, draft: ItemDraft?, actions: RowActions, handlers: NowHandlers, warm: Boolean) {
    val c = circuitColors
    Column(Modifier.fillMaxWidth().padding(start = 16.dp + 32.dp + 12.dp, end = 16.dp, bottom = 8.dp)) {
        item.blocks.filterNot { it.isDecide() }.forEach { EvidenceBlock(it) }
        item.blocks.filter { it.isDecide() }.forEach { b ->
            DecideBlock(b, draft, { k, i -> handlers.choice(item, k, i) }, { k, i -> handlers.check(item, k, i) })
        }
        // The chosen option's confirmation, so the primary says what will happen.
        NowModel.choiceBlocks(item).firstNotNullOfOrNull { b -> b.options.getOrNull(NowModel.chosenIndex(b, draft))?.message }?.let {
            Text(it, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(top = 8.dp))
        }
        ReplyBox(item, handlers)
        val textColor = if (warm) c.warm else c.accent
        val shown = actions.overflow.take(2)
        val rest = actions.overflow.drop(2)
        Row(Modifier.fillMaxWidth().padding(top = 8.dp).offset(x = (-4).dp), verticalAlignment = Alignment.CenterVertically) {
            actions.primary?.let { p ->
                FilledAction(NowModel.primaryLabel(item, draft) ?: p.label) { handlers.perform(item, p, true) }
            }
            shown.forEach { a -> TextAction(a.label, textColor) { handlers.perform(item, a, false) } }
            Spacer(Modifier.weight(1f))
            OverflowMenu(
                rest.map { a -> MenuEntry(a.label) { handlers.perform(item, a, false) } } + MenuEntry("Ask Dana about this") { handlers.ask(item) },
                divideBefore = rest.size
            )
        }
    }
}

/**
 * A waiting run (a live activity that needs your OK): the card on the warm
 * surface with the waiting node breathing, what it wants to do, and its
 * buttons (the first filled, the second as text); Open leads to its conversation.
 */
@Composable
fun LiveWaitingCard(row: NowLiveRow, shape: Shape, handlers: LiveHandlers, modifier: Modifier = Modifier) {
    val c = circuitColors
    Column(modifier.fillMaxWidth().padding(horizontal = NOW_GUTTER).clip(shape).background(c.warmSurface)) {
        CompositionLocalProvider(LocalNodeSurface provides c.warmSurface) {
            Row(
                Modifier.fillMaxWidth().clickable(onClickLabel = "Open") { handlers.open(row) }
                    .heightIn(min = 56.dp).padding(start = 16.dp, end = 16.dp, top = 10.dp, bottom = 4.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                CircuitNode(NodeShape.RoundedSquare, NodeState.Waiting, size = NodeSize.Leading, icon = row.glyph.vector())
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(
                        row.title,
                        style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold, textDirection = TextDirection.Content),
                        color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.semantics { heading() }
                    )
                    if (row.line.isNotBlank()) Text(
                        row.line,
                        style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
                        color = c.inkSoft, maxLines = 3, overflow = TextOverflow.Ellipsis
                    )
                }
            }
            Row(
                Modifier.fillMaxWidth().padding(start = 16.dp + 32.dp + 12.dp - 4.dp, end = 12.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                row.actions.forEachIndexed { i, a ->
                    if (i == 0) FilledAction(a.label) { handlers.act(row, a.id) } else TextAction(a.label, c.warm) { handlers.act(row, a.id) }
                }
                Spacer(Modifier.weight(1f))
                if (row.open.isNotBlank()) TextAction("Open", c.warm) { handlers.open(row) }
            }
        }
    }
}

/** A row's leading node: a ring (warm for something waiting on you) with the kind's glyph, or the app's icon in it. */
@Composable
internal fun ItemNode(item: NowItem) {
    val c = circuitColors
    val lead = remember(item) { NowModel.rowLead(item) }
    val visual = remember(item) { NowModel.visual(item) }
    val ring = if (needsRing(item)) c.warm else Color.Unspecified
    Box(Modifier.size(32.dp), contentAlignment = Alignment.Center) {
        if (lead is RowLead.App) {
            CircuitNode(NodeShape.Circle, NodeState.Idle, size = NodeSize.Leading, strokeColor = ring)
            RowAppIcon(lead)
        } else {
            val glyph = (visual as? NowVisual.Glyph)?.glyph ?: NowGlyph.NOTIFICATION
            CircuitNode(NodeShape.Circle, NodeState.Idle, size = NodeSize.Leading, icon = glyph.vector(), strokeColor = ring, glyphColor = c.inkSoft)
        }
    }
}

/** A row's lead is this big when it is an app icon. */
internal val ROW_ICON = 18.dp

/** A notification item's app icon (saved by [AppIcons]) inside its ring; until there is one, the app's letter. Decorative. */
@Composable
private fun RowAppIcon(lead: RowLead.App) {
    val ctx = LocalContext.current.applicationContext
    val icon by produceState<ImageBitmap?>(AppIcons.cached(lead.pkg)?.asImageBitmap(), lead.pkg) {
        if (value == null) value = withContext(Dispatchers.IO) { AppIcons.load(ctx, lead.pkg) }?.asImageBitmap()
    }
    val bmp = icon
    if (bmp != null) Image(bmp, contentDescription = null, modifier = Modifier.size(ROW_ICON).clip(CircleShape))
    else Text(lead.letter, style = MaterialTheme.typography.labelLarge, color = circuitColors.inkSoft)
}

/** The filled button of a card (44dp, the accent fill). */
@Composable
internal fun FilledAction(text: String, onClick: () -> Unit) {
    val c = circuitColors
    Box(
        Modifier
            .heightIn(min = 44.dp)
            .clip(RoundedCornerShape(22.dp))
            .background(c.accentFilled)
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = 20.dp, vertical = 10.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(text, style = MaterialTheme.typography.labelLarge.copy(textDirection = TextDirection.Content), color = c.onAccentFilled, maxLines = 1)
    }
}

/** A text button (44dp touch), a row's trailing action or a card's other actions. */
@Composable
internal fun TextAction(text: String, color: Color, onClick: () -> Unit) {
    Box(
        Modifier
            .heightIn(min = 44.dp)
            .clip(RoundedCornerShape(22.dp))
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 10.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text,
            style = MaterialTheme.typography.labelLarge.copy(textDirection = TextDirection.Content),
            color = color,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
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
    val c = circuitColors
    val n = item.notification
    val key = n?.key
    // Read live: what the shade can do now (the server's can_reply may be minutes old).
    val button = remember(key, item) { if (n == null) null else PhoneNotificationActions.replyButton(key) }
    val quick = remember(key, item) { if (button == null) emptyList() else PhoneNotificationActions.quickReplies(key) }
    var text by rememberSaveable(item.id) { mutableStateOf("") }
    if (n != null && button != null) {
        Column(Modifier.fillMaxWidth().padding(top = 8.dp)) {
            if (quick.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                quick.forEach { q -> TextAction(q, c.accent) { text = q } }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                BasicTextField(
                    value = text,
                    onValueChange = { text = it },
                    textStyle = MaterialTheme.typography.bodyLarge.copy(color = c.ink, textDirection = TextDirection.Content),
                    cursorBrush = SolidColor(c.accent),
                    maxLines = 4,
                    keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { if (text.isNotBlank()) { handlers.reply(item, text.trim()); text = "" } }),
                    modifier = Modifier.weight(1f),
                    decorationBox = { inner ->
                        Box(
                            Modifier.fillMaxWidth().heightIn(min = 44.dp).clip(RoundedCornerShape(16.dp))
                                .background(MaterialTheme.colorScheme.surfaceContainerHighest).padding(horizontal = 14.dp, vertical = 10.dp),
                            contentAlignment = Alignment.CenterStart
                        ) {
                            if (text.isEmpty()) Text("Write a reply", style = MaterialTheme.typography.bodyLarge, color = c.mute)
                            inner()
                        }
                    }
                )
                if (text.isNotBlank()) TextAction("Send", c.accent) {
                    handlers.reply(item, text.trim())
                    text = ""
                }
            }
        }
    }
}

/** A glyph for a live activity's kind. */
internal fun LiveGlyph.vector(): ImageVector = when (this) {
    LiveGlyph.RUN -> Icons.Outlined.AutoMode
    LiveGlyph.CODE -> Icons.Outlined.Laptop
    LiveGlyph.TRAIN -> Icons.Outlined.Train
    LiveGlyph.BUS -> Icons.Outlined.DirectionsBus
    LiveGlyph.CAR -> Icons.Outlined.DirectionsCar
    LiveGlyph.TIMER -> Icons.Outlined.Timer
    LiveGlyph.OTHER -> Icons.Outlined.Sync
}
