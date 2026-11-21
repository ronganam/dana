package io.github.ronganam.dana.data.network

import io.github.ronganam.dana.data.models.CalendarEvent
import io.github.ronganam.dana.data.models.Memory
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.data.models.TaskGroup
import io.github.ronganam.dana.data.models.Triggers
import io.github.ronganam.dana.data.models.ZoneItem
import io.github.ronganam.dana.data.network.ApiClient.Companion.enc
import io.github.ronganam.dana.data.network.ApiClient.Companion.jsonBody
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer

/**
 * The fields of a task to write (`POST /api/tasks`, `PUT /api/tasks/{id}`):
 * null = not given (a partial update leaves it as it is).
 */
@Serializable
data class TaskChanges(
    val title: String? = null,
    val description: String? = null,
    val priority: String? = null,
    val dueDate: String? = null,
    val status: String? = null,
    val triggers: Triggers? = null,
    val groupId: String? = null
) {
    val isEmpty: Boolean get() = this == TaskChanges()
}

/**
 * Snooze a reminder (`POST /api/tasks/{id}/snooze`; it replaces every trigger):
 * [type] `time` ([minutes]), `location` ([location], [event] arrive | leave),
 * `car` ([action] enter | leave) or `wake`.
 */
@Serializable
data class TaskSnooze(
    val type: String = "time",
    val minutes: Int? = null,
    val location: String? = null,
    val event: String? = null,
    val action: String? = null
)

@Serializable
private data class GroupFields(val name: String, val emoji: String, val color: String, val description: String? = null)

@Serializable
private data class MemoryFields(val key: String, val value: String, val category: String)

@Serializable
private data class CalendarEventWire(
    val id: String = "",
    val summary: String = "",
    val start: OptString = null,
    val end: OptString = null,
    val allDay: Boolean = false,
    val location: OptString = null
) {
    fun toModel() = CalendarEvent(id, summary.ifBlank { "(No title)" }, start, end, allDay, location)
}

@Serializable
private data class CreatedId(val id: OptString = null)

/** Tasks and their groups, the calendar, zones and the old memories (server docs/tasks-and-triggers.md). */
class TasksApi(private val http: ApiClient) {

    suspend fun tasks(): List<Task> =
        decodeListLenient(ApiJson.parseToJsonElement(http.text(http.request("/api/tasks"), "Failed to fetch tasks", "[]")), Task.serializer())

    /** The open tasks (`needs_action`). */
    suspend fun tasksDueSoon(): List<Task> = tasks().filter { it.status == "needs_action" }

    suspend fun create(fields: TaskChanges): Boolean =
        http.send(http.request("/api/tasks") { post(jsonBody(TaskChanges.serializer(), fields.withTriggers())) })

    /** Create a task and return its id (null if the server didn't say). */
    suspend fun createReturningId(title: String, groupId: String?): String? {
        val body = TaskChanges(title = title, priority = "medium", triggers = Triggers(), groupId = groupId)
        val text = http.text(http.request("/api/tasks") { post(jsonBody(TaskChanges.serializer(), body)) }, "Failed to create task", "{}")
        return http.decode(CreatedId.serializer(), text).id
    }

    /** `PUT /api/tasks/{id}`. */
    suspend fun update(taskId: String, fields: TaskChanges): Boolean =
        http.send(http.request("/api/tasks/${enc(taskId)}") { put(jsonBody(TaskChanges.serializer(), fields)) })

    /** [update] returning the HTTP status (for the offline queue). */
    suspend fun updateCode(taskId: String, fields: TaskChanges): Int =
        http.sendForCode(http.request("/api/tasks/${enc(taskId)}") { put(jsonBody(TaskChanges.serializer(), fields)) })

    suspend fun snooze(taskId: String, snooze: TaskSnooze): Boolean =
        http.send(http.request("/api/tasks/${enc(taskId)}/snooze") { post(jsonBody(TaskSnooze.serializer(), snooze)) })

    /** [snooze] returning the HTTP status (for the offline queue). */
    suspend fun snoozeCode(taskId: String, snooze: TaskSnooze): Int =
        http.sendForCode(http.request("/api/tasks/${enc(taskId)}/snooze") { post(jsonBody(TaskSnooze.serializer(), snooze)) })

    suspend fun delete(taskId: String): Boolean = http.send(http.request("/api/tasks/${enc(taskId)}") { delete() })

    // --- Groups ---

    suspend fun groups(): List<TaskGroup> =
        decodeListLenient(ApiJson.parseToJsonElement(http.text(http.request("/api/task-groups"), "Failed to fetch task groups", "[]")), TaskGroup.serializer())

    suspend fun createGroup(name: String, emoji: String, color: String, description: String?): Boolean =
        http.send(http.request("/api/task-groups") { post(jsonBody(GroupFields.serializer(), GroupFields(name, emoji, color, description))) })

    suspend fun updateGroup(id: String, name: String, emoji: String, color: String, description: String?): Boolean =
        http.send(http.request("/api/task-groups/${enc(id)}") { put(jsonBody(GroupFields.serializer(), GroupFields(name, emoji, color, description))) })

    suspend fun deleteGroup(id: String): Boolean = http.send(http.request("/api/task-groups/${enc(id)}") { delete() })

    // --- Memories (the old list; About you replaced it) ---

    suspend fun memories(): List<Memory> =
        decodeListLenient(ApiJson.parseToJsonElement(http.text(http.request("/api/memories"), "Failed to fetch memories", "[]")), Memory.serializer())

    suspend fun createMemory(key: String, value: String, category: String = "general"): Boolean =
        http.send(http.request("/api/memories") { post(jsonBody(MemoryFields.serializer(), MemoryFields(key, value, category))) })

    suspend fun deleteMemory(key: String): Boolean = http.send(http.request("/api/memories/$key") { delete() })

    // --- Calendar and zones ---

    /** Events between two local times ("yyyy-MM-ddTHH:mm:ss", the server's zone); all upcoming ones without. */
    suspend fun calendarEvents(start: String? = null, end: String? = null): List<CalendarEvent> {
        val query = if (start != null && end != null) "?start_date=${enc(start)}&end_date=${enc(end)}" else ""
        val text = http.text(http.request("/api/calendar/events$query"), "Failed to fetch calendar events", "[]")
        return decodeListLenient(ApiJson.parseToJsonElement(text), CalendarEventWire.serializer()).map { it.toModel() }
    }

    suspend fun zones(): List<ZoneItem> =
        http.decode(ListSerializer(ZoneItem.serializer()), http.text(http.request("/api/zones"), "Failed to fetch zones", "[]"), "[]")

    /** A create always sends `triggers` (`{}` = none), like the server's other clients. */
    private fun TaskChanges.withTriggers() = if (triggers == null) copy(triggers = Triggers()) else this
}
