package io.github.ronganam.dana.ui.screens.people

import android.telephony.PhoneNumberUtils
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.models.Person
import io.github.ronganam.dana.data.models.PersonEvent
import io.github.ronganam.dana.ui.screens.calls.isHebrew
import io.github.ronganam.dana.ui.theme.CategoryPalette
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale

// ---------------------------------------------------------------- avatar

/** A stable soft hue per person so faces are easy to spot in a long list. */
fun personHue(id: String): Color = CategoryPalette[Math.floorMod(id.hashCode(), CategoryPalette.size)]

@Composable
fun PersonAvatar(person: Person, size: Dp = 44.dp, ring: Boolean = false) =
    PersonAvatar(person.id, person.initials, size, ring)

@Composable
fun PersonAvatar(id: String, initials: String, size: Dp = 44.dp, ring: Boolean = false) {
    val hue = personHue(id)
    Box(
        Modifier.size(size).clip(CircleShape).background(hue.copy(alpha = 0.18f))
            .then(if (ring) Modifier.border(2.dp, MaterialTheme.colorScheme.primary, CircleShape) else Modifier),
        contentAlignment = Alignment.Center
    ) {
        Text(
            initials.ifBlank { "?" },
            color = hue,
            fontWeight = FontWeight.SemiBold,
            fontSize = (size.value * 0.36f).sp,
            maxLines = 1,
        )
    }
}

// ---------------------------------------------------------------- direction-aware text

/** Text laid out in its own script's direction: Hebrew lines start at the right, Latin at the left. */
@Composable
fun DirText(
    text: String,
    style: TextStyle,
    modifier: Modifier = Modifier,
    color: Color = Color.Unspecified,
    maxLines: Int = Int.MAX_VALUE,
) {
    val rtl = isHebrew(text)
    CompositionLocalProvider(LocalLayoutDirection provides if (rtl) LayoutDirection.Rtl else LayoutDirection.Ltr) {
        Text(
            text, style = style, color = color, maxLines = maxLines, overflow = TextOverflow.Ellipsis,
            textAlign = TextAlign.Start, modifier = modifier.fillMaxWidth()
        )
    }
}

/** A bullet line whose dot sits on the reading side (right for Hebrew). */
@Composable
fun DirBullet(text: String, dot: Color, style: TextStyle = MaterialTheme.typography.bodyMedium) {
    val rtl = isHebrew(text)
    CompositionLocalProvider(LocalLayoutDirection provides if (rtl) LayoutDirection.Rtl else LayoutDirection.Ltr) {
        Row(Modifier.fillMaxWidth().padding(vertical = 5.dp)) {
            Box(Modifier.padding(top = 8.dp).size(5.dp).clip(CircleShape).background(dot))
            Spacer(Modifier.width(10.dp))
            Text(text, style = style, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f))
        }
    }
}

// ---------------------------------------------------------------- time labels

private val ZONE: ZoneId get() = ZoneId.systemDefault()
private val HM: DateTimeFormatter get() = DateTimeFormatter.ofPattern("HH:mm", Locale.getDefault())
private val WEEKDAY: DateTimeFormatter get() = DateTimeFormatter.ofPattern("EEE", Locale.getDefault())
private val DAY_MONTH: DateTimeFormatter get() = DateTimeFormatter.ofPattern("d MMM", Locale.getDefault())
private val MONTH_YEAR: DateTimeFormatter get() = DateTimeFormatter.ofPattern("MMM yyyy", Locale.getDefault())
private val DAY_HEADER: DateTimeFormatter get() = DateTimeFormatter.ofPattern("EEEE d MMMM", Locale.getDefault())
private val DAY_HEADER_YEAR: DateTimeFormatter get() = DateTimeFormatter.ofPattern("EEEE d MMMM yyyy", Locale.getDefault())

private fun parseLocal(at: String?) = at?.let { runCatching { OffsetDateTime.parse(it).atZoneSameInstant(ZONE) }.getOrNull() }

/** Messaging-app style: 14:05 · Yesterday · Mon · 3 Oct · Oct 2025. */
fun shortWhen(at: String?): String {
    val t = parseLocal(at) ?: return ""
    val today = LocalDate.now(ZONE)
    val d = t.toLocalDate()
    val days = ChronoUnit.DAYS.between(d, today)
    return when {
        days <= 0L -> HM.format(t)
        days == 1L -> "Yesterday"
        days < 7 -> WEEKDAY.format(t)
        d.year == today.year -> DAY_MONTH.format(t)
        else -> MONTH_YEAR.format(t)
    }
}

fun timeOf(at: String): String = parseLocal(at)?.let { HM.format(it) } ?: ""

fun dayHeader(day: LocalDate): String {
    val today = LocalDate.now(ZONE)
    return when (ChronoUnit.DAYS.between(day, today)) {
        0L -> "Today"
        1L -> "Yesterday"
        else -> if (day.year == today.year) DAY_HEADER.format(day) else DAY_HEADER_YEAR.format(day)
    }
}

fun localDay(at: String): LocalDate? = parseLocal(at)?.toLocalDate()

fun prettyPhone(digits: String): String =
    runCatching { PhoneNumberUtils.formatNumber("+$digits", Locale.getDefault().country.ifBlank { "IL" }) }.getOrNull() ?: "+$digits"

fun firstName(name: String): String = name.trim().substringBefore(' ').ifBlank { name }

/** "Every 4 days" / "Weekly" / "Monthly" from the typical gap between contacts. */
fun rhythmLabel(gapDays: Double?): String? {
    val g = gapDays ?: return null
    return when {
        g < 1.5 -> "Daily"
        g < 6 -> "Every ${g.toInt().coerceAtLeast(2)} days"
        g < 10 -> "Weekly"
        g < 20 -> "Every ${(g / 7).toInt().coerceAtLeast(2)} weeks"
        g < 45 -> "Monthly"
        else -> "Every ${(g / 30).toInt().coerceAtLeast(2)} months"
    }
}

// ---------------------------------------------------------------- list sections

data class PeopleSection(val key: String, val title: String, val people: List<Person>, val accent: Boolean = false)

fun Person.needsYou(): Boolean = openTasks > 0 || stats.drifting || (birthdayInDays ?: 99) in 0..7

/** Why this person is in "Needs you" (short, for a tag). */
fun Person.attentionTags(): List<String> = buildList {
    birthdayInDays?.takeIf { it in 0..14 }?.let { add(if (it == 0) "Birthday today" else "Birthday in $it d") }
    if (openTasks > 0) add("$openTasks open")
    if (stats.drifting) add(stats.daysSince?.let { "Quiet ${it}d" } ?: "Been a while")
}

/**
 * Recent sort without a search: Needs you, then by how recently you were in touch.
 * A–Z: by first letter. Been a while: one list in the server's order.
 */
fun sectionsFor(people: List<Person>, sort: String, searching: Boolean): List<PeopleSection> {
    if (people.isEmpty()) return emptyList()
    if (searching) return listOf(PeopleSection("results", "${people.size} found", people))
    return when (sort) {
        "name" -> people.sortedBy { it.name.lowercase() }
            .groupBy { it.name.trim().firstOrNull()?.uppercaseChar()?.takeIf { c -> c.isLetter() }?.toString() ?: "#" }
            .map { (letter, list) -> PeopleSection("l-$letter", letter, list) }
        "drifting" -> listOf(PeopleSection("drifting", "Longest since last contact", people))
        else -> {
            val needs = people.filter { it.needsYou() }.take(6)
            val rest = people - needs.toSet()
            fun bucket(p: Person): Int = when (val d = p.stats.daysSince) {
                null -> 3
                in 0..6 -> 0
                in 7..30 -> 1
                else -> 2
            }
            val byBucket = rest.groupBy(::bucket)
            buildList {
                if (needs.isNotEmpty()) add(PeopleSection("needs", "Needs you", needs, accent = true))
                byBucket[0]?.let { add(PeopleSection("week", "This week", it)) }
                byBucket[1]?.let { add(PeopleSection("month", "This month", it)) }
                byBucket[2]?.let { add(PeopleSection("earlier", "Earlier", it)) }
                byBucket[3]?.let { add(PeopleSection("never", "No contact yet", it)) }
            }
        }
    }
}

// ---------------------------------------------------------------- timeline

/** One history row after merging: a call, or one chat's messages on one day. */
data class HistoryEntry(val key: String, val event: PersonEvent, val chat: String, val count: Int, val texts: List<String>)

private val COUNT_SUFFIX = Regex("""\s*\(\d+\s+messages?\)\s*$""", RegexOption.IGNORE_CASE)

/**
 * The server sends one entry per notification burst; the same chat often shows
 * up several times a day ("Family (6 messages)", "Family (7 messages)"). Merge
 * those into one row per chat per day and drop repeated lines.
 */
fun mergeHistory(events: List<PersonEvent>): List<Pair<LocalDate?, List<HistoryEntry>>> {
    val merged = mutableListOf<HistoryEntry>()
    val index = HashMap<String, Int>()
    for (e in events) {
        if (e.type == "call") {
            merged += HistoryEntry("c" + e.id, e, "", 0, emptyList())
            continue
        }
        val chat = e.chat.replace(COUNT_SUFFIX, "").trim()
        val k = "${localDay(e.at)}|${e.app}|$chat"
        val at = index[k]
        if (at == null) {
            index[k] = merged.size
            merged += HistoryEntry("m" + e.id, e, chat, e.count.coerceAtLeast(1), e.texts)
        } else {
            val prev = merged[at]
            merged[at] = prev.copy(count = prev.count + e.count.coerceAtLeast(1), texts = (prev.texts + e.texts).distinct())
        }
    }
    return merged.groupBy { localDay(it.event.at) }.toList()
}
