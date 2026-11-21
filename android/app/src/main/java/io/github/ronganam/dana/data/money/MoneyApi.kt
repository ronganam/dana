package io.github.ronganam.dana.data.money

import android.content.Context
import io.github.ronganam.dana.data.network.ApiClient
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.ApiService
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import io.github.ronganam.dana.data.network.Me
import org.json.JSONObject
import java.net.URLEncoder

/**
 * The server refused a money request: HTTP [code] with its `error` code
 * (`invalid`, `too_soon`, `needs_attention`, `bad_code`, `not_pending`,
 * `running`, `needs_answer`, `not_connected`…), its `message` (shown as is
 * when present), the `field` it is about and `retry_after` seconds. [body]
 * is the whole answer for errors that carry more (`needs_answer {question}`).
 */
class MoneyApiError(
    val code: Int,
    val error: String,
    override val message: String,
    val field: String? = null,
    val retryAfter: Int? = null,
    val body: JSONObject = JSONObject()
) : Exception(message)

/**
 * The `/api/money/…` routes (server docs/money.md → API, Page API). Every
 * answer is JSON, amounts in agorot; non-2xx throws [MoneyApiError], an
 * unreachable server `IOException`. A 403 `not_connected` also forgets the
 * add-on ([Me.drop]) so More hides Money.
 */
class MoneyApi(private val context: Context, private val api: () -> ApiService) {

    suspend fun get(path: String, query: Map<String, Any?> = emptyMap()): JSONObject = call("GET", path + qs(query), null)
    suspend fun post(path: String, body: JSONObject = JSONObject()): JSONObject = call("POST", path, body)
    suspend fun patch(path: String, body: JSONObject): JSONObject = call("PATCH", path, body)
    suspend fun delete(path: String, query: Map<String, Any?> = emptyMap()): JSONObject = call("DELETE", path + qs(query), null)

    /** `POST /api/undo/{id}` (the `undo_id` of a write). */
    suspend fun undo(undoId: String): JSONObject = call("POST", "/api/undo/${enc(undoId)}", JSONObject())

    private suspend fun call(method: String, path: String, body: JSONObject?): JSONObject {
        val full = if (path.startsWith("/api/")) path else "/api/money$path"
        val (code, text) = api().http.call(method, full, body?.let { ApiJson.parseToJsonElement(it.toString()) })
        val json = runCatching { JSONObject(text) }.getOrDefault(JSONObject())
        if (code !in 200..299) {
            val error = json.optString("error")
            if (code == 403 && error == "not_connected") Me.drop(context, ADDON)
            throw MoneyApiError(
                code = code,
                error = error,
                message = json.optString("message").ifBlank { describe(code, error) },
                field = json.optString("field").ifBlank { null },
                retryAfter = if (json.has("retry_after")) json.optInt("retry_after") else null,
                body = json
            )
        }
        return json
    }

    private fun describe(code: Int, error: String): String = when {
        error == "not_connected" -> "Money isn't connected for you"
        code == 404 -> "It's no longer there"
        code == 401 || code == 403 -> "Not authorized (check the API token)"
        error.isNotBlank() -> error.replace('_', ' ')
        else -> "The server answered HTTP $code"
    }

    companion object {
        const val ADDON = "money"

        fun enc(s: String): String = URLEncoder.encode(s, "UTF-8")

        /** `?a=1&b=x` from the non-null, non-blank values. */
        fun qs(query: Map<String, Any?>): String {
            val parts = query.mapNotNull { (k, v) ->
                val s = v?.toString()
                if (s.isNullOrBlank()) null else "${enc(k)}=${enc(s)}"
            }
            return if (parts.isEmpty()) "" else "?" + parts.joinToString("&")
        }
    }
}

/** What the phone itself sends the money add-on: bank sign-in codes and bank / card app notifications. */
class MoneyDeviceApi(private val http: ApiClient) {
    /** `GET /api/money/code-requests/{id}`: what the server waits for (`status`, `senders`, `code_regex`, `expires_at`). */
    suspend fun codeRequest(id: String): JSONObject =
        JSONObject(http.text(http.request("/api/money/code-requests/${MoneyApi.enc(id)}"), "Couldn't fetch the code request", "{}"))

    /**
     * Hand the server the sign-in code from the bank's SMS. Returns the HTTP
     * status only (200 taken, 400 `bad_code`, 404, 409 `not_pending`);
     * unreachable throws IOException. Nothing here logs the code.
     */
    suspend fun postCode(id: String, code: String): Int =
        http.call("POST", "/api/money/code-requests/${MoneyApi.enc(id)}", buildJsonObject { put("code", code) }).code

    /** `POST /api/money/signals`: bank / card app notifications. HTTP status + body (`accepted`, or `error`). */
    suspend fun postSignals(notifications: org.json.JSONArray): Pair<Int, JSONObject> {
        val r = http.call("POST", "/api/money/signals", ApiJson.parseToJsonElement(JSONObject().put("notifications", notifications).toString()))
        return r.code to runCatching { JSONObject(r.body) }.getOrDefault(JSONObject())
    }
}
