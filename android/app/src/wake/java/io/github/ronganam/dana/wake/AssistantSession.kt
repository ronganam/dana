package io.github.ronganam.dana.wake

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import io.github.ronganam.dana.data.voice.VoiceInput
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * One hands-free conversation with Dana, shared by the wall kiosk and the TV:
 * opens on "Hey Dana" (or a button), streams the voice turn from the
 * always-open mic starting at the wake frame, speaks finished replies, listens
 * once more for a follow-up, and closes itself when idle.
 *
 * While open it holds transient audio focus so whatever was playing (the TV)
 * pauses or ducks, and gives it back on close.
 */
class AssistantSession(
    private val context: Context,
    private val scope: CoroutineScope,
    private val vm: MainViewModel,
    private val speaker: DanaSpeaker,
    private val onClosed: () -> Unit = {},
) {
    /** Compose-observable: the assistant sheet is showing. */
    var visible by mutableStateOf(false)
        private set

    /** Mic frame where "Hey Dana" ended; the next voice turn starts there. */
    @Volatile private var pendingWakeFrame: Long? = null
    private var lastInteractionAt = 0L
    private var spoken = HashSet<String>()
    private var turnHadReply = false
    private var followUps = 0
    private var driverJob: Job? = null
    private val main = Handler(Looper.getMainLooper())
    private val audio = context.getSystemService(AudioManager::class.java)
    private var focusRequest: AudioFocusRequest? = null

    /** Voice turns read from the always-open mic instead of opening a second AudioRecord. */
    fun installVoiceSource() {
        VoiceInput.endOfSpeechMs = 1400L
        VoiceInput.noSpeechTimeoutMs = 5000L
        VoiceInput.speechStartFrames = 3 // 240 ms: far-field mics pick up clicks and the chime
        VoiceInput.source = {
            if (!MicHub.isRunning) MicHub.start()
            // Boost quiet far-field mics (the TV's), then call it speech when it is
            // clearly above the boosted room level. A fixed minimum here (it was 180)
            // was out of reach for the TV, so every turn timed out as "no speech".
            val floor = MicHub.level.value.noiseFloor
            val gain = GainStream.gainFor(floor)
            VoiceInput.silenceRms = GainStream.thresholdFor(floor)
            // Past the listening chime (250 ms, plus the TV's ~150 ms output latency).
            VoiceInput.speechGateUntil = System.currentTimeMillis() + 700
            OpenTimer.mark("mic stream opened")
            val from = pendingWakeFrame
            pendingWakeFrame = null
            GainStream(MicHub.openStream(from), gain, floor, VoiceInput.silenceRms)
        }
    }

    fun noteInteraction() { lastInteractionAt = System.currentTimeMillis() }

    fun open(wakeFrame: Long?) {
        if (visible) {
            // Already open: a button press just listens again.
            if (wakeFrame == null) startListening()
            return
        }
        WakeBus.detectionEnabled = false
        pendingWakeFrame = wakeFrame
        val now = System.currentTimeMillis()
        // A fresh conversation after a pause, but an empty one is reused: each new
        // session is a server round trip plus a session-list download, and an
        // open with no speech would leave an empty chat behind every time.
        val firstOpen = WakeBus.lastClosedAt == 0L
        val stale = now - WakeBus.lastClosedAt > NEW_SESSION_AFTER_MS
        if (firstOpen || (stale && vm.chatController.chatMessages.value.isNotEmpty())) {
            vm.startNewSession(saveOld = true)
        }
        spoken = vm.chatController.chatMessages.value.filter { !it.isUser }.mapTo(HashSet()) { speechKey(it.message) }
        turnHadReply = false
        followUps = 0
        lastInteractionAt = now
        visible = true
        OpenTimer.mark("session open")
        takeAudioFocus()
        Chime.listening()
        startListening()
        startDriver()
    }

    private fun startListening() {
        speaker.stop()
        vm.chatController.startVoiceRecording()
    }

    fun close() {
        if (!visible) return
        driverJob?.cancel()
        vm.chatController.stopVoiceRecording()
        speaker.stop()
        visible = false
        WakeBus.lastClosedAt = System.currentTimeMillis()
        pendingWakeFrame = null
        releaseAudioFocus()
        onClosed()
        // Re-enabling resets the detector, so the reply audio can't trigger it.
        main.postDelayed({ if (!visible) WakeBus.detectionEnabled = true }, 800)
    }

    fun release() {
        driverJob?.cancel()
        VoiceInput.source = null
        releaseAudioFocus()
        if (visible) {
            visible = false
            WakeBus.detectionEnabled = true
        }
    }

    /**
     * Speaks finished replies, listens again for a follow-up, and closes the
     * sheet once nothing has happened for a while.
     */
    private fun startDriver() {
        driverJob?.cancel()
        driverJob = scope.launch {
            launch {
                vm.chatController.chatMessages.collect { messages ->
                    for (m in messages) {
                        if (m.isUser && m.message.isNotBlank()) OpenTimer.mark("transcript shown")
                        if (!m.isUser && m.message.isNotBlank() && m.toolCall == null) OpenTimer.mark("reply text shown")
                        if (m.isUser || m.isThinking || m.isStreaming || m.toolCall != null) continue
                        if (m.sender != "Assistant" || m.message.isBlank()) continue
                        if (spoken.add(speechKey(m.message))) {
                            turnHadReply = true
                            if (WakePrefs.speakReplies(context)) speaker.speak(m.message)
                        }
                    }
                }
            }
            var idleSince = System.currentTimeMillis()
            while (isActive) {
                delay(250)
                val recording = vm.chatController.isRecording.value
                val busy = recording || vm.chatController.isStreaming.value || speaker.speaking.value
                if (recording && speaker.speaking.value) speaker.stop() // asked again mid-reply
                val now = System.currentTimeMillis()
                if (busy || now - lastInteractionAt < 1000) { idleSince = now; continue }

                val waitingOnUser = vm.chatController.pendingApproval.value != null || vm.chatController.selectionRequest.value != null
                if (turnHadReply && !waitingOnUser && WakePrefs.followUp(context) && followUps < MAX_FOLLOW_UPS) {
                    turnHadReply = false
                    followUps++
                    delay(350)
                    Chime.listening()
                    startListening()
                    idleSince = System.currentTimeMillis()
                    continue
                }
                val limit = if (waitingOnUser) 90_000L else IDLE_CLOSE_MS
                if (now - maxOf(idleSince, lastInteractionAt) > limit) {
                    close()
                    break
                }
            }
        }
    }

    private fun takeAudioFocus() {
        val req = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setOnAudioFocusChangeListener { }
            .build()
        audio.requestAudioFocus(req)
        focusRequest = req
    }

    private fun releaseAudioFocus() {
        focusRequest?.let { audio.abandonAudioFocusRequest(it) }
        focusRequest = null
    }

    private fun speechKey(text: String) = text.trim().hashCode().toString() + ":" + text.length

    companion object {
        private const val NEW_SESSION_AFTER_MS = 2 * 60_000L
        private const val IDLE_CLOSE_MS = 8_000L
        private const val MAX_FOLLOW_UPS = 3
    }
}
