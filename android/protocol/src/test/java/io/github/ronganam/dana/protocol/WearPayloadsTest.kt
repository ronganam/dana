package io.github.ronganam.dana.protocol

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

/** The JSON payloads both apps exchange: what one side writes, the other reads back unchanged. */
class WearPayloadsTest {
    private fun wire(o: JSONObject) = JSONObject(o.toString())

    @Test fun reminderAlarmRoundTrip() {
        val alarm = ReminderAlarm("t-1", "Call the bank", "high", "location", listOf("Home", "Gym"))
        assertEquals(alarm, ReminderAlarm.fromJson(wire(alarm.toJson())))
    }

    @Test fun reminderAlarmWithoutZonesKeepsThemUnset() {
        val read = ReminderAlarm.fromJson(JSONObject().put("task_id", "t-2"))
        assertNull(read.zones)
        assertEquals("Reminder", read.title)
        assertEquals("time", read.triggerType)
    }

    @Test fun snoozeRequestCarriesOnlyItsKind() {
        val place = SnoozeRequest("t-3", "location", location = "Home", event = "arrive")
        val json = wire(place.toJson())
        assertFalse(json.has("minutes"))
        assertEquals(place, SnoozeRequest.fromJson(json))
        val server = place.serverPayload()
        assertFalse(server.has("task_id"))
        assertEquals("arrive", server.getString("event"))
    }

    @Test fun taskStatusAndReply() {
        val req = TaskStatusRequest("t-4", "completed")
        assertEquals(req, TaskStatusRequest.fromJson(wire(req.toJson())))
        val reply = TaskActionReply(success = true, queued = true)
        assertEquals(reply, TaskActionReply.fromJson(wire(reply.toJson())))
    }

    @Test fun responsePaths() {
        assertEquals("/response/get_tasks", WearPaths.response(WearPaths.GET_TASKS))
        assertEquals("/response/voice_stream", WearPaths.RESPONSE_VOICE_STREAM)
    }
}
