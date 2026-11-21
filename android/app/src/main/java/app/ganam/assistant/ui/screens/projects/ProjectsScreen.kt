package app.ganam.assistant.ui.screens.projects

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.ganam.assistant.data.projects.ProjectsCore
import app.ganam.assistant.ui.adaptive.sidePaneWidth
import app.ganam.assistant.ui.components.InkButton
import app.ganam.assistant.ui.components.InkEmphasis
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.shell.edgeRule
import app.ganam.assistant.ui.viewmodels.MainViewModel
import app.ganam.assistant.ui.viewmodels.ProjectsController

/**
 * What is open in Projects: the project page or a section page, with the open
 * entry beside it when the stage is ≥ 760dp wide, else over it. [cover] = the
 * cover screen (one level at a time, a back link on top; the shell's Back pops).
 */
@Composable
fun ProjectsStage(viewModel: MainViewModel, cover: Boolean) {
    val c = viewModel.projectsController
    val projectId by c.projectId.collectAsState()
    val items by c.items.collectAsState()
    val error by c.itemsError.collectAsState()
    val sectionId by c.sectionId.collectAsState()
    val entryId by c.entryId.collectAsState()
    val list by c.list.collectAsState()
    val its = items?.takeIf { it.project.id == projectId }
    when {
        projectId == null -> NothingOpen(c, list?.isEmpty() == true)
        its == null -> Column(Modifier.padding(24.dp)) {
            if (cover) BackLink("Projects") { c.leaveProject() }
            Text(error ?: "Loading…", style = if (error != null) MaterialTheme.typography.bodyMedium else serifItalic(20),
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (error != null) InkButton("Try again", modifier = Modifier.padding(top = 10.dp)) { c.refresh() }
        }
        else -> ProjectScope(ProjectsCore.langOf(its.project, its.sections)) {
            BackHandler(enabled = !cover && entryId != null) { c.closeEntry() }
            BoxWithConstraints(Modifier.fillMaxSize()) {
                val s = its.sections.firstOrNull { it.id == sectionId }
                val e = its.entries.firstOrNull { it.id == entryId }
                val beside = !cover && maxWidth >= 760.dp
                val paneW = if (maxWidth >= 1000.dp) 400.dp else 370.dp
                val main: @Composable () -> Unit = {
                    if (s != null) SectionPage(c, its, s, cover) else ProjectPage(c, its, cover)
                }
                when {
                    e == null -> main()
                    beside -> Row(Modifier.fillMaxSize()) {
                        Box(Modifier.weight(1f).fillMaxHeight()) { main() }
                        Box(
                            Modifier.width(paneW).fillMaxHeight()
                                .background(MaterialTheme.colorScheme.surfaceContainerLow).edgeRule(start = true)
                        ) { EntryPane(c, its, e, viewModel.getServerUrl(), viewModel.getApiToken()) }
                    }
                    else -> EntryPane(c, its, e, viewModel.getServerUrl(), viewModel.getApiToken())
                }
            }
        }
    }
}

@Composable
private fun NothingOpen(c: ProjectsController, empty: Boolean) {
    Column(Modifier.fillMaxSize().padding(horizontal = 32.dp, vertical = 40.dp)) {
        MonoLabel("Projects")
        Text(
            if (empty) "Keep a project in one place." else "Pick a project.",
            style = serifItalic(28), modifier = Modifier.padding(top = 6.dp)
        )
        Text(
            "A wedding, a trip, a flat hunt: decisions with their options side by side, notes, links and the files people send.",
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.widthIn(max = 460.dp).padding(top = 8.dp, bottom = 16.dp)
        )
        InkButton("New project", emphasis = if (empty) InkEmphasis.SOLID else InkEmphasis.PRIMARY) { c.newProject() }
    }
}

/** The previous layout's Projects tab: the same panes, arranged by the screen's own width. */
@Composable
fun ProjectsScreen(viewModel: MainViewModel) {
    val c = viewModel.projectsController
    val projectId by c.projectId.collectAsState()
    val auto by c.auto.collectAsState()
    val list by c.list.collectAsState()
    LaunchedEffect(c) { c.refresh() }
    BoxWithConstraints(Modifier.fillMaxSize().statusBarsPadding()) {
        val cover = maxWidth < 600.dp
        LaunchedEffect(list, cover) { if (!cover) c.autoOpen() }
        if (cover) {
            val picked = projectId != null && !auto
            BackHandler(enabled = picked) { c.back() }
            if (picked) ProjectsStage(viewModel, cover = true) else ProjectList(c, tree = false)
        } else Row(Modifier.fillMaxSize()) {
            Box(
                Modifier.width(sidePaneWidth()).fillMaxHeight()
                    .background(MaterialTheme.colorScheme.surfaceContainerLow).edgeRule(end = true)
            ) { ProjectList(c, tree = true) }
            Box(Modifier.weight(1f).fillMaxHeight()) { ProjectsStage(viewModel, cover = false) }
        }
        ProjectsOverlays(c)
    }
}
