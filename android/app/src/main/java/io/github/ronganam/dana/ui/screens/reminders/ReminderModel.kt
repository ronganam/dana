package io.github.ronganam.dana.ui.screens.reminders

import io.github.ronganam.dana.data.models.Task
import io.github.ronganam.dana.data.models.LocationTrigger
import io.github.ronganam.dana.data.models.TaskGroup
import io.github.ronganam.dana.data.models.Triggers
import io.github.ronganam.dana.ui.shell.DaySlot
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.TemporalAdjusters
import java.util.Locale

/*
 * Plain logic behind the Reminders screen, the reminder editor and the day
 * strip: what a task's triggers mean, which bucket it falls in, and the one
 * sentence that says what Dana will do. No Compose here (unit-tested in
 * ReminderModelTest).
 */

/** The single "Remind me" choice of the editor. A task can carry several triggers; the first wins here. */
sealed class RemindWhen {
    object None : RemindWhen()
    data class At(val time: LocalDateTime) : RemindWhen()
    data class Place(val zone: String, val leave: Boolean) : RemindWhen()
    data class Car(val leave: Boolean) : RemindWhen()
    object Wake : RemindWhen()

    /** The `triggers` object for the task API (replaces every trigger the task had). */
    fun toTriggers(): Triggers = when (this) {
        None -> Triggers()
        is At -> Triggers(time = formatTrigger(time))
        is Place -> Triggers(location = LocationTrigger(zone, if (leave) "leave" else "arrive"))
        is Car -> Triggers(car = if (leave) "leave" else "enter")
        Wake -> Triggers(wake = true)
    }

    val kind: String get() = when (this) {
        None -> "none"; is At -> "time"; is Place -> "place"; is Car -> "car"; Wake -> "wake"
    }
}

enum class Bucket(val label: String) {
    OVERDUE("Overdue"), TODAY("Today"), WHEN_THERE("When I get there"), LATER("Later"), SOMEDAY("Someday")
}

private val TRIGGER_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:00")

/** Naive local time, the format the server and the old editor use ("2026-11-14T18:30:00"). */
fun formatTrigger(t: LocalDateTime): String = t.format(TRIGGER_FORMAT)

/**
 * A trigger or due time as local wall-clock time. Accepts naive values
 * ("…T18:30", "… 18:30:00") and ones with an offset (snoozes are stored as
 * "…+02:00"), which are converted to this phone's zone.
 */
fun parseTriggerTime(raw: String?): LocalDateTime? {
    val s = raw?.trim()?.replace(' ', 'T')?.takeIf { it.isNotEmpty() && it != "null" } ?: return null
    runCatching { return OffsetDateTime.parse(s).atZoneSameInstant(ZoneId.systemDefault()).toLocalDateTime() }
    runCatching { return LocalDateTime.parse(s.take(19)) }
    runCatching { return LocalDateTime.parse(s.take(16)) }
    runCatching { return LocalDate.parse(s.take(10)).atStartOfDay() }
    return null
}

/** Every trigger the task has, in the editor's order (time, place, car, wake). */
fun remindWhens(task: Task): List<RemindWhen> {
    val t = task.triggers
    val out = mutableListOf<RemindWhen>()
    parseTriggerTime(t.time)?.let { out += RemindWhen.At(it) }
    t.location?.let { loc ->
        loc.location?.takeIf { it.isNotBlank() }?.let { out += RemindWhen.Place(it, loc.event == "leave") }
    }
    t.car?.takeIf { it.isNotBlank() && it != "null" }?.let { out += RemindWhen.Car(it == "leave") }
    if (t.wake == true) out += RemindWhen.Wake
    return out
}

fun remindWhen(task: Task): RemindWhen = remindWhens(task).firstOrNull() ?: RemindWhen.None

fun isHomeZone(name: String?): Boolean = name?.trim()?.equals("home", ignoreCase = true) == true

/** "Home" as the server spells it (zones come from Home Assistant), for the "At home" slot. */
fun homeZoneName(zones: List<String>): String = zones.firstOrNull { isHomeZone(it) } ?: "Home"

private fun zoneLabel(z: String) = z.replaceFirstChar { it.titlecase(Locale.getDefault()) }

/** "Today 18:30", "Tomorrow 09:00", "Thu 18:00" (this week), "16 Nov 10:00". */
fun timeLabel(t: LocalDateTime, now: LocalDateTime): String {
    val day = t.toLocalDate()
    val today = now.toLocalDate()
    val hm = t.format(DateTimeFormatter.ofPattern("HH:mm"))
    return when {
        day == today -> "Today $hm"
        day == today.plusDays(1) -> "Tomorrow $hm"
        day == today.minusDays(1) -> "Yesterday $hm"
        day.isAfter(today) && day.isBefore(today.plusDays(7)) ->
            "${t.format(DateTimeFormatter.ofPattern("EEE", Locale.getDefault()))} $hm"
        day.year == today.year -> "${t.format(DateTimeFormatter.ofPattern("d MMM", Locale.getDefault()))} $hm"
        else -> "${t.format(DateTimeFormatter.ofPattern("d MMM yyyy", Locale.getDefault()))} $hm"
    }
}

/** The short mono label of one trigger ("Today 18:30", "At Home", "In the car", "On waking"). */
fun whenLabel(w: RemindWhen, now: LocalDateTime): String = when (w) {
    RemindWhen.None -> "No reminder set"
    is RemindWhen.At -> timeLabel(w.time, now)
    is RemindWhen.Place -> (if (w.leave) "Leaving " else "At ") + zoneLabel(w.zone)
    is RemindWhen.Car -> if (w.leave) "Getting out of the car" else "In the car"
    RemindWhen.Wake -> "On waking"
}

/** The row's mono label: its first trigger, else its due date, else "No reminder set". */
fun taskWhenText(task: Task, now: LocalDateTime): String {
    val w = remindWhen(task)
    if (w != RemindWhen.None) return whenLabel(w, now)
    val due = parseTriggerTime(task.dueDate)?.toLocalDate() ?: return "No reminder set"
    val today = now.toLocalDate()
    return when (due) {
        today -> "Due today"
        today.plusDays(1) -> "Due tomorrow"
        else -> "Due " + due.format(DateTimeFormatter.ofPattern(if (due.year == today.year) "EEE d MMM" else "d MMM yyyy", Locale.getDefault()))
    }
}

/**
 * Overdue / Today / When I get there / Later / Someday:
 * a time trigger decides by its time; otherwise a place, car or wake trigger
 * means "when I get there"; otherwise the due date decides; nothing at all is Someday.
 */
fun bucketOf(task: Task, now: LocalDateTime): Bucket {
    val whens = remindWhens(task)
    val at = whens.filterIsInstance<RemindWhen.At>().firstOrNull()
    if (at != null) return dayBucket(at.time, now, timed = true)
    if (whens.isNotEmpty()) return Bucket.WHEN_THERE
    val due = parseTriggerTime(task.dueDate) ?: return Bucket.SOMEDAY
    return dayBucket(due, now, timed = false)
}

private fun dayBucket(t: LocalDateTime, now: LocalDateTime, timed: Boolean): Bucket {
    val day = t.toLocalDate()
    val today = now.toLocalDate()
    return when {
        day.isBefore(today) -> Bucket.OVERDUE
        timed && t.isBefore(now) -> Bucket.OVERDUE
        day == today -> Bucket.TODAY
        else -> Bucket.LATER
    }
}

fun isOpen(task: Task) = task.status != "completed"

data class ReminderSection(val key: String, val title: String, val tasks: List<Task>)

/** "By when": the five buckets in order, each sorted by time (untimed by title), empty ones dropped. */
fun sectionsByWhen(open: List<Task>, now: LocalDateTime): List<ReminderSection> {
    val byBucket = open.groupBy { bucketOf(it, now) }
    return Bucket.values().mapNotNull { b ->
        val list = byBucket[b].orEmpty()
        if (list.isEmpty()) null else ReminderSection(b.name, b.label, list.sortedWith(timeOrder()))
    }
}

/** "By list": one section per task group, in the groups' order; tasks without a known group go to "General". */
fun sectionsByList(open: List<Task>, groups: List<TaskGroup>): List<ReminderSection> {
    val known = groups.associateBy { it.id }
    val byGroup = open.groupBy { t -> t.groupId?.takeIf { it in known } }
    val ordered = groups.sortedBy { it.sortOrder }.mapNotNull { g ->
        byGroup[g.id]?.let { ReminderSection(g.id, g.name, it.sortedWith(timeOrder())) }
    }
    val rest = byGroup[null]?.let { list ->
        // Unknown group ids (groups not loaded yet): fall back to the name the task carries.
        list.groupBy { it.groupName ?: "General" }.map { (name, l) -> ReminderSection("name:$name", name, l.sortedWith(timeOrder())) }
    }.orEmpty()
    return ordered + rest
}

private fun timeOrder(): Comparator<Task> = compareBy<Task>(
    { t -> remindWhens(t).filterIsInstance<RemindWhen.At>().firstOrNull()?.time ?: parseTriggerTime(t.dueDate) ?: LocalDateTime.MAX },
    { it.title.lowercase() }
)

/** Completed reminders, most recently changed first. */
fun doneRecently(tasks: List<Task>, limit: Int = 20): List<Task> =
    tasks.filter { !isOpen(it) }.sortedByDescending { it.updatedAt ?: it.createdAt ?: "" }.take(limit)

/** The plain sentence under "Remind me". */
fun sentence(task: Task, now: LocalDateTime): String {
    val whens = remindWhens(task)
    if (whens.isEmpty()) {
        val due = parseTriggerTime(task.dueDate)?.toLocalDate()
            ?: return "Dana will not remind you. It waits under Someday."
        val day = due.format(DateTimeFormatter.ofPattern("EEE d MMM", Locale.getDefault()))
        return if (due.isBefore(now.toLocalDate())) "Dana will not remind you. It was due $day." else "Dana will not remind you. It is due $day."
    }
    val parts = whens.map { w ->
        when (w) {
            is RemindWhen.At -> timePhrase(w.time, now)
            is RemindWhen.Place -> "when you ${if (w.leave) "leave" else "arrive at"} ${zoneLabel(w.zone)}"
            is RemindWhen.Car -> "when you ${if (w.leave) "get out of" else "get into"} the car"
            RemindWhen.Wake -> "once you are up in the morning"
            RemindWhen.None -> ""
        }
    }
    val past = whens.filterIsInstance<RemindWhen.At>().any { it.time.isBefore(now) }
    return "Dana reminds you ${parts.joinToString(", or ")}." + if (past) " That time has passed." else ""
}

/** "today at 18:30", "tomorrow at 09:00", "on Monday at 14:00", "on 16 Dec at 14:00". */
fun timePhrase(t: LocalDateTime, now: LocalDateTime): String {
    val day = t.toLocalDate()
    val today = now.toLocalDate()
    val hm = t.format(DateTimeFormatter.ofPattern("HH:mm"))
    val loc = Locale.getDefault()
    return when {
        day == today -> "today at $hm"
        day == today.plusDays(1) -> "tomorrow at $hm"
        day == today.minusDays(1) -> "yesterday at $hm"
        day.isAfter(today) && day.isBefore(today.plusDays(7)) -> "on ${t.format(DateTimeFormatter.ofPattern("EEEE", loc))} at $hm"
        day.year == today.year -> "on ${t.format(DateTimeFormatter.ofPattern("d MMM", loc))} at $hm"
        else -> "on ${t.format(DateTimeFormatter.ofPattern("d MMM yyyy", loc))} at $hm"
    }
}

/** Quick times offered under "At a time": tonight 20:00 (while it is still ahead), tomorrow 09:00, Saturday 10:00. */
fun quickTimes(now: LocalDateTime): List<Pair<String, LocalDateTime>> {
    val today = now.toLocalDate()
    val out = mutableListOf<Pair<String, LocalDateTime>>()
    val tonight = today.atTime(20, 0)
    if (now.isBefore(tonight.minusMinutes(30))) out += "Tonight 20:00" to tonight
    out += "Tomorrow 09:00" to today.plusDays(1).atTime(9, 0)
    val saturday = today.with(TemporalAdjusters.next(DayOfWeek.SATURDAY)).let { if (it == today.plusDays(1)) it.plusWeeks(1) else it }
    out += "${saturday.format(DateTimeFormatter.ofPattern("EEEE", Locale.getDefault()))} 10:00" to saturday.atTime(10, 0)
    return out
}

/** What "At a time" starts with when picked from another choice. */
fun defaultTime(now: LocalDateTime): LocalDateTime = quickTimes(now).first().second

/** The day-strip slot a reminder fires in today, if any. */
fun slotOf(task: Task, now: LocalDateTime): DaySlot? {
    if (!isOpen(task)) return null
    for (w in remindWhens(task)) {
        when (w) {
            is RemindWhen.At -> if (w.time.toLocalDate() == now.toLocalDate() && !w.time.isBefore(now.withMinute(0).withSecond(0).withNano(0))) return DaySlot.Hour(w.time.hour)
            is RemindWhen.Place -> if (!w.leave && isHomeZone(w.zone)) return DaySlot.AtHome
            RemindWhen.Wake -> return DaySlot.OnWaking
            else -> {}
        }
    }
    return null
}

/** The trigger a drop on [slot] sets. Hours are today (tomorrow if that hour has already gone). */
fun remindWhenFor(slot: DaySlot, now: LocalDateTime, zones: List<String>): RemindWhen = when (slot) {
    is DaySlot.Hour -> {
        val t = now.toLocalDate().atTime(slot.hour, 0)
        RemindWhen.At(if (t.isBefore(now.withMinute(0).withSecond(0).withNano(0))) t.plusDays(1) else t)
    }
    DaySlot.AtHome -> RemindWhen.Place(homeZoneName(zones), leave = false)
    DaySlot.OnWaking -> RemindWhen.Wake
}

/** "Moved to 21:00" / "Moved to when you get home" / "Moved to when you wake". */
fun slotPhrase(slot: DaySlot): String = when (slot) {
    is DaySlot.Hour -> "%02d:00".format(slot.hour)
    DaySlot.AtHome -> "when you get home"
    DaySlot.OnWaking -> "when you wake"
}
