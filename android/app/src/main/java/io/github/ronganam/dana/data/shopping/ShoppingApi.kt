package io.github.ronganam.dana.data.shopping

import io.github.ronganam.dana.data.network.ApiClient
import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.data.network.ApiJson
import io.github.ronganam.dana.data.network.HttpResult
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.IOException
import java.net.URLEncoder

/** A shopping answer: the HTTP status (-1: unreachable), what it carried when it was 2xx, else why it was refused. */
data class ShoppingAnswer<T>(val code: Int, val value: T?, val refusal: ShoppingRefusal?, val raw: String = "") {
    val ok: Boolean get() = code in 200..299
}

/** One event of `GET /api/shopping/events`: `changed {list_id, version}`, `lists {}`, `end {}`. */
data class ShoppingEvent(val type: String, val listId: String, val version: Long)

@Serializable
private data class EventWire(val listId: String = "", val version: Long = 0)

/** The `/api/shopping…` routes (server docs/shopping.md → API) and the list's sharing. */
class ShoppingApi(private val http: ApiClient) {

    /** One request, whatever its answer ([ShoppingStore]'s transport); unreachable throws [IOException]. */
    suspend fun call(method: String, path: String, body: JsonElement?): HttpResult = http.call(method, path, body)

    private suspend fun <T> answer(method: String, path: String, body: JsonElement? = null, read: (String) -> T?): ShoppingAnswer<T> {
        val r = try {
            http.call(method, path, body)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            return ShoppingAnswer(-1, null, null)
        }
        return if (r.ok) ShoppingAnswer(r.code, read(r.body), ShoppingJson.refusal(r.body), r.body)
        else ShoppingAnswer(r.code, null, ShoppingJson.refusal(r.body), r.body)
    }

    /** A list by id (`?list=`: one looked at, not the active one). */
    suspend fun screen(listId: String): ShoppingAnswer<ShoppingScreen> = answer("GET", "/api/shopping?list=${enc(listId)}") { ShoppingJson.screen(it) }

    /** The usual items matching [query] on [listId] (the active list when null). */
    suspend fun suggestions(query: String, listId: String?): ShoppingAnswer<List<Usual>> =
        answer("GET", "/api/shopping/suggestions?q=${enc(query)}" + listId?.let { "&list=${enc(it)}" }.orEmpty()) { ShoppingJson.usuals(it) }

    suspend fun undo(undoId: String): ShoppingAnswer<Unit> = answer("POST", "/api/undo/${enc(undoId)}") { }

    /**
     * Writes answered with a screen; unreachable throws [IOException]. [ShoppingAnswer.raw]
     * is the answer as the disk cache keeps it.
     */
    suspend fun answerInvite(listId: String, action: String, moveItems: Boolean): ShoppingAnswer<WriteAnswer> =
        write("POST", "/api/shopping/invites/${enc(listId)}", buildJsonObject {
            put("action", action)
            if (action == "use") put("move_items", moveItems)
        })

    suspend fun setActive(listId: String): ShoppingAnswer<WriteAnswer> =
        write("PUT", "/api/shopping/active", buildJsonObject { put("list_id", listId) })

    private suspend fun write(method: String, path: String, body: JsonElement): ShoppingAnswer<WriteAnswer> {
        val r = http.call(method, path, body)
        return ShoppingAnswer(r.code, if (r.ok) ShoppingJson.answer(r.body) else null, ShoppingJson.refusal(r.body), r.body)
    }

    suspend fun share(listId: String): ShoppingAnswer<SharePayload> =
        answer("GET", "/api/sharing/shopping_list/${enc(listId)}") { ShoppingJson.share(it) }

    /** Can edit (`edit`) / View only (`read`) / Remove (`none`). */
    suspend fun setShare(listId: String, userId: String, level: String): ShoppingAnswer<SharePayload> =
        answer("PUT", "/api/sharing/shopping_list/${enc(listId)}", buildJsonObject { put("user_id", userId); put("level", level) }) { ShoppingJson.share(it) }

    /**
     * Follow `GET /api/shopping/events` (the caller's active list, or [listId])
     * until the server closes it or the connection drops. A non-2xx answer
     * throws [ApiException].
     */
    suspend fun events(listId: String?, onEvent: (ShoppingEvent) -> Unit) {
        val path = "/api/shopping/events" + (listId?.let { "?list=${enc(it)}" } ?: "")
        http.stream(
            http.request(path) { header("Accept", "text/event-stream") },
            onError = { code, _ -> throw ApiException(code, "Shopping stream (HTTP $code)") }
        ) { event, data ->
            val w = runCatching { ApiJson.decodeFromString(EventWire.serializer(), data) }.getOrDefault(EventWire())
            onEvent(ShoppingEvent(event ?: "message", w.listId, w.version))
        }
    }

    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8").replace("+", "%20")
}
