package io.github.ronganam.dana.ui.screens.reminders

import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.ui.components.circuit.LineNode
import io.github.ronganam.dana.ui.components.circuit.LocalNodeSurface
import io.github.ronganam.dana.ui.components.circuit.NodeLine
import io.github.ronganam.dana.ui.components.circuit.NodeShape
import io.github.ronganam.dana.ui.components.circuit.NodeState
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
        prefs.edit().putStringSet(KEY, next).apply()
    }

    private companion object { const val KEY = "kept" }
}

@Composable
fun rememberKeptSuggestions(): KeptSuggestions {
    val context = LocalContext.current.applicationContext
    return remember(context) { KeptSuggestions(context) }
}

/**
 * Dana's suggestion for a reminder snoozed again and again: the two moments
 * as a short line (from → to), the sentence, and Move / Keep at the bottom.
 * [onKeep] null hides Keep (the sheet, where it is just an option).
 */
@Composable
fun SuggestionCard(s: MomentSuggestion, now: LocalDateTime, onMove: () -> Unit, onKeep: (() -> Unit)?, modifier: Modifier = Modifier) {
    val c = circuitColors
    val surface = MaterialTheme.colorScheme.surfaceContainerHighest
    Column(
        modifier.fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(surface).padding(start = 16.dp, end = 16.dp, top = 14.dp, bottom = 10.dp)
    ) {
        CompositionLocalProvider(LocalNodeSurface provides surface) {
            NodeLine(
                listOf(
                    LineNode(NodeShape.Circle, NodeState.Idle, label = momentPhrase(s.from, now), icon = momentIcon(s.from)),
                    LineNode(NodeShape.Circle, NodeState.Firing, label = momentPhrase(s.to, now), icon = momentIcon(s.to))
                ),
                pulseSegment = null,
                labelStyle = MaterialTheme.typography.bodySmall
            )
        }
        Text(s.text, style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.padding(top = 10.dp))
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = onMove,
                colors = ButtonDefaults.buttonColors(containerColor = c.accentFilled, contentColor = c.onAccentFilled)
            ) { Text(s.action) }
            if (onKeep != null) TextButton(onClick = onKeep) { Text("Keep", color = c.ink) }
        }
    }
}
