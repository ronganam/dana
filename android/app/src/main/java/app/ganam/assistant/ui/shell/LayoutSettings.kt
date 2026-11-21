package app.ganam.assistant.ui.shell

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/**
 * Safety net for the new main screen: Settings → "Previous layout" brings
 * back the old eight-tab screen (pref `previous_layout` in GanamPrefs,
 * default off). Observable from Compose, so the switch applies at once.
 */
object LayoutSettings {
    private const val KEY = "previous_layout"

    var previousLayout by mutableStateOf(false)
        private set

    fun load(context: Context) {
        previousLayout = context.getSharedPreferences("GanamPrefs", Context.MODE_PRIVATE).getBoolean(KEY, false)
    }

    fun set(context: Context, value: Boolean) {
        previousLayout = value
        context.getSharedPreferences("GanamPrefs", Context.MODE_PRIVATE).edit().putBoolean(KEY, value).apply()
    }
}
