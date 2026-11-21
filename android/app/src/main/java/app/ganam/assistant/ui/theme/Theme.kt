package app.ganam.assistant.ui.theme

import android.app.Activity
import android.content.Context
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

enum class ThemeMode(val label: String) { SYSTEM("System"), LIGHT("Light"), DARK("Dark") }

/** User theme preference, persisted in GanamPrefs ("theme_mode"). Observable from Compose. */
object ThemeSettings {
    private const val KEY = "theme_mode"
    var mode by mutableStateOf(ThemeMode.SYSTEM)
        private set

    fun load(context: Context) {
        val raw = context.getSharedPreferences("GanamPrefs", Context.MODE_PRIVATE).getString(KEY, null)
        mode = ThemeMode.values().firstOrNull { it.name == raw } ?: ThemeMode.SYSTEM
    }

    fun set(context: Context, value: ThemeMode) {
        mode = value
        context.getSharedPreferences("GanamPrefs", Context.MODE_PRIVATE).edit().putString(KEY, value.name).apply()
    }
}

private val DarkScheme = darkColorScheme(
    primary = AccentDark, onPrimary = OnAccentDark,
    primaryContainer = AccentContainerDark, onPrimaryContainer = OnAccentContainerDark,
    secondary = DarkInkSoft, onSecondary = DarkCanvas,
    secondaryContainer = DarkSurfaceHighest, onSecondaryContainer = DarkInk,
    tertiary = Info, onTertiary = DarkCanvas,
    tertiaryContainer = DarkSurfaceHigh, onTertiaryContainer = DarkInkSoft,
    background = DarkCanvas, onBackground = DarkInk,
    surface = DarkCanvas, onSurface = DarkInk,
    surfaceVariant = DarkSurface, onSurfaceVariant = DarkInkSoft,
    surfaceTint = AccentDark,
    surfaceContainerLowest = DarkCanvas,
    surfaceContainerLow = DarkSurface,
    surfaceContainer = DarkSurface,
    surfaceContainerHigh = DarkSurfaceHigh,
    surfaceContainerHighest = DarkSurfaceHighest,
    inverseSurface = DarkInk, inverseOnSurface = DarkCanvas, inversePrimary = AccentLight,
    outline = DarkOutline, outlineVariant = DarkOutlineSoft,
    error = Danger, onError = androidx.compose.ui.graphics.Color.White,
    errorContainer = androidx.compose.ui.graphics.Color(0xFF5A1F1B),
    onErrorContainer = androidx.compose.ui.graphics.Color(0xFFFFD9D5),
    scrim = androidx.compose.ui.graphics.Color.Black
)

private val LightScheme = lightColorScheme(
    primary = AccentLight, onPrimary = OnAccentLight,
    primaryContainer = AccentContainerLight, onPrimaryContainer = OnAccentContainerLight,
    secondary = LightInkSoft, onSecondary = LightSurface,
    secondaryContainer = LightSurfaceHighest, onSecondaryContainer = LightInk,
    tertiary = Info, onTertiary = LightSurface,
    tertiaryContainer = LightSurfaceHigh, onTertiaryContainer = LightInkSoft,
    background = LightCanvas, onBackground = LightInk,
    surface = LightCanvas, onSurface = LightInk,
    surfaceVariant = LightSurface, onSurfaceVariant = LightInkSoft,
    surfaceTint = AccentLight,
    surfaceContainerLowest = LightSurface,
    surfaceContainerLow = LightSurface,
    surfaceContainer = LightSurface,
    surfaceContainerHigh = LightSurfaceHigh,
    surfaceContainerHighest = LightSurfaceHighest,
    inverseSurface = LightInk, inverseOnSurface = LightCanvas, inversePrimary = AccentDark,
    outline = LightOutline, outlineVariant = LightOutlineSoft,
    error = androidx.compose.ui.graphics.Color(0xFFC2352B), onError = androidx.compose.ui.graphics.Color.White,
    errorContainer = androidx.compose.ui.graphics.Color(0xFFFFDAD6),
    onErrorContainer = androidx.compose.ui.graphics.Color(0xFF410002)
)


/** Hairline strokes for cards: soft in both themes. */
val androidx.compose.material3.ColorScheme.hairline get() = outlineVariant

@Composable
fun GanamAssistantTheme(
    darkTheme: Boolean = when (ThemeSettings.mode) {
        ThemeMode.SYSTEM -> isSystemInDarkTheme()
        ThemeMode.LIGHT -> false
        ThemeMode.DARK -> true
    },
    content: @Composable () -> Unit
) {
    val colorScheme = if (darkTheme) DarkScheme else LightScheme
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            var ctx = view.context
            while (ctx is android.content.ContextWrapper) {
                if (ctx is Activity) {
                    val window = ctx.window
                    window.statusBarColor = colorScheme.background.toArgb()
                    window.navigationBarColor = colorScheme.background.toArgb()
                    val controller = WindowCompat.getInsetsController(window, view)
                    controller.isAppearanceLightStatusBars = !darkTheme
                    controller.isAppearanceLightNavigationBars = !darkTheme
                    break
                }
                ctx = ctx.baseContext
            }
        }
    }
    MaterialTheme(colorScheme = colorScheme, shapes = GanamShapes, typography = GanamTypography, content = content)
}
