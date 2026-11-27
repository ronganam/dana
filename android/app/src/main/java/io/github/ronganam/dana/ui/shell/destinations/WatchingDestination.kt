package io.github.ronganam.dana.ui.shell.destinations

import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import io.github.ronganam.dana.data.watches.WatchRun
import io.github.ronganam.dana.ui.screens.watching.NewWatchPage
import io.github.ronganam.dana.ui.screens.watching.WatchDetailPage
import io.github.ronganam.dana.ui.screens.watching.WatchingList
import io.github.ronganam.dana.ui.shell.Destination
import io.github.ronganam.dana.ui.shell.LocalShellLayout
import io.github.ronganam.dana.ui.shell.ShellLayout
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.Place

/**
 * Watching (server docs/watches.md; docs/phone.md, "Watching"): the
 * watches as circuits ([WatchingList]), one watch ([WatchDetailPage]) and a
 * new one ([NewWatchPage]). Folded the list drills into a watch or the new
 * page; unfolded with nothing open the list takes the whole width with
 * today's track on each line, and an open watch or the new page is the stage
 * beside the list. While a page with its own field shows, the ask bar steps
 * aside. Visible, [io.github.ronganam.dana.ui.viewmodels.WatchingController]
 * follows the engine's live stream and the runs' live activities.
 */
@Composable
fun rememberWatchingDestination(viewModel: MainViewModel): Destination {
    val c = viewModel.watchingController
    val shell = viewModel.shellController
    val selected by c.selected.collectAsState()
    val newWatch by c.newWatch.collectAsState()
    val stacked = LocalShellLayout.current == ShellLayout.STACKED

    DisposableEffect(c) {
        c.start()
        onDispose { c.stop() }
    }
    val page = selected != null || newWatch != null
    DisposableEffect(page) {
        shell.askHidden = page
        onDispose { shell.askHidden = false }
    }
    val openRun: (WatchRun) -> Unit = { run ->
        viewModel.showConversation(run.sessionId)
        shell.openThread()
    }
    val back = { if (newWatch != null) c.closeNew() else c.open(null) }

    return Destination(
        place = Place.WATCHING,
        picked = page,
        onBack = back,
        list = {
            WatchingList(
                controller = c,
                tracks = !stacked && !page,
                onOpen = { c.open(it) },
                onNew = { c.startNew() },
                onOpenRun = openRun,
            )
        },
        stage = if (!page) null else {
            {
                val id = selected
                if (newWatch != null) NewWatchPage(c, showBack = true, onBack = back)
                else if (id != null) WatchDetailPage(c, id, showBack = true, onBack = back, onOpenRun = openRun)
            }
        },
        wide = true,
    )
}
