package io.github.ronganam.dana.ui.viewmodels

import android.content.Context
import io.github.ronganam.dana.data.signals.BluetoothChoice
import io.github.ronganam.dana.data.signals.PhoneSignalStatus
import io.github.ronganam.dana.data.signals.PhoneSignals
import io.github.ronganam.dana.data.signals.SignalSettings
import io.github.ronganam.dana.data.signals.SignalsOverview
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Settings → Signals (server `docs/signals.md`): each signal and where it comes
 * from (`GET /api/signals`), the wake window (`PUT /api/profile`), and the
 * phone sources' switches ([PhoneSignals]; none on the kiosk and the TV).
 */
class SignalsController(private val ctx: ControllerContext, private val appContext: Context) {
    private val _overview = MutableStateFlow<SignalsOverview?>(null)
    val overview: StateFlow<SignalsOverview?> = _overview.asStateFlow()

    private val _failed = MutableStateFlow(false)
    /** The overview couldn't be loaded (offline, an older server) and none is shown. */
    val failed: StateFlow<Boolean> = _failed.asStateFlow()

    private val _phone = MutableStateFlow<PhoneSignalStatus?>(null)
    /** The phone sources and the access each has. */
    val phone: StateFlow<PhoneSignalStatus?> = _phone.asStateFlow()

    fun refresh() {
        refreshPhone()
        ctx.scope.launch {
            val o = ctx.load("Signals") { ctx.api.signals.overview() }
            if (o != null) {
                _overview.value = o
                o.wakeWindow?.let { SignalSettings.setWakeWindow(appContext, it) }
            }
            _failed.value = o == null && _overview.value == null
        }
    }

    /** Re-read the switches and permissions (back from a system settings page). */
    fun refreshPhone() {
        _phone.value = PhoneSignals.status(appContext)
    }

    /** Set the wake window by hand ("HH:MM"). */
    fun setWakeWindow(start: String, end: String) = changeWakeWindow(start, end)

    /** Back to the window learned from the unlocks. */
    fun resetWakeWindow() = changeWakeWindow(null, null)

    private fun changeWakeWindow(start: String?, end: String?) = ctx.action("Couldn't change the wake window") {
        val window = ctx.api.signals.setWakeWindow(start, end)
        if (window != null) {
            SignalSettings.setWakeWindow(appContext, window)
            _overview.value = _overview.value?.copy(wakeWindow = window)
        }
        refresh()
        true
    }

    fun setScreenTime(on: Boolean) { PhoneSignals.setScreenTime(appContext, on); refreshPhone() }

    fun setCar(on: Boolean) { PhoneSignals.setCar(appContext, on); refreshPhone() }

    fun pairedDevices(): List<BluetoothChoice> = PhoneSignals.pairedDevices(appContext)

    fun setCarDevice(device: BluetoothChoice?) { PhoneSignals.setCarDevice(appContext, device); refreshPhone() }

    fun setPlaces(on: Boolean) { PhoneSignals.setPlaces(appContext, on); refreshPhone() }
}
