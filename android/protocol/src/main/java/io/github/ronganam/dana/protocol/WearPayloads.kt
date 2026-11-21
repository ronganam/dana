package io.github.ronganam.dana.protocol

import com.google.android.gms.wearable.DataMap
import org.json.JSONArray
import org.json.JSONObject

/** The phone's settings for the watch, as the [WearPaths.CONFIG] data item. */
data class WearConfig(
    val serverUrl: String,
    val apiToken: String,
    val sessionId: String,
    val calendarEventsJson: String = "[]",
    val dueTasksJson: String = "[]",
    val phoneVersionCode: Int = 0,
) {
    /** [timestamp] changes the item each time, so the watch is told even when nothing else did. */
    fun writeTo(map: DataMap, timestamp: Long) {
        map.putString(SERVER_URL, serverUrl)
        map.putString(API_TOKEN, apiToken)
        map.putString(CHAT_SESSION_ID, sessionId)
        map.putString(CALENDAR_EVENTS, calendarEventsJson)
        map.putString(DUE_TASKS, dueTasksJson)
        map.putInt(PHONE_VERSION_CODE, phoneVersionCode)
        map.putLong(TIMESTAMP, timestamp)
    }

    companion object {
        private const val SERVER_URL = "server_url"
        private const val API_TOKEN = "api_token"
        private const val CHAT_SESSION_ID = "chat_session_id"
        private const val CALENDAR_EVENTS = "calendar_events"
        private const val DUE_TASKS = "due_tasks"
        private const val PHONE_VERSION_CODE = "phone_version_code"
        private const val TIMESTAMP = "timestamp"

        fun from(map: DataMap) = WearConfig(
            serverUrl = map.getString(SERVER_URL) ?: "",
            apiToken = map.getString(API_TOKEN) ?: "",
            sessionId = map.getString(CHAT_SESSION_ID) ?: "",
            calendarEventsJson = map.getString(CALENDAR_EVENTS) ?: "[]",
            dueTasksJson = map.getString(DUE_TASKS) ?: "[]",
            phoneVersionCode = map.getInt(PHONE_VERSION_CODE, 0),
        )
    }
}

/** The watch app's version, as the [WearPaths.VERSION] data item. */
data class WearVersion(val versionCode: Int, val versionName: String) {
    /** [day] keeps the item changing (a daily re-delivery is enough for a version ping). */
    fun writeTo(map: DataMap, day: Long) {
        map.putInt(VERSION_CODE, versionCode)
        map.putString(VERSION_NAME, versionName)
        map.putLong(DAY, day)
    }

    companion object {
        private const val VERSION_CODE = "version_code"
        private const val VERSION_NAME = "version_name"
        private const val DAY = "day"

        fun from(map: DataMap) = WearVersion(map.getInt(VERSION_CODE, 0), map.getString(VERSION_NAME) ?: "")
    }
}

/** [WearPaths.REMINDER_ALARM]: a reminder rings on the phone. [zones]: the place snoozes to offer (null: keep the last). */
data class ReminderAlarm(
    val taskId: String,
    val title: String,
    val priority: String,
    val triggerType: String,
    val zones: List<String>? = null,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("task_id", taskId).put("task_title", title).put("priority", priority).put("trigger_type", triggerType)
        .apply { zones?.let { put("zones", JSONArray(it)) } }

    companion object {
        fun fromJson(o: JSONObject) = ReminderAlarm(
            taskId = o.optString("task_id", ""),
            title = o.optString("task_title", "Reminder"),
            priority = o.optString("priority", "medium"),
            triggerType = o.optString("trigger_type", "time"),
            zones = o.optJSONArray("zones")?.let { a -> List(a.length()) { a.optString(it) } },
        )
    }
}

/** [WearPaths.REMINDER_HANDLED_ON_PHONE] / [WearPaths.REMINDER_HANDLED_ON_WATCH]. */
data class ReminderHandled(val taskId: String) {
    fun toJson(): JSONObject = JSONObject().put("task_id", taskId)

    companion object {
        fun fromJson(o: JSONObject) = ReminderHandled(o.optString("task_id"))
    }
}

/** [WearPaths.UPDATE_TASK_STATUS]: e.g. "completed". */
data class TaskStatusRequest(val taskId: String, val status: String) {
    fun toJson(): JSONObject = JSONObject().put("task_id", taskId).put("status", status)

    companion object {
        fun fromJson(o: JSONObject) = TaskStatusRequest(o.getString("task_id"), o.getString("status"))
    }
}

/**
 * [WearPaths.SNOOZE_TASK]: any snooze the watch offers — time ([minutes]),
 * place ([location] + [event] arrive / leave), car ([action] enter / leave) or wake.
 */
data class SnoozeRequest(
    val taskId: String,
    val type: String,
    val minutes: Int? = null,
    val location: String? = null,
    val event: String? = null,
    val action: String? = null,
) {
    fun toJson(): JSONObject = JSONObject().put("task_id", taskId).put("type", type).apply {
        minutes?.let { put("minutes", it) }
        location?.let { put("location", it) }
        event?.let { put("event", it) }
        action?.let { put("action", it) }
    }

    /** What the server's snooze takes (everything but the task id). */
    fun serverPayload(): JSONObject = toJson().apply { remove("task_id") }

    companion object {
        fun fromJson(o: JSONObject) = SnoozeRequest(
            taskId = o.getString("task_id"),
            type = o.optString("type", "time"),
            minutes = if (o.has("minutes")) o.getInt("minutes") else null,
            location = if (o.has("location")) o.getString("location") else null,
            event = if (o.has("event")) o.getString("event") else null,
            action = if (o.has("action")) o.getString("action") else null,
        )
    }
}

/** The phone's answer to [TaskStatusRequest] / [SnoozeRequest]: [queued] = will be sent when the server is back. */
data class TaskActionReply(val success: Boolean, val queued: Boolean) {
    fun toJson(): JSONObject = JSONObject().put("success", success).put("queued", queued)

    companion object {
        fun fromJson(o: JSONObject) = TaskActionReply(o.optBoolean("success"), o.optBoolean("queued"))
    }
}
