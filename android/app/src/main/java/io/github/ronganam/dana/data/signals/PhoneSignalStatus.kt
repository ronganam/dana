package io.github.ronganam.dana.data.signals

/** How much location the app may use: none, while in use, or all the time (what geofences need). */
enum class LocationAccess { NONE, WHILE_IN_USE, ALWAYS }

/** Health Connect on this phone: none can run here (Android 8), it must be installed or updated, or it is ready. */
enum class HealthConnectState { UNSUPPORTED, NEEDS_UPDATE, AVAILABLE }

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
    /** "Detect driving" is on. */
    val driving: Boolean,
    /** Activity Recognition is allowed (Physical activity on Android 10+). */
    val activityAllowed: Boolean,
    /** "Place reminders" is on. */
    val places: Boolean,
    val location: LocationAccess,
    /** The geofences registered now. */
    val fences: Int,
    /** "Wake from sleep data" is on. */
    val sleep: Boolean,
    val healthConnect: HealthConnectState,
    /** Health Connect lets Dana read sleep. */
    val sleepAllowed: Boolean,
    /** Health Connect can share data in the background on this phone (else reads only while Dana is open). */
    val sleepBackgroundSupported: Boolean,
    /** ... and lets Dana read it in the background. */
    val sleepBackground: Boolean
)
