package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.data.models.PendingExtension
import io.github.ronganam.dana.data.models.SelectionRequest
import io.github.ronganam.dana.data.network.SessionState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

data class PendingApproval(
    val toolName: String,
    val toolArgs: String,
    val toolCallId: String,
    /** The call in words (server `card`), null from an older server. */
    val card: io.github.ronganam.dana.data.models.ApprovalWords? = null,
    /** Why it asks (see [io.github.ronganam.dana.data.models.ToolCallEntry.approvalReason]). */
    val reason: String = ""
)

/**
 * What the user still has to answer in the conversation: a tool call that
 * waits for approval, a choice between options, or a run that reached its
 * step limit. Set from run events and session states; the approval cards in
 * the [transcript] follow the approval prompt.
 */
internal class ChatPrompts(private val transcript: ChatTranscript) {
    private val _approval = MutableStateFlow<PendingApproval?>(null)
    val approval: StateFlow<PendingApproval?> = _approval.asStateFlow()

    private val _selection = MutableStateFlow<SelectionRequest?>(null)
    val selection: StateFlow<SelectionRequest?> = _selection.asStateFlow()

    private val _extension = MutableStateFlow<PendingExtension?>(null)
    val extension: StateFlow<PendingExtension?> = _extension.asStateFlow()

    /** Nothing to answer any more (switching sessions, stopping the run). */
    fun clear() {
        _approval.value = null
        _selection.value = null
        _extension.value = null
    }

    /** A loaded session (GET, or a POST answer): its prompts replace the shown ones. */
    fun fromSession(state: SessionState) {
        val pending = state.pendingApproval
        _approval.value = pending?.let { PendingApproval(it.toolName.orEmpty(), it.toolArgs.orEmpty(), it.toolCallId, it.card, it.reason) }
        if (pending != null && pending.toolCallId.isNotEmpty() && (pending.reason.isNotEmpty() || pending.card != null)) {
            transcript.updateToolCall(pending.toolCallId) {
                it.copy(approvalReason = pending.reason.ifEmpty { it.approvalReason }, approvalCard = pending.card ?: it.approvalCard)
            }
        }
        val selection = state.selection
        if (selection != null) _approval.value = null
        _selection.value = selection
    }

    /**
     * The `state` frame that closes a run. Live events already showed the
     * prompts; this restores one the state still has and, while the run is
     * shown streaming ([streaming]), clears the ones it no longer has.
     */
    fun fromTerminalState(state: SessionState, streaming: Boolean) {
        val status = state.status
        val pending = state.pendingApproval
        // The server stores "waiting_approval" while an approval is pending
        // (server/core/session_runner.py), so accept both spellings. Clear only when
        // the state carries no pending approval at all.
        if ((status == "pending_approval" || status == "waiting_approval") && pending != null) {
            val id = pending.toolCallId
            _approval.value = PendingApproval(
                toolName = pending.toolName ?: "Tool Execution",
                toolArgs = pending.toolArgs ?: "null",
                toolCallId = id,
                card = pending.card,
                reason = pending.reason
            )
            transcript.updateToolCall(id) {
                it.copy(
                    requiresApproval = true, isRunning = false, isCollapsed = false,
                    approvalReason = pending.reason.ifEmpty { it.approvalReason }, approvalCard = pending.card ?: it.approvalCard
                )
            }
        } else if (pending == null && streaming) {
            _approval.value = null
        }

        val selection = state.selection
        if (status == "awaiting_selection" && selection != null) {
            _selection.value = selection
        } else if (streaming) {
            _selection.value = null
        }

        if (status == "iterations_exceeded") {
            _extension.value = PendingExtension(state.extendSummary)
        } else if (streaming) {
            _extension.value = null
        }
    }

    /** `approval_required`: the card of [toolCallId] asks, expanded. */
    fun approvalRequired(
        toolCallId: String, name: String, argsText: String, prettyArgs: String, reason: String,
        card: io.github.ronganam.dana.data.models.ApprovalWords? = null
    ) {
        _approval.value = PendingApproval(toolName = name, toolArgs = argsText, toolCallId = toolCallId, card = card, reason = reason)
        transcript.upsertToolCall(toolCallId, name) {
            it.copy(
                requiresApproval = true,
                isRunning = false,
                isPreparing = false,
                parameters = it.parameters.ifEmpty { prettyArgs },
                toolArgs = argsText,
                isCollapsed = false,
                approvalReason = reason,
                approvalCard = card ?: it.approvalCard
            )
        }
    }

    /** `selection_required`: the choice is shown as its own card, not a tool run. */
    fun selectionRequired(toolCallId: String, selection: SelectionRequest?) {
        transcript.removeToolCall(toolCallId)
        _selection.value = selection
    }

    fun extensionNeeded(summary: String) {
        _extension.value = PendingExtension(summary)
    }

    /**
     * The approval being answered. The visible "Needs Approval" card is
     * authoritative: if the prompt state drifted (a terminal state frame
     * racing the approval event), it is rebuilt from the card. Null when
     * nothing asks.
     */
    fun currentApproval(): PendingApproval? = _approval.value ?: transcript.lastApprovalCard()?.let { card ->
        PendingApproval(card.toolName, card.toolArgs.ifEmpty { card.parameters }, card.toolCallId)
    }

    /** The user answered the approval: the prompt goes, its cards settle. */
    fun answerApproval(approved: Boolean) {
        _approval.value = null
        transcript.settleApprovalCards(approved)
    }

    /** Take the open choice (null when there is none). */
    fun takeSelection(): SelectionRequest? = _selection.value?.also { _selection.value = null }

    fun hasSelection(): Boolean = _selection.value != null

    fun hasExtension(): Boolean = _extension.value != null

    fun clearExtension() {
        _extension.value = null
    }
}
