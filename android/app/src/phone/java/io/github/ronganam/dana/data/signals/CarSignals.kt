package io.github.ronganam.dana.data.signals

import android.Manifest
import android.app.UiModeManager
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.os.Build
import android.util.Log
import androidx.core.content.ContextCompat

/**
 * Getting in and out of the car, two ways, neither needing a foreground service:
 *
 * - **Bluetooth** ([CarBluetoothReceiver], declared in the phone manifest):
 *   `ACL_CONNECTED` / `ACL_DISCONNECTED` are on Android's list of implicit
 *   broadcasts a manifest receiver still gets (8–16), so the app is woken for
 *   them from the background. Only the device the user marked as the car
 *   counts; its address stays in [SignalSettings].
 * - **Android Auto** puts the phone in car mode. `ACTION_ENTER_CAR_MODE` /
 *   `ACTION_EXIT_CAR_MODE` are not on that list, so they are watched by a
 *   receiver registered at runtime ([watchCarMode]: the app process and the
 *   notification listener, which Android keeps bound), and every periodic
 *   signals run compares the current mode with the last one sent
 *   ([reconcileCarMode]), so a change made while no process ran arrives late
 *   rather than never.
 *
 * A state already sent is not sent again (a flapping connection, or the
 * periodic check after the receiver saw it). A third way, driving detection
 * for a car without Bluetooth, is [Driving]; the chosen Bluetooth car wins
 * over it ([DrivingRule]).
 */
object CarSignals {
    private const val TAG = "CarSignals"
    @Volatile private var watching = false

    private val carModeReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                UiModeManager.ACTION_ENTER_CAR_MODE -> report(context, true, CarVia.ANDROID_AUTO)
                UiModeManager.ACTION_EXIT_CAR_MODE -> report(context, false, CarVia.ANDROID_AUTO)
            }
        }
    }

    fun watchCarMode(context: Context) {
        if (watching) return
        synchronized(this) {
            if (watching) return
            runCatching {
                val filter = IntentFilter().apply {
                    addAction(UiModeManager.ACTION_ENTER_CAR_MODE)
                    addAction(UiModeManager.ACTION_EXIT_CAR_MODE)
                }
                // Sent by the system (a protected broadcast): a not-exported receiver gets it.
                ContextCompat.registerReceiver(context.applicationContext, carModeReceiver, filter, ContextCompat.RECEIVER_NOT_EXPORTED)
                watching = true
            }.onFailure { Log.w(TAG, "Couldn't watch car mode", it) }
        }
    }

    /**
     * The car state [via] one source changed to [inCar]: queued and sent at
     * once, unless the car switch is off or that state was already sent.
     */
    fun report(context: Context, inCar: Boolean, via: CarVia, at: Long = System.currentTimeMillis()) {
        val app = context.applicationContext
        if (!SignalSettings.car(app)) return
        // The car's own connection now speaks: a ride driving detection started ends with it.
        if (via == CarVia.BLUETOOTH) SignalSettings.clearCarState(app, CarVia.ACTIVITY)
        if (SignalSettings.carState(app, via) == inCar) return
        SignalSettings.setCarState(app, via, inCar)
        Log.i(TAG, "Car ${if (inCar) "enter" else "leave"} via ${via.wire}")
        SignalSync.add(app, listOf(SignalEvent.Car(inCar, at, via)), now = true)
    }

    /**
     * Compare car mode now with the last state sent. [announce] false only
     * records it (turning the switch on in the car doesn't count as getting in).
     */
    fun reconcileCarMode(context: Context, announce: Boolean = true) {
        if (!SignalSettings.car(context)) return
        val ui = context.getSystemService(UiModeManager::class.java) ?: return
        val inCar = ui.currentModeType == Configuration.UI_MODE_TYPE_CAR
        val last = SignalSettings.carState(context, CarVia.ANDROID_AUTO)
        // Nothing sent yet and not in the car: nothing to say.
        if (!announce || (last == null && !inCar)) SignalSettings.setCarState(context, CarVia.ANDROID_AUTO, inCar)
        else report(context, inCar, CarVia.ANDROID_AUTO)
    }

    /** Whether the paired devices can be listed (Android 12+ asks for Nearby devices). */
    fun canListDevices(context: Context): Boolean =
        Build.VERSION.SDK_INT < 31 || context.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED

    /** The phone's paired Bluetooth devices, by name. */
    fun pairedDevices(context: Context): List<BluetoothChoice> {
        if (!canListDevices(context)) return emptyList()
        val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter ?: return emptyList()
        return try {
            adapter.bondedDevices.orEmpty()
                .map { BluetoothChoice(it.address, it.name?.takeIf { n -> n.isNotBlank() } ?: it.address) }
                .sortedBy { it.name.lowercase() }
        } catch (e: SecurityException) {
            Log.w(TAG, "No Bluetooth access", e)
            emptyList()
        }
    }
}

/** The car's Bluetooth connection (`ACL_CONNECTED` / `ACL_DISCONNECTED`, manifest-declared). */
class CarBluetoothReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val inCar = when (intent.action) {
            BluetoothDevice.ACTION_ACL_CONNECTED -> true
            BluetoothDevice.ACTION_ACL_DISCONNECTED -> false
            else -> return
        }
        val car = SignalSettings.carDevice(context)?.first ?: return
        val device: BluetoothDevice? = if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
        }
        if (device?.address.equals(car, ignoreCase = true)) CarSignals.report(context, inCar, CarVia.BLUETOOTH)
    }
}
