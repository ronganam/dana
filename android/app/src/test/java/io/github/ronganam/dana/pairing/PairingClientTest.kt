package io.github.ronganam.dana.pairing

import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.data.pairing.PairError
import io.github.ronganam.dana.data.pairing.PairOutcome
import io.github.ronganam.dana.data.pairing.PairedDevice
import io.github.ronganam.dana.data.pairing.PairingClient
import io.github.ronganam.dana.data.pairing.PairingCode
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** `POST /api/pair` from a tablet or TV: the request it sends and every answer the server can give. */
class PairingClientTest {
    private lateinit var server: MockWebServer
    private val client = PairingClient()

    @Before fun start() { server = MockWebServer().apply { start() } }
    @After fun stop() { server.shutdown() }

    private val base get() = server.url("/").toString()

    @Test fun pairsAndReadsTheDeviceWithItsRoomAndRole() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"token":"tok-123","device":{"id":"9f8e7d6c5b4a","user_id":"a1","name":"Lenovo Tab",
            "kind":"tablet","room":"Kitchen","role":"kitchen","paired":true},"user":{"id":"a1","name":"Gal"},"server":{"version":"10.1.0"}}"""))

        val outcome = client.pair(base, "k7qm-4xpz", kind = "tablet", name = "Lenovo Tab")

        val paired = outcome as PairOutcome.Paired
        assertEquals("tok-123", paired.token)
        assertEquals(PairedDevice("9f8e7d6c5b4a", "Lenovo Tab", "tablet", "Kitchen", "kitchen", "Gal"), paired.device)
        assertEquals("10.1.0", paired.serverVersion)
        assertEquals("Lenovo Tab · Kitchen", paired.device.summary)
        assertEquals("Hall TV · Upstairs · Living room", PairedDevice(name = "Hall TV", room = "Upstairs", role = "living_room").summary)

        val sent = server.takeRequest()
        assertEquals("POST", sent.method)
        assertEquals("/api/pair", sent.path)
        assertNull("pairing carries no token", sent.getHeader("Authorization"))
        val body = JSONObject(sent.body.readUtf8())
        assertEquals("K7QM4XPZ", body.getString("code"))
        assertEquals("tablet", body.getString("kind"))
        assertEquals("Lenovo Tab", body.getString("name"))
    }

    @Test fun wrongCodeIsInvalidCode() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(403).setBody("""{"error":"invalid_code","detail":"no such code"}"""))
        assertEquals(PairOutcome.Failed(PairError.InvalidCode), client.pair(base, "AAAAAAAA", "tv", "TV"))
    }

    @Test fun codeForAnotherKindNamesIt() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"error":"wrong_kind","expected":"phone","detail":"x"}"""))
        val error = (client.pair(base, "AAAAAAAA", "tv", "TV") as PairOutcome.Failed).error
        assertEquals(PairError.WrongKind("phone"), error)
        assertTrue(error.message.contains("phone"))
    }

    @Test fun rateLimitedSaysWhenToTryAgain() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(429).setHeader("Retry-After", "300").setBody("""{"error":"rate_limited"}"""))
        val error = (client.pair(base, "AAAAAAAA", "tablet", "Tab") as PairOutcome.Failed).error
        assertEquals(PairError.RateLimited(300), error)
        assertTrue(error.message, error.message.contains("5 minutes"))

        server.enqueue(MockResponse().setResponseCode(429).setHeader("Retry-After", "45"))
        assertTrue((client.pair(base, "AAAAAAAA", "tablet", "Tab") as PairOutcome.Failed).error.message.contains("45 seconds"))
    }

    @Test fun badKindAndOtherAnswers() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(400).setBody("""{"error":"invalid","field":"kind","detail":"kind must be one of phone"}"""))
        assertEquals(PairError.Invalid("kind must be one of phone"), (client.pair(base, "AAAAAAAA", "x", "X") as PairOutcome.Failed).error)

        server.enqueue(MockResponse().setResponseCode(404).setBody("<html>not here</html>"))
        assertEquals(PairError.Http(404), (client.pair(base, "AAAAAAAA", "tv", "TV") as PairOutcome.Failed).error)

        server.enqueue(MockResponse().setBody("{}"))
        assertEquals(PairError.Http(200), (client.pair(base, "AAAAAAAA", "tv", "TV") as PairOutcome.Failed).error)
    }

    @Test fun deadServerOrNoAddressIsUnreachable() = runBlocking {
        val url = base
        server.shutdown()
        assertTrue((client.pair(url, "AAAAAAAA", "tv", "TV") as PairOutcome.Failed).error is PairError.Unreachable)
        assertTrue((client.pair("  ", "AAAAAAAA", "tv", "TV") as PairOutcome.Failed).error is PairError.Unreachable)
        server = MockWebServer().apply { start() }
    }

    @Test fun readsItselfBackAndRemovesItselfWithItsOwnToken() = runBlocking {
        val config = ServerConfig(ServerConfig.normalize(base), "tok-123")
        server.enqueue(MockResponse().setBody("""{"devices":[{"id":"aaa","name":"Phone","kind":"phone","current":false},
            {"id":"bbb","name":"Hall TV","kind":"tv","room":"Hall","role":"hall","current":true}]}"""))
        assertEquals(PairedDevice("bbb", "Hall TV", "tv", "Hall", "hall", "Robin"), client.current(config, "Robin"))
        assertEquals("Bearer tok-123", server.takeRequest().getHeader("Authorization"))

        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        assertTrue(client.remove(config, "bbb"))
        val sent = server.takeRequest()
        assertEquals("DELETE", sent.method)
        assertEquals("/api/devices/bbb", sent.path)
        assertEquals("Bearer tok-123", sent.getHeader("Authorization"))

        server.enqueue(MockResponse().setResponseCode(401))
        assertFalse(client.remove(config, "bbb"))
        server.enqueue(MockResponse().setResponseCode(401))
        assertNull(client.current(config, "Robin"))
    }

    @Test fun codesAreNormalizedAndChecked() {
        assertEquals("K7QM4XPZ", PairingCode.normalize(" k7qm-4xpz "))
        assertTrue(PairingCode.isComplete("K7QM4XPZ"))
        assertFalse("no 0, O, 1 or I", PairingCode.isComplete("K7QM4XP0"))
        assertFalse(PairingCode.isComplete("K7QM4XP"))
        assertEquals("K7QM 4XPZ", PairingCode.grouped("K7QM4XPZ"))
        assertEquals("Living room", PairedDevice.roleLabel("living_room"))
    }
}
