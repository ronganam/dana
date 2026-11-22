package io.github.ronganam.dana.data.signals

import android.content.Context
import android.content.Intent
import androidx.activity.result.contract.ActivityResultContract

/** Kiosk / TV: the phone's signal sources don't exist there (no unlocks, car, places or sleep). */
object PhoneSignals {
    fun init(context: Context) {}
    fun watch(context: Context) {}
    fun afterUnlock(context: Context) {}
    fun zonesChanged(context: Context) {}
    fun onForeground(context: Context) {}
    fun onBoot(context: Context) {}
    /** No Signals page sources. */
    @Suppress("RedundantSuspendModifier")
    suspend fun status(context: Context): PhoneSignalStatus? = null
    fun setScreenTime(context: Context, on: Boolean) {}
    fun setCar(context: Context, on: Boolean) {}
    fun setDriving(context: Context, on: Boolean) {}
    fun setSleep(context: Context, on: Boolean) {}
    fun pairedDevices(context: Context): List<BluetoothChoice> = emptyList()
    fun setCarDevice(context: Context, device: BluetoothChoice?) {}
    fun setPlaces(context: Context, on: Boolean) {}
    fun openUsageAccess(context: Context) {}
    fun sleepPermissions(context: Context): Set<String> = emptySet()
    /** Never launched: the Signals page shows no phone sources here. */
    fun sleepPermissionContract(): ActivityResultContract<Set<String>, Set<String>> = object : ActivityResultContract<Set<String>, Set<String>>() {
        override fun createIntent(context: Context, input: Set<String>): Intent = Intent()
        override fun parseResult(resultCode: Int, intent: Intent?): Set<String> = emptySet()
    }
    fun openHealthConnectInstall(context: Context) {}
    fun openHealthConnect(context: Context) {}
}
