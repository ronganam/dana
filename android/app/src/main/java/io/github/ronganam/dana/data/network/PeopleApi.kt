package io.github.ronganam.dana.data.network

import io.github.ronganam.dana.data.models.Briefing
import io.github.ronganam.dana.data.models.Person
import io.github.ronganam.dana.data.models.PersonCard
import io.github.ronganam.dana.data.models.PersonDetail
import io.github.ronganam.dana.data.models.PersonEvent
import io.github.ronganam.dana.data.models.PersonTask
import io.github.ronganam.dana.data.models.ReviewItem
import io.github.ronganam.dana.data.models.ReviewResult
import io.github.ronganam.dana.data.models.WeeklyReview
import io.github.ronganam.dana.data.network.ApiClient.Companion.enc
import io.github.ronganam.dana.data.network.ApiClient.Companion.jsonBody
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray

/** Changes to a person (`PUT /api/people/{id}`): null = leave as is. */
@Serializable
data class PersonChanges(
    val name: String? = null,
    val relation: String? = null,
    val notes: String? = null,
    val birthday: String? = null,
    val pinned: Boolean? = null,
    val hidden: Boolean? = null,
    val phones: List<String>? = null,
    val aliases: List<String>? = null
)

@Serializable
private data class PeopleWire(val people: LenientList<Person> = emptyList())

@Serializable
private data class PersonDetailWire(
    val timeline: LenientList<PersonEvent> = emptyList(),
    val tasks: LenientList<PersonTask> = emptyList(),
    val suggestions: LenientList<PersonTask> = emptyList(),
    val briefStale: Boolean = false
)

@Serializable
private data class LookupWire(val found: Boolean = false, val card: PersonCard? = null)

@Serializable
private data class BriefingEventWire(val time: String = "", val title: String = "")

@Serializable
private data class BriefingTaskWire(val overdue: Boolean = false)

@Serializable
private data class BriefingTasksWire(val due: LenientList<BriefingTaskWire> = emptyList())

@Serializable
private data class BriefingInboxWire(val needsYou: Int = 0)

@Serializable
private data class BriefingDataWire(
    val events: LenientList<BriefingEventWire> = emptyList(),
    val tasks: BriefingTasksWire? = null,
    val inbox: BriefingInboxWire? = null
)

/** A briefing from `GET /api/briefing/today` (`briefing`), the `briefing` push, or the widgets' copy. */
@Serializable
private data class BriefingWire(
    val date: String = "",
    val headline: String = "",
    val points: TextList = emptyList(),
    val nextEvent: OptString = null,
    val due: Int? = null,
    val needsYou: Int? = null,
    val deliveredAt: OptString = null,
    val data: BriefingDataWire? = null
) {
    fun toModel() = Briefing(
        date = date,
        headline = headline,
        points = points,
        nextEvent = nextEvent ?: data?.events?.firstOrNull()?.let { "${it.time} ${it.title}".trim() },
        due = due ?: data?.tasks?.due.orEmpty().count { !it.overdue },
        needsYou = needsYou ?: data?.inbox?.needsYou ?: 0,
        deliveredAt = deliveredAt
    )
}

@Serializable
private data class BriefingAnswer(val briefing: BriefingWire? = null)

@Serializable
private data class ReviewAnswer(val review: WeeklyReview? = null)

/** People, the caller card, the wake briefing and the weekly review (server docs/people.md). */
class PeopleApi(private val http: ApiClient) {

    suspend fun people(sort: String = "recent", query: String = ""): List<Person> =
        http.decode(PeopleWire.serializer(), http.text(http.request("/api/people?sort=${enc(sort)}&q=${enc(query)}"), "Failed to load people", "{}")).people

    suspend fun person(id: String): PersonDetail {
        val text = http.text(http.request("/api/people/${enc(id)}"), "Failed to load person", "{}")
        val w = http.decode(PersonDetailWire.serializer(), text)
        return PersonDetail(http.decode(Person.serializer(), text), w.timeline, w.tasks, w.suggestions.map { it.copy(dueDate = null) }, w.briefStale)
    }

    suspend fun update(id: String, changes: PersonChanges): Boolean =
        http.send(http.request("/api/people/${enc(id)}") { put(jsonBody(PersonChanges.serializer(), changes)) })

    suspend fun refreshBrief(id: String): Boolean =
        http.send(http.request("/api/people/${enc(id)}/brief") { post(jsonBody(JsonObject(emptyMap()))) })

    /** The incoming-call card for [phone], or null when the caller isn't known (no model call on the server). */
    suspend fun lookupCaller(phone: String): PersonCard? {
        val w = http.decode(LookupWire.serializer(), http.text(http.request("/api/people/lookup?phone=${enc(phone)}"), "Lookup failed", "{}"))
        return w.card?.takeIf { w.found }
    }

    /** Today's wake briefing; [generate] makes it now when there's none yet. */
    suspend fun briefingToday(generate: Boolean = false): Briefing? =
        http.decode(BriefingAnswer.serializer(),
            http.text(http.request("/api/briefing/today${if (generate) "?generate=1" else ""}"), "Failed to load the briefing", "{}")).briefing?.toModel()

    suspend fun latestReview(): WeeklyReview? =
        http.decode(ReviewAnswer.serializer(), http.text(http.request("/api/review/latest"), "Failed to load the review", "{}")).review

    suspend fun generateReview(): WeeklyReview? =
        http.decode(ReviewAnswer.serializer(),
            http.text(http.request("/api/review/generate") { post(jsonBody(JsonObject(emptyMap()))) }, "Couldn't make a review", "{}")).review

    /** Apply the review with the user's choices. Returns how many changes were made and failed, or the server's `error`. */
    suspend fun applyReview(id: String, items: List<ReviewItem>): Result<ReviewResult> {
        val body = buildJsonObject {
            putJsonArray("items") {
                items.forEach {
                    addJsonObject {
                        put("ref", it.ref); put("action", it.action); put("enabled", it.enabled)
                        put("due_date", it.dueDate); put("group", it.group)
                    }
                }
            }
        }
        val text = http.text(http.request("/api/review/${enc(id)}/apply") { post(jsonBody(body)) }, "Couldn't apply the review", "{}")
        val json = ApiJson.parseToJsonElement(text) as? JsonObject ?: JsonObject(emptyMap())
        json.text("error")?.let { return Result.failure(ApiException(200, it)) }
        return Result.success(ApiJson.decodeFromJsonElement(ReviewResult.serializer(), json))
    }

    suspend fun dismissReview(id: String): Boolean =
        http.send(http.request("/api/review/${enc(id)}/dismiss") { post(jsonBody(JsonObject(emptyMap()))) })

    companion object {
        /** A briefing as the server or the widgets' copy holds it. */
        fun briefing(element: JsonObject): Briefing = ApiJson.decodeFromJsonElement(BriefingWire.serializer(), element).toModel()

        /** A briefing kept as text by [briefingText]. */
        fun briefing(text: String): Briefing? = runCatching { ApiJson.decodeFromString(BriefingWire.serializer(), text).toModel() }.getOrNull()

        fun briefingText(b: Briefing): String = ApiJson.encodeToString(Briefing.serializer(), b)

        /** A caller / meeting card as the server or the caller-card cache holds it. */
        fun card(element: JsonObject): PersonCard = ApiJson.decodeFromJsonElement(PersonCard.serializer(), element)

        fun cardJson(card: PersonCard): JsonObject = ApiJson.encodeToJsonElement(PersonCard.serializer(), card) as JsonObject
    }
}
