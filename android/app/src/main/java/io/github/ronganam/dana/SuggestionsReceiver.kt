package io.github.ronganam.dana

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.edit
import io.github.ronganam.dana.R

/**
 * The server's `suggestions` push event (see [io.github.ronganam.dana.push.PhoneEvents])
 * when a call that just ended, or a fresh notification, produced suggestions.
 * Posts a notification that stays until they're handled and, when the phone
 * isn't busy, opens
 * [SuggestionsPopupActivity] right away.
 *
 * Opening a screen from the background needs "Display over other apps"
 * ([canPopUp]); without it (or during a call / Do Not Disturb) only the
 * notification shows, with a full-screen intent for the lock screen.
 */
object SuggestionsReceiver {
    const val CHANNEL = "suggestion_review"
    const val CHANNEL_QUIET = "suggestion_review_quiet"
    private const val TAG = "SuggestionsPopup"

    /** Notification (kept until handled) + the pop-up when the phone is free. */
    fun show(context: Context, ids: List<String>, heading: String?, sourceKey: String, callId: String? = null) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, context.getString(R.string.channel_suggestions), NotificationManager.IMPORTANCE_HIGH).apply {
                description = context.getString(R.string.channel_suggestions_description)
            }
        )
        val screen = SuggestionsPopupActivity.intent(context, ids, heading, callId, notificationKey = sourceKey)
        val pending = PendingIntent.getActivity(
            context, sourceKey.hashCode(), screen,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_QUIET, context.getString(R.string.channel_suggestions_quiet), NotificationManager.IMPORTANCE_LOW).apply {
                description = context.getString(R.string.channel_suggestions_quiet_description)
            }
        )
        val n = ids.size
        val busy = inCall(context) || dnd(nm)
        // The screen opens by itself: the notification only keeps them from
        // being forgotten, so it arrives quietly (no heads-up over the screen).
        val popping = !busy && canPopUp(context)
        val builder = NotificationCompat.Builder(context, if (popping) CHANNEL_QUIET else CHANNEL)
            .setSmallIcon(android.R.drawable.ic_menu_agenda)
            .setContentTitle(if (n == 1) "1 suggestion to review" else "$n suggestions to review")
            .setContentText(heading ?: "Tap to review")
            .setPriority(if (popping) NotificationCompat.PRIORITY_LOW else NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setContentIntent(pending)
            .addAction(android.R.drawable.ic_menu_view, "Review", pending)
            .setAutoCancel(false)
        if (!busy && !popping) builder.setFullScreenIntent(pending, true)
        remember(context, sourceKey.hashCode(), ids)
        try {
            nm.notify(sourceKey.hashCode(), builder.build())
        } catch (e: SecurityException) {
            Log.w(TAG, "No notification permission", e)
        }
        if (popping) {
            try {
                context.startActivity(screen)
            } catch (e: Exception) {
                Log.w(TAG, "Couldn't open the pop-up; the notification stays", e)
            }
        }
    }

    private const val PREFS = "suggestion_notifications"

    private fun remember(context: Context, notifId: Int, ids: List<String>) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit { putStringSet(notifId.toString(), ids.toSet()) }
    }

    /**
     * Drop the review notifications whose suggestions are all handled (accepted or
     * dismissed in the Inbox, the pop-up, the web...). [pendingIds] = still pending.
     */
    fun reconcile(context: Context, pendingIds: Set<String>) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        prefs.edit {
            for ((k, v) in prefs.all) {
                @Suppress("UNCHECKED_CAST")
                val ids = (v as? Set<String>) ?: continue
                if (ids.none { it in pendingIds }) {
                    k.toIntOrNull()?.let { nm.cancel(it) }
                    remove(k)
                }
            }
        }
    }

    /** "Display over other apps": lets Dana open a screen from the background. */
    fun canPopUp(context: Context): Boolean = Settings.canDrawOverlays(context)

    /** Android 14+: "Full screen notifications" (screen on over the lock screen). */
    fun canUseFullScreen(context: Context): Boolean =
        if (Build.VERSION.SDK_INT >= 34) (context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).canUseFullScreenIntent()
        else true

    /** On a phone or VoIP call (audio mode needs no permission, unlike the call state). */
    private fun inCall(context: Context): Boolean {
        val mode = (context.getSystemService(Context.AUDIO_SERVICE) as android.media.AudioManager).mode
        return mode == android.media.AudioManager.MODE_IN_CALL || mode == android.media.AudioManager.MODE_IN_COMMUNICATION
    }

    private fun dnd(nm: NotificationManager) = nm.currentInterruptionFilter.let {
        it != NotificationManager.INTERRUPTION_FILTER_ALL && it != NotificationManager.INTERRUPTION_FILTER_UNKNOWN
    }
}
