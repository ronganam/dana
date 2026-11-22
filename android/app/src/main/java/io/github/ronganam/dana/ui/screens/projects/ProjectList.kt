package io.github.ronganam.dana.ui.screens.projects

import io.github.ronganam.dana.ui.components.arrive
import io.github.ronganam.dana.data.money.MoneyFormat
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.projects.Lang
import io.github.ronganam.dana.data.projects.Project
import io.github.ronganam.dana.data.projects.ProjectsCore
import io.github.ronganam.dana.data.projects.Section
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.viewmodels.ProjectsController
import java.time.LocalDate

/**
 * The project list. [tree] (unfolded side pane): the open project is marked and
 * its overview and sections hang under it, so the side pane is also the way
 * around a project. Without it (cover screen) a tap opens the project.
 */
@Composable
internal fun ProjectList(c: ProjectsController, tree: Boolean) {
    val list by c.list.collectAsState()
    val error by c.listError.collectAsState()
    val showArchived by c.showArchived.collectAsState()
    val openId by c.projectId.collectAsState()
    val items by c.items.collectAsState()
    val sectionId by c.sectionId.collectAsState()
    val today = remember { LocalDate.now() }
    val all = list?.let(ProjectsCore::orderProjects).orEmpty()
    val archived = all.count { it.archived }
    val shown = all.filter { showArchived || !it.archived || it.id == openId }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(top = if (tree) 12.dp else 4.dp, bottom = 24.dp)) {
        Row(Modifier.padding(horizontal = 18.dp), verticalAlignment = Alignment.Bottom) {
            Column(Modifier.weight(1f)) {
                MonoLabel(
                    when {
                        list == null && error == null -> "Loading…"
                        else -> ProjectsCore.plural(all.size - archived, "project") + if (archived > 0) " · $archived archived" else ""
                    }
                )
                Text("Projects", style = if (tree) MaterialTheme.typography.displayMedium else MaterialTheme.typography.displaySmall,
                    modifier = Modifier.padding(top = 2.dp))
            }
            InkButton("+ New", emphasis = InkEmphasis.PRIMARY, modifier = Modifier.padding(bottom = 6.dp)) { c.newProject() }
        }
        Spacer(Modifier.height(10.dp))
        Rule()
        error?.let {
            Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(18.dp))
            InkButton("Try again", modifier = Modifier.padding(horizontal = 18.dp)) { c.refresh() }
        }
        if (list != null && all.isEmpty()) {
            Column(Modifier.padding(horizontal = 18.dp, vertical = 24.dp)) {
                Text("Keep a project in one place.", style = serifItalic(22))
                Text(
                    "A wedding, a trip, a flat hunt: decisions with their options side by side, notes, links and the files people send.",
                    style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 6.dp, bottom = 14.dp)
                )
                InkButton("New project", emphasis = InkEmphasis.SOLID) { c.newProject() }
            }
        }
        shown.forEachIndexed { i, p ->
            val open = tree && p.id == openId
            ProjectRow(p, today, selected = open && sectionId == null, Modifier.arrive("projects", i)) { c.openProject(p.id); c.openSection(null) }
            if (open) {
                val its = items?.takeIf { it.project.id == p.id }
                its?.sections?.forEach { s ->
                    SectionRow(s, ProjectsCore.sectionState(s, its.entries, ProjectsCore.langOf(its.project, its.sections)).line,
                        selected = s.id == sectionId) { c.openSection(s.id) }
                }
                Rule()
            }
        }
        if (archived > 0) {
            InkButton(
                (if (showArchived) "Hide" else "Show") + " archived ($archived)",
                emphasis = InkEmphasis.BARE,
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp)
            ) { c.toggleArchived() }
        }
    }
}

@Composable
private fun Modifier.marked(selected: Boolean): Modifier {
    val accent = MaterialTheme.colorScheme.primary
    val canvas = MaterialTheme.colorScheme.background
    return if (!selected) this else background(canvas).drawBehind {
        drawLine(accent, Offset(1.5.dp.toPx(), 0f), Offset(1.5.dp.toPx(), size.height), 3.dp.toPx())
    }
}

@Composable
private fun ProjectRow(p: Project, today: LocalDate, selected: Boolean, modifier: Modifier = Modifier, onClick: () -> Unit) {
    val lang = if (ProjectsCore.isRtl(p.title)) Lang.HE else Lang.EN
    val budget = ProjectsCore.budgetInfo(p)
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    Column(
        modifier.fillMaxWidth().marked(selected)
            .clickable(role = Role.Tab, onClick = onClick).semantics { this.selected = selected }
    ) {
        // A Hebrew project's row reads right to left as a whole (tile, lines, ring), like its page.
        ProjectScope(lang) { Row(Modifier.padding(horizontal = 18.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            ProjectTile(p.icon, p.color)
            Column(Modifier.weight(1f).padding(horizontal = 12.dp)) {
                Text(
                    p.title,
                    style = MaterialTheme.typography.headlineSmall.copy(fontSize = 21.sp, lineHeight = 25.sp).byContent(),
                    color = if (p.archived) muted else MaterialTheme.colorScheme.onSurface,
                    maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.fillMaxWidth()
                )
                val line = listOfNotNull(
                    ProjectsCore.projectLine(p, today).ifBlank { null },
                    if (p.archived) "archived" else null,
                    p.sharedBy?.let { "shared by $it" + if (p.access == "read") " · view only" else "" }
                ).joinToString(" · ")
                if (line.isNotEmpty()) Kicker(line, Modifier.fillMaxWidth().padding(top = 3.dp))
                if (budget != null) {
                    Text(
                        if (budget.budget != null) "${MoneyFormat.thousands(budget.committed)} ${lang.t("of", "מתוך")} ${MoneyFormat.thousands(budget.budget)}"
                        else "${MoneyFormat.thousands(budget.committed)} ${lang.t("committed", "סגור")}",
                        style = MaterialTheme.typography.bodySmall.byContent(), color = muted, modifier = Modifier.fillMaxWidth().padding(top = 2.dp)
                    )
                }
            }
            if (p.decisions > 0) DecidedRing(p.decided, p.decisions)
        } }
        Rule()
    }
}

@Composable
private fun SectionRow(s: Section, line: String, selected: Boolean, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().marked(selected).clickable(role = Role.Tab, onClick = onClick)
            .semantics { this.selected = selected }
            .padding(start = 62.dp, end = 18.dp, top = 8.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(s.icon.ifBlank { if (s.isOptions) "⚖️" else "📝" }, fontSize = 15.sp, modifier = Modifier.width(26.dp))
        Column(Modifier.weight(1f)) {
            Text(s.title, style = MaterialTheme.typography.bodyLarge.byContent(), maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.fillMaxWidth(),
                color = if (selected) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
            Text(line, style = MaterialTheme.typography.bodySmall.byContent(), color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.fillMaxWidth())
        }
    }
}
