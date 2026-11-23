package io.github.ronganam.dana.sync

import io.github.ronganam.dana.data.sync.CallMatcher
import io.github.ronganam.dana.data.sync.LoggedCall
import io.github.ronganam.dana.data.sync.RecordingName
import io.github.ronganam.dana.data.sync.RecordingNames
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.ZoneOffset

class CallMatchTest {

    private val at = LocalDateTime.of(2024, 10, 31, 9, 30, 15)

    @Test
    fun samsungNamesWithBidiMarks() {
        assertEquals(RecordingName("Robin Levi", null, at), RecordingNames.parse("Call\u200eRobin Levi\u200f_241031_093015.m4a"))
        assertEquals(RecordingName("Robin", null, at), RecordingNames.parse("Call recording Robin_241031_093015.m4a"))
        assertEquals(RecordingName(null, "+15550100", at), RecordingNames.parse("Call+1 555 0100_241031_093015.m4a"))
    }

    @Test
    fun xiaomiNameNumberAndTime() {
        assertEquals(RecordingName("Gal", "+15550100", at), RecordingNames.parse("Gal(+15550100)_20241031093015.mp3"))
        assertEquals(RecordingName(null, "+15550100", at), RecordingNames.parse("+15550100(+15550100)_20241031093015.mp3"))
    }

    @Test
    fun otherRecorders() {
        // OnePlus / Oppo dialers: name, then a dashed date and time.
        assertEquals(RecordingName("Maya", null, at), RecordingNames.parse("Maya 2024-10-31 09-30-15.m4a"))
        // Call-recorder apps: number, compact date-time, direction.
        assertEquals(RecordingName(null, "+15550100", at), RecordingNames.parse("Call_+15550100_20241031-093015_IN.m4a"))
        assertEquals(RecordingName("Robin", "5550100", at.withSecond(0)), RecordingNames.parse("Robin_555-0100_202410310930.amr"))
    }

    @Test
    fun namesThatSayNothing() {
        assertEquals(RecordingName(null, null, null), RecordingNames.parse("Voice 001.m4a"))
        assertEquals(RecordingName(null, null, null), RecordingNames.parse("CallBob_2410_0930.m4a"))
        // A name starting with "Call" keeps it.
        assertEquals("Callum", RecordingNames.parse("Callum_241031_093015.m4a").name)
    }

    @Test
    fun invalidDatesAreNotTimes() {
        // 2024-13-45 is not a date.
        assertNull(RecordingNames.parse("Robin_20241345093015.m4a").start)
    }

    private val zone = ZoneOffset.UTC
    private fun ms(h: Int, m: Int, s: Int = 0) = LocalDateTime.of(2024, 10, 31, h, m, s).toInstant(zone).toEpochMilli()

    private val robin = LoggedCall(ms(9, 30), 300, "+1 555 0100", "Robin", "incoming")
    private val gal = LoggedCall(ms(9, 40), 120, "+1 555 0199", "Gal", "outgoing")
    private val missed = LoggedCall(ms(9, 31), 0, "+1 555 0142", null, "missed")
    private val calls = listOf(gal, missed, robin)

    @Test
    fun byTheNumberInTheName() {
        // The number wins over a closer call by time.
        val rec = RecordingName(null, "15550199", LocalDateTime.of(2024, 10, 31, 9, 30, 20))
        assertSame(gal, CallMatcher.match(rec, 0, calls, zone))
    }

    @Test
    fun byTheStartTimeInTheName() {
        val rec = RecordingName("Robin", null, LocalDateTime.of(2024, 10, 31, 9, 30, 15))
        assertSame(robin, CallMatcher.match(rec, 0, calls, zone))
        // Recorded a minute into Gal's call.
        assertSame(gal, CallMatcher.match(RecordingName(null, null, LocalDateTime.of(2024, 10, 31, 9, 41)), 0, calls, zone))
        // Nothing near: no guess.
        assertNull(CallMatcher.match(RecordingName(null, null, LocalDateTime.of(2024, 10, 31, 12, 0)), 0, calls, zone))
    }

    @Test
    fun byTheFileTimeWithoutAnyInTheName() {
        val nothing = RecordingName(null, null, null)
        // Written a few seconds after Robin's call ended (09:35).
        assertSame(robin, CallMatcher.match(nothing, ms(9, 35, 8), calls, zone))
        // Gal's ended at 09:42.
        assertSame(gal, CallMatcher.match(nothing, ms(9, 42, 30), calls, zone))
        assertNull(CallMatcher.match(nothing, ms(11, 0), calls, zone))
        assertNull(CallMatcher.match(nothing, 0, calls, zone))
    }

    @Test
    fun missedCallsHaveNoRecording() {
        val rec = RecordingName(null, "+15550142", LocalDateTime.of(2024, 10, 31, 9, 31))
        // The missed call has the number but no recording: falls back to the time, inside Robin's call.
        assertSame(robin, CallMatcher.match(rec, 0, calls, zone))
    }

    @Test
    fun numbersCompareByTheirLastDigits() {
        assertTrue(CallMatcher.sameNumber("+1 555 0100", "15550100"))
        assertTrue(CallMatcher.sameNumber("+44 20 7946 0100", "020 7946 0100"))
        assertFalse(CallMatcher.sameNumber("+1 555 0100", "+1 555 0199"))
        assertFalse(CallMatcher.sameNumber("100", "100"))
    }
}
