package io.github.ronganam.dana.kiosk

import android.content.Context
import io.github.ronganam.dana.wake.WakePrefs
import androidx.core.content.edit

/** Kiosk-only settings; voice settings live in [WakePrefs], the server and token come from pairing (`DevicePairing`). */
object KioskPrefs {
    // The old kiosk forced a DPR-1 viewport with ?zoom=220; at the tablet's native
    // DPR (2.25) the same on-screen size is ?zoom=98.
    const val DEFAULT_PATH = "/dashboard?zoom=98"

    private const val KEY_PATH = "kiosk_dashboard_path"

    fun dashboardPath(context: Context): String =
        WakePrefs.prefs(context).getString(KEY_PATH, null)?.takeIf { it.isNotBlank() } ?: DEFAULT_PATH

    fun save(context: Context, path: String, threshold: Float, speak: Boolean, followUp: Boolean) {
        WakePrefs.prefs(context).edit { putString(KEY_PATH, path.trim()) }
        WakePrefs.saveVoice(context, threshold = threshold, speak = speak, followUp = followUp)
    }
}
