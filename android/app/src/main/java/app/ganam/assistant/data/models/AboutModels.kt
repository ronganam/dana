package app.ganam.assistant.data.models

/** `GET /api/about`: what Dana keeps about the owner, by section (server `docs/about.md`). */
data class AboutPage(
    val sections: List<AboutSection>,
    val usedChars: Int = 0,
    val maxChars: Int = 0
) {
    val factCount: Int get() = sections.sumOf { it.facts.size }
}

/** `people` · `home` · `habits` · `other`, always in that order. */
data class AboutSection(val key: String, val label: String, val facts: List<AboutFact>)

data class AboutFact(
    val id: String,
    val text: String,
    /** Display text of where it came from ("a call with אמא", "you told Dana"). */
    val source: String,
    /** chat · call · told · owner · edited · imported */
    val sourceKind: String,
    val section: String = ""
) {
    /** The small line under the fact: "learned from a call with …" for learned ones, the source as is otherwise. */
    val sourceLine: String get() = when (sourceKind) {
        "chat", "call" -> "learned from $source"
        "imported" -> "from $source"
        else -> source
    }
}

/** A phrase Dana now runs without the model (`GET /api/shortcuts`). */
data class LearnedPhrase(
    val id: String,
    val phrase: String,
    /** In words, e.g. "Open cover Gate". */
    val does: String,
    val timesUsed: Int,
    /** Learned (runs without the model) vs still being seen. */
    val active: Boolean,
    val seen: Int = 0,
    val learnAfter: Int = 0
)

/** One self-check row (`GET /api/checks`). */
data class SelfCheck(
    val key: String,
    val label: String,
    val ok: Boolean,
    val detail: String,
    /** Local ISO time it's ignored until, or null. */
    val ignoredUntil: String?
) {
    val failing: Boolean get() = !ok && ignoredUntil == null
}
