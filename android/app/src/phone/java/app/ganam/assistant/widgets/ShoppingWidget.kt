package app.ganam.assistant.widgets

import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.unit.dp
import androidx.glance.GlanceId
import androidx.glance.ColorFilter
import androidx.glance.GlanceModifier
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.lazy.LazyColumn
import androidx.glance.appwidget.lazy.items
import androidx.glance.appwidget.provideContent
import androidx.glance.appwidget.updateAll
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.text.Text
import app.ganam.assistant.EXTRA_OPEN_SCREEN
import app.ganam.assistant.R
import app.ganam.assistant.data.shopping.ShoppingOp
import app.ganam.assistant.data.shopping.ShoppingSnapshot
import app.ganam.assistant.data.shopping.ShoppingSync
import app.ganam.assistant.data.shopping.ShoppingView
import app.ganam.assistant.data.wear.WearCards
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * The Shopping widget: what is left to buy in the server's order, aisle by
 * aisle (a light aisle label), the count, a tick per row, `+` for the quick-add
 * window and the header to open Shopping (under More). It draws from
 * [ShoppingSync] (the same cache and queue as the Shopping screen), so a tick
 * shows at once, works offline and goes through the same queue.
 */
class ShoppingWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val store = ShoppingSync.get(context).also { it.load() }
        provideContent {
            // Read inside the composition: a session kept alive after a tap
            // redraws from the store's state, not from values read once.
            val state by store.state.collectAsState()
            val screen = remember(state) { store.shown() }
            ShoppingCard(context, screen, state.queued.size)
        }
    }
}

private sealed class Entry {
    data class Aisle(val name: String) : Entry()
    data class Item(val line: ShoppingView.Line) : Entry()
}

@Composable
private fun ShoppingCard(context: Context, screen: app.ganam.assistant.data.shopping.ShoppingScreen?, waiting: Int) {
    val open = screen(context, "shopping")
    val canEdit = screen?.list?.canEdit ?: true
    val lines = screen?.let { ShoppingView.toBuyLines(it) }.orEmpty()
    val entries = buildList {
        var aisle: String? = null
        for (l in lines) {
            if (l.aisle != aisle) { add(Entry.Aisle(l.aisle)); aisle = l.aisle }
            add(Entry.Item(l))
        }
    }
    Box(GlanceModifier.fillMaxSize().background(W.surface).cornerRadius(20.dp).padding(start = 14.dp, end = 8.dp, top = 8.dp, bottom = 10.dp)) {
        Column(GlanceModifier.fillMaxSize()) {
            Row(GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Row(GlanceModifier.defaultWeight().clickable(actionStartActivity(open)).padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("SHOPPING", style = style(W.accent, 11, bold = true))
                    Spacer(GlanceModifier.width(8.dp))
                    val trailing = when {
                        screen == null -> ""
                        !canEdit -> "view only"
                        lines.isEmpty() -> ""
                        else -> "${lines.size} to buy"
                    }
                    if (trailing.isNotEmpty()) Text(trailing, style = style(W.mute, 11, bold = true))
                }
                if (canEdit) Box(
                    GlanceModifier.size(40.dp).cornerRadius(20.dp).clickable(actionStartActivity(quickAdd(context))),
                    contentAlignment = Alignment.Center
                ) { Text("+", style = style(W.accent, 22, bold = true)) }
            }
            if (waiting > 0) Text("Waiting to sync · $waiting", style = style(W.mute, 11), modifier = GlanceModifier.padding(bottom = 2.dp))
            if (lines.isEmpty()) {
                Spacer(GlanceModifier.height(4.dp))
                Text(if (screen == null) "Loading…" else "Nothing to buy.", style = style(W.ink, 14))
                if (screen != null && canEdit) Text("Tap + to add, or tell Dana.", style = style(W.mute, 12))
            } else LazyColumn(GlanceModifier.fillMaxSize()) {
                items(entries) { e ->
                    when (e) {
                        is Entry.Aisle -> Text(
                            e.name.uppercase(), style = style(W.mute, 10),
                            modifier = GlanceModifier.padding(start = 9.dp, top = 8.dp, bottom = 1.dp)
                        )
                        is Entry.Item -> ItemRow(e.line, screen?.list?.id.orEmpty(), canEdit)
                    }
                }
            }
        }
    }
}

@Composable
private fun ItemRow(line: ShoppingView.Line, listId: String, canEdit: Boolean) {
    val tick = actionRunCallback<ShoppingTickAction>(actionParametersOf(KeyId to line.id, KeyList to listId, KeyName to line.name))
    var m = GlanceModifier.fillMaxWidth().height(40.dp)
    if (canEdit) m = m.clickable(tick)
    // The row is laid out left to right whatever the name's language: the ring, then the name
    // (which renders in its own direction), so a mixed list stays one column.
    Row(m, verticalAlignment = Alignment.CenterVertically) {
        if (canEdit) CheckRing(tick) else Spacer(GlanceModifier.width(8.dp))
        Spacer(GlanceModifier.width(4.dp))
        Text(line.name, style = style(W.ink, 14), maxLines = 1)
        if (line.quantity.isNotEmpty()) {
            Spacer(GlanceModifier.width(6.dp))
            Text(line.quantity, style = style(W.mute, 12), maxLines = 1)
        }
    }
}

/** The check target: a 22 dp ring in a 40 dp tap box, clearly a control at widget sizes. */
@Composable
private fun CheckRing(tick: androidx.glance.action.Action) {
    Box(GlanceModifier.size(40.dp).cornerRadius(20.dp).clickable(tick), contentAlignment = Alignment.Center) {
        Image(
            ImageProvider(R.drawable.ic_widget_check_ring),
            contentDescription = "Mark as bought",
            colorFilter = ColorFilter.tint(W.inkSoft),
            modifier = GlanceModifier.size(22.dp)
        )
    }
}

private val KeyList = ActionParameters.Key<String>("list")
private val KeyName = ActionParameters.Key<String>("name")

private fun quickAdd(context: Context) = Intent(context, ShoppingQuickAddActivity::class.java)
    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)

/** A tick on the widget: shown at once, sent (or queued) like a tick on the Shopping screen. */
class ShoppingTickAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val id = parameters[KeyId] ?: return
        val listId = parameters[KeyList].orEmpty()
        if (listId.isEmpty()) return
        val outcome = ShoppingSync.submit(context, ShoppingOp.Check(ShoppingOp.newId(), listId, id, true, parameters[KeyName].orEmpty()))
        if (outcome is app.ganam.assistant.data.shopping.ShoppingStore.Outcome.Rejected) {
            Log.w("ShoppingWidget", "Tick refused: HTTP ${outcome.status}")
            ShoppingSync.get(context).refresh()
        }
    }
}

/**
 * Keeps the widget and the watch in step with [ShoppingSync]: every change
 * redraws the widget (almost at once) and, a moment later, writes the
 * shopping fields into the widget snapshot and sends it to the watch
 * ([ShoppingSnapshot]).
 */
object ShoppingWidgetSync {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    @Volatile private var drawJob: Job? = null
    @Volatile private var watchJob: Job? = null

    fun changed(context: Context) {
        drawJob?.cancel()
        drawJob = scope.launch {
            delay(120)
            runCatching { ShoppingWidget().updateAll(context) }.onFailure { Log.w("ShoppingWidget", "Redraw failed", it) }
        }
        watchJob?.cancel()
        watchJob = scope.launch {
            delay(1_500)
            val snapshot = writeFields(context)
            WearCards.sendSnapshot(context, snapshot)
        }
    }

    /** Put the current shopping fields into the widget snapshot; returns the whole snapshot. */
    @Synchronized
    fun writeFields(context: Context): JSONObject {
        val prefs = context.getSharedPreferences("GanamPrefs", Context.MODE_PRIVATE)
        val o = runCatching { JSONObject(prefs.getString("widget_snapshot", "{}") ?: "{}") }.getOrDefault(JSONObject())
        ShoppingSnapshot.merge(o, ShoppingSnapshot.fields(ShoppingSync.get(context).shown()))
        prefs.edit().putString("widget_snapshot", o.toString()).apply()
        return o
    }
}
