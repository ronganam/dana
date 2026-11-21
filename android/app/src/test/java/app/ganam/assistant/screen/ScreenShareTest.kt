package app.ganam.assistant.screen

import app.ganam.assistant.data.screen.DeviceScreen
import app.ganam.assistant.data.screen.ScreenShare
import app.ganam.assistant.data.screen.ScreenShare.Availability
import app.ganam.assistant.data.screen.ScreenShare.Missing
import app.ganam.assistant.data.screen.ScreenShare.Source
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The "Screen" control: off every opening, one message only, honest about what is missing. */
class ScreenShareTest {
    private val page = DeviceScreen(app = "Chrome", packageName = "com.android.chrome", text = "Espresso")

    private fun opened(textOff: Boolean = false, shotOff: Boolean = false, shotRequested: Boolean = true) =
        ScreenShare.opened<String>(Source.SYSTEM, textOff, shotOff, shotRequested)

    private fun ready() = opened().withText(page).withShot("shot")

    @Test
    fun startsOffAndSendsNothingByDefault() {
        val s = ready()
        assertEquals(Availability.READY, s.availability)
        assertFalse(s.on)
        val (after, taken) = s.take()
        assertNull("nothing goes unless he turns it on", taken)
        assertFalse(after.on)
    }

    @Test
    fun oneTapIncludesItForTheNextMessageOnly() {
        val on = ready().tap()
        assertTrue(on.on)
        val (after, taken) = on.take()
        assertEquals(ScreenShare.Taken(page, "shot"), taken)
        assertFalse("off again after that message", after.on)
        assertNull(after.take().second)
        // He can turn it on again for another message with the same capture.
        assertEquals("shot", after.tap().take().second?.shot)
    }

    @Test
    fun tapTwiceOrRemoveTurnsItOff() {
        assertFalse(ready().tap().tap().on)
        assertFalse(ready().tap().remove().on)
    }

    @Test
    fun canBeTurnedOnBeforeTheCaptureArrivesAndAppliesOnceItDoes() {
        // Tapped while listening, a fraction of a second after the sheet opened.
        val early = opened().tap()
        assertEquals(Availability.PENDING, early.availability)
        assertTrue(early.on)
        val arrived = early.withShot("shot").withText(page)
        assertTrue(arrived.on)
        assertEquals("shot", arrived.take().second?.shot)
        // Sent before anything arrived: nothing goes, and the control is off.
        val (after, taken) = opened().tap().take()
        assertNull(taken)
        assertFalse(after.on)
    }

    @Test
    fun aNewOpeningForgetsTheLastCapture() {
        val next = ScreenShare.opened<String>(Source.SYSTEM, false, false)
        assertFalse(next.on)
        assertNull(next.shot)
        assertNull(next.screen)
        assertEquals(Availability.PENDING, next.availability)
    }

    @Test
    fun hiddenOutsideTheSystemShow() {
        val dana = ScreenShare.opened<String>(Source.DANA, false, false).withText(page).withShot("shot")
        assertEquals(Availability.HIDDEN, dana.availability)
        assertFalse(dana.tap().on)
        assertEquals(Availability.HIDDEN, ScreenShare<String>().availability)
    }

    @Test
    fun switchesOffInSettingsExplainInsteadOfTurningOn() {
        val off = opened(textOff = true).withText(null).withShot(null)
        assertEquals(Availability.OFF_IN_SETTINGS, off.availability)
        val tapped = off.tap()
        assertFalse(tapped.on)
        assertTrue(tapped.explaining)
        assertFalse(tapped.tap().explaining)
        // The text switch off means no screenshot either.
        assertTrue(ScreenShare.opened<String>(Source.SYSTEM, true, false).screenshotOffInSettings)
    }

    @Test
    fun screenshotSwitchOffSendsTheTextAndSaysSo() {
        val s = opened(shotOff = true, shotRequested = false).withText(page)
        assertEquals(Availability.READY, s.availability)
        assertEquals(Missing.SCREENSHOT_OFF_IN_SETTINGS, s.missing)
        assertEquals(ScreenShare.Taken<String>(page, null), s.tap().take().second)
    }

    @Test
    fun anAppThatBlocksCaptureSendsWhatExists() {
        // Secure window: no screenshot, text still there.
        val textOnly = opened().withText(page).withShot(null)
        assertEquals(Missing.SCREENSHOT_BLOCKED, textOnly.missing)
        assertEquals(page, textOnly.tap().take().second?.screen)
        // Screenshot but no text from the app.
        val shotOnly = opened().withText(DeviceScreen(app = "Bank")).withShot("shot")
        assertEquals(Missing.TEXT, shotOnly.missing)
        // Neither: blocked, and a control that was on turns off.
        val blocked = opened().tap().withText(DeviceScreen(app = "Bank")).withShot(null)
        assertEquals(Availability.BLOCKED, blocked.availability)
        assertFalse(blocked.on)
        assertTrue(blocked.tap().explaining)
    }

    @Test
    fun aRefusedSendPutsItBack() {
        val (after, taken) = ready().tap().take()
        assertTrue(taken != null)
        assertTrue(after.putBack().on)
        // Nothing to put back on a capture that has nothing.
        assertFalse(opened().withText(null).withShot(null).putBack().on)
    }

    @Test
    fun whatIsStillComingIsNotCalledMissing() {
        assertEquals(Missing.NOTHING, opened().withText(page).missing)
        assertEquals(Missing.NOTHING, ready().missing)
    }
}
