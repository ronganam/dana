package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.models.CalendarEvent
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.time.LocalDate

/**
 * Today's and tomorrow's calendar events, for the day view at the top of Reminders (its track runs past midnight).
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
                ctx.api.tasks.calendarEvents("${today}T00:00:00", "${today.plusDays(2)}T00:00:00")
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
