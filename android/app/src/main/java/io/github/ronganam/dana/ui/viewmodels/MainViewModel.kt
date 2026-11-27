package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.network.ServerConfig
import io.github.ronganam.dana.data.pairing.DevicePairing
import io.github.ronganam.dana.data.pairing.PairingClient
import android.app.Application
import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.ronganam.dana.BuildConfig
import io.github.ronganam.dana.data.network.ServerStatus
import io.github.ronganam.dana.data.network.Reachability
import io.github.ronganam.dana.data.network.ServerStatusMonitor
import io.github.ronganam.dana.data.sync.TaskActionQueue
import io.github.ronganam.dana.data.update.AppVersionInfo
import io.github.ronganam.dana.data.update.UpdateManager
import io.github.ronganam.dana.data.wear.WearSyncManager
import io.github.ronganam.dana.protocol.WearVersion
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Composition root for the phone UI.
 *
 * Each feature lives in its own controller ([ChatController],
 * [TasksController], [MemoryController], [ProjectsController],
 * [CallsController], [RealtimeCallController]). This class owns server
 * config, app updates and Wear sync. It also re-exposes the controllers'
 * state and actions, so screens keep using `viewModel.x` without knowing
 * which controller owns `x`.
 */
class MainViewModel(application: Application) : AndroidViewModel(application) {

    private val appContext: Context = application.applicationContext
    private val prefs: SharedPreferences = ServerConfig.prefs(appContext)

    /** The server and device token every controller talks to (pairing changes it: [useServerConfig]). */
    var serverConfig: ServerConfig = ServerConfig.load(appContext)
        private set
    private var apiService = serverConfig.api()

    private val _paired = MutableStateFlow(serverConfig.isConfigured)
    /** Whether this app is paired with a server (else the phone shows the pairing screen). */
    val paired: StateFlow<Boolean> = _paired.asStateFlow()

    private val userMessages = UserMessages()
    private val pairingClient = PairingClient()
    private val ctx = ControllerContext(viewModelScope, userMessages, Texts.of(appContext)) { apiService }

    /** The pairing screen and Settings' Unpair / Pair again (kind `phone`). */
    val pairingController = PairingController(
        viewModelScope,
        current = { serverConfig },
        device = { DevicePairing.device(appContext) },
        pair = { server, code -> pairingClient.pair(server, code, kind = "phone", name = DevicePairing.deviceName()) },
        store = { server, paired ->
            // The shell starts (MainActivity: initialize) once the pairing screen closes.
            DevicePairing.store(appContext, server, paired).also {
                io.github.ronganam.dana.push.PushSetup.ensureRegistered(appContext, force = true)
            }
        },
        remove = { config, id -> pairingClient.remove(config, id) },
        forget = { DevicePairing.unpair(appContext, pairingClient) },
        reload = { useServerConfig(ServerConfig.load(appContext)) },
        notify = { userMessages.post(it) },
    )

    /** One-shot error/info messages for a Snackbar. */
    val messages: SharedFlow<String> = userMessages.messages

    /** Messages with an Undo action (the shell shows them as a Snackbar with "Undo"). */
    val undoableMessages: SharedFlow<UndoableMessage> = userMessages.undoable

    /** Whether the assistant server is reachable (VPN on, server up). */
    val serverStatus: StateFlow<ServerStatus> = ServerStatusMonitor.status

    val tasksController = TasksController(ctx, appContext)
    val memoryController = MemoryController(ctx)
    val projectsController = ProjectsController(ctx, appContext)
    val callsController = CallsController(ctx)
    val peopleController = PeopleController(ctx)
    val reviewController = ReviewController(ctx)
    val aboutController = AboutController(ctx)
    val dayController = DayController(ctx)
    /** The Shopping screen (the list itself is shared with the widget and the watch: `ShoppingSync`). */
    val shoppingController = ShoppingController(ctx, appContext)
    val moneyController = MoneyController(ctx, appContext)
    /** The Watching place: watches, their checks and runs, a new watch. */
    val watchingController = WatchingController(ctx)
    /** Settings → Signals: where each signal comes from, the wake window, the phone sources. */
    val signalsController = SignalsController(ctx, appContext)
    /** The addons the user has connected (`/api/me`), as they change: which places and More rows show. */
    val addons: kotlinx.coroutines.flow.StateFlow<Set<String>> = io.github.ronganam.dana.data.network.Me.addons(appContext)
    /** Navigation state of the new main screen (places, picks, thread sheet, on-screen context). */
    val shellController: ShellController = ShellController(
        titleFor = { kind, id -> contextTitle(kind, id) },
        onThreadOpened = { chatController.resyncIfIdle() }
    )
    val notificationsController = NotificationsController(ctx, appContext) {
        tasksController.fetchTasks()
        callsController.fetchSuggestions()
    }
    /** The Notifications place (apps, noise, rules). */
    val notificationOverviewController = NotificationOverviewController(ctx, appContext, notificationsController) {
        nowController.refresh()
        notificationsController.fetch()
    }
    /** The Now queue. */
    val nowController: NowController = NowController(ctx, appContext) { kind ->
        when (kind) {
            "notification" -> { notificationOverviewController.fetch(); notificationsController.fetch() }
            "reminder" -> tasksController.fetchTasks()
            else -> {
                tasksController.fetchTasks()
                callsController.fetchSuggestions()
                notificationsController.fetch()
                notificationOverviewController.fetch()
            }
        }
    }
    /** Search from the ask bar. */
    val searchController = SearchController(ctx)

    // The chat and call controllers refer to each other through callbacks, so
    // the call controller is declared lazily to break the cycle.
    val callController: RealtimeCallController by lazy {
        RealtimeCallController(
            ctx, appContext, prefs,
            serverUrl = { serverConfig.url },
            apiToken = { serverConfig.token },
            sessionId = { chatController.sessionId },
            onTranscript = { sender, text, isUser -> chatController.appendMessage(sender, text, isUser) },
            onStatusText = { chatController.setStatusText(it) }
        )
    }

    val chatController: ChatController = ChatController(
        ctx, appContext, prefs,
        serverUrl = { serverConfig.url },
        apiToken = { serverConfig.token },
        onSessionChanged = { syncToWear() },
        endRealtimeCall = { callController.endCall() },
        device = when {
            BuildConfig.TV -> "tv"
            BuildConfig.KIOSK -> "tablet"
            else -> "phone"
        },
        onThingsChanged = { refreshAfterDana() }
    ).also { chat ->
        // What is open on the main screen goes with each message.
        chat.screenProvider = { shellController.screen.value }
    }

    /** Dana acted (a run with tools, an Undo): refresh what the screens show. */
    private fun refreshAfterDana() {
        if (!initialized) return
        viewModelScope.launch {
            // Give the server a moment to settle (calendar caches, triage).
            kotlinx.coroutines.delay(400)
            nowController.refresh()
            tasksController.fetchTasks()
            callsController.fetchSuggestions()
            notificationOverviewController.fetch()
            shellController.notifApp.value?.id?.let { notificationOverviewController.fetchApp(it) }
            dayController.refreshIfStale()
        }
    }

    /** A readable name for an item pinned to the thread without one. */
    private fun contextTitle(kind: String, id: String): String = when (kind) {
        "task" -> tasksController.tasks.value.firstOrNull { it.id == id }?.title
        "call" -> callsController.calls.value.firstOrNull { it.id == id }?.let { it.title ?: it.caller }
        "person" -> peopleController.people.value.firstOrNull { it.id == id }?.name
        "suggestion" -> callsController.suggestions.value.firstOrNull { it.id == id }?.title
            ?: nowController.payload.value?.items?.firstOrNull { it.id == "sug:$id" }?.title
        "notification" -> nowController.payload.value?.items?.firstOrNull { it.id == "notif:$id" }?.title
        else -> null
    }.orEmpty()

    private var initialized = false
    /** A conversation asked for before the chat started (a live activity tapped on a cold start). */
    private var pendingConversation: String? = null

    init {
        // Send reminder actions queued while offline as soon as the server
        // answers again (the WorkManager retry is the fallback).
        viewModelScope.launch {
            var wasOnline = false
            ServerStatusMonitor.status.collect { status ->
                val online = status.reachability == Reachability.ONLINE
                if (online && !wasOnline) {
                    TaskActionQueue.flushAsync(appContext)
                    shoppingController.flush()
                    io.github.ronganam.dana.data.signals.SignalSync.flushAsync(appContext)
                }
                // Back online: what the screens show may be stale.
                if (online && !wasOnline && initialized) {
                    nowController.refresh()
                    notificationOverviewController.fetch()
                    shoppingController.restartLive()
                }
                wasOnline = online
            }
        }
        // A push event or a notification batch changed the server's state.
        viewModelScope.launch {
            var pending: kotlinx.coroutines.Job? = null
            io.github.ronganam.dana.data.sync.LiveRefresh.pings.collect {
                if (!initialized) return@collect
                pending?.cancel()
                pending = launch {
                    kotlinx.coroutines.delay(1_500)
                    nowController.refresh()
                    notificationOverviewController.fetch()
                }
            }
        }
    }

    /** The app came back to the foreground. */
    fun onAppForeground() {
        if (!initialized) return
        // A reply may have finished while we were in the background.
        chatController.resyncIfIdle()
        TaskActionQueue.flushAsync(appContext)
        io.github.ronganam.dana.data.signals.SignalSync.flushAsync(appContext)
        io.github.ronganam.dana.data.signals.PhoneSignals.onForeground(appContext)
        io.github.ronganam.dana.data.sync.RecordingScans.now(appContext)
        shoppingController.flush()
        shoppingController.fetchMe()
        callsController.fetchSuggestions()
        notificationsController.fetch()
        dayController.refreshIfStale()
        nowController.refresh()
        notificationOverviewController.fetch()
    }

    fun initialize(@Suppress("UNUSED_PARAMETER") context: Context) {
        ServerStatusMonitor.configure(appContext, serverConfig.url)
        if (!initialized) {
            initialized = true
            chatController.initialize()
            pendingConversation?.let { pendingConversation = null; showConversation(it) }
        } else {
            chatController.fetchSessions()
        }
        tasksController.fetchTasks()
        peopleController.fetch()
        tasksController.fetchZones()
        callsController.fetchCalls()
        callsController.fetchSuggestions()
        callsController.fetchLabelCatalog()
        notificationsController.fetch()
        dayController.fetch()
        nowController.refresh()
        notificationOverviewController.fetch()
        shoppingController.fetchMe()
        shoppingController.flush()
        syncToWear()
        checkForAppUpdate()
        fetchWearVersion()
    }

    /** Kiosk: only the assistant chat — no tasks, calls, wear sync or update checks. */
    fun initializeChatOnly() {
        ServerStatusMonitor.configure(appContext, serverConfig.url)
        if (!initialized) {
            initialized = true
            chatController.initialize()
        }
    }

    /** Point every controller at [config] (already saved: [ServerConfig]). */
    fun useServerConfig(config: ServerConfig) {
        serverConfig = config
        _paired.value = config.isConfigured
        apiService = serverConfig.api()
        ServerStatusMonitor.configure(appContext, serverConfig.url)
        shoppingController.accountChanged()
        moneyController.forget()
        projectsController.forget()
        syncToWear()
    }

    /** Re-check the server now (the banner's Retry button). */
    fun recheckServer() {
        ServerStatusMonitor.checkNow()
    }

    // --- App updates ---

    private val _appUpdateInfo = MutableStateFlow<AppVersionInfo?>(null)
    val appUpdateInfo: StateFlow<AppVersionInfo?> = _appUpdateInfo.asStateFlow()

    private val _updateCheckDone = MutableStateFlow(false)
    val updateCheckDone: StateFlow<Boolean> = _updateCheckDone.asStateFlow()

    private val _wearVersionInfo = MutableStateFlow<WearVersion?>(null)
    val wearVersionInfo: StateFlow<WearVersion?> = _wearVersionInfo.asStateFlow()

    private val _wearUpdateInfo = MutableStateFlow<AppVersionInfo?>(null)
    val wearUpdateInfo: StateFlow<AppVersionInfo?> = _wearUpdateInfo.asStateFlow()

    private val _updateCheckFailed = MutableStateFlow(false)
    /** True when the last check could not read the server's version list. */
    val updateCheckFailed: StateFlow<Boolean> = _updateCheckFailed.asStateFlow()

    /** Check the server for a newer phone-app version; Settings shows "Checking…" until it ends. */
    fun checkForAppUpdate() {
        _updateCheckDone.value = false
        viewModelScope.launch {
            try {
                val info = UpdateManager.checkForUpdate(serverConfig.url, "phone")
                _updateCheckFailed.value = info == null
                _appUpdateInfo.value = info?.takeIf { UpdateManager.isNewer(it, BuildConfig.VERSION_CODE) }
                _wearUpdateInfo.value = UpdateManager.checkForUpdate(serverConfig.url, "wear")
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                android.util.Log.w("MainViewModel", "Update check failed: $e")
                _updateCheckFailed.value = true
            } finally {
                _updateCheckDone.value = true
            }
        }
    }

    /** Read the watch's reported app version over the Wear Data Layer. */
    fun fetchWearVersion() {
        viewModelScope.launch {
            _wearVersionInfo.value = WearSyncManager.getWearVersion(appContext)
        }
    }

    // --- Wear sync ---

    private fun syncToWear() {
        val sessionId = chatController.sessionId
        viewModelScope.launch {
            val eventsJson = ctx.load("WearSync") { io.github.ronganam.dana.data.wear.WatchJson.events(eventsForWatch(apiService.tasks.calendarEvents())) } ?: "[]"
            val tasksJson = ctx.load("WearSync") { io.github.ronganam.dana.data.wear.WatchJson.tasks(apiService.tasks.tasksDueSoon()) } ?: "[]"
            WearSyncManager.syncConfigToWear(appContext, serverConfig.url, sessionId, eventsJson, tasksJson)
        }
    }

    /** Timed events that haven't ended more than half an hour ago. */
    private fun eventsForWatch(events: List<io.github.ronganam.dana.data.models.CalendarEvent>): List<io.github.ronganam.dana.data.models.CalendarEvent> {
        val now = Date()
        val graceMs = 30 * 60 * 1000L
        return events.filter { e ->
            if (e.allDay) return@filter false
            val endStr = e.end.orEmpty()
            if (endStr.isEmpty()) return@filter true
            val endMs = parseIsoTime(endStr)
            !(endMs != null && now.time > endMs + graceMs)
        }
    }

    private fun parseIsoTime(s: String): Long? {
        for (fmt in ISO_FORMATS) {
            try {
                val sdf = SimpleDateFormat(fmt, Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }
                return sdf.parse(s)?.time ?: continue
            } catch (_: java.text.ParseException) {
                // Not this format: try the next one.
            }
        }
        return null
    }

    // --- Navigation ---

    private val _navigateToRoute = MutableStateFlow<String?>(null)
    val navigateToRoute: StateFlow<String?> = _navigateToRoute.asStateFlow()

    // --- Call suggestions & context chats ---
    fun acceptSuggestion(s: io.github.ronganam.dana.data.models.CallSuggestion, overrides: io.github.ronganam.dana.data.network.SuggestionEdits = io.github.ronganam.dana.data.network.SuggestionEdits.None) =
        callsController.acceptSuggestion(s, overrides) { tasksController.fetchTasks(); notificationsController.fetch() }

    /** "Ask Dana about this": the thread opens with the item pinned as on-screen context. */
    fun openContextChat(kind: String, id: String, title: String = "") {
        chatController.returnToThread()
        shellController.openThread(ThreadContext(kind, id, title))
    }

    /** A call the Calls screen should select when it next shows (from the Debrief / a task link). */
    private val _focusCallId = MutableStateFlow<String?>(null)
    val focusCallId: StateFlow<String?> = _focusCallId.asStateFlow()
    /** Open a person's page (from a caller card, widget or notification). */
    fun openPerson(personId: String) {
        peopleController.select(personId)
        navigateTo("people")
    }

    fun openReview() {
        reviewController.fetch()
        navigateTo("review")
    }

    fun openCall(callId: String) {
        _focusCallId.value = callId
        callsController.fetchCalls()
        navigateTo("calls")
    }
    fun consumeFocusCall() { _focusCallId.value = null }

    /**
     * A search result was tapped: open it where it lives.
     */
    fun openSearchResult(r: io.github.ronganam.dana.data.models.SearchResult) {
        when (r.kind) {
            "reminder" -> shellController.openReminder(r.id)
            "call" -> openCall(r.id)
            "person" -> openPerson(r.id)
            "note" -> {
                if (r.id.startsWith("project:")) projectsController.focus(r.projectId ?: r.id.removePrefix("project:"), null, null)
                else projectsController.focus(r.projectId, r.subtitle, r.id)
                navigateTo("projects")
            }
            "notification" -> r.packageName?.let { shellController.openNotifApp(it) }
                ?: userMessages.post("That notification is gone")
            "shopping" -> navigateTo("shopping")
            else -> userMessages.post("Can't open that here")
        }
    }

    fun navigateTo(route: String) {
        _navigateToRoute.value = route
    }

    fun clearNavigation() {
        _navigateToRoute.value = null
    }

    override fun onCleared() {
        callController.endCall()
        chatController.release()
    }

    // ---------------------------------------------------------------------
    // Pass-throughs to the feature controllers.
    // ---------------------------------------------------------------------

    // Chat

    fun startNewSession(@Suppress("UNUSED_PARAMETER") saveOld: Boolean = true) = chatController.startNewSession()

    /**
     * Show conversation [sessionId] (an approval waiting in Now): the shared
     * thread is just opened; another session (a Telegram chat) is visited, with
     * "Back to the thread" on top.
     */
    fun showConversation(sessionId: String) {
        if (!initialized) {
            pendingConversation = sessionId
            return
        }
        if (!(chatController.isThreadMode && sessionId == chatController.sessionId)) chatController.loadSession(sessionId)
    }
    /** Returns false when the message was not sent (server unreachable). */
    fun startChatFromShare(text: String?, imageUri: Uri?) {
        chatController.startChatFromShare(text, imageUri)
        navigateTo("chat")
    }

    // Realtime call

    fun startRealtimeCall() {
        chatController.stopVoiceRecording()
        callController.startCall()
    }

    // Tasks


    // Memories


    // Call recordings


    private companion object {
        val ISO_FORMATS = listOf(
            "yyyy-MM-dd'T'HH:mm:ss'Z'",
            "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'",
            "yyyy-MM-dd'T'HH:mm:ssZ",
            "yyyy-MM-dd'T'HH:mm:ss.SSSZ",
            "yyyy-MM-dd'T'HH:mm:ssXXX",
            "yyyy-MM-dd'T'HH:mm:ss.SSSXXX"
        )
    }
}
