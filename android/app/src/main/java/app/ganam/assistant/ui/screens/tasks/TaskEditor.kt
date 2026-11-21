package app.ganam.assistant.ui.screens.tasks

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import app.ganam.assistant.data.models.Task
import app.ganam.assistant.data.models.TaskGroup
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.screens.common.auto
import app.ganam.assistant.ui.theme.Danger
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

typealias TaskSaveHandler = (String, String?, String, String?, JSONObject?, String?) -> Unit

private fun nowIso(): String = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).format(Date())

@Composable
private fun TriggerRow(
    label: String,
    checked: Boolean,
    onChecked: (Boolean) -> Unit,
    content: @Composable ColumnScope.() -> Unit = {}
) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(label, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
            Switch(checked = checked, onCheckedChange = onChecked)
        }
        if (checked) {
            Column(modifier = Modifier.padding(bottom = 10.dp), content = content)
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
    }
}

/**
 * The reminder form. Used inline in the wide detail pane and inside [TaskEditorSheet] on phones.
 * Scrolls by itself so it can be dropped into either container.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun TaskEditorForm(
    task: Task?,
    zones: List<String>,
    taskGroups: List<TaskGroup>,
    onSave: TaskSaveHandler,
    modifier: Modifier = Modifier,
    onDelete: (() -> Unit)? = null,
    onCancel: (() -> Unit)? = null,
    onOpenCall: ((String) -> Unit)? = null,
    onAsk: (() -> Unit)? = null
) {
    val context = LocalContext.current
    val key = task?.id

    var title by remember(key) { mutableStateOf(task?.title ?: "") }
    var description by remember(key) { mutableStateOf(task?.description ?: "") }
    var priority by remember(key) { mutableStateOf(task?.priority?.lowercase() ?: "medium") }
    var selectedGroupId by remember(key) { mutableStateOf(task?.groupId) }

    val triggersObj = remember(key, task?.triggers) { if (task != null) taskTriggersJson(task) else JSONObject() }
    var timeEnabled by remember(key) { mutableStateOf(triggersObj.has("time")) }
    var timeValue by remember(key) { mutableStateOf(triggersObj.optString("time", "")) }

    var locationEnabled by remember(key) { mutableStateOf(triggersObj.has("location")) }
    val initialLoc = triggersObj.optJSONObject("location")
    var locationZone by remember(key) { mutableStateOf(initialLoc?.optString("location") ?: zones.firstOrNull() ?: "Home") }
    var locationEvent by remember(key) { mutableStateOf(initialLoc?.optString("event") ?: "arrive") }

    var carEnabled by remember(key) { mutableStateOf(triggersObj.has("car")) }
    var carEvent by remember(key) { mutableStateOf(triggersObj.optString("car").ifEmpty { "enter" }) }

    var wakeEnabled by remember(key) { mutableStateOf(triggersObj.optBoolean("wake", false)) }

    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = if (task == null) "New reminder" else "Edit reminder",
                style = MaterialTheme.typography.headlineMedium,
                modifier = Modifier.weight(1f)
            )
            if (onDelete != null) {
                IconButton(onClick = onDelete) {
                    Icon(Icons.Filled.Delete, "Delete", tint = Danger)
                }
            }
            if (onCancel != null) {
                TextButton(onClick = onCancel) { Text("Cancel") }
            }
        }

        if (task != null && (onOpenCall != null || onAsk != null)) {
            TaskContextStrip(task, onOpenCall = onOpenCall ?: {}, onAsk = onAsk, modifier = Modifier.padding(top = 8.dp))
        }

        Spacer(Modifier.height(16.dp))

        OutlinedTextField(
            value = title,
            onValueChange = { title = it },
            label = { Text("Title") },
            textStyle = MaterialTheme.typography.bodyLarge.auto(),
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(10.dp)
        )
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(
            value = description,
            onValueChange = { description = it },
            label = { Text("Notes") },
            textStyle = MaterialTheme.typography.bodyMedium.auto(),
            minLines = 2,
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(10.dp)
        )

        Spacer(Modifier.height(22.dp))
        MonoLabel("Priority")
        Spacer(Modifier.height(8.dp))
        SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
            val options = listOf("low", "medium", "high")
            options.forEachIndexed { index, pr ->
                SegmentedButton(
                    selected = priority == pr,
                    onClick = { priority = pr },
                    shape = SegmentedButtonDefaults.itemShape(index, options.size)
                ) { Text(pr.replaceFirstChar { it.uppercase() }) }
            }
        }

        Spacer(Modifier.height(22.dp))
        MonoLabel("Group")
        Spacer(Modifier.height(8.dp))
        Row(
            modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            FilterChip(
                selected = selectedGroupId == null,
                onClick = { selectedGroupId = null },
                shape = RoundedCornerShape(10.dp),
                label = { Text("Auto-detect") }
            )
            taskGroups.forEach { group ->
                FilterChip(
                    selected = selectedGroupId == group.id,
                    onClick = { selectedGroupId = group.id },
                    shape = RoundedCornerShape(10.dp),
                    leadingIcon = {
                        Box(
                            Modifier
                                .size(8.dp)
                                .clip(CircleShape)
                                .background(parseGroupColor(group.color, MaterialTheme.colorScheme.primary))
                        )
                    },
                    label = { Text(group.name) }
                )
            }
        }

        Spacer(Modifier.height(22.dp))
        MonoLabel("Remind me")
        Spacer(Modifier.height(4.dp))
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)

        TriggerRow("At a time", timeEnabled, { timeEnabled = it }) {
            val calendar = Calendar.getInstance()
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = {
                    DatePickerDialog(context, { _, y, m, d ->
                        val cur = timeValue.ifEmpty { nowIso() }
                        val timePart = cur.substringAfter("T", "12:00:00")
                        timeValue = "$y-${String.format("%02d", m + 1)}-${String.format("%02d", d)}T$timePart"
                    }, calendar.get(Calendar.YEAR), calendar.get(Calendar.MONTH), calendar.get(Calendar.DAY_OF_MONTH)).show()
                }) { Text("Pick date") }
                OutlinedButton(onClick = {
                    TimePickerDialog(context, { _, h, min ->
                        val cur = timeValue.ifEmpty { nowIso() }
                        val datePart = cur.substringBefore("T", nowIso().substringBefore("T"))
                        timeValue = "${datePart}T${String.format("%02d", h)}:${String.format("%02d", min)}:00"
                    }, calendar.get(Calendar.HOUR_OF_DAY), calendar.get(Calendar.MINUTE), true).show()
                }) { Text("Pick time") }
            }
            if (timeValue.isNotEmpty()) {
                Spacer(Modifier.height(6.dp))
                MonoLabel(timeValue.take(16).replace("T", " · "), color = MaterialTheme.colorScheme.primary)
            }
        }

        TriggerRow("At a place", locationEnabled, { locationEnabled = it }) {
            var zoneExpanded by remember { mutableStateOf(false) }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Box {
                    OutlinedButton(onClick = { zoneExpanded = true }) { Text(locationZone) }
                    DropdownMenu(expanded = zoneExpanded, onDismissRequest = { zoneExpanded = false }) {
                        zones.forEach { z ->
                            DropdownMenuItem(text = { Text(z) }, onClick = {
                                locationZone = z
                                zoneExpanded = false
                            })
                        }
                    }
                }
                FilterChip(
                    selected = locationEvent == "arrive",
                    onClick = { locationEvent = "arrive" },
                    shape = RoundedCornerShape(10.dp),
                    label = { Text("Arrive") }
                )
                FilterChip(
                    selected = locationEvent == "leave",
                    onClick = { locationEvent = "leave" },
                    shape = RoundedCornerShape(10.dp),
                    label = { Text("Leave") }
                )
            }
        }

        TriggerRow("In the car", carEnabled, { carEnabled = it }) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(
                    selected = carEvent == "enter",
                    onClick = { carEvent = "enter" },
                    shape = RoundedCornerShape(10.dp),
                    label = { Text("Enter car") }
                )
                FilterChip(
                    selected = carEvent == "leave",
                    onClick = { carEvent = "leave" },
                    shape = RoundedCornerShape(10.dp),
                    label = { Text("Leave car") }
                )
            }
        }

        TriggerRow("When I wake up", wakeEnabled, { wakeEnabled = it })

        Spacer(Modifier.height(24.dp))

        Button(
            onClick = {
                if (title.isNotBlank()) {
                    val finalTriggers = JSONObject()
                    if (timeEnabled && timeValue.isNotEmpty()) finalTriggers.put("time", timeValue)
                    if (locationEnabled) {
                        finalTriggers.put("location", JSONObject().apply {
                            put("location", locationZone)
                            put("event", locationEvent)
                        })
                    }
                    if (carEnabled) finalTriggers.put("car", carEvent)
                    if (wakeEnabled) finalTriggers.put("wake", true)

                    onSave(
                        title,
                        description.takeIf { it.isNotEmpty() },
                        priority,
                        task?.dueDate?.takeIf { it.isNotEmpty() },
                        finalTriggers.takeIf { finalTriggers.length() > 0 },
                        selectedGroupId
                    )
                }
            },
            enabled = title.isNotBlank(),
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp)
        ) {
            Text("Save", fontWeight = FontWeight.SemiBold)
        }

        Spacer(Modifier.height(40.dp))
    }
}

/** Phone presentation of the editor: bottom sheet around [TaskEditorForm]. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun TaskEditorSheet(
    task: Task? = null,
    zones: List<String>,
    taskGroups: List<TaskGroup> = emptyList(),
    onDismiss: () -> Unit,
    onSave: TaskSaveHandler,
    onDelete: (() -> Unit)? = null,
    onOpenCall: ((String) -> Unit)? = null,
    onAsk: (() -> Unit)? = null
) {
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        dragHandle = { BottomSheetDefaults.DragHandle() },
        containerColor = MaterialTheme.colorScheme.surfaceContainerLow,
        contentColor = MaterialTheme.colorScheme.onSurface
    ) {
        TaskEditorForm(
            task = task,
            zones = zones,
            taskGroups = taskGroups,
            onSave = onSave,
            onDelete = onDelete,
            onOpenCall = onOpenCall,
            onAsk = onAsk,
            modifier = Modifier.fillMaxWidth()
        )
    }
}
