package io.github.ronganam.dana.kiosk

import io.github.ronganam.dana.data.network.ServerConfig
import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.net.http.SslError
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.MotionEvent
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.lifecycleScope
import io.github.ronganam.dana.BuildConfig
import io.github.ronganam.dana.data.network.ServerStatusMonitor
import io.github.ronganam.dana.ui.components.FloatingAssistantOverlay
import io.github.ronganam.dana.ui.theme.DanaTheme
import io.github.ronganam.dana.ui.theme.ThemeSettings
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.wake.AssistantSession
import io.github.ronganam.dana.wake.DanaSpeaker
import io.github.ronganam.dana.wake.MicHub
import io.github.ronganam.dana.wake.WakeBus
import io.github.ronganam.dana.wake.WakePrefs
import io.github.ronganam.dana.wake.WakeWordService
import kotlinx.coroutines.launch
import java.net.URLEncoder

/**
 * The wall tablet's home screen: the bedroom dashboard in a full-screen
 * WebView, with Dana's assistant sheet (the same FloatingAssistantOverlay as
 * the phone's power-button assistant) opening on "Hey Dana".
 *
 * Voice turns stream PCM to the server (`/api/assistant/voice_stream`, Gemini
 * transcription — Hebrew and English) straight from the always-open mic.
 * After a spoken reply it listens once more for a follow-up, then closes
 * itself when idle.
 *
 * Hidden settings: tap the top-left corner 5 times.
 */
class KioskActivity : ComponentActivity() {
    private val vm: MainViewModel by viewModels()
    private lateinit var speaker: DanaSpeaker
    private val main = Handler(Looper.getMainLooper())

    private lateinit var session: AssistantSession
    private val assistantVisible get() = session.visible
    private var settingsVisible by mutableStateOf(false)
    private var webGeneration by mutableIntStateOf(0)
    private var webView: WebView? = null

    /** Latest ambient light (lux); -1 until the sensor reports. */
    @Volatile private var lux = -1f
    /** Backlight the dashboard asked for (0..1), or null for system brightness. */
    private var pageBacklight: Float? = null

    private var cornerTaps = 0
    private var firstCornerTapAt = 0L

    private val micPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) WakeWordService.start(this) else WakeBus.setStatus("Microphone permission denied")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        hideSystemBars()
        WebView.setWebContentsDebuggingEnabled(true)

        ThemeSettings.load(this)
        speaker = DanaSpeaker(this)
        vm.initializeChatOnly()
        session = AssistantSession(this, lifecycleScope, vm, speaker, onClosed = { applyBrightness() })
        session.installVoiceSource()

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                when {
                    settingsVisible -> settingsVisible = false
                    assistantVisible -> closeAssistant()
                    else -> webView?.takeIf { it.canGoBack() }?.goBack()
                }
            }
        })

        setContent {
            DanaTheme {
                Box(Modifier.fillMaxSize().background(androidx.compose.ui.graphics.Color.Black)) {
                    key(webGeneration) {
                        AndroidView(
                            modifier = Modifier.fillMaxSize(),
                            factory = { ctx -> buildWebView(ctx).also { webView = it; loadWhenSized(it) } }
                        )
                    }
                    if (assistantVisible) {
                        FloatingAssistantOverlay(
                            viewModel = vm,
                            onMoveToApp = null,
                            onDismiss = { closeAssistant() },
                            onMinimizeOverride = { closeAssistant() }
                        )
                    }
                    if (settingsVisible) {
                        KioskSettingsDialog(
                            speaker = speaker,
                            onDismiss = { settingsVisible = false },
                            onSaved = { applySettings() },
                            onTestWake = { settingsVisible = false; openAssistant(null) }
                        )
                    }
                }
            }
        }

        lifecycleScope.launch {
            WakeBus.wake.collect { frame -> openAssistant(frame) }
        }

        if (ServerConfig.load(this).token.isEmpty()) settingsVisible = true
        ensureMic()
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    /** `adb shell am start -n io.github.ronganam.dana.kiosk/io.github.ronganam.dana.kiosk.KioskActivity --ez settings true` (or `--ez assistant true`). */
    private fun handleIntent(intent: Intent?) {
        if (intent?.getBooleanExtra("settings", false) == true) settingsVisible = true
        if (intent?.getBooleanExtra("assistant", false) == true) openAssistant(null)
        if (intent?.getBooleanExtra("reload", false) == true) webView?.let { loadDashboard(it) }
        // Debug: feed a 16 kHz mono PCM16 WAV into the mic (`--es inject /sdcard/Download/x.wav`).
        if (BuildConfig.DEBUG) intent?.getStringExtra("inject")?.let { path ->
            Thread {
                runCatching {
                    val bytes = java.io.File(path).readBytes()
                    val data = bytes.copyOfRange(44, bytes.size)
                    MicHub.inject(ShortArray(data.size / 2) { i -> ((data[2 * i + 1].toInt() shl 8) or (data[2 * i].toInt() and 0xFF)).toShort() })
                    Log.i(TAG, "Injected ${data.size / 32000.0}s of audio from $path")
                }.onFailure { Log.e(TAG, "Inject failed", it) }
            }.start()
        }
    }

    override fun onStart() {
        super.onStart()
        ServerStatusMonitor.start(this)
        val sm = getSystemService(SensorManager::class.java)
        sm.getDefaultSensor(Sensor.TYPE_LIGHT)?.let {
            sm.registerListener(lightListener, it, SensorManager.SENSOR_DELAY_NORMAL)
        } ?: Log.w(TAG, "No ambient light sensor")
    }

    override fun onStop() {
        getSystemService(SensorManager::class.java).unregisterListener(lightListener)
        ServerStatusMonitor.stop()
        CookieManager.getInstance().flush()
        super.onStop()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemBars()
    }

    override fun onDestroy() {
        session.release()
        speaker.shutdown()
        webView?.destroy()
        super.onDestroy()
    }

    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        if (ev.actionMasked == MotionEvent.ACTION_DOWN) {
            session.noteInteraction()
            val corner = 140 * resources.displayMetrics.density / 2
            if (ev.x < corner && ev.y < corner) {
                val now = System.currentTimeMillis()
                if (now - firstCornerTapAt > 3000) { firstCornerTapAt = now; cornerTaps = 0 }
                if (++cornerTaps >= 5) { cornerTaps = 0; settingsVisible = true }
            }
        }
        return super.dispatchTouchEvent(ev)
    }

    // --- Microphone & voice turns ---

    private fun ensureMic() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            WakeWordService.start(this)
        } else {
            micPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun openAssistant(wakeFrame: Long?) {
        if (settingsVisible) return
        val wasOpen = session.visible
        session.open(wakeFrame)
        if (!wasOpen) applyBrightness()
    }

    private fun closeAssistant() = session.close()

    /**
     * The window's backlight override. The dashboard sets the night level via
     * `DanaKiosk.setBacklight` (null = system brightness by day); while the
     * assistant sheet is open it is lifted to at least 20% so the sheet is readable
     * without blasting a dark bedroom.
     */
    private fun applyBrightness() {
        val page = pageBacklight
        val level = when {
            page == null -> WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE
            assistantVisible -> maxOf(page, 0.2f)
            else -> page.coerceAtLeast(0.004f) // 0 would switch the panel off on some devices
        }
        window.attributes = window.attributes.apply { screenBrightness = level }
    }

    private val lightListener = object : SensorEventListener {
        override fun onSensorChanged(event: SensorEvent) { lux = event.values[0] }
        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
    }

    private fun hideSystemBars() {
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    private fun applySettings() {
        settingsVisible = false
        ServerConfig.load(this).let { vm.updateServerConfig(it.url, it.token) }
        webView?.let { loadDashboard(it, forceLogin = true) }
    }

    // --- Dashboard WebView ---

    @SuppressLint("SetJavaScriptEnabled")
    private fun buildWebView(ctx: android.content.Context): WebView = WebView(ctx).apply {
        layoutParams = android.view.ViewGroup.LayoutParams(
            android.view.ViewGroup.LayoutParams.MATCH_PARENT,
            android.view.ViewGroup.LayoutParams.MATCH_PARENT
        )
        setBackgroundColor(Color.BLACK)
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.mediaPlaybackRequiresUserGesture = false
        settings.mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
        settings.userAgentString = settings.userAgentString + " DanaKiosk/" + BuildConfig.VERSION_NAME
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)
        addJavascriptInterface(Bridge(), "DanaKiosk")
        webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) = request.deny()
        }
        webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = false

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) {
                    Log.w(TAG, "Dashboard failed to load: ${error.errorCode} ${error.description}")
                    scheduleReload(view)
                }
            }

            override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
                if (request.isForMainFrame && response.statusCode >= 500) {
                    Log.w(TAG, "Dashboard HTTP ${response.statusCode}")
                    scheduleReload(view)
                }
            }

            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                Log.w(TAG, "SSL error on ${error.url}: ${error.primaryError}")
                handler.cancel()
                scheduleReload(view)
            }

            override fun onPageFinished(view: WebView, url: String) {
                // Landed on the login form: the saved token is wrong or missing.
                if (url.contains("/login")) {
                    Log.w(TAG, "Dashboard asked for login; opening settings")
                    settingsVisible = true
                }
            }

            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                Log.e(TAG, "WebView renderer gone (crash=${detail.didCrash()}); rebuilding")
                if (webView === view) webView = null
                view.destroy()
                webGeneration++
                return true
            }
        }
    }

    /**
     * Navigate only once the WebView has its real size: if the first page load
     * starts while it is still 0×0, this WebView keeps `100dvh` at 0 and the
     * dashboard (sized with dvh) collapses to nothing.
     */
    private fun loadWhenSized(view: WebView) {
        if (view.width > 0 && view.height > 0) {
            loadDashboard(view)
            return
        }
        view.addOnLayoutChangeListener(object : android.view.View.OnLayoutChangeListener {
            override fun onLayoutChange(v: android.view.View, l: Int, t: Int, r: Int, b: Int, ol: Int, ot: Int, or: Int, ob: Int) {
                if (r - l > 0 && b - t > 0) {
                    v.removeOnLayoutChangeListener(this)
                    v.post { loadDashboard(view) }
                }
            }
        })
    }

    private var reloadPending = false

    private fun scheduleReload(view: WebView) {
        if (reloadPending) return
        reloadPending = true
        main.postDelayed({
            reloadPending = false
            if (webView === view) loadDashboard(view)
        }, 15_000)
    }

    private fun loadDashboard(view: WebView, forceLogin: Boolean = false) {
        val (server, token) = ServerConfig.load(this)
        val path = KioskPrefs.dashboardPath(this)
        val hasSession = CookieManager.getInstance().getCookie(server)?.contains("$SESSION_COOKIE=") == true
        if (token.isNotEmpty() && (forceLogin || !hasSession)) {
            // Exchange the API token for the dashboard's long-lived session cookie.
            val body = "token=" + URLEncoder.encode(token, "UTF-8") + "&next=" + URLEncoder.encode(path, "UTF-8")
            view.postUrl("$server/login", body.toByteArray())
        } else {
            view.loadUrl(server + path)
        }
    }

    /** `window.DanaKiosk` for the dashboard page. */
    private inner class Bridge {
        @JavascriptInterface
        fun openAssistant() = main.post { openAssistant(null) }.let { }

        @JavascriptInterface
        fun version(): String = BuildConfig.VERSION_NAME

        /** Ambient light in lux from the tablet's sensor; -1 when there is no reading. */
        @JavascriptInterface
        fun lux(): Float = lux

        /** Real backlight for the dashboard's night mode: 0-255, or < 0 for system brightness. */
        @JavascriptInterface
        fun setBacklight(level: Int) {
            main.post {
                pageBacklight = if (level < 0) null else level.coerceAtMost(255) / 255f
                applyBrightness()
            }
        }
    }

    companion object {
        private const val TAG = "KioskActivity"
        /** The web session cookie `/login` sets (server `server/core/auth.py`); without it the kiosk signs in with its token. */
        private const val SESSION_COOKIE = "dana_session"
    }
}
