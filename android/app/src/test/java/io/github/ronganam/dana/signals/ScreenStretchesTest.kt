package io.github.ronganam.dana.signals

import io.github.ronganam.dana.data.signals.ScreenChange
import io.github.ronganam.dana.data.signals.ScreenStretches
import io.github.ronganam.dana.data.signals.SignalEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ScreenStretchesTest {
    private fun on(at: Long) = ScreenChange(true, at)
    private fun off(at: Long) = ScreenChange(false, at)

    @Test
    fun onOffPairsBecomeStretches() {
        val r = ScreenStretches.read(listOf(on(100), off(190), on(300), off(320)), openSince = null, until = 1000)
        assertEquals(listOf(SignalEvent.Screen(100, 190), SignalEvent.Screen(300, 320)), r.stretches)
        assertNull(r.openSince)
    }

    @Test
    fun aStretchStillGoingIsSentUpToNowAndContinuesFromThere() {
        val first = ScreenStretches.read(listOf(on(100)), openSince = null, until = 400)
        assertEquals(listOf(SignalEvent.Screen(100, 400)), first.stretches)
        assertEquals(400L, first.openSince)

        // The next read goes on from where this one stopped: no overlap, nothing lost.
        val second = ScreenStretches.read(listOf(off(650)), openSince = first.openSince, until = 900)
        assertEquals(listOf(SignalEvent.Screen(400, 650)), second.stretches)
        assertNull(second.openSince)
    }

    @Test
    fun aRepeatedOnKeepsTheFirstStartAndOrderDoesNotMatter() {
        val r = ScreenStretches.read(listOf(off(500), on(200), on(300)), openSince = null, until = 1000)
        assertEquals(listOf(SignalEvent.Screen(200, 500)), r.stretches)
    }

    @Test
    fun anOffWithNothingOpenIsSkipped() {
        // The screen was turned on before the history read: its start is unknown.
        val r = ScreenStretches.read(listOf(off(150), on(200), off(260)), openSince = null, until = 1000)
        assertEquals(listOf(SignalEvent.Screen(200, 260)), r.stretches)
    }

    @Test
    fun anOffAtTheVeryEndOfTheLastReadAddsNothing() {
        // The last read stopped at 400 with the screen on; it turned off exactly then.
        val r = ScreenStretches.read(listOf(off(400)), openSince = 400, until = 800)
        assertEquals(emptyList<SignalEvent.Screen>(), r.stretches)
        assertNull(r.openSince)
    }

    @Test
    fun changesAfterUntilWaitForTheNextRead() {
        val r = ScreenStretches.read(listOf(on(100), off(200), on(900)), openSince = null, until = 500)
        assertEquals(listOf(SignalEvent.Screen(100, 200)), r.stretches)
        assertNull(r.openSince)
    }

    @Test
    fun nothingChangedWithTheScreenOffSendsNothing() {
        val r = ScreenStretches.read(emptyList(), openSince = null, until = 500)
        assertEquals(emptyList<SignalEvent.Screen>(), r.stretches)
        assertNull(r.openSince)
    }
}
