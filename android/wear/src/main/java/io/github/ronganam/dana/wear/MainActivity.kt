package io.github.ronganam.dana.wear

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.lifecycle.ViewModelProvider
import io.github.ronganam.dana.wear.perf.WearPerf
import androidx.wear.compose.navigation.SwipeDismissableNavHost
import androidx.wear.compose.navigation.composable
import androidx.wear.compose.navigation.rememberSwipeDismissableNavController
import io.github.ronganam.dana.wear.data.WearConfigReceiver
import io.github.ronganam.dana.wear.ui.WearViewModel
import io.github.ronganam.dana.wear.ui.screens.AssistantScreen
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
                            text = "This is the Dana watch app.\nPlease install it on your watch.",
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
        if (missing.isNotEmpty()) requestPermissions(missing.toTypedArray(), 101)

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

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 101 && hasMicPermission()) wearViewModel?.onAppOpened(true)
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

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun WearApp(viewModel: WearViewModel) {
    val navController = rememberSwipeDismissableNavController()
    val coroutineScope = rememberCoroutineScope()

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
            SettingsScreen(viewModel = viewModel)
        }
    }
}
