package io.github.ronganam.dana.shopping

import io.github.ronganam.dana.data.shopping.AddPart
import io.github.ronganam.dana.data.shopping.ShoppingOp
import io.github.ronganam.dana.data.shopping.ShoppingStore
import io.github.ronganam.dana.data.shopping.Who
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.io.IOException
import java.nio.file.Files

/** The store: the disk cache, optimistic changes, the offline queue (order, client ids, dropped-when-gone, restarts), Undo. */
class ShoppingStoreTest {
    private lateinit var dir: File
    private val me = Who("d3b2f55f2a70", "Noa Peretz")
    private val list = "a142b3bd0785"

    /** A scripted server: answers per request, or unreachable. Records what it was sent. */
    private inner class FakeServer {
        var online = true
        val sent = mutableListOf<Triple<String, String, JSONObject?>>()
        var answer: (method: String, path: String, body: JSONObject?) -> Pair<Int, String> = { m, p, _ ->
            when {
                m == "GET" -> 200 to ShoppingFixture.raw("owner_shared")
                p.startsWith("/api/shopping/items") && m == "POST" -> 200 to ShoppingFixture.raw("add")
                else -> 200 to ShoppingFixture.raw("check")
            }
        }
        val transport = ShoppingStore.Transport { m, p, b ->
            if (!online) throw IOException("unreachable")
            sent += Triple(m, p, b)
            answer(m, p, b)
        }
        fun writes() = sent.filter { it.first != "GET" }
    }

    private var retries = 0
    private fun store(server: FakeServer) = ShoppingStore(dir, server.transport, me = { me }, scheduleRetry = { retries++ }, immediateTimeoutMs = 2_000)

    @Before fun setUp() { dir = Files.createTempDirectory("shopping").toFile() }
    @After fun tearDown() { dir.deleteRecursively() }

    @Test
    fun theLastListIsKeptOnDiskAndShownAfterARestart() = runBlocking {
        val server = FakeServer()
        val first = store(server)
        assertEquals(200, first.refresh().first)
        assertTrue(File(dir, ShoppingStore.CACHE).exists())
        server.online = false
        val again = store(server)
        again.load()
        assertEquals(14, again.shown()!!.items.size)
        assertEquals(-1, again.refresh().first)
        assertEquals("an unreachable server keeps the kept list", 14, again.shown()!!.items.size)
    }

    @Test
    fun aChangeIsShownAtOnceAndTheAnswerReplacesIt() = runBlocking {
        val server = FakeServer()
        val s = store(server)
        s.refresh()
        val bananas = s.shown()!!.items.first { it.name == "bananas" }
        val outcome = s.submit(ShoppingOp.Check("o1", list, bananas.id, true, "bananas"))
        assertTrue(outcome is ShoppingStore.Outcome.Sent)
        assertEquals("0b2e817d7fd3", (outcome as ShoppingStore.Outcome.Sent).answer?.undo?.id)
        assertEquals("/api/shopping/items/${bananas.id}?list=$list", server.writes().single().second)
        assertTrue(s.state.value.inflight.isEmpty() && s.state.value.queued.isEmpty())
    }

    @Test
    fun aRefusedChangeIsTakenBackAndSaid() = runBlocking {
        val server = FakeServer()
        val s = store(server)
        s.refresh()
        server.answer = { _, _, _ -> 403 to ShoppingFixture.raw("error_read_only") }
        val avocado = s.shown()!!.items.first { it.name == "Avocado" }
        val outcome = s.submit(ShoppingOp.Remove("o1", list, avocado.id, "Avocado"))
        assertTrue(outcome is ShoppingStore.Outcome.Rejected)
        assertEquals("read_only", (outcome as ShoppingStore.Outcome.Rejected).body?.optString("error"))
        assertTrue("reverted", s.shown()!!.items.any { it.id == avocado.id })
        assertFalse(File(dir, ShoppingStore.QUEUE).exists())
    }

    @Test
    fun offlineChangesQueueInOrderAndSurviveARestart() = runBlocking {
        val server = FakeServer()
        store(server).refresh()
        server.online = false
        val s = store(server)
        s.load()
        val avocado = s.shown()!!.items.first { it.name == "Avocado" }
        val add = ShoppingOp.Add("o1", list, listOf(AddPart("client-saffron", "Saffron"), AddPart("client-za-atar", "Za'atar", "2")))
        assertEquals(ShoppingStore.Outcome.Queued, s.submit(add))
        assertEquals(ShoppingStore.Outcome.Queued, s.submit(ShoppingOp.Check("o2", list, avocado.id, true, "Avocado")))
        // Back online: a new change still waits behind the queue, so the order never changes.
        server.online = true
        assertEquals(ShoppingStore.Outcome.Queued, s.submit(ShoppingOp.Remove("o3", list, "client-saffron", "Saffron")))
        assertTrue(retries >= 2)
        val shown = s.shown()!!
        assertTrue("queued changes are shown", shown.items.none { it.name == "Saffron" } && shown.items.first { it.id == avocado.id }.checked)
        assertEquals("2", shown.items.first { it.id == "client-za-atar" }.quantity)

        // The app is killed and started again: the queue is still there and shown.
        val restarted = store(server)
        restarted.load()
        assertEquals(listOf("o1", "o2", "o3"), restarted.state.value.queued.map { it.id })
        assertTrue(restarted.shown()!!.items.first { it.id == avocado.id }.checked)

        assertEquals(0, restarted.flush())
        val writes = server.writes()
        assertEquals(listOf("POST", "PATCH", "DELETE"), writes.map { it.first })
        assertEquals("/api/shopping/items/client-saffron?list=$list", writes[2].second)
        val ids = writes[0].third!!.getJSONArray("items").let { a -> (0 until a.length()).map { a.getJSONObject(it).getString("id") } }
        assertEquals("the client ids made offline are the ones sent", listOf("client-saffron", "client-za-atar"), ids)
        assertFalse(File(dir, ShoppingStore.QUEUE).exists())
    }

    @Test
    fun aRetriedAddSendsTheSameClientIds() = runBlocking {
        val server = FakeServer()
        val s = store(server)
        s.refresh()
        server.answer = { _, _, _ -> 503 to "{}" }
        val add = ShoppingOp.Add("o1", list, listOf(AddPart("client-milk-1", "Milk")))
        assertEquals(ShoppingStore.Outcome.Queued, s.submit(add))
        assertEquals("a 5xx keeps it waiting", 1, s.flush())
        server.answer = { _, _, _ -> 200 to ShoppingFixture.raw("add") }
        assertEquals(0, s.flush())
        val ids = server.writes().map { it.third!!.getJSONArray("items").getJSONObject(0).getString("id") }
        assertEquals(listOf("client-milk-1", "client-milk-1", "client-milk-1"), ids)
    }

    @Test
    fun aWaitingChangeWhoseItemIsGoneIsDroppedSilently() = runBlocking {
        val server = FakeServer()
        val s = store(server)
        s.refresh()
        server.online = false
        s.submit(ShoppingOp.Check("o1", list, "gone-item", true, "Ghost"))
        s.submit(ShoppingOp.Remove("o2", list, "read-only-item", "Bread"))
        s.submit(ShoppingOp.ClearBought("o3", list))
        server.online = true
        server.answer = { _, p, _ ->
            when {
                "gone-item" in p -> 404 to ShoppingFixture.raw("error_not_found")
                "read-only-item" in p -> 403 to ShoppingFixture.raw("error_read_only")
                else -> 200 to ShoppingFixture.raw("clear_bought")
            }
        }
        val notice = async(start = kotlinx.coroutines.CoroutineStart.UNDISPATCHED) { withTimeoutOrNull(2_000) { s.notices.first() } }
        assertEquals(0, s.flush())
        assertEquals("all three were tried, in order", 3, server.writes().size)
        val said = notice.await()
        assertNotNull(said)
        assertTrue("only the refusal that isn't 'gone' is said: $said", said!!.contains("Bread") && said.contains("view only"))
        assertTrue(s.state.value.queued.isEmpty())
    }

    @Test
    fun undoOfAWaitingChangeTakesItOutOrUndoesItOnTheServer() = runBlocking {
        val server = FakeServer()
        val s = store(server)
        s.refresh()
        server.online = false
        val avocado = s.shown()!!.items.first { it.name == "Avocado" }
        s.submit(ShoppingOp.Check("o1", list, avocado.id, true, "Avocado"))
        s.submit(ShoppingOp.Add("o2", list, listOf(AddPart("client-x-001", "X"))))
        // Still waiting: Undo takes it out; it never reaches the server.
        assertEquals(ShoppingStore.Cancel.Dropped, s.cancel("o1"))
        assertFalse(s.shown()!!.items.first { it.id == avocado.id }.checked)
        // Sent meanwhile: Undo uses the undo id the server gave it.
        server.online = true
        assertEquals(0, s.flush())
        assertEquals(listOf("POST"), server.writes().map { it.first })
        assertEquals(ShoppingStore.Cancel.Sent("8702857c9de9"), s.cancel("o2"))
        assertEquals(ShoppingStore.Cancel.Gone, s.cancel("o2"))
        assertEquals(ShoppingStore.Cancel.Gone, s.cancel("never"))
    }

    @Test
    fun anOlderReadNeverReplacesANewerAnswer() = runBlocking {
        val server = FakeServer()
        val s = store(server)
        s.refresh()
        assertEquals(20L, s.state.value.server!!.list.version)
        // A GET that comes back with an older version while a write is on its way is ignored...
        val gate = kotlinx.coroutines.CompletableDeferred<Unit>()
        server.answer = { m, _, _ -> if (m == "GET") 200 to ShoppingFixture.raw("check") else 200 to ShoppingFixture.raw("owner_shared") }
        val transportWrite = server.transport
        val slow = ShoppingStore(dir, { m, p, b -> if (m != "GET") gate.await(); transportWrite.call(m, p, b) }, me = { me })
        slow.load()
        val write = async { slow.submit(ShoppingOp.ClearBought("o1", list)) }
        while (!slow.busy) kotlinx.coroutines.delay(5)
        slow.refresh()
        assertEquals("busy: the older read (v4) is not kept", 20L, slow.state.value.server!!.list.version)
        gate.complete(Unit)
        write.await()
        // ...and with nothing on its way a read is the truth.
        slow.refresh()
        assertEquals(4L, slow.state.value.server!!.list.version)
    }

    @Test
    fun anotherUserStartsWithNothingOfThePreviousOnes() = runBlocking {
        val server = FakeServer()
        val s = store(server)
        s.refresh()
        server.online = false
        s.submit(ShoppingOp.ClearBought("o1", list))
        assertTrue(File(dir, ShoppingStore.QUEUE).exists())
        s.forget()
        assertEquals(null, s.shown())
        assertTrue(s.state.value.queued.isEmpty())
        assertFalse(File(dir, ShoppingStore.CACHE).exists())
        assertFalse(File(dir, ShoppingStore.QUEUE).exists())
        server.online = true
        assertEquals("nothing of theirs is sent for the new user", 0, s.flush())
        assertTrue(server.writes().isEmpty())
    }

    @Test
    fun aListLookedAtIsNotCachedAsTheActiveOne() = runBlocking {
        val server = FakeServer()
        val s = store(server)
        s.refresh()
        val before = File(dir, ShoppingStore.CACHE).readText()
        // A screen whose `lists` says it is not the active list (a member's view of another list).
        val viewed = ShoppingFixture.screen("member_shared").let { v -> v.copy(lists = v.lists.map { it.copy(active = !it.active) }) }
        assertFalse(viewed.isActive)
        s.accept(viewed, "{}")
        assertEquals(before, File(dir, ShoppingStore.CACHE).readText())
    }
}
