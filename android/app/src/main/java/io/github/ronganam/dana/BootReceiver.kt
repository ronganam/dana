package io.github.ronganam.dana

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * After a reboot or an app update: registers the geofences and the activity
 * transitions again (Android dropped them; `PhoneSignals.onBoot`). The
 * call-recordings scan is WorkManager's and comes back by itself.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED -> Unit
            else -> return
        }
        io.github.ronganam.dana.data.signals.PhoneSignals.onBoot(context)
    }
}
