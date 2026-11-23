package io.github.ronganam.dana.wear.ui

import io.github.ronganam.dana.protocol.DanaSnapshot
import io.github.ronganam.dana.protocol.SnapshotEvent
import io.github.ronganam.dana.protocol.SnapshotTask
import io.github.ronganam.dana.wear.ui.screens.WatchToday
import io.github.ronganam.dana.wear.ui.screens.WatchToday.Greeting
import io.github.ronganam.dana.wear.ui.screens.WatchToday.Kind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The idle screen's greeting, name and dot lines. */
class WatchTodayTest {

    @Test
    fun greetingByHour() {
        assertEquals(Greeting.NIGHT, WatchToday.greeting(4))
        assertEquals(Greeting.MORNING, WatchToday.greeting(6))
        assertEquals(Greeting.AFTERNOON, WatchToday.greeting(12))
        assertEquals(Greeting.EVENING, WatchToday.greeting(22))
        assertEquals(Greeting.NIGHT, WatchToday.greeting(23))
    }

    @Test
    fun eventsThenInboxThenTasksThreeAtMost() {
        val s = DanaSnapshot(
            events = listOf(SnapshotEvent("09:30", "Standup"), SnapshotEvent("12:00", "Lunch with Gal"), SnapshotEvent("15:00", "Dentist")),
            tasks = listOf(SnapshotTask("t1", "Water the plants", "overdue"), SnapshotTask("t2", "Call Maya", "today")),
            inboxNeeds = 2
        )
        val t = WatchToday.build(s, 6, "Robin Example")
        assertEquals(Greeting.MORNING, t.greeting)
        assertEquals("Robin", t.name)
        assertEquals(listOf(Kind.EVENT, Kind.EVENT, Kind.INBOX), t.lines.map { it.kind })
        assertEquals("Standup 09:30", t.lines[0].text)
        assertEquals(2, t.lines[2].count)

        val noEvents = WatchToday.build(s.copy(events = emptyList(), inboxNeeds = 0), 20, "Robin")
        assertEquals(listOf(Kind.OVERDUE, Kind.TASK), noEvents.lines.map { it.kind })
        assertEquals("Water the plants", noEvents.lines[0].text)
    }

    @Test
    fun nothingYet() {
        val t = WatchToday.build(null, 9, "  ")
        assertNull(t.name)
        assertTrue(t.lines.isEmpty())
        // A blank event title or time never makes an empty line.
        val blank = WatchToday.build(DanaSnapshot(events = listOf(SnapshotEvent("", ""), SnapshotEvent("10:00", ""))), 9, "Maya")
        assertEquals(listOf("10:00"), blank.lines.map { it.text })
    }
}
