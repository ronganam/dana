package io.github.ronganam.dana.widgets

import io.github.ronganam.dana.data.network.ServerConfig
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.lazy.LazyColumn
import androidx.glance.appwidget.lazy.items
import androidx.glance.appwidget.provideContent
import androidx.glance.appwidget.updateAll
import androidx.glance.background
import androidx.glance.color.ColorProvider
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
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider as GlanceColor
import io.github.ronganam.dana.AskDanaActivity
import io.github.ronganam.dana.EXTRA_OPEN_PERSON_ID
import io.github.ronganam.dana.EXTRA_OPEN_SCREEN
import io.github.ronganam.dana.MainActivity
import io.github.ronganam.dana.data.sync.TaskActionQueue
import org.json.JSONArray
import org.json.JSONObject

// ---- Ink & Paper colours (day / night) ----
internal object W {
    val surface = ColorProvider(Color(0xFFFAF6EE), Color(0xFF1B1713))
    val surfaceHigh = ColorProvider(Color(0xFFEAE2D3), Color(0xFF26201B))
    val ink = ColorProvider(Color(0xFF1A1511), Color(0xFFF2EBDD))
    val inkSoft = ColorProvider(Color(0xFF4A4036), Color(0xFFCBBFAD))
    val mute = ColorProvider(Color(0xFF6B6050), Color(0xFF9A8E7E))
    val accent = ColorProvider(Color(0xFFD1401A), Color(0xFFFF6A3D))
    val onAccent = ColorProvider(Color(0xFFFFFFFF), Color(0xFF1F0A02))
    val danger = ColorProvider(Color(0xFFE5483D), Color(0xFFE5483D))
    val warning = ColorProvider(Color(0xFFE0A02A), Color(0xFFE0A02A))
}

internal fun style(color: GlanceColor = W.ink, size: Int = 13, bold: Boolean = false) =
    TextStyle(color = color, fontSize = size.sp, fontWeight = if (bold) FontWeight.Medium else FontWeight.Normal)

internal fun openApp(context: Context, extras: Intent.() -> Unit = {}) =
    Intent(context, MainActivity::class.java)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        .apply(extras)

internal fun screen(context: Context, route: String) = openApp(context) { putExtra(EXTRA_OPEN_SCREEN, route) }

@Composable
internal fun Card(onClick: Intent? = null, content: @Composable () -> Unit) {
    var m = GlanceModifier.fillMaxSize().background(W.surface).cornerRadius(20.dp).padding(14.dp)
    if (onClick != null) m = m.clickable(actionStartActivity(onClick))
    Box(modifier = m) { content() }
}

@Composable
internal fun Header(title: String, trailing: String = "", trailingColor: GlanceColor = W.mute, onClick: Intent? = null) {
    var m = GlanceModifier.fillMaxWidth()
    if (onClick != null) m = m.clickable(actionStartActivity(onClick))
    Row(modifier = m, verticalAlignment = Alignment.CenterVertically) {
        Text(title.uppercase(), style = style(W.accent, 11, bold = true), modifier = GlanceModifier.defaultWeight())
        if (trailing.isNotEmpty()) Text(trailing, style = style(trailingColor, 11, bold = true))
    }
    Spacer(GlanceModifier.height(6.dp))
}

@Composable
internal fun Muted(text: String) = Text(text, style = style(W.mute, 12), maxLines = 2)

/** A round tap target ("○" to tick, "✕" to dismiss…). */
@Composable
internal fun Tap(label: String, action: androidx.glance.action.Action, color: GlanceColor = W.inkSoft) {
    Box(
        modifier = GlanceModifier.size(32.dp).cornerRadius(16.dp).clickable(action),
        contentAlignment = Alignment.Center,
    ) { Text(label, style = style(color, 15, bold = true)) }
}

internal val KeyId = ActionParameters.Key<String>("id")
private val KeyFlag = ActionParameters.Key<String>("flag")

// =====================================================================
// Ask Dana — a search-bar-like strip
// =====================================================================

class AskWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) = provideContent {
        val open = Intent(context, AskDanaActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        Row(
            modifier = GlanceModifier.fillMaxSize().background(W.surface).cornerRadius(28.dp)
                .padding(horizontal = 18.dp).clickable(actionStartActivity(open)),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("✦", style = style(W.accent, 18, bold = true))
            Spacer(GlanceModifier.width(10.dp))
            Text("Ask Dana…", style = style(W.mute, 15), modifier = GlanceModifier.defaultWeight())
            Box(
                modifier = GlanceModifier.size(36.dp).cornerRadius(18.dp).background(W.accent),
                contentAlignment = Alignment.Center,
            ) { Text("🎙", style = style(W.onAccent, 15)) }
        }
    }
}

// =====================================================================
// Today — briefing headline, next events, what's due (tick to complete)
// =====================================================================

class TodayWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) = provideContent {
        val s = WidgetSnapshot.load(context)
        Card {
            Column(GlanceModifier.fillMaxSize()) {
                Header("Today", if (s.tasks.isNotEmpty()) "${s.tasks.size} due" else "", onClick = screen(context, "tasks"))
                LazyColumn(GlanceModifier.fillMaxSize()) {
                    s.briefingHeadline?.let { h ->
                        item {
                            Column(GlanceModifier.fillMaxWidth().padding(bottom = 6.dp).clickable(actionStartActivity(screen(context, "tasks")))) {
                                Text("☀️ $h", style = style(W.ink, 14, bold = true), maxLines = 2)
                                s.briefingPoints.take(2).forEach { Text("· $it", style = style(W.inkSoft, 12), maxLines = 2) }
                            }
                        }
                    }
                    items(s.events) { (time, title) ->
                        Row(GlanceModifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(time, style = style(W.accent, 12, bold = true), modifier = GlanceModifier.width(52.dp))
                            Text(title, style = style(W.ink, 13), maxLines = 1)
                        }
                    }
                    if (s.events.isNotEmpty() && s.tasks.isNotEmpty()) item { Spacer(GlanceModifier.height(4.dp)) }
                    items(s.tasks) { (tid, title, due) ->
                        Row(GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Tap("○", actionRunCallback<CompleteTaskAction>(actionParametersOf(KeyId to tid)), W.mute)
                            Text(title, style = style(W.ink, 13), maxLines = 1, modifier = GlanceModifier.defaultWeight()
                                .clickable(actionStartActivity(screen(context, "tasks"))))
                            if (due.isNotEmpty()) Text(due, style = style(if (due == "overdue") W.danger else W.mute, 11, bold = due == "overdue"))
                        }
                    }
                    if (s.briefingHeadline == null && s.events.isEmpty() && s.tasks.isEmpty()) {
                        item { Muted(if (s.updatedAt == 0L) "Loading…" else "Nothing on today. Enjoy.") }
                    }
                }
            }
        }
    }
}

class CompleteTaskAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val id = parameters[KeyId] ?: return
        Snapshot.removeFrom(context, "tasks", id)
        TodayWidget().updateAll(context)
        runCatching { TaskActionQueue.complete(context, id) }.onFailure { Log.w("Widgets", "Complete failed", it) }
        WidgetRefreshWorker.refreshNow(context)
    }
}

// =====================================================================
// Waiting on you — pending suggestions with accept / dismiss
// =====================================================================

class WaitingWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) = provideContent {
        val s = WidgetSnapshot.load(context)
        Card {
            Column(GlanceModifier.fillMaxSize()) {
                Header("Waiting on you", if (s.suggestionCount > 0) "${s.suggestionCount}" else "", W.accent, screen(context, "waiting"))
                if (s.suggestions.isEmpty()) Muted("Nothing to decide right now.")
                LazyColumn(GlanceModifier.fillMaxSize()) {
                    items(s.suggestions) { (sid, title, source) ->
                        Row(GlanceModifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(GlanceModifier.defaultWeight().clickable(actionStartActivity(screen(context, if (source == "notification") "inbox" else "waiting")))) {
                                Text(title.ifBlank { "Suggestion" }, style = style(W.ink, 13), maxLines = 2)
                                Text(if (source == "notification") "from a notification" else "from a call", style = style(W.mute, 11))
                            }
                            Tap("✓", actionRunCallback<SuggestionAction>(actionParametersOf(KeyId to sid, KeyFlag to "accept")), W.accent)
                            Tap("✕", actionRunCallback<SuggestionAction>(actionParametersOf(KeyId to sid, KeyFlag to "dismiss")), W.mute)
                        }
                    }
                }
            }
        }
    }
}

class SuggestionAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val id = parameters[KeyId] ?: return
        val accept = parameters[KeyFlag] == "accept"
        Snapshot.removeFrom(context, "suggestions", id, countKey = "suggestion_count")
        WaitingWidget().updateAll(context)
        runCatching {
            val api = ServerConfig.load(context).api()
            if (accept) api.acceptSuggestion(id) else api.dismissSuggestion(id)
        }.onFailure { Log.w("Widgets", "Suggestion action failed", it) }
        WidgetRefreshWorker.refreshNow(context)
    }
}

// =====================================================================
// People — pinned people: tap for their page, ☎ to call
// =====================================================================

class PeopleWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) = provideContent {
        val s = WidgetSnapshot.load(context)
        Card {
            Column(GlanceModifier.fillMaxSize()) {
                Header("People", onClick = screen(context, "people"))
                if (s.people.isEmpty()) Muted("Pin people in Dana to see them here.")
                LazyColumn(GlanceModifier.fillMaxSize()) {
                    items(s.people) { p ->
                        Row(GlanceModifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                            Box(GlanceModifier.size(32.dp).cornerRadius(16.dp).background(W.surfaceHigh), contentAlignment = Alignment.Center) {
                                Text(p.initials.ifBlank { p.name.take(1) }, style = style(W.ink, 12, bold = true))
                            }
                            Spacer(GlanceModifier.width(10.dp))
                            Column(GlanceModifier.defaultWeight().clickable(actionStartActivity(openApp(context) { putExtra(EXTRA_OPEN_PERSON_ID, p.id) }))) {
                                Text(p.name, style = style(W.ink, 13, bold = true), maxLines = 1)
                                val sub = listOfNotNull(
                                    p.ago.takeIf { it.isNotBlank() },
                                    "${p.open} open".takeIf { p.open > 0 },
                                    "been a while".takeIf { p.drifting },
                                ).joinToString(" · ")
                                if (sub.isNotEmpty()) Text(sub, style = style(if (p.drifting) W.warning else W.mute, 11), maxLines = 1)
                            }
                            if (p.phone.isNotBlank()) {
                                Tap("☎", actionStartActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:${p.phone}")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)), W.accent)
                            }
                        }
                    }
                }
            }
        }
    }
}

// =====================================================================
// Inbox — needs-you count + the AI overview
// =====================================================================

class InboxWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) = provideContent {
        val s = WidgetSnapshot.load(context)
        Card(onClick = screen(context, "inbox")) {
            Column(GlanceModifier.fillMaxSize()) {
                Header("Inbox", "${s.inboxActive} active")
                Row(verticalAlignment = Alignment.Bottom) {
                    Text("${s.inboxNeeds}", style = style(if (s.inboxNeeds > 0) W.accent else W.ink, 30, bold = true))
                    Spacer(GlanceModifier.width(6.dp))
                    Text("need you", style = style(W.mute, 12), modifier = GlanceModifier.padding(bottom = 5.dp))
                }
                Spacer(GlanceModifier.height(4.dp))
                if (s.inboxPoints.isEmpty()) Muted(if (s.inboxActive == 0) "All clear." else "")
                s.inboxPoints.forEach { Text("· $it", style = style(W.inkSoft, 12), maxLines = 2) }
            }
        }
    }
}

// ---- Optimistic snapshot edits ----

private object Snapshot {
    @Synchronized
    fun removeFrom(context: Context, key: String, id: String, countKey: String? = null) {
        val prefs = ServerConfig.prefs(context)
        val o = runCatching { JSONObject(prefs.getString("widget_snapshot", "{}") ?: "{}") }.getOrDefault(JSONObject())
        val arr = o.optJSONArray(key) ?: JSONArray()
        val kept = JSONArray()
        var removed = false
        for (i in 0 until arr.length()) {
            val item = arr.optJSONObject(i) ?: continue
            if (item.optString("id") == id) removed = true else kept.put(item)
        }
        o.put(key, kept)
        if (removed && countKey != null) o.put(countKey, (o.optInt(countKey) - 1).coerceAtLeast(0))
        prefs.edit().putString("widget_snapshot", o.toString()).commit()
    }
}

// ---- Receivers (declared in the phone manifest) ----

class AskWidgetReceiver : GlanceAppWidgetReceiver() { override val glanceAppWidget: GlanceAppWidget = AskWidget() }
class TodayWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = TodayWidget()
    override fun onEnabled(context: Context) { super.onEnabled(context); io.github.ronganam.dana.DanaWidgets.schedule(context) }
}
class WaitingWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = WaitingWidget()
    override fun onEnabled(context: Context) { super.onEnabled(context); io.github.ronganam.dana.DanaWidgets.schedule(context) }
}
class PeopleWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = PeopleWidget()
    override fun onEnabled(context: Context) { super.onEnabled(context); io.github.ronganam.dana.DanaWidgets.schedule(context) }
}
class InboxWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = InboxWidget()
    override fun onEnabled(context: Context) { super.onEnabled(context); io.github.ronganam.dana.DanaWidgets.schedule(context) }
}
class ShoppingWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = ShoppingWidget()
    override fun onEnabled(context: Context) { super.onEnabled(context); io.github.ronganam.dana.DanaWidgets.schedule(context) }
}
