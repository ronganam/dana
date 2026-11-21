package io.github.ronganam.dana

import android.content.Context
import io.github.ronganam.dana.widgets.ShoppingWidgetSync
import io.github.ronganam.dana.widgets.WidgetRefreshWorker

/** Phone: home-screen widgets (widgets/). Kiosk / TV have a no-op twin in src/nopush. */
object DanaWidgets {
    /** Re-read the server and redraw every widget (debounced by WorkManager's unique work). */
    fun refresh(context: Context) = WidgetRefreshWorker.refreshNow(context.applicationContext)

    /** App start: keep the 30-minute refresh scheduled. */
    fun schedule(context: Context) {
        WidgetRefreshWorker.schedule(context.applicationContext)
        WidgetRefreshWorker.refreshNow(context.applicationContext)
    }

    /** The shopping list the phone shows changed: redraw the Shopping widget, update the watch. */
    fun shoppingChanged(context: Context) = ShoppingWidgetSync.changed(context.applicationContext)
}
