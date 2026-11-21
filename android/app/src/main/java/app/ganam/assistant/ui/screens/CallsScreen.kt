package app.ganam.assistant.ui.screens

import android.content.Context
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.People
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import app.ganam.assistant.ui.adaptive.Pane
import app.ganam.assistant.ui.adaptive.PaneRow
import app.ganam.assistant.ui.adaptive.asidePaneWidth
import app.ganam.assistant.ui.adaptive.sidePaneWidth
import app.ganam.assistant.ui.adaptive.WindowTier
import app.ganam.assistant.ui.adaptive.windowTier
import app.ganam.assistant.ui.components.ScreenTopBar
import app.ganam.assistant.ui.screens.calls.*
import app.ganam.assistant.ui.viewmodels.MainViewModel

/** Minimum pane area for the 3-pane layout: list 264 + detail ≥ 340 + actions 280. */
private val THREE_PANE_MIN_WIDTH = 900.dp

/**
 * Calls — adaptive:
 *  - COMPACT: day-grouped list; tapping a call drills into the detail (system back returns).
 *  - MEDIUM: list | detail (player lives in the detail).
 *  - EXPANDED with room: list | detail | actions (playback, follow-ups, facts).
 * Selection, search, drill-down and dialogs are saveable, so folding/unfolding keeps your place.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CallsScreen(viewModel: MainViewModel) {
    val context = LocalContext.current
    val tier = windowTier()
    val calls by viewModel.calls.collectAsState()
    val isLoading by viewModel.isLoadingCalls.collectAsState()
    val isScanning by viewModel.isScanningRecordings.collectAsState()

    var searchQuery by rememberSaveable { mutableStateOf("") }
    var selectedCallId by rememberSaveable { mutableStateOf<String?>(null) }
    var detailOpen by rememberSaveable { mutableStateOf(false) }
    var reanalyzeTargetId by rememberSaveable { mutableStateOf<String?>(null) }
    var deleteTargetId by rememberSaveable { mutableStateOf<String?>(null) }
    var showUploadOptions by rememberSaveable { mutableStateOf(false) }

    val prefs = remember { context.getSharedPreferences("GanamPrefs", Context.MODE_PRIVATE) }
    val serverUrl = remember { prefs.getString("server_url", "") ?: "" }
    val apiToken = remember { prefs.getString("api_token", "") ?: "" }
    val audioPlayer = remember(serverUrl, apiToken) { CallAudioPlayer(context, serverUrl, apiToken) }
    DisposableEffect(audioPlayer) { onDispose { audioPlayer.stop() } }

    LaunchedEffect(Unit) { viewModel.fetchCalls() }

    val uploadFormState by viewModel.uploadFormState.collectAsState()
    val sharedAudioUri by viewModel.sharedAudioUri.collectAsState()
    val recordingSelectionList by viewModel.recordingSelectionList.collectAsState()

    val audioPickerLauncher = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri: Uri? ->
        uri?.let { viewModel.showUploadForm(it, getDisplayName(context, it), context) }
    }
    LaunchedEffect(sharedAudioUri) {
        sharedAudioUri?.let { uri ->
            viewModel.showUploadForm(uri, getDisplayName(context, uri), context)
            viewModel.clearSharedAudioUri()
        }
    }

    // ---- Sheets (unchanged behaviour) ----
    if (showUploadOptions) {
        ModalBottomSheet(
            onDismissRequest = { showUploadOptions = false },
            containerColor = MaterialTheme.colorScheme.surface,
            shape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp)
        ) {
            UploadOptionsSheet(
                onPickFile = { showUploadOptions = false; audioPickerLauncher.launch("audio/*") },
                onSelectRecording = { showUploadOptions = false; viewModel.startRecordingSelection() }
            )
        }
    }
    recordingSelectionList?.let { recordings ->
        ModalBottomSheet(
            onDismissRequest = { viewModel.clearRecordingSelection() },
            containerColor = MaterialTheme.colorScheme.surface,
            shape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp)
        ) {
            RecordingSelectionSheet(
                recordings = recordings,
                isScanning = isScanning,
                onSelect = { pending ->
                    viewModel.showUploadFormForRecording(pending)
                    viewModel.clearRecordingSelection()
                }
            )
        }
    }
    uploadFormState?.let { formState ->
        ModalBottomSheet(
            onDismissRequest = { viewModel.dismissUploadForm() },
            containerColor = MaterialTheme.colorScheme.surface,
            shape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp)
        ) {
            UploadFormSheet(
                formState = formState,
                onCallerChange = { viewModel.updateUploadField(caller = it) },
                onReceiverChange = { viewModel.updateUploadField(receiver = it) },
                onTimestampChange = { viewModel.updateUploadField(timestamp = it) },
                onInstructionsChange = { viewModel.updateUploadField(instructions = it) },
                onSubmit = { viewModel.submitUploadForm(context) },
                onDismiss = { viewModel.dismissUploadForm() }
            )
        }
    }

    // ---- Data ----
    val suggestions by viewModel.callSuggestions.collectAsState()
    val busySuggestions by viewModel.busySuggestionIds.collectAsState()
    val labelCatalog by viewModel.callLabelCatalog.collectAsState()
    var labelFilter by rememberSaveable { mutableStateOf<String?>(null) }
    var showQuick by rememberSaveable { mutableStateOf(false) }
    var labelEditId by rememberSaveable { mutableStateOf<String?>(null) }

    // Opened from the Debrief / a task's call link: select that call.
    val focusCallId by viewModel.focusCallId.collectAsState()
    LaunchedEffect(focusCallId, calls) {
        val id = focusCallId ?: return@LaunchedEffect
        if (calls.any { it.id == id }) {
            searchQuery = ""; labelFilter = null; showQuick = true
            selectedCallId = id
            detailOpen = true
            viewModel.consumeFocusCall()
        }
    }

    // Labels actually used, most common first (the filter bar).
    val usedLabels = remember(calls) {
        calls.flatMap { it.labels }.groupingBy { it.id }.eachCount().entries
            .sortedByDescending { it.value }
            .mapNotNull { e -> calls.firstNotNullOfOrNull { c -> c.labels.firstOrNull { it.id == e.key } } }
    }
    val hiddenQuick = remember(calls) { calls.count { it.importance == "low" } }
    val filteredCalls = remember(calls, searchQuery, labelFilter, showQuick) {
        val base = calls
            .filter { showQuick || it.importance != "low" || it.pendingSuggestions > 0 }
            .filter { c -> labelFilter == null || c.labels.any { it.id == labelFilter } }
        if (searchQuery.isBlank()) base
        else base.filter { c ->
            c.caller.contains(searchQuery, true) || c.receiver.contains(searchQuery, true) ||
                (c.title ?: "").contains(searchQuery, true) ||
                (c.summary ?: "").contains(searchQuery, true) ||
                (c.transcript ?: "").contains(searchQuery, true)
        }
    }
    val days = remember(filteredCalls) { groupCallsByDay(filteredCalls) }
    val orderedIds = remember(days) { days.flatMap { d -> d.calls.map { it.id } } }
    val todayCount = remember(calls) { countCallsToday(calls) }

    // Multi-pane keeps a call selected (newest first); compact only drops a selection that vanished.
    LaunchedEffect(orderedIds, tier) {
        if (selectedCallId != null && selectedCallId !in orderedIds) {
            selectedCallId = if (tier == WindowTier.COMPACT) null else orderedIds.firstOrNull()
        } else if (selectedCallId == null && tier != WindowTier.COMPACT) {
            selectedCallId = orderedIds.firstOrNull()
        }
    }

    val selectedCall = calls.find { it.id == selectedCallId }
    val compactDetail = tier == WindowTier.COMPACT && detailOpen && selectedCall != null

    // What Dana sees when asked from here: the call whose detail is on screen.
    val viewing = if (tier == WindowTier.COMPACT && !compactDetail) null else selectedCall?.id
    LaunchedEffect(viewing) { viewModel.callsController.setViewing(viewing) }
    DisposableEffect(Unit) { onDispose { viewModel.callsController.setViewing(null) } }

    BackHandler(enabled = compactDetail) {
        audioPlayer.stop()
        detailOpen = false
    }

    val select: (app.ganam.assistant.data.models.PhoneCall) -> Unit = { call ->
        if (call.id != selectedCallId) audioPlayer.stop()
        selectedCallId = call.id
        if (tier == WindowTier.COMPACT) detailOpen = true
    }

    Scaffold(
        containerColor = Color.Transparent,
        floatingActionButton = {
            if (!compactDetail && tier == WindowTier.COMPACT) {
                FloatingActionButton(
                    onClick = { showUploadOptions = true },
                    containerColor = MaterialTheme.colorScheme.primary,
                    contentColor = MaterialTheme.colorScheme.onPrimary,
                    shape = RoundedCornerShape(16.dp)
                ) { Icon(Icons.Filled.Add, contentDescription = "Upload audio") }
            }
        },
        topBar = {
            if (compactDetail && selectedCall != null) {
                ScreenTopBar(
                    title = selectedCall.contactName,
                    kicker = "Call",
                    navigationIcon = {
                        IconButton(onClick = { audioPlayer.stop(); detailOpen = false }) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back to calls")
                        }
                    }
                )
            } else {
                ScreenTopBar(
                    title = "Calls",
                    kicker = if (calls.isEmpty()) null
                    else "${calls.size} recorded" + if (todayCount > 0) " · $todayCount today" else "",
                    actions = {
                        if (tier != WindowTier.COMPACT) {
                            TextButton(onClick = { showUploadOptions = true }) {
                                Icon(Icons.Filled.Add, contentDescription = null, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.width(6.dp))
                                Text("Add recording")
                            }
                        }
                        IconButton(onClick = { viewModel.navigateTo("people") }) {
                            Icon(Icons.Filled.People, contentDescription = "People")
                        }
                        IconButton(onClick = { viewModel.fetchCalls() }) {
                            Icon(Icons.Filled.Refresh, contentDescription = "Refresh calls")
                        }
                    }
                )
            }
        }
    ) { innerPadding ->
        BoxWithConstraints(Modifier.padding(innerPadding).fillMaxSize()) {
            val threePane = tier == WindowTier.EXPANDED && maxWidth >= THREE_PANE_MIN_WIDTH
            val listPane: @Composable () -> Unit = {
                CallListPane(
                    days = days,
                    selectedId = if (tier == WindowTier.COMPACT) null else selectedCallId,
                    playingId = audioPlayer.currentPlayingCallId.value,
                    query = searchQuery,
                    onQueryChange = { searchQuery = it },
                    isLoading = isLoading,
                    hasAnyCalls = calls.isNotEmpty(),
                    onSelect = select,
                    onUpload = { showUploadOptions = true },
                    labels = usedLabels,
                    labelFilter = labelFilter,
                    onLabelFilter = { labelFilter = it },
                    hiddenQuick = hiddenQuick,
                    showQuick = showQuick,
                    onToggleQuick = { showQuick = !showQuick }
                )
            }
            val detail: @Composable (Boolean) -> Unit = { withInlinePlayer ->
                val call = selectedCall
                if (call == null) PickACall()
                else CallDetailPane(
                    call = call,
                    player = audioPlayer,
                    showPlayer = withInlinePlayer,
                    showKeyPoints = withInlinePlayer,
                    onReanalyze = { reanalyzeTargetId = call.id },
                    onDelete = { deleteTargetId = call.id },
                    suggestions = suggestions.filter { it.callId == call.id },
                    busySuggestions = busySuggestions,
                    onAccept = { viewModel.acceptSuggestion(it) },
                    onDismiss = { viewModel.dismissSuggestion(it) },
                    onAskSuggestion = { viewModel.openContextChat("suggestion", it.id) },
                    onEditLabels = { labelEditId = call.id },
                    onOpenDebrief = { app.ganam.assistant.CallDebriefActivity.open(context, call.id) },
                    onAsk = { viewModel.openContextChat("call", call.id) }
                )
            }

            when {
                tier == WindowTier.COMPACT ->
                    if (compactDetail) detail(true) else listPane()

                else -> PaneRow {
                    Pane(width = sidePaneWidth(), tinted = true) { listPane() }
                    Pane(divider = threePane) { detail(!threePane) }
                    if (threePane) {
                        Pane(width = asidePaneWidth(), divider = false, tinted = true) {
                            selectedCall?.let { CallActionsPane(it, audioPlayer) }
                        }
                    }
                }
            }
        }
    }

    reanalyzeTargetId?.let { id ->
        calls.find { it.id == id }?.let { call ->
            ReanalyzeDialog(
                call = call,
                onDismiss = { reanalyzeTargetId = null },
                onReanalyze = { instructions ->
                    viewModel.reanalyzeCall(call.id, instructions)
                    reanalyzeTargetId = null
                }
            )
        } ?: run { reanalyzeTargetId = null }
    }
    labelEditId?.let { id ->
        calls.find { it.id == id }?.let { call ->
            LaunchedEffect(Unit) { if (labelCatalog.isEmpty()) viewModel.fetchLabelCatalog() }
            app.ganam.assistant.LabelEditorDialog(
                catalog = labelCatalog,
                selected = call.labels.map { it.id },
                onDismiss = { labelEditId = null },
                onSave = { viewModel.updateCallLabels(call.id, it); labelEditId = null }
            )
        } ?: run { labelEditId = null }
    }
    deleteTargetId?.let { id ->
        DeleteCallConfirmDialog(
            onConfirm = {
                if (selectedCallId == id) audioPlayer.stop()
                viewModel.deleteCall(id)
                deleteTargetId = null
                if (tier == WindowTier.COMPACT) detailOpen = false
            },
            onDismiss = { deleteTargetId = null }
        )
    }
}
