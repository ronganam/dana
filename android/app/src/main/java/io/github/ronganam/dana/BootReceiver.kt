package io.github.ronganam.dana

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import io.github.ronganam.dana.data.sync.CallUploadSettings

/**
 * After a reboot or an app update: registers the geofences again (Android
 * dropped them; `PhoneSignals.onBoot`) and restarts the call-recording
 * observer (when the user turned call recordings on), which otherwise only
 * started when the app was opened.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED -> Unit
            else -> return
        }
        io.github.ronganam.dana.data.signals.PhoneSignals.onBoot(context)
        if (!CallUploadSettings.canWatch(context)) {
            Log.i("BootReceiver", "Call recordings off or no storage access; not starting the call observer")
            return
        }
        Log.i("BootReceiver", "${intent.action}: starting the call observer")
        CallRecordingObserverService.start(context)
    }
}
