package io.github.ronganam.dana.ui.shell.destinations

import android.content.Intent
import androidx.compose.runtime.*
import androidx.core.net.toUri
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import io.github.ronganam.dana.data.models.NowAction
import io.github.ronganam.dana.data.models.NowItem
import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.data.notifications.PhoneNotificationActions
import io.github.ronganam.dana.ui.screens.now.NowHandlers
import io.github.ronganam.dana.ui.screens.now.NowListActions
import io.github.ronganam.dana.ui.screens.now.NowListPane
import io.github.ronganam.dana.ui.screens.now.NowListState
import io.github.ronganam.dana.ui.screens.now.NowModel
import io.github.ronganam.dana.ui.screens.now.NowWebLink
import io.github.ronganam.dana.ui.screens.now.NowStage
import io.github.ronganam.dana.ui.shell.DayItem
import io.github.ronganam.dana.ui.shell.DaySlot
import io.github.ronganam.dana.ui.shell.Destination
import io.github.ronganam.dana.ui.shell.LocalShellLayout
import io.github.ronganam.dana.ui.shell.ShellLayout
import io.github.ronganam.dana.ui.shell.dayItems
import io.github.ronganam.dana.ui.shell.rememberNow
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.Place
import io.github.ronganam.dana.ui.viewmodels.ThreadContext
import java.time.format.DateTimeFormatter

/**
 * Now: the queue of things that need a decision (`NowController`, from
 * `GET /api/now`) as the list; unfolded, the selected row's evidence and
 * decision on the stage (the first row is picked for you, `auto`; the day
 * strip's Now cell shows the rest of today instead). On the cover screen a
 * tap opens a row in place, so Now never drills down.
 *
 * The cover screen's cards swipe: towards the start = dismiss, towards the
 * end = the card's own snooze (both server actions, so both answer with Undo).
 *
 * Client actions are done here: `open` (approval → its thread, notification →
 * its app, review → the review screen, else the evidence), `reply` (the reply
 * box under the evidence), "Ask Dana about this" (a context chat).
 */
@Composable
fun rememberNowDestination(viewModel: MainViewModel): Destination {
    val shell = viewModel.shellController
    val nc = viewModel.nowController
    val context = LocalContext.current
    val pick by shell.now.collectAsState()
    val nowDay by shell.nowDay.collectAsState()
    val payload by nc.payload.collectAsState()
    val hidden by nc.hidden.collectAsState()
    val drafts by nc.drafts.collectAsState()
    val loading by nc.loading.collectAsState()
    val failed by nc.failed.collectAsState()
    val fresh by nc.fresh.collectAsState()
    val updatedAt by nc.updatedAt.collectAsState()
    val quietOpen by shell.quietOpen.collectAsState()
    val events by viewModel.dayController.events.collectAsState()
    val tasks by viewModel.tasksController.tasks.collectAsState()
    val now by rememberNow()
    val wide = LocalShellLayout.current != ShellLayout.STACKED

    val items = remember(payload, hidden) { payload?.items.orEmpty().filter { it.id !in hidden } }
    val quiet = remember(payload, hidden) { payload?.quiet.orEmpty().filter { it.id !in hidden } }
    val quietTotal = (payload?.quietTotal ?: 0) - (payload?.quiet.orEmpty().size - quiet.size)
    val later = remember(events, tasks, now) { laterToday(events, tasks, now) }

    // Fresh while on screen: on open / resume and every 60 s, stopped in the background (plus pushes and actions, see MainViewModel).
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val obs = LifecycleEventObserver { _, e ->
            when (e) {
                Lifecycle.Event.ON_RESUME -> nc.startPolling()
                Lifecycle.Event.ON_PAUSE -> nc.stopPolling()
                else -> Unit
            }
        }
        lifecycle.addObserver(obs)
        if (lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) nc.startPolling()
        onDispose { lifecycle.removeObserver(obs); nc.stopPolling() }
    }

    // Unfolded: keep a row on the stage (unless the owner asked for the day); drop picks that left.
    LaunchedEffect(wide, items, pick, nowDay, payload != null) {
        val cur = pick
        if (payload == null) return@LaunchedEffect
        if (cur != null && items.none { it.id == cur.id }) {
            val next = if (wide && !nowDay) items.firstOrNull()?.id else null
            shell.selectNow(next, auto = true)
        } else if (wide && cur == null && !nowDay) {
            items.firstOrNull()?.let { shell.selectNow(it.id, auto = true) }
        }
    }

    fun expand(item: NowItem) {
        shell.selectNow(item.id)
    }

    fun openClient(item: NowItem) {
        val n = item.notification
        when {
            item.ref?.type == "thread" -> {
                viewModel.showConversation(item.ref.id)
                shell.openThread()
            }
            n != null -> if (!PhoneNotificationActions.open(context, n.key, n.packageName)) {
                nc.notice("Couldn't open ${n.app ?: "it"}")
            }
            item.kind == "review" -> viewModel.openReview()
            // An unusual charge: "Ask Dana" about that transaction.
            item.ref?.type == "transaction" -> askAbout(viewModel, item)
            else -> expand(item)
        }
    }

    val handlers = remember(viewModel, wide) {
        NowHandlers(
            perform = { item, action, isPrimary -> perform(viewModel, item, action, isPrimary, wide, ::openClient, ::expand) },
            toggle = { item ->
                val cur = shell.now.value
                if (wide) shell.selectNow(item.id)
                else shell.selectNow(if (cur?.id == item.id && !cur.auto) null else item.id)
            },
            ask = { item -> askAbout(viewModel, item) },
            reply = { item, text ->
                val err = PhoneNotificationActions.reply(context, item.notification?.key, text)
                if (err == null) nc.replied(item, item.source.split(" · ").takeIf { it.size >= 3 }?.get(1)) else nc.notice(err)
            },
            choice = { item, key, i -> nc.pickChoice(item.id, key, i) },
            check = { item, key, i -> nc.toggleCheck(item, key, i) },
            schedule = { item, slot -> schedule(viewModel, item, slot, wide) }
        )
    }

    val listState = NowListState(
        payload = payload,
        items = items,
        quiet = quiet,
        quietTotal = quietTotal.coerceAtLeast(quiet.size),
        drafts = drafts,
        openId = pick?.id,
        openIsUser = pick?.auto == false,
        loading = loading,
        failed = failed,
        fresh = fresh,
        quietOpen = quietOpen,
        now = now,
        updatedAt = updatedAt
    )
    val listActions = remember(viewModel) {
        NowListActions(
            refresh = { nc.refresh(); viewModel.dayController.refreshIfStale() },
            toggleQuiet = { shell.toggleQuiet() },
            quietAct = { q, primary ->
                val a = if (primary) q.primary else q.secondary.firstOrNull()
                if (a != null) nc.actQuiet(q, a)
            },
            quietDismissAll = { nc.dismissAllQuiet() },
            openNotifications = { shell.go(Place.NOTIFICATIONS) },
            openDay = { e -> e.taskId?.let { shell.openReminder(it) } }
        )
    }
    val selected = items.firstOrNull { it.id == pick?.id }

    return Destination(
        place = Place.NOW,
        picked = false,
        onBack = { shell.selectNow(null) },
        list = { NowListPane(listState, handlers, listActions, wide) },
        stage = { NowStage(selected, selected?.let { drafts[it.id] }, handlers, now, later, queueEmpty = items.isEmpty()) }
    )
}

/** Run [action]: server actions go to the controller (the selection moves on), client ones are done here. */
private fun perform(
    viewModel: MainViewModel,
    item: NowItem,
    action: NowAction,
    isPrimary: Boolean,
    wide: Boolean,
    openClient: (NowItem) -> Unit,
    expand: (NowItem) -> Unit
) {
    val shell = viewModel.shellController
    if (!action.client) {
        moveSelectionOff(viewModel, item, wide)
        viewModel.nowController.act(item, action, isPrimary, onFailed = { if (wide) shell.selectNow(item.id) })
        return
    }
    when (action.action) {
        "open" -> openClient(item)
        // Reply happens in the reply box under the evidence; without one (gone from the shade) open the app.
        "reply" -> if (PhoneNotificationActions.replyButton(item.notification?.key) != null) expand(item) else openClient(item)
        else -> {
            // A fix the phone has no screen for (reconnect an addon, a web screen, a link): the web app does it.
            val ctx = viewModel.getApplication<android.app.Application>()
            val target = NowWebLink.target(ServerConfig.load(ctx).url, action)
            val opened = target != null && runCatching {
                ctx.startActivity(Intent(Intent.ACTION_VIEW, target.toUri()).addCategory(Intent.CATEGORY_BROWSABLE)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }.isSuccess
            if (!opened) expand(item)
        }
    }
}

private fun schedule(viewModel: MainViewModel, item: NowItem, slot: DaySlot, wide: Boolean) {
    moveSelectionOff(viewModel, item, wide)
    viewModel.nowController.schedule(item, NowModel.slotKey(slot), onFailed = { if (wide) viewModel.shellController.selectNow(item.id) })
}

/** Acting on the selected row selects the next one (unfolded). */
private fun moveSelectionOff(viewModel: MainViewModel, item: NowItem, wide: Boolean) {
    val shell = viewModel.shellController
    if (shell.now.value?.id != item.id) return
    if (!wide) {
        shell.selectNow(null)
        return
    }
    val visible = viewModel.nowController.visibleItems().map { it.id }
    shell.selectNow(NowModel.nextSelection(visible, item.id), auto = true)
}

/** "Ask Dana about this": the conversation with the item attached (approvals: their own thread). */
private fun askAbout(viewModel: MainViewModel, item: NowItem) {
    val shell = viewModel.shellController
    val ref = item.ref
    when {
        ref?.type == "thread" -> {
            viewModel.showConversation(ref.id)
            shell.openThread()
        }
        item.id.startsWith("sug:") -> shell.openThread(ThreadContext("suggestion", item.id.removePrefix("sug:"), item.title))
        ref != null && ref.type in setOf("call", "notification", "task", "review", "person", "transaction") -> shell.openThread(ThreadContext(ref.type, ref.id, item.title))
        else -> {
            // No context kind for it (a check): start the question in the ask bar.
            val text = "About “${item.title}”: "
            shell.askDraft = androidx.compose.ui.text.input.TextFieldValue(text, androidx.compose.ui.text.TextRange(text.length))
            shell.openThread()
        }
    }
}

private val HM = DateTimeFormatter.ofPattern("HH:mm")

/** The rest of today from the day strip's data (the same slots, so Now and the strip agree), as rows. */
internal fun laterToday(
    events: List<io.github.ronganam.dana.data.models.CalendarEvent>,
    tasks: List<io.github.ronganam.dana.data.models.Task>,
    now: java.time.LocalDateTime
): List<Pair<String, DayItem>> {
    val items = dayItems(events, tasks, now)
    return buildList {
        items[null].orEmpty().forEach { add((it.at?.format(HM) ?: "Today") to it) }
        items.keys.filterIsInstance<DaySlot.Hour>().sortedBy { it.hour }.forEach { slot ->
            items[slot].orEmpty().forEach { add((it.at?.format(HM) ?: "%02d:00".format(slot.hour)) to it) }
        }
        items[DaySlot.AtHome].orEmpty().forEach { add("At home" to it) }
        items[DaySlot.OnWaking].orEmpty().forEach { add("On waking" to it) }
    }
}
