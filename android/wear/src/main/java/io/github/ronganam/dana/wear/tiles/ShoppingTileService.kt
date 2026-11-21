package io.github.ronganam.dana.wear.tiles

import androidx.wear.protolayout.LayoutElementBuilders
import androidx.wear.protolayout.ResourceBuilders
import androidx.wear.tiles.RequestBuilders
import androidx.wear.tiles.TileBuilders.Tile
import androidx.wear.tiles.TileService
import io.github.ronganam.dana.wear.shopping.Shop
import io.github.ronganam.dana.wear.shopping.ShopState
import io.github.ronganam.dana.wear.shopping.WatchShopping
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture

/**
 * Shopping: how many items are left and the first few, in aisle order (the
 * watch's own waiting ticks applied). A tap opens the Shopping screen.
 */
class ShoppingTileService : TileService() {

    override fun onTileRequest(requestParams: RequestBuilders.TileRequest): ListenableFuture<Tile> {
        WatchShopping.init(applicationContext)
        return Futures.immediateFuture(DanaTile.tile(build(WatchShopping.state.value, WatchShopping.now())))
    }

    override fun onTileResourcesRequest(requestParams: RequestBuilders.ResourcesRequest): ListenableFuture<ResourceBuilders.Resources> =
        DanaTile.resources()

    private fun build(s: ShopState, now: Long): LayoutElementBuilders.LayoutElement {
        val col = DanaTile.column()
        val open = DanaTile.launch(ACTIVITY, "open_shopping")
        val list = s.list
        val count = Shop.toBuy(s, now)
        col.addContent(DanaTile.text(if (count > 0) "SHOPPING · $count" else "SHOPPING", COLOR_ORANGE, 11f, bold = true))
        col.addContent(DanaTile.gap(6f))
        val items = Shop.items(s, now).filter { !it.ticked }.map { it.item }
        when {
            list == null -> col.addContent(DanaTile.text("Open Dana on the phone", COLOR_LIGHT_GRAY, 13f, lines = 2))
            items.isEmpty() -> col.addContent(DanaTile.text("Nothing to buy", COLOR_LIGHT_GRAY, 14f))
            else -> {
                val shown = items.take(MAX_LINES)
                shown.forEach { item ->
                    val line = if (item.quantity.isNotEmpty()) "${item.name}  ${item.quantity}" else item.name
                    col.addContent(DanaTile.text(line, COLOR_WHITE, 15f))
                    col.addContent(DanaTile.gap(2f))
                }
                val more = count - shown.size
                if (more > 0) {
                    col.addContent(DanaTile.gap(2f))
                    col.addContent(DanaTile.text("+$more more", COLOR_GRAY, 12f))
                }
            }
        }
        if (list?.canEdit == false) {
            col.addContent(DanaTile.gap(4f))
            col.addContent(DanaTile.text("view only", COLOR_GRAY, 11f))
        }
        return DanaTile.screen(col.build(), open)
    }

    private companion object {
        const val ACTIVITY = "io.github.ronganam.dana.wear.shopping.ShoppingActivity"
        const val MAX_LINES = 4
    }
}
