package io.github.ronganam.dana.signals

import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.signals.CarVia
import io.github.ronganam.dana.data.signals.RejectedSignal
import io.github.ronganam.dana.data.signals.SignalEvent
import io.github.ronganam.dana.data.signals.SignalQueue
import io.github.ronganam.dana.data.signals.SignalSender
import io.github.ronganam.dana.data.signals.SignalsJson
import io.github.ronganam.dana.data.signals.SignalsPosted
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files

class SignalQueueTest {
    private lateinit var dir: File
    private val now = 1_795_400_000_000L
    private val sent = mutableListOf<List<SignalEvent>>()
    private var answer: (List<SignalEvent>) -> SignalsPosted = { SignalsPosted(200, it.size) }

    @Before fun setUp() { dir = Files.createTempDirectory("signals").toFile() }
    @After fun tearDown() { dir.deleteRecursively() }

    private fun queue() = SignalQueue(File(dir, "pending_signals.json"), SignalSender { batch -> sent += batch; answer(batch) }, now = { now })

    private fun unlocks(n: Int, from: Long = now - 3_600_000) = (0 until n).map { SignalEvent.Unlock(from + it * 1000L) }

    @Test
    fun sendsOldestFirstInBatchesOfAtMost200() = runTest {
        val q = queue()
        val events = unlocks(450)
        q.add(events)

        val r = q.flush()

        assertEquals(listOf(200, 200, 50), sent.map { it.size })
        assertEquals(events, sent.flatten())
        assertEquals(450, r.sent)
        assertEquals(0, r.remaining)
        assertFalse(r.retry)
        assertTrue(q.pending().isEmpty())
        assertFalse(File(dir, "pending_signals.json").exists())
    }

    @Test
    fun aFailedBatchStaysWholeAndIsSentAgain() = runTest {
        val q = queue()
        val events = unlocks(250)
        q.add(events)
        answer = { SignalsPosted(-1) }  // unreachable

        val failed = q.flush()

        assertTrue(failed.retry)
        assertEquals(250, failed.remaining)
        assertEquals(events, q.pending())
        assertEquals(1, sent.size)

        // A server error is retried the same way.
        answer = { SignalsPosted(503) }
        assertTrue(q.flush().retry)
        assertEquals(events, q.pending())

        sent.clear()
        answer = { SignalsPosted(200, it.size) }
        val ok = q.flush()
        assertEquals(events.take(200), sent[0])
        assertEquals(events.drop(200), sent[1])
        assertEquals(0, ok.remaining)
    }

    @Test
    fun rejectedEventsAreDroppedWithTheirBatch() = runTest {
        val q = queue()
        val events = listOf(
            SignalEvent.Unlock(now - 60_000),
            SignalEvent.Zone(arrive = true, zoneId = "gone", at = now - 50_000),
            SignalEvent.Car(enter = true, at = now - 40_000, via = CarVia.BLUETOOTH)
        )
        q.add(events)
        answer = { SignalsPosted(200, accepted = 2, rejected = listOf(RejectedSignal(1, "unknown_zone"))) }

        val r = q.flush()

        assertEquals(2, r.sent)
        assertEquals(0, r.remaining)
        assertTrue(q.pending().isEmpty())
    }

    @Test
    fun anUnreadableBatchIsDroppedAndTheRestStillGoes() = runTest {
        val q = queue()
        val events = unlocks(201)
        q.add(events)
        var calls = 0
        answer = { batch -> if (calls++ == 0) SignalsPosted(400) else SignalsPosted(200, batch.size) }

        val r = q.flush()

        assertEquals(2, sent.size)
        assertEquals(events.last(), sent[1].single())
        assertEquals(1, r.sent)
        assertTrue(q.pending().isEmpty())
    }

    @Test
    fun notTakenWithoutRetryKeepsTheEvents() = runTest {
        val q = queue()
        q.add(unlocks(3))
        answer = { SignalsPosted(404) }  // an older server without /api/signals

        val r = q.flush()

        assertFalse(r.retry)
        assertEquals(3, r.remaining)
        assertEquals(3, q.pending().size)
    }

    @Test
    fun eventsTooOldForTheServerAreDroppedBeforeSending() = runTest {
        val q = queue()
        val stale = SignalEvent.Screen(now - SignalQueue.MAX_AGE_MS - 120_000, now - SignalQueue.MAX_AGE_MS - 60_000)
        val fresh = SignalEvent.Unlock(now - 1000)
        q.add(listOf(stale, fresh))

        q.flush()

        assertEquals(listOf(fresh), sent.flatten())
    }

    @Test
    fun eventsAddedWhileSendingGoInTheSameRun() = runTest {
        lateinit var q: SignalQueue
        val late = SignalEvent.Unlock(now)
        var first = true
        q = SignalQueue(File(dir, "pending_signals.json"), SignalSender { batch ->
            sent += batch
            if (first) { first = false; q.add(listOf(late)) }
            SignalsPosted(200, batch.size)
        }, now = { now })
        q.add(unlocks(2))

        q.flush()

        assertEquals(listOf(2, 1), sent.map { it.size })
        assertEquals(late, sent[1].single())
        assertTrue(q.pending().isEmpty())
    }

    @Test
    fun theQueueSurvivesARestartWithEveryKindOfEvent() {
        val events = listOf(
            SignalEvent.Unlock(now - 5000),
            SignalEvent.Screen(now - 4000, now - 3000),
            SignalEvent.Car(enter = false, at = now - 2000, via = CarVia.ANDROID_AUTO),
            SignalEvent.Car(enter = true, at = now - 1500, via = CarVia.ACTIVITY),
            SignalEvent.Sleep(now - 30_000_000, now - 1200),
            SignalEvent.Zone(arrive = false, zoneId = "zone-1", at = now - 1000)
        )
        queue().add(events)

        assertEquals(events, queue().pending())
    }

    @Test
    fun theBodyIsTheContractsShape() {
        val body = SignalsJson.body(listOf(
            SignalEvent.Unlock(1795400000000),
            SignalEvent.Screen(1795400000000, 1795400090000),
            SignalEvent.Car(enter = true, at = 1795400000000, via = CarVia.BLUETOOTH),
            SignalEvent.Zone(arrive = true, zoneId = "z1", at = 1795400000000),
            SignalEvent.Car(enter = false, at = 1795400000000, via = CarVia.ACTIVITY),
            SignalEvent.Sleep(1795370000000, 1795398000000)
        ))
        val events = (body["events"] as JsonArray).map { it as JsonObject }

        assertEquals(setOf("type", "at"), events[0].keys)
        assertEquals(setOf("type", "start", "end"), events[1].keys)
        assertEquals("1795400090000", events[1]["end"]!!.jsonPrimitive.content)
        assertEquals(mapOf("type" to "car", "action" to "enter", "at" to "1795400000000", "via" to "bluetooth"),
            events[2].mapValues { it.value.jsonPrimitive.content })
        assertEquals(mapOf("type" to "zone", "action" to "arrive", "zone_id" to "z1", "at" to "1795400000000"),
            events[3].mapValues { it.value.jsonPrimitive.content })
        assertEquals(mapOf("type" to "car", "action" to "leave", "at" to "1795400000000", "via" to "activity"),
            events[4].mapValues { it.value.jsonPrimitive.content })
        assertEquals(mapOf("type" to "sleep", "start" to "1795370000000", "end" to "1795398000000", "via" to "health_connect"),
            events[5].mapValues { it.value.jsonPrimitive.content })
    }

    @Test
    fun theAnswerReadsAcceptedAndRejected() {
        val posted = SignalsJson.posted(200, """{"accepted": 4, "rejected": [{"index": 3, "error": "unknown_zone"}], "new": 1}""")
        assertEquals(4, posted.accepted)
        assertEquals(listOf(RejectedSignal(3, "unknown_zone")), posted.rejected)
        assertTrue(posted.ok)
        assertTrue(ApiJson.parseToJsonElement(SignalsJson.encodeEvents(emptyList())) is JsonArray)
    }

    @Test
    fun malformedQueuedEntriesAreSkipped() {
        val decoded = SignalsJson.decodeEvents(
            """[{"type":"unlock"},{"type":"screen","start":5,"end":3},{"type":"car","action":"enter","at":1,"via":"teleport"},
               {"type":"sleep","start":1,"end":5},{"type":"sleep","start":1,"end":5,"via":"guess"},
               {"type":"zone","action":"arrive","at":2},{"type":"unlock","at":7},3,{"type":"martian","at":1}]"""
        )
        assertEquals(listOf(SignalEvent.Unlock(7)), decoded)
    }
}
