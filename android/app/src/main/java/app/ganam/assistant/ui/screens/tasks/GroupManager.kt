package app.ganam.assistant.ui.screens.tasks

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import app.ganam.assistant.data.models.TaskGroup
import app.ganam.assistant.ui.theme.Danger

private val groupEmojiOptions = listOf("\uD83D\uDCC1", "\uD83D\uDCBC", "\uD83C\uDFE0", "\uD83D\uDED2", "\u2764\uFE0F", "\uD83D\uDC64", "\uD83C\uDF93", "\uD83D\uDCAA", "\uD83C\uDFB5", "\uD83C\uDFAE", "\u2708\uFE0F", "\uD83C\uDFD6\uFE0F", "\uD83C\uDFCB\uFE0F", "\uD83D\uDCDA", "\uD83C\uDFA8", "\uD83D\uDEE0\uFE0F", "\uD83D\uDCB0", "\uD83C\uDF3F", "\uD83D\uDC3E", "\uD83C\uDFAD", "\uD83D\uDCF7", "\uD83C\uDFAF", "\uD83D\uDCA1", "\uD83D\uDD2C", "\uD83C\uDFC6", "\uD83C\uDFAA")
private val groupColorOptions = listOf("#6366f1", "#ef4444", "#10b981", "#f59e0b", "#3b82f6", "#a855f7", "#ec4899", "#14b8a6", "#f97316", "#8b5cf6", "#06b6d4", "#84cc16")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GroupManagerSheet(
    groups: List<TaskGroup>,
    onDismiss: () -> Unit,
    onCreateGroup: (name: String, emoji: String, color: String, description: String) -> Unit,
    onUpdateGroup: (id: String, name: String, emoji: String, color: String, description: String) -> Unit,
    onDeleteGroup: (id: String) -> Unit
) {
    var addingGroup by remember { mutableStateOf(false) }
    var editingGroup by remember { mutableStateOf<TaskGroup?>(null) }
    var deleteConfirmId by remember { mutableStateOf<String?>(null) }

    val isGeneral = { group: TaskGroup -> group.id == "general" }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        dragHandle = { BottomSheetDefaults.DragHandle() },
        containerColor = MaterialTheme.colorScheme.surfaceColorAtElevation(1.dp),
        contentColor = MaterialTheme.colorScheme.onSurface
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp)
                .verticalScroll(rememberScrollState())
        ) {
            Text(
                text = "Manage groups",
                style = MaterialTheme.typography.headlineMedium,
                modifier = Modifier.padding(bottom = 16.dp)
            )

            groups.forEach { group ->
                val isLocked = isGeneral(group)
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable(enabled = !isLocked) { editingGroup = group }
                        .padding(vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    val groupColor = try { Color(android.graphics.Color.parseColor(group.color)) } catch (e: Exception) { MaterialTheme.colorScheme.primary }
                    Box(
                        modifier = Modifier
                            .size(32.dp)
                            .clip(CircleShape)
                            .background(groupColor),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(text = group.emoji, style = MaterialTheme.typography.titleSmall)
                    }

                    Spacer(modifier = Modifier.width(12.dp))

                    Column(modifier = Modifier.weight(1f)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                text = group.name,
                                fontWeight = FontWeight.SemiBold,
                                style = MaterialTheme.typography.bodyLarge
                            )
                            if (isLocked) {
                                Spacer(modifier = Modifier.width(4.dp))
                                Text(
                                    "\uD83D\uDD12",
                                    style = MaterialTheme.typography.bodySmall
                                )
                            }
                        }
                        if (group.taskCount != null) {
                            Text(
                                text = "${group.taskCount} tasks",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
                            )
                        }
                    }

                    if (!isLocked) {
                        IconButton(onClick = { deleteConfirmId = group.id }) {
                            Icon(
                                Icons.Default.Delete,
                                "Delete Group",
                                tint = Danger
                            )
                        }
                    }
                }

                if (deleteConfirmId == group.id) {
                    AlertDialog(
                        onDismissRequest = { deleteConfirmId = null },
                        title = { Text("Delete Group") },
                        text = { Text("Delete \"${group.name}\"? Tasks in this group will not be deleted.") },
                        confirmButton = {
                            TextButton(onClick = {
                                onDeleteGroup(group.id)
                                deleteConfirmId = null
                            }) {
                                Text("Delete", color = Danger)
                            }
                        },
                        dismissButton = {
                            TextButton(onClick = { deleteConfirmId = null }) {
                                Text("Cancel")
                            }
                        }
                    )
                }
            }

            Spacer(modifier = Modifier.height(8.dp))

            Button(
                onClick = { addingGroup = true },
                modifier = Modifier.fillMaxWidth()
            ) {
                Icon(Icons.Default.Add, "Add", modifier = Modifier.size(18.dp))
                Spacer(modifier = Modifier.width(4.dp))
                Text("Add Group")
            }

            Spacer(modifier = Modifier.height(24.dp))
        }
    }

    if (addingGroup) {
        GroupFormDialog(
            title = "New Group",
            onDismiss = { addingGroup = false },
            onSave = { name, emoji, color, description ->
                onCreateGroup(name, emoji, color, description)
                addingGroup = false
            }
        )
    }

    editingGroup?.let { group ->
        GroupFormDialog(
            title = "Edit Group",
            initialName = group.name,
            initialEmoji = group.emoji,
            initialColor = group.color,
            initialDescription = group.description ?: "",
            onDismiss = { editingGroup = null },
            onSave = { name, emoji, color, description ->
                onUpdateGroup(group.id, name, emoji, color, description)
                editingGroup = null
            }
        )
    }
}

@Composable
fun GroupFormDialog(
    title: String,
    initialName: String = "",
    initialEmoji: String = "\uD83D\uDCC1",
    initialColor: String = "#6366f1",
    initialDescription: String = "",
    onDismiss: () -> Unit,
    onSave: (name: String, emoji: String, color: String, description: String) -> Unit
) {
    var name by remember { mutableStateOf(initialName) }
    var emoji by remember { mutableStateOf(initialEmoji) }
    var color by remember { mutableStateOf(initialColor) }
    var description by remember { mutableStateOf(initialDescription) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title, fontWeight = FontWeight.Bold) },
        text = {
            Column {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Name") },
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(6.dp)
                )

                Spacer(modifier = Modifier.height(12.dp))

                Text("Emoji", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
                Spacer(modifier = Modifier.height(4.dp))
                LazyRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    items(groupEmojiOptions) { e ->
                        Box(
                            modifier = Modifier
                                .size(36.dp)
                                .clip(CircleShape)
                                .background(if (emoji == e) MaterialTheme.colorScheme.primary.copy(alpha = 0.2f) else Color.Transparent)
                                .clickable { emoji = e },
                            contentAlignment = Alignment.Center
                        ) {
                            Text(text = e, style = MaterialTheme.typography.titleMedium)
                        }
                    }
                }

                Spacer(modifier = Modifier.height(12.dp))

                Text("Color", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
                Spacer(modifier = Modifier.height(4.dp))
                LazyRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    items(groupColorOptions) { c ->
                        val parsedColor = try { Color(android.graphics.Color.parseColor(c)) } catch (e: Exception) { MaterialTheme.colorScheme.primary }
                        Box(
                            modifier = Modifier
                                .size(28.dp)
                                .clip(CircleShape)
                                .background(parsedColor)
                                .border(if (color == c) 2.dp else 0.dp, MaterialTheme.colorScheme.onSurface, CircleShape)
                                .clickable { color = c }
                        )
                    }
                }

                Spacer(modifier = Modifier.height(12.dp))

                OutlinedTextField(
                    value = description,
                    onValueChange = { description = it },
                    label = { Text("Description (optional)") },
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(6.dp)
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    if (name.isNotBlank()) {
                        onSave(name.trim(), emoji, color, description.trim())
                    }
                },
                enabled = name.isNotBlank()
            ) {
                Text("Save")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("Cancel")
            }
        }
    )
}
