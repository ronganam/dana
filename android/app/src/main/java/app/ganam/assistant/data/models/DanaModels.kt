package app.ganam.assistant.data.models

import org.json.JSONArray
import org.json.JSONObject

/*
 * People, the incoming-call card, the wake briefing and the weekly review
 * (server docs/people.md). Parsed by hand from org.json like the rest of the app.
 */

private fun JSONObject.s(key: String): String? =
    optString(key).takeIf { has(key) && !isNull(key) && it.isNotEmpty() && it != "null" }

private fun JSONArray?.strings(): List<String> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optString(it).takeIf { s -> s.isNotBlank() } }

private inline fun <T> JSONArray?.objects(f: (JSONObject) -> T?): List<T> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it)?.let(f) }

data class PersonStats(
    val calls: Int, val messages: Int, val lastContactAt: String?, val lastContactKind: String?,
    val lastContactAgo: String, val typicalGapDays: Double?, val daysSince: Int?, val drifting: Boolean
) {
    companion object {
        fun from(o: JSONObject?): PersonStats = PersonStats(
            calls = o?.optInt("calls") ?: 0, messages = o?.optInt("messages") ?: 0,
            lastContactAt = o?.s("last_contact_at"), lastContactKind = o?.s("last_contact_kind"),
            lastContactAgo = o?.s("last_contact_ago").orEmpty(),
            typicalGapDays = o?.takeIf { it.has("typical_gap_days") && !it.isNull("typical_gap_days") }?.optDouble("typical_gap_days"),
            daysSince = o?.takeIf { it.has("days_since") && !it.isNull("days_since") }?.optInt("days_since"),
            drifting = o?.optBoolean("drifting") ?: false,
        )
    }
}

data class PersonBrief(val points: List<String>, val open: List<String>, val updatedAt: String?)

data class Person(
    val id: String, val name: String, val initials: String, val relation: String, val phones: List<String>,
    val aliases: List<String>, val notes: String, val birthday: String, val pinned: Boolean, val hidden: Boolean,
    val brief: PersonBrief?, val stats: PersonStats, val openTasks: Int, val birthdayInDays: Int?,
) {
    companion object {
        fun from(o: JSONObject): Person = Person(
            id = o.optString("id"), name = o.optString("name"), initials = o.optString("initials", "?"),
            relation = o.optString("relation"), phones = o.optJSONArray("phones").strings(),
            aliases = o.optJSONArray("aliases").strings(), notes = o.optString("notes"),
            birthday = o.optString("birthday"), pinned = o.optBoolean("pinned"), hidden = o.optBoolean("hidden"),
            brief = o.optJSONObject("brief")?.let { PersonBrief(it.optJSONArray("points").strings(), it.optJSONArray("open").strings(), it.s("updated_at")) },
            stats = PersonStats.from(o.optJSONObject("stats")), openTasks = o.optInt("open_tasks"),
            birthdayInDays = o.takeIf { it.has("birthday_in_days") && !it.isNull("birthday_in_days") }?.optInt("birthday_in_days"),
        )
    }
}

/** One timeline entry: a call, or a day of messages in one chat. */
data class PersonEvent(
    val type: String, val id: String, val at: String, val title: String, val summary: String,
    val direction: String?, val durationSeconds: Int, val app: String, val chat: String, val count: Int, val texts: List<String>,
)

data class PersonTask(val id: String, val title: String, val dueDate: String?)

data class PersonDetail(
    val person: Person, val timeline: List<PersonEvent>, val tasks: List<PersonTask>,
    val suggestions: List<PersonTask>, val briefStale: Boolean,
) {
    companion object {
        fun from(o: JSONObject): PersonDetail = PersonDetail(
            person = Person.from(o),
            timeline = o.optJSONArray("timeline").objects {
                PersonEvent(
                    type = it.optString("type"), id = it.optString("id"), at = it.optString("at"),
                    title = it.optString("title"), summary = it.optString("summary"), direction = it.s("direction"),
                    durationSeconds = it.optInt("duration_seconds"), app = it.optString("app"), chat = it.optString("chat"),
                    count = it.optInt("count"), texts = it.optJSONArray("texts").strings(),
                )
            },
            tasks = o.optJSONArray("tasks").objects { PersonTask(it.optString("id"), it.optString("title"), it.s("due_date")) },
            suggestions = o.optJSONArray("suggestions").objects { PersonTask(it.optString("id"), it.optString("title"), null) },
            briefStale = o.optBoolean("brief_stale"),
        )
    }
}

/** The card shown while someone calls, or before a meeting with them. */
data class PersonCard(
    val personId: String, val name: String, val initials: String, val relation: String, val headline: String,
    val points: List<String>, val open: List<String>, val lastCallTitle: String?, val lastCallSummary: String?,
    val lastCallAgo: String?, val pendingSuggestions: Int, val openTasks: Int, val birthday: String?, val drifting: Boolean,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("person_id", personId).put("name", name).put("initials", initials).put("relation", relation)
        .put("headline", headline).put("points", JSONArray(points)).put("open", JSONArray(open))
        .put("last_call", if (lastCallTitle == null && lastCallSummary == null) JSONObject.NULL else
            JSONObject().put("title", lastCallTitle.orEmpty()).put("summary", lastCallSummary.orEmpty()).put("ago", lastCallAgo.orEmpty()))
        .put("pending_suggestions", pendingSuggestions).put("open_tasks", openTasks)
        .put("birthday", birthday ?: JSONObject.NULL).put("drifting", drifting)

    /** Lines for a notification / the watch. */
    fun summaryLines(): List<String> = buildList {
        addAll(points)
        open.forEach { add("Open: $it") }
        if (lastCallSummary != null && points.isEmpty()) add("Last call: ${lastCallTitle ?: ""} — $lastCallSummary")
        if (birthday != null) add("Birthday $birthday 🎂")
    }

    companion object {
        fun from(o: JSONObject): PersonCard {
            val lc = o.optJSONObject("last_call")
            return PersonCard(
                personId = o.optString("person_id"), name = o.optString("name"), initials = o.optString("initials", "?"),
                relation = o.optString("relation"), headline = o.optString("headline"),
                points = o.optJSONArray("points").strings(), open = o.optJSONArray("open").strings(),
                lastCallTitle = lc?.s("title"), lastCallSummary = lc?.s("summary"), lastCallAgo = lc?.s("ago"),
                pendingSuggestions = o.optInt("pending_suggestions"), openTasks = o.optInt("open_tasks"),
                birthday = o.s("birthday"), drifting = o.optBoolean("drifting"),
            )
        }
    }
}

data class Briefing(
    val date: String, val headline: String, val points: List<String>, val nextEvent: String?,
    val due: Int, val needsYou: Int, val deliveredAt: String?,
) {
    fun toJson(): JSONObject = JSONObject().put("date", date).put("headline", headline).put("points", JSONArray(points))
        .put("next_event", nextEvent ?: JSONObject.NULL).put("due", due).put("needs_you", needsYou)

    companion object {
        /** From GET /api/briefing/today (``briefing`` object) or the ``briefing`` push payload. */
        fun from(o: JSONObject): Briefing {
            val data = o.optJSONObject("data")
            val events = data?.optJSONArray("events")
            val next = o.s("next_event") ?: events?.optJSONObject(0)?.let { "${it.optString("time")} ${it.optString("title")}".trim() }
            val due = if (o.has("due")) o.optInt("due") else
                data?.optJSONObject("tasks")?.optJSONArray("due").objects { t -> t.takeIf { !it.optBoolean("overdue") } }.size
            val needs = if (o.has("needs_you")) o.optInt("needs_you") else data?.optJSONObject("inbox")?.optInt("needs_you") ?: 0
            return Briefing(o.optString("date"), o.optString("headline"), o.optJSONArray("points").strings(), next, due, needs, o.s("delivered_at"))
        }
    }
}

data class ReviewItem(
    val ref: String, val type: String, val title: String, val why: String, val facts: List<String>,
    val action: String, val dueDate: String?, val group: String?, val reason: String, val enabled: Boolean,
) {
    fun toJson(): JSONObject = JSONObject().put("ref", ref).put("action", action).put("enabled", enabled)
        .put("due_date", dueDate ?: JSONObject.NULL).put("group", group ?: JSONObject.NULL)

    companion object {
        val ACTIONS = mapOf(
            "task" to listOf("reschedule", "complete", "move_group", "delete", "keep"),
            "suggestion" to listOf("accept", "dismiss", "keep"),
            "person" to listOf("reach_out", "keep"),
        )
        val LABELS = mapOf(
            "complete" to "Mark done", "reschedule" to "Reschedule", "delete" to "Delete", "move_group" to "Move to group",
            "keep" to "Keep as is", "accept" to "Accept", "dismiss" to "Dismiss", "reach_out" to "Remind me to reach out",
        )
    }
}

data class WeeklyReview(
    val id: String, val week: String, val status: String, val headline: String, val summary: List<String>,
    val groups: List<String>, val items: List<ReviewItem>, val applied: Int?, val failed: Int?,
) {
    companion object {
        fun from(o: JSONObject): WeeklyReview = WeeklyReview(
            id = o.optString("id"), week = o.optString("week"), status = o.optString("status"),
            headline = o.optString("headline"), summary = o.optJSONArray("summary").strings(),
            groups = o.optJSONArray("groups").strings(),
            items = o.optJSONArray("items").objects {
                ReviewItem(
                    ref = it.optString("ref"), type = it.optString("type"), title = it.optString("title"), why = it.optString("why"),
                    facts = it.optJSONArray("facts").strings(), action = it.optString("action", "keep"),
                    dueDate = it.s("due_date"), group = it.s("group"), reason = it.optString("reason"), enabled = it.optBoolean("enabled"),
                )
            },
            applied = o.optJSONObject("result")?.optInt("applied"), failed = o.optJSONObject("result")?.optInt("failed"),
        )
    }
}
