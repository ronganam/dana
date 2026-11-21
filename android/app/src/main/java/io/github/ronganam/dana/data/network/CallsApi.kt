package io.github.ronganam.dana.data.network

import io.github.ronganam.dana.data.models.CallDetail
import io.github.ronganam.dana.data.models.CallLabel
import io.github.ronganam.dana.data.models.CallSuggestion
import io.github.ronganam.dana.data.models.ChatContext
import io.github.ronganam.dana.data.models.LinkedTask
import io.github.ronganam.dana.data.models.PhoneCall
import io.github.ronganam.dana.data.models.SuggestionSource
import io.github.ronganam.dana.data.models.Triggers
import io.github.ronganam.dana.data.network.ApiClient.Companion.enc
import io.github.ronganam.dana.data.network.ApiClient.Companion.jsonBody
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File

/** What the server answers when an upload is accepted (`POST /api/calls/upload`). */
@Serializable
data class CallUploadResult(
    val id: OptString = null,
    val status: OptString = null,
    val duplicate: Boolean = false,
    val title: OptString = null
)

/** What accepting a suggestion made (`POST /api/suggestions/{id}/accept`): a task or a calendar event. */
@Serializable
data class AcceptResult(
    val kind: OptString = null,
    val taskId: OptString = null,
    val eventId: OptString = null,
    val message: OptString = null,
    val error: OptString = null
)

/**
 * What the user changed before accepting a suggestion (the accept route's
 * overrides replace those payload fields; a null clears the field).
 */
sealed class SuggestionEdits {
    internal abstract fun toJson(): JsonObject

    object None : SuggestionEdits() {
        override fun toJson() = JsonObject(emptyMap())
    }

    data class Event(val title: String, val start: String, val end: String?, val location: String?) : SuggestionEdits() {
        override fun toJson() = buildJsonObject {
            put("title", title)
            put("start", start)
            put("end", end)
            put("location", location)
        }
    }

    data class Task(val title: String, val dueDate: String?, val priority: String, val group: String?, val triggers: Triggers) : SuggestionEdits() {
        override fun toJson() = buildJsonObject {
            put("title", title)
            put("due_date", dueDate)
            put("priority", priority)
            if (!group.isNullOrBlank()) put("group", group)
            put("triggers", ApiJson.encodeToJsonElement(Triggers.serializer(), triggers))
        }
    }
}

@Serializable
internal data class SuggestionCallWire(
    val id: String = "",
    val title: OptString = null,
    val contact: OptString = null,
    val callTimestamp: OptString = null,
    val labelDetails: LenientList<CallLabel> = emptyList(),
    val summary: OptString = null
)

@Serializable
internal data class SuggestionNotificationWire(
    val title: OptString = null,
    val appName: OptString = null,
    val postedAt: OptString = null,
    val text: OptString = null,
    val sender: OptString = null,
    @kotlinx.serialization.SerialName("package") val packageName: OptString = null
)

/** A suggestion as the server sends it (`call_suggestions` rows with their call or notification). */
@Serializable
internal data class SuggestionWire(
    val id: String = "",
    val callId: OptString = null,
    val kind: String = "task",
    val status: String = "pending",
    val payload: JsonObject = JsonObject(emptyMap()),
    val call: SuggestionCallWire? = null,
    val notification: SuggestionNotificationWire? = null,
    val resultRef: OptString = null,
    val sourceType: OptString = null,
    val sourceId: OptString = null
) {
    fun toModel(): CallSuggestion {
        val callId = callId.orEmpty()
        return CallSuggestion(
            id = id,
            callId = callId,
            kind = kind,
            status = status,
            payload = payload,
            source = call?.let {
                SuggestionSource(it.id, it.title, it.contact, it.callTimestamp, it.labelDetails, text = it.summary)
            } ?: notification?.let {
                // Shown by the same rows: "Reminder · WhatsApp · 2h ago".
                SuggestionSource("", it.title, it.appName, it.postedAt, emptyList(), it.text, it.sender, it.packageName)
            },
            resultRef = resultRef,
            sourceType = sourceType ?: "call",
            sourceId = sourceId ?: callId
        )
    }
}

/** A list of suggestions, skipping rows that don't read. */
@Serializable(with = SuggestionListSerializer::class)
internal class SuggestionList(val items: List<CallSuggestion>)

internal object SuggestionListSerializer : kotlinx.serialization.KSerializer<SuggestionList> {
    private val wire = LenientListSerializer(SuggestionWire.serializer())
    override val descriptor = wire.descriptor
    override fun deserialize(decoder: kotlinx.serialization.encoding.Decoder) = SuggestionList(wire.deserialize(decoder).map { it.toModel() })
    override fun serialize(encoder: kotlinx.serialization.encoding.Encoder, value: SuggestionList) =
        throw UnsupportedOperationException("suggestions are only read")
}

@Serializable
private data class CallDetailWire(
    val suggestions: SuggestionList = SuggestionList(emptyList()),
    val tasks: LenientList<LinkedTask> = emptyList()
)

/** What a chat was opened from, as the server sends it (`context` of a session). */
@Serializable
internal data class ChatContextWire(
    val kind: String = "",
    val refId: String = "",
    val callId: OptString = null,
    val emoji: String = "📎",
    val title: String = "",
    val subtitle: String = "",
    val quickPrompts: TextList = emptyList(),
    val status: OptString = null
) {
    fun toModel(): ChatContext? =
        if (kind.isEmpty()) null else ChatContext(kind, refId, callId, emoji, title, subtitle, quickPrompts, status)
}

/** Call recordings and the suggestions made from calls and notifications (server docs/calls.md). */
class CallsApi(private val http: ApiClient) {

    /**
     * Upload a recording. The server answers right away (202, `status:
     * processing`) and analyses it in the background; poll [detail]. A
     * re-upload of the same recording returns the existing call with
     * `duplicate: true`. Throws [ApiException] on 5xx so callers can retry;
     * returns null when the server rejected the file (4xx).
     */
    suspend fun upload(
        file: File,
        caller: String,
        receiver: String,
        callTimestamp: String,
        instructions: String? = null,
        durationSeconds: Int = 0,
        direction: String? = null,
        phoneNumber: String? = null
    ): CallUploadResult? {
        val builder = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("caller", caller)
            .addFormDataPart("receiver", receiver)
            .addFormDataPart("call_timestamp", callTimestamp)
        if (!instructions.isNullOrBlank()) builder.addFormDataPart("instructions", instructions.trim())
        if (durationSeconds > 0) builder.addFormDataPart("duration_seconds", durationSeconds.toString())
        if (!direction.isNullOrBlank()) builder.addFormDataPart("direction", direction)
        if (!phoneNumber.isNullOrBlank()) builder.addFormDataPart("phone_number", phoneNumber)
        builder.addFormDataPart("audio", file.name, file.asRequestBody("audio/mp4".toMediaTypeOrNull()))
        val r = http.result(http.request("/api/calls/upload") { post(builder.build()) }, HttpClients.upload)
        if (r.code >= 500) throw ApiException(r.code, "Server error ${r.code}")
        if (!r.ok) return null
        return runCatching { http.decode(CallUploadResult.serializer(), r.body) }.getOrDefault(CallUploadResult())
    }

    suspend fun calls(): List<PhoneCall> =
        http.decode(LenientListSerializer(PhoneCall.serializer()), http.text(http.request("/api/calls"), "Failed to fetch calls", "[]"), "[]")

    suspend fun detail(callId: String): CallDetail {
        val text = http.text(http.request("/api/calls/${enc(callId)}"), "Failed to fetch call", "{}")
        val wire = http.decode(CallDetailWire.serializer(), text)
        return CallDetail(http.decode(PhoneCall.serializer(), text), wire.suggestions.items, wire.tasks)
    }

    suspend fun labels(): List<CallLabel> =
        http.decode(LenientListSerializer(CallLabel.serializer()), http.text(http.request("/api/calls/labels"), "Failed to fetch labels", "[]"), "[]")

    suspend fun setLabels(callId: String, labelIds: List<String>): Boolean =
        http.send(http.request("/api/calls/${enc(callId)}") {
            put(jsonBody(buildJsonObject { putJsonArray("labels") { labelIds.forEach { add(JsonPrimitive(it)) } } }))
        })

    suspend fun delete(callId: String): Boolean = http.send(http.request("/api/calls/${enc(callId)}") { delete() })

    suspend fun reanalyze(callId: String, customInstructions: String? = null): Boolean =
        http.send(http.request("/api/calls/${enc(callId)}/reanalyze") {
            post(jsonBody(buildJsonObject { if (!customInstructions.isNullOrBlank()) put("custom_instructions", customInstructions.trim()) }))
        })

    // --- Suggestions ---

    /** [source]: call (calls only — the server's default), notification, or all. */
    suspend fun suggestions(status: String = "pending", source: String = "call"): List<CallSuggestion> =
        http.decode(SuggestionList.serializer(),
            http.text(http.request("/api/suggestions?status=${enc(status)}&source=${enc(source)}"), "Failed to fetch suggestions", "[]"), "[]").items

    /** Dismiss several; returns how many were. */
    suspend fun dismissMany(ids: List<String>): Int {
        val body = buildJsonObject { putJsonArray("ids") { ids.forEach { add(JsonPrimitive(it)) } }; put("action", "dismiss") }
        val text = http.text(http.request("/api/suggestions/bulk") { post(jsonBody(body)) }, "Couldn't dismiss", "{}")
        return http.decode(Dismissed.serializer(), text).dismissed
    }

    /** Create the task / event; [edits] replace payload fields. Throws [ApiException] with the server's reason. */
    suspend fun accept(id: String, edits: SuggestionEdits = SuggestionEdits.None): AcceptResult {
        val r = http.result(http.request("/api/suggestions/${enc(id)}/accept") { post(jsonBody(edits.toJson())) })
        val result = runCatching { http.decode(AcceptResult.serializer(), r.body) }.getOrDefault(AcceptResult())
        if (!r.ok) throw ApiException(r.code, result.error ?: "Couldn't create it (HTTP ${r.code})")
        return result
    }

    suspend fun dismiss(id: String): Boolean =
        http.send(http.request("/api/suggestions/${enc(id)}/dismiss") { post(jsonBody(JsonObject(emptyMap()))) })

    @Serializable
    private data class Dismissed(val dismissed: Int = 0)

    companion object {
        /** A list of suggestions as the server sends them (also inside notifications). */
        internal fun suggestionsOf(element: kotlinx.serialization.json.JsonElement?): List<CallSuggestion> =
            decodeListLenient(element, SuggestionWire.serializer()).map { it.toModel() }
    }
}
