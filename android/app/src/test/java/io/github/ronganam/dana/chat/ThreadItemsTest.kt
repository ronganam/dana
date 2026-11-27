package io.github.ronganam.dana.chat

import io.github.ronganam.dana.data.chat.ApprovalKind
import io.github.ronganam.dana.data.chat.ThreadItem
import io.github.ronganam.dana.data.chat.ThreadModel
import io.github.ronganam.dana.data.chat.ToolStep
import io.github.ronganam.dana.data.chat.approvalView
import io.github.ronganam.dana.data.chat.resultWords
import io.github.ronganam.dana.data.chat.toolStepWords
import io.github.ronganam.dana.data.models.ApprovalWords
import io.github.ronganam.dana.data.models.ChatMessage
import io.github.ronganam.dana.data.models.ToolCallEntry
import io.github.ronganam.dana.data.network.ApiJson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ThreadItemsTest {
    private fun user(id: String, text: String) = ThreadModel.Row.Message(ChatMessage(id = id, sender = "You", message = text, isUser = true))
    private fun dana(id: String, text: String) = ThreadModel.Row.Message(ChatMessage(id = id, sender = "Assistant", message = text, isUser = false))
    private fun tool(id: String, name: String, response: String = "ok", ok: Boolean? = true, asks: Boolean = false) = ThreadModel.Row.Message(
        ChatMessage(
            id = id, sender = "Tool", message = "", isUser = false,
            toolCall = ToolCallEntry(name, name, "{}", response = response, succeeded = ok, requiresApproval = asks)
        )
    )

    @Test
    fun `a turn's tool calls join one card with the reply after them`() {
        val items = ThreadItem.of(listOf(user("u1", "Remind me"), tool("t1", "get_zones"), tool("t2", "add_task"), dana("a1", "Done."), user("u2", "Thanks")))
        assertEquals(3, items.size)
        val tools = items[1] as ThreadItem.Tools
        assertEquals("t1", tools.key)
        assertEquals(listOf("Read your places", "Added a reminder"), tools.steps.map { it.label })
        assertEquals("a1", tools.reply?.id)
        assertTrue(items[2] is ThreadItem.Plain)
    }

    @Test
    fun `a call that asks first stands alone and splits the steps`() {
        val items = ThreadItem.of(listOf(tool("t1", "list_tasks"), tool("t2", "delete_task", response = "", ok = null, asks = true), tool("t3", "get_weather")))
        assertEquals(listOf("t1", "t2", "t3"), items.map { it.key })
        assertTrue(items[1] is ThreadItem.Approval)
        assertTrue(items[0] is ThreadItem.Tools && items[2] is ThreadItem.Tools)
    }

    @Test
    fun `a step's state`() {
        val running = ToolStep.of("x", ToolCallEntry("web_search", "web_search", "{}", isRunning = true))
        val failed = ToolStep.of("x", ToolCallEntry("add_task", "add_task", "{}", response = "Error executing tool: nope", succeeded = true))
        assertEquals(ToolStep.State.Running, running.state)
        assertEquals(ToolStep.State.Failed, failed.state)
    }

    @Test
    fun `tool names in words`() {
        assertEquals("Searched calls", toolStepWords("search_calls"))
        assertEquals("Read recent calls", toolStepWords("list_recent_calls"))
        assertEquals("Checked the weather", toolStepWords("get_weather"))
        assertEquals("Money insights", toolStepWords("money_insights"))
    }

    @Test
    fun `only short plain answers show on the card`() {
        assertEquals("Your zones:\n- Home (Home)", resultWords("Your zones:\n- Home (Home)"))
        assertNull(resultWords("{\"a\": 1}"))
        assertNull(resultWords("Error executing tool: x"))
        assertNull(resultWords("Your open tasks:\n- Milk (ID: e9942fe4-7bfd-4e50-bd38-8933f69d5637)"))
    }

    @Test
    fun `the approval card uses the server's words and the kind's buttons`() {
        val card = ApiJson.decodeFromString(
            ApprovalWords.serializer(),
            """{"title": "Send a WhatsApp message to Noa", "question": "Send this WhatsApp message?", "body_label": "Message", "body": "On my way", "facts": [["When", "now"], ["Empty", ""]]}"""
        )
        val v = approvalView("send_whatsapp_message", "{}", card, "")
        assertEquals(ApprovalKind.Send, v.kind)
        assertEquals("Send a WhatsApp message to Noa?", v.title)
        assertEquals("On my way", v.body)
        assertEquals(listOf("When" to "now"), v.details)
        assertEquals("Send" to "Don't send", v.yes to v.no)
        assertEquals("Nothing goes out until you say so.", v.note)
    }

    @Test
    fun `without the server's words the card falls back to the name and the draft`() {
        val v = approvalView("delete_task", "{\"item\": \"x\"}", null, "device_screen")
        assertEquals("Delete task?", v.title)
        assertEquals("Delete" to "Keep it", v.yes to v.no)
        assertEquals("Asking because the screen was shared.", v.note)
        assertEquals("Hi", approvalView("send_telegram_message", "{\"text\": \"Hi\"}", null, "").body)
    }
}
