package io.github.ronganam.dana.money

import io.github.ronganam.dana.data.money.EmptyJsonArray
import io.github.ronganam.dana.data.money.MoneyCodes
import io.github.ronganam.dana.data.money.MoneyCodes.Message
import io.github.ronganam.dana.data.money.MoneySignals
import io.github.ronganam.dana.data.money.longValue
import io.github.ronganam.dana.data.money.string
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MoneyCodesTest {
    private val now = 1_800_000_000_000L
    /** The server's regex for Bank Hapoalim, as JSON delivers it. */
    private val hapoalimRegex = "קוד האימות הוא:?\\s*(\\d{4,8})"
    private val req = MoneyCodes.Request("abc123def456", listOf("BHapoalim"), hapoalimRegex, now - 60_000, now + 120_000)
    private val sms = "לכניסה לאתר בנק הפועלים קוד האימות הוא: 123456 אין למסור קוד זה לאף אחד"

    @Test
    fun hebrewSmsGivesTheCode() {
        assertEquals("123456", MoneyCodes.match("BHapoalim", sms, req, now))
        assertEquals("4821", MoneyCodes.match("BHapoalim", "קוד האימות הוא 4821", req, now))
    }

    @Test
    fun senderIsCaseInsensitiveAndTrimmed() {
        assertEquals("123456", MoneyCodes.match("  bhapoalim ", sms, req, now))
        assertEquals("123456", MoneyCodes.match("BHAPOALIM", sms, req, now))
        // An RTL phone wraps names and numbers in direction marks.
        assertEquals("123456", MoneyCodes.match("⁨BHapoalim⁩", "קוד האימות הוא:‏ 123456", req, now))
    }

    @Test
    fun otherSendersAndTextsDoNotMatch() {
        assertNull(MoneyCodes.match("Leumi", sms, req, now))
        assertNull(MoneyCodes.match("", sms, req, now))
        assertNull(MoneyCodes.match("BHapoalim", "העברה של 123456 ש\"ח בוצעה", req, now))
        assertNull(MoneyCodes.match("BHapoalim", "קוד האימות הוא: 12", req, now))
    }

    @Test
    fun expiredRequestMatchesNothing() {
        assertNull(MoneyCodes.match("BHapoalim", sms, req, req.expiresAt))
        assertNull(MoneyCodes.match("BHapoalim", sms, req, req.expiresAt + 1))
    }

    @Test
    fun badRegexMatchesNothingAndIsNotArmed() {
        val bad = req.copy(regex = "(?P<code>\\d{6})")  // Python syntax: no compile on the JVM
        assertNull(MoneyCodes.compile(bad.regex))
        assertNull(MoneyCodes.match("BHapoalim", sms, bad, now))
        val o = request("code_regex" to JsonPrimitive("(?P<code>\\d{6})"))
        assertNull(MoneyCodes.parse("abc123def456", o, now))
    }

    @Test
    fun noGroupTakesTheMatchDigits() {
        val plain = req.copy(regex = "קוד האימות הוא:?\\s*\\d{4,8}")
        assertEquals("123456", MoneyCodes.match("BHapoalim", sms, plain, now))
    }

    @Test
    fun messagingStyleUsesEachMessageAndTheNewestCode() {
        val style = listOf(
            Message("BHapoalim", "קוד האימות הוא: 111111", now - 30 * 60_000),  // an earlier sign-in, still in the shade
            Message("", "קוד האימות הוא: 222222", now - 10_000),               // no sender: the conversation's title
        )
        val messages = MoneyCodes.messages("BHapoalim", "קוד האימות הוא: 222222", "", style, now)
        assertEquals(2, messages.size)
        assertEquals("BHapoalim", messages[1].sender)
        val found = MoneyCodes.find(messages, req, now)
        assertNotNull(found)
        assertEquals("222222", found!!.second)
        // Only the old message: older than the request, so nothing.
        assertNull(MoneyCodes.find(messages.take(1), req, now))
    }

    @Test
    fun messagingStyleSenderMustMatchToo() {
        val style = listOf(Message("Mom", "קוד האימות הוא: 333333", now))
        assertNull(MoneyCodes.find(MoneyCodes.messages("Family", "", "", style, now), req, now))
    }

    @Test
    fun plainNotificationUsesTitleAsSender() {
        val messages = MoneyCodes.messages("BHapoalim", sms, sms, null, now)
        assertEquals(1, messages.size)  // text and big text the same: once
        assertEquals("123456", MoneyCodes.find(messages, req, now)?.second)
        assertTrue(MoneyCodes.messages("BHapoalim", "", "", null, now).isEmpty())
    }

    @Test
    fun pendingRequestParses() {
        val r = MoneyCodes.parse("abc123def456", request(), now)
        assertNotNull(r)
        assertEquals(listOf("BHapoalim"), r!!.senders)
        assertEquals(now + 200_000, r.expiresAt)
        assertEquals(now - MoneyCodes.LOOKBACK_MS, r.since)
        assertEquals("123456", MoneyCodes.match("BHapoalim", sms, r, now))
    }

    @Test
    fun requestsNotToArm() {
        assertNull(MoneyCodes.parse("x1234567", request("status" to JsonPrimitive("done")), now))
        assertNull(MoneyCodes.parse("x1234567", request("status" to JsonPrimitive("expired")), now))
        assertNull(MoneyCodes.parse("x1234567", request("senders" to EmptyJsonArray), now))
        assertNull(MoneyCodes.parse("x1234567", request("expires_at" to JsonPrimitive(iso(now - 1_000))), now))
    }

    @Test
    fun farOrMissingExpiryIsCapped() {
        val far = MoneyCodes.parse("x1234567", request("expires_at" to JsonPrimitive(iso(now + 3_600_000))), now)
        assertEquals(now + MoneyCodes.MAX_ARMED_MS, far!!.expiresAt)
        val none = MoneyCodes.parse("x1234567", request("expires_at" to JsonPrimitive("garbage")), now)
        assertEquals(now + MoneyCodes.DEFAULT_LIFETIME_MS, none!!.expiresAt)
    }

    @Test
    fun isoTimesWithAndWithoutOffset() {
        assertEquals(now, MoneyCodes.parseInstant(iso(now)))
        assertEquals(now, MoneyCodes.parseInstant(java.time.Instant.ofEpochMilli(now).toString()))
        assertEquals(now, MoneyCodes.parseInstant(java.time.Instant.ofEpochMilli(now).toString().removeSuffix("Z")))
        assertNull(MoneyCodes.parseInstant(""))
        assertNull(MoneyCodes.parseInstant("null"))
    }

    @Test
    fun armedRequestsRoundTripWithoutACode() {
        val text = MoneyCodes.encode(listOf(req))
        assertFalse(text.contains("123456"))
        assertEquals(listOf(req), MoneyCodes.decode(text))
        assertTrue(MoneyCodes.decode("""[{"id":""},3,{"id":"a1234567","senders":[]}]""").isEmpty())
    }

    @Test
    fun moneySignalHidesCodes() {
        val o = MoneySignals.build("0|com.onoapps.cal4u|1|null|10", "com.onoapps.cal4u", "Cal",
            "חיוב בכרטיס", "קוד אימות 482913 לעסקה של 120 ₪", now)!!
        assertEquals("Cal", o.string("app_name"))
        assertEquals("קוד אימות [code] לעסקה של 120 ₪", o.string("text"))
        assertEquals(now, o.longValue("posted_at"))
        assertEquals(setOf("key", "package", "app_name", "title", "text", "posted_at"), o.keys)
        assertNull(MoneySignals.build("k", "com.max.android", "Max", " ", "", now))
        assertTrue("com.ideomobile.hapoalim" in MoneySignals.PACKAGES)
    }

    private fun request(vararg overrides: Pair<String, JsonElement>): JsonObject = JsonObject(
        buildJsonObject {
            put("id", "abc123def456")
            put("status", "pending")
            put("company", "hapoalim")
            put("company_name", "Bank Hapoalim")
            put("senders", buildJsonArray { add("BHapoalim") })
            put("code_regex", hapoalimRegex)
            put("expires_at", iso(now + 200_000))
        } + overrides.toMap()
    )

    /** The server's format: Python isoformat with an offset. */
    private fun iso(ms: Long) = java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSSxxx")
        .format(java.time.OffsetDateTime.ofInstant(java.time.Instant.ofEpochMilli(ms), java.time.ZoneOffset.UTC))
}
