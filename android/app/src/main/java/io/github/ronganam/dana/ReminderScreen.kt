package io.github.ronganam.dana

import android.view.HapticFeedbackConstants
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.outlined.DirectionsCar
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.WbSunny
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.LayoutCoordinates
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.ui.components.InkChip
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.components.StatusPill
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt

/*
 * The alarm screen ([ReminderActivity]): the header, the quick snoozes, the
 * snooze dial and its fan, drawn from [SnoozeDialGeometry].
 */

/**
 * The alarm screen, in the app's Ink & Paper language: mono kicker, serif
 * title, one vermilion accent, hairlines. Snooze either with one tap (the
 * quick row) or by holding the dial and dragging out to a time, place, the
 * car or waking up.
 *
 * Sized from the window it is in (Fold cover ≈ 555×876 dp, inner ≈ 821×1088
 * portrait / 1088×821 landscape): the title shrinks on short screens, the top
 * section scrolls instead of pushing the actions off, and wide landscape
 * windows split into two panes. The dial sits low, near the thumb, and its
 * options fan out *upward* so the hand never covers them; while dragging, a
 * readout at the top spells out what releasing will do.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ReminderScreen(
    taskTitle: String,
    priority: String,
    triggerType: String,
    onComplete: () -> Unit,
    onSnoozeSelected: (MenuNode.Leaf) -> Unit,
    onDismiss: () -> Unit,
    onInteractionStateChanged: (Boolean) -> Unit
) {
    val context = LocalContext.current
    // Place snoozes: the server's zones, else the last known ones.
    var zones by remember { mutableStateOf(io.github.ronganam.dana.data.network.Zones.cached(context)) }
    LaunchedEffect(Unit) { zones = io.github.ronganam.dana.data.network.Zones.fetch(context) }

    val menuTree = remember(zones) { buildSnoozeTree(zones) }

    val now = remember { java.time.LocalTime.now() }
    val (emoji, cleanTitle) = remember(taskTitle) { extractLeadingEmoji(taskTitle) }
    val pColor = when (priority.lowercase()) {
        "high" -> io.github.ronganam.dana.ui.theme.Danger
        "medium" -> io.github.ronganam.dana.ui.theme.Warning
        else -> io.github.ronganam.dana.ui.theme.Info
    }

    // Dial state shared between the dock (gesture) and the overlay (drawing).
    var activePath by remember { mutableStateOf<List<MenuNode>>(emptyList()) }
    val dragging = activePath.isNotEmpty()
    var rootCoords by remember { mutableStateOf<LayoutCoordinates?>(null) }
    var dialCenter by remember { mutableStateOf(Offset.Unspecified) }
    val fade by animateFloatAsState(if (dragging) 0.06f else 1f, tween(160), label = "fade")

    val density = LocalDensity.current
    val safeTopPx = WindowInsets.safeContent.getTop(density).toFloat()

    BoxWithConstraints(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .onGloballyPositioned { rootCoords = it }
    ) {
        val wide = maxWidth >= 720.dp && maxWidth > maxHeight
        val compact = maxHeight < 780.dp
        val widthPx = constraints.maxWidth.toFloat()
        val geometry = remember(dialCenter, widthPx, safeTopPx) {
            if (dialCenter == Offset.Unspecified) null
            else SnoozeDialGeometry.fit(
                origin = dialCenter,
                width = widthPx,
                topLimit = safeTopPx + with(density) { READOUT_HEIGHT.toPx() },
                density = density.density
            )
        }

        val header: @Composable () -> Unit = {
            ReminderHeader(
                kicker = "Reminder · ${"%02d:%02d".format(now.hour, now.minute)} · ${triggerLabel(triggerType)}",
                priority = priority,
                priorityColor = pColor,
                emoji = emoji,
                title = cleanTitle,
                compact = compact
            )
        }
        val quickSnoozes: @Composable () -> Unit = {
            Spacer(Modifier.height(if (compact) 10.dp else 14.dp))
            MetaLabel("Snooze")
            Spacer(Modifier.height(8.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                QUICK_SNOOZES.forEach { leaf ->
                    InkChip(leaf.label, onClick = { onSnoozeSelected(leaf) }, modifier = Modifier.heightIn(min = 40.dp))
                }
                InkChip(WAKE_LEAF.label, onClick = { onSnoozeSelected(WAKE_LEAF) }, modifier = Modifier.heightIn(min = 40.dp))
            }
        }
        val dock: @Composable (Modifier) -> Unit = { mod ->
            SnoozeDialDock(
                tree = menuTree,
                geometry = geometry,
                activePath = activePath,
                compact = compact,
                onPathChange = { activePath = it },
                onPositioned = { coords ->
                    val root = rootCoords
                    if (root != null && root.isAttached && coords.isAttached) {
                        val c = root.localPositionOf(coords, Offset(coords.size.width / 2f, coords.size.height / 2f))
                        if (c != dialCenter) dialCenter = c
                    }
                },
                onSelected = onSnoozeSelected,
                onInteractionStateChanged = onInteractionStateChanged,
                modifier = mod
            )
        }
        val actions: @Composable () -> Unit = {
            Rule()
            Spacer(Modifier.height(if (compact) 12.dp else 16.dp))
            Button(
                onClick = onComplete,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(if (compact) 52.dp else 56.dp)
            ) {
                Icon(Icons.Filled.Check, contentDescription = null, modifier = Modifier.size(20.dp))
                Spacer(Modifier.width(8.dp))
                Text("Mark complete", style = MaterialTheme.typography.titleMedium)
            }
            Spacer(Modifier.height(4.dp))
            TextButton(onClick = onDismiss, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
                Text("Dismiss", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        if (wide) {
            // Inner screen in landscape: words and actions left, the dial right.
            Row(
                Modifier
                    .fillMaxSize()
                    .wrapContentWidth()
                    .widthIn(max = 1100.dp)
                    .fillMaxWidth()
                    .safeContentPadding()
                    .padding(horizontal = 32.dp, vertical = 20.dp),
                horizontalArrangement = Arrangement.spacedBy(40.dp)
            ) {
                Column(Modifier.weight(1f).fillMaxHeight().alpha(fade)) {
                    Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
                        header()
                        Spacer(Modifier.height(20.dp))
                        Rule()
                        quickSnoozes()
                    }
                    actions()
                }
                // The dial in the middle of its pane (the fan opens up and to the sides).
                Box(Modifier.weight(1f).fillMaxHeight(), contentAlignment = Alignment.Center) {
                    dock(Modifier.padding(top = 120.dp))
                }
            }
        } else {
            Column(
                Modifier
                    .align(Alignment.TopCenter)
                    .fillMaxHeight()
                    .widthIn(max = 600.dp)
                    .fillMaxWidth()
                    .safeContentPadding()
                    .padding(horizontal = 24.dp, vertical = if (compact) 12.dp else 16.dp)
            ) {
                // Everything above the dial scrolls if a long title needs it, so
                // the dial and the actions always stay on screen.
                Column(
                    Modifier
                        .weight(1f)
                        .alpha(fade)
                        .verticalScroll(rememberScrollState())
                ) {
                    header()
                    Spacer(Modifier.height(if (compact) 14.dp else 20.dp))
                    Rule()
                    quickSnoozes()
                }
                dock(Modifier.fillMaxWidth().padding(vertical = if (compact) 8.dp else 16.dp))
                Column(Modifier.alpha(fade)) { actions() }
            }
        }

        // While dragging: the fan over the whole window, and the readout on top.
        if (dragging && geometry != null) {
            SnoozeFanOverlay(tree = menuTree, geometry = geometry, activePath = activePath, origin = dialCenter)
            SnoozeReadout(
                path = activePath,
                modifier = Modifier
                    // Two-pane: over the dial's pane; otherwise centred.
                    .align(if (wide) Alignment.TopEnd else Alignment.TopCenter)
                    .then(if (wide) Modifier.fillMaxWidth(0.5f) else Modifier)
                    .padding(top = with(density) { safeTopPx.toDp() } + 16.dp)
                    .padding(horizontal = 24.dp)
                    .widthIn(max = 560.dp)
                    .fillMaxWidth()
            )
        }
    }
}

@Composable
private fun ReminderHeader(
    kicker: String,
    priority: String,
    priorityColor: Color,
    emoji: String?,
    title: String,
    compact: Boolean
) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        MetaLabel(kicker, color = MaterialTheme.colorScheme.primary, modifier = Modifier.weight(1f))
        StatusPill(priority.replaceFirstChar { it.uppercase() }, priorityColor)
    }
    Spacer(Modifier.height(if (compact) 10.dp else 14.dp))
    Row(verticalAlignment = Alignment.Top) {
        if (emoji != null) {
            Text(
                emoji,
                fontSize = if (compact) 30.sp else 40.sp,
                modifier = Modifier.padding(end = 12.dp, top = 2.dp)
            )
        }
        Text(
            text = title,
            style = (if (compact) MaterialTheme.typography.headlineLarge else MaterialTheme.typography.displaySmall)
                .copy(textDirection = TextDirection.Content),
            color = MaterialTheme.colorScheme.onBackground,
            maxLines = if (compact) 3 else 4,
            overflow = TextOverflow.Ellipsis
        )
    }
}

/** Room kept clear at the top for the readout while dragging. */
private val READOUT_HEIGHT = 128.dp

private fun triggerLabel(type: String) = when (type.lowercase()) {
    "time" -> "On time"
    "location" -> "Location"
    "car" -> "Car"
    "wake" -> "Wake up"
    else -> type.replace("_", " ")
}

private fun level1Icon(label: String) = when (label) {
    "Time based" -> Icons.Outlined.Schedule
    "Location based" -> Icons.Outlined.Place
    "Car based" -> Icons.Outlined.DirectionsCar
    "When I wake up" -> Icons.Outlined.WbSunny
    else -> null
}

private fun shortLabel(node: MenuNode) = when (node.label) {
    "Time based" -> "Time"
    "Location based" -> "Place"
    "Car based" -> "Car"
    "When I wake up" -> "Wake up"
    else -> node.label
}

/**
 * The dial at rest: a hairline arc with four ticks above it (where the
 * categories will open), the "Hold" button, and a one-line hint. Owns the
 * gesture; the fan itself is drawn by [SnoozeFanOverlay] over the whole window.
 */
@Composable
private fun SnoozeDialDock(
    tree: MenuNode.Parent,
    geometry: SnoozeDialGeometry?,
    activePath: List<MenuNode>,
    compact: Boolean,
    onPathChange: (List<MenuNode>) -> Unit,
    onPositioned: (LayoutCoordinates) -> Unit,
    onSelected: (MenuNode.Leaf) -> Unit,
    onInteractionStateChanged: (Boolean) -> Unit,
    modifier: Modifier = Modifier
) {
    val view = LocalView.current
    val accent = MaterialTheme.colorScheme.primary
    val onAccent = MaterialTheme.colorScheme.onPrimary
    val outline = MaterialTheme.colorScheme.outline
    val hairline = MaterialTheme.colorScheme.outlineVariant
    val paper = MaterialTheme.colorScheme.background
    val ink = MaterialTheme.colorScheme.onSurface
    val inkSoft = MaterialTheme.colorScheme.onSurfaceVariant
    val dragging = activePath.isNotEmpty()
    val dialSize = if (compact) 96.dp else 108.dp

    // The gesture loop outlives recompositions; read the latest values through these.
    val currentTree by rememberUpdatedState(tree)
    val currentGeometry by rememberUpdatedState(geometry)
    val currentPath by rememberUpdatedState(activePath)

    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            Modifier.size(dialSize + 72.dp, dialSize + 40.dp),
            contentAlignment = Alignment.BottomCenter
        ) {
            // Idle hint: an arc of ticks above the dial where the options will open.
            Canvas(Modifier.fillMaxSize().alpha(if (dragging) 0f else 1f)) {
                val c = Offset(size.width / 2f, size.height - dialSize.toPx() / 2f)
                val r = dialSize.toPx() / 2f + 22.dp.toPx()
                drawArc(
                    color = hairline,
                    startAngle = 180f, sweepAngle = 180f, useCenter = false,
                    topLeft = Offset(c.x - r, c.y - r), size = Size(r * 2, r * 2),
                    style = Stroke(width = 1.dp.toPx())
                )
                for (k in 0 until 4) {
                    val rad = Math.toRadians(-180.0 + 22.5 + 45.0 * k)
                    drawCircle(outline, 3.dp.toPx(), Offset(c.x + r * cos(rad).toFloat(), c.y + r * sin(rad).toFloat()))
                }
            }
            Box(
                modifier = Modifier
                    .size(dialSize)
                    .onGloballyPositioned(onPositioned)
                    .clip(CircleShape)
                    .background(if (dragging) accent else paper)
                    .border(1.dp, if (dragging) accent else outline, CircleShape)
                    .semantics { contentDescription = "Snooze dial: hold and drag up to pick when" }
                    .pointerInput(Unit) {
                        awaitPointerEventScope {
                            while (true) {
                                val down = awaitFirstDown(requireUnconsumed = false)
                                val geo = currentGeometry ?: continue
                                val center = Offset(size.width / 2f, size.height / 2f)
                                down.consume()
                                onInteractionStateChanged(true)
                                var path: List<MenuNode> = listOf(currentTree)
                                onPathChange(path)
                                view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                                var released = false
                                while (!released) {
                                    val event = awaitPointerEvent()
                                    val change = event.changes.firstOrNull { it.id == down.id } ?: break
                                    if (change.pressed) {
                                        change.consume()
                                        val next = selectSnoozePath(change.position - center, currentTree, currentGeometry ?: geo, path)
                                        if (next != path) {
                                            path = next
                                            onPathChange(next)
                                            view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                                        }
                                    } else {
                                        released = true
                                    }
                                }
                                onPathChange(emptyList())
                                onInteractionStateChanged(false)
                                val leaf = path.lastOrNull() as? MenuNode.Leaf
                                if (released && leaf != null) {
                                    view.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
                                    onSelected(leaf)
                                }
                            }
                        }
                    },
                contentAlignment = Alignment.Center
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = "HOLD",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (dragging) onAccent.copy(alpha = 0.75f) else inkSoft
                    )
                    Text(
                        text = "Snooze",
                        style = MaterialTheme.typography.headlineSmall,
                        color = if (dragging) onAccent else ink
                    )
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        MetaLabel(
            "Hold & drag up · time · place · car",
            color = inkSoft,
            modifier = Modifier.alpha(if (dragging) 0f else 1f)
        )
    }
}

/** The open fan, drawn over the whole window around the dial ([origin], px). Touch-transparent. */
@Composable
private fun SnoozeFanOverlay(
    tree: MenuNode.Parent,
    geometry: SnoozeDialGeometry,
    activePath: List<MenuNode>,
    origin: Offset
) {
    val accent = MaterialTheme.colorScheme.primary
    val onAccent = MaterialTheme.colorScheme.onPrimary
    val hairline = MaterialTheme.colorScheme.outlineVariant
    val outline = MaterialTheme.colorScheme.outline
    val paper = MaterialTheme.colorScheme.background
    val ink = MaterialTheme.colorScheme.onSurface
    val density = LocalDensity.current
    val nodes = remember(tree, geometry, activePath) { layoutSnoozeFan(tree, geometry, activePath) }

    Box(Modifier.fillMaxSize()) {
        Canvas(Modifier.fillMaxSize()) {
            val hair = 1.dp.toPx()
            nodes.forEach { vn ->
                drawLine(
                    color = if (vn.isHighlighted) accent else hairline,
                    // Inner-ring spokes start at the dial's edge, not across its face.
                    start = if (vn.parentX == 0f && vn.parentY == 0f) {
                        val len = sqrt(vn.x * vn.x + vn.y * vn.y).coerceAtLeast(1f)
                        val edge = 56.dp.toPx()
                        Offset(origin.x + vn.x / len * edge, origin.y + vn.y / len * edge)
                    } else Offset(origin.x + vn.parentX, origin.y + vn.parentY),
                    end = Offset(origin.x + vn.x, origin.y + vn.y),
                    strokeWidth = if (vn.isHighlighted) 2.dp.toPx() else hair
                )
            }
            nodes.forEach { vn ->
                val c = Offset(origin.x + vn.x, origin.y + vn.y)
                drawCircle(if (vn.isHighlighted) accent else paper, vn.radiusPx, c)
                drawCircle(if (vn.isHighlighted) accent else outline, vn.radiusPx, c, style = Stroke(hair))
            }
        }
        nodes.forEach { vn ->
            val d = with(density) { (vn.radiusPx * 2).toDp() }
            Box(
                Modifier
                    .offset { IntOffset((origin.x + vn.x - vn.radiusPx).roundToInt(), (origin.y + vn.y - vn.radiusPx).roundToInt()) }
                    .size(d),
                contentAlignment = Alignment.Center
            ) {
                val icon = level1Icon(vn.label)
                val tint = if (vn.isHighlighted) onAccent else ink
                if (icon != null) {
                    Icon(icon, contentDescription = vn.label, tint = tint, modifier = Modifier.size(24.dp))
                } else {
                    // Sans, sentence case: mono caps break mid-word in a 60 dp circle.
                    Text(
                        text = vn.label,
                        style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content),
                        fontSize = if (vn.label.length > 9) 11.sp else 12.sp,
                        color = tint,
                        lineHeight = 13.sp,
                        textAlign = TextAlign.Center,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.padding(horizontal = 5.dp)
                    )
                }
            }
        }
    }
}

/** Top-of-screen readout while dragging: where you are, and what letting go does. */
@Composable
private fun SnoozeReadout(path: List<MenuNode>, modifier: Modifier = Modifier) {
    val (headline, hint) = snoozeReadout(path)
    val onLeaf = path.lastOrNull() is MenuNode.Leaf
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        MetaLabel(
            (listOf("Snooze") + path.drop(1).map(::shortLabel)).joinToString("  ›  "),
            color = MaterialTheme.colorScheme.primary
        )
        Spacer(Modifier.height(8.dp))
        Text(
            headline,
            style = MaterialTheme.typography.headlineMedium.copy(textDirection = TextDirection.Content),
            color = MaterialTheme.colorScheme.onBackground,
            textAlign = TextAlign.Center,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis
        )
        Spacer(Modifier.height(6.dp))
        Text(
            hint,
            style = MaterialTheme.typography.bodyMedium,
            color = if (onLeaf) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

fun extractLeadingEmoji(text: String): Pair<String?, String> {
    if (text.isEmpty()) return Pair(null, text)
    
    val codePoint = text.codePointAt(0)
    
    val isEmoji = (codePoint in 0x1F300..0x1F9FF) || 
                  (codePoint in 0x1FA00..0x1FAFF) ||
                  (codePoint in 0x2600..0x27BF) ||
                  (codePoint in 0x1F1E6..0x1F1FF)
                  
    if (isEmoji) {
        val emojiStr = String(Character.toChars(codePoint))
        var remaining = text.substring(emojiStr.length)
        remaining = remaining.trimStart { it == ' ' || it == '-' || it == ':' || it == '–' }
        return Pair(emojiStr, remaining)
    }
    
    return Pair(null, text)
}
