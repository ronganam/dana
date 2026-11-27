package io.github.ronganam.dana.ui.screens.reminders

import io.github.ronganam.dana.data.models.LocationTrigger
import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.data.models.Triggers
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/*
 * The moments a reminder reaches you at (the server's trigger kinds: a time,
 * arriving at / leaving a saved place, getting into / out of the car, waking
 * up; no recurrence), grouped the way the Reminders list and board show them.
 * No Compose here (unit-tested in MomentModelTest).
 */

/** The list's groups, in their order. */
enum class MomentGroup(val title: String) {
    LATE("Late"),
    WAKING("On waking"),
    TODAY("Today"),
    PLACES("Places & car"),
    LATER("Later"),
    NONE("No moment")
}

/** How many snoozes make Dana suggest another moment (the server's review uses the same, `REVIEW_SNOOZE_MIN`). */
const val SNOOZE_SUGGEST_MIN = 3

/**
 * Which group a reminder is in: Late when its time has passed (or, with no
 * moment, its due date has); else the moment that reaches you first — a time
 * today, then waking, then a place or the car, then a later time; no moment
 * at all is No moment.
 */
fun momentGroupOf(task: Task, now: LocalDateTime): MomentGroup {
    val whens = remindWhens(task)
    val at = whens.filterIsInstance<RemindWhen.At>().firstOrNull()
    if (whens.isEmpty()) {
        val due = parseTriggerTime(task.dueDate)?.toLocalDate()
        return if (due != null && due.isBefore(now.toLocalDate())) MomentGroup.LATE else MomentGroup.NONE
    }
    if (at != null && at.time.isBefore(now) && whens.size == 1) return MomentGroup.LATE
    return when {
        at != null && !at.time.isBefore(now) && at.time.toLocalDate() == now.toLocalDate() -> MomentGroup.TODAY
        whens.any { it == RemindWhen.Wake } -> MomentGroup.WAKING
        whens.any { it is RemindWhen.Place || it is RemindWhen.Car } -> MomentGroup.PLACES
        at != null && at.time.isBefore(now) -> MomentGroup.LATE
        else -> MomentGroup.LATER
    }
}

data class MomentSection(val group: MomentGroup, val tasks: List<Task>, val meta: String?)

/** The open reminders in their groups (empty ones dropped), each sorted by when it reaches you, then by title. */
fun momentSections(open: List<Task>, now: LocalDateTime): List<MomentSection> {
    val by = open.groupBy { momentGroupOf(it, now) }
    return MomentGroup.values().mapNotNull { g ->
        val list = by[g].orEmpty().sortedWith(momentOrder())
        if (list.isEmpty()) null else MomentSection(g, list, sectionMeta(g, list, now))
    }
}

private fun momentOrder(): Comparator<Task> = compareBy<Task>(
    { t -> remindWhens(t).filterIsInstance<RemindWhen.At>().firstOrNull()?.time ?: parseTriggerTime(t.dueDate) ?: LocalDateTime.MAX },
    { it.title.lowercase() }
)

/** The line at a group's end: only what the data says. */
fun sectionMeta(group: MomentGroup, tasks: List<Task>, now: LocalDateTime): String? = when (group) {
    MomentGroup.LATE -> when {
        tasks.all { remindWhens(it).isEmpty() } -> "No moment was set"
        tasks.none { remindWhens(it).isEmpty() } -> "Their time passed"
        else -> null
    }
    MomentGroup.WAKING -> "Next time you wake up"
    MomentGroup.TODAY -> tasks.mapNotNull { t -> remindWhens(t).filterIsInstance<RemindWhen.At>().firstOrNull()?.time }
        .filter { !it.isBefore(now) }.minOrNull()?.let { "Next at ${it.format(HM)}" }
    MomentGroup.PLACES -> null
    MomentGroup.LATER -> tasks.mapNotNull { t -> remindWhens(t).filterIsInstance<RemindWhen.At>().firstOrNull()?.time }
        .minOrNull()?.let { "From ${dayName(it.toLocalDate(), now.toLocalDate())}" }
    MomentGroup.NONE -> "These won't reach you on their own"
}

private val HM: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm")

private fun dayName(d: LocalDate, today: LocalDate): String = when (d) {
    today -> "today"
    today.plusDays(1) -> "tomorrow"
    else -> d.format(DateTimeFormatter.ofPattern(if (d.isBefore(today.plusDays(7))) "EEEE" else "EEE d MMM", Locale.getDefault()))
}

private fun zoneName(z: String) = z.replaceFirstChar { it.titlecase(Locale.getDefault()) }

/** One moment in words, short ("At 18:30", "Arriving at Office", "Getting into the car", "On waking"). */
fun momentPhrase(w: RemindWhen, now: LocalDateTime): String = when (w) {
    RemindWhen.None -> "No moment"
    is RemindWhen.At -> if (w.time.toLocalDate() == now.toLocalDate()) "At ${w.time.format(HM)}" else timeLabel(w.time, now)
    is RemindWhen.Place -> (if (w.leave) "Leaving " else "Arriving at ") + zoneName(w.zone)
    is RemindWhen.Car -> if (w.leave) "Getting out of the car" else "Getting into the car"
    RemindWhen.Wake -> "On waking"
}

/**
 * A row's supporting line: its moments ("Arriving at Office or Mon 08:45"),
 * else its due date, else its list; then how often it was snoozed. [omit]: moments the
 * group already says (On waking).
 */
fun rowLine(task: Task, now: LocalDateTime, listName: String?, omit: Set<RemindWhen> = emptySet()): String {
    val whens = remindWhens(task) - omit
    val main = when {
        whens.isNotEmpty() -> {
            val late = whens.singleOrNull() as? RemindWhen.At
            if (late != null && late.time.isBefore(now)) "Was ${timeLabel(late.time, now).replaceFirstChar { it.lowercase() }}"
            else whens.mapIndexed { i, w -> momentPhrase(w, now).let { p -> if (i == 0 || w is RemindWhen.At) p else p.replaceFirstChar { it.lowercase() } } }.joinToString(" or ")
        }
        task.dueDate != null && parseTriggerTime(task.dueDate) != null -> dueLine(parseTriggerTime(task.dueDate)!!.toLocalDate(), now.toLocalDate())
        else -> listName
    }
    val snoozed = if (task.snoozeCount > 0) "snoozed ${task.snoozeCount}×" else null
    return listOfNotNull(main, snoozed).joinToString(" · ").replaceFirstChar { it.titlecase(Locale.getDefault()) }
}

/** "Due today", "Due yesterday", "Due Wednesday" (within a week), "Due 3 Dec". */
fun dueLine(due: LocalDate, today: LocalDate): String = when {
    due == today -> "Due today"
    due == today.minusDays(1) -> "Due yesterday"
    due == today.plusDays(1) -> "Due tomorrow"
    due.isAfter(today.minusDays(7)) && due.isBefore(today.plusDays(7)) ->
        "Due " + due.format(DateTimeFormatter.ofPattern("EEEE", Locale.getDefault()))
    else -> "Due " + due.format(DateTimeFormatter.ofPattern(if (due.year == today.year) "d MMM" else "d MMM yyyy", Locale.getDefault()))
}

// --- Several moments at once ---------------------------------------------------------------------

/** [w] added to these triggers, replacing the moment of its kind (one time, one place, one car event, waking). */
fun Triggers.with(w: RemindWhen): Triggers = when (w) {
    RemindWhen.None -> Triggers()
    is RemindWhen.At -> copy(time = formatTrigger(w.time))
    is RemindWhen.Place -> copy(location = LocationTrigger(w.zone, if (w.leave) "leave" else "arrive"))
    is RemindWhen.Car -> copy(car = if (w.leave) "leave" else "enter")
    RemindWhen.Wake -> copy(wake = true)
}

/** These triggers without the moment of [w]'s kind. */
fun Triggers.without(w: RemindWhen): Triggers = when (w) {
    RemindWhen.None -> this
    is RemindWhen.At -> copy(time = null)
    is RemindWhen.Place -> copy(location = null)
    is RemindWhen.Car -> copy(car = null)
    RemindWhen.Wake -> copy(wake = null)
}

// --- Dana's suggestion for a reminder snoozed again and again -------------------------------------

/** "Move [from] to [to]?" with the sentence and the button that say so. */
data class MomentSuggestion(val from: RemindWhen, val to: RemindWhen, val text: String, val action: String)

/**
 * For a reminder snoozed [SNOOZE_SUGGEST_MIN] times or more: another moment
 * of the kinds the server has. Arriving somewhere → leaving it; getting into
 * the car → getting out; a time or waking → arriving home (when there is a
 * home place); leaving somewhere → waking up. Null when nothing fits.
 */
fun snoozeSuggestion(task: Task, zones: List<String>): MomentSuggestion? {
    val n = task.snoozeCount
    if (n < SNOOZE_SUGGEST_MIN || !isOpen(task)) return null
    val whens = remindWhens(task)
    val home = zones.firstOrNull { isHomeZone(it) }
    val moved = "You snoozed this $n times."
    for (w in whens) {
        when (w) {
            is RemindWhen.Place -> return if (!w.leave) {
                MomentSuggestion(w, w.copy(leave = true), "$moved Remind you when you leave ${zoneName(w.zone)} instead?", "Move to leaving")
            } else if (whens.none { it == RemindWhen.Wake }) {
                MomentSuggestion(w, RemindWhen.Wake, "$moved Remind you when you wake up instead?", "Move to waking")
            } else null
            is RemindWhen.Car -> if (!w.leave) return MomentSuggestion(w, RemindWhen.Car(leave = true), "$moved Remind you when you get out of the car instead?", "Move to getting out")
            is RemindWhen.At, RemindWhen.Wake -> if (home != null && whens.none { it is RemindWhen.Place }) {
                return MomentSuggestion(w, RemindWhen.Place(home, leave = false), "$moved Remind you when you get home instead?", "Move to arriving home")
            }
            RemindWhen.None -> {}
        }
    }
    return null
}

/** The triggers after taking [s]: its old moment swapped for the new one. */
fun Triggers.applying(s: MomentSuggestion): Triggers = without(s.from).with(s.to)

// --- The board: the next 24 hours as moments ------------------------------------------------------

/** A node on the board's 24-hour line: a time (a drop sets it), or waking. */
sealed class BoardMoment {
    data class At(val time: LocalDateTime) : BoardMoment()
    object Waking : BoardMoment()

    val remindWhen: RemindWhen get() = when (this) {
        is At -> RemindWhen.At(time)
        Waking -> RemindWhen.Wake
    }
}

/** Hours offered on the line when nothing is set then: a time of day a reminder is often wanted. */
private val ANCHOR_HOURS = listOf(9, 12, 15, 18, 20, 22)

/** Where waking sits on the line: before the first anchor of the next morning. */
private const val WAKING_HOUR = 7

/**
 * The 24-hour line after [now]: the next full hour, the anchor hours, every
 * reminder's own time in that window, and waking (the next morning, before
 * the morning's hours), in order. Times are minute-exact and unique.
 */
fun boardMoments(now: LocalDateTime, tasks: List<Task>): List<BoardMoment> {
    val end = now.plusHours(24)
    val nextHour = now.withMinute(0).withSecond(0).withNano(0).plusHours(1)
    val times = sortedSetOf<LocalDateTime>()
    times += nextHour
    for (d in 0..1) for (h in ANCHOR_HOURS) {
        val t = now.toLocalDate().plusDays(d.toLong()).atTime(h, 0)
        if (t.isAfter(nextHour) && !t.isAfter(end)) times += t
    }
    tasks.filter(::isOpen).forEach { t ->
        remindWhens(t).filterIsInstance<RemindWhen.At>().forEach { a ->
            if (!a.time.isBefore(now) && !a.time.isAfter(end)) times += a.time.withSecond(0).withNano(0)
        }
    }
    // Waking: the next morning (today's when it is still night).
    val wakeDay = if (now.hour < WAKING_HOUR) now.toLocalDate() else now.toLocalDate().plusDays(1)
    val wakeAt = wakeDay.atTime(WAKING_HOUR, 0)
    val out = mutableListOf<BoardMoment>()
    var wakePlaced = false
    for (t in times) {
        if (!wakePlaced && !t.isBefore(wakeAt)) { out += BoardMoment.Waking; wakePlaced = true }
        out += BoardMoment.At(t)
    }
    if (!wakePlaced) out += BoardMoment.Waking
    return out
}

/** The reminders a board moment holds: a time node its open reminders set to that minute, waking those that wait for it. */
fun tasksAt(m: BoardMoment, open: List<Task>): List<Task> = open.filter { t ->
    val whens = remindWhens(t)
    when (m) {
        is BoardMoment.At -> whens.any { it is RemindWhen.At && it.time.withSecond(0).withNano(0) == m.time }
        BoardMoment.Waking -> whens.any { it == RemindWhen.Wake }
    }
}

/** The "Anytime" column: arriving at / leaving each saved place, then the car. */
fun anytimeMoments(zones: List<String>): List<RemindWhen> =
    zones.distinctBy { it.lowercase() }.flatMap { listOf(RemindWhen.Place(it, false), RemindWhen.Place(it, true)) } +
        listOf(RemindWhen.Car(false), RemindWhen.Car(true))

/** The open reminders that wait for [w] (a place event or a car event). */
fun tasksFor(w: RemindWhen, open: List<Task>): List<Task> = open.filter { t ->
    remindWhens(t).any { x ->
        when {
            w is RemindWhen.Place && x is RemindWhen.Place -> x.leave == w.leave && x.zone.equals(w.zone, ignoreCase = true)
            else -> x == w
        }
    }
}

/** A board moment's title ("Leave Office", "Get in the car", "On waking", "At a time"). */
fun anytimeTitle(w: RemindWhen): String = when (w) {
    is RemindWhen.Place -> (if (w.leave) "Leave " else "Arrive at ") + zoneName(w.zone)
    is RemindWhen.Car -> if (w.leave) "Get out of the car" else "Get in the car"
    RemindWhen.Wake -> "On waking"
    is RemindWhen.At -> "At a time"
    RemindWhen.None -> "No moment"
}

/** The undo message after a drop ("Set to 18:00", "Set to tomorrow 09:00", "Set to arriving at Office", "Moment cleared"). */
fun dropPhrase(w: RemindWhen, now: LocalDateTime): String = when (w) {
    RemindWhen.None -> "Moment cleared"
    is RemindWhen.At -> "Set to " + if (w.time.toLocalDate() == now.toLocalDate()) w.time.format(HM) else timeLabel(w.time, now).replaceFirstChar { it.lowercase() }
    else -> "Set to " + momentPhrase(w, now).replaceFirstChar { it.lowercase() }
}

/** The moment sheet's line under the title: its list and due date ("Work · due Monday 30 November"). */
fun sheetSubtitle(task: Task, listName: String?): String = listOfNotNull(listName, parseTriggerTime(task.dueDate)?.toLocalDate()?.let {
    "due " + it.format(DateTimeFormatter.ofPattern("EEEE d MMMM", Locale.getDefault()))
}).joinToString(" · ")
