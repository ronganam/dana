package io.github.ronganam.dana.tv

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.focus.FocusRequester
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import io.github.ronganam.dana.ui.theme.DanaTheme
import io.github.ronganam.dana.wake.WakeBus
import io.github.ronganam.dana.wake.WakePrefs
import io.github.ronganam.dana.wake.WakeWordService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Home-screen state: installed apps and the Home Assistant tiles (polled while visible). */
class HomeModel(val context: Context, private val scope: CoroutineScope) {
    var apps by mutableStateOf<List<TvApp>>(emptyList())
        private set
    var entities by mutableStateOf<Map<String, HaEntity>>(emptyMap())
        private set
    var haError by mutableStateOf<String?>(null)
        private set
    var resumed by mutableStateOf(false)
    /** Bumped on every Home press: scroll back and focus the first app. */
    var homeRequests by mutableIntStateOf(0)

    private var poller: Job? = null

    fun refreshApps() = scope.launch {
        runCatching { TvApps.list(context) }.onSuccess { apps = it }.onFailure { Log.w(TAG, "App list failed", it) }
    }

    fun startPolling() {
        poller?.cancel()
        poller = scope.launch {
            while (isActive) {
                refreshEntities()
                delay(POLL_MS)
            }
        }
    }

    fun stopPolling() { poller?.cancel(); poller = null }

    private suspend fun refreshEntities() {
        val ids = TvConfig.current.entities
        if (ids.isEmpty()) return
        runCatching { HaClient.states(context, ids) }
            .onSuccess { list -> entities = list.associateBy { it.entityId }; haError = null }
            .onFailure {
                // Polling stops when the screen pauses; that cancellation isn't "offline".
                if (it is kotlinx.coroutines.CancellationException) throw it
                haError = "offline"; Log.w(TAG, "HA states failed: ${it.message}")
            }
    }

    /** OK on a tile: flip it on screen right away, then confirm with HA. */
    fun runPrimary(id: String) {
        val e = entities[id] ?: return
        val (domain, service) = e.primaryService() ?: return
        if (service == "toggle") {
            val flipped = when (e.state) {
                "on" -> "off"; "off" -> "on"; "open" -> "closing"; "closed" -> "opening"; else -> null
            }
            if (flipped != null) entities = entities + (id to e.copy(state = flipped))
        }
        call(id, domain, service)
    }

    fun call(id: String, domain: String, service: String, data: JSONObject = JSONObject()) = scope.launch {
        runCatching { HaClient.call(context, domain, service, id, data) }
            .onFailure { haError = "action failed"; Log.w(TAG, "$domain.$service failed", it) }
        delay(600)
        refreshEntities()
    }

    companion object {
        private const val TAG = "HomeModel"
        private const val POLL_MS = 5_000L
    }
}

private enum class Screen { HOME, ALL_APPS, SETTINGS }

/**
 * Dana TV's home screen (HOME + leanback launcher): favourite apps, Home
 * Assistant controls, cameras, and the entry to settings. Starts the "Hey
 * Dana" wake-word service; the assistant itself opens in [AssistantActivity].
 *
 * adb setup: `am start -n io.github.ronganam.dana.tv/.tv.TvHomeActivity --es server URL --es token TOKEN`
 * (`--ez settings true` opens settings).
 */
class TvHomeActivity : ComponentActivity() {
    private lateinit var model: HomeModel
    private var screen by mutableStateOf(Screen.HOME)
    private val firstApp = FocusRequester()

    private val micPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) WakeWordService.start(this) else WakeBus.setStatus("Microphone permission denied")
    }

    private val packagesChanged = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) { model.refreshApps() }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        TvConfig.load(this)
        model = HomeModel(applicationContext, lifecycleScope)
        applyIntent(intent)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                // The launcher has nowhere to go back to.
                if (screen != Screen.HOME) goHome()
            }
        })

        setContent {
            DanaTheme(darkTheme = true) {
                when (screen) {
                    Screen.HOME -> HomeScreen(
                        model, firstApp,
                        onAllApps = { screen = Screen.ALL_APPS },
                        onSettings = { screen = Screen.SETTINGS },
                        onAskDana = ::askDana,
                    )
                    Screen.ALL_APPS -> AllAppsScreen(model, onBack = ::goHome)
                    Screen.SETTINGS -> TvSettingsScreen(model, onBack = ::goHome, onAskDana = ::askDana)
                }
            }
        }

        registerReceiver(packagesChanged, IntentFilter().apply {
            addAction(Intent.ACTION_PACKAGE_ADDED)
            addAction(Intent.ACTION_PACKAGE_REMOVED)
            addAction(Intent.ACTION_PACKAGE_CHANGED)
            addDataScheme("package")
        })
        model.refreshApps()
        ensureMic()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        applyIntent(intent)
        // Home pressed while already home: back to the top.
        if (intent.hasCategory(Intent.CATEGORY_HOME) && !intent.getBooleanExtra("settings", false)) goHome()
    }

    private fun applyIntent(intent: Intent?) {
        intent ?: return
        val server = intent.getStringExtra("server")
        val token = intent.getStringExtra("token")
        if (server != null || token != null) {
            WakePrefs.saveServer(this, server ?: WakePrefs.serverUrl(this), token ?: WakePrefs.apiToken(this))
            Log.i(TAG, "Server config set from intent")
        }
        if (intent.getBooleanExtra("settings", false)) screen = Screen.SETTINGS
        // adb: `--ez assistant true` opens Dana, as the Ask Dana button does.
        if (intent.getBooleanExtra("assistant", false)) askDana()
    }

    private fun goHome() {
        screen = Screen.HOME
        model.homeRequests++
    }

    private fun askDana() {
        io.github.ronganam.dana.wake.OpenTimer.start("button")
        startActivity(Intent(this, AssistantActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    override fun onResume() {
        super.onResume()
        model.resumed = true
        model.startPolling()
        KeyServiceGuard.ensureEnabled(this)
        if (model.homeRequests == 0) model.homeRequests++
    }

    override fun onPause() {
        model.resumed = false
        model.stopPolling()
        super.onPause()
    }

    override fun onDestroy() {
        runCatching { unregisterReceiver(packagesChanged) }
        super.onDestroy()
    }

    private fun ensureMic() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            WakeWordService.start(this)
        } else {
            micPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    companion object {
        private const val TAG = "TvHomeActivity"
    }
}
