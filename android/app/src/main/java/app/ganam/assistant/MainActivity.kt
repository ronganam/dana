package app.ganam.assistant

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Email
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.People
import androidx.compose.material.icons.outlined.People
import androidx.compose.material.icons.outlined.Email
import androidx.compose.material.icons.outlined.Folder
import androidx.compose.material.icons.automirrored.outlined.List
import androidx.compose.material.icons.outlined.Phone
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Star
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavDestination
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import app.ganam.assistant.ui.screens.CallsScreen
import app.ganam.assistant.ui.screens.ChatScreen
import app.ganam.assistant.ui.screens.MemoryScreen
import app.ganam.assistant.ui.screens.projects.ProjectsScreen
import app.ganam.assistant.ui.screens.SettingsScreen
import app.ganam.assistant.ui.screens.TasksScreen
import androidx.compose.material.icons.filled.Phone
import app.ganam.assistant.ui.theme.GanamAssistantTheme
import app.ganam.assistant.ui.theme.isWideLayout

import androidx.lifecycle.viewmodel.compose.viewModel
import app.ganam.assistant.ui.viewmodels.MainViewModel

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.draw.clip
import app.ganam.assistant.ui.components.AnimatedBackground
import androidx.compose.ui.Alignment
import androidx.compose.foundation.clickable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.material.icons.filled.ChatBubble
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Inbox
import androidx.compose.material.icons.outlined.Inbox
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.HorizontalDivider
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

import android.os.Build
import android.content.Intent
import android.net.Uri
import android.util.Log
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import app.ganam.assistant.ui.components.ServerStatusBanner
import androidx.compose.ui.text.style.TextAlign

/** Intent extra: open the Inbox tab (Dana's snooze / alert notifications). */
const val EXTRA_OPEN_INBOX = "app.ganam.assistant.OPEN_INBOX"

class MainActivity : ComponentActivity() {
    private companion object {
        const val EXTRA_SHARE_HANDLED = "app.ganam.assistant.SHARE_HANDLED"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)

        if (packageManager.hasSystemFeature(android.content.pm.PackageManager.FEATURE_WATCH)) {
            setContent {
                MaterialTheme {
                    Box(
                        modifier = Modifier
                            .fillMaxSize(),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            text = "This is the Dana phone app.\nPlease install it on your phone.",
                            textAlign = TextAlign.Center,
                            modifier = Modifier.padding(24.dp)
                        )
                    }
                }
            }
            return
        }
        
        val permissionsToRequest = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            permissionsToRequest.add(android.Manifest.permission.POST_NOTIFICATIONS)
            permissionsToRequest.add(android.Manifest.permission.READ_MEDIA_AUDIO)
        } else {
            permissionsToRequest.add(android.Manifest.permission.READ_EXTERNAL_STORAGE)
        }
        // Call direction / number / name for auto-uploaded recordings.
        permissionsToRequest.add(android.Manifest.permission.READ_CALL_LOG)
        // Caller card: notice when the phone rings (phone build only declares it).
        if (!BuildConfig.KIOSK && !BuildConfig.TV) permissionsToRequest.add(android.Manifest.permission.READ_PHONE_STATE)

        val ungranted = permissionsToRequest.filter {
            checkSelfPermission(it) != android.content.pm.PackageManager.PERMISSION_GRANTED
        }

        if (ungranted.isNotEmpty()) {
            requestPermissions(ungranted.toTypedArray(), 1001)
        } else {
            startCallObserverService()
        }

        app.ganam.assistant.ui.theme.ThemeSettings.load(applicationContext)
        app.ganam.assistant.ui.shell.LayoutSettings.load(applicationContext)
        setContent {
            GanamAssistantTheme {
                val mainViewModel: MainViewModel = viewModel()
                LaunchedEffect(Unit) {
                    mainViewModel.initialize(applicationContext)
                }
                // The new layout talks in the shared thread; "Previous layout" in per-session chats.
                val previousLayout = app.ganam.assistant.ui.shell.LayoutSettings.previousLayout
                LaunchedEffect(previousLayout) { mainViewModel.chatController.setThreadMode(!previousLayout) }
                if (previousLayout) {
                    PreviousMainScreen(mainViewModel)
                } else {
                    // The main screen: Now / Reminders / Notifications / More + ask bar + thread.
                    app.ganam.assistant.ui.shell.DanaShell(mainViewModel)
                }
            }
        }

        DanaWidgets.schedule(applicationContext)

        if (savedInstanceState == null) {
            handleShareIntent(intent)
            handleDeepLink(intent)
        }
    }

    /** From the Debrief / notifications: open a chat with something attached, or a call. */
    private fun handleDeepLink(intent: Intent?) {
        intent ?: return
        val vm = ViewModelProvider(this)[MainViewModel::class.java]
        val kind = intent.getStringExtra(CallDebriefActivity.EXTRA_CONTEXT_KIND)
        val id = intent.getStringExtra(CallDebriefActivity.EXTRA_CONTEXT_ID)
        val callId = intent.getStringExtra(CallDebriefActivity.EXTRA_OPEN_CALL_ID)
        val openInbox = intent.getBooleanExtra(EXTRA_OPEN_INBOX, false)
        val personId = intent.getStringExtra(EXTRA_OPEN_PERSON_ID)
        val openReview = intent.getBooleanExtra(EXTRA_OPEN_REVIEW, false)
        val openScreen = intent.getStringExtra(EXTRA_OPEN_SCREEN)
        when {
            kind != null && id != null -> vm.openContextChat(kind, id, intent.getStringExtra(CallDebriefActivity.EXTRA_CONTEXT_TITLE).orEmpty())
            callId != null -> vm.openCall(callId)
            personId != null -> vm.openPerson(personId)
            openReview -> vm.openReview()
            openInbox -> vm.navigateTo(Screen.Inbox.route)
            // "waiting" (the Waiting-on-you widget): Now in the new layout, Calls in the old one.
            openScreen == "waiting" -> vm.navigateTo(if (app.ganam.assistant.ui.shell.LayoutSettings.previousLayout) Screen.Calls.route else "now")
            openScreen != null -> vm.navigateTo(openScreen)
            else -> return
        }
        intent.removeExtra(EXTRA_OPEN_INBOX)
        intent.removeExtra(EXTRA_OPEN_PERSON_ID)
        intent.removeExtra(EXTRA_OPEN_REVIEW)
        intent.removeExtra(EXTRA_OPEN_SCREEN)
        // Handle once (not again after a configuration change).
        intent.removeExtra(CallDebriefActivity.EXTRA_CONTEXT_KIND)
        intent.removeExtra(CallDebriefActivity.EXTRA_CONTEXT_ID)
        intent.removeExtra(CallDebriefActivity.EXTRA_CONTEXT_TITLE)
        intent.removeExtra(CallDebriefActivity.EXTRA_OPEN_CALL_ID)
    }

    override fun onStart() {
        super.onStart()
        // Poll server reachability only while the app is visible.
        app.ganam.assistant.data.network.ServerStatusMonitor.start(this)
        if (!packageManager.hasSystemFeature(android.content.pm.PackageManager.FEATURE_WATCH)) {
            ViewModelProvider(this)[MainViewModel::class.java].onAppForeground()
        }
    }

    override fun onStop() {
        super.onStop()
        app.ganam.assistant.data.network.ServerStatusMonitor.stop()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleShareIntent(intent)
        handleDeepLink(intent)
    }

    private fun handleShareIntent(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND) return
        // Handle each share once (not again after a configuration change).
        if (intent.getBooleanExtra(EXTRA_SHARE_HANDLED, false)) return
        intent.putExtra(EXTRA_SHARE_HANDLED, true)

        val uri = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)
        }
        val type = intent.type.orEmpty()
        val vm = ViewModelProvider(this)[MainViewModel::class.java]
        try {
            when {
                type.startsWith("audio/") && uri != null -> {
                    vm.setSharedAudioUri(uri)
                    vm.navigateTo(Screen.Calls.route)
                }
                type.startsWith("image/") && uri != null -> {
                    val text = sharedText(intent)
                    // The read grant on a shared content:// URI ends with this
                    // activity, so keep our own copy for the upload.
                    lifecycleScope.launch {
                        val copy = withContext(Dispatchers.IO) { copySharedImage(uri, contentResolver.getType(uri)) }
                        vm.startChatFromShare(text, copy ?: uri, newSession = app.ganam.assistant.ui.shell.LayoutSettings.previousLayout)
                    }
                }
                else -> {
                    val text = sharedText(intent) ?: return
                    vm.startChatFromShare(text, null, newSession = app.ganam.assistant.ui.shell.LayoutSettings.previousLayout)
                }
            }
        } catch (e: Exception) {
            Log.e("MainActivity", "Error handling share intent", e)
        }
    }

    /** Subject and text of a share, e.g. an article's title and link. */
    private fun sharedText(intent: Intent): String? {
        val subject = intent.getStringExtra(Intent.EXTRA_SUBJECT)?.trim().orEmpty()
        val text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString()?.trim().orEmpty()
        val combined = when {
            subject.isNotEmpty() && text.isNotEmpty() && !text.contains(subject) -> "$subject\n$text"
            text.isNotEmpty() -> text
            else -> subject
        }
        return combined.ifEmpty { null }
    }

    private fun copySharedImage(uri: Uri, mimeType: String?): Uri? = try {
        val dir = java.io.File(cacheDir, "shared").apply { mkdirs() }
        // Keep the folder small: drop earlier shares.
        dir.listFiles()?.forEach { it.delete() }
        val ext = android.webkit.MimeTypeMap.getSingleton().getExtensionFromMimeType(mimeType ?: "") ?: "jpg"
        val out = java.io.File(dir, "share_${System.currentTimeMillis()}.$ext")
        contentResolver.openInputStream(uri)?.use { input -> out.outputStream().use { input.copyTo(it) } }
            ?: throw java.io.IOException("Couldn't open the shared image")
        Uri.fromFile(out)
    } catch (e: Exception) {
        Log.e("MainActivity", "Couldn't copy the shared image", e)
        null
    }

    private fun startCallObserverService() {
        CallRecordingObserverService.start(this)
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 1001) {
            val hasStoragePermission = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                checkSelfPermission(android.Manifest.permission.READ_MEDIA_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED
            } else {
                checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE) == android.content.pm.PackageManager.PERMISSION_GRANTED
            }
            if (hasStoragePermission) {
                startCallObserverService()
            }
        }
    }
}

sealed class Screen(
    val route: String,
    val label: String,
    val icon: @Composable () -> Unit,
    val iconIdle: @Composable () -> Unit
) {
    object Chat : Screen("chat", "Chat",
        { Icon(Icons.Filled.ChatBubble, contentDescription = "Chat") },
        { Icon(Icons.Outlined.ChatBubbleOutline, contentDescription = "Chat") })
    object Tasks : Screen("tasks", "Reminders",
        { Icon(Icons.Filled.Notifications, contentDescription = "Reminders") },
        { Icon(Icons.Outlined.Notifications, contentDescription = "Reminders") })
    object Inbox : Screen("inbox", "Inbox",
        { Icon(Icons.Filled.Inbox, contentDescription = "Inbox") },
        { Icon(Icons.Outlined.Inbox, contentDescription = "Inbox") })
    object Memory : Screen("memory", "Memory",
        { Icon(Icons.Filled.Psychology, contentDescription = "Memory") },
        { Icon(Icons.Outlined.Psychology, contentDescription = "Memory") })
    object Projects : Screen("projects", "Projects",
        { Icon(Icons.Filled.Folder, contentDescription = "Projects") },
        { Icon(Icons.Outlined.Folder, contentDescription = "Projects") })
    object Calls : Screen("calls", "Calls",
        { Icon(Icons.Filled.Phone, contentDescription = "Calls") },
        { Icon(Icons.Outlined.Phone, contentDescription = "Calls") })
    object People : Screen("people", "People",
        { Icon(Icons.Filled.People, contentDescription = "People") },
        { Icon(Icons.Outlined.People, contentDescription = "People") })
    object Settings : Screen("settings", "Settings",
        { Icon(Icons.Filled.Settings, contentDescription = "Settings") },
        { Icon(Icons.Outlined.Settings, contentDescription = "Settings") })
}

/**
 * Primary destinations (max 5 per Material guidance). Settings is reached from
 * every top bar; Memory from Settings (and the wide rail).
 */
val bottomNavigationItems = listOf(
    Screen.Chat,
    Screen.Tasks,
    Screen.Inbox,
    Screen.Projects,
    Screen.Calls
)

/** The wide rail has room for People and Memory too. On phones People opens from Tasks and Calls. */
val railNavigationItems = bottomNavigationItems + Screen.People + Screen.Memory

/** Weekly review (from its notification or the banner on Tasks); not in the nav. */
const val REVIEW_ROUTE = "review"

/** Every route the previous layout's NavHost knows. */
private val PREVIOUS_ROUTES = (railNavigationItems + Screen.Settings).map { it.route }.toSet() + REVIEW_ROUTE

/**
 * The previous main screen (eight tabs, sessions list, Chat tab), kept behind
 * Settings → "Previous layout" as a safety net while the new shell settles.
 */
@Composable
private fun PreviousMainScreen(mainViewModel: MainViewModel) {
    val navController = rememberNavController()

    // Keyed on the value: a share arriving while the app is open
    // must navigate too, not only the first request.
    val pendingRoute by mainViewModel.navigateToRoute.collectAsState()
    LaunchedEffect(pendingRoute) {
        pendingRoute?.let { route ->
            // Routes of the new layout ("now", "about"…) have no destination here.
            if (route !in PREVIOUS_ROUTES) {
                mainViewModel.clearNavigation()
                return@LaunchedEffect
            }
            navController.navigate(route) {
                popUpTo(navController.graph.findStartDestination().id) {
                    saveState = true
                }
                launchSingleTop = true
                restoreState = true
            }
            mainViewModel.clearNavigation()
        }
    }

    AnimatedBackground {
        MainAppScreen(mainViewModel, navController)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainAppScreen(viewModel: MainViewModel, navController: androidx.navigation.NavHostController = rememberNavController()) {
    // Rail only from 720dp: on a ~650dp unfolded screen the bottom bar keeps both content panes roomy.
    val isWide = androidx.compose.ui.platform.LocalConfiguration.current.screenWidthDp >= 720
    val navBackStackEntry by navController.currentBackStackEntryAsState()
    val currentDestination = navBackStackEntry?.destination
    val serverStatus by viewModel.serverStatus.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }
    val inboxCounts by viewModel.notificationsController.counts.collectAsState()
    val inboxSuggestions by viewModel.notificationsController.suggestions.collectAsState()
    val inboxBadge = inboxCounts.needsYou + inboxSuggestions.size

    LaunchedEffect(viewModel) {
        viewModel.messages.collect { message ->
            snackbarHostState.currentSnackbarData?.dismiss()
            snackbarHostState.showSnackbar(message, withDismissAction = true)
        }
    }

    Scaffold(
        containerColor = Color.Transparent,
        snackbarHost = { SnackbarHost(snackbarHostState) { data -> Snackbar(data, shape = RoundedCornerShape(12.dp)) } },
        bottomBar = {
            if (!isWide) {
                AppBottomNavBar(
                    navController = navController,
                    currentDestination = currentDestination,
                    inboxBadge = inboxBadge
                )
            }
        }
    ) { innerPadding ->
        val density = LocalDensity.current
        val isKeyboardVisible = WindowInsets.ime.getBottom(density) > 0
        val bottomPadding = if (isKeyboardVisible) 0.dp else innerPadding.calculateBottomPadding()

        Row(
            modifier = Modifier
                .fillMaxSize()
                .padding(
                    bottom = bottomPadding
                )
        ) {
            if (isWide) {
                AppNavigationRail(
                    navController = navController,
                    currentDestination = currentDestination,
                    inboxBadge = inboxBadge
                )
            }

            var bannerShown by remember { mutableStateOf(false) }
            Column(modifier = Modifier.weight(1f)) {
            ServerStatusBanner(
                status = serverStatus,
                onRetry = { viewModel.recheckServer() },
                onOpenSettings = if (currentDestination?.route == Screen.Settings.route) null else {
                    { navController.navigateTopLevel(Screen.Settings.route) }
                },
                onVisibleChange = { bannerShown = it }
            )
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .then(if (bannerShown) Modifier.consumeWindowInsets(WindowInsets.statusBars) else Modifier)
            ) {
            val openSettings = remember(navController) { { navController.navigateTopLevel(Screen.Settings.route) } }
            androidx.compose.runtime.CompositionLocalProvider(
                app.ganam.assistant.ui.components.LocalOpenSettings provides
                    (if (isWide) null else openSettings)
            ) {
            NavHost(
                navController = navController,
                startDestination = Screen.Chat.route,
                modifier = Modifier.fillMaxSize()
            ) {
                composable(Screen.Chat.route) { app.ganam.assistant.ui.screens.chat.ChatWorkspace(viewModel) }
                composable(Screen.Tasks.route) { TasksScreen(viewModel) }
                composable(Screen.Inbox.route) { app.ganam.assistant.ui.screens.inbox.InboxScreen(viewModel) }
                composable(Screen.Memory.route) { MemoryScreen(viewModel) }
                composable(Screen.Projects.route) { ProjectsScreen(viewModel) }
                composable(Screen.Calls.route) { CallsScreen(viewModel) }
                composable(Screen.Settings.route) { SettingsScreen(viewModel, onBack = { navController.popBackStack() }) }
                composable(Screen.People.route) {
                    app.ganam.assistant.ui.screens.people.PeopleScreen(viewModel, onBack = if (isWide) null else ({ navController.popBackStack() }))
                }
                composable(REVIEW_ROUTE) {
                    app.ganam.assistant.ui.screens.review.ReviewScreen(viewModel, onBack = { navController.popBackStack() })
                }
            }
            }
            }
            }
        }
    }
}

@Composable
private fun AppBottomNavBar(
    navController: androidx.navigation.NavHostController,
    currentDestination: NavDestination?,
    inboxBadge: Int = 0
) {
    NavigationBar(
        containerColor = MaterialTheme.colorScheme.surfaceContainer,
        tonalElevation = 0.dp
    ) {
        bottomNavigationItems.forEach { screen ->
            val selected = currentDestination?.hierarchy?.any { it.route == screen.route } == true
            NavigationBarItem(
                selected = selected,
                onClick = { navController.navigateTopLevel(screen.route) },
                icon = { NavIcon(screen, selected, inboxBadge) },
                label = { Text(screen.label, maxLines = 1, style = androidx.compose.ui.text.TextStyle(fontFamily = app.ganam.assistant.ui.theme.GanamFontFamily, fontSize = 11.sp, fontWeight = FontWeight.Medium)) },
                colors = NavigationBarItemDefaults.colors(
                    selectedIconColor = MaterialTheme.colorScheme.onPrimaryContainer,
                    selectedTextColor = MaterialTheme.colorScheme.onSurface,
                    indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                    unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant
                )
            )
        }
    }
}

private fun androidx.navigation.NavHostController.navigateTopLevel(route: String) {
    navigate(route) {
        popUpTo(graph.findStartDestination().id) { saveState = true }
        launchSingleTop = true
        restoreState = true
    }
}

@Composable
private fun AppNavigationRail(
    navController: androidx.navigation.NavHostController,
    currentDestination: NavDestination?,
    inboxBadge: Int = 0
) {
    NavigationRail(
        containerColor = MaterialTheme.colorScheme.surfaceContainer,
        header = {
            Box(
                modifier = Modifier
                    .padding(top = 12.dp)
                    .size(40.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(MaterialTheme.colorScheme.primary),
                contentAlignment = Alignment.Center
            ) {
                Text("G", style = MaterialTheme.typography.headlineSmall, color = MaterialTheme.colorScheme.onPrimary)
            }
        },
        modifier = Modifier.fillMaxHeight()
    ) {
        Spacer(Modifier.weight(0.15f))
        (railNavigationItems + Screen.Settings).forEach { screen ->
            val selected = currentDestination?.hierarchy?.any { it.route == screen.route } == true
            NavigationRailItem(
                selected = selected,
                onClick = { navController.navigateTopLevel(screen.route) },
                icon = { NavIcon(screen, selected, inboxBadge) },
                label = { Text(screen.label, maxLines = 1, softWrap = false, style = androidx.compose.ui.text.TextStyle(fontFamily = app.ganam.assistant.ui.theme.GanamFontFamily, fontSize = 11.sp, fontWeight = FontWeight.Medium)) },
                colors = NavigationRailItemDefaults.colors(
                    selectedIconColor = MaterialTheme.colorScheme.onPrimaryContainer,
                    selectedTextColor = MaterialTheme.colorScheme.onSurface,
                    indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                    unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant
                )
            )
        }
    }
}

/** Destination icon; the Inbox carries a count of what needs you. */
@Composable
private fun NavIcon(screen: Screen, selected: Boolean, inboxBadge: Int) {
    if (screen == Screen.Inbox && inboxBadge > 0) {
        BadgedBox(badge = { Badge { Text(if (inboxBadge > 99) "99+" else inboxBadge.toString()) } }) {
            if (selected) screen.icon() else screen.iconIdle()
        }
    } else {
        if (selected) screen.icon() else screen.iconIdle()
    }
}
