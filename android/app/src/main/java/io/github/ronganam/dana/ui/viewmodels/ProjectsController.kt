package io.github.ronganam.dana.ui.viewmodels

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Log
import androidx.core.content.FileProvider
import io.github.ronganam.dana.data.projects.Column
import io.github.ronganam.dana.data.projects.Entry
import io.github.ronganam.dana.data.projects.EntryFile
import io.github.ronganam.dana.data.projects.Project
import io.github.ronganam.dana.data.projects.ProjectItems
import io.github.ronganam.dana.data.projects.ProjectsApi
import io.github.ronganam.dana.data.projects.ProjectsApiError
import io.github.ronganam.dana.data.projects.ProjectsCore
import io.github.ronganam.dana.data.projects.ProjectsJson
import io.github.ronganam.dana.data.projects.Section
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

/** A project or section editor's draft (lives here so a fold / unfold keeps it). */
sealed class ProjectsEditor {
    data class ProjectDraft(
        val id: String? = null,
        val title: String = "",
        val description: String = "",
        val icon: String = "📁",
        val color: String = "",
        val targetDate: String = "",
        val budget: String = "",
        val status: String = "active"
    ) : ProjectsEditor()

    data class SectionDraft(
        val id: String? = null,
        val title: String = "",
        val icon: String = "",
        val kind: String = "options",
        val columns: List<Column> = emptyList(),
        /** How many items the section holds (the delete question says they stay). */
        val entries: Int = 0
    ) : ProjectsEditor()
}

/** "Delete …?" waiting for a yes. */
class ProjectsConfirm(val title: String, val message: String, val action: String, val cancel: String = "Cancel", val onYes: () -> Unit)

/**
 * The Projects place (server docs/knowledge.md; Android docs/projects.md):
 * the list, the open project / section / entry, the editors and every write.
 * What is open lives here, not in the composables, so folding keeps it.
 */
class ProjectsController(private val ctx: ControllerContext, private val appContext: Context) {

    private val api = ProjectsApi(appContext) { ctx.api }

    private val _list = MutableStateFlow<List<Project>?>(null)
    /** GET /api/projects (null until it first loads). */
    val list: StateFlow<List<Project>?> = _list.asStateFlow()

    private val _listError = MutableStateFlow<String?>(null)
    val listError: StateFlow<String?> = _listError.asStateFlow()

    private val _showArchived = MutableStateFlow(false)
    val showArchived: StateFlow<Boolean> = _showArchived.asStateFlow()

    private val _projectId = MutableStateFlow<String?>(null)
    val projectId: StateFlow<String?> = _projectId.asStateFlow()

    private val _auto = MutableStateFlow(false)
    /** The open project was picked by the unfolded screen itself (the cover then still shows the list). */
    val auto: StateFlow<Boolean> = _auto.asStateFlow()

    private val _items = MutableStateFlow<ProjectItems?>(null)
    /** GET /api/projects/{id}/items of the open project. */
    val items: StateFlow<ProjectItems?> = _items.asStateFlow()

    private val _itemsError = MutableStateFlow<String?>(null)
    val itemsError: StateFlow<String?> = _itemsError.asStateFlow()

    private val _sectionId = MutableStateFlow<String?>(null)
    val sectionId: StateFlow<String?> = _sectionId.asStateFlow()

    private val _entryId = MutableStateFlow<String?>(null)
    val entryId: StateFlow<String?> = _entryId.asStateFlow()

    private val _view = MutableStateFlow<String?>(null)
    /** "compare" / "cards" chosen by the user; null = by width. */
    val view: StateFlow<String?> = _view.asStateFlow()

    private val _editing = MutableStateFlow<String?>(null)
    /** What of the open entry is being edited: "title", "body", "col:<key>", "extra:<i>". */
    val editing: StateFlow<String?> = _editing.asStateFlow()

    private val _summaryDraft = MutableStateFlow<String?>(null)
    /** The open section's summary while it is being edited. */
    val summaryDraft: StateFlow<String?> = _summaryDraft.asStateFlow()

    private val _editor = MutableStateFlow<ProjectsEditor?>(null)
    val editor: StateFlow<ProjectsEditor?> = _editor.asStateFlow()

    private val _confirm = MutableStateFlow<ProjectsConfirm?>(null)
    val confirm: StateFlow<ProjectsConfirm?> = _confirm.asStateFlow()

    private val _uploading = MutableStateFlow(0)
    val uploading: StateFlow<Int> = _uploading.asStateFlow()

    private val _opening = MutableStateFlow<String?>(null)
    /** The file being downloaded to open. */
    val opening: StateFlow<String?> = _opening.asStateFlow()

    private var pendingEntry: String? = null
    private var itemsJob: Job? = null

    // ── Derived ──

    fun project(): Project? = _items.value?.project?.takeIf { it.id == _projectId.value }
        ?: _list.value?.firstOrNull { it.id == _projectId.value }
    fun section(id: String? = _sectionId.value): Section? = _items.value?.sections?.firstOrNull { it.id == id }
    fun entry(id: String? = _entryId.value): Entry? = _items.value?.entries?.firstOrNull { it.id == id }

    // ── Loading ──

    fun refresh() {
        ctx.scope.launch {
            loadList()
            _projectId.value?.let { loadItems(it) }
        }
    }

    private suspend fun loadList() {
        try {
            _list.value = api.projects()
            _listError.value = null
            val open = _projectId.value
            if (open != null && _list.value?.none { it.id == open } == true) closeProject()
        } catch (e: CancellationException) {
            throw e
        } catch (e: ProjectsApiError) {
            _listError.value = e.message
        } catch (e: Exception) {
            Log.w(TAG, "projects", e)
            if (_list.value == null) _listError.value = "Can't reach Dana: ${describeError(e)}"
        }
    }

    private suspend fun loadItems(id: String) {
        try {
            val items = api.items(id)
            if (_projectId.value != id) return
            _items.value = items
            _itemsError.value = null
            if (_sectionId.value != null && section() == null) _sectionId.value = null
            if (_entryId.value != null && entry() == null) _entryId.value = null
            pendingEntry?.let { want ->
                items.entries.firstOrNull { it.id == want }?.let { e ->
                    _sectionId.value = e.sectionId
                    _entryId.value = e.id
                }
                pendingEntry = null
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: ProjectsApiError) {
            if (e.code == 404) closeProject() else _itemsError.value = e.message
        } catch (e: Exception) {
            Log.w(TAG, "items", e)
            if (_items.value?.project?.id != id) _itemsError.value = "Can't reach Dana: ${describeError(e)}"
        }
    }

    private fun reloadItems() {
        val id = _projectId.value ?: return
        itemsJob?.cancel()
        itemsJob = ctx.scope.launch { loadItems(id) }
    }

    private fun reloadListQuietly() { ctx.scope.launch { loadList() } }

    // ── Navigation ──

    fun openProject(id: String, auto: Boolean = false) {
        _auto.value = auto
        if (_projectId.value == id) {
            reloadItems()
            return
        }
        _projectId.value = id
        _sectionId.value = null
        _entryId.value = null
        _editing.value = null
        _summaryDraft.value = null
        _items.value = null
        _itemsError.value = null
        reloadItems()
    }

    /** Unfolded with nothing open: open the first active project (the list stays beside it). */
    fun autoOpen() {
        if (_projectId.value != null) return
        val first = _list.value?.let(ProjectsCore::orderProjects)?.firstOrNull { !it.archived } ?: return
        openProject(first.id, auto = true)
    }

    fun closeProject() {
        _projectId.value = null
        _auto.value = false
        _items.value = null
        _sectionId.value = null
        _entryId.value = null
        _editing.value = null
        _summaryDraft.value = null
    }

    fun openSection(id: String?) {
        _auto.value = false
        val changed = _sectionId.value != id
        _sectionId.value = id
        _entryId.value = null
        _editing.value = null
        _summaryDraft.value = null
        if (changed) reloadItems()
    }

    fun openEntry(id: String) {
        _auto.value = false
        _entryId.value = id
        _editing.value = null
    }

    fun closeEntry() {
        _entryId.value = null
        _editing.value = null
    }

    /** One level up: entry → section → project → list. */
    fun back() {
        when {
            _editing.value != null -> _editing.value = null
            _entryId.value != null -> closeEntry()
            _sectionId.value != null -> openSection(null)
            _projectId.value != null -> leaveProject()
        }
    }

    /** Back to the list (its stats may have changed while the project was open). */
    fun leaveProject() {
        closeProject()
        reloadListQuietly()
    }

    fun toggleArchived() { _showArchived.value = !_showArchived.value }
    fun setView(mode: String) { _view.value = mode }

    /** Open project [projectId] (or the one titled [title]) and, with [entryId], that entry (search results). */
    fun focus(projectId: String?, title: String?, entryId: String?) {
        ctx.scope.launch {
            if (_list.value == null) loadList()
            val p = _list.value?.firstOrNull { it.id == projectId }
                ?: title?.let { t -> _list.value?.firstOrNull { it.title.equals(t, ignoreCase = true) } }
                ?: return@launch
            if (entryId != null && _projectId.value == p.id && entry(entryId) != null) {
                _sectionId.value = entry(entryId)?.sectionId
                _entryId.value = entryId
                return@launch
            }
            pendingEntry = entryId
            if (_projectId.value == p.id) { _auto.value = false; reloadItems() } else openProject(p.id)
        }
    }

    // ── Writes ──

    private fun act(failure: String, block: suspend () -> Unit): Job = ctx.scope.launch {
        try {
            block()
        } catch (e: CancellationException) {
            throw e
        } catch (e: ProjectsApiError) {
            ctx.messages.post(e.message)
        } catch (e: Exception) {
            Log.w(TAG, failure, e)
            ctx.messages.post("$failure: ${describeError(e)}")
        }
    }

    private fun replaceEntry(updated: Entry) {
        val items = _items.value ?: return
        _items.value = items.copy(entries = items.entries.map { if (it.id == updated.id) updated else it })
    }

    /** PUT the entry with [changes]; the answer replaces it locally. */
    fun putEntry(entryId: String, changes: JSONObject, done: String? = null, listChanged: Boolean = false) =
        act("Couldn't save") {
            api.updateEntry(entryId, changes)?.let(::replaceEntry) ?: reloadItems()
            done?.let { ctx.messages.post(it) }
            if (listChanged) reloadListQuietly()
        }

    fun decide(entry: Entry, decision: String) {
        if (entry.decision == decision) return
        // Shown at once; the server's answer confirms it.
        replaceEntry(entry.copy(decision = decision))
        putEntry(entry.id, JSONObject().put("decision", decision),
            done = if (decision == "chosen") "Chosen: ${entry.title}" else null, listChanged = true)
    }

    fun setValue(entry: Entry, col: Column, raw: Any?) {
        _editing.value = null
        val fields = ProjectsCore.setField(entry.fields, col, raw)
        replaceEntry(entry.copy(fields = fields))
        putEntry(entry.id, JSONObject().put("fields", ProjectsJson.fieldsJson(fields)), listChanged = true)
    }

    fun rename(entry: Entry, title: String) {
        _editing.value = null
        val t = title.trim()
        if (t.isEmpty() || t == entry.title) return
        replaceEntry(entry.copy(title = t))
        putEntry(entry.id, JSONObject().put("title", t))
    }

    fun saveBody(entry: Entry, body: String) {
        _editing.value = null
        replaceEntry(entry.copy(body = body))
        putEntry(entry.id, JSONObject().put("body", body))
    }

    fun move(entry: Entry, sectionId: String?) {
        if (entry.sectionId == sectionId) return
        putEntry(entry.id, JSONObject().put("section_id", sectionId ?: ""), done = "Moved", listChanged = true)
        if (_sectionId.value != null) _sectionId.value = sectionId
    }

    fun edit(key: String?) { _editing.value = key }

    fun addEntry(title: String) {
        val t = title.trim()
        val pid = _projectId.value ?: return
        if (t.isEmpty()) return
        val s = section()
        act("Couldn't add") {
            val body = JSONObject().put("project_id", pid).put("title", t)
                .put("kind", if (s?.isOptions == true) "record" else "note")
            s?.let { body.put("section_id", it.id) }
            val created = api.createEntry(body)
            if (created != null) {
                _items.value?.let { _items.value = it.copy(entries = it.entries + created) }
                if (s?.isOptions == true) openEntry(created.id)
            }
            reloadItems()
            reloadListQuietly()
        }
    }

    fun askDeleteEntry(entry: Entry, lang: io.github.ronganam.dana.data.projects.Lang) {
        val files = entry.files.size
        _confirm.value = ProjectsConfirm(
            title = lang.t("Delete “${entry.title}”?", "למחוק את “${entry.title}”?"),
            message = if (files > 0) lang.t("Its ${ProjectsCore.plural(files, "file")} go with it.", "גם הקבצים שלו יימחקו.")
                else lang.t("This can't be undone.", "אי אפשר לבטל."),
            action = lang.t("Delete", "מחיקה"),
            cancel = lang.t("Cancel", "ביטול")
        ) {
            act("Couldn't delete") {
                api.deleteEntry(entry.id)
                if (_entryId.value == entry.id) closeEntry()
                _items.value?.let { _items.value = it.copy(entries = it.entries.filter { e -> e.id != entry.id }) }
                ctx.messages.post("Deleted")
                reloadItems()
                reloadListQuietly()
            }
        }
    }

    fun dismissConfirm() { _confirm.value = null }

    // Summary

    fun editSummary() { _summaryDraft.value = section()?.summary.orEmpty() }
    fun setSummaryDraft(text: String) { _summaryDraft.value = text }
    fun cancelSummary() { _summaryDraft.value = null }
    fun saveSummary() {
        val s = section() ?: return
        val text = _summaryDraft.value ?: return
        act("Couldn't save the summary") {
            api.updateSection(s.id, JSONObject().put("summary", text))
            _summaryDraft.value = null
            _items.value?.let { _items.value = it.copy(sections = it.sections.map { x -> if (x.id == s.id) x.copy(summary = text) else x }) }
            reloadItems()
        }
    }

    // Files

    fun upload(entryId: String, uris: List<Uri>, note: String = "") {
        if (uris.isEmpty()) return
        act("Couldn't upload") {
            var ok = 0
            for (uri in uris) {
                _uploading.value += 1
                try {
                    val (name, size) = withContext(Dispatchers.IO) { describe(uri) }
                    val mime = appContext.contentResolver.getType(uri).orEmpty()
                    api.uploadFile(entryId, name, mime, size, note) {
                        appContext.contentResolver.openInputStream(uri) ?: throw java.io.IOException("Can't read $name")
                    }
                    ok++
                } catch (e: ProjectsApiError) {
                    ctx.messages.post(e.message)
                } finally {
                    _uploading.value -= 1
                }
            }
            if (ok > 0) ctx.messages.post(if (ok == 1) "File added" else "$ok files added")
            reloadItems()
        }
    }

    private fun describe(uri: Uri): Pair<String, Long> {
        var name = uri.lastPathSegment ?: "file"
        var size = -1L
        appContext.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                c.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 }?.let { i -> c.getString(i)?.let { name = it } }
                c.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 && !c.isNull(it) }?.let { size = c.getLong(it) }
            }
        }
        return name to size
    }

    fun askDeleteFile(file: EntryFile, lang: io.github.ronganam.dana.data.projects.Lang) {
        _confirm.value = ProjectsConfirm(lang.t("Remove “${file.name}”?", "להסיר את “${file.name}”?"), "", lang.t("Remove", "הסרה"), lang.t("Cancel", "ביטול")) {
            act("Couldn't remove the file") {
                api.deleteFile(file)
                reloadItems()
            }
        }
    }

    /** Download the file (Bearer token) to the cache and hand it to an app that opens its type. */
    fun openFile(file: EntryFile) {
        if (_opening.value != null) return
        _opening.value = file.id
        act("Couldn't open the file") {
            try {
                val safe = file.name.replace(Regex("[/\\\\:*?\"<>|]"), "_").ifBlank { "file" }
                val dest = File(appContext.cacheDir, "project_files/${file.id}/$safe")
                if (!dest.exists() || dest.length() == 0L) api.download(file, dest)
                val uri = FileProvider.getUriForFile(appContext, "${appContext.packageName}.fileprovider", dest)
                val intent = Intent(Intent.ACTION_VIEW).setDataAndType(uri, file.mime.ifBlank { "*/*" })
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
                try {
                    appContext.startActivity(Intent.createChooser(intent, file.name).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION))
                } catch (e: ActivityNotFoundException) {
                    ctx.messages.post("No app on this phone opens ${file.name}")
                }
            } finally {
                _opening.value = null
            }
        }
    }

    /** Open a tel: / https: / wa.me link outside the app. */
    fun openLink(link: String) {
        try {
            val action = if (link.startsWith("tel:")) Intent.ACTION_DIAL else Intent.ACTION_VIEW
            appContext.startActivity(Intent(action, Uri.parse(link)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: ActivityNotFoundException) {
            ctx.messages.post("No app opens that link")
        }
    }

    // ── Editors ──

    fun newProject() { _editor.value = ProjectsEditor.ProjectDraft() }

    fun editProject() {
        val p = project() ?: return
        _editor.value = ProjectsEditor.ProjectDraft(
            id = p.id, title = p.title, description = p.description, icon = p.icon.ifBlank { "📁" }, color = p.color,
            targetDate = p.targetDate.orEmpty(), budget = p.budget?.let { ProjectsCore.number(it).replace(",", "") }.orEmpty(),
            status = p.status
        )
    }

    fun newSection(kind: String) {
        val lang = ProjectsCore.langOf(project(), _items.value?.sections.orEmpty())
        _editor.value = ProjectsEditor.SectionDraft(kind = kind, columns = if (kind == "options") ProjectsCore.defaultColumns(lang) else emptyList())
    }

    fun editSection() {
        val s = section() ?: return
        _editor.value = ProjectsEditor.SectionDraft(
            id = s.id, title = s.title, icon = s.icon, kind = s.kind, columns = s.columns,
            entries = _items.value?.entries?.count { it.sectionId == s.id } ?: 0
        )
    }

    fun updateEditor(draft: ProjectsEditor) { _editor.value = draft }
    fun closeEditor() { _editor.value = null }

    fun saveEditor() {
        when (val d = _editor.value) {
            is ProjectsEditor.ProjectDraft -> saveProject(d)
            is ProjectsEditor.SectionDraft -> saveSection(d)
            null -> Unit
        }
    }

    private fun saveProject(d: ProjectsEditor.ProjectDraft) {
        val title = d.title.trim()
        if (title.isEmpty()) return
        act("Couldn't save the project") {
            val body = JSONObject()
                .put("title", title)
                .put("icon", d.icon.trim().ifEmpty { "📁" })
                .put("description", d.description.trim())
                .put("target_date", d.targetDate.trim())
                .put("budget", d.budget.trim())
            if (d.color.isNotBlank()) body.put("color", d.color)
            if (d.id == null) {
                val id = api.createProject(body)
                _editor.value = null
                loadList()
                if (id.isNotEmpty()) openProject(id)
            } else {
                api.updateProject(d.id, body)
                _editor.value = null
                loadList()
                reloadItems()
            }
        }
    }

    fun archiveProject() {
        val d = _editor.value as? ProjectsEditor.ProjectDraft ?: return
        val id = d.id ?: return
        act("Couldn't archive") {
            api.updateProject(id, JSONObject().put("status", if (d.status == "archived") "active" else "archived"))
            _editor.value = null
            loadList()
            reloadItems()
        }
    }

    fun askDeleteProject() {
        val d = _editor.value as? ProjectsEditor.ProjectDraft ?: return
        val id = d.id ?: return
        _confirm.value = ProjectsConfirm("Delete “${d.title}”?", "Everything in it goes too: sections, items and files.", "Delete") {
            act("Couldn't delete") {
                api.deleteProject(id)
                _editor.value = null
                closeProject()
                loadList()
                ctx.messages.post("Deleted ${d.title}")
            }
        }
    }

    private fun saveSection(d: ProjectsEditor.SectionDraft) {
        val title = d.title.trim()
        val pid = _projectId.value ?: return
        if (title.isEmpty()) return
        act("Couldn't save the section") {
            val body = JSONObject().put("title", title).put("icon", d.icon.trim()).put("kind", d.kind)
            if (d.kind == "options") body.put("columns", ProjectsJson.columnsJson(ProjectsCore.columnsPayload(d.columns)))
            else if (d.id == null) body.put("columns", org.json.JSONArray())
            if (d.id == null) {
                val id = api.createSection(pid, body)
                _editor.value = null
                loadItems(pid)
                if (id.isNotEmpty()) openSection(id)
            } else {
                api.updateSection(d.id, body)
                _editor.value = null
                loadItems(pid)
            }
            reloadListQuietly()
        }
    }

    fun askDeleteSection() {
        val d = _editor.value as? ProjectsEditor.SectionDraft ?: return
        val id = d.id ?: return
        _confirm.value = ProjectsConfirm(
            "Delete the section “${d.title}”?",
            if (d.entries > 0) "Its ${ProjectsCore.plural(d.entries, "item")} stay in the project, under Other items." else "",
            "Delete"
        ) {
            act("Couldn't delete") {
                api.deleteSection(id)
                _editor.value = null
                if (_sectionId.value == id) openSection(null)
                reloadItems()
                reloadListQuietly()
            }
        }
    }

    /** Server config changed / signed out. */
    fun forget() {
        closeProject()
        _list.value = null
        _listError.value = null
    }

    private companion object { const val TAG = "Projects" }
}
