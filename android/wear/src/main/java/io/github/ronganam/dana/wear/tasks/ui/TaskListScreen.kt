package io.github.ronganam.dana.wear.tasks.ui

import android.view.HapticFeedbackConstants
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.border
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.layout
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.style.ResolvedTextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.wear.compose.foundation.ExperimentalWearFoundationApi
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.items
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.material.*
import io.github.ronganam.dana.wear.R
import io.github.ronganam.dana.wear.tasks.TasksViewModel
import io.github.ronganam.dana.wear.tasks.WearTask
import io.github.ronganam.dana.wear.ui.components.rotaryScroll
import io.github.ronganam.dana.wear.ui.theme.DanaMotion
import io.github.ronganam.dana.wear.ui.theme.DanaWear
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

private sealed interface TaskListRow {
    val key: String
    data class Header(val title: String, val emoji: String?, val hue: Color) : TaskListRow {
        override val key: String = "header:$title"
    }
    data class Item(val task: WearTask, val hue: Color) : TaskListRow {
        override val key: String = task.id
    }
}

private fun buildTaskRows(tasks: List<WearTask>, general: String): List<TaskListRow> {
    val grouped = tasks.groupBy { it.groupName?.takeIf { g -> g.isNotBlank() } }
    val rows = mutableListOf<TaskListRow>()
    grouped.entries.sortedBy { (it.key ?: general).lowercase() }.forEach { (name, groupTasks) ->
        val hue = DanaWear.groupHue(name)
        rows.add(TaskListRow.Header(name ?: general, groupTasks.firstNotNullOfOrNull { it.groupEmoji }, hue))
        rows.addAll(groupTasks.map { TaskListRow.Item(it, hue) })
    }
    return rows
}

/**
 * The open reminders by group, each group in its hue (a dot and a mono label,
 * rows tinted with it, the ring drawn in it). Done fills the ring, strikes the
 * title through and folds the row away before the phone is told.
 */
@OptIn(ExperimentalWearFoundationApi::class)
@Composable
fun TaskListScreen(
    viewModel: TasksViewModel,
    onTaskSnooze: (taskId: String, taskTitle: String) -> Unit
) {
    val tasks by viewModel.tasks.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val statusText by viewModel.statusText.collectAsState()
    var expandedTaskId by remember { mutableStateOf<String?>(null) }
    var completing by remember { mutableStateOf(emptySet<String>()) }
    val view = LocalView.current

    val general = stringResource(R.string.tasks_group_general)
    val rows = remember(tasks, general) { buildTaskRows(tasks, general) }

    val listState = rememberScalingLazyListState()
    val focusRequester = remember { FocusRequester() }

    Scaffold(
        positionIndicator = {
            PositionIndicator(scalingLazyListState = listState)
        }
    ) {
        ScalingLazyColumn(
            state = listState,
            modifier = Modifier
                .fillMaxSize()
                .rotaryScroll(listState, focusRequester),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(5.dp),
            contentPadding = PaddingValues(top = 28.dp, bottom = 36.dp, start = 10.dp, end = 10.dp)
        ) {
            item(key = "title") {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(stringResource(R.string.label_tasks), fontFamily = DanaWear.Serif, fontSize = 24.sp, color = DanaWear.Ink)
                    if (tasks.isNotEmpty()) Text(
                        pluralStringResource(R.plurals.tasks_open, tasks.size, tasks.size).uppercase(),
                        fontFamily = DanaWear.Mono, fontSize = 11.sp, color = DanaWear.Ember, maxLines = 1
                    )
                }
            }

            if (statusText.isNotEmpty()) {
                item(key = "status") {
                    Text(
                        text = statusText,
                        style = MaterialTheme.typography.caption2,
                        color = MaterialTheme.colors.secondary,
                        modifier = Modifier.padding(vertical = 4.dp)
                    )
                }
            }

            if (isLoading) {
                item(key = "loading") {
                    CircularProgressIndicator(
                        modifier = Modifier.size(24.dp),
                        strokeWidth = 2.dp
                    )
                }
            }

            items(rows, key = { it.key }) { row ->
                when (row) {
                    is TaskListRow.Header -> TaskGroupHeader(header = row)
                    is TaskListRow.Item -> TaskRow(
                        task = row.task,
                        hue = row.hue,
                        isExpanded = expandedTaskId == row.task.id,
                        completing = row.task.id in completing,
                        onClick = {
                            expandedTaskId = if (expandedTaskId == row.task.id) null else row.task.id
                        },
                        onComplete = {
                            view.performHapticFeedback(HapticFeedbackConstants.CONFIRM)
                            completing = completing + row.task.id
                            expandedTaskId = null
                        },
                        onFolded = {
                            viewModel.completeTask(row.task.id)
                            completing = completing - row.task.id
                        },
                        onSnooze = {
                            onTaskSnooze(row.task.id, row.task.title)
                            expandedTaskId = null
                        }
                    )
                }
            }

            item(key = "refresh") {
                CompactChip(
                    onClick = { viewModel.refreshTasks() },
                    label = { Text(stringResource(R.string.refresh)) },
                    colors = ChipDefaults.secondaryChipColors(),
                    modifier = Modifier.fillMaxWidth().padding(top = 4.dp)
                )
            }
        }
    }
}

@Composable
private fun TaskGroupHeader(header: TaskListRow.Header) {
    val label = buildString {
        header.emoji?.let { append("$it ") }
        append(header.title.uppercase())
    }
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = 14.dp, end = 14.dp, top = 8.dp)
    ) {
        Box(Modifier.size(6.dp).background(header.hue, CircleShape))
        Spacer(Modifier.width(6.dp))
        Text(
            text = label,
            fontFamily = DanaWear.Mono,
            fontSize = 11.sp,
            color = header.hue,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
    }
}

@Composable
private fun TaskRow(
    task: WearTask,
    hue: Color,
    isExpanded: Boolean,
    completing: Boolean,
    onClick: () -> Unit,
    onComplete: () -> Unit,
    onFolded: () -> Unit,
    onSnooze: () -> Unit
) {
    val isOverdue = remember(task.dueDate) {
        task.dueDate?.let { due ->
            try {
                val parts = due.split("-")
                if (parts.size == 3) {
                    val cal = java.util.Calendar.getInstance()
                    val today = java.util.Calendar.getInstance().apply {
                        set(java.util.Calendar.HOUR_OF_DAY, 0)
                        set(java.util.Calendar.MINUTE, 0)
                        set(java.util.Calendar.SECOND, 0)
                        set(java.util.Calendar.MILLISECOND, 0)
                    }
                    cal.set(parts[0].toInt(), parts[1].toInt() - 1, parts[2].toInt(), 0, 0, 0)
                    cal.before(today)
                } else false
            } catch (_: Exception) { false }
        } ?: false
    }

    // Done: the ring fills and the strike-through grows (fill), then the row
    // fades and folds (fold 1 → 0), ~380 ms in all; only then is the phone told.
    // Read in draw / layer / layout lambdas only, so a frame never recomposes.
    val fill = remember { Animatable(0f) }
    val fold = remember { Animatable(1f) }
    val folded by rememberUpdatedState(onFolded)
    LaunchedEffect(completing) {
        if (!completing) return@LaunchedEffect
        fill.animateTo(1f, tween(DanaMotion.BASE - 40, easing = DanaMotion.Standard))
        launch { fold.animateTo(0f, tween(DanaMotion.BASE + 20, easing = DanaMotion.Accelerate)) }.join()
        folded()
    }

    val shape = RoundedCornerShape(26.dp)
    val layoutHolder = remember { arrayOfNulls<TextLayoutResult>(1) }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clipToBounds()
            .layout { measurable, constraints ->
                val p = measurable.measure(constraints)
                val h = (p.height * fold.value).roundToInt()
                layout(p.width, h) { p.placeRelative(0, 0) }
            }
            .graphicsLayer { alpha = fold.value }
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .heightIn(min = 52.dp)
                .clip(shape)
                .background(DanaWear.tint(hue))
                .clickable(enabled = !completing, role = Role.Button, onClick = onClick)
                .padding(start = 12.dp, end = 14.dp, top = 7.dp, bottom = 7.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Ring(hue, fill)
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                val displayTitle = buildString {
                    task.groupEmoji?.let { append("$it ") }
                    append(task.title)
                }
                Text(
                    text = displayTitle,
                    fontSize = 15.sp,
                    color = DanaWear.Ink,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    onTextLayout = { layoutHolder[0] = it },
                    modifier = Modifier.drawWithContent {
                        drawContent()
                        val f = fill.value
                        val layout = layoutHolder[0]
                        if (f > 0f && layout != null) {
                            for (line in 0 until layout.lineCount) {
                                val y = (layout.getLineTop(line) + layout.getLineBottom(line)) / 2f
                                val left = layout.getLineLeft(line)
                                val right = layout.getLineRight(line)
                                val rtl = layout.getParagraphDirection(layout.getLineStart(line)) == ResolvedTextDirection.Rtl
                                val len = (right - left) * f
                                val (x0, x1) = if (rtl) (right - len) to right else left to (left + len)
                                drawLine(DanaWear.Ink, Offset(x0, y), Offset(x1, y), strokeWidth = 1.5.dp.toPx())
                            }
                        }
                    }
                )
                if (task.dueDate != null) {
                    val formatted = formatDueDate(task.dueDate)
                    if (formatted.isNotEmpty()) {
                        Text(
                            text = stringResource(if (isOverdue) R.string.task_overdue else R.string.task_due, formatted),
                            fontFamily = DanaWear.Mono,
                            fontSize = 11.sp,
                            // Overdue needs the user: ember.
                            color = if (isOverdue) DanaWear.Ember else DanaWear.Mute
                        )
                    }
                }
            }
        }

        if (isExpanded) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 16.dp, top = 4.dp, end = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                CompactChip(
                    onClick = onComplete,
                    label = { Text(stringResource(R.string.done)) },
                    colors = ChipDefaults.chipColors(
                        backgroundColor = DanaWear.tint(DanaWear.Sage),
                        contentColor = DanaWear.Sage
                    ),
                    modifier = Modifier.weight(1f)
                )
                CompactChip(
                    onClick = onSnooze,
                    label = { Text(stringResource(R.string.snooze)) },
                    colors = ChipDefaults.chipColors(
                        backgroundColor = DanaWear.tint(DanaWear.Amber),
                        contentColor = DanaWear.Amber
                    ),
                    modifier = Modifier.weight(1f)
                )
            }
        }
    }
}

/** The ring in the group's hue; [fill] 0 → 1 fills it and draws the check in. */
@Composable
private fun Ring(hue: Color, fill: Animatable<Float, *>) {
    Box(
        Modifier
            .size(20.dp)
            .border(2.dp, hue, CircleShape)
            .drawBehind { if (fill.value > 0f) drawCircle(hue, alpha = fill.value) },
        contentAlignment = Alignment.Center
    ) {
        Icon(
            Icons.Filled.Check,
            contentDescription = null,
            tint = DanaWear.OnAccent,
            modifier = Modifier
                .size(14.dp)
                .graphicsLayer {
                    alpha = fill.value
                    scaleX = 0.4f + 0.6f * fill.value
                    scaleY = 0.4f + 0.6f * fill.value
                }
        )
    }
}

private fun formatDueDate(dueDate: String): String {
    return try {
        val parts = dueDate.split("-")
        if (parts.size == 3) "${parts[1]}/${parts[2]}" else ""
    } catch (_: Exception) { "" }
}
