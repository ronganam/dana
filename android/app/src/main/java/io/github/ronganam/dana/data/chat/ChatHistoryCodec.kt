package io.github.ronganam.dana.data.chat

import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.models.ToolCallEntry
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import io.github.ronganam.dana.data.network.OptString
import io.github.ronganam.dana.data.network.TextList
import io.github.ronganam.dana.data.network.text
import java.util.UUID

/**
 * JSON form of a chat transcript. It is used for the local cache and to parse
 * the server's session detail (`GET /api/sessions/{id}` uses the same field
 * names, camelCase: `isUser`, `toolCall`).
 */
object ChatHistoryCodec {

    @OptIn(ExperimentalSerializationApi::class)
    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        coerceInputValues = true
        explicitNulls = false
    }

    @Serializable
    private data class StoredToolCall(
        val toolName: String = "",
        val toolSummary: String = "",
        val parameters: String = "",
        val response: String = "",
        val timestamp: Long? = null,
        val requiresApproval: Boolean = false,
        val toolCallId: String = "",
        val toolArgs: String = "",
        val isCollapsed: Boolean = true,
        val isRunning: Boolean = false,
        val succeeded: Boolean? = null,
        val durationMs: Long? = null
    )

    @Serializable
    private data class StoredMessage(
        val id: String = "",
        val sender: String = "",
        val message: String = "",
        val isUser: Boolean = false,
        val images: TextList = emptyList(),
        /** Same key and unit as the server's thread (`ts`, epoch seconds). */
        val ts: Double? = null,
        val device: OptString = null,
        val shortcut: Boolean = false,
        /** Our own cache's screen caption (the server may send an object here: ignored). */
        val screen: JsonElement? = null,
        /** What the power-button sheet sent with the message (server). */
        @SerialName("device_screen") val deviceScreen: JsonObject? = null,
        val toolCall: StoredToolCall? = null
    ) {
        fun toModel(): ChatMessage {
            val at = ts?.let { (it * 1000).toLong() }?.takeIf { it > 0 }
            return ChatMessage(
                id = id.ifEmpty { UUID.randomUUID().toString() },
                sender = sender,
                message = message,
                isUser = isUser,
                toolCall = toolCall?.let {
                    ToolCallEntry(
                        toolName = it.toolName,
                        toolSummary = it.toolSummary,
                        parameters = it.parameters,
                        response = it.response,
                        // Stored thread messages carry the time as `ts` on the message.
                        timestamp = it.timestamp ?: at ?: System.currentTimeMillis(),
                        requiresApproval = it.requiresApproval,
                        toolCallId = it.toolCallId,
                        toolArgs = it.toolArgs,
                        isCollapsed = it.isCollapsed,
                        // A cached "running" card can't still be running after a reload.
                        isRunning = false,
                        succeeded = it.succeeded,
                        durationMs = it.durationMs
                    )
                },
                images = images,
                timestamp = at,
                device = device.orEmpty(),
                shortcut = shortcut,
                screen = screenLabel()
            )
        }

        /**
         * The "Screen" caption of a user message: our own cache's `screen`, or the
         * server's `device_screen` (what the power-button sheet sent with it).
         */
        private fun screenLabel(): String {
            (screen as? JsonPrimitive)?.takeIf { it.isString }?.content?.takeIf { it.isNotBlank() && it != "null" }?.let { return it }
            val ds = deviceScreen ?: return ""
            return ds.text("app")?.takeIf { it.isNotBlank() } ?: "Screen"
        }
    }

    private fun stored(msg: ChatMessage) = StoredMessage(
        id = msg.id,
        sender = msg.sender,
        message = msg.message,
        isUser = msg.isUser,
        images = msg.images,
        ts = msg.timestamp?.let { it / 1000.0 },
        device = msg.device.ifEmpty { null },
        shortcut = msg.shortcut,
        screen = msg.screen.takeIf { it.isNotEmpty() }?.let(::JsonPrimitive),
        toolCall = msg.toolCall?.let { tc ->
            StoredToolCall(
                tc.toolName, tc.toolSummary, tc.parameters, tc.response, tc.timestamp, tc.requiresApproval,
                tc.toolCallId, tc.toolArgs, tc.isCollapsed, tc.isRunning, tc.succeeded, tc.durationMs
            )
        }
    )

    fun encode(messages: List<ChatMessage>): String =
        json.encodeToString(ListSerializer(StoredMessage.serializer()), messages.map(::stored))

    fun decode(text: String): List<ChatMessage> = decode(json.parseToJsonElement(text) as? JsonArray)

    /** The server's `messages` (or the cache's); entries that aren't messages are skipped. */
    fun decode(arr: JsonArray?): List<ChatMessage> =
        arr.orEmpty().filterIsInstance<JsonObject>().map { json.decodeFromJsonElement(StoredMessage.serializer(), it).toModel() }
}
