package io.github.ronganam.dana.ui.viewmodels

import android.content.Context
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.data.models.TaskGroup
import io.github.ronganam.dana.data.sync.SubmitResult
import io.github.ronganam.dana.data.sync.TaskActionQueue
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

enum class SortMode { BY_GROUP, BY_PRIORITY, BY_DUE_DATE }

/** Tasks, task groups, list filters and zones (used by location triggers). */
class TasksController(private val ctx: ControllerContext, private val appContext: Context) {

    private val _tasks = MutableStateFlow<List<Task>>(emptyList())
    val tasks: StateFlow<List<Task>> = _tasks.asStateFlow()

    private val _isLoadingTasks = MutableStateFlow(false)
    val isLoadingTasks: StateFlow<Boolean> = _isLoadingTasks.asStateFlow()

    private val _taskGroups = MutableStateFlow<List<TaskGroup>>(emptyList())
    val taskGroups: StateFlow<List<TaskGroup>> = _taskGroups.asStateFlow()

    private val _isLoadingGroups = MutableStateFlow(false)
    val isLoadingGroups: StateFlow<Boolean> = _isLoadingGroups.asStateFlow()

    private val _selectedGroupFilter = MutableStateFlow<String?>(null)
    val selectedGroupFilter: StateFlow<String?> = _selectedGroupFilter.asStateFlow()

    private val _sortMode = MutableStateFlow(SortMode.BY_GROUP)
    val sortMode: StateFlow<SortMode> = _sortMode.asStateFlow()

    private val _selectedTriggerFilter = MutableStateFlow<String?>(null)
    val selectedTriggerFilter: StateFlow<String?> = _selectedTriggerFilter.asStateFlow()

    private val _zones = MutableStateFlow<List<String>>(emptyList())
    val zones: StateFlow<List<String>> = _zones.asStateFlow()

    fun fetchTasks() {
        ctx.scope.launch {
            _isLoadingTasks.value = true
            try {
                ctx.load("Tasks") { ctx.api.getTasks() }?.let { _tasks.value = it }
            } finally {
                _isLoadingTasks.value = false
            }
            fetchTaskGroups()
        }
    }

    fun createTask(title: String, description: String?, priority: String, dueDate: String?, triggers: JSONObject?, groupId: String?) {
        ctx.action("Couldn't create task") {
            ctx.api.createTaskDetailed(title, description, priority, dueDate, triggers, groupId).also { if (it) fetchTasks() }
        }
    }

    fun updateTask(taskId: String, title: String, description: String?, priority: String, dueDate: String?, status: String, triggers: JSONObject?, groupId: String?) {
        ctx.action("Couldn't update task") {
            ctx.api.updateTaskDetailed(taskId, title, description, priority, dueDate, status, triggers, groupId).also { if (it) fetchTasks() }
        }
    }

    fun updateTaskStatus(taskId: String, newStatus: String) {
        ctx.action("Couldn't update task") {
            ctx.api.updateTaskStatus(taskId, newStatus).also { if (it) fetchTasks() }
        }
    }

    fun deleteTask(taskId: String) {
        ctx.action("Couldn't delete task") {
            ctx.api.deleteTask(taskId).also { if (it) fetchTasks() }
        }
    }

    fun fetchTaskGroups() {
        ctx.scope.launch {
            _isLoadingGroups.value = true
            try {
                ctx.load("TaskGroups") { ctx.api.getTaskGroups() }?.let { _taskGroups.value = it }
            } finally {
                _isLoadingGroups.value = false
            }
        }
    }

    fun createTaskGroup(name: String, emoji: String, color: String, description: String?) {
        ctx.action("Couldn't create group") {
            ctx.api.createTaskGroup(name, emoji, color, description).also { if (it) fetchTaskGroups() }
        }
    }

    fun updateTaskGroup(id: String, name: String, emoji: String, color: String, description: String?) {
        ctx.action("Couldn't update group") {
            ctx.api.updateTaskGroup(id, name, emoji, color, description).also { if (it) fetchTaskGroups() }
        }
    }

    fun deleteTaskGroup(id: String) {
        ctx.action("Couldn't delete group") {
            ctx.api.deleteTaskGroup(id).also { if (it) fetchTasks() }
        }
    }

    fun setGroupFilter(groupId: String?) {
        _selectedGroupFilter.value = groupId
    }

    fun setSortMode(mode: SortMode) {
        _sortMode.value = mode
    }

    fun setTriggerFilter(triggerType: String?) {
        _selectedTriggerFilter.value = triggerType
    }

    fun fetchZones() {
        ctx.scope.launch {
            val zones = ctx.load("Zones") { ctx.api.getZones() }
            _zones.value = zones?.map { it.name } ?: listOf("Home", "Work")
        }
    }

    // ---------------------------------------------------------------------
    // Reminders screen (new shell): optimistic edits that save as you make
    // them, through the offline queue, with Undo where it makes sense.
    // ---------------------------------------------------------------------

    private var lastQueuedNotice = 0L

    private fun nowStamp(): String =
        SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }.format(Date())

    private fun applyLocal(taskId: String, change: (Task) -> Task) {
        _tasks.update { list -> list.map { if (it.id == taskId) change(it) else it } }
    }

    private suspend fun report(result: SubmitResult, failure: String) {
        when (result) {
            SubmitResult.SENT -> {}
            SubmitResult.QUEUED -> {
                // Once a minute is enough: every keystroke of a title edit can land here.
                if (System.currentTimeMillis() - lastQueuedNotice > 60_000) {
                    lastQueuedNotice = System.currentTimeMillis()
                    ctx.messages.post("Saved on the phone · it syncs when the server is back")
                }
            }
            SubmitResult.REJECTED -> {
                ctx.messages.post("$failure: the server rejected the request")
                ctx.load("Tasks") { ctx.api.getTasks() }?.let { _tasks.value = it }
            }
        }
    }

    /**
     * Change some fields of a reminder: [local] updates the list right away,
     * [fields] go to `PUT /api/tasks/{id}` (queued when the server can't be reached).
     */
    fun edit(taskId: String, fields: JSONObject, failure: String = "Couldn't save the reminder", local: (Task) -> Task) {
        applyLocal(taskId) { local(it).copy(updatedAt = nowStamp()) }
        ctx.scope.launch { report(TaskActionQueue.update(appContext, taskId, fields), failure) }
    }

    /** Complete / reopen with "Undo". */
    fun setDone(taskId: String, done: Boolean, undoable: Boolean = true) {
        val status = if (done) "completed" else "needs_action"
        applyLocal(taskId) { it.copy(status = status, updatedAt = nowStamp()) }
        ctx.scope.launch {
            report(TaskActionQueue.setStatus(appContext, taskId, status), "Couldn't update the reminder")
        }
        if (undoable) {
            ctx.messages.postUndoable(if (done) "Completed" else "Back on the list", onUndo = { setDone(taskId, !done, undoable = false) })
        }
    }

    /** Set the reminder's triggers; [undoLabel] shows "Moved to …" with Undo (drops on the day strip). */
    fun setTriggers(taskId: String, triggers: JSONObject, undoLabel: String? = null) {
        val before = _tasks.value.firstOrNull { it.id == taskId }?.triggers
        edit(taskId, JSONObject().put("triggers", triggers)) { it.copy(triggers = triggers.toString()) }
        if (undoLabel != null) {
            ctx.messages.postUndoable(undoLabel, onUndo = {
                val old = try { JSONObject(before ?: "{}") } catch (e: Exception) { JSONObject() }
                edit(taskId, JSONObject().put("triggers", old)) { it.copy(triggers = old.toString()) }
            })
        }
    }

    /**
     * Delete with Undo: the reminder leaves the list at once and is deleted on
     * the server only when the Undo message goes away.
     */
    fun deleteWithUndo(taskId: String) {
        val list = _tasks.value
        val index = list.indexOfFirst { it.id == taskId }
        if (index < 0) return
        val task = list[index]
        _tasks.value = list.filter { it.id != taskId }
        ctx.messages.postUndoable(
            "Deleted",
            onUndo = {
                _tasks.update { cur ->
                    if (cur.any { it.id == taskId }) cur
                    else cur.toMutableList().apply { add(index.coerceAtMost(size), task) }
                }
            },
            onTimeout = {
                ctx.action("Couldn't delete the reminder") {
                    ctx.api.deleteTask(taskId).also { if (!it) fetchTasks() }
                }
            }
        )
    }

    /** "+ Add a reminder": create it, then [onCreated] with its id (to open it in the editor). */
    fun quickAdd(title: String, groupId: String?, onCreated: (String) -> Unit) {
        ctx.action("Couldn't add the reminder") {
            val id = ctx.api.createTaskReturningId(title, groupId)
            ctx.load("Tasks") { ctx.api.getTasks() }?.let { _tasks.value = it }
            if (id != null) onCreated(id)
            id != null
        }
    }
}
