package io.github.ronganam.dana.ui.screens.reminders

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.ui.components.circuit.CircuitNode
import io.github.ronganam.dana.ui.components.circuit.LocalNodeSurface
import io.github.ronganam.dana.ui.components.circuit.NodeShape
import io.github.ronganam.dana.ui.components.circuit.NodeSize
import io.github.ronganam.dana.ui.components.circuit.NodeState
import io.github.ronganam.dana.ui.components.circuit.PlaceHeader
import io.github.ronganam.dana.ui.components.circuit.SectionHeader
import io.github.ronganam.dana.ui.shell.dragToDay
import io.github.ronganam.dana.ui.shell.rememberNow
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.tabular
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import java.time.LocalDateTime

private val GUTTER = 16.dp

/**
 * Reminders, the list: every open reminder grouped by the moment it reaches
 * you (Late · On waking · Today · Places & car · Later · No moment), each
 * group a tonal card whose rows sit on a line of moment nodes. Dana's
 * suggestion for a reminder snoozed again and again opens under its row.
 * The lists filter at the top; "Done recently" stays at the end (tap to
 * bring one back). A row opens the moment sheet ([onMoments]); unfolded, a
 * row can still be dragged onto the day strip.
 */
@Composable
fun ReminderListPane(
    viewModel: MainViewModel,
    onMoments: (String) -> Unit,
    headerExtra: (@Composable () -> Unit)? = null
) {
    val tc = viewModel.tasksController
    val tasks by tc.tasks.collectAsState()
    val groups by tc.taskGroups.collectAsState()
    val zones by tc.zones.collectAsState()
    val loading by tc.isLoadingTasks.collectAsState()
    val filter by tc.selectedGroupFilter.collectAsState()
    val now by rememberNow()
    val kept = rememberKeptSuggestions()
    var adding by rememberSaveable { mutableStateOf(false) }
    val doneOpen by viewModel.shellController.doneOpen.collectAsState()

    val open = remember(tasks) { tasks.filter(::isOpen) }
    val shown = remember(open, filter) { if (filter == null) open else open.filter { it.groupId == filter } }
    val sections = remember(shown, now) { momentSections(shown, now) }
    val done = remember(tasks, filter) { doneRecently(tasks.filter { filter == null || it.groupId == filter }, limit = 10) }
    val groupNames = remember(groups) { groups.associate { it.id to it.name } }
    fun listName(t: Task) = t.groupId?.let { groupNames[it] } ?: t.groupName

    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 32.dp)) {
        item(key = "head") {
            PlaceHeader("Reminders", trailing = {
                IconButton(onClick = { adding = !adding }) { Icon(Icons.Outlined.Add, contentDescription = "Add a reminder", tint = circuitColors.ink) }
                headerExtra?.invoke()
            })
        }
        if (groups.size > 1) item(key = "filters") {
            ListFilters(
                all = open.size,
                groups = groups.sortedBy { it.sortOrder }.map { g -> Triple(g.id, g.name, open.count { it.groupId == g.id }) }.filter { it.third > 0 || it.first == filter },
                selected = filter,
                onSelect = { tc.setGroupFilter(it) }
            )
        }
        if (adding) item(key = "add") {
            QuickAdd(onAdd = { title ->
                tc.quickAdd(title, filter) { id -> onMoments(id) }
                adding = false
            }, onCancel = { adding = false })
        }
        if (shown.isEmpty() && !loading) item(key = "empty") {
            Column(Modifier.padding(horizontal = GUTTER, vertical = 24.dp)) {
                Text("Nothing to remember.", style = MaterialTheme.typography.titleLarge, color = circuitColors.ink)
                Text("Add one with +, or tell Dana.", style = MaterialTheme.typography.bodyMedium, color = circuitColors.inkSoft)
            }
        }
        sections.forEach { section ->
            item(key = "s_${section.group}") {
                MomentSectionCard(
                    section = section,
                    now = now,
                    zones = zones,
                    kept = kept,
                    listName = ::listName,
                    onOpen = { onMoments(it.id) },
                    onToggle = { tc.setDone(it.id, true) },
                    onMove = { t, s -> tc.setTriggers(t.id, t.triggers.applying(s), dropPhrase(s.to, now)) },
                    onDrop = { t, w -> tc.setTriggers(t.id, w, "Moved") },
                    zonesFor = zones,
                    modifier = Modifier.padding(horizontal = GUTTER).padding(top = 16.dp)
                )
            }
        }
        if (done.isNotEmpty()) item(key = "done") {
            DoneCard(done, doneOpen, onFold = { viewModel.shellController.toggleDone() }, onReopen = { tc.setDone(it.id, false) },
                modifier = Modifier.padding(horizontal = GUTTER).padding(top = 24.dp))
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

@Composable
private fun QuickAdd(onAdd: (String) -> Unit, onCancel: () -> Unit) {
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
        modifier = Modifier.fillMaxWidth().padding(horizontal = GUTTER).padding(top = 12.dp).focusRequester(requester)
            .semantics { contentDescription = "New reminder" },
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

/**
 * One group: its header (Late in warm), then one tonal card (warm for Late)
 * whose rows sit on a vertical line through their moment nodes — dashed
 * where nothing will reach you.
 */
@Composable
private fun MomentSectionCard(
    section: MomentSection,
    now: LocalDateTime,
    zones: List<String>,
    kept: KeptSuggestions,
    listName: (Task) -> String?,
    onOpen: (Task) -> Unit,
    onToggle: (Task) -> Unit,
    onMove: (Task, MomentSuggestion) -> Unit,
    onDrop: (Task, io.github.ronganam.dana.data.models.Triggers) -> Unit,
    zonesFor: List<String>,
    modifier: Modifier = Modifier
) {
    val c = circuitColors
    val late = section.group == MomentGroup.LATE
    val surface = if (late) c.warmSurface else MaterialTheme.colorScheme.surfaceContainerLow
    Column(modifier.fillMaxWidth()) {
        SectionHeader(section.group.title, meta = section.meta, titleColor = if (late) c.warm else Color.Unspecified)
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(surface).padding(vertical = 8.dp)) {
            CompositionLocalProvider(LocalNodeSurface provides surface) {
                section.tasks.forEachIndexed { i, t ->
                    key(t.id) {
                        val suggestion = remember(t, zones) { snoozeSuggestion(t, zones) }?.takeIf { !kept.isKept(t) }
                        MomentRow(
                            task = t,
                            group = section.group,
                            line = rowLine(t, now, listName(t)),
                            first = i == 0,
                            last = i == section.tasks.lastIndex,
                            onOpen = { onOpen(t) },
                            onToggle = { onToggle(t) },
                            modifier = Modifier.dragToDay(t.title) { slot ->
                                onDrop(t, remindWhenFor(slot, LocalDateTime.now(), zonesFor).toTriggers())
                            }
                        ) {
                            if (suggestion != null) SuggestionCard(
                                suggestion, now,
                                onMove = { onMove(t, suggestion) },
                                onKeep = { kept.keep(t) },
                                modifier = Modifier.padding(top = 10.dp, bottom = 4.dp)
                            )
                        }
                    }
                }
            }
        }
    }
}

/** A row's node: its first moment's glyph in a circle; dashed (warm when late) with no moment. */
@Composable
fun MomentNode(task: Task, late: Boolean, modifier: Modifier = Modifier) {
    val c = circuitColors
    val w = remindWhen(task)
    CircuitNode(
        NodeShape.Circle,
        if (w == RemindWhen.None) NodeState.Paused else NodeState.Idle,
        modifier,
        size = NodeSize.Timeline,
        icon = momentIcon(w),
        strokeColor = if (late) c.warm else Color.Unspecified,
        glyphColor = if (late) c.warm else c.inkSoft
    )
}

/**
 * One reminder on its group's line: node | title and its moment line |
 * "Pick a moment" when late, else a box to complete it. [below] goes under
 * the text (the suggestion), on the same line.
 */
@Composable
private fun MomentRow(
    task: Task,
    group: MomentGroup,
    line: String,
    first: Boolean,
    last: Boolean,
    onOpen: () -> Unit,
    onToggle: () -> Unit,
    modifier: Modifier = Modifier,
    below: @Composable ColumnScope.() -> Unit
) {
    val c = circuitColors
    val late = group == MomentGroup.LATE
    val dashed = remindWhens(task).isEmpty()
    val wire = if (late) c.warm.copy(alpha = 0.6f) else c.wire
    val nodeCenter = 22.dp
    Row(
        modifier.fillMaxWidth().height(IntrinsicSize.Min).clickable(onClick = onOpen)
            .padding(start = 16.dp, end = 8.dp)
    ) {
        Box(
            Modifier.width(24.dp).fillMaxHeight().drawBehind {
                val x = size.width / 2f
                val cy = nodeCenter.toPx()
                val top = if (first) cy else 0f
                val bottom = if (last) cy else size.height
                if (bottom > top) drawLine(
                    wire, Offset(x, top), Offset(x, bottom), 1.5.dp.toPx(),
                    pathEffect = if (dashed) PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 4.dp.toPx())) else null
                )
            },
            contentAlignment = Alignment.TopCenter
        ) {
            MomentNode(task, late, Modifier.offset(y = nodeCenter - 12.dp))
        }
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f).padding(vertical = 10.dp)) {
            Text(
                task.title,
                style = MaterialTheme.typography.titleMedium.copy(textDirection = TextDirection.Content),
                color = c.ink,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis
            )
            Text(line, style = MaterialTheme.typography.bodyMedium, color = if (late) c.warm.copy(alpha = 0.85f) else c.inkSoft, maxLines = 2, overflow = TextOverflow.Ellipsis)
            below()
        }
        Box(Modifier.padding(top = 6.dp, start = 4.dp)) {
            if (late) Text(
                "Pick a moment",
                style = MaterialTheme.typography.labelLarge,
                color = c.warm,
                modifier = Modifier.clip(RoundedCornerShape(12.dp)).clickable(role = Role.Button, onClick = onOpen)
                    .padding(horizontal = 8.dp, vertical = 10.dp)
            ) else CheckBox(done = false, onClick = onToggle)
        }
    }
}

/** The square to complete a reminder (48dp target). */
@Composable
private fun CheckBox(done: Boolean, onClick: () -> Unit) {
    val c = circuitColors
    Box(
        Modifier.size(44.dp).clip(CircleShape).clickable(role = Role.Checkbox, onClickLabel = if (done) "Bring back" else "Done", onClick = onClick)
            .semantics { contentDescription = if (done) "Done" else "Not done" },
        contentAlignment = Alignment.Center
    ) {
        Box(
            Modifier.size(20.dp).clip(RoundedCornerShape(5.dp))
                .then(if (done) Modifier.background(c.accentTonal) else Modifier.border(1.5.dp, c.node, RoundedCornerShape(5.dp))),
            contentAlignment = Alignment.Center
        ) {
            if (done) Icon(Icons.Outlined.Check, contentDescription = null, tint = c.onAccentTonal, modifier = Modifier.size(14.dp))
        }
    }
}

/** "Done recently": folded under its header; open, each done reminder with its box to bring it back. */
@Composable
private fun DoneCard(done: List<Task>, open: Boolean, onFold: () -> Unit, onReopen: (Task) -> Unit, modifier: Modifier = Modifier) {
    val c = circuitColors
    Column(modifier.fillMaxWidth()) {
        SectionHeader("Done recently", meta = if (open) "Hide" else "Show ${done.size}", metaColor = c.accent, onMeta = onFold)
        if (open) Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(MaterialTheme.colorScheme.surfaceContainerLow).padding(vertical = 4.dp)) {
            done.forEach { t ->
                Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).padding(start = 16.dp, end = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        t.title,
                        style = MaterialTheme.typography.bodyLarge.copy(textDecoration = TextDecoration.LineThrough, textDirection = TextDirection.Content),
                        color = c.inkSoft,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f)
                    )
                    CheckBox(done = true) { onReopen(t) }
                }
            }
        }
    }
}
