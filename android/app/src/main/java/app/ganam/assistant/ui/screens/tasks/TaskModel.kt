package app.ganam.assistant.ui.screens.tasks

import androidx.compose.ui.graphics.Color
import app.ganam.assistant.data.models.Task
import app.ganam.assistant.data.models.TaskGroup
import app.ganam.assistant.ui.theme.Danger
import app.ganam.assistant.ui.theme.Info
import app.ganam.assistant.ui.theme.Warning
import app.ganam.assistant.ui.viewmodels.SortMode
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

internal val triggerTypes = listOf("time", "location", "car", "wake")

internal fun triggerTypeLabel(type: String) = when (type) {
    "time" -> "Time"
    "location" -> "Location"
    "car" -> "Car"
    else -> "Wake"
}

internal fun taskTriggersJson(task: Task): JSONObject =
    try { JSONObject(task.triggers ?: "{}") } catch (e: Exception) { JSONObject() }

internal fun taskHasTriggerType(task: Task, type: String): Boolean {
    val obj = taskTriggersJson(task)
    return if (type == "wake") obj.optBoolean("wake", false) else obj.has(type)
}

internal fun priorityColor(priority: String): Color = when (priority.lowercase()) {
    "high" -> Danger
    "medium" -> Warning
    else -> Info
}

private fun isoFormat() = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US)

internal fun parseTaskDate(value: String?): Long? =
    if (value.isNullOrBlank()) null else try { isoFormat().parse(value)?.time } catch (e: Exception) { null }

/** Mono-friendly "6 Nov · 18:30" for a due date or time trigger, or null. */
internal fun taskWhenLabel(task: Task): String? {
    val ms = parseTaskDate(task.dueDate) ?: parseTaskDate(taskTriggersJson(task).optString("time", ""))
        ?: return null
    val cal = Calendar.getInstance().apply { timeInMillis = ms }
    val hasTime = cal.get(Calendar.HOUR_OF_DAY) != 0 || cal.get(Calendar.MINUTE) != 0
    val day = SimpleDateFormat("d MMM", Locale.getDefault()).format(Date(ms))
    return if (hasTime) "$day · ${SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(ms))}" else day
}

/** Human-readable trigger descriptions: (type, "Arrive · Home" / "Car · Enter" / "Wake up"). */
internal fun taskTriggerLabels(task: Task): List<Pair<String, String>> {
    val obj = taskTriggersJson(task)
    val out = mutableListOf<Pair<String, String>>()
    if (obj.has("time")) out.add("time" to obj.optString("time").take(16).replace("T", " "))
    obj.optJSONObject("location")?.let { loc ->
        val event = if (loc.optString("event") == "arrive") "Arrive" else "Leave"
        out.add("location" to "$event · ${loc.optString("location")}")
    }
    if (obj.has("car")) out.add("car" to "Car · ${if (obj.optString("car") == "enter") "Enter" else "Leave"}")
    if (obj.optBoolean("wake", false)) out.add("wake" to "Wake up")
    return out
}

internal data class TaskSection(
    val key: String,
    val title: String,
    val tasks: List<Task>,
    val accent: Color? = null
)

private fun startOfDay(ms: Long): Long {
    val cal = Calendar.getInstance().apply { timeInMillis = ms }
    cal.set(Calendar.HOUR_OF_DAY, 0)
    cal.set(Calendar.MINUTE, 0)
    cal.set(Calendar.SECOND, 0)
    cal.set(Calendar.MILLISECOND, 0)
    return cal.timeInMillis
}

/** Same grouping rules as before (group / priority / due bucket); tasks inside a section are ordered by due date. */
internal fun buildSections(active: List<Task>, sortMode: SortMode, groups: List<TaskGroup>): List<TaskSection> {
    val byDue = compareBy<Task> { parseTaskDate(it.dueDate) ?: Long.MAX_VALUE }
    return when (sortMode) {
        SortMode.BY_GROUP -> {
            val groupMap = groups.associateBy { it.id }
            active.groupBy { it.groupId ?: "general" }
                .toList()
                .sortedBy { (key, _) -> if (key == "general") Int.MAX_VALUE else groupMap[key]?.sortOrder ?: 0 }
                .map { (key, list) ->
                    val g = groupMap[key]
                    TaskSection(key, if (key == "general") "General" else g?.name ?: key, list.sortedWith(byDue))
                }
        }
        SortMode.BY_PRIORITY -> listOf("high", "medium", "low").mapNotNull { p ->
            val list = active.filter { it.priority.lowercase() == p }
            if (list.isEmpty()) null else TaskSection(
                p,
                when (p) { "high" -> "High priority"; "medium" -> "Medium"; else -> "Low" },
                list.sortedWith(byDue),
                priorityColor(p)
            )
        }
        SortMode.BY_DUE_DATE -> {
            val todayMs = startOfDay(System.currentTimeMillis())
            val tomorrowMs = todayMs + 86_400_000L
            val weekMs = todayMs + 7 * 86_400_000L
            val buckets = active.groupBy { task ->
                val ms = parseTaskDate(task.dueDate)
                if (task.dueDate == null) "nodate"
                else if (ms == null) "later"
                else {
                    val d = startOfDay(ms)
                    when {
                        d < todayMs -> "overdue"
                        d < tomorrowMs -> "today"
                        d < tomorrowMs + 86_400_000L -> "tomorrow"
                        d < weekMs -> "thisweek"
                        else -> "later"
                    }
                }
            }
            listOf(
                "overdue" to "Overdue", "today" to "Today", "tomorrow" to "Tomorrow",
                "thisweek" to "This week", "later" to "Later", "nodate" to "No date"
            ).mapNotNull { (key, title) ->
                val list = buckets[key].orEmpty()
                if (list.isEmpty()) null
                else TaskSection(key, title, list.sortedWith(byDue), if (key == "overdue") Danger else null)
            }
        }
    }
}

internal fun sortModeLabel(mode: SortMode) = when (mode) {
    SortMode.BY_DUE_DATE -> "Due"
    SortMode.BY_PRIORITY -> "Priority"
    SortMode.BY_GROUP -> "Group"
}

internal fun parseGroupColor(hex: String?, fallback: Color): Color =
    try { Color(android.graphics.Color.parseColor(hex ?: "")) } catch (e: Exception) { fallback }
