package io.github.ronganam.dana.data.wear

import io.github.ronganam.dana.data.models.CalendarEvent
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.data.network.ApiJson
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer

/**
 * The calendar events and due tasks in the watch's config item
 * (`WearConfig.calendarEventsJson` / `dueTasksJson`), in the server's shape
 * the watch reads: events `{id, summary, start, end, all_day, location}`,
 * tasks as `GET /api/tasks` sends them.
 */
object WatchJson {
    @Serializable
    private data class WatchEvent(
        val id: String,
        val summary: String,
        val start: String? = null,
        val end: String? = null,
        val allDay: Boolean = false,
        val location: String? = null
    )

    fun events(events: List<CalendarEvent>): String = ApiJson.encodeToString(
        ListSerializer(WatchEvent.serializer()),
        events.map { WatchEvent(it.id, it.title, it.start, it.end, it.allDay, it.location) }
    )

    fun tasks(tasks: List<Task>): String = ApiJson.encodeToString(ListSerializer(Task.serializer()), tasks)
}
