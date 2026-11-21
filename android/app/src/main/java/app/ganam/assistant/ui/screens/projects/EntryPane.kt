package app.ganam.assistant.ui.screens.projects

import android.app.DatePickerDialog
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.ganam.assistant.data.projects.Column as Col
import app.ganam.assistant.data.projects.Entry
import app.ganam.assistant.data.projects.EntryFile
import app.ganam.assistant.data.projects.Lang
import app.ganam.assistant.data.projects.ProjectItems
import app.ganam.assistant.data.projects.ProjectsCore
import app.ganam.assistant.ui.components.InkButton
import app.ganam.assistant.ui.components.InkChoice
import app.ganam.assistant.ui.components.InkEmphasis
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.components.Rule
import app.ganam.assistant.ui.components.serverImageRequest
import app.ganam.assistant.ui.viewmodels.ProjectsController
import coil.compose.AsyncImage
import java.time.LocalDate

/**
 * An entry: its decision, call / WhatsApp / link, its values edited in place
 * by type, details (Markdown), files, the section it sits in, delete. Beside
 * the table when the stage is wide, else full screen (× / Back closes it).
 */
@Composable
internal fun EntryPane(c: ProjectsController, items: ProjectItems, e: Entry, serverUrl: String, apiToken: String) {
    val lang = LocalProjectLang.current
    val s = items.sections.firstOrNull { it.id == e.sectionId }
    val edit = items.project.canEdit && e.access != "read"
    val editing by c.editing.collectAsState()
    val phone = ProjectsCore.firstPhone(e, s)
    val link = ProjectsCore.safeUrl(e.url)
    val extras = ProjectsCore.extraFields(e, s)

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp).padding(bottom = 32.dp)) {
        // Header
        Row(Modifier.padding(top = 12.dp), verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f).padding(top = 6.dp)) {
                Kicker(listOfNotNull(s?.title ?: t("Other items", "פריטים נוספים"),
                    e.sharedBy?.let { lang.t("in $it's project", "בפרויקט של $it") }).joinToString(" · "))
                if (editing == "title") {
                    var draft by rememberSaveable(e.id) { mutableStateOf(e.title) }
                    InkField(draft, { draft = it.take(120) }, Modifier.fillMaxWidth().padding(top = 6.dp), onDone = { c.rename(e, draft) })
                    Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        InkButton(t("Cancel", "ביטול"), emphasis = InkEmphasis.BARE) { c.edit(null) }
                        InkButton(t("Save", "שמירה"), emphasis = InkEmphasis.SOLID) { c.rename(e, draft) }
                    }
                } else Text(
                    e.title.ifBlank { t("Untitled", "ללא שם") },
                    style = MaterialTheme.typography.headlineMedium.byContent(),
                    modifier = Modifier.padding(top = 2.dp).then(if (edit) Modifier.clickable(role = Role.Button) { c.edit("title") } else Modifier)
                )
            }
            Text("×", fontSize = 28.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.clickable(role = Role.Button) { c.closeEntry() }.padding(horizontal = 10.dp, vertical = 2.dp))
        }
        // Decision
        if (s?.isOptions == true) {
            @OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
            FlowRow(Modifier.padding(top = 14.dp).fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp)) {
                ProjectsCore.DECISIONS.forEach { d ->
                    InkChoice(
                        (if (d == "chosen") "✓ " else "") + ProjectsCore.decisionLabel(d, lang),
                        selected = e.decision == d,
                        solid = d == "chosen" && e.decision == "chosen"
                    ) { if (edit) c.decide(e, d) }
                }
            }
        }
        // Call / WhatsApp / link
        if (phone != null || link.isNotEmpty()) {
            @OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
            FlowRow(Modifier.padding(top = 12.dp).fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                phone?.let {
                    InkButton(t("Call ", "חיוג ") + it.value, emphasis = InkEmphasis.PRIMARY) { c.openLink("tel:${it.tel}") }
                    InkButton("WhatsApp") { c.openLink("https://wa.me/${it.wa}") }
                }
                if (link.isNotEmpty()) InkButton("↗ " + ProjectsCore.host(link)) { c.openLink(link) }
            }
        }
        // Values
        val cols = s?.columns.orEmpty()
        if (cols.isNotEmpty() || extras.isNotEmpty()) {
            Spacer(Modifier.height(16.dp))
            Rule()
            cols.forEach { col -> ValueRow(c, e, col, "col:${col.key}", editing, edit) }
            extras.forEachIndexed { i, f -> ValueRow(c, e, Col(key = "", name = f.name, type = f.type), "extra:$i", editing, edit) }
        }
        e.summary?.let {
            Block(t("From the link", "מהקישור")) { Text(it, style = MaterialTheme.typography.bodyMedium.byContent()) }
        }
        // Details
        Block(t("Details", "פרטים"), action = if (edit && editing != "body" && e.body.isNotBlank()) ({ c.edit("body") }) else null) {
            when {
                editing == "body" -> {
                    var draft by rememberSaveable(e.id) { mutableStateOf(e.body) }
                    InkField(draft, { draft = it }, Modifier.fillMaxWidth().heightIn(min = 160.dp),
                        placeholder = t("Details (Markdown)", "פרטים (Markdown)"), singleLine = false)
                    Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        InkButton(t("Cancel", "ביטול"), emphasis = InkEmphasis.BARE) { c.edit(null) }
                        InkButton(t("Save", "שמירה"), emphasis = InkEmphasis.SOLID) { c.saveBody(e, draft) }
                    }
                }
                e.body.isNotBlank() -> MarkdownBody(e.body, Modifier.fillMaxWidth())
                edit -> InkButton(t("Add details", "הוספת פרטים"), emphasis = InkEmphasis.BARE) { c.edit("body") }
                else -> Text("—", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Files(c, e, edit, serverUrl, apiToken)
        if (edit) {
            Spacer(Modifier.height(20.dp))
            Rule()
            Row(Modifier.padding(top = 12.dp).fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                MoveMenu(c, items, e, Modifier.weight(1f))
                InkButton(t("Delete", "מחיקה")) { c.askDeleteEntry(e, lang) }
            }
        }
    }
}

@Composable
private fun Block(title: String, action: (() -> Unit)? = null, content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.padding(top = 20.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(bottom = 6.dp)) {
            MonoLabel(title, Modifier.weight(1f))
            action?.let { InkButton(t("Edit", "עריכה"), emphasis = InkEmphasis.BARE, onClick = it) }
        }
        content()
    }
}

/** One column value: name, value; a tap edits it the way its type asks. */
@Composable
private fun ValueRow(c: ProjectsController, e: Entry, col: Col, key: String, editing: String?, edit: Boolean) {
    val lang = LocalProjectLang.current
    val v = ProjectsCore.field(e, col.name)?.value
    val context = LocalContext.current
    val open = editing == key
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 48.dp)
                .then(if (edit && !open) Modifier.clickable(role = Role.Button) {
                    when (col.type) {
                        "checkbox" -> c.setValue(e, col, v != true)
                        "date" -> {
                            val d = runCatching { LocalDate.parse(v?.toString()?.take(10)) }.getOrDefault(LocalDate.now())
                            DatePickerDialog(context, { _, y, m, day -> c.setValue(e, col, "%04d-%02d-%02d".format(y, m + 1, day)) },
                                d.year, d.monthValue - 1, d.dayOfMonth).show()
                        }
                        else -> c.edit(key)
                    }
                } else Modifier)
                .padding(vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Kicker(col.name, Modifier.weight(0.4f).padding(end = 10.dp))
            Box(Modifier.weight(0.6f)) {
                if (col.type == "rating" && edit) Stars(v) { n -> c.setValue(e, col, if (n == ProjectsCore.asNumber(v)?.toInt()) "" else n) }
                else ValueText(ProjectsCore.shown(col, v, lang), maxLines = 3, style = MaterialTheme.typography.bodyLarge,
                    onLink = if (col.type == "url" || col.type == "phone") ({ l -> c.openLink(l) }) else null)
            }
        }
        if (open) ValueEditor(c, e, col, v)
        Rule()
    }
}

@Composable
private fun Stars(v: Any?, onPick: (Int) -> Unit) {
    val n = ProjectsCore.asNumber(v)?.toInt() ?: 0
    Row {
        (1..5).forEach { i ->
            Text("★", fontSize = 22.sp, color = if (i <= n) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outlineVariant,
                modifier = Modifier.clickable(role = Role.Button) { onPick(i) }.padding(horizontal = 3.dp))
        }
    }
}

@Composable
private fun ValueEditor(c: ProjectsController, e: Entry, col: Col, v: Any?) {
    Column(Modifier.padding(bottom = 12.dp)) {
        if (col.type == "select" && col.options.isNotEmpty()) {
            @OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                col.options.forEach { o -> InkChoice(o, selected = v?.toString() == o) { c.setValue(e, col, o) } }
                InkChoice("—", selected = ProjectsCore.isEmpty(v)) { c.setValue(e, col, "") }
            }
            InkButton(t("Cancel", "ביטול"), emphasis = InkEmphasis.BARE) { c.edit(null) }
        } else {
            var draft by rememberSaveable(e.id, col.name) {
                mutableStateOf(if (ProjectsCore.isEmpty(v)) "" else v.let { if (it is Number) ProjectsCore.number(it.toDouble()).replace(",", "") else it.toString() })
            }
            val kb = when (col.type) {
                "money", "number", "rating" -> KeyboardOptions(keyboardType = KeyboardType.Decimal)
                "phone" -> KeyboardOptions(keyboardType = KeyboardType.Phone)
                "url" -> KeyboardOptions(keyboardType = KeyboardType.Uri)
                else -> KeyboardOptions.Default
            }
            InkField(draft, { draft = it }, Modifier.fillMaxWidth(), keyboard = kb, onDone = { c.setValue(e, col, draft) })
            Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                InkButton(t("Cancel", "ביטול"), emphasis = InkEmphasis.BARE) { c.edit(null) }
                InkButton(t("Save", "שמירה"), emphasis = InkEmphasis.SOLID) { c.setValue(e, col, draft) }
            }
        }
    }
}

@Composable
private fun Files(c: ProjectsController, e: Entry, edit: Boolean, serverUrl: String, apiToken: String) {
    val lang = LocalProjectLang.current
    val uploading by c.uploading.collectAsState()
    val opening by c.opening.collectAsState()
    var note by rememberSaveable(e.id) { mutableStateOf("") }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetMultipleContents()) { uris ->
        if (uris.isNotEmpty()) { c.upload(e.id, uris, note); note = "" }
    }
    Block(t("Files", "קבצים") + if (e.files.isNotEmpty()) " · ${e.files.size}" else "") {
        if (e.files.isEmpty() && uploading == 0) Text(t("No files.", "אין קבצים."), style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        e.files.forEach { f -> FileRow(c, f, edit, opening == f.id, lang, serverUrl, apiToken) }
        if (uploading > 0) MonoLabel(t("Uploading…", "מעלה…"), Modifier.padding(vertical = 8.dp), color = MaterialTheme.colorScheme.primary)
        if (edit) Row(Modifier.padding(top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            InkField(note, { note = it.take(500) }, Modifier.weight(1f), placeholder = t("Note for the files (optional)", "הערה לקבצים (לא חובה)"))
            InkButton(t("+ Add files", "+ הוספת קבצים"), emphasis = InkEmphasis.PRIMARY, modifier = Modifier.padding(start = 8.dp)) { picker.launch("*/*") }
        }
    }
}

@Composable
private fun FileRow(c: ProjectsController, f: EntryFile, edit: Boolean, opening: Boolean, lang: Lang, serverUrl: String, apiToken: String) {
    val kind = ProjectsCore.fileKind(f.mime)
    var transcriptOpen by rememberSaveable(f.id) { mutableStateOf(false) }
    val context = LocalContext.current
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Row(Modifier.fillMaxWidth().clickable(role = Role.Button) { c.openFile(f) }.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(
                when (kind) { "image" -> "IMG"; "audio" -> "AUD"; "video" -> "VID"; "pdf" -> "PDF"; else -> "FILE" },
                style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary, modifier = Modifier.width(42.dp)
            )
            Column(Modifier.weight(1f)) {
                Text(f.name, style = MaterialTheme.typography.bodyMedium.byContent(), maxLines = 2, overflow = TextOverflow.Ellipsis)
                Kicker(listOfNotNull(
                    ProjectsCore.fileSize(f.size),
                    if (f.source == "whatsapp") "WhatsApp" else null,
                    if (opening) lang.t("Opening…", "פותח…") else null
                ).joinToString(" · "))
            }
            if (edit) Text("×", fontSize = 22.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.clickable(role = Role.Button) { c.askDeleteFile(f, lang) }.padding(horizontal = 10.dp))
        }
        if (f.note.isNotBlank()) Text(f.note, style = MaterialTheme.typography.bodySmall.byContent(),
            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(start = 42.dp))
        if (kind == "image") AsyncImage(
            model = serverImageRequest(context, f.url, serverUrl, apiToken),
            contentDescription = f.name,
            contentScale = ContentScale.FillWidth,
            modifier = Modifier.padding(top = 6.dp).fillMaxWidth().heightIn(max = 320.dp).clip(RoundedCornerShape(8.dp))
                .clickable { c.openFile(f) }
        )
        f.transcript?.takeIf { it.isNotBlank() }?.let { tr ->
            InkButton((if (transcriptOpen) "▾ " else "▸ ") + lang.t("Transcript", "תמלול"), emphasis = InkEmphasis.BARE,
                modifier = Modifier.padding(start = 38.dp)) { transcriptOpen = !transcriptOpen }
            if (transcriptOpen) Text(tr, style = MaterialTheme.typography.bodySmall.byContent(), modifier = Modifier.padding(start = 42.dp, top = 2.dp))
        }
    }
    Rule()
}

@Composable
private fun MoveMenu(c: ProjectsController, items: ProjectItems, e: Entry, modifier: Modifier) {
    var open by remember { mutableStateOf(false) }
    val lang = LocalProjectLang.current
    val current = items.sections.firstOrNull { it.id == e.sectionId }?.title ?: lang.t("No section", "ללא חלק")
    Box(modifier) {
        InkButton(lang.t("Section: ", "חלק: ") + current + " ▾") { open = true }
        DropdownMenu(open, onDismissRequest = { open = false }) {
            (listOf(null) + items.sections).forEach { s ->
                DropdownMenuItem(
                    text = { Text((if (s?.id == e.sectionId) "✓ " else "") + (s?.title ?: lang.t("No section", "ללא חלק")),
                        style = MaterialTheme.typography.bodyMedium.byContent()) },
                    onClick = { open = false; c.move(e, s?.id) }
                )
            }
        }
    }
}
