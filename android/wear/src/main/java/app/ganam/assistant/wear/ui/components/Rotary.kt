package app.ganam.assistant.wear.ui.components

import android.view.HapticFeedbackConstants
import androidx.compose.foundation.focusable
import androidx.compose.foundation.gestures.ScrollableState
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.rotary.onRotaryScrollEvent
import androidx.compose.ui.platform.LocalView
import kotlinx.coroutines.launch
import kotlin.math.abs

/**
 * Crown / bezel scrolling for a list.
 *
 * Order matters: Compose delivers rotary events to the focused node and its
 * *ancestors*, so the handler has to sit before `focusable()` in the chain.
 * (It used to sit after it, which is why the wheel did nothing.)
 *
 * [active] re-requests focus when this screen becomes the visible one — needed
 * inside a pager, where every page is composed and the last one would win.
 */
@Composable
fun Modifier.rotaryScroll(
    state: ScrollableState,
    focusRequester: FocusRequester,
    active: Boolean = true
): Modifier {
    val scope = rememberCoroutineScope()
    val view = LocalView.current
    val ticker = remember { FloatArray(1) }
    LaunchedEffect(active) {
        if (active) runCatching { focusRequester.requestFocus() }
    }
    return this
        .onRotaryScrollEvent { event ->
            scope.launch { state.scrollBy(event.verticalScrollPixels) }
            // A light tick every ~60 px, like the system lists.
            ticker[0] += event.verticalScrollPixels
            if (abs(ticker[0]) > 60f) {
                ticker[0] = 0f
                view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
            }
            true
        }
        .focusRequester(focusRequester)
        .focusable()
}
