package app.ganam.assistant.shopping

import app.ganam.assistant.data.shopping.ShoppingOp
import app.ganam.assistant.data.shopping.ShoppingStore
import app.ganam.assistant.data.shopping.ShoppingWatch
import app.ganam.assistant.data.shopping.Who
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.io.IOException
import java.nio.file.Files

/** The watch's `/shopping_action` on the phone: the request it makes, the answer (`ok`, `queued`, `error`, `ref`). */
class ShoppingWatchTest {
    private lateinit var dir: File
    private val list = "a142b3bd0785"
    private var online = true
    private var reply: (String, String) -> Pair<Int, String> = { m, p ->
        when {
            m == "GET" -> 200 to ShoppingFixture.raw("owner_shared")
            p.startsWith("/api/shopping/items") && m == "POST" -> 200 to ShoppingFixture.raw("add")
            else -> 200 to ShoppingFixture.raw("check")
        }
    }
    private val sent = mutableListOf<Triple<String, String, JSONObject?>>()

    private fun store() = ShoppingStore(
        dir,
        ShoppingStore.Transport { m, p, b ->
            if (!online) throw IOException("unreachable")
            sent += Triple(m, p, b)
            reply(m, p)
        },
        me = { Who("d3b2f55f2a70", "Noa Peretz") },
        scheduleRetry = {},
        immediateTimeoutMs = 2_000
    )

    private fun writes() = sent.filter { it.first != "GET" }

    @Before fun setUp() { dir = Files.createTempDirectory("shopping-watch").toFile() }
    @After fun tearDown() { dir.deleteRecursively() }

    @Test
    fun aTickGoesThroughTheStoreAndTheRefComesBack() = runBlocking {
        val s = store().also { it.refresh() }
        val bananas = s.shown()!!.items.first { it.name == "bananas" }
        val answer = ShoppingWatch.answer(s, JSONObject().put("action", "check").put("id", bananas.id).put("list_id", list).put("ref", "w-1"))
        assertTrue(answer.getBoolean("ok"))
        assertFalse(answer.getBoolean("queued"))
        assertEquals("w-1", answer.getString("ref"))
        val (method, path, body) = writes().single()
        assertEquals("PATCH", method)
        assertEquals("/api/shopping/items/${bananas.id}?list=$list", path)
        assertEquals(true, body!!.getBoolean("checked"))
    }

    @Test
    fun anUncheckSendsCheckedFalse() = runBlocking {
        val s = store().also { it.refresh() }
        val id = s.shown()!!.items.first().id
        ShoppingWatch.answer(s, JSONObject().put("action", "uncheck").put("id", id).put("list_id", list))
        assertEquals(false, writes().single().third!!.getBoolean("checked"))
    }

    @Test
    fun anAddIsSplitLikeThePhonesAddField() = runBlocking {
        val s = store().also { it.refresh() }
        val answer = ShoppingWatch.answer(s, JSONObject().put("action", "add").put("text", "2 חלב, eggs").put("list_id", list).put("ref", "w-2"))
        assertTrue(answer.getBoolean("ok"))
        val items = writes().single().third!!.getJSONArray("items")
        assertEquals(2, items.length())
        assertEquals("חלב", items.getJSONObject(0).getString("name"))
        assertEquals("2", items.getJSONObject(0).getString("quantity"))
        assertEquals("eggs", items.getJSONObject(1).getString("name"))
    }

    @Test
    fun offlineTheChangeIsQueuedAndThatCountsAsDone() = runBlocking {
        val s = store().also { it.refresh() }
        online = false
        val id = s.shown()!!.items.first().id
        val answer = ShoppingWatch.answer(s, JSONObject().put("action", "check").put("id", id).put("list_id", list).put("ref", "w-3"))
        assertTrue(answer.getBoolean("ok"))
        assertTrue(answer.getBoolean("queued"))
        assertEquals("w-3", answer.getString("ref"))
        assertEquals(1, s.state.value.queued.size)
        assertFalse("shown ticked at once", s.shown()!!.items.first { it.id == id }.let { !it.checked })
    }

    @Test
    fun aRefusalIsASentenceToShow() = runBlocking {
        val s = store().also { it.refresh() }
        reply = { m, _ -> if (m == "GET") 200 to ShoppingFixture.raw("owner_shared") else 403 to ShoppingFixture.raw("error_read_only") }
        val answer = ShoppingWatch.answer(s, JSONObject().put("action", "check").put("id", s.shown()!!.items.first().id).put("list_id", list).put("ref", "w-4"))
        assertFalse(answer.getBoolean("ok"))
        assertEquals("This list is view only.", answer.getString("error"))
        assertEquals("w-4", answer.getString("ref"))
    }

    @Test
    fun badRequestsAreRefusedWithoutAnyWrite() = runBlocking {
        val s = store().also { it.refresh() }
        assertEquals("Nothing to add.", ShoppingWatch.answer(s, JSONObject().put("action", "add").put("text", " , ").put("list_id", list)).getString("error"))
        assertEquals("Unknown action.", ShoppingWatch.answer(s, JSONObject().put("action", "explode").put("list_id", list)).getString("error"))
        assertTrue(writes().isEmpty())
        val empty = store()
        dir.listFiles()?.forEach { it.delete() }
        assertEquals("The list hasn't loaded yet.", ShoppingWatch.answer(empty, JSONObject().put("action", "check").put("id", "x")).getString("error"))
    }

    @Test
    fun aFailingChangeStillAnswersWithItsRef() = runBlocking {
        val s = store().also { it.refresh() }
        val answer = ShoppingWatch.answer(s, JSONObject().put("action", "check").put("id", "x").put("list_id", list).put("ref", "w-5")) {
            throw IllegalStateException("boom")
        }
        assertFalse(answer.getBoolean("ok"))
        assertEquals("Couldn't do that on the phone.", answer.getString("error"))
        assertEquals("w-5", answer.getString("ref"))
    }

    @Test
    fun theRequestIsTheSameOpAsATapOnThePhone() = runBlocking {
        val s = store().also { it.refresh() }
        var made: ShoppingOp? = null
        val id = s.shown()!!.items.first().id
        ShoppingWatch.answer(s, JSONObject().put("action", "check").put("id", id).put("list_id", list)) { made = it; ShoppingStore.Outcome.Queued }
        val check = made as ShoppingOp.Check
        assertEquals(list, check.listId)
        assertEquals(id, check.itemId)
        assertTrue(check.checked)
    }
}
