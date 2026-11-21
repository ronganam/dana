package app.ganam.assistant.wear.cards

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
import app.ganam.assistant.wear.MainActivity
import app.ganam.assistant.wear.complications.InboxComplicationService
import app.ganam.assistant.wear.complications.ShoppingComplicationService
import app.ganam.assistant.wear.shopping.WatchShopping
import app.ganam.assistant.wear.tiles.ShoppingTileService
import app.ganam.assistant.wear.tiles.TodayTileService
import app.ganam.assistant.wear.tiles.WaitingTileService
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService
import org.json.JSONArray
import org.json.JSONObject

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
        val json = runCatching { JSONObject(String(event.data)) }.getOrElse {
            Log.w(TAG, "Bad payload on ${event.path}", it); return
        }
        when (event.path) {
            PATH_CARD -> showCard(this, json)
            PATH_CLEAR -> notificationManager(this).cancel(notificationId(json.optString("kind")))
            PATH_SNAPSHOT -> DanaSnapshot.save(this, json)
        }
    }

    companion object {
        private const val TAG = "DanaCards"
        const val PATH_CARD = "/wear/dana_card"
        const val PATH_CLEAR = "/wear/dana_card_clear"
        const val PATH_SNAPSHOT = "/wear/dana_snapshot"

        private const val CHANNEL_CALLER = "dana_caller"
        private const val CHANNEL_CARDS = "dana_cards"

        fun notificationId(kind: String) = 7100 + (kind.hashCode() and 0xff)

        private fun notificationManager(context: Context) =
            context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        private fun ensureChannels(nm: NotificationManager) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_CALLER, "Who's calling", NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "What's going on with the person calling you"
                    enableVibration(false) // the ringing phone already buzzes the watch
                }
            )
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_CARDS, "Briefing & meetings", NotificationManager.IMPORTANCE_DEFAULT).apply {
                    description = "Morning briefing and who you're about to meet"
                }
            )
        }

        fun showCard(context: Context, json: JSONObject) {
            val kind = json.optString("kind")
            val title = json.optString("title")
            if (kind.isBlank() || title.isBlank()) return
            val subtitle = json.optString("subtitle")
            val lines = json.optJSONArray("lines")?.let { a -> (0 until a.length()).map { a.optString(it) } }.orEmpty()
                .filter { it.isNotBlank() }

            val nm = notificationManager(context)
            ensureChannels(nm)
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

/** The phone's last widget snapshot, kept for the tiles and complications. */
object DanaSnapshot {
    private const val PREFS = "GanamWearPrefs"
    private const val KEY = "dana_snapshot"

    fun load(context: Context): JSONObject = runCatching {
        JSONObject(context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, "{}") ?: "{}")
    }.getOrDefault(JSONObject())

    /** A new snapshot from the phone: stored, then the shopping list and the tiles / complications follow it. */
    fun save(context: Context, o: JSONObject) {
        store(context, o)
        WatchShopping.onSnapshot(context, o)
        refreshSurfaces(context)
    }

    private fun store(context: Context, o: JSONObject) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, o.toString()).apply()
    }

    /** Drop one suggestion locally right after Accept / Dismiss, before the phone's next snapshot. */
    fun removeSuggestion(context: Context, id: String) {
        val o = load(context)
        val list = o.optJSONArray("suggestions") ?: return
        val kept = JSONArray()
        for (i in 0 until list.length()) list.optJSONObject(i)?.takeIf { it.optString("id") != id }?.let { kept.put(it) }
        if (kept.length() == list.length()) return
        o.put("suggestions", kept).put("suggestion_count", (o.optInt("suggestion_count") - 1).coerceAtLeast(0))
        store(context, o)
        refreshSurfaces(context)
    }

    fun refreshSurfaces(context: Context) {
        runCatching {
            val updater = TileService.getUpdater(context)
            updater.requestUpdate(TodayTileService::class.java)
            updater.requestUpdate(WaitingTileService::class.java)
            updater.requestUpdate(ShoppingTileService::class.java)
        }.onFailure { Log.d("DanaSnapshot", "Tile update request failed: ${it.message}") }
        for (cls in listOf(InboxComplicationService::class.java, ShoppingComplicationService::class.java)) {
            runCatching {
                ComplicationDataSourceUpdateRequester.create(context, ComponentName(context, cls)).requestUpdateAll()
            }.onFailure { Log.d("DanaSnapshot", "Complication update request failed: ${it.message}") }
        }
    }
}
