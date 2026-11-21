package io.github.ronganam.dana.wear.shopping

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The watch's shopping rules: the snapshot, ticks and Undo, the queue to the phone, a new snapshot over changes on their way. */
class ShopModelTest {
    private val list = "a142b3bd0785"

    private fun item(id: String, name: String, aisleKey: String, aisle: String, qty: String = "") =
        JSONObject().put("id", id).put("name", name).put("quantity", qty).put("aisle", aisle).put("aisle_key", aisleKey)

    /** A snapshot as the phone sends it (`ShoppingSnapshot.fields` + `WearCards.sendSnapshot`). */
    private fun snapshot(vararg items: JSONObject, count: Int = items.size, canEdit: Boolean = true, version: Long = 7): JSONObject =
        JSONObject().put("inbox_needs", 2).put("shopping_count", count).put("shopping", JSONArray(items.toList()))
            .put("shopping_list_id", list).put("shopping_version", version).put("shopping_can_edit", canEdit)

    private val tomatoes = item("t1", "עגבניות שרי", "produce", "Fruit & vegetables")
    private val bananas = item("b1", "bananas", "produce", "Fruit & vegetables", "1 kg")
    private val bread = item("p1", "לחם מחמצת", "bakery", "Bread & bakery")
    private val milk = item("m1", "Milk", "dairy", "Dairy & eggs", "2")

    private fun state(vararg items: JSONObject, count: Int = items.size, canEdit: Boolean = true) =
        ShopState(list = ShopList.parse(snapshot(*items, count = count, canEdit = canEdit)))

    private fun names(s: ShopState, now: Long) = Shop.items(s, now).map { it.item.name + if (it.ticked) " ✓" else "" }
    private fun ShopState.item(id: String) = list!!.items.first { it.id == id }

    @Test
    fun theSnapshotIsParsedInItsOrderWithAisleLabels() {
        val s = state(tomatoes, bananas, bread, milk, count = 12)
        assertEquals(list, s.list!!.listId)
        assertEquals(7L, s.list!!.version)
        assertTrue(s.list!!.canEdit)
        assertEquals("1 kg", s.list!!.items[1].quantity)
        val rows = Shop.rows(s, 0)
        assertEquals(
            listOf("aisle:produce", "item:t1", "item:b1", "aisle:bakery", "item:p1", "aisle:dairy", "item:m1"),
            rows.map { it.key.substringBeforeLast(':').takeIf { k -> k.startsWith("aisle") } ?: it.key }
        )
        assertEquals("Fruit & vegetables", (rows[0] as ShopRow.Aisle).name)
        assertEquals("the title counts what the snapshot left out", 12, Shop.toBuy(s, 0))
        assertEquals("12 to buy", Shop.subtitle(s, 0))
    }

    @Test
    fun noShoppingPartMeansNoListAndBadItemsAreSkipped() {
        assertNull(ShopList.parse(JSONObject().put("inbox_needs", 3)))
        val odd = snapshot(tomatoes, JSONObject().put("name", "no id"), JSONObject().put("id", "x"))
        assertEquals(listOf("t1"), ShopList.parse(odd)!!.items.map { it.id })
        assertTrue("an older phone without can_edit can still tick", ShopList.parse(JSONObject().put("shopping", JSONArray()))!!.canEdit)
        assertEquals(0, Shop.toBuy(ShopState(), 0))
    }

    @Test
    fun aTickShowsAtOnceLingersThenLeavesWithUndoOffered() {
        var s = state(tomatoes, bananas, bread)
        s = Shop.tick(s, s.item("b1"), "r1", now = 1_000)
        assertEquals(listOf("עגבניות שרי", "bananas ✓", "לחם מחמצת"), names(s, 1_500))
        assertEquals("a ticked row no longer counts", 2, Shop.toBuy(s, 1_500))
        assertEquals("b1", s.undo!!.itemId)
        assertEquals(1, s.tickedThisVisit)
        assertEquals(listOf("עגבניות שרי", "לחם מחמצת"), names(s, 1_000 + Shop.LINGER_MS))
        s = Shop.expire(s, 1_000 + Shop.LINGER_MS + 1)
        assertTrue(s.lingering.isEmpty())
        assertNotNull("Undo stays after the row left", s.undo)
        assertNull(Shop.expire(s, 1_000 + Shop.LINGER_MS + Shop.UNDO_MS).undo)
        assertEquals(1_000 + Shop.LINGER_MS, Shop.nextDeadline(Shop.tick(state(bananas), state(bananas).item("b1"), "r", 1_000)))
    }

    @Test
    fun aSecondTapWhileItLingersPutsItBackAndNothingIsSent() {
        var s = state(tomatoes, bananas)
        s = Shop.tick(s, s.item("b1"), "r1", 0)
        s = Shop.untick(s, "b1", "r2", 300)
        assertTrue("the tick never left the watch", s.pending.isEmpty())
        assertEquals(listOf("עגבניות שרי", "bananas"), names(s, 400))
        assertNull(s.undo)
        assertEquals(0, s.tickedThisVisit)
    }

    @Test
    fun requestsGoOneAtATimeInOrder() {
        var s = state(tomatoes, bananas, bread)
        s = Shop.tick(s, s.item("t1"), "r1", 0)
        s = Shop.tick(s, s.item("b1"), "r2", 10)
        assertEquals("r1", Shop.next(s)!!.ref)
        s = Shop.sent(s, "r1", 20)
        assertNull("one on its way at a time", Shop.next(s))
        s = Shop.answered(s, JSONObject().put("ok", true).put("queued", false).put("ref", "r1"), 200)
        assertEquals("r2", Shop.next(s)!!.ref)
        val payload = Shop.next(s)!!.payload()
        assertEquals("check", payload.getString("action"))
        assertEquals("b1", payload.getString("id"))
        assertEquals(list, payload.getString("list_id"))
        assertEquals("r2", payload.getString("ref"))
    }

    @Test
    fun undoAfterTheTickWasSentSendsAnUncheckAndTheRowComesBack() {
        var s = state(tomatoes, bananas, bread)
        s = Shop.tick(s, s.item("b1"), "r1", 0)
        s = Shop.sent(s, "r1", 10)
        s = Shop.answered(s, JSONObject().put("ok", true).put("queued", true).put("ref", "r1"), 300)
        // The phone's next snapshot no longer has bananas.
        s = Shop.onSnapshot(s, ShopList.parse(snapshot(tomatoes, bread, version = 8)))
        assertTrue("answered requests leave with the snapshot", s.pending.isEmpty())
        s = Shop.expire(s, 2_000)
        assertEquals(listOf("עגבניות שרי", "לחם מחמצת"), names(s, 2_000))
        s = Shop.untick(s, s.undo!!.itemId, "r2", 2_500)
        assertEquals(listOf("עגבניות שרי", "bananas", "לחם מחמצת"), names(s, 2_500))
        val p = Shop.next(s)!!
        assertEquals("uncheck", p.payload().getString("action"))
        assertEquals("b1", p.payload().getString("id"))
        assertNull(s.undo)
    }

    @Test
    fun aSnapshotBuiltBeforeTheAnswerDoesNotUndoATick() {
        var s = state(tomatoes, bananas)
        s = Shop.tick(s, s.item("b1"), "r1", 0)
        s = Shop.sent(s, "r1", 10)
        // A snapshot the phone had already scheduled still has bananas.
        s = Shop.onSnapshot(s, ShopList.parse(snapshot(tomatoes, bananas, item("n1", "eggs", "dairy", "Dairy & eggs"), version = 8)))
        assertEquals(1, s.pending.size)
        assertEquals(listOf("עגבניות שרי", "eggs"), names(Shop.expire(s, 5_000), 5_000))
        assertEquals("its new items are taken", 2, Shop.toBuy(s, 5_000))
    }

    @Test
    fun aRefusalBringsTheRowBackAndSaysWhy() {
        var s = state(tomatoes, bananas)
        s = Shop.tick(s, s.item("b1"), "r1", 0)
        s = Shop.sent(s, "r1", 10)
        s = Shop.answered(s, JSONObject().put("ok", false).put("error", "This list is view only.").put("ref", "r1"), 200)
        assertEquals(listOf("עגבניות שרי", "bananas"), names(s, 300))
        assertEquals("This list is view only.", s.notice!!.text)
        assertTrue(s.notice!!.error)
        assertNull(s.undo)
        assertEquals(0, s.tickedThisVisit)
        assertTrue(s.pending.isEmpty())
    }

    @Test
    fun anAnswerForSomethingElseChangesNothing() {
        val s = Shop.sent(Shop.tick(state(bananas), state(bananas).item("b1"), "r1", 0), "r1", 1)
        assertEquals(s, Shop.answered(s, JSONObject().put("ok", false).put("ref", "other"), 5))
        assertEquals(s, Shop.answered(s, JSONObject().put("ok", true), 5))
    }

    @Test
    fun anUnansweredRequestCountsAsDoneAfterTheTimeout() {
        var s = Shop.sent(Shop.tick(state(bananas, milk), state(bananas).item("b1"), "r1", 0), "r1", 100)
        assertEquals(Pending.Stage.SENT, Shop.timeouts(s, 100 + Shop.ANSWER_TIMEOUT_MS - 1).pending[0].stage)
        s = Shop.timeouts(s, 100 + Shop.ANSWER_TIMEOUT_MS)
        assertEquals(Pending.Stage.ANSWERED, s.pending[0].stage)
        assertNull(Shop.next(s))
    }

    @Test
    fun withoutThePhoneTicksStayOnTheWatchAndAreSentWhenItIsBack() {
        var s = state(tomatoes, bananas, bread)
        s = Shop.tick(s, s.item("t1"), "r1", 0)
        s = Shop.unreachable(s, 5)
        s = Shop.tick(s, s.item("p1"), "r2", 50)
        s = Shop.unreachable(s, 60)
        assertTrue(s.unreachable)
        assertEquals(2, Shop.waiting(s))
        assertEquals("shown as done", listOf("bananas"), names(s, 5_000))
        // Kept across a restart of the watch app.
        val restored = Shop.parseQueue(Shop.queueJson(s.pending))
        assertEquals(s.pending, restored)
        // The phone is back: a snapshot (from before the ticks) arrives.
        s = Shop.onSnapshot(s.copy(pending = restored), ShopList.parse(snapshot(tomatoes, bananas, bread)))
        assertFalse(s.unreachable)
        assertEquals("r1", Shop.next(s)!!.ref)
        assertEquals(listOf("bananas"), names(s, 5_000))
    }

    @Test
    fun undoOfATickStillOnTheWatchSimplyDropsIt() {
        var s = state(tomatoes, bananas)
        s = Shop.unreachable(Shop.tick(s, s.item("b1"), "r1", 0), 1)
        s = Shop.untick(s, "b1", "r2", 2_000)
        assertTrue(s.pending.isEmpty())
        assertEquals(listOf("עגבניות שרי", "bananas"), names(s, 2_000))
    }

    @Test
    fun aRequestSentBeforeARestartIsTakenAsAnswered() {
        var s = state(bananas)
        s = Shop.sent(Shop.tick(s, s.item("b1"), "r1", 0), "r1", 5)
        val restored = Shop.parseQueue(Shop.queueJson(s.pending))
        assertEquals(Pending.Stage.ANSWERED, restored.single().stage)
        assertTrue("answered ones are not written at all", Shop.parseQueue(Shop.queueJson(restored)).isEmpty())
        assertTrue(Shop.parseQueue("not json").isEmpty())
    }

    @Test
    fun addingByVoiceSaysWhatWasAddedOrWhatTheyDo() {
        var s = state(bananas)
        s = Shop.add(s, "  milk, eggs ", "r1", 0)
        assertEquals("add", Shop.next(s)!!.payload().getString("action"))
        assertEquals("milk, eggs", Shop.next(s)!!.payload().getString("text"))
        val offline = Shop.unreachable(s, 1)
        assertEquals("Phone not reachable. Will add “milk, eggs” when it's back.", offline.notice!!.text)
        s = Shop.answered(Shop.sent(s, "r1", 2), JSONObject().put("ok", true).put("queued", false).put("ref", "r1"), 300)
        assertEquals("Added: milk, eggs", s.notice!!.text)
        assertFalse(s.notice!!.error)
        assertNull(Shop.expire(s, 300 + Shop.NOTICE_MS).notice)
        assertEquals("blank text adds nothing", s, Shop.add(s, "   ", "r9", 5))
    }

    @Test
    fun aViewOnlyListCantBeTickedOrAddedTo() {
        val s = state(bananas, canEdit = false)
        assertEquals(s, Shop.tick(s, s.item("b1"), "r1", 0))
        assertEquals(s, Shop.add(s, "milk", "r2", 0))
        assertEquals("View only · 1 to buy", Shop.subtitle(s, 0))
    }

    @Test
    fun emptyAndAllDone() {
        var s = state(bananas)
        assertFalse(Shop.allDone(s, 0))
        s = Shop.tick(s, s.item("b1"), "r1", 0)
        assertFalse("not while it lingers", Shop.allDone(s, 500))
        assertTrue(Shop.allDone(Shop.expire(s, 2_000), 2_000))
        assertEquals("the screen says it, not the title", "", Shop.subtitle(Shop.expire(s, 2_000), 2_000))
        assertEquals("View only", Shop.subtitle(state(canEdit = false), 0))
        val fresh = Shop.startVisit(Shop.expire(s, 2_000))
        assertEquals(0, fresh.tickedThisVisit)
        assertFalse("a new visit to an empty list is just empty", Shop.allDone(fresh, 3_000))
        assertEquals("a visit doesn't reset while a row lingers", 1, Shop.startVisit(s).tickedThisVisit)
    }

    @Test
    fun aRowPutBackGoesNearWhereItWasEvenWhenTheListChanged() {
        var s = state(tomatoes, bananas, bread, milk)
        s = Shop.tick(s, s.item("p1"), "r1", 0)
        s = Shop.answered(Shop.sent(s, "r1", 1), JSONObject().put("ok", true).put("ref", "r1"), 2)
        s = Shop.onSnapshot(s, ShopList.parse(snapshot(tomatoes, milk)))
        s = Shop.expire(s, 5_000)
        s = Shop.untick(s, "p1", "r2", 5_100)
        assertEquals(listOf("עגבניות שרי", "Milk", "לחם מחמצת"), names(s, 5_100))
    }

    @Test
    fun directionComesFromTheLetters() {
        assertTrue(Shop.mostlyRtl("עגבניות שרי"))
        assertTrue(Shop.mostlyRtl("חלב 3%"))
        assertFalse(Shop.mostlyRtl("bananas"))
        assertFalse(Shop.mostlyRtl("1 kg"))
        assertFalse(Shop.mostlyRtl("Coca Cola זירו"))
    }
}
