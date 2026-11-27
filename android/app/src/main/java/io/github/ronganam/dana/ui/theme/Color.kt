package io.github.ronganam.dana.ui.theme

import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp

/**
 * Dana design tokens ("Circuit", `docs/redesign-circuit.md`).
 *
 * Night violet, dark first: tonal surfaces on a violet night, ONE accent
 * (violet) for Dana, live things and actions, and warm ([Hue.EMBER]) only for
 * "needs you" and late. A green ([Hue.SAGE]) only where something must read as
 * done. The light theme follows the same roles on lavender paper. The seven
 * named [Hue]s colour-code kinds; their names are shared with the web app and
 * the watch.
 */

// ---- Dark neutrals (night) ----
val DarkCanvas = Color(0xFF14101C)
val DarkSurface = Color(0xFF1C1726)
val DarkSurfaceHigh = Color(0xFF241E30)
val DarkSurfaceHighest = Color(0xFF2E2740)
val DarkOutline = Color(0xFF6E6684)
/** Hairlines and idle wires on the canvas / a group. */
val DarkOutlineSoft = Color(0xFF3A3249)
val DarkInk = Color(0xFFF2EEF8)
val DarkInkSoft = Color(0xFFB9B0C9)
val DarkMute = Color(0xFF8C849C)

// ---- Light neutrals (lavender paper) ----
val LightCanvas = Color(0xFFF7F4FC)
val LightSurface = Color(0xFFEFEAF8)
val LightSurfaceHigh = Color(0xFFE6DFF3)
val LightSurfaceHighest = Color(0xFFDCD3EE)
val LightOutline = Color(0xFF857D9A)
val LightOutlineSoft = Color(0xFFD6CDE6)
val LightInk = Color(0xFF1C1726)
val LightInkSoft = Color(0xFF4B4360)
val LightMute = Color(0xFF6B6382)

// ---- Accent (violet): Dana, live, actions ----
val AccentDark = Color(0xFFB9A5FF)
val OnAccentDark = Color(0xFF21163A)
/** Tonal accent (a filled node that is done, the user's bubble, tonal buttons). */
val AccentContainerDark = Color(0xFF3A2F55)
val OnAccentContainerDark = Color(0xFFEBDDFF)
val AccentLight = Color(0xFF5F45C2)
val OnAccentLight = Color(0xFFFFFFFF)
val AccentContainerLight = Color(0xFFE6DDFF)
val OnAccentContainerLight = Color(0xFF22124F)

/** A filled button's container (dark text on it: [OnAccentDark] / [OnAccentLight]). */
val AccentFilledDark = Color(0xFFC9B8FF)
val AccentFilledLight = Color(0xFF5F45C2)

/** The bottom bar's and rail's pill indicator, and the tonal header buttons (Ask Dana). */
val IndicatorDark = DarkSurfaceHighest
val OnIndicatorDark = Color(0xFFE6DCFF)
val IndicatorLight = LightSurfaceHighest
val OnIndicatorLight = Color(0xFF2A1D57)

// ---- Needs you (warm): only for what waits on the owner, and late ----
val WarmDark = Color(0xFFFFB27A)
val WarmLight = Color(0xFFAD4F12)
/** A "needs you" group's surface (warm-tinted night / paper) and the draft bubble inside it. */
val WarmSurfaceDark = Color(0xFF2A1F27)
val WarmSurfaceHighDark = Color(0xFF3A2A31)
val WarmSurfaceLight = Color(0xFFFBEDE3)
val WarmSurfaceHighLight = Color(0xFFF4DDCD)

/**
 * The named hues (dark theme value, light theme value). Default uses:
 * EMBER needs you / late (warm; never decoration) · AMBER reminders, tasks,
 * warnings · SAGE done, calls, money in · TEAL messages, notifications · SKY
 * calendar, info · PLUM people, memory · ROSE alerts, errors, money out.
 * Tuned to sit on the violet night without competing with the accent; only
 * EMBER reads orange.
 */
enum class Hue(val dark: Color, val light: Color, private val stored: Long, private val legacyLight: Long) {
    EMBER(WarmDark, WarmLight, 0xFF6A3D, 0xD1401A),
    AMBER(Color(0xFFE8C66A), Color(0xFF7F6200), 0xF2A93B, 0xA86A00),
    SAGE(Color(0xFF7FD3A2), Color(0xFF2B7A50), 0x5FBF8A, 0x2E7D52),
    TEAL(Color(0xFF6CCFC6), Color(0xFF1C7770), 0x3DBFB3, 0x1F7A72),
    SKY(Color(0xFF8DB6FF), Color(0xFF2F64B8), 0x5AA7F0, 0x2F6FB5),
    PLUM(Color(0xFFE09CEB), Color(0xFF8E3B9E), 0xB892F2, 0x7044B8),
    ROSE(Color(0xFFF58EB0), Color(0xFFB0365F), 0xF0709A, 0xB83A64);

    fun of(dark: Boolean): Color = if (dark) this.dark else light

    /**
     * How a picked hue is stored ("#rrggbb", a list's or project's colour): the
     * value the web app and the watch know it by, which predates Circuit's
     * tuning. [DanaHues.fromHex] maps it back to this theme's value.
     */
    val storedHex: String get() = String.format("#%06x", stored)

    internal fun matches(rgb: Long): Boolean =
        rgb == stored || rgb == legacyLight || rgb == colorRgb(dark) || rgb == colorRgb(light)
}

private fun colorRgb(c: Color): Long = (((c.red * 255f + 0.5f).toInt() shl 16) or ((c.green * 255f + 0.5f).toInt() shl 8) or (c.blue * 255f + 0.5f).toInt()).toLong()

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
    /** Needs you (warm). */
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
     * theme's value, or its stored value) comes back as this theme's value,
     * any other colour as it is; null when blank or malformed.
     */
    fun fromHex(hex: String?): Color? {
        val h = hex?.trim()?.removePrefix("#")?.takeIf { it.length == 6 } ?: return null
        val rgb = h.toLongOrNull(16) ?: return null
        return Hue.entries.firstOrNull { it.matches(rgb) }?.let(::of) ?: Color(0xFF000000 or rgb)
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
val Neutral = Color(0xFF8C849C)

/**
 * Circuit roles beyond Material's scheme: the warm "needs you" colours, the
 * filled button, the pill indicator and the node / wire strokes of the line
 * components (`ui/components/circuit/`). Read through [circuitColors].
 */
@Immutable
class CircuitColors(
    val accent: Color,
    val accentFilled: Color,
    val onAccentFilled: Color,
    val accentTonal: Color,
    val onAccentTonal: Color,
    val indicator: Color,
    val onIndicator: Color,
    val warm: Color,
    val warmSurface: Color,
    val warmSurfaceHigh: Color,
    /** An idle wire on the canvas or a group, and on a raised surface. */
    val wire: Color,
    val wireRaised: Color,
    /** A paused / sleeping wire. */
    val wireDim: Color,
    /** An idle node's stroke, and a dropped / paused one's. */
    val node: Color,
    val nodeDim: Color,
    val ink: Color,
    val inkSoft: Color,
    val mute: Color
)

val DarkCircuit = CircuitColors(
    accent = AccentDark, accentFilled = AccentFilledDark, onAccentFilled = OnAccentDark,
    accentTonal = Color(0xFF4B3F73), onAccentTonal = OnAccentContainerDark,
    indicator = IndicatorDark, onIndicator = OnIndicatorDark,
    warm = WarmDark, warmSurface = WarmSurfaceDark, warmSurfaceHigh = WarmSurfaceHighDark,
    wire = DarkOutlineSoft, wireRaised = Color(0xFF4A4160), wireDim = Color(0xFF2C263A),
    node = DarkMute, nodeDim = Color(0xFF5A5270),
    ink = DarkInk, inkSoft = DarkInkSoft, mute = DarkMute
)

val LightCircuit = CircuitColors(
    accent = AccentLight, accentFilled = AccentFilledLight, onAccentFilled = OnAccentLight,
    accentTonal = Color(0xFFD9CCFF), onAccentTonal = OnAccentContainerLight,
    indicator = IndicatorLight, onIndicator = OnIndicatorLight,
    warm = WarmLight, warmSurface = WarmSurfaceLight, warmSurfaceHigh = WarmSurfaceHighLight,
    wire = Color(0xFFCFC6E0), wireRaised = Color(0xFFC2B8D6), wireDim = Color(0xFFE2DCEC),
    node = LightMute, nodeDim = Color(0xFFADA5C0),
    ink = LightInk, inkSoft = LightInkSoft, mute = LightMute
)

val LocalCircuitColors = staticCompositionLocalOf { DarkCircuit }

/** The current theme's Circuit roles: `circuitColors.warm`, `circuitColors.wire`. */
val circuitColors: CircuitColors
    @Composable @ReadOnlyComposable get() = LocalCircuitColors.current
