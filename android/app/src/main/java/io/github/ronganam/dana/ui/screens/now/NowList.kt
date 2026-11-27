package io.github.ronganam.dana.ui.screens.now

import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.pulltorefresh.PullToRefreshDefaults
import androidx.compose.material3.pulltorefresh.rememberPullToRefreshState
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.models.NowItem
import io.github.ronganam.dana.data.models.NowPayload
import io.github.ronganam.dana.data.models.QuietEntry
import io.github.ronganam.dana.ui.components.arrive
import io.github.ronganam.dana.ui.components.circuit.PlaceHeader
import io.github.ronganam.dana.ui.components.circuit.SectionHeader
import io.github.ronganam.dana.ui.components.circuit.groupItemShape
import io.github.ronganam.dana.ui.components.riseIn
import io.github.ronganam.dana.ui.theme.Motion
import io.github.ronganam.dana.ui.theme.circuitColors
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/** Everything the Now list needs, read once by the destination. */
class NowListState(
    val payload: NowPayload?,
    val items: List<NowItem>,
    /** Running live activities (waiting first). */
    val live: List<NowLiveRow>,
    val quiet: List<QuietEntry>,
    val quietTotal: Int,
    val drafts: Map<String, ItemDraft>,
    /** The row the owner opened. */
    val openId: String?,
    val loading: Boolean,
    val failed: Boolean,
    val quietOpen: Boolean,
    val now: LocalDateTime,
    /** When the shown answer came from the server (epoch ms; null = never). */
    val updatedAt: Long?,
    /** Reminders done today, newest first. */
    val done: List<DoneEntry>
)

/** What the list does besides the cards. */
class NowListActions(
    val refresh: () -> Unit,
    val toggleQuiet: () -> Unit,
    val quietAct: (QuietEntry) -> Unit,
    /** A timeline item tapped (it has a reminder behind it). */
    val openDay: (DayEntry) -> Unit,
    val openDone: (DoneEntry) -> Unit
)

/** Two columns (Going on | Next) from this content width up. */
private val TWO_COLUMNS = 440.dp

/**
 * Now, top to bottom: the greeting with the date (and the server's lede);
 * "Needs you" (a waiting run first, then the queue: the first card open when
 * it is an approval, the rest compact; each opens in place and swipes), or
 * the calm card when nothing needs you; "Going on" (live activities); "Next"
 * (the rest of today as a timeline); "Earlier today" (reminders done);
 * "Can wait". Wide content puts Going on and Next side by side. Pull to
 * refresh. Only the first load's rows arrive staggered; later only new rows
 * rise in, and a row acted on fades out while the others close the gap.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NowListPane(state: NowListState, handlers: NowHandlers, live: LiveHandlers, actions: NowListActions, headerAsk: (@Composable () -> Unit)? = null) {
    val pull = rememberPullToRefreshState()
    var pullRefreshing by remember { mutableStateOf(false) }
    LaunchedEffect(state.loading) { if (!state.loading) pullRefreshing = false }
    val newCards = remember { NewCards() }
    val ids = state.items.map { it.id }
    val fresh = remember(ids) { newCards.fresh(ids) }
    val loaded = state.payload != null
    val waiting = state.live.filter { it.waiting }
    val running = state.live.filter { !it.waiting }
    val needsCount = waiting.size + state.items.size
    val calm = loaded && needsCount == 0
    val slots = state.payload?.daySlots.orEmpty()
    val calendarOk = state.payload?.calendarOk ?: true
    val c = circuitColors

    PullToRefreshBox(
        isRefreshing = pullRefreshing, onRefresh = { pullRefreshing = true; actions.refresh() }, state = pull, modifier = Modifier.fillMaxSize(),
        indicator = {
            PullToRefreshDefaults.Indicator(
                state = pull, isRefreshing = pullRefreshing, modifier = Modifier.align(Alignment.TopCenter),
                containerColor = MaterialTheme.colorScheme.surfaceContainerHigh, color = MaterialTheme.colorScheme.primary
            )
        }
    ) {
        BoxWithConstraints(Modifier.fillMaxSize()) {
            // Side by side only unfolded (a wide cover screen keeps one column).
            val two = maxWidth >= TWO_COLUMNS && io.github.ronganam.dana.ui.shell.LocalShellLayout.current != io.github.ronganam.dana.ui.shell.ShellLayout.STACKED
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 32.dp)) {
                // Calm, the lede goes in the calm card instead.
                item(key = "head") { Header(state, showLede = !calm, headerAsk) }
                when {
                    calm -> item(key = "calm") {
                        CalmCard(
                            calmLine(state.payload?.lede, state.payload?.notifications?.noise ?: 0),
                            state.done, slots, state.now.toLocalTime(),
                            Modifier.padding(top = 16.dp)
                        )
                    }
                    needsCount > 0 -> {
                        item(key = "needs") {
                            SectionHeader(
                                "Needs you", meta = needsCount.toString(), metaColor = c.warm,
                                modifier = Modifier.padding(horizontal = NOW_GUTTER).padding(top = 16.dp)
                            )
                        }
                        waiting.forEachIndexed { i, r ->
                            item(key = "w_" + r.id) {
                                LiveWaitingCard(r, groupItemShape(i, needsCount), live, Modifier.padding(bottom = 2.dp))
                            }
                        }
                        state.items.forEachIndexed { j, item ->
                            val index = waiting.size + j
                            item(key = "i_" + item.id) {
                                NeedsRow(
                                    item = item,
                                    draft = state.drafts[item.id],
                                    expanded = state.openId == item.id,
                                    // The waiting one first: an approval at the top is open.
                                    featured = index == 0 && item.kind == "approval",
                                    shape = groupItemShape(index, needsCount),
                                    handlers = handlers,
                                    modifier = Modifier
                                        .animateItem(fadeInSpec = null, placementSpec = tween(Motion.BASE, easing = Motion.Standard), fadeOutSpec = tween(Motion.COMPLETE - Motion.BASE, easing = Motion.Accelerate))
                                        .arrive("now", index)
                                        .riseIn(item.id in fresh)
                                        .padding(bottom = 2.dp)
                                )
                            }
                        }
                    }
                    state.failed -> item(key = "unreachable") {
                        Text(
                            "Can't reach Dana. Pull to try again.",
                            style = MaterialTheme.typography.bodyLarge, color = c.inkSoft,
                            modifier = Modifier.padding(horizontal = NOW_GUTTER, vertical = 16.dp)
                        )
                    }
                    else -> item(key = "loading") {
                        Text(
                            "Looking…", style = MaterialTheme.typography.bodyLarge, color = c.inkSoft,
                            modifier = Modifier.padding(horizontal = NOW_GUTTER, vertical = 16.dp)
                        )
                    }
                }
                if (loaded) {
                    if (two) item(key = "cols") {
                        Row(Modifier.fillMaxWidth().padding(horizontal = NOW_GUTTER).padding(top = 24.dp), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                            if (running.isNotEmpty()) GoingOnSection(running, live, Modifier.weight(1f))
                            NextSection(slots, state.now.toLocalTime(), calendarOk, actions.openDay, Modifier.weight(1f))
                        }
                    } else {
                        if (running.isNotEmpty()) item(key = "going") {
                            GoingOnSection(running, live, Modifier.padding(horizontal = NOW_GUTTER).padding(top = 24.dp))
                        }
                        item(key = "next") {
                            NextSection(slots, state.now.toLocalTime(), calendarOk, actions.openDay, Modifier.padding(horizontal = NOW_GUTTER).padding(top = 24.dp))
                        }
                    }
                    if (state.done.isNotEmpty()) item(key = "earlier") {
                        EarlierSection(state.done, actions.openDone, Modifier.padding(horizontal = NOW_GUTTER).padding(top = 24.dp))
                    }
                    if (state.quiet.isNotEmpty()) item(key = "quiet") {
                        CanWaitSection(state.quiet, state.quietTotal, actions.quietAct, Modifier.padding(horizontal = NOW_GUTTER).padding(top = 24.dp))
                    }
                }
            }
        }
    }
}

private val DATE = DateTimeFormatter.ofPattern("EEEE d MMMM", Locale.ENGLISH)

/** The greeting in the header row (beside Ask Dana), the date and the server's lede under it, and how old the data is when the last refresh failed. */
@Composable
private fun Header(state: NowListState, showLede: Boolean, trailing: (@Composable () -> Unit)?) {
    val lede = state.payload?.lede?.trim()?.trimEnd('.')?.takeIf { it.isNotEmpty() && showLede }
    val date = state.now.format(DATE)
    PlaceHeader(
        NowModel.greeting(state.now.hour),
        supporting = if (lede != null) "$date · $lede" else date,
        trailing = trailing
    )
    val at = state.updatedAt
    if (state.payload != null && state.failed && at != null) Text(
        NowModel.updatedAgo(at, System.currentTimeMillis()),
        style = MaterialTheme.typography.bodyMedium,
        color = circuitColors.mute,
        modifier = Modifier.padding(horizontal = NOW_GUTTER).padding(top = 4.dp)
    )
}
