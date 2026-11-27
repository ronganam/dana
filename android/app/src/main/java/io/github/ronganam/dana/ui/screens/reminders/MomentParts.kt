package io.github.ronganam.dana.ui.screens.reminders

import android.content.Context
import androidx.core.content.edit
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.ArrowDownward
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.material3.Icon
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.DirectionsCar
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Place
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.WbSunny
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.ui.theme.circuitColors
import java.time.LocalDateTime

/** The glyph inside a moment's circle. */
fun momentIcon(w: RemindWhen): ImageVector? = when (w) {
    RemindWhen.None -> null
    is RemindWhen.At -> Icons.Outlined.Schedule
    is RemindWhen.Place -> if (isHomeZone(w.zone)) Icons.Outlined.Home else Icons.Outlined.Place
    is RemindWhen.Car -> Icons.Outlined.DirectionsCar
    RemindWhen.Wake -> Icons.Outlined.WbSunny
}

/**
 * The suggestions the owner chose to keep, per reminder and snooze count: a
 * kept one leaves the list (the row still says how often it was snoozed, and
 * the moment sheet still offers it); it comes back after the next snooze.
 */
class KeptSuggestions(context: Context) {
    private val prefs = context.getSharedPreferences("reminder_suggestions", Context.MODE_PRIVATE)
    private val state = mutableStateOf(prefs.getStringSet(KEY, emptySet()).orEmpty())

    fun isKept(task: Task): Boolean = "${task.id}:${task.snoozeCount}" in state.value

    fun keep(task: Task) {
        val next = state.value.filterNot { it.startsWith("${task.id}:") }.toSet() + "${task.id}:${task.snoozeCount}"
        state.value = next
        prefs.edit { putStringSet(KEY, next) }
    }

    private companion object { const val KEY = "kept" }
}

@Composable
fun rememberKeptSuggestions(): KeptSuggestions {
    val context = LocalContext.current.applicationContext
    return remember(context) { KeptSuggestions(context) }
}

/**
 * Dana's suggestion for a reminder snoozed again and again, built to fit any
 * width (a narrow list pane, the cover screen): the question on its own
 * lines, the two moments as chips that wrap (from → to), then Move / Keep,
 * which wrap too. [onKeep] null hides Keep (the sheet, where it is just an
 * option).
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SuggestionCard(s: MomentSuggestion, now: LocalDateTime, onMove: () -> Unit, onKeep: (() -> Unit)?, modifier: Modifier = Modifier) {
    val c = circuitColors
    val surface = MaterialTheme.colorScheme.surfaceContainerHighest
    Column(
        modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(surface).padding(start = 14.dp, end = 10.dp, top = 12.dp, bottom = 8.dp)
    ) {
        Row(verticalAlignment = Alignment.Top) {
            Icon(Icons.Outlined.AutoAwesome, contentDescription = null, tint = c.accent, modifier = Modifier.padding(top = 2.dp, end = 8.dp).size(16.dp))
            Text(s.text, style = MaterialTheme.typography.bodyMedium, color = c.ink)
        }
        // Side by side when they fit, else one under the other.
        BoxWithConstraints(Modifier.padding(top = 10.dp).fillMaxWidth()) {
            if (maxWidth >= 300.dp) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                MomentChip(s.from, now, on = false, modifier = Modifier.weight(1f, fill = false))
                Icon(Icons.AutoMirrored.Outlined.ArrowForward, contentDescription = "to", tint = c.mute, modifier = Modifier.size(16.dp))
                MomentChip(s.to, now, on = true, modifier = Modifier.weight(1f, fill = false))
            } else Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                MomentChip(s.from, now, on = false)
                Icon(Icons.Outlined.ArrowDownward, contentDescription = "to", tint = c.mute, modifier = Modifier.padding(start = 12.dp).size(14.dp))
                MomentChip(s.to, now, on = true)
            }
        }
        FlowRow(
            Modifier.padding(top = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(4.dp),
            itemVerticalAlignment = Alignment.CenterVertically
        ) {
            Button(
                onClick = onMove,
                colors = ButtonDefaults.buttonColors(containerColor = c.accentFilled, contentColor = c.onAccentFilled),
                contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp)
            ) { Text(s.action, maxLines = 2, textAlign = TextAlign.Center) }
            if (onKeep != null) TextButton(onClick = onKeep) { Text("Keep it", color = c.ink, maxLines = 1) }
        }
    }
}

/** One moment as a small chip: its glyph and its words; [on] = the suggested one (accent). */
@Composable
private fun MomentChip(w: RemindWhen, now: LocalDateTime, on: Boolean, modifier: Modifier = Modifier) {
    val c = circuitColors
    val fg = if (on) c.onAccentTonal else c.inkSoft
    Row(
        modifier.clip(RoundedCornerShape(10.dp))
            .background(if (on) c.accentTonal else MaterialTheme.colorScheme.surfaceContainerHigh)
            .heightIn(min = 30.dp)
            .padding(horizontal = 10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        momentIcon(w)?.let { Icon(it, contentDescription = null, tint = fg, modifier = Modifier.padding(end = 6.dp).size(15.dp)) }
        Text(momentPhrase(w, now), style = MaterialTheme.typography.labelLarge, color = fg, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(vertical = 5.dp))
    }
}
