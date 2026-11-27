package io.github.ronganam.dana.ui.screens.calls

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.ui.draw.clip
import io.github.ronganam.dana.data.models.CallSuggestion
import io.github.ronganam.dana.data.models.PhoneCall
import io.github.ronganam.dana.ui.components.LabelChips
import io.github.ronganam.dana.ui.components.MetaChips
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.components.SuggestionRow
import io.github.ronganam.dana.ui.theme.Danger

/**
 * The call "document": serif title, optional player, summary, key points and an editorial
 * dialogue transcript. [showPlayer]/[showKeyPoints] are false when the actions pane carries them.
 */
@Composable
internal fun CallDetailPane(
    call: PhoneCall,
    player: CallAudioPlayer,
    showPlayer: Boolean,
    showKeyPoints: Boolean,
    onReanalyze: () -> Unit,
    onDelete: () -> Unit,
    modifier: Modifier = Modifier,
    suggestions: List<CallSuggestion> = emptyList(),
    busySuggestions: Set<String> = emptySet(),
    onAccept: (CallSuggestion) -> Unit = {},
    onDismiss: (CallSuggestion) -> Unit = {},
    onAskSuggestion: (CallSuggestion) -> Unit = {},
    onEditLabels: () -> Unit = {},
    onOpenDebrief: () -> Unit = {},
    onAsk: () -> Unit = {}
) {
    val turns = remember(call.transcript) { call.transcript?.let(::parseTranscript).orEmpty() }
    val keyPoints = remember(call.summary) { call.summary?.let(::extractKeyPoints).orEmpty() }
    val summary = remember(call.summary) { call.summary?.let(::parseMarkdownToAnnotatedString) }
    val speakers = remember(turns) { turns.mapNotNull { it.speaker }.distinct() }

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 8.dp, bottom = 96.dp)
    ) {
        item(key = "head") {
            Column(Modifier.fillMaxWidth()) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    MetaLabel(
                        text = buildString {
                            append(call.fullDateLabel())
                            if (call.durationSeconds > 0) append(" · ").append(formatDuration(call.durationSeconds))
                        },
                        color = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.weight(1f)
                    )
                    IconButton(onClick = onReanalyze) {
                        Icon(Icons.Filled.Refresh, contentDescription = "Regenerate summary", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    IconButton(onClick = onDelete) {
                        Icon(Icons.Filled.Delete, contentDescription = "Delete call", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                RtlSupportingText(
                    text = call.displayTitle(),
                    color = MaterialTheme.colorScheme.onSurface,
                    style = MaterialTheme.typography.headlineLarge
                )
                Spacer(Modifier.height(6.dp))
                MetaLabel(buildString {
                    append("${call.caller}  →  ${call.receiver}")
                    call.direction?.let { append(" · ").append(it) }
                })
                Spacer(Modifier.height(10.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    if (call.labels.isEmpty()) MetaLabel("No labels", modifier = Modifier.padding(end = 4.dp))
                    LabelChips(call.labels, Modifier.weight(1f, fill = false))
                    IconButton(onClick = onEditLabels) {
                        Icon(Icons.Outlined.Edit, contentDescription = "Edit labels",
                            tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp))
                    }
                }
                Row(Modifier.padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = onAsk) {
                        Icon(Icons.AutoMirrored.Outlined.Chat, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("Ask about it")
                    }
                    TextButton(onClick = onOpenDebrief) { Text("Debrief") }
                }
                Spacer(Modifier.height(12.dp))
            }
        }

        call.securityAlert?.let { alert ->
            item(key = "alert") {
                Row(
                    Modifier.fillMaxWidth().padding(bottom = 8.dp).clip(RoundedCornerShape(10.dp))
                        .background(Danger.copy(alpha = 0.12f)).padding(14.dp)
                ) {
                    Text("🔐", fontSize = 18.sp)
                    Spacer(Modifier.width(10.dp))
                    RtlSupportingText(alert, color = MaterialTheme.colorScheme.onSurface)
                }
            }
        }

        if (call.status == "processing" || call.status == "failed") {
            item(key = "status") {
                PendingNote(
                    if (call.status == "processing") "Analysing — summary, labels and suggestions arrive in a minute."
                    else call.error ?: "This recording couldn't be analysed."
                )
            }
        }

        val pending = suggestions.filter { it.status == "pending" }
        if (pending.isNotEmpty()) {
            item(key = "suggestions") {
                SectionHead("Suggested · ${pending.size}")
                pending.forEach { s ->
                    SuggestionRow(
                        suggestion = s,
                        busy = s.id in busySuggestions,
                        onAccept = { onAccept(s) },
                        onAsk = { onAskSuggestion(s) },
                        onDismiss = { onDismiss(s) },
                        onOpen = onOpenDebrief,
                        showSource = false
                    )
                }
            }
        }

        if (showPlayer) {
            item(key = "player") {
                CallPlayer(call, player, Modifier.padding(bottom = 8.dp))
            }
        }

        item(key = "summary") {
            SectionHead("Summary")
            if (summary != null) {
                RtlSupportingText(
                    text = summary,
                    color = MaterialTheme.colorScheme.onSurface,
                    style = MaterialTheme.typography.bodyLarge.copy(lineHeight = 25.sp)
                )
            } else {
                PendingNote("Analysis pending — the summary appears once processing finishes.")
            }
        }

        if (showKeyPoints && call.keyFacts.isNotEmpty()) {
            item(key = "facts") {
                SectionHead("Key facts")
                MetaChips(call.keyFacts.map { it.text })
            }
        } else if (showKeyPoints && keyPoints.isNotEmpty()) {
            item(key = "points") {
                SectionHead("Follow-ups")
                FollowUpList(keyPoints)
            }
        }

        item(key = "transcript_head") {
            SectionHead(if (turns.isEmpty()) "Transcript" else "Transcript · ${turns.size} turns")
            if (call.transcript == null) PendingNote("No transcript yet.")
        }
        items(turns.size, key = { "turn_$it" }) { i ->
            val t = turns[i]
            TurnRow(t, speakerIndex = t.speaker?.let { speakers.indexOf(it) } ?: 0)
        }
    }
}

/** Third pane (≥ ~900dp): playback, follow-ups and call facts. */
@Composable
internal fun CallActionsPane(call: PhoneCall, player: CallAudioPlayer, modifier: Modifier = Modifier) {
    val keyPoints = remember(call.summary) { call.summary?.let(::extractKeyPoints).orEmpty() }
    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 18.dp, vertical = 16.dp)
    ) {
        MetaLabel("Playback", color = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.height(12.dp))
        CallPlayer(call, player)

        Spacer(Modifier.height(28.dp))
        if (call.keyFacts.isNotEmpty()) {
            SectionHead("Key facts")
            MetaChips(call.keyFacts.map { it.text })
        } else {
            SectionHead("Follow-ups")
            if (keyPoints.isNotEmpty()) FollowUpList(keyPoints)
            else PendingNote("No action items in this summary.")
        }
        if (call.participants.isNotEmpty()) {
            Spacer(Modifier.height(28.dp))
            SectionHead("People")
            call.participants.forEach { p -> Fact(p.role.ifBlank { "Participant" }, p.name) }
        }

        Spacer(Modifier.height(28.dp))
        SectionHead("Details")
        Fact("With", call.contactName)
        Fact("Caller", call.caller)
        Fact("Receiver", call.receiver)
        Fact("Date", call.fullDateLabel())
        if (call.durationSeconds > 0) Fact("Length", formatDuration(call.durationSeconds))
        Fact("File", call.filename)
    }
}

@Composable
private fun SectionHead(text: String) {
    Column(Modifier.fillMaxWidth().padding(top = 20.dp, bottom = 10.dp)) {
        MetaLabel(text, color = MaterialTheme.colorScheme.onSurface)
        Spacer(Modifier.height(6.dp))
        Rule()
    }
}

@Composable
private fun PendingNote(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodyMedium,
        fontStyle = FontStyle.Italic,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(vertical = 4.dp)
    )
}

@Composable
internal fun FollowUpList(points: List<String>) {
    Column(Modifier.fillMaxWidth()) {
        points.forEachIndexed { i, p ->
            Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.Top) {
                MetaLabel((i + 1).toString().padStart(2, '0'), color = MaterialTheme.colorScheme.primary, modifier = Modifier.width(30.dp).padding(top = 3.dp))
                RtlSupportingText(
                    text = p,
                    color = MaterialTheme.colorScheme.onSurface,
                    style = MaterialTheme.typography.bodyMedium.copy(lineHeight = 21.sp)
                )
            }
            if (i < points.lastIndex) Rule()
        }
    }
}

@Composable
private fun Fact(label: String, value: String) {
    Column(Modifier.fillMaxWidth().padding(vertical = 7.dp)) {
        MetaLabel(label)
        Spacer(Modifier.height(2.dp))
        Text(value, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurface)
    }
}

/** One dialogue turn: mono speaker label, hairline spine (vermilion for the first speaker). */
@Composable
private fun TurnRow(turn: TranscriptTurn, speakerIndex: Int) {
    val spine = if (speakerIndex == 0) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline
    Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min).padding(vertical = 8.dp)) {
        Box(Modifier.width(2.dp).fillMaxHeight().background(spine))
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            if (turn.speaker != null) {
                MetaLabel(turn.speaker, color = if (speakerIndex == 0) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(3.dp))
            }
            RtlSupportingText(
                text = turn.text,
                color = MaterialTheme.colorScheme.onSurface,
                style = MaterialTheme.typography.bodyMedium.copy(lineHeight = 22.sp, fontSize = 15.sp)
            )
        }
    }
}

@Composable
internal fun DeleteCallConfirmDialog(onConfirm: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Delete recording?") },
        text = { Text("This call, its summary and transcript will be permanently deleted.") },
        confirmButton = { TextButton(onClick = onConfirm) { Text("Delete", color = MaterialTheme.colorScheme.error) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

@Composable
internal fun ReanalyzeDialog(call: PhoneCall, onDismiss: () -> Unit, onReanalyze: (String?) -> Unit) {
    var instructions by rememberSaveable { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Regenerate summary") },
        text = {
            Column {
                Text(
                    text = "Call with ${call.contactName}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = instructions,
                    onValueChange = { instructions = it },
                    label = { Text("Custom instructions (optional)") },
                    placeholder = { Text("e.g. focus on action items for tomorrow") },
                    modifier = Modifier.fillMaxWidth(),
                    minLines = 2,
                    maxLines = 4
                )
            }
        },
        confirmButton = { TextButton(onClick = { onReanalyze(instructions.trim().ifEmpty { null }) }) { Text("Regenerate") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}
