package io.github.ronganam.dana.sync

import io.github.ronganam.dana.data.network.NotificationJson
import io.github.ronganam.dana.data.notifications.NotificationExtractor
import io.github.ronganam.dana.data.notifications.Redactor
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationInboxTest {

    @Test
    fun oneTimeCodesAreHidden() {
        assertEquals("Your verification code is [code]", Redactor.redact("Your verification code is 482913"))
        assertEquals("קוד האימות שלך: [code]", Redactor.redact("קוד האימות שלך: 123456"))
        assertEquals("Code [code] expires soon", Redactor.redact("Code 12-34-56 expires soon"))
    }

    @Test
    fun ordinaryNumbersStay() {
        assertEquals("Meet at 1930 near gate 4", Redactor.redact("Meet at 1930 near gate 4"))
        assertEquals("Your PIN is 12", Redactor.redact("Your PIN is 12"))
    }

    @Test
    fun removalReasonsMapToServerNames() {
        assertEquals("click", NotificationExtractor.reasonName(1))
        assertEquals("cancel", NotificationExtractor.reasonName(2))
        assertEquals("dana", NotificationExtractor.reasonName(10))
        assertEquals("app", NotificationExtractor.reasonName(8))
        assertEquals("cancel", NotificationExtractor.reasonName(24))
        assertEquals("other", NotificationExtractor.reasonName(999))
    }

    @Test
    fun notificationWithSuggestionParses() {
        val json = JSONObject(
            """
            {"id":"n1","key":"0|com.whatsapp|1","package":"com.whatsapp","app_name":"WhatsApp",
             "title":"Gal","text":"can you buy milk?","sender":"Gal","conversation":null,"is_group":false,
             "posted_at":"2026-11-10T14:00:00+02:00","status":"active","removed_reason":null,
             "triage_status":"done","category":"personal_message","category_emoji":"💬",
             "priority":"important","bucket":"needs_you","escalated":false,
             "suggestions":[{"id":"s1","call_id":null,"source_type":"notification","source_id":"n1","kind":"task",
               "status":"pending","payload":{"title":"Buy milk","triggers":{"car":"enter"}},
               "notification":{"id":"n1","app_name":"WhatsApp","posted_at":"2026-11-10T14:00:00+02:00"}}]}
            """.trimIndent()
        )
        val n = NotificationJson.notification(json)
        assertEquals("needs_you", n.bucket)
        assertEquals("important", n.priority)
        assertEquals(null, n.conversation)
        assertEquals(1, n.pendingSuggestions.size)
        val s = n.pendingSuggestions[0]
        assertTrue(s.fromNotification)
        assertEquals(null, s.source?.text)
        assertEquals("", s.callId)
        assertEquals("n1", s.sourceId)
        assertEquals("WhatsApp", s.source?.contact)
        assertEquals("Buy milk", s.title)
    }

    @Test
    fun offsetTimestampsAreShownInLocalTime() {
        val utc = java.time.Instant.parse("2026-11-09T23:50:39Z")
        val local = utc.atZone(java.time.ZoneId.systemDefault()).toLocalDateTime()
        assertEquals(local, io.github.ronganam.dana.ui.components.parseLocalDateTime("2026-11-09T23:50:39+00:00"))
        // Naive call timestamps are already local.
        assertEquals(
            java.time.LocalDateTime.parse("2026-11-08T11:41:29"),
            io.github.ronganam.dana.ui.components.parseLocalDateTime("2026-11-08T11:41:29.123")
        )
    }

    @Test
    fun snoozeOptionsFollowTheClock() {
        val zone = java.time.ZoneId.of("Asia/Jerusalem")
        val afternoon = java.time.ZonedDateTime.of(2026, 11, 10, 14, 0, 0, 0, zone)
        val labels = io.github.ronganam.dana.data.notifications.Snoozer.options(afternoon).map { it.label }
        assertEquals(listOf("15 min", "1 hour", "3 hours", "This evening", "Tomorrow 08:00"), labels)
        val evening = io.github.ronganam.dana.data.notifications.Snoozer.options(afternoon).first { it.label == "This evening" }
        assertEquals(afternoon.withHour(20).toInstant().toEpochMilli(), evening.untilMillis)
        // After 19:00 "This evening" is gone.
        val late = afternoon.withHour(19).withMinute(30)
        assertFalse(io.github.ronganam.dana.data.notifications.Snoozer.options(late).any { it.label == "This evening" })
        val tomorrow = io.github.ronganam.dana.data.notifications.Snoozer.options(late).last()
        assertEquals("until tomorrow 08:00", io.github.ronganam.dana.data.notifications.Snoozer.untilLabel(tomorrow.untilMillis, late))
        assertEquals("until 14:15", io.github.ronganam.dana.data.notifications.Snoozer.untilLabel(
            afternoon.plusMinutes(15).toInstant().toEpochMilli(), afternoon))
    }

    @Test
    fun snoozeKeysParse() {
        val keys = NotificationJson.snoozeKeys(org.json.JSONArray(
            """[{"key":"0|com.whatsapp|1","until_ms":1794300000000},{"key":"bad","until_ms":0},{"until_ms":5}]"""))
        assertEquals(mapOf("0|com.whatsapp|1" to 1794300000000L), keys)
        val n = NotificationJson.notification(JSONObject("""{"id":"x","package":"p","snoozed_until":"2026-11-10T20:00:00+02:00"}"""))
        assertEquals("2026-11-10T20:00:00+02:00", n.snoozedUntil)
    }

    @Test
    fun suggestionSourceCarriesTheOriginalText() {
        val n = NotificationJson.notification(JSONObject("""{"id":"x","package":"p","suggestions":[
            {"id":"s","call_id":null,"source_type":"notification","source_id":"x","kind":"task","status":"pending","payload":{},
             "notification":{"id":"x","app_name":"Slack","package":"com.Slack","title":"Alon","text":"Robin can you run the report?",
               "sender":"Alon","posted_at":"2026-11-10T10:00:00+02:00"}},
            {"id":"c","call_id":"c1","kind":"task","status":"pending","payload":{},
             "call":{"id":"c1","contact":"אמא","call_timestamp":"2026-11-08T11:41:29","summary":"- רובין יבדוק את התקליטן"}}]}"""))
        val fromNote = n.suggestions[0].source!!
        assertEquals("Robin can you run the report?", fromNote.text)
        assertEquals("Alon", fromNote.sender)
        assertEquals("com.Slack", fromNote.packageName)
        assertEquals("- רובין יבדוק את התקליטן", n.suggestions[1].source?.text)
    }

    @Test
    fun callSuggestionStillParsesFromOldShape() {
        val n = NotificationJson.notification(JSONObject("""{"id":"x","package":"p","suggestions":[
            {"id":"s","call_id":"c1","kind":"task","status":"pending","payload":{},
             "call":{"id":"c1","contact":"אמא","call_timestamp":"2026-11-08T11:41:29"}}]}"""))
        val s = n.suggestions[0]
        assertFalse(s.fromNotification)
        assertEquals("c1", s.callId)
        assertEquals("c1", s.sourceId)
        assertEquals("אמא", s.source?.contact)
    }
}
