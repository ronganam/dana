package io.github.ronganam.dana.ui.screens.reminders

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.components.arrive
import io.github.ronganam.dana.ui.components.circuit.PlaceHeader
import io.github.ronganam.dana.ui.shell.DaySlot
import io.github.ronganam.dana.ui.shell.LocalDayDrop
import io.github.ronganam.dana.ui.shell.dayDropTarget
import io.github.ronganam.dana.ui.shell.rememberNow
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.tabular
import io.github.ronganam.dana.ui.viewmodels.MainViewModel

/** The Board's columns: the List's groups side by side. */
private enum class BoardColumn(val title: String, val groups: List<MomentGroup>, val hint: String) {
    NEEDS("Needs a moment", listOf(MomentGroup.LATE, MomentGroup.NONE), "Drop here to clear a moment."),
    NEXT("Next", listOf(MomentGroup.TODAY, MomentGroup.WAKING), "Drag onto the day above to set a time."),
    PLACES("Places & car", listOf(MomentGroup.PLACES), "Drop on a place or the car."),
    LATER("Later", listOf(MomentGroup.LATER), "")
}

private val MIN_COLUMN: Dp = 232.dp

/**
 * Unfolded Reminders, the board: the List's groups as four columns —
 * Needs a moment (Late, No moment) | Next (Today, On waking) | Places & car
 * | Later — with the same group headers and the same items ([GroupItem]):
 * the circle completes, a tap opens the moment sheet. Long-press an item and
 * drop it on the day view above (a time, waking, home), on Needs a moment
 * (no moment), or on a place or the car (Places & car shows them while
 * dragging); each drop is saved with Undo. The day view is the one timeline;
 * the board holds the reminders.
 */
@Composable
fun ReminderBoard(viewModel: MainViewModel, onMoments: (String) -> Unit, onSuggest: () -> Unit, headerExtra: @Composable () -> Unit) {
    val tc = viewModel.tasksController
    val tasks by tc.tasks.collectAsState()
    val groups by tc.taskGroups.collectAsState()
    val zones by tc.zones.collectAsState()
    val loading by tc.isLoadingTasks.collectAsState()
    val now by rememberNow()
    var adding by rememberSaveable { mutableStateOf(false) }
    val actions = rememberReminderActions(tc, onMoments, zones)
    val drop = LocalDayDrop.current
    val dragging = drop?.dragging != null

    val open = remember(tasks) { tasks.filter(::isOpen) }
    val sections = remember(open, now) { momentSections(open, now) }
    val anytime = remember(zones) { anytimeMoments(zones) }

    Column(Modifier.fillMaxSize()) {
        PlaceHeader(
            "Reminders",
            supporting = if (loading && tasks.isEmpty()) null else listSummary(sections),
            trailing = {
                IconButton(onClick = { adding = !adding }) { Icon(Icons.Outlined.Add, contentDescription = "Add a reminder", tint = circuitColors.ink) }
                headerExtra()
            }
        )
        if (adding) ReminderQuickAdd(
            onAdd = { title -> tc.quickAdd(title, null) { id -> onMoments(id) }; adding = false },
            onCancel = { adding = false },
            modifier = Modifier.padding(horizontal = 16.dp).padding(top = 12.dp).widthIn(max = 520.dp)
        )
        BoxWithConstraints(Modifier.weight(1f).fillMaxWidth().padding(top = 12.dp)) {
            val fits = maxWidth >= MIN_COLUMN * 4 + 16.dp * 2 + 12.dp * 3
            Row(
                Modifier.fillMaxSize().then(if (fits) Modifier else Modifier.horizontalScroll(rememberScrollState()))
                    .padding(horizontal = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                BoardColumn.entries.forEachIndexed { ci, col ->
                    val colSections = sections.filter { it.group in col.groups }.sortedBy { col.groups.indexOf(it.group) }
                    val count = colSections.sumOf { it.tasks.size }
                    val target = if (col == BoardColumn.NEEDS) DaySlot.Moment(RemindWhen.None) else null
                    val hovered = target != null && drop?.hovered == target
                    val outline = if (hovered) circuitColors.accent else circuitColors.accent.copy(alpha = 0.6f)
                    Column(
                        (if (fits) Modifier.weight(1f) else Modifier.width(MIN_COLUMN + 16.dp))
                            .fillMaxHeight()
                            .clip(RoundedCornerShape(24.dp))
                            .background(if (hovered) circuitColors.accentTonal.copy(alpha = 0.5f) else MaterialTheme.colorScheme.surfaceContainerLow.copy(alpha = 0.45f))
                            .then(if (target != null) Modifier.dayDropTarget(target) else Modifier)
                            .then(if (dragging && target != null) Modifier.drawBehind {
                                drawRoundRect(
                                    outline, cornerRadius = CornerRadius(24.dp.toPx()),
                                    style = Stroke(1.5.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(6.dp.toPx(), 5.dp.toPx())))
                                )
                            } else Modifier)
                            .verticalScroll(rememberScrollState())
                            .padding(start = 8.dp, end = 8.dp, top = 14.dp, bottom = 16.dp)
                    ) {
                        ColumnHead(col.title, count, Modifier.padding(horizontal = 8.dp))
                        if (col.hint.isNotEmpty() && (count == 0 || dragging)) Text(
                            col.hint, style = MaterialTheme.typography.bodySmall, color = circuitColors.inkSoft,
                            modifier = Modifier.padding(horizontal = 8.dp).padding(top = 2.dp)
                        )
                        // The places and the car take drops while something is dragged.
                        if (col == BoardColumn.PLACES) AnimatedVisibility(dragging, enter = fadeIn() + expandVertically(), exit = fadeOut() + shrinkVertically()) {
                            Column(Modifier.padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                anytime.forEach { w -> PlaceTarget(w, drop?.hovered == DaySlot.Moment(w)) }
                            }
                        }
                        var row = 0
                        colSections.forEach { section ->
                            // A column of one group needs no second title; the board leaves the group's note to the List (no room).
                            if (col.groups.size > 1) GroupHeader(section, Modifier.padding(horizontal = 8.dp).padding(top = 16.dp), withMeta = false)
                            else Spacer(Modifier.height(12.dp))
                            section.tasks.forEachIndexed { i, t ->
                                key(t.id) {
                                    GroupItem(
                                        task = t,
                                        group = section.group,
                                        index = i,
                                        count = section.tasks.size,
                                        now = now,
                                        listName = listNameOf(t, groups),
                                        zones = zones,
                                        actions = actions,
                                        modifier = Modifier.padding(top = if (i == 0) 0.dp else 2.dp).arrive("board", ci * 3 + row++)
                                    )
                                }
                            }
                        }
                        if (col == BoardColumn.NEEDS && count > 0) TextButton(onClick = onSuggest, modifier = Modifier.padding(top = 8.dp)) { Text("Ask Dana for moments") }
                        if (col != BoardColumn.NEEDS && count == 0 && !dragging) Text(
                            "Nothing here.", style = MaterialTheme.typography.bodyMedium, color = circuitColors.mute,
                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 12.dp)
                        )
                        if (col == BoardColumn.NEEDS && count == 0) Text(
                            "Every reminder has a moment.", style = MaterialTheme.typography.bodyMedium, color = circuitColors.inkSoft,
                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 12.dp)
                        )
                    }
                }
            }
        }
    }
}

/** A column's title and how many it holds. */
@Composable
private fun ColumnHead(title: String, count: Int, modifier: Modifier = Modifier) {
    val c = circuitColors
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(title, style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold), color = c.ink, maxLines = 2, modifier = Modifier.weight(1f, fill = false))
        if (count > 0) Text(
            count.toString(),
            style = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.SemiBold).tabular(),
            color = c.inkSoft,
            modifier = Modifier.padding(start = 8.dp).clip(RoundedCornerShape(10.dp)).background(MaterialTheme.colorScheme.surfaceContainerHigh)
                .padding(horizontal = 8.dp, vertical = 2.dp)
        )
    }
}

/** A place's arrive / leave, or the car's in / out: a drop target with its glyph. */
@Composable
private fun PlaceTarget(w: RemindWhen, hovered: Boolean) {
    val c = circuitColors
    Row(
        Modifier.fillMaxWidth().heightIn(min = 40.dp).clip(RoundedCornerShape(12.dp))
            .background(if (hovered) c.accentTonal else MaterialTheme.colorScheme.surfaceContainerHigh)
            .dayDropTarget(DaySlot.Moment(w))
            .padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        momentIcon(w)?.let { Icon(it, contentDescription = null, tint = if (hovered) c.onAccentTonal else c.inkSoft, modifier = Modifier.size(16.dp)) }
        Spacer(Modifier.width(8.dp))
        Text(anytimeTitle(w), style = MaterialTheme.typography.labelLarge, color = if (hovered) c.onAccentTonal else c.ink, maxLines = 1)
    }
}
