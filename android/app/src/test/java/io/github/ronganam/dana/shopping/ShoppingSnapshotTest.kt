package io.github.ronganam.dana.shopping

import io.github.ronganam.dana.data.shopping.ShoppingOp
import io.github.ronganam.dana.data.shopping.ShoppingOps
import io.github.ronganam.dana.data.shopping.ShoppingSnapshot
import io.github.ronganam.dana.data.shopping.Who
import io.github.ronganam.dana.protocol.DanaSnapshot
import io.github.ronganam.dana.protocol.SnapshotTask
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The shopping fields of the widget snapshot, which the watch gets too. */
class ShoppingSnapshotTest {
    private fun fields(screen: io.github.ronganam.dana.data.shopping.ShoppingScreen?) = ShoppingSnapshot.apply(DanaSnapshot(), screen)

    @Test
    fun itemsToBuyInTheServersOrderWithTheirAisles() {
        val f = fields(ShoppingFixture.screen("owner_shared"))
        assertEquals(12, f.shoppingCount)
        assertEquals(ShoppingFixture.list, f.shoppingListId)
        assertEquals(20L, f.shoppingVersion)
        assertTrue(f.shoppingCanEdit)
        val items = f.shopping!!
        assertEquals(12, items.size)
        assertEquals("עגבניות שרי", items[0].name)
        assertEquals("Fruit & vegetables", items[0].aisle)
        assertEquals("produce", items[0].aisleKey)
        assertEquals("1 kg", items[1].quantity)
        assertEquals("Other", items[11].aisle)
        // On the wire: the keys the watch reads.
        val wire = Json.parseToJsonElement(f.encode()).jsonObject
        assertEquals(setOf("id", "name", "quantity", "aisle", "aisle_key"), wire["shopping"]!!.jsonArray[0].jsonObject.keys)
    }

    @Test
    fun aTickOnThePhoneIsInTheNextSnapshot() {
        val s = ShoppingFixture.screen("owner_shared")
        val shown = ShoppingOps.applyOps(s, listOf(ShoppingOp.Check("o", s.list.id, s.items[0].id, true, "x")), Who("me", "Me"), "2026-11-16T15:00:00Z")
        val f = fields(shown)
        assertEquals(11, f.shoppingCount)
        assertFalse(f.shopping!!.any { it.id == s.items[0].id })
    }

    @Test
    fun aReadOnlyListCantBeTickedAndNoListMeansNothing() {
        assertFalse(fields(ShoppingFixture.screen("viewer")).shoppingCanEdit)
        val none = fields(null)
        assertEquals(0, none.shoppingCount)
        assertEquals(0, none.shopping!!.size)
        assertEquals("", none.shoppingListId)
    }

    @Test
    fun applyReplacesOnlyTheShoppingFields() {
        val snapshot = DanaSnapshot(tasks = listOf(SnapshotTask("t1", "Call Gal")), shoppingListId = "old", shoppingVersion = 1)
        val merged = ShoppingSnapshot.apply(snapshot, null)
        assertEquals(snapshot.tasks, merged.tasks)
        assertEquals("a stale list id goes", "", merged.shoppingListId)
        assertEquals(0L, merged.shoppingVersion)
        assertEquals(0, merged.shoppingCount)
    }

    @Test
    fun aLongListIsCappedButCountedInFull() {
        val s = ShoppingFixture.screen("owner_shared")
        val many = s.copy(items = (1..80).map { s.items[0].copy(id = "i$it", name = "Item $it") })
        val f = fields(many)
        assertEquals(80, f.shoppingCount)
        assertEquals(ShoppingSnapshot.MAX_ITEMS, f.shopping!!.size)
        assertEquals("the watch gets at most 40", 40, f.forWatch().shopping!!.size)
    }

    @Test
    fun aSnapshotWithMissingFieldsReadsWithDefaults() {
        val read = DanaSnapshot.decode("""{"suggestion_count": 2, "unknown": true}""")!!
        assertEquals(2, read.suggestionCount)
        assertNull("no shopping part (an older phone)", read.shopping)
        assertTrue(read.shoppingCanEdit)
        assertEquals(emptyList<Any>(), read.tasks)
        assertNull(DanaSnapshot.decode("not json"))
    }
}
