package app.ganam.assistant.ui.viewmodels

import android.app.assist.AssistContent
import android.app.assist.AssistStructure
import android.content.Context
import android.graphics.Bitmap
import android.util.Log
import app.ganam.assistant.data.screen.AssistCapture
import app.ganam.assistant.data.screen.OutgoingScreen
import app.ganam.assistant.data.screen.ScreenAttachments
import app.ganam.assistant.data.screen.ScreenShare
import app.ganam.assistant.data.screen.ScreenShot
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * The power-button sheet's "Screen" control (phone only; the session owns
 * it). Holds what the system captured of the app underneath for one opening,
 * in memory, and hands it to [ChatController] only for a message sent while
 * the control is on ([ScreenShare] has the rules).
 */
class ScreenShareController(private val context: Context) : ScreenAttachments {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val _state = MutableStateFlow(ScreenShare<ScreenShot>())
    val state: StateFlow<ScreenShare<ScreenShot>> = _state.asStateFlow()

    /** Bumped per opening: a capture still being processed from the last one is dropped. */
    @Volatile private var epoch = 0
    private var timeoutJob: Job? = null
    /** The system's callbacks for this opening came (processing may still be running). */
    @Volatile private var assistReceived = false
    @Volatile private var shotReceived = false

    fun opened(source: ScreenShare.Source, textOffInSettings: Boolean, screenshotOffInSettings: Boolean, screenshotRequested: Boolean) {
        epoch++
        assistReceived = false
        shotReceived = false
        _state.value = ScreenShare.opened(source, textOffInSettings, screenshotOffInSettings, screenshotRequested)
        timeoutJob?.cancel()
        if (source == ScreenShare.Source.SYSTEM) {
            val at = epoch
            // A callback the system never makes (a secure window, a capture
            // that failed silently) must not leave the control waiting.
            timeoutJob = scope.launch {
                delay(CAPTURE_TIMEOUT_MS)
                if (epoch != at) return@launch
                _state.update { s ->
                    var t = s
                    if (!assistReceived) t = t.withText(null)
                    if (!shotReceived) t = t.withShot(null)
                    t
                }
            }
        }
    }

    fun onAssist(structure: AssistStructure?, content: AssistContent?) {
        val at = epoch
        assistReceived = true
        if (_state.value.source != ScreenShare.Source.SYSTEM) return
        // Opened over Dana itself: nothing underneath worth sending.
        if (AssistCapture.packageOf(structure) == context.packageName) {
            _state.update { it.copy(source = ScreenShare.Source.DANA, on = false) }
            return
        }
        scope.launch {
            val screen = withContext(Dispatchers.Default) { AssistCapture.describe(context, structure, content) }
            if (epoch == at) _state.update { it.withText(screen) }
            Log.d(TAG, "Screen text: ${screen?.app} · ${screen?.text?.length ?: 0} chars · url=${screen?.url != null}")
        }
    }

    fun onAssistFailure() {
        assistReceived = true
        _state.update { if (it.source == ScreenShare.Source.SYSTEM) it.withText(null) else it }
    }

    fun onScreenshot(bitmap: Bitmap?) {
        val at = epoch
        shotReceived = true
        if (_state.value.source != ScreenShare.Source.SYSTEM) return
        if (bitmap == null) {
            _state.update { it.withShot(null) }
            return
        }
        scope.launch {
            val shot = withContext(Dispatchers.Default) { AssistCapture.encode(bitmap) }
            if (epoch == at) _state.update { it.withShot(shot) }
            Log.d(TAG, "Screenshot: ${shot?.width}x${shot?.height} · ${shot?.jpeg?.size ?: 0} bytes")
        }
    }

    fun tap() {
        _state.update { it.tap() }
        Log.d(TAG, "Screen control: ${_state.value.availability} on=${_state.value.on}")
    }

    fun remove() = _state.update { it.remove() }

    /** The sheet went away: drop the capture (it never outlives one opening). */
    fun closed() {
        epoch++
        timeoutJob?.cancel()
        _state.value = ScreenShare()
    }

    override fun take(): OutgoingScreen? {
        var taken: ScreenShare.Taken<ScreenShot>? = null
        _state.update { s -> s.take().also { taken = it.second }.first }
        val t = taken ?: return null
        Log.d(TAG, "Screen goes with this message: image=${t.shot != null} text=${t.screen?.text?.length ?: 0} chars")
        val label = t.screen?.app ?: t.screen?.packageName?.substringAfterLast('.')?.replaceFirstChar { it.uppercase() } ?: ""
        return OutgoingScreen(t.screen, t.shot?.jpeg, label)
    }

    override fun putBack(screen: OutgoingScreen) = _state.update { it.putBack() }

    fun release() {
        scope.cancel()
    }

    companion object {
        private const val TAG = "ScreenShare"
        /** After this long without the system's capture, whatever came is all there is. */
        private const val CAPTURE_TIMEOUT_MS = 3_000L
    }
}
