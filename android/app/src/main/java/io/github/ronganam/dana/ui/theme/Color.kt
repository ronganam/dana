package io.github.ronganam.dana.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * Dana design tokens.
 *
 * Paper-and-ink neutrals + ONE accent (vermilion) used for primary actions, selection and focus.
 * Status hues are semantic only (success / warning / info / danger) and never decorative.
 */

// ---- Dark neutrals ----
val DarkCanvas = Color(0xFF13100E)
val DarkSurface = Color(0xFF1B1713)
val DarkSurfaceHigh = Color(0xFF26201B)
val DarkSurfaceHighest = Color(0xFF322A24)
val DarkOutline = Color(0xFF766B5E)
val DarkOutlineSoft = Color(0xFF302922)
val DarkInk = Color(0xFFF2EBDD)
val DarkInkSoft = Color(0xFFCBBFAD)
val DarkMute = Color(0xFF9A8E7E)

// ---- Light neutrals ----
val LightCanvas = Color(0xFFF3EDE1)
val LightSurface = Color(0xFFFAF6EE)
val LightSurfaceHigh = Color(0xFFEAE2D3)
val LightSurfaceHighest = Color(0xFFE0D6C4)
val LightOutline = Color(0xFF857A69)
val LightOutlineSoft = Color(0xFFD8CDB8)
val LightInk = Color(0xFF1A1511)
val LightInkSoft = Color(0xFF4A4036)
val LightMute = Color(0xFF6B6050)

// ---- Accent (ember amber) ----
val AccentDark = Color(0xFFFF6A3D)
val OnAccentDark = Color(0xFF1F0A02)
val AccentContainerDark = Color(0xFF4A2314)
val OnAccentContainerDark = Color(0xFFFFD9CB)
val AccentLight = Color(0xFFD1401A)
val OnAccentLight = Color(0xFFFFFFFF)
val AccentContainerLight = Color(0xFFFFDDD0)
val OnAccentContainerLight = Color(0xFF4A1400)

// ---- Semantic status hues: mid-tone, legible as icon/dot/text on both themes ----
val Success = Color(0xFF3FAE6A)
val Warning = Color(0xFFE0A02A)
val Info = Color(0xFF4C9BE8)
val Danger = Color(0xFFE5483D)

// Category palette for user-assignable tags (groups, calls, memory categories).
val CategoryPalette = listOf(
    Color(0xFFE5483D), Color(0xFFE0A02A), Color(0xFF3FAE6A),
    Color(0xFF4C9BE8), Color(0xFF8B7CE8), Color(0xFFD9609A)
)

/** Neutral for "unset / archived / low" states. */
val Neutral = Color(0xFF8F877C)

// ---- Backward-compatible names (dark palette) ----
val Canvas = DarkCanvas
val CanvasSofter = DarkSurfaceHigh
val CanvasRaised = DarkSurfaceHighest
val Hairline = DarkOutlineSoft
val HairlineStrong = DarkOutline
val Ink = DarkInk
val BodyStrong = DarkInkSoft
val Body = DarkInkSoft
