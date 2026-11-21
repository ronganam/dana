package app.ganam.assistant.shopping

import app.ganam.assistant.data.shopping.ShoppingOp
import app.ganam.assistant.data.shopping.ShoppingOps
import app.ganam.assistant.data.shopping.ShoppingSnapshot
import app.ganam.assistant.data.shopping.Who
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The shopping fields of the widget snapshot, which the watch gets too. */
class ShoppingSnapshotTest {
    @Test
    fun itemsToBuyInTheServersOrderWithTheirAisles() {
        val f = ShoppingSnapshot.fields(ShoppingFixture.screen("owner_shared"))
        assertEquals(12, f.getInt("shopping_count"))
        assertEquals("a142b3bd0785", f.getString("shopping_list_id"))
        assertEquals(20L, f.getLong("shopping_version"))
        assertTrue(f.getBoolean("shopping_can_edit"))
        val items = f.getJSONArray("shopping")
        assertEquals(12, items.length())
        val first = items.getJSONObject(0)
        assertEquals("עגבניות שרי", first.getString("name"))
        assertEquals("Fruit & vegetables", first.getString("aisle"))
        assertEquals("produce", first.getString("aisle_key"))
        assertEquals(setOf("id", "name", "quantity", "aisle", "aisle_key"), first.keys().asSequence().toSet())
        assertEquals("1 kg", items.getJSONObject(1).getString("quantity"))
        assertEquals("Other", items.getJSONObject(11).getString("aisle"))
    }

    @Test
    fun aTickOnThePhoneIsInTheNextSnapshot() {
        val s = ShoppingFixture.screen("owner_shared")
        val shown = ShoppingOps.applyOps(s, listOf(ShoppingOp.Check("o", s.list.id, s.items[0].id, true, "x")), Who("me", "Me"), "2026-11-16T15:00:00Z")
        val f = ShoppingSnapshot.fields(shown)
        assertEquals(11, f.getInt("shopping_count"))
        assertFalse((0 until 11).any { f.getJSONArray("shopping").getJSONObject(it).getString("id") == s.items[0].id })
    }

    @Test
    fun aReadOnlyListCantBeTickedAndNoListMeansNothing() {
        assertFalse(ShoppingSnapshot.fields(ShoppingFixture.screen("viewer")).getBoolean("shopping_can_edit"))
        val none = ShoppingSnapshot.fields(null)
        assertEquals(0, none.getInt("shopping_count"))
        assertEquals(0, none.getJSONArray("shopping").length())
        assertFalse(none.has("shopping_list_id"))
    }

    @Test
    fun mergeReplacesOnlyTheShoppingFields() {
        val snapshot = JSONObject().put("tasks_count", 3).put("shopping_list_id", "old").put("shopping_version", 1)
        ShoppingSnapshot.merge(snapshot, ShoppingSnapshot.fields(null))
        assertEquals(3, snapshot.getInt("tasks_count"))
        assertFalse("a stale list id goes", snapshot.has("shopping_list_id"))
        assertEquals(0, snapshot.getInt("shopping_count"))
    }

    @Test
    fun aLongListIsCappedButCountedInFull() {
        val s = ShoppingFixture.screen("owner_shared")
        val many = s.copy(items = (1..80).map { s.items[0].copy(id = "i$it", name = "Item $it") })
        val f = ShoppingSnapshot.fields(many)
        assertEquals(80, f.getInt("shopping_count"))
        assertEquals(ShoppingSnapshot.MAX_ITEMS, f.getJSONArray("shopping").length())
    }
}
