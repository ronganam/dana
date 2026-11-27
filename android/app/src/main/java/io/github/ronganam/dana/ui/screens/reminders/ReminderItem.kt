package io.github.ronganam.dana.ui.screens.reminders

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathMeasure
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.layout.layout
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.ui.theme.Motion
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.hues
import io.github.ronganam.dana.ui.theme.motionOff
import kotlinx.coroutines.launch
import java.time.LocalDateTime
import kotlin.math.roundToInt

/*
 * One reminder, the same everywhere it is listed (the List's rows, the
 * Board's cards, the day view's points use its hue): the circle that
 * completes it | its title and when it reaches you | an optional end. Late
 * ones are warm. Completing plays once — the circle fills and its check
 * draws, the title strikes through, the item folds away — then the reminder
 * is done (with Undo, from the controller).
 */

/** A reminder's colour: its list's stored colour, amber without one. */
@Composable
fun reminderHue(task: Task): Color = hues.fromHex(task.groupColor) ?: hues.amber

/**
 * The words under a reminder's title, the same in the List and on the Board:
 * its moments (or due date), then its list (when the moments are not already
 * the list), then how often it was snoozed.
 */
fun whenLine(task: Task, now: LocalDateTime, listName: String?, omit: Set<RemindWhen> = emptySet()): String {
    val base = rowLine(task, now, listName, omit)
    val hasMoment = (remindWhens(task) - omit).isNotEmpty() || (task.dueDate != null && parseTriggerTime(task.dueDate) != null)
    return if (hasMoment && !listName.isNullOrBlank() && !base.contains(listName)) {
        val parts = base.split(" · ")
        (listOf(parts.first(), listName) + parts.drop(1)).joinToString(" · ")
    } else base
}

/**
 * The completing state of one item: [progress] 0 → 1 draws the check and
 * the strike-through, [fold] 0 → 1 folds the item away; [start] plays both,
 * then calls back. Under system animations off it calls back at once.
 */
class Completion {
    val progress = Animatable(0f)
    val fold = Animatable(0f)
    var running by mutableStateOf(false)
        private set

    /** [fold] false: only the check (the editor, which stays open on the done reminder). */
    suspend fun play(animate: Boolean, fold: Boolean = true, then: () -> Unit) {
        if (running) return
        running = true
        if (animate) {
            progress.animateTo(1f, tween(Motion.COMPLETE - 120, easing = Motion.Standard))
            if (fold) this.fold.animateTo(1f, tween(Motion.SLOW, easing = Motion.Accelerate))
        }
        then()
        if (!fold) { progress.snapTo(0f); running = false }
    }
}

/**
 * The circle that completes a reminder (a 44dp target). Its ring is the
 * reminder's [hue] (warm when [late]); [done] fills it with the hue and
 * draws the check. [progress] (0–1) draws that fill while completing.
 */
@Composable
fun CompleteCircle(
    done: Boolean,
    hue: Color,
    late: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    progress: () -> Float = { if (done) 1f else 0f },
    size: Dp = 22.dp
) {
    val c = circuitColors
    val ring = if (late) c.warm else hue
    val checkColor = c.onAccentFilled
    Box(
        modifier
            .size(44.dp)
            .clip(CircleShape)
            .clickable(role = Role.Checkbox, onClickLabel = if (done) "Bring back" else "Done", onClick = onClick)
            .semantics {
                contentDescription = "Complete"
                stateDescription = if (done) "Done" else "Not done"
            },
        contentAlignment = Alignment.Center
    ) {
        Box(
            Modifier.size(size).drawBehind {
                val p = if (done) 1f else progress()
                val r = this.size.minDimension / 2f
                val stroke = 2.dp.toPx()
                // The ring, then the fill growing from the centre, then the check drawing in.
                drawCircle(ring, radius = r - stroke / 2, style = Stroke(stroke))
                if (p > 0f) {
                    drawCircle(ring, radius = (r - stroke / 2) * (0.35f + 0.65f * (p * 1.6f).coerceAtMost(1f)), alpha = (p * 2f).coerceAtMost(1f))
                    val checkP = ((p - 0.35f) / 0.65f).coerceIn(0f, 1f)
                    if (checkP > 0f) {
                        val path = Path().apply {
                            moveTo(r * 0.55f, r * 1.02f)
                            lineTo(r * 0.88f, r * 1.34f)
                            lineTo(r * 1.46f, r * 0.70f)
                        }
                        val measure = PathMeasure().apply { setPath(path, false) }
                        val part = Path()
                        measure.getSegment(0f, measure.length * checkP, part, true)
                        drawPath(part, checkColor, style = Stroke(2.2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
                    }
                }
            }
        )
    }
}

/**
 * One reminder: [CompleteCircle] | title and [line] | [trailing]. A tap on
 * the text opens it ([onOpen]); the circle completes it ([onComplete], after
 * the animation). [surface] and [shape] make it a card (Board) or a joined
 * row (List). [below] goes under the text (Dana's suggestion).
 */
@Composable
fun ReminderItem(
    task: Task,
    line: String,
    late: Boolean,
    onOpen: () -> Unit,
    onComplete: () -> Unit,
    modifier: Modifier = Modifier,
    surface: Color = MaterialTheme.colorScheme.surfaceContainerLow,
    shape: Shape = MaterialTheme.shapes.medium,
    titleLines: Int = 2,
    trailing: (@Composable () -> Unit)? = null,
    below: @Composable ColumnScope.() -> Unit = {}
) {
    val c = circuitColors
    val hue = reminderHue(task)
    val completion = remember(task.id) { Completion() }
    val scope = rememberCoroutineScope()
    val haptics = LocalHapticFeedback.current
    val animate = !motionOff()
    val complete by rememberUpdatedState(onComplete)
    val done = !isOpen(task)
    val firstLine = remember { floatArrayOf(0f, 0f) }

    Column(
        modifier
            // Fold away after the check (the one animated size in the app).
            .layout { measurable, constraints ->
                val p = measurable.measure(constraints)
                val h = (p.height * (1f - completion.fold.value)).roundToInt()
                layout(p.width, h) { p.placeRelative(0, 0) }
            }
            .graphicsLayer { alpha = 1f - completion.fold.value }
            .fillMaxWidth()
            .clip(shape)
            .background(if (late) c.warmSurface else surface)
            .clickable(onClick = onOpen)
    ) {
        Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(start = 6.dp, end = if (trailing != null) 4.dp else 16.dp), verticalAlignment = Alignment.Top) {
            CompleteCircle(
                done = done,
                hue = hue,
                late = late,
                onClick = {
                    if (!done) {
                        haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                        scope.launch { completion.play(animate) { complete() } }
                    } else complete()
                },
                progress = { completion.progress.value },
                modifier = Modifier.padding(top = 6.dp)
            )
            Spacer(Modifier.width(4.dp))
            Column(Modifier.weight(1f).padding(vertical = 14.dp)) {
                Text(
                    task.title,
                    style = MaterialTheme.typography.titleMedium.copy(textDirection = TextDirection.Content),
                    color = c.ink,
                    maxLines = titleLines,
                    overflow = TextOverflow.Ellipsis,
                    onTextLayout = { firstLine[0] = it.getLineLeft(0); firstLine[1] = it.getLineRight(0) },
                    // Full width, so a Hebrew title sits on the right (its own script's side).
                    modifier = Modifier.fillMaxWidth().strikeThrough(c.inkSoft, startsRtl(task.title), firstLine) { if (done) 1f else completion.progress.value }
                )
                if (line.isNotEmpty()) WhenLine(task, line, late)
            }
            if (trailing != null) Box(Modifier.padding(top = 6.dp)) { trailing() }
        }
        // Under the whole item (Dana's suggestion), so it has the item's full width.
        Column(Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp)) { below() }
    }
}

/** The moment's glyph and the words, in the hue's quiet text (warm when late). */
@Composable
fun WhenLine(task: Task, line: String, late: Boolean, modifier: Modifier = Modifier) {
    val c = circuitColors
    val color = if (late) c.warm else c.inkSoft
    Row(modifier.padding(top = 2.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
        val icon = momentIcon(remindWhen(task))
        if (icon != null) Icon(icon, contentDescription = null, tint = color.copy(alpha = 0.9f), modifier = Modifier.size(15.dp))
        Text(line, style = MaterialTheme.typography.bodyMedium, color = color, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
}

/** Whether [text] reads right to left (its first letter is Hebrew, Arabic…). */
fun startsRtl(text: String): Boolean =
    text.firstOrNull { it.isLetter() }?.let { Character.getDirectionality(it) == Character.DIRECTIONALITY_RIGHT_TO_LEFT } == true

/** A strike-through that grows along the text's first line in its reading order as [progress] goes 0 → 1 (drawn, no relayout). */
private fun Modifier.strikeThrough(color: Color, rtl: Boolean, line: FloatArray, progress: () -> Float): Modifier = drawWithContent {
    drawContent()
    val p = progress()
    if (p > 0f) {
        val y = 13.dp.toPx()
        // The first line's own extent (the text, not the full-width box).
        val left = line[0]
        val right = if (line[1] > line[0]) line[1] else size.width
        val w = right - left
        if (rtl) drawLine(color, Offset(right, y), Offset(right - w * p, y), 1.5.dp.toPx())
        else drawLine(color, Offset(left, y), Offset(left + w * p, y), 1.5.dp.toPx())
    }
}
