package io.github.ronganam.dana

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log

/**
 * Restarts the call-recording observer after a reboot or an app update.
 * Before this it only started when the app was opened, so recordings made
 * after a restart were missed until then.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED -> Unit
            else -> return
        }
        val permission = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            android.Manifest.permission.READ_MEDIA_AUDIO
        } else {
            android.Manifest.permission.READ_EXTERNAL_STORAGE
        }
        if (context.checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) {
            Log.i("BootReceiver", "No storage permission yet; not starting the call observer")
            return
        }
        Log.i("BootReceiver", "${intent.action}: starting the call observer")
        CallRecordingObserverService.start(context)
    }
}
