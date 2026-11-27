package io.github.ronganam.dana.ui.screens.watching

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.watches.Watch
import io.github.ronganam.dana.data.watches.WatchRun
import io.github.ronganam.dana.data.watches.circuitOf
import io.github.ronganam.dana.data.watches.quietUntil
import io.github.ronganam.dana.data.watches.summaryWords
import io.github.ronganam.dana.data.watches.whenWords
import io.github.ronganam.dana.ui.components.circuit.CircuitColumnHeader
import io.github.ronganam.dana.ui.components.circuit.CircuitRow
import io.github.ronganam.dana.ui.components.circuit.GroupedSection
import io.github.ronganam.dana.ui.components.circuit.LineNode
import io.github.ronganam.dana.ui.components.circuit.NodeLine
import io.github.ronganam.dana.ui.components.circuit.NodeShape
import io.github.ronganam.dana.ui.components.circuit.SectionHeader
import io.github.ronganam.dana.ui.components.circuit.askSlotInset
import io.github.ronganam.dana.ui.theme.Space
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.viewmodels.WatchingController
import kotlinx.coroutines.delay
import java.time.ZonedDateTime

/** How long a step of the engine keeps a watch's line pulsing. */
private const val PULSE_MS = 5_000L

/**
 * The Watching list: every watch as its circuit. A run waiting for the owner
 * comes first, its card open under its watch (Approve / Deny); a running one
 * next with its steps; then the others, paused ones dimmed; ended ones last.
 * [tracks]: unfolded, each line gets a track of today's events.
 */
@Composable
fun WatchingList(
    controller: WatchingController,
    tracks: Boolean,
    onOpen: (String) -> Unit,
    onNew: () -> Unit,
    onOpenRun: (WatchRun) -> Unit,
    modifier: Modifier = Modifier,
) {
    val watches by controller.watches.collectAsState()
    val loaded by controller.loaded.collectAsState()
    val failed by controller.failed.collectAsState()
    val runs by controller.runs.collectAsState()
    val steps by controller.steps.collectAsState()
    val drafts by controller.drafts.collectAsState()
    val today by controller.today.collectAsState()
    val pulses by controller.pulses.collectAsState()
    val acting by controller.acting.collectAsState()
    val c = circuitColors

    // A clock for the pulses: ticks only while one is fresh.
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(pulses) {
        now = System.currentTimeMillis()
        while (pulses.values.any { now - it < PULSE_MS }) {
            delay(1_000)
            now = System.currentTimeMillis()
        }
    }
    val zoned = ZonedDateTime.now()

    val withRuns = watches.filter { runs[it.id] != null }.sortedByDescending { runs[it.id]?.waiting == true }
    val rest = watches.filter { runs[it.id] == null && !it.isClosed }
        .sortedWith(compareBy<Watch> { it.isPaused }.thenByDescending { it.lastFiredMs ?: it.createdMs ?: 0 })
    val ended = watches.filter { runs[it.id] == null && it.isClosed }
    val needsYou = runs.values.count { it.waiting }

    val trackFrom = remember(today) {
        io.github.ronganam.dana.data.watches.trackStart(
            io.github.ronganam.dana.data.watches.trackMarks(today.values.flatten()), java.time.LocalDate.now(), java.time.ZoneId.systemDefault()
        )
    }
    androidx.compose.runtime.CompositionLocalProvider(LocalTrackStart provides trackFrom) {
    Column(
        modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Space.screen, vertical = 12.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.askSlotInset()) {
            Text("Watching", style = MaterialTheme.typography.displayMedium, modifier = Modifier.weight(1f).semantics { heading() })
            if (tracks) {
                Button(onClick = onNew, colors = ButtonDefaults.buttonColors(containerColor = c.accentFilled, contentColor = c.onAccentFilled), modifier = Modifier.height(48.dp)) {
                    Icon(Icons.Outlined.Add, contentDescription = null, modifier = Modifier.size(20.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("New watch", style = MaterialTheme.typography.titleMedium)
                }
            } else {
                IconButton(onClick = onNew, modifier = Modifier.size(48.dp)) {
                    Icon(Icons.Outlined.Add, contentDescription = "New watch", tint = c.ink)
                }
            }
        }
        val line = when {
            !loaded && failed -> "Couldn't reach Dana. Pull down later."
            !loaded -> "Loading…"
            watches.isEmpty() -> "Nothing yet."
            tracks -> summaryWords(watches, needsYou) + todayWords(today.values.flatten())
            else -> summaryWords(watches, needsYou)
        }
        Text(line, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(top = 4.dp).askSlotInset())

        if (loaded && watches.isEmpty()) EmptyWatching(onNew)
        if (watches.isNotEmpty()) {
        if (tracks) TrackHeader(Modifier.padding(top = 20.dp, bottom = 8.dp))
        else CircuitColumnHeader(Modifier.padding(top = 16.dp, bottom = 8.dp), startInset = 16.dp, endInset = 16.dp)

        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            for (w in withRuns) {
                val run = runs.getValue(w.id)
                GroupedSection {
                    item(key = w.id, color = if (run.waiting) c.warmSurface else null) {
                        RunCard(
                            watch = w,
                            run = run,
                            steps = steps[w.id].orEmpty(),
                            draft = drafts[w.id],
                            busy = run.activityId in acting,
                            wide = tracks,
                            todayRows = today[w.id].orEmpty(),
                            onOpen = { onOpen(w.id) },
                            onApprove = { controller.answer(run, true) },
                            onDeny = { controller.answer(run, false) },
                            onOpenRun = { onOpenRun(run) },
                        )
                    }
                }
            }
            if (rest.isNotEmpty()) GroupedSection {
                for (w in rest) item(key = w.id) {
                    val rows = today[w.id].orEmpty()
                    val live = watchLive(w, null, rows.any { it.outcome == "fired" }, (pulses[w.id]?.let { now - it < PULSE_MS }) == true)
                    WatchLine(w, live, rows, tracks, zoned) { onOpen(w.id) }
                }
            }
            if (ended.isNotEmpty()) {
                SectionHeader("Ended", Modifier.padding(top = 16.dp), meta = ended.size.toString())
                GroupedSection {
                    for (w in ended) item(key = w.id) { WatchLine(w, WatchLive.Closed, emptyList(), tracks, zoned) { onOpen(w.id) } }
                }
            }
        }
        if (tracks) TrackLegend(Modifier.padding(top = 16.dp))
        }
        Spacer(Modifier.height(24.dp))
    }
    }
}

/** ". Today: 71 checks, 4 reached the AI check, 3 fired." over every watch's checks today. */
private fun todayWords(rows: List<io.github.ronganam.dana.data.watches.TraceRow>): String {
    if (rows.isEmpty()) return ""
    val counts = io.github.ronganam.dana.data.watches.countChecks(rows)
    val ai = rows.count { it.outcome.startsWith("semantic") }
    return " Today: ${counts.checks} ${if (counts.checks == 1) "check" else "checks"}, $ai reached an AI check, ${counts.fired} fired."
}

/** One watch as a row: its circuit (and, unfolded, today's track before it). */
@Composable
private fun WatchLine(w: Watch, live: WatchLive, rows: List<io.github.ronganam.dana.data.watches.TraceRow>, tracks: Boolean, now: ZonedDateTime, onClick: () -> Unit) {
    val circuit = remember(w.spec) { circuitOf(w.parsed) }
    val nodes = circuitNodes(circuit, live)
    val time = w.lastFiredMs?.let { whenWords(it, now) }
    val state = stateWord(live, if (live == WatchLive.Idle || live == WatchLive.FiredToday) quietUntil(w.parsed, now) else null)
    if (tracks) {
        TrackedRow(w.title, time, nodes, state, rows, now, dimmed = live == WatchLive.Off || live == WatchLive.Closed, onClick = onClick)
    } else {
        CircuitRow(
            title = w.title,
            source = nodes.source, rule = nodes.rule, ai = nodes.ai, action = nodes.action,
            time = time, state = state.text, stateColor = state.color,
            dimmed = live == WatchLive.Off || live == WatchLive.Closed,
            onClick = onClick,
        )
    }
}

@Composable
private fun EmptyWatching(onNew: () -> Unit) {
    val c = circuitColors
    GroupedSection(Modifier.padding(top = 24.dp)) {
        item {
            Column(Modifier.padding(16.dp)) {
                Text("Each watch is a line", style = MaterialTheme.typography.titleMedium)
                Text(
                    "Something happens, a rule and Dana check it, then Dana acts or asks you.",
                    style = MaterialTheme.typography.bodyMedium, color = c.inkSoft,
                    modifier = Modifier.padding(top = 2.dp, bottom = 16.dp)
                )
                NodeLine(
                    listOf(
                        LineNode(NodeShape.Circle, label = "When"),
                        LineNode(NodeShape.Slot, label = "Rule"),
                        LineNode(NodeShape.Diamond, label = "AI check"),
                        LineNode(NodeShape.RoundedSquare, label = "Then")
                    ),
                    Modifier.fillMaxWidth()
                )
                Box(Modifier.fillMaxWidth().padding(top = 16.dp), contentAlignment = Alignment.CenterEnd) {
                    Button(onClick = onNew, colors = ButtonDefaults.buttonColors(containerColor = c.accentFilled, contentColor = c.onAccentFilled)) {
                        Text("New watch")
                    }
                }
            }
        }
    }
}
