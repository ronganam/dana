package io.github.ronganam.dana.shopping

import io.github.ronganam.dana.data.network.HttpResult
import io.github.ronganam.dana.data.shopping.ShoppingOp
import io.github.ronganam.dana.data.shopping.ShoppingStore
import io.github.ronganam.dana.data.shopping.ShoppingWatch
import io.github.ronganam.dana.data.shopping.Who
import kotlinx.coroutines.runBlocking
import io.github.ronganam.dana.protocol.ShopRequest
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
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
    private val list = ShoppingFixture.list
    private var online = true
    private var reply: (String, String) -> Pair<Int, String> = { m, p ->
        when {
            m == "GET" -> 200 to ShoppingFixture.raw("owner_shared")
            p.startsWith("/api/shopping/items") && m == "POST" -> 200 to ShoppingFixture.raw("add")
            else -> 200 to ShoppingFixture.raw("check")
        }
    }
    private val sent = mutableListOf<Triple<String, String, JsonObject?>>()

    private fun store() = ShoppingStore(
        dir,
        ShoppingStore.Transport { m, p, b ->
            if (!online) throw IOException("unreachable")
            sent += Triple(m, p, b as? JsonObject)
            reply(m, p).let { (code, text) -> HttpResult(code, text) }
        },
        me = { ShoppingFixture.noa },
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
        val answer = ShoppingWatch.answer(s, ShopRequest(action = "check", id = bananas.id, listId = list, ref = "w-1"))
        assertTrue(answer.ok)
        assertFalse(answer.queued)
        assertEquals("w-1", answer.ref)
        val (method, path, body) = writes().single()
        assertEquals("PATCH", method)
        assertEquals("/api/shopping/items/${bananas.id}?list=$list", path)
        assertEquals(true, body!!["checked"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun anUncheckSendsCheckedFalse() = runBlocking {
        val s = store().also { it.refresh() }
        val id = s.shown()!!.items.first().id
        ShoppingWatch.answer(s, ShopRequest(action = "uncheck", id = id, listId = list))
        assertEquals(false, writes().single().third!!["checked"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun anAddIsSplitLikeThePhonesAddField() = runBlocking {
        val s = store().also { it.refresh() }
        val answer = ShoppingWatch.answer(s, ShopRequest(action = "add", text = "2 חלב, eggs", listId = list, ref = "w-2"))
        assertTrue(answer.ok)
        val items = writes().single().third!!["items"]!!.jsonArray
        assertEquals(2, items.size)
        assertEquals("חלב", items[0].jsonObject["name"]!!.jsonPrimitive.content)
        assertEquals("2", items[0].jsonObject["quantity"]!!.jsonPrimitive.content)
        assertEquals("eggs", items[1].jsonObject["name"]!!.jsonPrimitive.content)
    }

    @Test
    fun offlineTheChangeIsQueuedAndThatCountsAsDone() = runBlocking {
        val s = store().also { it.refresh() }
        online = false
        val id = s.shown()!!.items.first().id
        val answer = ShoppingWatch.answer(s, ShopRequest(action = "check", id = id, listId = list, ref = "w-3"))
        assertTrue(answer.ok)
        assertTrue(answer.queued)
        assertEquals("w-3", answer.ref)
        assertEquals(1, s.state.value.queued.size)
        assertFalse("shown ticked at once", s.shown()!!.items.first { it.id == id }.let { !it.checked })
    }

    @Test
    fun aRefusalIsASentenceToShow() = runBlocking {
        val s = store().also { it.refresh() }
        reply = { m, _ -> if (m == "GET") 200 to ShoppingFixture.raw("owner_shared") else 403 to ShoppingFixture.raw("error_read_only") }
        val answer = ShoppingWatch.answer(s, ShopRequest(action = "check", id = s.shown()!!.items.first().id, listId = list, ref = "w-4"))
        assertFalse(answer.ok)
        assertEquals("This list is view only.", answer.error)
        assertEquals("w-4", answer.ref)
    }

    @Test
    fun badRequestsAreRefusedWithoutAnyWrite() = runBlocking {
        val s = store().also { it.refresh() }
        assertEquals("Nothing to add.", ShoppingWatch.answer(s, ShopRequest(action = "add", text = " , ", listId = list)).error)
        assertEquals("Unknown action.", ShoppingWatch.answer(s, ShopRequest(action = "explode", listId = list)).error)
        assertTrue(writes().isEmpty())
        val empty = store()
        dir.listFiles()?.forEach { it.delete() }
        assertEquals("The list hasn't loaded yet.", ShoppingWatch.answer(empty, ShopRequest(action = "check", id = "x")).error)
    }

    @Test
    fun aFailingChangeStillAnswersWithItsRef() = runBlocking {
        val s = store().also { it.refresh() }
        val answer = ShoppingWatch.answer(s, ShopRequest(action = "check", id = "x", listId = list, ref = "w-5")) {
            throw IllegalStateException("boom")
        }
        assertFalse(answer.ok)
        assertEquals("Couldn't do that on the phone.", answer.error)
        assertEquals("w-5", answer.ref)
    }

    @Test
    fun theRequestIsTheSameOpAsATapOnThePhone() = runBlocking {
        val s = store().also { it.refresh() }
        var made: ShoppingOp? = null
        val id = s.shown()!!.items.first().id
        ShoppingWatch.answer(s, ShopRequest(action = "check", id = id, listId = list)) { made = it; ShoppingStore.Outcome.Queued }
        val check = made as ShoppingOp.Check
        assertEquals(list, check.listId)
        assertEquals(id, check.itemId)
        assertTrue(check.checked)
    }
}
