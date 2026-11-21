package io.github.ronganam.dana.wear.ui.theme

import android.animation.ValueAnimator
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import io.github.ronganam.dana.wear.R
import androidx.wear.compose.material.Colors
import androidx.wear.compose.material.MaterialTheme

/**
 * Same Ink & Paper palette as the phone's dark theme: warm neutrals, the
 * vermilion accent (also the full stop in the Dana icon) and the named hues
 * every Dana client shares (dark values; DESIGN.md → Watch). The canvas stays
 * true black for OLED.
 */
object DanaWear {
    val Canvas = Color(0xFF000000)
    val Surface = Color(0xFF1B1713)
    val SurfaceHigh = Color(0xFF26201B)
    val Ink = Color(0xFFF2EBDD)
    val InkSoft = Color(0xFFCBBFAD)
    val Mute = Color(0xFF9A8E7E)
    val Outline = Color(0xFF766B5E)
    val Hairline = Color(0xFF302922)
    /** Text and icons on a filled hue (ember, amber, sage…). */
    val OnAccent = Color(0xFF1F0A02)

    // The named hues (dark values, the same on web, phone, TV and watch).
    /** The accent: primary actions, selection, "needs you", approvals. */
    val Ember = Color(0xFFFF6A3D)
    /** Reminders / tasks, warnings. */
    val Amber = Color(0xFFF2A93B)
    /** Calls, done / success. */
    val Sage = Color(0xFF5FBF8A)
    /** Messages, notifications. */
    val Teal = Color(0xFF3DBFB3)
    /** Calendar, events, info. */
    val Sky = Color(0xFF5AA7F0)
    /** People, contacts, memory. */
    val Plum = Color(0xFFB892F2)
    /** Alerts, errors, danger. */
    val Rose = Color(0xFFF0709A)

    val Accent = Ember

    /** A hue as a fill or chip background: 14% on the dark canvas. Never in ambient. */
    fun tint(hue: Color): Color = hue.copy(alpha = 0.14f)

    private val groupHues = listOf(Amber, Sage, Teal, Sky, Plum, Rose)

    /**
     * The hue of a reminder group. The watch is not sent the group's stored
     * colour, so it is picked by a stable hash of the name; ungrouped
     * reminders are amber. Ember is left out: it means "needs you".
     */
    fun groupHue(name: String?): Color =
        if (name.isNullOrBlank()) Amber else groupHues[Math.floorMod(name.trim().lowercase().hashCode(), groupHues.size)]

    /** Instrument Serif — the phone's headline face. */
    val Serif = FontFamily(Font(R.font.instrument_serif_regular))
    /** DM Mono — the phone's kicker / label face. */
    val Mono = FontFamily(Font(R.font.dm_mono_regular))
}

/**
 * The shared motion tokens. Compose animations follow the system animator
 * duration scale on their own; [enabled] is for the one custom loop (the
 * listening pulse), which must not run at all when motion is off.
 */
object DanaMotion {
    const val FAST = 120
    const val BASE = 200
    const val SLOW = 320
    val Standard = CubicBezierEasing(0.2f, 0f, 0f, 1f)
    val Decelerate = CubicBezierEasing(0.05f, 0.7f, 0.1f, 1f)
    val Accelerate = CubicBezierEasing(0.3f, 0f, 0.8f, 0.15f)

    /** False when the animator duration scale is 0 (motion turned off). */
    fun enabled(): Boolean = ValueAnimator.areAnimatorsEnabled()
}

private val WearColors = Colors(
    primary = DanaWear.Ember,
    primaryVariant = Color(0xFFD1401A),
    // Headings / status text: the phone's warm soft ink, not a second hue.
    secondary = DanaWear.InkSoft,
    secondaryVariant = DanaWear.Mute,
    background = DanaWear.Canvas,
    surface = DanaWear.Surface,
    error = DanaWear.Rose,
    onPrimary = DanaWear.OnAccent,
    onSecondary = Color.Black,
    onBackground = DanaWear.Ink,
    onSurface = DanaWear.Ink,
    onSurfaceVariant = DanaWear.InkSoft,
    onError = DanaWear.OnAccent
)

@Composable
fun DanaWearTheme(content: @Composable () -> Unit) {
    MaterialTheme(colors = WearColors, content = content)
}
