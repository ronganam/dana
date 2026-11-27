package io.github.ronganam.dana.ui.shell.destinations

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ViewAgenda
import androidx.compose.material.icons.outlined.ViewKanban
import androidx.compose.ui.graphics.vector.ImageVector
import io.github.ronganam.dana.ui.shell.DayView
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.ui.screens.reminders.MomentSheet
import io.github.ronganam.dana.ui.screens.reminders.ReminderBoard
import io.github.ronganam.dana.ui.screens.reminders.ReminderEditor
import io.github.ronganam.dana.ui.screens.reminders.ReminderListPane
import io.github.ronganam.dana.ui.screens.reminders.isOpen
import io.github.ronganam.dana.ui.screens.reminders.momentSections
import io.github.ronganam.dana.ui.shell.Destination
import io.github.ronganam.dana.ui.shell.HeaderAsk
import io.github.ronganam.dana.ui.shell.LocalShellLayout
import io.github.ronganam.dana.ui.shell.ShellLayout
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.Place
import io.github.ronganam.dana.ui.viewmodels.ThreadContext
import java.time.LocalDateTime

/**
 * Reminders. Folded: the compact day view, then the list by moment; an item
 * opens the moment sheet, the sheet's pencil the editor (the stage).
 * Unfolded (the day view above the panes): the board (the List's groups as
 * columns, drag onto the day or a column to set a moment) across the width,
 * or — List in the header — the list beside the editor. An opened editor
 * always shows as list | editor; Back returns to the board.
 */
@Composable
fun rememberRemindersDestination(viewModel: MainViewModel): Destination {
    val shell = viewModel.shellController
    val tc = viewModel.tasksController
    val pick by shell.reminder.collectAsState()
    val tasks by tc.tasks.collectAsState()
    val loading by tc.isLoadingTasks.collectAsState()
    val layout = LocalShellLayout.current
    val wide = layout != ShellLayout.STACKED
    var boardMode by rememberSaveable { mutableStateOf(true) }
    val sheetFor by shell.momentSheet.collectAsState()
    val board = wide && boardMode && pick?.auto != false

    LaunchedEffect(Unit) { if (tc.zoneItems.value.isEmpty()) tc.fetchZones() }

    // Keep the stage meaningful: show the first open reminder unfolded (list mode), and drop a
    // pick whose reminder is gone (deleted here or elsewhere).
    LaunchedEffect(wide, board, tasks, pick, loading) {
        val current = pick
        if (current != null && tasks.none { it.id == current.id } && !loading && tasks.isNotEmpty()) {
            shell.selectReminder(null)
        } else if (wide && !board && current == null) {
            momentSections(tasks.filter(::isOpen), LocalDateTime.now()).firstOrNull()?.tasks?.firstOrNull()?.let {
                shell.selectReminder(it.id, auto = true)
            }
        } else if (board && current?.auto == true) {
            shell.selectReminder(null)
        }
    }

    // Folded, the conversation button and Ask Dana sit in the header (they scroll with it); unfolded, List | Board does (the rail has the mic).
    val toggle: @Composable () -> Unit = {
        if (wide) ViewToggle(board = boardMode, onBoard = { boardMode = it; if (it) shell.selectReminder(null) })
        else HeaderAsk(viewModel, conversation = true)
    }
    val sheetTask = tasks.firstOrNull { it.id == sheetFor }
    if (sheetFor != null && sheetTask != null) RemindersSheet(viewModel, sheetTask, onEdit = {
        shell.openMoments(null)
        shell.selectReminder(sheetTask.id)
    }, onDismiss = { shell.openMoments(null) })
    val openSheet: (String) -> Unit = { shell.openMoments(it) }

    return Destination(
        place = Place.REMINDERS,
        picked = pick?.auto == false,
        onBack = { shell.selectReminder(null) },
        list = {
            if (board) ReminderBoard(
                viewModel,
                onMoments = openSheet,
                onSuggest = {
                    shell.openThread()
                    viewModel.chatController.sendChatMessage("Suggest a moment for each of my reminders that has none. Ask me before changing them.")
                },
                headerExtra = toggle
            ) else if (wide) ReminderListPane(viewModel, onMoments = openSheet, belowHeader = toggle)
            // Folded, the day view heads the list (unfolded it sits above the panes).
            else ReminderListPane(viewModel, onMoments = openSheet, headerExtra = toggle, top = {
                DayView(viewModel, onNow = { shell.showToday() }, onOpenReminder = openSheet, compact = true)
            })
        },
        stage = if (board) null else ({ ReminderStage(viewModel, onMoments = openSheet) }),
        wide = board,
        askSlot = false
    )
}

/** List | Board, unfolded: two joined buttons in the header, each with its glyph. */
@Composable
private fun ViewToggle(board: Boolean, onBoard: (Boolean) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(2.dp)) {
        ToggleHalf("List", Icons.Outlined.ViewAgenda, !board, RoundedCornerShape(topStart = 20.dp, bottomStart = 20.dp, topEnd = 6.dp, bottomEnd = 6.dp)) { onBoard(false) }
        ToggleHalf("Board", Icons.Outlined.ViewKanban, board, RoundedCornerShape(topStart = 6.dp, bottomStart = 6.dp, topEnd = 20.dp, bottomEnd = 20.dp)) { onBoard(true) }
    }
}

@Composable
private fun ToggleHalf(label: String, icon: ImageVector, on: Boolean, shape: RoundedCornerShape, onClick: () -> Unit) {
    val c = circuitColors
    Row(
        Modifier.clip(shape).background(if (on) c.indicator else MaterialTheme.colorScheme.surfaceContainerLow)
            .clickable(role = Role.Tab, onClick = onClick).heightIn(min = 44.dp).padding(horizontal = 14.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(icon, contentDescription = null, tint = if (on) c.onIndicator else c.inkSoft, modifier = Modifier.padding(end = 6.dp).size(18.dp))
        Text(label, style = MaterialTheme.typography.labelLarge, color = if (on) c.onIndicator else c.ink)
    }
}

@Composable
private fun RemindersSheet(viewModel: MainViewModel, task: Task, onEdit: () -> Unit, onDismiss: () -> Unit) {
    val tc = viewModel.tasksController
    val places by tc.zoneItems.collectAsState()
    val zones by tc.zones.collectAsState()
    val groups by tc.taskGroups.collectAsState()
    MomentSheet(
        task = task,
        places = places,
        zones = zones,
        listName = task.groupId?.let { id -> groups.firstOrNull { it.id == id }?.name } ?: task.groupName,
        onTriggers = { tc.setTriggers(task.id, it) },
        onEdit = onEdit,
        onDismiss = onDismiss
    )
}

@Composable
private fun ReminderStage(viewModel: MainViewModel, onMoments: (String) -> Unit) {
    val shell = viewModel.shellController
    val tc = viewModel.tasksController
    val pick by shell.reminder.collectAsState()
    val tasks by tc.tasks.collectAsState()
    val stacked = LocalShellLayout.current == ShellLayout.STACKED
    val task = tasks.firstOrNull { it.id == pick?.id }

    if (task == null) {
        Column(Modifier.padding(horizontal = 26.dp, vertical = 28.dp)) {
            Text("Nothing open.", style = MaterialTheme.typography.headlineSmall)
            Text("Add one on the left, or tell Dana.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    } else OpenReminder(viewModel, task, stacked, onMoments)
}

@Composable
private fun OpenReminder(viewModel: MainViewModel, task: Task, stacked: Boolean, onMoments: (String) -> Unit) {
    val shell = viewModel.shellController
    val tc = viewModel.tasksController
    val tasks by tc.tasks.collectAsState()
    val groups by tc.taskGroups.collectAsState()
    val context = LocalContext.current
    // After Done / Delete: back to the list when folded, the next reminder when unfolded.
    fun leave() {
        if (stacked) shell.selectReminder(null)
        else {
            val next = momentSections(tasks.filter { isOpen(it) && it.id != task.id }, LocalDateTime.now()).firstOrNull()?.tasks?.firstOrNull()
            shell.selectReminder(next?.id, auto = true)
        }
    }
    ReminderEditor(
        task = task,
        tasks = tc,
        groups = groups,
        onBack = if (stacked) ({ shell.selectReminder(null) }) else null,
        onDoneChanged = { done ->
            tc.setDone(task.id, done)
            if (done) leave()
        },
        onDeleted = {
            leave()
            tc.deleteWithUndo(task.id)
        },
        onOpenCall = { io.github.ronganam.dana.CallDebriefActivity.open(context, it) },
        onAsk = { shell.openThread(ThreadContext("task", task.id, task.title)) },
        onMoments = { onMoments(task.id) }
    )
}
