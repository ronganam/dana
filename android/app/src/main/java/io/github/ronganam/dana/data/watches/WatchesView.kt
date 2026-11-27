package io.github.ronganam.dana.data.watches

import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.models.LiveActivity
import io.github.ronganam.dana.data.network.ApiJson
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale

/*
 * What the Watching screens show, worked out from the server's payloads
 * (pure, no Android): a watch's circuit in words, its runs in live
 * activities, today's checks counted and grouped, and the times in rows.
 */

/** What starts a watch, for its source node's glyph. */
enum class SourceKind { Message, Email, Notification, Place, Car, Money, Time, Task, Call, Wake }

/** A watch's circuit: When → Rule → AI check (when it has one) → Then, each in a few words. */
data class Circuit(
    val sourceKind: SourceKind,
    val source: String,
    val rule: String,
    val ai: String?,
    val action: String,
)

private val MESSAGE_APPS = listOf("whatsapp", "telegram", "signal", "messages", "messenger", "sms")

/** The circuit of [spec]: its first trigger (an event trigger first) and first action. */
fun circuitOf(spec: WatchSpec): Circuit {
    val t = spec.triggers.firstOrNull { it.kind == "event" } ?: spec.triggers.firstOrNull()
    val kind = sourceKind(t)
    val app = t?.where?.firstOrNull { it.field == "app" && it.op in setOf("eq", "contains") }?.value
    val source = when {
        t == null -> "Nothing yet"
        t.kind == "time" -> timeWords(t)
        kind == SourceKind.Message && app != null -> "$app message"
        kind == SourceKind.Notification && app != null -> "$app notification"
        else -> sourceWords(t, kind)
    }
    val semantic = spec.triggers.firstNotNullOfOrNull { it.semantic?.takeIf(String::isNotBlank) }
    return Circuit(kind, source, t?.let(::ruleWords) ?: "Any", semantic?.let { shorten(it, 60) }, actionWords(spec.onMatch.firstOrNull()))
}

internal fun sourceKind(t: WatchTrigger?): SourceKind {
    if (t == null || t.kind == "time") return SourceKind.Time
    val type = t.eventType.orEmpty()
    return when {
        type == "phone.notification.received" -> {
            val app = t.where.firstOrNull { it.field == "app" }?.value.orEmpty().lowercase()
            if (MESSAGE_APPS.any { app.contains(it) } || t.where.any { it.field == "conversation" }) SourceKind.Message else SourceKind.Notification
        }
        type.contains("email") -> SourceKind.Email
        type.contains("whatsapp") || type.contains("telegram") -> SourceKind.Message
        type == "core.signal.location" -> SourceKind.Place
        type == "core.signal.car" -> SourceKind.Car
        type.startsWith("money.") -> SourceKind.Money
        type.startsWith("tasks.") -> SourceKind.Task
        type.contains("call") -> SourceKind.Call
        type == "core.user.woke" -> SourceKind.Wake
        else -> SourceKind.Notification
    }
}

private fun sourceWords(t: WatchTrigger, kind: SourceKind): String {
    val cond = { f: String -> t.where.firstOrNull { it.field == f }?.value }
    return when (kind) {
        SourceKind.Place -> {
            val zone = cond("zone") ?: cond("target") ?: "a place"
            when (cond("action")) { "leave" -> "Leaving $zone"; "arrive" -> "Arriving at $zone"; else -> zone }
        }
        SourceKind.Car -> when (cond("action")) { "enter" -> "Getting in the car"; "leave" -> "Leaving the car"; else -> "The car" }
        SourceKind.Money -> when (cond("direction")) { "out" -> "Money out"; "in" -> "Money in"; else -> "A transaction" }
        SourceKind.Email -> "Email"
        SourceKind.Message -> "Message"
        SourceKind.Task -> if (t.eventType.orEmpty().endsWith("completed")) "Task done" else "New task"
        SourceKind.Call -> "Call ended"
        SourceKind.Wake -> "You wake up"
        SourceKind.Notification -> "Notification"
        SourceKind.Time -> timeWords(t)
    }
}

private fun timeWords(t: WatchTrigger): String {
    val at = t.at?.substringAfter(' ')?.take(5)
    return when {
        t.relativeTo != null -> "${kotlin.math.abs(t.offsetMinutes ?: 0)} min ${if ((t.offsetMinutes ?: 0) < 0) "before" else "after"}"
        t.every == "daily" && at != null -> "Every day $at"
        t.every == "weekdays" && at != null -> "Weekdays $at"
        t.every != null && at != null -> "${t.every!!.replaceFirstChar { it.uppercase() }} $at"
        at != null -> "At $at"
        else -> "A time"
    }
}

private val FIELD_WORDS = mapOf(
    "sender" to "From", "from" to "From", "conversation" to "In", "counterparty" to "With", "person" to "With",
    "amount" to "Amount", "text" to "Text", "title" to "Title", "subject" to "Subject", "zone" to "At",
    "account" to "Account", "category" to "Category", "direction" to "Direction", "app" to "App",
    "is_group" to "Group", "duration_s" to "Seconds", "flow" to "Flow", "status" to "Status",
)

/** A field's name for people ("sender" → "From"). */
internal fun fieldWord(field: String): String = FIELD_WORDS[field] ?: field.replace('_', ' ').replaceFirstChar { it.uppercase() }

/** One condition in words (`In “Class 3B parents”`, `Amount over 500`). */
internal fun conditionWords(c: WatchCondition): String {
    val f = fieldWord(c.field)
    val v = c.value.orEmpty()
    val q = "“$v”"
    return when (c.op) {
        "eq" -> if (c.field == "amount") "$f $v" else "$f $q"
        "ne" -> "$f not $q"
        "in", "contains_any" -> "$f: ${v.split('|').filter(String::isNotBlank).joinToString(", ") { "“$it”" }}"
        "contains" -> "$f has $q"
        "not_contains" -> "$f without $q"
        "gt" -> "$f over $v"
        "gte" -> "$f $v or more"
        "lt" -> "$f under $v"
        "lte" -> "$f $v or less"
        "between" -> "$f $v–${c.value2.orEmpty()}"
        "approx" -> "$f about $v"
        "exists" -> "Has ${f.lowercase()}"
        else -> "$f ${c.op} $q"
    }
}

/**
 * The rule node's words: the first condition the source does not already
 * say, else the keywords, else the time of day; "Any" when nothing narrows it.
 */
internal fun ruleWords(t: WatchTrigger): String {
    if (t.kind == "time") return t.every?.let { "Repeats" } ?: "Once"
    val said = setOf("app", "action", "zone", "target", "direction")
    val c = t.where.firstOrNull { it.field !in said }
    return when {
        c != null -> conditionWords(c)
        t.keywords.isNotEmpty() -> "Says " + t.keywords.take(2).joinToString(" or ") { "“$it”" }
        t.during != null -> t.during!!.replace("-", "–")
        else -> "Any"
    }
}

/** The Then node's words. */
internal fun actionWords(a: WatchActionSpec?): String = when (a?.type) {
    null, "" -> "Nothing"
    "notify" -> "Tell you"
    "create_task" -> "Add a reminder"
    "complete_task" -> "Complete the task"
    "suggest_complete" -> "Ask if it's done"
    "tool" -> a.tool?.replace('_', ' ')?.replaceFirstChar { it.uppercase() } ?: "Use a tool"
    "agent" -> a.instructions?.takeIf(String::isNotBlank)?.let { shorten(it, 60) } ?: "Dana acts"
    else -> a.type.replace('_', ' ').replaceFirstChar { it.uppercase() }
}

/** [s] cut at a word to at most [max] characters, with an ellipsis. */
internal fun shorten(s: String, max: Int): String {
    val t = s.trim()
    if (t.length <= max) return t
    val cut = t.take(max - 1)
    val at = cut.lastIndexOf(' ').takeIf { it > max / 2 } ?: cut.length
    return cut.take(at).trimEnd(',', '.', ' ') + "…"
}

// --- Runs in live activities --------------------------------------------------------------

/** A watch's agent run as its live activity shows it: working, or waiting for Approve / Deny. */
data class WatchRun(
    val watchId: String,
    val activityId: String,
    val sessionId: String,
    val waiting: Boolean,
    /** What Dana wants to do (waiting), or its current step ("Using search_calendar"). */
    val text: String,
    val updatedMs: Long?,
)

private val RUN_ID = Regex("^watches:run:(watch-([0-9a-f]+)(?:-\\d+)?):.+$")

/** The runs of watches among [activities] (still running), newest first; one per watch, a waiting one first. */
fun watchRuns(activities: List<LiveActivity>): Map<String, WatchRun> {
    val runs = activities.filter { !it.ended }.mapNotNull { a ->
        val m = RUN_ID.matchEntire(a.id) ?: return@mapNotNull null
        val waiting = a.actions.any { it.id == "approve" }
        WatchRun(m.groupValues[2], a.id, m.groupValues[1], waiting, a.look().text, a.updatedMs)
    }
    return runs.groupBy { it.watchId }.mapValues { (_, rs) -> rs.sortedWith(compareByDescending<WatchRun> { it.waiting }.thenByDescending { it.updatedMs ?: 0 }).first() }
}

/** One step of a run, in words, and where it is. */
data class RunStep(val label: String, val state: RunStepState)

enum class RunStepState { Done, Current, Waiting, Failed }

/**
 * A run's steps from its session: the tool calls after the last user turn
 * (the watch's message), done or failed, then what it does now: the live
 * step ("Using x" → current) or, [waiting], the approval it waits for.
 */
fun runSteps(messages: List<ChatMessage>, run: WatchRun): List<RunStep> {
    val lastUser = messages.indexOfLast { it.isUser }
    val tools = messages.drop(lastUser + 1).mapNotNull { it.toolCall }
    val steps = tools.filter { !it.requiresApproval || it.succeeded != null }.map { tc ->
        val label = tc.toolSummary.takeIf { it.isNotBlank() && it.length <= 40 } ?: toolWords(tc.toolName)
        RunStep(label, when { tc.isRunning || tc.succeeded == null -> RunStepState.Current; tc.succeeded == false -> RunStepState.Failed; else -> RunStepState.Done })
    }.toMutableList()
    if (run.waiting) {
        steps.removeAll { it.state == RunStepState.Current }
        steps += RunStep("Your OK", RunStepState.Waiting)
    } else if (steps.none { it.state == RunStepState.Current }) {
        val now = run.text.removePrefix("Using ").trim()
        if (now.isNotBlank() && now != "Starting…") steps += RunStep(toolWords(now), RunStepState.Current)
        else steps += RunStep("Starting", RunStepState.Current)
    }
    return steps.takeLast(5)
}

/** A tool's name in words (`search_calendar` → "Search calendar"). */
internal fun toolWords(name: String): String = name.replace('_', ' ').trim().replaceFirstChar { it.uppercase() }

// --- Today's checks --------------------------------------------------------------------------

/** Today's checks of one watch, counted along its circuit. */
data class TodayCounts(
    /** Events it looked at (the stored checks plus those counted over the hourly cap). */
    val checks: Int,
    /** Got past the rule. */
    val passedRule: Int,
    /** The AI check said no. */
    val aiNo: Int,
    val fired: Int,
)

private val PASSED = setOf("semantic_yes", "semantic_no", "semantic_failed", "fired", "cooldown", "skipped")

/** How many checks an `unstored` row stands for ("41 more checks not stored …"). */
internal fun unstoredCount(row: TraceRow): Int = row.summary.trim().takeWhile { it.isDigit() }.toIntOrNull() ?: 0

/** The rows of [trace] from the start of [today] on. */
fun todays(trace: List<TraceRow>, today: LocalDate, zone: ZoneId): List<TraceRow> {
    val start = today.atStartOfDay(zone).toInstant().toEpochMilli()
    return trace.filter { (it.atMs ?: 0) >= start }
}

/** [rows] counted (pass today's rows). */
fun countChecks(rows: List<TraceRow>): TodayCounts = TodayCounts(
    checks = rows.sumOf { if (it.outcome == "unstored") unstoredCount(it) else 1 },
    passedRule = rows.count { it.outcome in PASSED },
    aiNo = rows.count { it.outcome == "semantic_no" },
    fired = rows.count { it.outcome == "fired" },
)

/** The groups of "Recent checks", in the order they show. */
enum class CheckGroup { Fired, AiNo, RuleNo, Other }

/** [rows] grouped by outcome (each group newest first, empty groups left out). */
fun groupChecks(rows: List<TraceRow>): Map<CheckGroup, List<TraceRow>> {
    val out = linkedMapOf<CheckGroup, MutableList<TraceRow>>()
    for (g in CheckGroup.entries) out[g] = mutableListOf()
    for (r in rows.sortedByDescending { it.atMs ?: 0 }) {
        val g = when (r.outcome) {
            "fired" -> CheckGroup.Fired
            "semantic_no" -> CheckGroup.AiNo
            "drop", "during", "outside_window", "unstored" -> CheckGroup.RuleNo
            else -> CheckGroup.Other
        }
        out.getValue(g) += r
    }
    return out.filterValues { it.isNotEmpty() }
}

/** A check's group total ("unstored" rows count for what they stand for). */
fun groupCount(rows: List<TraceRow>): Int = rows.sumOf { if (it.outcome == "unstored") unstoredCount(it) else 1 }

private val COND = Regex("^(\\w+) (\\w+)(?: \"(.*)\")? ← \"(.*)\"$")
private val DURING = Regex("^during (\\S+) ← (\\d\\d:\\d\\d)$")
private val KEYWORDS = Regex("^keywords \"(.*)\" not in the text$")

/**
 * A check's reason in plain words. The engine says which condition failed
 * and the value it saw (`app eq "WhatsApp" ← "Gmail"`); the AI check's
 * reason is already words.
 */
fun reasonWords(row: TraceRow): String {
    val r = row.reason.trim()
    COND.matchEntire(r)?.let { m ->
        val (field, op, value, seen) = m.destructured
        val f = fieldWord(field)
        return when {
            op == "exists" -> "No ${f.lowercase()}"
            seen.isBlank() || seen == "None" -> "$f was empty"
            op == "eq" || op == "in" -> "$f was “$seen”"
            op in setOf("gt", "gte", "lt", "lte", "between", "approx") -> "$f was $seen"
            op == "contains" || op == "contains_any" -> "$f had no ${value.split('|').joinToString(" or ") { "“$it”" }}"
            op == "not_contains" -> "$f had “$value”"
            else -> "$f was “$seen”"
        }
    }
    DURING.matchEntire(r)?.let { m -> return "At ${m.groupValues[2]}, outside ${m.groupValues[1].replace("-", "–")}" }
    KEYWORDS.matchEntire(r)?.let { m -> return "No ${m.groupValues[1].split('|').joinToString(" or ") { "“$it”" }} in it" }
    return when {
        row.outcome == "outside_window" -> "Outside the watch's dates"
        row.outcome == "unstored" -> "Far misses, not kept"
        row.outcome == "cooldown" -> "Fired for this a moment ago"
        row.outcome == "semantic_failed" -> "The AI check did not answer"
        r == "same as an earlier fire" -> "Same as an earlier fire"
        r.isBlank() -> ""
        else -> r.replaceFirstChar { it.uppercase() }
    }
}

// --- A day's track ------------------------------------------------------------------------

/** One mark on a watch's track of today's events. */
enum class MarkKind { Dropped, AiNo, Fired, Held }

data class TrackMark(val atMs: Long, val kind: MarkKind)

/**
 * Today's [rows] as marks: rule drops, AI noes and fires; a fire whose run
 * waits for the owner ([heldSince] at or before it) is held.
 */
fun trackMarks(rows: List<TraceRow>, heldSince: Long? = null): List<TrackMark> = rows.mapNotNull { r ->
    val at = r.atMs ?: return@mapNotNull null
    val kind = when (r.outcome) {
        "fired" -> if (heldSince != null && at >= heldSince - 60_000) MarkKind.Held else MarkKind.Fired
        "semantic_no", "semantic_failed" -> MarkKind.AiNo
        "unstored" -> null
        else -> MarkKind.Dropped
    }
    kind?.let { TrackMark(at, it) }
}.sortedBy { it.atMs }

/** Where a track starts: 06:00, or midnight when something happened before 06:00. */
fun trackStart(marks: List<TrackMark>, today: LocalDate, zone: ZoneId): Long {
    val six = today.atTime(6, 0).atZone(zone).toInstant().toEpochMilli()
    return if (marks.any { it.atMs < six }) today.atStartOfDay(zone).toInstant().toEpochMilli() else six
}

// --- Words for the list --------------------------------------------------------------------

private val HM = DateTimeFormatter.ofPattern("HH:mm", Locale.ENGLISH)
private val DAY = DateTimeFormatter.ofPattern("EEE", Locale.ENGLISH)
private val DATE = DateTimeFormatter.ofPattern("d MMM", Locale.ENGLISH)

/** A time in a row: "08:31" today, "Tue" this week, "9 Nov" before. */
fun whenWords(ms: Long, now: ZonedDateTime): String {
    val t = Instant.ofEpochMilli(ms).atZone(now.zone)
    val days = ChronoUnit.DAYS.between(t.toLocalDate(), now.toLocalDate())
    return when {
        days == 0L -> HM.format(t)
        days in 1..6 -> DAY.format(t)
        else -> DATE.format(t)
    }
}

/** A clock time ("08:31"). */
fun clockWords(ms: Long, zone: ZoneId): String = HM.format(Instant.ofEpochMilli(ms).atZone(zone))

/** "On since 12 Nov. Fired 4 times." */
fun sinceWords(w: Watch, zone: ZoneId): String {
    val since = w.createdMs?.let { DATE.format(Instant.ofEpochMilli(it).atZone(zone)) }
    val state = when { w.isPaused -> "Paused"; w.isClosed -> if (w.status == "done") "Done" else "Ended"; else -> "On" }
    val head = if (since != null) "$state since $since." else "$state."
    val fired = when (w.firesCount) { 0 -> "Not fired yet."; 1 -> "Fired once."; else -> "Fired ${w.firesCount} times." }
    return "$head $fired"
}

/** The list's line under its title: "6 on, 1 paused. One needs you." */
fun summaryWords(watches: List<Watch>, needsYou: Int): String {
    val on = watches.count { it.isActive }
    val paused = watches.count { it.isPaused }
    val parts = buildList {
        add("$on on")
        if (paused > 0) add("$paused paused")
    }.joinToString(", ") + "."
    val needs = when (needsYou) { 0 -> ""; 1 -> " One needs you."; else -> " $needsYou need you." }
    return parts + needs
}

private val DRAFT_KEYS = listOf("text", "message", "body", "content", "reply")

/** The words a waiting tool call would send (its `text` / `message` / `body` argument); null when it has none. */
fun draftOf(toolArgs: String?): String? {
    if (toolArgs.isNullOrBlank()) return null
    val obj = runCatching { ApiJson.parseToJsonElement(toolArgs) as? JsonObject }.getOrNull() ?: return null
    return DRAFT_KEYS.firstNotNullOfOrNull { k -> (obj[k] as? JsonPrimitive)?.takeIf { it.isString }?.content?.takeIf(String::isNotBlank) }
}

/** A watch's quiet hours as their start ("From 23:00") when they are not now; null when it has none or they are now. */
fun quietUntil(spec: WatchSpec, now: ZonedDateTime): String? {
    val during = spec.triggers.firstNotNullOfOrNull { it.during } ?: return null
    val parts = during.split("-")
    if (parts.size != 2) return null
    val start = runCatching { java.time.LocalTime.parse(parts[0].trim()) }.getOrNull() ?: return null
    val end = runCatching { java.time.LocalTime.parse(parts[1].trim()) }.getOrNull() ?: return null
    val t = now.toLocalTime()
    val inside = if (start <= end) t >= start && t < end else t >= start || t < end
    return if (inside) null else "From ${HM.format(start)}"
}
