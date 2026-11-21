package app.ganam.assistant.ui.screens.reminders

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.ganam.assistant.data.models.Task
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.components.Rule
import app.ganam.assistant.ui.shell.LocalShellLayout
import app.ganam.assistant.ui.shell.ShellLayout
import app.ganam.assistant.ui.shell.dragToDay
import app.ganam.assistant.ui.shell.rememberNow
import app.ganam.assistant.ui.theme.GanamSerif
import app.ganam.assistant.ui.viewmodels.MainViewModel

private val GUTTER = 18.dp

/**
 * Reminders, the list pane: every open reminder grouped By when or By list,
 * "+ Add a reminder", a circle to complete (with Undo) and "Done recently"
 * folded at the end. Unfolded, a row can be long-pressed and dragged onto the
 * day strip.
 */
@Composable
fun ReminderListPane(viewModel: MainViewModel) {
    val shell = viewModel.shellController
    val tc = viewModel.tasksController
    val tasks by tc.tasks.collectAsState()
    val groups by tc.taskGroups.collectAsState()
    val zones by tc.zones.collectAsState()
    val loading by tc.isLoadingTasks.collectAsState()
    val byList by shell.remindersByList.collectAsState()
    val doneOpen by shell.doneOpen.collectAsState()
    val pick by shell.reminder.collectAsState()
    val now by rememberNow()
    val showSelection = LocalShellLayout.current != ShellLayout.STACKED

    val open = remember(tasks) { tasks.filter(::isOpen) }
    val sections = remember(open, groups, byList, now.toLocalDate(), now.hour) {
        if (byList) sectionsByList(open, groups) else sectionsByWhen(open, now)
    }
    val done = remember(tasks) { doneRecently(tasks) }
    val forToday = remember(open, now) { open.count { bucketOf(it, now).let { b -> b == Bucket.OVERDUE || b == Bucket.TODAY } } }
    val groupNames = remember(groups) { groups.associate { it.id to it.name } }

    fun listName(t: Task) = t.groupId?.let { groupNames[it] } ?: t.groupName ?: "General"

    val row: @Composable (Task) -> Unit = { t ->
        ReminderRow(
            task = t,
            sub = if (byList) taskWhenText(t, now) else "${taskWhenText(t, now)} · ${listName(t)}",
            selected = showSelection && pick?.id == t.id,
            onClick = { shell.selectReminder(t.id) },
            onToggle = { tc.setDone(t.id, isOpen(t)) },
            modifier = if (isOpen(t)) Modifier.dragToDay(t.title) { slot ->
                tc.setTriggers(t.id, remindWhenFor(slot, java.time.LocalDateTime.now(), zones).toTriggers(), "Moved to ${slotPhrase(slot)}")
            } else Modifier
        )
    }

    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(top = 20.dp, bottom = 32.dp)) {
        item(key = "head") {
            Column(Modifier.padding(horizontal = GUTTER)) {
                MonoLabel("${open.size} open · $forToday for today")
                Text("Reminders", style = MaterialTheme.typography.displayMedium, modifier = Modifier.padding(top = 2.dp))
                Row(Modifier.padding(top = 12.dp), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                    SegTab("By when", !byList) { shell.setRemindersByList(false) }
                    SegTab("By list", byList) { shell.setRemindersByList(true) }
                }
                QuickAdd { title ->
                    // "By list" adds to the list of the open reminder, else Dana picks the list.
                    val group = if (byList) tasks.firstOrNull { it.id == pick?.id }?.groupId else null
                    tc.quickAdd(title, group) { id -> shell.selectReminder(id) }
                }
            }
        }
        if (open.isEmpty() && !loading) item(key = "empty") {
            Column(Modifier.padding(horizontal = GUTTER, vertical = 24.dp)) {
                Text(
                    "Nothing to remember.",
                    style = MaterialTheme.typography.headlineSmall.copy(fontFamily = GanamSerif, fontStyle = androidx.compose.ui.text.font.FontStyle.Italic)
                )
                Text("Add one above, or tell Dana.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        sections.forEach { section ->
            item(key = "h_${section.key}") { SectionHead(section.title, section.tasks.size) }
            items(section.tasks, key = { "t_${section.key}_${it.id}" }) { row(it) }
        }
        if (done.isNotEmpty()) {
            item(key = "done_fold") {
                Row(
                    Modifier.fillMaxWidth().clickable { shell.toggleDone() }.padding(horizontal = GUTTER, vertical = 14.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text("Done recently · ${done.size}", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
                    MonoLabel(if (doneOpen) "hide" else "show")
                }
                Rule(Modifier.padding(horizontal = GUTTER))
            }
            if (doneOpen) items(done, key = { "d_${it.id}" }) { row(it) }
        }
    }
}

@Composable
private fun SegTab(text: String, on: Boolean, onClick: () -> Unit) {
    val accent = MaterialTheme.colorScheme.primary
    Box(
        Modifier
            .clickable(onClick = onClick)
            .heightIn(min = 36.dp)
            .drawBehind { if (on) drawLine(accent, Offset(0f, size.height - 4.dp.toPx()), Offset(size.width, size.height - 4.dp.toPx()), 1.5.dp.toPx()) }
            .padding(vertical = 8.dp),
        contentAlignment = Alignment.Center
    ) {
        MonoLabel(text, color = if (on) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f))
    }
}

@Composable
private fun QuickAdd(onAdd: (String) -> Unit) {
    var text by rememberSaveable { mutableStateOf("") }
    val focus = LocalFocusManager.current
    val rule = MaterialTheme.colorScheme.outline.copy(alpha = 0.6f)
    fun submit() {
        val t = text.trim()
        if (t.isEmpty()) return
        onAdd(t)
        text = ""
        focus.clearFocus()
    }
    BasicTextField(
        value = text,
        onValueChange = { text = it },
        singleLine = true,
        textStyle = MaterialTheme.typography.bodyLarge.copy(color = MaterialTheme.colorScheme.onSurface, textDirection = TextDirection.Content),
        cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Done),
        keyboardActions = KeyboardActions(onDone = { submit() }),
        modifier = Modifier.fillMaxWidth().padding(top = 12.dp).semantics { contentDescription = "Add a reminder" },
        decorationBox = { inner ->
            Box(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = 44.dp)
                    .drawBehind {
                        drawRoundRect(
                            rule,
                            cornerRadius = androidx.compose.ui.geometry.CornerRadius(12.dp.toPx()),
                            style = Stroke(1.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(6.dp.toPx(), 4.dp.toPx())))
                        )
                    }
                    .padding(horizontal = 12.dp, vertical = 11.dp),
                contentAlignment = Alignment.CenterStart
            ) {
                if (text.isEmpty()) Text("+ Add a reminder", style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                inner()
            }
        }
    )
}

@Composable
private fun SectionHead(title: String, count: Int) {
    Column(Modifier.padding(horizontal = GUTTER).padding(top = 20.dp)) {
        Row(Modifier.fillMaxWidth().padding(bottom = 8.dp)) {
            MonoLabel(title, modifier = Modifier.weight(1f))
            MonoLabel(count.toString())
        }
        Rule()
    }
}

/** One reminder: circle, title (● when important), mono "when · list" line. Selected (unfolded) = canvas + accent bar. */
@Composable
fun ReminderRow(
    task: Task,
    sub: String,
    selected: Boolean,
    onClick: () -> Unit,
    onToggle: () -> Unit,
    modifier: Modifier = Modifier
) {
    val done = !isOpen(task)
    val accent = MaterialTheme.colorScheme.primary
    val canvas = MaterialTheme.colorScheme.background
    Column(
        modifier
            .fillMaxWidth()
            .then(
                if (selected) Modifier.background(canvas).drawBehind {
                    drawLine(accent, Offset(1.5.dp.toPx(), 0f), Offset(1.5.dp.toPx(), size.height), 3.dp.toPx())
                } else Modifier
            )
            .clickable(onClick = onClick)
    ) {
        Row(Modifier.fillMaxWidth().padding(start = GUTTER - 6.dp, end = GUTTER, top = 7.dp, bottom = 7.dp), verticalAlignment = Alignment.Top) {
            Box(
                Modifier
                    .size(32.dp)
                    .clip(CircleShape)
                    .clickable(onClick = onToggle)
                    .semantics { contentDescription = if (done) "Mark not done" else "Mark done" },
                contentAlignment = Alignment.Center
            ) {
                Box(
                    Modifier
                        .size(20.dp)
                        .clip(CircleShape)
                        .then(if (done) Modifier.background(accent) else Modifier)
                        .border(1.5.dp, if (done) accent else MaterialTheme.colorScheme.outline, CircleShape)
                )
            }
            Spacer(Modifier.width(6.dp))
            Column(Modifier.weight(1f).padding(top = 5.dp)) {
                Text(
                    buildAnnotatedString {
                        if (task.priority.equals("high", true) && !done) {
                            pushStyle(SpanStyle(color = accent)); append("● "); pop()
                        }
                        append(task.title)
                    },
                    style = MaterialTheme.typography.bodyLarge.copy(
                        fontWeight = if (done) FontWeight.Normal else FontWeight.SemiBold,
                        fontSize = 14.5.sp,
                        textDirection = TextDirection.Content,
                        textDecoration = if (done) TextDecoration.LineThrough else null
                    ),
                    color = if (done) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.fillMaxWidth()
                )
                MonoLabel(sub, modifier = Modifier.padding(top = 3.dp))
            }
        }
        Rule(Modifier.padding(horizontal = GUTTER).background(Color.Transparent))
    }
}
