package io.github.ronganam.dana.push

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The gate that stopped the catch-up ↔ registration loop: a server that keeps
 * answering `registered: false` (and a phone with no push app) gets one
 * registration attempt per 15 minutes, however often the app fetches.
 */
class RegistrationGateTest {

    private var clock = 1_700_000_000_000L
    private var stored = 0L
    private fun gate() = RegistrationGate(now = { clock }, read = { stored }, write = { stored = it })

    @Test
    fun theFirstAttemptGoesAndIsNoted() {
        assertTrue(gate().tryAcquire())
        assertEquals(clock, stored)
    }

    @Test
    fun aLoopOfCatchUpsRegistersOnceEvery15Minutes() {
        // What production saw: ~28 fetches a second, each told "not registered".
        var attempts = 0
        repeat(28 * 60 * 28) { i ->
            clock += 1000L / 28  // 28 minutes of it
            if (gate().tryAcquire()) attempts++
            if (i == 0) assertEquals(1, attempts)
        }
        assertEquals("one at the start, one 15 minutes later", 2, attempts)
    }

    @Test
    fun theTimeSurvivesARestart() {
        assertTrue(gate().tryAcquire())
        clock += RegistrationGate.MIN_INTERVAL_MS - 1
        assertFalse("a new gate over the same stored time stays shut", gate().tryAcquire())
        clock += 1
        assertTrue(gate().tryAcquire())
    }

    @Test
    fun aClockSetBackOpensTheGate() {
        assertTrue(gate().tryAcquire())
        clock -= 3_600_000
        assertTrue(gate().tryAcquire())
        assertEquals(clock, stored)
        assertFalse(gate().tryAcquire())
    }
}
