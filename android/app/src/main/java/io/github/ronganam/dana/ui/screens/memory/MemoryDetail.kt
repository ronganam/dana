package io.github.ronganam.dana.ui.screens.memory

import io.github.ronganam.dana.ui.components.rememberCopyText
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.models.Memory
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.screens.common.DotLabel
import io.github.ronganam.dana.ui.screens.common.auto
import io.github.ronganam.dana.ui.theme.Danger
import io.github.ronganam.dana.R

/** Read-first view of one memory: serif topic, generous selectable body, quiet actions. */
@Composable
internal fun MemoryDetailPane(
    memory: Memory,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
    modifier: Modifier = Modifier
) {
    val copy = rememberCopyText()
    val context = LocalContext.current
    var confirmDelete by remember { mutableStateOf(false) }
    val muted = MaterialTheme.colorScheme.onSurfaceVariant

    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 28.dp, vertical = 20.dp)
            .widthIn(max = 640.dp)
    ) {
        DotLabel(categoryLabel(memory.category), memoryCategoryColor(memory.category))
        Spacer(Modifier.height(14.dp))
        Text(
            text = memoryTitle(memory.key),
            style = MaterialTheme.typography.headlineLarge.auto(),
            color = MaterialTheme.colorScheme.onSurface
        )
        Spacer(Modifier.height(16.dp))
        HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.5f))
        Spacer(Modifier.height(18.dp))
        SelectionContainer {
            Text(
                text = memoryValueDisplay(memory.value),
                style = MaterialTheme.typography.bodyLarge.copy(fontSize = 17.sp, lineHeight = 27.sp).auto(),
                color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.92f)
            )
        }

        Spacer(Modifier.height(28.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
            Button(onClick = onEdit, modifier = Modifier.heightIn(min = 48.dp)) {
                Text("Edit", fontWeight = FontWeight.SemiBold)
            }
            FilledTonalButton(
                onClick = {
                    copy(memory.value)
                    android.widget.Toast.makeText(context, R.string.toast_copied, android.widget.Toast.LENGTH_SHORT).show()
                },
                modifier = Modifier.heightIn(min = 48.dp)
            ) { Text("Copy") }
            Spacer(Modifier.weight(1f))
            IconButton(onClick = { confirmDelete = true }) {
                Icon(Icons.Filled.Delete, contentDescription = "Delete memory", tint = muted)
            }
        }

        if (!memory.updatedAt.isNullOrBlank()) {
            Spacer(Modifier.height(24.dp))
            MonoLabel("Updated ${memory.updatedAt.take(16).replace("T", " · ")}", color = muted.copy(alpha = 0.7f))
        }
    }

    if (confirmDelete) {
        AlertDialog(
            onDismissRequest = { confirmDelete = false },
            title = { Text("Delete memory") },
            text = { Text("Forget \"${memoryTitle(memory.key)}\"?") },
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

/** Create / edit form, used inline (wide) and inside the phone sheet. Scrolls by itself. */
@Composable
internal fun MemoryEditorForm(
    memory: Memory?,
    availableCategories: List<String>,
    onSave: (key: String, value: String, category: String) -> Unit,
    modifier: Modifier = Modifier,
    onDelete: (() -> Unit)? = null,
    onCancel: (() -> Unit)? = null
) {
    var key by remember(memory?.key) { mutableStateOf(memory?.key ?: "") }
    var value by remember(memory?.key) { mutableStateOf(memory?.value ?: "") }
    var category by remember(memory?.key) { mutableStateOf(memory?.category ?: "general") }

    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            Text(
                text = if (memory == null) "New memory" else "Edit memory",
                style = MaterialTheme.typography.headlineMedium,
                modifier = Modifier.weight(1f)
            )
            if (onDelete != null) {
                IconButton(onClick = onDelete) { Icon(Icons.Filled.Delete, "Delete", tint = Danger) }
            }
            if (onCancel != null) TextButton(onClick = onCancel) { Text("Cancel") }
        }

        Spacer(Modifier.height(16.dp))

        OutlinedTextField(
            value = key,
            onValueChange = { key = it },
            label = { Text("Topic (e.g. WiFi password)") },
            textStyle = MaterialTheme.typography.bodyLarge.auto(),
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(10.dp)
        )
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(
            value = value,
            onValueChange = { value = it },
            label = { Text("Content") },
            textStyle = MaterialTheme.typography.bodyLarge.auto(),
            minLines = 4,
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(10.dp)
        )

        Spacer(Modifier.height(22.dp))
        MonoLabel("Type")
        Spacer(Modifier.height(8.dp))
        // Every existing type plus the defaults is selectable.
        val typeChips = remember(availableCategories, category) {
            (listOf("general", "personal", "work") + availableCategories + category).distinctBy { it.lowercase() }
        }
        Row(
            modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            typeChips.forEach { cat ->
                FilterChip(
                    selected = category.equals(cat, ignoreCase = true),
                    onClick = { category = cat },
                    shape = RoundedCornerShape(10.dp),
                    leadingIcon = {
                        Box(Modifier.size(8.dp).clip(CircleShape).background(memoryCategoryColor(cat)))
                    },
                    label = { Text(categoryLabel(cat)) }
                )
            }
        }

        Spacer(Modifier.height(24.dp))
        Button(
            onClick = { if (key.isNotBlank() && value.isNotBlank()) onSave(key, value, category) },
            enabled = key.isNotBlank() && value.isNotBlank(),
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp)
        ) { Text("Save", fontWeight = FontWeight.SemiBold) }
        Spacer(Modifier.height(40.dp))
    }
}

/** Phone presentation: bottom sheet that reads first, then flips to the form on Edit / New. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun MemorySheet(
    memory: Memory?,
    startEditing: Boolean,
    availableCategories: List<String>,
    onDismiss: () -> Unit,
    onSave: (key: String, value: String, category: String) -> Unit,
    onDelete: () -> Unit
) {
    var editing by rememberSaveable { mutableStateOf(startEditing || memory == null) }
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        dragHandle = { BottomSheetDefaults.DragHandle() },
        containerColor = MaterialTheme.colorScheme.surfaceContainerLow
    ) {
        if (memory != null && !editing) {
            MemoryDetailPane(
                memory = memory,
                onEdit = { editing = true },
                onDelete = onDelete,
                modifier = Modifier.fillMaxWidth()
            )
        } else {
            MemoryEditorForm(
                memory = memory,
                availableCategories = availableCategories,
                onSave = onSave,
                onDelete = if (memory != null) onDelete else null,
                modifier = Modifier.fillMaxWidth()
            )
        }
    }
}
