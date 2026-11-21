package app.ganam.assistant.ui.screens.now

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshContainer
import androidx.compose.material3.pulltorefresh.rememberPullToRefreshState
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.ganam.assistant.data.models.NowItem
import app.ganam.assistant.data.models.NowPayload
import app.ganam.assistant.data.models.QuietEntry
import app.ganam.assistant.ui.components.InkButton
import app.ganam.assistant.ui.components.InkEmphasis
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.components.Rule
import app.ganam.assistant.ui.shell.DayItem
import app.ganam.assistant.ui.theme.GanamSerif
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/** Everything the Now list needs, read once by the destination. */
class NowListState(
    val payload: NowPayload?,
    val items: List<NowItem>,
    val quiet: List<QuietEntry>,
    val quietTotal: Int,
    val drafts: Map<String, ItemDraft>,
    val openId: String?,
    val openIsUser: Boolean,
    val loading: Boolean,
    val failed: Boolean,
    val fresh: Boolean,
    val quietOpen: Boolean,
    val now: LocalDateTime,
    /** The rest of today (cover only): time label → item. */
    val later: List<Pair<String, DayItem>>
)

/** What the list does besides the rows. */
class NowListActions(
    val refresh: () -> Unit,
    val toggleQuiet: () -> Unit,
    val quietAct: (QuietEntry, primary: Boolean) -> Unit,
    val quietDismissAll: () -> Unit,
    val clearNoise: () -> Unit,
    val openNotifications: () -> Unit
)

/**
 * Now, the list pane: date + "Now", the lede, "Needs you · N" and the rows,
 * the folded quieter suggestions, the notification line and — on the cover
 * screen — "Later today".
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NowListPane(state: NowListState, handlers: NowHandlers, actions: NowListActions, wide: Boolean) {
    val pull = rememberPullToRefreshState()
    if (pull.isRefreshing) LaunchedEffect(true) { actions.refresh() }
    LaunchedEffect(state.loading) { if (!state.loading) pull.endRefresh() }

    Box(Modifier.fillMaxSize().nestedScroll(pull.nestedScrollConnection)) {
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(top = 20.dp, bottom = 32.dp)) {
            item(key = "head") { Header(state) }
            item(key = "needs") {
                Row(Modifier.fillMaxWidth().padding(horizontal = NOW_GUTTER).padding(top = 22.dp, bottom = 8.dp)) {
                    MonoLabel(if (state.items.isNotEmpty()) "Needs you · ${state.items.size}" else "Needs you", modifier = Modifier.weight(1f))
                    if (state.items.isNotEmpty()) MonoLabel(if (wide) "drag → day" else "swipe → do · ← dismiss")
                }
                Rule(Modifier.padding(horizontal = NOW_GUTTER))
            }
            when {
                state.items.isNotEmpty() -> items(state.items, key = { "i_" + it.id }) { item ->
                    NowRow(
                        item = item,
                        draft = state.drafts[item.id],
                        expanded = !wide && state.openId == item.id && state.openIsUser,
                        selected = wide && state.openId == item.id,
                        wide = wide,
                        handlers = handlers
                    )
                }
                state.payload == null && state.failed -> item(key = "unreachable") {
                    Empty("Can't reach Dana.", "Your queue shows here once the server answers.", "Try again", actions.refresh)
                }
                state.payload == null -> item(key = "loading") {
                    Text(
                        "Looking…",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = NOW_GUTTER, vertical = 16.dp)
                    )
                }
                else -> item(key = "empty") { Empty("Nothing needs you.", "New things land here quietly. Only urgent ones interrupt.") }
            }
            if (state.quiet.isNotEmpty()) {
                item(key = "quiet_fold") {
                    Fold(
                        "${state.quietTotal} quieter suggestion${if (state.quietTotal == 1) "" else "s"}, never shown as pop-ups",
                        if (state.quietOpen) "hide" else "show",
                        actions.toggleQuiet
                    )
                }
                if (state.quietOpen) {
                    items(state.quiet, key = { "q_" + it.id }) { q -> QuietRow(q, actions) }
                    item(key = "quiet_all") {
                        InkButton("Dismiss all", modifier = Modifier.padding(horizontal = NOW_GUTTER, vertical = 8.dp)) { actions.quietDismissAll() }
                    }
                }
            }
            state.payload?.notifications?.takeIf { it.total > 0 }?.let { n ->
                item(key = "nline") {
                    Row(
                        Modifier.fillMaxWidth().padding(horizontal = NOW_GUTTER).padding(vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            "${n.total} notification${if (n.total == 1) "" else "s"}" + if (n.noise > 0) ", ${n.noise} of them noise" else "",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.weight(1f)
                        )
                        if (n.noise > 0) InkButton("Clear noise", emphasis = InkEmphasis.BARE) { actions.clearNoise() }
                        InkButton("Open", emphasis = InkEmphasis.BARE) { actions.openNotifications() }
                    }
                    Rule(Modifier.padding(horizontal = NOW_GUTTER))
                }
            }
            if (!wide && state.payload != null) {
                item(key = "later_head") {
                    Row(Modifier.fillMaxWidth().padding(horizontal = NOW_GUTTER).padding(top = 22.dp, bottom = 8.dp)) { MonoLabel("Later today") }
                    Rule(Modifier.padding(horizontal = NOW_GUTTER))
                }
                if (state.later.isEmpty()) item(key = "later_none") {
                    Text(
                        "Nothing else today.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = NOW_GUTTER, vertical = 10.dp)
                    )
                }
                items(state.later.size, key = { "l_$it" }) { i -> LaterRow(state.later[i].first, state.later[i].second) }
            }
        }
        if (pull.progress > 0f || pull.isRefreshing) PullToRefreshContainer(
            state = pull, modifier = Modifier.align(Alignment.TopCenter),
            containerColor = MaterialTheme.colorScheme.surfaceContainerHigh, contentColor = MaterialTheme.colorScheme.primary
        )
    }
}

private val KICKER = DateTimeFormatter.ofPattern("EEE d MMM · HH:mm", Locale.ENGLISH)

@Composable
private fun Header(state: NowListState) {
    Column(Modifier.padding(horizontal = NOW_GUTTER)) {
        MonoLabel(state.now.format(KICKER) + if (state.payload != null && (state.failed || !state.fresh)) " · last update" else "")
        Text("Now", style = MaterialTheme.typography.displayMedium, modifier = Modifier.padding(top = 2.dp))
        state.payload?.lede?.takeIf { it.isNotBlank() }?.let { lede ->
            Text(
                lede,
                style = MaterialTheme.typography.headlineSmall.copy(
                    fontFamily = GanamSerif, fontStyle = FontStyle.Italic, fontSize = 20.sp, lineHeight = 25.sp,
                    textDirection = TextDirection.Content
                ),
                modifier = Modifier.padding(top = 14.dp).fillMaxWidth()
            )
        }
    }
}

@Composable
private fun Empty(title: String, line: String, action: String? = null, onAction: (() -> Unit)? = null) {
    Column(Modifier.padding(horizontal = NOW_GUTTER).padding(top = 26.dp, bottom = 10.dp)) {
        Text(title, style = MaterialTheme.typography.headlineSmall.copy(fontFamily = GanamSerif, fontStyle = FontStyle.Italic, fontSize = 26.sp))
        Text(line, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp))
        if (action != null && onAction != null) InkButton(action, emphasis = InkEmphasis.PRIMARY, modifier = Modifier.padding(top = 12.dp), onClick = onAction)
    }
}

@Composable
internal fun Fold(text: String, trailing: String, onClick: () -> Unit) {
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = NOW_GUTTER, vertical = 13.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
            MonoLabel(trailing)
        }
        Rule(Modifier.padding(horizontal = NOW_GUTTER))
    }
}

@Composable
private fun QuietRow(q: QuietEntry, actions: NowListActions) {
    Column(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = NOW_GUTTER, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            NowTile(remember(q) { NowModel.visual(q) }, size = 24.dp)
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(q.title, style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content), color = MaterialTheme.colorScheme.onSurface)
                if (q.source.isNotBlank()) MonoLabel(app.ganam.assistant.ui.screens.common.isolatedParts(q.source), modifier = Modifier.padding(top = 2.dp))
            }
            Spacer(Modifier.width(8.dp))
            q.primary?.let { InkButton(it.label) { actions.quietAct(q, true) } }
            if (q.secondary.isNotEmpty()) InkButton("×", emphasis = InkEmphasis.BARE, modifier = Modifier.padding(start = 4.dp)) { actions.quietAct(q, false) }
        }
        Rule(Modifier.padding(horizontal = NOW_GUTTER))
    }
}

@Composable
internal fun LaterRow(at: String, item: DayItem, gutter: androidx.compose.ui.unit.Dp = NOW_GUTTER) {
    Column(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = gutter, vertical = 10.dp), verticalAlignment = Alignment.Top) {
            Box(Modifier.width(80.dp).padding(top = 2.dp)) { MonoLabel(at) }
            Text(
                item.title,
                style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Content),
                color = if (item.isEvent) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f)
            )
        }
        Rule(Modifier.padding(horizontal = gutter))
    }
}
