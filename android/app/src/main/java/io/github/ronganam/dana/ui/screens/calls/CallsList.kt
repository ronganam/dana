package io.github.ronganam.dana.ui.screens.calls

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import io.github.ronganam.dana.data.models.CallLabel
import io.github.ronganam.dana.data.models.PhoneCall
import io.github.ronganam.dana.ui.components.InkChip
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule

/**
 * The list pane: search, then calls grouped by day with sticky mono date headers
 * and hairline-ruled rows (no card-per-row).
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun CallListPane(
    days: List<CallDay>,
    selectedId: String?,
    playingId: String?,
    query: String,
    onQueryChange: (String) -> Unit,
    isLoading: Boolean,
    hasAnyCalls: Boolean,
    onSelect: (PhoneCall) -> Unit,
    onUpload: () -> Unit,
    modifier: Modifier = Modifier,
    labels: List<CallLabel> = emptyList(),
    labelFilter: String? = null,
    onLabelFilter: (String?) -> Unit = {},
    hiddenQuick: Int = 0,
    showQuick: Boolean = false,
    onToggleQuick: () -> Unit = {}
) {
    Column(modifier = modifier.fillMaxSize()) {
        CallSearchField(query, onQueryChange)
        if (labels.isNotEmpty() || hiddenQuick > 0 || showQuick) {
            Row(
                Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                InkChip("All", selected = labelFilter == null, onClick = { onLabelFilter(null) })
                labels.forEach { l ->
                    InkChip("${l.emoji} ${l.name}", selected = labelFilter == l.id,
                        onClick = { onLabelFilter(if (labelFilter == l.id) null else l.id) })
                }
                if (hiddenQuick > 0 || showQuick) {
                    InkChip(if (showQuick) "Hide quick calls" else "+$hiddenQuick quick", selected = showQuick, onClick = onToggleQuick)
                }
            }
        }

        when {
            isLoading && !hasAnyCalls -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                MonoLabel("Loading calls…")
            }
            days.isEmpty() -> CallsEmpty(searching = query.isNotBlank(), query = query, onUpload = onUpload)
            else -> LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(bottom = 96.dp)
            ) {
                days.forEach { day ->
                    stickyHeader(key = "day_${day.label}") {
                        Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.background)) {
                            Row(
                                modifier = Modifier.fillMaxWidth().padding(start = 20.dp, end = 20.dp, top = 14.dp, bottom = 6.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                MonoLabel(day.label, color = MaterialTheme.colorScheme.onSurface)
                                MonoLabel("${day.calls.size}")
                            }
                            Rule()
                        }
                    }
                    items(day.calls, key = { it.id }) { call ->
                        CallRow(
                            call = call,
                            selected = call.id == selectedId,
                            playing = call.id == playingId,
                            onClick = { onSelect(call) }
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun CallSearchField(query: String, onQueryChange: (String) -> Unit) {
    OutlinedTextField(
        value = query,
        onValueChange = onQueryChange,
        singleLine = true,
        placeholder = { Text("Search calls…", maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis) },
        leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
        trailingIcon = {
            if (query.isNotEmpty()) {
                IconButton(onClick = { onQueryChange("") }) {
                    Icon(Icons.Filled.Close, contentDescription = "Clear search")
                }
            }
        },
        shape = RoundedCornerShape(14.dp),
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = MaterialTheme.colorScheme.primary,
            unfocusedBorderColor = MaterialTheme.colorScheme.outlineVariant,
            focusedContainerColor = MaterialTheme.colorScheme.background,
            unfocusedContainerColor = MaterialTheme.colorScheme.background
        ),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 8.dp)
    )
}

/** One call: serif monogram tile, name + title, mono time/length. Selected = tinted row with a vermilion edge. */
@Composable
private fun CallRow(call: PhoneCall, selected: Boolean, playing: Boolean, onClick: () -> Unit) {
    val accent = MaterialTheme.colorScheme.primary
    val tint = MaterialTheme.colorScheme.surfaceContainerHigh
    Column {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 72.dp)
                .drawBehind {
                    if (selected) {
                        drawRect(tint)
                        drawRect(accent, Offset.Zero, Size(3.dp.toPx(), size.height))
                    }
                }
                .clickable(onClick = onClick)
                .padding(horizontal = 20.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Monogram(call.contactName)
            Spacer(Modifier.width(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = call.contactName,
                    style = MaterialTheme.typography.titleSmall,
                    color = MaterialTheme.colorScheme.onSurface,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                Spacer(Modifier.height(2.dp))
                when (call.status) {
                    "processing" -> MonoLabel("Analysing…", color = accent)
                    "failed" -> MonoLabel("Couldn't analyse", color = io.github.ronganam.dana.ui.theme.Danger)
                    else -> RtlSupportingText(
                        text = call.displayTitle(),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodySmall.copy(),
                        maxLines = 1
                    )
                }
                if (call.labels.isNotEmpty()) {
                    Spacer(Modifier.height(3.dp))
                    Text(
                        call.labels.joinToString("   ") { "${it.emoji} ${it.name}" },
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
            Spacer(Modifier.width(10.dp))
            Column(horizontalAlignment = Alignment.End) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    if (call.pendingSuggestions > 0) {
                        Box(Modifier.size(7.dp).clip(CircleShape).background(accent))
                        Spacer(Modifier.width(6.dp))
                    }
                    MonoLabel(call.timeLabel(), color = if (playing) accent else MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (call.durationSeconds > 0) {
                    Spacer(Modifier.height(2.dp))
                    MonoLabel(formatDuration(call.durationSeconds))
                }
                if (playing) {
                    Spacer(Modifier.height(4.dp))
                    Box(Modifier.size(6.dp).clip(CircleShape).background(accent))
                }
            }
        }
        Rule(Modifier.padding(start = 72.dp))
    }
}

/** Hairline-bordered serif initial — deliberately not a coloured circle avatar. */
@Composable
internal fun Monogram(name: String, size: androidx.compose.ui.unit.Dp = 40.dp) {
    Box(
        modifier = Modifier
            .size(size)
            .clip(RoundedCornerShape(10.dp))
            .border(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.6f), RoundedCornerShape(10.dp)),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = monogram(name),
            style = MaterialTheme.typography.headlineSmall,
            color = MaterialTheme.colorScheme.onSurface
        )
    }
}

/** Serif statement instead of an icon-in-a-circle. */
@Composable
internal fun CallsEmpty(searching: Boolean, query: String, onUpload: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxSize().padding(horizontal = 28.dp, vertical = 40.dp),
        verticalArrangement = Arrangement.Center
    ) {
        MonoLabel(if (searching) "No matches" else "Call log", color = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.height(8.dp))
        Text(
            text = if (searching) "No call mentions “${query.trim()}”." else "Nothing recorded yet.",
            style = MaterialTheme.typography.displaySmall,
            fontStyle = FontStyle.Italic,
            color = MaterialTheme.colorScheme.onSurface
        )
        Spacer(Modifier.height(10.dp))
        Text(
            text = if (searching) "Try a name, a topic, or a word from the transcript."
            else "Recorded calls are transcribed, summarised and filed here.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        if (!searching) {
            Spacer(Modifier.height(12.dp))
            TextButton(onClick = onUpload, modifier = Modifier.heightIn(min = 48.dp)) {
                Text("Upload a recording")
            }
        }
    }
}

/** Shown in the detail pane when nothing is selected. */
@Composable
internal fun PickACall(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
        Text(
            text = "Pick a call.",
            style = MaterialTheme.typography.displaySmall,
            fontStyle = FontStyle.Italic,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center
        )
    }
}
