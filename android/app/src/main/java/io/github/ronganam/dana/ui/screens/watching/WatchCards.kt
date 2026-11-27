package io.github.ronganam.dana.ui.screens.watching

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowRight
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.watches.RunStep
import io.github.ronganam.dana.data.watches.RunStepState
import io.github.ronganam.dana.data.watches.TraceRow
import io.github.ronganam.dana.data.watches.Watch
import io.github.ronganam.dana.data.watches.WatchRun
import io.github.ronganam.dana.data.watches.circuitOf
import io.github.ronganam.dana.data.watches.clockWords
import io.github.ronganam.dana.ui.components.circuit.CircuitRow
import io.github.ronganam.dana.ui.components.circuit.LineNode
import io.github.ronganam.dana.ui.components.circuit.NodeShape
import io.github.ronganam.dana.ui.components.circuit.NodeState
import io.github.ronganam.dana.ui.components.circuit.Step
import io.github.ronganam.dana.ui.components.circuit.StepLine
import io.github.ronganam.dana.ui.components.circuit.StepState
import io.github.ronganam.dana.ui.components.circuit.TimelineRow
import io.github.ronganam.dana.ui.theme.circuitColors
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * A watch with its run going on, open in the list: its circuit, then either
 * what Dana wants to do with Approve / Deny / Open run (a waiting run, on the
 * warm surface), or the run's steps with the current one live. [wide]: the
 * unfolded card (today's track on the line; the steps beside the request).
 */
@Composable
fun RunCard(
    watch: Watch,
    run: WatchRun,
    steps: List<RunStep>,
    draft: String?,
    busy: Boolean,
    wide: Boolean,
    todayRows: List<TraceRow>,
    onOpen: () -> Unit,
    onApprove: () -> Unit,
    onDeny: () -> Unit,
    onOpenRun: () -> Unit,
) {
    val c = circuitColors
    val circuit = remember(watch.spec) { circuitOf(watch.parsed) }
    val live = if (run.waiting) WatchLive.Waiting else WatchLive.Running
    val nodes = circuitNodes(circuit, live)
    val state = stateWord(live, null)
    val time = run.updatedMs?.let { clockWords(it, ZoneId.systemDefault()) }
    Column(Modifier.fillMaxWidth()) {
        if (wide) TrackedRow(watch.title, time, nodes, state, todayRows, ZonedDateTime.now(), dimmed = false, heldSince = if (run.waiting) run.updatedMs else null, onClick = onOpen)
        else CircuitRow(
            title = watch.title, source = nodes.source, rule = nodes.rule, ai = nodes.ai, action = nodes.action,
            time = if (narrowRow()) state.text else time, state = if (narrowRow()) null else state.text, stateColor = state.color,
            onClick = onOpen, columns = LocalCircuitColumns.current,
        )
        if (run.waiting) {
            if (wide && steps.size > 1) Row(Modifier.padding(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 16.dp), horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                StepList(steps, Modifier.weight(1f))
                RequestBlock(run, draft, busy, onApprove, onDeny, onOpenRun, Modifier.weight(1f))
            } else RequestBlock(run, draft, busy, onApprove, onDeny, onOpenRun, Modifier.padding(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 16.dp))
        } else if (steps.isNotEmpty()) {
            Box(
                Modifier
                    .padding(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 16.dp)
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(16.dp))
                    .background(MaterialTheme.colorScheme.surfaceContainerHigh)
                    .padding(horizontal = 8.dp, vertical = 12.dp)
            ) {
                StepLine(steps.map { Step(it.label, it.state.toStep()) }, Modifier.fillMaxWidth(), surface = MaterialTheme.colorScheme.surfaceContainerHigh)
            }
        } else {
            Text(run.text, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(start = 16.dp, end = 16.dp, bottom = 16.dp))
        }
    }
}

private fun RunStepState.toStep(): StepState = when (this) {
    RunStepState.Done -> StepState.Done
    RunStepState.Current, RunStepState.Waiting -> StepState.Current
    RunStepState.Failed -> StepState.Failed
}

/** What a waiting run wants to do, the words it would send, and the answer buttons. */
@Composable
internal fun RequestBlock(run: WatchRun, draft: String?, busy: Boolean, onApprove: () -> Unit, onDeny: () -> Unit, onOpenRun: () -> Unit, modifier: Modifier = Modifier) {
    val c = circuitColors
    Column(modifier) {
        Text(run.text, style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold), color = c.ink, maxLines = 4, overflow = TextOverflow.Ellipsis)
        if (draft != null) {
            Text(
                draft,
                style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content),
                color = c.ink,
                maxLines = 6,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier
                    .padding(top = 12.dp)
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(16.dp))
                    .background(c.warmSurfaceHigh)
                    .padding(16.dp)
            )
        }
        androidx.compose.foundation.layout.BoxWithConstraints(Modifier.padding(top = 16.dp).fillMaxWidth()) {
            // Narrow (the side pane, large text): Open run goes under the answers.
            val narrow = maxWidth < 330.dp
            val openRun = @Composable {
                TextButton(onClick = onOpenRun, modifier = Modifier.heightIn(min = 48.dp)) {
                    Text("Open run", style = MaterialTheme.typography.titleMedium, color = c.inkSoft, maxLines = 1)
                    Icon(Icons.AutoMirrored.Outlined.KeyboardArrowRight, contentDescription = null, tint = c.inkSoft, modifier = Modifier.size(20.dp))
                }
            }
            Column {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Button(
                        onClick = onApprove, enabled = !busy,
                        colors = ButtonDefaults.buttonColors(containerColor = c.accentFilled, contentColor = c.onAccentFilled),
                        modifier = Modifier.heightIn(min = 48.dp)
                    ) { Text("Approve", style = MaterialTheme.typography.titleMedium, maxLines = 1) }
                    Spacer(Modifier.width(8.dp))
                    TextButton(onClick = onDeny, enabled = !busy, modifier = Modifier.heightIn(min = 48.dp)) {
                        Text("Deny", style = MaterialTheme.typography.titleMedium, color = c.accent, maxLines = 1)
                    }
                    if (!narrow) {
                        Spacer(Modifier.weight(1f))
                        openRun()
                    }
                }
                if (narrow) Box(Modifier.padding(top = 4.dp)) { openRun() }
            }
        }
    }
}

/** A run's steps down a line (unfolded beside its request). */
@Composable
private fun StepList(steps: List<RunStep>, modifier: Modifier = Modifier) {
    Column(modifier.padding(top = 4.dp)) {
        steps.forEachIndexed { i, s ->
            val state = when (s.state) {
                RunStepState.Done -> NodeState.Passed
                RunStepState.Current -> NodeState.Firing
                RunStepState.Waiting -> NodeState.Waiting
                RunStepState.Failed -> NodeState.Dropped
            }
            TimelineRow(
                time = null,
                node = LineNode(if (s.state == RunStepState.Waiting) NodeShape.RoundedSquare else NodeShape.Circle, state),
                title = s.label,
                first = i == 0,
                last = i == steps.lastIndex,
                timeColumn = 0.dp,
            )
        }
    }
}
