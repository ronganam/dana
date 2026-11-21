package io.github.ronganam.dana.data.projects

import org.json.JSONArray
import org.json.JSONObject

/** org.json ↔ the Projects models (payloads: server integrations/knowledge/api.py, test_fixtures/projects_web.json). */
object ProjectsJson {

    private fun JSONObject.str(key: String): String? =
        if (!has(key) || isNull(key)) null else optString(key).takeIf { it.isNotEmpty() }

    private fun JSONObject.num(key: String): Double? =
        if (!has(key) || isNull(key)) null else (opt(key) as? Number)?.toDouble() ?: optString(key).toDoubleOrNull()

    private fun JSONArray?.objects(): List<JSONObject> =
        if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }

    fun projects(arr: JSONArray): List<Project> = arr.objects().map(::project)

    fun project(o: JSONObject) = Project(
        id = o.optString("id"),
        title = o.optString("title"),
        description = o.str("description").orEmpty(),
        icon = o.str("icon").orEmpty(),
        color = o.str("color").orEmpty(),
        status = o.str("status") ?: "active",
        targetDate = o.str("target_date"),
        budget = o.num("budget"),
        entryCount = o.optInt("entry_count", 0),
        taskCount = o.optInt("task_count", 0),
        sectionCount = o.optInt("section_count", 0),
        decisions = o.optInt("decisions", 0),
        decided = o.optInt("decided", 0),
        committed = o.num("committed") ?: 0.0,
        sharedBy = o.str("shared_by"),
        access = o.str("access"),
        createdAt = o.str("created_at")
    )

    fun column(o: JSONObject) = Column(
        key = o.optString("key"),
        name = o.optString("name"),
        type = o.str("type") ?: "text",
        role = o.str("role").orEmpty(),
        best = o.str("best").orEmpty(),
        options = o.optJSONArray("options")?.let { a -> (0 until a.length()).map { a.optString(it) }.filter { it.isNotBlank() } }.orEmpty()
    )

    fun section(o: JSONObject) = Section(
        id = o.optString("id"),
        projectId = o.optString("project_id"),
        title = o.optString("title"),
        kind = o.str("kind") ?: "notes",
        icon = o.str("icon").orEmpty(),
        summary = o.str("summary").orEmpty(),
        columns = o.optJSONArray("columns").objects().map(::column),
        position = o.optInt("position", 0)
    )

    /** A JSON value as Kotlin: String / Number / Boolean, null for JSON null. */
    private fun plain(v: Any?): Any? = when (v) {
        null, JSONObject.NULL -> null
        is Number, is Boolean, is String -> v
        else -> v.toString()
    }

    fun field(o: JSONObject) = EntryField(
        name = o.optString("name"),
        type = o.str("type") ?: "text",
        value = plain(if (o.has("value")) o.opt("value") else null)
    )

    fun file(o: JSONObject) = EntryFile(
        id = o.optString("id"),
        entryId = o.optString("entry_id"),
        name = o.optString("name"),
        mime = o.str("mime").orEmpty(),
        size = o.optLong("size", 0),
        note = o.str("note").orEmpty(),
        source = o.str("source").orEmpty(),
        transcript = o.str("transcript"),
        url = o.str("url") ?: "/api/entries/${o.optString("entry_id")}/files/${o.optString("id")}"
    )

    fun entry(o: JSONObject) = Entry(
        id = o.optString("id"),
        projectId = o.optString("project_id"),
        sectionId = o.str("section_id"),
        kind = o.str("kind") ?: "note",
        title = o.optString("title"),
        body = o.str("body").orEmpty(),
        fields = o.optJSONArray("fields").objects().map(::field).filter { it.name.isNotBlank() },
        decision = o.str("decision").orEmpty(),
        url = o.str("url"),
        summary = o.str("summary"),
        transcript = o.str("transcript"),
        files = o.optJSONArray("files").objects().map(::file),
        sharedBy = o.str("shared_by"),
        access = o.str("access"),
        createdAt = o.str("created_at")
    )

    fun task(o: JSONObject) = ProjectTask(o.optString("id"), o.optString("title"), o.str("status") ?: "pending")

    fun items(o: JSONObject) = ProjectItems(
        project = project(o.optJSONObject("project") ?: JSONObject()),
        sections = o.optJSONArray("sections").objects().map(::section).sortedBy { it.position },
        entries = o.optJSONArray("entries").objects().map(::entry),
        tasks = o.optJSONArray("tasks").objects().map(::task)
    )

    // ── Writes ──

    fun fieldsJson(fields: List<EntryField>): JSONArray = JSONArray().apply {
        fields.forEach { f ->
            put(JSONObject().put("name", f.name).put("type", f.type).put("value", f.value ?: JSONObject.NULL))
        }
    }

    /** What a section's PUT / POST sends for its columns (keys kept, see [ProjectsCore.columnsPayload]). */
    fun columnsJson(columns: List<Column>): JSONArray = JSONArray().apply {
        columns.forEach { c ->
            put(JSONObject().apply {
                put("name", c.name)
                put("type", c.type)
                if (c.key.isNotEmpty()) put("key", c.key)
                if (c.role == "price") put("role", "price")
                if (c.best.isNotEmpty()) put("best", c.best)
                if (c.type == "select" && c.options.isNotEmpty()) put("options", JSONArray(c.options))
            })
        }
    }
}
