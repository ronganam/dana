package io.github.ronganam.dana.ui.shell.destinations

import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import io.github.ronganam.dana.data.models.OverviewApp
import io.github.ronganam.dana.data.notifications.NotificationSettings
import io.github.ronganam.dana.data.notifications.PhoneNotificationActions
import io.github.ronganam.dana.ui.screens.notifications.AppDetail
import io.github.ronganam.dana.ui.screens.notifications.NoAppPicked
import io.github.ronganam.dana.ui.screens.notifications.NotificationsActions
import io.github.ronganam.dana.ui.screens.notifications.NotificationsListPane
import io.github.ronganam.dana.ui.shell.Destination
import io.github.ronganam.dana.ui.shell.HeaderAsk
import io.github.ronganam.dana.ui.shell.LocalShellLayout
import io.github.ronganam.dana.ui.shell.ShellLayout
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.OverviewMath
import io.github.ronganam.dana.ui.viewmodels.Place

/**
 * The Inbox: notifications per app (`NotificationOverviewController`, from
 * `GET /api/notifications/overview`): the apps as the list, the open app on
 * the stage (unfolded: the first app is picked for you; on the cover a tap
 * drills down with "← Inbox"). The old Inbox stays behind Settings'
 * "Previous layout".
 */
@Composable
fun rememberNotificationsDestination(viewModel: MainViewModel): Destination {
    val shell = viewModel.shellController
    val oc = viewModel.notificationOverviewController
    val context = LocalContext.current
    val overview by oc.overview.collectAsState()
    val loading by oc.loading.collectAsState()
    val failed by oc.failed.collectAsState()
    val access by oc.accessGranted.collectAsState()
    val appItems by oc.appItems.collectAsState()
    val pick by shell.notifApp.collectAsState()
    val quietOpen by shell.quietAppsOpen.collectAsState()
    val wide = LocalShellLayout.current != ShellLayout.STACKED

    // Fresh while on screen: sync + fetch on resume and every 20 s.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val obs = LifecycleEventObserver { _, e ->
            when (e) {
                Lifecycle.Event.ON_RESUME -> oc.startPolling()
                Lifecycle.Event.ON_PAUSE -> oc.stopPolling()
                else -> Unit
            }
        }
        lifecycle.addObserver(obs)
        if (lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) oc.startPolling()
        onDispose { lifecycle.removeObserver(obs); oc.stopPolling() }
    }

    // The open app as last seen: after "Clear all" (or a rule that hides it) the page stays, empty.
    var lastSeen by remember { mutableStateOf<OverviewApp?>(null) }
    val live = overview?.let { o -> (o.apps + o.quietApps).firstOrNull { it.packageName == pick?.id } }
    if (live != null && live != lastSeen) lastSeen = live
    val shown = live ?: lastSeen?.takeIf { it.packageName == pick?.id }?.copy(count = 0, needsYou = 0, noise = 0)

    // Unfolded: keep an app on the stage (the first one) unless the owner picked one.
    LaunchedEffect(wide, overview, pick) {
        val o = overview ?: return@LaunchedEffect
        val cur = pick
        val first = o.apps.firstOrNull()?.packageName
        if (wide && (cur == null || (cur.auto && (o.apps + o.quietApps).none { it.packageName == cur.id }))) {
            if (first != null && first != cur?.id) shell.selectNotifApp(first, auto = true)
        }
    }
    // Its waiting notifications: on opening, and whenever its count changes.
    LaunchedEffect(pick?.id, live?.count, live?.needsYou) {
        pick?.id?.let { oc.fetchApp(it) }
    }

    val actions = remember(viewModel) {
        NotificationsActions(
            refresh = { oc.syncAndFetch(); shell.notifApp.value?.id?.let(oc::fetchApp) },
            openApp = { a -> shell.selectNotifApp(a.packageName) },
            clearNoise = { oc.clearNoise() },
            clearApp = { a -> oc.clearApp(a) },
            answer = { s, yes -> oc.answer(s, yes) },
            toggleQuietApps = { shell.toggleQuietApps() },
            setRule = { a, r ->
                oc.setRule(a, r)
                // Keep the page right even when the new rule drops the app from the overview.
                lastSeen = lastSeen?.takeIf { it.packageName == a.packageName }?.let { OverviewMath.ruled(it, r) }
            },
            clearOne = { n -> oc.clearOne(n) },
            snooze = { n -> oc.snoozeOne(n) },
            open = { n ->
                if (!PhoneNotificationActions.open(context, n.key, n.packageName)) oc.notice("Couldn't open ${n.appName}")
            },
            reply = { n, text ->
                val err = PhoneNotificationActions.reply(context, n.key, text)
                if (err == null) oc.replied(n) else oc.notice(err)
            },
            press = { n, title ->
                val err = PhoneNotificationActions.press(context, n.key, title)
                if (err == null) oc.syncAndFetch() else oc.notice(err)
            },
            allowAccess = { NotificationSettings.openAccessSettings(context) }
        )
    }

    val userPicked = pick != null && pick?.auto == false
    return Destination(
        place = Place.INBOX,
        picked = userPicked && shown != null,
        onBack = { shell.selectNotifApp(null) },
        list = {
            NotificationsListPane(
                overview, pick?.id, wide, loading, failed, access, quietOpen, actions,
                headerAsk = if (!wide) ({ HeaderAsk(viewModel, conversation = true) }) else null
            )
        },
        askSlot = false,
        stage = {
            val app = shown
            if (app != null) AppDetail(app, appItems[app.packageName], onBack = if (wide) null else ({ shell.selectNotifApp(null) }), actions)
            else NoAppPicked(allClear = overview?.apps?.isEmpty() == true)
        }
    )
}
