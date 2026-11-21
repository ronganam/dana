package io.github.ronganam.dana.chat

import io.github.ronganam.dana.data.models.ChatContext
import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.network.ChatApi
import io.github.ronganam.dana.data.network.RunEvent
import io.github.ronganam.dana.testing.ResourceTexts
import io.github.ronganam.dana.ui.viewmodels.ChatPrompts
import io.github.ronganam.dana.ui.viewmodels.ChatRunRenderer
import io.github.ronganam.dana.ui.viewmodels.ChatTranscript
import io.github.ronganam.dana.ui.viewmodels.ChatUndos
import io.github.ronganam.dana.ui.viewmodels.ControllerContext
import io.github.ronganam.dana.ui.viewmodels.RunStreamClient
import io.github.ronganam.dana.ui.viewmodels.ThreadView
import io.github.ronganam.dana.ui.viewmodels.UserMessages
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The event-to-state reduction of a run ([ChatRunRenderer]) without a
 * network: the server's golden stream (`server/tests/fixtures/golden.sse`)
 * and hand-made frames go in, the transcript, prompts and status line are
 * checked.
 */
class ChatRunRendererTest {
    private val transcript = ChatTranscript(persist = { saves++ }, persistSoon = { soonSaves++ })
    private val prompts = ChatPrompts(transcript)
    private var cached: List<ChatMessage> = emptyList()
    private val thread = ThreadView(transcript) { cached = it }
    private val runs = RunStreamClient { error("no network in this test") }
    private val ctx = ControllerContext(CoroutineScope(Dispatchers.Unconfined), UserMessages(), ResourceTexts) { error("no network in this test") }
    private val undos = ChatUndos(ctx) {}
    private val status = MutableStateFlow("")
    private val context = MutableStateFlow<ChatContext?>(null)
    private var inThread = false
    private var streaming = true
    private val utterances = mutableListOf<String>()
    private var toolEnds = 0
    private var saves = 0
    private var soonSaves = 0

    private val renderer = ChatRunRenderer(
        runs, transcript, prompts, thread, undos, ResourceTexts, status, context,
        inThread = { inThread },
        streaming = { streaming },
        onUtterance = { utterances += it },
        onToolEnded = { toolEnds++ }
    )

    private val golden: List<RunEvent> = javaClass.classLoader!!.getResource("golden.sse")!!.readText()
        .lineSequence().filter { it.startsWith("data:") }
        .mapNotNull { ChatApi.parseEvent(it.removePrefix("data:").trim()) }.toList()

    private fun frame(json: String): RunEvent = ChatApi.parseEvent(json)!!

    private var seq = 0
    private fun event(json: String): RunEvent = frame(json.trim().removeSuffix("}") + """, "run_id": "r1", "seq": ${++seq}}""")

    private val messages get() = transcript.value

    @Test
    fun goldenRunEndsWithToolCardsAndTheReply() {
        golden.forEach(renderer::render)

        val tools = messages.mapNotNull { it.toolCall }
        assertEquals(listOf("call_a", "call_b"), tools.map { it.toolCallId })
        assertTrue(tools.all { it.succeeded == true && !it.isRunning && !it.isPreparing })
        assertEquals(listOf(300L, 300L), tools.map { it.durationMs })
        assertTrue("only the newest card stays open", tools[0].isCollapsed && !tools[1].isCollapsed)
        val reply = messages.last()
        assertEquals("Both lookups done.", reply.message)
        assertFalse(reply.isUser || reply.isStreaming)
        assertTrue(messages.none { it.isThinking || it.isStreaming })
        assertEquals("the closing state clears the status line", "", status.value)
        assertEquals(2, toolEnds)
        assertNull(prompts.approval.value)
    }

    @Test
    fun replayedFramesAreNotRenderedTwice() {
        golden.forEach(renderer::render)
        val after = messages
        golden.forEach(renderer::render)
        assertEquals(after, messages)
        assertEquals(2, toolEnds)
    }

    @Test
    fun inTheThreadTheStoredTurnReplacesTheLiveBubbles() {
        inThread = true
        golden.forEach(renderer::render)
        assertEquals(listOf("msg_user_0", "msg_tool_1_call_a", "msg_tool_1_call_b"), messages.take(3).map { it.id })
        assertEquals("Both lookups done.", messages.last().message)
        assertEquals(messages, cached)
    }

    @Test
    fun statusLineFollowsTheRun() {
        renderer.render(event("""{"type": "run_start", "model": {"id": "fast", "label": "Fast"}}"""))
        renderer.render(event("""{"type": "step_start", "iteration": 1, "max_iterations": 10}"""))
        assertEquals("Thinking…", status.value)
        assertEquals("Fast · Thinking…", messages.single { it.isThinking }.thinkingLabel)
        renderer.render(event("""{"type": "step_start", "iteration": 2, "max_iterations": 10}"""))
        assertEquals("Step 2 of 10", status.value)
        renderer.render(event("""{"type": "tool_call", "id": "c1", "name": "add_task"}"""))
        assertEquals("Preparing add_task…", status.value)
        assertTrue(messages.single { it.toolCall != null }.toolCall!!.isPreparing)
        renderer.render(event("""{"type": "tool_start", "id": "c1", "name": "add_task", "args": {"title": "Milk"}}"""))
        assertEquals("Running add_task…", status.value)
        renderer.render(event("""{"type": "tool_end", "id": "c1", "name": "add_task", "result": "ok", "ok": false, "duration_ms": 12}"""))
        assertEquals(false, messages.single { it.toolCall != null }.toolCall!!.succeeded)
        assertEquals("Reading the results…", messages.single { it.isThinking }.thinkingLabel.substringAfter(" · "))
        renderer.render(event("""{"type": "text_delta", "iteration": 2, "delta": "Do"}"""))
        renderer.render(event("""{"type": "text_delta", "iteration": 2, "delta": "ne"}"""))
        assertEquals("Writing…", status.value)
        val bubble = messages.last()
        assertEquals("Done", bubble.message)
        assertTrue(bubble.isStreaming)
        assertTrue("the typing indicator gives way to the reply", messages.none { it.isThinking })
    }

    @Test
    fun finalTextIsAuthoritativeOverTheStreamedBubble() {
        renderer.render(event("""{"type": "text_delta", "iteration": 1, "delta": "Hel"}"""))
        renderer.render(event("""{"type": "final", "content": "Hello there", "shortcut": true}"""))
        val reply = messages.single()
        assertEquals("Hello there", reply.message)
        assertTrue(reply.shortcut)
        assertFalse(reply.isStreaming)
    }

    @Test
    fun finalAfterAToolCardIsANewMessage() {
        renderer.render(event("""{"type": "text_delta", "iteration": 1, "delta": "Let me check."}"""))
        renderer.render(event("""{"type": "tool_start", "id": "c1", "name": "lookup"}"""))
        renderer.render(event("""{"type": "final", "content": "Found it."}"""))
        assertEquals(listOf("Let me check.", "", "Found it."), messages.map { it.message })
        assertTrue(messages.none { it.isStreaming })
    }

    @Test
    fun retryDropsTheStepsBubble() {
        renderer.render(event("""{"type": "text_delta", "iteration": 1, "delta": "half a sen"}"""))
        renderer.render(event("""{"type": "retry", "iteration": 1}"""))
        assertTrue(messages.none { it.message.isNotEmpty() })
        assertEquals(1, messages.count { it.isThinking })
    }

    @Test
    fun approvalAsksOnTheCardAndAStateWithoutItClearsIt() {
        renderer.render(event("""{"type": "approval_required", "id": "c9", "name": "send_email", "args": {"to": "robin@example.com"}, "reason": "Sends mail"}"""))
        val approval = prompts.approval.value
        assertNotNull(approval)
        assertEquals("send_email", approval!!.toolName)
        assertEquals("""{"to":"robin@example.com"}""", approval.toolArgs)
        val card = messages.single().toolCall!!
        assertTrue(card.requiresApproval && !card.isCollapsed)
        assertEquals("Sends mail", card.approvalReason)
        assertEquals("Waiting for your approval", status.value)

        renderer.render(event("""{"type": "state", "state": {"status": "idle", "messages": []}}"""))
        assertNull(prompts.approval.value)
    }

    @Test
    fun aWaitingStateRestoresTheApproval() {
        renderer.render(event("""{"type": "tool_start", "id": "c9", "name": "send_email"}"""))
        renderer.render(event(
            """{"type": "state", "state": {"status": "waiting_approval", "messages": [],
               "pending_approval": {"tool_call_id": "c9", "tool_name": "send_email", "tool_args": "{}", "approval_reason": "Sends mail"}}}"""
        ))
        assertEquals("c9", prompts.approval.value?.toolCallId)
        val card = messages.single().toolCall!!
        assertTrue(card.requiresApproval && !card.isRunning)
        assertEquals("Sends mail", card.approvalReason)
    }

    @Test
    fun theCardStandsInForADriftedApproval() {
        renderer.render(event("""{"type": "approval_required", "id": "c9", "name": "send_email", "args": {"to": "gal@example.com"}}"""))
        prompts.clear()
        val fromCard = prompts.currentApproval()
        assertEquals("c9", fromCard?.toolCallId)
        assertEquals("""{"to":"gal@example.com"}""", fromCard?.toolArgs)
        prompts.answerApproval(approved = false)
        val card = messages.single().toolCall!!
        assertFalse(card.requiresApproval)
        assertEquals(false, card.succeeded)
        assertNull(prompts.currentApproval())
    }

    @Test
    fun aChoiceReplacesItsToolCard() {
        renderer.render(event("""{"type": "tool_start", "id": "s1", "name": "pick"}"""))
        renderer.render(event(
            """{"type": "selection_required", "id": "s1", "prompt": "Which one?", "options": [{"label": "A"}, {"label": "B"}]}"""
        ))
        assertTrue(messages.none { it.toolCall != null })
        assertNotNull(prompts.selection.value)
        assertEquals("Waiting for your choice", status.value)
    }

    @Test
    fun stepLimitAsksToContinue() {
        renderer.render(event("""{"type": "iterations_exceeded", "summary": "Looked up 9 things"}"""))
        assertEquals("Looked up 9 things", prompts.extension.value?.summary)
        assertEquals("Step limit reached", status.value)
        streaming = true
        renderer.render(event("""{"type": "state", "state": {"status": "idle", "messages": []}}"""))
        assertNull(prompts.extension.value)
    }

    @Test
    fun anErrorIsSaidOnce() {
        renderer.render(event("""{"type": "error", "message": "model overloaded"}"""))
        renderer.render(event("""{"type": "state", "state": {"status": "error", "error": "model overloaded", "messages": []}}"""))
        assertEquals(listOf("Error: model overloaded"), messages.map { it.message })
        renderer.render(frame("""{"type": "state", "run_id": "r1", "seq": 99, "state": {"status": "error", "error": "quota", "messages": []}}"""))
        assertEquals("Error: quota", messages.last().message)
    }

    @Test
    fun aTranscriptionStartsATurn() {
        renderer.render(event("""{"type": "transcription", "text": "remind me tomorrow"}"""))
        assertEquals(listOf("remind me tomorrow"), utterances)
        assertEquals("Thinking…", messages.single { it.isThinking }.thinkingLabel)
    }

    @Test
    fun cancelledSaysStopped() {
        renderer.render(event("""{"type": "step_start", "iteration": 1, "max_iterations": 10}"""))
        renderer.render(event("""{"type": "cancelled"}"""))
        assertEquals(listOf("Stopped."), messages.map { it.message })
    }
}
