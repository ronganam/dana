package io.github.ronganam.dana.data.network

import io.github.ronganam.dana.data.models.AboutFact
import io.github.ronganam.dana.data.models.AboutPage
import io.github.ronganam.dana.data.models.AboutSection
import io.github.ronganam.dana.data.models.ActResult
import io.github.ronganam.dana.data.models.LearnedPhrase
import io.github.ronganam.dana.data.models.SelfCheck
import io.github.ronganam.dana.data.network.ApiClient.Companion.enc
import io.github.ronganam.dana.data.network.ApiClient.Companion.jsonBody
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

@Serializable
internal data class AboutFactWire(
    val id: OptString = null,
    val text: OptString = null,
    val source: OptString = null,
    val sourceKind: OptString = null,
    val section: OptString = null
) {
    fun toModel(sectionKey: String? = null): AboutFact? {
        val id = id ?: return null
        val text = text?.takeIf { it.isNotBlank() } ?: return null
        val kind = sourceKind.orEmpty()
        return AboutFact(id, text, source?.takeIf { it.isNotBlank() } ?: defaultSource(kind), kind, section ?: sectionKey.orEmpty())
    }

    private fun defaultSource(kind: String) = when (kind) {
        "chat" -> "a chat"
        "call" -> "a call"
        "told" -> "you told Dana"
        "owner" -> "you added this"
        "edited" -> "you edited this"
        "imported" -> "your old Memory list"
        else -> ""
    }
}

@Serializable
internal data class AboutSectionWire(val key: OptString = null, val label: OptString = null, val facts: LenientList<AboutFactWire> = emptyList())

@Serializable
internal data class AboutPageWire(val sections: LenientList<AboutSectionWire> = emptyList(), val usedChars: Int = 0, val maxChars: Int = 0) {
    fun toModel() = AboutPage(
        sections.mapNotNull { sec ->
            val key = sec.key ?: return@mapNotNull null
            AboutSection(key, sec.label ?: SECTION_LABELS[key] ?: key.replaceFirstChar { it.uppercase() }, sec.facts.mapNotNull { it.toModel(key) })
        },
        usedChars, maxChars
    )

    companion object {
        private val SECTION_LABELS = linkedMapOf("people" to "People", "home" to "Home", "habits" to "Habits", "other" to "Other")
    }
}

@Serializable
private data class FactWritten(val fact: AboutFactWire? = null, val undoId: OptString = null)

@Serializable
internal data class ShortcutWire(
    val id: OptString = null,
    val phrase: OptString = null,
    val does: OptString = null,
    val tool: OptString = null,
    val timesUsed: Int = 0,
    val active: Boolean = false,
    val seen: Int = 0,
    val learnAfter: Int? = null
)

@Serializable
internal data class ShortcutsWire(val learnAfter: Int = 3, val shortcuts: LenientList<ShortcutWire> = emptyList()) {
    /** Learned (active) phrases first, most used first. */
    fun toModel(): List<LearnedPhrase> = shortcuts.mapNotNull { s ->
        val id = s.id ?: return@mapNotNull null
        val phrase = s.phrase?.takeIf { it.isNotBlank() } ?: return@mapNotNull null
        LearnedPhrase(id, phrase, s.does?.takeIf { it.isNotBlank() } ?: s.tool?.takeIf { it.isNotBlank() } ?: "", s.timesUsed, s.active, s.seen, s.learnAfter ?: learnAfter)
    }.sortedWith(compareByDescending<LearnedPhrase> { it.active }.thenByDescending { it.timesUsed })
}

@Serializable
internal data class CheckWire(val key: OptString = null, val label: OptString = null, val ok: Boolean = true, val detail: OptString = null, val ignoredUntil: OptString = null)

@Serializable
internal data class ChecksWire(val checks: LenientList<CheckWire> = emptyList()) {
    /** Failing checks first (server order kept within each group), then ignored ones, then the healthy ones. */
    fun toModel(): List<SelfCheck> = checks.mapNotNull { c ->
        val key = c.key ?: return@mapNotNull null
        SelfCheck(key, c.label?.takeIf { it.isNotBlank() } ?: key.replace('_', ' ').replaceFirstChar { it.uppercase() }, c.ok, c.detail?.takeIf { it.isNotBlank() } ?: "", c.ignoredUntil)
    }.sortedBy { if (it.failing) 0 else if (!it.ok) 1 else 2 }
}

/** About you, learned phrases and the self-check (server docs/about.md, agent-actions.md, now.md). */
class AboutApi(private val http: ApiClient) {

    suspend fun page(): AboutPage =
        http.decode(AboutPageWire.serializer(), http.text(http.request("/api/about"), "Failed to load About you", "{}")).toModel()

    /** Add a fact; [section] null lets the server guess. Returns the stored fact (or the one already known) and its undo id. */
    suspend fun addFact(text: String, section: String?): Pair<AboutFact?, String?> {
        val payload = buildJsonObject { put("text", text); if (!section.isNullOrEmpty()) put("section", section) }
        val w = http.jsonOrThrow(http.request("/api/about/facts") { post(jsonBody(payload)) }, "Couldn't add it", FactWritten.serializer())
        return w.fact?.toModel() to w.undoId
    }

    /** Edit a fact's text and/or section. Returns the undo id. */
    suspend fun updateFact(id: String, text: String?, section: String?): String? {
        val payload = buildJsonObject { text?.let { put("text", it) }; section?.let { put("section", it) } }
        return http.jsonOrThrow(http.request("/api/about/facts/${enc(id)}") { put(jsonBody(payload)) }, "Couldn't save it", FactWritten.serializer()).undoId
    }

    /** Forget a fact. Returns the undo id. */
    suspend fun deleteFact(id: String): String? =
        http.jsonOrThrow(http.request("/api/about/facts/${enc(id)}") { delete() }, "Couldn't remove it", FactWritten.serializer()).undoId

    suspend fun shortcuts(): List<LearnedPhrase> =
        http.decode(ShortcutsWire.serializer(), http.text(http.request("/api/shortcuts"), "Failed to load learned phrases", "{}")).toModel()

    suspend fun deleteShortcut(id: String): Boolean = http.send(http.request("/api/shortcuts/${enc(id)}") { delete() })

    suspend fun checks(): List<SelfCheck> =
        http.decode(ChecksWire.serializer(), http.text(http.request("/api/checks"), "Failed to load the self-check", "{}")).toModel()

    /** Ignore a failing check for [days] (0 = stop ignoring). */
    suspend fun ignoreCheck(key: String, days: Int = 7): ActResult =
        NotificationsApi.actResult(http.post("/api/checks/${enc(key)}/ignore", buildJsonObject { put("days", days) }, "Couldn't ignore it"), "Ignored for a week")
}
