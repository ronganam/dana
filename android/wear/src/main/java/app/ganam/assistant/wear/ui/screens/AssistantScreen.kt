package app.ganam.assistant.wear.ui.screens

import app.ganam.assistant.wear.ui.components.rotaryScroll

import app.ganam.assistant.wear.ui.theme.GanamWear

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.focusable
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.rotary.onRotaryScrollEvent
import androidx.compose.runtime.remember
import kotlinx.coroutines.launch
import androidx.wear.compose.foundation.ExperimentalWearFoundationApi
import androidx.wear.compose.foundation.rememberActiveFocusRequester
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.items
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.material.*
import app.ganam.assistant.wear.ui.WearViewModel
import app.ganam.assistant.wear.ui.components.WearMarkdownText
import app.ganam.assistant.wear.perf.WearPerf

@OptIn(ExperimentalWearFoundationApi::class)
@Composable
fun AssistantScreen(
    viewModel: WearViewModel,
    active: Boolean = true
) {
    val status by viewModel.statusText.collectAsState()
    val isRecording by viewModel.isRecording.collectAsState()
    val chatMessages by viewModel.chatMessages.collectAsState()
    val pendingApproval by viewModel.pendingApproval.collectAsState()
    val pendingSelection by viewModel.pendingSelection.collectAsState()
    val pendingExtension by viewModel.pendingExtension.collectAsState()

    val micBgColor by animateColorAsState(
        targetValue = if (isRecording) GanamWear.Danger else MaterialTheme.colors.primary,
        animationSpec = tween(300),
        label = "micBgColor"
    )

    if (isRecording) {
        // DanaPerf: the first frame that shows the listening state.
        LaunchedEffect(Unit) {
            withFrameNanos { }
            WearPerf.mark("listening shown")
        }
    }

    val listState = rememberScalingLazyListState()
    val focusRequester = remember { androidx.compose.ui.focus.FocusRequester() }
    val coroutineScope = rememberCoroutineScope()

    LaunchedEffect(chatMessages.size) {
        if (chatMessages.isNotEmpty()) {
            try {
                listState.animateScrollToItem(chatMessages.size - 1)
            } catch (e: Exception) {
                // ignore scroll bounce
            }
        }
    }

    Scaffold(
        timeText = { TimeText() },
        positionIndicator = {
            PositionIndicator(scalingLazyListState = listState)
        }
    ) {
        Box(modifier = Modifier.fillMaxSize()) {
            ScalingLazyColumn(
                state = listState,
                modifier = Modifier
                    .fillMaxSize()
                    .rotaryScroll(listState, focusRequester, active = active),
                contentPadding = PaddingValues(top = 28.dp, bottom = 72.dp, start = 12.dp, end = 12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                if (status != "Ready") {
                    item {
                        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                            if (status == "Processing voice..." || status == "Thinking...") {
                                CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                                Spacer(modifier = Modifier.width(4.dp))
                            }
                            Text(
                                text = status,
                                style = MaterialTheme.typography.caption2,
                                color = MaterialTheme.colors.secondary,
                                textAlign = TextAlign.Center,
                                modifier = Modifier.weight(1f) // Take remaining space
                            )
                        }
                    }
                }

                items(chatMessages, key = { it.id }) { msg ->
                    val isUser = msg.isUser
                    val isTool = msg.isToolCall
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start
                    ) {
                        Box(
                            modifier = Modifier
                                .widthIn(max = 160.dp)
                                .clip(
                                    RoundedCornerShape(
                                        topStart = 14.dp,
                                        topEnd = 14.dp,
                                        bottomStart = if (isUser) 14.dp else if (isTool) 4.dp else 4.dp,
                                        bottomEnd = if (isUser) 4.dp else if (isTool) 4.dp else 14.dp
                                    )
                                )
                                .background(
                                    if (isUser) MaterialTheme.colors.primary.copy(alpha = 0.85f)
                                    else if (isTool) GanamWear.Success // Greenish tint for tool calls
                                    else GanamWear.SurfaceHigh
                                )
                                .padding(horizontal = 10.dp, vertical = 8.dp)
                        ) {
                            if (isUser) {
                                Text(
                                    text = msg.text,
                                    style = MaterialTheme.typography.body2,
                                    color = Color.White
                                )
                            } else if (isTool) {
                                Column { // Use Column for tool name and args
                                    Text(
                                        text = msg.toolName ?: "Tool", // Display tool name
                                        style = MaterialTheme.typography.caption1.copy(fontWeight = FontWeight.Bold), // Smaller bold font
                                        color = Color.White,
                                        maxLines = 1, overflow = TextOverflow.Ellipsis
                                    )
                                    Text(
                                        text = msg.text, // Display summary or args
                                        style = MaterialTheme.typography.caption2, // Even smaller font
                                        color = Color.White.copy(alpha = 0.8f),
                                        maxLines = 2, overflow = TextOverflow.Ellipsis
                                    )
                                }
                            } else {
                                WearMarkdownText(
                                    text = msg.text,
                                    color = Color.White,
                                    style = MaterialTheme.typography.body2
                                )
                            }
                        }
                    }
                }
            }

            // Bottom interactive area: Approval Card (if any) + Fixed Mic Button
            Column(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(bottom = 6.dp, start = 12.dp, end = 12.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                val approval = pendingApproval
                if (approval != null) {
                    Card(
                        onClick = {},
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(bottom = 4.dp),
                        backgroundPainter = CardDefaults.cardBackgroundPainter(
                            startBackgroundColor = GanamWear.SurfaceHigh,
                            endBackgroundColor = GanamWear.Surface
                        )
                    ) {
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            modifier = Modifier.padding(2.dp)
                        ) {
                            Text(
                                text = "Approve Tool?",
                                style = MaterialTheme.typography.caption2,
                                color = GanamWear.Warning,
                                fontWeight = FontWeight.Bold
                            )
                            Text(
                                text = approval.summary,
                                style = MaterialTheme.typography.caption2,
                                textAlign = TextAlign.Center
                            )
                            Row(
                                horizontalArrangement = Arrangement.spacedBy(12.dp),
                                modifier = Modifier.padding(top = 4.dp)
                            ) {
                                Button(
                                    onClick = { viewModel.approveAction(false) },
                                    colors = ButtonDefaults.buttonColors(backgroundColor = GanamWear.Danger),
                                    modifier = Modifier.size(32.dp)
                                ) {
                                    Icon(Icons.Default.Close, contentDescription = "Deny", tint = Color.White)
                                }
                                Button(
                                    onClick = { viewModel.approveAction(true) },
                                    colors = ButtonDefaults.buttonColors(backgroundColor = GanamWear.Success),
                                    modifier = Modifier.size(32.dp)
                                ) {
                                    Icon(Icons.Default.Check, contentDescription = "Approve", tint = Color.White)
                                }
                            }
                        }
                    }
                }

                val selection = pendingSelection
                if (selection != null) {
                    Card(
                        onClick = {},
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(bottom = 4.dp),
                        backgroundPainter = CardDefaults.cardBackgroundPainter(
                            startBackgroundColor = GanamWear.SurfaceHigh,
                            endBackgroundColor = GanamWear.Surface
                        )
                    ) {
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            modifier = Modifier.padding(4.dp)
                        ) {
                            Text(
                                text = selection.prompt.ifBlank { "Choose an option" },
                                style = MaterialTheme.typography.caption2,
                                color = GanamWear.Warning,
                                fontWeight = FontWeight.Bold,
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis,
                                textAlign = TextAlign.Center
                            )
                            // The watch picks a single option per tap; for a
                            // multi-select that means choosing the first one.
                            selection.options.take(4).forEachIndexed { idx, option ->
                                val label = option.label.ifBlank { option.value }
                                Button(
                                    onClick = { viewModel.submitSelection(listOf(idx)) },
                                    colors = ButtonDefaults.buttonColors(
                                        backgroundColor = GanamWear.SurfaceHigh
                                    ),
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(top = 2.dp)
                                ) {
                                    Text(
                                        text = label,
                                        style = MaterialTheme.typography.caption2,
                                        color = Color.White,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis
                                    )
                                }
                            }
                        }
                    }
                }

                val extension = pendingExtension
                if (extension != null) {
                    Card(
                        onClick = {},
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(bottom = 4.dp),
                        backgroundPainter = CardDefaults.cardBackgroundPainter(
                            startBackgroundColor = GanamWear.SurfaceHigh,
                            endBackgroundColor = GanamWear.Surface
                        )
                    ) {
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            modifier = Modifier.padding(4.dp)
                        ) {
                            Text(
                                text = "Step limit reached",
                                style = MaterialTheme.typography.caption2,
                                color = GanamWear.Warning,
                                fontWeight = FontWeight.Bold
                            )
                            if (extension.summary.isNotBlank()) {
                                Text(
                                    text = extension.summary,
                                    style = MaterialTheme.typography.caption2,
                                    color = GanamWear.InkSoft,
                                    maxLines = 2,
                                    overflow = TextOverflow.Ellipsis,
                                    textAlign = TextAlign.Center
                                )
                            }
                            Row(
                                horizontalArrangement = Arrangement.spacedBy(12.dp),
                                modifier = Modifier.padding(top = 4.dp)
                            ) {
                                Button(
                                    onClick = { viewModel.declineIterations() },
                                    colors = ButtonDefaults.buttonColors(backgroundColor = GanamWear.Danger),
                                    modifier = Modifier.size(32.dp)
                                ) {
                                    Icon(Icons.Default.Close, contentDescription = "Stop here", tint = Color.White)
                                }
                                Button(
                                    onClick = { viewModel.extendIterations() },
                                    colors = ButtonDefaults.buttonColors(backgroundColor = GanamWear.Success),
                                    modifier = Modifier.size(32.dp)
                                ) {
                                    Icon(
                                        Icons.Default.Add,
                                        contentDescription = "Give more steps",
                                        tint = Color.White
                                    )
                                }
                            }
                        }
                    }
                }

                Button(
                    onClick = {
                        if (isRecording) {
                            viewModel.stopVoiceRecording()
                        } else {
                            viewModel.startVoiceRecording()
                        }
                    },
                    modifier = Modifier
                        .size(48.dp)
                        .clip(CircleShape),
                    colors = ButtonDefaults.buttonColors(
                        backgroundColor = micBgColor
                    )
                ) {
                    Icon(
                        imageVector = if (isRecording) Icons.Default.Stop else Icons.Default.Mic,
                        contentDescription = if (isRecording) "Stop" else "Speak",
                        tint = Color.White,
                        modifier = Modifier.size(26.dp)
                    )
                }
            }
        }
    }
}
