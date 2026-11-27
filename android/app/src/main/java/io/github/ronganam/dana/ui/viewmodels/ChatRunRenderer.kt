package io.github.ronganam.dana.ui.viewmodels

import io.github.ronganam.dana.R
import io.github.ronganam.dana.data.models.ChatContext
import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.network.RunEvent
import io.github.ronganam.dana.data.network.SessionState
import io.github.ronganam.dana.data.network.pretty
import kotlinx.coroutines.flow.MutableStateFlow

/**
 * Maps the events of a run (the SSE stream, a reattach, the voice socket)
 * onto what the conversation shows: the [transcript], the [prompts], the
 * Undo row, the status line and the chat's context. Events already rendered
 * (replayed on reattach) are skipped through [runs]. The closing `state`
 * frame is authoritative: it reconciles the prompts and, in the shared
 * thread, replaces the live bubbles with the stored turn.
 */
internal class ChatRunRenderer(
    private val runs: RunStreamClient,
    private val transcript: ChatTranscript,
    private val prompts: ChatPrompts,
    private val thread: ThreadView,
    private val undos: ChatUndos,
    private val texts: Texts,
    private val status: MutableStateFlow<String>,
    private val context: MutableStateFlow<ChatContext?>,
    /** The shared thread is shown. */
    private val inThread: () -> Boolean,
    /** A run is being shown as it streams. */
    private val streaming: () -> Boolean,
    /** The words of a spoken message arrived (`transcription`): show them as the user's bubble. */
    private val onUtterance: (String) -> Unit,
    /** A tool ran: other screens may need a refresh when the run ends. */
    private val onToolEnded: () -> Unit
) {
    private val run: RunTracker get() = runs.run

    /** The typing indicator, labelled with the run's model and [label]. */
    fun thinking(label: String = "") {
        val full = listOfNotNull(run.modelLabel, label.ifEmpty { null }).joinToString(" · ")
        transcript.showThinking(full)
    }

    /** Stop the streaming state on every reply bubble of this run. */
    fun settleBubbles() = transcript.settle(run.stepBubbles.values.toSet())

    /** The undos the server lists for the latest run (anchored at the latest user message). */
    fun reconcileUndos(state: SessionState) {
        state.undos?.let { undos.reconcile(it, transcript.value.lastOrNull { m -> m.isUser }?.timestamp) }
    }

    private fun assistantLine(text: String) =
        transcript.append(ChatMessage(sender = ChatTranscript.ASSISTANT, message = text, isUser = false))

    /** Map one streamed run event onto the chat state. */
    fun render(event: RunEvent) {
        if (!runs.accept(event)) return  // already rendered (replayed on reattach)

        when (event.type) {
            "run_start" -> {
                run.modelLabel = event.model?.label
            }

            "transcription" -> {
                val text = event.text
                if (text.isNotEmpty()) {
                    transcript.removeThinking()
                    undos.clear()
                    onUtterance(text)
                    thinking(texts.get(R.string.chat_thinking))
                }
            }

            "step_start" -> {
                val iteration = event.iteration
                val max = event.maxIterations
                val label = if (iteration > 1 && max > 0) texts.get(R.string.chat_step, iteration, max) else texts.get(R.string.chat_thinking)
                status.value = label
                thinking(label)
            }

            "reasoning_delta" -> {
                val delta = event.delta
                transcript.updateStepBubble(run.stepBubbles, event.iteration) { it.copy(reasoning = it.reasoning + delta, isStreaming = true) }
                status.value = texts.get(R.string.chat_thinking)
            }

            "text_delta" -> {
                val delta = event.delta
                transcript.updateStepBubble(run.stepBubbles, event.iteration) { it.copy(message = it.message + delta, isStreaming = true) }
                status.value = texts.get(R.string.chat_writing)
            }

            "retry" -> {
                transcript.dropStepBubble(run.stepBubbles, event.iteration)
                thinking(texts.get(R.string.chat_retrying))
            }

            "tool_call" -> {
                val name = event.name
                transcript.upsertToolCall(event.id, name) { it.copy(toolName = name, isPreparing = true) }
                status.value = texts.get(R.string.chat_preparing_tool, name)
            }

            "tool_start" -> {
                val name = event.name
                transcript.upsertToolCall(event.id, name) {
                    it.copy(toolName = name, parameters = event.args.pretty(), isPreparing = false, isRunning = true)
                }
                status.value = texts.get(R.string.chat_running_tool, name)
            }

            "tool_end" -> {
                onToolEnded()
                undos.add(event.undos)
                transcript.upsertToolCall(event.id, event.name) {
                    it.copy(
                        response = event.result.pretty(),
                        isPreparing = false,
                        isRunning = false,
                        succeeded = event.ok,
                        durationMs = event.durationMs
                    )
                }
                if (!transcript.anyToolBusy()) {
                    thinking(texts.get(R.string.chat_reading_results))
                    status.value = texts.get(R.string.chat_thinking)
                }
            }

            "final" -> transcript.finalReply(run.stepBubbles, event.content, event.shortcut)

            "error" -> {
                transcript.removeThinking()
                assistantLine(texts.get(R.string.chat_error, event.message))
            }

            "approval_required" -> {
                prompts.approvalRequired(event.id, event.name, event.argsText, event.args.pretty(), event.reason.orEmpty(), event.card)
                transcript.removeThinking()
                status.value = texts.get(R.string.chat_waiting_approval)
            }

            "selection_required" -> {
                prompts.selectionRequired(event.id, event.selection)
                transcript.removeThinking()
                status.value = texts.get(R.string.chat_waiting_choice)
            }

            "iterations_exceeded" -> {
                prompts.extensionNeeded(event.summary)
                transcript.removeThinking()
                status.value = texts.get(R.string.chat_step_limit)
            }

            "cancelled" -> {
                transcript.removeThinking()
                assistantLine(texts.get(R.string.chat_stopped))
            }

            "run_end" -> {
                transcript.removeThinking()
                settleBubbles()
            }

            "state" -> {
                run.gotState = true
                event.state?.let(::applyTerminalState)
            }
        }
    }

    /**
     * The authoritative session state that closes every stream. Live events
     * already rendered the content, so this only reconciles the prompts the
     * user still has to answer and clears stale ones.
     */
    private fun applyTerminalState(state: SessionState) {
        // Refreshed every turn: a suggestion accepted in this chat shows as done.
        if (state.hasContext) context.value = state.context
        transcript.removeThinking()
        status.value = ""
        if (inThread()) {
            thread.setMemory(state.memory)
            // The stored turn (stable ids, times, devices) replaces the live bubbles.
            if (state.messages.isNotEmpty()) thread.applyPage(state, state.messages)
        }
        reconcileUndos(state)
        prompts.fromTerminalState(state, streaming())

        // The error event usually said it already; a state frame adds a line only when it has news.
        val stateError = state.error
        if (state.status == "error" && stateError != null && transcript.value.lastOrNull()?.message?.contains(stateError) != true) {
            assistantLine(texts.get(R.string.chat_error, stateError))
        }
    }
}
