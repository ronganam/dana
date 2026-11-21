package io.github.ronganam.dana

import io.github.ronganam.dana.data.network.SuggestionEdits
import io.github.ronganam.dana.data.network.ServerConfig
import android.app.Application
import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.ronganam.dana.data.models.CallDetail
import io.github.ronganam.dana.data.models.CallLabel
import io.github.ronganam.dana.data.models.CallSuggestion
import io.github.ronganam.dana.ui.components.*
import io.github.ronganam.dana.ui.screens.calls.RtlSupportingText
import io.github.ronganam.dana.ui.screens.calls.formatDuration
import io.github.ronganam.dana.ui.screens.calls.fullDateLabel
import io.github.ronganam.dana.ui.screens.calls.parseMarkdownToAnnotatedString
import io.github.ronganam.dana.ui.theme.Danger
import io.github.ronganam.dana.ui.theme.DanaTheme
import io.github.ronganam.dana.ui.theme.ThemeSettings
import io.github.ronganam.dana.ui.viewmodels.describeError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * The call Debrief: opened from the "call analysed" notification or any call.
 * Summary, labels, key facts, and the call's suggestions as cards you act on
 * in one tap — Create, Edit, Ask the assistant (opens a chat with the
 * suggestion attached) or Dismiss.
 */
class CallDebriefActivity : ComponentActivity() {

    private val vm: DebriefViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        ThemeSettings.load(applicationContext)
        vm.load(intent.getStringExtra(EXTRA_CALL_ID).orEmpty())
        setContent {
            DanaTheme {
                DebriefScreen(
                    vm = vm,
                    onClose = { finish() },
                    onAsk = { kind, id -> openInApp(EXTRA_CONTEXT_KIND to kind, EXTRA_CONTEXT_ID to id, EXTRA_CONTEXT_TITLE to askTitle(kind, id)) },
                    onOpenCall = { openInApp(EXTRA_OPEN_CALL_ID to it) }
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.getStringExtra(EXTRA_CALL_ID)?.let { vm.load(it) }
    }

    /** The name the thread's "On screen ·" line shows (the main app may not have the call loaded yet). */
    private fun askTitle(kind: String, id: String): String {
        val d = vm.detail.value ?: return ""
        return when (kind) {
            "call" -> d.call.title ?: d.call.caller
            "suggestion" -> d.suggestions.firstOrNull { it.id == id }?.title
            else -> null
        }.orEmpty()
    }

    /** Hand over to the main app (the shared thread with something on screen, or the call). */
    private fun openInApp(vararg extras: Pair<String, String>) {
        val i = Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        extras.forEach { (k, v) -> i.putExtra(k, v) }
        startActivity(i)
        finish()
    }

    companion object {
        const val EXTRA_CALL_ID = "call_id"
        /** MainActivity extras: open a chat with [EXTRA_CONTEXT_KIND]/[EXTRA_CONTEXT_ID] attached, or a call. */
        const val EXTRA_CONTEXT_KIND = "open_context_kind"
        const val EXTRA_CONTEXT_ID = "open_context_id"
        /** Optional: what [EXTRA_CONTEXT_ID] is called, for the "On screen ·" line. */
        const val EXTRA_CONTEXT_TITLE = "open_context_title"
        const val EXTRA_OPEN_CALL_ID = "open_call_id"

        fun open(context: Context, callId: String) {
            context.startActivity(
                Intent(context, CallDebriefActivity::class.java)
                    .putExtra(EXTRA_CALL_ID, callId)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }
    }
}

class DebriefViewModel(app: Application) : AndroidViewModel(app) {
    private val api get() = ServerConfig.load(getApplication()).api()

    private val _detail = MutableStateFlow<CallDetail?>(null)
    val detail = _detail.asStateFlow()
    private val _error = MutableStateFlow<String?>(null)
    val error = _error.asStateFlow()
    private val _busy = MutableStateFlow<Set<String>>(emptySet())
    val busy = _busy.asStateFlow()
    private val _labels = MutableStateFlow<List<CallLabel>>(emptyList())
    val labelCatalog = _labels.asStateFlow()
    private val _groups = MutableStateFlow<List<String>>(emptyList())
    val groups = _groups.asStateFlow()
    private val _toast = MutableStateFlow<String?>(null)
    val toast = _toast.asStateFlow()

    private var callId = ""

    fun load(id: String) {
        if (id.isBlank()) return
        callId = id
        viewModelScope.launch {
            refresh()
            // Still analysing: keep checking until it's done.
            var tries = 0
            while (_detail.value?.call?.status == "processing" && tries++ < 60) {
                delay(4000)
                refresh()
            }
        }
        viewModelScope.launch {
            runCatching { _labels.value = api.calls.labels() }
            runCatching { _groups.value = api.tasks.groups().map { it.name } }
        }
    }

    suspend fun refresh() {
        try {
            _detail.value = api.calls.detail(callId)
            _error.value = null
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (_detail.value == null) _error.value = describeError(e)
        }
    }

    fun consumeToast() { _toast.value = null }

    private fun withBusy(id: String, block: suspend () -> Unit) {
        if (id in _busy.value) return
        _busy.value = _busy.value + id
        viewModelScope.launch {
            try {
                block()
                refresh()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _toast.value = describeError(e)
            } finally {
                _busy.value = _busy.value - id
            }
        }
    }

    fun accept(s: CallSuggestion, overrides: SuggestionEdits = SuggestionEdits.None) = withBusy(s.id) {
        val r = api.calls.accept(s.id, overrides)
        _toast.value = if (r.kind == "event") "Added to your calendar" else "Reminder created"
    }

    fun dismiss(s: CallSuggestion) = withBusy(s.id) { api.calls.dismiss(s.id) }

    fun setLabels(ids: List<String>) = withBusy("labels") { api.calls.setLabels(callId, ids) }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun DebriefScreen(
    vm: DebriefViewModel,
    onClose: () -> Unit,
    onAsk: (kind: String, id: String) -> Unit,
    onOpenCall: (String) -> Unit
) {
    val detail by vm.detail.collectAsState()
    val error by vm.error.collectAsState()
    val busy by vm.busy.collectAsState()
    val catalog by vm.labelCatalog.collectAsState()
    val groups by vm.groups.collectAsState()
    val toast by vm.toast.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    var editing by remember { mutableStateOf<CallSuggestion?>(null) }
    var editingLabels by rememberSaveable { mutableStateOf(false) }

    LaunchedEffect(toast) {
        toast?.let { snackbar.showSnackbar(it); vm.consumeToast() }
    }

    val call = detail?.call
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            ScreenTopBar(
                title = call?.let { if (it.caller == "Me") it.receiver else it.caller } ?: "Call",
                kicker = call?.let {
                    buildString {
                        append("Debrief · ").append(it.fullDateLabel())
                        if (it.durationSeconds > 0) append(" · ").append(formatDuration(it.durationSeconds))
                        it.direction?.let { d -> append(" · ").append(d) }
                    }
                } ?: "Debrief",
                showSettings = false,
                navigationIcon = { IconButton(onClick = onClose) { Icon(Icons.Filled.Close, contentDescription = "Close") } }
            )
        },
        bottomBar = {
            if (call != null) {
                Column(
                    Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.background).navigationBarsPadding(),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Rule()
                    Row(Modifier.widthIn(max = 720.dp).fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        OutlinedButton(onClick = { onAsk("call", call.id) }, modifier = Modifier.weight(1f)) {
                            Icon(Icons.AutoMirrored.Outlined.Chat, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(8.dp))
                            Text("Ask about it")
                        }
                        Button(onClick = onClose, modifier = Modifier.weight(1f)) { Text("Done") }
                    }
                }
            }
        }
    ) { padding ->
        when {
            call == null && error != null -> Column(Modifier.padding(padding).padding(28.dp)) {
                MonoLabel("Couldn't load", color = MaterialTheme.colorScheme.primary)
                Spacer(Modifier.height(8.dp))
                Text(error ?: "", style = MaterialTheme.typography.headlineSmall, fontStyle = FontStyle.Italic)
            }
            call == null -> Box(Modifier.padding(padding).fillMaxSize(), contentAlignment = Alignment.Center) { MonoLabel("Loading…") }
            else -> Box(Modifier.padding(padding).fillMaxSize(), contentAlignment = Alignment.TopCenter) { LazyColumn(
                // A readable measure on the unfolded screen; full width on the cover.
                Modifier.widthIn(max = 720.dp).fillMaxSize(),
                contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 4.dp, bottom = 24.dp)
            ) {
                item(key = "title") {
                    RtlSupportingText(
                        text = call.title ?: "Call",
                        color = MaterialTheme.colorScheme.onSurface,
                        style = MaterialTheme.typography.headlineLarge
                    )
                    Spacer(Modifier.height(10.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        LabelChips(call.labels, Modifier.weight(1f, fill = false))
                        IconButton(onClick = { editingLabels = true }) {
                            Icon(Icons.Outlined.Edit, contentDescription = "Edit labels", tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp))
                        }
                    }
                }
                if (call.status == "processing") {
                    item(key = "processing") {
                        Column(Modifier.padding(vertical = 24.dp)) {
                            MonoLabel("Analysing", color = MaterialTheme.colorScheme.primary)
                            Spacer(Modifier.height(8.dp))
                            LinearProgressIndicator(Modifier.fillMaxWidth())
                            Spacer(Modifier.height(8.dp))
                            Text("Transcribing and pulling out what matters. This page updates by itself.",
                                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
                if (call.status == "failed") {
                    item(key = "failed") {
                        Text(call.error ?: "This recording couldn't be analysed.", style = MaterialTheme.typography.bodyLarge,
                            fontStyle = FontStyle.Italic, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(vertical = 20.dp))
                    }
                }
                call.securityAlert?.let { alert ->
                    item(key = "alert") {
                        Row(
                            Modifier.fillMaxWidth().padding(top = 16.dp).clip(RoundedCornerShape(10.dp))
                                .background(Danger.copy(alpha = 0.12f)).padding(14.dp)
                        ) {
                            Text("🔐", fontSize = 20.sp)
                            Spacer(Modifier.width(10.dp))
                            Text(alert, style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content), color = MaterialTheme.colorScheme.onSurface)
                        }
                    }
                }
                if (!call.summary.isNullOrBlank() && call.status != "processing") {
                    item(key = "summary") {
                        DebriefSection("Summary")
                        RtlSupportingText(
                            text = parseMarkdownToAnnotatedString(call.summary),
                            color = MaterialTheme.colorScheme.onSurface,
                            style = MaterialTheme.typography.bodyLarge.copy(lineHeight = 25.sp)
                        )
                    }
                }
                if (call.keyFacts.isNotEmpty()) {
                    item(key = "facts") {
                        DebriefSection("Key facts")
                        MetaChips(call.keyFacts.map { it.text })
                    }
                }
                val suggestions = detail?.suggestions.orEmpty().filter { it.status == "pending" } +
                    detail?.suggestions.orEmpty().filter { it.status != "pending" }
                if (suggestions.isNotEmpty()) {
                    item(key = "sugg_head") {
                        DebriefSection("Suggested · ${suggestions.count { it.status == "pending" }} waiting")
                    }
                    items(suggestions, key = { it.id }) { s ->
                        SuggestionCard(
                            suggestion = s,
                            busy = s.id in busy,
                            onAccept = { vm.accept(s) },
                            onEdit = { editing = s },
                            onAsk = { onAsk("suggestion", s.id) },
                            onDismiss = { vm.dismiss(s) },
                            modifier = Modifier.padding(bottom = 10.dp)
                        )
                    }
                }
                val tasks = detail?.tasks.orEmpty()
                if (tasks.isNotEmpty()) {
                    item(key = "tasks") {
                        DebriefSection("Reminders from this call")
                        tasks.forEach { t ->
                            Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                                MonoLabel(if (t.status == "completed") "Done" else "Open",
                                    color = if (t.status == "completed") MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.primary,
                                    modifier = Modifier.width(52.dp))
                                Text(t.title, style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content), modifier = Modifier.weight(1f))
                                t.dueDate?.let { MonoLabel(friendlyDate(it) ?: it) }
                            }
                            Rule()
                        }
                    }
                }
                if (call.participants.isNotEmpty()) {
                    item(key = "people") {
                        DebriefSection("People")
                        call.participants.forEach { p ->
                            Column(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
                                Text(p.name, style = MaterialTheme.typography.titleSmall.copy(textDirection = TextDirection.Content))
                                if (p.role.isNotBlank()) Text(p.role, style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content), color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
                item(key = "open") {
                    Spacer(Modifier.height(16.dp))
                    TextButton(onClick = { onOpenCall(call.id) }) { Text("Open transcript & recording") }
                }
            } }
        }
    }

    editing?.let { s ->
        ModalBottomSheet(onDismissRequest = { editing = null }, containerColor = MaterialTheme.colorScheme.surface) {
            SuggestionEditSheet(
                suggestion = s,
                groups = groups,
                onCreate = { overrides -> vm.accept(s, overrides); editing = null },
                onAsk = { editing = null; onAsk("suggestion", s.id) },
                onCancel = { editing = null }
            )
        }
    }
    if (editingLabels && call != null) {
        LabelEditorDialog(
            catalog = catalog,
            selected = call.labels.map { it.id },
            onDismiss = { editingLabels = false },
            onSave = { vm.setLabels(it); editingLabels = false }
        )
    }
}

@Composable
private fun DebriefSection(text: String) {
    Column(Modifier.fillMaxWidth().padding(top = 24.dp, bottom = 10.dp)) {
        MonoLabel(text, color = MaterialTheme.colorScheme.onSurface)
        Spacer(Modifier.height(6.dp))
        Rule()
    }
}

/** Pick up to three labels from the catalog. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun LabelEditorDialog(
    catalog: List<CallLabel>,
    selected: List<String>,
    onDismiss: () -> Unit,
    onSave: (List<String>) -> Unit
) {
    var picked by remember(selected) { mutableStateOf(selected) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Labels") },
        text = {
            Column {
                Text("Up to three.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.height(12.dp))
                if (catalog.isEmpty()) MonoLabel("Loading…")
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    catalog.forEach { l ->
                        val on = l.id in picked
                        InkChip("${l.emoji} ${l.name}", selected = on, onClick = {
                            picked = if (on) picked - l.id else if (picked.size < 3) picked + l.id else picked
                        })
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { onSave(picked) }) { Text("Save") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}
