package io.github.ronganam.dana.data.wear

import io.github.ronganam.dana.data.models.CalendarEvent
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.protocol.WearEvent
import io.github.ronganam.dana.protocol.WearTaskItem

/**
 * The calendar events and due tasks in the watch's config item
 * (`WearConfig.calendarEventsJson` / `dueTasksJson`), as the shared
 * [WearEvent] / [WearTaskItem] the watch reads.
 */
object WatchJson {
    fun events(events: List<CalendarEvent>): String =
        WearEvent.encodeList(events.map { WearEvent(it.id, it.title, it.start.orEmpty(), it.end.orEmpty(), it.allDay, it.location) })

    fun tasks(tasks: List<Task>): String = WearTaskItem.encodeList(tasks.map { it.forWatch() })

    /** A task as the watch shows it. */
    fun Task.forWatch(): WearTaskItem = WearTaskItem(
        id = id, title = title, description = description, priority = priority, dueDate = dueDate,
        status = status, groupName = groupName, groupEmoji = groupEmoji
    )
}
