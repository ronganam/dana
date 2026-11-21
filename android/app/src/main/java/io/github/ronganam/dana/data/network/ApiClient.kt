package io.github.ronganam.dana.data.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.serializer
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okio.BufferedSource
import java.io.IOException
import java.net.URLEncoder
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** Non-2xx answer from the server. Transport failures stay [IOException]. */
class ApiException(val code: Int, message: String) : Exception(message)

/**
 * The session already has a run in progress (HTTP 409). [runId] is that run,
 * so the caller can attach to it instead of sending again.
 */
class SessionBusyException(val runId: String?) : Exception("This chat is still working on the previous request")

/** One HTTP answer read whole: its status and body text. */
data class HttpResult(val code: Int, val body: String) {
    val ok: Boolean get() = code in 200..299

    /** The body as a JSON object ({} when it isn't one). */
    fun json(): JsonObject = runCatching { ApiJson.parseToJsonElement(body) as? JsonObject }.getOrNull() ?: JsonObject(emptyMap())

    /** The server's `error` code ("" when none). */
    val error: String get() = json().text("error").orEmpty()
}

/**
 * The transport under every `*Api` class: requests to the Dana server at
 * [serverUrl] (normalized, see [ServerConfig]) with the bearer token, on the
 * shared [HttpClients]. Responses are always closed; non-2xx answers throw
 * [ApiException] (or come back as an [HttpResult] where the caller reads the
 * status), an unreachable server throws [IOException]. Bodies are encoded
 * and decoded with [ApiJson] here, so the rest of the app sees typed values.
 */
class ApiClient(private val serverUrl: String, private val apiToken: String) {

    fun request(path: String, builder: Request.Builder.() -> Unit = {}): Request =
        Request.Builder().url("$serverUrl$path").bearerAuth(apiToken).apply(builder).build()

    /** Run [request] on [client], cancelling the HTTP call with the coroutine. The caller closes the response. */
    suspend fun execute(request: Request, client: OkHttpClient = HttpClients.base): Response =
        client.newCall(request).await()

    /** The body of a 2xx answer ([default] when empty); anything else throws [ApiException] "[what] (HTTP n)". */
    suspend fun text(request: Request, what: String, default: String = ""): String = withContext(Dispatchers.IO) {
        execute(request).use { response ->
            if (!response.isSuccessful) throw ApiException(response.code, "$what (HTTP ${response.code})")
            response.body.string().ifEmpty { default }
        }
    }

    /** GET [path] and decode a 2xx answer as [T]. */
    suspend inline fun <reified T> get(path: String, what: String): T =
        decode(serializer(), text(request(path), what), if (List::class.java.isAssignableFrom(T::class.java)) "[]" else "{}")

    fun <T> decode(serializer: KSerializer<T>, text: String, blank: String = "{}"): T =
        ApiJson.decodeFromString(serializer, text.ifBlank { blank })

    /** Whether the server accepted [request] (2xx); unreachable still throws [IOException]. */
    suspend fun send(request: Request): Boolean = withContext(Dispatchers.IO) { execute(request).use { it.isSuccessful } }

    /** The HTTP status of [request] (for the offline queues, which retry 5xx). */
    suspend fun sendForCode(request: Request): Int = withContext(Dispatchers.IO) { execute(request).use { it.code } }

    /** The whole answer of [request], whatever its status. */
    suspend fun result(request: Request, client: OkHttpClient = HttpClients.base): HttpResult = withContext(Dispatchers.IO) {
        execute(request, client).use { HttpResult(it.code, it.body.string()) }
    }

    /** GET [request] into [dest] (written only on a 2xx); returns the HTTP status. */
    suspend fun download(request: Request, dest: java.io.File, client: OkHttpClient = HttpClients.upload): Int = withContext(Dispatchers.IO) {
        execute(request, client).use { response ->
            if (response.isSuccessful) {
                dest.parentFile?.mkdirs()
                response.body.byteStream().use { input -> dest.outputStream().use { input.copyTo(it) } }
            }
            response.code
        }
    }

    /**
     * One JSON request: [method] on [path] with an optional JSON [body] (`{}` for a
     * bodyless POST / PUT / PATCH). Returns the status and the body text.
     */
    suspend fun call(method: String, path: String, body: JsonElement? = null): HttpResult =
        result(request(path) {
            val rb = body?.let(::jsonBody)
            when (method) {
                "GET" -> get()
                "POST" -> post(rb ?: EMPTY)
                "PUT" -> put(rb ?: EMPTY)
                "PATCH" -> patch(rb ?: EMPTY)
                "DELETE" -> if (rb != null) delete(rb) else delete()
                else -> throw IllegalArgumentException("Unknown method $method")
            }
        })

    /**
     * [request] answered with JSON; a non-2xx answer throws [ApiException]
     * carrying the server's `error` text (shown to the user), else "[failure] (HTTP n)".
     */
    suspend fun jsonOrThrow(request: Request, failure: String): JsonObject {
        val r = result(request)
        if (!r.ok) throw ApiException(r.code, r.error.ifBlank { "$failure (HTTP ${r.code})" })
        return r.json()
    }

    /** [jsonOrThrow] decoded as [T]. */
    suspend fun <T> jsonOrThrow(request: Request, failure: String, serializer: KSerializer<T>): T =
        ApiJson.decodeFromJsonElement(serializer, jsonOrThrow(request, failure))

    /** POST [payload] to [path]: [jsonOrThrow]. */
    suspend fun post(path: String, payload: JsonElement, failure: String): JsonObject =
        jsonOrThrow(request(path) { post(jsonBody(payload)) }, failure)

    /**
     * Follow a server-sent event stream: [onFrame] gets each frame's `event:`
     * name (null when it has none) and its `data:` lines joined, until the
     * server closes it. A non-2xx answer goes to [onError] with its body, which
     * throws.
     */
    suspend fun stream(
        request: Request,
        onError: (code: Int, body: String) -> Nothing,
        onFrame: (event: String?, data: String) -> Unit
    ) = withContext(Dispatchers.IO) {
        execute(request, HttpClients.streaming).use { response ->
            if (!response.isSuccessful) onError(response.code, response.body.string())
            readSseFrames(response.body.source(), onFrame)
        }
    }

    companion object {
        private val JSON = "application/json".toMediaType()
        private val EMPTY: RequestBody = "{}".toRequestBody(JSON)

        fun jsonBody(element: JsonElement): RequestBody = element.toString().toRequestBody(JSON)

        /** A typed request body. */
        fun <T> jsonBody(serializer: KSerializer<T>, value: T): RequestBody =
            ApiJson.encodeToString(serializer, value).toRequestBody(JSON)

        fun enc(value: String): String = URLEncoder.encode(value, "UTF-8")

        /**
         * Read server-sent events from [source] until it ends: [onFrame] gets each
         * frame's `event:` name (null when it has none) and its `data:` lines
         * joined. Comment lines (`: ping`, `: heartbeat`) are keepalives.
         */
        fun readSseFrames(source: BufferedSource, onFrame: (event: String?, data: String) -> Unit) {
            val buffer = StringBuilder()
            var event: String? = null
            while (!source.exhausted()) {
                val line = source.readUtf8LineStrict()
                if (line.isEmpty()) {
                    if (buffer.isNotEmpty()) onFrame(event, buffer.toString())
                    buffer.setLength(0)
                    event = null
                    continue
                }
                if (line.startsWith(":")) continue  // keepalive comment
                if (line.startsWith("event:")) { event = line.removePrefix("event:").trim(); continue }
                if (!line.startsWith("data:")) continue
                buffer.append(line.removePrefix("data:").trim())
            }
            if (buffer.isNotEmpty()) onFrame(event, buffer.toString())
        }
    }
}

/**
 * Cancellable `enqueue`. If the coroutine is cancelled, the HTTP call is
 * cancelled too (blocking `execute()` inside `withContext(IO)` can't be).
 */
suspend fun Call.await(): Response = suspendCancellableCoroutine { continuation ->
    continuation.invokeOnCancellation { cancel() }
    enqueue(object : Callback {
        override fun onResponse(call: Call, response: Response) {
            if (continuation.isActive) continuation.resume(response) else response.close()
        }

        override fun onFailure(call: Call, e: IOException) {
            if (continuation.isActive) continuation.resumeWithException(e)
        }
    })
}
