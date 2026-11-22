package io.github.ronganam.dana.data.signals

import android.content.Context
import androidx.core.content.edit
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.ServerConfig
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer

/**
 * The phone sources' switches and what they remember between runs, in the
 * app's prefs (`DanaPrefs`): wake from screen time, the car (its Bluetooth
 * device stays here: the server never sees the address), places, the last
 * wake window the server sent, where the screen history was last read, the
 * car state last sent, the geofences registered.
 */
object SignalSettings {
    private const val SCREEN_TIME = "signals_screen_time"
    private const val CAR = "signals_car"
    private const val CAR_ADDRESS = "signals_car_address"
    private const val CAR_NAME = "signals_car_name"
    private const val PLACES = "signals_places"
    private const val WAKE_START = "signals_wake_start"
    private const val WAKE_END = "signals_wake_end"
    private const val WAKE_LEARNED = "signals_wake_learned"
    private const val WAKE_AT = "signals_wake_at"
    private const val SCREEN_READ_AT = "signals_screen_read_at"
    private const val SCREEN_OPEN = "signals_screen_open"
    private const val CAR_STATE = "signals_car_state_"
    private const val FENCES = "signals_fences"
    private const val FENCES_FULL_AT = "signals_fences_full_at"

    private fun prefs(context: Context) = ServerConfig.prefs(context)

    fun screenTime(context: Context): Boolean = prefs(context).getBoolean(SCREEN_TIME, false)
    fun setScreenTime(context: Context, on: Boolean) = prefs(context).edit { putBoolean(SCREEN_TIME, on) }

    fun car(context: Context): Boolean = prefs(context).getBoolean(CAR, false)
    fun setCar(context: Context, on: Boolean) = prefs(context).edit { putBoolean(CAR, on) }

    /** The paired Bluetooth device that is the car: its address and name, null when none is chosen. */
    fun carDevice(context: Context): Pair<String, String>? {
        val p = prefs(context)
        val address = p.getString(CAR_ADDRESS, null) ?: return null
        return address to p.getString(CAR_NAME, null).orEmpty().ifEmpty { address }
    }

    fun setCarDevice(context: Context, address: String?, name: String?) = prefs(context).edit {
        if (address == null) { remove(CAR_ADDRESS); remove(CAR_NAME) } else { putString(CAR_ADDRESS, address); putString(CAR_NAME, name.orEmpty()) }
        remove(CAR_STATE + CarVia.BLUETOOTH.wire)
    }

    fun places(context: Context): Boolean = prefs(context).getBoolean(PLACES, false)
    fun setPlaces(context: Context, on: Boolean) = prefs(context).edit { putBoolean(PLACES, on) }

    /** The wake window the server last sent (05:00–12:00 before the first). */
    fun wakeWindow(context: Context): WakeWindow {
        val p = prefs(context)
        val start = p.getString(WAKE_START, null) ?: return WakeWindow.DEFAULT
        val end = p.getString(WAKE_END, null) ?: return WakeWindow.DEFAULT
        return WakeWindow(start, end, p.getBoolean(WAKE_LEARNED, true))
    }

    /** When [wakeWindow] was last refreshed from the server (epoch ms, 0 = never). */
    fun wakeWindowAt(context: Context): Long = prefs(context).getLong(WAKE_AT, 0)

    fun setWakeWindow(context: Context, window: WakeWindow, at: Long = System.currentTimeMillis()) = prefs(context).edit {
        putString(WAKE_START, window.start); putString(WAKE_END, window.end); putBoolean(WAKE_LEARNED, window.learned); putLong(WAKE_AT, at)
    }

    /** Where the last screen-history read ended and the stretch it left open (null: none). */
    fun screenRead(context: Context): Pair<Long, Long?> {
        val p = prefs(context)
        return p.getLong(SCREEN_READ_AT, 0) to p.getLong(SCREEN_OPEN, -1).takeIf { it >= 0 }
    }

    fun setScreenRead(context: Context, until: Long, openSince: Long?) = prefs(context).edit {
        putLong(SCREEN_READ_AT, until)
        if (openSince == null) remove(SCREEN_OPEN) else putLong(SCREEN_OPEN, openSince)
    }

    /** The car state last sent [via] it: true = in the car, false = out, null = nothing sent yet. */
    fun carState(context: Context, via: CarVia): Boolean? =
        prefs(context).getString(CAR_STATE + via.wire, null)?.let { it == "enter" }

    fun setCarState(context: Context, via: CarVia, inCar: Boolean) =
        prefs(context).edit { putString(CAR_STATE + via.wire, if (inCar) "enter" else "leave") }

    @Serializable
    private data class StoredFence(val id: String = "", val latitude: Double = 0.0, val longitude: Double = 0.0, val radiusM: Float = 0f)

    /** The geofences the phone registered last (what [GeofenceDiff.diff] starts from). */
    fun fences(context: Context): List<Fence> = runCatching {
        ApiJson.decodeFromString(ListSerializer(StoredFence.serializer()), prefs(context).getString(FENCES, null) ?: "[]")
            .filter { it.id.isNotEmpty() }.map { Fence(it.id, it.latitude, it.longitude, it.radiusM) }
    }.getOrDefault(emptyList())

    fun setFences(context: Context, fences: List<Fence>) = prefs(context).edit {
        if (fences.isEmpty()) remove(FENCES)
        else putString(FENCES, ApiJson.encodeToString(ListSerializer(StoredFence.serializer()), fences.map { StoredFence(it.id, it.latitude, it.longitude, it.radiusM) }))
    }

    /** When every geofence was last registered afresh (epoch ms, 0 = never). */
    fun fencesFullAt(context: Context): Long = prefs(context).getLong(FENCES_FULL_AT, 0)
    fun setFencesFullAt(context: Context, at: Long) = prefs(context).edit { putLong(FENCES_FULL_AT, at) }
}
