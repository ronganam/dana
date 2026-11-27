package io.github.ronganam.dana.ui.screens.watching

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowRight
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Pause
import androidx.compose.material.icons.outlined.PlayArrow
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.watches.CheckGroup
import io.github.ronganam.dana.data.watches.TraceRow
import io.github.ronganam.dana.data.watches.Watch
import io.github.ronganam.dana.data.watches.WatchFire
import io.github.ronganam.dana.data.watches.WatchRun
import io.github.ronganam.dana.data.watches.circuitOf
import io.github.ronganam.dana.data.watches.clockWords
import io.github.ronganam.dana.data.watches.countChecks
import io.github.ronganam.dana.data.watches.groupChecks
import io.github.ronganam.dana.data.watches.groupCount
import io.github.ronganam.dana.data.watches.reasonWords
import io.github.ronganam.dana.data.watches.sinceWords
import io.github.ronganam.dana.data.watches.todays
import io.github.ronganam.dana.data.watches.whenWords
import io.github.ronganam.dana.ui.components.circuit.CircuitNode
import io.github.ronganam.dana.ui.components.circuit.GroupedSection
import io.github.ronganam.dana.ui.components.circuit.NodeShape
import io.github.ronganam.dana.ui.components.circuit.NodeSize
import io.github.ronganam.dana.ui.components.circuit.NodeState
import io.github.ronganam.dana.ui.components.circuit.SectionHeader
import io.github.ronganam.dana.ui.components.circuit.Step
import io.github.ronganam.dana.ui.components.circuit.StepLine
import io.github.ronganam.dana.ui.theme.Space
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.tabular
import io.github.ronganam.dana.ui.viewmodels.WatchingController
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * One watch: its circuit large with today's counts, a run waiting for the
 * owner or going on, its recent checks grouped by outcome with their reasons,
 * Pause / Turn on, Delete, and a field at the bottom to change it in words.
 */
@Composable
fun WatchDetailPage(
    controller: WatchingController,
    watchId: String,
    showBack: Boolean,
    onBack: () -> Unit,
    onOpenRun: (WatchRun) -> Unit,
) {
    val watches by controller.watches.collectAsState()
    val detail by controller.detail.collectAsState()
    val trace by controller.trace.collectAsState()
    val runs by controller.runs.collectAsState()
    val steps by controller.steps.collectAsState()
    val drafts by controller.drafts.collectAsState()
    val acting by controller.acting.collectAsState()
    val changing by controller.changing.collectAsState()
    val c = circuitColors
    val watch = detail?.watch?.takeIf { it.id == watchId } ?: watches.firstOrNull { it.id == watchId }
    val zone = ZoneId.systemDefault()
    val run = runs[watchId]

    Column(Modifier.fillMaxSize()) {
        // The top bar: Back, then Pause / Turn on and the menu at the end.
        Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            if (showBack) IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back", tint = c.ink) }
            Spacer(Modifier.weight(1f))
            if (watch != null && !watch.isClosed) {
                FilledTonalButton(onClick = { controller.setPaused(watch.id, !watch.isPaused) }, modifier = Modifier.heightIn(min = 48.dp)) {
                    Icon(if (watch.isPaused) Icons.Outlined.PlayArrow else Icons.Outlined.Pause, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text(if (watch.isPaused) "Turn on" else "Pause")
                }
            } else if (watch != null) {
                FilledTonalButton(onClick = { controller.setPaused(watch.id, false) }, modifier = Modifier.heightIn(min = 48.dp)) { Text("Turn on again") }
            }
            if (watch != null) DetailMenu { controller.delete(watch.id) }
        }
        if (watch == null) {
            Text("Loading…", style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(Space.screen))
        } else {
            Column(
                Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = Space.screen)
            ) {
                Text(watch.title, style = MaterialTheme.typography.displaySmall, color = c.ink, modifier = Modifier.semantics { heading() })
                Text(sinceWords(watch, zone), style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(top = 4.dp))
                CircuitCard(watch, trace, run, Modifier.padding(top = 20.dp))
                if (run != null) {
                    GroupedSection(Modifier.padding(top = 12.dp)) {
                        item(color = if (run.waiting) c.warmSurface else null) {
                            Column(Modifier.padding(16.dp)) {
                                if (run.waiting) RunRequest(run, drafts[watchId], run.activityId in acting, { controller.answer(run, true) }, { controller.answer(run, false) }, { onOpenRun(run) })
                                else {
                                    Text("Running now", style = MaterialTheme.typography.titleMedium, color = c.accent)
                                    val s = steps[watchId].orEmpty()
                                    if (s.isNotEmpty()) StepLine(s.map { Step(it.label, it.state.toStepState()) }, Modifier.fillMaxWidth().padding(top = 12.dp))
                                    else Text(run.text, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft)
                                }
                            }
                        }
                    }
                }
                watch.parsed.unsupported?.let {
                    Text("What it can't do: $it", style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(top = 12.dp))
                }
                RecentChecks(trace, detail?.fires.orEmpty(), run, onOpenRun, Modifier.padding(top = 28.dp))
                Spacer(Modifier.height(24.dp))
            }
            if (!watch.isClosed) ChangeField(changing) { words, done -> controller.change(watch.id, words, done) }
        }
    }
}

private fun io.github.ronganam.dana.data.watches.RunStepState.toStepState() = when (this) {
    io.github.ronganam.dana.data.watches.RunStepState.Done -> io.github.ronganam.dana.ui.components.circuit.StepState.Done
    io.github.ronganam.dana.data.watches.RunStepState.Failed -> io.github.ronganam.dana.ui.components.circuit.StepState.Failed
    else -> io.github.ronganam.dana.ui.components.circuit.StepState.Current
}

@Composable
private fun DetailMenu(onDelete: () -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { open = true }) { Icon(Icons.Outlined.MoreVert, contentDescription = "More", tint = circuitColors.inkSoft) }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            DropdownMenuItem(text = { Text("Delete watch") }, onClick = { open = false; onDelete() })
        }
    }
}

/** The circuit large with today's counts over its wires and the line under it. */
@Composable
private fun CircuitCard(watch: Watch, trace: List<TraceRow>, run: WatchRun?, modifier: Modifier = Modifier) {
    val c = circuitColors
    val circuit = remember(watch.spec) { circuitOf(watch.parsed) }
    val zone = ZoneId.systemDefault()
    val today = remember(trace) { todays(trace, LocalDate.now(zone), zone) }
    val counts = if (trace.isEmpty() && today.isEmpty()) null else countChecks(today)
    val live = watchLive(watch, run, today.any { it.outcome == "fired" }, false)
    val nodes = circuitNodes(circuit, live).let { n ->
        // Today's flow: what got through each part.
        if (counts == null || live != WatchLive.Idle && live != WatchLive.FiredToday) n
        else n.copy(
            source = n.source.copy(state = if (counts.checks > 0) NodeState.Passed else NodeState.Idle),
            rule = n.rule.copy(state = if (counts.passedRule > 0) NodeState.Passed else NodeState.Idle),
            ai = n.ai?.copy(state = if (counts.fired > 0) NodeState.Passed else NodeState.Idle),
        )
    }
    GroupedSection(modifier) {
        item {
            Column(Modifier.fillMaxWidth().padding(vertical = 20.dp, horizontal = 8.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                LargeCircuit(circuit, nodes, counts)
                val line = when {
                    counts == null -> "Nothing checked yet."
                    counts.checks == 0 -> "Nothing came in today."
                    else -> "Today: ${counts.checks} ${if (counts.checks == 1) "event" else "events"}, ${counts.passedRule} passed the rule, ${counts.fired} fired."
                }
                Text(line, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.fillMaxWidth().padding(top = 16.dp, start = 8.dp, end = 8.dp))
            }
        }
    }
}

/** What a waiting run wants to do, the words it would send, and Approve / Deny / Open run. */
@Composable
fun RunRequest(run: WatchRun, draft: String?, busy: Boolean, onApprove: () -> Unit, onDeny: () -> Unit, onOpenRun: () -> Unit, modifier: Modifier = Modifier) {
    RequestBlock(run, draft, busy, onApprove, onDeny, onOpenRun, modifier)
}

/** "Recent checks": the checks grouped by outcome on a line, each with its reason. */
@Composable
private fun RecentChecks(trace: List<TraceRow>, fires: List<WatchFire>, run: WatchRun?, onOpenRun: (WatchRun) -> Unit, modifier: Modifier = Modifier) {
    val c = circuitColors
    val groups = remember(trace) { groupChecks(trace) }
    val now = ZonedDateTime.now()
    Column(modifier) {
        SectionHeader("Recent checks", meta = "Kept 30 days", metaColor = c.mute)
        if (groups.isEmpty()) Text("No checks yet. Each event this watch looks at shows here, with why it went on or stopped.", style = MaterialTheme.typography.bodyMedium, color = c.inkSoft)
        val keys = groups.keys.toList()
        keys.forEachIndexed { gi, g ->
            val rows = groups.getValue(g)
            var all by rememberSaveable(g) { mutableStateOf(false) }
            val cap = if (g == CheckGroup.RuleNo || g == CheckGroup.Other) 2 else 5
            val shown = if (all) rows.take(60) else rows.take(cap)
            val last = gi == keys.lastIndex
            val line = c.wire
            Row(
                Modifier
                    .fillMaxWidth()
                    .height(IntrinsicSize.Min)
            ) {
                Box(
                    Modifier.width(24.dp).fillMaxHeight().drawBehind {
                        if (!last) drawLine(line, Offset(size.width / 2, 26.dp.toPx()), Offset(size.width / 2, size.height), 1.5.dp.toPx())
                    },
                    contentAlignment = Alignment.TopCenter
                ) {
                    CircuitNode(g.shape(), g.nodeState(run != null), Modifier.padding(top = 4.dp), size = NodeSize.Timeline)
                }
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f).padding(bottom = 20.dp)) {
                    Row(Modifier.heightIn(min = 32.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(g.title(), style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold), color = c.ink)
                        Text(" ${groupCount(rows)}", style = MaterialTheme.typography.titleMedium.tabular(), color = c.inkSoft, modifier = Modifier.weight(1f))
                        if (rows.size > cap) Text(
                            if (all) "Show less" else "Show all",
                            style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
                            color = c.accent,
                            modifier = Modifier.clip(RoundedCornerShape(8.dp)).clickable { all = !all }.padding(horizontal = 8.dp, vertical = 8.dp)
                        )
                    }
                    shown.forEach { r ->
                        CheckRow(r, fireOf(r, fires), run, now, onOpenRun)
                    }
                }
            }
        }
    }
}

private fun CheckGroup.title() = when (this) {
    CheckGroup.Fired -> "Fired"
    CheckGroup.AiNo -> "The AI said no"
    CheckGroup.RuleNo -> "The rule said no"
    CheckGroup.Other -> "Held back"
}

private fun CheckGroup.shape() = when (this) {
    CheckGroup.Fired -> NodeShape.RoundedSquare
    CheckGroup.AiNo -> NodeShape.Diamond
    CheckGroup.RuleNo -> NodeShape.Slot
    CheckGroup.Other -> NodeShape.Circle
}

private fun CheckGroup.nodeState(running: Boolean) = when (this) {
    CheckGroup.Fired -> if (running) NodeState.Waiting else NodeState.Done
    else -> NodeState.Idle
}

/** The fire a "fired" check made (the same moment, give or take a few seconds). */
private fun fireOf(r: TraceRow, fires: List<WatchFire>): WatchFire? {
    if (r.outcome != "fired") return null
    val at = r.atMs ?: return null
    return fires.firstOrNull { f -> f.atMs?.let { kotlin.math.abs(it - at) < 5_000 } == true && (f.triggerId == null || f.triggerId == r.triggerId) }
}

private val EVENT_TEXT = listOf("text", "subject", "summary", "title")

/** The event's own words, when they say more than its summary. */
private fun eventText(r: TraceRow): String? {
    val d: JsonObject = r.data ?: return null
    return EVENT_TEXT.firstNotNullOfOrNull { k -> (d[k] as? JsonPrimitive)?.takeIf { it.isString }?.content?.trim()?.takeIf { it.isNotEmpty() && !r.summary.contains(it) } }
}

/** One check: who or what (and when), the event's words, why it went on or stopped; a fire's outcome. */
@Composable
private fun CheckRow(r: TraceRow, fire: WatchFire?, run: WatchRun?, now: ZonedDateTime, onOpenRun: (WatchRun) -> Unit) {
    val c = circuitColors
    Column(Modifier.fillMaxWidth().padding(top = 8.dp)) {
        Row(verticalAlignment = Alignment.Top) {
            Text(r.summary.ifBlank { r.eventType.orEmpty() }, style = MaterialTheme.typography.bodyLarge, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            r.atMs?.let { Text(whenWords(it, now).let { w -> if (w.length == 5) w else "$w ${clockWords(it, now.zone)}" }, style = MaterialTheme.typography.bodyMedium.tabular(), color = c.inkSoft, modifier = Modifier.padding(start = 12.dp)) }
        }
        eventText(r)?.let {
            Text(it, style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content), color = c.inkSoft, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        val reason = when {
            fire != null -> fireWords(fire)
            else -> reasonWords(r)
        }
        if (reason.isNotBlank()) Text(reason, style = MaterialTheme.typography.bodyMedium, color = c.ink.copy(alpha = 0.85f), maxLines = 3, overflow = TextOverflow.Ellipsis)
        val held = fire?.actions?.firstOrNull { it.status == "held" }
        if (held != null && run != null && run.waiting) {
            Row(
                Modifier.padding(top = 4.dp).clip(RoundedCornerShape(8.dp)).clickable { onOpenRun(run) }.heightIn(min = 40.dp).padding(end = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("Review it", style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold), color = c.accent)
                Icon(Icons.AutoMirrored.Outlined.KeyboardArrowRight, contentDescription = null, tint = c.accent, modifier = Modifier.size(18.dp))
            }
        }
    }
}

/** What a fire did, in words. */
private fun fireWords(f: WatchFire): String = f.actions.joinToString(" ") { a ->
    when (a.status) {
        "held" -> "Waiting for your OK."
        "failed" -> "It did not work${a.detail.takeIf { it.isNotBlank() }?.let { ": $it" } ?: ""}."
        "skipped" -> "Skipped${a.detail.takeIf { it.isNotBlank() }?.let { ": $it" } ?: ""}."
        else -> when (a.type) {
            "notify" -> "Told you."
            "create_task" -> "Added a reminder."
            "complete_task" -> "Completed the task."
            "agent", "tool" -> "Dana did it."
            else -> "Done."
        }
    }
}.ifBlank { "Fired." }

/** "Change this watch in words": compiled again by the server (`PATCH {change}`). */
@Composable
private fun ChangeField(busy: Boolean, onSend: (String, (Boolean) -> Unit) -> Unit) {
    val c = circuitColors
    var text by rememberSaveable { mutableStateOf("") }
    val send = { if (text.isNotBlank() && !busy) onSend(text) { ok -> if (ok) text = "" } }
    Row(
        Modifier
            .imePadding()
            .fillMaxWidth()
            .padding(horizontal = Space.screen, vertical = 8.dp)
            .clip(RoundedCornerShape(28.dp))
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .heightIn(min = 56.dp)
            .padding(start = 20.dp, end = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(Modifier.weight(1f).padding(vertical = 12.dp)) {
            if (text.isEmpty()) Text("Change this watch in words", style = MaterialTheme.typography.bodyLarge, color = c.mute)
            BasicTextField(
                value = text,
                onValueChange = { text = it },
                enabled = !busy,
                textStyle = MaterialTheme.typography.bodyLarge.copy(color = c.ink, textDirection = TextDirection.Content),
                cursorBrush = SolidColor(c.accent),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { send() }),
                maxLines = 4,
                modifier = Modifier.fillMaxWidth()
            )
        }
        if (busy) Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp, color = c.accent) }
        else IconButton(onClick = send, enabled = text.isNotBlank(), modifier = Modifier.clip(CircleShape)) {
            Icon(Icons.AutoMirrored.Outlined.Send, contentDescription = "Change it", tint = if (text.isNotBlank()) c.accent else c.mute)
        }
    }
}
