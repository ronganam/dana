package app.ganam.assistant.ui.shell.destinations

import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.unit.dp
import app.ganam.assistant.ui.screens.reminders.ReminderEditor
import app.ganam.assistant.ui.screens.reminders.ReminderListPane
import app.ganam.assistant.ui.screens.reminders.isOpen
import app.ganam.assistant.ui.screens.reminders.sectionsByWhen
import app.ganam.assistant.ui.shell.Destination
import app.ganam.assistant.ui.shell.LocalShellLayout
import app.ganam.assistant.ui.shell.ShellLayout
import app.ganam.assistant.ui.theme.GanamSerif
import app.ganam.assistant.ui.viewmodels.MainViewModel
import app.ganam.assistant.ui.viewmodels.Place
import app.ganam.assistant.ui.viewmodels.ThreadContext
import java.time.LocalDateTime

/**
 * Reminders: the list pane + the editor as the stage. Unfolded, the first
 * open reminder is shown when nothing is picked (an `auto` pick, so folding
 * the phone shows the list, not that editor).
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

    // Keep the stage meaningful: show the first open reminder unfolded, and drop a
    // pick whose reminder is gone (deleted here or elsewhere).
    LaunchedEffect(wide, tasks, pick, loading) {
        val current = pick
        if (current != null && tasks.none { it.id == current.id } && !loading && tasks.isNotEmpty()) {
            shell.selectReminder(null)
        } else if (wide && current == null) {
            sectionsByWhen(tasks.filter(::isOpen), LocalDateTime.now()).firstOrNull()?.tasks?.firstOrNull()?.let {
                shell.selectReminder(it.id, auto = true)
            }
        }
    }

    return Destination(
        place = Place.REMINDERS,
        picked = pick?.auto == false,
        onBack = { shell.selectReminder(null) },
        list = { ReminderListPane(viewModel) },
        stage = { ReminderStage(viewModel) }
    )
}

@Composable
private fun ReminderStage(viewModel: MainViewModel) {
    val shell = viewModel.shellController
    val tc = viewModel.tasksController
    val pick by shell.reminder.collectAsState()
    val tasks by tc.tasks.collectAsState()
    val stacked = LocalShellLayout.current == ShellLayout.STACKED
    val task = tasks.firstOrNull { it.id == pick?.id }

    if (task == null) {
        Column(Modifier.padding(horizontal = 26.dp, vertical = 28.dp)) {
            Text("Nothing open.", style = MaterialTheme.typography.headlineSmall.copy(fontFamily = GanamSerif, fontStyle = FontStyle.Italic))
            Text("Add one on the left, or tell Dana.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    } else OpenReminder(viewModel, task, stacked)
}

@Composable
private fun OpenReminder(viewModel: MainViewModel, task: app.ganam.assistant.data.models.Task, stacked: Boolean) {
    val shell = viewModel.shellController
    val tc = viewModel.tasksController
    val tasks by tc.tasks.collectAsState()
    val groups by tc.taskGroups.collectAsState()
    val zones by tc.zones.collectAsState()
    val context = LocalContext.current
    // After Done / Delete: back to the list when folded, the next reminder when unfolded.
    fun leave() {
        if (stacked) shell.selectReminder(null)
        else {
            val next = sectionsByWhen(tasks.filter { isOpen(it) && it.id != task.id }, LocalDateTime.now()).firstOrNull()?.tasks?.firstOrNull()
            shell.selectReminder(next?.id, auto = true)
        }
    }
    ReminderEditor(
        task = task,
        tasks = tc,
        groups = groups,
        zones = zones,
        onBack = if (stacked) ({ shell.selectReminder(null) }) else null,
        onDoneChanged = { done ->
            tc.setDone(task.id, done)
            if (done) leave()
        },
        onDeleted = {
            leave()
            tc.deleteWithUndo(task.id)
        },
        onOpenCall = { app.ganam.assistant.CallDebriefActivity.open(context, it) },
        onAsk = { shell.openThread(ThreadContext("task", task.id, task.title)) }
    )
}
