package io.github.ronganam.dana.data.projects

import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.LenientList
import io.github.ronganam.dana.data.network.LenientListSerializer
import io.github.ronganam.dana.data.network.OptString
import io.github.ronganam.dana.data.network.TextList
import io.github.ronganam.dana.data.network.toJsonElement
import io.github.ronganam.dana.data.network.toPlain
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull

/*
 * The Projects payloads (server addons/knowledge/api.py, server/tests/fixtures/projects_web.json)
 * ↔ the Projects models, read and written with ApiJson.
 */

/** A number the server may send as a number or as text ("4500"); null when it is neither. */
private fun JsonElement?.number(): Double? =
    (this as? JsonPrimitive)?.takeIf { it !is JsonNull }?.let { it.doubleOrNull ?: it.content.toDoubleOrNull() }

@Serializable
internal data class ProjectWire(
    val id: String = "", val title: String = "", val description: OptString = null, val icon: OptString = null,
    val color: OptString = null, val status: OptString = null, val targetDate: OptString = null, val budget: JsonElement? = null,
    val entryCount: Int = 0, val taskCount: Int = 0, val sectionCount: Int = 0, val decisions: Int = 0, val decided: Int = 0,
    val committed: JsonElement? = null, val sharedBy: OptString = null, val access: OptString = null, val createdAt: OptString = null
) {
    fun toModel() = Project(
        id, title, description.orEmpty(), icon.orEmpty(), color.orEmpty(), status ?: "active", targetDate, budget.number(),
        entryCount, taskCount, sectionCount, decisions, decided, committed.number() ?: 0.0, sharedBy, access, createdAt
    )
}

@Serializable
internal data class ColumnWire(
    val key: String = "", val name: String = "", val type: OptString = null, val role: OptString = null,
    val best: OptString = null, val options: TextList = emptyList()
) {
    fun toModel() = Column(key, name, type ?: "text", role.orEmpty(), best.orEmpty(), options)
}

@Serializable
internal data class SectionWire(
    val id: String = "", val projectId: String = "", val title: String = "", val kind: OptString = null, val icon: OptString = null,
    val summary: OptString = null, val columns: LenientList<ColumnWire> = emptyList(), val position: Int = 0
) {
    fun toModel() = Section(id, projectId, title, kind ?: "notes", icon.orEmpty(), summary.orEmpty(), columns.map { it.toModel() }, position)
}

@Serializable
internal data class FieldWire(val name: String = "", val type: OptString = null, val value: JsonElement? = null) {
    /** [EntryField.value] is plain Kotlin: String, Long / Double, Boolean or null. */
    fun toModel() = EntryField(name, type ?: "text", value.toPlain()?.let { if (it is Map<*, *> || it is List<*>) value.toString() else it })
}

@Serializable
internal data class FileWire(
    val id: String = "", val entryId: String = "", val name: String = "", val mime: OptString = null, val size: Long = 0,
    val note: OptString = null, val source: OptString = null, val transcript: OptString = null, val url: OptString = null
) {
    fun toModel() = EntryFile(id, entryId, name, mime.orEmpty(), size, note.orEmpty(), source.orEmpty(), transcript, url ?: "/api/entries/$entryId/files/$id")
}

@Serializable
internal data class EntryWire(
    val id: String = "", val projectId: String = "", val sectionId: OptString = null, val kind: OptString = null, val title: String = "",
    val body: OptString = null, val fields: LenientList<FieldWire> = emptyList(), val decision: OptString = null, val url: OptString = null,
    val summary: OptString = null, val transcript: OptString = null, val files: LenientList<FileWire> = emptyList(),
    val sharedBy: OptString = null, val access: OptString = null, val createdAt: OptString = null
) {
    fun toModel() = Entry(
        id, projectId, sectionId, kind ?: "note", title, body.orEmpty(), fields.map { it.toModel() }.filter { it.name.isNotBlank() },
        decision.orEmpty(), url, summary, transcript, files.map { it.toModel() }, sharedBy, access, createdAt
    )
}

@Serializable
internal data class TaskWire(val id: String = "", val title: String = "", val status: OptString = null)

@Serializable
internal data class ItemsWire(
    val project: ProjectWire = ProjectWire(),
    val sections: LenientList<SectionWire> = emptyList(),
    val entries: LenientList<EntryWire> = emptyList(),
    val tasks: LenientList<TaskWire> = emptyList()
) {
    fun toModel() = ProjectItems(
        project.toModel(), sections.map { it.toModel() }.sortedBy { it.position }, entries.map { it.toModel() },
        tasks.map { ProjectTask(it.id, it.title, it.status ?: "pending") }
    )
}

/** One value of an entry as a PUT sends it (`value: null` included: it clears the value). */
@Serializable
data class FieldBody(val name: String, val type: String, val value: JsonElement = JsonNull)

/** A section column as a PUT / POST sends it (keys kept, see [ProjectsCore.columnsPayload]). */
@Serializable
data class ColumnBody(
    val name: String,
    val type: String,
    val key: String? = null,
    val role: String? = null,
    val best: String? = null,
    val options: List<String>? = null
)

/** A project's fields to write (`POST /api/projects`, `PUT /api/projects/{id}`); null = leave as is. */
@Serializable
data class ProjectChanges(
    val title: String? = null,
    val icon: String? = null,
    val description: String? = null,
    val targetDate: String? = null,
    val budget: String? = null,
    val color: String? = null,
    val status: String? = null
)

/** A section's fields to write; null = leave as is. */
@Serializable
data class SectionChanges(
    val title: String? = null,
    val icon: String? = null,
    val kind: String? = null,
    val summary: String? = null,
    val columns: List<ColumnBody>? = null
)

/** An entry's fields to write (`POST /api/entries`, `PUT /api/entries/{id}`); null = leave as is, `section_id: ""` = no section. */
@Serializable
data class EntryChanges(
    val projectId: String? = null,
    val sectionId: String? = null,
    val title: String? = null,
    val kind: String? = null,
    val body: String? = null,
    val decision: String? = null,
    val fields: List<FieldBody>? = null
)

object ProjectsJson {
    fun projects(text: String): List<Project> =
        ApiJson.decodeFromString(LenientListSerializer(ProjectWire.serializer()), text.ifBlank { "[]" }).map { it.toModel() }

    fun items(text: String): ProjectItems = ApiJson.decodeFromString(ItemsWire.serializer(), text.ifBlank { "{}" }).toModel()

    fun entry(element: JsonElement): Entry = ApiJson.decodeFromJsonElement(EntryWire.serializer(), element).toModel()

    fun file(element: JsonElement): EntryFile = ApiJson.decodeFromJsonElement(FileWire.serializer(), element).toModel()

    // ── Writes ──

    fun fieldsBody(fields: List<EntryField>): List<FieldBody> = fields.map { FieldBody(it.name, it.type, it.value.toJsonElement()) }

    fun columnsBody(columns: List<Column>): List<ColumnBody> = columns.map { c ->
        ColumnBody(
            name = c.name,
            type = c.type,
            key = c.key.ifEmpty { null },
            role = "price".takeIf { c.role == "price" },
            best = c.best.ifEmpty { null },
            options = c.options.takeIf { c.type == "select" && it.isNotEmpty() }
        )
    }
}
