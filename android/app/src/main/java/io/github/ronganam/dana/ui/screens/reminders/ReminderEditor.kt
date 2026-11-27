package io.github.ronganam.dana.ui.screens.reminders

import io.github.ronganam.dana.data.network.TaskChanges
import android.app.DatePickerDialog
import android.app.TimePickerDialog
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.data.models.TaskGroup
import io.github.ronganam.dana.ui.components.InkChoice
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.components.friendlyDateTime
import io.github.ronganam.dana.ui.components.isolate
import io.github.ronganam.dana.ui.shell.rememberNow
import io.github.ronganam.dana.ui.viewmodels.TasksController
import kotlinx.coroutines.delay
import java.time.LocalDateTime

/**
 * The reminder editor (the stage of Reminders): name and note edited in
 * place, "Remind me" as one choice with its follow-ups and a sentence that
 * says what will happen, the list, priority, where it came from, Done and
 * Delete. Every change saves as it is made (optimistically, through the
 * offline queue); there is no Save button.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ReminderEditor(
    task: Task,
    tasks: TasksController,
    groups: List<TaskGroup>,
    zones: List<String>,
    onBack: (() -> Unit)?,
    onDoneChanged: (done: Boolean) -> Unit,
    onDeleted: () -> Unit,
    onOpenCall: (String) -> Unit,
    onAsk: () -> Unit,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    val now by rememberNow()
    val current by rememberUpdatedState(task)
    val done = !isOpen(task)
    val whens = remember(task.triggers) { remindWhens(task) }
    val primary = whens.firstOrNull() ?: RemindWhen.None
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    val focus = LocalFocusManager.current

    fun setWhen(w: RemindWhen) = tasks.setTriggers(task.id, w.toTriggers())

    // Name and note: saved 0.8 s after typing stops, and when the editor goes away.
    var title by remember(task.id) { mutableStateOf(task.title) }
    var note by remember(task.id) { mutableStateOf(task.description.orEmpty()) }
    val latestTitle by rememberUpdatedState(title)
    val latestNote by rememberUpdatedState(note)
    fun commitText() {
        val t = latestTitle.trim()
        val n = latestNote.trim()
        val fields = TaskChanges(
            title = t.takeIf { it.isNotEmpty() && it != current.title.trim() },
            description = n.takeIf { it != current.description.orEmpty().trim() }
        )
        if (fields.isEmpty) return
        tasks.edit(current.id, fields) { it.copy(title = if (t.isNotEmpty()) t else it.title, description = n.ifEmpty { null }) }
    }
    LaunchedEffect(task.id, title, note) {
        delay(800)
        commitText()
    }
    DisposableEffect(task.id) { onDispose { commitText() } }

    Column(
        modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 22.dp, vertical = 18.dp)
            .widthIn(max = 680.dp)
    ) {
        if (onBack != null) {
            Text(
                "← Reminders",
                style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium),
                color = muted,
                modifier = Modifier.clickable(onClick = onBack).padding(vertical = 8.dp)
            )
        }
        val listName = task.groupId?.let { id -> groups.firstOrNull { it.id == id }?.name } ?: task.groupName ?: "General"
        MetaLabel("${if (done) "Done" else bucketOf(task, now).label} · $listName")

        InlineField(
            value = title,
            onValueChange = { title = it },
            placeholder = "Name",
            style = MaterialTheme.typography.headlineSmall.copy(
                fontFamily = MaterialTheme.typography.bodyLarge.fontFamily,
                fontWeight = FontWeight.SemiBold,
                fontSize = 22.sp,
                lineHeight = 28.sp
            ),
            singleLine = false,
            imeAction = ImeAction.Done,
            onDone = { focus.clearFocus() },
            modifier = Modifier.padding(top = 6.dp)
        )
        InlineField(
            value = note,
            onValueChange = { note = it },
            placeholder = "Add a note",
            style = MaterialTheme.typography.bodyLarge.copy(color = muted),
            singleLine = false,
            imeAction = ImeAction.Default,
            modifier = Modifier.padding(top = 4.dp)
        )

        Field("Remind me") {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                InkChoice("No reminder", primary == RemindWhen.None) { setWhen(RemindWhen.None) }
                InkChoice("At a time", primary is RemindWhen.At) { if (primary !is RemindWhen.At) setWhen(RemindWhen.At(defaultTime(now))) }
                InkChoice("At a place", primary is RemindWhen.Place) {
                    if (primary !is RemindWhen.Place) setWhen(RemindWhen.Place(homeZoneName(zones), leave = false))
                }
                InkChoice("In the car", primary is RemindWhen.Car) { if (primary !is RemindWhen.Car) setWhen(RemindWhen.Car(leave = false)) }
                InkChoice("On waking", primary == RemindWhen.Wake) { setWhen(RemindWhen.Wake) }
            }
            when (primary) {
                is RemindWhen.At -> {
                    val quick = quickTimes(now)
                    FlowRow(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (quick.none { it.second == primary.time }) InkChoice(timeLabel(primary.time, now), selected = true) {}
                        quick.forEach { (label, t) -> InkChoice(label, t == primary.time) { setWhen(RemindWhen.At(t)) } }
                        InkChoice("Pick a time…") {
                            val start = primary.time
                            DatePickerDialog(context, { _, y, m, d ->
                                TimePickerDialog(context, { _, h, min ->
                                    setWhen(RemindWhen.At(LocalDateTime.of(y, m + 1, d, h, min)))
                                }, start.hour, start.minute, true).show()
                            }, start.year, start.monthValue - 1, start.dayOfMonth).apply {
                                datePicker.minDate = System.currentTimeMillis() - 1000
                            }.show()
                        }
                    }
                }
                is RemindWhen.Place -> {
                    val choices = (zones + primary.zone).distinctBy { it.lowercase() }
                    FlowRow(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        choices.forEach { z ->
                            InkChoice(z.replaceFirstChar { it.titlecase() }, z.equals(primary.zone, true)) { setWhen(primary.copy(zone = z)) }
                        }
                    }
                    FlowRow(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        InkChoice("When I arrive", !primary.leave) { setWhen(primary.copy(leave = false)) }
                        InkChoice("When I leave", primary.leave) { setWhen(primary.copy(leave = true)) }
                    }
                }
                is RemindWhen.Car -> FlowRow(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    InkChoice("Getting in", !primary.leave) { setWhen(RemindWhen.Car(leave = false)) }
                    InkChoice("Getting out", primary.leave) { setWhen(RemindWhen.Car(leave = true)) }
                }
                else -> {}
            }
            Text(sentence(task, now), style = MaterialTheme.typography.bodyMedium, color = muted, modifier = Modifier.padding(top = 10.dp))
        }

        if (groups.isNotEmpty()) Field("List") {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                groups.sortedBy { it.sortOrder }.forEach { g ->
                    InkChoice(g.name, g.id == task.groupId) {
                        tasks.edit(task.id, TaskChanges(groupId = g.id)) {
                            it.copy(groupId = g.id, groupName = g.name, groupEmoji = g.emoji, groupColor = g.color)
                        }
                    }
                }
            }
        }

        Field("Priority") {
            val p = task.priority.lowercase()
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("low" to "Low", "medium" to "Normal", "high" to "Important").forEach { (value, label) ->
                    InkChoice(label, p == value || (value == "medium" && p !in setOf("low", "high"))) {
                        tasks.edit(task.id, TaskChanges(priority = value)) { it.copy(priority = value) }
                    }
                }
            }
        }

        if (task.links.isNotEmpty()) Field("Came from") {
            task.links.forEach { link ->
                val text = when (link.refType) {
                    "call" -> listOfNotNull("Call", (link.contact ?: link.title)?.let(::isolate), friendlyDateTime(link.callTimestamp)).joinToString(" · ")
                    else -> listOfNotNull(link.refType.replaceFirstChar { it.uppercase() }, link.title?.let(::isolate)).joinToString(" · ")
                }
                Text(
                    text,
                    style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content),
                    color = if (link.refType == "call") MaterialTheme.colorScheme.onSurface else muted,
                    modifier = Modifier.fillMaxWidth()
                        .then(if (link.refType == "call") Modifier.clickable { onOpenCall(link.refId) } else Modifier)
                        .padding(vertical = 9.dp)
                )
                Rule()
            }
        }

        FlowRow(Modifier.padding(top = 24.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            InkChoice(if (done) "Not done" else "Done", solid = true) { onDoneChanged(!done) }
            InkChoice("Delete") { onDeleted() }
            TextButton(onClick = onAsk) { Text("Ask Dana about this →") }
        }
        Text("Saved as you change it.", style = MaterialTheme.typography.bodySmall, color = muted, modifier = Modifier.padding(top = 12.dp, bottom = 24.dp))
    }
}

@Composable
private fun Field(label: String, content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxWidth().padding(top = 20.dp)) {
        MetaLabel(label, modifier = Modifier.padding(bottom = 8.dp))
        content()
    }
}

/** A borderless text field that looks like the text it edits. */
@Composable
private fun InlineField(
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    style: TextStyle,
    singleLine: Boolean,
    imeAction: ImeAction,
    modifier: Modifier = Modifier,
    onDone: () -> Unit = {}
) {
    val s = style.copy(textDirection = TextDirection.Content, color = if (style.color.alpha > 0f) style.color else MaterialTheme.colorScheme.onSurface)
    BasicTextField(
        value = value,
        onValueChange = onValueChange,
        textStyle = s,
        singleLine = singleLine,
        cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = imeAction),
        keyboardActions = KeyboardActions(onDone = { onDone() }),
        modifier = modifier.fillMaxWidth(),
        decorationBox = { inner ->
            Box {
                if (value.isEmpty()) Text(placeholder, style = s.copy(color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f)))
                inner()
            }
        }
    )
}
