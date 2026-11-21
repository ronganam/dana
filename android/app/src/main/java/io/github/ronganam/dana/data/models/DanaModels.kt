package io.github.ronganam.dana.data.models

import io.github.ronganam.dana.data.network.LenientList
import io.github.ronganam.dana.data.network.OptString
import io.github.ronganam.dana.data.network.TextList
import kotlinx.serialization.Serializable

/*
 * People, the incoming-call card, the wake briefing and the weekly review
 * (server docs/people.md). Read with ApiJson (data/network/PeopleApi.kt).
 */

@Serializable
data class PersonStats(
    val calls: Int = 0,
    val messages: Int = 0,
    val lastContactAt: OptString = null,
    val lastContactKind: OptString = null,
    val lastContactAgo: String = "",
    val typicalGapDays: Double? = null,
    val daysSince: Int? = null,
    val drifting: Boolean = false
)

@Serializable
data class PersonBrief(val points: TextList = emptyList(), val open: TextList = emptyList(), val updatedAt: OptString = null)

@Serializable
data class Person(
    val id: String = "",
    val name: String = "",
    val initials: String = "?",
    val relation: String = "",
    val phones: TextList = emptyList(),
    val aliases: TextList = emptyList(),
    val notes: String = "",
    val birthday: String = "",
    val pinned: Boolean = false,
    val hidden: Boolean = false,
    val brief: PersonBrief? = null,
    val stats: PersonStats = PersonStats(),
    val openTasks: Int = 0,
    val birthdayInDays: Int? = null,
)

/** One timeline entry: a call, or a day of messages in one chat. */
@Serializable
data class PersonEvent(
    val type: String = "", val id: String = "", val at: String = "", val title: String = "", val summary: String = "",
    val direction: OptString = null, val durationSeconds: Int = 0, val app: String = "", val chat: String = "", val count: Int = 0,
    val texts: TextList = emptyList(),
)

@Serializable
data class PersonTask(val id: String = "", val title: String = "", val dueDate: OptString = null)

data class PersonDetail(
    val person: Person, val timeline: List<PersonEvent>, val tasks: List<PersonTask>,
    val suggestions: List<PersonTask>, val briefStale: Boolean,
)

/** The card's last call (`last_call`). */
@Serializable
data class LastCall(val title: OptString = null, val summary: OptString = null, val ago: OptString = null)

/** The card shown while someone calls, or before a meeting with them (also kept as is in the caller-card cache). */
@Serializable
data class PersonCard(
    val personId: String = "", val name: String = "", val initials: String = "?", val relation: String = "", val headline: String = "",
    val points: TextList = emptyList(), val open: TextList = emptyList(), val lastCall: LastCall? = null,
    val pendingSuggestions: Int = 0, val openTasks: Int = 0, val birthday: OptString = null, val drifting: Boolean = false,
) {
    val lastCallTitle: String? get() = lastCall?.title
    val lastCallSummary: String? get() = lastCall?.summary
    val lastCallAgo: String? get() = lastCall?.ago

    /** Lines for a notification / the watch. */
    fun summaryLines(): List<String> = buildList {
        addAll(points)
        open.forEach { add("Open: $it") }
        if (lastCallSummary != null && points.isEmpty()) add("Last call: ${lastCallTitle ?: ""} — $lastCallSummary")
        if (birthday != null) add("Birthday $birthday 🎂")
    }
}

/** Today's wake briefing (kept as is for the widgets). */
@Serializable
data class Briefing(
    val date: String = "", val headline: String = "", val points: TextList = emptyList(), val nextEvent: OptString = null,
    val due: Int = 0, val needsYou: Int = 0, val deliveredAt: OptString = null,
)

@Serializable
data class ReviewItem(
    val ref: String = "", val type: String = "", val title: String = "", val why: String = "", val facts: TextList = emptyList(),
    val action: String = "keep", val dueDate: OptString = null, val group: OptString = null, val reason: String = "",
    val enabled: Boolean = false,
) {
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

@Serializable
data class ReviewResult(val applied: Int? = null, val failed: Int? = null)

@Serializable
data class WeeklyReview(
    val id: String = "", val week: String = "", val status: String = "", val headline: String = "", val summary: TextList = emptyList(),
    val groups: TextList = emptyList(), val items: LenientList<ReviewItem> = emptyList(), val result: ReviewResult? = null,
) {
    val applied: Int? get() = result?.applied
    val failed: Int? get() = result?.failed
}
