package io.github.ronganam.dana.push

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.edit
import io.github.ronganam.dana.EXTRA_OPEN_SCREEN
import io.github.ronganam.dana.MainActivity
import io.github.ronganam.dana.R
import io.github.ronganam.dana.data.network.ServerConfig

/**
 * This phone signs in with a token of no device (one issued by hand before
 * devices had their own tokens): the events feed answers `"device": false`
 * (server `docs/devices.md`, "Tokens without a device"). Push still works,
 * but the server can't tell which phone it is and refuses its push
 * registration, so push stops if the push address changes. The app keeps
 * the flag, Settings → Server shows "Pair this phone again" over its Pair
 * again button, and one notification (once, not repeated) points there.
 * Pairing clears it ([clear], from `DevicePairing.store`), and so does an
 * answer that says the phone is a device.
 */
object PairAgain {
    private const val TAG = "PairAgain"
    private const val KEY_NEEDED = "pair_again_needed"
    private const val KEY_NOTIFIED = "pair_again_notified"
    private const val CHANNEL = "dana_important"
    private const val NOTIFICATION_ID = 0x5041

    /** What is remembered: whether pairing again is needed and whether the notification was shown. */
    data class State(val needed: Boolean = false, val notified: Boolean = false)

    /**
     * The state after an events answer ([device]: the server's `device` field;
     * older servers send none, read as true) and whether to post the
     * notification now: once, the first time the phone is found to be no device.
     */
    fun next(state: State, device: Boolean): Pair<State, Boolean> =
        if (device) State() to false
        else State(needed = true, notified = true) to !state.notified

    fun state(context: Context): State {
        val prefs = ServerConfig.prefs(context)
        return State(prefs.getBoolean(KEY_NEEDED, false), prefs.getBoolean(KEY_NOTIFIED, false))
    }

    /** Whether Settings → Server should ask to pair this phone again. */
    fun needed(context: Context): Boolean = state(context).needed

    /** One events answer's `device` field ([PhoneEvents.catchUp]). */
    fun note(context: Context, device: Boolean) {
        val old = state(context)
        val (new, notify) = next(old, device)
        if (new != old) save(context, new)
        if (notify) {
            Log.w(TAG, "This phone signs in with a token of no device: asking to pair again")
            show(context)
        }
    }

    /** Paired (a new device token): nothing to ask any more. */
    fun clear(context: Context) {
        save(context, State())
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager
        nm?.cancel(NOTIFICATION_ID)
    }

    private fun save(context: Context, state: State) {
        ServerConfig.prefs(context).edit {
            if (state.needed) putBoolean(KEY_NEEDED, true) else remove(KEY_NEEDED)
            if (state.notified) putBoolean(KEY_NOTIFIED, true) else remove(KEY_NOTIFIED)
        }
    }

    private fun show(context: Context) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(NotificationChannel(CHANNEL, context.getString(R.string.channel_important),
            NotificationManager.IMPORTANCE_HIGH))
        val open = Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            .putExtra(EXTRA_OPEN_SCREEN, "settings")
        val text = context.getString(R.string.pair_again_text)
        val notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(context.getString(R.string.pair_again_title))
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .setContentIntent(PendingIntent.getActivity(context, NOTIFICATION_ID, open,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
            .build()
        try {
            nm.notify(NOTIFICATION_ID, notification)
        } catch (e: SecurityException) {
            Log.w(TAG, "No notification permission", e)
        }
    }
}
