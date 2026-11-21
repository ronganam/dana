package app.ganam.assistant.ui.viewmodels

import android.app.Application
import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import app.ganam.assistant.BuildConfig
import app.ganam.assistant.data.network.ApiService
import app.ganam.assistant.data.network.ServerStatus
import app.ganam.assistant.data.network.Reachability
import app.ganam.assistant.data.network.ServerStatusMonitor
import app.ganam.assistant.data.sync.TaskActionQueue
import app.ganam.assistant.data.update.AppVersionInfo
import app.ganam.assistant.data.update.UpdateManager
import app.ganam.assistant.data.wear.WearSyncManager
import app.ganam.assistant.data.wear.WearVersionInfo
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
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
    private val prefs: SharedPreferences = appContext.getSharedPreferences("GanamPrefs", Context.MODE_PRIVATE)

    private var serverUrl = prefs.getString("server_url", "https://") ?: "https://"
    private var apiToken = prefs.getString("api_token", "") ?: ""
    private var apiService = ApiService(serverUrl, apiToken)

    private val userMessages = UserMessages()
    private val ctx = ControllerContext(viewModelScope, userMessages) { apiService }

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
    /** The integrations the user has connected (`/api/me`), as they change: which places and More rows show. */
    val kinds: kotlinx.coroutines.flow.StateFlow<Set<String>> = app.ganam.assistant.data.network.Me.kinds(appContext)
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
            serverUrl = { serverUrl },
            apiToken = { apiToken },
            sessionId = { chatController.sessionId },
            onTranscript = { sender, text, isUser -> chatController.appendMessage(sender, text, isUser) },
            onStatusText = { chatController.setStatusText(it) }
        )
    }

    val chatController: ChatController = ChatController(
        ctx, appContext, prefs,
        serverUrl = { serverUrl },
        apiToken = { apiToken },
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
            app.ganam.assistant.data.sync.LiveRefresh.pings.collect {
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
        shoppingController.flush()
        shoppingController.fetchMe()
        callsController.fetchSuggestions()
        notificationsController.fetch()
        dayController.refreshIfStale()
        nowController.refresh()
        notificationOverviewController.fetch()
    }

    fun initialize(@Suppress("UNUSED_PARAMETER") context: Context) {
        ServerStatusMonitor.configure(appContext, serverUrl)
        if (!initialized) {
            initialized = true
            chatController.initialize()
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
        ServerStatusMonitor.configure(appContext, serverUrl)
        if (!initialized) {
            initialized = true
            chatController.initialize()
        }
    }

    fun getServerUrl(): String = serverUrl

    fun getApiToken(): String = apiToken

    fun updateServerConfig(newServerUrl: String, newApiToken: String) {
        serverUrl = newServerUrl
        apiToken = newApiToken
        apiService = ApiService(serverUrl, apiToken)
        prefs.edit()
            .putString("server_url", serverUrl)
            .putString("api_token", apiToken)
            .apply()
        ServerStatusMonitor.configure(appContext, serverUrl)
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

    private val _wearVersionInfo = MutableStateFlow<WearVersionInfo?>(null)
    val wearVersionInfo: StateFlow<WearVersionInfo?> = _wearVersionInfo.asStateFlow()

    private val _wearUpdateInfo = MutableStateFlow<AppVersionInfo?>(null)
    val wearUpdateInfo: StateFlow<AppVersionInfo?> = _wearUpdateInfo.asStateFlow()

    /** Check the server for a newer phone-app version (silent). */
    fun checkForAppUpdate() {
        viewModelScope.launch {
            try {
                val info = UpdateManager.checkForUpdate(serverUrl, "phone")
                _appUpdateInfo.value = info?.takeIf { UpdateManager.isNewer(it, BuildConfig.VERSION_CODE) }
                _wearUpdateInfo.value = UpdateManager.checkForUpdate(serverUrl, "wear")
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                android.util.Log.w("MainViewModel", "Update check failed: ${describeError(e)}")
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
            val eventsJson = ctx.load("WearSync") { filterEventsForWatch(apiService.getCalendarEvents()) } ?: "[]"
            val tasksJson = ctx.load("WearSync") { apiService.getTasksDueSoon() } ?: "[]"
            WearSyncManager.syncConfigToWear(appContext, serverUrl, apiToken, sessionId, eventsJson, tasksJson)
        }
    }

    private fun filterEventsForWatch(rawJson: String): String {
        val now = Date()
        val graceMs = 30 * 60 * 1000L
        return try {
            val array = JSONArray(rawJson)
            val filtered = JSONArray()
            for (i in 0 until array.length()) {
                val obj = array.getJSONObject(i)
                if (obj.optBoolean("all_day", false)) continue
                val endStr = obj.optString("end", "")
                if (endStr.isEmpty()) { filtered.put(obj); continue }
                val endMs = parseIsoTime(endStr)
                if (endMs != null && now.time > endMs + graceMs) continue
                filtered.put(obj)
            }
            filtered.toString()
        } catch (_: Exception) {
            rawJson
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
    val callSuggestions get() = callsController.suggestions
    val busySuggestionIds get() = callsController.busySuggestionIds
    val callLabelCatalog get() = callsController.labelCatalog
    val sessionContext get() = chatController.sessionContext
    fun fetchSuggestions() = callsController.fetchSuggestions()
    fun fetchLabelCatalog() = callsController.fetchLabelCatalog()
    fun acceptSuggestion(s: app.ganam.assistant.data.models.CallSuggestion, overrides: JSONObject = JSONObject()) =
        callsController.acceptSuggestion(s, overrides) { tasksController.fetchTasks(); notificationsController.fetch() }
    fun dismissSuggestion(s: app.ganam.assistant.data.models.CallSuggestion) = callsController.dismissSuggestion(s)
    fun updateCallLabels(callId: String, labelIds: List<String>) = callsController.updateCallLabels(callId, labelIds)

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
    fun openSearchResult(r: app.ganam.assistant.data.models.SearchResult) {
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
        super.onCleared()
        callController.endCall()
        chatController.release()
    }

    // ---------------------------------------------------------------------
    // Pass-throughs to the feature controllers.
    // ---------------------------------------------------------------------

    // Chat
    val currentSessionId get() = chatController.currentSessionId
    val chatMessages get() = chatController.chatMessages
    val sessions get() = chatController.sessions
    val pendingApproval get() = chatController.pendingApproval
    val selectionRequest get() = chatController.selectionRequest
    val pendingExtension get() = chatController.pendingExtension
    val isStreaming get() = chatController.isStreaming
    val isRecording get() = chatController.isRecording
    val chatStatusText get() = chatController.chatStatusText
    val pendingImageUri get() = chatController.pendingImageUri
    val chatModels get() = chatController.models
    val chatModel get() = chatController.chatModel
    fun setChatModel(id: String) = chatController.setChatModel(id)

    fun fetchSessions() = chatController.fetchSessions()
    fun startNewSession(@Suppress("UNUSED_PARAMETER") saveOld: Boolean = true) = chatController.startNewSession()
    fun loadSession(targetSessionId: String) = chatController.loadSession(targetSessionId)

    /**
     * Show conversation [sessionId] (an approval waiting in Now): the shared
     * thread is just opened; another session (a Telegram chat) is visited, with
     * "Back to the thread" on top.
     */
    fun showConversation(sessionId: String) {
        if (!(chatController.isThreadMode && sessionId == chatController.sessionId)) chatController.loadSession(sessionId)
    }
    /** Returns false when the message was not sent (server unreachable). */
    fun sendChatMessage(message: String): Boolean = chatController.sendChatMessage(message)
    fun stopAssistant() = chatController.stopAssistant()
    fun approveAction(approved: Boolean) = chatController.approveAction(approved)
    fun sendSelection(indices: List<Int>) = chatController.sendSelection(indices)
    fun cancelSelection() = chatController.cancelSelection()
    fun extendIterations() = chatController.extendIterations()
    fun declineIterations() = chatController.declineIterations()
    fun toggleToolCallCollapse(messageId: String) = chatController.toggleToolCallCollapse(messageId)
    fun setPendingImage(uri: Uri?) = chatController.setPendingImage(uri)
    fun clearPendingImage() = chatController.clearPendingImage()
    val draftText get() = chatController.draftText
    fun consumeDraft() = chatController.consumeDraft()
    fun startChatFromShare(text: String?, imageUri: Uri?) {
        chatController.startChatFromShare(text, imageUri)
        navigateTo("chat")
    }
    fun startVoiceRecording() = chatController.startVoiceRecording()
    fun stopVoiceRecording() = chatController.stopVoiceRecording()

    // Realtime call
    val selectedRealtimeModel get() = callController.selectedRealtimeModel
    val isCallActive get() = callController.isCallActive
    val isCallMuted get() = callController.isCallMuted
    val isSpeakerOn get() = callController.isSpeakerOn
    val callStatus get() = callController.callStatus
    val currentLiveTranscript get() = callController.currentLiveTranscript
    val activeCallToolName get() = callController.activeCallToolName

    fun setRealtimeModel(model: String) = callController.setRealtimeModel(model)
    fun updateRealtimeModel(model: String) = callController.setRealtimeModel(model)
    fun toggleCallMute() = callController.toggleCallMute()
    fun toggleSpeaker() = callController.toggleSpeaker()
    fun startRealtimeCall() {
        chatController.stopVoiceRecording()
        callController.startCall()
    }
    fun endRealtimeCall() = callController.endCall()

    // Tasks
    val tasks get() = tasksController.tasks
    val isLoadingTasks get() = tasksController.isLoadingTasks
    val taskGroups get() = tasksController.taskGroups
    val isLoadingGroups get() = tasksController.isLoadingGroups
    val selectedGroupFilter get() = tasksController.selectedGroupFilter
    val sortMode get() = tasksController.sortMode
    val selectedTriggerFilter get() = tasksController.selectedTriggerFilter
    val zones get() = tasksController.zones

    fun fetchTasks() = tasksController.fetchTasks()
    fun fetchZones() = tasksController.fetchZones()
    fun createTask(title: String, description: String?, priority: String, dueDate: String?, triggers: JSONObject?, groupId: String? = null) =
        tasksController.createTask(title, description, priority, dueDate, triggers, groupId)
    fun updateTask(taskId: String, title: String, description: String?, priority: String, dueDate: String?, status: String, triggers: JSONObject?, groupId: String? = null) =
        tasksController.updateTask(taskId, title, description, priority, dueDate, status, triggers, groupId)
    fun updateTaskStatus(taskId: String, newStatus: String) = tasksController.updateTaskStatus(taskId, newStatus)
    fun deleteTask(taskId: String) = tasksController.deleteTask(taskId)
    fun fetchTaskGroups() = tasksController.fetchTaskGroups()
    fun createTaskGroup(name: String, emoji: String, color: String, description: String?) =
        tasksController.createTaskGroup(name, emoji, color, description)
    fun updateTaskGroup(id: String, name: String, emoji: String, color: String, description: String?) =
        tasksController.updateTaskGroup(id, name, emoji, color, description)
    fun deleteTaskGroup(id: String) = tasksController.deleteTaskGroup(id)
    fun setGroupFilter(groupId: String?) = tasksController.setGroupFilter(groupId)
    fun setSortMode(mode: SortMode) = tasksController.setSortMode(mode)
    fun setTriggerFilter(triggerType: String?) = tasksController.setTriggerFilter(triggerType)

    // Memories
    val memories get() = memoryController.memories
    val isLoadingMemories get() = memoryController.isLoadingMemories

    fun fetchMemories() = memoryController.fetchMemories()
    fun createMemory(key: String, value: String, category: String) = memoryController.createMemory(key, value, category)
    fun updateMemory(oldKey: String, newKey: String, value: String, category: String) =
        memoryController.updateMemory(oldKey, newKey, value, category)
    fun deleteMemory(key: String) = memoryController.deleteMemory(key)

    // Call recordings
    val calls get() = callsController.calls
    val isLoadingCalls get() = callsController.isLoadingCalls
    val isScanningRecordings get() = callsController.isScanningRecordings
    val recordingSelectionList get() = callsController.recordingSelectionList
    val uploadFormState get() = callsController.uploadFormState
    val sharedAudioUri get() = callsController.sharedAudioUri

    fun fetchCalls() = callsController.fetchCalls()
    fun deleteCall(callId: String) = callsController.deleteCall(callId)
    fun reanalyzeCall(callId: String, customInstructions: String?) = callsController.reanalyzeCall(callId, customInstructions)
    fun startRecordingSelection() = callsController.startRecordingSelection()
    fun clearRecordingSelection() = callsController.clearRecordingSelection()
    fun showUploadForm(uri: Uri, displayName: String, context: Context) = callsController.showUploadForm(uri, displayName, context)
    fun showUploadFormForRecording(pending: PendingRecordingFile) = callsController.showUploadFormForRecording(pending)
    fun dismissUploadForm() = callsController.dismissUploadForm()
    fun updateUploadField(caller: String? = null, receiver: String? = null, timestamp: String? = null, instructions: String? = null) =
        callsController.updateUploadField(caller, receiver, timestamp, instructions)
    fun submitUploadForm(context: Context) = callsController.submitUploadForm(context)
    fun setSharedAudioUri(uri: Uri?) = callsController.setSharedAudioUri(uri)
    fun clearSharedAudioUri() = callsController.clearSharedAudioUri()

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
