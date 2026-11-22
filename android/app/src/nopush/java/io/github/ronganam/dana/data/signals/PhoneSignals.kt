package io.github.ronganam.dana.data.signals

import android.content.Context

/** Kiosk / TV: the phone's signal sources don't exist there (no unlocks, car or places). */
object PhoneSignals {
    fun init(context: Context) {}
    fun watch(context: Context) {}
    fun afterUnlock(context: Context) {}
    fun zonesChanged(context: Context) {}
    fun onBoot(context: Context) {}
    /** No Signals page sources. */
    fun status(context: Context): PhoneSignalStatus? = null
    fun setScreenTime(context: Context, on: Boolean) {}
    fun setCar(context: Context, on: Boolean) {}
    fun pairedDevices(context: Context): List<BluetoothChoice> = emptyList()
    fun setCarDevice(context: Context, device: BluetoothChoice?) {}
    fun setPlaces(context: Context, on: Boolean) {}
    fun openUsageAccess(context: Context) {}
}
