package io.github.ronganam.dana.reminders

import io.github.ronganam.dana.data.models.Triggers
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.ui.screens.reminders.RemindWhen
import io.github.ronganam.dana.ui.screens.reminders.parseTriggerTime
import io.github.ronganam.dana.ui.screens.reminders.quickTimes
import io.github.ronganam.dana.ui.screens.reminders.remindWhen
import io.github.ronganam.dana.ui.screens.reminders.remindWhenFor
import io.github.ronganam.dana.ui.screens.reminders.sentence
import io.github.ronganam.dana.ui.shell.DaySlot
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

class ReminderModelTest {
    // Saturday 14 Nov 2026, 18:40
    private val now = LocalDateTime.of(2026, 11, 14, 18, 40)

    private fun task(id: String, triggers: String? = null, due: String? = null, group: String? = null, status: String = "needs_action") =
        task(id, Triggers.of(triggers?.let { ApiJson.parseToJsonElement(it) }), due, group, status)

    private fun task(id: String, triggers: Triggers, due: String? = null, group: String? = null, status: String = "needs_action") =
        Task(id, "Task $id", null, "medium", due, status, triggers, null, null, groupId = group)

    @Test
    fun triggerTimesParseNaiveAndWithOffset() {
        assertEquals(LocalDateTime.of(2026, 11, 14, 18, 30), parseTriggerTime("2026-11-14T18:30"))
        assertEquals(LocalDateTime.of(2026, 11, 14, 18, 30), parseTriggerTime("2026-11-14 18:30:00"))
        assertTrue(parseTriggerTime("2026-11-14T18:30:00.123456+02:00") != null)
        assertNull(parseTriggerTime("null"))
        assertNull(parseTriggerTime(""))
    }

    @Test
    fun labelsAndSentences() {
        assertEquals("Dana reminds you when you arrive at Home.", sentence(task("c", """{"location":{"location":"home","event":"arrive"}}"""), now))
        assertEquals("Dana will not remind you. Pick a moment so it reaches you.", sentence(task("f"), now))
        assertEquals("Dana will not remind you. It was due Wed 30 Sep.", sentence(task("f").copy(dueDate = "2026-09-30"), now))
        assertEquals(
            "Dana reminds you today at 22:00, or when you get into the car.",
            sentence(task("g", """{"time":"2026-11-14T22:00:00","car":"enter"}"""), now)
        )
    }

    @Test
    fun triggersRoundTrip() {
        val w = RemindWhen.At(LocalDateTime.of(2026, 11, 14, 21, 0))
        assertEquals("2026-11-14T21:00:00", w.toTriggers().time)
        val wire = ApiJson.encodeToString(Triggers.serializer(), RemindWhen.Place("Home", leave = true).toTriggers())
        assertEquals("""{"location":{"location":"Home","event":"leave"}}""", wire)
        assertEquals(RemindWhen.Place("Home", leave = true), remindWhen(task("a", wire)))
        assertEquals(RemindWhen.Wake, remindWhen(task("b", RemindWhen.Wake.toTriggers())))
        assertEquals("{}", ApiJson.encodeToString(Triggers.serializer(), RemindWhen.None.toTriggers()))
        assertEquals(RemindWhen.None, remindWhen(task("c", RemindWhen.None.toTriggers())))
    }

    @Test
    fun daySlotsAndDrops() {
        assertEquals(RemindWhen.At(LocalDateTime.of(2026, 11, 14, 21, 0)), remindWhenFor(DaySlot.Hour(21), now, listOf("Home")))
        assertEquals(RemindWhen.At(LocalDateTime.of(2026, 11, 15, 9, 0)), remindWhenFor(DaySlot.Hour(9), now, listOf("Home")))
        assertEquals(RemindWhen.Place("home", leave = false), remindWhenFor(DaySlot.AtHome, now, listOf("Work", "home")))
        assertEquals(RemindWhen.At(LocalDateTime.of(2026, 11, 15, 0, 30)), remindWhenFor(DaySlot.At(LocalDateTime.of(2026, 11, 15, 0, 30)), now, listOf("Home")))
        assertEquals(RemindWhen.None, remindWhenFor(DaySlot.Moment(RemindWhen.None), now, listOf("Home")))
    }

    @Test
    fun quickTimesSkipTonightWhenLate() {
        assertEquals(listOf("Tonight 20:00", "Tomorrow 09:00"), quickTimes(now).take(2).map { it.first })
        assertEquals("Tomorrow 09:00", quickTimes(now.withHour(21)).first().first)
    }
}
