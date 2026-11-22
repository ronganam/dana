package io.github.ronganam.dana.data.wake

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.util.Log
import androidx.core.content.ContextCompat
import androidx.core.content.edit
import io.github.ronganam.dana.BuildConfig
import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.data.signals.PhoneSignals
import io.github.ronganam.dana.data.signals.SignalEvent
import io.github.ronganam.dana.data.signals.SignalSettings
import io.github.ronganam.dana.data.signals.SignalSync
import java.time.LocalTime

/**
 * The phone's unlocks, sent as "unlock" signals (server `docs/signals.md`:
 * the wake flow, and the wake window it learns). An alarm ringing or a glance
 * at the clock never unlocks the phone, so they don't count.
 *
 * Every unlock is queued, at most one every 2 min; inside the wake window
 * (the server's, cached in [SignalSettings]) it is sent at once and the
 * screen history is read a few minutes later ([PhoneSignals.afterUnlock]),
 * so the server can confirm the user is up; outside it the periodic signals
 * run sends it.
 *
 * ACTION_USER_PRESENT can't be declared in the manifest, so the receiver is
 * registered at runtime by the notification listener (which Android keeps
 * bound) and the app process.
 */
object UnlockWatcher {
    private const val TAG = "UnlockWatcher"
    private const val KEY_LAST = "wake_last_report"
    private const val MIN_GAP_MS = 2 * 60 * 1000L

    @Volatile private var registered = false

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == Intent.ACTION_USER_PRESENT) onUnlock(context.applicationContext)
        }
    }

    fun register(context: Context) {
        if (BuildConfig.KIOSK || BuildConfig.TV) return
        if (registered) return
        synchronized(this) {
            if (registered) return
            runCatching {
                ContextCompat.registerReceiver(
                    context.applicationContext, receiver, IntentFilter(Intent.ACTION_USER_PRESENT),
                    // USER_PRESENT is sent by SystemUI (not the system uid), which a
                    // NOT_EXPORTED receiver never gets. It's a protected broadcast,
                    // so no app can fake it.
                    ContextCompat.RECEIVER_EXPORTED
                )
                registered = true
            }.onFailure { Log.w(TAG, "Couldn't watch unlocks", it) }
        }
    }

    internal fun onUnlock(context: Context, now: Long = System.currentTimeMillis()) {
        val time = LocalTime.now()
        Log.i(TAG, "Unlock seen at ${time.withNano(0)}")
        val prefs = ServerConfig.prefs(context)
        if (now - prefs.getLong(KEY_LAST, 0) < MIN_GAP_MS) return
        prefs.edit { putLong(KEY_LAST, now) }
        val inWindow = SignalSettings.wakeWindow(context).contains(time.hour * 60 + time.minute)
        SignalSync.add(context, listOf(SignalEvent.Unlock(now)), now = inWindow)
        if (inWindow) PhoneSignals.afterUnlock(context)
    }
}
