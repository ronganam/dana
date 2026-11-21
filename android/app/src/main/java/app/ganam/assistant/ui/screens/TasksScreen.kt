package app.ganam.assistant.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.People
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import app.ganam.assistant.data.models.Task
import app.ganam.assistant.ui.adaptive.PaneRow
import app.ganam.assistant.ui.adaptive.sidePaneWidth
import app.ganam.assistant.ui.adaptive.Pane
import app.ganam.assistant.ui.adaptive.WindowTier
import app.ganam.assistant.ui.adaptive.windowTier
import app.ganam.assistant.ui.components.ScreenTopBar
import app.ganam.assistant.ui.screens.common.QuietEmptyState
import app.ganam.assistant.ui.screens.common.todayKicker
import app.ganam.assistant.ui.screens.tasks.GroupManagerSheet
import app.ganam.assistant.ui.screens.tasks.TaskDetailPane
import app.ganam.assistant.ui.screens.tasks.TaskEditorForm
import app.ganam.assistant.ui.screens.tasks.TaskEditorSheet
import app.ganam.assistant.ui.screens.tasks.TaskFilterPane
import app.ganam.assistant.ui.screens.tasks.TaskFilterStrip
import app.ganam.assistant.ui.screens.tasks.TaskRow
import app.ganam.assistant.ui.screens.tasks.TaskSectionHeader
import app.ganam.assistant.ui.screens.tasks.buildSections
import app.ganam.assistant.ui.screens.tasks.taskHasTriggerType
import app.ganam.assistant.ui.screens.tasks.triggerTypes
import app.ganam.assistant.ui.viewmodels.MainViewModel
import org.json.JSONObject

/**
 * Reminders — an indexed, hairline-ruled list.
 *
 *  COMPACT : filter strip + list; editor in a bottom sheet
 *  MEDIUM  : list (with filter strip) | detail / editor
 *  EXPANDED: filters | list | detail / editor
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TasksScreen(viewModel: MainViewModel) {
    LaunchedEffect(Unit) { viewModel.fetchTasks() }
    val context = androidx.compose.ui.platform.LocalContext.current

    val tasks by viewModel.tasks.collectAsState()
    val taskGroups by viewModel.taskGroups.collectAsState()
    val selectedGroupFilter by viewModel.selectedGroupFilter.collectAsState()
    val selectedTriggerFilter by viewModel.selectedTriggerFilter.collectAsState()
    val sortMode by viewModel.sortMode.collectAsState()
    val zones by viewModel.zones.collectAsState()
    val isLoading by viewModel.isLoadingTasks.collectAsState()

    val tier = windowTier()
    val wide = tier != WindowTier.COMPACT

    // Selection/editing survive fold, unfold and rotation.
    var selectedId by rememberSaveable { mutableStateOf<String?>(null) }
    var editing by rememberSaveable { mutableStateOf(false) }
    var creating by rememberSaveable { mutableStateOf(false) }
    var sheetOpen by rememberSaveable { mutableStateOf(false) }
    var showDone by rememberSaveable { mutableStateOf(false) }
    var showGroupManager by remember { mutableStateOf(false) }
    val collapsedSections = remember { mutableStateMapOf<String, Boolean>() }

    val activeAll = remember(tasks) { tasks.filter { it.status != "completed" } }
    val groupCounts = remember(activeAll) { activeAll.groupBy { it.groupId ?: "general" }.mapValues { it.value.size } }
    val triggerCounts = remember(activeAll) {
        triggerTypes.associateWith { type -> activeAll.count { taskHasTriggerType(it, type) } }
    }
    val filtered = remember(tasks, selectedGroupFilter, selectedTriggerFilter) {
        tasks.filter { t ->
            (selectedGroupFilter == null || t.groupId == selectedGroupFilter) &&
                (selectedTriggerFilter == null || taskHasTriggerType(t, selectedTriggerFilter!!))
        }
    }
    val active = remember(filtered) { filtered.filter { it.status != "completed" } }
    val completed = remember(filtered) { filtered.filter { it.status == "completed" } }
    val sections = remember(active, sortMode, taskGroups) { buildSections(active, sortMode, taskGroups) }
    val selectedTask = tasks.find { it.id == selectedId }
    // Wide layouts always show a reminder in the detail pane (like Calls/Memory/Projects).
    LaunchedEffect(wide, active) {
        if (wide && selectedId == null && !creating && active.isNotEmpty()) selectedId = active.first().id
    }

    fun select(task: Task) {
        selectedId = task.id
        editing = false
        creating = false
        sheetOpen = !wide
    }
    fun clearFilters() {
        viewModel.setGroupFilter(null)
        viewModel.setTriggerFilter(null)
    }
    val toggle: (Task, Boolean) -> Unit = { task, checked ->
        viewModel.updateTaskStatus(task.id, if (checked) "completed" else "needs_action")
    }

    val filterStrip: @Composable () -> Unit = {
        TaskFilterStrip(
            sortMode = sortMode,
            onSort = viewModel::setSortMode,
            groups = taskGroups,
            groupCounts = groupCounts,
            totalActive = activeAll.size,
            selectedGroup = selectedGroupFilter,
            selectedTrigger = selectedTriggerFilter,
            triggerCounts = triggerCounts,
            onAll = ::clearFilters,
            onGroup = { id ->
                viewModel.setGroupFilter(id)
                viewModel.setTriggerFilter(null)
            },
            onTrigger = { type -> viewModel.setTriggerFilter(if (selectedTriggerFilter == type) null else type) },
            onManage = { showGroupManager = true }
        )
    }

    val list: @Composable () -> Unit = {
        Box(modifier = Modifier.fillMaxSize()) {
            when {
                isLoading && tasks.isEmpty() ->
                    CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))

                tasks.isEmpty() -> QuietEmptyState(
                    title = "Nothing to remember.",
                    message = "Ask Dana to remind you of something, or add one yourself.",
                    modifier = Modifier.align(Alignment.TopStart).padding(top = 24.dp),
                    actionLabel = "New reminder",
                    onAction = { creating = true; editing = false }
                )

                filtered.isEmpty() -> QuietEmptyState(
                    title = "Nothing here.",
                    message = "No reminders match this filter.",
                    modifier = Modifier.align(Alignment.TopStart).padding(top = 24.dp),
                    actionLabel = "Show all",
                    onAction = ::clearFilters
                )

                else -> LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(bottom = if (wide) 24.dp else 96.dp)
                ) {
                    sections.forEach { section ->
                        val expanded = !(collapsedSections[section.key] ?: false)
                        item(key = "h_${section.key}") {
                            TaskSectionHeader(
                                title = section.title,
                                count = section.tasks.size,
                                expanded = expanded,
                                accent = section.accent,
                                onToggle = { collapsedSections[section.key] = expanded }
                            )
                        }
                        if (expanded) {
                            items(section.tasks, key = { "t_${section.key}_${it.id}" }) { task ->
                                TaskRow(
                                    task = task,
                                    selected = wide && task.id == selectedId,
                                    showGroup = sortMode != app.ganam.assistant.ui.viewmodels.SortMode.BY_GROUP,
                                    onToggle = { toggle(task, it) },
                                    onClick = { select(task) }
                                )
                            }
                        }
                    }
                    if (completed.isNotEmpty()) {
                        item(key = "h_done") {
                            TaskSectionHeader(
                                title = "Done",
                                count = completed.size,
                                expanded = showDone,
                                accent = null,
                                onToggle = { showDone = !showDone }
                            )
                        }
                        if (showDone) {
                            items(completed, key = { "d_${it.id}" }) { task ->
                                TaskRow(
                                    task = task,
                                    selected = wide && task.id == selectedId,
                                    showGroup = true,
                                    onToggle = { toggle(task, it) },
                                    onClick = { select(task) }
                                )
                            }
                        }
                    }
                }
            }
        }
    }

    val detail: @Composable () -> Unit = {
        Box(modifier = Modifier.fillMaxSize()) {
            when {
                creating -> TaskEditorForm(
                    task = null,
                    zones = zones,
                    taskGroups = taskGroups,
                    onSave = { title, description, priority, dueDate, triggers, groupId ->
                        viewModel.createTask(title, description, priority, dueDate, triggers, groupId)
                        creating = false
                    },
                    onCancel = { creating = false },
                    modifier = Modifier.padding(top = 8.dp).widthIn(max = 640.dp)
                )

                selectedTask != null && editing -> TaskEditorForm(
                    task = selectedTask,
                    zones = zones,
                    taskGroups = taskGroups,
                    onSave = { title, description, priority, dueDate, triggers, groupId ->
                        viewModel.updateTask(selectedTask.id, title, description, priority, dueDate, selectedTask.status, triggers, groupId)
                        editing = false
                    },
                    onDelete = {
                        viewModel.deleteTask(selectedTask.id)
                        editing = false
                        selectedId = null
                    },
                    onCancel = { editing = false },
                    modifier = Modifier.padding(top = 8.dp).widthIn(max = 640.dp)
                )

                selectedTask != null -> TaskDetailPane(
                    task = selectedTask,
                    onToggle = { toggle(selectedTask, it) },
                    onEdit = { editing = true },
                    onDelete = {
                        viewModel.deleteTask(selectedTask.id)
                        selectedId = null
                    },
                    onOpenCall = { app.ganam.assistant.CallDebriefActivity.open(context, it) },
                    onAsk = { viewModel.openContextChat("task", selectedTask.id) }
                )

                else -> QuietEmptyState(
                    title = "Pick a reminder.",
                    message = "Its details open here.",
                    modifier = Modifier.align(Alignment.TopStart).padding(top = 24.dp)
                )
            }
        }
    }

    Scaffold(
        containerColor = Color.Transparent,
        topBar = {
            ScreenTopBar(
                title = "Reminders",
                kicker = "${todayKicker()} · ${activeAll.size} OPEN",
                actions = {
                    IconButton(onClick = { viewModel.navigateTo("people") }) {
                        Icon(Icons.Filled.People, contentDescription = "People")
                    }
                    if (wide) {
                        TextButton(
                            onClick = { creating = true; editing = false },
                            modifier = Modifier.heightIn(min = 48.dp)
                        ) {
                            Icon(Icons.Filled.Add, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("New")
                        }
                    }
                }
            )
        },
        floatingActionButton = {
            if (!wide) {
                FloatingActionButton(
                    onClick = { creating = true },
                    containerColor = MaterialTheme.colorScheme.primary,
                    contentColor = MaterialTheme.colorScheme.onPrimary
                ) { Icon(Icons.Filled.Add, "Add reminder") }
            }
        }
    ) { innerPadding ->
        BoxWithConstraints(modifier = Modifier.padding(innerPadding).fillMaxSize()) {
            val sideWidth: Dp = sidePaneWidth()

            // Three panes need ~900dp; the Fold's inner screen rotated (≈740dp next to the rail) gets list | detail.
            val layout = if (tier == WindowTier.EXPANDED && maxWidth < 900.dp) WindowTier.MEDIUM else tier
            when (layout) {
                WindowTier.COMPACT -> Column(Modifier.fillMaxSize()) {
                    app.ganam.assistant.ui.screens.review.TodayStrip(viewModel)
                    filterStrip()
                    Box(Modifier.weight(1f)) { list() }
                }

                WindowTier.MEDIUM -> PaneRow {
                    Pane(width = sideWidth, tinted = true) {
                        Column(Modifier.fillMaxSize()) {
                            app.ganam.assistant.ui.screens.review.TodayStrip(viewModel)
                            filterStrip()
                            Box(Modifier.weight(1f)) { list() }
                        }
                    }
                    Pane(divider = false) { detail() }
                }

                WindowTier.EXPANDED -> PaneRow {
                    Pane(width = sideWidth, tinted = true) {
                        TaskFilterPane(
                            sortMode = sortMode,
                            onSort = viewModel::setSortMode,
                            groups = taskGroups,
                            groupCounts = groupCounts,
                            totalActive = activeAll.size,
                            selectedGroup = selectedGroupFilter,
                            selectedTrigger = selectedTriggerFilter,
                            triggerCounts = triggerCounts,
                            onAll = ::clearFilters,
                            onGroup = { id ->
                                viewModel.setGroupFilter(id)
                                viewModel.setTriggerFilter(null)
                            },
                            onTrigger = { type -> viewModel.setTriggerFilter(if (selectedTriggerFilter == type) null else type) },
                            onManage = { showGroupManager = true }
                        )
                    }
                    Pane {
                        Column(Modifier.fillMaxSize()) {
                            app.ganam.assistant.ui.screens.review.TodayStrip(viewModel)
                            Box(Modifier.weight(1f)) { list() }
                        }
                    }
                    Pane(divider = false) { detail() }
                }
            }
        }

        // Phone presentation of create / edit: bottom sheets.
        if (!wide && creating) {
            TaskEditorSheet(
                zones = zones,
                taskGroups = taskGroups,
                onDismiss = { creating = false },
                onSave = { title, description, priority, dueDate, triggers, groupId ->
                    viewModel.createTask(title, description, priority, dueDate, triggers, groupId)
                    creating = false
                }
            )
        }
        if (!wide && sheetOpen && selectedTask != null) {
            TaskEditorSheet(
                task = selectedTask,
                zones = zones,
                taskGroups = taskGroups,
                onDismiss = { sheetOpen = false; selectedId = null },
                onSave = { title, description, priority, dueDate, triggers, groupId ->
                    viewModel.updateTask(selectedTask.id, title, description, priority, dueDate, selectedTask.status, triggers, groupId)
                    sheetOpen = false
                    selectedId = null
                },
                onDelete = {
                    viewModel.deleteTask(selectedTask.id)
                    sheetOpen = false
                    selectedId = null
                },
                onOpenCall = { app.ganam.assistant.CallDebriefActivity.open(context, it) },
                onAsk = {
                    val id = selectedTask.id
                    sheetOpen = false
                    selectedId = null
                    viewModel.openContextChat("task", id)
                }
            )
        }

        if (showGroupManager) {
            GroupManagerSheet(
                groups = taskGroups,
                onDismiss = { showGroupManager = false },
                onCreateGroup = { name, emoji, color, description -> viewModel.createTaskGroup(name, emoji, color, description) },
                onUpdateGroup = { id, name, emoji, color, description -> viewModel.updateTaskGroup(id, name, emoji, color, description) },
                onDeleteGroup = { id -> viewModel.deleteTaskGroup(id) }
            )
        }
    }
}
