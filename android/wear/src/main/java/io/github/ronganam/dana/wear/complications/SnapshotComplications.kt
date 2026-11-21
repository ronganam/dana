package io.github.ronganam.dana.wear.complications

import android.app.Activity
import android.app.PendingIntent
import android.content.Intent
import androidx.wear.watchface.complications.data.ComplicationData
import androidx.wear.watchface.complications.data.ComplicationType
import androidx.wear.watchface.complications.data.NoDataComplicationData
import androidx.wear.watchface.complications.data.PlainComplicationText
import androidx.wear.watchface.complications.data.ShortTextComplicationData
import androidx.wear.watchface.complications.datasource.ComplicationRequest
import androidx.wear.watchface.complications.datasource.SuspendingComplicationDataSourceService
import io.github.ronganam.dana.wear.MainActivity
import androidx.annotation.StringRes
import io.github.ronganam.dana.protocol.DanaSnapshot
import io.github.ronganam.dana.wear.R
import io.github.ronganam.dana.wear.cards.SnapshotStore
import io.github.ronganam.dana.wear.shopping.ShoppingActivity
import io.github.ronganam.dana.wear.shopping.WatchShopping

/** A number + label complication from the phone's snapshot (refreshed when a new snapshot arrives). */
abstract class SnapshotCountComplication(
    /** The count in the snapshot; null when the phone hasn't sent it. */
    private val count: (DanaSnapshot) -> Int?,
    /** A stable id for the tap's PendingIntent. */
    private val key: String,
    @param:StringRes private val labelRes: Int,
    private val preview: String,
    private val opens: Class<out Activity> = MainActivity::class.java
) : SuspendingComplicationDataSourceService() {

    override suspend fun onComplicationRequest(request: ComplicationRequest): ComplicationData {
        if (request.complicationType != ComplicationType.SHORT_TEXT) return NoDataComplicationData()
        val shown = SnapshotStore.load(applicationContext)?.let(count)
        return build(if (shown != null) value(shown) else "--")
    }

    /** What the complication says for the snapshot's [count]. */
    protected open fun value(count: Int): String = count.toString()

    override fun getPreviewData(type: ComplicationType): ComplicationData? =
        if (type == ComplicationType.SHORT_TEXT) build(preview) else null

    private fun build(value: String): ShortTextComplicationData {
        val label = getString(labelRes)
        val tap = PendingIntent.getActivity(
            applicationContext, key.hashCode(),
            Intent(applicationContext, opens).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return ShortTextComplicationData.Builder(
            text = PlainComplicationText.Builder(value).build(),
            contentDescription = PlainComplicationText.Builder("$label: $value").build()
        ).setTitle(PlainComplicationText.Builder(label).build()).setTapAction(tap).build()
    }
}

/** Inbox notifications that need you (urgent + important). */
class InboxComplicationService : SnapshotCountComplication({ it.inboxNeeds }, "inbox_needs", R.string.complication_inbox, "3")

/** Shopping-list items not ticked yet (the watch's own waiting ticks included); a tap opens the Shopping screen. */
class ShoppingComplicationService : SnapshotCountComplication({ it.shoppingCount }, "shopping_count", R.string.complication_shop, "7", ShoppingActivity::class.java) {
    override fun value(count: Int): String = WatchShopping.toBuy(applicationContext).toString()
}
