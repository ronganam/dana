package io.github.ronganam.dana

import io.github.ronganam.dana.data.network.ServerConfig
import android.app.Application
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.ronganam.dana.data.models.CallSuggestion
import io.github.ronganam.dana.data.notifications.AppIcons
import io.github.ronganam.dana.ui.components.*
import io.github.ronganam.dana.ui.theme.DanaTheme
import io.github.ronganam.dana.ui.theme.ThemeSettings
import io.github.ronganam.dana.ui.viewmodels.describeError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * Pops up when new suggestions come from something that just happened (a call
 * that ended, a notification asking for something): where they came from, and
 * Create / Edit / Ask Dana / Dismiss for each, so they're handled while it's
 * fresh. "Later" keeps them pending (the notification stays, and they're in
 * the Inbox). Opened by [SuggestionsReceiver] (server push) or from the call
 * result notification.
 */
class SuggestionsPopupActivity : ComponentActivity() {

    private val vm: SuggestionsPopupViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        ThemeSettings.load(applicationContext)
        load(intent)
        setContent {
            DanaTheme {
                SuggestionsPopupScreen(
                    vm = vm,
                    onLater = { finish() },
                    onDone = { done() },
                    onAsk = { s -> openInApp(CallDebriefActivity.EXTRA_CONTEXT_KIND to "suggestion", CallDebriefActivity.EXTRA_CONTEXT_ID to s.id, CallDebriefActivity.EXTRA_CONTEXT_TITLE to s.title) },
                    onOpenCall = { id -> CallDebriefActivity.open(this, id); finish() }
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        load(intent)
    }

    private fun load(intent: Intent) {
        val ids = intent.getStringArrayListExtra(EXTRA_IDS).orEmpty()
        vm.load(ids, intent.getStringExtra(EXTRA_HEADING), intent.getStringExtra(EXTRA_CALL_ID))
    }

    /** Everything handled: drop the reminder notification and close. */
    private fun done() {
        val key = intent.getStringExtra(EXTRA_NOTIFICATION_KEY)
        if (key != null) (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(key.hashCode())
        finish()
    }

    private fun openInApp(vararg extras: Pair<String, String>) {
        val i = Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        extras.forEach { (k, v) -> i.putExtra(k, v) }
        startActivity(i)
        finish()
    }

    companion object {
        const val EXTRA_IDS = "suggestion_ids"
        const val EXTRA_HEADING = "heading"
        /** Suggestions of this call (when the ids aren't known, e.g. from the call result notification). */
        const val EXTRA_CALL_ID = "call_id"
        const val EXTRA_NOTIFICATION_KEY = "notification_key"

        fun intent(context: Context, ids: List<String>, heading: String?, callId: String? = null, notificationKey: String? = null) =
            Intent(context, SuggestionsPopupActivity::class.java)
                .putStringArrayListExtra(EXTRA_IDS, ArrayList(ids))
                .putExtra(EXTRA_HEADING, heading)
                .putExtra(EXTRA_CALL_ID, callId)
                .putExtra(EXTRA_NOTIFICATION_KEY, notificationKey)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
    }
}

class SuggestionsPopupViewModel(app: Application) : AndroidViewModel(app) {
    private val api get() = ServerConfig.load(getApplication()).api()

    private val _items = MutableStateFlow<List<CallSuggestion>?>(null)
    val items = _items.asStateFlow()
    private val _heading = MutableStateFlow<String?>(null)
    val heading = _heading.asStateFlow()
    private val _error = MutableStateFlow<String?>(null)
    val error = _error.asStateFlow()
    private val _busy = MutableStateFlow<Set<String>>(emptySet())
    val busy = _busy.asStateFlow()
    private val _groups = MutableStateFlow<List<String>>(emptyList())
    val groups = _groups.asStateFlow()
    private val _toast = MutableStateFlow<String?>(null)
    val toast = _toast.asStateFlow()

    private var ids: List<String> = emptyList()
    private var callId: String? = null

    fun load(newIds: List<String>, heading: String?, call: String?) {
        // A second push while open adds to what's shown.
        ids = (ids + newIds).distinct()
        callId = call ?: callId
        if (heading != null) _heading.value = heading
        viewModelScope.launch { refresh() }
        viewModelScope.launch { runCatching { _groups.value = api.getTaskGroups().map { it.name } } }
    }

    private suspend fun refresh() {
        try {
            val all = if (callId != null && ids.isEmpty()) api.getCallDetail(callId!!).suggestions
            else api.getSuggestions(status = "all", source = "all").filter { it.id in ids }
            // Keep the order they arrived in; resolved ones stay, marked done.
            val byId = all.associateBy { it.id }
            _items.value = if (ids.isEmpty()) all else ids.mapNotNull { byId[it] }
            if (ids.isEmpty()) ids = all.map { it.id }
            _error.value = null
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (_items.value == null) _error.value = describeError(e)
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

    fun accept(s: CallSuggestion, overrides: JSONObject = JSONObject()) = withBusy(s.id) {
        val r = api.acceptSuggestion(s.id, overrides)
        _toast.value = if (r.optString("kind") == "event") "Added to your calendar" else "Reminder created"
    }

    fun dismiss(s: CallSuggestion) = withBusy(s.id) { api.dismissSuggestion(s.id) }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SuggestionsPopupScreen(
    vm: SuggestionsPopupViewModel,
    onLater: () -> Unit,
    onDone: () -> Unit,
    onAsk: (CallSuggestion) -> Unit,
    onOpenCall: (String) -> Unit
) {
    val items by vm.items.collectAsState()
    val heading by vm.heading.collectAsState()
    val error by vm.error.collectAsState()
    val busy by vm.busy.collectAsState()
    val groups by vm.groups.collectAsState()
    val toast by vm.toast.collectAsState()
    val context = LocalContext.current
    var editing by remember { mutableStateOf<CallSuggestion?>(null) }

    LaunchedEffect(toast) {
        toast?.let { Toast.makeText(context, it, Toast.LENGTH_SHORT).show(); vm.consumeToast() }
    }
    val list = items
    val waiting = list?.count { it.status == "pending" } ?: 0
    // Nothing left (also when they were handled elsewhere, e.g. in the Inbox).
    val allHandled = list != null && waiting == 0
    // Everything handled: a beat to see it, then close.
    LaunchedEffect(allHandled) {
        if (allHandled) { delay(1200); onDone() }
    }

    Surface(color = MaterialTheme.colorScheme.background, modifier = Modifier.fillMaxSize()) {
        // Unfolded: a readable column in the middle, not edge-to-edge lines.
        Column(Modifier.fillMaxSize().wrapContentWidth().widthIn(max = 760.dp).fillMaxWidth()
            .systemBarsPadding().padding(horizontal = 20.dp)) {
            Spacer(Modifier.height(20.dp))
            MonoLabel(
                when {
                    list == null -> "Suggestions"
                    allHandled -> "All handled"
                    waiting == 1 -> "1 suggestion"
                    else -> "$waiting suggestions"
                },
                color = MaterialTheme.colorScheme.primary
            )
            Spacer(Modifier.height(4.dp))
            Text(
                heading ?: list?.firstOrNull()?.let { s ->
                    if (s.fromNotification) "From ${s.source?.contact ?: "a notification"}" else "From the call with ${s.source?.contact ?: "someone"}"
                } ?: "",
                style = MaterialTheme.typography.headlineSmall.copy(textDirection = TextDirection.Content)
            )
            Spacer(Modifier.height(16.dp))
            Box(Modifier.weight(1f)) {
                when {
                    list == null && error != null -> Text(error!!, color = MaterialTheme.colorScheme.error)
                    list == null -> LinearProgressIndicator(Modifier.fillMaxWidth())
                    allHandled -> Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Center) {
                        Icon(Icons.Filled.CheckCircle, contentDescription = null, tint = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.size(56.dp))
                        Spacer(Modifier.height(8.dp))
                        Text("All handled", style = MaterialTheme.typography.titleMedium)
                    }
                    else -> LazyColumn(verticalArrangement = Arrangement.spacedBy(14.dp)) {
                        // Where they came from, once (they share a source almost always).
                        list.firstOrNull()?.let { first ->
                            item(key = "source") {
                                SuggestionSourceBlock(
                                    suggestion = first,
                                    icon = { SourceIcon(first, 36.dp) },
                                    onOpen = if (!first.fromNotification && first.callId.isNotEmpty()) ({ onOpenCall(first.callId) }) else null
                                )
                            }
                        }
                        items(list.sortedBy { it.status != "pending" }, key = { it.id }) { s ->
                            SuggestionCard(
                                suggestion = s,
                                busy = s.id in busy,
                                onAccept = { vm.accept(s) },
                                onEdit = { editing = s },
                                onAsk = { onAsk(s) },
                                onDismiss = { vm.dismiss(s) }
                            )
                        }
                        item(key = "pad") { Spacer(Modifier.height(8.dp)) }
                    }
                }
            }
            Rule()
            Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    if (waiting > 0) "Later keeps them in your Inbox" else "",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f)
                )
                TextButton(onClick = if (waiting > 0) onLater else onDone) { Text(if (waiting > 0) "Later" else "Close") }
            }
        }
    }

    editing?.let { s ->
        ModalBottomSheet(onDismissRequest = { editing = null }, containerColor = MaterialTheme.colorScheme.surface) {
            SuggestionEditSheet(
                suggestion = s,
                groups = groups,
                onCreate = { overrides -> vm.accept(s, overrides); editing = null },
                onAsk = { editing = null; onAsk(s) },
                onCancel = { editing = null },
                onDismiss = { vm.dismiss(s); editing = null }
            )
        }
    }
}

@Composable
private fun SourceIcon(s: CallSuggestion, size: Dp) {
    val ctx = LocalContext.current.applicationContext
    val pkg = s.source?.packageName?.takeIf { s.fromNotification }
    val icon by produceState<ImageBitmap?>(null, pkg) {
        value = pkg?.let { withContext(Dispatchers.IO) { AppIcons.load(ctx, it) } }?.asImageBitmap()
    }
    val bmp = icon
    if (bmp != null) {
        Image(bmp, contentDescription = null, modifier = Modifier.size(size).clip(RoundedCornerShape(10.dp)))
    } else {
        Box(Modifier.size(size).clip(CircleShape).background(MaterialTheme.colorScheme.primary.copy(alpha = 0.14f)),
            contentAlignment = Alignment.Center) { Text(if (s.fromNotification) "🔔" else "📞") }
    }
}
