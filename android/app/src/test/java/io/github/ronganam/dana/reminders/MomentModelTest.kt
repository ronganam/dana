package io.github.ronganam.dana.reminders

import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.data.models.Triggers
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.ui.screens.reminders.BoardMoment
import io.github.ronganam.dana.ui.screens.reminders.MomentGroup
import io.github.ronganam.dana.ui.screens.reminders.RemindWhen
import io.github.ronganam.dana.ui.screens.reminders.anytimeMoments
import io.github.ronganam.dana.ui.screens.reminders.applying
import io.github.ronganam.dana.ui.screens.reminders.boardMoments
import io.github.ronganam.dana.ui.screens.reminders.dropPhrase
import io.github.ronganam.dana.ui.screens.reminders.momentGroupOf
import io.github.ronganam.dana.ui.screens.reminders.momentSections
import io.github.ronganam.dana.ui.screens.reminders.remindWhens
import io.github.ronganam.dana.ui.screens.reminders.rowLine
import io.github.ronganam.dana.ui.screens.reminders.snoozeSuggestion
import io.github.ronganam.dana.ui.screens.reminders.tasksAt
import io.github.ronganam.dana.ui.screens.reminders.tasksFor
import io.github.ronganam.dana.ui.screens.reminders.with
import io.github.ronganam.dana.ui.screens.reminders.without
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

class MomentModelTest {
    // Saturday 14 Nov 2026, 18:40
    private val now = LocalDateTime.of(2026, 11, 14, 18, 40)

    private fun task(id: String, triggers: String? = null, due: String? = null, snoozes: Int = 0, status: String = "needs_action") =
        Task(id, "Task $id", null, "medium", due, status, Triggers.of(triggers?.let { ApiJson.parseToJsonElement(it) }), null, null, snoozeCount = snoozes)

    @Test
    fun groupsByTheMomentThatReachesYouFirst() {
        assertEquals(MomentGroup.LATE, momentGroupOf(task("a", """{"time":"2026-11-14T14:00:00"}"""), now))
        assertEquals(MomentGroup.LATE, momentGroupOf(task("b", due = "2026-11-12"), now))
        assertEquals(MomentGroup.NONE, momentGroupOf(task("c", due = "2026-11-20"), now))
        assertEquals(MomentGroup.NONE, momentGroupOf(task("d"), now))
        assertEquals(MomentGroup.TODAY, momentGroupOf(task("e", """{"time":"2026-11-14T21:00:00"}"""), now))
        assertEquals(MomentGroup.WAKING, momentGroupOf(task("f", """{"wake":true}"""), now))
        assertEquals(MomentGroup.PLACES, momentGroupOf(task("g", """{"location":{"location":"office","event":"arrive"}}"""), now))
        assertEquals(MomentGroup.PLACES, momentGroupOf(task("h", """{"car":"leave"}"""), now))
        assertEquals(MomentGroup.LATER, momentGroupOf(task("i", """{"time":"2026-11-16T09:00:00"}"""), now))
        // A time today beats waking; a passed time with a place still waits for the place.
        assertEquals(MomentGroup.TODAY, momentGroupOf(task("j", """{"time":"2026-11-14T20:00:00","wake":true}"""), now))
        assertEquals(MomentGroup.PLACES, momentGroupOf(task("k", """{"time":"2026-11-14T10:00:00","car":"enter"}"""), now))
    }

    @Test
    fun sectionsAreOrderedWithTheirLines() {
        val s = momentSections(listOf(
            task("none"),
            task("late", due = "2026-11-10"),
            task("t2", """{"time":"2026-11-14T22:00:00"}"""),
            task("t1", """{"time":"2026-11-14T19:30:00"}"""),
            task("wake", """{"wake":true}""")
        ), now)
        assertEquals(listOf(MomentGroup.LATE, MomentGroup.WAKING, MomentGroup.TODAY, MomentGroup.NONE), s.map { it.group })
        assertEquals(listOf("t1", "t2"), s[2].tasks.map { it.id })
        assertEquals("Next at 19:30", s[2].meta)
        assertEquals("No moment was set", s[0].meta)
    }

    @Test
    fun rowLines() {
        assertEquals("At 21:00", rowLine(task("a", """{"time":"2026-11-14T21:00:00"}"""), now, "Work"))
        assertEquals("Arriving at Office · snoozed 8×", rowLine(task("b", """{"location":{"location":"office","event":"arrive"}}""", snoozes = 8), now, null))
        assertEquals("Due yesterday", rowLine(task("c", due = "2026-11-13"), now, "Work"))
        assertEquals("Work", rowLine(task("d"), now, "Work"))
        assertEquals("Was today 14:00", rowLine(task("e", """{"time":"2026-11-14T14:00:00"}"""), now, null))
        assertEquals("At 21:00 or getting into the car", rowLine(task("f", """{"time":"2026-11-14T21:00:00","car":"enter"}"""), now, null))
    }

    @Test
    fun severalMomentsAtOnce() {
        val t = Triggers().with(RemindWhen.Wake).with(RemindWhen.Place("office", leave = true))
        assertEquals(listOf(RemindWhen.Place("office", true), RemindWhen.Wake), remindWhens(task("x").copy(triggers = t)))
        val t2 = t.with(RemindWhen.Place("home", leave = false)).without(RemindWhen.Wake)
        assertEquals(listOf(RemindWhen.Place("home", false)), remindWhens(task("y").copy(triggers = t2)))
        assertEquals(Triggers(), t.with(RemindWhen.None))
    }

    @Test
    fun suggestionsOnlyForOftenSnoozed() {
        assertNull(snoozeSuggestion(task("a", """{"location":{"location":"office","event":"arrive"}}""", snoozes = 2), listOf("Home")))
        val s = snoozeSuggestion(task("b", """{"location":{"location":"office","event":"arrive"}}""", snoozes = 8), listOf("Home", "Office"))!!
        assertEquals(RemindWhen.Place("office", leave = true), s.to)
        assertEquals("You snoozed this 8 times. Remind you when you leave Office instead?", s.text)
        val moved = task("b2", """{"location":{"location":"office","event":"arrive"}}""").triggers.applying(s)
        assertEquals("leave", moved.location?.event)
        assertEquals(RemindWhen.Car(leave = true), snoozeSuggestion(task("c", """{"car":"enter"}""", snoozes = 3), emptyList())!!.to)
        assertEquals(RemindWhen.Place("Home", false), snoozeSuggestion(task("d", """{"time":"2026-11-14T21:00:00"}""", snoozes = 5), listOf("Home"))!!.to)
        assertNull(snoozeSuggestion(task("e", """{"time":"2026-11-14T21:00:00"}""", snoozes = 5), listOf("Office")))
        assertNull(snoozeSuggestion(task("f", snoozes = 9), listOf("Home")))
    }

    @Test
    fun boardLineHasHoursWakingAndOwnTimes() {
        val own = task("a", """{"time":"2026-11-14T21:15:00"}""")
        val m = boardMoments(now, listOf(own))
        val times = m.filterIsInstance<BoardMoment.At>().map { it.time }
        assertEquals(LocalDateTime.of(2026, 11, 14, 19, 0), times.first())
        assertTrue(LocalDateTime.of(2026, 11, 14, 21, 15) in times)
        assertTrue(times.all { it.isAfter(now) && !it.isAfter(now.plusHours(24)) })
        assertEquals(times.sorted(), times)
        // Waking sits just before the next morning's first hour.
        val wake = m.indexOf(BoardMoment.Waking)
        assertEquals(LocalDateTime.of(2026, 11, 15, 9, 0), (m[wake + 1] as BoardMoment.At).time)
        assertEquals(listOf(own), tasksAt(BoardMoment.At(LocalDateTime.of(2026, 11, 14, 21, 15)), listOf(own)))
    }

    @Test
    fun anytimeIsEveryPlaceBothWaysAndTheCar() {
        val a = anytimeMoments(listOf("Home", "Office", "home"))
        assertEquals(6, a.size)
        val office = task("o", """{"location":{"location":"office","event":"leave"}}""")
        assertEquals(listOf(office), tasksFor(RemindWhen.Place("Office", leave = true), listOf(office)))
        assertEquals(emptyList<Task>(), tasksFor(RemindWhen.Place("Office", leave = false), listOf(office)))
        assertEquals("Set to arriving at Office", dropPhrase(RemindWhen.Place("office", false), now))
        assertEquals("Moment cleared", dropPhrase(RemindWhen.None, now))
    }
}
