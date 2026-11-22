package io.github.ronganam.dana.ui.theme

import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp

/**
 * Dana design tokens ("Ink & Paper", with Ember's hues).
 *
 * Paper-and-ink neutrals, ONE accent (ember / vermilion) for primary actions,
 * selection, focus and "needs you", and seven named [Hue]s for colour coding
 * by kind. The hue values are shared with the web app and the watch: change
 * them everywhere or nowhere.
 */

// ---- Dark neutrals ----
val DarkCanvas = Color(0xFF13100E)
val DarkSurface = Color(0xFF1C1713)
val DarkSurfaceHigh = Color(0xFF28201A)
val DarkSurfaceHighest = Color(0xFF342A23)
val DarkOutline = Color(0xFF766B5E)
val DarkOutlineSoft = Color(0xFF312922)
val DarkInk = Color(0xFFF2EBDD)
val DarkInkSoft = Color(0xFFCBBFAD)
val DarkMute = Color(0xFF9A8E7E)

// ---- Light neutrals ----
val LightCanvas = Color(0xFFF3EDE1)
val LightSurface = Color(0xFFFBF5EC)
val LightSurfaceHigh = Color(0xFFEDE2D1)
val LightSurfaceHighest = Color(0xFFE3D6C2)
val LightOutline = Color(0xFF857A69)
val LightOutlineSoft = Color(0xFFD9CDB7)
val LightInk = Color(0xFF1A1511)
val LightInkSoft = Color(0xFF4A4036)
val LightMute = Color(0xFF6B6050)

// ---- Accent (ember) ----
val AccentDark = Color(0xFFFF6A3D)
val OnAccentDark = Color(0xFF1F0A02)
val AccentContainerDark = Color(0xFF4A2314)
val OnAccentContainerDark = Color(0xFFFFD9CB)
val AccentLight = Color(0xFFD1401A)
val OnAccentLight = Color(0xFFFFFFFF)
val AccentContainerLight = Color(0xFFFFDDD0)
val OnAccentContainerLight = Color(0xFF4A1400)

/** The warmer, lighter end of a primary button's ember gradient (accent → this). */
val AccentWarmDark = Color(0xFFFF8A4C)
val AccentWarmLight = Color(0xFFE2582A)

/**
 * The named hues (dark theme value, light theme value). Default uses:
 * EMBER the accent · AMBER reminders, tasks, warnings · SAGE calls, done,
 * money in · TEAL messages, notifications · SKY calendar, info · PLUM people,
 * memory · ROSE alerts, errors, money out.
 */
enum class Hue(val dark: Color, val light: Color) {
    EMBER(Color(0xFFFF6A3D), Color(0xFFD1401A)),
    AMBER(Color(0xFFF2A93B), Color(0xFFA86A00)),
    SAGE(Color(0xFF5FBF8A), Color(0xFF2E7D52)),
    TEAL(Color(0xFF3DBFB3), Color(0xFF1F7A72)),
    SKY(Color(0xFF5AA7F0), Color(0xFF2F6FB5)),
    PLUM(Color(0xFFB892F2), Color(0xFF7044B8)),
    ROSE(Color(0xFFF0709A), Color(0xFFB83A64));

    fun of(dark: Boolean): Color = if (dark) this.dark else light
}

/** Hues for user-assignable tags (lists, memory categories, project colours), in picker order. */
val CategoryHues = listOf(Hue.ROSE, Hue.AMBER, Hue.SAGE, Hue.SKY, Hue.PLUM, Hue.TEAL)

/** Hues a person's initials can sit on (not ember: that one means "needs you"). */
private val PersonHues = listOf(Hue.AMBER, Hue.SAGE, Hue.TEAL, Hue.SKY, Hue.PLUM, Hue.ROSE)

/**
 * The hues of the current theme. Fills / tints are the hue at 14% (dark) or
 * 12% (light); bars, dots and icons use the full hue; text uses [text], which
 * darkens the light theme's hues enough to keep 4.5:1 on their own tint.
 */
@Immutable
class DanaHues(val dark: Boolean) {
    fun of(hue: Hue): Color = hue.of(dark)
    val ember get() = of(Hue.EMBER)
    val amber get() = of(Hue.AMBER)
    val sage get() = of(Hue.SAGE)
    val teal get() = of(Hue.TEAL)
    val sky get() = of(Hue.SKY)
    val plum get() = of(Hue.PLUM)
    val rose get() = of(Hue.ROSE)

    /** A fill in [color] (rows, tiles, pills). */
    fun tint(color: Color): Color = color.copy(alpha = if (dark) 0.14f else 0.12f)
    fun tint(hue: Hue): Color = tint(of(hue))

    /** [color] as text on its tint or the canvas (≥ 4.5:1 in both themes). */
    fun text(color: Color): Color = if (dark) color else lerp(color, LightInk, 0.3f)
    fun text(hue: Hue): Color = text(of(hue))

    /** A stable hue for a name or id (people's initials). */
    fun forName(name: String): Color = of(hueForName(name))

    /**
     * A stored "#rrggbb" (a list's or project's colour): one of the hues (either
     * theme's value) comes back as this theme's value, any other colour as it is;
     * null when blank or malformed.
     */
    fun fromHex(hex: String?): Color? {
        val h = hex?.trim()?.removePrefix("#")?.takeIf { it.length == 6 } ?: return null
        val c = h.toLongOrNull(16)?.let { Color(0xFF000000 or it) } ?: return null
        return Hue.entries.firstOrNull { it.dark == c || it.light == c }?.let(::of) ?: c
    }
}

/** The same name always gets the same hue, on every device (String.hashCode is specified). */
fun hueForName(name: String): Hue = PersonHues[Math.floorMod(name.trim().lowercase().hashCode(), PersonHues.size)]

val LocalHues = staticCompositionLocalOf { DanaHues(dark = true) }

/** The current theme's hues: `hues.sage`, `hues.tint(hues.sky)`, `hues.text(Hue.ROSE)`. */
val hues: DanaHues
    @Composable @ReadOnlyComposable get() = LocalHues.current

// ---- Semantic names over the hues (status only) ----
val Success: Color @Composable @ReadOnlyComposable get() = LocalHues.current.sage
val Warning: Color @Composable @ReadOnlyComposable get() = LocalHues.current.amber
val Info: Color @Composable @ReadOnlyComposable get() = LocalHues.current.sky
val Danger: Color @Composable @ReadOnlyComposable get() = LocalHues.current.rose

/** Neutral for "unset / archived / low" states. */
val Neutral = Color(0xFF8F877C)
