package app.ganam.assistant.ui.screens.tasks

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.ganam.assistant.data.models.Task
import app.ganam.assistant.ui.components.InkChip
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.components.friendlyDateTime
import app.ganam.assistant.ui.screens.common.DotLabel
import app.ganam.assistant.ui.screens.common.auto
import app.ganam.assistant.ui.theme.Danger

/**
 * Where a reminder came from (e.g. "📞 From the call with Dan · 6 Nov") and a
 * one-tap "Ask Dana" that opens a chat with the reminder attached.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun TaskContextStrip(
    task: Task,
    onOpenCall: (String) -> Unit,
    onAsk: (() -> Unit)?,
    modifier: Modifier = Modifier
) {
    val calls = task.links.filter { it.refType == "call" }
    if (calls.isEmpty() && onAsk == null) return
    FlowRow(modifier, horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        calls.forEach { link ->
            val who = link.contact ?: link.title ?: "a call"
            val date = friendlyDateTime(link.callTimestamp)?.let { " · $it" }.orEmpty()
            InkChip("📞 From the call with $who$date", onClick = { onOpenCall(link.refId) })
        }
        if (onAsk != null) InkChip("💬 Ask Dana", selected = true, onClick = onAsk)
    }
}

/** Read-first detail for a reminder (wide layouts). Editing swaps in [TaskEditorForm]. */
@Composable
internal fun TaskDetailPane(
    task: Task,
    onToggle: (Boolean) -> Unit,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
    modifier: Modifier = Modifier,
    onOpenCall: (String) -> Unit = {},
    onAsk: (() -> Unit)? = null
) {
    val done = task.status == "completed"
    var confirmDelete by remember { mutableStateOf(false) }
    val muted = MaterialTheme.colorScheme.onSurfaceVariant

    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 28.dp, vertical = 20.dp)
            .widthIn(max = 640.dp)
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
            DotLabel(task.priority, priorityColor(task.priority))
            if (task.groupName != null) MonoLabel(task.groupName)
            if (done) MonoLabel("Done", color = MaterialTheme.colorScheme.primary)
        }

        Spacer(Modifier.height(14.dp))

        Text(
            text = task.title,
            style = MaterialTheme.typography.headlineLarge.auto(),
            color = MaterialTheme.colorScheme.onSurface
        )

        taskWhenLabel(task)?.let { whenText ->
            Spacer(Modifier.height(8.dp))
            MonoLabel(whenText, color = MaterialTheme.colorScheme.primary)
        }

        TaskContextStrip(task, onOpenCall = onOpenCall, onAsk = onAsk, modifier = Modifier.padding(top = 14.dp))

        if (!task.description.isNullOrBlank()) {
            Spacer(Modifier.height(20.dp))
            Text(
                text = task.description,
                style = MaterialTheme.typography.bodyLarge.copy(lineHeight = 26.sp, fontSize = 17.sp).auto(),
                color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.9f)
            )
        }

        val triggers = taskTriggerLabels(task)
        if (triggers.isNotEmpty()) {
            Spacer(Modifier.height(28.dp))
            MonoLabel("Remind me")
            Spacer(Modifier.height(6.dp))
            HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.5f))
            triggers.forEach { (type, label) ->
                Row(
                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(triggerIcon(type), contentDescription = null, tint = muted, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(12.dp))
                    Text(label, style = MaterialTheme.typography.bodyLarge.auto())
                }
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            }
        }

        Spacer(Modifier.height(28.dp))

        Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
            if (done) {
                OutlinedButton(onClick = { onToggle(false) }, modifier = Modifier.heightIn(min = 48.dp)) { Text("Reopen") }
            } else {
                Button(onClick = { onToggle(true) }, modifier = Modifier.heightIn(min = 48.dp)) {
                    Text("Mark done", fontWeight = FontWeight.SemiBold)
                }
            }
            FilledTonalButton(onClick = onEdit, modifier = Modifier.heightIn(min = 48.dp)) { Text("Edit") }
            Spacer(Modifier.weight(1f))
            IconButton(onClick = { confirmDelete = true }) {
                Icon(Icons.Filled.Delete, contentDescription = "Delete reminder", tint = muted)
            }
        }

        if (!task.updatedAt.isNullOrBlank()) {
            Spacer(Modifier.height(24.dp))
            MonoLabel("Updated ${task.updatedAt.take(16).replace("T", " · ")}", color = muted.copy(alpha = 0.7f))
        }
    }

    if (confirmDelete) {
        AlertDialog(
            onDismissRequest = { confirmDelete = false },
            title = { Text("Delete reminder") },
            text = { Text("Delete \"${task.title}\"?") },
            confirmButton = {
                TextButton(onClick = {
                    confirmDelete = false
                    onDelete()
                }) { Text("Delete", color = Danger) }
            },
            dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("Cancel") } }
        )
    }
}
