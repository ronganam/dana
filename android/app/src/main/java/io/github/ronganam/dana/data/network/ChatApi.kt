package io.github.ronganam.dana.data.network

import android.util.Log
import io.github.ronganam.dana.data.chat.ChatHistoryCodec
import io.github.ronganam.dana.data.chat.ThreadModel
import io.github.ronganam.dana.data.chat.UndoAction
import io.github.ronganam.dana.data.models.ChatContext
import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.models.ModelOption
import io.github.ronganam.dana.data.models.SelectionOption
import io.github.ronganam.dana.data.models.SelectionRequest
import io.github.ronganam.dana.data.network.ApiClient.Companion.enc
import io.github.ronganam.dana.data.network.ApiClient.Companion.jsonBody
import io.github.ronganam.dana.data.screen.DeviceScreen
import io.github.ronganam.dana.ui.viewmodels.ScreenRef
import kotlinx.serialization.Serializable
import kotlinx.serialization.Transient
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** One conversation in the old per-session list (`GET /api/sessions`). */
data class SessionItem(
    val id: String,
    val timestamp: Long,
    val snippet: String,
    val status: String = "idle",
    val title: String = ""
)

/** The chat model catalog (`GET /api/assistant/models`). */
data class ModelCatalog(val default: String, val models: List<ModelOption>)

/** An approval the session is waiting for (`pending_approval`). */
data class PendingApprovalState(
    val toolName: String?,
    /** The arguments as JSON text (a string argument as is); null when absent. */
    val toolArgs: String?,
    val toolCallId: String,
    /** Why it asks ("device_screen" when only the shared screen made it ask); "" when it doesn't say. */
    val reason: String
)

/**
 * A session's state: what `GET /api/sessions/{id}` (a thread page for
 * `main`), "New topic" and every run's closing `state` frame carry. Fields a
 * payload leaves out are "not said" ([hasContext], [undos] null), so a
 * partial state never wipes what is shown.
 */
data class SessionState(
    val status: String,
    val messages: List<ChatMessage>,
    val hasContext: Boolean,
    val context: ChatContext?,
    val undos: List<UndoAction>?,
    val pendingApproval: PendingApprovalState?,
    val selection: SelectionRequest?,
    /** The run in flight (`active_run.run_id`; "" when it names none), null when there is none. */
    val activeRunId: String?,
    val memory: ThreadModel.Memory,
    val gapMinutes: Double?,
    val shortcut: Boolean,
    val hasMore: Boolean,
    val before: String?,
    val extendSummary: String,
    val error: String?
)

@Serializable
data class RunModel(val id: String = "", val label: OptString = null)

/**
 * One event of a run (server `core/runs.py`), from the SSE stream, a reattach
 * or the voice socket. Which fields are set depends on [type]; the rest keep
 * their defaults. The closing `state` frame carries [state].
 */
@Serializable
data class RunEvent(
    val type: String = "",
    val runId: OptString = null,
    val seq: Int? = null,
    val model: RunModel? = null,
    val text: String = "",
    val iteration: Int = 0,
    val maxIterations: Int = 0,
    val delta: String = "",
    val id: String = "",
    val name: String = "Tool Execution",
    val args: JsonElement? = null,
    val result: JsonElement? = null,
    val ok: Boolean = true,
    val durationMs: Long? = null,
    val content: String = "",
    val shortcut: Boolean = false,
    val message: String = "",
    val reason: OptString = null,
    val summary: String = "",
    /** The voice socket's error code (`session_busy`). */
    val code: OptString = null,
    @Transient val undos: List<UndoAction> = emptyList(),
    @Transient val selection: SelectionRequest? = null,
    @Transient val state: SessionState? = null
) {
    /** The arguments as JSON text, the way an approval sends them back ("null" when absent). */
    val argsText: String get() = args?.toString() ?: "null"
}

/** A `POST /api/assistant/approve` action: approve, deny, select, extend_iterations, decline_iterations, cancel. */
@Serializable
data class RunAction(
    val sessionId: String,
    val action: String,
    val device: String? = null,
    val toolCallId: String? = null,
    val selection: List<Int>? = null
)

@Serializable
private data class ModelWire(val id: String = "", val label: OptString = null, val description: String = "", val showsReasoning: Boolean = false)

@Serializable
private data class CatalogWire(val default: String = "", val models: LenientList<ModelWire> = emptyList())

@Serializable
private data class SessionItemWire(
    val sessionId: String = "",
    val updatedAt: OptString = null,
    val snippet: String = "Empty chat",
    val status: String = "idle",
    val title: String = "New Chat"
)

@Serializable
private data class PendingWire(
    val toolName: JsonText = null,
    val toolArgs: JsonText = null,
    val toolCallId: String = "",
    val approvalReason: OptString = null,
    val untrustedContent: Boolean = false
)

@Serializable
private data class SelectionOptionWire(val label: String = "", val value: String = "")

@Serializable
private data class SelectionWire(val prompt: String = "", val options: LenientList<SelectionOptionWire> = emptyList(), val multiple: Boolean = false) {
    fun toModel() = SelectionRequest(prompt, options.map { SelectionOption(it.label, it.value) }, multiple)
}

@Serializable
private data class ActiveRunWire(val runId: String = "")

@Serializable
private data class StateWire(
    val status: String = "idle",
    val messages: JsonArray? = null,
    val context: ChatContextWire? = null,
    val undos: JsonArray? = null,
    val pendingApproval: PendingWire? = null,
    val selectionRequest: SelectionWire? = null,
    val activeRun: ActiveRunWire? = null,
    val gapMinutes: Double? = null,
    val shortcut: Boolean = false,
    val hasMore: Boolean = false,
    val before: OptString = null,
    val extendSummary: String = "",
    val error: OptString = null
)

@Serializable
private data class MessageBody(
    val message: String,
    val sessionId: String,
    val model: String? = null,
    val device: String? = null,
    val screen: ScreenBody? = null,
    val deviceScreen: JsonObject? = null
)

@Serializable
private data class ScreenBody(val kind: String, val id: String)

@Serializable
private data class VoiceStop(
    val event: String = "stop",
    val sessionId: String,
    val sampleRate: Int,
    val screen: ScreenBody? = null,
    val deviceScreen: JsonObject? = null
)

@Serializable
private data class BusyWire(val activeRun: ActiveRunWire? = null)

@Serializable
private data class NewSession(val sessionId: String, val title: String)

/** The conversation: messages and their run streams, approvals, sessions and the shared thread. */
class ChatApi(private val http: ApiClient) {

    /**
     * Send a message and follow its run. [device] stamps it in the shared
     * thread (`phone`, `watch`, `tablet`, `tv`); [screen] is what is open while
     * asking (that turn only); [deviceScreen] is the app under the
     * power-button sheet when the owner included it.
     */
    suspend fun send(
        message: String,
        sessionId: String,
        model: String?,
        onEvent: (RunEvent) -> Unit,
        device: String? = null,
        screen: ScreenRef? = null,
        deviceScreen: DeviceScreen? = null
    ) {
        val body = MessageBody(
            message, sessionId, model?.ifEmpty { null }, device?.ifEmpty { null },
            screen?.let { ScreenBody(it.kind, it.id) }, deviceScreen?.toJson()
        )
        follow(http.request("/api/assistant/message") { post(jsonBody(MessageBody.serializer(), body)) }, onEvent)
    }

    /** [send] with an image (multipart); the screenshot of a shared screen goes this way too. */
    suspend fun sendWithImage(
        message: String,
        sessionId: String,
        model: String?,
        imageBytes: ByteArray,
        mimeType: String,
        onEvent: (RunEvent) -> Unit,
        device: String? = null,
        screen: ScreenRef? = null,
        deviceScreen: DeviceScreen? = null
    ) {
        val multipart = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("message", message)
            .addFormDataPart("image", "image.jpg", imageBytes.toRequestBody(mimeType.toMediaTypeOrNull()))
        if (!device.isNullOrEmpty()) multipart.addFormDataPart("device", device)
        screen?.let { multipart.addFormDataPart("screen", ApiJson.encodeToString(ScreenBody.serializer(), ScreenBody(it.kind, it.id))) }
        deviceScreen?.let { multipart.addFormDataPart("device_screen", it.toJson().toString()) }
        val query = "session_id=${enc(sessionId)}" + (if (!model.isNullOrEmpty()) "&model=${enc(model)}" else "")
        follow(http.request("/api/assistant/message_with_image?$query") { post(multipart.build()) }, onEvent)
    }

    /**
     * Follow the session's current (or just-finished) run: replays every event
     * after [afterSeq] of [runId], then streams live until the run ends. With no
     * run to follow the server sends only the closing `state` frame.
     */
    suspend fun attach(sessionId: String, runId: String?, afterSeq: Int, onEvent: (RunEvent) -> Unit) {
        val query = "after=$afterSeq" + (if (!runId.isNullOrEmpty()) "&run_id=${enc(runId)}" else "")
        follow(http.request("/api/sessions/${enc(sessionId)}/stream?$query"), onEvent)
    }

    /** Answer what the run is waiting for ([RunAction]) and follow the run on. */
    suspend fun act(action: RunAction, onEvent: (RunEvent) -> Unit) =
        follow(http.request("/api/assistant/approve") { post(jsonBody(RunAction.serializer(), action)) }, onEvent)

    /** Stop the session's run; false when the server didn't take it. */
    suspend fun cancel(sessionId: String): Boolean = try {
        http.send(http.request("/api/assistant/approve") { post(jsonBody(RunAction.serializer(), RunAction(sessionId, "cancel"))) })
    } catch (e: Exception) {
        if (e is kotlinx.coroutines.CancellationException) throw e
        Log.w(TAG, "Failed to cancel assistant", e)
        false
    }

    suspend fun models(): ModelCatalog {
        val w = http.decode(CatalogWire.serializer(), http.text(http.request("/api/assistant/models"), "Failed to fetch models", "{}"))
        return ModelCatalog(
            w.default,
            w.models.filter { it.id.isNotEmpty() }.map { ModelOption(it.id, it.label ?: it.id, it.description, it.showsReasoning) }
        )
    }

    suspend fun sessions(): List<SessionItem> =
        http.decode(ListSerializer(SessionItemWire.serializer()), http.text(http.request("/api/sessions"), "Failed to fetch sessions", "[]"), "[]")
            .map { SessionItem(it.sessionId, parseServerTimestamp(it.updatedAt) ?: 0L, it.snippet, it.status, it.title) }

    suspend fun session(sessionId: String): SessionState =
        parseState(http.text(http.request("/api/sessions/$sessionId"), "Failed to fetch session detail", "{}"))

    /**
     * A page of the shared thread (`GET /api/sessions/main`): the newest
     * [limit] messages, or the ones before the cursor [before], with
     * `has_more`, `before` and `gap_minutes`.
     */
    suspend fun thread(limit: Int = 60, before: String? = null): SessionState {
        val query = "limit=$limit" + (if (!before.isNullOrEmpty()) "&before=${enc(before)}" else "")
        return parseState(http.text(http.request("/api/sessions/main?$query"), "Failed to fetch the thread", "{}"))
    }

    /**
     * "New topic" in the shared thread: Dana forgets it so far (messages stay).
     * Returns the thread state; [ApiException] 409 while a run is going.
     */
    suspend fun clearThread(device: String): SessionState =
        parseState(http.post("/api/sessions/main/clear", buildJsonObject { put("device", device) }, "Couldn't start a new topic"))

    suspend fun createSession(sessionId: String, title: String): Boolean =
        http.send(http.request("/api/sessions") { post(jsonBody(NewSession.serializer(), NewSession(sessionId, title))) })

    suspend fun deleteSession(sessionId: String): Boolean = http.send(http.request("/api/sessions/$sessionId") { delete() })

    /** The voice socket's request: PCM goes up it, the run's events come down (`/api/assistant/voice_stream`). */
    fun voiceRequest(serverUrl: String, apiToken: String, sessionId: String, model: String?, device: String?): Request {
        val m = model?.let { "&model=" + enc(it) }.orEmpty()
        val d = device?.let { "&device=$it" }.orEmpty()
        val url = serverUrl.replaceFirst("http", "ws") + "/api/assistant/voice_stream?session_id=$sessionId$m$d"
        return Request.Builder().url(url).bearerAuth(apiToken).build()
    }

    private suspend fun follow(request: Request, onEvent: (RunEvent) -> Unit) =
        http.stream(request, onError = { code, body -> throw failure(code, body) }) { _, data ->
            parseEvent(data)?.let(onEvent)
        }

    companion object {
        private const val TAG = "ChatApi"

        /** The 409 of a session that is already running ([SessionBusyException]), else the server's `error`. */
        private fun failure(code: Int, body: String): Exception {
            if (code == 409) {
                val runId = runCatching { ApiJson.decodeFromString(BusyWire.serializer(), body).activeRun?.runId }.getOrNull()
                return SessionBusyException(runId?.takeIf { it.isNotEmpty() })
            }
            val text = body.take(300)
            val message = HttpResult(code, text).error.ifEmpty { text }
            return ApiException(code, "Assistant request failed (HTTP $code): $message")
        }

        /** One stream or voice-socket frame; null for `[DONE]`, blanks and frames that aren't events. */
        fun parseEvent(data: String): RunEvent? {
            if (data.isEmpty() || data == "[DONE]") return null
            return try {
                val obj = ApiJson.parseToJsonElement(data) as? JsonObject ?: return null
                val event = ApiJson.decodeFromJsonElement(RunEvent.serializer(), obj)
                event.copy(
                    undos = ThreadModel.parseUndos(obj["undos"] as? JsonArray),
                    selection = if (event.type == "selection_required") ApiJson.decodeFromJsonElement(SelectionWire.serializer(), obj).toModel() else null,
                    state = (obj["state"] as? JsonObject)?.let(::parseState)
                )
            } catch (e: Exception) {
                Log.w(TAG, "Ignoring unparseable event frame", e)
                null
            }
        }

        fun parseState(text: String): SessionState = parseState(ApiJson.parseToJsonElement(text.ifBlank { "{}" }) as? JsonObject ?: JsonObject(emptyMap()))

        fun parseState(obj: JsonObject): SessionState {
            val w = ApiJson.decodeFromJsonElement(StateWire.serializer(), obj)
            return SessionState(
                status = w.status,
                messages = ChatHistoryCodec.decode(w.messages),
                hasContext = obj.containsKey("context"),
                context = w.context?.toModel(),
                undos = if (obj.containsKey("undos")) ThreadModel.parseUndos(w.undos) else null,
                pendingApproval = w.pendingApproval?.let {
                    PendingApprovalState(
                        it.toolName, it.toolArgs, it.toolCallId,
                        it.approvalReason ?: if (it.untrustedContent) "device_screen" else ""
                    )
                },
                selection = w.selectionRequest?.toModel(),
                activeRunId = w.activeRun?.runId,
                memory = ThreadModel.parseMemory(obj),
                gapMinutes = w.gapMinutes?.takeIf { it > 0 },
                shortcut = w.shortcut,
                hasMore = w.hasMore,
                before = w.before?.takeIf { it.isNotBlank() },
                extendSummary = w.extendSummary,
                error = w.error?.takeIf { it.isNotBlank() }
            )
        }

        /** The `stop` frame that ends an utterance on the voice socket. */
        fun voiceStop(sessionId: String, sampleRate: Int, screen: ScreenRef?, deviceScreen: DeviceScreen?): String =
            ApiJson.encodeToString(
                VoiceStop.serializer(),
                VoiceStop(sessionId = sessionId, sampleRate = sampleRate, screen = screen?.let { ScreenBody(it.kind, it.id) }, deviceScreen = deviceScreen?.toJson())
            )

        /**
         * Parse a session's `updated_at`. SQLite's CURRENT_TIMESTAMP writes
         * "YYYY-MM-DD HH:MM:SS" in UTC; Python writes ISO-8601, with or
         * without fractions and offset. Returns epoch millis, or null.
         */
        fun parseServerTimestamp(raw: String?): Long? {
            val text = raw?.trim().orEmpty()
            if (text.isEmpty() || text == "null") return null
            text.toLongOrNull()?.let { n -> return if (n < 100_000_000_000L) n * 1000 else n }
            var t = text.replace(' ', 'T')
            // Drop fractional seconds; SimpleDateFormat can't take micros.
            t = t.replace(Regex("""(T\d{2}:\d{2}:\d{2})\.\d+"""), "$1")
            val hasZone = t.endsWith("Z") || Regex("""[+-]\d{2}:?\d{2}$""").containsMatchIn(t.substringAfter('T', ""))
            if (!hasZone) t += "Z"
            t = t.replace(Regex("Z$"), "+0000").replace(Regex("""([+-]\d{2}):(\d{2})$"""), "$1$2")
            for (pattern in listOf("yyyy-MM-dd'T'HH:mm:ssZ", "yyyy-MM-dd'T'HH:mmZ")) {
                try {
                    val fmt = java.text.SimpleDateFormat(pattern, java.util.Locale.US)
                    fmt.isLenient = false
                    return fmt.parse(t)?.time
                } catch (_: java.text.ParseException) {
                    // Not this format: try the next one.
                }
            }
            return null
        }
    }
}
