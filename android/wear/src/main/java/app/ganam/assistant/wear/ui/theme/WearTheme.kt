package app.ganam.assistant.wear.ui.theme

import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import app.ganam.assistant.wear.R
import androidx.wear.compose.material.Colors
import androidx.wear.compose.material.MaterialTheme

/**
 * Same Ink & Paper palette as the phone's dark theme: warm neutrals, ONE
 * vermilion accent (also the full stop in the Dana icon), semantic status hues.
 * The canvas stays true black for OLED.
 */
object GanamWear {
    val Canvas = Color(0xFF000000)
    val Surface = Color(0xFF1B1713)
    val SurfaceHigh = Color(0xFF26201B)
    val Ink = Color(0xFFF2EBDD)
    val InkSoft = Color(0xFFCBBFAD)
    val Mute = Color(0xFF9A8E7E)
    val Outline = Color(0xFF766B5E)
    val Hairline = Color(0xFF302922)
    val Accent = Color(0xFFFF6A3D)
    val OnAccent = Color(0xFF1F0A02)
    val AccentContainer = Color(0xFF4A2314)

    /** Instrument Serif — the phone's headline face. */
    val Serif = FontFamily(Font(R.font.instrument_serif_regular))
    /** DM Mono — the phone's kicker / label face. */
    val Mono = FontFamily(Font(R.font.dm_mono_regular))
    val Success = Color(0xFF3FAE6A)
    val Warning = Color(0xFFE0A02A)
    val Danger = Color(0xFFE5483D)
    val Info = Color(0xFF4C9BE8)
}

private val WearColors = Colors(
    primary = GanamWear.Accent,
    primaryVariant = Color(0xFFD1401A),
    // Headings / status text: the phone's warm soft ink, not a second hue.
    secondary = GanamWear.InkSoft,
    secondaryVariant = GanamWear.Mute,
    background = GanamWear.Canvas,
    surface = GanamWear.Surface,
    error = GanamWear.Danger,
    onPrimary = GanamWear.OnAccent,
    onSecondary = Color.Black,
    onBackground = GanamWear.Ink,
    onSurface = GanamWear.Ink,
    onSurfaceVariant = GanamWear.InkSoft,
    onError = Color.White
)

@Composable
fun GanamWearTheme(content: @Composable () -> Unit) {
    MaterialTheme(colors = WearColors, content = content)
}
