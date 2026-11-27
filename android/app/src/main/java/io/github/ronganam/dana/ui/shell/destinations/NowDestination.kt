package io.github.ronganam.dana.ui.shell.destinations

import android.content.Intent
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import androidx.core.net.toUri
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import io.github.ronganam.dana.data.models.NowAction
import io.github.ronganam.dana.data.models.NowItem
import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.data.notifications.PhoneNotificationActions
import io.github.ronganam.dana.push.LiveActivities
import io.github.ronganam.dana.ui.screens.now.LiveHandlers
import io.github.ronganam.dana.ui.screens.now.NowDay
import io.github.ronganam.dana.ui.screens.now.NowHandlers
import io.github.ronganam.dana.ui.screens.now.NowListActions
import io.github.ronganam.dana.ui.screens.now.NowListPane
import io.github.ronganam.dana.ui.screens.now.NowListState
import io.github.ronganam.dana.ui.screens.now.NowLive
import io.github.ronganam.dana.ui.screens.now.NowLiveRow
import io.github.ronganam.dana.ui.screens.now.NowModel
import io.github.ronganam.dana.ui.screens.now.NowWebLink
import io.github.ronganam.dana.ui.shell.DaySlot
import io.github.ronganam.dana.ui.shell.Destination
import io.github.ronganam.dana.ui.shell.rememberNow
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.Place
import io.github.ronganam.dana.ui.viewmodels.ThreadContext

/**
 * Now: what needs a decision (`NowController`, from `GET /api/now`, with the
 * live activities waiting for an OK first), what is going on (the live
 * activities), the rest of today and what was done earlier. One list on
 * every width: rows open in place, so Now never drills down. Folded, Ask
 * Dana and the conversation sit in the header (no docked ask bar);
 * unfolded, the conversation is a pane beside it (`conversation = true`).
 *
 * The rows swipe: towards the start = dismiss, towards the end = the card's
 * own snooze (both server actions, so both answer with Undo).
 *
 * Client actions are done here: `open` (approval → its thread, notification →
 * its app, review → the review screen, else the row opens), `reply` (the
 * reply box in the open row), "Ask Dana about this" (a context chat).
 */
@Composable
fun rememberNowDestination(viewModel: MainViewModel): Destination {
    val shell = viewModel.shellController
    val nc = viewModel.nowController
    val context = LocalContext.current
    val pick by shell.now.collectAsState()
    val payload by nc.payload.collectAsState()
    val hidden by nc.hidden.collectAsState()
    val drafts by nc.drafts.collectAsState()
    val loading by nc.loading.collectAsState()
    val failed by nc.failed.collectAsState()
    val updatedAt by nc.updatedAt.collectAsState()
    val liveList by nc.live.collectAsState()
    val quietOpen by shell.quietOpen.collectAsState()
    val tasks by viewModel.tasksController.tasks.collectAsState()
    val now by rememberNow()

    val items = remember(payload, hidden) { payload?.items.orEmpty().filter { it.id !in hidden } }
    val quiet = remember(payload, hidden) { payload?.quiet.orEmpty().filter { it.id !in hidden } }
    val quietTotal = (payload?.quietTotal ?: 0) - (payload?.quiet.orEmpty().size - quiet.size)
    val live = remember(liveList) { NowLive.rows(liveList) }
    val done = remember(tasks, now.toLocalDate()) { NowDay.doneToday(tasks, now.toLocalDate()) }

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

    // An opened row that left the queue closes.
    LaunchedEffect(items, pick) {
        val cur = pick ?: return@LaunchedEffect
        if (payload != null && items.none { it.id == cur.id }) shell.selectNow(null)
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
            else -> shell.selectNow(item.id)
        }
    }

    val handlers = remember(viewModel) {
        NowHandlers(
            perform = { item, action, isPrimary -> perform(viewModel, item, action, isPrimary, ::openClient) },
            toggle = { item ->
                val cur = shell.now.value
                shell.selectNow(if (cur?.id == item.id) null else item.id)
            },
            ask = { item -> askAbout(viewModel, item) },
            reply = { item, text ->
                val err = PhoneNotificationActions.reply(context, item.notification?.key, text)
                if (err == null) nc.replied(item, item.source.split(" · ").takeIf { it.size >= 3 }?.get(1)) else nc.notice(err)
            },
            choice = { item, key, i -> nc.pickChoice(item.id, key, i) },
            check = { item, key, i -> nc.toggleCheck(item, key, i) },
            schedule = { item, slot -> schedule(viewModel, item, slot) }
        )
    }
    val liveHandlers = remember(viewModel) {
        LiveHandlers(
            act = { row, action -> nc.actLive(row.id, action) },
            open = { row -> openLive(viewModel, row) }
        )
    }

    val listState = NowListState(
        payload = payload,
        items = items,
        live = live,
        quiet = quiet,
        quietTotal = quietTotal.coerceAtLeast(quiet.size),
        drafts = drafts,
        openId = pick?.id,
        loading = loading,
        failed = failed,
        quietOpen = quietOpen,
        now = now,
        updatedAt = updatedAt,
        done = done
    )
    val listActions = remember(viewModel) {
        NowListActions(
            refresh = { nc.refresh(); viewModel.dayController.refreshIfStale(); viewModel.tasksController.fetchTasks() },
            toggleQuiet = { shell.toggleQuiet() },
            quietAct = { q -> q.primary?.let { nc.actQuiet(q, it) } },
            openDay = { e -> e.taskId?.let { shell.openReminder(it) } },
            openDone = { d -> shell.openReminder(d.taskId) }
        )
    }

    return Destination(
        place = Place.NOW,
        picked = false,
        onBack = { shell.selectNow(null) },
        list = { NowListPane(listState, handlers, liveHandlers, listActions) },
        stage = null,
        conversation = true
    )
}

/** Run [action]: server actions go to the controller, client ones are done here. */
private fun perform(viewModel: MainViewModel, item: NowItem, action: NowAction, isPrimary: Boolean, openClient: (NowItem) -> Unit) {
    val shell = viewModel.shellController
    if (!action.client) {
        if (shell.now.value?.id == item.id) shell.selectNow(null)
        viewModel.nowController.act(item, action, isPrimary)
        return
    }
    when (action.action) {
        "open" -> openClient(item)
        // Reply happens in the reply box of the open row; without one (gone from the shade) open the app.
        "reply" -> if (PhoneNotificationActions.replyButton(item.notification?.key) != null) shell.selectNow(item.id) else openClient(item)
        else -> {
            // A fix the phone has no screen for (reconnect an addon, a web screen, a link): the web app does it.
            val ctx = viewModel.getApplication<android.app.Application>()
            val target = NowWebLink.target(ServerConfig.load(ctx).url, action)
            val opened = target != null && runCatching {
                ctx.startActivity(Intent(Intent.ACTION_VIEW, target.toUri()).addCategory(Intent.CATEGORY_BROWSABLE)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }.isSuccess
            if (!opened) shell.selectNow(item.id)
        }
    }
}

private fun schedule(viewModel: MainViewModel, item: NowItem, slot: DaySlot) {
    viewModel.nowController.schedule(item, NowModel.slotKey(slot))
}

/** A live row tapped: its conversation (a watch's run), a screen of the app, or the web page it names. */
private fun openLive(viewModel: MainViewModel, row: NowLiveRow) {
    val shell = viewModel.shellController
    val ctx = viewModel.getApplication<android.app.Application>()
    when (val target = LiveActivities.openTarget(row.open, ServerConfig.load(ctx).url)) {
        is LiveActivities.Open.Session -> {
            viewModel.showConversation(target.id)
            shell.openThread()
        }
        is LiveActivities.Open.Screen -> shell.openRoute(target.route)
        is LiveActivities.Open.Web -> runCatching {
            ctx.startActivity(Intent(Intent.ACTION_VIEW, target.url.toUri()).addCategory(Intent.CATEGORY_BROWSABLE).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
        LiveActivities.Open.Home -> Unit
    }
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
