package io.github.ronganam.dana.data.chat

import io.github.ronganam.dana.data.models.ApprovalWords
import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.models.ToolCallEntry
import io.github.ronganam.dana.data.network.ApiJson
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * What the conversation draws, from [ThreadModel.rows]: the tool calls of one
 * turn joined into one card with their step line ([Tools]), a call that waits
 * for the user's OK as its own card ([Approval]), everything else as it is
 * ([Plain]). Shared by the thread, the power-button sheet and kiosk / TV.
 */
sealed class ThreadItem {
    abstract val key: String

    data class Plain(val row: ThreadModel.Row) : ThreadItem() {
        override val key: String get() = row.key
    }

    /**
     * Consecutive tool calls (keyed by the first, so the card stays put as
     * steps arrive) and Dana's reply right after them, which the card holds
     * under its step line.
     */
    data class Tools(
        override val key: String,
        val messages: List<ChatMessage>,
        val forgotten: Boolean,
        val reply: ChatMessage? = null
    ) : ThreadItem() {
        val steps: List<ToolStep> get() = messages.mapNotNull { m -> m.toolCall?.let { ToolStep.of(m.id, it) } }
    }

    data class Approval(override val key: String, val message: ChatMessage, val forgotten: Boolean) : ThreadItem() {
        val entry: ToolCallEntry get() = message.toolCall!!
    }

    companion object {
        fun of(rows: List<ThreadModel.Row>): List<ThreadItem> {
            val out = mutableListOf<ThreadItem>()
            var run: MutableList<ChatMessage>? = null
            var runKey = ""
            var runForgotten = false
            fun flush(reply: ChatMessage? = null) {
                run?.let { out += Tools(runKey, it.toList(), runForgotten, reply) }
                run = null
            }
            for (row in rows) {
                val msg = (row as? ThreadModel.Row.Message)?.msg
                val tc = msg?.toolCall
                when {
                    // Dana's answer after the steps goes into their card.
                    tc == null && run != null && msg != null && !msg.isUser && !msg.isThinking -> flush(reply = msg)
                    tc == null -> { flush(); out += Plain(row) }
                    tc.requiresApproval -> { flush(); out += Approval(row.key, (row as ThreadModel.Row.Message).msg, row.forgotten) }
                    else -> {
                        val m = (row as ThreadModel.Row.Message)
                        val cur = run
                        if (cur == null) {
                            run = mutableListOf(m.msg)
                            runKey = row.key
                            runForgotten = m.forgotten
                        } else {
                            cur += m.msg
                            runForgotten = runForgotten && m.forgotten
                        }
                    }
                }
            }
            flush()
            return out
        }
    }
}

/** One tool call as a step: what Dana did, in words, and where it is. */
data class ToolStep(val id: String, val label: String, val state: State, val entry: ToolCallEntry) {
    enum class State { Done, Running, Failed }

    companion object {
        fun of(id: String, tc: ToolCallEntry): ToolStep {
            val state = when {
                tc.isRunning || tc.isPreparing || (tc.succeeded == null && tc.response.isBlank()) -> State.Running
                tc.succeeded == false || tc.response.startsWith("Error executing tool") -> State.Failed
                else -> State.Done
            }
            return ToolStep(id, stepWords(tc), state, tc)
        }
    }
}

private val PAST = mapOf(
    "get" to "Read", "list" to "Read", "read" to "Read", "recall" to "Recalled", "search" to "Searched",
    "find" to "Found", "add" to "Added", "create" to "Created", "save" to "Saved", "update" to "Changed",
    "edit" to "Changed", "set" to "Set", "delete" to "Deleted", "remove" to "Removed", "forget" to "Forgot",
    "send" to "Sent", "remember" to "Remembered", "dismiss" to "Dismissed", "snooze" to "Snoozed",
    "pause" to "Paused", "resume" to "Resumed", "accept" to "Accepted", "link" to "Linked",
    "summarize" to "Summarized", "run" to "Ran", "sync" to "Synced", "categorize" to "Categorized",
    "call" to "Called", "turn" to "Turned", "play" to "Played", "open" to "Opened"
)

/** Words for the tools Dana uses most, where the name alone reads badly. */
private val NAMED = mapOf(
    "get_current_time" to "Checked the time",
    "get_weather" to "Checked the weather",
    "web_search" to "Searched the web",
    "get_zones" to "Read your places",
    "add_task" to "Added a reminder",
    "update_task" to "Changed a reminder",
    "delete_task" to "Deleted a reminder",
    "list_tasks" to "Read your reminders",
    "run_code" to "Ran a calculation",
    "remember_info" to "Remembered it",
    "recall_info" to "Looked in what it remembers",
    "money_summary" to "Read your money",
    "money_forecast" to "Read the forecast",
    "can_i_afford" to "Checked what you can afford",
)

/**
 * A tool call as a short past-tense step: the server's own summary when it
 * says more than the name, else [NAMED] or the name's verb in the past
 * ("search_calls" → "Searched calls"), else the name in words.
 */
fun stepWords(tc: ToolCallEntry): String {
    val summary = tc.toolSummary.trim()
    if (summary.isNotEmpty() && summary != tc.toolName && summary.length <= 40) return summary
    return toolStepWords(tc.toolName)
}

fun toolStepWords(name: String): String {
    NAMED[name]?.let { return it }
    val parts = name.split('_').filter { it.isNotBlank() }
    if (parts.isEmpty()) return "Used a tool"
    val verb = PAST[parts[0].lowercase()]
    return if (verb != null && parts.size > 1) "$verb ${parts.drop(1).joinToString(" ")}"
    else parts.joinToString(" ").replaceFirstChar { it.uppercase() }
}

/**
 * What a finished step said, when it reads as words: its first lines, or
 * nothing for JSON, errors and long dumps (those stay behind Details).
 */
fun resultWords(response: String): String? {
    val t = response.trim()
    if (t.isEmpty() || t.startsWith("{") || t.startsWith("[") || t.startsWith("Error")) return null
    // Lists of records (ids, many lines) are for the model, not for reading here.
    if (t.length > 200 || UUID_ISH.containsMatchIn(t) || t.contains("ID:")) return null
    val lines = t.lineSequence().map { it.trim() }.filter { it.isNotEmpty() }.toList()
    if (lines.isEmpty() || lines.size > 3) return null
    return lines.joinToString("\n")
}

private val UUID_ISH = Regex("[0-9a-f]{8}-[0-9a-f]{4}-")

/** What kind of thing a waiting call does, for its buttons and its line. */
enum class ApprovalKind { Send, Delete, Other }

/** A waiting call as its card shows it. */
data class ApprovalView(
    val kind: ApprovalKind,
    /** "Delete the reminder “Milk”?" */
    val title: String,
    /** Why it waits, in one line. */
    val note: String,
    /** The words it would send, or other content; null when none. */
    val body: String?,
    val details: List<Pair<String, String>>,
    val yes: String,
    val no: String
)

private val DRAFT_KEYS = listOf("text", "message", "body", "content", "reply")

/** A waiting call's card: the server's words ([card]) when it sent them, else words from the tool's name and arguments. */
fun approvalView(toolName: String, toolArgs: String, card: ApprovalWords?, reason: String): ApprovalView {
    val first = toolName.substringBefore('_').lowercase()
    val kind = when {
        first == "send" || first == "reply" || toolName.contains("message") -> ApprovalKind.Send
        first == "delete" || first == "remove" || first == "forget" -> ApprovalKind.Delete
        else -> ApprovalKind.Other
    }
    val base = card?.title?.trim()?.takeIf { it.isNotEmpty() }
        ?: toolName.replace('_', ' ').trim().replaceFirstChar { it.uppercase() }.ifEmpty { "Do this" }
    val title = if (base.last() in ".?!") base else "$base?"
    val note = when (reason) {
        "device_screen" -> "Asking because the screen was shared."
        "untrusted_result", "untrusted_context" -> "Asking because this chat holds text others wrote."
        else -> when (kind) {
            ApprovalKind.Send -> "Nothing goes out until you say so."
            ApprovalKind.Delete -> "Nothing is deleted until you say so."
            ApprovalKind.Other -> "Nothing happens until you say so."
        }
    }
    val body = card?.body?.trim()?.takeIf { it.isNotEmpty() } ?: draftIn(toolArgs)
    val (yes, no) = when (kind) {
        ApprovalKind.Send -> "Send" to "Don't send"
        ApprovalKind.Delete -> "Delete" to "Keep it"
        ApprovalKind.Other -> "Go ahead" to "Don't"
    }
    return ApprovalView(kind, title, note, body, card?.details.orEmpty(), yes, no)
}

private fun draftIn(toolArgs: String): String? {
    if (toolArgs.isBlank()) return null
    val obj = runCatching { ApiJson.parseToJsonElement(toolArgs) as? JsonObject }.getOrNull() ?: return null
    return DRAFT_KEYS.firstNotNullOfOrNull { k -> (obj[k] as? JsonPrimitive)?.takeIf { it.isString }?.content?.takeIf(String::isNotBlank) }
}
