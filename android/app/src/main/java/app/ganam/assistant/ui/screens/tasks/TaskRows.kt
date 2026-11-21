package app.ganam.assistant.ui.screens.tasks

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.DirectionsCar
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Place
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.WbSunny
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.ganam.assistant.data.models.Task
import app.ganam.assistant.data.models.TaskGroup
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.screens.common.auto
import app.ganam.assistant.ui.theme.Danger
import app.ganam.assistant.ui.viewmodels.SortMode

internal fun triggerIcon(type: String): ImageVector = when (type) {
    "time" -> Icons.Filled.Schedule
    "location" -> Icons.Filled.Place
    "car" -> Icons.Filled.DirectionsCar
    else -> Icons.Filled.WbSunny
}

/**
 * One hairline-ruled reminder row: a slim priority bar, a 48dp check target, the title,
 * and a single mono meta line (when · group · triggers · HIGH).
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun TaskRow(
    task: Task,
    selected: Boolean,
    showGroup: Boolean,
    onToggle: (Boolean) -> Unit,
    onClick: () -> Unit
) {
    val done = task.status == "completed"
    val pColor = priorityColor(task.priority)
    val muted = MaterialTheme.colorScheme.onSurfaceVariant

    val meta = buildList<Pair<String, Color>> {
        taskWhenLabel(task)?.let { add(it to muted) }
        if (showGroup && task.groupName != null) add(task.groupName to muted)
        taskTriggerLabels(task).filter { it.first != "time" }.forEach { add(it.second to muted) }
        task.links.firstOrNull { it.refType == "call" }?.let { add("📞 ${it.contact ?: "Call"}" to muted) }
        if (task.priority.equals("high", true) && !done) add("High" to Danger)
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(if (selected) MaterialTheme.colorScheme.surfaceContainerHigh else Color.Transparent)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 64.dp)
                .height(IntrinsicSize.Min)
                .clickable(onClick = onClick),
            verticalAlignment = Alignment.Top
        ) {
            Box(
                Modifier
                    .width(3.dp)
                    .fillMaxHeight()
                    .background(if (done) Color.Transparent else pColor.copy(alpha = if (selected) 1f else 0.7f))
            )
            Box(
                modifier = Modifier
                    .size(52.dp)
                    .clickable { onToggle(!done) },
                contentAlignment = Alignment.Center
            ) {
                Box(
                    modifier = Modifier
                        .size(22.dp)
                        .clip(CircleShape)
                        .background(if (done) MaterialTheme.colorScheme.primary else Color.Transparent)
                        .border(
                            1.5.dp,
                            if (done) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline,
                            CircleShape
                        ),
                    contentAlignment = Alignment.Center
                ) {
                    if (done) {
                        Icon(
                            Icons.Filled.Check,
                            contentDescription = "Completed",
                            tint = MaterialTheme.colorScheme.onPrimary,
                            modifier = Modifier.size(14.dp)
                        )
                    }
                }
            }
            Column(
                modifier = Modifier
                    .weight(1f)
                    .padding(top = 13.dp, bottom = 12.dp, end = 16.dp),
                verticalArrangement = Arrangement.spacedBy(3.dp)
            ) {
                Text(
                    text = task.title,
                    style = MaterialTheme.typography.bodyLarge.auto(),
                    fontWeight = FontWeight.Medium,
                    textDecoration = if (done) TextDecoration.LineThrough else TextDecoration.None,
                    color = if (done) muted else MaterialTheme.colorScheme.onSurface,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis
                )
                if (!task.description.isNullOrEmpty()) {
                    Text(
                        text = task.description,
                        style = MaterialTheme.typography.bodySmall.auto(),
                        color = muted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
                if (meta.isNotEmpty()) {
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                        verticalArrangement = Arrangement.spacedBy(2.dp),
                        modifier = Modifier.padding(top = 2.dp)
                    ) {
                        meta.forEach { (text, color) -> MonoLabel(text, color = color) }
                    }
                }
            }
        }
        HorizontalDivider(
            modifier = Modifier.padding(start = 55.dp),
            thickness = 1.dp,
            color = MaterialTheme.colorScheme.outlineVariant
        )
    }
}

/** Serif section title with a mono count and collapse chevron. */
@Composable
internal fun TaskSectionHeader(
    title: String,
    count: Int,
    expanded: Boolean,
    accent: Color?,
    onToggle: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 52.dp)
            .clickable(onClick = onToggle)
            .padding(start = 16.dp, end = 12.dp, top = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            text = title,
            style = MaterialTheme.typography.headlineSmall,
            color = accent ?: MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.weight(1f)
        )
        MonoLabel(count.toString())
        Icon(
            imageVector = if (expanded) Icons.Filled.KeyboardArrowUp else Icons.Filled.KeyboardArrowDown,
            contentDescription = if (expanded) "Collapse" else "Expand",
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 6.dp)
        )
    }
    HorizontalDivider(
        modifier = Modifier.padding(horizontal = 16.dp),
        thickness = 1.dp,
        color = MaterialTheme.colorScheme.outline.copy(alpha = 0.5f)
    )
}

/** Text tabs "Due · Priority · Group" with an accent underline on the active one. */
@Composable
internal fun SortTabs(sortMode: SortMode, onSort: (SortMode) -> Unit, modifier: Modifier = Modifier) {
    Row(modifier = modifier, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        listOf(SortMode.BY_DUE_DATE, SortMode.BY_PRIORITY, SortMode.BY_GROUP).forEach { mode ->
            val selected = sortMode == mode
            Box(
                modifier = Modifier
                    .heightIn(min = 48.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .clickable { onSort(mode) }
                    .padding(horizontal = 10.dp),
                contentAlignment = Alignment.Center
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = sortModeLabel(mode),
                        style = MaterialTheme.typography.titleSmall,
                        color = if (selected) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Box(
                        Modifier
                            .padding(top = 4.dp)
                            .height(2.dp)
                            .width(18.dp)
                            .background(if (selected) MaterialTheme.colorScheme.primary else Color.Transparent)
                    )
                }
            }
        }
    }
}

@Composable
private fun TaskChip(
    selected: Boolean,
    label: String,
    count: Int?,
    onClick: () -> Unit,
    leading: @Composable (() -> Unit)? = null
) {
    FilterChip(
        selected = selected,
        onClick = onClick,
        shape = RoundedCornerShape(10.dp),
        leadingIcon = leading,
        label = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(label, maxLines = 1)
                if (count != null) {
                    Spacer(Modifier.width(6.dp))
                    MonoLabel(count.toString())
                }
            }
        }
    )
}

/** Compact / medium filter header: sort tabs + one consistent chip row (groups, then trigger kinds). */
@Composable
internal fun TaskFilterStrip(
    sortMode: SortMode,
    onSort: (SortMode) -> Unit,
    groups: List<TaskGroup>,
    groupCounts: Map<String, Int>,
    totalActive: Int,
    selectedGroup: String?,
    selectedTrigger: String?,
    triggerCounts: Map<String, Int>,
    onAll: () -> Unit,
    onGroup: (String) -> Unit,
    onTrigger: (String) -> Unit,
    onManage: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(modifier = modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(start = 6.dp, end = 4.dp)) {
            SortTabs(sortMode, onSort, Modifier.weight(1f))
            IconButton(onClick = onManage) {
                Icon(Icons.Filled.Tune, contentDescription = "Manage groups", tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        LazyRow(
            contentPadding = PaddingValues(horizontal = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(bottom = 8.dp)
        ) {
            item {
                TaskChip(selectedGroup == null && selectedTrigger == null, "All", totalActive, onAll)
            }
            items(groups.sortedByDescending { groupCounts[it.id] ?: 0 }) { g ->
                TaskChip(
                    selected = selectedGroup == g.id && selectedTrigger == null,
                    label = g.name,
                    count = groupCounts[g.id] ?: 0,
                    onClick = { onGroup(g.id) },
                    leading = {
                        Box(
                            Modifier
                                .size(8.dp)
                                .clip(CircleShape)
                                .background(parseGroupColor(g.color, MaterialTheme.colorScheme.primary))
                        )
                    }
                )
            }
            items(triggerTypes.filter { (triggerCounts[it] ?: 0) > 0 }) { type ->
                TaskChip(
                    selected = selectedTrigger == type,
                    label = triggerTypeLabel(type),
                    count = triggerCounts[type],
                    onClick = { onTrigger(type) },
                    leading = { Icon(triggerIcon(type), contentDescription = null, modifier = Modifier.size(16.dp)) }
                )
            }
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
    }
}

@Composable
private fun NavRow(
    label: String,
    count: Int?,
    selected: Boolean,
    onClick: () -> Unit,
    leading: @Composable (() -> Unit)? = null
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 44.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(if (selected) MaterialTheme.colorScheme.surfaceContainerHighest else Color.Transparent)
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        if (leading != null) {
            leading()
            Spacer(Modifier.width(10.dp))
        }
        Text(
            text = label,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
            color = if (selected) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f)
        )
        if (count != null) MonoLabel(count.toString())
    }
}

/** Expanded layout's left pane: view mode, groups and trigger kinds as a quiet index. */
@Composable
internal fun TaskFilterPane(
    sortMode: SortMode,
    onSort: (SortMode) -> Unit,
    groups: List<TaskGroup>,
    groupCounts: Map<String, Int>,
    totalActive: Int,
    selectedGroup: String?,
    selectedTrigger: String?,
    triggerCounts: Map<String, Int>,
    onAll: () -> Unit,
    onGroup: (String) -> Unit,
    onTrigger: (String) -> Unit,
    onManage: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 8.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp)
    ) {
        MonoLabel("View", modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
        listOf(SortMode.BY_DUE_DATE, SortMode.BY_PRIORITY, SortMode.BY_GROUP).forEach { mode ->
            NavRow(sortModeLabel(mode), null, sortMode == mode, { onSort(mode) })
        }

        MonoLabel("Groups", modifier = Modifier.padding(start = 12.dp, end = 12.dp, top = 20.dp, bottom = 8.dp))
        NavRow("All", totalActive, selectedGroup == null && selectedTrigger == null, onAll)
        groups.sortedByDescending { groupCounts[it.id] ?: 0 }.forEach { g ->
            NavRow(
                label = g.name,
                count = groupCounts[g.id] ?: 0,
                selected = selectedGroup == g.id && selectedTrigger == null,
                onClick = { onGroup(g.id) },
                leading = {
                    Box(
                        Modifier
                            .size(8.dp)
                            .clip(CircleShape)
                            .background(parseGroupColor(g.color, MaterialTheme.colorScheme.primary))
                    )
                }
            )
        }

        val activeTriggers = triggerTypes.filter { (triggerCounts[it] ?: 0) > 0 }
        if (activeTriggers.isNotEmpty()) {
            MonoLabel("Triggers", modifier = Modifier.padding(start = 12.dp, end = 12.dp, top = 20.dp, bottom = 8.dp))
            activeTriggers.forEach { type ->
                NavRow(
                    label = triggerTypeLabel(type),
                    count = triggerCounts[type],
                    selected = selectedTrigger == type,
                    onClick = { onTrigger(type) },
                    leading = {
                        Icon(
                            triggerIcon(type),
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.size(16.dp)
                        )
                    }
                )
            }
        }

        Spacer(Modifier.height(16.dp))
        TextButton(onClick = onManage) {
            Icon(Icons.Filled.Tune, contentDescription = null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text("Manage groups")
        }
    }
}
