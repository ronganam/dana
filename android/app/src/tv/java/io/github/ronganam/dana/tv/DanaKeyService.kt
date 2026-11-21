package io.github.ronganam.dana.tv

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** Hands the next remote button to the settings screen ("press the button to bind"). */
object KeyCapture {
    private val _active = MutableStateFlow(false)
    val active = _active.asStateFlow()
    private val _captured = MutableStateFlow<Int?>(null)
    val captured = _captured.asStateFlow()

    fun start() { _captured.value = null; _active.value = true }
    fun cancel() { _active.value = false }
    fun deliver(keyCode: Int) { _captured.value = keyCode; _active.value = false }
}

/**
 * Remote-button bindings, system-wide (over any app): an accessibility service
 * that filters key events. A bound button runs its short-press action on
 * release, or its long-press action once held for [LONG_PRESS_MS]; buttons
 * without bindings pass through untouched. Bound buttons lose their original
 * function (an accessibility service can't re-inject the key).
 *
 * Also hosts the camera PiP window ([CameraPip]) and, when enabled, catches the
 * Home button so it opens Dana TV even if Google TV keeps its launcher on Home.
 */
class DanaKeyService : AccessibilityService() {
    private val main = Handler(Looper.getMainLooper())
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    private var heldKey = 0
    private var longFired = false
    private var longRunnable: Runnable? = null

    override fun onServiceConnected() {
        instance = this
        TvConfig.load(this)
        Log.i(TAG, "Key service connected")
    }

    /**
     * Google TV's launcher outranks Dana TV for HOME (privileged, higher
     * intent-filter priority), so when its home screen comes forward — Home
     * pressed elsewhere, Back out of an app, boot — open Dana TV instead.
     */
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return
        if (!TvConfig.current.homeOverride) return
        if (event.packageName?.toString() != GOOGLE_TV_LAUNCHER) return
        if (event.className?.toString()?.endsWith(".home.HomeActivity") != true) return
        val now = System.currentTimeMillis()
        if (now - lastRedirectAt < 1500) return
        lastRedirectAt = now
        Log.i(TAG, "Google TV home came forward; opening Dana TV")
        goHome()
    }

    private var lastRedirectAt = 0L
    override fun onInterrupt() {}

    override fun onUnbind(intent: Intent?): Boolean {
        instance = null
        main.post { CameraPip.hide() }
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    override fun onKeyEvent(event: KeyEvent): Boolean {
        val code = event.keyCode
        if (io.github.ronganam.dana.BuildConfig.DEBUG) Log.d(TAG, "key ${KeyEvent.keyCodeToString(code)} action=${event.action} repeat=${event.repeatCount}")
        if (KeyCapture.active.value) {
            if (code == KeyEvent.KEYCODE_BACK) return false // Back cancels the capture dialog
            if (event.action == KeyEvent.ACTION_UP) KeyCapture.deliver(code)
            return true
        }
        val settings = TvConfig.current
        // Home: Dana TV; held: Android TV settings (stands in for Google TV's dashboard).
        val binds = if (code == KeyEvent.KEYCODE_HOME && settings.homeOverride) HOME_BINDINGS
        else settings.bindings.filter { it.keyCode == code }
        if (binds.isEmpty()) return false
        val short = binds.firstOrNull { it.press == Press.SHORT }
        val long = binds.firstOrNull { it.press == Press.LONG }

        when (event.action) {
            KeyEvent.ACTION_DOWN -> if (event.repeatCount == 0) {
                cancelLong()
                heldKey = code
                longFired = false
                if (long != null) {
                    longRunnable = Runnable {
                        if (heldKey == code) { longFired = true; perform(long.action) }
                    }.also { main.postDelayed(it, LONG_PRESS_MS) }
                }
            }
            KeyEvent.ACTION_UP -> {
                cancelLong()
                // Decide from the event's own timestamps: the timer can run late when the
                // main thread is busy, but the key's down/up times are exact.
                val heldMs = event.eventTime - event.downTime
                if (heldKey == code && !longFired) {
                    when {
                        long != null && heldMs >= LONG_PRESS_MS -> perform(long.action)
                        short != null -> perform(short.action)
                    }
                }
                heldKey = 0
            }
        }
        return true
    }

    private fun cancelLong() {
        longRunnable?.let { main.removeCallbacks(it) }
        longRunnable = null
    }

    private fun perform(action: TvAction) {
        Log.i(TAG, "Button action: $action")
        when (action) {
            is TvAction.TogglePip -> CameraPip.toggle(this, action.cameraId)
            is TvAction.OpenApp -> TvApps.launch(this, action.pkg)
            is TvAction.ToggleEntity -> scope.launch {
                runCatching {
                    val e = HaClient.states(this@DanaKeyService, listOf(action.entityId)).firstOrNull()
                    val (domain, service) = e?.primaryService() ?: ("homeassistant" to "toggle")
                    HaClient.call(this@DanaKeyService, domain, service, action.entityId)
                }.onFailure { Log.w(TAG, "HA action failed", it) }
            }
            TvAction.OpenAssistant -> {
                io.github.ronganam.dana.wake.OpenTimer.start("remote button")
                startActivity(Intent(this, AssistantActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            TvAction.GoHome -> goHome()
            TvAction.OpenSettings -> openAndroidSettings(this)
        }
    }

    private fun goHome() {
        startActivity(
            Intent(this, TvHomeActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        )
    }

    companion object {
        private const val TAG = "DanaKeyService"
        const val LONG_PRESS_MS = 550L
        const val GOOGLE_TV_LAUNCHER = "com.google.android.apps.tv.launcherx"
        private val HOME_BINDINGS = listOf(
            Binding(KeyEvent.KEYCODE_HOME, Press.SHORT, TvAction.GoHome),
            Binding(KeyEvent.KEYCODE_HOME, Press.LONG, TvAction.OpenSettings),
        )

        @Volatile var instance: DanaKeyService? = null
            private set
    }
}
