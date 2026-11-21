package io.github.ronganam.dana.shopping

import io.github.ronganam.dana.data.shopping.AddPart
import io.github.ronganam.dana.data.shopping.ShoppingOp
import io.github.ronganam.dana.data.shopping.ShoppingOps
import io.github.ronganam.dana.data.shopping.Who
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/** Changes applied on screen (the server's add rules), versions, requests and the queue's encoding. */
class ShoppingOpsTest {
    private val me = Who("d3b2f55f2a70", "Noa Peretz")
    private val now = "2026-11-16T15:00:00Z"
    private val s = ShoppingFixture.screen("owner_shared")
    private val list = s.list.id

    private fun apply(vararg ops: ShoppingOp) = ShoppingOps.applyOps(s, ops.toList(), me, now)!!

    @Test
    fun addingANewNameShowsAtOnceInItsKnownAisle() {
        val pita = s.usuals.first { it.name == "Pita" }
        val shown = apply(ShoppingOp.Add("o1", list, listOf(AddPart("c-pita-0001", "pita"), AddPart("c-new-00001", "Saffron", "1"))))
        val added = shown.items.first { it.id == "c-pita-0001" }
        assertEquals(pita.category, added.category)
        assertTrue(added.pending)
        assertEquals(me, added.addedBy)
        assertEquals("other", shown.items.first { it.id == "c-new-00001" }.category)
        assertEquals(14, shown.counts.toBuy)
        assertTrue("an added usual leaves the usuals", shown.usuals.none { it.name == "Pita" })
        // To buy stays in aisle order.
        val order = shown.items.filter { !it.checked }.map { it.category }
        val rank = s.categories.map { it.key }
        assertEquals(order.sortedBy { rank.indexOf(it) }, order)
    }

    @Test
    fun addingFollowsTheServersRules() {
        // Already there (open): no second row, a typed quantity replaces the old one.
        val already = apply(ShoppingOp.Add("o1", list, listOf(AddPart("c-banana-01", "Bananas", "2"))))
        assertEquals(1, already.items.count { it.name.lowercase() == "bananas" })
        assertEquals("2", already.items.first { it.name == "bananas" }.quantity)
        // Bought: back on the list, same id.
        val eggs = s.items.first { it.name == "Eggs" }
        val back = apply(ShoppingOp.Add("o2", list, listOf(AddPart("c-eggs-0001", "eggs"))))
        val restored = back.items.first { it.id == eggs.id }
        assertFalse(restored.checked)
        assertTrue(back.items.none { it.id == "c-eggs-0001" })
        // A repeated request (same client id) adds nothing twice.
        val twice = apply(
            ShoppingOp.Add("o3", list, listOf(AddPart("c-saffron1", "Saffron"))),
            ShoppingOp.Add("o3", list, listOf(AddPart("c-saffron1", "Saffron")))
        )
        assertEquals(1, twice.items.count { it.name == "Saffron" })
    }

    @Test
    fun tickRemoveClearAndForgetShowAtOnce() {
        val avocado = s.items.first { it.name == "Avocado" }
        val ticked = apply(ShoppingOp.Check("o1", list, avocado.id, true, "Avocado"))
        assertTrue(ticked.items.first { it.id == avocado.id }.checked)
        assertEquals(11, ticked.counts.toBuy)
        assertEquals(3, ticked.counts.bought)
        assertEquals("bought newest first", avocado.id, ticked.items.first { it.checked }.id)
        val removed = apply(ShoppingOp.Remove("o2", list, avocado.id, "Avocado"))
        assertTrue(removed.items.none { it.id == avocado.id })
        val cleared = apply(ShoppingOp.ClearBought("o3", list))
        assertEquals(0, cleared.counts.bought)
        assertEquals(12, cleared.counts.toBuy)
        val forgot = apply(ShoppingOp.Forget("o4", list, "oat MILK"))
        assertEquals(listOf("Pita"), forgot.usuals.map { it.name })
        val edited = apply(ShoppingOp.Edit("o5", list, avocado.id, mapOf("category" to "pantry", "note" to "ripe"), "Avocado"))
        val e = edited.items.first { it.id == avocado.id }
        assertEquals("pantry" to "ripe", e.category to e.note)
    }

    @Test
    fun changesOfAnotherListAreNotApplied() {
        val other = ShoppingOp.Remove("o1", "another-list", s.items[0].id, "x")
        assertSame(s, ShoppingOps.applyOps(s, listOf(other), me, now))
        assertNull(ShoppingOps.applyOps(null, listOf(other), me, now))
    }

    @Test
    fun revertingIsShowingTheServerWithoutTheChange() {
        val pending = listOf(ShoppingOp.Remove("o1", list, s.items[0].id, "x"))
        assertEquals(13, ShoppingOps.applyOps(s, pending, me, now)!!.items.size)
        assertEquals(s, ShoppingOps.applyOps(s, emptyList(), me, now))
    }

    @Test
    fun versionsDecideWhichAnswerIsKept() {
        val v20 = s
        val v4 = ShoppingFixture.screen("check")
        val other = ShoppingFixture.screen("member_invite")
        assertTrue(ShoppingOps.accepts(null, v20))
        assertFalse(ShoppingOps.accepts(v20, null))
        assertFalse("an older answer never replaces a newer one", ShoppingOps.accepts(v20, v20.copy(list = v20.list.copy(version = 19))))
        assertTrue(ShoppingOps.accepts(v20, v20))
        assertTrue(ShoppingOps.accepts(v4, v20))
        assertTrue("another list (switched)", ShoppingOps.accepts(v20, other))
    }

    @Test
    fun streamEventsReloadOnlyWhenNewer() {
        assertEquals(ShoppingOps.Reload.NO, ShoppingOps.onChanged(list, 20, s, 0))
        assertEquals(ShoppingOps.Reload.NO, ShoppingOps.onChanged(list, 18, s, 0))
        assertEquals(ShoppingOps.Reload.NOW, ShoppingOps.onChanged(list, 21, s, 0))
        assertEquals("own write on its way: its answer may carry this version", ShoppingOps.Reload.LATER, ShoppingOps.onChanged(list, 21, s, 1))
        assertEquals(ShoppingOps.Reload.NOW, ShoppingOps.onChanged("other", 1, s, 0))
        assertEquals(ShoppingOps.Reload.NOW, ShoppingOps.onChanged(list, 1, null, 0))
    }

    @Test
    fun requestsAlwaysNameTheListAndAddsCarryClientIds() {
        val add = ShoppingOps.request(ShoppingOp.Add("o1", list, listOf(AddPart("client-id-1", "חלב", "2", "3%"), AddPart("client-id-2", "Bread"))))
        assertEquals("POST", add.method)
        assertEquals("/api/shopping/items?list=$list", add.path)
        val items = add.body!!.getJSONArray("items")
        assertEquals("client-id-1", items.getJSONObject(0).getString("id"))
        assertEquals("חלב", items.getJSONObject(0).getString("name"))
        assertEquals("2", items.getJSONObject(0).getString("quantity"))
        assertEquals("3%", items.getJSONObject(0).getString("note"))
        assertFalse(items.getJSONObject(1).has("quantity"))
        val ops = listOf(
            ShoppingOp.Check("o", list, "i 1", true, "x"),
            ShoppingOp.Edit("o", list, "i1", mapOf("name" to "y"), "y"),
            ShoppingOp.Remove("o", list, "i1", "x"),
            ShoppingOp.ClearBought("o", list),
            ShoppingOp.Forget("o", list, "x")
        )
        for (op in ops) assertTrue(op.toString(), ShoppingOps.request(op).path.endsWith("?list=$list"))
        assertEquals("/api/shopping/items/i%201?list=$list", ShoppingOps.request(ops[0]).path)
        assertEquals(true, ShoppingOps.request(ops[0]).body!!.getBoolean("checked"))
        assertEquals("DELETE", ShoppingOps.request(ops[2]).method)
    }

    @Test
    fun typedTextIsSplitWithAClientIdPerItem() {
        val add = ShoppingOps.addTyped(list, "2 חלב, eggs x12\n1 kg tomatoes")!!
        assertEquals(listOf("חלב" to "2", "eggs" to "12", "tomatoes" to "1 kg"), add.parts.map { it.name to it.quantity })
        assertEquals(3, add.parts.map { it.itemId }.toSet().size)
        assertTrue(add.parts.all { it.itemId.matches(Regex("[A-Za-z0-9_-]{8,64}")) })
        assertNull(ShoppingOps.addTyped(list, " , \n"))
        assertEquals("2 eggs", ShoppingOps.addName(list, "2 eggs").parts.single().name)
    }

    @Test
    fun theQueueRoundTripsEveryKind() {
        val ops = listOf(
            ShoppingOp.Add("a", list, listOf(AddPart("c1-abcdefg", "חלב", "2", "3%"))),
            ShoppingOp.Check("b", list, "i1", false, "Milk"),
            ShoppingOp.Edit("c", list, "i1", mapOf("quantity" to "", "category" to "dairy"), "Milk"),
            ShoppingOp.Remove("d", list, "i2", "Bread"),
            ShoppingOp.ClearBought("e", list),
            ShoppingOp.Forget("f", list, "Hummus")
        )
        assertEquals(ops, ShoppingOps.decodeAll(ShoppingOps.encodeAll(ops)))
        assertEquals(listOf("b"), ShoppingOps.decodeAll("""[{"type":"nope","id":"x"},{"type":"check","id":"b","list_id":"l","item_id":"i","checked":true,"name":"n"},7]""").map { it.id })
        assertTrue(ShoppingOps.decodeAll("not json").isEmpty())
    }

    @Test
    fun onlyUnreachableAndServerErrorsWait() {
        for (c in listOf(-1, 408, 429, 500, 502, 503)) assertTrue("$c", ShoppingOps.isRetryable(c))
        for (c in listOf(200, 400, 403, 404, 409)) assertFalse("$c", ShoppingOps.isRetryable(c))
    }
}
