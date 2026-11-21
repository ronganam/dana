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
import io.github.ronganam.dana.wear.cards.DanaSnapshot
import io.github.ronganam.dana.wear.shopping.ShoppingActivity
import io.github.ronganam.dana.wear.shopping.WatchShopping

/** A number + label complication from the phone's snapshot (refreshed when a new snapshot arrives). */
abstract class SnapshotCountComplication(
    private val key: String,
    private val label: String,
    private val preview: String,
    private val opens: Class<out Activity> = MainActivity::class.java
) : SuspendingComplicationDataSourceService() {

    override suspend fun onComplicationRequest(request: ComplicationRequest): ComplicationData {
        if (request.complicationType != ComplicationType.SHORT_TEXT) return NoDataComplicationData()
        val s = DanaSnapshot.load(applicationContext)
        return build(if (s.has(key)) value(s.optInt(key)) else "--")
    }

    /** What the complication says for the snapshot's [count]. */
    protected open fun value(count: Int): String = count.toString()

    override fun getPreviewData(type: ComplicationType): ComplicationData? =
        if (type == ComplicationType.SHORT_TEXT) build(preview) else null

    private fun build(value: String): ShortTextComplicationData {
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
class InboxComplicationService : SnapshotCountComplication("inbox_needs", "Inbox", "3")

/** Shopping-list items not ticked yet (the watch's own waiting ticks included); a tap opens the Shopping screen. */
class ShoppingComplicationService : SnapshotCountComplication("shopping_count", "Shop", "7", ShoppingActivity::class.java) {
    override fun value(count: Int): String = WatchShopping.toBuy(applicationContext).toString()
}
