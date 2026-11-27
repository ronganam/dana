package io.github.ronganam.dana.ui.screens.reminders

import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.data.models.TaskGroup
import io.github.ronganam.dana.ui.components.circuit.SectionHeader
import io.github.ronganam.dana.ui.components.circuit.groupItemShape
import io.github.ronganam.dana.ui.shell.DaySlot
import io.github.ronganam.dana.ui.shell.dragToDay
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.viewmodels.TasksController
import java.time.LocalDateTime

/*
 * What the List and the Board share: the same group headers, the same
 * joined items, the same actions (open the moment sheet, complete with
 * Undo, take Dana's suggestion, drop on a moment).
 */

/** Everything a reminder item can do, wired once for the List and the Board. */
class ReminderActions(
    val open: (Task) -> Unit,
    val complete: (Task) -> Unit,
    val move: (Task, MomentSuggestion) -> Unit,
    val drop: (Task, DaySlot) -> Unit,
    val keep: (Task) -> Unit,
    val isKept: (Task) -> Boolean
)

@Composable
fun rememberReminderActions(tc: TasksController, onMoments: (String) -> Unit, zones: List<String>): ReminderActions {
    val kept = rememberKeptSuggestions()
    return remember(tc, onMoments, zones, kept) {
        ReminderActions(
            open = { onMoments(it.id) },
            complete = { tc.setDone(it.id, isOpen(it)) },
            move = { t, s -> tc.setTriggers(t.id, t.triggers.applying(s), dropPhrase(s.to, LocalDateTime.now())) },
            drop = { t, slot ->
                val w = remindWhenFor(slot, LocalDateTime.now(), zones)
                if (t.triggers != w.toTriggers()) tc.setTriggers(t.id, w.toTriggers(), dropPhrase(w, LocalDateTime.now()))
            },
            keep = { kept.keep(it) },
            isKept = { kept.isKept(it) }
        )
    }
}

/** A list's name for a reminder (its group's current name, else the name it came with). */
fun listNameOf(task: Task, groups: List<TaskGroup>): String? =
    task.groupId?.let { id -> groups.firstOrNull { it.id == id }?.name } ?: task.groupName

/** A group's header: its title (Late warm) and what the data says about it. */
@Composable
fun GroupHeader(section: MomentSection, modifier: Modifier = Modifier, withMeta: Boolean = true) {
    val late = section.group == MomentGroup.LATE
    // Inside a Board column: a quiet subhead under the column's title.
    if (!withMeta) Text(
        section.group.title,
        style = MaterialTheme.typography.labelLarge,
        color = if (late) circuitColors.warm else circuitColors.inkSoft,
        modifier = modifier.padding(bottom = 6.dp)
    ) else SectionHeader(
        section.group.title,
        meta = section.meta?.takeIf { withMeta },
        titleColor = if (late) circuitColors.warm else Color.Unspecified,
        modifier = modifier
    )
}

/**
 * The [index]th of [count] items of a group, joined to its neighbours, with
 * Dana's suggestion under it when it was snoozed again and again; long-press
 * drags it onto the day view (or a Board column) while one is on screen.
 */
@Composable
fun GroupItem(
    task: Task,
    group: MomentGroup,
    index: Int,
    count: Int,
    now: LocalDateTime,
    listName: String?,
    zones: List<String>,
    actions: ReminderActions,
    modifier: Modifier = Modifier,
    surface: Color = MaterialTheme.colorScheme.surfaceContainerLow
) {
    val late = group == MomentGroup.LATE
    val suggestion = remember(task, zones) { snoozeSuggestion(task, zones) }?.takeIf { !actions.isKept(task) }
    ReminderItem(
        task = task,
        line = whenLine(task, now, listName, omit = if (group == MomentGroup.WAKING) setOf(RemindWhen.Wake) else emptySet()),
        late = late,
        onOpen = { actions.open(task) },
        onComplete = { actions.complete(task) },
        shape = groupItemShape(index, count),
        surface = surface,
        modifier = modifier.dragToDay(task.title) { slot -> actions.drop(task, slot) }
    ) {
        if (suggestion != null) SuggestionCard(
            suggestion, now,
            onMove = { actions.move(task, suggestion) },
            onKeep = { actions.keep(task) },
            modifier = Modifier.padding(bottom = 12.dp)
        )
    }
}
