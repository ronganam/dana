package io.github.ronganam.dana.data.notifications

import io.github.ronganam.dana.data.network.ApiService
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.TimeUnit

/** The app-icon upload against `/api/phone/app-icons` (a fake server) and its decisions: what to send, when. */
class AppIconSyncTest {

    private val hour = 3600_000L
    private val now = 1_800_000_000_000L

    @Test
    fun theHashIsTheFirstEightHexCharsOfTheSha256() {
        // sha256("abc") = ba7816bf8f01cfea…
        assertEquals("ba7816bf", AppIconSyncPlan.hashOf("abc".toByteArray()))
    }

    @Test
    fun uploadsWhatTheServerLacksOrHoldsWithAnotherHash() {
        val local = mapOf("com.whatsapp" to "aaaa0001", "com.example.bank" to "bbbb0002", "org.example.mail" to "cccc0003")
        val server = mapOf("com.whatsapp" to "aaaa0001", "com.example.bank" to "ffff9999", "net.example.gone" to "dddd0004")
        // Same hash: skipped; changed: sent; missing: sent; only on the server: left alone.
        assertEquals(listOf("com.example.bank", "org.example.mail"), AppIconSyncPlan.toUpload(local, server))
        assertEquals(emptyList<String>(), AppIconSyncPlan.toUpload(local, local))
        assertEquals(listOf("com.whatsapp"), AppIconSyncPlan.toUpload(mapOf("com.whatsapp" to "x"), emptyMap()))
    }

    @Test
    fun atMostOneSyncInSixHoursUnlessANewPackageAppeared() {
        val seen = setOf("com.whatsapp", "com.example.bank")
        // Never synced.
        assertTrue(AppIconSyncPlan.due(now, 0, seen, emptySet()))
        // Recently synced, nothing new: wait.
        assertFalse(AppIconSyncPlan.due(now, now - hour, seen, seen))
        assertFalse(AppIconSyncPlan.due(now, now - 6 * hour + 1, seen, seen))
        // Six hours on: due.
        assertTrue(AppIconSyncPlan.due(now, now - 6 * hour, seen, seen))
        // A package the last sync didn't see: due at once.
        assertTrue(AppIconSyncPlan.due(now, now - 60_000, seen + "org.example.mail", seen))
        // A package gone since is not a reason.
        assertFalse(AppIconSyncPlan.due(now, now - 60_000, setOf("com.whatsapp"), seen))
        // The clock went back: don't wait for a sync "in the future".
        assertTrue(AppIconSyncPlan.due(now, now + hour, seen, seen))
    }

    @Test
    fun onlyPackageNamesTheServerTakes() {
        assertTrue(AppIconSyncPlan.validPackage("com.whatsapp"))
        assertTrue(AppIconSyncPlan.validPackage("com.example.app_2"))
        assertFalse(AppIconSyncPlan.validPackage("android"))
        assertFalse(AppIconSyncPlan.validPackage("2com.example"))
        assertFalse(AppIconSyncPlan.validPackage("com..example"))
        assertFalse(AppIconSyncPlan.validPackage("com.example-app"))
        assertFalse(AppIconSyncPlan.validPackage("com." + "a".repeat(200)))
    }

    private fun sync(
        vararg answers: MockResponse,
        local: Map<String, ByteArray>
    ): Pair<AppIconSyncResult, List<RecordedRequest>> = MockWebServer().use { server ->
        answers.forEach { server.enqueue(it) }
        server.start()
        val api = ApiService(server.url("").toString().trimEnd('/'), "device-token")
        val hashes = local.mapValues { AppIconSyncPlan.hashOf(it.value) }
        val result = runBlocking { AppIconSync.upload(api.devices, hashes) { local[it] } }
        val requests = generateSequence { server.takeRequest(100, TimeUnit.MILLISECONDS) }.toList()
        result to requests
    }

    private val whatsapp = byteArrayOf(1, 2, 3)
    private val bank = byteArrayOf(4, 5, 6)
    private val mail = byteArrayOf(7, 8, 9)

    @Test
    fun putsOnlyTheMissingAndChangedIcons() {
        val list = """{"icons": {"com.whatsapp": "${AppIconSyncPlan.hashOf(whatsapp)}", "com.example.bank": "00000000"}}"""
        val (result, requests) = sync(
            MockResponse().setBody(list), MockResponse().setResponseCode(204), MockResponse().setResponseCode(204),
            local = mapOf("com.whatsapp" to whatsapp, "com.example.bank" to bank, "org.example.mail" to mail)
        )
        assertEquals(AppIconSyncResult.Done(listOf("com.example.bank", "org.example.mail")), result)
        assertEquals("GET", requests[0].method)
        assertEquals("/api/phone/app-icons", requests[0].path)
        assertEquals("Bearer device-token", requests[0].getHeader("Authorization"))
        assertEquals(listOf("PUT /api/phone/app-icons/com.example.bank", "PUT /api/phone/app-icons/org.example.mail"),
            requests.drop(1).map { "${it.method} ${it.path}" })
        assertEquals("image/png", requests[1].getHeader("Content-Type"))
        assertArrayEquals(bank, requests[1].body.readByteArray())
        assertArrayEquals(mail, requests[2].body.readByteArray())
    }

    @Test
    fun nothingToSendIsDoneAfterTheList() {
        val list = """{"icons": {"com.whatsapp": "${AppIconSyncPlan.hashOf(whatsapp)}"}}"""
        val (result, requests) = sync(MockResponse().setBody(list), local = mapOf("com.whatsapp" to whatsapp))
        assertEquals(AppIconSyncResult.Done(emptyList()), result)
        assertEquals(1, requests.size)
    }

    @Test
    fun withoutThePhoneAddonNothingIsSent() {
        val (result, requests) = sync(
            MockResponse().setResponseCode(403).setBody("""{"error":"not_connected","addon":"phone"}"""),
            local = mapOf("com.whatsapp" to whatsapp)
        )
        assertEquals(AppIconSyncResult.NotConnected, result)
        assertEquals(1, requests.size)
    }

    @Test
    fun tooManyStopsARefusedIconIsSkippedAServerErrorFails() {
        // 400 for the first: skipped, the next still goes; 409 then: stop.
        val (stopped, requests) = sync(
            MockResponse().setBody("""{"icons": {}}"""),
            MockResponse().setResponseCode(400).setBody("""{"error":"not_png"}"""),
            MockResponse().setResponseCode(409).setBody("""{"error":"too_many"}"""),
            local = mapOf("com.example.bank" to bank, "com.whatsapp" to whatsapp, "org.example.mail" to mail)
        )
        assertEquals(AppIconSyncResult.Done(emptyList()), stopped)
        assertEquals(listOf("/api/phone/app-icons/com.example.bank", "/api/phone/app-icons/com.whatsapp"), requests.drop(1).map { it.path })

        val (failed, _) = sync(
            MockResponse().setBody("""{"icons": {}}"""), MockResponse().setResponseCode(503),
            local = mapOf("com.whatsapp" to whatsapp)
        )
        assertEquals(AppIconSyncResult.Failed, failed)
        val (listFailed, _) = sync(MockResponse().setResponseCode(500), local = mapOf("com.whatsapp" to whatsapp))
        assertEquals(AppIconSyncResult.Failed, listFailed)
    }

    @Test
    fun anUnreachableServerFails() {
        val server = MockWebServer().apply { start() }
        val url = server.url("").toString().trimEnd('/')
        server.shutdown()
        val result = runBlocking { AppIconSync.upload(ApiService(url, "t").devices, mapOf("com.whatsapp" to "x")) { whatsapp } }
        assertEquals(AppIconSyncResult.Failed, result)
    }
}
