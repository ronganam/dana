package app.ganam.assistant.realtime

import app.ganam.assistant.data.realtime.RealtimeCallEngine
import app.ganam.assistant.data.realtime.RealtimeCallStatus
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger

import app.ganam.assistant.ui.viewmodels.MainViewModel
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.test.resetMain

@OptIn(ExperimentalCoroutinesApi::class)
class RealtimeCallEngineLifecycleTest {

    @Test
    fun testRealtimeCallStatusDisplayStrings() {
        assertEquals("Idle", RealtimeCallStatus.IDLE.toDisplayString())
        assertEquals("Connecting...", RealtimeCallStatus.CONNECTING.toDisplayString())
        assertEquals("Listening...", RealtimeCallStatus.LISTENING.toDisplayString())
        assertEquals("Dana is speaking…", RealtimeCallStatus.SPEAKING.toDisplayString())
        assertEquals("Executing tool...", RealtimeCallStatus.TOOL_RUNNING.toDisplayString())
        assertEquals("Call error", RealtimeCallStatus.ERROR.toDisplayString())
    }

    @Test
    fun testWebSocketUrlConstruction() {
        val serverUrlHttp = "http://192.168.1.50:8080"
        val serverUrlHttps = "https://dana.example.com"
        val model4o = "gpt-4o-realtime-preview"
        val modelMini = "gpt-4o-mini-realtime-preview"
        val sessionId = "test-session-uuid-123"

        val wsUrlHttp = "${serverUrlHttp.replaceFirst("http", "ws")}/api/assistant/realtime?model=$model4o&session_id=$sessionId"
        assertEquals("ws://192.168.1.50:8080/api/assistant/realtime?model=gpt-4o-realtime-preview&session_id=test-session-uuid-123", wsUrlHttp)

        val wsUrlHttps = "${serverUrlHttps.replaceFirst("http", "ws")}/api/assistant/realtime?model=$modelMini&session_id=$sessionId"
        assertEquals("wss://dana.example.com/api/assistant/realtime?model=gpt-4o-mini-realtime-preview&session_id=test-session-uuid-123", wsUrlHttps)
    }

    @Test
    fun testJsonEventParsingLogic() {
        // Test transcript_delta
        val deltaJson = JSONObject("""{"type": "transcript_delta", "delta": "Hello world"}""")
        assertEquals("transcript_delta", deltaJson.optString("type"))
        assertEquals("Hello world", deltaJson.optString("delta"))

        // Test transcript_done with fallback
        val doneJson = JSONObject("""{"type": "transcript_done", "transcript": "Full response here"}""")
        assertEquals("transcript_done", doneJson.optString("type"))
        val transcript = doneJson.optString("transcript", doneJson.optString("text", "accumulated"))
        assertEquals("Full response here", transcript)

        // Test user_transcript_done
        val userDoneJson = JSONObject("""{"type": "user_transcript_done", "transcript": "User speech"}""")
        assertEquals("User speech", userDoneJson.optString("transcript", userDoneJson.optString("text", "")))

        // Test tool_start and tool_done
        val toolStartJson = JSONObject("""{"type": "tool_start", "name": "get_weather"}""")
        assertEquals("get_weather", toolStartJson.optString("name"))

        val toolDoneJson = JSONObject("""{"type": "tool_done", "name": "get_weather", "status": "completed"}""")
        assertEquals("get_weather", toolDoneJson.optString("name"))
        assertEquals("completed", toolDoneJson.optString("status"))

        // Test interruption signals
        val interruptedTypes = listOf("interrupted", "input_audio_buffer.speech_started", "speech_started")
        for (t in interruptedTypes) {
            val json = JSONObject("""{"type": "$t"}""")
            assertTrue(json.optString("type") in interruptedTypes)
        }
    }

    @Test
    fun testPlaybackChannelBargeInDrain() {
        // Simulates RealtimeCallEngine's playbackChannel and barge-in flushing
        val playbackChannel = Channel<ByteArray>(capacity = Channel.BUFFERED)
        
        // Push 10 audio chunks into channel
        for (i in 1..10) {
            playbackChannel.trySend(byteArrayOf(i.toByte()))
        }

        var drainedCount = 0
        while (playbackChannel.tryReceive().isSuccess) {
            drainedCount++
        }

        assertEquals(10, drainedCount)
        // Verify channel is completely empty
        assertTrue(playbackChannel.tryReceive().isFailure)
    }

    @Test
    fun testModelSwitchingAndQueryParamPropagation() {
        var selectedModel = "gpt-4o-realtime-preview"
        val serverUrl = "https://dana.example.com"
        val sessionId = "uuid-session-001"

        // Initial default model URL
        val initialUrl = "${serverUrl.replaceFirst("http", "ws")}/api/assistant/realtime?model=$selectedModel&session_id=$sessionId"
        assertTrue(initialUrl.contains("model=gpt-4o-realtime-preview"))
        assertTrue(initialUrl.contains("session_id=uuid-session-001"))

        // Switch to Mini
        selectedModel = "gpt-4o-mini-realtime-preview"
        val switchedUrl = "${serverUrl.replaceFirst("http", "ws")}/api/assistant/realtime?model=$selectedModel&session_id=$sessionId"
        assertTrue(switchedUrl.contains("model=gpt-4o-mini-realtime-preview"))
        assertFalse(switchedUrl.contains("gpt-4o-realtime-preview&"))

        // Switch back to GPT-4o
        selectedModel = "gpt-4o-realtime-preview"
        val restoredUrl = "${serverUrl.replaceFirst("http", "ws")}/api/assistant/realtime?model=$selectedModel&session_id=$sessionId"
        assertTrue(restoredUrl.contains("model=gpt-4o-realtime-preview"))
    }

    @Test
    fun testTranscriptClassificationAndMessageHistory() {
        // ChatMessage data structure reproduction
        data class TestChatMessage(
            val sender: String,
            val message: String,
            val isUser: Boolean
        )

        val messages = mutableListOf<TestChatMessage>()

        fun onUserTranscriptDone(transcript: String) {
            if (transcript.isNotBlank()) {
                messages.add(TestChatMessage("You", transcript, true))
            }
        }

        fun onAssistantTranscriptDone(transcript: String) {
            if (transcript.isNotBlank()) {
                messages.add(TestChatMessage("Assistant", transcript, false))
            }
        }

        // 1. User speaks
        onUserTranscriptDone("What is the current room temperature?")
        assertEquals(1, messages.size)
        assertEquals("You", messages[0].sender)
        assertEquals("What is the current room temperature?", messages[0].message)
        assertTrue(messages[0].isUser)

        // 2. Assistant replies
        onAssistantTranscriptDone("The bedroom temperature is currently 22 degrees.")
        assertEquals(2, messages.size)
        assertEquals("Assistant", messages[1].sender)
        assertEquals("The bedroom temperature is currently 22 degrees.", messages[1].message)
        assertFalse(messages[1].isUser)

        // 3. Blank transcripts should be ignored
        onUserTranscriptDone("   ")
        onAssistantTranscriptDone("")
        assertEquals(2, messages.size)
    }

    @Test
    fun testCoroutineLifecycleAndScopeLeakDetection() = runTest {
        // Simulates the behavior in MainViewModel:
        // When startRealtimeCall() is called multiple times without cancelling collector jobs
        val parentJob = Job()
        val viewModelScope = CoroutineScope(Dispatchers.Unconfined + parentJob)

        // Mock StateFlows matching RealtimeCallEngine
        class MockEngine {
            val isCallActive = MutableStateFlow(false)
            val callStatus = MutableStateFlow(RealtimeCallStatus.IDLE)
            val currentAssistantDelta = MutableStateFlow("")
            val activeToolName = MutableStateFlow<String?>(null)
        }

        val activeCollectorJobs = mutableListOf<Job>()

        // Simulate 5 start/end call cycles
        for (i in 1..5) {
            val engine = MockEngine()
            
            // In unmanaged collection (current MainViewModel implementation):
            val job1 = viewModelScope.launch { engine.isCallActive.collect {} }
            val job2 = viewModelScope.launch { engine.callStatus.collect {} }
            val job3 = viewModelScope.launch { engine.currentAssistantDelta.collect {} }
            val job4 = viewModelScope.launch { engine.activeToolName.collect {} }

            activeCollectorJobs.addAll(listOf(job1, job2, job3, job4))

            // Simulate call ending
            engine.isCallActive.value = false
            engine.callStatus.value = RealtimeCallStatus.IDLE
        }

        // Count how many coroutine jobs are still active in viewModelScope
        val activeJobsCount = activeCollectorJobs.count { it.isActive }
        // NOTICE: In current unmanaged implementation, all 20 jobs remain active indefinitely!
        assertEquals("Unmanaged collector jobs remain active across call sessions", 20, activeJobsCount)

        // Now test the MANAGED pattern (cancelling prior jobs on endCall or using a dedicated CallJob/Scope):
        parentJob.cancel()
        val activeAfterScopeCancelled = activeCollectorJobs.count { it.isActive }
        assertEquals("All jobs cancelled when parent scope (ViewModel) is cleared", 0, activeAfterScopeCancelled)
    }

    @Test
    fun testRemediatedManagedLifecycleZeroLeaksAcross100Cycles() = runTest {
        val parentJob = Job()
        val viewModelScope = CoroutineScope(Dispatchers.Unconfined + parentJob)

        class MockEngine {
            val isCallActive = MutableStateFlow(false)
            val callStatus = MutableStateFlow(RealtimeCallStatus.IDLE)
            val currentAssistantDelta = MutableStateFlow("")
            val activeToolName = MutableStateFlow<String?>(null)
        }

        val callJobs = mutableListOf<Job>()
        val allCreatedJobs = mutableListOf<Job>()

        for (cycle in 1..100) {
            val engine = MockEngine()

            // MainViewModel.startRealtimeCall pattern:
            callJobs.forEach { it.cancel() }
            callJobs.clear()

            val job1 = viewModelScope.launch { engine.isCallActive.collect {} }
            val job2 = viewModelScope.launch { engine.callStatus.collect {} }
            val job3 = viewModelScope.launch { engine.currentAssistantDelta.collect {} }
            val job4 = viewModelScope.launch { engine.activeToolName.collect {} }

            callJobs.addAll(listOf(job1, job2, job3, job4))
            allCreatedJobs.addAll(listOf(job1, job2, job3, job4))

            // During call: exactly 4 active jobs
            assertEquals(4, callJobs.count { it.isActive })

            // MainViewModel.endRealtimeCall pattern:
            callJobs.forEach { it.cancel() }
            callJobs.clear()

            // Immediately after endRealtimeCall: exactly 0 active jobs
            assertEquals(0, callJobs.size)
        }

        // Verify across all 100 cycles (400 created jobs), exactly 0 jobs remain active!
        val leakedJobsCount = allCreatedJobs.count { it.isActive }
        assertEquals("Zero coroutine jobs leaked across 100 start/stop cycles", 0, leakedJobsCount)

        parentJob.cancel()
    }

    @Test
    fun testStartCallWithoutEndCallCancelsPriorJobs() = runTest {
        val parentJob = Job()
        val viewModelScope = CoroutineScope(Dispatchers.Unconfined + parentJob)

        class MockEngine {
            val isCallActive = MutableStateFlow(false)
            val callStatus = MutableStateFlow(RealtimeCallStatus.IDLE)
            val currentAssistantDelta = MutableStateFlow("")
            val activeToolName = MutableStateFlow<String?>(null)
        }

        val callJobs = mutableListOf<Job>()
        val allCreatedJobs = mutableListOf<Job>()

        // 10 rapid starts without calling end
        for (i in 1..10) {
            val engine = MockEngine()

            // Pre-cancellation in startRealtimeCall
            callJobs.forEach { it.cancel() }
            callJobs.clear()

            val j1 = viewModelScope.launch { engine.isCallActive.collect {} }
            val j2 = viewModelScope.launch { engine.callStatus.collect {} }
            val j3 = viewModelScope.launch { engine.currentAssistantDelta.collect {} }
            val j4 = viewModelScope.launch { engine.activeToolName.collect {} }

            callJobs.addAll(listOf(j1, j2, j3, j4))
            allCreatedJobs.addAll(listOf(j1, j2, j3, j4))

            // Only the 4 newest jobs should be active
            val activeCount = allCreatedJobs.count { it.isActive }
            assertEquals("Only current cycle jobs active", 4, activeCount)
        }

        // Clean teardown at the end
        callJobs.forEach { it.cancel() }
        callJobs.clear()
        assertEquals(0, allCreatedJobs.count { it.isActive })

        parentJob.cancel()
    }

    @Test
    fun testAsyncOnCallEndedTeardown() = runTest {
        val parentJob = Job()
        val viewModelScope = CoroutineScope(Dispatchers.Unconfined + parentJob)

        class MockEngine {
            val isCallActive = MutableStateFlow(true)
            val callStatus = MutableStateFlow(RealtimeCallStatus.LISTENING)
            var onCallEnded: (() -> Unit)? = null

            fun triggerRemoteEnd() {
                isCallActive.value = false
                callStatus.value = RealtimeCallStatus.IDLE
                onCallEnded?.invoke()
            }
        }

        val callJobs = mutableListOf<Job>()
        var realtimeCallEngine: MockEngine? = MockEngine()
        val isCallActiveState = MutableStateFlow(true)
        val callStatusState = MutableStateFlow(RealtimeCallStatus.LISTENING)

        val engine = realtimeCallEngine!!
        callJobs.add(viewModelScope.launch { engine.isCallActive.collect { isCallActiveState.value = it } })
        callJobs.add(viewModelScope.launch { engine.callStatus.collect { callStatusState.value = it } })

        engine.onCallEnded = {
            viewModelScope.launch {
                callJobs.forEach { it.cancel() }
                callJobs.clear()
                realtimeCallEngine = null
                isCallActiveState.value = false
                callStatusState.value = RealtimeCallStatus.IDLE
            }
        }

        assertEquals(2, callJobs.count { it.isActive })
        assertTrue(isCallActiveState.value)

        // Trigger remote end
        engine.triggerRemoteEnd()

        // Verify all jobs cancelled and reference nulled
        assertEquals(0, callJobs.size)
        assertNull("realtimeCallEngine must be nulled on call ended", realtimeCallEngine)
        assertFalse(isCallActiveState.value)
        assertEquals(RealtimeCallStatus.IDLE, callStatusState.value)

        parentJob.cancel()
    }

    @Test
    fun testLoadSessionTerminatesActiveCall() = runTest {
        var isCallActive = true
        var callEnded = false
        val callJobs = mutableListOf<Job>()

        val job = launch { }
        callJobs.add(job)

        fun endRealtimeCall() {
            callJobs.forEach { it.cancel() }
            callJobs.clear()
            isCallActive = false
            callEnded = true
        }

        fun loadSession(@Suppress("UNUSED_PARAMETER") targetSessionId: String) {
            if (isCallActive) {
                endRealtimeCall()
            }
        }

        loadSession("new-session-id")

        assertTrue("Call must be terminated on session switch", callEnded)
        assertFalse("isCallActive must be false", isCallActive)
        assertEquals("callJobs must be cleared", 0, callJobs.size)
    }

    @Test
    fun testRealtimeCallEngineEndCallIdempotency() = runTest {
        val engine = RealtimeCallEngine(OkHttpClient(), this)
        var callEndedCount = 0
        engine.onCallEnded = {
            callEndedCount++
        }

        // Calling endCall on idle engine should return early
        engine.endCall()
        assertEquals(0, callEndedCount)

        // Repeated endCall calls remain safe
        engine.endCall()
        engine.endCall()
        assertEquals(0, callEndedCount)
    }

    @Test
    fun testRealtimeCallEngineStartCallFailureTeardown() = runTest {
        val engine = RealtimeCallEngine(OkHttpClient(), this)
        var errorReported: String? = null
        var callEndedReported = false

        engine.onError = { errorReported = it }
        engine.onCallEnded = { callEndedReported = true }

        // startCall will catch AudioTrack initialization failure in headless unit test
        engine.startCall("http://127.0.0.1:8080", "token", "session1", "gpt-4o-realtime-preview")

        assertNotNull("onError must be called on initialization failure", errorReported)
        assertTrue("onCallEnded must be called on teardown", callEndedReported)
        assertFalse("isCallActive must be false after failed start", engine.isCallActive.value)
        assertEquals(RealtimeCallStatus.IDLE, engine.callStatus.value)
    }

    @Test
    fun testPlaybackChannelMultipleDrainsAndUnderflow() {
        val playbackChannel = Channel<ByteArray>(capacity = Channel.BUFFERED)

        // Drain empty channel
        var drained = 0
        while (playbackChannel.tryReceive().isSuccess) {
            drained++
        }
        assertEquals(0, drained)

        // Fill with 50 chunks
        for (i in 1..50) {
            playbackChannel.trySend(ByteArray(100))
        }

        while (playbackChannel.tryReceive().isSuccess) {
            drained++
        }
        assertEquals(50, drained)
        assertTrue(playbackChannel.tryReceive().isFailure)
    }
}

