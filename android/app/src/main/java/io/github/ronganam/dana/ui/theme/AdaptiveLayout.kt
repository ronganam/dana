package io.github.ronganam.dana.ui.theme

import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalConfiguration

/** Minimum screen width (dp) at which the app switches to the adaptive sidebar / two-pane layout. */
const val WIDE_SCREEN_MIN_WIDTH_DP = 600

/**
 * True when the current screen width is at least [WIDE_SCREEN_MIN_WIDTH_DP].
 * Recomputes automatically on configuration change, so folding/unfolding a foldable
 * or rotating a tablet live-switches between layouts.
 */
@Composable
fun isWideLayout(): Boolean {
    return LocalConfiguration.current.screenWidthDp >= WIDE_SCREEN_MIN_WIDTH_DP
}