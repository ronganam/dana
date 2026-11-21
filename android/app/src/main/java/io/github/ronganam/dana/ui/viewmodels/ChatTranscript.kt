package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.models.ToolCallEntry
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * The messages shown in the conversation and every change to them: user
 * bubbles, the typing indicator, tool-call cards, the live reply bubble of
 * each LLM step and the live transcript of an utterance. Settled changes are
 * written through [persist] at once; streaming ones through [persistSoon]
 * (coalesced by [ChatCache]).
 */
internal class ChatTranscript(
    private val persist: (List<ChatMessage>) -> Unit,
    private val persistSoon: () -> Unit
) {
    private val _messages = MutableStateFlow<List<ChatMessage>>(emptyList())
    val messages: StateFlow<List<ChatMessage>> = _messages.asStateFlow()

    /** What is shown now. */
    var value: List<ChatMessage>
        get() = _messages.value
        set(list) {
            _messages.value = list
        }

    /** Append [msg] and write the transcript. */
    fun append(msg: ChatMessage) {
        val list = _messages.value + msg
        _messages.value = list
        persist(list)
    }

    /** Append [msg] while a run streams (coalesced write). */
    fun appendSoon(msg: ChatMessage) {
        _messages.value = _messages.value + msg
        persistSoon()
    }

    fun save() = persist(_messages.value)

    fun remove(id: String) {
        _messages.value = _messages.value.filter { it.id != id }
    }

    fun contains(id: String?): Boolean = id != null && _messages.value.any { it.id == id }

    // --- The typing indicator ---

    /** One typing indicator at the end, labelled [label] ("Fast · Thinking…"). */
    fun showThinking(label: String) {
        val current = _messages.value.filter { !it.isThinking }
        _messages.value = current +
            ChatMessage(sender = ASSISTANT, message = "", isUser = false, isThinking = true, thinkingLabel = label)
    }

    fun removeThinking() {
        val current = _messages.value
        if (current.any { it.isThinking }) _messages.value = current.filter { !it.isThinking }
    }

    // --- Tool-call cards ---

    /** Create or update the card for one tool call. */
    fun upsertToolCall(toolCallId: String, name: String, transform: (ToolCallEntry) -> ToolCallEntry) {
        val exists = toolCallId.isNotEmpty() && _messages.value.any { it.toolCall?.toolCallId == toolCallId }
        if (exists) {
            updateToolCall(toolCallId, transform)
        } else {
            removeThinking()
            addToolCall(transform(ToolCallEntry(toolName = name, toolSummary = "", parameters = "", toolCallId = toolCallId)))
        }
    }

    /** Add a tool-call card, collapsing the previous expanded one. */
    private fun addToolCall(entry: ToolCallEntry) {
        val current = _messages.value.toMutableList()
        val lastToolIdx = current.indexOfLast { it.toolCall != null && !it.toolCall.isCollapsed }
        if (lastToolIdx >= 0) {
            val prev = current[lastToolIdx]
            current[lastToolIdx] = prev.copy(toolCall = prev.toolCall!!.copy(isCollapsed = true))
        }
        current.add(ChatMessage(sender = TOOL, message = "", isUser = false, toolCall = entry))
        _messages.value = current
        persistSoon()
    }

    /** Mutate a tool-call card in place (start -> end, approval granted, ...). */
    fun updateToolCall(toolCallId: String, transform: (ToolCallEntry) -> ToolCallEntry) {
        if (toolCallId.isEmpty()) return
        val current = _messages.value.toMutableList()
        val idx = current.indexOfLast { it.toolCall?.toolCallId == toolCallId }
        if (idx < 0) return
        val msg = current[idx]
        val existing = msg.toolCall ?: return
        current[idx] = msg.copy(toolCall = transform(existing))
        _messages.value = current
        persistSoon()
    }

    /** Drop the card of [toolCallId] (a selection prompt is shown as its own card). */
    fun removeToolCall(toolCallId: String) {
        if (toolCallId.isNotEmpty()) _messages.value = _messages.value.filter { it.toolCall?.toolCallId != toolCallId }
    }

    /** Clear the approval prompt on every card that is showing one. */
    fun settleApprovalCards(approved: Boolean) {
        val current = _messages.value.toMutableList()
        var changed = false
        for (i in current.indices) {
            val tc = current[i].toolCall
            if (tc != null && tc.requiresApproval) {
                current[i] = current[i].copy(
                    toolCall = tc.copy(requiresApproval = false, isRunning = approved, succeeded = if (approved) null else false)
                )
                changed = true
            }
        }
        if (changed) {
            _messages.value = current
            persistSoon()
        }
    }

    /** The newest card still asking for approval. */
    fun lastApprovalCard(): ToolCallEntry? = _messages.value.lastOrNull { it.toolCall?.requiresApproval == true }?.toolCall

    fun toggleCollapse(messageId: String) {
        val current = _messages.value.toMutableList()
        val idx = current.indexOfFirst { it.id == messageId }
        if (idx < 0) return
        val msg = current[idx]
        val tc = msg.toolCall ?: return
        current[idx] = msg.copy(toolCall = tc.copy(isCollapsed = !tc.isCollapsed))
        _messages.value = current
        persist(current)
    }

    fun anyToolBusy(): Boolean =
        _messages.value.any { it.toolCall?.let { tc -> tc.isRunning || tc.isPreparing } == true }

    // --- Live reply bubbles (one per LLM step of the run, [bubbles]: step -> message id) ---

    /** Mutate (creating on first use) the streaming reply bubble of one LLM step. */
    fun updateStepBubble(bubbles: MutableMap<Int, String>, iteration: Int, transform: (ChatMessage) -> ChatMessage) {
        val current = _messages.value.filter { !it.isThinking }.toMutableList()
        val existingId = bubbles[iteration]
        val idx = if (existingId != null) current.indexOfFirst { it.id == existingId } else -1
        if (idx >= 0) {
            current[idx] = transform(current[idx])
        } else {
            val bubble = ChatMessage(sender = ASSISTANT, message = "", isUser = false, isStreaming = true)
            bubbles[iteration] = bubble.id
            current.add(transform(bubble))
        }
        _messages.value = current
        persistSoon()
    }

    fun dropStepBubble(bubbles: MutableMap<Int, String>, iteration: Int) {
        val id = bubbles.remove(iteration) ?: return
        remove(id)
    }

    /** Stop the blinking/streaming state on the bubbles [ids]. */
    fun settle(ids: Collection<String>) {
        if (ids.isEmpty()) return
        val current = _messages.value
        if (current.none { it.id in ids && it.isStreaming }) return
        _messages.value = current.map { if (it.id in ids && it.isStreaming) it.copy(isStreaming = false) else it }
    }

    /**
     * The run's final reply: authoritative over the last step's streamed
     * bubble (a delta may have gone missing) unless a tool card came after
     * it, in which case it is a new message.
     */
    fun finalReply(bubbles: MutableMap<Int, String>, content: String, shortcut: Boolean) {
        removeThinking()
        val lastStep = bubbles.keys.maxOrNull()
        val bubbleId = lastStep?.let { bubbles[it] }
        val list = _messages.value
        val bubbleIdx = list.indexOfFirst { it.id == bubbleId }
        val toolAfter = bubbleIdx >= 0 && list.drop(bubbleIdx + 1).any { it.toolCall != null }
        if (lastStep != null && bubbleIdx >= 0 && !toolAfter) {
            updateStepBubble(bubbles, lastStep) { it.copy(message = content.ifEmpty { it.message }, isStreaming = false, shortcut = shortcut) }
        } else if (content.isNotEmpty()) {
            append(ChatMessage(sender = ASSISTANT, message = content, isUser = false, shortcut = shortcut))
        }
        settle(bubbles.values.toSet())
    }

    // --- The live transcript of an utterance ---

    /** Show [text] in the live bubble [id] (creating it when gone); returns the bubble's id. */
    fun showLive(id: String?, text: String, device: String): String {
        val current = _messages.value
        if (id != null && current.any { it.id == id }) {
            _messages.value = current.map { if (it.id == id) it.copy(message = text) else it }
            return id
        }
        val bubble = ChatMessage(
            sender = YOU, message = text, isUser = true, isStreaming = true,
            timestamp = System.currentTimeMillis(), device = device
        )
        _messages.value = current + bubble
        return bubble.id
    }

    /**
     * Turn the bubble [id] into the settled user message [settled] (keeping
     * the bubble's id), or add [settled] when that bubble is gone. Returns the
     * id shown and writes the transcript.
     */
    fun settleUser(id: String?, settled: ChatMessage): String {
        val current = _messages.value
        val shownId = if (id != null && current.any { it.id == id }) {
            _messages.value = current.map { if (it.id == id) settled.copy(id = id) else it }
            id
        } else {
            _messages.value = current + settled
            settled.id
        }
        persist(_messages.value)
        return shownId
    }

    companion object {
        /** Senders as the cache and the server's history spell them. */
        const val ASSISTANT = "Assistant"
        const val YOU = "You"
        const val TOOL = "Tool"
    }
}
