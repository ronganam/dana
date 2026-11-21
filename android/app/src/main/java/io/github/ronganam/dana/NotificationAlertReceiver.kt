package io.github.ronganam.dana

import io.github.ronganam.dana.data.network.text
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat

/**
 * The server's `alert` push event (see [io.github.ronganam.dana.push.PhoneEvents])
 * when a phone notification is urgent (important + time-sensitive) or comes
 * from an app with the "Alert me" rule, or a person saying it's urgent. Opens
 * [NotificationAlertActivity] full screen. The server sends it again (with a
 * higher `attempt`) at 3 and 10 minutes while the notification wasn't seen.
 *
 * Do Not Disturb wins: then it's only a high-priority notification (no full
 * screen, the system keeps it quiet).
 */
object NotificationAlertReceiver {

    fun show(context: Context, alert: Alert) {
        synchronized(recent) {
            val now = System.currentTimeMillis()
            recent.entries.removeAll { now - it.value > 10 * 60_000 }
            // A repeated event has the same attempt; a re-nudge
            // (the server's "still not seen") carries the next one.
            val dedupe = "${alert.id}#${alert.attempt}"
            if (recent.containsKey(dedupe)) return
            recent[dedupe] = now
        }
        Log.i(TAG, "Alert for ${alert.app}: ${alert.title}")

        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "Urgent notifications", NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Phone notifications Dana thinks you shouldn't miss"
                    enableVibration(true)
                    vibrationPattern = longArrayOf(0, 600, 150, 600, 150, 600, 900, 600, 150, 600)
                }
            )
        }
        val dnd = nm.currentInterruptionFilter.let {
            it != NotificationManager.INTERRUPTION_FILTER_ALL && it != NotificationManager.INTERRUPTION_FILTER_UNKNOWN
        }

        val screen = alert.toIntent(context).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pending = PendingIntent.getActivity(
            context, alert.notificationId, screen,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_QUIET, "Urgent notifications (screen open)", NotificationManager.IMPORTANCE_LOW).apply {
                    description = "Kept while the alert screen is showing (the screen rings itself)"
                }
            )
        }
        // The alert screen opens by itself and rings + vibrates on its own, so
        // its notification only stays in the shade: no heads-up over the screen.
        val popping = !dnd && android.provider.Settings.canDrawOverlays(context)
        val builder = build(context, alert, pending, quiet = popping)
        if (!dnd && !popping) builder.setFullScreenIntent(pending, true)
        try {
            nm.notify(alert.notificationId, builder.build())
        } catch (e: SecurityException) {
            Log.w(TAG, "No notification permission", e)
        }
        if (!dnd) {
            try {
                context.startActivity(screen)
            } catch (e: Exception) {
                // Background start blocked: the full-screen notification covers it.
                if (popping) try {
                    nm.notify(alert.notificationId, build(context, alert, pending, quiet = false).setFullScreenIntent(pending, true).build())
                } catch (se: SecurityException) { android.util.Log.w("NotificationAlert", "No notification permission for the alert", se) }
            }
        }
    }

    private fun build(context: Context, alert: Alert, pending: PendingIntent, quiet: Boolean): NotificationCompat.Builder =
        NotificationCompat.Builder(context, if (quiet) CHANNEL_QUIET else CHANNEL)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle("⚡ ${if (alert.attempt > 1) "Still waiting · " else ""}${alert.app}${alert.title?.let { ": $it" } ?: ""}")
            .setContentText(alert.text ?: "Needs you now")
            .setStyle(NotificationCompat.BigTextStyle().bigText(alert.text ?: ""))
            .setPriority(if (quiet) NotificationCompat.PRIORITY_LOW else NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setContentIntent(pending)
            .setAutoCancel(true)

    /** What the server sends. */
    data class Alert(
        val id: String, val key: String?, val packageName: String?, val app: String,
        val title: String?, val text: String?, val priority: String, val emoji: String,
        /** 1 = first alert; 2+ = re-nudges because it wasn't seen yet. */
        val attempt: Int = 1
    ) {
        val notificationId: Int get() = ("alert:$id").hashCode()

        fun toIntent(context: Context) = Intent(context, NotificationAlertActivity::class.java).apply {
            putExtra(EXTRA_ID, id); putExtra(EXTRA_KEY, key); putExtra(EXTRA_PACKAGE, packageName)
            putExtra(EXTRA_APP, app); putExtra(EXTRA_TITLE, title); putExtra(EXTRA_TEXT, text)
            putExtra(EXTRA_PRIORITY, priority); putExtra(EXTRA_EMOJI, emoji)
            putExtra(EXTRA_ATTEMPT, attempt.toString())
        }

        companion object {
            /** From the server's `alert` event. */
            fun from(json: kotlinx.serialization.json.JsonObject): Alert? = build { k -> json.text(k)?.takeIf { it.isNotBlank() } }

            /** From our own activity intent. */
            fun from(intent: Intent): Alert? = build { k -> intent.getStringExtra(k)?.takeIf { it.isNotBlank() } }

            private fun build(get: (String) -> String?): Alert? {
                val id = get(EXTRA_ID) ?: return null
                return Alert(
                    id = id, key = get(EXTRA_KEY), packageName = get(EXTRA_PACKAGE),
                    app = get(EXTRA_APP) ?: get(EXTRA_PACKAGE) ?: "Notification",
                    title = get(EXTRA_TITLE), text = get(EXTRA_TEXT),
                    priority = get(EXTRA_PRIORITY) ?: "urgent", emoji = get(EXTRA_EMOJI) ?: "🔔",
                    attempt = get(EXTRA_ATTEMPT)?.toIntOrNull()?.coerceAtLeast(1) ?: 1
                )
            }
        }
    }

    const val CHANNEL = "urgent_notifications"
    const val CHANNEL_QUIET = "urgent_notifications_quiet"
    private const val TAG = "NotificationAlert"
    const val EXTRA_ID = "notification_id"
    const val EXTRA_KEY = "key"
    const val EXTRA_PACKAGE = "package"
    const val EXTRA_APP = "app"
    const val EXTRA_TITLE = "title"
    const val EXTRA_TEXT = "text"
    const val EXTRA_PRIORITY = "priority"
    const val EXTRA_EMOJI = "emoji"
    const val EXTRA_ATTEMPT = "attempt"
    private val recent = HashMap<String, Long>()
}
