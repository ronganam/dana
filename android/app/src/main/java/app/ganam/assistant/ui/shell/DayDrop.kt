package app.ganam.assistant.ui.shell

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.boundsInRoot
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import kotlin.math.roundToInt

/** A cell of the day strip that accepts drops. */
sealed class DaySlot {
    /** That hour today ("21:00"). */
    data class Hour(val hour: Int) : DaySlot()
    /** When I arrive home. */
    object AtHome : DaySlot()
    /** When I wake up. */
    object OnWaking : DaySlot()
}

/**
 * One thing being dragged: what the floating chip says, and what happens
 * when it is let go over a slot. Any row can carry one (reminders today, the
 * Now queue next).
 */
class DayDrag(val label: String, val onDrop: (DaySlot) -> Unit)

/**
 * Drag-onto-the-day state, provided by the shell through [LocalDayDrop].
 * Slots register their bounds with [dayDropTarget]; rows start drags with
 * [dragToDay]; the shell draws the chip with [DayDragGhost]. Everything is in
 * root coordinates.
 */
class DayDropState {
    var dragging by mutableStateOf<DayDrag?>(null)
        private set
    var pointer by mutableStateOf(Offset.Zero)
        private set
    var hovered by mutableStateOf<DaySlot?>(null)
        private set
    internal val targets = mutableStateMapOf<DaySlot, Rect>()

    /** True while a day strip is on screen (rows only become draggable then). */
    val hasTargets: Boolean get() = targets.isNotEmpty()

    fun start(drag: DayDrag, at: Offset) {
        dragging = drag
        move(at)
    }

    fun move(at: Offset) {
        pointer = at
        hovered = targets.entries.firstOrNull { it.value.contains(at) }?.key
    }

    fun drop() {
        val d = dragging
        val slot = hovered
        dragging = null
        hovered = null
        if (d != null && slot != null) d.onDrop(slot)
    }

    fun cancel() {
        dragging = null
        hovered = null
    }
}

val LocalDayDrop = staticCompositionLocalOf<DayDropState?> { null }

/** Marks this element as the drop area for [slot]. */
fun Modifier.dayDropTarget(slot: DaySlot): Modifier = composed {
    val state = LocalDayDrop.current ?: return@composed Modifier
    DisposableEffect(state, slot) { onDispose { state.targets.remove(slot) } }
    Modifier.onGloballyPositioned { state.targets[slot] = it.boundsInRoot() }
}

/**
 * Long-press this element and drag it onto the day strip. Does nothing while
 * no strip is on screen (cover screen), so lists keep their normal taps and
 * scrolling there.
 */
fun Modifier.dragToDay(label: String, onDrop: (DaySlot) -> Unit): Modifier = composed {
    val state = LocalDayDrop.current
    if (state == null || !state.hasTargets) return@composed Modifier
    val haptics = LocalHapticFeedback.current
    val currentLabel by rememberUpdatedState(label)
    val currentOnDrop by rememberUpdatedState(onDrop)
    var origin by remember { mutableStateOf(Offset.Zero) }
    Modifier
        .onGloballyPositioned { origin = it.positionInRoot() }
        .pointerInput(state) {
            detectDragGesturesAfterLongPress(
                onDragStart = { at ->
                    haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                    state.start(DayDrag(currentLabel) { slot -> currentOnDrop(slot) }, origin + at)
                },
                onDrag = { change, _ ->
                    change.consume()
                    state.move(origin + change.position)
                },
                onDragEnd = { state.drop() },
                onDragCancel = { state.cancel() }
            )
        }
}

/** The chip that follows the finger while dragging. Place it in a full-window Box. */
@Composable
fun DayDragGhost(state: DayDropState) {
    val drag = state.dragging
    if (drag != null) DragChip(state, drag.label)
}

@Composable
private fun DragChip(state: DayDropState, label: String) {
    var originInRoot by remember { mutableStateOf(Offset.Zero) }
    val shift = with(LocalDensity.current) { 14.dp.toPx() }
    Box(Modifier.onGloballyPositioned { originInRoot = it.positionInRoot() }) {
        val p = state.pointer - originInRoot
        Text(
            label,
            style = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.SemiBold, textDirection = TextDirection.Content),
            color = MaterialTheme.colorScheme.background,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier
                .offset { IntOffset((p.x + shift).roundToInt(), (p.y + shift).roundToInt()) }
                .widthIn(max = 220.dp)
                .shadow(8.dp, RoundedCornerShape(10.dp))
                .clip(RoundedCornerShape(10.dp))
                .background(MaterialTheme.colorScheme.onBackground)
                .padding(horizontal = 12.dp, vertical = 8.dp)
        )
    }
}
