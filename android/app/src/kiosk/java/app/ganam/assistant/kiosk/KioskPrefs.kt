package app.ganam.assistant.kiosk

import android.content.Context
import app.ganam.assistant.wake.WakePrefs

/** Kiosk-only settings; server, token and voice settings live in [WakePrefs]. */
object KioskPrefs {
    // The old kiosk forced a DPR-1 viewport with ?zoom=220; at the tablet's native
    // DPR (2.25) the same on-screen size is ?zoom=98.
    const val DEFAULT_PATH = "/dashboard?zoom=98"

    private const val KEY_PATH = "kiosk_dashboard_path"

    fun dashboardPath(context: Context): String =
        WakePrefs.prefs(context).getString(KEY_PATH, null)?.takeIf { it.isNotBlank() } ?: DEFAULT_PATH

    fun save(
        context: Context,
        serverUrl: String,
        apiToken: String,
        path: String,
        threshold: Float,
        speak: Boolean,
        followUp: Boolean
    ) {
        WakePrefs.saveServer(context, serverUrl, apiToken)
        WakePrefs.prefs(context).edit().putString(KEY_PATH, path.trim()).apply()
        WakePrefs.saveVoice(context, threshold = threshold, speak = speak, followUp = followUp)
    }
}
