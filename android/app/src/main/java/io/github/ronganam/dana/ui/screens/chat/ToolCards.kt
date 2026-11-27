package io.github.ronganam.dana.ui.screens.chat

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Bolt
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.chat.ApprovalKind
import io.github.ronganam.dana.data.chat.ThreadItem
import io.github.ronganam.dana.data.chat.ToolStep
import io.github.ronganam.dana.data.chat.approvalView
import io.github.ronganam.dana.data.chat.resultWords
import io.github.ronganam.dana.data.models.ToolCallEntry
import io.github.ronganam.dana.ui.components.circuit.LocalNodeSurface
import io.github.ronganam.dana.ui.components.circuit.Step
import io.github.ronganam.dana.ui.components.circuit.StepLine
import io.github.ronganam.dana.ui.components.circuit.StepLineMini
import io.github.ronganam.dana.ui.components.circuit.StepState
import io.github.ronganam.dana.ui.theme.DmMono
import io.github.ronganam.dana.ui.theme.circuitColors

/** Widest a conversation card grows (the unfolded sheet, kiosk, TV). */
val CARD_MAX = 560.dp
private val CARD_SHAPE = RoundedCornerShape(20.dp)

/**
 * What Dana did in one turn: its tool calls as a card. One step: a line
 * with a tiny step node and the step in words ("Added a reminder"); more:
 * the run's [StepLine] (the last four), the step in progress in ink. Under
 * it the last step's answer when it reads as words, and Details (the raw
 * arguments and answers) at the end.
 */
@Composable
fun ToolRunCard(item: ThreadItem.Tools, modifier: Modifier = Modifier, reply: (@Composable () -> Unit)? = null) {
    val c = circuitColors
    val steps = item.steps
    val surface = MaterialTheme.colorScheme.surfaceContainerLow
    var open by rememberSaveable(item.key) { mutableStateOf(false) }
    // A Tools item always holds at least one call (ThreadItem.of); no early return in a composable.
    val last = steps.last()
    Column(
        modifier
            .widthIn(max = CARD_MAX)
            .fillMaxWidth()
            .clip(CARD_SHAPE)
            .background(surface)
            .animateContentSize()
            .padding(start = 16.dp, end = 8.dp, top = 12.dp, bottom = 4.dp)
    ) {
        CompositionLocalProvider(LocalNodeSurface provides surface) {
            val done = steps.count { it.state != ToolStep.State.Running }
            if (steps.size == 1) {
                Row(Modifier.padding(end = 8.dp).heightIn(min = 24.dp), verticalAlignment = Alignment.CenterVertically) {
                    StepLineMini(done = done, total = 1, width = 16.dp)
                    Spacer(Modifier.width(10.dp))
                    Text(
                        last.label,
                        style = MaterialTheme.typography.bodyMedium.copy(fontSize = 14.sp),
                        color = if (last.state == ToolStep.State.Failed) c.warm else c.inkSoft,
                        maxLines = 2, overflow = TextOverflow.Ellipsis
                    )
                }
            } else {
                StepLine(
                    steps.takeLast(4).map { Step(it.label, it.state.toStep()) },
                    Modifier.fillMaxWidth().padding(end = 8.dp, top = 4.dp),
                    surface = surface
                )
                if (steps.size > 4) Text(
                    "${steps.size} steps",
                    style = MaterialTheme.typography.bodySmall, color = c.mute,
                    modifier = Modifier.padding(top = 6.dp)
                )
            }
        }
        // Dana's reply after the steps, else what the last step said when it reads as words.
        if (reply != null) Box(Modifier.padding(top = 8.dp, end = 8.dp)) { reply() }
        val answer = if (reply != null) null else last.takeIf { it.state == ToolStep.State.Done }?.let { resultWords(it.entry.response) }
        if (answer != null) Text(
            answer,
            style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
            color = c.ink,
            modifier = Modifier.padding(top = 10.dp, end = 8.dp)
        )
        if (last.state == ToolStep.State.Failed && reply == null) Text(
            "That didn't work.",
            style = MaterialTheme.typography.bodyMedium, color = c.inkSoft,
            modifier = Modifier.padding(top = 8.dp, end = 8.dp)
        )
        if (open) Column(Modifier.padding(top = 10.dp, end = 8.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            steps.forEach { s -> StepDetails(s) }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            SmallTextAction(if (open) "Hide details" else "Details", c.inkSoft) { open = !open }
        }
    }
}

private fun ToolStep.State.toStep(): StepState = when (this) {
    ToolStep.State.Done -> StepState.Done
    ToolStep.State.Running -> StepState.Current
    ToolStep.State.Failed -> StepState.Failed
}

/** One step's tool, arguments and answer as they are (for the curious; mono, scrolls). */
@Composable
private fun StepDetails(step: ToolStep) {
    val c = circuitColors
    val e = step.entry
    Column {
        Text(e.toolName, style = MaterialTheme.typography.labelLarge, color = c.inkSoft)
        listOf(e.parameters, e.response).filter { it.isNotBlank() && it != "{}" }.forEach { text ->
            Text(
                text,
                fontFamily = DmMono, fontSize = 11.sp, lineHeight = 16.sp,
                color = c.inkSoft,
                modifier = Modifier
                    .padding(top = 4.dp)
                    .fillMaxWidth()
                    .heightIn(max = 160.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(MaterialTheme.colorScheme.surfaceContainerHigh)
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 10.dp, vertical = 8.dp)
            )
        }
    }
}

/**
 * A call that waits for the user's OK, in the warm card: what it will do
 * ("Send a WhatsApp message to Noa?"), why it waits, the words it would send
 * in their own bubble, its other details, then the answer: the filled
 * "Send" / "Delete" / "Go ahead" and the quiet "Don't send" / "Keep it" / "Don't".
 */
@Composable
fun ApprovalCard(entry: ToolCallEntry, onAnswer: (Boolean) -> Unit, modifier: Modifier = Modifier) {
    val c = circuitColors
    val view = remember(entry.toolName, entry.toolArgs, entry.approvalCard, entry.approvalReason) {
        approvalView(entry.toolName, entry.toolArgs.ifEmpty { entry.parameters }, entry.approvalCard, entry.approvalReason)
    }
    Column(
        modifier
            .widthIn(max = CARD_MAX)
            .fillMaxWidth()
            .clip(CARD_SHAPE)
            .background(c.warmSurface)
            .padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 8.dp)
    ) {
        Row(verticalAlignment = Alignment.Top) {
            Box(
                Modifier.size(32.dp).clip(RoundedCornerShape(10.dp)).background(c.warm),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    when (view.kind) {
                        ApprovalKind.Send -> Icons.AutoMirrored.Outlined.Chat
                        ApprovalKind.Delete -> Icons.Outlined.DeleteOutline
                        ApprovalKind.Other -> Icons.Outlined.Bolt
                    },
                    contentDescription = null, tint = c.onWarm, modifier = Modifier.size(18.dp)
                )
            }
            Column(Modifier.padding(start = 12.dp).weight(1f)) {
                Text(
                    view.title,
                    style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold, textDirection = TextDirection.Content),
                    color = c.ink
                )
                Text(view.note, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.padding(top = 2.dp))
            }
        }
        view.body?.let { body ->
            Text(
                body,
                style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content),
                color = c.ink,
                maxLines = 8,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier
                    .padding(top = 12.dp)
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(16.dp))
                    .background(c.warmSurfaceHigh)
                    .padding(horizontal = 14.dp, vertical = 12.dp)
            )
        }
        if (view.details.isNotEmpty()) Column(Modifier.padding(top = 10.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            view.details.take(5).forEach { (label, value) ->
                Row {
                    Text(label, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft, modifier = Modifier.width(96.dp), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(
                        value,
                        style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
                        color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f)
                    )
                }
            }
        }
        Row(Modifier.padding(top = 12.dp, bottom = 4.dp).fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .heightIn(min = 48.dp)
                    .clip(RoundedCornerShape(24.dp))
                    .background(c.accentFilled)
                    .clickable(role = Role.Button) { onAnswer(true) }
                    .padding(horizontal = 24.dp, vertical = 12.dp),
                contentAlignment = Alignment.Center
            ) { Text(view.yes, style = MaterialTheme.typography.titleMedium, color = c.onAccentFilled, maxLines = 1) }
            Spacer(Modifier.width(8.dp))
            SmallTextAction(view.no, c.warm, minHeight = 48) { onAnswer(false) }
        }
    }
}

@Composable
private fun SmallTextAction(text: String, color: Color, minHeight: Int = 40, onClick: () -> Unit) {
    Box(
        Modifier
            .heightIn(min = minHeight.dp)
            .clip(RoundedCornerShape(20.dp))
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 8.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(text, style = if (minHeight >= 48) MaterialTheme.typography.titleMedium else MaterialTheme.typography.labelLarge, color = color, maxLines = 1)
    }
}
