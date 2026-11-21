package io.github.ronganam.dana.data.chat

import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.models.ToolCallEntry
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/**
 * JSON form of a chat transcript. It is used for the local cache and to parse
 * the server's session detail (`GET /api/sessions/{id}` uses the same field
 * names).
 */
object ChatHistoryCodec {

    fun encode(messages: List<ChatMessage>): String {
        val arr = JSONArray()
        messages.forEach { arr.put(encodeMessage(it)) }
        return arr.toString()
    }

    fun decode(json: String): List<ChatMessage> = decode(JSONArray(json))

    fun decode(arr: JSONArray): List<ChatMessage> {
        val list = ArrayList<ChatMessage>(arr.length())
        for (i in 0 until arr.length()) {
            val obj = arr.optJSONObject(i) ?: continue
            list.add(decodeMessage(obj))
        }
        return list
    }

    private fun encodeMessage(msg: ChatMessage): JSONObject = JSONObject().apply {
        put("id", msg.id)
        put("sender", msg.sender)
        put("message", msg.message)
        put("isUser", msg.isUser)
        if (msg.images.isNotEmpty()) {
            put("images", JSONArray(msg.images))
        }
        // Same key and unit as the server's thread (`ts`, epoch seconds).
        msg.timestamp?.let { put("ts", it / 1000.0) }
        if (msg.device.isNotEmpty()) put("device", msg.device)
        if (msg.shortcut) put("shortcut", true)
        if (msg.screen.isNotEmpty()) put("screen", msg.screen)
        msg.toolCall?.let { tc ->
            put("toolCall", JSONObject().apply {
                put("toolName", tc.toolName)
                put("toolSummary", tc.toolSummary)
                put("parameters", tc.parameters)
                put("response", tc.response)
                put("timestamp", tc.timestamp)
                put("requiresApproval", tc.requiresApproval)
                put("toolCallId", tc.toolCallId)
                put("toolArgs", tc.toolArgs)
                put("isCollapsed", tc.isCollapsed)
                put("isRunning", tc.isRunning)
                put("succeeded", tc.succeeded ?: JSONObject.NULL)
                put("durationMs", tc.durationMs ?: JSONObject.NULL)
            })
        }
    }

    private fun decodeMessage(obj: JSONObject): ChatMessage {
        val ts = if (obj.has("ts") && !obj.isNull("ts")) (obj.optDouble("ts") * 1000).toLong().takeIf { it > 0 } else null
        val toolCall = obj.optJSONObject("toolCall")?.let {
            ToolCallEntry(
                toolName = it.optString("toolName"),
                toolSummary = it.optString("toolSummary"),
                parameters = it.optString("parameters"),
                response = it.optString("response"),
                // Stored thread messages carry the time as `ts` on the message.
                timestamp = it.optLong("timestamp", ts ?: System.currentTimeMillis()),
                requiresApproval = it.optBoolean("requiresApproval", false),
                toolCallId = it.optString("toolCallId"),
                toolArgs = it.optString("toolArgs"),
                isCollapsed = it.optBoolean("isCollapsed", true),
                // A cached "running" card can't still be running after a reload.
                isRunning = false,
                succeeded = if (it.has("succeeded") && !it.isNull("succeeded")) it.optBoolean("succeeded") else null,
                durationMs = if (it.has("durationMs") && !it.isNull("durationMs")) it.optLong("durationMs") else null
            )
        }
        val imagesArr = obj.optJSONArray("images")
        val images = if (imagesArr != null) {
            (0 until imagesArr.length()).mapNotNull { imagesArr.optString(it).takeIf { s -> s.isNotEmpty() } }
        } else emptyList()
        return ChatMessage(
            id = obj.optString("id").ifEmpty { UUID.randomUUID().toString() },
            sender = obj.optString("sender"),
            message = obj.optString("message"),
            isUser = obj.optBoolean("isUser"),
            toolCall = toolCall,
            images = images,
            timestamp = ts,
            device = obj.optString("device").takeIf { it != "null" }.orEmpty(),
            shortcut = obj.optBoolean("shortcut", false),
            screen = screenLabel(obj)
        )
    }

    /**
     * The "Screen" caption of a user message: our own cache's `screen`, or the
     * server's `device_screen` (what the power-button sheet sent with it).
     */
    private fun screenLabel(obj: JSONObject): String {
        (obj.opt("screen") as? String)?.takeIf { it.isNotBlank() && it != "null" }?.let { return it }
        val ds = obj.optJSONObject("device_screen") ?: return ""
        return ds.optString("app").takeIf { it.isNotBlank() && it != "null" } ?: "Screen"
    }
}
