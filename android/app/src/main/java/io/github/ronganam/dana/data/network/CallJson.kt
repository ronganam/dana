package io.github.ronganam.dana.data.network

import io.github.ronganam.dana.data.models.CallDetail
import io.github.ronganam.dana.data.models.CallFact
import io.github.ronganam.dana.data.models.CallLabel
import io.github.ronganam.dana.data.models.CallParticipant
import io.github.ronganam.dana.data.models.CallSuggestion
import io.github.ronganam.dana.data.models.ChatContext
import io.github.ronganam.dana.data.models.LinkedTask
import io.github.ronganam.dana.data.models.PhoneCall
import io.github.ronganam.dana.data.models.SuggestionSource
import io.github.ronganam.dana.data.models.TaskLink
import org.json.JSONArray
import org.json.JSONObject

/** JSON → models for the call pipeline endpoints (calls, suggestions, links, chat context). */
object CallJson {

    private fun JSONObject.str(key: String): String? =
        optString(key).takeIf { has(key) && !isNull(key) && it.isNotEmpty() && it != "null" }

    private inline fun <T> JSONArray?.mapObjects(transform: (JSONObject) -> T?): List<T> {
        if (this == null) return emptyList()
        val out = ArrayList<T>(length())
        for (i in 0 until length()) optJSONObject(i)?.let(transform)?.let(out::add)
        return out
    }

    fun labels(arr: JSONArray?): List<CallLabel> = arr.mapObjects {
        CallLabel(it.optString("id"), it.optString("emoji"), it.optString("name"))
    }

    fun call(obj: JSONObject): PhoneCall = PhoneCall(
        id = obj.optString("id"),
        filename = obj.optString("filename"),
        caller = obj.optString("caller", "Unknown"),
        receiver = obj.optString("receiver", "Me"),
        callTimestamp = obj.optString("call_timestamp"),
        transcript = obj.str("transcript"),
        summary = obj.str("summary"),
        title = obj.str("title"),
        durationSeconds = obj.optInt("duration_seconds", 0),
        status = obj.optString("status", "ready").ifEmpty { "ready" },
        labels = labels(obj.optJSONArray("label_details")),
        importance = obj.optString("importance", "normal").ifEmpty { "normal" },
        direction = obj.str("direction"),
        phoneNumber = obj.str("phone_number"),
        audioAvailable = obj.optBoolean("audio_available", true),
        audioDeleted = obj.str("audio_deleted_at") != null,
        participants = obj.optJSONArray("participants").mapObjects {
            CallParticipant(it.optString("name"), it.optString("role"))
        },
        keyFacts = obj.optJSONArray("key_facts").mapObjects { CallFact(it.optString("kind"), it.optString("text")) },
        securityAlert = obj.str("security_alert"),
        pendingSuggestions = obj.optInt("pending_suggestions", 0),
        error = obj.str("error")
    )

    fun calls(arr: JSONArray): List<PhoneCall> = arr.mapObjects(::call)

    fun suggestion(obj: JSONObject): CallSuggestion {
        val callObj = obj.optJSONObject("call")
        val notifObj = obj.optJSONObject("notification")
        val callId = obj.str("call_id").orEmpty()
        return CallSuggestion(
            id = obj.optString("id"),
            callId = callId,
            kind = obj.optString("kind", "task"),
            status = obj.optString("status", "pending"),
            payload = obj.optJSONObject("payload") ?: JSONObject(),
            source = callObj?.let {
                SuggestionSource(
                    callId = it.optString("id"),
                    title = it.str("title"),
                    contact = it.str("contact"),
                    callTimestamp = it.str("call_timestamp"),
                    labels = labels(it.optJSONArray("label_details")),
                    text = it.str("summary")
                )
            } ?: notifObj?.let {
                // Shown by the same rows: "Reminder · WhatsApp · 2h ago".
                SuggestionSource(
                    callId = "",
                    title = it.str("title"),
                    contact = it.str("app_name"),
                    callTimestamp = it.str("posted_at"),
                    labels = emptyList(),
                    text = it.str("text"),
                    sender = it.str("sender"),
                    packageName = it.str("package")
                )
            },
            resultRef = obj.str("result_ref"),
            sourceType = obj.str("source_type") ?: "call",
            sourceId = obj.str("source_id") ?: callId
        )
    }

    fun suggestions(arr: JSONArray): List<CallSuggestion> = arr.mapObjects(::suggestion)

    fun detail(obj: JSONObject): CallDetail = CallDetail(
        call = call(obj),
        suggestions = suggestions(obj.optJSONArray("suggestions") ?: JSONArray()),
        tasks = obj.optJSONArray("tasks").mapObjects {
            LinkedTask(it.optString("id"), it.optString("title"), it.optString("status"), it.str("due_date"))
        }
    )

    fun taskLinks(arr: JSONArray?): List<TaskLink> = arr.mapObjects {
        TaskLink(
            refType = it.optString("ref_type"),
            refId = it.optString("ref_id"),
            role = it.optString("role"),
            title = it.str("title"),
            contact = it.str("contact"),
            callTimestamp = it.str("call_timestamp")
        )
    }

    fun chatContext(obj: JSONObject?): ChatContext? {
        if (obj == null || obj.optString("kind").isEmpty()) return null
        val prompts = obj.optJSONArray("quick_prompts")
        return ChatContext(
            kind = obj.optString("kind"),
            refId = obj.optString("ref_id"),
            callId = obj.str("call_id"),
            emoji = obj.optString("emoji", "📎"),
            title = obj.optString("title"),
            subtitle = obj.optString("subtitle"),
            quickPrompts = (0 until (prompts?.length() ?: 0)).mapNotNull { prompts?.optString(it)?.takeIf(String::isNotBlank) },
            status = obj.str("status")
        )
    }
}
