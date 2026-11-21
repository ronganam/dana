package io.github.ronganam.dana.wear.cards

import io.github.ronganam.dana.protocol.WearPaths
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.wear.tiles.TileService
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceUpdateRequester
import io.github.ronganam.dana.wear.MainActivity
import io.github.ronganam.dana.wear.complications.InboxComplicationService
import io.github.ronganam.dana.wear.complications.ShoppingComplicationService
import io.github.ronganam.dana.wear.shopping.WatchShopping
import io.github.ronganam.dana.wear.tiles.ShoppingTileService
import io.github.ronganam.dana.wear.tiles.TodayTileService
import io.github.ronganam.dana.wear.tiles.WaitingTileService
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService
import io.github.ronganam.dana.protocol.DanaSnapshot
import io.github.ronganam.dana.protocol.WearCard
import io.github.ronganam.dana.wear.R

/**
 * Phone → watch Dana cards (see the phone's `data/wear/WearCards.kt`):
 *
 *   /wear/dana_card        {kind, title, subtitle, lines[], ref}  → one notification per kind
 *   /wear/dana_card_clear  {kind}                                → removes it (call ended)
 *   /wear/dana_snapshot    trimmed widget snapshot               → Today / Waiting / Shopping tiles, complications, the Shopping screen
 */
class DanaCardListenerService : WearableListenerService() {

    override fun onMessageReceived(event: MessageEvent) {
        super.onMessageReceived(event)
        val text = String(event.data)
        when (event.path) {
            WearPaths.CARD -> WearCard.decode(text)?.let { showCard(this, it) } ?: Log.w(TAG, "Bad payload on ${event.path}")
            WearPaths.CARD_CLEAR -> WearCard.decode(text)?.let { notificationManager(this).cancel(notificationId(it.kind)) }
                ?: Log.w(TAG, "Bad payload on ${event.path}")
            WearPaths.SNAPSHOT -> DanaSnapshot.decode(text)?.let { SnapshotStore.save(this, it) } ?: Log.w(TAG, "Bad payload on ${event.path}")
        }
    }

    companion object {
        private const val TAG = "DanaCards"

        private const val CHANNEL_CALLER = "dana_caller"
        private const val CHANNEL_CARDS = "dana_cards"

        fun notificationId(kind: String) = 7100 + (kind.hashCode() and 0xff)

        private fun notificationManager(context: Context) =
            context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        private fun ensureChannels(context: Context, nm: NotificationManager) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_CALLER, context.getString(R.string.channel_caller), NotificationManager.IMPORTANCE_HIGH).apply {
                    description = context.getString(R.string.channel_caller_description)
                    enableVibration(false) // the ringing phone already buzzes the watch
                }
            )
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_CARDS, context.getString(R.string.channel_cards), NotificationManager.IMPORTANCE_DEFAULT).apply {
                    description = context.getString(R.string.channel_cards_description)
                }
            )
        }

        fun showCard(context: Context, card: WearCard) {
            val kind = card.kind
            val title = card.title
            if (kind.isBlank() || title.isBlank()) return
            val subtitle = card.subtitle
            val lines = card.lines.filter { it.isNotBlank() }

            val nm = notificationManager(context)
            ensureChannels(context, nm)
            val open = PendingIntent.getActivity(
                context, notificationId(kind),
                Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            val style = NotificationCompat.InboxStyle().setBigContentTitle(title)
            if (subtitle.isNotBlank()) style.setSummaryText(subtitle)
            lines.forEach { style.addLine(it) }

            val caller = kind == "caller"
            val builder = NotificationCompat.Builder(context, if (caller) CHANNEL_CALLER else CHANNEL_CARDS)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(title)
                .setContentText(subtitle.ifBlank { lines.firstOrNull().orEmpty() })
                .setStyle(style)
                .setContentIntent(open)
                .setAutoCancel(true)
                .setOnlyAlertOnce(true)
                .setCategory(if (caller) NotificationCompat.CATEGORY_CALL else NotificationCompat.CATEGORY_REMINDER)
                .setPriority(if (caller) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
            // A caller card the phone never clears (lost message) shouldn't linger.
            if (caller) builder.setTimeoutAfter(10 * 60 * 1000L)
            if (kind == "meeting") builder.setTimeoutAfter(30 * 60 * 1000L)
            try {
                nm.notify(notificationId(kind), builder.build())
            } catch (e: SecurityException) {
                Log.w(TAG, "Notification permission missing", e)
            }
        }
    }
}

/** The phone's last widget snapshot ([DanaSnapshot]), kept for the tiles and complications. */
object SnapshotStore {
    private const val PREFS = "DanaWearPrefs"
    private const val KEY = "dana_snapshot"

    /** The last snapshot; null before the phone sent one. */
    fun load(context: Context): DanaSnapshot? =
        DanaSnapshot.decode(context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null))

    /** A new snapshot from the phone: stored, then the shopping list and the tiles / complications follow it. */
    fun save(context: Context, snapshot: DanaSnapshot) {
        store(context, snapshot)
        WatchShopping.onSnapshot(context, snapshot)
        refreshSurfaces(context)
    }

    private fun store(context: Context, snapshot: DanaSnapshot) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, snapshot.encode()).apply()
    }

    /** Drop one suggestion locally right after Accept / Dismiss, before the phone's next snapshot. */
    fun removeSuggestion(context: Context, id: String) {
        val o = load(context) ?: return
        val kept = o.suggestions.filter { it.id != id }
        if (kept.size == o.suggestions.size) return
        store(context, o.copy(suggestions = kept, suggestionCount = (o.suggestionCount - 1).coerceAtLeast(0)))
        refreshSurfaces(context)
    }

    fun refreshSurfaces(context: Context) {
        runCatching {
            val updater = TileService.getUpdater(context)
            updater.requestUpdate(TodayTileService::class.java)
            updater.requestUpdate(WaitingTileService::class.java)
            updater.requestUpdate(ShoppingTileService::class.java)
        }.onFailure { Log.d("SnapshotStore", "Tile update request failed: ${it.message}") }
        for (cls in listOf(InboxComplicationService::class.java, ShoppingComplicationService::class.java)) {
            runCatching {
                ComplicationDataSourceUpdateRequester.create(context, ComponentName(context, cls)).requestUpdateAll()
            }.onFailure { Log.d("SnapshotStore", "Complication update request failed: ${it.message}") }
        }
    }
}
