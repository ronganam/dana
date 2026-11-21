package io.github.ronganam.dana.data.shopping

import io.github.ronganam.dana.protocol.DanaSnapshot
import io.github.ronganam.dana.protocol.ShopItem

/**
 * The shopping part of the widget snapshot ([DanaSnapshot], pref
 * `widget_snapshot`), which is also what the watch gets (`/wear/dana_snapshot`,
 * `WearCards.sendSnapshot`): the items to buy in the server's order (aisle by
 * aisle, at most [MAX_ITEMS]), how many in all, the list they are on (sent back
 * with a tick), its version and whether it can be changed (false on a list
 * shared read-only).
 *
 * Built from what the phone shows (the server's last answer with the changes
 * still on their way), so a tick on the phone reaches the watch at once.
 */
object ShoppingSnapshot {
    const val MAX_ITEMS = 60

    /** [snapshot] with its shopping part from [screen] (null: no list known yet → an empty list). */
    fun apply(snapshot: DanaSnapshot, screen: ShoppingScreen?): DanaSnapshot {
        if (screen == null) {
            return snapshot.copy(shoppingCount = 0, shopping = emptyList(), shoppingListId = "", shoppingVersion = 0, shoppingCanEdit = true)
        }
        val lines = ShoppingView.toBuyLines(screen)
        return snapshot.copy(
            shoppingCount = lines.size,
            shopping = lines.take(MAX_ITEMS).map { ShopItem(it.id, it.name, it.quantity, it.aisle, it.aisleKey) },
            shoppingListId = screen.list.id,
            shoppingVersion = screen.list.version,
            shoppingCanEdit = screen.list.canEdit
        )
    }
}
