package io.github.ronganam.dana.push

import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.ApiService
import io.github.ronganam.dana.data.network.PushRegistered
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.util.concurrent.TimeUnit

/** `POST /api/phone/register` for each transport, the server's refusals, and which distributor is used. */
class PushRegistrationTest {

    private fun register(answer: MockResponse, transport: PushTransport, address: String): Pair<PushRegistered, JsonObject> =
        MockWebServer().use { server ->
            server.enqueue(answer)
            server.start()
            val api = ApiService(server.url("").toString().trimEnd('/'), "device-token")
            val res = runBlocking { api.devices.registerPush(transport.wire, address, "10.1.0") }
            val req = server.takeRequest(5, TimeUnit.SECONDS)!!
            assertEquals("POST", req.method)
            assertEquals("/api/phone/register", req.path)
            assertEquals("Bearer device-token", req.getHeader("Authorization"))
            res to (ApiJson.parseToJsonElement(req.body.readUtf8()) as JsonObject)
        }

    @Test
    fun unifiedPushSendsTheEndpoint() {
        val (res, body) = register(MockResponse().setBody("""{"ok":true,"transport":"unifiedpush","latest_seq":42}"""),
            PushTransport.UNIFIEDPUSH, "https://ntfy.sh/upAbC?up=1")
        assertEquals(PushRegistered.Ok(42), res)
        assertEquals(
            mapOf("transport" to JsonPrimitive("unifiedpush"), "endpoint" to JsonPrimitive("https://ntfy.sh/upAbC?up=1"),
                "app_version" to JsonPrimitive("10.1.0")),
            body.toMap()
        )
    }

    @Test
    fun fcmSendsTheToken() {
        val (res, body) = register(MockResponse().setBody("""{"ok":true,"transport":"fcm","latest_seq":3}"""),
            PushTransport.FCM, "fcm-token-1")
        assertEquals(PushRegistered.Ok(3), res)
        assertEquals(setOf("transport", "token", "app_version"), body.keys)
        assertEquals(JsonPrimitive("fcm"), body["transport"])
        assertEquals(JsonPrimitive("fcm-token-1"), body["token"])
    }

    @Test
    fun aRefusalCarriesTheServersReason() {
        val (res, _) = register(MockResponse().setResponseCode(400)
            .setBody("""{"error":"fcm_not_configured","detail":"This server has no Firebase key."}"""),
            PushTransport.FCM, "fcm-token-1")
        assertEquals(PushRegistered.Refused(400, "fcm_not_configured", "This server has no Firebase key."), res)
    }

    @Test
    fun theDistributorIsTheChosenOneElseNtfyElseTheFirst() {
        val self = "io.github.ronganam.dana"
        assertEquals("org.other.push", Distributors.pick(listOf(Distributors.NTFY, "org.other.push"), "org.other.push", self))
        assertEquals(Distributors.NTFY, Distributors.pick(listOf("org.other.push", Distributors.NTFY), null, self))
        assertEquals("ntfy when the chosen one is gone",
            Distributors.NTFY, Distributors.pick(listOf("org.other.push", Distributors.NTFY), "org.removed", self))
        assertEquals("org.other.push", Distributors.pick(listOf(self, "org.other.push"), null, self))
        assertNull(Distributors.pick(listOf(self), null, self))
        assertNull(Distributors.pick(emptyList(), null, self))
    }

    @Test
    fun transportsMatchTheServersNames() {
        assertEquals(listOf("fcm", "unifiedpush"), PushTransport.entries.map { it.wire })
        assertEquals(PushTransport.UNIFIEDPUSH, PushTransport.of("unifiedpush"))
        assertNull(PushTransport.of("apns"))
    }
}
