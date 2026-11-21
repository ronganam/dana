package io.github.ronganam.dana.protocol

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer

/**
 * The watch's conversation through the phone: what the watch asks
 * ([WearSendMessage], [WearRunAction]) and the frames the phone answers on the
 * response paths ([WearReply]). The phone follows the run on the server and
 * folds it into these small frames (data-layer messages cap at 4 KB).
 */

/** [WearPaths.SEND_MESSAGE]: a typed message. */
@Serializable
data class WearSendMessage(@SerialName("session_id") val sessionId: String = "", val message: String) {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    companion object {
        fun decode(text: String): WearSendMessage? = serializer().decodeOrNull(text)
    }
}

/** [WearPaths.APPROVE_ACTION]: approve / deny / select ([selection]) / extend_iterations / decline_iterations. */
@Serializable
data class WearRunAction(
    @SerialName("session_id") val sessionId: String = "",
    val action: String = "",
    @SerialName("tool_call_id") val toolCallId: String? = null,
    val selection: List<Int>? = null,
) {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    companion object {
        fun decode(text: String): WearRunAction? = serializer().decodeOrNull(text)
    }
}

/** One conversation in the [WearPaths.GET_SESSIONS] answer (the thread is the only one). */
@Serializable
data class WearSession(@SerialName("session_id") val sessionId: String = "", val title: String = "") {
    companion object {
        fun encodeList(sessions: List<WearSession>): String = WearJson.encodeToString(ListSerializer(serializer()), sessions)
    }
}

/** A tool the run used, by name. */
@Serializable
data class WearToolCall(@SerialName("tool_name") val toolName: String? = null)

/** The tool call waiting for the user's approval. */
@Serializable
data class WearApprovalPrompt(
    @SerialName("tool_name") val toolName: String? = null,
    @SerialName("tool_call_id") val toolCallId: String = "",
)

@Serializable
data class WearOption(val label: String = "", val value: String = "")

/** A choice the run waits for. */
@Serializable
data class WearSelection(
    val prompt: String? = null,
    val multiple: Boolean = false,
    val options: List<WearOption> = emptyList(),
)

/** The run reached its step limit; [summary] says what it did so far. */
@Serializable
data class WearExtension(val summary: String = "")

/** A thread message as the watch shows it: the text (clamped), who said it, the tool's name. */
@Serializable
data class WearThreadMessage(
    val id: String? = null,
    val isUser: Boolean = false,
    val message: String = "",
    val toolCall: WearToolCall? = null,
)

/**
 * One frame on a response path: a folded reply ([status] set), a live
 * transcript ([transcription] alone), the reply so far ([partialResponse]),
 * the thread ([messages]) or an [error].
 */
@Serializable
data class WearReply(
    val status: String? = null,
    val response: String? = null,
    val transcription: String? = null,
    @SerialName("partial_response") val partialResponse: String? = null,
    @SerialName("tool_calls") val toolCalls: List<WearToolCall>? = null,
    @SerialName("pending_approval") val pendingApproval: WearApprovalPrompt? = null,
    @SerialName("selection_request") val selection: WearSelection? = null,
    @SerialName("extend_summary") val extension: WearExtension? = null,
    @SerialName("session_id") val sessionId: String? = null,
    val messages: List<WearThreadMessage>? = null,
    val error: String? = null,
) {
    fun encode(): String = WearJson.encodeToString(serializer(), this)

    /** The encoded size in bytes (the data layer's limit is on bytes). */
    fun byteSize(): Int = encode().toByteArray(Charsets.UTF_8).size

    companion object {
        fun decode(text: String): WearReply? = serializer().decodeOrNull(text)
    }
}
