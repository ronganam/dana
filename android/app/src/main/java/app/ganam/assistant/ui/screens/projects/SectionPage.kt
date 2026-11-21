package app.ganam.assistant.ui.screens.projects

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.ganam.assistant.data.projects.Column as Col
import app.ganam.assistant.data.projects.Entry
import app.ganam.assistant.data.projects.ProjectItems
import app.ganam.assistant.data.projects.ProjectsCore
import app.ganam.assistant.data.projects.Section
import app.ganam.assistant.ui.components.InkButton
import app.ganam.assistant.ui.components.InkChoice
import app.ganam.assistant.ui.components.InkEmphasis
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.components.Rule
import app.ganam.assistant.ui.viewmodels.ProjectsController

/**
 * A section: its summary, then its options compared (a table: option names in a
 * sticky first column, the section's columns across, sideways scroll when they
 * don't fit) or as cards, or its notes; an add box at the end.
 */
@Composable
internal fun SectionPage(c: ProjectsController, items: ProjectItems, s: Section, cover: Boolean) {
    val lang = LocalProjectLang.current
    val viewPick by c.view.collectAsState()
    val openEntry by c.entryId.collectAsState()
    val mine = remember(items, s.id) { ProjectsCore.orderOptions(items.entries.filter { it.sectionId == s.id }) }
    val canEdit = items.project.canEdit
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val w = maxWidth
        val narrow = w < 600.dp
        val mode = when {
            !s.isOptions -> "list"
            viewPick != null -> viewPick!!
            // The cover shows cards; unfolded the table, also beside an open entry (it scrolls sideways).
            cover || mine.size < 2 -> "cards"
            else -> "compare"
        }
        val gutter = if (narrow) 18.dp else 24.dp
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 32.dp)) {
            Column(Modifier.padding(horizontal = gutter)) {
                if (cover) BackLink(items.project.title) { c.openSection(null) } else Spacer(Modifier.height(16.dp))
                Kicker(
                    (if (cover) "" else items.project.title + " · ") +
                        (if (s.isOptions) t("Decision", "החלטה") else t("Notes", "הערות")) + " · " +
                        ProjectsCore.sectionState(s, items.entries, lang).line
                )
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 2.dp)) {
                    Text(s.icon.ifBlank { if (s.isOptions) "⚖️" else "📝" }, fontSize = 26.sp, modifier = Modifier.padding(end = 10.dp))
                    Text(s.title, style = MaterialTheme.typography.displaySmall.byContent(), modifier = Modifier.weight(1f))
                }
                Row(Modifier.padding(top = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    if (s.isOptions && mine.isNotEmpty()) {
                        InkChoice(t("Compare", "השוואה"), selected = mode == "compare") { c.setView("compare") }
                        InkChoice(t("Cards", "כרטיסים"), selected = mode == "cards") { c.setView("cards") }
                    }
                    Spacer(Modifier.weight(1f))
                    if (canEdit) InkButton(t("Edit section", "עריכה")) { c.editSection() }
                }
                Summary(c, s, canEdit)
            }
            when {
                mine.isEmpty() -> Text(
                    if (s.isOptions) t("No options yet. Add the first one below, or ask Dana to collect them.", "אין עדיין אפשרויות. אפשר להוסיף כאן למטה, או לבקש מדנה לאסוף אותן.")
                    else t("Nothing here yet.", "עדיין ריק."),
                    style = serifItalic(18).byContent(), color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = gutter, vertical = 16.dp)
                )
                mode == "compare" -> {
                    CompareTable(s, mine, openEntry, gutter, w) { c.openEntry(it) }
                    if (s.columns.isEmpty() && canEdit) Row(Modifier.padding(horizontal = gutter, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(t("Add columns (price, phone…) to compare the options.", "כדי להשוות, הוסיפו עמודות (מחיר, טלפון…)."),
                            style = MaterialTheme.typography.bodySmall.byContent(), color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
                        InkButton(t("Edit section", "עריכה"), emphasis = InkEmphasis.BARE) { c.editSection() }
                    }
                }
                mode == "cards" -> OptionCards(s, mine, openEntry, gutter, w) { c.openEntry(it) }
                else -> Column(Modifier.padding(horizontal = gutter)) {
                    Rule()
                    mine.forEach { e -> ItemRow(e) { c.openEntry(e.id) } }
                }
            }
            if (canEdit) AddBox(Modifier.padding(horizontal = gutter, vertical = 16.dp),
                if (s.isOptions) t("Add an option…", "אפשרות חדשה…") else t("Add a note…", "הערה חדשה…")) { c.addEntry(it) }
        }
    }
}

@Composable
private fun Summary(c: ProjectsController, s: Section, canEdit: Boolean) {
    val draft by c.summaryDraft.collectAsState()
    var open by rememberSaveable(s.id) { mutableStateOf(false) }
    Column(Modifier.padding(top = 18.dp, bottom = 18.dp)) {
        val d = draft
        when {
            d != null -> Boxed(Modifier.fillMaxWidth()) {
                MonoLabel(t("Summary", "סיכום") + " · Markdown")
                InkField(d, { c.setSummaryDraft(it) }, Modifier.fillMaxWidth().heightIn(min = 160.dp).padding(top = 8.dp),
                    placeholder = t("TL;DR, a comparison table, the recommendation…", "תקציר, טבלת השוואה, המלצה…"), singleLine = false)
                Row(Modifier.align(Alignment.End).padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    InkButton(t("Cancel", "ביטול"), emphasis = InkEmphasis.BARE) { c.cancelSummary() }
                    InkButton(t("Save", "שמירה"), emphasis = InkEmphasis.SOLID) { c.saveSummary() }
                }
            }
            s.summary.isBlank() -> if (canEdit) Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    t("No summary yet. Ask Dana to compare and write the TL;DR, or", "אין עדיין סיכום. אפשר לבקש מדנה להשוות ולכתוב תקציר, או"),
                    style = MaterialTheme.typography.bodySmall.byContent(), color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f, fill = false)
                )
                InkButton(t("write one", "לכתוב אחד"), emphasis = InkEmphasis.BARE) { c.editSummary() }
            }
            else -> {
                val accent = MaterialTheme.colorScheme.primary
                val long = s.summary.length > 700
                Column(Modifier.fillMaxWidth().drawBehind {
                    drawLine(accent, Offset(0f, 0f), Offset(0f, size.height), 2.dp.toPx())
                }.padding(start = 16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        MonoLabel(t("Summary", "סיכום"), Modifier.weight(1f), color = accent)
                        if (canEdit) InkButton(t("Edit", "עריכה"), emphasis = InkEmphasis.BARE) { c.editSummary() }
                    }
                    Box(if (long && !open) Modifier.heightIn(max = 260.dp).clip(RoundedCornerShape(0.dp)) else Modifier) {
                        MarkdownBody(s.summary, Modifier.fillMaxWidth())
                    }
                    if (long) InkButton(if (open) t("Show less", "פחות") else t("Show all", "הצג הכול"), emphasis = InkEmphasis.BARE) { open = !open }
                }
            }
        }
    }
}

/** A hairline text field in the app's style. */
@Composable
internal fun InkField(
    value: String,
    onChange: (String) -> Unit,
    modifier: Modifier = Modifier,
    placeholder: String = "",
    singleLine: Boolean = true,
    keyboard: KeyboardOptions = KeyboardOptions.Default,
    onDone: (() -> Unit)? = null
) {
    val shape = RoundedCornerShape(8.dp)
    val style = MaterialTheme.typography.bodyLarge.copy(color = MaterialTheme.colorScheme.onSurface).byContent()
    BasicTextField(
        value, onChange,
        modifier = modifier.clip(shape).border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape)
            .background(MaterialTheme.colorScheme.background).padding(horizontal = 12.dp, vertical = 10.dp),
        textStyle = style,
        singleLine = singleLine,
        cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
        keyboardOptions = if (onDone != null) keyboard.copy(imeAction = ImeAction.Done) else keyboard,
        keyboardActions = KeyboardActions(onDone = { onDone?.invoke() }),
        decorationBox = { inner ->
            Box {
                if (value.isEmpty()) Text(placeholder, style = style, color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f))
                inner()
            }
        }
    )
}

@Composable
private fun AddBox(modifier: Modifier, placeholder: String, onAdd: (String) -> Unit) {
    var text by rememberSaveable { mutableStateOf("") }
    val add = { if (text.isNotBlank()) { onAdd(text); text = "" } }
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        InkField(text, { text = it.take(120) }, Modifier.weight(1f), placeholder = placeholder, onDone = add)
        InkButton(t("Add", "הוספה"), emphasis = if (text.isBlank()) InkEmphasis.PLAIN else InkEmphasis.SOLID,
            modifier = Modifier.padding(start = 8.dp)) { add() }
    }
}

// ── Compare table ──

private val ROW_H = 64.dp
private val HEAD_H = 44.dp

private fun colWidth(col: Col): Dp = when (col.type) {
    "money", "number" -> 120.dp
    "checkbox" -> 96.dp
    "rating" -> 112.dp
    "date" -> 112.dp
    "phone", "url", "select" -> 140.dp
    else -> 170.dp
}

/** One compared column: a section column, or the Files / Notes columns at the end. */
private sealed class Cmp(val width: Dp) {
    class Value(val col: Col) : Cmp(colWidth(col))
    object Files : Cmp(84.dp)
    object Notes : Cmp(220.dp)
}

@Composable
private fun CompareTable(s: Section, options: List<Entry>, openId: String?, gutter: Dp, width: Dp, onOpen: (String) -> Unit) {
    val lang = LocalProjectLang.current
    val best = remember(s, options) { ProjectsCore.bestIds(s, options) }
    val cols = s.columns.map { Cmp.Value(it) } + Cmp.Files + Cmp.Notes
    val nameW = if (width < 600.dp) 150.dp else 200.dp
    val natural = cols.fold(0.dp) { a, c -> a + c.width }
    val room = width - gutter * 2 - nameW
    // When everything fits, the columns share the width; otherwise they keep their size and scroll.
    val stretch = if (natural < room) room / natural else 1f
    val line = MaterialTheme.colorScheme.outlineVariant
    val accent = MaterialTheme.colorScheme.primary
    val chosenTint = decisionColor("chosen").copy(alpha = 0.08f)
    val canvas = MaterialTheme.colorScheme.background
    val ruled = Modifier.drawBehind {
        val w = 1.dp.toPx()
        drawLine(line, Offset(0f, size.height - w / 2), Offset(size.width, size.height - w / 2), w)
    }
    @Composable
    fun rowBg(e: Entry): Modifier = Modifier
        .then(if (e.decision == "chosen") Modifier.background(chosenTint) else Modifier)
        .then(if (e.id == openId) Modifier.background(accent.copy(alpha = 0.08f)) else Modifier)

    Row(Modifier.fillMaxWidth().padding(horizontal = gutter)) {
        // Sticky names
        Column(Modifier.width(nameW).background(canvas)) {
            Box(Modifier.height(HEAD_H).fillMaxWidth().then(ruled), contentAlignment = Alignment.BottomStart) {
                MonoLabel(lang.t("Option", "אפשרות"), Modifier.padding(bottom = 8.dp))
            }
            options.forEach { e ->
                Column(
                    Modifier.height(ROW_H).fillMaxWidth().then(rowBg(e)).then(ruled)
                        .drawBehind { if (e.decision == "chosen" || e.id == openId) drawLine(accent, Offset(1.5.dp.toPx(), 0f), Offset(1.5.dp.toPx(), size.height), 3.dp.toPx()) }
                        .clickable(role = Role.Button) { onOpen(e.id) }
                        .alpha(if (e.decision == "rejected") 0.45f else 1f)
                        .padding(start = 10.dp, end = 8.dp),
                    verticalArrangement = Arrangement.Center
                ) {
                    Text(e.title, style = MaterialTheme.typography.bodyLarge.copy(fontSize = 15.sp).byContent(), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    DecisionPill(e.decision, Modifier.padding(top = 3.dp))
                }
            }
        }
        Box(Modifier.weight(1f).horizontalScroll(rememberScrollState())) {
            Column {
                Row(Modifier.height(HEAD_H).then(ruled)) {
                    cols.forEach { c ->
                        Row(Modifier.width(c.width * stretch).fillMaxHeight().padding(horizontal = 10.dp, vertical = 8.dp),
                            verticalAlignment = Alignment.Bottom) {
                            val label = when (c) {
                                is Cmp.Value -> c.col.name
                                Cmp.Files -> lang.t("Files", "קבצים")
                                Cmp.Notes -> lang.t("Notes", "הערות")
                            }
                            Kicker(label + if (c is Cmp.Value && c.col.role == "price") " ₪" else "")
                        }
                    }
                }
                options.forEach { e ->
                    Row(
                        Modifier.height(ROW_H).then(rowBg(e)).then(ruled).clickable { onOpen(e.id) }
                            .alpha(if (e.decision == "rejected") 0.45f else 1f)
                    ) {
                        cols.forEach { c ->
                            Column(Modifier.width(c.width * stretch).fillMaxHeight().padding(horizontal = 10.dp), verticalArrangement = Arrangement.Center) {
                                when (c) {
                                    is Cmp.Value -> {
                                        val isBest = best[c.col.key]?.contains(e.id) == true
                                        ValueText(ProjectsCore.shown(c.col, ProjectsCore.valueOf(e, c.col), lang), best = isBest, maxLines = 2)
                                        if (isBest) Text(
                                            (if (ProjectsCore.bestWay(c.col) == "low") lang.t("Lowest", "הכי זול") else lang.t("Best", "הכי טוב")).uppercase(),
                                            style = MaterialTheme.typography.labelSmall, color = accent
                                        )
                                    }
                                    Cmp.Files -> Text(
                                        if (e.files.isEmpty()) "—" else "📎 ${e.files.size}",
                                        style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant
                                    )
                                    Cmp.Notes -> Text(
                                        ProjectsCore.preview(e.body, 110).ifEmpty { "—" },
                                        style = MaterialTheme.typography.bodySmall.byContent(), color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        maxLines = 2, overflow = TextOverflow.Ellipsis
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

// ── Option cards ──

@Composable
private fun OptionCards(s: Section, options: List<Entry>, openId: String?, gutter: Dp, width: Dp, onOpen: (String) -> Unit) {
    val lang = LocalProjectLang.current
    val best = remember(s, options) { ProjectsCore.bestIds(s, options) }
    val columns = when {
        width >= 900.dp -> 3
        width >= 560.dp -> 2
        else -> 1
    }
    Column(Modifier.padding(horizontal = gutter)) {
        options.chunked(columns).forEach { row ->
            Row(Modifier.fillMaxWidth().padding(bottom = 12.dp).height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                row.forEach { e ->
                    Boxed(
                        Modifier.weight(1f).fillMaxHeight().alpha(if (e.decision == "rejected") 0.5f else 1f),
                        accent = e.decision == "chosen" || e.id == openId,
                        onClick = { onOpen(e.id) }
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(e.title, style = MaterialTheme.typography.titleMedium.byContent(), maxLines = 2, overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.weight(1f).padding(end = 8.dp))
                            DecisionPill(e.decision)
                        }
                        // The values it has (key values first, as the columns are ordered); empty ones stay in the entry.
                        val shownCols = s.columns.filter { !ProjectsCore.isEmpty(ProjectsCore.valueOf(e, it)) }.take(5)
                        if (shownCols.isNotEmpty()) Column(Modifier.padding(top = 10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            shownCols.forEach { col ->
                                val isBest = best[col.key]?.contains(e.id) == true
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Kicker(col.name, Modifier.weight(0.42f).padding(end = 8.dp))
                                    Row(Modifier.weight(0.58f), verticalAlignment = Alignment.CenterVertically) {
                                        ValueText(ProjectsCore.shown(col, ProjectsCore.valueOf(e, col), lang), best = isBest, maxLines = 1,
                                            modifier = Modifier.weight(1f, fill = false))
                                        if (isBest) Text(
                                            " " + (if (ProjectsCore.bestWay(col) == "low") lang.t("Lowest", "הכי זול") else lang.t("Best", "הכי טוב")).uppercase(),
                                            style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary, maxLines = 1
                                        )
                                    }
                                }
                            }
                        } else if (e.body.isNotBlank()) Text(ProjectsCore.preview(e.body), style = MaterialTheme.typography.bodySmall.byContent(),
                            color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 3, modifier = Modifier.padding(top = 8.dp))
                        val meta = listOfNotNull(
                            if (e.files.isNotEmpty()) (if (lang == app.ganam.assistant.data.projects.Lang.HE) ProjectsCore.heCount(e.files.size, "קובץ אחד", "קבצים") else ProjectsCore.plural(e.files.size, "file")) else null,
                            if (e.body.isNotBlank()) lang.t("Details", "פרטים") else null
                        )
                        if (meta.isNotEmpty()) Kicker(meta.joinToString(" · "), Modifier.padding(top = 10.dp))
                    }
                }
                repeat(columns - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
    }
}
