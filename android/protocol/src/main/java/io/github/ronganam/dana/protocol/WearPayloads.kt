package io.github.ronganam.dana.protocol

import com.google.android.gms.wearable.DataMap
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * The JSON the phone and the watch exchange, and the watch keeps in its own
 * files: unknown keys are ignored, a missing or null field takes the
 * property's default, nulls are left out of what is written.
 */
@OptIn(ExperimentalSerializationApi::class)
val WearJson: Json = Json {
    ignoreUnknownKeys = true
    isLenient = true
    coerceInputValues = true
    explicitNulls = false
    encodeDefaults = true
}

/** Decode [text] with [WearJson]; null when it isn't [T] (a frame from another version). */
fun <T> KSerializer<T>.decodeOrNull(text: String): T? = runCatching { WearJson.decodeFromString(this, text) }.getOrNull()

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
@Serializable
data class ReminderAlarm(
    @SerialName("task_id") val taskId: String = "",
    @SerialName("task_title") val title: String = "Reminder",
    val priority: String = "medium",
    @SerialName("trigger_type") val triggerType: String = "time",
    val zones: List<String>? = null,
) {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    companion object {
        fun decode(text: String): ReminderAlarm? = serializer().decodeOrNull(text)
    }
}

/** [WearPaths.REMINDER_HANDLED_ON_PHONE] / [WearPaths.REMINDER_HANDLED_ON_WATCH]. */
@Serializable
data class ReminderHandled(@SerialName("task_id") val taskId: String = "") {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    companion object {
        fun decode(text: String): ReminderHandled? = serializer().decodeOrNull(text)
    }
}

/** [WearPaths.UPDATE_TASK_STATUS]: e.g. "completed". Both fields are required. */
@Serializable
data class TaskStatusRequest(@SerialName("task_id") val taskId: String, val status: String) {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    companion object {
        fun decode(text: String): TaskStatusRequest? = serializer().decodeOrNull(text)
    }
}

/**
 * [WearPaths.SNOOZE_TASK]: any snooze the watch offers — time ([minutes]),
 * place ([location] + [event] arrive / leave), car ([action] enter / leave) or wake.
 * Only the fields of its kind are written; [taskId] is required.
 */
@Serializable
data class SnoozeRequest(
    @SerialName("task_id") val taskId: String,
    val type: String = "time",
    val minutes: Int? = null,
    val location: String? = null,
    val event: String? = null,
    val action: String? = null,
) {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    companion object {
        fun decode(text: String): SnoozeRequest? = serializer().decodeOrNull(text)
    }
}

/** The phone's answer to [TaskStatusRequest] / [SnoozeRequest]: [queued] = will be sent when the server is back. */
@Serializable
data class TaskActionReply(val success: Boolean = false, val queued: Boolean = false) {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    companion object {
        fun decode(text: String): TaskActionReply? = serializer().decodeOrNull(text)
    }
}
