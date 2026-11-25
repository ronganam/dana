package io.github.ronganam.dana.push

import io.github.ronganam.dana.data.network.ApiService
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The events feed's `device` field (false: a token of no device) and what the app remembers and shows for it. */
class PairAgainTest {

    private fun page(body: String) = MockWebServer().use { server ->
        server.enqueue(MockResponse().setBody(body))
        server.start()
        runBlocking { ApiService(server.url("").toString().trimEnd('/'), "old-token").devices.phoneEvents(0) }
    }

    @Test
    fun aTokenOfNoDeviceReadsDeviceFalse() {
        val res = page("""{"events":[],"latest":4,"device":false}""")
        assertFalse(res.device)
        assertTrue("no registered field from a token of no device: no register loop", res.registered)
        assertEquals(4, res.latest)
    }

    @Test
    fun aPairedDeviceAndAnOlderServerReadDeviceTrue() {
        assertTrue(page("""{"events":[],"latest":1,"registered":true,"device":true}""").device)
        assertTrue(page("""{"events":[],"latest":1}""").device)
    }

    @Test
    fun theFirstNoDeviceAnswerAsksOnceAndRemembers() {
        val (first, notify) = PairAgain.next(PairAgain.State(), device = false)
        assertEquals(PairAgain.State(needed = true, notified = true), first)
        assertTrue(notify)
        val (second, again) = PairAgain.next(first, device = false)
        assertEquals(first, second)
        assertFalse("the notification is not repeated", again)
    }

    @Test
    fun aDeviceAnswerClearsIt() {
        val (state, notify) = PairAgain.next(PairAgain.State(needed = true, notified = true), device = true)
        assertEquals(PairAgain.State(), state)
        assertFalse(notify)
        assertEquals(PairAgain.State() to false, PairAgain.next(PairAgain.State(), device = true))
    }
}
