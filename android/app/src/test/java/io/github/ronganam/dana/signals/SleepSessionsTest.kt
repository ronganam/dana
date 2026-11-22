package io.github.ronganam.dana.signals

import io.github.ronganam.dana.data.signals.SignalEvent
import io.github.ronganam.dana.data.signals.SignalQueue
import io.github.ronganam.dana.data.signals.SleepSession
import io.github.ronganam.dana.data.signals.SleepSessions
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SleepSessionsTest {
    private val hour = 3_600_000L
    private val now = 1_795_400_000_000L
    private val night = SleepSession(start = now - 9 * hour, end = now - hour, writtenAt = now - hour + 60_000)

    @Test
    fun aSessionWrittenAfterTheCursorIsAnEvent() {
        val read = SleepSessions.read(listOf(night), cursor = now - 2 * hour, now = now)
        assertEquals(listOf(SignalEvent.Sleep(night.start, night.end)), read.events)
        assertEquals(night.writtenAt, read.cursor)
    }

    @Test
    fun theNextReadSendsNothingAgain() {
        val first = SleepSessions.read(listOf(night), cursor = now - 2 * hour, now = now)
        val second = SleepSessions.read(listOf(night), cursor = first.cursor, now = now + hour)
        assertTrue(second.events.isEmpty())
        assertEquals(first.cursor, second.cursor)
    }

    @Test
    fun aNightSyncedLateStillArrivesOnce() {
        // The watch synced last night hours after it ended, after a read had moved the cursor past its end.
        val late = night.copy(writtenAt = now + 3 * hour)
        val read = SleepSessions.read(listOf(late), cursor = now, now = now + 4 * hour)
        assertEquals(listOf(SignalEvent.Sleep(late.start, late.end)), read.events)
        assertEquals(late.writtenAt, read.cursor)
    }

    @Test
    fun twoAppsWritingTheSameNightGiveOneEventOldestFirst() {
        val nap = SleepSession(now - 30 * hour, now - 29 * hour, writtenAt = now - 28 * hour)
        val twin = night.copy(writtenAt = night.writtenAt + 5_000)
        val read = SleepSessions.read(listOf(night, twin, nap), cursor = 0, now = now)
        assertEquals(listOf(SignalEvent.Sleep(nap.start, nap.end), SignalEvent.Sleep(night.start, night.end)), read.events)
        assertEquals(twin.writtenAt, read.cursor)
    }

    @Test
    fun sessionsTheServerWouldRefuseAreSkippedButCounted() {
        val backwards = SleepSession(now - hour, now - 2 * hour, writtenAt = now - 10)
        val tooLong = SleepSession(now - 30 * hour, now - hour, writtenAt = now - 20)
        val future = SleepSession(now - hour, now + hour, writtenAt = now - 30)
        val stale = SleepSession(now - SignalQueue.MAX_AGE_MS - 2 * hour, now - SignalQueue.MAX_AGE_MS - hour, writtenAt = now - 5)
        val read = SleepSessions.read(listOf(backwards, tooLong, future, stale), cursor = now - 2 * hour, now = now)
        assertTrue(read.events.isEmpty())
        // Not read again next time.
        assertEquals(now - 5, read.cursor)
    }

    @Test
    fun nothingNewKeepsTheCursor() {
        val read = SleepSessions.read(emptyList(), cursor = 42, now = now)
        assertEquals(SleepSessions.read(listOf(night.copy(writtenAt = 40)), cursor = 42, now = now), read)
        assertEquals(42, read.cursor)
    }

    @Test
    fun aSpanOfExactlyADayIsKept() {
        val day = SleepSession(now - 25 * hour, now - hour, writtenAt = now)
        assertEquals(1, SleepSessions.read(listOf(day), cursor = 0, now = now).events.size)
    }
}
