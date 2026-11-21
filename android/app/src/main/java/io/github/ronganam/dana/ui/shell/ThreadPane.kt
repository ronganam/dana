package io.github.ronganam.dana.ui.shell

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
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.chat.ThreadModel
import io.github.ronganam.dana.data.chat.UndoItem
import io.github.ronganam.dana.ui.components.ContextAttachmentCard
import io.github.ronganam.dana.ui.components.InkChoice
import io.github.ronganam.dana.ui.components.IterationPromptCard
import io.github.ronganam.dana.ui.components.ModelPickerChip
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.components.SelectionCard
import io.github.ronganam.dana.data.models.ToolCallEntry
import io.github.ronganam.dana.ui.components.ToolCallCard
import io.github.ronganam.dana.ui.screens.chat.ChatBubble
import io.github.ronganam.dana.ui.screens.chat.FORGOTTEN_ALPHA
import io.github.ronganam.dana.ui.screens.chat.MemoryLine
import io.github.ronganam.dana.ui.screens.chat.rememberMemoryNow
import io.github.ronganam.dana.ui.screens.chat.rememberTts
import io.github.ronganam.dana.ui.screens.common.byScript
import io.github.ronganam.dana.ui.theme.DanaSerif
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
 * the mic are in the ask bar, not here.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun ThreadPane(viewModel: MainViewModel, onClose: (() -> Unit)?, modifier: Modifier = Modifier) {
    val chat = viewModel.chatController
    val shell = viewModel.shellController
    val messages by viewModel.chatMessages.collectAsState()
    val sessionContext by viewModel.sessionContext.collectAsState()
    val selectionRequest by viewModel.selectionRequest.collectAsState()
    val pendingExtension by viewModel.pendingExtension.collectAsState()
    val statusText by viewModel.chatStatusText.collectAsState()
    val chatModels by viewModel.chatModels.collectAsState()
    val chatModel by viewModel.chatModel.collectAsState()
    val gapMinutes by chat.gapMinutes.collectAsState()
    val hasOlder by chat.hasOlder.collectAsState()
    val loadingOlder by chat.loadingOlder.collectAsState()
    val undos by chat.undos.collectAsState()
    val inThread by chat.inThread.collectAsState()
    val memory by chat.memory.collectAsState()
    val clearing by chat.clearing.collectAsState()
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
    val pendingApproval by viewModel.pendingApproval.collectAsState()
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
        Row(
            Modifier.fillMaxWidth().edgeRule(bottom = true).padding(start = 18.dp, end = 6.dp, top = 6.dp, bottom = 6.dp).heightIn(min = 44.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            MonoLabel("Dana", modifier = Modifier.padding(end = 10.dp))
            if (statusText.isNotEmpty()) {
                MonoLabel(statusText, color = MaterialTheme.colorScheme.primary, modifier = Modifier.weight(1f))
            } else {
                if (chatModels.isNotEmpty()) ModelPickerChip(models = chatModels, selectedId = chatModel, onSelect = { viewModel.setChatModel(it) })
                Spacer(Modifier.weight(1f))
            }
            if (inThread && chat.isThreadMode && memory.remembersAt(memoryNow)) {
                NewTopicButton(enabled = !clearing && statusText.isEmpty(), onClick = { chat.clearThread() })
            }
            if (onClose != null) TextButton(onClick = onClose) { Text("Done") }
        }
        if (!inThread && chat.isThreadMode) OtherConversationBar(onBack = { chat.returnToThread() })
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
                    MonoLabel(
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
                        onPrompt = { prompt -> viewModel.sendChatMessage(prompt) },
                        onOpenSource = ctxCard.callId?.let { cid -> { io.github.ronganam.dana.CallDebriefActivity.open(context, cid) } },
                        showPrompts = messages.none { it.isUser }
                    )
                }
            } else if (messages.isEmpty() && pendingApproval == null) {
                item(key = "empty") {
                    Column(Modifier.padding(top = 24.dp, bottom = 8.dp)) {
                        Text(
                            "Nothing said yet.",
                            style = MaterialTheme.typography.headlineSmall.copy(fontFamily = DanaSerif, fontStyle = FontStyle.Italic)
                        )
                        Text(
                            "Type or speak below. The watch, the TV and the Mac talk here too.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }
            items(rows, key = { it.key }) { row ->
                when (row) {
                    is ThreadModel.Row.Separator -> QuietSeparator(row.label, Modifier.animateItemPlacement())
                    is ThreadModel.Row.MemoryLine -> MemoryLine(row.label, Modifier.animateItemPlacement())
                    is ThreadModel.Row.Message -> {
                        val msg = row.msg
                        val faded = if (row.forgotten) Modifier.alpha(FORGOTTEN_ALPHA) else Modifier
                        if (msg.toolCall != null) {
                            ToolCallCard(
                                messageId = msg.id,
                                entry = msg.toolCall,
                                onToggleCollapse = { id -> viewModel.toggleToolCallCollapse(id) },
                                onApprove = { approved -> viewModel.approveAction(approved) },
                                modifier = Modifier.fillMaxWidth().animateItemPlacement().then(faded)
                            )
                        } else Column(Modifier.animateItemPlacement().then(faded)) {
                            ChatBubble(
                                msg = msg,
                                tts = tts,
                                wide = wide,
                                serverUrl = viewModel.getServerUrl(),
                                apiToken = viewModel.getApiToken()
                            )
                            if (msg.shortcut && !msg.isUser) {
                                MonoLabel("learned phrase · no model", modifier = Modifier.padding(start = 12.dp, top = 2.dp))
                            }
                        }
                    }
                }
            }
            // A tool that asks first but has no card in the loaded messages (an older
            // conversation opened from Now, or the card is on an earlier page): ask here.
            val orphanApproval = pendingApproval?.takeIf { p -> messages.none { it.toolCall?.requiresApproval == true } }
            orphanApproval?.let { p ->
                item(key = "pending_approval_card") {
                    ToolCallCard(
                        messageId = "pending_approval",
                        entry = ToolCallEntry(
                            toolName = p.toolName,
                            toolSummary = "",
                            parameters = prettyArgs(p.toolArgs),
                            requiresApproval = true,
                            toolCallId = p.toolCallId,
                            toolArgs = p.toolArgs
                        ),
                        onToggleCollapse = {},
                        onApprove = { approved -> viewModel.approveAction(approved) },
                        modifier = Modifier.fillMaxWidth().animateItemPlacement()
                    )
                }
            }
            if (undos.isNotEmpty()) {
                item(key = "undos") { UndoRows(undos, onUndo = { chat.undo(it) }, modifier = Modifier.animateItemPlacement()) }
            }
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
    }
}

private fun prettyArgs(raw: String): String =
    runCatching { org.json.JSONObject(raw).toString(2) }.getOrDefault(raw)

/**
 * "New topic": Dana forgets the conversation so far (the messages stay).
 * Shown only while Dana remembers something.
 */
@Composable
internal fun NewTopicButton(enabled: Boolean, onClick: () -> Unit) {
    IconButton(onClick = onClick, enabled = enabled, modifier = Modifier.size(44.dp)) {
        Icon(
            Icons.Filled.RestartAlt,
            contentDescription = "New topic — Dana forgets this conversation",
            tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = if (enabled) 1f else 0.4f),
            modifier = Modifier.size(20.dp)
        )
    }
}

/** "13:32 · watch" between two hairlines: a new conversation started here. */
@Composable
private fun QuietSeparator(label: String, modifier: Modifier = Modifier) {
    Row(modifier.fillMaxWidth().padding(top = 10.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically) {
        Rule(Modifier.weight(1f))
        MonoLabel(label, modifier = Modifier.padding(horizontal = 10.dp))
        Rule(Modifier.weight(1f))
    }
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
private fun OtherConversationBar(onBack: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceContainerHigh).padding(start = 18.dp, end = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        MonoLabel("Another conversation", modifier = Modifier.weight(1f))
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
        MonoLabel("On screen ·", modifier = Modifier.padding(end = 6.dp))
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
