package app.ganam.assistant.ui.screens.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.ganam.assistant.data.models.CallSuggestion
import app.ganam.assistant.data.models.Memory
import app.ganam.assistant.data.models.Task
import app.ganam.assistant.ui.adaptive.*
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.components.Rule
import app.ganam.assistant.ui.components.SuggestionRow
import androidx.compose.ui.platform.LocalContext
import app.ganam.assistant.ui.screens.ChatScreen
import app.ganam.assistant.ui.viewmodels.MainViewModel
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/**
 * Chat adapts to the window: compact = conversation only; medium = sessions | conversation;
 * expanded (unfolded landscape / tablet) = sessions | conversation | context.
 */
@Composable
fun ChatWorkspace(viewModel: MainViewModel) {
    val tier = windowTier()
    LaunchedEffect(Unit) {
        viewModel.fetchSessions()
        viewModel.fetchTasks()
        viewModel.fetchMemories()
    }
    val wide = windowWidthDp()
    var contextOpen by rememberSaveable { mutableStateOf(wide >= 960) }
    when (tier) {
        WindowTier.COMPACT -> ChatScreen(viewModel)
        WindowTier.MEDIUM -> Column(Modifier.fillMaxSize()) {
            ChatHeader(viewModel, onToggleContext = null, contextOpen = false)
            PaneRow(Modifier.weight(1f)) {
                Pane(width = sidePaneWidth(), tinted = true) { SessionsPane(viewModel) }
                Pane(divider = false) { ChatScreen(viewModel, embedded = true, showTopBar = false) }
            }
        }
        WindowTier.EXPANDED -> Column(Modifier.fillMaxSize()) {
            // Roomy (>=960dp): SIDE sessions | MAIN conversation | ASIDE context. Tighter (800-959dp):
            // context swaps in for the sessions list so the conversation keeps >= ~420dp.
            val roomy = wide >= 960
            ChatHeader(viewModel, onToggleContext = { contextOpen = !contextOpen }, contextOpen = contextOpen)
            PaneRow(Modifier.weight(1f)) {
                if (roomy || !contextOpen) {
                    Pane(width = sidePaneWidth(), tinted = true) { SessionsPane(viewModel) }
                } else {
                    Pane(width = sidePaneWidth(), tinted = true) { ContextPane(viewModel) }
                }
                Pane(divider = roomy && contextOpen) { ChatScreen(viewModel, embedded = true, showTopBar = false) }
                if (roomy && contextOpen) Pane(width = asidePaneWidth(), divider = false, tinted = true) { ContextPane(viewModel) }
            }
        }
    }
}

private val isoParser get() = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US)

internal fun parseDue(due: String?): Date? =
    if (due == null) null else try { isoParser.parse(due) } catch (e: Exception) { null }

internal fun formatDue(date: Date): String {
    val now = Calendar.getInstance()
    val d = Calendar.getInstance().apply { time = date }
    return when {
        now.get(Calendar.YEAR) == d.get(Calendar.YEAR) && now.get(Calendar.DAY_OF_YEAR) == d.get(Calendar.DAY_OF_YEAR) ->
            SimpleDateFormat("HH:mm", Locale.getDefault()).format(date)
        else -> SimpleDateFormat("EEE d", Locale.getDefault()).format(date)
    }
}

internal fun upcoming(tasks: List<Task>, limit: Int): List<Pair<Task, Date?>> =
    tasks.filter { it.status != "completed" }
        .map { it to parseDue(it.dueDate) }
        .sortedBy { it.second?.time ?: Long.MAX_VALUE }
        .take(limit)

@Composable
private fun SessionsPane(viewModel: MainViewModel) {
    val all by viewModel.sessions.collectAsState()
    val currentId by viewModel.currentSessionId.collectAsState()
    // Hide never-used conversations (keep the open one).
    val sessions = remember(all, currentId) {
        all.filter { it.id == currentId || !(it.snippet.equals("Empty chat", true) || it.snippet.isBlank()) }
    }
    Column(Modifier.fillMaxSize()) {
        Box(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 14.dp)) {
            MonoLabel("${sessions.size} conversations")
        }
        Rule()
        LazyColumn(Modifier.fillMaxSize()) {
            items(sessions, key = { it.id }) { s ->
                val selected = s.id == currentId
                val accent = MaterialTheme.colorScheme.primary
                Column(
                    Modifier
                        .fillMaxWidth()
                        .clickable { viewModel.loadSession(s.id) }
                        .background(if (selected) MaterialTheme.colorScheme.surfaceContainerHigh else androidx.compose.ui.graphics.Color.Transparent)
                ) {
                    Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
                        Box(Modifier.width(3.dp).fillMaxHeight().background(if (selected) accent else androidx.compose.ui.graphics.Color.Transparent))
                        Column(Modifier.padding(horizontal = 17.dp, vertical = 14.dp)) {
                            Text(
                                s.snippet.ifBlank { "Untitled" },
                                style = MaterialTheme.typography.bodyMedium.copy(textDirection = androidx.compose.ui.text.style.TextDirection.Content),
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis
                            )
                            if (s.timestamp > 0) {
                                Spacer(Modifier.height(4.dp))
                                MonoLabel(SimpleDateFormat("d MMM · HH:mm", Locale.getDefault()).format(Date(s.timestamp)))
                            }
                        }
                    }
                    Rule()
                }
            }
        }
    }
}

@Composable
private fun ContextPane(viewModel: MainViewModel) {
    val tasks by viewModel.tasks.collectAsState()
    val memories by viewModel.memories.collectAsState()
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(bottom = 24.dp)
    ) {
        item {
            Box(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 14.dp)) { MonoLabel("Alongside") }
            Rule()
        }
        item { WaitingOnYou(viewModel, horizontalPadding = 20.dp, limit = 5) }
        item { ContextHeading("Coming up") }
        val next = upcoming(tasks, 6)
        if (next.isEmpty()) {
            item { ContextEmpty("Nothing pending.") }
        } else {
            items(next) { (task, due) ->
                Column {
                    Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        if (due != null) Box(Modifier.width(52.dp)) {
                            MonoLabel(formatDue(due), color = MaterialTheme.colorScheme.primary)
                        }
                        Text(
                            task.title,
                            style = MaterialTheme.typography.bodyMedium.copy(textDirection = androidx.compose.ui.text.style.TextDirection.Content),
                            maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f)
                        )
                    }
                    Rule(Modifier.padding(start = 20.dp))
                }
            }
        }
        item { ContextHeading("Remembered") }
        val recent = memories.sortedByDescending { it.updatedAt ?: "" }.take(6)
        if (recent.isEmpty()) {
            item { ContextEmpty("Nothing saved yet.") }
        } else {
            items(recent, key = { it.key }) { m -> MemoryLine(m) }
        }
    }
}

@Composable
private fun ContextHeading(text: String) {
    Box(Modifier.padding(start = 20.dp, end = 20.dp, top = 20.dp, bottom = 6.dp)) { MonoLabel(text) }
}

@Composable
private fun ContextEmpty(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.titleMedium.copy(
            fontFamily = app.ganam.assistant.ui.theme.GanamSerif, fontStyle = FontStyle.Italic
        ),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp)
    )
}

@Composable
private fun MemoryLine(m: Memory) {
    Column {
        Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp)) {
            MonoLabel(m.key.replace('_', ' '))
            Spacer(Modifier.height(2.dp))
            Text(m.value, style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        Rule(Modifier.padding(start = 20.dp))
    }
}

/**
 * "Waiting on you": reminders, events and alerts suggested from recent calls.
 * Each row creates it (✓), opens a chat with it attached to change it (💬)
 * or dismisses it (✕); tapping the row opens the call's Debrief. Hidden when
 * nothing is pending.
 */
@Composable
fun WaitingOnYou(viewModel: MainViewModel, modifier: Modifier = Modifier, horizontalPadding: androidx.compose.ui.unit.Dp = 0.dp, limit: Int = 3) {
    val suggestions by viewModel.callSuggestions.collectAsState()
    val busy by viewModel.busySuggestionIds.collectAsState()
    val context = LocalContext.current
    var expanded by rememberSaveable { mutableStateOf(false) }
    if (suggestions.isEmpty()) return
    val shown = if (expanded) suggestions else suggestions.take(limit)
    Column(modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().padding(start = horizontalPadding, end = horizontalPadding, top = 20.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            MonoLabel("Waiting on you", color = MaterialTheme.colorScheme.primary, modifier = Modifier.weight(1f))
            MonoLabel("${suggestions.size} from calls")
        }
        Rule()
        shown.forEach { s: CallSuggestion ->
            SuggestionRow(
                suggestion = s,
                busy = s.id in busy,
                onAccept = { viewModel.acceptSuggestion(s) },
                onAsk = { viewModel.openContextChat("suggestion", s.id) },
                onDismiss = { viewModel.dismissSuggestion(s) },
                onOpen = { app.ganam.assistant.CallDebriefActivity.open(context, s.callId) },
                horizontalPadding = horizontalPadding
            )
        }
        if (suggestions.size > limit) {
            TextButton(
                onClick = { expanded = !expanded },
                modifier = Modifier.padding(start = horizontalPadding - 12.dp.coerceAtMost(horizontalPadding))
            ) { Text(if (expanded) "Show less" else "Show all ${suggestions.size}") }
        }
    }
}

/** Empty-conversation state: today's date, what's waiting, what's coming up, and a few things to say. */
@Composable
fun TodayBriefing(
    tasks: List<Task>,
    onPrompt: (String) -> Unit,
    modifier: Modifier = Modifier,
    waiting: (@Composable () -> Unit)? = null
) {
    val now = remember { Date() }
    val hour = remember { Calendar.getInstance().get(Calendar.HOUR_OF_DAY) }
    val greeting = when { hour < 5 -> "Late night."; hour < 12 -> "Good morning."; hour < 18 -> "Good afternoon."; else -> "Good evening." }
    val next = remember(tasks) { upcoming(tasks, 3) }
    Column(modifier.fillMaxWidth().padding(top = 28.dp, bottom = 12.dp)) {
        MonoLabel(SimpleDateFormat("EEEE", Locale.getDefault()).format(now), color = MaterialTheme.colorScheme.primary)
        Text(SimpleDateFormat("d MMMM", Locale.getDefault()).format(now), style = MaterialTheme.typography.displayMedium)
        Text(
            greeting,
            style = MaterialTheme.typography.headlineSmall.copy(
                fontFamily = app.ganam.assistant.ui.theme.GanamSerif, fontStyle = FontStyle.Italic
            ),
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        if (waiting != null) {
            Spacer(Modifier.height(8.dp))
            waiting()
        }
        Spacer(Modifier.height(28.dp))
        MonoLabel("Coming up")
        Spacer(Modifier.height(6.dp))
        Rule()
        if (next.isEmpty()) {
            Text(
                "A clear desk.",
                style = MaterialTheme.typography.titleLarge.copy(
                    fontFamily = app.ganam.assistant.ui.theme.GanamSerif, fontStyle = FontStyle.Italic
                ),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(vertical = 14.dp)
            )
            Rule()
        } else {
            next.forEach { (task, due) ->
                Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (due != null) Box(Modifier.width(56.dp)) { MonoLabel(formatDue(due), color = MaterialTheme.colorScheme.primary) }
                    Text(
                        task.title,
                        style = MaterialTheme.typography.bodyLarge.copy(textDirection = androidx.compose.ui.text.style.TextDirection.Content),
                        maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f)
                    )
                }
                Rule()
            }
        }
        Spacer(Modifier.height(28.dp))
        MonoLabel("Try saying")
        Spacer(Modifier.height(6.dp))
        Rule()
        listOf("What's on my plate today?", "Remind me to…", "What did I agree to on my last call?", "Remember that…").forEach { prompt ->
            Row(
                Modifier.fillMaxWidth().clickable { onPrompt(prompt) }.heightIn(min = 48.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(prompt, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
                Text("↗", color = MaterialTheme.colorScheme.primary)
            }
            Rule()
        }
    }
}
