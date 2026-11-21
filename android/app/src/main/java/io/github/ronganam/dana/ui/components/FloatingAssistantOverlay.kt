package io.github.ronganam.dana.ui.components

import androidx.compose.foundation.gestures.scrollBy
import android.Manifest
import android.content.pm.PackageManager
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.awaitTouchSlopOrCancellation
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import io.github.ronganam.dana.ui.components.assistant.AssistantDock
import io.github.ronganam.dana.ui.components.assistant.AssistantHeader
import io.github.ronganam.dana.ui.components.assistant.AssistantStage
import io.github.ronganam.dana.ui.components.assistant.AssistantTurn
import io.github.ronganam.dana.ui.components.assistant.DefaultQuickActions
import io.github.ronganam.dana.ui.components.assistant.QuickActionRow
import io.github.ronganam.dana.ui.components.assistant.ScreenChip
import io.github.ronganam.dana.ui.components.assistant.ScreenStatusLine
import io.github.ronganam.dana.ui.components.assistant.VoiceDial
import io.github.ronganam.dana.ui.components.assistant.VoiceDialMode
import io.github.ronganam.dana.ui.theme.DanaSerif
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

enum class AssistantOverlayState {
    EXPANDED,
    MINIMIZED
}

/**
 * Power-button assistant. A thumb-zone sheet that rises from the bottom over whatever app
 * is underneath (capped to 560dp and centred on wide/unfolded screens), and a draggable
 * bubble when minimised. See DESIGN.md "Power-button assistant".
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun FloatingAssistantOverlay(
    viewModel: MainViewModel,
    /** Null hides "Continue in app" (the kiosk has no app to move to). */
    onMoveToApp: (() -> Unit)?,
    onDismiss: () -> Unit,
    onUpdateTouchableRegion: (Boolean, android.graphics.Region) -> Unit = { _, _ -> },
    /** When set, "minimise" (scrim tap, pull down, header arrow) calls this instead of showing the bubble. */
    onMinimizeOverride: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
    /**
     * The phone's power-button sheet: the "Screen" control (include the app
     * underneath with the next message). Null on the kiosk and TV, which keep
     * the attach-image button instead.
     */
    screenShare: io.github.ronganam.dana.ui.viewmodels.ScreenShareController? = null,
    /** Opens the system's assistant settings (the "Screen" control's fix). */
    onOpenAssistSettings: () -> Unit = {}
) {
    val context = LocalContext.current
    val density = LocalDensity.current
    val scope = rememberCoroutineScope()
    val clipboardManager = LocalClipboardManager.current

    val messages by viewModel.chatMessages.collectAsState()
    val isRecording by viewModel.isRecording.collectAsState()
    val isStreaming by viewModel.isStreaming.collectAsState()
    val statusText by viewModel.chatStatusText.collectAsState()
    val currentSessionId by viewModel.currentSessionId.collectAsState()
    val pendingImageUri by viewModel.pendingImageUri.collectAsState()
    val selectionRequest by viewModel.selectionRequest.collectAsState()
    val serverStatus by viewModel.serverStatus.collectAsState()
    val undos by viewModel.chatController.undos.collectAsState()
    val threadMode = viewModel.chatController.isThreadMode
    val memoryState by viewModel.chatController.memory.collectAsState()
    val clearing by viewModel.chatController.clearing.collectAsState()
    val inThread by viewModel.chatController.inThread.collectAsState()
    val memory = if (threadMode && inThread) memoryState else io.github.ronganam.dana.data.chat.ThreadModel.Memory.Unknown
    val memoryNow = io.github.ronganam.dana.ui.screens.chat.rememberMemoryNow(memory)
    // The transcript with the memory line ("Dana remembers from here"); older rows dimmed.
    val rows = remember(messages, memory, memoryNow) {
        io.github.ronganam.dana.data.chat.ThreadModel.withMemory(
            messages.map { io.github.ronganam.dana.data.chat.ThreadModel.Row.Message(it) }, messages, memory, memoryNow
        )
    }
    // The sheet's fresh view hides older messages Dana may still have in mind: say so.
    val remembersHidden = remember(messages, memory, memoryNow) {
        io.github.ronganam.dana.data.chat.ThreadModel.remembersEarlier(messages, memory, memoryNow)
    }
    val newTopic: (() -> Unit)? = if (memory.remembersAt(memoryNow)) ({
        viewModel.chatController.clearThread { Toast.makeText(context, it, Toast.LENGTH_SHORT).show() }
    }) else null
    val screenState = screenShare?.state?.collectAsState()?.value

    var overlayState by remember { mutableStateOf(AssistantOverlayState.EXPANDED) }
    val minimize: () -> Unit = {
        if (onMinimizeOverride != null) onMinimizeOverride() else overlayState = AssistantOverlayState.MINIMIZED
    }
    var inputText by remember { mutableStateOf("") }
    val listState = rememberLazyListState()

    // Vertical drag of the sheet (px, only downward is meaningful)
    var sheetDragY by remember { mutableFloatStateOf(0f) }

    // Bubble position with edge-snapping physics
    val bubbleAnimX = remember { Animatable(0f) }
    val bubbleAnimY = remember { Animatable(300f) }
    var hasInitializedBubblePos by remember { mutableStateOf(false) }
    var isBubbleDragging by remember { mutableStateOf(false) }

    // Speaks a reply when it is tapped (the engine binds on the first tap).
    val tapToSpeech = remember(context) { TapToSpeech(context) }
    DisposableEffect(tapToSpeech) { onDispose { tapToSpeech.shutdown() } }

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

    val imagePickerLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.PickVisualMedia()
    ) { uri ->
        if (uri != null) {
            viewModel.setPendingImage(uri)
        }
    }

    // Keep the latest turn in view
    LaunchedEffect(messages.size, rows.size) {
        if (messages.isNotEmpty()) {
            // The last transcript row (after the "remembers earlier" line, when shown).
            listState.animateScrollToItem(rows.size - 1 + if (remembersHidden) 1 else 0)
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

    // A turn just finished: show the end (Undo rows and "Open the thread" come after the reply).
    LaunchedEffect(isStreaming, undos.size) {
        if (!isStreaming && listState.layoutInfo.totalItemsCount > 0) {
            kotlinx.coroutines.delay(150)
            listState.animateScrollToItem(listState.layoutInfo.totalItemsCount - 1)
        }
    }

    // Scroll to reveal a prompt: a selection, or a tool that asks first (its card grows when it does).
    val pendingApproval by viewModel.pendingApproval.collectAsState()
    LaunchedEffect(selectionRequest, pendingApproval) {
        if ((selectionRequest != null || pendingApproval != null) && listState.layoutInfo.totalItemsCount > 0) {
            kotlinx.coroutines.delay(150)
            listState.animateScrollToItem(listState.layoutInfo.totalItemsCount - 1)
        }
    }

    // Expand overlay whenever a new session starts
    LaunchedEffect(currentSessionId) {
        if (currentSessionId.isNotEmpty()) {
            overlayState = AssistantOverlayState.EXPANDED
        }
    }

    val toggleVoice: () -> Unit = {
        val hasPermission = ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED
        if (!hasPermission) {
            recordAudioPermissionLauncher?.launch(Manifest.permission.RECORD_AUDIO)
        } else if (isRecording) {
            viewModel.stopVoiceRecording()
        } else {
            viewModel.startVoiceRecording()
        }
    }

    BoxWithConstraints(
        modifier = modifier
            .fillMaxSize()
            .statusBarsPadding()
            .navigationBarsPadding()
    ) {
        val screenWidthPx = with(density) { maxWidth.toPx() }
        val screenHeightPx = with(density) { maxHeight.toPx() }
        val bubbleSizeDp = 64.dp
        val bubbleSizePx = with(density) { bubbleSizeDp.toPx() }

        // Initialize bubble position near the right edge
        if (!hasInitializedBubblePos && screenWidthPx > 0f) {
            LaunchedEffect(screenWidthPx) {
                bubbleAnimX.snapTo(screenWidthPx - bubbleSizePx - with(density) { 16.dp.toPx() })
                bubbleAnimY.snapTo(screenHeightPx * 0.4f)
                hasInitializedBubblePos = true
            }
        }

        // Dynamically update touchable insets so touches pass through when minimized
        LaunchedEffect(overlayState, bubbleAnimX.value, bubbleAnimY.value, isBubbleDragging, screenWidthPx, screenHeightPx) {
            if (overlayState == AssistantOverlayState.MINIMIZED && screenWidthPx > 0f) {
                val pad = with(density) { 10.dp.toPx() }
                val bubbleRect = android.graphics.Rect(
                    (bubbleAnimX.value - pad).roundToInt().coerceAtLeast(0),
                    (bubbleAnimY.value - pad).roundToInt().coerceAtLeast(0),
                    (bubbleAnimX.value + bubbleSizePx + pad).roundToInt(),
                    (bubbleAnimY.value + bubbleSizePx + pad).roundToInt()
                )
                val region = android.graphics.Region(bubbleRect)
                if (isBubbleDragging) {
                    val dismissWidth = with(density) { 180.dp.toPx() }
                    val dismissHeight = with(density) { 120.dp.toPx() }
                    val dismissRect = android.graphics.Rect(
                        ((screenWidthPx - dismissWidth) / 2).roundToInt().coerceAtLeast(0),
                        (screenHeightPx - dismissHeight).roundToInt().coerceAtLeast(0),
                        ((screenWidthPx + dismissWidth) / 2).roundToInt(),
                        screenHeightPx.roundToInt()
                    )
                    region.union(dismissRect)
                }
                onUpdateTouchableRegion(true, region)
            } else {
                onUpdateTouchableRegion(false, android.graphics.Region())
            }
        }

        // Scrim (no AnimatedVisibility so the clickable can't intercept touches during exit)
        if (overlayState == AssistantOverlayState.EXPANDED) {
            val scrimAlpha = (0.5f - (sheetDragY / (screenHeightPx.coerceAtLeast(1f))) * 0.6f).coerceIn(0.15f, 0.5f)
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(Color.Black.copy(alpha = scrimAlpha))
                    .clickable(
                        indication = null,
                        interactionSource = remember { MutableInteractionSource() }
                    ) {
                        minimize()
                    }
            )
        }

        AnimatedContent(
            targetState = overlayState,
            transitionSpec = {
                if (targetState == AssistantOverlayState.EXPANDED) {
                    (fadeIn(tween(200)) + slideInVertically(tween(320, easing = FastOutSlowInEasing)) { it / 3 })
                        .togetherWith(fadeOut(tween(120)))
                } else {
                    fadeIn(tween(220, easing = FastOutSlowInEasing)) + scaleIn(
                        initialScale = 0.85f,
                        animationSpec = spring(dampingRatio = 0.75f, stiffness = Spring.StiffnessMediumLow)
                    ) togetherWith (fadeOut(tween(150, easing = LinearEasing)) +
                        slideOutVertically(tween(200)) { it / 4 })
                }
            },
            label = "overlay_transition"
        ) { state ->
            when (state) {
                AssistantOverlayState.EXPANDED -> {
                    val sheetMaxHeight = if (maxHeight > 760.dp) maxHeight * 0.72f else maxHeight * 0.86f
                    val hasUserTurn = messages.any { it.isUser }
                    val mode = when {
                        isRecording -> VoiceDialMode.LISTENING
                        isStreaming -> VoiceDialMode.THINKING
                        else -> VoiceDialMode.IDLE
                    }
                    val stateLabel = when {
                        isRecording -> "Listening"
                        isStreaming -> statusText.ifBlank { "Working" }
                        serverStatus.isOffline -> "Server unreachable"
                        else -> "Ready"
                    }

                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .imePadding(),
                        contentAlignment = Alignment.BottomCenter
                    ) {
                        Surface(
                            modifier = Modifier
                                .offset { IntOffset(0, sheetDragY.coerceAtLeast(0f).roundToInt()) }
                                .padding(horizontal = 8.dp, vertical = 8.dp)
                                .widthIn(max = 560.dp)
                                .fillMaxWidth()
                                .heightIn(min = 300.dp, max = sheetMaxHeight)
                                .shadow(20.dp, RoundedCornerShape(28.dp), spotColor = Color.Black.copy(alpha = 0.5f))
                                .clip(RoundedCornerShape(28.dp))
                                // Swallow taps inside the sheet so they never reach the scrim
                                .clickable(
                                    indication = null,
                                    interactionSource = remember { MutableInteractionSource() }
                                ) { },
                            color = MaterialTheme.colorScheme.surface,
                            shape = RoundedCornerShape(28.dp),
                            border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant)
                        ) {
                            Column(modifier = Modifier.fillMaxWidth()) {
                                // Grab zone: pull down to minimise
                                Box(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .pointerInput(Unit) {
                                            detectVerticalDragGestures(
                                                onDragEnd = {
                                                    if (sheetDragY > with(density) { 96.dp.toPx() }) {
                                                        minimize()
                                                    }
                                                    sheetDragY = 0f
                                                },
                                                onDragCancel = { sheetDragY = 0f }
                                            ) { change, dy ->
                                                change.consume()
                                                sheetDragY = (sheetDragY + dy).coerceAtLeast(0f)
                                            }
                                        },
                                    contentAlignment = Alignment.TopCenter
                                ) {
                                    Column {
                                        Box(
                                            modifier = Modifier
                                                .align(Alignment.CenterHorizontally)
                                                .padding(top = 10.dp, bottom = 4.dp)
                                                .size(width = 36.dp, height = 4.dp)
                                                .clip(RoundedCornerShape(2.dp))
                                                .background(MaterialTheme.colorScheme.outline.copy(alpha = 0.6f))
                                        )
                                        AssistantHeader(
                                            stateLabel = stateLabel,
                                            live = isRecording || isStreaming,
                                            sessionId = if (viewModel.chatController.isThreadMode) "" else currentSessionId,
                                            onNewSession = if (viewModel.chatController.isThreadMode) null else ({ viewModel.startNewSession(saveOld = true) }),
                                            onNewTopic = newTopic,
                                            newTopicEnabled = !clearing && !isStreaming,
                                            onMinimize = { minimize() },
                                            onMoveToApp = onMoveToApp,
                                            onDismiss = onDismiss,
                                            onCopySession = {
                                                if (currentSessionId.isNotEmpty()) {
                                                    clipboardManager.setText(AnnotatedString(currentSessionId))
                                                    Toast.makeText(context, "Session ID copied!", Toast.LENGTH_SHORT).show()
                                                }
                                            }
                                        )
                                    }
                                }

                                ServerStatusBanner(
                                    status = serverStatus,
                                    onRetry = { viewModel.recheckServer() },
                                    modifier = Modifier
                                        .align(Alignment.CenterHorizontally)
                                        .consumeWindowInsets(WindowInsets.statusBars)
                                )

                                if (!hasUserTurn && messages.none { it.toolCall != null }) {
                                    AssistantStage(isRecording = isRecording)
                                }

                                // Transcript
                                LazyColumn(
                                    state = listState,
                                    modifier = Modifier
                                        .weight(1f, fill = false)
                                        .fillMaxWidth(),
                                    contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 8.dp),
                                    verticalArrangement = Arrangement.spacedBy(18.dp)
                                ) {
                                    if (remembersHidden) {
                                        item(key = "remembers_hidden") {
                                            io.github.ronganam.dana.ui.screens.chat.MemoryLine(
                                                if (messages.any { !it.isThinking }) "Dana also remembers earlier messages" else "Dana remembers the last conversation",
                                                modifier = Modifier.padding(horizontal = 20.dp).animateItemPlacement()
                                            )
                                        }
                                    }
                                    items(rows, key = { it.key }) { row ->
                                        when (row) {
                                            is io.github.ronganam.dana.data.chat.ThreadModel.Row.MemoryLine ->
                                                io.github.ronganam.dana.ui.screens.chat.MemoryLine(
                                                    row.label,
                                                    modifier = Modifier.padding(horizontal = 20.dp).animateItemPlacement()
                                                )
                                            is io.github.ronganam.dana.data.chat.ThreadModel.Row.Message -> {
                                                val msg = row.msg
                                                val faded = if (row.forgotten) Modifier.alpha(io.github.ronganam.dana.ui.screens.chat.FORGOTTEN_ALPHA) else Modifier
                                                if (msg.toolCall != null) {
                                                    ToolCallCard(
                                                        messageId = msg.id,
                                                        entry = msg.toolCall,
                                                        onToggleCollapse = { id -> viewModel.toggleToolCallCollapse(id) },
                                                        onApprove = { approved -> viewModel.approveAction(approved) },
                                                        modifier = Modifier
                                                            .fillMaxWidth()
                                                            .padding(horizontal = 20.dp)
                                                            .animateItemPlacement()
                                                            .then(faded)
                                                    )
                                                } else {
                                                    AssistantTurn(
                                                        msg = msg,
                                                        context = context,
                                                        clipboard = clipboardManager,
                                                        tapToSpeech = tapToSpeech,
                                                        serverUrl = viewModel.serverConfig.url,
                                                        apiToken = viewModel.serverConfig.token,
                                                        modifier = Modifier.animateItemPlacement().then(faded)
                                                    )
                                                }
                                            }
                                            is io.github.ronganam.dana.data.chat.ThreadModel.Row.Separator -> {}
                                        }
                                    }

                                    // Undo under the reply, as in the thread.
                                    if (undos.isNotEmpty()) {
                                        item(key = "undos") {
                                            io.github.ronganam.dana.ui.shell.UndoRows(
                                                undos,
                                                onUndo = { viewModel.chatController.undo(it) },
                                                modifier = Modifier.padding(horizontal = 24.dp).animateItemPlacement()
                                            )
                                        }
                                    }
                                    // Continue where there is room: the same conversation in the app.
                                    if (hasUserTurn && !isStreaming && onMoveToApp != null) {
                                        item(key = "open_thread") {
                                            InkButton(
                                                "Open the thread in Dana →",
                                                emphasis = InkEmphasis.BARE,
                                                onClick = onMoveToApp,
                                                modifier = Modifier.padding(horizontal = 20.dp).heightIn(min = 44.dp)
                                            )
                                        }
                                    }

                                    selectionRequest?.let { sel ->
                                        item(key = "selection_request_card") {
                                            SelectionCard(
                                                request = sel,
                                                onSelect = { indices -> viewModel.sendSelection(indices) },
                                                onCancel = { viewModel.cancelSelection() },
                                                modifier = Modifier
                                                    .padding(horizontal = 20.dp)
                                                    .animateItemPlacement()
                                            )
                                        }
                                    }
                                }

                                // Quick starters, only before the first exchange
                                if (!hasUserTurn && !isRecording && inputText.isBlank() && pendingImageUri == null) {
                                    QuickActionRow(
                                        actions = DefaultQuickActions,
                                        onAction = { action ->
                                            when {
                                                action.prefill != null -> inputText = action.prefill
                                                action.send != null -> viewModel.sendChatMessage(action.send)
                                            }
                                        },
                                        modifier = Modifier.padding(top = 8.dp, bottom = 10.dp)
                                    )
                                } else {
                                    androidx.compose.foundation.layout.Spacer(Modifier.size(8.dp))
                                }

                                AssistantDock(
                                    inputText = inputText,
                                    onInputTextChange = { inputText = it },
                                    mode = mode,
                                    pendingImageUri = pendingImageUri,
                                    onSend = {
                                        if (inputText.isNotBlank() || pendingImageUri != null) {
                                            if (viewModel.sendChatMessage(inputText)) inputText = ""
                                        }
                                    },
                                    onVoiceToggle = toggleVoice,
                                    onPickImage = {
                                        imagePickerLauncher.launch(
                                            PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)
                                        )
                                    },
                                    onClearImage = { viewModel.clearPendingImage() },
                                    modifier = Modifier.padding(bottom = 14.dp),
                                    leading = if (screenShare != null && screenState != null) {
                                        { ScreenDockControl(screenState, screenShare) }
                                    } else null,
                                    above = if (screenShare != null && screenState != null) {
                                        {
                                            ScreenStatusLine(
                                                state = screenState,
                                                onRemove = { screenShare.remove() },
                                                onOpenSettings = onOpenAssistSettings
                                            )
                                        }
                                    } else null
                                )
                            }
                        }
                    }
                }

                AssistantOverlayState.MINIMIZED -> {
                    val dismissCenterY = screenHeightPx - with(density) { 60.dp.toPx() }
                    val centerX = screenWidthPx / 2
                    val bubbleCenterX = bubbleAnimX.value + bubbleSizePx / 2
                    val bubbleCenterY = bubbleAnimY.value + bubbleSizePx / 2
                    val distToDismiss = kotlin.math.hypot(bubbleCenterX - centerX, bubbleCenterY - dismissCenterY)
                    val isOverDismissTarget = isBubbleDragging && (
                            distToDismiss < with(density) { 100.dp.toPx() } ||
                            (bubbleCenterY > screenHeightPx - with(density) { 130.dp.toPx() } && kotlin.math.abs(bubbleCenterX - centerX) < with(density) { 100.dp.toPx() })
                    )

                    Box(modifier = Modifier.fillMaxSize()) {
                        // Dismiss target (shown while dragging the bubble)
                        AnimatedVisibility(
                            visible = isBubbleDragging,
                            enter = fadeIn(animationSpec = tween(150)) + scaleIn(initialScale = 0.8f),
                            exit = fadeOut(animationSpec = tween(150)) + scaleOut(targetScale = 0.8f),
                            modifier = Modifier
                                .align(Alignment.BottomCenter)
                                .padding(bottom = 28.dp)
                        ) {
                            val danger = MaterialTheme.colorScheme.error
                            Surface(
                                shape = CircleShape,
                                color = if (isOverDismissTarget) danger else MaterialTheme.colorScheme.surfaceContainerHigh,
                                border = BorderStroke(
                                    if (isOverDismissTarget) 2.dp else 1.dp,
                                    if (isOverDismissTarget) MaterialTheme.colorScheme.onError else MaterialTheme.colorScheme.outline
                                ),
                                modifier = Modifier
                                    .size(if (isOverDismissTarget) 72.dp else 56.dp)
                                    .shadow(12.dp, CircleShape, spotColor = if (isOverDismissTarget) danger else Color.Black)
                            ) {
                                Box(contentAlignment = Alignment.Center) {
                                    Icon(
                                        imageVector = Icons.Default.Close,
                                        contentDescription = "Dismiss",
                                        tint = if (isOverDismissTarget) MaterialTheme.colorScheme.onError else MaterialTheme.colorScheme.onSurfaceVariant,
                                        modifier = Modifier.size(if (isOverDismissTarget) 34.dp else 24.dp)
                                    )
                                }
                            }
                        }

                        AssistantBubble(
                            bubbleX = bubbleAnimX.value,
                            bubbleY = bubbleAnimY.value,
                            mode = when {
                                isRecording -> VoiceDialMode.LISTENING
                                isStreaming -> VoiceDialMode.THINKING
                                else -> VoiceDialMode.IDLE
                            },
                            isOverDismiss = isOverDismissTarget,
                            onTap = { overlayState = AssistantOverlayState.EXPANDED },
                            onDragStart = { isBubbleDragging = true },
                            onDrag = { dx, dy ->
                                scope.launch {
                                    bubbleAnimX.snapTo(bubbleAnimX.value + dx)
                                    bubbleAnimY.snapTo(
                                        (bubbleAnimY.value + dy).coerceIn(
                                            with(density) { 16.dp.toPx() },
                                            screenHeightPx - bubbleSizePx - with(density) { 16.dp.toPx() }
                                        )
                                    )
                                }
                            },
                            onDragEnd = {
                                val currentCenterX = bubbleAnimX.value + bubbleSizePx / 2
                                val currentCenterY = bubbleAnimY.value + bubbleSizePx / 2
                                val currentDist = kotlin.math.hypot(currentCenterX - centerX, currentCenterY - dismissCenterY)
                                val shouldDismiss = currentDist < with(density) { 100.dp.toPx() } ||
                                        (currentCenterY > screenHeightPx - with(density) { 130.dp.toPx() } && kotlin.math.abs(currentCenterX - centerX) < with(density) { 100.dp.toPx() })

                                if (shouldDismiss) {
                                    onDismiss()
                                } else {
                                    scope.launch {
                                        val leftTarget = with(density) { 16.dp.toPx() }
                                        val rightTarget = screenWidthPx - bubbleSizePx - with(density) { 16.dp.toPx() }
                                        val targetX = if (bubbleAnimX.value + bubbleSizePx / 2 < screenWidthPx / 2) leftTarget else rightTarget
                                        bubbleAnimX.animateTo(
                                            targetValue = targetX,
                                            animationSpec = spring(dampingRatio = 0.72f, stiffness = Spring.StiffnessMediumLow)
                                        )
                                    }
                                }
                                isBubbleDragging = false
                            }
                        )
                    }
                }
            }
        }
    }
}

/**
 * The phone sheet's slot at the start of the dock: the "Screen" chip, or
 * nothing when there is no screen to offer (opened from Dana itself). The
 * attach-image button isn't offered here (the session has no activity to
 * return a picked image to).
 */
@Composable
private fun ScreenDockControl(
    state: io.github.ronganam.dana.data.screen.ScreenShare<io.github.ronganam.dana.data.screen.ScreenShot>,
    controller: io.github.ronganam.dana.ui.viewmodels.ScreenShareController
) {
    if (state.availability != io.github.ronganam.dana.data.screen.ScreenShare.Availability.HIDDEN) {
        ScreenChip(state = state, onTap = { controller.tap() })
    }
}

/**
 * Minimised state: a 56dp ink disc carrying a serif italic "d", ringed by a miniature
 * voice dial while listening/working. Drag to move, snap to an edge, drop on the target to dismiss.
 */
@Composable
private fun AssistantBubble(
    bubbleX: Float,
    bubbleY: Float,
    mode: VoiceDialMode,
    isOverDismiss: Boolean = false,
    onTap: () -> Unit,
    onDragStart: () -> Unit,
    onDrag: (Float, Float) -> Unit,
    onDragEnd: () -> Unit
) {
    val bubbleSize = 64.dp
    val bubbleScale by animateFloatAsState(
        targetValue = if (isOverDismiss) 0.85f else 1.0f,
        animationSpec = spring(dampingRatio = 0.7f, stiffness = Spring.StiffnessMediumLow),
        label = "bubble_scale"
    )
    val live = mode != VoiceDialMode.IDLE

    Box(
        modifier = Modifier
            .offset { IntOffset(bubbleX.roundToInt(), bubbleY.roundToInt()) }
            .size(bubbleSize)
            .graphicsLayer {
                scaleX = bubbleScale
                scaleY = bubbleScale
            }
            .pointerInput(Unit) {
                awaitEachGesture {
                    val down = awaitFirstDown(requireUnconsumed = false)
                    var isDragStarted = false

                    val dragResult = awaitTouchSlopOrCancellation(down.id) { change, over ->
                        change.consume()
                        isDragStarted = true
                        onDragStart()
                        onDrag(over.x, over.y)
                    }

                    if (isDragStarted && dragResult != null) {
                        val pointerId = dragResult.id
                        while (true) {
                            val event = awaitPointerEvent()
                            val change = event.changes.firstOrNull { it.id == pointerId } ?: break
                            if (!change.pressed) {
                                change.consume()
                                break
                            }
                            val changeAmount = change.positionChange()
                            if (changeAmount != Offset.Zero) {
                                change.consume()
                                onDrag(changeAmount.x, changeAmount.y)
                            }
                        }
                        onDragEnd()
                    } else {
                        onTap()
                    }
                }
            },
        contentAlignment = Alignment.Center
    ) {
        if (live) {
            // Ring only: the dial's button is covered by the ink disc below, and taps pass to the gesture layer.
            VoiceDial(mode = mode, onClick = {}, size = bubbleSize, ticks = 28)
        }
        Surface(
            modifier = Modifier
                .size(if (live) 40.dp else 54.dp)
                .shadow(10.dp, CircleShape, spotColor = Color.Black.copy(alpha = 0.5f)),
            color = MaterialTheme.colorScheme.inverseSurface,
            shape = CircleShape,
            border = BorderStroke(
                if (live) 0.dp else 1.5.dp,
                MaterialTheme.colorScheme.primary
            )
        ) {
            Box(contentAlignment = Alignment.Center, modifier = Modifier.fillMaxSize()) {
                Text(
                    text = "d",
                    fontFamily = DanaSerif,
                    fontStyle = FontStyle.Italic,
                    fontSize = if (live) 24.sp else 32.sp,
                    color = MaterialTheme.colorScheme.inverseOnSurface
                )
            }
        }
    }
}
