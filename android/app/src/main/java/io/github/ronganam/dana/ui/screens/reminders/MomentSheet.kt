package io.github.ronganam.dana.ui.screens.reminders

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.data.models.Triggers
import io.github.ronganam.dana.data.models.ZoneItem
import io.github.ronganam.dana.ui.components.circuit.LineNode
import io.github.ronganam.dana.ui.components.circuit.LocalNodeSurface
import io.github.ronganam.dana.ui.components.circuit.NodeLine
import io.github.ronganam.dana.ui.components.circuit.NodeShape
import io.github.ronganam.dana.ui.components.circuit.NodeState
import io.github.ronganam.dana.ui.components.circuit.SectionHeader
import io.github.ronganam.dana.ui.shell.rememberNow
import io.github.ronganam.dana.ui.theme.circuitColors
import java.time.LocalDateTime

/**
 * The moment picker: a sheet that sets when a reminder reaches you, with the
 * moments the server has — one saved place (arrive or leave), waking up,
 * the car (getting in or out) and a time — any of them together (whichever
 * comes first). The current moments head it as a line with the sentence
 * that says what will happen. Every tap saves ([onTriggers]); Done closes.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun MomentSheet(
    task: Task,
    places: List<ZoneItem>,
    zones: List<String>,
    listName: String?,
    onTriggers: (Triggers) -> Unit,
    onEdit: () -> Unit,
    onDismiss: () -> Unit
) {
    val c = circuitColors
    val now by rememberNow()
    val context = LocalContext.current
    val sheetSurface = MaterialTheme.colorScheme.surfaceContainer
    val group = MaterialTheme.colorScheme.surfaceContainerHigh
    val t = task.triggers
    val whens = remindWhens(task)
    fun set(next: Triggers) = onTriggers(next)
    val suggestion = remember(task, zones) { snoozeSuggestion(task, zones) }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = sheetSurface
    ) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp).padding(bottom = 16.dp)) {
            // Title, its list and due date; edit the rest; close.
            Row(verticalAlignment = Alignment.Top) {
                Column(Modifier.weight(1f).padding(start = 8.dp, top = 4.dp)) {
                    Text(task.title, style = MaterialTheme.typography.titleLarge.copy(textDirection = TextDirection.Content), color = c.ink, maxLines = 3, overflow = TextOverflow.Ellipsis)
                    val sub = sheetSubtitle(task, listName)
                    if (sub.isNotEmpty()) Text(sub, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft)
                }
                IconButton(onClick = onEdit) { Icon(Icons.Outlined.Edit, contentDescription = "Edit the reminder", tint = c.inkSoft) }
                IconButton(onClick = onDismiss) { Icon(Icons.Outlined.Close, contentDescription = "Close", tint = c.inkSoft) }
            }

            // What reaches you now.
            Column(Modifier.padding(top = 12.dp).fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(group).padding(16.dp)) {
                if (whens.isNotEmpty()) CompositionLocalProvider(LocalNodeSurface provides group) {
                    NodeLine(
                        whens.map { LineNode(NodeShape.Circle, NodeState.Done, label = momentPhrase(it, now), icon = momentIcon(it)) },
                        modifier = Modifier.padding(bottom = 10.dp),
                        pulseSegment = null,
                        labelStyle = MaterialTheme.typography.bodySmall
                    )
                }
                Text(
                    sentence(task, now) + if (whens.size > 1) " Whichever comes first." else "",
                    style = MaterialTheme.typography.bodyMedium,
                    color = c.ink
                )
            }

            if (suggestion != null) SuggestionCard(suggestion, now, onMove = { set(t.applying(suggestion)) }, onKeep = null, modifier = Modifier.padding(top = 12.dp))

            // Place: one saved place, arriving or leaving.
            val placeList = places.map { it.name to it.isHome }.ifEmpty { zones.map { it to isHomeZone(it) } }
            val loc = whens.filterIsInstance<RemindWhen.Place>().firstOrNull()
            SectionHeader("Place", modifier = Modifier.padding(top = 20.dp, start = 8.dp))
            SheetGroup(group) {
                if (placeList.isEmpty()) Text(
                    "No saved places yet. Add them in Dana's settings.",
                    style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(16.dp)
                )
                placeList.forEach { (name, home) ->
                    val here = loc?.zone?.equals(name, ignoreCase = true) == true
                    SheetRow(
                        icon = momentIcon(RemindWhen.Place(if (home) "home" else name, false)),
                        title = name.replaceFirstChar { it.titlecase() }
                    ) {
                        val arriving = here && loc?.leave == false
                        val leaving = here && loc?.leave == true
                        val arrive = RemindWhen.Place(name, leave = false)
                        val leave = RemindWhen.Place(name, leave = true)
                        Pair2(
                            "Arrive", arriving,
                            "Leave", leaving,
                            onFirst = { set(if (arriving) t.without(arrive) else t.with(arrive)) },
                            onSecond = { set(if (leaving) t.without(leave) else t.with(leave)) }
                        )
                    }
                }
            }

            // Also: waking, the car, a time.
            SectionHeader("Also", meta = "Any one of them reaches you", modifier = Modifier.padding(top = 20.dp, start = 8.dp))
            SheetGroup(group) {
                SheetRow(icon = momentIcon(RemindWhen.Wake), title = "On waking") {
                    MomentSwitch(t.wake == true) { on -> set(if (on) t.with(RemindWhen.Wake) else t.without(RemindWhen.Wake)) }
                }
                val car = whens.filterIsInstance<RemindWhen.Car>().firstOrNull()
                SheetRow(icon = momentIcon(RemindWhen.Car(false)), title = "Car") {
                    Pair2(
                        "Get in", car != null && !car.leave,
                        "Get out", car != null && car.leave,
                        onFirst = { set(if (car != null && !car.leave) t.without(car) else t.with(RemindWhen.Car(false))) },
                        onSecond = { set(if (car != null && car.leave) t.without(car) else t.with(RemindWhen.Car(true))) }
                    )
                }
                val at = whens.filterIsInstance<RemindWhen.At>().firstOrNull()
                SheetRow(
                    icon = momentIcon(RemindWhen.At(now)),
                    title = "At a time",
                    subtitle = at?.let { timePhrase(it.time, now).replaceFirstChar { c -> c.titlecase() } }
                ) {
                    MomentSwitch(at != null) { on -> set(if (on) t.with(RemindWhen.At(defaultTime(now))) else t.without(RemindWhen.At(now))) }
                }
                if (at != null) FlowRow(
                    Modifier.padding(start = 60.dp, end = 12.dp, bottom = 12.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    val quick = quickTimes(now)
                    if (quick.none { it.second == at.time }) TimeChoice(timeLabel(at.time, now), true) {}
                    quick.forEach { (label, time) -> TimeChoice(label, time == at.time) { set(t.with(RemindWhen.At(time))) } }
                    TimeChoice("Other", false) {
                        val start = at.time
                        DatePickerDialog(context, { _, y, m, d ->
                            TimePickerDialog(context, { _, h, min ->
                                set(t.with(RemindWhen.At(LocalDateTime.of(y, m + 1, d, h, min))))
                            }, start.hour, start.minute, true).show()
                        }, start.year, start.monthValue - 1, start.dayOfMonth).apply {
                            datePicker.minDate = System.currentTimeMillis() - 1000
                        }.show()
                    }
                }
            }

            Row(Modifier.fillMaxWidth().padding(top = 20.dp), verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = { set(Triggers()) }, enabled = whens.isNotEmpty()) { Text("Clear moments") }
                Spacer(Modifier.weight(1f))
                Button(
                    onClick = onDismiss,
                    colors = ButtonDefaults.buttonColors(containerColor = c.accentFilled, contentColor = c.onAccentFilled),
                    contentPadding = PaddingValues(horizontal = 28.dp, vertical = 12.dp)
                ) { Text("Done") }
            }
        }
    }
}

@Composable
private fun SheetGroup(color: androidx.compose.ui.graphics.Color, content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(color).padding(vertical = 4.dp)) {
        CompositionLocalProvider(LocalNodeSurface provides color) { content() }
    }
}

/** icon | title (and subtitle) | the row's control at the end. */
@Composable
private fun SheetRow(icon: ImageVector?, title: String, subtitle: String? = null, control: @Composable () -> Unit) {
    val c = circuitColors
    Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(start = 16.dp, end = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.width(28.dp), contentAlignment = Alignment.Center) {
            if (icon != null) Icon(icon, contentDescription = null, tint = c.inkSoft, modifier = Modifier.size(22.dp))
        }
        Spacer(Modifier.width(16.dp))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (subtitle != null) Text(subtitle, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Spacer(Modifier.width(8.dp))
        control()
    }
}

/** Two joined choices (Arrive | Leave): the chosen one filled with a check; tap it again to clear. */
@Composable
private fun Pair2(first: String, firstOn: Boolean, second: String, secondOn: Boolean, onFirst: () -> Unit, onSecond: () -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(2.dp)) {
        Half(first, firstOn, RoundedCornerShape(topStart = 20.dp, bottomStart = 20.dp, topEnd = 6.dp, bottomEnd = 6.dp), onFirst)
        Half(second, secondOn, RoundedCornerShape(topStart = 6.dp, bottomStart = 6.dp, topEnd = 20.dp, bottomEnd = 20.dp), onSecond)
    }
}

@Composable
private fun Half(label: String, on: Boolean, shape: RoundedCornerShape, onClick: () -> Unit) {
    val c = circuitColors
    Row(
        Modifier.clip(shape).background(if (on) c.accentFilled else MaterialTheme.colorScheme.surfaceContainerHighest)
            .clickable(role = Role.RadioButton, onClick = onClick).heightIn(min = 40.dp).widthIn(min = 72.dp).padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center
    ) {
        if (on) Icon(Icons.Outlined.Check, contentDescription = null, tint = c.onAccentFilled, modifier = Modifier.size(16.dp).padding(end = 2.dp))
        Text(label, style = MaterialTheme.typography.labelLarge, color = if (on) c.onAccentFilled else c.ink, maxLines = 1)
    }
}

@Composable
private fun MomentSwitch(on: Boolean, onChange: (Boolean) -> Unit) {
    val c = circuitColors
    Switch(
        checked = on,
        onCheckedChange = onChange,
        colors = SwitchDefaults.colors(checkedTrackColor = c.accentFilled, checkedThumbColor = c.onAccentFilled)
    )
}

@Composable
private fun TimeChoice(label: String, on: Boolean, onClick: () -> Unit) {
    val c = circuitColors
    Text(
        label,
        style = MaterialTheme.typography.labelLarge,
        color = if (on) c.onAccentFilled else c.ink,
        modifier = Modifier.clip(RoundedCornerShape(12.dp))
            .background(if (on) c.accentFilled else MaterialTheme.colorScheme.surfaceContainerHighest)
            .clickable(onClick = onClick)
            .heightIn(min = 40.dp)
            .padding(horizontal = 14.dp, vertical = 10.dp)
    )
}
