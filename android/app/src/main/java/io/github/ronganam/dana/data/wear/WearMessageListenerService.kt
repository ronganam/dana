package io.github.ronganam.dana.data.wear

import io.github.ronganam.dana.data.network.TaskSnooze
import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.protocol.ReminderHandled
import io.github.ronganam.dana.protocol.SnoozeRequest
import io.github.ronganam.dana.protocol.TaskActionReply
import io.github.ronganam.dana.protocol.TaskStatusRequest
import io.github.ronganam.dana.protocol.WearPaths
import android.content.Context
import android.util.Log
import io.github.ronganam.dana.data.network.ApiService
import io.github.ronganam.dana.data.network.ChatApi
import io.github.ronganam.dana.data.network.RunAction
import io.github.ronganam.dana.data.network.RunEvent
import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.models.SelectionOption
import io.github.ronganam.dana.data.models.SelectionRequest
import io.github.ronganam.dana.data.sync.SubmitResult
import io.github.ronganam.dana.data.sync.TaskActionQueue
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.Wearable
import com.google.android.gms.wearable.WearableListenerService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.RequestBody.Companion.toRequestBody
import okio.ByteString.Companion.toByteString
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class WearMessageListenerService : WearableListenerService() {

    private companion object {
        const val TAG = "WearMessageListener"

        /** Wearable data-layer messages cap at 4 KB; keep tool names bounded. */
        const val MAX_WEAR_TOOL_CALLS = 25

        /**
         * Leave headroom under the 4 KB envelope for the surrounding JSON.
         * The LLM's final answer is unbounded, so it is the field that must be
         * trimmed — a single oversize reply otherwise fails to send entirely and
         * the watch sits waiting forever.
         */
        const val MAX_RESPONSE_CHARS = 1500
        const val MAX_SELECTION_OPTIONS = 6
        const val MAX_OPTION_CHARS = 80

        /** Byte budget safely under the 4 KB data-layer message envelope. */
        const val MAX_PAYLOAD_BYTES = 3800

        /** At most one partial-reply update to the watch per this interval. */
        const val PARTIAL_INTERVAL_MS = 400L

        /**
         * Every watch turn goes to Dana's shared thread, whatever session id
         * the watch sends (older watch builds still make up their own).
         */
        const val THREAD = "main"
        const val DEVICE = "watch"

        /** How much of the thread the watch gets when it opens. */
        const val THREAD_TURNS = 12
        const val MAX_THREAD_MESSAGE_CHARS = 280

        const val BUSY_TEXT = "Dana is still answering the last request. Try again when she's done."
    }

    /** Truncate to a character budget without splitting surrogate pairs. */
    private fun clamp(text: String, maxChars: Int): String =
        if (text.length <= maxChars) text else text.take(maxChars - 1) + "…"

    /** Bound the selection list so a verbose prompt cannot blow the payload. */
    private fun clampOptions(options: List<SelectionOption>): JSONArray {
        val out = JSONArray()
        options.take(MAX_SELECTION_OPTIONS).forEach { opt ->
            out.put(JSONObject().apply {
                put("label", clamp(opt.label, MAX_OPTION_CHARS))
                put("value", clamp(opt.value, MAX_OPTION_CHARS))
            })
        }
        return out
    }

    private fun selectionJson(sel: SelectionRequest) = JSONObject().apply {
        put("prompt", clamp(sel.prompt, MAX_OPTION_CHARS * 2))
        put("multiple", sel.multiple)
        put("options", clampOptions(sel.options))
    }

    private val job = SupervisorJob()
    private val scope = CoroutineScope(Dispatchers.IO + job)

    @Volatile private var activeWebSocket: WebSocket? = null
    private var activeNodeId: String? = null

    private fun getApiService(): ApiService = ServerConfig.load(this).api()

    /**
     * Folds a run's event stream into the compact JSON reply the watch shows.
     *
     * The watch cannot hold an open HTTP connection through the data layer, so
     * it gets one snapshot when the run finishes instead of live events. The
     * payload is deliberately small (Wearable data-layer messages cap at
     * 4 KB), carrying only what the watch UI renders:
     *
     *   status, response, transcription, tool_calls[], pending_approval,
     *   selection_request, extend_summary
     */
    private inner class WearReplyFolder {
        private val toolNames = JSONArray()
        private var response = ""
        private var status = "completed"
        private var transcription: String? = null
        private var pendingApproval: JSONObject? = null
        private var selectionRequest: JSONObject? = null
        private var extendSummary: JSONObject? = null

        fun onEvent(event: RunEvent) {
            when (event.type) {
                "transcription" -> transcription = event.text
                // Objects, not bare strings: the watch parses tool_name/name.
                // Capped so a long run cannot overflow the 4 KB message limit.
                "tool_start" -> if (toolNames.length() < MAX_WEAR_TOOL_CALLS) {
                    toolNames.put(
                        JSONObject().apply {
                            put("name", event.name)
                            put("tool_name", event.name)
                        }
                    )
                }
                "final" -> response = clamp(event.content, MAX_RESPONSE_CHARS)
                "error" -> {
                    status = "error"
                    response = if (event.code == "session_busy") BUSY_TEXT
                        else clamp(event.message, MAX_RESPONSE_CHARS)
                }
                "cancelled" -> response = "Stopped."
                "approval_required" -> {
                    status = "pending_approval"
                    pendingApproval = JSONObject().apply {
                        put("tool_name", event.name)
                        put("tool_call_id", event.id)
                    }
                }
                "selection_required" -> event.selection?.let {
                    status = "awaiting_selection"
                    selectionRequest = selectionJson(it)
                }
                "iterations_exceeded" -> {
                    status = "iterations_exceeded"
                    extendSummary = JSONObject().put("summary", event.summary)
                }
                "state" -> event.state?.let { st ->
                    status = st.status
                    st.extendSummary.takeIf { it.isNotEmpty() }?.let {
                        extendSummary = JSONObject().put("summary", it)
                    }
                    // A prompt we missed on the event stream still reaches the watch.
                    st.pendingApproval?.let { pa ->
                        status = "pending_approval"
                        pendingApproval = JSONObject().apply {
                            put("tool_name", pa.toolName ?: "Tool Execution")
                            put("tool_call_id", pa.toolCallId)
                        }
                    }
                    st.selection?.let { sel ->
                        status = "awaiting_selection"
                        selectionRequest = selectionJson(sel)
                    }
                }
            }
        }

        fun build(): JSONObject = fitPayload(JSONObject().apply {
            put("status", status)
            put("response", response)
            transcription?.let { put("transcription", it) }
            put("tool_calls", toolNames)
            pendingApproval?.let { put("pending_approval", it) }
            selectionRequest?.let { put("selection_request", it) }
            extendSummary?.let { put("extend_summary", it) }
        })
    }

    /**
     * Pushes a run's progress to the watch while it streams, ahead of the
     * folded final reply: the transcript the moment the server has it
     * (`{"transcription"}`), then the reply text so far
     * (`{"partial_response"}`, at most every [PARTIAL_INTERVAL_MS]). Watches
     * that predate this ignore both frames until the final one.
     */
    private inner class WearLiveForwarder(private val nodeId: String, private val path: String) {
        private val text = StringBuilder()
        private var lastSentAt = 0L
        private var sentLength = 0

        fun onEvent(event: RunEvent) {
            when (event.type) {
                "transcription" -> event.text.takeIf { it.isNotEmpty() }?.let {
                    send(JSONObject().put("transcription", it))
                }
                // A new LLM step (or a retried one) starts its text over.
                "step_start", "retry" -> {
                    text.setLength(0)
                    sentLength = 0
                }
                "text_delta" -> {
                    text.append(event.delta)
                    val now = android.os.SystemClock.uptimeMillis()
                    if (text.length > sentLength && now - lastSentAt >= PARTIAL_INTERVAL_MS) {
                        lastSentAt = now
                        sentLength = text.length
                        send(JSONObject().put("partial_response", clamp(text.toString(), MAX_RESPONSE_CHARS)))
                    }
                }
            }
        }

        private fun send(json: JSONObject) = sendReply(nodeId, path, json.toString().toByteArray())
    }

    /** Consume an assistant SSE stream on behalf of the watch; one folded reply. */
    private suspend fun runAssistantStream(
        live: WearLiveForwarder? = null,
        block: suspend (onEvent: (RunEvent) -> Unit) -> Unit
    ): JSONObject {
        val folder = WearReplyFolder()
        try {
            block { event ->
                live?.onEvent(event)
                folder.onEvent(event)
            }
        } catch (e: io.github.ronganam.dana.data.network.SessionBusyException) {
            // Another device's request is still running in the shared thread.
            return JSONObject().apply {
                put("status", "completed")
                put("response", BUSY_TEXT)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Assistant stream for watch failed", e)
            return JSONObject().apply {
                put("status", "error")
                put("response", e.message ?: "Stream failed")
            }
        }
        return folder.build()
    }

    /** The chat model picked on the phone, so watch requests use it too. */
    private fun chatModel(): String? =
        ServerConfig.prefs(this).getString("chat_model", null)?.ifEmpty { null }

    /**
     * Guarantee the reply fits the data-layer envelope. Tool history is the most
     * expendable field, so it is dropped first; the answer text is trimmed only
     * if the payload still does not fit.
     */
    private fun fitPayload(payload: JSONObject): JSONObject {
        fun size() = payload.toString().toByteArray(Charsets.UTF_8).size

        if (size() <= MAX_PAYLOAD_BYTES) return payload

        payload.optJSONArray("tool_calls")?.let { tools ->
            while (tools.length() > 0 && size() > MAX_PAYLOAD_BYTES) {
                tools.remove(tools.length() - 1)
            }
        }
        if (size() <= MAX_PAYLOAD_BYTES) return payload

        val response = payload.optString("response")
        var keep = response.length
        while (keep > 0 && size() > MAX_PAYLOAD_BYTES) {
            keep -= 80
            payload.put("response", clamp(response, keep.coerceAtLeast(0)))
        }
        if (size() > MAX_PAYLOAD_BYTES) {
            Log.w(TAG, "Assistant payload still over budget after trimming: ${size()}B")
        }
        return payload
    }

    /**
     * The thread's latest turns, small enough for one data-layer message:
     * id, text (clamped), who said it, tool name. Oldest turns go first
     * when it doesn't fit.
     */
    private fun slimThread(messages: List<ChatMessage>): JSONObject {
        val slim = ArrayList<JSONObject>(messages.size)
        for (m in messages) {
            slim += JSONObject().apply {
                put("id", m.id)
                put("isUser", m.isUser)
                put("message", clamp(m.message, MAX_THREAD_MESSAGE_CHARS))
                m.toolCall?.let { put("toolCall", JSONObject().put("toolName", it.toolName)) }
            }
        }
        fun build() = JSONObject().put("session_id", THREAD).put("messages", JSONArray(slim))
        var out = build()
        while (slim.size > 1 && out.toString().toByteArray(Charsets.UTF_8).size > MAX_PAYLOAD_BYTES) {
            slim.removeAt(0)
            out = build()
        }
        return out
    }

    override fun onMessageReceived(messageEvent: MessageEvent) {
        super.onMessageReceived(messageEvent)
        val path = messageEvent.path
        val nodeId = messageEvent.sourceNodeId
        val data = messageEvent.data

        // Voice frames are handled right here, in arrival order: launching
        // each on the IO pool could reorder audio chunks or run one before
        // the socket exists (it was then dropped). WebSocket calls only queue.
        when (path) {
            WearPaths.VOICE_START -> {
                startVoiceWebSocket(nodeId)
                Log.d(TAG, "Handled message path: $path from node: $nodeId")
                return
            }
            WearPaths.VOICE_CHUNK -> {
                activeWebSocket?.send(data.toByteString())
                return
            }
            WearPaths.VOICE_CANCEL -> {
                // The watch stopped listening without speech: drop the
                // stream instead of asking for a reply to silence.
                activeWebSocket?.close(1000, "Cancelled on watch")
                activeWebSocket = null
                return
            }
            WearPaths.VOICE_STOP -> {
                stopVoiceWebSocket()
                return
            }
        }

        scope.launch {
            try {
                when (path) {
                    // One conversation: the watch's menu and old builds' session lists see just the thread.
                    WearPaths.GET_SESSIONS, WearPaths.GET_RECENT_SESSIONS -> {
                        val one = JSONArray().put(JSONObject().put("session_id", THREAD).put("title", "Dana"))
                        sendReply(nodeId, WearPaths.response(WearPaths.GET_SESSIONS), one.toString().toByteArray())
                    }
                    WearPaths.GET_SESSION_DETAIL -> {
                        val thread = getApiService().chat.thread(THREAD_TURNS)
                        sendReply(nodeId, WearPaths.response(WearPaths.GET_SESSION_DETAIL), slimThread(thread.messages).toString().toByteArray())
                    }
                    // Older watch builds create a session per conversation; nothing to create now.
                    WearPaths.CREATE_SESSION -> {
                        sendReply(nodeId, WearPaths.response(WearPaths.CREATE_SESSION), JSONObject().put("session_id", THREAD).toString().toByteArray())
                    }
                    WearPaths.SEND_MESSAGE -> {
                        val json = JSONObject(String(data))
                        val msg = json.getString("message")
                        val responseJson = runAssistantStream(WearLiveForwarder(nodeId, WearPaths.response(WearPaths.SEND_MESSAGE))) { onEvent ->
                            getApiService().chat.send(msg, THREAD, chatModel(), onEvent, device = DEVICE)
                        }
                        sendReply(nodeId, WearPaths.response(WearPaths.SEND_MESSAGE), responseJson.toString().toByteArray())
                    }
                    WearPaths.APPROVE_ACTION -> {
                        val json = JSONObject(String(data))
                        val action = RunAction(
                            sessionId = THREAD,
                            action = json.optString("action"),
                            device = DEVICE,
                            toolCallId = json.optString("tool_call_id").takeIf { it.isNotEmpty() },
                            selection = json.optJSONArray("selection")?.let { a -> List(a.length()) { a.optInt(it) } }
                        )
                        val responseJson = runAssistantStream(WearLiveForwarder(nodeId, WearPaths.response(WearPaths.APPROVE_ACTION))) { onEvent ->
                            getApiService().chat.act(action, onEvent)
                        }
                        sendReply(nodeId, WearPaths.response(WearPaths.APPROVE_ACTION), responseJson.toString().toByteArray())
                    }
                    WearPaths.GET_TASKS -> {
                        val tasks = getApiService().tasks.tasks()
                        val jsonArray = JSONArray()
                        tasks.forEach { task ->
                            jsonArray.put(JSONObject().apply {
                                put("id", task.id)
                                put("title", task.title)
                                put("description", task.description ?: "")
                                put("priority", task.priority)
                                put("due_date", task.dueDate ?: "")
                                put("status", task.status)
                                put("group_name", task.groupName ?: "")
                                put("group_emoji", task.groupEmoji ?: "")
                            })
                        }
                        sendReply(nodeId, WearPaths.response(WearPaths.GET_TASKS), jsonArray.toString().toByteArray())
                    }
                    WearPaths.UPDATE_TASK_STATUS -> {
                        val req = TaskStatusRequest.fromJson(JSONObject(String(data)))
                        // Queued and retried if the server is unreachable.
                        val result = TaskActionQueue.setStatus(applicationContext, req.taskId, req.status)
                        val reply = TaskActionReply(success = result != SubmitResult.REJECTED, queued = result == SubmitResult.QUEUED)
                        sendReply(nodeId, WearPaths.response(WearPaths.UPDATE_TASK_STATUS), reply.toJson().toString().toByteArray())
                    }
                    WearPaths.SNOOZE_TASK -> {
                        // Any snooze kind the watch offers: time (minutes),
                        // place (location + event), car (action) or wake.
                        val req = SnoozeRequest.fromJson(JSONObject(String(data)))
                        val result = TaskActionQueue.snooze(applicationContext, req.taskId, TaskSnooze(req.type, req.minutes, req.location, req.event, req.action))
                        val reply = TaskActionReply(success = result != SubmitResult.REJECTED, queued = result == SubmitResult.QUEUED)
                        sendReply(nodeId, WearPaths.response(WearPaths.SNOOZE_TASK), reply.toJson().toString().toByteArray())
                    }
                    WearPaths.SUGGESTION_ACTION -> {
                        // Accept / Dismiss from the watch's "Waiting on you" tile.
                        val json = JSONObject(String(data))
                        val id = json.getString("id")
                        val ok = if (json.optString("action") == "accept") {
                            getApiService().calls.accept(id); true
                        } else getApiService().calls.dismiss(id)
                        sendReply(nodeId, WearPaths.response(WearPaths.SUGGESTION_ACTION), JSONObject().put("success", ok).put("id", id).toString().toByteArray())
                        io.github.ronganam.dana.DanaWidgets.refresh(applicationContext)
                    }
                    WearPaths.SHOPPING_ACTION -> {
                        // Tick / untick / add from the watch: the same path as a tap on the phone.
                        val json = JSONObject(String(data))
                        val outcome = io.github.ronganam.dana.data.shopping.ShoppingWatch.handle(applicationContext, json)
                        sendReply(nodeId, WearPaths.response(WearPaths.SHOPPING_ACTION), outcome.toString().toByteArray())
                    }
                    WearPaths.REMINDER_HANDLED_ON_WATCH -> {
                        WearReminderBridge.dismissOnPhone(applicationContext, ReminderHandled.fromJson(JSONObject(String(data))).taskId)
                    }
                }
                Log.d("WearMessageListener", "Handled message path: $path from node: $nodeId")
            } catch (e: Exception) {
                Log.e("WearMessageListener", "Error handling message path: $path", e)
                val errJson = JSONObject().apply { put("error", e.message ?: "Unknown error") }
                sendReply(nodeId, WearPaths.RESPONSE_ERROR, errJson.toString().toByteArray())
            }
        }
    }

    private fun startVoiceWebSocket(nodeId: String) {
        activeWebSocket?.close(1000, "Superseded by a new voice turn")
        activeWebSocket = null
        activeNodeId = nodeId
        val config = ServerConfig.load(this)
        val request = config.api().chat.voiceRequest(config.url, config.token, THREAD, chatModel(), DEVICE)

        val client = io.github.ronganam.dana.data.network.HttpClients.wearProxyWebSocket

        // The socket streams the run's events; the watch gets one folded reply.
        var folder = WearReplyFolder()
        var live = WearLiveForwarder(nodeId, WearPaths.RESPONSE_VOICE_STREAM)
        activeWebSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                val event = ChatApi.parseEvent(text) ?: return
                live.onEvent(event)
                when (event.type) {
                    "no_speech" -> {
                        val reply = JSONObject().apply {
                            put("status", "completed")
                            put("response", "I couldn't hear anything.")
                            put("transcription", "")
                        }
                        sendReply(nodeId, WearPaths.RESPONSE_VOICE_STREAM, reply.toString().toByteArray())
                    }
                    "state" -> {
                        folder.onEvent(event)
                        sendReply(nodeId, WearPaths.RESPONSE_VOICE_STREAM, folder.build().toString().toByteArray())
                        folder = WearReplyFolder()
                        live = WearLiveForwarder(nodeId, WearPaths.RESPONSE_VOICE_STREAM)
                    }
                    else -> folder.onEvent(event)
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e("WearMessageListener", "Proxy Voice WebSocket Failure: ${t.message}", t)
                val current = activeWebSocket
                if (current != null && current !== webSocket) return // a superseded turn's socket
                val errJson = JSONObject().apply { put("error", "Voice connection failed: " + t.message) }
                sendReply(nodeId, WearPaths.RESPONSE_VOICE_STREAM, errJson.toString().toByteArray())
                activeWebSocket = null
            }
        })
    }

    private fun stopVoiceWebSocket() {
        val ws = activeWebSocket ?: return
        try {
            ws.send(ChatApi.voiceStop(THREAD, 16000, null, null))
        } catch (e: Exception) {
            Log.e("WearMessageListener", "Error sending stop event to WebSocket", e)
        }
    }

    private fun sendReply(nodeId: String, path: String, data: ByteArray) {
        Wearable.getMessageClient(applicationContext).sendMessage(nodeId, path, data)
            .addOnFailureListener { e ->
                Log.e("WearMessageListener", "Failed to send reply to watch: $path", e)
            }
    }

    override fun onDestroy() {
        super.onDestroy()
        activeWebSocket?.close(1000, "Service destroyed")
    }
}
