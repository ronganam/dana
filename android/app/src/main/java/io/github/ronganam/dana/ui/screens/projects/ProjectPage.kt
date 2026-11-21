package io.github.ronganam.dana.ui.screens.projects

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.projects.BudgetInfo
import io.github.ronganam.dana.data.projects.Entry
import io.github.ronganam.dana.data.projects.ProjectItems
import io.github.ronganam.dana.data.projects.ProjectsCore
import io.github.ronganam.dana.data.projects.Section
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.viewmodels.ProjectsController
import java.time.LocalDate

/**
 * A project: its hero (date, decisions made), the budget, the decision and
 * notes section cards, other items and tasks. From ~720dp two columns: the
 * sections on the left, budget / other items / tasks on the right.
 */
@Composable
internal fun ProjectPage(c: ProjectsController, items: ProjectItems, cover: Boolean) {
    val p = items.project
    val lang = LocalProjectLang.current
    val decisions = items.sections.filter { it.isOptions }
    val notes = items.sections.filter { !it.isOptions }
    val loose = items.entries.filter { it.sectionId == null }
    val today = remember { LocalDate.now() }
    val cd = ProjectsCore.countdown(p.targetDate, today, lang)
    val budget = ProjectsCore.budgetInfo(p, items.sections, items.entries)
    val decided = decisions.count { ProjectsCore.sectionState(it, items.entries, lang).decided }
    val decidedText = lang.t("$decided of ${decisions.size} decided", "$decided מתוך ${decisions.size} הוחלטו")

    BoxWithConstraints(Modifier.fillMaxSize()) {
        val w = maxWidth
        val two = w >= 720.dp
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState())
                .padding(horizontal = if (maxWidth >= 600.dp) 28.dp else 18.dp).padding(bottom = 32.dp)
        ) {
            if (cover) BackLink("Projects") { c.leaveProject() } else Spacer(Modifier.height(18.dp))
            // Hero
            Row(verticalAlignment = Alignment.Top) {
                Text(p.icon.ifBlank { "📁" }, fontSize = 38.sp, modifier = Modifier.padding(end = 14.dp, top = 4.dp))
                Column(Modifier.weight(1f)) {
                    Kicker(listOfNotNull(
                        cd?.let { "${it.date} · ${it.label}" },
                        if (decisions.isNotEmpty()) decidedText else null,
                        if (p.archived) t("Archived", "בארכיון") else null
                    ).joinToString(" · ").ifEmpty { t("Project", "פרויקט") },
                        color = if (cd != null && cd.days in 0..30) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(p.title, style = MaterialTheme.typography.displaySmall.byContent(), modifier = Modifier.padding(top = 2.dp))
                    if (p.description.isNotBlank()) Text(p.description, style = serifItalic(18).byContent(),
                        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 2.dp))
                    p.sharedBy?.let {
                        Kicker(t("Shared by", "שותף על ידי") + " $it" + if (p.access == "read") t(" · view only", " · צפייה בלבד") else "",
                            Modifier.padding(top = 6.dp))
                    }
                }
            }
            if (p.canEdit) Row(Modifier.padding(top = 14.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (p.isOwner) InkButton(t("Edit project", "עריכת הפרויקט")) { c.editProject() }
                InkButton(t("+ Decision", "+ החלטה")) { c.newSection("options") }
                InkButton(t("+ Notes", "+ הערות")) { c.newSection("notes") }
            }
            Spacer(Modifier.height(20.dp))
            if (two) Row(horizontalArrangement = Arrangement.spacedBy(28.dp)) {
                Column(Modifier.weight(1f)) { SectionsBlock(c, items, decisions, notes, decidedText, columns = if (w >= 1000.dp) 3 else 2) }
                Column(Modifier.width(300.dp)) { AsideBlock(c, items, budget, loose) }
            } else {
                budget?.let { BudgetCard(it); Spacer(Modifier.height(20.dp)) }
                SectionsBlock(c, items, decisions, notes, decidedText, columns = if (w >= 500.dp) 2 else 1)
                AsideBlock(c, items, null, loose)
            }
        }
    }
}

@Composable
private fun Heading(title: String, meta: String?) {
    Row(Modifier.fillMaxWidth().padding(top = 8.dp, bottom = 10.dp), verticalAlignment = Alignment.Bottom) {
        Text(title, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f))
        meta?.let { Kicker(it) }
    }
}

@Composable
private fun SectionsBlock(c: ProjectsController, items: ProjectItems, decisions: List<Section>, notes: List<Section>, decidedText: String, columns: Int) {
    val canEdit = items.project.canEdit
    Heading(t("Decisions", "החלטות"), if (decisions.isNotEmpty()) decidedText else null)
    if (decisions.isEmpty()) Text(
        if (canEdit) t("No decisions yet. Add one, or ask Dana to collect the options.", "אין עדיין החלטות. אפשר להוסיף, או לבקש מדנה לאסוף אפשרויות.")
        else t("No decisions.", "אין החלטות."),
        style = MaterialTheme.typography.bodyMedium.byContent(), color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(bottom = 12.dp)
    )
    CardGrid(decisions, columns) { SectionCard(c, it, items) }
    if (notes.isNotEmpty()) {
        Spacer(Modifier.height(12.dp))
        Heading(t("Notes", "הערות"), null)
        CardGrid(notes, columns) { SectionCard(c, it, items) }
    }
    Spacer(Modifier.height(12.dp))
}

@Composable
private fun <T> CardGrid(list: List<T>, columns: Int, card: @Composable (T) -> Unit) {
    list.chunked(columns).forEach { row ->
        Row(Modifier.fillMaxWidth().padding(bottom = 12.dp).height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            row.forEach { Box(Modifier.weight(1f).fillMaxHeight()) { card(it) } }
            repeat(columns - row.size) { Spacer(Modifier.weight(1f)) }
        }
    }
}

@Composable
private fun SectionCard(c: ProjectsController, s: Section, items: ProjectItems) {
    val lang = LocalProjectLang.current
    val st = ProjectsCore.sectionState(s, items.entries, lang)
    val mine = ProjectsCore.orderOptions(items.entries.filter { it.sectionId == s.id })
    val files = mine.sumOf { it.files.size }
    val price = ProjectsCore.priceRange(st)
    Boxed(Modifier.fillMaxSize(), accent = st.decided, onClick = { c.openSection(s.id) }) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(s.icon.ifBlank { if (s.isOptions) "⚖️" else "📝" }, fontSize = 18.sp, modifier = Modifier.padding(end = 8.dp))
            Text(s.title, style = MaterialTheme.typography.titleMedium.byContent(), maxLines = 2, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f))
            if (st.decided) Text("✓", color = decisionColor("chosen"), style = MaterialTheme.typography.titleMedium)
        }
        Text(st.line, style = MaterialTheme.typography.bodyMedium.byContent(), color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 4.dp))
        if (s.isOptions && mine.isNotEmpty()) {
            Column(Modifier.padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                mine.take(4).forEach { e ->
                    Text(
                        (if (e.decision == "chosen") "✓ " else "· ") + e.title,
                        style = MaterialTheme.typography.bodySmall.byContent(),
                        color = decisionColor(e.decision).copy(alpha = if (e.decision == "rejected") 0.5f else 1f),
                        maxLines = 1, overflow = TextOverflow.Ellipsis
                    )
                }
                if (mine.size > 4) Text("+${mine.size - 4}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        } else if (mine.isNotEmpty()) {
            Text(mine.take(3).joinToString(" · ") { it.title }, style = MaterialTheme.typography.bodySmall.byContent(),
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 8.dp))
        }
        Spacer(Modifier.weight(1f))
        val meta = listOfNotNull(
            price.ifEmpty { null },
            if (files > 0) (if (lang == io.github.ronganam.dana.data.projects.Lang.HE) ProjectsCore.heCount(files, "קובץ אחד", "קבצים") else ProjectsCore.plural(files, "file")) else null,
            if (s.summary.isNotBlank()) t("Summary", "סיכום") else null
        )
        if (meta.isNotEmpty()) Kicker(meta.joinToString(" · "), Modifier.padding(top = 10.dp))
    }
}

@Composable
private fun AsideBlock(c: ProjectsController, items: ProjectItems, budget: BudgetInfo?, loose: List<Entry>) {
    budget?.let { BudgetCard(it); Spacer(Modifier.height(20.dp)) }
    if (loose.isNotEmpty()) {
        Heading(t("Other items", "פריטים נוספים"), loose.size.toString())
        Rule()
        loose.forEach { ItemRow(it) { c.openEntry(it.id) } }
        Spacer(Modifier.height(16.dp))
    }
    if (items.tasks.isNotEmpty()) {
        Heading(t("Tasks", "משימות"), items.tasks.size.toString())
        Rule()
        items.tasks.forEach { task ->
            Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(if (task.done) "✓" else "○", color = if (task.done) decisionColor("chosen") else MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.width(24.dp))
                Text(task.title, style = MaterialTheme.typography.bodyMedium.byContent(),
                    color = if (task.done) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface)
            }
            Rule()
        }
    }
}

@Composable
internal fun BudgetCard(b: BudgetInfo) {
    Boxed(Modifier.fillMaxWidth()) {
        MonoLabel(t("Committed", "סגור"))
        Row(verticalAlignment = Alignment.Bottom, modifier = Modifier.padding(top = 2.dp)) {
            Text(ProjectsCore.money(b.committed), style = MaterialTheme.typography.headlineMedium.copy(textDirection = androidx.compose.ui.text.style.TextDirection.Ltr))
            b.budget?.let {
                Text(" " + t("of", "מתוך") + " " + ProjectsCore.money(it), style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(start = 6.dp, bottom = 5.dp))
            }
        }
        if (b.budget != null && b.pct != null && b.left != null) {
            Bar(b.pct, over = b.left < 0, modifier = Modifier.padding(vertical = 8.dp))
            Text(
                if (b.left >= 0) t("Left:", "נשאר:") + " " + ProjectsCore.money(b.left) else t("Over budget:", "חריגה:") + " " + ProjectsCore.money(-b.left),
                style = MaterialTheme.typography.bodySmall,
                color = if (b.left < 0) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}

/** A loose item or a note: icon glyph, title, link host or a preview, files count. */
@Composable
internal fun ItemRow(e: Entry, onClick: () -> Unit) {
    val sub = e.url?.let { ProjectsCore.host(ProjectsCore.safeUrl(it).ifEmpty { it }) } ?: ProjectsCore.preview(e.summary ?: e.body, 90)
    Row(
        Modifier.fillMaxWidth().clickable(role = Role.Button, onClick = onClick).padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(if (e.url != null) "↗" else if (e.kind == "record") "▤" else "¶", color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(24.dp))
        Column(Modifier.weight(1f)) {
            Text(e.title.ifBlank { t("Untitled", "ללא שם") }, style = MaterialTheme.typography.bodyLarge.byContent(), maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (sub.isNotBlank()) Text(sub, style = MaterialTheme.typography.bodySmall.byContent(), color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (e.files.isNotEmpty()) MonoLabel("📎 ${e.files.size}")
    }
    Rule()
}
