package io.github.ronganam.dana.wear.tasks.ui

import io.github.ronganam.dana.wear.ui.components.rotaryScroll

import io.github.ronganam.dana.wear.ui.theme.DanaWear

import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.input.rotary.onRotaryScrollEvent
import androidx.wear.compose.foundation.ExperimentalWearFoundationApi
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.items
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.foundation.rememberActiveFocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.wear.compose.material.*
import io.github.ronganam.dana.wear.tasks.TasksViewModel
import io.github.ronganam.dana.wear.tasks.WearTask

private sealed interface TaskListRow {
    val key: String
    data class Header(val title: String, val emoji: String?) : TaskListRow {
        override val key: String = "header:$title"
    }
    data class Item(val task: WearTask) : TaskListRow {
        override val key: String = task.id
    }
}

private fun buildTaskRows(tasks: List<WearTask>): List<TaskListRow> {
    val grouped = tasks.groupBy { it.groupName?.takeIf { g -> g.isNotBlank() } ?: "General" }
    val rows = mutableListOf<TaskListRow>()
    grouped.entries.sortedBy { it.key.lowercase() }.forEach { (name, groupTasks) ->
        rows.add(TaskListRow.Header(name, groupTasks.firstNotNullOfOrNull { it.groupEmoji }))
        rows.addAll(groupTasks.map { TaskListRow.Item(it) })
    }
    return rows
}

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

    val rows = remember(tasks) { buildTaskRows(tasks) }

    val listState = rememberScalingLazyListState()
    val focusRequester = rememberActiveFocusRequester()

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
            contentPadding = PaddingValues(top = 28.dp, bottom = 36.dp, start = 12.dp, end = 12.dp)
        ) {
            item {
                Text(
                    text = "Tasks",
                    style = MaterialTheme.typography.caption1,
                    color = MaterialTheme.colors.secondary
                )
            }

            if (statusText.isNotEmpty()) {
                item {
                    Text(
                        text = statusText,
                        style = MaterialTheme.typography.caption2,
                        color = MaterialTheme.colors.secondary,
                        modifier = Modifier.padding(vertical = 4.dp)
                    )
                }
            }

            if (isLoading) {
                item {
                    CircularProgressIndicator(
                        modifier = Modifier.size(24.dp),
                        strokeWidth = 2.dp
                    )
                }
            }

            items(rows, key = { it.key }) { row ->
                when (row) {
                    is TaskListRow.Header -> TaskGroupHeader(header = row)
                    is TaskListRow.Item -> TaskChip(
                        task = row.task,
                        isExpanded = expandedTaskId == row.task.id,
                        onClick = {
                            expandedTaskId = if (expandedTaskId == row.task.id) null else row.task.id
                        },
                        onComplete = {
                            viewModel.completeTask(row.task.id)
                            expandedTaskId = null
                        },
                        onSnooze = {
                            onTaskSnooze(row.task.id, row.task.title)
                            expandedTaskId = null
                        }
                    )
                }
            }

            item {
                Spacer(modifier = Modifier.height(4.dp))
            }

            item {
                CompactChip(
                    onClick = { viewModel.refreshTasks() },
                    label = { Text("Refresh") },
                    colors = ChipDefaults.secondaryChipColors(),
                    modifier = Modifier.fillMaxWidth()
                )
            }
        }
    }
}

@Composable
private fun TaskGroupHeader(header: TaskListRow.Header) {
    val label = buildString {
        header.emoji?.let { append("$it ") }
        append(header.title)
    }
    Text(
        text = label,
        style = MaterialTheme.typography.caption1,
        color = MaterialTheme.colors.primary,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 12.dp, bottom = 4.dp)
    )
}

@Composable
private fun TaskChip(
    task: WearTask,
    isExpanded: Boolean,
    onClick: () -> Unit,
    onComplete: () -> Unit,
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

    Column(modifier = Modifier.fillMaxWidth()) {
        Chip(
            onClick = onClick,
            label = {
                Column(modifier = Modifier.fillMaxWidth()) {
                    val displayTitle = buildString {
                        task.groupEmoji?.let { append("$it ") }
                        append(task.title)
                    }
                    Text(
                        text = displayTitle,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis
                    )
                    if (task.dueDate != null) {
                        val formatted = formatDueDate(task.dueDate)
                        if (formatted.isNotEmpty()) {
                            Text(
                                text = if (isOverdue) "Overdue: $formatted" else "Due: $formatted",
                                style = MaterialTheme.typography.caption2,
                                color = if (isOverdue) MaterialTheme.colors.error else MaterialTheme.colors.secondary
                            )
                        }
                    }
                }
            },
            colors = ChipDefaults.secondaryChipColors(),
            modifier = Modifier.fillMaxWidth()
        )

        if (isExpanded) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 16.dp, top = 4.dp, end = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                CompactChip(
                    onClick = onComplete,
                    label = { Text("Done") },
                    colors = ChipDefaults.chipColors(
                        backgroundColor = DanaWear.Success.copy(alpha = 0.2f),
                        contentColor = DanaWear.Success
                    ),
                    modifier = Modifier.weight(1f)
                )
                CompactChip(
                    onClick = onSnooze,
                    label = { Text("Snooze") },
                    colors = ChipDefaults.chipColors(
                        backgroundColor = DanaWear.Info.copy(alpha = 0.2f),
                        contentColor = DanaWear.Info
                    ),
                    modifier = Modifier.weight(1f)
                )
            }
        }
    }
}

private fun formatDueDate(dueDate: String): String {
    return try {
        val parts = dueDate.split("-")
        if (parts.size == 3) "${parts[1]}/${parts[2]}" else ""
    } catch (_: Exception) { "" }
}
