package io.github.ronganam.dana.protocol

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The JSON payloads both apps exchange: what one side writes, the other reads back unchanged. */
class WearPayloadsTest {
    @Test fun reminderAlarmRoundTrip() {
        val alarm = ReminderAlarm("t-1", "Call the bank", "high", "location", listOf("Home", "Gym"))
        assertEquals(alarm, ReminderAlarm.decode(alarm.encode()))
        assertTrue(alarm.encode().contains("\"task_title\":\"Call the bank\""))
    }

    @Test fun reminderAlarmWithoutZonesKeepsThemUnset() {
        val read = ReminderAlarm.decode("""{"task_id": "t-2", "from_a_newer_phone": 1}""")!!
        assertNull(read.zones)
        assertEquals("Reminder", read.title)
        assertEquals("medium", read.priority)
        assertEquals("time", read.triggerType)
        assertFalse(ReminderAlarm("t-2").encode().contains("zones"))
    }

    @Test fun snoozeRequestCarriesOnlyItsKind() {
        val place = SnoozeRequest("t-3", "location", location = "Home", event = "arrive")
        val json = place.encode()
        assertFalse(json.contains("minutes"))
        assertEquals(place, SnoozeRequest.decode(json))
        assertEquals(SnoozeRequest("t-5"), SnoozeRequest.decode("""{"task_id": "t-5"}"""))
    }

    @Test fun taskStatusAndReply() {
        val req = TaskStatusRequest("t-4", "completed")
        assertEquals(req, TaskStatusRequest.decode(req.encode()))
        val reply = TaskActionReply(success = true, queued = true)
        assertEquals(reply, TaskActionReply.decode(reply.encode()))
        assertEquals(TaskActionReply(success = false, queued = false), TaskActionReply.decode("{}"))
    }

    @Test fun requiredFieldsMissingReadAsNothing() {
        assertNull(TaskStatusRequest.decode("""{"status": "completed"}"""))
        assertNull(SnoozeRequest.decode("""{"type": "time", "minutes": 10}"""))
        assertNull(ReminderHandled.decode("not json"))
        assertEquals("", ReminderHandled.decode("{}")!!.taskId)
    }

    @Test fun responsePaths() {
        assertEquals("/response/get_tasks", WearPaths.response(WearPaths.GET_TASKS))
        assertEquals("/response/voice_stream", WearPaths.RESPONSE_VOICE_STREAM)
    }

    @Test fun snapshotKeepsWhatTheWatchShowsAndToleratesMissingParts() {
        val full = DanaSnapshot(
            briefingPoints = List(6) { "p$it" },
            tasks = List(6) { SnapshotTask(id = "t$it", title = "Task $it") },
            people = listOf(SnapshotPerson()),
            shopping = List(50) { ShopItem(id = "i$it", name = "item $it") },
            shoppingCount = 50,
        ).forWatch()
        assertEquals(4, full.briefingPoints.size)
        assertEquals(4, full.tasks.size)
        assertTrue(full.people.isEmpty())
        assertEquals(40, full.shopping!!.size)
        assertEquals(full, DanaSnapshot.decode(full.encode()))

        val bare = DanaSnapshot.decode("""{"suggestion_count": 2, "unknown": {"x": 1}}""")!!
        assertEquals(2, bare.suggestionCount)
        assertNull("not counted yet", bare.inboxNeeds)
        assertNull("no shopping part", bare.shopping)
        assertTrue(bare.shoppingCanEdit)
        assertNull(DanaSnapshot.decode(null))
        assertNull(DanaSnapshot.decode("not json"))
    }

    @Test fun shoppingRequestAndAnswer() {
        val request = ShopRequest(action = "check", id = "i1", listId = "l1", ref = "r1")
        assertFalse("no text on a tick", request.encode().contains("text"))
        assertEquals(request, ShopRequest.decode(request.encode()))
        val answer = ShopAnswer.decode("""{"ok": false, "error": "This list is view only.", "ref": "r1"}""")!!
        assertFalse(answer.ok || answer.queued)
        assertEquals("This list is view only.", answer.error)
        assertEquals(ShopAnswer(), ShopAnswer.decode("{}"))
    }

    @Test fun assistantFramesRoundTripAndMissingPartsStayUnset() {
        val folded = WearReply(
            status = "completed",
            response = "Done.",
            toolCalls = listOf(WearToolCall("add_task")),
            pendingApproval = WearApprovalPrompt(toolName = "send_email", toolCallId = "c1"),
            selection = WearSelection(prompt = "Which?", options = listOf(WearOption("A", "a"))),
        )
        assertEquals(folded, WearReply.decode(folded.encode()))
        assertTrue(folded.byteSize() > 0)

        val partial = WearReply.decode("""{"partial_response": "Do", "extra": true}""")!!
        assertEquals("Do", partial.partialResponse)
        assertNull("a live frame is not final", partial.status ?: partial.response ?: partial.messages)
        val noPrompt = WearReply.decode("""{"status": "waiting_selection", "selection_request": {"options": [{"label": "A"}]}}""")!!
        assertNull(noPrompt.selection!!.prompt)
        assertEquals("", noPrompt.selection!!.options.single().value)
        assertNull(WearReply.decode("[]"))

        val select = WearRunAction(sessionId = "main", action = "select", selection = listOf(0, 2))
        assertEquals("""{"session_id":"main","action":"select","selection":[0,2]}""", select.encode())
        assertEquals("hi", WearSendMessage.decode("""{"session_id": "x", "message": "hi"}""")!!.message)
        assertNull("the message is required", WearSendMessage.decode("""{"session_id": "x"}"""))
    }
}
