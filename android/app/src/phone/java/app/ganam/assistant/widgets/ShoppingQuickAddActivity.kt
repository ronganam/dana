package app.ganam.assistant.widgets

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import app.ganam.assistant.data.network.ApiService
import app.ganam.assistant.data.shopping.ShoppingJson
import app.ganam.assistant.data.shopping.ShoppingOp
import app.ganam.assistant.data.shopping.ShoppingOps
import app.ganam.assistant.data.shopping.ShoppingStore
import app.ganam.assistant.data.shopping.ShoppingSync
import app.ganam.assistant.data.shopping.ShoppingText
import app.ganam.assistant.data.shopping.ShoppingView
import app.ganam.assistant.data.shopping.Usual
import app.ganam.assistant.ui.components.InkButton
import app.ganam.assistant.ui.components.InkEmphasis
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.screens.common.byScript
import app.ganam.assistant.ui.screens.shopping.AddField
import app.ganam.assistant.ui.screens.shopping.SuggestionList
import app.ganam.assistant.ui.screens.shopping.UsualsRow
import app.ganam.assistant.ui.theme.GanamAssistantTheme
import app.ganam.assistant.ui.theme.ThemeSettings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.URLEncoder

/**
 * The Shopping widget's `+`: a small sheet over the home screen with the
 * keyboard up at once. Add several things in a row (each Enter adds and keeps
 * the field), tap a usual or a suggestion; Back or Done closes it. Adds go
 * through [ShoppingSync] like the Shopping screen's, so they work offline and
 * the widget shows them at once.
 */
class ShoppingQuickAddActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        ThemeSettings.load(applicationContext)
        setContent { GanamAssistantTheme { QuickAddSheet(onClose = ::finish) } }
    }
}

@Composable
private fun QuickAddSheet(onClose: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val store = remember { ShoppingSync.get(context) }
    val state by store.state.collectAsState()
    val screen = remember(state) { store.shown() }
    var text by remember { mutableStateOf(TextFieldValue("")) }
    var suggestions by remember { mutableStateOf<List<Usual>>(emptyList()) }
    // What this window did, newest first ("Added milk", "Already on the list: eggs").
    val said = remember { mutableStateListOf<String>() }
    val focus = remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current

    LaunchedEffect(Unit) {
        withContext(Dispatchers.IO) { store.load() }
        focus.requestFocus()
        keyboard?.show()
        withContext(Dispatchers.IO) { store.refresh() }
    }
    val listId = screen?.list?.id.orEmpty()
    val canEdit = screen?.list?.canEdit ?: true
    LaunchedEffect(text.text, listId) {
        val q = text.text.substringAfterLast(',').trim()
        if (q.isEmpty() || listId.isEmpty()) { suggestions = emptyList(); return@LaunchedEffect }
        delay(180)
        suggestions = withContext(Dispatchers.IO) {
            val path = "/api/shopping/suggestions?q=${enc(q)}&list=${enc(listId)}"
            runCatching {
                val (code, body) = ApiService.fromPrefs(context).jsonCall("GET", path, null)
                if (code in 200..299) ShoppingJson.usuals(JSONObject(body)) else null
            }.getOrNull() ?: run {
                val key = ShoppingText.nameKey(q)
                screen?.usuals.orEmpty().filter { ShoppingText.nameKey(it.name).contains(key) }
            }
        }
    }

    fun submit(op: ShoppingOp.Add) {
        scope.launch {
            val line = when (val o = ShoppingSync.submit(context, op)) {
                is ShoppingStore.Outcome.Sent -> ShoppingView.addResultText(o.answer?.results.orEmpty()).text
                    .ifEmpty { ShoppingView.queuedLabel(op).removeSuffix(" · will sync") }
                ShoppingStore.Outcome.Queued -> ShoppingView.queuedLabel(op)
                is ShoppingStore.Outcome.Rejected -> ShoppingView.errorText(o.status, o.body)
            }
            said.add(0, line)
        }
    }

    fun addTyped() {
        val parts = ShoppingText.parse(text.text)
        if (parts.isEmpty() || listId.isEmpty()) return
        if (parts.size > ShoppingText.MAX_ITEMS) {
            said.add(0, "That's ${parts.size} items — at most ${ShoppingText.MAX_ITEMS} at a time.")
            return
        }
        val op = ShoppingOps.addTyped(listId, text.text) ?: return
        text = TextFieldValue("")
        suggestions = emptyList()
        submit(op)
    }

    fun addName(name: String) {
        if (listId.isEmpty()) return
        suggestions = emptyList()
        if (text.text.substringAfterLast(',').isNotBlank()) text = TextFieldValue("")
        submit(ShoppingOps.addName(listId, name))
    }

    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black.copy(alpha = 0.32f))
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClose),
        contentAlignment = Alignment.BottomCenter
    ) {
        Column(
            Modifier
                .widthIn(max = 560.dp)
                .fillMaxWidth()
                .clip(RoundedCornerShape(topStart = 18.dp, topEnd = 18.dp))
                .background(MaterialTheme.colorScheme.background)
                // Taps inside the sheet don't close it.
                .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) {}
                .imePadding()
                .navigationBarsPadding()
                .padding(top = 14.dp, bottom = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Row(Modifier.padding(start = 18.dp, end = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    MonoLabel("Shopping", color = MaterialTheme.colorScheme.primary)
                    Text(
                        screen?.let { ShoppingView.subtitle(it, null) } ?: "Loading…",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                InkButton("Done", emphasis = InkEmphasis.SOLID, onClick = onClose)
            }
            said.take(3).forEach { line ->
                Text(
                    line,
                    style = MaterialTheme.typography.bodySmall.byScript(line),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    modifier = Modifier.padding(horizontal = 18.dp)
                )
            }
            if (!canEdit) Text(
                "This list is view only.",
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(horizontal = 18.dp, vertical = 8.dp)
            ) else {
                if (text.text.isBlank()) UsualsRow(screen?.usuals.orEmpty(), onAdd = ::addName)
                else if (suggestions.isNotEmpty()) SuggestionList(suggestions, onAdd = ::addName, onForget = { name ->
                    suggestions = suggestions.filter { it.name != name }
                    scope.launch { ShoppingSync.submit(context, ShoppingOp.Forget(ShoppingOp.newId(), listId, name)) }
                })
                AddField(
                    value = text,
                    onValue = { v ->
                        val t = ShoppingText.pasteAsLine(v.text)
                        text = if (t == v.text) v else TextFieldValue(t, TextRange(t.length))
                    },
                    onAdd = ::addTyped,
                    onFocus = {},
                    focusRequester = focus,
                    modifier = Modifier.padding(horizontal = 12.dp)
                )
            }
        }
    }
}

private fun enc(s: String) = URLEncoder.encode(s, "UTF-8").replace("+", "%20")
