package io.github.ronganam.dana.ui.theme

import android.provider.Settings
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.FiniteAnimationSpec
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.MotionDurationScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlin.coroutines.CoroutineContext

/**
 * Ember's motion tokens (shared with the web app and the watch).
 *
 * Only alpha, translation and scale animate (graphicsLayer or a draw
 * transform), never size or layout — except collapsing the one completed row.
 * Compose's own animations follow the system animator duration scale; at 0
 * they jump to their end. Code that loops or waits must ask [motionOff] first.
 */
object Motion {
    const val FAST = 120
    const val BASE = 200
    const val SLOW = 320

    /** A reminder's whole completion: check, strike-through, fade and collapse. */
    const val COMPLETE = 420
    /** "This just changed": the highlight tint fades out over this long. */
    const val FLASH = 600
    /** Rows arriving: the gap between two rows, and how many rows take part. */
    const val STAGGER = 30
    const val STAGGER_ROWS = 8
    /** TV focus: the focused tile scales to [FOCUS_SCALE] in this long. */
    const val FOCUS = 150
    const val FOCUS_SCALE = 1.05f
    const val PRESS_SCALE = 0.97f

    val Standard = CubicBezierEasing(0.2f, 0f, 0f, 1f)
    /** Things arriving. */
    val Decelerate = CubicBezierEasing(0.05f, 0.7f, 0.1f, 1f)
    /** Things leaving. */
    val Accelerate = CubicBezierEasing(0.3f, 0f, 0.8f, 0.15f)

    /** Rows and messages arriving rise this far; panes slide this far. */
    val Rise = 8.dp
    val Slide = 12.dp

    fun <T> fast(): FiniteAnimationSpec<T> = tween(FAST, easing = Standard)
    fun <T> base(): FiniteAnimationSpec<T> = tween(BASE, easing = Standard)
    fun <T> slow(): FiniteAnimationSpec<T> = tween(SLOW, easing = Standard)
    fun <T> arrive(duration: Int = BASE): FiniteAnimationSpec<T> = tween(duration, easing = Decelerate)
    fun <T> leave(duration: Int = FAST): FiniteAnimationSpec<T> = tween(duration, easing = Accelerate)
}

/**
 * The richer patterns (rows arriving, shimmer, the canvas glow) are for the
 * phone only. The TV and the wall tablet get press, focus and state changes:
 * set by [DanaTheme] from the flavour.
 */
val LocalRichMotion = staticCompositionLocalOf { true }

val richMotion: Boolean
    @Composable @ReadOnlyComposable get() = LocalRichMotion.current

/** True when the system turned animations off (animator duration scale 0). Read once per composition. */
@Composable
fun motionOff(): Boolean {
    val context = LocalContext.current
    return remember(context) {
        Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f
    }
}

/** Inside an animation coroutine: are animations turned off for this window? */
fun CoroutineContext.motionOff(): Boolean = (this[MotionDurationScale]?.scaleFactor ?: 1f) == 0f
