package io.github.ronganam.dana.pairing

import io.github.ronganam.dana.data.network.ApiService
import io.github.ronganam.dana.data.network.PairingException
import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.data.wear.WatchPairingOffer
import io.github.ronganam.dana.protocol.WearPairRequest
import io.github.ronganam.dana.ui.screens.pairedAsText
import io.github.ronganam.dana.ui.viewmodels.PairingController
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
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
 * Pairing the phone (and handing the watch a code) against a local server
 * that answers like `POST /api/pair`, `POST /api/devices/pairings` and
 * `DELETE /api/devices/{id}` (server docs/devices.md).
 */
class PairingTest {
    private lateinit var server: MockWebServer
    private lateinit var scope: CoroutineScope
    private val requests = CopyOnWriteArrayList<RecordedRequest>()

    /** The server's answer per "METHOD path". */
    private val answers = mutableMapOf<String, MockResponse>()

    private val pairedJson = """{"token":"tok-new","device":{"id":"dev-new","user_id":"u1","name":"Google Pixel 8","kind":"phone",
        "room":"","role":"","paired":true},"user":{"id":"u1","name":"Robin"},"server":{"version":"10.0.0"}}"""

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request
                return answers["${request.method} ${request.path}"] ?: MockResponse().setResponseCode(404)
            }
        }
        server.start()
        scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    }

    @After fun tearDown() {
        scope.cancel()
        server.shutdown()
    }

    private val base get() = server.url("/").toString().trimEnd('/')

    private fun json(code: Int, body: String) = MockResponse().setResponseCode(code)
        .setHeader("Content-Type", "application/json").setBody(body)

    private fun request(method: String, path: String) = requests.lastOrNull { it.method == method && it.path == path }

    // --- the API ---

    @Test fun pairSendsTheCodeWithoutATokenAndReadsTheDevice() = runBlocking {
        answers["POST /api/pair"] = json(200, pairedJson)
        val answer = ApiService(base, "").pairing.pair("K7QM4XPZ", "phone", "Google Pixel 8")
        assertEquals("tok-new", answer.token)
        assertEquals("dev-new", answer.device.id)
        assertEquals("Robin", answer.user.name)
        val sent = request("POST", "/api/pair")!!
        assertNull(sent.getHeader("Authorization"))
        val body = JSONObject(sent.body.readUtf8())
        assertEquals("K7QM4XPZ", body.getString("code"))
        assertEquals("phone", body.getString("kind"))
        assertEquals("Google Pixel 8", body.getString("name"))
    }

    @Test fun pairRefusalsBecomeSentences() = runBlocking {
        suspend fun refusal(response: MockResponse): String {
            answers["POST /api/pair"] = response
            try {
                ApiService(base, "").pairing.pair("K7QM4XPZ", "phone", "P")
            } catch (e: PairingException) {
                return e.message.orEmpty()
            }
            fail("pair() should have failed")
            return ""
        }
        assertTrue(refusal(json(403, """{"error":"invalid_code","detail":"x"}""")).startsWith("That code isn't right"))
        assertTrue(refusal(json(409, """{"error":"wrong_kind","expected":"watch"}""")).contains("another kind of device"))
        assertEquals("Too many wrong codes. Try again in 5 minutes.",
            refusal(json(429, """{"error":"rate_limited"}""").setHeader("Retry-After", "300")))
    }

    @Test fun startPairingForTheWatchUsesThePhonesToken() = runBlocking {
        answers["POST /api/devices/pairings"] = json(201,
            """{"id":"p1","code":"WATCH234","expires_at":"2026-11-22T12:05:00+00:00","qr":"dana://pair?x","kind":"watch"}""")
        val offer = WatchPairingOffer.offer(ServerConfig(base, "tok-phone", "dev-phone"), WearPairRequest("Galaxy Watch"))
        assertEquals(base, offer.server)
        assertEquals("WATCH234", offer.code)
        assertNull(offer.error)
        val sent = request("POST", "/api/devices/pairings")!!
        assertEquals("Bearer tok-phone", sent.getHeader("Authorization"))
        val body = JSONObject(sent.body.readUtf8())
        assertEquals("watch", body.getString("kind"))
        assertEquals("Galaxy Watch", body.getString("name"))
    }

    @Test fun theWatchGetsNoCodeFromAnUnpairedPhoneOrARefusal() = runBlocking {
        val unpaired = WatchPairingOffer.offer(ServerConfig("", ""), WearPairRequest("W"))
        assertEquals("", unpaired.code)
        assertEquals("Pair the phone with Dana first.", unpaired.error)
        assertTrue(requests.isEmpty())
        answers["POST /api/devices/pairings"] = json(429, """{"error":"too_many_pairings"}""")
        val refused = WatchPairingOffer.offer(ServerConfig(base, "tok-phone"), WearPairRequest("W"))
        assertEquals("", refused.code)
        assertEquals("The server refused: too_many_pairings", refused.error)
    }

    // --- the controller (the pairing screen and Settings) ---

    private class Recorder {
        var config = ServerConfig("", "")
        val paired = mutableListOf<ServerConfig>()
        var unpaired = 0
        val notes = mutableListOf<String>()
    }

    private fun controller(rec: Recorder) = PairingController(
        scope, deviceName = "Google Pixel 8",
        current = { rec.config },
        onPaired = { rec.paired += it; rec.config = it },
        onUnpaired = { rec.unpaired++; rec.config = ServerConfig("", "") },
        notify = { rec.notes += it },
    )

    @Test fun aLinkPairsAnUnpairedPhone() = runBlocking {
        answers["POST /api/pair"] = json(200, pairedJson)
        val rec = Recorder()
        val c = controller(rec)
        assertTrue(c.openLink("dana://pair?server=${java.net.URLEncoder.encode(base, "UTF-8")}&code=k7qm-4xpz"))
        waitFor { rec.paired.isNotEmpty() }
        assertEquals(ServerConfig(base, "tok-new", "dev-new", "Google Pixel 8", "Robin"), rec.paired.single())
        assertEquals("K7QM4XPZ", JSONObject(request("POST", "/api/pair")!!.body.readUtf8()).getString("code"))
        assertFalse(c.state.value.requested)
        assertNull(c.state.value.error)
        assertEquals("Paired as Google Pixel 8, Robin's device", pairedAsText(rec.paired.single()))
    }

    @Test fun aLinkWhilePairedOnlyFillsTheForm() {
        val rec = Recorder().apply { config = ServerConfig(base, "tok-old", "dev-old") }
        val c = controller(rec)
        assertTrue(c.openLink("dana://pair?server=https%3A%2F%2Fother.example.com&code=K7QM4XPZ"))
        assertFalse(c.openLink("https://example.com"))
        assertEquals("https://other.example.com", c.state.value.server)
        assertEquals("K7QM4XPZ", c.state.value.code)
        assertTrue(c.state.value.requested)
        assertTrue(requests.isEmpty())
    }

    @Test fun theFormIsCheckedBeforeAnythingIsSent() {
        val c = controller(Recorder())
        c.setCode("K7QM4XPZ")
        assertNull(c.pair())
        assertEquals("Type your Dana server's address.", c.state.value.error)
        c.setServer("dana.example.com")
        c.setCode("K7QM")
        assertNull(c.pair())
        assertTrue(c.state.value.error!!.startsWith("A pairing code has 8"))
        assertTrue(requests.isEmpty())
    }

    @Test fun aRefusedCodeIsShownAndNothingIsSaved() = runBlocking {
        answers["POST /api/pair"] = json(403, """{"error":"invalid_code"}""")
        val rec = Recorder()
        val c = controller(rec)
        c.setServer(base)
        c.setCode("K7QM4XPZ")
        c.pair()!!.join()
        assertTrue(c.state.value.error!!.startsWith("That code isn't right"))
        assertFalse(c.state.value.busy)
        assertTrue(rec.paired.isEmpty())
    }

    @Test fun anUnreachableServerSaysSo() = runBlocking {
        val rec = Recorder()
        val c = controller(rec)
        val gone = base
        server.shutdown()
        c.setServer(gone)
        c.setCode("K7QM4XPZ")
        c.pair()!!.join()
        assertTrue(c.state.value.error!!.startsWith("Couldn't reach a Dana server at $gone"))
        assertTrue(rec.paired.isEmpty())
    }

    @Test fun pairingAgainRemovesTheOldDeviceWithItsOwnToken() = runBlocking {
        answers["POST /api/pair"] = json(200, pairedJson)
        answers["DELETE /api/devices/dev-old"] = json(200, """{"ok":true}""")
        val rec = Recorder().apply { config = ServerConfig(base, "tok-old", "dev-old", "Old", "Robin") }
        val c = controller(rec)
        c.pairAgain()
        assertEquals(base, c.state.value.server)
        assertTrue(c.state.value.requested)
        c.setCode("K7QM4XPZ")
        c.pair()!!.join()
        waitFor { request("DELETE", "/api/devices/dev-old") != null }
        assertEquals("Bearer tok-old", request("DELETE", "/api/devices/dev-old")!!.getHeader("Authorization"))
        assertEquals("dev-new", rec.config.deviceId)
        assertTrue(rec.notes.isEmpty())
    }

    @Test fun unpairRemovesThisDeviceThenForgetsTheServer() = runBlocking {
        answers["DELETE /api/devices/dev-1"] = json(200, """{"ok":true}""")
        val rec = Recorder().apply { config = ServerConfig(base, "tok-1", "dev-1") }
        controller(rec).unpair().join()
        assertEquals("Bearer tok-1", request("DELETE", "/api/devices/dev-1")!!.getHeader("Authorization"))
        assertEquals(1, rec.unpaired)
        assertTrue(rec.notes.isEmpty())
    }

    @Test fun unpairWorksOfflineAndSaysTheDeviceIsStillListed() = runBlocking {
        val rec = Recorder().apply { config = ServerConfig(base, "tok-1", "dev-1") }
        server.shutdown()
        controller(rec).unpair().join()
        assertEquals(1, rec.unpaired)
        assertTrue(rec.notes.single().contains("Devices page"))
    }

    @Test fun pairedConfigFromTheAnswer() {
        val answer = io.github.ronganam.dana.protocol.PairAnswer.decode(pairedJson)!!
        assertEquals(ServerConfig("https://dana.example.com", "tok-new", "dev-new", "Google Pixel 8", "Robin"),
            ServerConfig.paired("dana.example.com/", answer))
        assertTrue(ServerConfig.paired("dana.example.com", answer).isConfigured)
        assertEquals("Paired as this phone", pairedAsText(ServerConfig("https://x", "t")))
    }

    private fun waitFor(condition: () -> Boolean) {
        val until = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
        while (!condition()) {
            if (System.nanoTime() > until) fail("timed out")
            Thread.sleep(10)
        }
    }
}
