package app.ganam.assistant.data.shopping

import org.json.JSONArray
import org.json.JSONObject

/**
 * The shopping part of the widget snapshot (pref `widget_snapshot`), which is
 * also what the watch gets (`/wear/dana_snapshot`, `WearCards.sendSnapshot`):
 *
 *   shopping_count     items to buy
 *   shopping           [{id, name, quantity, aisle, aisle_key}] to buy, in the server's order (aisle by aisle)
 *   shopping_list_id   the list they are on (send it back with a tick)
 *   shopping_version   the list's version the items come from
 *   shopping_can_edit  false on a list shared read-only (no ticking)
 *
 * Built from what the phone shows (the server's last answer with the changes
 * still on their way), so a tick on the phone reaches the watch at once.
 */
object ShoppingSnapshot {
    const val MAX_ITEMS = 60
    val KEYS = listOf("shopping_count", "shopping", "shopping_list_id", "shopping_version", "shopping_can_edit")

    /** The fields for [screen] (null: no list known yet → an empty list). */
    fun fields(screen: ShoppingScreen?): JSONObject {
        val o = JSONObject()
        if (screen == null) return o.put("shopping_count", 0).put("shopping", JSONArray())
        val lines = ShoppingView.toBuyLines(screen)
        return o.put("shopping_count", lines.size)
            .put("shopping", JSONArray(lines.take(MAX_ITEMS).map {
                JSONObject().put("id", it.id).put("name", it.name).put("quantity", it.quantity).put("aisle", it.aisle).put("aisle_key", it.aisleKey)
            }))
            .put("shopping_list_id", screen.list.id)
            .put("shopping_version", screen.list.version)
            .put("shopping_can_edit", screen.list.canEdit)
    }

    /** [snapshot] with its shopping fields replaced by [fields]. */
    fun merge(snapshot: JSONObject, fields: JSONObject): JSONObject {
        KEYS.forEach { snapshot.remove(it) }
        fields.keys().forEach { snapshot.put(it, fields.get(it)) }
        return snapshot
    }
}
