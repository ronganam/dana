package app.ganam.assistant.ui.screens.memory

import androidx.compose.ui.graphics.Color
import app.ganam.assistant.data.models.Memory
import app.ganam.assistant.ui.theme.CategoryPalette
import app.ganam.assistant.ui.theme.Danger
import app.ganam.assistant.ui.theme.Info
import app.ganam.assistant.ui.theme.Success

internal fun memoryTitle(key: String): String = key.replace("_", " ").replaceFirstChar { it.uppercase() }

private val ISO_TIMESTAMP = Regex("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}(:\\d{2}(\\.\\d+)?)?(Z|[+-]\\d{2}:?\\d{2})?$")

/** Log-style memories whose value is just an ISO timestamp read as 'Fri 6 Nov · 21:45' (local time). */
internal fun memoryValueDisplay(value: String): String {
    val v = value.trim()
    if (!ISO_TIMESTAMP.matches(v)) return value
    return try {
        val zoned = if (v.length > 19 && (v.endsWith("Z") || v.substring(19).any { it == '+' || it == '-' })) {
            java.time.OffsetDateTime.parse(v).atZoneSameInstant(java.time.ZoneId.systemDefault())
        } else {
            java.time.LocalDateTime.parse(v).atZone(java.time.ZoneId.systemDefault())
        }
        java.time.format.DateTimeFormatter.ofPattern("EEE d MMM · HH:mm", java.util.Locale.getDefault()).format(zoned)
    } catch (e: Exception) {
        value
    }
}

internal fun categoryLabel(category: String): String = category.replaceFirstChar { it.uppercase() }

internal fun memoryCategoryColor(category: String): Color = when (category.lowercase()) {
    "personal" -> Danger
    "work" -> Info
    "general" -> Success
    else -> CategoryPalette[4]
}

internal data class LetterSection(val letter: String, val items: List<Memory>)

/** Address-book style index: memories sorted by title and grouped under their first letter. */
internal fun letterSections(memories: List<Memory>): List<LetterSection> =
    memories
        .sortedBy { memoryTitle(it.key).lowercase() }
        .groupBy { m ->
            val first = memoryTitle(m.key).trim().firstOrNull()
            if (first != null && first.isLetter()) first.uppercase() else "#"
        }
        .map { (letter, items) -> LetterSection(letter, items) }
