package io.github.ronganam.dana.ui.screens.reminders

import io.github.ronganam.dana.ui.components.circuit.askSlotInset
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.layout.layout
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.style.ResolvedTextDirection
import androidx.compose.ui.text.style.TextOverflow
import io.github.ronganam.dana.ui.components.RollingText
import io.github.ronganam.dana.ui.components.arrive
import io.github.ronganam.dana.ui.components.startBar
import io.github.ronganam.dana.ui.theme.Motion
import io.github.ronganam.dana.ui.theme.hues
import kotlin.math.roundToInt
import androidx.compose.foundation.background
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
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.shell.LocalShellLayout
import io.github.ronganam.dana.ui.shell.ShellLayout
import io.github.ronganam.dana.ui.shell.dragToDay
import io.github.ronganam.dana.ui.shell.rememberNow
import io.github.ronganam.dana.ui.viewmodels.MainViewModel

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

    // A reminder being completed stays (as it was) until its animation ends; one coming back (Undo) animates in.
    val completing = remember { mutableStateMapOf<String, Task>() }
    val flow = remember { CompletionFlow() }
    val open = tasks.mapNotNull { t -> completing[t.id] ?: t.takeIf(::isOpen) }
    open.forEach { if (it.id in flow.gone && it.id !in completing) { flow.gone -= it.id; flow.returning += it.id } }
    val sections = remember(open, groups, byList, now.toLocalDate(), now.hour) {
        if (byList) sectionsByList(open, groups) else sectionsByWhen(open, now)
    }
    val done = remember(tasks) { doneRecently(tasks) }
    val forToday = remember(open, now) { open.count { bucketOf(it, now).let { b -> b == Bucket.OVERDUE || b == Bucket.TODAY } } }
    val groupNames = remember(groups) { groups.associate { it.id to it.name } }
    val groupColors = remember(groups) { groups.associate { it.id to it.color } }
    val h = hues

    fun listName(t: Task) = t.groupId?.let { groupNames[it] } ?: t.groupName ?: "General"
    // The list's stored colour (the theme's value when it is one of the hues); reminders' amber without one.
    fun listColor(t: Task) = h.fromHex(t.groupId?.let { groupColors[it] } ?: t.groupColor) ?: h.amber

    val row: @Composable (Task, Int) -> Unit = { t, index ->
        val id = t.id
        ReminderRow(
            task = t,
            whenText = taskWhenText(t, now),
            listName = if (byList) null else listName(t),
            hue = listColor(t),
            selected = showSelection && pick?.id == t.id,
            phase = when (id) {
                in completing -> RowPhase.COMPLETING
                in flow.returning -> RowPhase.RETURNING
                else -> RowPhase.REST
            },
            onPhaseDone = {
                if (completing.remove(id) != null) flow.gone += id
                flow.returning -= id
            },
            onClick = { shell.selectReminder(t.id) },
            onToggle = {
                if (isOpen(t)) completing[id] = t else flow.gone += id
                tc.setDone(id, isOpen(t))
            },
            modifier = if (isOpen(t)) Modifier.dragToDay(t.title) { slot ->
                tc.setTriggers(t.id, remindWhenFor(slot, java.time.LocalDateTime.now(), zones).toTriggers(), "Moved to ${slotPhrase(slot)}")
            }.arrive("reminders", index) else Modifier
        )
    }

    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(top = 20.dp, bottom = 32.dp)) {
        item(key = "head") {
            Column(Modifier.padding(horizontal = GUTTER)) {
                MetaLabel("${open.size} open · $forToday for today", modifier = Modifier.askSlotInset())
                Text("Reminders", style = MaterialTheme.typography.displayMedium, modifier = Modifier.padding(top = 2.dp).askSlotInset())
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
                    style = MaterialTheme.typography.headlineSmall
                )
                Text("Add one above, or tell Dana.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        var shown = 0
        sections.forEach { section ->
            val first = shown
            shown += section.tasks.size
            // By list: the section head carries the list's colour as a dot.
            val dot = if (byList) section.tasks.firstOrNull()?.let(::listColor) else null
            item(key = "h_${section.key}") { SectionHead(section.title, section.tasks.size, dot) }
            itemsIndexed(section.tasks, key = { _, t -> "t_${section.key}_${t.id}" }) { i, t -> row(t, first + i) }
        }
        if (done.isNotEmpty()) {
            item(key = "done_fold") {
                Row(
                    Modifier.fillMaxWidth().clickable { shell.toggleDone() }.padding(horizontal = GUTTER, vertical = 14.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text("Done recently · ${done.size}", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
                    MetaLabel(if (doneOpen) "hide" else "show")
                }
                Rule(Modifier.padding(horizontal = GUTTER))
            }
            if (doneOpen) items(done, key = { "d_${it.id}" }) { row(it, Motion.STAGGER_ROWS) }
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
        MetaLabel(text, color = if (on) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f))
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
private fun SectionHead(title: String, count: Int, dot: Color? = null) {
    Column(Modifier.padding(horizontal = GUTTER).padding(top = 20.dp)) {
        Row(Modifier.fillMaxWidth().padding(bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            if (dot != null) Box(Modifier.padding(end = 8.dp).size(7.dp).clip(CircleShape).background(dot))
            MetaLabel(title, modifier = Modifier.weight(1f))
            RollingText(count.toString(), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Rule()
    }
}

/** Where a row is in its completion: at rest, being completed (then it leaves), or coming back (Undo). */
enum class RowPhase { REST, COMPLETING, RETURNING }

/** Which reminders were completed here (their return animates) and which are animating back in. */
private class CompletionFlow {
    val gone = HashSet<String>()
    val returning = HashSet<String>()
}

private fun part(p: Float, from: Float, to: Float) = ((p - from) / (to - from)).coerceIn(0f, 1f)

/**
 * One reminder, a row on the canvas: the circle (its ring softly in the
 * list's [hue]), title (● when important), mono "when" line with its list as
 * a chip tinted in that hue; no start bar.
 * Selected (unfolded) = canvas + accent bar. Done = the circle filled with the
 * hue and checked.
 *
 * Completing (~420ms, [Motion.COMPLETE]): the circle fills with the hue and its
 * check draws, the title's strike-through grows line by line, then the row
 * fades and collapses; RETURNING plays it backwards. One Animatable read only
 * in draw / layer / layout, so no frame recomposes the list.
 */
@Composable
fun ReminderRow(
    task: Task,
    whenText: String,
    listName: String?,
    hue: Color,
    selected: Boolean,
    onClick: () -> Unit,
    onToggle: () -> Unit,
    modifier: Modifier = Modifier,
    phase: RowPhase = RowPhase.REST,
    onPhaseDone: () -> Unit = {}
) {
    val done = !isOpen(task)
    val accent = MaterialTheme.colorScheme.primary
    val canvas = MaterialTheme.colorScheme.background
    val h = hues
    val ink = MaterialTheme.colorScheme.onSurface
    val outline = MaterialTheme.colorScheme.outline
    val onHue = if (h.dark) MaterialTheme.colorScheme.background else Color.White
    // 0 = open and at rest, 1 = completed and gone.
    val progress = remember(task.id) { Animatable(if (phase == RowPhase.RETURNING) 1f else 0f) }
    val latestDone by rememberUpdatedState(onPhaseDone)
    LaunchedEffect(phase) {
        when (phase) {
            RowPhase.COMPLETING -> { progress.animateTo(1f, tween(Motion.COMPLETE, easing = LinearEasing)); latestDone() }
            RowPhase.RETURNING -> { progress.animateTo(0f, tween(Motion.COMPLETE, easing = LinearEasing)); latestDone() }
            RowPhase.REST -> progress.snapTo(0f)
        }
    }
    var titleLayout by remember { mutableStateOf<TextLayoutResult?>(null) }
    Column(
        modifier
            .clipToBounds()
            .layout { measurable, constraints ->
                // Collapse in the last 40%: the one layout animation Ember allows.
                val p = measurable.measure(constraints)
                val f = part(progress.value, 0.6f, 1f)
                val height = (p.height * (1f - f)).roundToInt()
                layout(p.width, height) { if (f == 0f) p.place(0, 0) else p.placeWithLayer(0, 0) { alpha = 1f - f } }
            }
            .fillMaxWidth()
            .then(
                if (selected) Modifier.background(canvas).startBar(accent) else Modifier
            )
            .clickable(onClick = onClick)
    ) {
        Row(Modifier.fillMaxWidth().padding(start = GUTTER - 6.dp, end = GUTTER, top = 10.dp, bottom = 10.dp), verticalAlignment = Alignment.Top) {
            Box(
                Modifier
                    .size(32.dp)
                    .clip(CircleShape)
                    .clickable(onClick = onToggle)
                    .semantics { contentDescription = if (done) "Mark not done" else "Mark done" },
                contentAlignment = Alignment.Center
            ) {
                Canvas(Modifier.size(20.dp)) {
                    val fill = if (done) 1f else part(progress.value, 0f, 0.3f)
                    val tick = if (done) 1f else part(progress.value, 0.1f, 0.4f)
                    val r = size.minDimension / 2f
                    val stroke = 1.5.dp.toPx()
                    if (fill > 0f) drawCircle(hue.copy(alpha = fill), r)
                    // At rest the ring carries the list's hue, softly (the row has no bar).
                    drawCircle(lerp(lerp(outline, hue, 0.55f), hue, fill), r - stroke / 2, style = Stroke(stroke))
                    if (tick > 0f) {
                        // The check draws itself: down-stroke, then the long up-stroke.
                        val a = Offset(size.width * 0.28f, size.height * 0.52f)
                        val b = Offset(size.width * 0.44f, size.height * 0.68f)
                        val c = Offset(size.width * 0.73f, size.height * 0.36f)
                        val w = 1.8.dp.toPx()
                        val first = (tick / 0.4f).coerceAtMost(1f)
                        drawLine(onHue, a, a + (b - a) * first, w, StrokeCap.Round)
                        if (tick > 0.4f) drawLine(onHue, b, b + (c - b) * ((tick - 0.4f) / 0.6f), w, StrokeCap.Round)
                    }
                }
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
                        fontWeight = if (done) FontWeight.Normal else FontWeight.Medium,
                        fontSize = 15.sp,
                        textDirection = TextDirection.Content,
                        textDecoration = if (done) TextDecoration.LineThrough else null
                    ),
                    color = if (done) MaterialTheme.colorScheme.onSurfaceVariant else ink,
                    onTextLayout = { titleLayout = it },
                    modifier = Modifier.fillMaxWidth().drawWithContent {
                        drawContent()
                        val strike = part(progress.value, 0.25f, 0.6f)
                        val layout = titleLayout
                        if (strike > 0f && layout != null && !done) {
                            // Grows along the lines in reading order (from the right on a Hebrew line).
                            val total = (0 until layout.lineCount).sumOf { (layout.getLineRight(it) - layout.getLineLeft(it)).toDouble() }.toFloat()
                            var left = strike * total
                            for (line in 0 until layout.lineCount) {
                                if (left <= 0f) break
                                val l = layout.getLineLeft(line)
                                val r = layout.getLineRight(line)
                                val len = minOf(left, r - l)
                                val y = (layout.getLineTop(line) + layout.getLineBottom(line)) / 2f
                                val rtl = layout.getParagraphDirection(layout.getLineStart(line)) == ResolvedTextDirection.Rtl
                                if (rtl) drawLine(ink, Offset(r, y), Offset(r - len, y), 1.5.dp.toPx())
                                else drawLine(ink, Offset(l, y), Offset(l + len, y), 1.5.dp.toPx())
                                left -= len
                            }
                        }
                    }
                )
                Row(Modifier.padding(top = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                    MetaLabel(whenText, modifier = Modifier.weight(1f, fill = false))
                    if (listName != null) Text(
                        listName.uppercase(),
                        style = MaterialTheme.typography.labelMedium.copy(textDirection = TextDirection.Content),
                        color = h.text(hue),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier
                            .padding(start = 8.dp)
                            .clip(RoundedCornerShape(5.dp))
                            .background(h.tint(hue))
                            .padding(horizontal = 6.dp, vertical = 1.dp)
                    )
                }
            }
        }
        Rule(Modifier.padding(horizontal = GUTTER).background(Color.Transparent))
    }
}
