package io.github.ronganam.dana.data.network

import io.github.ronganam.dana.data.models.LiveActivity
import io.github.ronganam.dana.data.models.LiveList
import io.github.ronganam.dana.data.network.ApiClient.Companion.enc
import io.github.ronganam.dana.data.network.ApiClient.Companion.jsonBody
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** The answer of `act` / `select`: the activity after it (null: it ended). */
@Serializable
private data class LiveAnswer(val activity: LiveActivity? = null)

/**
 * Live activities (server `core/live_api.py`): act on one, change its
 * selection, stop it. Each throws [ApiException] (400 `unknown_action` /
 * `unknown_choice`, 404 gone) or [java.io.IOException] when unreachable.
 */
class LiveApi(private val http: ApiClient) {

    /** `GET /api/live`: every activity (running, and ended ones kept for their final), newest-updated first. */
    suspend fun list(): List<LiveActivity> = parse(http.jsonOrThrow(http.request("/api/live"), "Couldn't load what is going on"))

    /** `POST /api/live/{id}/act`: run the add-on's action [action]; the activity after it, null when it ended. */
    suspend fun act(id: String, action: String, choice: String? = null): LiveActivity? {
        val body = buildJsonObject { put("action", action); if (choice != null) put("choice", choice) }
        return answer(http.jsonOrThrow(http.request("/api/live/${enc(id)}/act") { post(jsonBody(body)) }, "Couldn't do that"))
    }

    /** `POST /api/live/{id}/select`: show the choice [choice]; the activity after it. */
    suspend fun select(id: String, choice: String): LiveActivity? {
        val body = buildJsonObject { put("choice", choice) }
        return answer(http.jsonOrThrow(http.request("/api/live/${enc(id)}/select") { post(jsonBody(body)) }, "Couldn't switch"))
    }

    /** `DELETE /api/live/{id}`: stop the activity; whether the server accepted it. */
    suspend fun stop(id: String): Boolean = http.send(http.request("/api/live/${enc(id)}") { delete() })

    private fun answer(json: JsonElement): LiveActivity? = ApiJson.decodeFromJsonElement(LiveAnswer.serializer(), json).activity

    companion object {
        /** Read a live payload (the push's data, the cached copy); malformed activities are dropped. */
        fun parse(json: JsonElement): List<LiveActivity> =
            runCatching { ApiJson.decodeFromJsonElement(LiveList.serializer(), json).activities }.getOrDefault(emptyList())
                .filter { it.id.isNotBlank() }

        /** [parse] from JSON text ("" or unreadable → none). */
        fun parse(text: String): List<LiveActivity> =
            runCatching { ApiJson.parseToJsonElement(text) }.getOrNull()?.let(::parse).orEmpty()

        /** The JSON text of [activities], for the phone's cached copy. */
        fun encode(activities: List<LiveActivity>): String = ApiJson.encodeToString(LiveList.serializer(), LiveList(activities))
    }
}
