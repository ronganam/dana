package io.github.ronganam.dana

import io.github.ronganam.dana.data.network.ServerConfig
import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.app.assist.AssistContent
import android.app.assist.AssistStructure
import android.graphics.Bitmap
import android.os.Bundle
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSession.AssistState
import android.speech.tts.TextToSpeech
import android.view.View
import androidx.activity.result.ActivityResultCallback
import androidx.activity.result.ActivityResultRegistry
import androidx.activity.result.ActivityResultRegistryOwner
import androidx.activity.result.contract.ActivityResultContract
import androidx.compose.runtime.CompositionLocalProvider
import androidx.activity.compose.LocalActivityResultRegistryOwner
import androidx.compose.ui.platform.ComposeView
import androidx.core.view.WindowCompat
import androidx.compose.ui.platform.ViewCompositionStrategy
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.lifecycle.setViewTreeViewModelStoreOwner
import androidx.savedstate.SavedStateRegistry
import androidx.savedstate.SavedStateRegistryController
import androidx.savedstate.SavedStateRegistryOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner
import io.github.ronganam.dana.ui.components.FloatingAssistantOverlay
import io.github.ronganam.dana.ui.theme.DanaTheme
import io.github.ronganam.dana.data.screen.ScreenShare
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.ScreenShareController
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch
import io.github.ronganam.dana.data.voice.useVoiceFor
import androidx.core.content.edit

class DanaVoiceInteractionSession(context: Context) : VoiceInteractionSession(context),
    TextToSpeech.OnInitListener,
    LifecycleOwner,
    ViewModelStoreOwner,
    SavedStateRegistryOwner {

    init {
        setTheme(R.style.Theme_Dana_VoiceSession)
    }

    private val lifecycleRegistry = LifecycleRegistry(this)
    private val store = ViewModelStore()
    private val savedStateRegistryController = SavedStateRegistryController.create(this)

    override val lifecycle: Lifecycle get() = lifecycleRegistry
    override val viewModelStore: ViewModelStore get() = store
    override val savedStateRegistry: SavedStateRegistry get() = savedStateRegistryController.savedStateRegistry

    private lateinit var mainViewModel: MainViewModel
    /** The "Screen" control: what the system captured of the app underneath, for this opening only. */
    private lateinit var screenShare: ScreenShareController
    private val prefs = ServerConfig.prefs(context)
    private var tts: TextToSpeech? = null

    /**
     * Replies already spoken, by text: when the run ends the thread's stored
     * copies replace the live bubbles (new ids, sometimes more rows), and the
     * same answer must not be read out twice.
     */
    private val spokenReplies = ArrayDeque<String>()
    private var voiceCollectionJob: Job? = null
    private var delayedVoiceRecordingJob: Job? = null

    private var isOverlayMinimized = false
    private val overlayTouchableRegion = android.graphics.Region()
    private var sessionComposeView: View? = null

    override fun onComputeInsets(outInsets: Insets) {
        super.onComputeInsets(outInsets)
        if (isOverlayMinimized) {
            outInsets.touchableInsets = Insets.TOUCHABLE_INSETS_REGION
            outInsets.touchableRegion.set(overlayTouchableRegion)
        } else {
            outInsets.touchableInsets = Insets.TOUCHABLE_INSETS_FRAME
            outInsets.touchableRegion.setEmpty()
        }
    }

    private fun updateOverlayTouchableRegion(minimized: Boolean, region: android.graphics.Region) {
        isOverlayMinimized = minimized
        overlayTouchableRegion.set(region)
        sessionComposeView?.requestLayout()
    }

    override fun onCreate() {
        super.onCreate()
        savedStateRegistryController.performRestore(null)
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_CREATE)

        window.window?.let { w ->
            w.setGravity(android.view.Gravity.FILL)
            w.setLayout(
                android.view.ViewGroup.LayoutParams.MATCH_PARENT,
                android.view.ViewGroup.LayoutParams.MATCH_PARENT
            )
            w.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            // Transparent system bars, content drawn behind them.
            WindowCompat.enableEdgeToEdge(w)

            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.P) {
                val params = w.attributes
                params.layoutInDisplayCutoutMode = android.view.WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
                w.attributes = params
            }

            softInputResize(w)
        }

        tts = TextToSpeech(context, this)

        mainViewModel = MainViewModel(context.applicationContext as android.app.Application)
        mainViewModel.initialize(context)
        screenShare = ScreenShareController(context)
        mainViewModel.chatController.screenAttachments = screenShare
    }

    override fun onInit(status: Int) {
        if (status != TextToSpeech.SUCCESS) android.util.Log.w("DanaVoiceSession", "TTS init failed: $status")
    }

    override fun onCreateContentView(): View {
        val themedContext = android.view.ContextThemeWrapper(context, R.style.Theme_Dana_VoiceSession)
        val composeView = ComposeView(themedContext).apply {
            layoutParams = android.view.ViewGroup.LayoutParams(
                android.view.ViewGroup.LayoutParams.MATCH_PARENT,
                android.view.ViewGroup.LayoutParams.MATCH_PARENT
            )
            setViewCompositionStrategy(ViewCompositionStrategy.DisposeOnViewTreeLifecycleDestroyed)
            setViewTreeLifecycleOwner(this@DanaVoiceInteractionSession)
            setViewTreeViewModelStoreOwner(this@DanaVoiceInteractionSession)
            setViewTreeSavedStateRegistryOwner(this@DanaVoiceInteractionSession)
            setContent {
                val stubOwner = object : ActivityResultRegistryOwner {
                    override val activityResultRegistry: ActivityResultRegistry
                        get() = object : ActivityResultRegistry() {
                            override fun <I : Any?, O : Any?> onLaunch(
                                requestCode: Int,
                                contract: ActivityResultContract<I, O>,
                                input: I,
                                options: androidx.core.app.ActivityOptionsCompat?
                            ) {}
                        }
                }
                io.github.ronganam.dana.ui.theme.ThemeSettings.load(context)
                CompositionLocalProvider(LocalActivityResultRegistryOwner provides stubOwner) {
                    DanaTheme() {
                        FloatingAssistantOverlay(
                            viewModel = mainViewModel,
                            onMoveToApp = { moveToApp() },
                            onDismiss = { hide() },
                            onUpdateTouchableRegion = { minimized, region ->
                                updateOverlayTouchableRegion(minimized, region)
                            },
                            screenShare = screenShare,
                            onOpenAssistSettings = { openAssistSettings() }
                        )
                    }
                }
            }
        }
        sessionComposeView = composeView
        return composeView
    }

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        startScreenCapture(args, showFlags)
        // Pick up a theme change made in Settings since the session process last ran.
        io.github.ronganam.dana.ui.theme.ThemeSettings.load(context)
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_START)
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_RESUME)
        // Probe the server right away so the overlay can say "unreachable"
        // before the user starts talking.
        io.github.ronganam.dana.data.network.ServerStatusMonitor.start(context)

        isOverlayMinimized = false
        overlayTouchableRegion.setEmpty()
        sessionComposeView?.requestLayout()

        window.window?.let { w ->
            w.setGravity(android.view.Gravity.FILL)
            w.setLayout(
                android.view.ViewGroup.LayoutParams.MATCH_PARENT,
                android.view.ViewGroup.LayoutParams.MATCH_PARENT
            )
            w.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.P) {
                val params = w.attributes
                params.layoutInDisplayCutoutMode = android.view.WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
                w.attributes = params
            }
            softInputResize(w)
            WindowCompat.setDecorFitsSystemWindows(w, false)
        }

        val now = System.currentTimeMillis()
        val lastActiveTime = prefs.getLong("last_active_timestamp", 0L)
        val idleThresholdMs = 2 * 60 * 1000L // 2 minutes

        val isIdleTooLong = (now - lastActiveTime) > idleThresholdMs
        val hasMessages = mainViewModel.chatController.chatMessages.value.isNotEmpty()

        if (isIdleTooLong || !hasMessages) {
            mainViewModel.startNewSession(saveOld = true)
        }
        prefs.edit { putLong("last_active_timestamp", now) }

        // Brief delay before auto-starting voice recording to let session initialize
        // and avoid picking up ambient noise during transition
        delayedVoiceRecordingJob?.cancel()
        delayedVoiceRecordingJob = CoroutineScope(Dispatchers.Main).launch {
            kotlinx.coroutines.delay(400)
            mainViewModel.chatController.startVoiceRecording()
        }

        // Speak replies as they finish. What is on screen when the sheet opens
        // was said before; later replies are spoken once each, by text (the
        // thread's stored copies replace the live bubbles when a run ends).
        spokenReplies.clear()
        mainViewModel.chatController.chatMessages.value.filter { isSpeakable(it) }.forEach { rememberSpoken(it.message) }
        var lastUserId = mainViewModel.chatController.chatMessages.value.lastOrNull { it.isUser }?.id
        voiceCollectionJob = CoroutineScope(Dispatchers.Main).launch {
            mainViewModel.chatController.chatMessages.collect { messages ->
                val isMuted = prefs.getBoolean("is_muted", false)
                // A new user turn supersedes anything still queued.
                val userId = messages.lastOrNull { it.isUser }?.id
                if (userId != null && userId != lastUserId && !userId.startsWith("msg_user_")) tts?.stop()
                lastUserId = userId
                val turnStart = messages.indexOfLast { it.isUser } + 1
                for (i in turnStart until messages.size) {
                    val msg = messages[i]
                    if (!isSpeakable(msg) || msg.message.trim() in spokenReplies) continue
                    rememberSpoken(msg.message)
                    if (!isMuted) tts?.run {
                        useVoiceFor(msg.message, context)
                        speak(msg.message, TextToSpeech.QUEUE_ADD, null, "dana_response")
                    }
                }
            }
        }
    }

    /**
     * A new opening: forget the last capture and expect this one. The system
     * captures the app underneath only when the show asks for it
     * (`SHOW_WITH_ASSIST` / `SHOW_WITH_SCREENSHOT`: the power button and the
     * assist gesture do; Dana's own widget doesn't) and the owner left the
     * assistant's "use text from screen" / "use screenshot" switches on
     * ([getUserDisabledShowContext]). The control starts off every time.
     */
    private fun startScreenCapture(args: Bundle?, showFlags: Int) {
        val disabled = runCatching { userDisabledShowContext }.getOrDefault(0)
        val fromDana = args?.getBoolean(DanaVoiceInteractionService.ARG_FROM_DANA, false) == true
        val withAssist = showFlags and SHOW_WITH_ASSIST != 0
        val source = when {
            fromDana -> ScreenShare.Source.DANA
            withAssist -> ScreenShare.Source.SYSTEM
            else -> ScreenShare.Source.NONE
        }
        android.util.Log.d(
            "DanaVoiceSession",
            "onShow flags=$showFlags userDisabled=$disabled source=$source"
        )
        screenShare.opened(
            source = source,
            textOffInSettings = disabled and SHOW_WITH_ASSIST != 0,
            screenshotOffInSettings = disabled and SHOW_WITH_SCREENSHOT != 0,
            screenshotRequested = showFlags and SHOW_WITH_SCREENSHOT != 0
        )
    }

    @Deprecated("Replaced by onHandleAssist(AssistState) on API 29+; still the call below 29.")
    override fun onHandleAssist(data: Bundle?, structure: AssistStructure?, content: AssistContent?) {
        android.util.Log.d("DanaVoiceSession", "onHandleAssist (legacy) structure=${structure != null}")
        screenShare.onAssist(structure, content)
    }

    // Android 10+ (older versions call the overload above).
    @androidx.annotation.RequiresApi(android.os.Build.VERSION_CODES.Q)
    override fun onHandleAssist(state: AssistState) {
        // One call per visible activity (split screen): the focused one is what he is looking at.
        android.util.Log.d(
            "DanaVoiceSession",
            "onHandleAssist ${state.index + 1}/${state.count} focused=${state.isFocused} structure=${state.assistStructure != null}"
        )
        if (state.isFocused || state.count <= 1) screenShare.onAssist(state.assistStructure, state.assistContent)
    }

    override fun onAssistStructureFailure(failure: Throwable) {
        android.util.Log.w("DanaVoiceSession", "Assist structure failed", failure)
        screenShare.onAssistFailure()
    }

    override fun onHandleScreenshot(screenshot: Bitmap?) {
        android.util.Log.d("DanaVoiceSession", "onHandleScreenshot ${screenshot?.width}x${screenshot?.height}")
        screenShare.onScreenshot(screenshot)
    }

    /** The system's assistant settings (where "use text from screen" / "use screenshot" live). */
    private fun openAssistSettings() {
        val intents = listOf(
            android.content.Intent(android.provider.Settings.ACTION_VOICE_INPUT_SETTINGS),
            android.content.Intent(android.provider.Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS),
            android.content.Intent(android.provider.Settings.ACTION_SETTINGS)
        )
        for (intent in intents) {
            intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
            if (intent.resolveActivity(context.packageManager) == null) continue
            try {
                context.startActivity(intent)
                hide()
                return
            } catch (e: Exception) {
                android.util.Log.w("DanaVoiceSession", "Couldn't open ${intent.action}", e)
            }
        }
    }

    /** A finished reply (not a tool card, the thinking row or text still streaming). */
    private fun isSpeakable(msg: io.github.ronganam.dana.data.models.ChatMessage) =
        !msg.isUser && msg.toolCall == null && !msg.isThinking && !msg.isStreaming && msg.message.isNotBlank()

    private fun rememberSpoken(text: String) {
        spokenReplies.addLast(text.trim())
        while (spokenReplies.size > 40) spokenReplies.removeFirst()
    }


    override fun onKeyDown(keyCode: Int, event: android.view.KeyEvent): Boolean {
        android.util.Log.d("DanaVoiceSession", "onKeyDown: keyCode=$keyCode")
        if (keyCode == android.view.KeyEvent.KEYCODE_ASSIST || 
            keyCode == android.view.KeyEvent.KEYCODE_VOICE_ASSIST ||
            keyCode == android.view.KeyEvent.KEYCODE_HEADSETHOOK ||
            keyCode == android.view.KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE) {
            event.startTracking()
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onKeyUp(keyCode: Int, event: android.view.KeyEvent): Boolean {
        android.util.Log.d("DanaVoiceSession", "onKeyUp: keyCode=$keyCode")
        if (keyCode == android.view.KeyEvent.KEYCODE_ASSIST || 
            keyCode == android.view.KeyEvent.KEYCODE_VOICE_ASSIST ||
            keyCode == android.view.KeyEvent.KEYCODE_HEADSETHOOK ||
            keyCode == android.view.KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE) {
            mainViewModel.chatController.stopVoiceRecording()
            return true
        }
        return super.onKeyUp(keyCode, event)
    }

    override fun onHide() {
        super.onHide()
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_PAUSE)
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_STOP)
        io.github.ronganam.dana.data.network.ServerStatusMonitor.stop()

        isOverlayMinimized = false
        overlayTouchableRegion.setEmpty()
        sessionComposeView?.requestLayout()

        delayedVoiceRecordingJob?.cancel()
        delayedVoiceRecordingJob = null
        mainViewModel.chatController.stopVoiceRecording()
        prefs.edit { putLong("last_active_timestamp", System.currentTimeMillis()) }
        voiceCollectionJob?.cancel()
        voiceCollectionJob = null
        tts?.stop()
        // The capture never outlives the opening it came with.
        screenShare.closed()
    }

    fun moveToApp() {
        // The app opens on the thread, where this conversation goes on.
        val intent = android.content.Intent(context, MainActivity::class.java).apply {
            addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK or android.content.Intent.FLAG_ACTIVITY_SINGLE_TOP)
            putExtra(EXTRA_OPEN_SCREEN, "chat")
        }
        try {
            startAssistantActivity(intent)
        } catch (e: Exception) {
            context.startActivity(intent)
        }
        hide()
    }

    override fun onDestroy() {
        super.onDestroy()
        delayedVoiceRecordingJob?.cancel()
        mainViewModel.chatController.screenAttachments = null
        screenShare.release()
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_DESTROY)
        store.clear()
        tts?.shutdown()
    }

    /** Below Android 11 the IME insets reach Compose only in adjustResize mode; later versions send them with edge-to-edge. */
    private fun softInputResize(w: android.view.Window) {
        if (android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.R) {
            @Suppress("DEPRECATION")
            w.setSoftInputMode(android.view.WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        }
    }
}
