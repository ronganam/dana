package io.github.ronganam.dana.ui.screens.projects

import android.app.DatePickerDialog
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import io.github.ronganam.dana.data.projects.Column as Col
import io.github.ronganam.dana.data.projects.ProjectsCore
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkChoice
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.theme.CategoryPalette
import io.github.ronganam.dana.ui.viewmodels.ProjectsController
import io.github.ronganam.dana.ui.viewmodels.ProjectsEditor
import java.time.LocalDate

/** The project / section editor: full screen on the cover, a 560dp sheet in the middle unfolded. */
@Composable
internal fun ProjectsEditorDialog(c: ProjectsController, draft: ProjectsEditor) {
    Dialog(onDismissRequest = { c.closeEditor() }, properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false)) {
        BoxWithConstraints(Modifier.fillMaxSize().imePadding(), contentAlignment = Alignment.Center) {
            val full = maxWidth < 600.dp
            val shape = RoundedCornerShape(if (full) 0.dp else 14.dp)
            Column(
                (if (full) Modifier.fillMaxSize() else Modifier.widthIn(max = 560.dp).fillMaxWidth().heightIn(max = maxHeight - 48.dp))
                    .clip(shape).background(MaterialTheme.colorScheme.surface)
                    .then(if (full) Modifier.systemBarsPadding() else Modifier.border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape))
            ) {
                when (draft) {
                    is ProjectsEditor.ProjectDraft -> ProjectForm(c, draft)
                    is ProjectsEditor.SectionDraft -> ProjectScope(ProjectsCore.langOf(c.project(), c.items.value?.sections.orEmpty())) { SectionForm(c, draft) }
                }
            }
        }
    }
}

@Composable
private fun FormHeader(title: String, onClose: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp, top = 12.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(title, style = MaterialTheme.typography.headlineMedium, modifier = Modifier.weight(1f))
        Text("×", fontSize = 28.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.clickable(role = Role.Button, onClick = onClose).padding(horizontal = 12.dp))
    }
}

@Composable
private fun Labeled(label: String, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Column(modifier.padding(top = 14.dp)) {
        MonoLabel(label, Modifier.padding(bottom = 6.dp))
        content()
    }
}

@Composable
private fun ColumnScope.Footer(content: @Composable RowScope.() -> Unit) {
    Rule()
    Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically, content = content)
}

@Composable
private fun ColumnScope.ProjectForm(c: ProjectsController, d: ProjectsEditor.ProjectDraft) {
    val context = LocalContext.current
    val isNew = d.id == null
    FormHeader(if (isNew) "New project" else "Edit project") { c.closeEditor() }
    Column(Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()).padding(horizontal = 20.dp).padding(bottom = 16.dp)) {
        Row {
            Labeled("Icon", Modifier.width(76.dp)) {
                InkField(d.icon, { c.updateEditor(d.copy(icon = it.take(4))) }, Modifier.fillMaxWidth())
            }
            Spacer(Modifier.width(10.dp))
            Labeled("Name", Modifier.weight(1f)) {
                InkField(d.title, { c.updateEditor(d.copy(title = it.take(80))) }, Modifier.fillMaxWidth(), placeholder = "Our wedding")
            }
        }
        Labeled("Description") {
            InkField(d.description, { c.updateEditor(d.copy(description = it.take(200))) }, Modifier.fillMaxWidth(), placeholder = "One line")
        }
        Row {
            Labeled("Date · shows a countdown", Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    InkButton(d.targetDate.ifBlank { "Pick a date" }.let { if (d.targetDate.isNotBlank()) ProjectsCore.formatDate(it) else it }) {
                        val date = runCatching { LocalDate.parse(d.targetDate) }.getOrDefault(LocalDate.now())
                        DatePickerDialog(context, { _, y, m, day -> c.updateEditor(d.copy(targetDate = "%04d-%02d-%02d".format(y, m + 1, day))) },
                            date.year, date.monthValue - 1, date.dayOfMonth).show()
                    }
                    if (d.targetDate.isNotBlank()) InkButton("Clear", emphasis = InkEmphasis.BARE) { c.updateEditor(d.copy(targetDate = "")) }
                }
            }
            Spacer(Modifier.width(10.dp))
            Labeled("Budget ₪", Modifier.weight(1f)) {
                InkField(d.budget, { c.updateEditor(d.copy(budget = it.filter { ch -> ch.isDigit() || ch == '.' || ch == ',' })) },
                    Modifier.fillMaxWidth(), placeholder = "150000", keyboard = KeyboardOptions(keyboardType = KeyboardType.Decimal))
            }
        }
        Labeled("Colour") {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                CategoryPalette.forEach { color ->
                    val hex = String.format("#%06x", color.toArgb() and 0xFFFFFF)
                    val on = d.color.equals(hex, ignoreCase = true)
                    Box(
                        Modifier.size(30.dp).clip(CircleShape).background(color)
                            .border(if (on) 3.dp else 0.dp, MaterialTheme.colorScheme.onSurface, CircleShape)
                            .clickable(role = Role.RadioButton) { c.updateEditor(d.copy(color = hex)) }
                    )
                }
            }
        }
    }
    Footer {
        if (!isNew) {
            InkButton(if (d.status == "archived") "Restore" else "Archive") { c.archiveProject() }
            InkButton("Delete") { c.askDeleteProject() }
        }
        Spacer(Modifier.weight(1f))
        InkButton("Cancel", emphasis = InkEmphasis.BARE) { c.closeEditor() }
        InkButton(if (isNew) "Create" else "Save", emphasis = InkEmphasis.SOLID, enabled = d.title.isNotBlank()) { c.saveEditor() }
    }
}

@Composable
private fun ColumnScope.SectionForm(c: ProjectsController, d: ProjectsEditor.SectionDraft) {
    val isNew = d.id == null
    val lang = LocalProjectLang.current
    fun cols(f: (MutableList<Col>) -> Unit) = c.updateEditor(d.copy(columns = d.columns.toMutableList().also(f)))
    FormHeader(if (isNew) (if (d.kind == "options") t("New decision", "החלטה חדשה") else t("New notes", "הערות חדשות")) else t("Edit section", "עריכת חלק")) { c.closeEditor() }
    Column(Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()).padding(horizontal = 20.dp).padding(bottom = 16.dp)) {
        Row {
            Labeled(t("Icon", "סמל"), Modifier.width(76.dp)) {
                InkField(d.icon, { c.updateEditor(d.copy(icon = it.take(4))) }, Modifier.fillMaxWidth(), placeholder = if (d.kind == "options") "⚖️" else "📝")
            }
            Spacer(Modifier.width(10.dp))
            Labeled(t("Name", "שם"), Modifier.weight(1f)) {
                InkField(d.title, { c.updateEditor(d.copy(title = it.take(60))) }, Modifier.fillMaxWidth(),
                    placeholder = if (d.kind == "options") t("Photographers", "צלמים") else t("Ideas", "רעיונות"))
            }
        }
        Labeled(t("Kind", "סוג")) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                InkChoice(t("Decision — compare options", "החלטה — השוואת אפשרויות"), selected = d.kind == "options") {
                    c.updateEditor(d.copy(kind = "options", columns = d.columns.ifEmpty { ProjectsCore.defaultColumns(lang) }))
                }
                InkChoice(t("Notes", "הערות"), selected = d.kind != "options") { c.updateEditor(d.copy(kind = "notes")) }
            }
        }
        if (d.kind == "options") {
            Labeled(t("Columns — what every option is compared on", "עמודות — על מה משווים כל אפשרות")) {
                Column {
                    Rule()
                    d.columns.forEachIndexed { i, col ->
                        ColumnRow(
                            col, first = i == 0,
                            onChange = { n -> cols { it[i] = n } },
                            onUp = { cols { l -> val x = l[i]; l[i] = l[i - 1]; l[i - 1] = x } },
                            onRemove = { cols { it.removeAt(i) } },
                            // One column counts toward the budget: turning it on turns it off elsewhere.
                            onPrice = { on -> cols { l -> l.indices.forEach { j ->
                                l[j] = l[j].copy(role = if (j == i) (if (on) "price" else "") else if (on) "" else l[j].role)
                            } } }
                        )
                    }
                    InkButton(t("+ Add column", "+ עמודה"), emphasis = InkEmphasis.BARE, modifier = Modifier.padding(top = 6.dp)) {
                        cols { it.add(Col(key = "", name = "", type = "text")) }
                    }
                }
            }
        }
    }
    Footer {
        if (!isNew) InkButton(t("Delete", "מחיקה")) { c.askDeleteSection() }
        Spacer(Modifier.weight(1f))
        InkButton(t("Cancel", "ביטול"), emphasis = InkEmphasis.BARE) { c.closeEditor() }
        InkButton(if (isNew) t("Create", "יצירה") else t("Save", "שמירה"), emphasis = InkEmphasis.SOLID, enabled = d.title.isNotBlank()) { c.saveEditor() }
    }
}

/** One column: name, type, its choices (select), counts toward the budget, best low / high, ↑, ×. */
@Composable
private fun ColumnRow(col: Col, first: Boolean, onChange: (Col) -> Unit, onUp: () -> Unit, onRemove: () -> Unit, onPrice: (Boolean) -> Unit) {
    var typeOpen by remember { mutableStateOf(false) }
    var optionsText by remember(col.key, col.name) { mutableStateOf(col.options.joinToString(", ")) }
    Column(Modifier.fillMaxWidth().padding(vertical = 10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            InkField(col.name, { onChange(col.copy(name = it.take(40))) }, Modifier.weight(1f), placeholder = t("Column name", "שם העמודה"))
            Box(Modifier.padding(start = 8.dp)) {
                InkButton((ProjectsCore.TYPE_LABELS[col.type] ?: col.type) + " ▾") { typeOpen = true }
                DropdownMenu(typeOpen, onDismissRequest = { typeOpen = false }) {
                    ProjectsCore.TYPES.forEach { ty ->
                        DropdownMenuItem(text = { Text((if (ty == col.type) "✓ " else "") + ProjectsCore.TYPE_LABELS[ty]) },
                            onClick = { typeOpen = false; onChange(col.copy(type = ty)) })
                    }
                }
            }
            Text("↑", fontSize = 20.sp, color = if (first) MaterialTheme.colorScheme.outlineVariant else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.clickable(enabled = !first, role = Role.Button, onClick = onUp).padding(horizontal = 10.dp, vertical = 6.dp))
            Text("×", fontSize = 22.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.clickable(role = Role.Button, onClick = onRemove).padding(horizontal = 6.dp, vertical = 6.dp))
        }
        if (col.type == "select") {
            InkField(optionsText, {
                optionsText = it
                onChange(col.copy(options = it.split(",").map { o -> o.trim() }.filter { o -> o.isNotEmpty() }))
            }, Modifier.fillMaxWidth().padding(top = 6.dp), placeholder = t("Choices, separated by commas", "אפשרויות, מופרדות בפסיקים"))
        }
        Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
            if (col.type == "money" || col.type == "number") InkChoice(t("Counts toward the budget", "נספר בתקציב"), selected = col.role == "price") { onPrice(col.role != "price") }
            if (col.type in listOf("money", "number", "rating", "date")) {
                val way = col.best
                val auto = ProjectsCore.bestWay(col.copy(best = ""))
                InkChoice(
                    t("Best: ", "הכי טוב: ") + when (way) {
                        "low" -> t("lowest", "הנמוך")
                        "high" -> t("highest", "הגבוה")
                        else -> when (auto) { "low" -> t("lowest (auto)", "הנמוך (אוטומטי)"); "high" -> t("highest (auto)", "הגבוה (אוטומטי)"); else -> "—" }
                    },
                    selected = way.isNotEmpty()
                ) { onChange(col.copy(best = when (way) { "" -> "low"; "low" -> "high"; else -> "" })) }
            }
        }
    }
    Rule()
}
