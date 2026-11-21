package app.ganam.assistant.data.network

import app.ganam.assistant.data.models.CalendarEvent
import app.ganam.assistant.data.models.CallDetail
import app.ganam.assistant.data.models.CallLabel
import app.ganam.assistant.data.models.CallSuggestion
import app.ganam.assistant.data.models.InboxSummaries
import app.ganam.assistant.data.models.Memory
import app.ganam.assistant.data.models.NotificationApp
import app.ganam.assistant.data.models.NotificationCounts
import app.ganam.assistant.data.models.PhoneNotification
import app.ganam.assistant.data.models.PhoneCall
import app.ganam.assistant.data.models.Task
import app.ganam.assistant.data.models.TaskGroup
import app.ganam.assistant.data.models.ZoneItem
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.Response
import okhttp3.RequestBody.Companion.toRequestBody
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okio.source
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** Non-2xx answer from the server. Transport failures stay [IOException]. */
class ApiException(val code: Int, message: String) : Exception(message)

/**
 * The session already has a run in progress (HTTP 409). [runId] is that run,
 * so the caller can attach to it instead of sending again.
 */
class SessionBusyException(val runId: String?) : Exception("This chat is still working on the previous request")

class ApiService(private val serverUrl: String, private val apiToken: String) {

    companion object {
        /** For services and workers that run without the ViewModel. */
        fun fromPrefs(context: android.content.Context): ApiService {
            val prefs = context.getSharedPreferences("GanamPrefs", android.content.Context.MODE_PRIVATE)
            return ApiService(
                prefs.getString("server_url", "").orEmpty().trim().trimEnd('/'),
                prefs.getString("api_token", "").orEmpty()
            )
        }
    }

    private val client = HttpClients.base
    private val streamingClient = HttpClients.streaming

    /**
     * Cancellable `enqueue`. If the coroutine is cancelled, the HTTP call is
     * cancelled too (blocking `execute()` inside `withContext(IO)` can't be).
     */
    private suspend fun Call.await(): Response = suspendCancellableCoroutine { continuation ->
        continuation.invokeOnCancellation { cancel() }
        enqueue(object : Callback {
            override fun onResponse(call: Call, response: Response) {
                if (continuation.isActive) {
                    continuation.resume(response)
                } else {
                    response.close()
                }
            }
            override fun onFailure(call: Call, e: IOException) {
                if (continuation.isActive) {
                    continuation.resumeWithException(e)
                }
            }
        })
    }

    /**
     * Run [request] and return its body, closing the response in every case.
     * Non-2xx answers throw [ApiException]; transport failures throw
     * [IOException]. An empty body yields [default].
     */
    private suspend fun fetch(request: Request, what: String, default: String = ""): String =
        withContext(Dispatchers.IO) {
            client.newCall(request).await().use { response ->
                if (!response.isSuccessful) {
                    throw ApiException(response.code, "$what (HTTP ${response.code})")
                }
                response.body?.string().orEmpty().ifEmpty { default }
            }
        }

    /**
     * Run [request] for its status only, closing the response. Returns whether
     * the server accepted it; transport failures still throw [IOException] so
     * callers can tell "rejected" from "unreachable".
     */
    private suspend fun send(request: Request): Boolean =
        client.newCall(request).await().use { it.isSuccessful }

    /** Like [send], but returns the HTTP status so callers can retry 5xx. */
    private suspend fun sendForCode(request: Request): Int =
        client.newCall(request).await().use { it.code }

    private fun buildRequest(urlPath: String, builder: Request.Builder.() -> Unit = {}): Request {
        return Request.Builder().url("$serverUrl$urlPath").bearerAuth(apiToken).apply(builder).build()
    }

    // --- CALENDAR EVENTS ---
    suspend fun getCalendarEvents(): String = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/calendar/events")
        fetch(request, "Failed to fetch calendar events", "[]")
    }

    /** Events between two local times ("yyyy-MM-ddTHH:mm:ss", the server's zone). */
    suspend fun getCalendarEventsBetween(start: String, end: String): List<CalendarEvent> = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/calendar/events?start_date=${enc(start)}&end_date=${enc(end)}")
        val arr = JSONArray(fetch(request, "Failed to fetch calendar events", "[]"))
        (0 until arr.length()).mapNotNull { i ->
            val o = arr.optJSONObject(i) ?: return@mapNotNull null
            CalendarEvent(
                id = o.optString("id"),
                title = o.optString("summary").ifBlank { "(No title)" },
                start = o.optString("start").takeIf { it.isNotBlank() && it != "null" },
                end = o.optString("end").takeIf { it.isNotBlank() && it != "null" },
                allDay = o.optBoolean("all_day", false),
                location = o.optString("location").takeIf { it.isNotBlank() && it != "null" }
            )
        }
    }

    // --- TASKS DUE SOON ---
    suspend fun getTasksDueSoon(): String = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/tasks")
        val jsonString = fetch(request, "Failed to fetch tasks", "[]")
        try {
            val originalArray = org.json.JSONArray(jsonString)
            val filteredArray = org.json.JSONArray()
            for (i in 0 until originalArray.length()) {
                val obj = originalArray.getJSONObject(i)
                val status = obj.optString("status", "")
                if (status == "needs_action") {
                    filteredArray.put(obj)
                }
            }
            filteredArray.toString()
        } catch (e: Exception) {
            "[]"
        }
    }

    // --- TASKS ---
    suspend fun getTasks(): List<Task> = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/tasks")
        val jsonString = fetch(request, "Failed to fetch tasks", "[]")
        val jsonArray = JSONArray(jsonString)
        val tasks = mutableListOf<Task>()
        for (i in 0 until jsonArray.length()) {
            val obj = jsonArray.getJSONObject(i)
            tasks.add(
                Task(
                    id = obj.optString("id"),
                    title = obj.optString("title"),
                    description = obj.optString("description").takeIf { it.isNotEmpty() && it != "null" },
                    priority = obj.optString("priority", "medium"),
                    dueDate = obj.optString("due_date").takeIf { it.isNotEmpty() && it != "null" },
                    status = obj.optString("status", "needs_action"),
                    triggers = obj.optString("triggers").takeIf { it.isNotEmpty() && it != "null" },
                    createdAt = obj.optString("created_at").takeIf { it.isNotEmpty() && it != "null" },
                    updatedAt = obj.optString("updated_at").takeIf { it.isNotEmpty() && it != "null" },
                    groupId = obj.optString("group_id").takeIf { it.isNotEmpty() && it != "null" },
                    groupName = obj.optString("group_name").takeIf { it.isNotEmpty() && it != "null" },
                    groupEmoji = obj.optString("group_emoji").takeIf { it.isNotEmpty() && it != "null" },
                    groupColor = obj.optString("group_color").takeIf { it.isNotEmpty() && it != "null" },
                    links = CallJson.taskLinks(obj.optJSONArray("links"))
                )
            )
        }
        tasks
    }

    suspend fun createTaskDetailed(
        title: String,
        description: String?,
        priority: String,
        dueDate: String?,
        triggers: JSONObject?,
        groupId: String? = null
    ): Boolean = withContext(Dispatchers.IO) {
        val jsonPayload = JSONObject().apply {
            put("title", title)
            description?.let { put("description", it) }
            put("priority", priority)
            dueDate?.let { put("due_date", it) }
            put("triggers", triggers ?: JSONObject())
            groupId?.let { put("group_id", it) }
        }
        val requestBody = jsonPayload.toString().toRequestBody("application/json".toMediaTypeOrNull())
        val request = buildRequest("/api/tasks") { post(requestBody) }
        send(request)
    }

    suspend fun updateTaskDetailed(
        taskId: String,
        title: String,
        description: String?,
        priority: String,
        dueDate: String?,
        status: String,
        triggers: JSONObject?,
        groupId: String? = null
    ): Boolean = withContext(Dispatchers.IO) {
        val jsonPayload = JSONObject().apply {
            put("title", title)
            description?.let { put("description", it) }
            put("priority", priority)
            dueDate?.let { put("due_date", it) }
            put("status", status)
            put("triggers", triggers ?: JSONObject())
            groupId?.let { put("group_id", it) }
        }
        val requestBody = jsonPayload.toString().toRequestBody("application/json".toMediaTypeOrNull())
        val request = buildRequest("/api/tasks/$taskId") { put(requestBody) }
        send(request)
    }

    /** Create a task and return its id (null if the server didn't say). */
    suspend fun createTaskReturningId(title: String, groupId: String?): String? = withContext(Dispatchers.IO) {
        val payload = JSONObject().put("title", title).put("priority", "medium").put("triggers", JSONObject())
        groupId?.let { payload.put("group_id", it) }
        val request = buildRequest("/api/tasks") { post(jsonBody(payload)) }
        JSONObject(fetch(request, "Failed to create task", "{}")).optString("id").takeIf { it.isNotBlank() }
    }

    /** `PUT /api/tasks/{id}` with only the given fields; returns the HTTP status (for the offline queue). */
    suspend fun updateTaskFieldsCode(taskId: String, fields: JSONObject): Int = withContext(Dispatchers.IO) {
        sendForCode(buildRequest("/api/tasks/$taskId") { put(jsonBody(fields)) })
    }

    suspend fun updateTaskStatus(taskId: String, newStatus: String): Boolean = withContext(Dispatchers.IO) {
        val jsonPayload = JSONObject().apply {
            put("status", newStatus)
        }
        val requestBody = jsonPayload.toString().toRequestBody("application/json".toMediaTypeOrNull())
        val request = buildRequest("/api/tasks/$taskId") { put(requestBody) }
        send(request)
    }

    suspend fun snoozeTask(taskId: String, payload: JSONObject): Boolean = withContext(Dispatchers.IO) {
        val requestBody = payload.toString().toRequestBody("application/json".toMediaTypeOrNull())
        val request = buildRequest("/api/tasks/$taskId/snooze") { post(requestBody) }
        send(request)
    }

    /** [updateTaskStatus] returning the HTTP status (for the offline queue). */
    suspend fun updateTaskStatusCode(taskId: String, newStatus: String): Int = withContext(Dispatchers.IO) {
        val body = JSONObject().put("status", newStatus).toString()
            .toRequestBody("application/json".toMediaTypeOrNull())
        sendForCode(buildRequest("/api/tasks/$taskId") { put(body) })
    }

    /** [snoozeTask] returning the HTTP status (for the offline queue). */
    suspend fun snoozeTaskCode(taskId: String, payload: JSONObject): Int = withContext(Dispatchers.IO) {
        val body = payload.toString().toRequestBody("application/json".toMediaTypeOrNull())
        sendForCode(buildRequest("/api/tasks/$taskId/snooze") { post(body) })
    }

    suspend fun deleteTask(taskId: String): Boolean = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/tasks/$taskId") { delete() }
        send(request)
    }

    // --- TASK GROUPS ---
    suspend fun getTaskGroups(): List<TaskGroup> = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/task-groups")
        val jsonString = fetch(request, "Failed to fetch task groups", "[]")
        val jsonArray = JSONArray(jsonString)
        val groups = mutableListOf<TaskGroup>()
        for (i in 0 until jsonArray.length()) {
            val obj = jsonArray.getJSONObject(i)
            groups.add(
                TaskGroup(
                    id = obj.optString("id"),
                    name = obj.optString("name"),
                    emoji = obj.optString("emoji"),
                    color = obj.optString("color"),
                    description = obj.optString("description").takeIf { it.isNotEmpty() && it != "null" },
                    sortOrder = obj.optInt("sort_order", 0),
                    taskCount = if (obj.has("task_count") && !obj.isNull("task_count")) obj.optInt("task_count") else null
                )
            )
        }
        groups
    }

    suspend fun createTaskGroup(name: String, emoji: String, color: String, description: String?): Boolean = withContext(Dispatchers.IO) {
        val jsonPayload = JSONObject().apply {
            put("name", name)
            put("emoji", emoji)
            put("color", color)
            description?.let { put("description", it) }
        }
        val requestBody = jsonPayload.toString().toRequestBody("application/json".toMediaTypeOrNull())
        val request = buildRequest("/api/task-groups") { post(requestBody) }
        send(request)
    }

    suspend fun updateTaskGroup(id: String, name: String, emoji: String, color: String, description: String?): Boolean = withContext(Dispatchers.IO) {
        val jsonPayload = JSONObject().apply {
            put("name", name)
            put("emoji", emoji)
            put("color", color)
            description?.let { put("description", it) }
        }
        val requestBody = jsonPayload.toString().toRequestBody("application/json".toMediaTypeOrNull())
        val request = buildRequest("/api/task-groups/$id") { put(requestBody) }
        send(request)
    }

    suspend fun deleteTaskGroup(id: String): Boolean = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/task-groups/$id") { delete() }
        send(request)
    }

    // --- MEMORIES ---
    suspend fun getMemories(): List<Memory> = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/memories")
        val jsonString = fetch(request, "Failed to fetch memories", "[]")
        val jsonArray = JSONArray(jsonString)
        val memories = mutableListOf<Memory>()
        for (i in 0 until jsonArray.length()) {
            val obj = jsonArray.getJSONObject(i)
            memories.add(
                Memory(
                    key = obj.optString("key"),
                    value = obj.optString("value"),
                    category = obj.optString("category", "general"),
                    updatedAt = obj.optString("updated_at").takeIf { it.isNotEmpty() && it != "null" }
                )
            )
        }
        memories
    }

    suspend fun createMemory(key: String, value: String, category: String = "general"): Boolean = withContext(Dispatchers.IO) {
        val jsonPayload = JSONObject().apply {
            put("key", key)
            put("value", value)
            put("category", category)
        }
        val requestBody = jsonPayload.toString().toRequestBody("application/json".toMediaTypeOrNull())
        val request = buildRequest("/api/memories") { post(requestBody) }
        send(request)
    }

    suspend fun deleteMemory(key: String): Boolean = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/memories/$key") { delete() }
        send(request)
    }

    // --- PROJECTS: JSON goes through jsonCall (data/projects/ProjectsApi); files need the raw client ---

    /**
     * `POST /api/entries/{id}/files`: multipart `file` (streamed from [open], [size] bytes or -1)
     * and `note`. Returns the HTTP status and body (413 = too big). The name is percent-encoded
     * (the server unquotes it), so Hebrew names arrive intact.
     */
    suspend fun uploadEntryFile(
        entryId: String, name: String, mime: String, size: Long, note: String, open: () -> java.io.InputStream
    ): Pair<Int, String> = withContext(Dispatchers.IO) {
        val type = mime.ifBlank { "application/octet-stream" }.toMediaTypeOrNull()
        val body = object : okhttp3.RequestBody() {
            override fun contentType() = type
            override fun contentLength() = size
            override fun writeTo(sink: okio.BufferedSink) {
                open().use { input -> input.source().use { sink.writeAll(it) } }
            }
        }
        val form = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("file", android.net.Uri.encode(name), body)
            .apply { if (note.isNotBlank()) addFormDataPart("note", note.trim()) }
            .build()
        val request = buildRequest("/api/entries/${enc(entryId)}/files") { post(form) }
        HttpClients.upload.newCall(request).await().use { it.code to (it.body?.string().orEmpty()) }
    }

    /** GET a server path (a project file's `url`) into [dest]; returns the HTTP status. */
    suspend fun downloadTo(path: String, dest: File): Int = withContext(Dispatchers.IO) {
        HttpClients.upload.newCall(buildRequest(path)).await().use { response ->
            if (response.isSuccessful) {
                dest.parentFile?.mkdirs()
                response.body?.byteStream()?.use { input -> dest.outputStream().use { input.copyTo(it) } }
            }
            response.code
        }
    }
    // --- CHAT & APPROVAL (Server-Sent Events) ---

    /**
     * Consume an assistant SSE stream, invoking [onEvent] for every `data:`
     * frame. The server sends a `state` frame last carrying the authoritative
     * session state, so terminal handling needs no per-endpoint special casing.
     *
     * `: heartbeat` comment frames are keepalives and are skipped.
     */
    private suspend fun streamAssistant(
        request: Request,
        onEvent: (JSONObject) -> Unit
    ) = withContext(Dispatchers.IO) {
        val response = streamingClient.newCall(request).await()
        try {
            if (response.code == 409) {
                val body = response.body?.string().orEmpty()
                val runId = runCatching {
                    JSONObject(body).optJSONObject("active_run")?.optString("run_id")
                }.getOrNull()
                throw SessionBusyException(runId?.takeIf { it.isNotEmpty() })
            }
            if (!response.isSuccessful) {
                val errBody = response.body?.string().orEmpty().take(300)
                val message = runCatching { JSONObject(errBody).optString("error") }.getOrNull()
                    ?.takeIf { it.isNotEmpty() } ?: errBody
                throw ApiException(response.code, "Assistant request failed (HTTP ${response.code}): $message")
            }
            val source = response.body?.source() ?: throw Exception("Empty response body")
            readSseFrames(source) { _, data -> dispatchSseFrame(data, onEvent) }
        } finally {
            response.close()
        }
    }

    /**
     * Read server-sent events from [source] until it ends: [onFrame] gets each
     * frame's `event:` name (null when it has none) and its `data:` lines
     * joined. Comment lines (`: ping`, `: heartbeat`) are keepalives.
     */
    private fun readSseFrames(source: okio.BufferedSource, onFrame: (event: String?, data: String) -> Unit) {
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

    private fun dispatchSseFrame(raw: String, onEvent: (JSONObject) -> Unit) {
        if (raw.isEmpty() || raw == "[DONE]") return
        try {
            onEvent(JSONObject(raw))
        } catch (e: Exception) {
            Log.w("ApiService", "Ignoring unparseable SSE frame", e)
        }
    }

    private fun assistantJsonRequest(urlPath: String, payload: JSONObject): Request {
        val body = payload.toString().toRequestBody("application/json".toMediaTypeOrNull())
        return buildRequest(urlPath) { post(body) }
    }

    /**
     * Send a message. [device] stamps it in the shared thread (`phone`,
     * `watch`, `tablet`, `tv`); [screen] is what is open while asking
     * (`{kind, id}`, that turn only); [deviceScreen] is the app under the
     * power-button sheet when the owner included it (`device_screen`).
     */
    suspend fun streamMessage(
        message: String,
        sessionId: String,
        model: String?,
        onEvent: (JSONObject) -> Unit,
        device: String? = null,
        screen: app.ganam.assistant.ui.viewmodels.ScreenRef? = null,
        deviceScreen: app.ganam.assistant.data.screen.DeviceScreen? = null
    ) = streamAssistant(
        assistantJsonRequest(
            "/api/assistant/message",
            JSONObject().apply {
                put("message", message)
                put("session_id", sessionId)
                if (!model.isNullOrEmpty()) put("model", model)
                if (!device.isNullOrEmpty()) put("device", device)
                screen?.let { put("screen", screenJson(it)) }
                deviceScreen?.let { put("device_screen", it.toJson()) }
            }
        ),
        onEvent
    )

    private fun screenJson(s: app.ganam.assistant.ui.viewmodels.ScreenRef) = JSONObject().put("kind", s.kind).put("id", s.id)

    suspend fun streamMessageWithImage(
        message: String,
        sessionId: String,
        model: String?,
        imageBytes: ByteArray,
        mimeType: String,
        onEvent: (JSONObject) -> Unit,
        device: String? = null,
        screen: app.ganam.assistant.ui.viewmodels.ScreenRef? = null,
        deviceScreen: app.ganam.assistant.data.screen.DeviceScreen? = null
    ) {
        val imageBody = imageBytes.toRequestBody(mimeType.toMediaTypeOrNull())
        val multipart = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("message", message)
            .addFormDataPart("image", "image.jpg", imageBody)
        if (!device.isNullOrEmpty()) multipart.addFormDataPart("device", device)
        screen?.let { multipart.addFormDataPart("screen", screenJson(it).toString()) }
        deviceScreen?.let { multipart.addFormDataPart("device_screen", it.toJson().toString()) }
        val query = "session_id=${enc(sessionId)}" + (if (!model.isNullOrEmpty()) "&model=${enc(model)}" else "")
        val request = buildRequest("/api/assistant/message_with_image?$query") {
            post(multipart.build())
        }
        streamAssistant(request, onEvent)
    }

    /**
     * Follow the session's current (or just-finished) run: replays every event
     * after [afterSeq] of [runId], then streams live until the run ends. With no
     * run to follow the server sends only the closing `state` frame.
     */
    suspend fun attachRunStream(
        sessionId: String,
        runId: String?,
        afterSeq: Int,
        onEvent: (JSONObject) -> Unit
    ) {
        val query = buildString {
            append("after=").append(afterSeq)
            if (!runId.isNullOrEmpty()) append("&run_id=").append(enc(runId))
        }
        streamAssistant(buildRequest("/api/sessions/${enc(sessionId)}/stream?$query"), onEvent)
    }

    /** The chat model catalog: `{"default": id, "models": [{id, label, description, shows_reasoning}]}`. */
    suspend fun getAssistantModels(): String = withContext(Dispatchers.IO) {
        fetch(buildRequest("/api/assistant/models"), "Failed to fetch models", "{}")
    }

    private fun enc(value: String): String = java.net.URLEncoder.encode(value, "UTF-8")

    /** `action` is one of: approve, deny, extend_iterations, decline_iterations, select, cancel, stop, interrupt. */
    suspend fun streamApproveAction(payload: JSONObject, onEvent: (JSONObject) -> Unit) =
        streamAssistant(assistantJsonRequest("/api/assistant/approve", payload), onEvent)

    suspend fun cancelAssistant(sessionId: String): Boolean = withContext(Dispatchers.IO) {
        try {
            val payload = JSONObject().apply {
                put("session_id", sessionId)
                put("action", "cancel")
            }
            val body = payload.toString().toRequestBody("application/json".toMediaTypeOrNull())
            val request = buildRequest("/api/assistant/approve") { post(body) }
            send(request)
        } catch (e: Exception) {
            Log.w("ApiService", "Failed to cancel assistant", e)
            false
        }
    }

    // --- AUTHORITATIVE SERVER SESSIONS ---
    suspend fun getSessions(): String = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/sessions")
        fetch(request, "Failed to fetch sessions", "[]")
    }

    suspend fun getSession(sessionId: String): String = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/sessions/$sessionId")
        fetch(request, "Failed to fetch session detail", "{}")
    }

    /**
     * A page of the shared thread (`GET /api/sessions/main`): the newest
     * [limit] messages, or the ones before the cursor [before]. The answer is
     * the usual session state plus `has_more`, `before`, `gap_minutes`.
     */
    suspend fun getThread(limit: Int = 60, before: String? = null): String = withContext(Dispatchers.IO) {
        val query = "limit=$limit" + (if (!before.isNullOrEmpty()) "&before=${enc(before)}" else "")
        fetch(buildRequest("/api/sessions/main?$query"), "Failed to fetch the thread", "{}")
    }

    /**
     * "New topic" in the shared thread: Dana forgets it so far (messages stay).
     * Returns the thread state; [ApiException] 409 while a run is going.
     */
    suspend fun clearThread(device: String): JSONObject = withContext(Dispatchers.IO) {
        postForJson("/api/sessions/main/clear", JSONObject().put("device", device), "Couldn't start a new topic")
    }

    suspend fun createSession(sessionId: String, title: String): String = withContext(Dispatchers.IO) {
        val payload = JSONObject().apply {
            put("session_id", sessionId)
            put("title", title)
        }
        val requestBody = payload.toString().toRequestBody("application/json".toMediaTypeOrNull())
        val request = buildRequest("/api/sessions") { post(requestBody) }
        fetch(request, "Failed to create session", "{}")
    }

    suspend fun deleteSession(sessionId: String): Boolean = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/sessions/$sessionId") { delete() }
        send(request)
    }

    // --- ZONES ---
    suspend fun getZones(): List<ZoneItem> = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/zones")
        val jsonString = fetch(request, "Failed to fetch zones", "[]")
        val jsonArray = JSONArray(jsonString)
        val zones = mutableListOf<ZoneItem>()
        for (i in 0 until jsonArray.length()) {
            val obj = jsonArray.getJSONObject(i)
            zones.add(
                ZoneItem(
                    name = obj.optString("name"),
                    entityId = obj.optString("entity_id")
                )
            )
        }
        zones
    }

    // --- CALLS & VOICE MEMOS ---

    /**
     * Upload a recording. The server answers right away (202, `status:
     * processing`) and analyses it in the background; poll [getCallDetail].
     * A re-upload of the same recording returns the existing call with
     * `duplicate: true`. Throws [ApiException] on 5xx so callers can retry;
     * returns null when the server rejected the file (4xx).
     */
    suspend fun uploadCall(
        file: File,
        caller: String,
        receiver: String,
        callTimestamp: String,
        instructions: String? = null,
        durationSeconds: Int = 0,
        direction: String? = null,
        phoneNumber: String? = null
    ): JSONObject? = withContext(Dispatchers.IO) {
        val fileBody = file.asRequestBody("audio/mp4".toMediaTypeOrNull())
        val builder = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("caller", caller)
            .addFormDataPart("receiver", receiver)
            .addFormDataPart("call_timestamp", callTimestamp)
        if (!instructions.isNullOrBlank()) builder.addFormDataPart("instructions", instructions.trim())
        if (durationSeconds > 0) builder.addFormDataPart("duration_seconds", durationSeconds.toString())
        if (!direction.isNullOrBlank()) builder.addFormDataPart("direction", direction)
        if (!phoneNumber.isNullOrBlank()) builder.addFormDataPart("phone_number", phoneNumber)
        builder.addFormDataPart("audio", file.name, fileBody)

        val request = buildRequest("/api/calls/upload") { post(builder.build()) }
        HttpClients.upload.newCall(request).await().use { response ->
            if (!response.isSuccessful && response.code >= 500) {
                throw ApiException(response.code, "Server error ${response.code}")
            }
            if (!response.isSuccessful) return@use null
            runCatching { JSONObject(response.body?.string().orEmpty()) }.getOrDefault(JSONObject())
        }
    }

    suspend fun uploadCallRecording(
        file: File,
        caller: String,
        receiver: String,
        callTimestamp: String,
        instructions: String? = null
    ): Boolean = uploadCall(file, caller, receiver, callTimestamp, instructions) != null

    suspend fun getCalls(): List<PhoneCall> = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/calls")
        CallJson.calls(JSONArray(fetch(request, "Failed to fetch calls", "[]")))
    }

    suspend fun getCallDetail(callId: String): CallDetail = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/calls/${enc(callId)}")
        CallJson.detail(JSONObject(fetch(request, "Failed to fetch call", "{}")))
    }

    suspend fun getCallLabels(): List<CallLabel> = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/calls/labels")
        val arr = JSONArray(fetch(request, "Failed to fetch labels", "[]"))
        CallJson.labels(arr)
    }

    suspend fun updateCallLabels(callId: String, labelIds: List<String>): Boolean = withContext(Dispatchers.IO) {
        val body = JSONObject().put("labels", JSONArray(labelIds)).toString()
            .toRequestBody("application/json".toMediaTypeOrNull())
        send(buildRequest("/api/calls/${enc(callId)}") { put(body) })
    }

    // --- CALL SUGGESTIONS ---
    /** [source]: call (calls only — the server's default), notification, or all. */
    suspend fun getSuggestions(status: String = "pending", source: String = "call"): List<CallSuggestion> = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/suggestions?status=${enc(status)}&source=${enc(source)}")
        CallJson.suggestions(JSONArray(fetch(request, "Failed to fetch suggestions", "[]")))
    }

    suspend fun dismissSuggestions(ids: List<String>): Int = withContext(Dispatchers.IO) {
        val body = JSONObject().put("ids", JSONArray(ids)).put("action", "dismiss").toString()
            .toRequestBody("application/json".toMediaTypeOrNull())
        JSONObject(fetch(buildRequest("/api/suggestions/bulk") { post(body) }, "Couldn't dismiss", "{}")).optInt("dismissed")
    }

    // --- PHONE NOTIFICATIONS ---
    /** Upload captured notifications + shade removals; returns the server's answer (with `cancel_keys`). */
    suspend fun postNotificationBatch(
        posted: JSONArray, removed: JSONArray, activeKeys: JSONArray, snoozedKeys: JSONArray = JSONArray()
    ): JSONObject =
        withContext(Dispatchers.IO) {
            val body = JSONObject().put("posted", posted).put("removed", removed).put("active_keys", activeKeys)
                .put("snoozed_keys", snoozedKeys)
                .toString().toRequestBody("application/json".toMediaTypeOrNull())
            JSONObject(fetch(buildRequest("/api/notifications/batch") { post(body) }, "Notification upload failed", "{}"))
        }

    suspend fun getNotifications(
        status: String = "active", bucket: String? = null, query: String? = null, limit: Int = 300
    ): Pair<List<PhoneNotification>, NotificationCounts> = withContext(Dispatchers.IO) {
        val qs = buildString {
            append("?status=").append(enc(status)).append("&limit=").append(limit)
            bucket?.let { append("&bucket=").append(enc(it)) }
            query?.takeIf { it.isNotBlank() }?.let { append("&q=").append(enc(it)) }
        }
        val json = JSONObject(fetch(buildRequest("/api/notifications$qs"), "Failed to fetch notifications", "{}"))
        NotificationJson.notifications(json.optJSONArray("items")) to NotificationJson.counts(json.optJSONObject("counts"))
    }

    /** Dismiss / archive / restore. Returns the device keys to cancel in the shade. */
    suspend fun setNotificationStatus(
        ids: List<String>, status: String, bucket: String? = null, dismissSuggestions: Boolean = false
    ): List<String> = withContext(Dispatchers.IO) {
        val payload = JSONObject().put("ids", JSONArray(ids)).put("status", status)
        bucket?.let { payload.put("bucket", it) }
        if (dismissSuggestions) payload.put("dismiss_suggestions", true)
        val body = payload.toString().toRequestBody("application/json".toMediaTypeOrNull())
        val json = JSONObject(fetch(buildRequest("/api/notifications/status") { post(body) }, "Couldn't update notifications", "{}"))
        json.optJSONArray("device_keys").toStringList()
    }

    /** The user saw these on the alert screen: the server stops re-nudging. */
    suspend fun markNotificationsSeen(ids: List<String>) = withContext(Dispatchers.IO) {
        val body = JSONObject().put("ids", JSONArray(ids)).toString().toRequestBody("application/json".toMediaTypeOrNull())
        fetch(buildRequest("/api/notifications/seen") { post(body) }, "Couldn't mark notifications seen", "{}")
        Unit
    }

    /**
     * Snooze until [untilMillis] (epoch ms), or wake them with null. Returns
     * the shade keys to snooze on this phone → their end time.
     */
    suspend fun snoozeNotifications(ids: List<String>, untilMillis: Long?): Map<String, Long> = withContext(Dispatchers.IO) {
        val payload = JSONObject().put("ids", JSONArray(ids))
        if (untilMillis == null) payload.put("clear", true)
        else payload.put("until", java.time.Instant.ofEpochMilli(untilMillis).toString())
        val body = payload.toString().toRequestBody("application/json".toMediaTypeOrNull())
        val json = JSONObject(fetch(buildRequest("/api/notifications/snooze") { post(body) }, "Couldn't snooze", "{}"))
        NotificationJson.snoozeKeys(json.optJSONArray("device_keys"))
    }

    /** AI summary per app + the Inbox briefing (cached on the server; never waits for the model). */
    /** A notification button pressed on the web: what to do (`action`, `key`, `text`, `status`). */
    suspend fun getNotificationRequest(id: String): JSONObject = withContext(Dispatchers.IO) {
        JSONObject(fetch(buildRequest("/api/notifications/requests/${java.net.URLEncoder.encode(id, "UTF-8")}"), "Couldn't fetch the request", "{}"))
    }

    /** Tell the web whether the phone did it. */
    suspend fun finishNotificationRequest(id: String, ok: Boolean, error: String?) = withContext(Dispatchers.IO) {
        val payload = JSONObject().put("ok", ok)
        if (!error.isNullOrBlank()) payload.put("error", error)
        val body = payload.toString().toRequestBody("application/json".toMediaTypeOrNull())
        fetch(buildRequest("/api/notifications/requests/${java.net.URLEncoder.encode(id, "UTF-8")}/result") { post(body) }, "Couldn't report the result", "{}")
        Unit
    }

    // --- MONEY (the server's `money` kind: bank sign-in codes, bank / card app notifications) ---

    /** `GET /api/money/code-requests/{id}`: what the server waits for (`status`, `senders`, `code_regex`, `expires_at`). */
    suspend fun getMoneyCodeRequest(id: String): JSONObject = withContext(Dispatchers.IO) {
        JSONObject(fetch(buildRequest("/api/money/code-requests/${enc(id)}"), "Couldn't fetch the code request", "{}"))
    }

    /**
     * Hand the server the sign-in code from the bank's SMS. Returns the HTTP
     * status only (200 taken, 400 `bad_code`, 404, 409 `not_pending`);
     * unreachable throws [IOException]. Nothing here logs the code.
     */
    suspend fun postMoneyCode(id: String, code: String): Int = withContext(Dispatchers.IO) {
        sendForCode(buildRequest("/api/money/code-requests/${enc(id)}") { post(jsonBody(JSONObject().put("code", code))) })
    }

    /** `POST /api/money/signals`: bank / card app notifications. HTTP status + body (`accepted`, or `error`). */
    suspend fun postMoneySignals(notifications: JSONArray): Pair<Int, JSONObject> = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/money/signals") { post(jsonBody(JSONObject().put("notifications", notifications))) }
        client.newCall(request).await().use { r ->
            r.code to runCatching { JSONObject(r.body?.string().orEmpty()) }.getOrDefault(JSONObject())
        }
    }

    // --- DIRECT PUSH (FCM wake-up → fetch events) ---

    /** Register this phone's FCM token; returns `latest_seq`. */
    suspend fun registerPush(deviceId: String, token: String, name: String, appVersion: String): JSONObject = withContext(Dispatchers.IO) {
        val body = JSONObject().put("device_id", deviceId).put("token", token).put("name", name)
            .put("app_version", appVersion).toString().toRequestBody("application/json".toMediaTypeOrNull())
        JSONObject(fetch(buildRequest("/api/phone/register") { post(body) }, "Couldn't register for push", "{}"))
    }

    /** Forget one of the user's coding-agent sessions (the live notification's Hide). */
    suspend fun hideAgentSession(id: String): Boolean = withContext(Dispatchers.IO) {
        send(buildRequest("/api/agent-sessions/${java.net.URLEncoder.encode(id, "UTF-8")}") { delete() })
    }

    /** Events newer than [after]: `{events: [{seq, type, data}], latest}`. */
    suspend fun getPhoneEvents(after: Long, deviceId: String): JSONObject = withContext(Dispatchers.IO) {
        val q = "after=$after&device_id=${java.net.URLEncoder.encode(deviceId, "UTF-8")}"
        JSONObject(fetch(buildRequest("/api/phone/events?$q"), "Couldn't fetch phone events", "{}"))
    }

    suspend fun getNotificationSummaries(): InboxSummaries = withContext(Dispatchers.IO) {
        NotificationJson.summaries(JSONObject(fetch(buildRequest("/api/notifications/summaries"), "Failed to fetch summaries", "{}")))
    }

    suspend fun getNotificationApps(): List<NotificationApp> = withContext(Dispatchers.IO) {
        val json = JSONObject(fetch(buildRequest("/api/notifications/apps"), "Failed to fetch apps", "{}"))
        NotificationJson.apps(json.optJSONArray("apps"))
    }

    /** [action]: mute | important | alert | none. */
    suspend fun setNotificationRule(packageName: String, action: String, sender: String = ""): Boolean = withContext(Dispatchers.IO) {
        val body = JSONObject().put("package", packageName).put("sender", sender).put("action", action).put("apply", true)
            .toString().toRequestBody("application/json".toMediaTypeOrNull())
        send(buildRequest("/api/notifications/rules") { post(body) })
    }

    /** Same rule change, answered with the server's wording and an undo id (restores the rule and the buckets it moved). */
    suspend fun changeNotificationRule(packageName: String, action: String): app.ganam.assistant.data.models.ActResult =
        withContext(Dispatchers.IO) {
            NowJson.actResult(postForJson("/api/notifications/rules",
                JSONObject().put("package", packageName).put("sender", "").put("action", action).put("apply", true),
                "Couldn't save the rule"))
        }

    suspend fun deleteNotificationsForApp(packageName: String): Boolean = withContext(Dispatchers.IO) {
        send(buildRequest("/api/notifications?package=${enc(packageName)}") { delete() })
    }

    /** Create the task/event; [overrides] replaces payload fields. Throws [ApiException] with the server's reason. */
    suspend fun acceptSuggestion(id: String, overrides: JSONObject = JSONObject()): JSONObject = withContext(Dispatchers.IO) {
        val body = overrides.toString().toRequestBody("application/json".toMediaTypeOrNull())
        val request = buildRequest("/api/suggestions/${enc(id)}/accept") { post(body) }
        client.newCall(request).await().use { response ->
            val text = response.body?.string().orEmpty()
            val json = runCatching { JSONObject(text) }.getOrDefault(JSONObject())
            if (!response.isSuccessful) {
                throw ApiException(response.code, json.optString("error").ifBlank { "Couldn't create it (HTTP ${response.code})" })
            }
            json
        }
    }

    suspend fun updateSuggestion(id: String, changes: JSONObject): CallSuggestion? = withContext(Dispatchers.IO) {
        val body = changes.toString().toRequestBody("application/json".toMediaTypeOrNull())
        val request = buildRequest("/api/suggestions/${enc(id)}") { put(body) }
        runCatching { CallJson.suggestion(JSONObject(fetch(request, "Failed to update suggestion", "{}"))) }.getOrNull()
    }

    suspend fun dismissSuggestion(id: String): Boolean = withContext(Dispatchers.IO) {
        val body = "{}".toRequestBody("application/json".toMediaTypeOrNull())
        send(buildRequest("/api/suggestions/${enc(id)}/dismiss") { post(body) })
    }

    // --- PEOPLE / CALL CARD / BRIEFING / WEEKLY REVIEW (server docs/people.md) ---

    private fun jsonBody(o: JSONObject) = o.toString().toRequestBody("application/json".toMediaTypeOrNull())

    suspend fun getPeople(sort: String = "recent", query: String = ""): List<app.ganam.assistant.data.models.Person> =
        withContext(Dispatchers.IO) {
            val json = JSONObject(fetch(buildRequest("/api/people?sort=${enc(sort)}&q=${enc(query)}"), "Failed to load people", "{}"))
            val arr = json.optJSONArray("people") ?: JSONArray()
            (0 until arr.length()).map { app.ganam.assistant.data.models.Person.from(arr.getJSONObject(it)) }
        }

    suspend fun getPerson(id: String): app.ganam.assistant.data.models.PersonDetail = withContext(Dispatchers.IO) {
        app.ganam.assistant.data.models.PersonDetail.from(JSONObject(fetch(buildRequest("/api/people/${enc(id)}"), "Failed to load person", "{}")))
    }

    /** PUT /api/people/{id} with any of name, relation, notes, birthday, pinned, hidden, phones, aliases. */
    suspend fun updatePerson(id: String, changes: JSONObject): Boolean = withContext(Dispatchers.IO) {
        send(buildRequest("/api/people/${enc(id)}") { put(jsonBody(changes)) })
    }

    suspend fun refreshPersonBrief(id: String): Boolean = withContext(Dispatchers.IO) {
        send(buildRequest("/api/people/${enc(id)}/brief") { post(jsonBody(JSONObject())) })
    }

    /** The incoming-call card for [phone], or null when the caller isn't known (no model call on the server). */
    suspend fun lookupCaller(phone: String): app.ganam.assistant.data.models.PersonCard? = withContext(Dispatchers.IO) {
        val json = JSONObject(fetch(buildRequest("/api/people/lookup?phone=${enc(phone)}"), "Lookup failed", "{}"))
        json.optJSONObject("card")?.takeIf { json.optBoolean("found") }?.let { app.ganam.assistant.data.models.PersonCard.from(it) }
    }

    /** Today's wake briefing; [generate] makes it now when there's none yet. */
    suspend fun getBriefingToday(generate: Boolean = false): app.ganam.assistant.data.models.Briefing? = withContext(Dispatchers.IO) {
        val json = JSONObject(fetch(buildRequest("/api/briefing/today${if (generate) "?generate=1" else ""}"), "Failed to load the briefing", "{}"))
        json.optJSONObject("briefing")?.let { app.ganam.assistant.data.models.Briefing.from(it) }
    }

    suspend fun getLatestReview(): app.ganam.assistant.data.models.WeeklyReview? = withContext(Dispatchers.IO) {
        val json = JSONObject(fetch(buildRequest("/api/review/latest"), "Failed to load the review", "{}"))
        json.optJSONObject("review")?.let { app.ganam.assistant.data.models.WeeklyReview.from(it) }
    }

    suspend fun generateReview(): app.ganam.assistant.data.models.WeeklyReview? = withContext(Dispatchers.IO) {
        val json = JSONObject(fetch(buildRequest("/api/review/generate") { post(jsonBody(JSONObject())) }, "Couldn't make a review", "{}"))
        json.optJSONObject("review")?.let { app.ganam.assistant.data.models.WeeklyReview.from(it) }
    }

    /** Apply the review with the user's choices. Returns {applied, failed, items}. */
    suspend fun applyReview(id: String, items: List<app.ganam.assistant.data.models.ReviewItem>): JSONObject = withContext(Dispatchers.IO) {
        val body = JSONObject().put("items", JSONArray(items.map { it.toJson() }))
        JSONObject(fetch(buildRequest("/api/review/${enc(id)}/apply") { post(jsonBody(body)) }, "Couldn't apply the review", "{}"))
    }

    suspend fun dismissReview(id: String): Boolean = withContext(Dispatchers.IO) {
        send(buildRequest("/api/review/${enc(id)}/dismiss") { post(jsonBody(JSONObject())) })
    }

    /** The phone was unlocked (the wake signal). Returns the server's status: confirming | done | outside_window. */
    suspend fun reportUnlock(atMillis: Long): String = withContext(Dispatchers.IO) {
        JSONObject(fetch(buildRequest("/api/phone/unlock") { post(jsonBody(JSONObject().put("at", atMillis))) }, "Unlock report failed", "{}"))
            .optString("status")
    }

    // --- JSON calls (shopping: server docs/shopping.md, data/shopping/; money: data/money/MoneyApi) ---

    /**
     * One JSON request: [method] on [path] with an optional JSON [body].
     * Returns the HTTP status and the body text; an unreachable server throws
     * [IOException] (the caller queues the change).
     */
    suspend fun jsonCall(method: String, path: String, body: JSONObject? = null): Pair<Int, String> = withContext(Dispatchers.IO) {
        val request = buildRequest(path) {
            val rb = body?.let { jsonBody(it) }
            when (method) {
                "GET" -> get()
                "POST" -> post(rb ?: jsonBody(JSONObject()))
                "PUT" -> put(rb ?: jsonBody(JSONObject()))
                "PATCH" -> patch(rb ?: jsonBody(JSONObject()))
                "DELETE" -> if (rb != null) delete(rb) else delete()
                else -> throw IllegalArgumentException("Unknown method $method")
            }
        }
        client.newCall(request).await().use { it.code to (it.body?.string().orEmpty()) }
    }

    /**
     * Follow `GET /api/shopping/events` (the caller's active list, or [listId])
     * until the server closes it or the connection drops: [onEvent] gets
     * `changed {list_id, version}`, `lists {}` and `end {}`. A non-2xx answer
     * throws [ApiException].
     */
    suspend fun shoppingEvents(listId: String?, onEvent: (String, JSONObject) -> Unit) = withContext(Dispatchers.IO) {
        val path = "/api/shopping/events" + (listId?.let { "?list=${enc(it)}" } ?: "")
        val response = streamingClient.newCall(buildRequest(path) { header("Accept", "text/event-stream") }).await()
        response.use {
            if (!it.isSuccessful) throw ApiException(it.code, "Shopping stream (HTTP ${it.code})")
            val source = it.body?.source() ?: return@use
            readSseFrames(source) { event, data ->
                val json = runCatching { JSONObject(data) }.getOrNull() ?: JSONObject()
                onEvent(event ?: "message", json)
            }
        }
    }

    /** `GET /api/me`: who the token belongs to and which integrations they have (`kinds`). */
    suspend fun getMe(): JSONObject = withContext(Dispatchers.IO) {
        JSONObject(fetch(buildRequest("/api/me"), "Failed to load your account", "{}"))
    }

    suspend fun deleteCall(callId: String): Boolean = withContext(Dispatchers.IO) {
        val request = buildRequest("/api/calls/$callId") { delete() }
        send(request)
    }

    suspend fun reanalyzeCall(callId: String, customInstructions: String? = null): Boolean = withContext(Dispatchers.IO) {
        val jsonPayload = JSONObject()
        if (!customInstructions.isNullOrBlank()) {
            jsonPayload.put("custom_instructions", customInstructions.trim())
        }
        val requestBody = jsonPayload.toString().toRequestBody("application/json".toMediaTypeOrNull())
        val request = buildRequest("/api/calls/$callId/reanalyze") { post(requestBody) }
        send(request)
    }

    // --- NOW, NOTIFICATIONS SCREEN, SEARCH, UNDO (server docs/now.md) ---

    /**
     * POST [path] with [payload] and return the JSON answer. A non-2xx answer
     * throws [ApiException] carrying the server's `error` text (shown to the user).
     */
    private suspend fun postForJson(path: String, payload: JSONObject, failure: String): JSONObject =
        client.newCall(buildRequest(path) { post(jsonBody(payload)) }).await().use { response ->
            val text = response.body?.string().orEmpty()
            val json = runCatching { JSONObject(text) }.getOrDefault(JSONObject())
            if (!response.isSuccessful) {
                throw ApiException(response.code, json.optString("error").ifBlank { "$failure (HTTP ${response.code})" })
            }
            json
        }

    /** The raw `/api/now` answer (cached on disk so the screen is never blank). */
    suspend fun getNowRaw(): String = withContext(Dispatchers.IO) {
        fetch(buildRequest("/api/now"), "Failed to load Now", "{}")
    }

    /** Act on a Now card. Throws [ApiException] with the server's reason (400 / 404 / 409 / 500). */
    suspend fun nowAct(id: String, action: String, args: JSONObject): app.ganam.assistant.data.models.ActResult =
        withContext(Dispatchers.IO) {
            val payload = JSONObject().put("id", id).put("action", action)
            if (args.length() > 0) payload.put("args", args)
            NowJson.actResult(postForJson("/api/now/act", payload, "That didn't work"))
        }

    /** Result of `POST /api/undo/{id}`. */
    enum class UndoOutcome { DONE, TOO_LATE }

    /** Undo an action by its `undo_id`. 404 = expired / already used; a refused undo throws with the reason. */
    suspend fun undo(undoId: String): UndoOutcome = withContext(Dispatchers.IO) {
        try {
            postForJson("/api/undo/${enc(undoId)}", JSONObject(), "Couldn't undo")
            UndoOutcome.DONE
        } catch (e: ApiException) {
            if (e.code == 404) UndoOutcome.TOO_LATE else throw e
        }
    }

    suspend fun getNotificationsOverview(): app.ganam.assistant.data.models.NotificationsOverview = withContext(Dispatchers.IO) {
        NowJson.overview(JSONObject(fetch(buildRequest("/api/notifications/overview"), "Failed to load notifications", "{}")))
    }

    /** Active notifications of one app, newest first. */
    suspend fun getAppNotifications(packageName: String, limit: Int = 100): List<PhoneNotification> = withContext(Dispatchers.IO) {
        val json = JSONObject(fetch(buildRequest("/api/notifications?status=active&package=${enc(packageName)}&limit=$limit"),
            "Failed to fetch notifications", "{}"))
        NotificationJson.notifications(json.optJSONArray("items"))
    }

    suspend fun getNotification(id: String): PhoneNotification? = withContext(Dispatchers.IO) {
        runCatching { NotificationJson.notification(JSONObject(fetch(buildRequest("/api/notifications/${enc(id)}"), "Failed to fetch the notification", "{}"))) }
            .getOrNull()?.takeIf { it.packageName.isNotBlank() }
    }

    /**
     * Clear notifications in Dana: by [ids], a whole app ([packageName]) or a
     * bucket (`dismiss` = the noise). Returns the shade keys to cancel and the undo id.
     */
    suspend fun clearNotifications(
        ids: List<String> = emptyList(), packageName: String? = null, bucket: String? = null
    ): app.ganam.assistant.data.models.ActResult = withContext(Dispatchers.IO) {
        val payload = JSONObject().put("status", "dismissed")
        if (ids.isNotEmpty()) payload.put("ids", JSONArray(ids))
        packageName?.let { payload.put("package", it) }
        bucket?.let { payload.put("bucket", it) }
        val json = postForJson("/api/notifications/status", payload, "Couldn't clear")
        val n = json.optInt("updated")
        NowJson.actResult(json, if (n == 1) "Cleared 1" else "Cleared $n")
    }

    /** "Dana noticed": accept (the app gets the mute rule) or decline (not offered again for 60 days). */
    suspend fun answerRuleSuggestion(packageName: String, accept: Boolean): app.ganam.assistant.data.models.ActResult =
        withContext(Dispatchers.IO) {
            NowJson.actResult(postForJson("/api/notifications/rule-suggestions",
                JSONObject().put("package", packageName).put("accept", accept), "Couldn't save that"))
        }

    suspend fun search(query: String, limit: Int = 5): List<app.ganam.assistant.data.models.SearchResult> = withContext(Dispatchers.IO) {
        NowJson.searchResults(JSONObject(fetch(buildRequest("/api/search?q=${enc(query)}&limit=$limit"), "Search failed", "{}")))
    }

    // --- About you, learned phrases, self-check (server docs/about.md, agent-actions.md, now.md) ---

    suspend fun getAbout(): app.ganam.assistant.data.models.AboutPage = withContext(Dispatchers.IO) {
        AboutJson.page(JSONObject(fetch(buildRequest("/api/about"), "Failed to load About you", "{}")))
    }

    /** Add a fact; [section] null lets the server guess. Returns the stored fact (or the one already known) and its undo id. */
    suspend fun addAboutFact(text: String, section: String?): Pair<app.ganam.assistant.data.models.AboutFact?, String?> = withContext(Dispatchers.IO) {
        val payload = JSONObject().put("text", text)
        if (!section.isNullOrEmpty()) payload.put("section", section)
        val json = postForJson("/api/about/facts", payload, "Couldn't add it")
        json.optJSONObject("fact")?.let { AboutJson.fact(it) } to json.optString("undo_id").takeIf { it.isNotBlank() && it != "null" }
    }

    /** Edit a fact's text and/or section. Returns the undo id. */
    suspend fun updateAboutFact(id: String, text: String?, section: String?): String? = withContext(Dispatchers.IO) {
        val payload = JSONObject()
        text?.let { payload.put("text", it) }
        section?.let { payload.put("section", it) }
        val json = sendJsonForJson(buildRequest("/api/about/facts/${enc(id)}") { put(jsonBody(payload)) }, "Couldn't save it")
        json.optString("undo_id").takeIf { it.isNotBlank() && it != "null" }
    }

    /** Forget a fact. Returns the undo id. */
    suspend fun deleteAboutFact(id: String): String? = withContext(Dispatchers.IO) {
        val json = sendJsonForJson(buildRequest("/api/about/facts/${enc(id)}") { delete() }, "Couldn't remove it")
        json.optString("undo_id").takeIf { it.isNotBlank() && it != "null" }
    }

    suspend fun getShortcuts(): List<app.ganam.assistant.data.models.LearnedPhrase> = withContext(Dispatchers.IO) {
        AboutJson.shortcuts(JSONObject(fetch(buildRequest("/api/shortcuts"), "Failed to load learned phrases", "{}")))
    }

    suspend fun deleteShortcut(id: String): Boolean = withContext(Dispatchers.IO) {
        send(buildRequest("/api/shortcuts/${enc(id)}") { delete() })
    }

    suspend fun getChecks(): List<app.ganam.assistant.data.models.SelfCheck> = withContext(Dispatchers.IO) {
        AboutJson.checks(JSONObject(fetch(buildRequest("/api/checks"), "Failed to load the self-check", "{}")))
    }

    /** Ignore a failing check for [days] (0 = stop ignoring). */
    suspend fun ignoreCheck(key: String, days: Int = 7): app.ganam.assistant.data.models.ActResult = withContext(Dispatchers.IO) {
        NowJson.actResult(postForJson("/api/checks/${enc(key)}/ignore", JSONObject().put("days", days), "Couldn't ignore it"), "Ignored for a week")
    }

    /** Like [postForJson] for any method: a non-2xx answer throws [ApiException] with the server's `error`. */
    private suspend fun sendJsonForJson(request: Request, failure: String): JSONObject =
        client.newCall(request).await().use { response ->
            val text = response.body?.string().orEmpty()
            val json = runCatching { JSONObject(text) }.getOrDefault(JSONObject())
            if (!response.isSuccessful) {
                throw ApiException(response.code, json.optString("error").ifBlank { "$failure (HTTP ${response.code})" })
            }
            json
        }
}

private fun JSONArray?.toStringList(): List<String> {
    if (this == null) return emptyList()
    val list = mutableListOf<String>()
    for (i in 0 until length()) {
        val value = opt(i)
        if (value is String && value.isNotBlank()) list.add(value)
    }
    return list
}
