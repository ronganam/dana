package io.github.ronganam.dana.chat

import io.github.ronganam.dana.data.chat.ChatHistoryCodec
import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.models.ToolCallEntry
import io.github.ronganam.dana.data.network.ApiException
import io.github.ronganam.dana.ui.viewmodels.CallsController
import io.github.ronganam.dana.ui.viewmodels.describeError
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.UnknownHostException

class ChatHistoryCodecTest {

    @Test
    fun roundTripPreservesMessagesAndToolCalls() {
        val messages = listOf(
            ChatMessage(id = "1", sender = "You", message = "שלום", isUser = true, images = listOf("/api/images/a.jpg")),
            ChatMessage(
                id = "2", sender = "Tool", message = "", isUser = false,
                toolCall = ToolCallEntry(
                    toolName = "create_task", toolSummary = "", parameters = "{\"a\":1}",
                    response = "ok", timestamp = 42L, toolCallId = "tc1", isCollapsed = true,
                    isRunning = true, succeeded = true, durationMs = 120L
                )
            ),
            ChatMessage(id = "3", sender = "Assistant", message = "Done", isUser = false)
        )

        val decoded = ChatHistoryCodec.decode(ChatHistoryCodec.encode(messages))

        assertEquals(3, decoded.size)
        assertEquals(messages[0], decoded[0])
        assertEquals(messages[2], decoded[2])
        val tc = decoded[1].toolCall!!
        assertEquals("create_task", tc.toolName)
        assertEquals("tc1", tc.toolCallId)
        assertEquals(true, tc.succeeded)
        assertEquals(120L, tc.durationMs)
        // A cached card can never still be running after a reload.
        assertFalse(tc.isRunning)
    }

    @Test
    fun decodesServerSessionShapeWithMissingFields() {
        val json = """[{"sender":"Assistant","message":"hi","isUser":false,
            "toolCall":{"toolName":"x","succeeded":null}}, "garbage"]"""
        val decoded = ChatHistoryCodec.decode(json)
        assertEquals(1, decoded.size)
        assertTrue(decoded[0].id.isNotEmpty())
        assertNull(decoded[0].toolCall!!.succeeded)
        assertNull(decoded[0].toolCall!!.durationMs)
        assertTrue(decoded[0].images.isEmpty())
    }

    @Test
    fun describeErrorSeparatesUnreachableFromRejected() {
        assertTrue(describeError(UnknownHostException("x")).contains("unreachable"))
        assertTrue(describeError(ApiException(401, "no")).contains("API token"))
        assertEquals("server error (HTTP 500)", describeError(ApiException(500, "boom")))
    }

    @Test
    fun parsesSamsungRecordingNames() {
        val parsed = CallsController.parseSamsungFilename("Call‎Dana Cohen‏_241031_093015.m4a")
        assertEquals("Dana Cohen" to "2024-10-31T09:30:15", parsed)
        assertNull(CallsController.parseSamsungFilename("Voice 001.m4a"))
        assertNull(CallsController.parseSamsungFilename("CallBob_2410_0930.m4a"))
    }
}
