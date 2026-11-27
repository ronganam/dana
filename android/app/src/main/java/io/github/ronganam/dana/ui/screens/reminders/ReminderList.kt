package io.github.ronganam.dana.ui.screens.reminders

import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.ui.components.arrive
import io.github.ronganam.dana.ui.components.circuit.PlaceHeader
import io.github.ronganam.dana.ui.components.circuit.SectionHeader
import io.github.ronganam.dana.ui.components.circuit.groupItemShape
import io.github.ronganam.dana.ui.shell.rememberNow
import io.github.ronganam.dana.ui.theme.Motion
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.tabular
import io.github.ronganam.dana.ui.viewmodels.MainViewModel

private val GUTTER = 16.dp

/**
 * Reminders, the list: every open reminder grouped by the moment it reaches
 * you (Late · On waking · Today · Places & car · Later · No moment), each
 * group a header over joined items ([GroupItem], the Board's items too):
 * the circle completes it (Undo in the message), a tap opens the moment
 * sheet ([onMoments]), Dana's suggestion opens under one snoozed again and
 * again. The lists filter at the top; "Done recently" stays at the end (its
 * circles bring one back). [top] goes under the header (the folded day view);
 * unfolded, an item can be dragged onto the day view above.
 */
@Composable
fun ReminderListPane(
    viewModel: MainViewModel,
    onMoments: (String) -> Unit,
    headerExtra: (@Composable () -> Unit)? = null,
    belowHeader: (@Composable () -> Unit)? = null,
    top: (@Composable () -> Unit)? = null
) {
    val tc = viewModel.tasksController
    val tasks by tc.tasks.collectAsState()
    val groups by tc.taskGroups.collectAsState()
    val zones by tc.zones.collectAsState()
    val loading by tc.isLoadingTasks.collectAsState()
    val filter by tc.selectedGroupFilter.collectAsState()
    val now by rememberNow()
    var adding by rememberSaveable { mutableStateOf(false) }
    val doneOpen by viewModel.shellController.doneOpen.collectAsState()
    val actions = rememberReminderActions(tc, onMoments, zones)

    val open = remember(tasks) { tasks.filter(::isOpen) }
    val shown = remember(open, filter) { if (filter == null) open else open.filter { it.groupId == filter } }
    val sections = remember(shown, now) { momentSections(shown, now) }
    val done = remember(tasks, filter) { doneRecently(tasks.filter { filter == null || it.groupId == filter }, limit = 10) }
    fun listName(t: Task) = listNameOf(t, groups)
    val rowIndex = remember(sections) { sections.flatMap { it.tasks }.withIndex().associate { (i, t) -> t.id to i } }
    val placement = tween<androidx.compose.ui.unit.IntOffset>(Motion.SLOW, easing = Motion.Standard)

    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 32.dp)) {
        item(key = "head") {
            val add: @Composable () -> Unit = {
                IconButton(onClick = { adding = !adding }) { Icon(Icons.Outlined.Add, contentDescription = "Add a reminder", tint = circuitColors.ink) }
            }
            val summary = if (loading && tasks.isEmpty()) null else listSummary(sections)
            // A narrow pane (unfolded, beside the editor) puts its buttons on a row of their own.
            if (belowHeader != null) {
                PlaceHeader("Reminders", supporting = summary)
                Row(Modifier.fillMaxWidth().padding(horizontal = GUTTER).padding(top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    belowHeader()
                    Spacer(Modifier.weight(1f))
                    add()
                }
            } else PlaceHeader("Reminders", supporting = summary, trailing = {
                add()
                headerExtra?.invoke()
            })
        }
        if (top != null) item(key = "top") { top() }
        if (groups.size > 1) item(key = "filters") {
            ListFilters(
                all = open.size,
                groups = groups.sortedBy { it.sortOrder }.map { g -> Triple(g.id, g.name, open.count { it.groupId == g.id }) }.filter { it.third > 0 || it.first == filter },
                selected = filter,
                onSelect = { tc.setGroupFilter(it) }
            )
        }
        if (adding) item(key = "add") {
            ReminderQuickAdd(onAdd = { title ->
                tc.quickAdd(title, filter) { id -> onMoments(id) }
                adding = false
            }, onCancel = { adding = false }, modifier = Modifier.padding(horizontal = GUTTER).padding(top = 12.dp))
        }
        if (shown.isEmpty() && !loading) item(key = "empty") {
            Column(Modifier.padding(horizontal = GUTTER, vertical = 28.dp).arrive("reminders-empty", 0)) {
                Text("Nothing to remember.", style = MaterialTheme.typography.titleLarge, color = circuitColors.ink)
                Text("Add one with +, or tell Dana.", style = MaterialTheme.typography.bodyMedium, color = circuitColors.inkSoft)
            }
        }
        sections.forEach { section ->
            item(key = "s_${section.group}") {
                GroupHeader(section, Modifier.padding(horizontal = GUTTER).padding(top = 20.dp).animateItem(fadeInSpec = null, fadeOutSpec = null, placementSpec = placement))
            }
            itemsIndexed(section.tasks, key = { _, t -> "t_${t.id}" }) { i, t ->
                GroupItem(
                    task = t,
                    group = section.group,
                    index = i,
                    count = section.tasks.size,
                    now = now,
                    listName = listName(t),
                    zones = zones,
                    actions = actions,
                    modifier = Modifier
                        .padding(horizontal = GUTTER)
                        .padding(top = if (i == 0) 0.dp else 2.dp)
                        .animateItem(fadeInSpec = null, fadeOutSpec = null, placementSpec = placement)
                        .arrive("reminders", rowIndex[t.id] ?: 0)
                )
            }
        }
        if (done.isNotEmpty()) {
            item(key = "done_head") {
                SectionHeader(
                    "Done recently",
                    meta = if (doneOpen) "Hide" else "Show ${done.size}",
                    metaColor = circuitColors.accent,
                    onMeta = { viewModel.shellController.toggleDone() },
                    modifier = Modifier.padding(horizontal = GUTTER).padding(top = 28.dp).animateItem(fadeInSpec = null, fadeOutSpec = null, placementSpec = placement)
                )
            }
            if (doneOpen) itemsIndexed(done, key = { _, t -> "d_${t.id}" }) { i, t ->
                ReminderItem(
                    task = t,
                    line = listName(t).orEmpty(),
                    late = false,
                    onOpen = { actions.open(t) },
                    onComplete = { actions.complete(t) },
                    shape = groupItemShape(i, done.size),
                    titleLines = 1,
                    modifier = Modifier.padding(horizontal = GUTTER).padding(top = if (i == 0) 0.dp else 2.dp)
                        .animateItem(fadeInSpec = null, fadeOutSpec = null, placementSpec = placement)
                )
            }
        }
    }
}

/** "All 14 · Personal 6 · Work 5": one pill per list with open reminders. */
@Composable
private fun ListFilters(all: Int, groups: List<Triple<String, String, Int>>, selected: String?, onSelect: (String?) -> Unit) {
    LazyRow(
        contentPadding = PaddingValues(horizontal = GUTTER),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier.padding(top = 8.dp)
    ) {
        item(key = "all") { FilterPill("All", all, selected == null) { onSelect(null) } }
        items(groups, key = { it.first }) { (id, name, n) -> FilterPill(name, n, selected == id) { onSelect(if (selected == id) null else id) } }
    }
}

@Composable
private fun FilterPill(label: String, count: Int, on: Boolean, onClick: () -> Unit) {
    val c = circuitColors
    Row(
        Modifier
            .clip(RoundedCornerShape(18.dp))
            .background(if (on) c.indicator else MaterialTheme.colorScheme.surfaceContainerLow)
            .clickable(role = Role.Tab, onClick = onClick)
            .heightIn(min = 36.dp)
            .padding(horizontal = 14.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(label, style = MaterialTheme.typography.labelLarge, color = if (on) c.onIndicator else c.ink, maxLines = 1)
        Text(
            " $count",
            style = MaterialTheme.typography.labelLarge.copy(fontWeight = FontWeight.Normal).tabular(),
            color = if (on) c.onIndicator.copy(alpha = 0.75f) else c.inkSoft
        )
    }
}

/** The field a new reminder is typed in (List and Board): Done adds it and opens its moment sheet. */
@Composable
fun ReminderQuickAdd(onAdd: (String) -> Unit, onCancel: () -> Unit, modifier: Modifier = Modifier) {
    var text by rememberSaveable { mutableStateOf("") }
    val focus = LocalFocusManager.current
    val requester = remember { FocusRequester() }
    val c = circuitColors
    LaunchedEffect(Unit) { requester.requestFocus() }
    fun submit() {
        val t = text.trim()
        if (t.isEmpty()) { onCancel(); return }
        onAdd(t)
        text = ""
        focus.clearFocus()
    }
    BasicTextField(
        value = text,
        onValueChange = { text = it },
        singleLine = true,
        textStyle = MaterialTheme.typography.bodyLarge.copy(color = c.ink, textDirection = TextDirection.Content),
        cursorBrush = SolidColor(c.accent),
        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Done),
        keyboardActions = KeyboardActions(onDone = { submit() }),
        modifier = modifier.fillMaxWidth().focusRequester(requester).semantics { contentDescription = "New reminder" },
        decorationBox = { inner ->
            Box(
                Modifier.fillMaxWidth().heightIn(min = 52.dp).clip(RoundedCornerShape(16.dp))
                    .background(MaterialTheme.colorScheme.surfaceContainerLow).padding(horizontal = 16.dp, vertical = 14.dp),
                contentAlignment = Alignment.CenterStart
            ) {
                if (text.isEmpty()) Text("New reminder, then pick its moment", style = MaterialTheme.typography.bodyLarge, color = c.inkSoft)
                inner()
            }
        }
    )
}
