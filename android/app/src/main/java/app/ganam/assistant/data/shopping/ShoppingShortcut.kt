package app.ganam.assistant.data.shopping

import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat
import app.ganam.assistant.BuildConfig
import app.ganam.assistant.EXTRA_OPEN_SCREEN
import app.ganam.assistant.MainActivity
import app.ganam.assistant.R

/**
 * The launcher shortcut "Shopping" (long-press the app icon): opens the
 * Shopping screen. Pushed while the user has the shopping list, removed when
 * they don't; phone only (the kiosk and the TV have no Shopping screen).
 */
object ShoppingShortcut {
    private const val ID = "shopping"

    fun update(context: Context, enabled: Boolean) {
        if (BuildConfig.KIOSK || BuildConfig.TV) return
        runCatching {
            if (enabled) {
                val intent = Intent(context, MainActivity::class.java)
                    .setAction(Intent.ACTION_VIEW)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                    .putExtra(EXTRA_OPEN_SCREEN, "shopping")
                val info = ShortcutInfoCompat.Builder(context, ID)
                    .setShortLabel("Shopping")
                    .setLongLabel("Shopping list")
                    .setIcon(IconCompat.createWithResource(context, R.drawable.ic_shortcut_shopping))
                    .setIntent(intent)
                    .build()
                ShortcutManagerCompat.pushDynamicShortcut(context, info)
            } else {
                ShortcutManagerCompat.removeDynamicShortcuts(context, listOf(ID))
            }
        }.onFailure { Log.w("ShoppingShortcut", "Couldn't update the launcher shortcut", it) }
    }
}
