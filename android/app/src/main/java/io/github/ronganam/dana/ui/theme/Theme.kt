package io.github.ronganam.dana.ui.theme

import io.github.ronganam.dana.BuildConfig
import io.github.ronganam.dana.data.network.ServerConfig
import android.app.Activity
import android.content.Context
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.foundation.LocalIndication
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat
import androidx.core.content.edit

enum class ThemeMode(val label: String) { SYSTEM("System"), LIGHT("Light"), DARK("Dark") }

/** User theme preference, persisted in DanaPrefs ("theme_mode"). Observable from Compose. */
object ThemeSettings {
    private const val KEY = "theme_mode"
    /** Dana opens dark (Circuit is dark first) until the owner picks Light or System. */
    var mode by mutableStateOf(ThemeMode.DARK)
        private set

    fun load(context: Context) {
        val raw = ServerConfig.prefs(context).getString(KEY, null)
        mode = ThemeMode.values().firstOrNull { it.name == raw } ?: ThemeMode.DARK
    }

    fun set(context: Context, value: ThemeMode) {
        mode = value
        ServerConfig.prefs(context).edit { putString(KEY, value.name) }
    }
}

private val DarkScheme = darkColorScheme(
    primary = AccentDark, onPrimary = OnAccentDark,
    primaryContainer = AccentContainerDark, onPrimaryContainer = OnAccentContainerDark,
    secondary = DarkInkSoft, onSecondary = DarkCanvas,
    // Material's NavigationBar / NavigationRail draw their pill in secondaryContainer.
    secondaryContainer = IndicatorDark, onSecondaryContainer = OnIndicatorDark,
    tertiary = Hue.SKY.dark, onTertiary = DarkCanvas,
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
    error = Hue.ROSE.dark, onError = DarkCanvas,
    errorContainer = androidx.compose.ui.graphics.Color(0xFF4D1F33),
    onErrorContainer = androidx.compose.ui.graphics.Color(0xFFFFD9E3),
    scrim = androidx.compose.ui.graphics.Color.Black
)

private val LightScheme = lightColorScheme(
    primary = AccentLight, onPrimary = OnAccentLight,
    primaryContainer = AccentContainerLight, onPrimaryContainer = OnAccentContainerLight,
    secondary = LightInkSoft, onSecondary = LightSurface,
    secondaryContainer = IndicatorLight, onSecondaryContainer = OnIndicatorLight,
    tertiary = Hue.SKY.light, onTertiary = LightSurface,
    tertiaryContainer = LightSurfaceHigh, onTertiaryContainer = LightInkSoft,
    background = LightCanvas, onBackground = LightInk,
    surface = LightCanvas, onSurface = LightInk,
    surfaceVariant = LightSurface, onSurfaceVariant = LightInkSoft,
    surfaceTint = AccentLight,
    surfaceContainerLowest = LightCanvas,
    surfaceContainerLow = LightSurface,
    surfaceContainer = LightSurface,
    surfaceContainerHigh = LightSurfaceHigh,
    surfaceContainerHighest = LightSurfaceHighest,
    inverseSurface = LightInk, inverseOnSurface = LightCanvas, inversePrimary = AccentDark,
    outline = LightOutline, outlineVariant = LightOutlineSoft,
    error = Hue.ROSE.light, onError = androidx.compose.ui.graphics.Color.White,
    errorContainer = androidx.compose.ui.graphics.Color(0xFFFFDAE3),
    onErrorContainer = androidx.compose.ui.graphics.Color(0xFF3E0018)
)


private val DarkHues = DanaHues(dark = true)
private val LightHues = DanaHues(dark = false)

/** Hairline strokes for cards: soft in both themes. */
val androidx.compose.material3.ColorScheme.hairline get() = outlineVariant

@Composable
fun DanaTheme(
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
                    val controller = WindowCompat.getInsetsController(window, view)
                    controller.isAppearanceLightStatusBars = !darkTheme
                    controller.isAppearanceLightNavigationBars = !darkTheme
                    break
                }
                ctx = ctx.baseContext
            }
        }
    }
    MaterialTheme(colorScheme = colorScheme, shapes = DanaShapes, typography = DanaTypography) {
        // Inside MaterialTheme: it would otherwise put its ripple back as the indication.
        CompositionLocalProvider(
            LocalHues provides if (darkTheme) DarkHues else LightHues,
            LocalCircuitColors provides if (darkTheme) DarkCircuit else LightCircuit,
            LocalRichMotion provides !(BuildConfig.TV || BuildConfig.KIOSK),
            LocalIndication provides PressIndication,
            content = content
        )
    }
}
