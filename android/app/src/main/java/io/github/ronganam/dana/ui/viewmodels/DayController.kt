package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.models.CalendarEvent
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.time.LocalDate

/**
 * Today's calendar events, for the day strip and "Later today" on Now.
 * The reminders in each slot come from [TasksController.tasks].
 */
class DayController(private val ctx: ControllerContext) {
    private val _events = MutableStateFlow<List<CalendarEvent>>(emptyList())
    val events: StateFlow<List<CalendarEvent>> = _events.asStateFlow()

    private var loadedFor: LocalDate? = null

    fun fetch() {
        ctx.scope.launch {
            val today = LocalDate.now()
            val events = ctx.load("DayEvents") {
                ctx.api.getCalendarEventsBetween("${today}T00:00:00", "${today.plusDays(1)}T00:00:00")
            } ?: return@launch
            loadedFor = today
            _events.value = events
        }
    }

    /** Refetch when the date changed since the last load (the app stayed open past midnight). */
    fun refreshIfStale() {
        if (loadedFor != LocalDate.now()) fetch()
    }
}
