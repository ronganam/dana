package io.github.ronganam.dana.reminders

import io.github.ronganam.dana.data.models.CalendarEvent
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.data.models.Triggers
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.ui.screens.reminders.assignLanes
import io.github.ronganam.dana.ui.screens.reminders.dayTrack
import io.github.ronganam.dana.ui.screens.reminders.nextHalfHour
import io.github.ronganam.dana.ui.screens.reminders.trackHours
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

class DayTimelineTest {
    // Saturday 14 Nov 2026, 18:40
    private val now = LocalDateTime.of(2026, 11, 14, 18, 40)

    private fun task(id: String, triggers: String? = null, status: String = "needs_action") =
        Task(id, "Task $id", null, "medium", null, status, Triggers.of(triggers?.let { ApiJson.parseToJsonElement(it) }), null, null)

    @Test
    fun trackHoldsWhatOverlapsIt() {
        val events = listOf(
            CalendarEvent("1", "Dinner", "2026-11-14T19:30:00", "2026-11-14T21:00:00", false),
            CalendarEvent("2", "Started earlier", "2026-11-14T18:00:00", "2026-11-14T19:00:00", false),
            CalendarEvent("3", "Finished", "2026-11-14T16:00:00", "2026-11-14T17:00:00", false),
            CalendarEvent("4", "Birthday", "2026-11-14", "2026-11-15", true),
            CalendarEvent("5", "Too late", "2026-11-15T09:00:00", "2026-11-15T10:00:00", false)
        )
        val tasks = listOf(
            task("a", """{"time":"2026-11-14T22:00:00"}"""),
            task("b", """{"time":"2026-11-14T17:00:00"}"""),
            task("c", """{"wake":true}"""),
            task("d", """{"location":{"location":"Home","event":"arrive"}}"""),
            task("e", """{"time":"2026-11-14T20:00:00"}""", status = "completed")
        )
        val t = dayTrack(events, tasks, now, hours = 6)
        assertEquals(listOf("Started earlier", "Dinner", "Task a"), t.items.map { it.title })
        assertEquals(now, t.items.first().start) // began before now: drawn from now
        assertEquals(listOf("Birthday"), t.allDay)
        assertEquals(listOf("c"), t.waking.map { it.id })
        assertEquals(listOf("d"), t.home.map { it.id })
        assertEquals((19..23).map { now.toLocalDate().atTime(it, 0) } + now.toLocalDate().plusDays(1).atStartOfDay(), t.hours())
    }

    @Test
    fun dropsSnapToTheHalfHourAndNeverBeforeNow() {
        val t = dayTrack(emptyList(), emptyList(), now, hours = 6)
        assertEquals(nextHalfHour(now), t.timeAt(0f))
        assertEquals(LocalDateTime.of(2026, 11, 14, 19, 0), nextHalfHour(now))
        assertEquals(LocalDateTime.of(2026, 11, 15, 0, 30), t.timeAt(1f)) // 00:40 → 00:30
        assertEquals(LocalDateTime.of(2026, 11, 14, 21, 30), t.timeAt(t.fraction(LocalDateTime.of(2026, 11, 14, 21, 40))))
        assertTrue(t.fraction(now.minusHours(1)) == 0f && t.fraction(now.plusDays(1)) == 1f)
    }

    @Test
    fun lanesAndHours() {
        // The fifth starts before either lane is free again: a point only.
        assertEquals(listOf(0, 1, 0, 1, null), assignLanes(listOf(0f to 50f, 10f to 40f, 60f to 90f, 61f to 70f, 62f to 65f), lanes = 2, gap = 4f))
        assertEquals(3, trackHours(100f))
        assertEquals(18, trackHours(4000f))
        assertEquals(10, trackHours(640f))
    }
}
