package io.github.ronganam.dana.ui.shell

import io.github.ronganam.dana.ui.components.TailArrivals
import io.github.ronganam.dana.ui.components.riseIn
import io.github.ronganam.dana.data.network.pretty
import kotlinx.serialization.json.JsonPrimitive
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.RestartAlt
import androidx.compose.material3.IconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.chat.BackgroundRuns
import io.github.ronganam.dana.data.chat.ThreadModel
import io.github.ronganam.dana.data.chat.UndoItem
import io.github.ronganam.dana.ui.components.ContextAttachmentCard
import io.github.ronganam.dana.ui.components.InkChoice
import io.github.ronganam.dana.ui.components.IterationPromptCard
import io.github.ronganam.dana.ui.components.ModelPickerChip
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.components.SelectionCard
import io.github.ronganam.dana.data.models.ToolCallEntry
import io.github.ronganam.dana.data.chat.ThreadItem
import io.github.ronganam.dana.ui.screens.chat.ApprovalCard
import io.github.ronganam.dana.ui.screens.chat.ToolRunCard
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.tabular
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Edit
import io.github.ronganam.dana.ui.screens.chat.ChatBubble
import io.github.ronganam.dana.ui.screens.chat.FORGOTTEN_ALPHA
import io.github.ronganam.dana.ui.screens.chat.MemoryLine
import io.github.ronganam.dana.ui.screens.chat.rememberMemoryNow
import io.github.ronganam.dana.ui.screens.chat.rememberTts
import io.github.ronganam.dana.ui.screens.common.byScript
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.ScreenRef
import java.time.ZoneId

/**
 * The conversation with Dana — the one thread every device shares — as a
 * sheet (cover / unfolded portrait, with [onClose]) or the permanent right
 * pane (unfolded landscape). It renders
 * [io.github.ronganam.dana.ui.viewmodels.ChatController]: quiet separators where
 * a new conversation started ("13:32 · watch"), streamed replies, tool cards
 * (approvals live inside them), the latest reply's Undo buttons, a mono
 * caption on replies from a learned phrase, selection and step-limit prompts.
 * Older messages load when scrolled to the top. On the cover the item the
 * thread was opened about sits at the top with a × to drop it. Typing and
 * the mic are in the ask bar, not here. "Runs" lists the conversations a
 * watch's AI agent had on its own ([BackgroundRunsDialog]); one opens as a
 * visit with its title on the "Back to the thread" bar.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun ThreadPane(viewModel: MainViewModel, onClose: (() -> Unit)?, modifier: Modifier = Modifier) {
    val chat = viewModel.chatController
    val shell = viewModel.shellController
    val messages by viewModel.chatController.chatMessages.collectAsState()
    val sessionContext by viewModel.chatController.sessionContext.collectAsState()
    val selectionRequest by viewModel.chatController.selectionRequest.collectAsState()
    val pendingExtension by viewModel.chatController.pendingExtension.collectAsState()
    val statusText by viewModel.chatController.chatStatusText.collectAsState()
    val chatModels by viewModel.chatController.models.collectAsState()
    val chatModel by viewModel.chatController.chatModel.collectAsState()
    val gapMinutes by chat.gapMinutes.collectAsState()
    val hasOlder by chat.hasOlder.collectAsState()
    val loadingOlder by chat.loadingOlder.collectAsState()
    val undos by chat.undos.collectAsState()
    val inThread by chat.inThread.collectAsState()
    val memory by chat.memory.collectAsState()
    val clearing by chat.clearing.collectAsState()
    val backgroundRuns by chat.backgroundRuns.collectAsState()
    val currentSession by chat.currentSessionId.collectAsState()
    var showRuns by remember { mutableStateOf(false) }
    val memoryNow = rememberMemoryNow(memory)
    val pinned by shell.pinned.collectAsState()
    val context = LocalContext.current
    val tts = rememberTts()
    val listState = rememberLazyListState()
    // Bubbles may run wider only in the unfolded-portrait sheet.
    val wide = LocalShellLayout.current == ShellLayout.SPLIT
    val stacked = LocalShellLayout.current == ShellLayout.STACKED
    val shownMemory = if (inThread) memory else ThreadModel.Memory.Unknown
    val rows = remember(messages, gapMinutes, shownMemory, memoryNow) {
        ThreadModel.rows(messages, gapMinutes, memoryNow, ZoneId.systemDefault(), shownMemory)
    }
    // Tool calls joined into their turn's card, a waiting one in the warm card.
    val threadItems = remember(rows) { ThreadItem.of(rows) }
    // Messages that just arrived at the end rise in (not the ones there on opening, not older pages).
    val arrivals = remember { TailArrivals() }
    val fresh = remember(threadItems) { arrivals.fresh(threadItems.map { it.key }) }

    // Follow the conversation: jump to a new last message (not when older ones are
    // added on top), keep up with a streaming reply unless the user scrolled up to read.
    val lastId = messages.lastOrNull()?.id
    // The memory line at the bottom ("New topic") counts as a new last row.
    val lastRowKey = rows.lastOrNull()?.key
    LaunchedEffect(lastId, lastRowKey, undos.size) {
        if (lastId != null) listState.animateScrollToItem(listState.layoutInfo.totalItemsCount.coerceAtLeast(1) - 1)
    }
    val streamingLength = messages.lastOrNull()?.takeIf { it.isStreaming }?.let { it.message.length + it.reasoning.length } ?: -1
    LaunchedEffect(streamingLength) {
        if (streamingLength < 0) return@LaunchedEffect
        val info = listState.layoutInfo
        val lastVisible = info.visibleItemsInfo.lastOrNull()?.index ?: return@LaunchedEffect
        if (lastVisible >= info.totalItemsCount - 2) listState.scrollBy(10_000f)
    }
    val pendingApproval by viewModel.chatController.pendingApproval.collectAsState()
    LaunchedEffect(selectionRequest, pendingExtension, pendingApproval) {
        if ((selectionRequest != null || pendingExtension != null || pendingApproval != null) && listState.layoutInfo.totalItemsCount > 0) {
            kotlinx.coroutines.delay(150)
            listState.animateScrollToItem(listState.layoutInfo.totalItemsCount - 1)
        }
    }
    // Older pages: when the top of the list comes into view.
    val atTop by remember { derivedStateOf { listState.firstVisibleItemIndex <= 1 && listState.layoutInfo.totalItemsCount > 0 } }
    LaunchedEffect(atTop, hasOlder, loadingOlder) {
        if (atTop && hasOlder && !loadingOlder && listState.isScrollInProgress.not()) chat.loadOlder()
    }

    Column(modifier.fillMaxSize()) {
        // The header: ← (as a page or sheet) · Dana · the model, or what Dana is doing; Runs and New topic at the end.
        val c = circuitColors
        Row(
            Modifier.fillMaxWidth().padding(start = if (onClose != null) 4.dp else 16.dp, end = 4.dp, top = 4.dp, bottom = 4.dp).heightIn(min = 56.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            if (onClose != null) IconButton(onClick = onClose, modifier = Modifier.size(48.dp)) {
                Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Close the conversation", tint = c.ink)
            }
            Text(
                if (onClose != null) "Dana" else "Conversation",
                style = MaterialTheme.typography.titleLarge,
                color = c.ink,
                maxLines = 1,
                modifier = Modifier.padding(start = if (onClose != null) 4.dp else 0.dp, end = 12.dp)
            )
            if (statusText.isNotEmpty()) {
                Text(statusText, style = MaterialTheme.typography.bodyMedium, color = c.accent, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            } else {
                if (chatModels.isNotEmpty()) ModelPickerChip(models = chatModels, selectedId = chatModel, onSelect = { viewModel.chatController.setChatModel(it) })
                Spacer(Modifier.weight(1f))
            }
            // A watch's agent runs have their own conversations: listed here, opened as a visit.
            if (inThread && chat.isThreadMode && backgroundRuns.isNotEmpty()) {
                TextButton(onClick = { chat.fetchBackgroundRuns(); showRuns = true }, modifier = Modifier.heightIn(min = 48.dp)) {
                    Text("Runs", style = MaterialTheme.typography.labelLarge, color = c.accent)
                }
            }
            if (inThread && chat.isThreadMode && memory.remembersAt(memoryNow)) {
                NewTopicButton(enabled = !clearing && statusText.isEmpty(), onClick = { chat.clearThread() })
            }
        }
        if (!inThread && chat.isThreadMode) {
            val label = if (BackgroundRuns.isBackgroundRun(currentSession)) {
                backgroundRuns.firstOrNull { it.id == currentSession }?.title?.takeIf { it.isNotBlank() } ?: "Background run"
            } else "Another conversation"
            OtherConversationBar(label, onBack = { chat.returnToThread() })
        }
        if (showRuns) {
            BackgroundRunsDialog(
                runs = backgroundRuns,
                onOpen = { id -> showRuns = false; viewModel.showConversation(id) },
                onDismiss = { showRuns = false }
            )
        }
        // The cover screen: what the thread was opened about (unfolded it shows above the ask bar).
        val pin = pinned
        if (stacked && pin != null) OnScreenLine(pin, onDrop = { shell.dropScreen() }, modifier = Modifier.padding(start = 16.dp, end = 4.dp, top = 6.dp))
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
            contentPadding = PaddingValues(top = 10.dp, bottom = 16.dp)
        ) {
            if (hasOlder || loadingOlder) {
                item(key = "older") {
                    MetaLabel(
                        if (loadingOlder) "Loading earlier…" else "Earlier ↑",
                        modifier = Modifier.fillMaxWidth().clickable { chat.loadOlder() }.padding(vertical = 8.dp)
                    )
                }
            }
            val ctxCard = sessionContext
            if (ctxCard != null) {
                item(key = "context_card") {
                    ContextAttachmentCard(
                        context = ctxCard,
                        onPrompt = { prompt -> viewModel.chatController.sendChatMessage(prompt) },
                        onOpenSource = ctxCard.callId?.let { cid -> { io.github.ronganam.dana.CallDebriefActivity.open(context, cid) } },
                        showPrompts = messages.none { it.isUser }
                    )
                }
            } else if (messages.isEmpty() && pendingApproval == null) {
                item(key = "empty") {
                    Column(Modifier.padding(top = 24.dp, bottom = 8.dp)) {
                        Text(
                            "Nothing said yet.",
                            style = MaterialTheme.typography.headlineSmall
                        )
                        Text(
                            "Type or speak below. The watch, the TV and the Mac talk here too.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }
            items(threadItems, key = { it.key }) { item ->
                val anim = Modifier.animateItem(fadeInSpec = null, fadeOutSpec = null)
                when (item) {
                    is ThreadItem.Plain -> when (val row = item.row) {
                        is ThreadModel.Row.Separator -> QuietSeparator(row.label, anim)
                        is ThreadModel.Row.MemoryLine -> MemoryLine(row.label, anim)
                        is ThreadModel.Row.Message -> {
                            val msg = row.msg
                            val faded = if (row.forgotten) Modifier.alpha(FORGOTTEN_ALPHA) else Modifier
                            Column(anim.riseIn(row.key in fresh).then(faded)) {
                                ChatBubble(msg = msg, tts = tts, wide = wide, serverUrl = viewModel.serverConfig.url, apiToken = viewModel.serverConfig.token)
                                if (msg.shortcut && !msg.isUser) LearnedPhraseNote()
                            }
                        }
                    }
                    // The steps of one turn on their line, Dana's answer under them.
                    is ThreadItem.Tools -> ToolRunCard(
                        item,
                        modifier = anim.riseIn(item.key in fresh).then(if (item.forgotten) Modifier.alpha(FORGOTTEN_ALPHA) else Modifier),
                        reply = item.reply?.let { r ->
                            {
                                Column {
                                    ChatBubble(msg = r, tts = tts, wide = wide, serverUrl = viewModel.serverConfig.url, apiToken = viewModel.serverConfig.token, bare = true)
                                    if (r.shortcut) LearnedPhraseNote()
                                }
                            }
                        }
                    )
                    is ThreadItem.Approval -> ApprovalCard(
                        item.entry,
                        onAnswer = { approved -> viewModel.chatController.approveAction(approved) },
                        modifier = anim.riseIn(item.key in fresh)
                    )
                }
            }
            // A tool that asks first but has no card in the loaded messages (an older
            // conversation opened from Now, or the card is on an earlier page): ask here.
            val orphanApproval = pendingApproval?.takeIf { p -> messages.none { it.toolCall?.requiresApproval == true } }
            orphanApproval?.let { p ->
                item(key = "pending_approval_card") {
                    ApprovalCard(
                        ToolCallEntry(
                            toolName = p.toolName,
                            toolSummary = "",
                            parameters = prettyArgs(p.toolArgs),
                            requiresApproval = true,
                            toolCallId = p.toolCallId,
                            toolArgs = p.toolArgs,
                            approvalReason = p.reason,
                            approvalCard = p.card
                        ),
                        onAnswer = { approved -> viewModel.chatController.approveAction(approved) },
                        modifier = Modifier.animateItem(fadeInSpec = null, fadeOutSpec = null)
                    )
                }
            }
            if (undos.isNotEmpty()) {
                item(key = "undos") { UndoRows(undos, onUndo = { chat.undo(it) }, modifier = Modifier.animateItem(fadeInSpec = null, fadeOutSpec = null)) }
            }
            selectionRequest?.let { sel ->
                item(key = "selection_request_card") {
                    SelectionCard(
                        request = sel,
                        onSelect = { indices -> viewModel.chatController.sendSelection(indices) },
                        onCancel = { viewModel.chatController.cancelSelection() },
                        modifier = Modifier.animateItem(fadeInSpec = null, fadeOutSpec = null)
                    )
                }
            }
            pendingExtension?.let { ext ->
                item(key = "pending_extension_card") {
                    IterationPromptCard(
                        summary = ext.summary,
                        onExtend = { viewModel.chatController.extendIterations() },
                        onStop = { viewModel.chatController.declineIterations() },
                        modifier = Modifier.animateItem(fadeInSpec = null, fadeOutSpec = null)
                    )
                }
            }
        }
    }
}

private fun prettyArgs(raw: String): String = JsonPrimitive(raw).pretty()

/** Under a reply a learned phrase answered (no model). */
@Composable
internal fun LearnedPhraseNote() {
    Text(
        "Learned phrase, no model",
        style = MaterialTheme.typography.bodySmall,
        color = circuitColors.mute,
        modifier = Modifier.padding(start = 4.dp, top = 4.dp)
    )
}

/**
 * "New topic": Dana forgets the conversation so far (the messages stay).
 * Shown only while Dana remembers something.
 */
@Composable
internal fun NewTopicButton(enabled: Boolean, onClick: () -> Unit) {
    // A pencil (start fresh), as in the design's header.
    IconButton(onClick = onClick, enabled = enabled, modifier = Modifier.size(48.dp)) {
        Icon(
            Icons.Outlined.Edit,
            contentDescription = "New topic — Dana forgets this conversation",
            tint = circuitColors.inkSoft.copy(alpha = if (enabled) 1f else 0.4f),
            modifier = Modifier.size(22.dp)
        )
    }
}

/** "13:32 · watch", centred in the mute: a new conversation started here. */
@Composable
private fun QuietSeparator(label: String, modifier: Modifier = Modifier) {
    Text(
        label,
        style = MaterialTheme.typography.labelMedium.tabular(),
        color = circuitColors.mute,
        textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        modifier = modifier.fillMaxWidth().padding(top = 12.dp, bottom = 4.dp)
    )
}

/**
 * Under the latest reply: an Undo per reversible thing Dana did (label from
 * the server), then what pressing it did.
 */
@Composable
internal fun UndoRows(items: List<UndoItem>, onUndo: (String) -> Unit, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth().padding(start = 12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        items.forEach { u ->
            when (u.status) {
                UndoItem.Status.READY, UndoItem.Status.BUSY, UndoItem.Status.FAILED -> Row(verticalAlignment = Alignment.CenterVertically) {
                    InkChoice(
                        text = if (u.status == UndoItem.Status.BUSY) "Undoing…" else if (u.status == UndoItem.Status.FAILED) "Try again" else "Undo",
                        selected = u.status != UndoItem.Status.FAILED,
                        onClick = { if (u.status != UndoItem.Status.BUSY) onUndo(u.id) }
                    )
                    Column(Modifier.padding(start = 10.dp).weight(1f)) {
                        Text(
                            u.label,
                            style = MaterialTheme.typography.bodySmall.byScript(u.label),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis
                        )
                        u.error?.let {
                            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                        }
                    }
                }
                UndoItem.Status.DONE -> QuietNote("Undone.", u.label)
                UndoItem.Status.TOO_LATE -> QuietNote("Too late to undo that.", "Undo lasts 15 minutes · ${u.label}")
            }
        }
    }
}

@Composable
private fun QuietNote(title: String, detail: String) {
    Column {
        Text(title, style = MaterialTheme.typography.bodyMedium)
        Text(
            detail,
            style = MaterialTheme.typography.bodySmall.byScript(detail),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(top = 2.dp)
        )
    }
}

/** "Another conversation · Back to the thread" (an approval opened from Now in a Telegram chat). */
@Composable
private fun OtherConversationBar(label: String, onBack: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerHigh).padding(start = 18.dp, end = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            label,
            style = MaterialTheme.typography.labelLarge.copy(textDirection = TextDirection.Content),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f)
        )
        TextButton(onClick = onBack) { Text("Back to the thread") }
    }
}

/**
 * "On screen · Take the car to the garage ×": what Dana sees with the next
 * message. The title keeps its own direction; the × drops it.
 */
@Composable
fun OnScreenLine(ref: ScreenRef, onDrop: () -> Unit, modifier: Modifier = Modifier) {
    val accent = MaterialTheme.colorScheme.primary
    val title = ref.title.ifBlank { io.github.ronganam.dana.ui.viewmodels.ScreenContext.kindLabel(ref.kind) }
    Row(
        modifier
            .fillMaxWidth()
            .drawBehind { drawLine(accent, Offset(0f, 0f), Offset(0f, size.height), 2.dp.toPx()) }
            .padding(start = 10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        MetaLabel("On screen ·", modifier = Modifier.padding(end = 6.dp))
        Text(
            title,
            style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f)
        )
        Box(
            Modifier.size(40.dp).clickable(onClick = onDrop),
            contentAlignment = Alignment.Center
        ) {
            Icon(Icons.Filled.Close, contentDescription = "Don't show this to Dana", tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(16.dp))
        }
    }
}
