package io.github.ronganam.dana.reminders

import io.github.ronganam.dana.data.models.Triggers
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.data.models.TaskGroup
import io.github.ronganam.dana.ui.screens.reminders.Bucket
import io.github.ronganam.dana.ui.screens.reminders.RemindWhen
import io.github.ronganam.dana.ui.screens.reminders.bucketOf
import io.github.ronganam.dana.ui.screens.reminders.parseTriggerTime
import io.github.ronganam.dana.ui.screens.reminders.quickTimes
import io.github.ronganam.dana.ui.screens.reminders.remindWhen
import io.github.ronganam.dana.ui.screens.reminders.remindWhenFor
import io.github.ronganam.dana.ui.screens.reminders.sectionsByList
import io.github.ronganam.dana.ui.screens.reminders.sectionsByWhen
import io.github.ronganam.dana.ui.screens.reminders.sentence
import io.github.ronganam.dana.ui.screens.reminders.slotOf
import io.github.ronganam.dana.ui.screens.reminders.taskWhenText
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
    fun bucketsFollowTheMock() {
        assertEquals(Bucket.OVERDUE, bucketOf(task("a", """{"time":"2026-11-14T18:30:00"}"""), now))
        assertEquals(Bucket.TODAY, bucketOf(task("b", """{"time":"2026-11-14T22:00:00"}"""), now))
        assertEquals(Bucket.LATER, bucketOf(task("c", """{"time":"2026-11-15T09:00:00"}"""), now))
        assertEquals(Bucket.WHEN_THERE, bucketOf(task("d", """{"location":{"location":"home","event":"arrive"}}"""), now))
        assertEquals(Bucket.WHEN_THERE, bucketOf(task("e", """{"car":"enter"}"""), now))
        assertEquals(Bucket.WHEN_THERE, bucketOf(task("f", """{"wake":true}"""), now))
        assertEquals(Bucket.SOMEDAY, bucketOf(task("g"), now))
        assertEquals(Bucket.SOMEDAY, bucketOf(task("g2", "{}"), now))
        assertEquals(Bucket.OVERDUE, bucketOf(task("h", due = "2026-11-10"), now))
        assertEquals(Bucket.TODAY, bucketOf(task("i", due = "2026-11-14"), now))
    }

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
        assertEquals("Today 22:00", taskWhenText(task("a", """{"time":"2026-11-14T22:00:00"}"""), now))
        assertEquals("Tomorrow 09:00", taskWhenText(task("b", """{"time":"2026-11-15T09:00:00"}"""), now))
        assertEquals("At Home", taskWhenText(task("c", """{"location":{"location":"home","event":"arrive"}}"""), now))
        assertEquals("Leaving Work", taskWhenText(task("d", """{"location":{"location":"Work","event":"leave"}}"""), now))
        assertEquals("In the car", taskWhenText(task("e", """{"car":"enter"}"""), now))
        assertEquals("No reminder set", taskWhenText(task("f"), now))
        assertEquals("Dana reminds you when you arrive at Home.", sentence(task("c", """{"location":{"location":"home","event":"arrive"}}"""), now))
        assertEquals("Dana will not remind you. It waits under Someday.", sentence(task("f"), now))
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
        assertEquals(DaySlot.Hour(22), slotOf(task("a", """{"time":"2026-11-14T22:00:00"}"""), now))
        assertEquals(DaySlot.Hour(18), slotOf(task("a2", """{"time":"2026-11-14T18:50:00"}"""), now))
        assertNull(slotOf(task("b", """{"time":"2026-11-14T17:00:00"}"""), now))
        assertNull(slotOf(task("c", """{"time":"2026-11-15T09:00:00"}"""), now))
        assertEquals(DaySlot.AtHome, slotOf(task("d", """{"location":{"location":"home","event":"arrive"}}"""), now))
        assertNull(slotOf(task("e", """{"location":{"location":"home","event":"leave"}}"""), now))
        assertEquals(DaySlot.OnWaking, slotOf(task("f", """{"wake":true}"""), now))
        assertNull(slotOf(task("g", """{"wake":true}""", status = "completed"), now))

        assertEquals(RemindWhen.At(LocalDateTime.of(2026, 11, 14, 21, 0)), remindWhenFor(DaySlot.Hour(21), now, listOf("Home")))
        assertEquals(RemindWhen.At(LocalDateTime.of(2026, 11, 15, 9, 0)), remindWhenFor(DaySlot.Hour(9), now, listOf("Home")))
        assertEquals(RemindWhen.Place("home", leave = false), remindWhenFor(DaySlot.AtHome, now, listOf("Work", "home")))
    }

    @Test
    fun sectionsAreOrderedAndGrouped() {
        val tasks = listOf(
            task("late", """{"time":"2026-11-14T22:00:00"}""", group = "g2"),
            task("early", """{"time":"2026-11-14T19:00:00"}""", group = "g1"),
            task("some", group = "g1"),
            task("home", """{"location":{"location":"home","event":"arrive"}}""", group = "unknown")
        )
        val byWhen = sectionsByWhen(tasks, now)
        assertEquals(listOf("Today", "When I get there", "Someday"), byWhen.map { it.title })
        assertEquals(listOf("early", "late"), byWhen[0].tasks.map { it.id })

        val groups = listOf(TaskGroup("g2", "Work", "", "", null, 2, null), TaskGroup("g1", "Home", "", "", null, 1, null))
        val byList = sectionsByList(tasks, groups)
        assertEquals(listOf("Home", "Work", "General"), byList.map { it.title })
    }

    @Test
    fun quickTimesSkipTonightWhenLate() {
        assertEquals(listOf("Tonight 20:00", "Tomorrow 09:00"), quickTimes(now).take(2).map { it.first })
        assertEquals("Tomorrow 09:00", quickTimes(now.withHour(21)).first().first)
    }
}
