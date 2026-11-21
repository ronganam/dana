package app.ganam.assistant.tv

import android.Manifest
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.Settings
import android.util.Log

/**
 * Keeps the button service ([DanaKeyService]) switched on.
 *
 * On this TV an app update leaves its accessibility service listed but
 * unbound, with `accessibility_enabled` back at 0 (that is how TvQuickBars
 * ended up dead). With WRITE_SECURE_SETTINGS (granted once over adb by
 * bin/deploy-tv.sh) Dana TV re-enables itself after updates, at boot and
 * whenever the home screen comes back.
 */
object KeyServiceGuard {
    private const val TAG = "KeyServiceGuard"

    fun canRepair(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.WRITE_SECURE_SETTINGS) == PackageManager.PERMISSION_GRANTED

    fun ensureEnabled(context: Context) {
        if (DanaKeyService.instance != null) return
        if (!canRepair(context)) {
            Log.w(TAG, "Button service is off and WRITE_SECURE_SETTINGS isn't granted")
            return
        }
        val me = ComponentName(context, DanaKeyService::class.java).flattenToString()
        val cr = context.contentResolver
        val listed = Settings.Secure.getString(cr, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES)
            .orEmpty().split(':').filter { it.isNotBlank() }
        val others = listed.filter { !it.equals(me, ignoreCase = true) }
        if (repairing) return
        repairing = true
        Thread {
            runCatching {
                // Clear first, then re-add a moment later: the accessibility manager only
                // re-reads and binds on a real change, and back-to-back writes get coalesced.
                Settings.Secure.putString(cr, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES, others.joinToString(":"))
                Thread.sleep(1000)
                Settings.Secure.putString(cr, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES, (others + me).joinToString(":"))
                Settings.Secure.putInt(cr, Settings.Secure.ACCESSIBILITY_ENABLED, 1)
                Log.i(TAG, "Re-enabled the button service")
            }.onFailure { Log.e(TAG, "Couldn't re-enable the button service", it) }
            repairing = false
        }.start()
    }

    @Volatile private var repairing = false
}

/** After an update or a reboot: switch the button service back on. */
class TvSystemReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        Log.i("TvSystemReceiver", "${intent.action}")
        KeyServiceGuard.ensureEnabled(context)
        // After a reboot Google TV's launcher comes up first (it outranks us); take over.
        if (intent.action == Intent.ACTION_BOOT_COMPLETED && TvConfig.current.homeOverride) {
            runCatching {
                context.startActivity(Intent(context, TvHomeActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }.onFailure { Log.w("TvSystemReceiver", "Couldn't open Dana TV after boot", it) }
        }
    }
}
