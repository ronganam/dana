package app.ganam.assistant.chat

import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import app.ganam.assistant.data.network.ApiService
import app.ganam.assistant.ui.viewmodels.ChatController
import app.ganam.assistant.ui.viewmodels.ControllerContext
import app.ganam.assistant.ui.viewmodels.UserMessages
import app.ganam.assistant.ui.viewmodels.ScreenRef
import app.ganam.assistant.data.chat.UndoItem
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import app.ganam.assistant.data.network.ServerStatus
import app.ganam.assistant.data.network.ServerStatusMonitor
import app.ganam.assistant.data.screen.DeviceScreen
import app.ganam.assistant.data.screen.OutgoingScreen
import app.ganam.assistant.data.screen.ScreenAttachments
import app.ganam.assistant.data.voice.PcmStream
import app.ganam.assistant.data.voice.VoiceInput
import kotlinx.coroutines.flow.MutableStateFlow
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * Drives [ChatController] against a local HTTP server: the SSE event flow,
 * approval prompts, and following a run after the stream drops.
 */
class ChatControllerStreamTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private lateinit var server: MockWebServer
    private lateinit var scope: CoroutineScope
    private lateinit var chat: ChatController

    /** What POST /api/assistant/message answers. */
    @Volatile private var messageResponse: MockResponse = MockResponse()
    /** What GET /api/sessions/{id}/stream answers (reattach). */
    @Volatile private var attachResponse: MockResponse = MockResponse()
    @Volatile private var attachPaths = mutableListOf<String>()
    @Volatile private var messageBodies = mutableListOf<String>()
    @Volatile private var messagePaths = mutableListOf<String>()
    /** Text frames the phone sent on the voice socket (the `stop` frame). */
    @Volatile private var voiceFrames = mutableListOf<String>()
    /** What GET /api/sessions/{id} answers. */
    @Volatile private var sessionDetail: JSONObject = JSONObject().put("status", "idle")
    @Volatile private var sessionGets = 0
    @Volatile private var sessionPaths = mutableListOf<String>()
    /** What POST /api/undo/{id} answers, by id. */
    @Volatile private var undoResponses = mutableMapOf<String, MockResponse>()

    @Before
    fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                return when {
                    path.contains("/api/assistant/voice_stream") -> MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                        override fun onMessage(webSocket: WebSocket, text: String) {
                            voiceFrames.add(text)
                            webSocket.send(JSONObject().put("type", "transcription").put("text", "remind me about this tomorrow").toString())
                            webSocket.send(JSONObject().put("type", "state").put("state", JSONObject().put("status", "idle")).toString())
                        }

                        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                            webSocket.close(1000, null)
                        }
                    })
                    path.startsWith("/api/assistant/message") -> {
                        messagePaths.add(path)
                        messageBodies.add(request.body.readUtf8())
                        messageResponse
                    }
                    path.startsWith("/api/assistant/models") -> MockResponse().setBody(
                        JSONObject().put("default", "fast").put(
                            "models", JSONArray()
                                .put(JSONObject().put("id", "fast").put("label", "Fast"))
                                .put(JSONObject().put("id", "deep").put("label", "Deep").put("shows_reasoning", true))
                        ).toString()
                    )
                    request.method == "GET" && path.contains("/stream") -> {
                        attachPaths.add(path)
                        attachResponse
                    }
                    request.method == "GET" && path == "/api/sessions" -> MockResponse().setBody("[]")
                    request.method == "POST" && path.startsWith("/api/undo/") ->
                        undoResponses[path.removePrefix("/api/undo/")] ?: MockResponse().setResponseCode(404).setBody("{}")
                    request.method == "GET" && path.startsWith("/api/sessions/") -> {
                        sessionGets++
                        sessionPaths.add(path)
                        MockResponse().setBody(sessionDetail.toString())
                    }
                    else -> MockResponse().setBody("{}")
                }
            }
        }
        server.start()

        scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val api = ApiService(server.url("").toString().trimEnd('/'), "token")
        val context = FakeContext(tmp.newFolder("files"))
        chat = ChatController(
            ControllerContext(scope, UserMessages()) { api },
            context,
            FakePrefs(),
            serverUrl = { server.url("").toString() },
            apiToken = { "token" },
            onSessionChanged = {},
            endRealtimeCall = {}
        )
        chat.startNewSession()
    }

    @After
    fun tearDown() {
        scope.cancel()
        server.shutdown()
    }

    private fun sse(vararg events: JSONObject): String =
        events.joinToString("") { ev -> (if (ev.has("seq")) "id: ${ev.getInt("seq")}\n" else "") + "data: $ev\n\n" }

    /** Stamp events with run_id + consecutive seq, like core/runs.py does. */
    private fun runEvents(runId: String, startSeq: Int, vararg events: JSONObject): Array<JSONObject> =
        events.mapIndexed { i, e -> e.put("run_id", runId).put("seq", startSeq + i) }.toTypedArray()

    private fun ev(type: String) = JSONObject().put("type", type)
    private fun state(status: String = "idle") = ev("state").put("state", JSONObject().put("status", status))

    private fun waitUntil(timeoutMs: Long = 10_000, what: String, condition: () -> Boolean) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (condition()) return
            Thread.sleep(20)
        }
        throw AssertionError("Timed out waiting for: $what")
    }

    @Test
    fun streamRendersReasoningToolsAndStreamedAnswer() {
        messageResponse = MockResponse()
            .setHeader("Content-Type", "text/event-stream")
            .setBody(
                sse(
                    *runEvents(
                        "r1", 1,
                        ev("run_start").put("model", JSONObject().put("id", "deep").put("label", "Deep")),
                        ev("step_start").put("iteration", 1).put("max_iterations", 10),
                        ev("reasoning_delta").put("iteration", 1).put("delta", "Need to "),
                        ev("reasoning_delta").put("iteration", 1).put("delta", "add a task."),
                        ev("tool_call").put("id", "t1").put("name", "create_task").put("iteration", 1),
                        ev("tool_start").put("id", "t1").put("name", "create_task")
                            .put("args", JSONObject().put("title", "Milk")),
                        ev("tool_end").put("id", "t1").put("ok", true).put("result", "created"),
                        ev("step_start").put("iteration", 2).put("max_iterations", 10),
                        ev("text_delta").put("iteration", 2).put("delta", "Added "),
                        ev("text_delta").put("iteration", 2).put("delta", "milk."),
                        ev("final").put("content", "Added milk."),
                        ev("run_end").put("status", "idle")
                    ),
                    state()
                )
            )

        assertTrue(chat.sendChatMessage("Add milk"))
        waitUntil(what = "stream to finish") { !chat.isStreaming.value && chat.chatMessages.value.any { it.message == "Added milk." } }

        val messages = chat.chatMessages.value
        assertEquals("Add milk", messages.first().message)
        val tool = messages.single { it.toolCall != null }.toolCall!!
        assertEquals("create_task", tool.toolName)
        assertEquals(true, tool.succeeded)
        assertFalse(tool.isRunning)
        assertFalse(tool.isPreparing)
        assertTrue(tool.parameters.contains("Milk"))
        val thoughts = messages.single { it.reasoning.isNotEmpty() }
        assertEquals("Need to add a task.", thoughts.reasoning)
        assertTrue("reasoning sits above the tool card", messages.indexOf(thoughts) < messages.indexOfFirst { it.toolCall != null })
        assertEquals("one answer bubble, built from deltas", 1, messages.count { it.message == "Added milk." })
        assertFalse(messages.any { it.isThinking || it.isStreaming })
        assertEquals("", chat.chatStatusText.value)
    }

    @Test
    fun sendsTheSelectedModel() {
        messageResponse = MockResponse().setBody(sse(*runEvents("r", 1, ev("final").put("content", "ok"), ev("run_end")), state()))
        chat.fetchModels()
        waitUntil(what = "model catalog") { chat.models.value.size == 2 }
        assertEquals("fast", chat.chatModel.value)
        chat.setChatModel("deep")

        chat.sendChatMessage("Hi")
        waitUntil(what = "stream to finish") { !chat.isStreaming.value && chat.chatMessages.value.any { it.message == "ok" } }
        assertEquals("deep", JSONObject(messageBodies.last()).optString("model"))
    }

    @Test
    fun approvalPromptSurvivesTheTerminalState() {
        val pending = JSONObject().put("tool_name", "send_email").put("tool_args", "{}").put("tool_call_id", "t9")
        messageResponse = MockResponse().setBody(
            sse(
                JSONObject().put("type", "tool_start").put("id", "t9").put("name", "send_email"),
                JSONObject().put("type", "approval_required").put("id", "t9").put("name", "send_email")
                    .put("args", JSONObject()),
                JSONObject().put("type", "state").put(
                    "state", JSONObject().put("status", "waiting_approval").put("pending_approval", pending)
                )
            )
        )

        chat.sendChatMessage("Email Dana")
        waitUntil(what = "stream to finish") { !chat.isStreaming.value && chat.pendingApproval.value != null }

        assertEquals("t9", chat.pendingApproval.value!!.toolCallId)
        assertTrue(chat.chatMessages.value.single { it.toolCall != null }.toolCall!!.requiresApproval)
    }

    @Test
    fun droppedStreamReattachesAndResumesAfterTheLastSeq() {
        // Three events, then the connection dies mid-body. The server keeps
        // running the task; the client reattaches and resumes after seq 3.
        val padding = ": heartbeat\n\n".repeat(400)
        messageResponse = MockResponse()
            .setBody(
                sse(
                    *runEvents(
                        "run42", 1,
                        ev("run_start"),
                        ev("step_start").put("iteration", 1),
                        ev("text_delta").put("iteration", 1).put("delta", "Here is ")
                    )
                ) + padding
            )
            .setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY)
        attachResponse = MockResponse().setBody(
            sse(
                // The server may resend an event we already have; it must be ignored.
                *runEvents("run42", 3, ev("text_delta").put("iteration", 1).put("delta", "Here is ")),
                *runEvents(
                    "run42", 4,
                    ev("text_delta").put("iteration", 1).put("delta", "your day."),
                    ev("final").put("content", "Here is your day."),
                    ev("run_end").put("status", "idle")
                ),
                state()
            )
        )

        chat.sendChatMessage("Plan my day")
        waitUntil(what = "reply completed after reattach") {
            chat.chatMessages.value.any { it.message == "Here is your day." } && !chat.isStreaming.value
        }

        assertTrue(attachPaths.isNotEmpty())
        assertTrue(attachPaths.first(), attachPaths.first().contains("run_id=run42") && attachPaths.first().contains("after=3"))
        val replies = chat.chatMessages.value.filter { !it.isUser && it.toolCall == null }
        assertEquals("no duplicated text", listOf("Here is your day."), replies.map { it.message })
        assertFalse(chat.chatMessages.value.any { it.message.startsWith("Couldn't get a reply") })
        assertFalse(chat.chatMessages.value.any { it.isThinking })
    }

    @Test
    fun busySessionFollowsTheRunningReply() {
        messageResponse = MockResponse().setResponseCode(409).setBody(
            JSONObject().put("error", "busy").put("code", "session_busy")
                .put("active_run", JSONObject().put("run_id", "older")).toString()
        )
        attachResponse = MockResponse().setBody(
            sse(*runEvents("older", 1, ev("run_start"), ev("final").put("content", "Earlier answer."), ev("run_end")), state())
        )

        chat.sendChatMessage("Again?")
        waitUntil(what = "followed the earlier run") {
            chat.chatMessages.value.any { it.message == "Earlier answer." } && !chat.isStreaming.value
        }
        assertTrue(attachPaths.first().contains("run_id=older"))
    }

    @Test
    fun loadingARunningSessionAttachesToItsRun() {
        sessionDetail = JSONObject()
            .put("status", "running")
            .put("active_run", JSONObject().put("run_id", "live1").put("last_seq", 2))
            .put("messages", JSONArray()
                .put(JSONObject().put("id", "u").put("sender", "You").put("message", "Weather?").put("isUser", true)))
        attachResponse = MockResponse().setBody(
            sse(*runEvents("live1", 1, ev("run_start"), ev("text_delta").put("iteration", 1).put("delta", "Sunny."),
                ev("final").put("content", "Sunny."), ev("run_end")), state())
        )

        chat.loadSession("other-session")
        waitUntil(what = "attached reply") {
            chat.chatMessages.value.any { it.message == "Sunny." } && !chat.isStreaming.value
        }
        assertEquals("Weather?", chat.chatMessages.value.first().message)
        assertTrue(attachPaths.first().contains("run_id=live1"))
    }

    @Test
    fun streamThatNeverStartedReportsTheError() {
        // Server down (VPN off): nothing reached it, so there is nothing to follow.
        server.shutdown()

        chat.sendChatMessage("Hello")
        waitUntil(what = "error message") {
            chat.chatMessages.value.any { it.message.startsWith("Couldn't get a reply") }
        }
        assertFalse(chat.isStreaming.value)
    }

    // --- The shared thread (new layout: initialize() without "previous_layout") ---

    private fun startThread() {
        sessionDetail = JSONObject().put("status", "idle").put("thread", true).put("gap_minutes", 20.0).put("messages", JSONArray())
        chat.initialize()
        waitUntil(what = "thread loaded") { sessionPaths.any { it.startsWith("/api/sessions/main?limit=") } }
        assertTrue(chat.isThreadMode)
        assertEquals("main", chat.sessionId)
    }

    @Test
    fun threadMessagesCarryDeviceAndScreen() {
        startThread()
        chat.screenProvider = { ScreenRef("task", "t1", "Milk") }
        val undos = JSONArray().put(JSONObject().put("id", "u1").put("label", "Added milk to the shopping list"))
        messageResponse = MockResponse().setBody(
            sse(
                *runEvents(
                    "r1", 1,
                    ev("tool_start").put("id", "t1").put("name", "add_shopping_items"),
                    ev("tool_end").put("id", "t1").put("ok", true).put("result", "ok").put("undos", undos),
                    ev("final").put("content", "Done."),
                    ev("run_end")
                ),
                ev("state").put("state", JSONObject().put("status", "idle").put("undos", undos))
            )
        )
        chat.sendChatMessage("add milk")
        waitUntil(what = "run to finish") { !chat.isStreaming.value && chat.chatMessages.value.any { it.message == "Done." } }

        val body = JSONObject(messageBodies.last())
        assertEquals("main", body.getString("session_id"))
        assertEquals("phone", body.getString("device"))
        assertEquals("task", body.getJSONObject("screen").getString("kind"))
        assertEquals("t1", body.getJSONObject("screen").getString("id"))
        assertEquals(listOf("Added milk to the shopping list"), chat.undos.value.map { it.label })
        val sent = chat.chatMessages.value.first { it.isUser }
        assertEquals("phone", sent.device)
        assertNotNull(sent.timestamp)
    }

    @Test
    fun undoOutcomes() {
        startThread()
        val undos = JSONArray()
            .put(JSONObject().put("id", "ok1").put("label", "Added milk"))
            .put(JSONObject().put("id", "old2").put("label", "Moved the reminder"))
            .put(JSONObject().put("id", "chg3").put("label", "Turned on the light"))
        messageResponse = MockResponse().setBody(
            sse(*runEvents("r1", 1, ev("final").put("content", "Done."), ev("run_end")),
                ev("state").put("state", JSONObject().put("status", "idle").put("undos", undos)))
        )
        chat.sendChatMessage("do three things")
        waitUntil(what = "undos") { chat.undos.value.size == 3 }
        undoResponses["ok1"] = MockResponse().setBody(JSONObject().put("ok", true).put("label", "Added milk").toString())
        undoResponses["chg3"] = MockResponse().setResponseCode(500)
            .setBody(JSONObject().put("error", "Undo failed: It was changed again since, so it was left as it is.").toString())

        chat.undo("ok1")
        chat.undo("old2")
        chat.undo("chg3")
        waitUntil(what = "outcomes") { chat.undos.value.none { it.status == UndoItem.Status.READY || it.status == UndoItem.Status.BUSY } }
        val byId = chat.undos.value.associateBy { it.id }
        assertEquals(UndoItem.Status.DONE, byId["ok1"]!!.status)
        assertEquals(UndoItem.Status.TOO_LATE, byId["old2"]!!.status)
        assertEquals(UndoItem.Status.FAILED, byId["chg3"]!!.status)
        assertEquals("It was changed again since, so it was left as it is.", byId["chg3"]!!.error)
    }

    @Test
    fun busyThreadGivesTheMessageBack() {
        startThread()
        messageResponse = MockResponse().setResponseCode(409).setBody(
            JSONObject().put("error", "busy").put("code", "session_busy")
                .put("active_run", JSONObject().put("run_id", "watch-run")).toString()
        )
        attachResponse = MockResponse().setBody(
            sse(*runEvents("watch-run", 1, ev("run_start"), ev("final").put("content", "Answer for the watch."), ev("run_end")), state())
        )
        // The thread as the server shows it meanwhile: the watch's run is going.
        sessionDetail = JSONObject().put("status", "running").put("thread", true)
            .put("active_run", JSONObject().put("run_id", "watch-run"))
            .put("messages", JSONArray().put(JSONObject().put("id", "msg_user_7").put("sender", "You")
                .put("message", "What's the weather?").put("isUser", true).put("device", "watch")))
        chat.sendChatMessage("Again?")
        waitUntil(what = "followed the other device's run") {
            chat.chatMessages.value.any { it.message == "Answer for the watch." } && !chat.isStreaming.value
        }
        assertFalse("the refused message isn't left in the thread", chat.chatMessages.value.any { it.isUser && it.message == "Again?" })
        assertEquals("Again?", chat.draftText.value)
    }

    @Test
    fun olderPagesLoadWithTheCursor() {
        sessionDetail = JSONObject().put("status", "idle").put("thread", true).put("has_more", true).put("before", "41")
            .put("messages", JSONArray()
                .put(JSONObject().put("id", "msg_user_41").put("sender", "You").put("message", "newer").put("isUser", true).put("ts", 1_700_000_000.0))
                .put(JSONObject().put("id", "msg_assistant_42").put("sender", "Dana").put("message", "ok").put("isUser", false).put("ts", 1_700_000_005.0)))
        chat.initialize()
        waitUntil(what = "first page") { chat.hasOlder.value }
        sessionDetail = JSONObject().put("status", "idle").put("thread", true).put("has_more", false)
            .put("messages", JSONArray()
                .put(JSONObject().put("id", "msg_user_30").put("sender", "You").put("message", "older").put("isUser", true).put("ts", 1_699_990_000.0)))
        chat.loadOlder()
        waitUntil(what = "older page") { chat.chatMessages.value.size == 3 }
        assertEquals(listOf("older", "newer", "ok"), chat.chatMessages.value.map { it.message })
        assertTrue(sessionPaths.any { it.contains("before=41") })
        assertFalse(chat.hasOlder.value)
    }

    // --- The power-button sheet's "Screen" control ------------------------

    /** What the sheet's control hands over: [armed] goes with the next message only. */
    private class FakeScreens(@Volatile var armed: OutgoingScreen? = null) : ScreenAttachments {
        @Volatile var putBacks = 0
        override fun take(): OutgoingScreen? = armed.also { armed = null }
        override fun putBack(screen: OutgoingScreen) {
            putBacks++
            armed = screen
        }
    }

    private val page = DeviceScreen(app = "Chrome", packageName = "com.android.chrome", title = "Espresso - Wikipedia",
        url = "https://en.wikipedia.org/wiki/Espresso", text = "Espresso is a concentrated form of coffee")

    @Test
    fun includedScreenGoesAsTheImageWithDeviceScreenThenNotAgain() {
        val screens = FakeScreens(OutgoingScreen(page, "JPEGBYTES".toByteArray(), "Chrome"))
        chat.screenAttachments = screens
        messageResponse = MockResponse().setBody(sse(*runEvents("r", 1, ev("final").put("content", "It's espresso."), ev("run_end")), state()))

        chat.sendChatMessage("what is this?")
        waitUntil(what = "stream to finish") { !chat.isStreaming.value && chat.chatMessages.value.any { it.message == "It's espresso." } }
        assertTrue(messagePaths.last().startsWith("/api/assistant/message_with_image?"))
        val body = messageBodies.last()
        assertTrue("screenshot in the image part", body.contains("name=\"image\"") && body.contains("JPEGBYTES"))
        val part = body.substringAfter("name=\"device_screen\"").substringAfter("\r\n\r\n").substringBefore("\r\n--")
        val sent = JSONObject(part)
        assertEquals("Chrome", sent.getString("app"))
        assertEquals("com.android.chrome", sent.getString("package"))
        assertEquals("https://en.wikipedia.org/wiki/Espresso", sent.getString("url"))
        assertEquals(page.text, sent.getString("text"))
        val bubble = chat.chatMessages.value.first { it.isUser }
        assertEquals("Chrome", bubble.screen)
        assertEquals("the sent screenshot shows in the bubble", 1, bubble.images.size)

        // The next message goes on its own.
        messageResponse = MockResponse().setBody(sse(*runEvents("r2", 1, ev("final").put("content", "ok"), ev("run_end")), state()))
        chat.sendChatMessage("thanks")
        waitUntil(what = "second stream") { !chat.isStreaming.value && chat.chatMessages.value.any { it.message == "ok" } }
        assertEquals("/api/assistant/message", messagePaths.last())
        assertFalse(JSONObject(messageBodies.last()).has("device_screen"))
    }

    @Test
    fun screenWithoutAScreenshotGoesOnTheJsonPath() {
        chat.screenAttachments = FakeScreens(OutgoingScreen(page, null, "Chrome"))
        messageResponse = MockResponse().setBody(sse(*runEvents("r", 1, ev("final").put("content", "ok"), ev("run_end")), state()))
        chat.sendChatMessage("summarise this")
        waitUntil(what = "stream to finish") { !chat.isStreaming.value && chat.chatMessages.value.any { it.message == "ok" } }
        assertEquals("/api/assistant/message", messagePaths.last())
        val body = JSONObject(messageBodies.last())
        assertEquals(page.text, body.getJSONObject("device_screen").getString("text"))
        assertTrue(chat.chatMessages.value.first { it.isUser }.images.isEmpty())
    }

    @Test
    fun aBusyThreadPutsTheScreenBack() {
        val screens = FakeScreens(OutgoingScreen(page, null, "Chrome"))
        chat.screenAttachments = screens
        messageResponse = MockResponse().setResponseCode(409).setBody(
            JSONObject().put("error", "busy").put("code", "session_busy").put("active_run", JSONObject().put("run_id", "x")).toString()
        )
        attachResponse = MockResponse().setBody(sse(state()))
        startThread()
        chat.sendChatMessage("what is this?")
        waitUntil(what = "refused") { screens.putBacks == 1 }
        assertNotNull("included again for the next try", screens.armed)
    }

    @Test
    fun voiceSocketCarriesTheScreenTurnedOnWhileListening() {
        // The server-transcribed path: PCM over the socket, then the stop frame.
        ServerStatusMonitor.configure(FakeContext(tmp.newFolder("probe")), server.url("").toString())
        waitUntil(what = "server online") { ServerStatusMonitor.status.value.reachability == app.ganam.assistant.data.network.Reachability.ONLINE }
        VoiceInput.source = {
            object : PcmStream {
                override fun read(buffer: ByteArray): Int {
                    Thread.sleep(40)
                    val loud = true
                    for (i in buffer.indices step 2) {
                        val v = if (loud) (if ((i / 2) % 2 == 0) 6000 else -6000) else 0
                        buffer[i] = (v and 0xFF).toByte()
                        buffer[i + 1] = (v shr 8).toByte()
                    }
                    return buffer.size
                }
                override fun close() {}
            }
        }
        try {
            val screens = FakeScreens()
            chat.screenAttachments = screens
            chat.startVoiceRecording()
            waitUntil(what = "listening") { chat.isRecording.value }
            // He taps "Screen" while still talking: it applies to this utterance.
            screens.armed = OutgoingScreen(page, "JPEGBYTES".toByteArray(), "Chrome")
            Thread.sleep(400)
            // He taps the dial (silence would end it the same way).
            chat.stopVoiceRecording()
            waitUntil(what = "stop frame") { voiceFrames.isNotEmpty() }
            val stop = JSONObject(voiceFrames.single())
            assertEquals("stop", stop.getString("event"))
            assertEquals(page.text, stop.getJSONObject("device_screen").getString("text"))
            assertFalse("the socket carries no image", stop.has("images"))
            waitUntil(what = "transcript bubble") { chat.chatMessages.value.any { it.isUser && it.message == "remind me about this tomorrow" } }
            val bubble = chat.chatMessages.value.first { it.isUser }
            assertEquals("Chrome", bubble.screen)
            assertTrue("text only on this path", bubble.images.isEmpty())
            assertEquals(null, screens.armed)
        } finally {
            VoiceInput.source = null
            chat.release()
            resetServerStatus()
        }
    }

    @Test
    fun approvalAskedBecauseOfTheSharedScreenSaysSo() {
        val pending = JSONObject().put("tool_name", "call_ha_service").put("tool_args", "{}").put("tool_call_id", "h1")
            .put("untrusted_content", true).put("approval_reason", "device_screen")
        messageResponse = MockResponse().setBody(
            sse(
                JSONObject().put("type", "tool_start").put("id", "h1").put("name", "call_ha_service"),
                JSONObject().put("type", "approval_required").put("id", "h1").put("name", "call_ha_service")
                    .put("args", JSONObject()).put("reason", "device_screen"),
                JSONObject().put("type", "state").put("state", JSONObject().put("status", "waiting_approval").put("pending_approval", pending))
            )
        )
        chat.sendChatMessage("turn off the lights")
        waitUntil(what = "approval") { !chat.isStreaming.value && chat.pendingApproval.value != null }
        val card = chat.chatMessages.value.single { it.toolCall != null }.toolCall!!
        assertTrue(card.requiresApproval)
        assertEquals("device_screen", card.approvalReason)
    }

    /** Back to the monitor's untouched state (it is one object for the whole JVM). */
    @Suppress("UNCHECKED_CAST")
    private fun resetServerStatus() {
        val cls = ServerStatusMonitor::class.java
        cls.getDeclaredField("baseUrl").apply { isAccessible = true }.set(null, null)
        (cls.getDeclaredField("_status").apply { isAccessible = true }.get(null) as MutableStateFlow<ServerStatus>).value = ServerStatus()
    }

    @Test
    fun parsesServerTimestamps() {
        // SQLite CURRENT_TIMESTAMP (UTC, no zone)
        assertEquals(1_700_000_000_000L, ChatController.parseServerTimestamp("2023-11-14 22:13:20"))
        // ISO with micros and offset
        assertEquals(1_700_000_000_000L, ChatController.parseServerTimestamp("2023-11-15T00:13:20.123456+02:00"))
        assertEquals(1_700_000_000_000L, ChatController.parseServerTimestamp("2023-11-14T22:13:20Z"))
        assertEquals(1_700_000_000_000L, ChatController.parseServerTimestamp("1700000000"))
        assertEquals(null, ChatController.parseServerTimestamp(""))
        assertEquals(null, ChatController.parseServerTimestamp("null"))
        assertEquals(null, ChatController.parseServerTimestamp("yesterday"))
        assertNotNull(ChatController.parseServerTimestamp("2025-01-02T03:04"))
    }

    // --- Fakes ---------------------------------------------------------------

    private class FakeContext(private val files: File) : ContextWrapper(null) {
        private val prefs = FakePrefs()
        override fun getApplicationContext(): Context = this
        override fun getFilesDir(): File = files
        override fun getCacheDir(): File = File(files, "cache").apply { mkdirs() }
        override fun getSharedPreferences(name: String?, mode: Int): SharedPreferences = prefs
    }

    private class FakePrefs : SharedPreferences {
        private val values = HashMap<String, Any?>()
        override fun getAll(): MutableMap<String, *> = HashMap(values)
        override fun getString(key: String, defValue: String?) = values[key] as String? ?: defValue
        @Suppress("UNCHECKED_CAST")
        override fun getStringSet(key: String, defValues: MutableSet<String>?) = values[key] as MutableSet<String>? ?: defValues
        override fun getInt(key: String, defValue: Int) = values[key] as Int? ?: defValue
        override fun getLong(key: String, defValue: Long) = values[key] as Long? ?: defValue
        override fun getFloat(key: String, defValue: Float) = values[key] as Float? ?: defValue
        override fun getBoolean(key: String, defValue: Boolean) = values[key] as Boolean? ?: defValue
        override fun contains(key: String) = values.containsKey(key)
        override fun registerOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener?) {}
        override fun unregisterOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener?) {}
        override fun edit(): SharedPreferences.Editor = object : SharedPreferences.Editor {
            override fun putString(key: String, value: String?) = apply { values[key] = value }
            override fun putStringSet(key: String, value: MutableSet<String>?) = apply { values[key] = value }
            override fun putInt(key: String, value: Int) = apply { values[key] = value }
            override fun putLong(key: String, value: Long) = apply { values[key] = value }
            override fun putFloat(key: String, value: Float) = apply { values[key] = value }
            override fun putBoolean(key: String, value: Boolean) = apply { values[key] = value }
            override fun remove(key: String) = apply { values.remove(key) }
            override fun clear() = apply { values.clear() }
            override fun commit() = true
            override fun apply() {}
        }
    }
}
