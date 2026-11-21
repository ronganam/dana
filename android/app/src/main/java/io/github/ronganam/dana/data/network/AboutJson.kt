package io.github.ronganam.dana.data.network

import io.github.ronganam.dana.data.models.AboutFact
import io.github.ronganam.dana.data.models.AboutPage
import io.github.ronganam.dana.data.models.AboutSection
import io.github.ronganam.dana.data.models.LearnedPhrase
import io.github.ronganam.dana.data.models.SelfCheck
import org.json.JSONArray
import org.json.JSONObject

/** Parsing for `/api/about`, `/api/shortcuts` and `/api/checks`. Unknown fields are ignored, missing ones defaulted. */
object AboutJson {

    private fun JSONObject.s(key: String): String? =
        if (!has(key) || isNull(key)) null else optString(key).takeIf { it.isNotBlank() && it != "null" }

    private fun JSONArray?.objects(): List<JSONObject> =
        if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }

    private val SECTION_LABELS = linkedMapOf("people" to "People", "home" to "Home", "habits" to "Habits", "other" to "Other")

    fun page(o: JSONObject): AboutPage {
        val sections = o.optJSONArray("sections").objects().mapNotNull { sec ->
            val key = sec.s("key") ?: return@mapNotNull null
            AboutSection(
                key = key,
                label = sec.s("label") ?: SECTION_LABELS[key] ?: key.replaceFirstChar { it.uppercase() },
                facts = sec.optJSONArray("facts").objects().mapNotNull { fact(it, key) }
            )
        }
        return AboutPage(sections, o.optInt("used_chars"), o.optInt("max_chars"))
    }

    fun fact(o: JSONObject, sectionKey: String? = null): AboutFact? {
        val id = o.s("id") ?: return null
        val text = o.s("text") ?: return null
        val kind = o.s("source_kind") ?: ""
        return AboutFact(
            id = id,
            text = text,
            source = o.s("source") ?: defaultSource(kind),
            sourceKind = kind,
            section = o.s("section") ?: sectionKey.orEmpty()
        )
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

    /** Learned (active) phrases first, most used first. */
    fun shortcuts(o: JSONObject): List<LearnedPhrase> {
        val learnAfter = o.optInt("learn_after", 3)
        return o.optJSONArray("shortcuts").objects().mapNotNull { s ->
            val id = s.s("id") ?: return@mapNotNull null
            val phrase = s.s("phrase") ?: return@mapNotNull null
            LearnedPhrase(
                id = id,
                phrase = phrase,
                does = s.s("does") ?: s.s("tool") ?: "",
                timesUsed = s.optInt("times_used"),
                active = s.optBoolean("active", false),
                seen = s.optInt("seen"),
                learnAfter = s.optInt("learn_after", learnAfter)
            )
        }.sortedWith(compareByDescending<LearnedPhrase> { it.active }.thenByDescending { it.timesUsed })
    }

    /** Failing checks first (server order kept within each group), then ignored ones, then the healthy ones. */
    fun checks(o: JSONObject): List<SelfCheck> =
        o.optJSONArray("checks").objects().mapNotNull { c ->
            val key = c.s("key") ?: return@mapNotNull null
            SelfCheck(
                key = key,
                label = c.s("label") ?: key.replace('_', ' ').replaceFirstChar { it.uppercase() },
                ok = c.optBoolean("ok", true),
                detail = c.s("detail") ?: "",
                ignoredUntil = c.s("ignored_until")
            )
        }.sortedBy { if (it.failing) 0 else if (!it.ok) 1 else 2 }
}
