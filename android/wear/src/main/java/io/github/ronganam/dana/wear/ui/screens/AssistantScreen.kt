package io.github.ronganam.dana.wear.ui.screens

import io.github.ronganam.dana.wear.ui.components.rotaryScroll

import io.github.ronganam.dana.wear.ui.theme.DanaWear

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.border
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.graphics.graphicsLayer
import io.github.ronganam.dana.wear.ui.theme.DanaMotion
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
import io.github.ronganam.dana.wear.ui.WearViewModel
import io.github.ronganam.dana.wear.ui.components.WearMarkdownText
import io.github.ronganam.dana.wear.perf.WearPerf
import io.github.ronganam.dana.wear.R
import androidx.compose.ui.res.stringResource

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
        val ready = stringResource(R.string.status_ready)
        val busy = setOf(stringResource(R.string.status_processing_voice), stringResource(R.string.status_thinking))
        Box(modifier = Modifier.fillMaxSize()) {
            ScalingLazyColumn(
                state = listState,
                modifier = Modifier
                    .fillMaxSize()
                    .rotaryScroll(listState, focusRequester, active = active),
                contentPadding = PaddingValues(top = 28.dp, bottom = 72.dp, start = 12.dp, end = 12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                if (status != ready) {
                    item {
                        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                            if (status in busy) {
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
                                    // The user's words on ember; a tool Dana used, tinted sage; Dana on the warm surface.
                                    if (isUser) DanaWear.Ember
                                    else if (isTool) DanaWear.tint(DanaWear.Sage)
                                    else DanaWear.SurfaceHigh
                                )
                                .padding(horizontal = 10.dp, vertical = 8.dp)
                        ) {
                            if (isUser) {
                                Text(
                                    text = msg.text,
                                    style = MaterialTheme.typography.body2,
                                    color = DanaWear.OnAccent
                                )
                            } else if (isTool) {
                                Column {
                                    val name = msg.toolName ?: stringResource(R.string.tool)
                                    Text(
                                        text = name,
                                        fontFamily = DanaWear.Mono,
                                        style = MaterialTheme.typography.caption2,
                                        color = DanaWear.Sage,
                                        maxLines = 1, overflow = TextOverflow.Ellipsis
                                    )
                                    if (msg.text != name) Text(
                                        text = msg.text,
                                        style = MaterialTheme.typography.caption2,
                                        color = DanaWear.InkSoft,
                                        maxLines = 2, overflow = TextOverflow.Ellipsis
                                    )
                                }
                            } else {
                                WearMarkdownText(
                                    text = msg.text,
                                    color = DanaWear.Ink,
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
                // Prompts the run waits on need the user: an ember-tinted card, sage to go ahead, rose to stop.
                val needsYou = DanaWear.tint(DanaWear.Ember).compositeOver(DanaWear.Surface)
                val yesColors = ButtonDefaults.buttonColors(backgroundColor = DanaWear.Sage)
                val noColors = ButtonDefaults.buttonColors(backgroundColor = DanaWear.tint(DanaWear.Rose))
                val approval = pendingApproval
                if (approval != null) {
                    Card(
                        onClick = {},
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(bottom = 4.dp),
                        backgroundPainter = CardDefaults.cardBackgroundPainter(
                            startBackgroundColor = needsYou,
                            endBackgroundColor = DanaWear.Surface
                        )
                    ) {
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            modifier = Modifier.padding(2.dp)
                        ) {
                            Text(
                                text = stringResource(R.string.approve_tool),
                                style = MaterialTheme.typography.caption2,
                                color = DanaWear.Ember,
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
                                    colors = noColors,
                                    modifier = Modifier.size(32.dp)
                                ) {
                                    Icon(Icons.Default.Close, contentDescription = stringResource(R.string.deny), tint = DanaWear.Rose)
                                }
                                Button(
                                    onClick = { viewModel.approveAction(true) },
                                    colors = yesColors,
                                    modifier = Modifier.size(32.dp)
                                ) {
                                    Icon(Icons.Default.Check, contentDescription = stringResource(R.string.approve), tint = DanaWear.OnAccent)
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
                            startBackgroundColor = needsYou,
                            endBackgroundColor = DanaWear.Surface
                        )
                    ) {
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            modifier = Modifier.padding(4.dp)
                        ) {
                            Text(
                                text = selection.prompt.ifBlank { stringResource(R.string.choose_an_option) },
                                style = MaterialTheme.typography.caption2,
                                color = DanaWear.Ember,
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
                                        backgroundColor = DanaWear.tint(DanaWear.Ember)
                                    ),
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(top = 2.dp)
                                ) {
                                    Text(
                                        text = label,
                                        style = MaterialTheme.typography.caption2,
                                        color = DanaWear.Ink,
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
                            startBackgroundColor = needsYou,
                            endBackgroundColor = DanaWear.Surface
                        )
                    ) {
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            modifier = Modifier.padding(4.dp)
                        ) {
                            Text(
                                text = stringResource(R.string.step_limit_reached),
                                style = MaterialTheme.typography.caption2,
                                color = DanaWear.Ember,
                                fontWeight = FontWeight.Bold
                            )
                            if (extension.summary.isNotBlank()) {
                                Text(
                                    text = extension.summary,
                                    style = MaterialTheme.typography.caption2,
                                    color = DanaWear.InkSoft,
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
                                    colors = noColors,
                                    modifier = Modifier.size(32.dp)
                                ) {
                                    Icon(Icons.Default.Close, contentDescription = stringResource(R.string.stop_here), tint = DanaWear.Rose)
                                }
                                Button(
                                    onClick = { viewModel.extendIterations() },
                                    colors = yesColors,
                                    modifier = Modifier.size(32.dp)
                                ) {
                                    Icon(
                                        Icons.Default.Add,
                                        contentDescription = stringResource(R.string.give_more_steps),
                                        tint = DanaWear.OnAccent
                                    )
                                }
                            }
                        }
                    }
                }

                Box(contentAlignment = Alignment.Center) {
                    // Only while listening, and never with motion turned off: it stops with the turn.
                    if (isRecording && DanaMotion.enabled()) ListeningPulse()
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
                        colors = ButtonDefaults.buttonColors(backgroundColor = DanaWear.Ember)
                    ) {
                        Icon(
                            imageVector = if (isRecording) Icons.Default.Stop else Icons.Default.Mic,
                            contentDescription = stringResource(if (isRecording) R.string.stop else R.string.speak),
                            tint = DanaWear.OnAccent,
                            modifier = Modifier.size(26.dp)
                        )
                    }
                }
            }
        }
    }
}

/**
 * The listening ring: an ember outline that grows from the mic and fades,
 * over and over while listening. Composed only while listening, so it stops
 * with the turn; the frame reads its value in the layer only (no recomposition).
 */
@Composable
private fun ListeningPulse() {
    val transition = rememberInfiniteTransition(label = "listening")
    val t = transition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(1400, easing = LinearEasing), RepeatMode.Restart),
        label = "pulse"
    )
    Box(
        Modifier
            .size(48.dp)
            .graphicsLayer {
                val v = t.value
                scaleX = 1f + 0.42f * v
                scaleY = 1f + 0.42f * v
                alpha = 0.6f * (1f - v)
            }
            .border(2.dp, DanaWear.Ember, CircleShape)
    )
}
