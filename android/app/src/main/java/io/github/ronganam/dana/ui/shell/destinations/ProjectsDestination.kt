package io.github.ronganam.dana.ui.shell.destinations

import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import io.github.ronganam.dana.ui.screens.projects.ProjectList
import io.github.ronganam.dana.ui.screens.projects.ProjectsOverlays
import io.github.ronganam.dana.ui.screens.projects.ProjectsStage
import io.github.ronganam.dana.ui.shell.Destination
import io.github.ronganam.dana.ui.shell.LocalShellLayout
import io.github.ronganam.dana.ui.shell.ShellLayout
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.Place

/**
 * Projects (server docs/knowledge.md; docs/projects.md). [Destination.wide]:
 * unfolded, the side pane is the project list with the open project's
 * sections under it, the stage the project or section page (the open entry
 * beside it when there is room). On the cover one level at a time: projects →
 * project → section → entry; Back pops one level. What is open lives in
 * ProjectsController. On-screen context: none (the server has no project kind).
 */
@Composable
fun rememberProjectsDestination(viewModel: MainViewModel): Destination {
    val c = viewModel.projectsController
    val stacked = LocalShellLayout.current == ShellLayout.STACKED
    val projectId by c.projectId.collectAsState()
    val auto by c.auto.collectAsState()
    val list by c.list.collectAsState()

    // Fresh on arrival and whenever the app comes back to the front (Dana may have changed things).
    val lifecycle = androidx.lifecycle.compose.LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val obs = androidx.lifecycle.LifecycleEventObserver { _, e ->
            if (e == androidx.lifecycle.Lifecycle.Event.ON_RESUME) c.refresh()
        }
        lifecycle.addObserver(obs)
        onDispose { lifecycle.removeObserver(obs) }
    }
    // Unfolded, the first project opens by itself (marked auto: the cover still shows the list).
    LaunchedEffect(list, stacked) { if (!stacked) c.autoOpen() }

    val picked = stacked && projectId != null && !auto
    return Destination(
        place = Place.MORE,
        picked = picked,
        onBack = { c.back() },
        list = {
            ProjectList(c, tree = !stacked)
            if (!picked) ProjectsOverlays(c)
        },
        stage = {
            ProjectsStage(viewModel, cover = stacked)
            if (stacked) ProjectsOverlays(c)
        },
        wide = true
    )
}
