package app.ganam.assistant.ui.screens.memory

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.ganam.assistant.data.models.Memory
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.screens.common.DotLabel
import app.ganam.assistant.ui.screens.common.auto

/** One hairline-ruled index entry: category bar, topic, two-line preview, mono category tag. */
@Composable
internal fun MemoryRow(
    memory: Memory,
    selected: Boolean,
    onClick: () -> Unit
) {
    val color = memoryCategoryColor(memory.category)
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(if (selected) MaterialTheme.colorScheme.surfaceContainerHigh else Color.Transparent)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(IntrinsicSize.Min)
                .clickable(onClick = onClick)
        ) {
            Box(
                Modifier
                    .width(3.dp)
                    .fillMaxHeight()
                    .background(color.copy(alpha = if (selected) 1f else 0.55f))
            )
            Column(
                modifier = Modifier
                    .weight(1f)
                    .heightIn(min = 64.dp)
                    .padding(horizontal = 17.dp, vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(3.dp)
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = memoryTitle(memory.key),
                        style = MaterialTheme.typography.titleMedium.auto(),
                        color = MaterialTheme.colorScheme.onSurface,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f)
                    )
                    Spacer(Modifier.width(8.dp))
                    DotLabel(categoryLabel(memory.category), color)
                }
                if (memory.value.isNotBlank()) {
                    Text(
                        text = memoryValueDisplay(memory.value),
                        style = MaterialTheme.typography.bodySmall.auto(),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
        }
        HorizontalDivider(modifier = Modifier.padding(start = 20.dp), color = MaterialTheme.colorScheme.outlineVariant)
    }
}

/** Big serif initial that opens each alphabetical group. */
@Composable
internal fun LetterHeader(letter: String) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Text(
            text = letter,
            style = MaterialTheme.typography.headlineMedium,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(start = 20.dp, top = 14.dp, bottom = 4.dp)
        )
        HorizontalDivider(
            modifier = Modifier.padding(horizontal = 20.dp),
            color = MaterialTheme.colorScheme.outline.copy(alpha = 0.5f)
        )
    }
}

@Composable
internal fun MemorySearchField(
    value: String,
    onChange: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        placeholder = { Text("Search topic or content") },
        leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
        trailingIcon = {
            if (value.isNotEmpty()) {
                IconButton(onClick = { onChange("") }) { Icon(Icons.Filled.Close, contentDescription = "Clear search") }
            }
        },
        singleLine = true,
        shape = RoundedCornerShape(10.dp),
        modifier = modifier.fillMaxWidth()
    )
}

/** Compact/medium header: search + a single row of type chips. */
@Composable
internal fun MemoryFilterHeader(
    searchQuery: String,
    onSearchChange: (String) -> Unit,
    categories: List<String>,
    categoryCounts: Map<String, Int>,
    total: Int,
    categoryFilter: String,
    onSelectCategory: (String) -> Unit,
    showChips: Boolean
) {
    Column(modifier = Modifier.fillMaxWidth().padding(top = 4.dp)) {
        MemorySearchField(searchQuery, onSearchChange, Modifier.padding(horizontal = 20.dp))
        if (showChips && categories.isNotEmpty()) {
            LazyRow(
                contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                item {
                    FilterChip(
                        selected = categoryFilter.isEmpty(),
                        onClick = { onSelectCategory("") },
                        shape = RoundedCornerShape(10.dp),
                        label = { ChipLabel("All", total) }
                    )
                }
                items(categories) { cat ->
                    val selected = categoryFilter.equals(cat, ignoreCase = true)
                    FilterChip(
                        selected = selected,
                        // Tapping the active chip clears the filter (back to All).
                        onClick = { onSelectCategory(if (selected) "" else cat) },
                        shape = RoundedCornerShape(10.dp),
                        leadingIcon = {
                            Box(Modifier.size(8.dp).clip(CircleShape).background(memoryCategoryColor(cat)))
                        },
                        label = { ChipLabel(categoryLabel(cat), categoryCounts[cat] ?: 0) }
                    )
                }
            }
        } else {
            Spacer(Modifier.height(8.dp))
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
    }
}

@Composable
private fun ChipLabel(text: String, count: Int) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(text, maxLines = 1)
        Spacer(Modifier.width(6.dp))
        MonoLabel(count.toString())
    }
}

/** Expanded layout's left pane: the type index. */
@Composable
internal fun MemoryCategoryPane(
    categories: List<String>,
    categoryCounts: Map<String, Int>,
    total: Int,
    categoryFilter: String,
    onSelectCategory: (String) -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 8.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp)
    ) {
        MonoLabel("Types", modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
        CategoryRow("All", total, null, categoryFilter.isEmpty()) { onSelectCategory("") }
        categories.forEach { cat ->
            CategoryRow(
                label = categoryLabel(cat),
                count = categoryCounts[cat] ?: 0,
                color = memoryCategoryColor(cat),
                selected = categoryFilter.equals(cat, ignoreCase = true)
            ) { onSelectCategory(if (categoryFilter.equals(cat, ignoreCase = true)) "" else cat) }
        }
    }
}

@Composable
private fun CategoryRow(label: String, count: Int, color: Color?, selected: Boolean, onClick: () -> Unit) {
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
        if (color != null) {
            Box(Modifier.size(8.dp).clip(CircleShape).background(color))
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
        MonoLabel(count.toString())
    }
}
