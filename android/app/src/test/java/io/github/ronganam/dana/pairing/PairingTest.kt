package io.github.ronganam.dana.pairing

import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.data.pairing.PairOutcome
import io.github.ronganam.dana.data.pairing.PairedDevice
import io.github.ronganam.dana.data.wear.WatchPairingOffer
import io.github.ronganam.dana.protocol.PairError
import io.github.ronganam.dana.protocol.WearPairRequest
import io.github.ronganam.dana.ui.screens.pairedAsText
import io.github.ronganam.dana.ui.viewmodels.PairingController
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit

/**
 * The phone's pairing: the controller over the shared PairingFlow (a
 * `dana://pair` link, Pair again, Unpair) with fakes for the Android parts,
 * and the code it hands the watch (`POST /api/devices/pairings` against a
 * local server). The `/api/pair` call itself: PairingClientTest.
 */
class PairingTest {
    private lateinit var server: MockWebServer
    private lateinit var scope: CoroutineScope

    @Before fun setUp() {
        server = MockWebServer()
        server.start()
        scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    }

    @After fun tearDown() {
        scope.cancel()
        server.shutdown()
    }

    private val base get() = server.url("/").toString().trimEnd('/')

    // --- the watch's code ---

    @Test fun theWatchGetsACodeStartedWithThePhonesToken() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(201).setBody(
            """{"id":"p1","code":"WATCH234","expires_at":"2026-11-22T12:05:00+00:00","qr":"dana://pair?x","kind":"watch"}"""))
        val offer = WatchPairingOffer.offer(ServerConfig(base, "tok-phone"), WearPairRequest("Galaxy Watch"))
        assertEquals(base, offer.server)
        assertEquals("WATCH234", offer.code)
        assertNull(offer.error)
        val sent = server.takeRequest(5, TimeUnit.SECONDS)!!
        assertEquals("POST", sent.method)
        assertEquals("/api/devices/pairings", sent.path)
        assertEquals("Bearer tok-phone", sent.getHeader("Authorization"))
        val body = JSONObject(sent.body.readUtf8())
        assertEquals("watch", body.getString("kind"))
        assertEquals("Galaxy Watch", body.getString("name"))
    }

    @Test fun theWatchGetsNoCodeFromAnUnpairedPhoneOrARefusal() = runBlocking {
        val unpaired = WatchPairingOffer.offer(ServerConfig("", ""), WearPairRequest("W"))
        assertEquals("", unpaired.code)
        assertEquals("Pair the phone with Dana first.", unpaired.error)
        assertEquals(0, server.requestCount)
        server.enqueue(MockResponse().setResponseCode(429).setBody("""{"error":"too_many_pairings"}"""))
        val refused = WatchPairingOffer.offer(ServerConfig(base, "tok-phone"), WearPairRequest("W"))
        assertEquals("", refused.code)
        assertEquals("The server refused: too_many_pairings", refused.error)
        val down = base
        server.shutdown()
        assertEquals("The phone can't reach Dana right now.", WatchPairingOffer.offer(ServerConfig(down, "t"), WearPairRequest("W")).error)
    }

    // --- the controller (the pairing screen and Settings) ---

    private class Fakes {
        var config = ServerConfig("", "")
        var device: PairedDevice? = null
        var answer: PairOutcome = PairOutcome.Paired("tok-new", PairedDevice("dev-new", "Google Pixel 8", "phone", userName = "Robin"), "10.0.0")
        val pairCalls = CopyOnWriteArrayList<Pair<String, String>>()
        val stored = CopyOnWriteArrayList<String>()
        val removed = CopyOnWriteArrayList<Pair<ServerConfig, String>>()
        var removeWorks = true
        var forgetTellsServer = true
        var forgets = 0
        var reloads = 0
        val notes = CopyOnWriteArrayList<String>()
    }

    private fun controller(f: Fakes) = PairingController(
        scope,
        current = { f.config },
        device = { f.device },
        pair = { s, c -> f.pairCalls += s to c; f.answer },
        store = { s, paired ->
            f.stored += s
            f.config = ServerConfig(ServerConfig.normalize(s), paired.token)
            f.device = paired.device
            f.config
        },
        remove = { config, id -> f.removed += config to id; f.removeWorks },
        forget = { f.forgets++; f.config = ServerConfig(f.config.url, ""); f.device = null; f.forgetTellsServer },
        reload = { f.reloads++ },
        notify = { f.notes += it },
    )

    @Test fun aLinkPairsAnUnpairedPhone() {
        val f = Fakes()
        val c = controller(f)
        assertTrue(c.openLink("dana://pair?server=https%3A%2F%2Fdana.example.com&code=k7qm-4xpz"))
        waitFor { f.reloads == 1 }
        assertEquals(listOf("https://dana.example.com" to "K7QM4XPZ"), f.pairCalls.toList())
        assertEquals(listOf("https://dana.example.com"), f.stored.toList())
        assertEquals("tok-new", f.config.token)
        assertFalse(c.requested.value)
        assertNull(c.flow.state.value.error)
        assertTrue(f.removed.isEmpty())
        assertEquals("Paired as Google Pixel 8, Robin's device", pairedAsText(f.device))
    }

    @Test fun aLinkWhilePairedOnlyFillsTheForm() {
        val f = Fakes().apply { config = ServerConfig("https://dana.example.com", "tok-old") }
        val c = controller(f)
        assertTrue(c.openLink("dana://pair?server=https%3A%2F%2Fother.example.com&code=K7QM4XPZ"))
        assertFalse(c.openLink("https://example.com"))
        assertEquals("https://other.example.com", c.flow.state.value.server)
        assertEquals("K7QM4XPZ", c.flow.state.value.code)
        assertTrue(c.requested.value)
        Thread.sleep(100)
        assertTrue(f.pairCalls.isEmpty())
        c.close()
        assertFalse(c.requested.value)
    }

    @Test fun aRefusedLinkCodeIsShownAndNothingIsStored() {
        val f = Fakes().apply { answer = PairOutcome.Failed(PairError.InvalidCode) }
        val c = controller(f)
        c.openLink("dana://pair?server=https%3A%2F%2Fdana.example.com&code=K7QM4XPZ")
        waitFor { c.flow.state.value.error != null }
        assertEquals(PairError.InvalidCode.message, c.flow.state.value.error)
        assertTrue(c.requested.value)
        assertTrue(f.stored.isEmpty())
        assertEquals(0, f.reloads)
    }

    @Test fun pairingAgainRemovesTheOldDeviceWithItsOwnToken() {
        val old = ServerConfig("https://dana.example.com", "tok-old")
        val f = Fakes().apply { config = old; device = PairedDevice("dev-old", "Old phone") }
        val c = controller(f)
        c.pairAgain()
        assertEquals("https://dana.example.com", c.flow.state.value.server)
        assertEquals("", c.flow.state.value.code)
        assertTrue(c.requested.value)
        c.flow.setCode("K7QM4XPZ")
        c.flow.submit()
        waitFor { f.removed.isNotEmpty() }
        assertEquals(listOf(old to "dev-old"), f.removed.toList())
        assertEquals("tok-new", f.config.token)
        assertFalse(c.requested.value)
        assertTrue(f.notes.isEmpty())
    }

    @Test fun anOldDeviceThatCannotBeRemovedIsSaid() {
        val f = Fakes().apply { config = ServerConfig("https://dana.example.com", "tok-old"); device = PairedDevice("dev-old"); removeWorks = false }
        val c = controller(f)
        c.pairAgain()
        c.flow.setCode("K7QM4XPZ")
        c.flow.submit()
        waitFor { f.notes.isNotEmpty() }
        assertTrue(f.notes.single().contains("Devices page"))
    }

    @Test fun unpairForgetsAndSaysWhenTheServerWasNotTold() = runBlocking {
        val f = Fakes().apply { config = ServerConfig("https://dana.example.com", "tok-1"); device = PairedDevice("dev-1") }
        val c = controller(f)
        c.unpair().join()
        assertEquals(1, f.forgets)
        assertEquals(1, f.reloads)
        assertTrue(f.notes.isEmpty())
        assertFalse(f.config.isConfigured)
        f.forgetTellsServer = false
        c.unpair().join()
        assertTrue(f.notes.single().contains("remove this phone on Dana's Devices page"))
    }

    @Test fun pairedAsTextWithoutADevice() {
        assertEquals("Paired as this phone", pairedAsText(null))
        assertEquals("Paired as Pixel", pairedAsText(PairedDevice("d", "Pixel")))
    }

    private fun waitFor(condition: () -> Boolean) {
        val until = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
        while (!condition()) {
            if (System.nanoTime() > until) fail("timed out")
            Thread.sleep(10)
        }
    }
}
