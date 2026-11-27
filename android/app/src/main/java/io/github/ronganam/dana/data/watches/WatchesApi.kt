package io.github.ronganam.dana.data.watches

import io.github.ronganam.dana.data.network.ApiClient
import io.github.ronganam.dana.data.network.ApiClient.Companion.enc
import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.HttpResult
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** A refused watch: the server's reasons (`400 {error: "invalid", errors}`), in its words. */
class WatchInvalid(val errors: List<String>) : Exception(errors.joinToString("; "))

/**
 * The watches add-on's routes (server `addons/watches/api.py`). Each call
 * throws [ApiException] (403 `not_connected`, 404 gone), [WatchInvalid] for a
 * refused text, spec or change, or [java.io.IOException] when unreachable.
 */
class WatchesApi(private val http: ApiClient) {

    /** `GET /api/watches`: every watch of the user, newest first. */
    suspend fun list(): List<Watch> = decode(WatchList.serializer(), call("GET", "/api/watches", "Couldn't load watches")).watches

    /** `GET /api/watches/{id}`: the watch and its fires (newest first). */
    suspend fun get(id: String): WatchDetail = decode(WatchDetail.serializer(), call("GET", "/api/watches/${enc(id)}", "Couldn't load the watch"))

    /** `GET /api/watches/{id}/trace`: its recent checks, newest first. */
    suspend fun trace(id: String, limit: Int = 300, outcome: String = "all"): WatchTrace =
        decode(WatchTrace.serializer(), call("GET", "/api/watches/${enc(id)}/trace?limit=$limit&outcome=${enc(outcome)}", "Couldn't load the checks"))

    /**
     * `POST /api/watches {text, status: "paused"}`: compile [text] into a new
     * watch that is off until it is turned on. A server that ignores
     * `status` creates it on; it is paused here then.
     */
    suspend fun createOff(text: String): Watch {
        val body = buildJsonObject { put("text", text); put("status", "paused") }
        val w = watch(call("POST", "/api/watches", "Couldn't make the watch", body))
        return if (w.isPaused) w else setStatus(w.id, paused = true)
    }

    /** `PATCH /api/watches/{id} {status}`: pause or turn on. */
    suspend fun setStatus(id: String, paused: Boolean): Watch =
        watch(call("PATCH", "/api/watches/${enc(id)}", "Couldn't change the watch", buildJsonObject { put("status", if (paused) "paused" else "active") }))

    /** `PATCH /api/watches/{id} {change}`: change it in words (compiled again). */
    suspend fun change(id: String, words: String): Watch =
        watch(call("PATCH", "/api/watches/${enc(id)}", "Couldn't change the watch", buildJsonObject { put("change", words) }))

    /** `DELETE /api/watches/{id}`: the undo id. */
    suspend fun delete(id: String): String? {
        val r = call("DELETE", "/api/watches/${enc(id)}", "Couldn't delete the watch")
        return (r["undo_id"] as? JsonPrimitive)?.content
    }

    /** `POST /api/undo/{id}`. */
    suspend fun undo(undoId: String) {
        call("POST", "/api/undo/${enc(undoId)}", "Couldn't undo")
    }

    /** `POST /api/watches/dry-run {spec, days}`: what the rule would have let through. */
    suspend fun dryRun(spec: JsonObject, days: Int = 14): DryRun =
        decode(DryRun.serializer(), call("POST", "/api/watches/dry-run", "Couldn't try the watch", buildJsonObject { put("spec", spec); put("days", days) }))

    /** `GET /api/watches/pipeline`: the engine's recent steps. */
    suspend fun recent(): List<PipelineEvent> {
        val r = call("GET", "/api/watches/pipeline", "Couldn't load the pipeline")
        return (r["recent"] as? JsonArray).orEmpty().mapNotNull { e ->
            runCatching { ApiJson.decodeFromJsonElement(PipelineEvent.serializer(), e) }.getOrNull()
        }
    }

    /**
     * `GET /api/watches/pipeline/stream`: [onEvent] gets each engine step
     * live, until the server closes the stream or the coroutine is cancelled.
     */
    suspend fun stream(onEvent: (PipelineEvent) -> Unit) {
        http.stream(
            http.request("/api/watches/pipeline/stream"),
            onError = { code, body -> throw ApiException(code, HttpResult(code, body).error.ifBlank { "Live view failed (HTTP $code)" }) }
        ) { _, data -> parseEvent(data)?.let(onEvent) }
    }

    private suspend fun call(method: String, path: String, failure: String, body: JsonElement? = null): JsonObject {
        val r = http.call(method, path, body)
        if (r.ok) return r.json()
        val errors = (r.json()["errors"] as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.content }.orEmpty()
        if (r.code == 400 && errors.isNotEmpty()) throw WatchInvalid(errors)
        throw ApiException(r.code, r.error.ifBlank { "$failure (HTTP ${r.code})" })
    }

    private fun watch(json: JsonObject): Watch =
        decode(WatchAnswer.serializer(), json).watch ?: throw ApiException(500, "The server sent no watch")

    private fun <T> decode(serializer: KSerializer<T>, json: JsonObject): T = ApiJson.decodeFromJsonElement(serializer, json)

    companion object {
        /** One stream frame's data; null when it is not a pipeline step. */
        fun parseEvent(data: String): PipelineEvent? = runCatching {
            ApiJson.decodeFromString(PipelineEvent.serializer(), data)
        }.getOrNull()?.takeIf { it.stage.isNotBlank() }
    }
}
