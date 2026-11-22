package io.github.ronganam.dana.wear

import androidx.activity.result.contract.ActivityResultContracts
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.lifecycle.ViewModelProvider
import io.github.ronganam.dana.wear.perf.WearPerf
import androidx.wear.compose.navigation.SwipeDismissableNavHost
import androidx.wear.compose.navigation.composable
import androidx.wear.compose.navigation.rememberSwipeDismissableNavController
import io.github.ronganam.dana.wear.data.NeedsCodeReason
import io.github.ronganam.dana.wear.data.WatchPairState
import io.github.ronganam.dana.wear.data.WatchPairing
import io.github.ronganam.dana.wear.data.WearConfigReceiver
import io.github.ronganam.dana.wear.ui.WearViewModel
import io.github.ronganam.dana.wear.ui.screens.AssistantScreen
import io.github.ronganam.dana.wear.ui.screens.PairScreen
import io.github.ronganam.dana.wear.ui.screens.SettingsScreen
import io.github.ronganam.dana.wear.ui.screens.SideMenuScreen
import kotlinx.coroutines.launch
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.wear.compose.material.MaterialTheme
import androidx.wear.compose.material.Text
import io.github.ronganam.dana.wear.R
import androidx.compose.ui.res.stringResource

class MainActivity : ComponentActivity() {

    private var wearViewModel: WearViewModel? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        markOpen(intent, cold = true)
        WearPerf.mark("activity created")

        if (!packageManager.hasSystemFeature(android.content.pm.PackageManager.FEATURE_WATCH)) {
            setContent {
                io.github.ronganam.dana.wear.ui.theme.DanaWearTheme {
                    Box(
                        modifier = Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            text = stringResource(R.string.not_a_phone_app),
                            textAlign = TextAlign.Center,
                            modifier = Modifier.padding(16.dp)
                        )
                    }
                }
            }
            return
        }

        val missing = buildList {
            if (!hasMicPermission()) add(android.Manifest.permission.RECORD_AUDIO)
            // Reminder alarms forwarded from the phone post a notification.
            if (android.os.Build.VERSION.SDK_INT >= 33 &&
                checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED
            ) add(android.Manifest.permission.POST_NOTIFICATIONS)
        }
        if (missing.isNotEmpty()) permissionRequest.launch(missing.toTypedArray())

        WearConfigReceiver.onConfigUpdated = {
            runOnUiThread {
                wearViewModel?.reloadConfiguration()
            }
        }

        // Start listening before the first composition: the mic opens on its
        // own thread while Compose lays out the screen (it used to wait for a
        // LaunchedEffect after the first frame, then a Play Services lookup).
        val vm = ViewModelProvider(this)[WearViewModel::class.java]
        wearViewModel = vm
        vm.initialize(this)
        if (!handleIntent(intent)) vm.onAppOpened(hasMicPermission())
        // Its own device on the server: through the phone, else the code entry opens (WearApp).
        WatchPairing.ensure(this)

        setContent {
            io.github.ronganam.dana.wear.ui.theme.DanaWearTheme {
                WearApp(viewModel = vm)
            }
        }
    }

    /** Starts the DanaPerf timer unless the assist button already did. */
    private fun markOpen(intent: Intent?, cold: Boolean) {
        val fromAssistButton = intent?.getBooleanExtra("start_listening", false) == true
        if (fromAssistButton && WearPerf.startedWithin(10_000)) return
        val cause = when {
            intent?.action == Intent.ACTION_ASSIST -> "assist intent"
            fromAssistButton -> "assist button"
            else -> "launcher"
        }
        // The log line carries the process age; a fresh process shows there.
        WearPerf.start(if (cold) "$cause (activity created)" else cause)
    }

    private var startedOnce = false
    private var handledNewIntent = false

    override fun onStart() {
        super.onStart()
        // Opening the app again (from the launcher, or waking back into it)
        // listens right away unless a conversation is in progress. The first
        // start is handled once the ViewModel exists (see setContent).
        if (startedOnce && !handledNewIntent) {
            WearPerf.start("app resumed")
            wearViewModel?.onAppOpened(hasMicPermission())
        }
        startedOnce = true
        handledNewIntent = false
    }

    override fun onStop() {
        super.onStop()
        if (!isChangingConfigurations) wearViewModel?.onAppHidden()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        markOpen(intent, cold = false)
        // onNewIntent runs before onStart; don't open twice.
        handledNewIntent = handleIntent(intent)
    }

    /** The mic (and notifications): once the mic is allowed, the app opens listening as it would have. */
    private val permissionRequest = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        if (hasMicPermission()) wearViewModel?.onAppOpened(true)
    }

    private fun hasMicPermission() =
        checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED

    /** Returns true when the intent asked to start listening (and it did). */
    private fun handleIntent(intent: Intent?): Boolean {
        if (intent == null) return false
        val isAssist = intent.action == Intent.ACTION_ASSIST || intent.getBooleanExtra("start_listening", false)
        if (isAssist && hasMicPermission()) {
            wearViewModel?.startNewSessionAndListen(handsFree = true)
        }
        return isAssist && wearViewModel != null
    }

    override fun onDestroy() {
        super.onDestroy()
        WearConfigReceiver.onConfigUpdated = null
    }
}

/** Whether the code entry already opened by itself in this process. */
private var codeEntryShown = false

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun WearApp(viewModel: WearViewModel) {
    val navController = rememberSwipeDismissableNavController()
    val coroutineScope = rememberCoroutineScope()
    // No phone to pair through: show the code entry (once per process).
    val pairState by WatchPairing.state.collectAsState()
    LaunchedEffect(pairState) {
        val s = pairState
        if (s is WatchPairState.NeedsCode && s.reason == NeedsCodeReason.NO_PHONE && !codeEntryShown) {
            codeEntryShown = true
            navController.navigate("pair")
        }
    }

    SwipeDismissableNavHost(
        navController = navController,
        startDestination = "home"
    ) {
        composable("home") {
            val pagerState = rememberPagerState(pageCount = { 2 })
            HorizontalPager(state = pagerState) { page ->
                when (page) {
                    0 -> AssistantScreen(viewModel = viewModel, active = pagerState.currentPage == 0)
                    1 -> SideMenuScreen(
                        viewModel = viewModel,
                        onSessionSelected = {
                            coroutineScope.launch { pagerState.animateScrollToPage(0) }
                        },
                        onNavigateToSettings = {
                            navController.navigate("settings")
                        },
                        active = pagerState.currentPage == 1
                    )
                }
            }
        }
        composable("settings") {
            SettingsScreen(viewModel = viewModel, onOpenPairing = { navController.navigate("pair") })
        }
        composable("pair") {
            PairScreen()
        }
    }
}
