package io.github.ronganam.dana.ui.components

import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.models.ModelOption

/**
 * The model's streamed thought summary, shown above its reply. Open while the
 * model is still thinking (no answer text yet), folded once the answer starts;
 * the user can toggle it either way.
 */
@Composable
fun ReasoningBlock(
    text: String,
    thinking: Boolean,
    modifier: Modifier = Modifier
) {
    var userChoice by rememberSaveable { mutableStateOf<Boolean?>(null) }
    val expanded = userChoice ?: thinking
    val muted = MaterialTheme.colorScheme.onSurfaceVariant

    Column(modifier = modifier.animateContentSize()) {
        Row(
            modifier = Modifier
                .clickable { userChoice = !expanded }
                .padding(vertical = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp)
        ) {
            Icon(
                if (expanded) Icons.Filled.KeyboardArrowDown else Icons.AutoMirrored.Filled.KeyboardArrowRight,
                contentDescription = if (expanded) "Hide thinking" else "Show thinking",
                tint = muted,
                modifier = Modifier.size(16.dp)
            )
            Text(
                text = if (thinking) "Thinking…" else "Thought process",
                style = MaterialTheme.typography.labelMedium,
                color = muted
            )
        }
        if (expanded) {
            val scroll = rememberScrollState()
            // Keep the newest thought in view while it streams.
            LaunchedEffect(text.length) { if (thinking) scroll.scrollTo(scroll.maxValue) }
            Box(
                modifier = Modifier
                    .heightIn(max = 180.dp)
                    .verticalScroll(scroll)
                    .padding(start = 20.dp, top = 2.dp, bottom = 6.dp)
            ) {
                Text(
                    text = text.replace("**", ""),
                    style = MaterialTheme.typography.bodySmall,
                    fontStyle = FontStyle.Italic,
                    color = muted
                )
            }
        }
    }
}

/** Small caption under the typing dots ("Fast · Step 2 of 10"). */
@Composable
fun ThinkingCaption(text: String, modifier: Modifier = Modifier) {
    if (text.isEmpty()) return
    Text(
        text = text,
        fontSize = 11.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = modifier.padding(start = 8.dp, bottom = 2.dp)
    )
}

/** Compact chip that picks the chat model from the server catalog. */
@Composable
fun ModelPickerChip(
    models: List<ModelOption>,
    selectedId: String,
    onSelect: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    if (models.isEmpty()) return
    var open by remember { mutableStateOf(false) }
    val selected = models.firstOrNull { it.id == selectedId } ?: models.first()

    Box(modifier = modifier) {
        Surface(
            shape = RoundedCornerShape(16.dp),
            color = MaterialTheme.colorScheme.surfaceContainerHigh,
            modifier = Modifier.clickable { open = true }
        ) {
            Row(
                modifier = Modifier.padding(start = 10.dp, end = 4.dp, top = 6.dp, bottom = 6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = selected.label,
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onSurface
                )
                Icon(
                    Icons.Filled.KeyboardArrowDown,
                    contentDescription = "Choose model",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(18.dp)
                )
            }
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            models.forEach { option ->
                DropdownMenuItem(
                    text = {
                        Column {
                            Text(option.label, style = MaterialTheme.typography.bodyMedium)
                            if (option.description.isNotEmpty()) {
                                Text(
                                    option.description,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                )
                            }
                        }
                    },
                    onClick = {
                        open = false
                        onSelect(option.id)
                    }
                )
            }
        }
    }
}
