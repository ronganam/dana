package io.github.ronganam.dana.ui.screens.now

import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.pulltorefresh.PullToRefreshDefaults
import androidx.compose.material3.pulltorefresh.rememberPullToRefreshState
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.models.NowItem
import io.github.ronganam.dana.data.models.NowPayload
import io.github.ronganam.dana.data.models.QuietEntry
import io.github.ronganam.dana.ui.components.ActionPill
import io.github.ronganam.dana.ui.components.HueMark
import io.github.ronganam.dana.ui.components.MenuEntry
import io.github.ronganam.dana.ui.components.OverflowMenu
import androidx.compose.ui.text.font.FontWeight
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.RollingText
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.components.arrive
import io.github.ronganam.dana.ui.components.riseIn
import io.github.ronganam.dana.ui.shell.DayItem
import io.github.ronganam.dana.ui.theme.DanaSerif
import io.github.ronganam.dana.ui.theme.Hue
import io.github.ronganam.dana.ui.theme.Motion
import io.github.ronganam.dana.ui.theme.hues
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
    /** When the shown answer came from the server (epoch ms; null = never). */
    val updatedAt: Long?
)

/** What the list does besides the cards. */
class NowListActions(
    val refresh: () -> Unit,
    val toggleQuiet: () -> Unit,
    val quietAct: (QuietEntry, primary: Boolean) -> Unit,
    val quietDismissAll: () -> Unit,
    val openNotifications: () -> Unit,
    /** A day-timeline item tapped (it has a reminder behind it). */
    val openDay: (DayEntry) -> Unit
)

/**
 * Now, the list pane, top to bottom: the date (mono) and the lede (serif);
 * on the cover screen the day (the strip of hours and what is left today;
 * unfolded the shell's day strip is above); "Needs you · N" and the rows;
 * "Can wait · N" folded; the notifications line. With an empty queue the
 * top is one centred block instead ([AllClear]). Pull to refresh. Only the
 * first load's rows arrive staggered; later only new rows rise in, and a row
 * acted on fades out while the others close the gap.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NowListPane(state: NowListState, handlers: NowHandlers, actions: NowListActions, wide: Boolean) {
    val pull = rememberPullToRefreshState()
    var pullRefreshing by remember { mutableStateOf(false) }
    LaunchedEffect(state.loading) { if (!state.loading) pullRefreshing = false }
    val newCards = remember { NewCards() }
    val ids = state.items.map { it.id }
    val fresh = remember(ids) { newCards.fresh(ids) }
    val loaded = state.payload != null
    val allClear = loaded && state.items.isEmpty()
    val slots = state.payload?.daySlots.orEmpty()
    val calendarOk = state.payload?.calendarOk ?: true

    PullToRefreshBox(
        isRefreshing = pullRefreshing, onRefresh = { pullRefreshing = true; actions.refresh() }, state = pull, modifier = Modifier.fillMaxSize(),
        indicator = {
            PullToRefreshDefaults.Indicator(
                state = pull, isRefreshing = pullRefreshing, modifier = Modifier.align(Alignment.TopCenter),
                containerColor = MaterialTheme.colorScheme.surfaceContainerHigh, color = MaterialTheme.colorScheme.primary
            )
        }
    ) {
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(top = 24.dp, bottom = 32.dp)) {
            if (allClear) item(key = "clear") {
                AllClear(state, if (wide) null else slots, calendarOk, actions.openDay, Modifier.fillParentMaxHeight(0.78f))
            } else {
                item(key = "head") { Header(state, wide) }
                if (!wide && loaded) item(key = "day") {
                    Column(Modifier.padding(horizontal = NOW_GUTTER).padding(top = 22.dp)) {
                        NowDayBlock(slots, state.now.toLocalTime(), actions.openDay, centred = false)
                    }
                    if (!calendarOk) CalendarNote()
                } else if (wide && loaded && !calendarOk) item(key = "cal") { CalendarNote() }
                item(key = "needs") {
                    Row(
                        Modifier.fillMaxWidth().padding(horizontal = NOW_GUTTER).padding(top = 30.dp, bottom = 2.dp).semantics(mergeDescendants = true) { heading() },
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        MonoLabel(if (state.items.isNotEmpty()) "Needs you · " else "Needs you")
                        if (state.items.isNotEmpty()) RollingText(
                            state.items.size.toString(),
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        Spacer(Modifier.weight(1f))
                        if (state.items.isNotEmpty() && !wide) MonoLabel(
                            "← dismiss · snooze →",
                            color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f)
                        )
                    }
                }
            }
            when {
                state.items.isNotEmpty() -> itemsIndexed(state.items, key = { _, it -> "i_" + it.id }) { index, item ->
                    NowRow(
                        modifier = Modifier
                            // Acting: the card fades out, then the ones below close the gap (translation only).
                            .animateItem(fadeInSpec = null, placementSpec = tween(Motion.BASE, easing = Motion.Standard), fadeOutSpec = tween(Motion.COMPLETE - Motion.BASE, easing = Motion.Accelerate))
                            .arrive("now", index)
                            .riseIn(item.id in fresh),
                        item = item,
                        draft = state.drafts[item.id],
                        expanded = !wide && state.openId == item.id && state.openIsUser,
                        selected = wide && state.openId == item.id,
                        wide = wide,
                        handlers = handlers
                    )
                }
                state.payload == null && state.failed -> item(key = "unreachable") {
                    Empty("Can't reach Dana.", "Your queue shows here once the server answers.", "Try again", hues.rose, actions.refresh)
                }
                state.payload == null -> item(key = "loading") {
                    Text(
                        "Looking…",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = NOW_GUTTER, vertical = 16.dp)
                    )
                }
                else -> Unit
            }
            if (state.quiet.isNotEmpty()) {
                item(key = "quiet_fold") { CanWait(state.quietTotal, state.quietOpen, actions.toggleQuiet) }
                if (state.quietOpen) {
                    items(state.quiet, key = { "q_" + it.id }) { q -> QuietRow(q, actions) }
                    item(key = "quiet_all") {
                        InkButton("Dismiss all", modifier = Modifier.padding(horizontal = NOW_GUTTER, vertical = 8.dp)) { actions.quietDismissAll() }
                    }
                }
            }
            state.payload?.notifications?.takeIf { it.total > 0 }?.let { n ->
                item(key = "nline") { NotificationsLine(n.total, n.needsYou, n.noise, actions.openNotifications) }
            }
        }
    }
}

private val DATE = DateTimeFormatter.ofPattern("EEEE · d MMMM", Locale.ENGLISH)

/** The date in mono caps, the lede as a large serif line, and how old the data is when the last refresh failed. */
@Composable
private fun Header(state: NowListState, wide: Boolean) {
    val lede = state.payload?.lede?.takeIf { it.isNotBlank() }
        ?: if (state.payload != null) NowModel.ledeFallback(state.now.hour, state.items.isEmpty()) else null
    Column(Modifier.padding(horizontal = NOW_GUTTER)) {
        MonoLabel(state.now.format(DATE))
        if (lede != null) Text(
            lede,
            style = MaterialTheme.typography.displayMedium.copy(
                fontSize = if (wide) 34.sp else 40.sp, lineHeight = if (wide) 38.sp else 44.sp, textDirection = TextDirection.Content
            ),
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.padding(top = 12.dp).fillMaxWidth().semantics { heading() }
        )
        UpdatedAgo(state, Modifier.padding(top = 10.dp))
    }
}

/** "Updated N min ago", only when the last refresh failed. */
@Composable
private fun UpdatedAgo(state: NowListState, modifier: Modifier = Modifier) {
    val at = state.updatedAt
    if (state.payload != null && state.failed && at != null) MonoLabel(
        NowModel.updatedAgo(at, System.currentTimeMillis()),
        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.8f),
        modifier = modifier
    )
}

/**
 * Nothing in the queue, centred: the date in mono caps, the italic serif
 * "All clear." with a sage dot under it, the day ([slots]; null unfolded,
 * where the shell's strip is the day) and the server's lede as one quiet line.
 */
@Composable
private fun AllClear(state: NowListState, slots: List<io.github.ronganam.dana.data.models.DaySlotInfo>?, calendarOk: Boolean, openDay: (DayEntry) -> Unit, modifier: Modifier = Modifier) {
    val lede = state.payload?.lede?.takeIf { it.isNotBlank() }
    Column(
        modifier.fillMaxWidth().padding(horizontal = NOW_GUTTER),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        MonoLabel(state.now.format(DATE))
        Text(
            "All clear.",
            style = MaterialTheme.typography.displayLarge.copy(fontStyle = FontStyle.Italic, fontSize = 46.sp, lineHeight = 50.sp),
            color = MaterialTheme.colorScheme.onSurface,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
            modifier = Modifier.padding(top = 12.dp).semantics { heading() }
        )
        Box(Modifier.padding(top = 14.dp).size(8.dp).clip(CircleShape).background(hues.sage))
        if (slots != null) NowDayBlock(slots, state.now.toLocalTime(), openDay, centred = true, modifier = Modifier.padding(top = 26.dp))
        if (!calendarOk) CalendarNote(centred = true)
        if (lede != null) Text(
            lede,
            style = MaterialTheme.typography.bodySmall.copy(fontSize = 13.sp, lineHeight = 19.sp, textDirection = TextDirection.Content),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
            modifier = Modifier.padding(top = 22.dp).widthIn(max = 260.dp)
        )
        UpdatedAgo(state, Modifier.padding(top = 10.dp))
    }
}

@Composable
private fun CalendarNote(centred: Boolean = false) {
    Text(
        "Calendar couldn't be read",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        textAlign = if (centred) androidx.compose.ui.text.style.TextAlign.Center else null,
        modifier = Modifier.fillMaxWidth().padding(horizontal = NOW_GUTTER).padding(top = 8.dp)
    )
}

@Composable
private fun Empty(title: String, line: String, action: String? = null, mark: Color? = null, onAction: (() -> Unit)? = null) {
    Column(Modifier.padding(horizontal = NOW_GUTTER).padding(top = 26.dp, bottom = 10.dp)) {
        if (mark != null) HueMark(mark, Modifier.padding(bottom = 10.dp))
        Text(title, style = MaterialTheme.typography.headlineSmall.copy(fontFamily = DanaSerif, fontStyle = FontStyle.Italic, fontSize = 26.sp))
        Text(line, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp))
        if (action != null && onAction != null) InkButton(action, emphasis = InkEmphasis.SOLID, modifier = Modifier.padding(top = 12.dp), onClick = onAction)
    }
}

/** "Can wait · 7", folded; the count rolls when it changes. */
@Composable
private fun CanWait(total: Int, open: Boolean, onClick: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(top = 18.dp)) {
        Row(
            Modifier.fillMaxWidth()
                .clickable(role = Role.Button, onClickLabel = if (open) "Hide" else "Show", onClick = onClick)
                .padding(horizontal = NOW_GUTTER, vertical = 13.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            MonoLabel("Can wait · ")
            RollingText(total.toString(), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.weight(1f))
            MonoLabel(if (open) "hide ↑" else "show ↓")
        }
        Rule(Modifier.padding(horizontal = NOW_GUTTER))
    }
}

/** A quiet row: the dot in its kind's hue, the title, its source; its primary as a pill and the rest under ⋯. */
@Composable
private fun QuietRow(q: QuietEntry, actions: NowListActions) {
    val hue = hues.of(remember(q) { NowModel.hueOf(q) })
    Column(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().padding(start = NOW_GUTTER, end = NOW_GUTTER - 10.dp).padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).clip(CircleShape).background(hue))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(q.title, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium, textDirection = TextDirection.Content), color = MaterialTheme.colorScheme.onSurface)
                if (q.source.isNotBlank()) Text(
                    io.github.ronganam.dana.ui.screens.common.isolatedParts(q.source),
                    style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                    modifier = Modifier.padding(top = 2.dp)
                )
            }
            Spacer(Modifier.width(8.dp))
            q.primary?.let { ActionPill(it.label) { actions.quietAct(q, true) } }
            q.secondary.firstOrNull()?.let { s -> OverflowMenu(listOf(MenuEntry(s.label) { actions.quietAct(q, false) })) }
        }
        Rule(Modifier.padding(horizontal = NOW_GUTTER))
    }
}

/** "17 notifications · 1 needs you · 11 noise": one quiet line that opens the Notifications place. */
@Composable
private fun NotificationsLine(total: Int, needsYou: Int, noise: Int, onOpen: () -> Unit) {
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth()
                .clickable(role = Role.Button, onClickLabel = "Open notifications", onClick = onOpen)
                .padding(horizontal = NOW_GUTTER, vertical = 13.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(Modifier.size(6.dp).clip(CircleShape).background(hues.of(Hue.TEAL)))
            Spacer(Modifier.width(10.dp))
            Text(
                NowModel.notificationsLine(total, needsYou, noise),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f)
            )
            MonoLabel("→")
        }
        Rule(Modifier.padding(horizontal = NOW_GUTTER))
    }
}

@Composable
internal fun LaterRow(at: String, item: DayItem, gutter: Dp = NOW_GUTTER) {
    Column(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = gutter, vertical = 10.dp), verticalAlignment = Alignment.Top) {
            // Events in sky, reminders in amber (Ember's kinds).
            Box(Modifier.width(80.dp).padding(top = 2.dp)) { MonoLabel(at, color = hues.text(if (item.isEvent) Hue.SKY else Hue.AMBER)) }
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
