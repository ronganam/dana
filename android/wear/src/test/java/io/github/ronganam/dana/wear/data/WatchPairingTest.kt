package io.github.ronganam.dana.wear.data

import com.sun.net.httpserver.HttpServer
import io.github.ronganam.dana.protocol.PairAnswer
import io.github.ronganam.dana.protocol.PairAnswerDevice
import io.github.ronganam.dana.protocol.PairError
import io.github.ronganam.dana.protocol.WearPairOffer
import io.github.ronganam.dana.protocol.WearPairRequest
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.IOException
import java.net.InetSocketAddress

/**
 * The watch's own pairing: redeeming a code at a local server that answers
 * like `POST /api/pair`, and the steps through the phone (fakes for the
 * Data Layer side).
 */
class WatchPairingTest {
    private lateinit var http: HttpServer
    private var status = 200
    private var answer = ""
    private var retryAfter: String? = null
    private var lastBody = ""
    private var lastPath = ""

    @Before fun setUp() {
        http = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        http.createContext("/") { exchange ->
            lastPath = exchange.requestURI.path
            lastBody = exchange.requestBody.readBytes().toString(Charsets.UTF_8)
            retryAfter?.let { exchange.responseHeaders.add("Retry-After", it) }
            val bytes = answer.toByteArray()
            exchange.sendResponseHeaders(status, bytes.size.toLong())
            exchange.responseBody.use { it.write(bytes) }
        }
        http.start()
    }

    @After fun tearDown() = http.stop(0)

    private val base get() = "http://127.0.0.1:${http.address.port}"

    private val pairedJson = """{"token":"tok-w","device":{"id":"dev-w","name":"Galaxy Watch","kind":"watch"},
        "user":{"id":"u1","name":"Robin"},"server":{"version":"10.0.0"}}"""

    @Test fun redeemSendsKindWatchAndReadsTheDevice() {
        answer = pairedJson
        val outcome = WatchPairClient.redeem("$base/", "K7QM4XPZ", "Galaxy Watch")
        assertTrue(outcome is PairOutcome.Paired)
        outcome as PairOutcome.Paired
        assertEquals(base, outcome.server)
        assertEquals("tok-w", outcome.answer.token)
        assertEquals("dev-w", outcome.answer.device.id)
        assertEquals("/api/pair", lastPath)
        assertEquals("""{"code":"K7QM4XPZ","kind":"watch","name":"Galaxy Watch"}""", lastBody)
    }

    @Test fun redeemRefusalsBecomeSentences() {
        status = 403; answer = """{"error":"invalid_code"}"""
        assertEquals(PairError.InvalidCode.message, (WatchPairClient.redeem(base, "K7QM4XPZ", "W") as PairOutcome.Refused).message)
        status = 409; answer = """{"error":"wrong_kind","expected":"phone"}"""
        assertEquals(PairError.WrongKind("phone").message, (WatchPairClient.redeem(base, "K7QM4XPZ", "W") as PairOutcome.Refused).message)
        status = 429; answer = """{"error":"rate_limited"}"""; retryAfter = "45"
        assertEquals("Too many wrong codes. Try again in 45 seconds.",
            (WatchPairClient.redeem(base, "K7QM4XPZ", "W") as PairOutcome.Refused).message)
        status = 200; answer = """{"device":{}}"""; retryAfter = null
        assertEquals("The server's answer had no token.", (WatchPairClient.redeem(base, "K7QM4XPZ", "W") as PairOutcome.Refused).message)
    }

    @Test fun serverAddressNormalization() {
        assertEquals("https://dana.example.com", WatchPairClient.normalizeServer(" dana.example.com/ "))
        assertEquals("http://10.0.2.2:18081", WatchPairClient.normalizeServer("http://10.0.2.2:18081"))
        assertEquals("", WatchPairClient.normalizeServer("https://"))
        assertTrue(WatchPairClient.redeem("  ", "K7QM4XPZ", "W") is PairOutcome.Refused)
    }

    // --- the steps through the phone ---

    private class Fakes {
        var phone = true
        var offer: WearPairOffer? = null
        val asked = mutableListOf<WearPairRequest>()
        val redeemed = mutableListOf<Pair<String, String>>()
        val saved = mutableListOf<Pair<String, PairAnswer>>()
        var redeem: (String, String) -> PairOutcome = { s, _ -> PairOutcome.Paired(s, PairAnswer("tok", PairAnswerDevice("d", "Galaxy Watch", "watch"))) }
    }

    private fun flow(f: Fakes) = WatchPairingFlow(
        name = "Galaxy Watch",
        phoneConnected = { f.phone },
        askPhone = { f.asked += it; f.offer },
        redeem = { server, code, _ -> f.redeemed += server to code; f.redeem(server, code) },
        save = { server, answer -> f.saved += server to answer },
    )

    @Test fun thePhonesCodeIsRedeemedAndSaved() = runBlocking {
        val f = Fakes().apply { offer = WearPairOffer("https://dana.example.com", "watch234") }
        val state = flow(f).throughPhone()
        assertEquals(WatchPairState.Paired("https://dana.example.com", "Galaxy Watch"), state)
        assertEquals(listOf(WearPairRequest("Galaxy Watch")), f.asked)
        assertEquals(listOf("https://dana.example.com" to "WATCH234"), f.redeemed)
        assertEquals("tok", f.saved.single().second.token)
    }

    @Test fun noPhoneMeansTheCodeEntry() = runBlocking {
        val f = Fakes().apply { phone = false }
        assertEquals(WatchPairState.NeedsCode(NeedsCodeReason.NO_PHONE), flow(f).throughPhone())
        assertTrue(f.asked.isEmpty())
    }

    @Test fun aSilentOrRefusingPhoneMeansTheCodeEntry() = runBlocking {
        val f = Fakes()
        assertEquals(WatchPairState.NeedsCode(NeedsCodeReason.PHONE_SILENT), flow(f).throughPhone())
        f.offer = WearPairOffer(error = "Pair the phone with Dana first.")
        assertEquals(WatchPairState.NeedsCode(NeedsCodeReason.PHONE_REFUSED, "Pair the phone with Dana first."), flow(f).throughPhone())
        assertTrue(f.redeemed.isEmpty())
        assertTrue(f.saved.isEmpty())
    }

    @Test fun typedCodesAreCheckedAndFailuresKept() {
        val f = Fakes()
        assertEquals(WatchPairState.NeedsCode(NeedsCodeReason.BAD_CODE), flow(f).withCode("https://x", "K7QM"))
        assertTrue(f.redeemed.isEmpty())
        f.redeem = { _, _ -> PairOutcome.Refused("That code isn't right") }
        assertEquals(WatchPairState.NeedsCode(NeedsCodeReason.SERVER_REFUSED, "That code isn't right"), flow(f).withCode("https://x", "k7qm-4xpz"))
        f.redeem = { _, _ -> throw IOException("down") }
        assertEquals(WatchPairState.NeedsCode(NeedsCodeReason.UNREACHABLE), flow(f).withCode("https://x", "K7QM4XPZ"))
        assertTrue(f.saved.isEmpty())
        assertNull((flow(f).withCode("https://x", "K7QM4XPZ") as WatchPairState.NeedsCode).message)
    }
}
