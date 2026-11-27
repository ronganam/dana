package io.github.ronganam.dana.ui.screens.reminders

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.DragIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.boundsInRoot
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.ui.components.circuit.CircuitNode
import io.github.ronganam.dana.ui.components.circuit.LocalNodeSurface
import io.github.ronganam.dana.ui.components.circuit.NodeShape
import io.github.ronganam.dana.ui.components.circuit.NodeSize
import io.github.ronganam.dana.ui.components.circuit.NodeState
import io.github.ronganam.dana.ui.components.circuit.PlaceHeader
import io.github.ronganam.dana.ui.shell.rememberNow
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.tabular
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import kotlin.math.roundToInt

private val HM: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm")

/**
 * Drag state of the board: the reminder being dragged, the finger, and the
 * moments that take a drop (their bounds in root coordinates, registered by
 * [boardTarget]). A drop sets the reminder's moment to the one under the
 * finger, replacing what it had; the tray takes a drop as "no moment".
 */
class BoardDrag {
    var task by mutableStateOf<Task?>(null)
        private set
    var pointer by mutableStateOf(Offset.Zero)
        private set
    var hovered by mutableStateOf<String?>(null)
        private set
    internal val targets = mutableMapOf<String, Pair<Rect, RemindWhen>>()

    fun start(t: Task, at: Offset) { task = t; move(at) }
    fun move(at: Offset) {
        pointer = at
        hovered = targets.entries.firstOrNull { it.value.first.contains(at) }?.key
    }
    fun drop(onDrop: (Task, RemindWhen) -> Unit) {
        val t = task
        val w = hovered?.let { targets[it]?.second }
        task = null
        hovered = null
        if (t != null && w != null) onDrop(t, w)
    }
    fun cancel() { task = null; hovered = null }
}

private fun Modifier.boardTarget(drag: BoardDrag, key: String, w: RemindWhen): Modifier = composed {
    DisposableEffect(drag, key) { onDispose { drag.targets.remove(key) } }
    Modifier.onGloballyPositioned { drag.targets[key] = it.boundsInRoot() to w }
}

private fun Modifier.boardDraggable(drag: BoardDrag, task: Task, onDrop: (Task, RemindWhen) -> Unit): Modifier = composed {
    val haptics = LocalHapticFeedback.current
    val current by rememberUpdatedState(task)
    val drop by rememberUpdatedState(onDrop)
    var origin by remember { mutableStateOf(Offset.Zero) }
    Modifier
        .onGloballyPositioned { origin = it.positionInRoot() }
        .pointerInput(drag) {
            detectDragGesturesAfterLongPress(
                onDragStart = { at ->
                    haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                    drag.start(current, origin + at)
                },
                onDrag = { change, _ -> change.consume(); drag.move(origin + change.position) },
                onDragEnd = { drag.drop(drop) },
                onDragCancel = { drag.cancel() }
            )
        }
}

/**
 * Unfolded Reminders, the board: the tray of reminders with no moment (the
 * late ones first) | the next 24 hours as a line of moment nodes (times and
 * waking, each holding its reminders; later times under it) | anytime (each
 * saved place's arrive and leave, the car). Long-press a reminder and drop
 * it on a node to give it that moment (saved through the API, with Undo);
 * drop it on the tray to clear it. A tap opens the moment sheet.
 */
@Composable
fun ReminderBoard(viewModel: MainViewModel, onMoments: (String) -> Unit, onSuggest: () -> Unit, headerExtra: @Composable () -> Unit) {
    val tc = viewModel.tasksController
    val tasks by tc.tasks.collectAsState()
    val zones by tc.zones.collectAsState()
    val now by rememberNow()
    val drag = remember { BoardDrag() }
    var adding by remember { mutableStateOf(false) }

    val open = remember(tasks) { tasks.filter(::isOpen) }
    val tray = remember(open, now) { open.filter { momentGroupOf(it, now).let { g -> g == MomentGroup.NONE || g == MomentGroup.LATE } } }
    val late = tray.filter { momentGroupOf(it, now) == MomentGroup.LATE }
    val notDue = tray - late.toSet()
    val moments = remember(open, now) { boardMoments(now, open) }
    val later = remember(open, now) {
        open.filter { t -> momentGroupOf(t, now) == MomentGroup.LATER }.sortedBy { remindWhens(it).filterIsInstance<RemindWhen.At>().firstOrNull()?.time }
    }
    val anytime = remember(zones) { anytimeMoments(zones) }
    val onDrop: (Task, RemindWhen) -> Unit = { t, w ->
        if (t.triggers != w.toTriggers()) tc.setTriggers(t.id, w.toTriggers(), dropPhrase(w, now))
    }
    var boardOrigin by remember { mutableStateOf(Offset.Zero) }

    Box(Modifier.fillMaxSize().onGloballyPositioned { boardOrigin = it.positionInRoot() }) {
        Column(Modifier.fillMaxSize()) {
            PlaceHeader(
                "Reminders",
                supporting = "Drag a reminder onto the moment it should reach you.",
                trailing = {
                    IconButton(onClick = { adding = !adding }) { Icon(Icons.Outlined.Add, contentDescription = "Add a reminder", tint = circuitColors.ink) }
                    headerExtra()
                }
            )
            if (adding) BoardQuickAdd(onAdd = { title -> tc.quickAdd(title, null) { id -> onMoments(id) }; adding = false }, onCancel = { adding = false })
            Row(
                Modifier.weight(1f).fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                // The tray: no moment.
                BoardColumn(
                    "No moment", "These won't reach you on their own.",
                    Modifier.weight(0.9f).boardTarget(drag, "tray", RemindWhen.None),
                    highlighted = drag.hovered == "tray"
                ) {
                    if (late.isNotEmpty()) {
                        SubHead("Late", circuitColors.warm)
                        late.forEach { t -> BoardCard(t, now, drag, onDrop, onMoments, late = true) }
                    }
                    if (notDue.isNotEmpty()) {
                        if (late.isNotEmpty()) SubHead("Not due yet", circuitColors.inkSoft)
                        notDue.forEach { t -> BoardCard(t, now, drag, onDrop, onMoments, showLine = t.dueDate != null) }
                    }
                    if (tray.isEmpty()) Text("Every reminder has a moment.", style = MaterialTheme.typography.bodyMedium, color = circuitColors.inkSoft)
                    if (tray.isNotEmpty()) TextButton(onClick = onSuggest, modifier = Modifier.padding(top = 4.dp)) { Text("Ask Dana for moments") }
                }
                // The next 24 hours.
                BoardColumn("Next 24 hours", "Drop on a time or on waking.", Modifier.weight(1.3f)) {
                    NowNode(now)
                    var lastDay = now.toLocalDate()
                    moments.forEachIndexed { i, m ->
                        val day = (m as? BoardMoment.At)?.time?.toLocalDate()
                        if (day != null && day != lastDay) {
                            lastDay = day
                            DayLabel("Tomorrow")
                        } else if (m == BoardMoment.Waking && lastDay == now.toLocalDate() && now.hour >= 7) {
                            lastDay = now.toLocalDate().plusDays(1)
                            DayLabel("Tomorrow")
                        }
                        val key = when (m) { is BoardMoment.At -> "t_${m.time}"; BoardMoment.Waking -> "wake" }
                        val held = tasksAt(m, open)
                        MomentNodeRow(
                            time = (m as? BoardMoment.At)?.time?.format(HM),
                            title = when (m) { is BoardMoment.At -> if (held.isEmpty()) null else "At a time"; BoardMoment.Waking -> "On waking" },
                            w = m.remindWhen,
                            hovered = drag.hovered == key,
                            last = i == moments.lastIndex,
                            modifier = Modifier.boardTarget(drag, key, m.remindWhen)
                        ) { held.forEach { t -> BoardCard(t, now, drag, onDrop, onMoments) } }
                    }
                    if (later.isNotEmpty()) {
                        SubHead("Later", circuitColors.inkSoft)
                        later.forEach { t -> BoardCard(t, now, drag, onDrop, onMoments, showLine = true) }
                    }
                }
                // Anytime: places and the car.
                BoardColumn("Anytime", "Places and the car, whenever they happen.", Modifier.weight(1f)) {
                    anytime.forEachIndexed { i, w ->
                        val key = "a_${anytimeTitle(w)}"
                        MomentNodeRow(
                            time = null,
                            title = anytimeTitle(w),
                            w = w,
                            hovered = drag.hovered == key,
                            last = i == anytime.lastIndex,
                            modifier = Modifier.boardTarget(drag, key, w)
                        ) { tasksFor(w, open).forEach { t -> BoardCard(t, now, drag, onDrop, onMoments) } }
                    }
                }
            }
        }
        // The card under the finger.
        val dragged = drag.task
        if (dragged != null) {
            val p = drag.pointer - boardOrigin
            Text(
                dragged.title,
                style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content),
                color = circuitColors.ink,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier
                    .offset { IntOffset((p.x - 40.dp.toPx()).roundToInt(), (p.y - 28.dp.toPx()).roundToInt()) }
                    .widthIn(max = 220.dp)
                    .shadow(10.dp, RoundedCornerShape(14.dp))
                    .clip(RoundedCornerShape(14.dp))
                    .background(MaterialTheme.colorScheme.surfaceContainerHighest)
                    .padding(horizontal = 14.dp, vertical = 10.dp)
            )
        }
    }
}

@Composable
private fun BoardColumn(
    title: String,
    supporting: String,
    modifier: Modifier,
    highlighted: Boolean = false,
    content: @Composable ColumnScope.() -> Unit
) {
    val c = circuitColors
    val surface = MaterialTheme.colorScheme.surfaceContainerLow
    Column(
        modifier.fillMaxHeight().clip(RoundedCornerShape(24.dp)).background(surface)
            .then(if (highlighted) Modifier.border(2.dp, c.accent, RoundedCornerShape(24.dp)) else Modifier)
            .verticalScroll(rememberScrollState())
            .padding(start = 14.dp, end = 14.dp, top = 16.dp, bottom = 16.dp)
    ) {
        Text(title, style = MaterialTheme.typography.titleLarge, color = c.ink)
        Text(supporting, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(top = 2.dp, bottom = 12.dp))
        CompositionLocalProvider(LocalNodeSurface provides surface) { content() }
    }
}

@Composable
private fun SubHead(text: String, color: Color) {
    Text(text, style = MaterialTheme.typography.titleSmall, color = color, modifier = Modifier.padding(top = 8.dp, bottom = 6.dp))
}

@Composable
private fun DayLabel(text: String) {
    Text(text, style = MaterialTheme.typography.titleSmall, color = circuitColors.inkSoft, modifier = Modifier.padding(start = 46.dp, top = 6.dp, bottom = 10.dp))
}

/** The line's first point: now. */
@Composable
private fun NowNode(now: LocalDateTime) {
    val c = circuitColors
    Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
        Text(now.format(HM), style = MaterialTheme.typography.titleSmall.tabular(), color = c.accent, modifier = Modifier.width(44.dp).padding(top = 2.dp))
        Box(Modifier.width(24.dp).fillMaxHeight().drawBehind {
            val x = size.width / 2f
            drawLine(c.wire, Offset(x, 11.dp.toPx()), Offset(x, size.height), 1.5.dp.toPx())
        }, contentAlignment = Alignment.TopCenter) {
            CircuitNode(NodeShape.Circle, NodeState.Firing, Modifier.offset(y = (-1).dp), size = NodeSize.Dot)
        }
        Spacer(Modifier.width(10.dp))
        Text("Now", style = MaterialTheme.typography.titleSmall, color = c.accent, modifier = Modifier.padding(top = 2.dp, bottom = 16.dp))
    }
}

/**
 * A moment on a line: time (or nothing) | its node on the line | its title,
 * then the reminders it holds. The whole row takes a drop; hovered, it
 * draws an accent outline and the node fills.
 */
@Composable
private fun MomentNodeRow(
    time: String?,
    title: String?,
    w: RemindWhen,
    hovered: Boolean,
    last: Boolean,
    modifier: Modifier,
    content: @Composable ColumnScope.() -> Unit
) {
    val c = circuitColors
    val icon = if (w is RemindWhen.At) null else momentIcon(w)
    val nodeSize = if (icon != null) NodeSize.Timeline else NodeSize.Dot
    Row(
        modifier.fillMaxWidth().height(IntrinsicSize.Min)
            .drawBehind {
                if (hovered) drawRoundRect(
                    c.accent, cornerRadius = androidx.compose.ui.geometry.CornerRadius(14.dp.toPx()),
                    style = Stroke(1.5.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(6.dp.toPx(), 4.dp.toPx())))
                )
            }
    ) {
        if (time != null) Text(
            time,
            style = MaterialTheme.typography.titleSmall.tabular(),
            color = if (title != null) c.ink else c.inkSoft,
            modifier = Modifier.width(44.dp).padding(top = 2.dp)
        )
        Box(Modifier.width(24.dp).fillMaxHeight().drawBehind {
            val x = size.width / 2f
            val bottom = if (last) 11.dp.toPx() else size.height
            drawLine(c.wire, Offset(x, 0f), Offset(x, bottom), 1.5.dp.toPx())
        }, contentAlignment = Alignment.TopCenter) {
            CircuitNode(
                NodeShape.Circle, if (hovered) NodeState.Firing else NodeState.Idle,
                Modifier.offset(y = 11.dp - nodeSize.box / 2), size = nodeSize, icon = icon, glyphColor = if (hovered) Color.Unspecified else c.inkSoft
            )
        }
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f).padding(bottom = 12.dp, end = 4.dp)) {
            Text(
                title ?: "",
                style = MaterialTheme.typography.titleSmall,
                color = c.ink,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.heightIn(min = 22.dp).padding(top = 2.dp)
            )
            content()
        }
    }
}

/** A reminder on the board: drag handle and title, its moment line; late in warm. */
@Composable
private fun BoardCard(
    task: Task,
    now: LocalDateTime,
    drag: BoardDrag,
    onDrop: (Task, RemindWhen) -> Unit,
    onOpen: (String) -> Unit,
    late: Boolean = false,
    showLine: Boolean = false
) {
    val c = circuitColors
    val surface = if (late) c.warmSurface else MaterialTheme.colorScheme.surfaceContainerHigh
    val dragging = drag.task?.id == task.id
    // Under a moment the node says when; the line shows only what it does not (other moments, snoozes).
    val sub = rowLine(task, now, task.groupName).takeIf { showLine || late || task.snoozeCount > 0 || remindWhens(task).size > 1 }
    Row(
        Modifier.padding(top = 6.dp).fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(surface)
            .then(if (dragging) Modifier.border(1.5.dp, c.accent, RoundedCornerShape(14.dp)) else Modifier)
            .boardDraggable(drag, task, onDrop)
            .clickable { onOpen(task.id) }
            .padding(start = 6.dp, end = 12.dp, top = 10.dp, bottom = 10.dp),
        verticalAlignment = Alignment.Top
    ) {
        Icon(Icons.Outlined.DragIndicator, contentDescription = null, tint = c.mute, modifier = Modifier.size(18.dp).padding(top = 2.dp))
        Spacer(Modifier.width(6.dp))
        Column(Modifier.weight(1f)) {
            Text(task.title, style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content), color = c.ink, maxLines = 3, overflow = TextOverflow.Ellipsis)
            if (!sub.isNullOrEmpty()) Text(sub, style = MaterialTheme.typography.bodySmall, color = if (late) c.warm else c.inkSoft, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
    }
}

@Composable
private fun BoardQuickAdd(onAdd: (String) -> Unit, onCancel: () -> Unit) {
    var text by remember { mutableStateOf("") }
    val c = circuitColors
    val focus = remember { androidx.compose.ui.focus.FocusRequester() }
    LaunchedEffect(Unit) { focus.requestFocus() }
    androidx.compose.foundation.text.BasicTextField(
        value = text,
        onValueChange = { text = it },
        singleLine = true,
        textStyle = MaterialTheme.typography.bodyLarge.copy(color = c.ink, textDirection = TextDirection.Content),
        cursorBrush = androidx.compose.ui.graphics.SolidColor(c.accent),
        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
            capitalization = androidx.compose.ui.text.input.KeyboardCapitalization.Sentences,
            imeAction = androidx.compose.ui.text.input.ImeAction.Done
        ),
        keyboardActions = androidx.compose.foundation.text.KeyboardActions(onDone = { text.trim().let { if (it.isEmpty()) onCancel() else onAdd(it) } }),
        modifier = Modifier.padding(horizontal = 16.dp).padding(top = 12.dp).widthIn(max = 520.dp).fillMaxWidth()
            .focusRequester(focus),
        decorationBox = { inner ->
            Box(
                Modifier.fillMaxWidth().heightIn(min = 52.dp).clip(RoundedCornerShape(16.dp))
                    .background(MaterialTheme.colorScheme.surfaceContainerLow).padding(horizontal = 16.dp, vertical = 14.dp),
                contentAlignment = Alignment.CenterStart
            ) {
                if (text.isEmpty()) Text("New reminder, then pick its moment", style = MaterialTheme.typography.bodyLarge, color = c.inkSoft)
                inner()
            }
        }
    )
}
