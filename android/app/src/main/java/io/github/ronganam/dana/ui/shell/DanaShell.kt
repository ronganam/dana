package io.github.ronganam.dana.ui.shell

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.components.ServerStatusBanner
import io.github.ronganam.dana.ui.components.enterOnChange
import io.github.ronganam.dana.ui.shell.destinations.rememberMoreDestination
import io.github.ronganam.dana.ui.shell.destinations.rememberNotificationsDestination
import io.github.ronganam.dana.ui.shell.destinations.rememberNowDestination
import io.github.ronganam.dana.ui.shell.destinations.rememberRemindersDestination
import io.github.ronganam.dana.ui.shell.destinations.rememberWatchingDestination
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.Place
import io.github.ronganam.dana.ui.viewmodels.ScreenContext
import io.github.ronganam.dana.ui.viewmodels.ScreenRef
import io.github.ronganam.dana.ui.viewmodels.ShellSnapshot
import kotlinx.coroutines.launch

/**
 * The phone's main screen: its places (Now, Reminders, Watching, Inbox, More —
 * which also opens Shopping, Money and Projects), a bottom bar folded and a
 * rail (Ask Dana on top) unfolded, and one conversation with Dana — a page
 * over the place when folded, a sheet or the right pane unfolded — whose ask
 * bar comes with it. Folded, Ask Dana and the conversation button sit in each
 * place's header. Reminders alone has the day view on top (unfolded; folded it heads the list).
 *
 * Each place is a [Destination] (list + stage) built in `destinations/`; this
 * function only wires shared state (routes, shares, messages, Back) and picks
 * the arrangement for the window ([ShellLayout]). State lives in
 * [io.github.ronganam.dana.ui.viewmodels.ShellController], so fold/unfold keeps it.
 */
@Composable
fun DanaShell(viewModel: MainViewModel) {
    val shell = viewModel.shellController
    val place by shell.place.collectAsState()
    val threadOpen by shell.threadOpen.collectAsState()
    val serverStatus by viewModel.serverStatus.collectAsState()
    val overview by viewModel.notificationOverviewController.overview.collectAsState()
    val nowPayload by viewModel.nowController.payload.collectAsState()
    // The needs-you number (the Inbox's, else Now's copy of it); nothing when zero.
    val inboxBadge = overview?.counts?.needsYou ?: nowPayload?.notifications?.needsYou ?: 0
    val places = remember { Place.entries.toList() }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val dropState = remember { DayDropState() }

    // Deep links, notifications and older code still say "chat", "tasks", "calls"…
    val route by viewModel.navigateToRoute.collectAsState()
    LaunchedEffect(route) {
        route?.let {
            shell.openRoute(it)
            viewModel.clearNavigation()
        }
    }
    // More's search field searches your things (2+ characters, debounced in the controller).
    LaunchedEffect(shell.searchDraft.text) { viewModel.searchController.setQuery(shell.searchDraft.text) }
    // Text shared from another app lands in the ask bar, thread open.
    val draft by viewModel.chatController.draftText.collectAsState()
    LaunchedEffect(draft) {
        draft?.let {
            shell.askDraft = TextFieldValue(it, TextRange(it.length))
            viewModel.chatController.consumeDraft()
            shell.openThread()
        }
    }
    LaunchedEffect(viewModel) {
        launch {
            viewModel.messages.collect { message ->
                scope.launch {
                    snackbar.currentSnackbarData?.dismiss()
                    snackbar.showSnackbar(message, withDismissAction = true)
                }
            }
        }
        launch {
            viewModel.undoableMessages.collect { m ->
                scope.launch {
                    snackbar.currentSnackbarData?.dismiss()
                    val r = snackbar.showSnackbar(m.text, actionLabel = "Undo", withDismissAction = true, duration = SnackbarDuration.Long)
                    if (r == SnackbarResult.ActionPerformed) m.onUndo() else m.onTimeout()
                }
            }
        }
    }

    BoxWithConstraints(
        Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
    ) {
        val layout = shellLayoutFor(maxWidth.value)
        CompositionLocalProvider(
            LocalShellLayout provides layout,
            LocalDayDrop provides dropState,
            LocalContentColor provides MaterialTheme.colorScheme.onBackground
        ) {
            val placeDest = when (place) {
                Place.NOW -> rememberNowDestination(viewModel)
                Place.REMINDERS -> rememberRemindersDestination(viewModel)
                Place.WATCHING -> rememberWatchingDestination(viewModel)
                Place.INBOX -> rememberNotificationsDestination(viewModel)
                Place.MORE -> rememberMoreDestination(viewModel)
            }
            // Search lives in More's header field (its results replace More's rows).
            val dest = placeDest
            // What Dana sees with the next message (shown as "On screen · …").
            val live = rememberLiveScreen(viewModel)
            SideEffect {
                shell.setStackedLayout(layout == ShellLayout.STACKED)
                shell.setLive(live)
            }
            // The thread sheet has its own Back handler (it must beat the stage's); this one is for drill-downs.
            BackHandler(enabled = layout == ShellLayout.STACKED && dest.backable && !(threadOpen && layout != ShellLayout.FULL)) {
                dest.onBack()
            }
            val imeVisible = WindowInsets.ime.getBottom(LocalDensity.current) > 0
            val slots = ShellSlots(
                thread = { onClose -> ThreadPane(viewModel, onClose) },
                // A place's own field with the keyboard (Shopping's add field) takes the ask bar's spot.
                ask = { if (!(shell.askHidden && layout != ShellLayout.FULL)) AskBar(viewModel) },
                // Folded only: unfolded the rail beside the frame has the places.
                nav = { if (layout == ShellLayout.STACKED) ShellNav(place, places, inboxBadge, onPlace = { shell.go(it) }) },
                // The day view belongs to Reminders (drop a reminder on a time); the other places have their own day.
                strip = { if (dest.place == Place.REMINDERS) DayView(viewModel, onNow = { shell.showToday() }, onOpenReminder = { shell.openMoments(it) }) },
                headerAsk = { HeaderAsk(viewModel, conversation = true) },
                snackbar = snackbar,
                snackbarLift = shell.snackbarLift
            )
            Column(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing)) {
                ServerStatusBanner(
                    status = serverStatus,
                    onRetry = { viewModel.recheckServer() },
                    onOpenSettings = { viewModel.navigateTo("settings") }
                )
                // Switching place: the new one fades in and slides 12dp from the side of travel.
                val travel = remember { PlaceTravel(place) }.update(place, places)
                Row(Modifier.weight(1f).fillMaxWidth()) {
                if (layout != ShellLayout.STACKED) {
                    // Beside a conversation pane the mic listens there; where the thread is a sheet, the conversation button opens it.
                    val paneShown = dest.conversation || (layout == ShellLayout.FULL && !dest.wide)
                    ShellRail(place, places, inboxBadge, onPlace = { shell.go(it) }, header = {
                        CompositionLocalProvider(LocalShellLayout provides if (paneShown) ShellLayout.FULL else ShellLayout.SPLIT) {
                            HeaderAsk(viewModel, conversation = !paneShown, vertical = true)
                        }
                    })
                }
                Box(Modifier.weight(1f).fillMaxHeight().enterOnChange(place, travel)) {
                    when {
                        layout != ShellLayout.STACKED && dest.conversation -> ConversationFrame(dest, slots)
                        else -> when (layout) {
                        ShellLayout.STACKED -> StackedFrame(dest, slots, threadOpen, { shell.closeThread() }, showNav = !imeVisible)
                        ShellLayout.SPLIT -> SplitFrame(dest, slots, threadOpen, { shell.closeThread() })
                        // A wide place gets the whole width: drawn like portrait, the thread as a sheet.
                        ShellLayout.FULL -> if (dest.wide) {
                            CompositionLocalProvider(LocalShellLayout provides ShellLayout.SPLIT) {
                                SplitFrame(dest, slots, threadOpen, { shell.closeThread() })
                            }
                        } else FullFrame(dest, slots) { TitleCell(dest) }
                        }
                    }
                }
                }
            }
            DayDragGhost(dropState)
        }
    }
}

/** Which way the last place switch went in the nav's order (+1 towards the end, -1 back). */
private class PlaceTravel(private var last: Place) {
    private var direction = 1
    fun update(place: Place, order: List<Place>): Int {
        if (place != last) {
            direction = if (order.indexOf(place) >= order.indexOf(last)) 1 else -1
            last = place
        }
        return direction
    }
}

/** What the open place shows, as on-screen context ([ScreenContext.forShell]). */
@Composable
private fun rememberLiveScreen(viewModel: MainViewModel): ScreenRef? {
    val shell = viewModel.shellController
    val place by shell.place.collectAsState()
    val nowPick by shell.now.collectAsState()
    val reminderPick by shell.reminder.collectAsState()
    val notifPick by shell.notifApp.collectAsState()
    val more by shell.more.collectAsState()
    val nowPayload by viewModel.nowController.payload.collectAsState()
    val tasks by viewModel.tasksController.tasks.collectAsState()
    val overview by viewModel.notificationOverviewController.overview.collectAsState()
    val appItems by viewModel.notificationOverviewController.appItems.collectAsState()
    val callId by viewModel.callsController.viewingCallId.collectAsState()
    val calls by viewModel.callsController.calls.collectAsState()
    val personId by viewModel.peopleController.selectedId.collectAsState()
    val people by viewModel.peopleController.people.collectAsState()
    val review by viewModel.reviewController.review.collectAsState()
    val placeRef by shell.placeRef.collectAsState()
    return remember(place, nowPick, reminderPick, notifPick, more, nowPayload, tasks, overview, appItems, callId, calls, personId, people, review, placeRef) {
        val nowItem = nowPick?.let { p -> nowPayload?.items?.firstOrNull { it.id == p.id } }
        val reminder = reminderPick?.let { p -> tasks.firstOrNull { it.id == p.id } }
        val notifApp = notifPick?.let { p ->
            val app = overview?.let { o -> (o.apps + o.quietApps).firstOrNull { it.packageName == p.id } }
            val items = appItems[p.id].orEmpty()
            ShellSnapshot.NotifPick(
                packageName = p.id,
                appName = app?.app ?: items.firstOrNull()?.appName ?: "",
                newestId = items.maxByOrNull { it.postedAt.orEmpty() }?.id
            )
        }
        val call = callId?.let { id -> calls.firstOrNull { it.id == id } }
        val person = personId?.let { id -> people.firstOrNull { it.id == id } }
        ScreenContext.forShell(
            ShellSnapshot(
                place = place,
                nowItem = nowItem?.let { ShellSnapshot.NowPick(it.id, it.title, it.ref?.type, it.ref?.id) },
                reminder = reminder?.let { it.id to it.title },
                notifApp = notifApp,
                more = more,
                call = call?.let { it.id to (it.title ?: it.caller) },
                person = person?.let { it.id to it.name },
                reviewId = review?.id?.takeIf { it.isNotBlank() },
                placeRef = placeRef
            )
        )
    }
}

/** Top-left cell (unfolded landscape) for a place whose content spans list + stage. */
@Composable
private fun TitleCell(dest: Destination) {
    Column(Modifier.fillMaxSize().padding(horizontal = 18.dp, vertical = 12.dp), verticalArrangement = Arrangement.Bottom) {
        dest.kicker?.let { MetaLabel(it) }
        Text(dest.title, style = MaterialTheme.typography.displaySmall)
    }
}
