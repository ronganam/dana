package io.github.ronganam.dana.data.signals

/** How much location the app may use: none, while in use, or all the time (what geofences need). */
enum class LocationAccess { NONE, WHILE_IN_USE, ALWAYS }

/** A paired Bluetooth device the user can mark as their car (the address never leaves the phone). */
data class BluetoothChoice(val address: String, val name: String)

/** The phone sources on the Signals page: each switch and the access it needs. */
data class PhoneSignalStatus(
    /** "Wake from screen time" is on. */
    val screenTime: Boolean,
    /** The screen history can be read on this Android (9+). */
    val screenTimeSupported: Boolean,
    /** Usage access is granted (Settings → Apps → Special app access). */
    val usageAccess: Boolean,
    /** The car switch is on. */
    val car: Boolean,
    /** The paired device marked as the car (its name), null when none. */
    val carDevice: String?,
    /** Paired devices can be listed (Nearby devices on Android 12+; always before). */
    val bluetoothAllowed: Boolean,
    /** "Place reminders" is on. */
    val places: Boolean,
    val location: LocationAccess,
    /** The geofences registered now. */
    val fences: Int
)
