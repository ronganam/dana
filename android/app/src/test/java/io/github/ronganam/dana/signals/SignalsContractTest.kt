package io.github.ronganam.dana.signals

import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.signals.SignalsJson
import io.github.ronganam.dana.data.signals.SignalSource
import io.github.ronganam.dana.data.signals.WakeWindow
import io.github.ronganam.dana.ui.screens.signals.lastLine
import io.github.ronganam.dana.ui.screens.signals.signalLabel
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * The server's own `GET /api/signals` answer, `server/tests/fixtures/signals_web.json`
 * (read from there: a test resources dir), decoded as the Signals page reads it.
 */
class SignalsContractTest {
    /** The captured body: the file itself, or the `body` of the entry that holds one. */
    private val body: String by lazy {
        val text = javaClass.classLoader!!.getResource("signals_web.json")!!.readText()
        val root = ApiJson.parseToJsonElement(text) as JsonObject
        if ("signals" in root) root.toString()
        else root.values.map { it.jsonObject["body"]?.jsonObject ?: it.jsonObject }.first { "signals" in it }.toString()
    }

    @Test
    fun everySignalInTheServersOrder() {
        val o = SignalsJson.overview(body)
        assertEquals(listOf("wake", "screen", "car", "location", "sleep", "weather"), o.signals.map { it.signal })
        o.signals.forEach { s ->
            assertTrue("${s.signal} has sources", s.sources.isNotEmpty())
            assertTrue("${s.signal}: at most one source in use", s.sources.count { it.active } <= 1)
            s.sources.forEach { assertTrue(it.source.isNotEmpty() && it.label.isNotEmpty()) }
        }
    }

    @Test
    fun thePhoneIsTheSourceAndOpenMeteoTheWeathers() {
        val o = SignalsJson.overview(body)
        val wake = o.signal("wake")!!
        assertEquals("phone", wake.sources.first().source)
        assertEquals("phone", wake.activeSource?.source)
        assertNotNull(wake.activeSource?.lastAt)
        o.signals.filter { it.signal != "weather" }.forEach { s ->
            assertEquals("${s.signal} comes from the phone", listOf("phone"), s.sources.map { it.source })
        }
        assertEquals(listOf("open_meteo"), o.signal("weather")!!.sources.map { it.source })
    }

    @Test
    fun theWakeWindowReads() {
        val w = SignalsJson.overview(body).wakeWindow!!
        assertNotNull(WakeWindow.minutes(w.start))
        assertNotNull(WakeWindow.minutes(w.end))
    }

    @Test
    fun unknownKeysAndSignalsAreKept() {
        val o = SignalsJson.overview("""{"signals":[{"signal":"tide","sources":[{"source":"x","label":"X","active":true,"future":1}]}],"other":2}""")
        assertEquals("tide", o.signals.single().signal)
        assertEquals("Tide", signalLabel("tide"))
        assertNull(o.wakeWindow)
    }

    @Test
    fun theProfileAnswerCarriesTheWakeWindow() {
        val w = SignalsJson.profileWakeWindow("""{"name":"Robin","wake_window":{"start":"06:30","end":"10:00","learned":false}}""")
        assertEquals(WakeWindow("06:30", "10:00", learned = false), w)
        assertNull(SignalsJson.profileWakeWindow("""{"name":"Robin","wake_window":{"start":"25:00","end":"10:00"}}"""))
    }

    @Test
    fun wakeWindowContainsWrapsPastMidnight() {
        val morning = WakeWindow("05:00", "12:00", learned = true)
        assertTrue(morning.contains(5 * 60))
        assertFalse(morning.contains(12 * 60))
        assertFalse(morning.contains(4 * 60 + 59))
        val night = WakeWindow("22:00", "02:00", learned = false)
        assertTrue(night.contains(23 * 60))
        assertTrue(night.contains(60))
        assertFalse(night.contains(12 * 60))
        assertEquals("07:05", WakeWindow.hhmm(425))
    }

    @Test
    fun theLastReportLine() {
        val zone = ZoneId.of("UTC")
        val now = ZonedDateTime.of(2026, 11, 22, 9, 0, 0, 0, zone)
        assertEquals("Last · 07:12 · unlock", lastLine(SignalSource("phone", "Dana's phone app", true, "2026-11-22T07:12:03Z", "unlock"), now))
        assertEquals("Last · Sat 21 Nov 18:40", lastLine(SignalSource("phone", "Dana's phone app", true, "2026-11-21T18:40:00Z", ""), now))
        assertEquals("Nothing yet", lastLine(SignalSource("open_meteo", "Open-Meteo", false, null, ""), now))
    }
}
