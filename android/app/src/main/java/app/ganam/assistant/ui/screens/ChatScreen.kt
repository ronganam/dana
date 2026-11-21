package app.ganam.assistant.ui.screens

import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import app.ganam.assistant.ui.theme.Success
import app.ganam.assistant.ui.theme.Warning
import app.ganam.assistant.ui.theme.Info
import app.ganam.assistant.ui.theme.CategoryPalette

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.border
import androidx.compose.material.icons.outlined.ViewSidebar
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.draw.drawBehind
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.Phone
import androidx.compose.material.icons.filled.VolumeDown
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material.icons.automirrored.filled.ExitToApp
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.outlined.Image
import androidx.compose.material3.*
import app.ganam.assistant.data.realtime.RealtimeCallStatus
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.animation.core.*
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import app.ganam.assistant.ui.components.GlassCard
import app.ganam.assistant.ui.components.serverImageRequest
import app.ganam.assistant.ui.components.IterationPromptCard
import app.ganam.assistant.ui.components.SelectionCard
import app.ganam.assistant.ui.components.SiriGlowSphere
import app.ganam.assistant.ui.components.ToolCallCard
import app.ganam.assistant.ui.components.ModelPickerChip
import app.ganam.assistant.ui.components.ReasoningBlock
import app.ganam.assistant.ui.components.ThinkingCaption
import app.ganam.assistant.ui.theme.isWideLayout
import app.ganam.assistant.ui.viewmodels.MainViewModel
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.*
import androidx.compose.foundation.BorderStroke
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import app.ganam.assistant.ui.theme.DmMono
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.unit.LayoutDirection
import android.speech.tts.TextToSpeech
import android.widget.Toast
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.size
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import coil.compose.AsyncImage

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun ChatScreen(
    viewModel: MainViewModel,
    applyImePadding: Boolean = true,
    embedded: Boolean = false,
    showTopBar: Boolean = true,
    onToggleContext: (() -> Unit)? = null,
    contextOpen: Boolean = false,
    onMoveToApp: (() -> Unit)? = null
) {
    val messages by viewModel.chatMessages.collectAsState()
    val sessionContext by viewModel.sessionContext.collectAsState()
    val sessions by viewModel.sessions.collectAsState()
    val isRecording by viewModel.isRecording.collectAsState()
    val statusText by viewModel.chatStatusText.collectAsState()
    val selectionRequest by viewModel.selectionRequest.collectAsState()
    val pendingExtension by viewModel.pendingExtension.collectAsState()

    val isWide = isWideLayout()

    val currentSessionId by viewModel.currentSessionId.collectAsState()

    val selectedRealtimeModel by viewModel.selectedRealtimeModel.collectAsState()
    val isCallActive by viewModel.isCallActive.collectAsState()
    val callStatus by viewModel.callStatus.collectAsState()
    val currentLiveTranscript by viewModel.currentLiveTranscript.collectAsState()
    val activeCallToolName by viewModel.activeCallToolName.collectAsState()
    val isCallMuted by viewModel.isCallMuted.collectAsState()
    val isSpeakerOn by viewModel.isSpeakerOn.collectAsState()

    var modelMenuExpanded by remember { mutableStateOf(false) }

    var inputText by remember { mutableStateOf("") }
    val draftText by viewModel.draftText.collectAsState()
    LaunchedEffect(draftText) {
        draftText?.let {
            inputText = it
            viewModel.consumeDraft()
        }
    }
    var showHistorySheet by remember { mutableStateOf(false) }
    val serverStatus by viewModel.serverStatus.collectAsState()

    val context = LocalContext.current
    val listState = rememberLazyListState()
    val clipboardManager = LocalClipboardManager.current

    var tts by remember { mutableStateOf<TextToSpeech?>(null) }
    DisposableEffect(context) {
        val ttsInstance = TextToSpeech(context) { status ->
            if (status == TextToSpeech.SUCCESS) {
                // TextToSpeech initialized successfully
            }
        }
        tts = ttsInstance
        onDispose {
            ttsInstance.stop()
            ttsInstance.shutdown()
        }
    }

    val registryOwner = androidx.activity.compose.LocalActivityResultRegistryOwner.current
    val recordAudioPermissionLauncher = if (registryOwner != null) {
        rememberLauncherForActivityResult(
            ActivityResultContracts.RequestPermission()
        ) { isGranted ->
            if (isGranted) {
                viewModel.startVoiceRecording()
            }
        }
    } else {
        null
    }

    val callAudioPermissionLauncher = if (registryOwner != null) {
        rememberLauncherForActivityResult(
            ActivityResultContracts.RequestPermission()
        ) { isGranted ->
            if (isGranted) {
                viewModel.startRealtimeCall()
            } else {
                Toast.makeText(context, "Microphone permission required for call mode", Toast.LENGTH_SHORT).show()
            }
        }
    } else {
        null
    }

    val pendingImageUri by viewModel.pendingImageUri.collectAsState()

    val imagePickerLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.PickVisualMedia()
    ) { uri ->
        if (uri != null) {
            viewModel.setPendingImage(uri)
        }
    }

    // Scroll to bottom on new messages
    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) {
            listState.animateScrollToItem(messages.size - 1)
        }
    }

    // Follow a reply while it streams in, unless the user scrolled up to read.
    val streamingLength = messages.lastOrNull()?.takeIf { it.isStreaming }
        ?.let { it.message.length + it.reasoning.length } ?: -1
    LaunchedEffect(streamingLength) {
        if (streamingLength < 0) return@LaunchedEffect
        val info = listState.layoutInfo
        val lastVisible = info.visibleItemsInfo.lastOrNull()?.index ?: return@LaunchedEffect
        if (lastVisible >= info.totalItemsCount - 2) listState.scrollBy(10_000f)
    }

    // Scroll to reveal a pending selection prompt card
    LaunchedEffect(selectionRequest) {
        if (selectionRequest != null && listState.layoutInfo.totalItemsCount > 0) {
            listState.animateScrollToItem(listState.layoutInfo.totalItemsCount - 1)
        }
    }

    Scaffold(
        containerColor = Color.Transparent,
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        topBar = {
            if (showTopBar) app.ganam.assistant.ui.components.ScreenTopBarSlots(
                                title = { 
                    Column(horizontalAlignment = Alignment.Start) {
                        if (currentSessionId.isNotEmpty()) {
                            app.ganam.assistant.ui.components.MonoLabel(
                                text = java.text.SimpleDateFormat("EEE d MMM", java.util.Locale.getDefault()).format(java.util.Date()),
                                modifier = Modifier.clickable {
                                    clipboardManager.setText(AnnotatedString(currentSessionId))
                                    Toast.makeText(context, "Session ID copied!", Toast.LENGTH_SHORT).show()
                                }
                            )
                        }
                        Text(
                            "Dana",
                            style = MaterialTheme.typography.headlineMedium
                        )
                    }
                },
                navigationIcon = {
                    IconButton(onClick = { viewModel.startNewSession(saveOld = true) }) {
                        Icon(Icons.Filled.Add, contentDescription = "New Session")
                    }
                },
                actions = {
                    // Realtime Model Dropdown Menu
                    Box {
                        Surface(
                            shape = RoundedCornerShape(20.dp),
                            color = MaterialTheme.colorScheme.surfaceContainerHigh,
                            modifier = Modifier
                                .clip(RoundedCornerShape(20.dp))
                                .clickable(enabled = !isCallActive) { modelMenuExpanded = true }
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
                            ) {
                                Text(
                                    text = if (selectedRealtimeModel.contains("mini")) "Mini" else "Realtime",
                                    style = MaterialTheme.typography.labelMedium,
                                    fontWeight = FontWeight.Bold,
                                    color = MaterialTheme.colorScheme.primary
                                )
                                Icon(
                                    Icons.Default.ArrowDropDown,
                                    contentDescription = "Select Realtime Model",
                                    modifier = Modifier.size(16.dp)
                                )
                            }
                        }

                        DropdownMenu(
                            expanded = modelMenuExpanded,
                            onDismissRequest = { modelMenuExpanded = false }
                        ) {
                            DropdownMenuItem(
                                text = { Text("GPT Realtime") },
                                onClick = {
                                    viewModel.setRealtimeModel("gpt-realtime")
                                    modelMenuExpanded = false
                                },
                                leadingIcon = {
                                    if (selectedRealtimeModel == "gpt-realtime") {
                                        Icon(Icons.Default.Check, contentDescription = null)
                                    }
                                }
                            )
                            DropdownMenuItem(
                                text = { Text("GPT Realtime Mini") },
                                onClick = {
                                    viewModel.setRealtimeModel("gpt-realtime-mini")
                                    modelMenuExpanded = false
                                },
                                leadingIcon = {
                                    if (selectedRealtimeModel == "gpt-realtime-mini") {
                                        Icon(Icons.Default.Check, contentDescription = null)
                                    }
                                }
                            )
                        }
                    }

                    Spacer(modifier = Modifier.width(2.dp))

                    // Realtime Call Action Button (Start / Hang up)
                    IconButton(
                        onClick = {
                            val hasAudioPermission = ContextCompat.checkSelfPermission(
                                context,
                                Manifest.permission.RECORD_AUDIO
                            ) == PackageManager.PERMISSION_GRANTED

                            if (isCallActive) {
                                viewModel.endRealtimeCall()
                            } else {
                                if (hasAudioPermission) {
                                    viewModel.startRealtimeCall()
                                } else {
                                    callAudioPermissionLauncher?.launch(Manifest.permission.RECORD_AUDIO)
                                }
                            }
                        }
                    ) {
                        if (isCallActive) {
                            Icon(
                                imageVector = Icons.Default.CallEnd,
                                contentDescription = "Hang Up Call",
                                tint = MaterialTheme.colorScheme.error
                            )
                        } else {
                            Icon(
                                imageVector = Icons.Default.Phone,
                                contentDescription = "Start Realtime Call",
                                tint = MaterialTheme.colorScheme.primary
                            )
                        }
                    }

                    if (onMoveToApp != null) {
                        IconButton(onClick = onMoveToApp) {
                            Icon(
                                imageVector = Icons.AutoMirrored.Filled.ExitToApp,
                                contentDescription = "Continue in App",
                                tint = MaterialTheme.colorScheme.primary
                            )
                        }
                    }
                    if (onToggleContext != null) {
                        IconButton(onClick = onToggleContext) {
                            Icon(
                                androidx.compose.material.icons.Icons.Outlined.ViewSidebar,
                                contentDescription = if (contextOpen) "Hide context" else "Show context",
                                tint = if (contextOpen) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                    if (!embedded) {
                        IconButton(onClick = {
                            viewModel.fetchSessions()
                            showHistorySheet = true
                        }) {
                            Icon(Icons.AutoMirrored.Filled.List, contentDescription = "History")
                        }
                    }
                }
            )
        }
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(top = innerPadding.calculateTopPadding())
                .then(
                    if (applyImePadding) {
                        Modifier.windowInsetsPadding(WindowInsets.ime)
                    } else {
                        Modifier
                    }
                )
        ) {
            // Realtime In-Call Status Banner
            if (isCallActive) {
                RealtimeCallBanner(
                    status = callStatus,
                    modelName = if (selectedRealtimeModel.contains("mini")) "GPT Realtime Mini" else "GPT Realtime",
                    activeToolName = activeCallToolName,
                    liveTranscript = currentLiveTranscript,
                    isMuted = isCallMuted,
                    isSpeakerOn = isSpeakerOn,
                    onToggleMute = { viewModel.toggleCallMute() },
                    onToggleSpeaker = { viewModel.toggleSpeaker() },
                    onHangUp = { viewModel.endRealtimeCall() },
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 8.dp)
                )
            }

            // Chat history area
            LazyColumn(
                state = listState,
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                if (sessionContext != null) {
                    // Opened from a call suggestion / call / reminder: show what's attached
                    // and one-tap prompts (they send right away).
                    item(key = "context_card") {
                        val ctxCard = sessionContext ?: return@item
                        val localContext = androidx.compose.ui.platform.LocalContext.current
                        app.ganam.assistant.ui.components.ContextAttachmentCard(
                            context = ctxCard,
                            onPrompt = { prompt -> viewModel.sendChatMessage(prompt) },
                            onOpenSource = ctxCard.callId?.let { cid -> { app.ganam.assistant.CallDebriefActivity.open(localContext, cid) } },
                            showPrompts = messages.none { it.isUser }
                        )
                    }
                } else if (messages.isEmpty()) {
                    item(key = "empty_state") {
                        val allTasks by viewModel.tasks.collectAsState()
                        app.ganam.assistant.ui.screens.chat.TodayBriefing(
                            tasks = allTasks,
                            onPrompt = { inputText = it },
                            waiting = { app.ganam.assistant.ui.screens.chat.WaitingOnYou(viewModel) }
                        )
                    }
                }
                items(messages, key = { it.id }) { msg ->
                    // Tool-call messages get a ToolCallCard
                    if (msg.toolCall != null) {
                        ToolCallCard(
                            messageId = msg.id,
                            entry = msg.toolCall,
                            onToggleCollapse = { id -> viewModel.toggleToolCallCollapse(id) },
                            onApprove = { approved -> viewModel.approveAction(approved) },
                            modifier = Modifier
                                .fillMaxWidth()
                                .animateItemPlacement()
                        )
                    } else {
                        // Regular user / assistant chat bubble or typing indicator
                        app.ganam.assistant.ui.screens.chat.ChatBubble(
                            msg = msg,
                            tts = tts,
                            wide = isWide,
                            serverUrl = viewModel.getServerUrl(),
                            apiToken = viewModel.getApiToken(),
                            modifier = Modifier.animateItemPlacement()
                        )
                    }
                }

                // In-Call Streaming Assistant Transcript Item
                if (isCallActive && currentLiveTranscript.isNotBlank()) {
                    item(key = "live_call_streaming_transcript") {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .animateItemPlacement(),
                            horizontalArrangement = Arrangement.Start
                        ) {
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth(0.85f)
                                    .clip(RoundedCornerShape(8.dp))
                                    .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f))
                                    .padding(12.dp)
                            ) {
                                Column {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        VoiceWaveformIndicator()
                                        Spacer(modifier = Modifier.width(6.dp))
                                        Text(
                                            text = "Dana (speaking…)",
                                            style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.primary
                                        )
                                    }
                                    Spacer(modifier = Modifier.height(4.dp))
                                    Text(
                                        text = currentLiveTranscript,
                                        style = MaterialTheme.typography.bodyMedium,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant
                                    )
                                }
                            }
                        }
                    }
                }

                // Interactive user-selection prompt card
                selectionRequest?.let { sel ->
                    item(key = "selection_request_card") {
                        SelectionCard(
                            request = sel,
                            onSelect = { indices -> viewModel.sendSelection(indices) },
                            onCancel = { viewModel.cancelSelection() },
                            modifier = Modifier.animateItemPlacement()
                        )
                    }
                }

                // Step-limit prompt: the run paused mid-task and needs a decision
                pendingExtension?.let { ext ->
                    item(key = "pending_extension_card") {
                        IterationPromptCard(
                            summary = ext.summary,
                            onExtend = { viewModel.extendIterations() },
                            onStop = { viewModel.declineIterations() },
                            modifier = Modifier.animateItemPlacement()
                        )
                    }
                }
            }

            // Model picker + live status
            val chatModels by viewModel.chatModels.collectAsState()
            val chatModel by viewModel.chatModel.collectAsState()
            if (chatModels.isNotEmpty() || statusText.isNotEmpty()) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    ModelPickerChip(
                        models = chatModels,
                        selectedId = chatModel,
                        onSelect = { viewModel.setChatModel(it) }
                    )
                    Spacer(modifier = Modifier.weight(1f))
                    if (statusText.isNotEmpty()) {
                        Text(
                            text = statusText,
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.primary,
                            maxLines = 1
                        )
                    }
                }
            }

            // NOTE: Approval UI is now embedded inside ToolCallCard.
            // The pendingApproval state still exists for backward-compat with voice/websocket paths
            // but is NOT shown as a separate floating card anymore.

            // Bottom controls
            val borderGlowTransition = rememberInfiniteTransition(label = "borderGlow")
            val borderAlpha by if (isRecording || isCallActive) {
                borderGlowTransition.animateFloat(
                    initialValue = 0.2f,
                    targetValue = 0.8f,
                    animationSpec = infiniteRepeatable(
                        animation = tween(1000, easing = FastOutSlowInEasing),
                        repeatMode = RepeatMode.Reverse
                    ),
                    label = "borderAlpha"
                )
            } else {
                remember { mutableStateOf(0.12f) }
            }

            // Image preview
            if (pendingImageUri != null) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    AsyncImage(
                        model = pendingImageUri,
                        contentDescription = "Selected image",
                        modifier = Modifier
                            .size(80.dp)
                            .clip(RoundedCornerShape(4.dp)),
                        contentScale = ContentScale.Crop
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    IconButton(onClick = { viewModel.clearPendingImage() }) {
                        Icon(
                            Icons.Default.Close,
                            contentDescription = "Remove image",
                            tint = MaterialTheme.colorScheme.error
                        )
                    }
                }
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                // Image attachment button
                IconButton(
                    onClick = { imagePickerLauncher.launch(
                        PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)
                    ) },
                    modifier = Modifier.size(40.dp)
                ) {
                    Icon(
                        Icons.Outlined.Image,
                        contentDescription = "Attach image",
                        tint = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
                    )
                }

                Spacer(modifier = Modifier.width(4.dp))

                // Outlined message input
                OutlinedTextField(
                    value = inputText,
                    onValueChange = { inputText = it },
                    placeholder = {
                        if (isCallActive) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                VoiceWaveformIndicator()
                                Spacer(modifier = Modifier.width(8.dp))
                                Text("In call with Dana…", color = MaterialTheme.colorScheme.primary)
                            }
                        } else if (isRecording) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                VoiceWaveformIndicator()
                                Spacer(modifier = Modifier.width(8.dp))
                                Text("Listening...", color = MaterialTheme.colorScheme.primary)
                            }
                        } else {
                            Text("Say it, or type it…")
                        }
                    },
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(24.dp),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                        unfocusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                        focusedBorderColor = if (isRecording || isCallActive) MaterialTheme.colorScheme.primary.copy(alpha = borderAlpha) else MaterialTheme.colorScheme.primary.copy(alpha = 0.5f),
                        unfocusedBorderColor = if (isRecording || isCallActive) MaterialTheme.colorScheme.primary.copy(alpha = borderAlpha) else MaterialTheme.colorScheme.outlineVariant
                    ),
                    trailingIcon = {
                        val isStreaming = viewModel.isStreaming.value
                        if (isStreaming) {
                            IconButton(onClick = { viewModel.stopAssistant() }) {
                                Icon(Icons.Default.Stop, contentDescription = "Stop", tint = MaterialTheme.colorScheme.error)
                            }
                        } else if (inputText.isNotBlank() || pendingImageUri != null) {
                            IconButton(onClick = {
                                // Keep the text if the server is unreachable so it can be resent.
                                if (viewModel.sendChatMessage(inputText)) inputText = ""
                            }) {
                                Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send", tint = MaterialTheme.colorScheme.primary)
                            }
                        }
                    }
                )

                Spacer(modifier = Modifier.width(12.dp))

                // Siri visualizer orb button with ripples drawing outside bounds
                Box(
                    modifier = Modifier
                        .size(72.dp)
                        // Dimmed while the server is unreachable: tapping re-checks instead of listening.
                        .alpha(if (serverStatus.isOffline && !isRecording && !isCallActive) 0.4f else 1f),
                    contentAlignment = Alignment.Center
                ) {
                    app.ganam.assistant.ui.components.VoiceDial(
                        listening = isRecording || isCallActive,
                        modifier = Modifier.fillMaxSize()
                    ) {
                        Icon(
                            if (isRecording || isCallActive) androidx.compose.material.icons.Icons.Filled.Stop
                            else androidx.compose.material.icons.Icons.Filled.Mic,
                            contentDescription = if (isRecording || isCallActive) "Stop" else "Talk",
                            tint = MaterialTheme.colorScheme.onPrimary,
                            modifier = Modifier.size(24.dp)
                        )
                    }
                    // Centered circular click target matching the inner core size
                    Box(
                        modifier = Modifier
                            .size(56.dp)
                            .clip(RoundedCornerShape(20.dp))
                            .clickable {
                                val hasPermission = ContextCompat.checkSelfPermission(
                                    context,
                                    Manifest.permission.RECORD_AUDIO
                                ) == PackageManager.PERMISSION_GRANTED
                                if (!hasPermission) {
                                    recordAudioPermissionLauncher?.launch(Manifest.permission.RECORD_AUDIO)
                                } else {
                                    if (isCallActive) {
                                        viewModel.endRealtimeCall()
                                    } else if (isRecording) {
                                        viewModel.stopVoiceRecording()
                                    } else {
                                        viewModel.startVoiceRecording()
                                    }
                                }
                            }
                    )
                }
            }
        }
    }

    // iOS-style session history list sheet
    if (showHistorySheet) {
        ModalBottomSheet(
            onDismissRequest = { showHistorySheet = false },
            dragHandle = { BottomSheetDefaults.DragHandle() },
            containerColor = MaterialTheme.colorScheme.surfaceColorAtElevation(1.dp),
            contentColor = MaterialTheme.colorScheme.onSurface
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp)
            ) {
                Text(
                    text = "History",
                    fontWeight = FontWeight.Bold,
                    style = MaterialTheme.typography.titleLarge,
                    modifier = Modifier.padding(vertical = 12.dp)
                )

                var customIdInput by remember { mutableStateOf("") }
                Row(
                    modifier = Modifier.fillMaxWidth().padding(bottom = 12.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    OutlinedTextField(
                        value = customIdInput,
                        onValueChange = { customIdInput = it },
                        label = { Text("Load Custom Session ID") },
                        modifier = Modifier.weight(1f).padding(end = 8.dp),
                        singleLine = true,
                        textStyle = MaterialTheme.typography.bodyMedium
                    )
                    Button(
                        onClick = {
                            if (customIdInput.trim().isNotEmpty()) {
                                viewModel.loadSession(customIdInput.trim())
                                showHistorySheet = false
                            }
                        },
                        enabled = customIdInput.trim().isNotEmpty()
                    ) {
                        Text("Load")
                    }
                }

                if (sessions.isEmpty()) {
                    Text(
                        text = "No previous conversations found.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f),
                        modifier = Modifier.padding(vertical = 16.dp)
                    )
                } else {
                    LazyColumn(
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(max = 400.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        items(sessions) { session ->
                            val date = if (session.timestamp > 0) SimpleDateFormat("MMM dd, HH:mm", Locale.getDefault()).format(Date(session.timestamp)) else ""
                            Card(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable {
                                        viewModel.loadSession(session.id)
                                        showHistorySheet = false
                                    },
                                colors = CardDefaults.cardColors(
                                    containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.3f)
                                )
                            ) {
                                Column(modifier = Modifier.padding(16.dp)) {
                                    Row(
                                        modifier = Modifier.fillMaxWidth(),
                                        horizontalArrangement = Arrangement.SpaceBetween
                                    ) {
                                        Text(
                                            text = session.snippet,
                                            fontWeight = FontWeight.SemiBold,
                                            style = MaterialTheme.typography.bodyMedium,
                                            maxLines = 1,
                                            modifier = Modifier.weight(1f)
                                        )
                                        Text(
                                            text = date,
                                            style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.primary
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
                Spacer(modifier = Modifier.height(24.dp))
            }
        }
    }
}

@Composable
fun VoiceWaveformIndicator(
    modifier: Modifier = Modifier
) {
    val infiniteTransition = rememberInfiniteTransition(label = "waveform")
    val numBars = 5
    val animations = (0 until numBars).map { index ->
        infiniteTransition.animateFloat(
            initialValue = 0.2f,
            targetValue = 1.0f,
            animationSpec = infiniteRepeatable(
                animation = tween(
                    durationMillis = when (index) {
                        0 -> 450
                        1 -> 350
                        2 -> 550
                        3 -> 400
                        else -> 500
                    },
                    easing = FastOutSlowInEasing
                ),
                repeatMode = RepeatMode.Reverse
            ),
            label = "bar_$index"
        )
    }

    Row(
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        animations.forEachIndexed { index, animValue ->
            val heightMultiplier = when (index) {
                0, 4 -> 0.6f
                1, 3 -> 0.8f
                else -> 1.0f
            }
            Box(
                modifier = Modifier
                    .size(width = 4.dp, height = 24.dp)
                    .graphicsLayer {
                        scaleY = animValue.value * heightMultiplier
                    }
                    .background(
                        brush = Brush.verticalGradient(
                            colors = listOf(
                                Color(0xFF00F0FF),
                                Color(0xFFD000FF)
                            )
                        ),
                        shape = RoundedCornerShape(2.dp)
                    )
            )
        }
    }
}

@Composable
fun RealtimeCallBanner(
    status: RealtimeCallStatus,
    modelName: String,
    activeToolName: String?,
    liveTranscript: String,
    isMuted: Boolean,
    isSpeakerOn: Boolean,
    onToggleMute: () -> Unit,
    onToggleSpeaker: () -> Unit,
    onHangUp: () -> Unit,
    modifier: Modifier = Modifier
) {
    val statusColor = when (status) {
        RealtimeCallStatus.IDLE -> MaterialTheme.colorScheme.onSurfaceVariant
        RealtimeCallStatus.CONNECTING -> Warning
        RealtimeCallStatus.LISTENING -> Success
        RealtimeCallStatus.SPEAKING -> Info
        RealtimeCallStatus.TOOL_RUNNING -> CategoryPalette[4]
        RealtimeCallStatus.ERROR -> MaterialTheme.colorScheme.error
    }

    val infiniteTransition = rememberInfiniteTransition(label = "pulse")
    val pulseAlpha by infiniteTransition.animateFloat(
        initialValue = 0.4f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(900, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "pulseAlpha"
    )

    GlassCard(
        modifier = modifier,
        cornerRadius = 8.dp
    ) {
        Column(
            modifier = Modifier.fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    // Pulsing status orb
                    Box(
                        modifier = Modifier
                            .size(12.dp)
                            .graphicsLayer { alpha = pulseAlpha }
                            .background(statusColor, shape = RoundedCornerShape(4.dp))
                    )

                    Column {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                text = "Realtime Call",
                                style = MaterialTheme.typography.titleSmall,
                                fontWeight = FontWeight.Bold,
                                color = MaterialTheme.colorScheme.onSurface
                            )
                            Spacer(modifier = Modifier.width(6.dp))
                            Surface(
                                shape = RoundedCornerShape(4.dp),
                                color = MaterialTheme.colorScheme.primary.copy(alpha = 0.15f)
                            ) {
                                Text(
                                    text = modelName,
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.primary,
                                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                                    fontWeight = FontWeight.SemiBold
                                )
                            }
                        }

                        Text(
                            text = when (status) {
                                RealtimeCallStatus.TOOL_RUNNING -> "Executing ${activeToolName ?: "tool"}..."
                                else -> status.toDisplayString()
                            },
                            style = MaterialTheme.typography.bodySmall,
                            color = statusColor
                        )
                    }
                }

                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    if (status == RealtimeCallStatus.SPEAKING || status == RealtimeCallStatus.LISTENING) {
                        VoiceWaveformIndicator()
                    }

                    IconButton(onClick = onToggleMute) {
                        Icon(
                            imageVector = if (isMuted) Icons.Default.MicOff else Icons.Default.Mic,
                            contentDescription = if (isMuted) "Unmute microphone" else "Mute microphone",
                            tint = if (isMuted) MaterialTheme.colorScheme.error
                            else MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }

                    IconButton(onClick = onToggleSpeaker) {
                        Icon(
                            imageVector = if (isSpeakerOn) Icons.Default.VolumeUp else Icons.Default.VolumeDown,
                            contentDescription = if (isSpeakerOn) "Switch to earpiece" else "Switch to speaker",
                            tint = if (isSpeakerOn) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }

                    FilledTonalButton(
                        onClick = onHangUp,
                        colors = ButtonDefaults.filledTonalButtonColors(
                            containerColor = MaterialTheme.colorScheme.errorContainer,
                            contentColor = MaterialTheme.colorScheme.onErrorContainer
                        ),
                        contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp),
                        shape = RoundedCornerShape(8.dp)
                    ) {
                        Icon(
                            imageVector = Icons.Default.CallEnd,
                            contentDescription = "Hang Up",
                            modifier = Modifier.size(16.dp)
                        )
                        Spacer(modifier = Modifier.width(4.dp))
                        Text("Hang Up", style = MaterialTheme.typography.labelMedium)
                    }
                }
            }

            // Live streaming assistant transcript preview
            if (liveTranscript.isNotBlank()) {
                Surface(
                    shape = RoundedCornerShape(6.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.4f),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text(
                        text = liveTranscript,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                        fontStyle = FontStyle.Italic
                    )
                }
            }
        }
    }
}

@Composable
fun TypingIndicator(
    modifier: Modifier = Modifier
) {
    val transition = rememberInfiniteTransition(label = "typing")

    val alpha1 by transition.animateFloat(
        initialValue = 0.2f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 600, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse,
            initialStartOffset = StartOffset(0)
        ),
        label = "dot_alpha1"
    )
    val alpha2 by transition.animateFloat(
        initialValue = 0.2f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 600, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse,
            initialStartOffset = StartOffset(200)
        ),
        label = "dot_alpha2"
    )
    val alpha3 by transition.animateFloat(
        initialValue = 0.2f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 600, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse,
            initialStartOffset = StartOffset(400)
        ),
        label = "dot_alpha3"
    )

    Row(
        modifier = modifier.padding(vertical = 4.dp, horizontal = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        val color = MaterialTheme.colorScheme.onSurfaceVariant
        Box(
            modifier = Modifier
                .size(8.dp)
                .graphicsLayer { alpha = alpha1 }
                .background(color, RoundedCornerShape(4.dp))
        )
        Box(
            modifier = Modifier
                .size(8.dp)
                .graphicsLayer { alpha = alpha2 }
                .background(color, RoundedCornerShape(4.dp))
        )
        Box(
            modifier = Modifier
                .size(8.dp)
                .graphicsLayer { alpha = alpha3 }
                .background(color, RoundedCornerShape(4.dp))
        )
    }
}

sealed class MarkdownBlock {
    data class Paragraph(val text: AnnotatedString) : MarkdownBlock()
    data class Heading(val text: AnnotatedString, val level: Int) : MarkdownBlock()
    data class BulletItem(val text: AnnotatedString) : MarkdownBlock()
    data class CodeBlock(val code: String, val language: String?) : MarkdownBlock()
}

fun parseInlineMarkdown(text: String): AnnotatedString {
    val builder = AnnotatedString.Builder()
    var i = 0
    val n = text.length
    while (i < n) {
        // Inline code: `code`
        if (text[i] == '`') {
            val nextBacktick = text.indexOf('`', i + 1)
            if (nextBacktick != -1) {
                builder.pushStyle(SpanStyle(
                    fontFamily = DmMono,
                    background = Color(0xFFF7F5F0).copy(alpha = 0.12f),
                    color = Color(0xFFC9C0AD)
                ))
                builder.append(text.substring(i + 1, nextBacktick))
                builder.pop()
                i = nextBacktick + 1
                continue
            }
        }
        
        // Bold: **text**
        if (i + 1 < n && text[i] == '*' && text[i + 1] == '*') {
            val nextBold = text.indexOf("**", i + 2)
            if (nextBold != -1) {
                builder.pushStyle(SpanStyle(fontWeight = FontWeight.Bold))
                builder.append(parseInlineMarkdown(text.substring(i + 2, nextBold)))
                builder.pop()
                i = nextBold + 2
                continue
            }
        }
        if (i + 1 < n && text[i] == '_' && text[i + 1] == '_') {
            val nextBold = text.indexOf("__", i + 2)
            if (nextBold != -1) {
                builder.pushStyle(SpanStyle(fontWeight = FontWeight.Bold))
                builder.append(parseInlineMarkdown(text.substring(i + 2, nextBold)))
                builder.pop()
                i = nextBold + 2
                continue
            }
        }
        
        // Italic: *text*
        if (text[i] == '*') {
            val nextItalic = text.indexOf('*', i + 1)
            if (nextItalic != -1) {
                builder.pushStyle(SpanStyle(fontStyle = FontStyle.Italic))
                builder.append(parseInlineMarkdown(text.substring(i + 1, nextItalic)))
                builder.pop()
                i = nextItalic + 1
                continue
            }
        }
        if (text[i] == '_') {
            val nextItalic = text.indexOf('_', i + 1)
            if (nextItalic != -1) {
                builder.pushStyle(SpanStyle(fontStyle = FontStyle.Italic))
                builder.append(parseInlineMarkdown(text.substring(i + 1, nextItalic)))
                builder.pop()
                i = nextItalic + 1
                continue
            }
        }
        
        // Otherwise, regular character
        builder.append(text[i].toString())
        i++
    }
    
    return builder.toAnnotatedString()
}

fun parseMarkdown(text: String): List<MarkdownBlock> {
    val lines = text.split("\n")
    val blocks = mutableListOf<MarkdownBlock>()
    
    var inCodeBlock = false
    val codeContent = StringBuilder()
    var codeLanguage: String? = null
    
    for (line in lines) {
        if (line.trim().startsWith("```")) {
            if (inCodeBlock) {
                blocks.add(MarkdownBlock.CodeBlock(codeContent.toString().trimEnd(), codeLanguage))
                codeContent.setLength(0)
                codeLanguage = null
                inCodeBlock = false
            } else {
                inCodeBlock = true
                val lang = line.trim().substring(3).trim()
                codeLanguage = if (lang.isNotEmpty()) lang else null
            }
            continue
        }
        
        if (inCodeBlock) {
            codeContent.append(line).append("\n")
            continue
        }
        
        val trimmed = line.trim()
        if (trimmed.isEmpty()) {
            continue
        }
        
        // Check for headings
        if (trimmed.startsWith("#")) {
            val hashCount = trimmed.takeWhile { it == '#' }.length
            if (hashCount in 1..6 && trimmed.getOrNull(hashCount) == ' ') {
                val headingText = trimmed.substring(hashCount + 1).trim()
                blocks.add(MarkdownBlock.Heading(parseInlineMarkdown(headingText), hashCount))
                continue
            }
        }
        
        // Check for bullet list items
        if (trimmed.startsWith("- ") || trimmed.startsWith("* ") || trimmed.startsWith("• ")) {
            val itemText = trimmed.substring(2).trim()
            blocks.add(MarkdownBlock.BulletItem(parseInlineMarkdown(itemText)))
            continue
        }
        
        // Default: paragraph
        blocks.add(MarkdownBlock.Paragraph(parseInlineMarkdown(line)))
    }
    
    if (inCodeBlock) {
        blocks.add(MarkdownBlock.CodeBlock(codeContent.toString().trimEnd(), codeLanguage))
    }
    
    return blocks
}

@Composable
fun MarkdownText(
    text: String,
    color: Color,
    style: TextStyle,
    modifier: Modifier = Modifier
) {
    val blocks = remember(text) { parseMarkdown(text) }
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        blocks.forEach { block ->
            when (block) {
                is MarkdownBlock.Heading -> {
                    val scale = when (block.level) {
                        1 -> 1.3f
                        2 -> 1.2f
                        3 -> 1.1f
                        else -> 1.0f
                    }
                    Text(
                        text = block.text,
                        color = color,
                        fontWeight = FontWeight.Bold,
                        style = style.copy(fontSize = style.fontSize * scale),
                        modifier = Modifier.padding(top = 6.dp, bottom = 2.dp)
                    )
                }
                is MarkdownBlock.BulletItem -> {
                    Row(
                        modifier = Modifier.padding(start = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        Text(
                            text = "•",
                            color = color,
                            style = style
                        )
                        Text(
                            text = block.text,
                            color = color,
                            style = style,
                            modifier = Modifier.weight(1f)
                        )
                    }
                }
                is MarkdownBlock.CodeBlock -> {
                    Surface(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 4.dp),
                        shape = RoundedCornerShape(4.dp),
                        color = Color.Black.copy(alpha = 0.15f),
                        border = BorderStroke(1.dp, Color.White.copy(alpha = 0.1f))
                    ) {
                        Column(modifier = Modifier.padding(12.dp)) {
                            block.language?.let {
                                Text(
                                    text = it.uppercase(),
                                    color = color.copy(alpha = 0.5f),
                                    style = MaterialTheme.typography.labelSmall,
                                    modifier = Modifier.padding(bottom = 4.dp)
                                )
                            }
                            Text(
                                text = block.code,
                                color = color.copy(alpha = 0.9f),
                                style = style.copy(
                                    fontFamily = DmMono,
                                    fontSize = style.fontSize * 0.9f
                                )
                            )
                        }
                    }
                }
                is MarkdownBlock.Paragraph -> {
                    Text(
                        text = block.text,
                        color = color,
                        style = style
                    )
                }
            }
        }
    }
}
