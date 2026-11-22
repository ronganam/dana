package io.github.ronganam.dana.push

import io.github.ronganam.dana.data.network.ApiService
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.TimeUnit

/** A ping on either transport → the time noted, then the one catch-up (PhoneEvents.catchUp's fetch). */
class PushPingTest {

    @Test
    fun aPingNotesItsTimeThenCatchesUpOnce() = runTest {
        val noted = mutableListOf<Long>()
        var catchUps = 0
        val ok = PushPing(now = { 1_700_000_000_000L }, noteLastPing = { noted += it }, catchUp = { catchUps++ }).run()
        assertTrue(ok)
        assertEquals(listOf(1_700_000_000_000L), noted)
        assertEquals(1, catchUps)
    }

    @Test
    fun aSlowCatchUpIsCutOffAtTheTimeout() = runTest {
        val noted = mutableListOf<Long>()
        val ok = PushPing(now = { 5L }, noteLastPing = { noted += it }, catchUp = { delay(60_000) }, timeoutMs = 9_000).run()
        assertFalse(ok)
        assertEquals("the ping is noted even when the catch-up runs out of time", listOf(5L), noted)
    }

    @Test
    fun theCatchUpFetchesTheEventsWithTheAppsToken() {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("""{"events":[{"seq":8,"type":"reminder","data":{"task_id":"t1"}}],"latest":8,"registered":true}"""))
            server.start()
            val api = ApiService(server.url("").toString().trimEnd('/'), "device-token")
            var fetched = 0
            val ping = PushPing(now = { 0L }, noteLastPing = {}, catchUp = {
                fetched = api.devices.phoneEvents(7).events.size
            })
            assertTrue(runBlocking { ping.run() })
            val req = server.takeRequest(5, TimeUnit.SECONDS)!!
            assertEquals("GET", req.method)
            assertTrue(req.path!!.startsWith("/api/phone/events?after=7"))
            assertEquals("Bearer device-token", req.getHeader("Authorization"))
            assertEquals(1, fetched)
        }
    }
}
