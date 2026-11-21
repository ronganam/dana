package io.github.ronganam.dana.data.projects

/**
 * The Projects payloads (server docs/knowledge.md, integrations/knowledge/values.py).
 * `GET /api/projects` → [Project] (with its stats); `GET /api/projects/{id}/items` → [ProjectItems].
 * [access] is null for the user's own projects, "edit" / "read" through a share.
 */
data class Project(
    val id: String,
    val title: String,
    val description: String = "",
    val icon: String = "",
    val color: String = "",
    val status: String = "active",
    val targetDate: String? = null,
    val budget: Double? = null,
    val entryCount: Int = 0,
    val taskCount: Int = 0,
    val sectionCount: Int = 0,
    val decisions: Int = 0,
    val decided: Int = 0,
    val committed: Double = 0.0,
    val sharedBy: String? = null,
    val access: String? = null,
    val createdAt: String? = null
) {
    val archived: Boolean get() = status == "archived"
    val canEdit: Boolean get() = access != "read"
    val isOwner: Boolean get() = access == null
}

/** A section's column: [type] from [ProjectsCore.TYPES]; [role] "price" counts toward the budget; [best] "low" / "high". */
data class Column(
    val key: String,
    val name: String,
    val type: String = "text",
    val role: String = "",
    val best: String = "",
    val options: List<String> = emptyList()
)

/** A section: `options` (a decision among options sharing [columns]) or `notes`. */
data class Section(
    val id: String,
    val projectId: String,
    val title: String,
    val kind: String = "notes",
    val icon: String = "",
    val summary: String = "",
    val columns: List<Column> = emptyList(),
    val position: Int = 0
) {
    val isOptions: Boolean get() = kind == "options"
}

/** One value of an entry: [value] is a String, Number, Boolean or null. */
data class EntryField(val name: String, val type: String = "text", val value: Any? = null)

data class EntryFile(
    val id: String,
    val entryId: String,
    val name: String,
    val mime: String = "",
    val size: Long = 0,
    val note: String = "",
    val source: String = "",
    val transcript: String? = null,
    val url: String
)

data class Entry(
    val id: String,
    val projectId: String,
    val sectionId: String? = null,
    val kind: String = "note",
    val title: String = "",
    val body: String = "",
    val fields: List<EntryField> = emptyList(),
    val decision: String = "",
    val url: String? = null,
    val summary: String? = null,
    val transcript: String? = null,
    val files: List<EntryFile> = emptyList(),
    val sharedBy: String? = null,
    val access: String? = null,
    val createdAt: String? = null
)

data class ProjectTask(val id: String, val title: String, val status: String) {
    val done: Boolean get() = status == "done"
}

data class ProjectItems(
    val project: Project,
    val sections: List<Section>,
    val entries: List<Entry>,
    val tasks: List<ProjectTask>
)
