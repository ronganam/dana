package io.github.ronganam.dana.money

import io.github.ronganam.dana.data.money.Effort
import io.github.ronganam.dana.data.money.EmptyJson
import io.github.ronganam.dana.data.money.EvidenceLink
import io.github.ronganam.dana.data.money.MoneySave
import io.github.ronganam.dana.data.money.Outcome
import io.github.ronganam.dana.data.money.SavingItem
import io.github.ronganam.dana.data.money.SavingsOverview
import io.github.ronganam.dana.data.money.Target
import io.github.ronganam.dana.data.money.absent
import io.github.ronganam.dana.data.money.arr
import io.github.ronganam.dana.data.money.jsonObjectOf
import io.github.ronganam.dana.data.money.obj
import io.github.ronganam.dana.data.money.objects
import io.github.ronganam.dana.data.money.string
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId

/**
 * The Save view against the server's payloads: `server/tests/fixtures/money_savings_web.json`
 * (synthetic), the same assertions as `server/addons/money/tests/test_web_money.mjs`.
 */
class MoneySaveTest {
    private val f = jsonObjectOf(MoneySaveTest::class.java.classLoader!!.getResource("money_savings_web.json")!!.readText())
    private val sv = SavingsOverview.parse(f.obj("savings")!!)
    private val dayIso = f.obj("savings")!!.string("as_of").take(10)
    private val day = LocalDate.parse(dayIso)
    private val zone = ZoneId.of("Asia/Jerusalem")
    private fun svi(k: String) = sv.items.first { it.key == k }
    private val bill = "bill:סלולר הצפון"
    private val arnona = "arnona:עיריית גבעת אורנים:2027"
    private val cash get() = sv.items.first { it.type == "cash" }.key
    private val fx get() = sv.items.first { it.type == "fx" }.key
    private val spree = "spree:חנות הצפון אונליין:2026-09-14"

    @Test
    fun everyItemIsListedOnceInItsSection() {
        listOf("open", "doing", "done", "dismissed", "snoozed", "accepted", "watching").forEach { st -> assertTrue(st, sv.items.any { it.status == st }) }
        val parts = MoneySave.parts(sv.items, dayIso)
        assertEquals(sv.items.size, parts.sumOf { p -> p.sections.sumOf { it.items.size } })
        assertEquals(listOf("save" to "Ways to save", "watch" to "Spending to watch"), parts.map { it.group to it.label })
        fun secs(g: String) = parts.first { it.group == g }.sections.associate { s -> s.key to s.items.map { it.key } }
        assertEquals(listOf("now", "doing", "done", "choices"), secs("save").keys.toList())
        assertEquals(listOf(arnona, "renewal:ביטוח הדרום חובה:2026-12", "renewal:ביטוח הדרום דירה:2026-11", "cushion", fx, "mortgage", "insurance_review", "subscriptions"), secs("save")["now"])
        assertEquals(listOf(bill), secs("save")["doing"])
        assertEquals(listOf("electricity"), secs("save")["done"])
        assertEquals(listOf(cash, "fees"), secs("save")["choices"])
        assertEquals(mapOf("now" to listOf("overlap:ai", "leak:ענן הצפון", "penalties"), "watching" to listOf("creep:eating_out"), "choices" to listOf("late_night", spree)), secs("watch"))
        // The owner's rule: dismissed, accepted and snoozed always stay listed (as choices).
        sv.items.filter { it.status in setOf("dismissed", "accepted", "snoozed") }.forEach { assertEquals("choices", MoneySave.section(it, dayIso)) }
        val later = svi(arnona).let { it.copy(timing = it.timing!!.copy(from = "2026-12-01")) }
        assertEquals("coming", MoneySave.section(later, dayIso))
        assertEquals("choices", MoneySave.section(svi("fees").copy(status = "paused"), dayIso))
    }

    @Test
    fun tilesAndChips() {
        val t = MoneySave.tiles(sv, day)
        assertEquals("₪2,281–₪2,548", t.could)
        assertEquals("a year · 4 ideas", t.couldSub)
        assertEquals("≈ ₪359 a year", t.savedSub)
        assertEquals("Bank of Israel rate 4.25% (18 Nov) · amounts from your charges", t.source)
        assertEquals("+₪14/yr" to "save", MoneySave.savingChip(svi(arnona))!!.let { it.text to it.kind })
        assertEquals("+₪768–₪888/yr", MoneySave.savingChip(svi(bill))!!.text)
        assertEquals("₪60,000/yr at stake" to "stake", MoneySave.savingChip(svi("mortgage"))!!.let { it.text to it.kind })
        assertEquals("₪615 spent", MoneySave.savingChip(svi(spree))!!.text)
        assertEquals("20-min call", MoneySave.effortText(svi(bill).effort))
        assertEquals("10 min in the app", MoneySave.effortText(svi("cushion").effort))
        assertEquals("15 min online", MoneySave.effortText(svi(arnona).effort))
        assertEquals("1 h comparing", MoneySave.effortText(svi("mortgage").effort))
        assertEquals("10 min to review", MoneySave.effortText(svi("subscriptions").effort))
        assertEquals("1.5 h call", MoneySave.effortText(Effort(90, "call")))
        assertEquals("", MoneySave.effortText(null))
        assertEquals("measured", MoneySave.basisChip(svi("cushion")))
        assertEquals("market", MoneySave.basisChip(svi(bill)))
        assertNull(MoneySave.basisChip(svi("mortgage")))
    }

    @Test
    fun statusLinesActionsAndToasts() {
        assertEquals("Not for you · 18 Nov", MoneySave.statusLine(svi("fees"), day, zone))
        assertEquals("Snoozed until 18 Dec", MoneySave.statusLine(svi(cash), day, zone))
        assertEquals("Worth it to you · 18 Nov", MoneySave.statusLine(svi("late_night"), day, zone))
        assertEquals("Watching since 10 Sep", MoneySave.statusLine(svi("creep:eating_out"), day, zone))
        assertEquals("On it since 18 Nov", MoneySave.statusLine(svi(bill), day, zone))
        assertEquals("Done 18 Nov", MoneySave.statusLine(svi("electricity"), day, zone))
        assertEquals("", MoneySave.statusLine(svi(arnona), day, zone))
        // A body as sorted "k":v pairs, so key order does not matter.
        fun norm(o: JsonObject?) = o?.let { b -> "{" + b.keys.sorted().joinToString(",") { k -> "\"$k\":${b[k]}" } + "}" }
        fun acts(i: SavingItem) = MoneySave.actions(i).map { Triple(it.label, norm(it.body), it.input) }
        assertEquals(listOf(
            Triple("I'm on it", """{"status":"doing"}""", false), Triple("I did it", """{"status":"done"}""", false),
            Triple("Snooze a month", """{"snooze_days":30,"status":"snoozed"}""", false), Triple("Not for me", """{"status":"dismissed"}""", false)
        ), acts(svi(arnona)))
        assertEquals(listOf(Triple("Cut back", null, true), Triple("Worth it", """{"status":"accepted"}""", false)), acts(svi("overlap:ai")))
        assertEquals(listOf(Triple("I did it", """{"status":"done"}""", false), Triple("Not doing it", """{"status":"open"}""", false)), acts(svi(bill)))
        assertEquals(listOf(Triple("Not done", """{"status":"open"}""", false)), acts(svi("electricity")))
        assertEquals(listOf(Triple("Flag again", """{"status":"open"}""", false)), acts(svi("fees")))
        assertEquals(listOf(Triple("Unsnooze", """{"status":"open"}""", false)), acts(svi(cash)))
        assertEquals(listOf(Triple("Edit target", null, true), Triple("Stop tracking", """{"target":null}""", false)), acts(svi("creep:eating_out")))
        assertEquals(listOf(Triple("Dana decides again", """{"status":"open"}""", false)), acts(svi("fees").copy(status = "paused")))
        // Every choice the user made has a way back to Dana.
        sv.items.filter { it.status != "open" }.forEach { i ->
            assertTrue(i.key, MoneySave.actions(i).any { a -> a.body?.let { it.string("status") == "open" || ("target" in it && it.absent("target")) } == true })
        }
        val patched = SavingItem.parse(f.obj("patch")!!.obj("item")!!)
        assertEquals("On it · ${MoneySave.isolateHebrew(patched.title)}", MoneySave.toast(patched, buildJsonObject { put("status", "doing") }, day))
        assertEquals("Snoozed until 18 Dec · ${svi(cash).title}", MoneySave.toast(svi(cash), buildJsonObject { put("status", "snoozed"); put("snooze_days", 30) }, day))
        assertEquals("Target ₪650 a month · Eating out is up: ₪860 a month", MoneySave.toast(svi("creep:eating_out"), buildJsonObject { put("target", 65000) }, day))
        assertTrue(MoneySave.toast(svi("creep:eating_out"), buildJsonObject { put("target", JsonNull) }, day).startsWith("Stopped tracking · "))
        assertEquals("⁨ענן הצפון⁩: marked to cancel",
            MoneySave.toast(svi("subscriptions"), buildJsonObject { put("subitem", buildJsonObject { put("key", "ענן הצפון"); put("choice", "cancel") }) }, day))
        assertTrue(MoneySave.toast(svi(bill), buildJsonObject { put("answers", buildJsonObject { put("lines", 2) }) }, day).startsWith("Answer saved · "))
        assertTrue(MoneySave.toast(svi("fees"), buildJsonObject { put("status", "open") }, day).startsWith("Dana decides again · "))
    }

    @Test
    fun targets() {
        assertEquals(65000L, MoneySave.targetAgorot("650"))
        assertEquals(120000L, MoneySave.targetAgorot("₪1,200"))
        assertEquals(7000L, MoneySave.targetAgorot(" 70 "))
        listOf(" ", "", "6.5", "0", "-5", "abc", "99999999", null).forEach { assertNull(it, MoneySave.targetAgorot(it)) }
        val tp = MoneySave.targetProgress(svi("creep:eating_out").target)!!
        assertEquals(43, tp.bar)
        assertFalse(tp.over)
        assertEquals("₪300 of ₪700 this month", tp.text)
        assertEquals("On track" to "good", tp.track to tp.tone)
        assertEquals("12 days left", tp.left)
        assertEquals(listOf(Triple("Sep", "₪900 of ₪700", false), Triple("Oct", "₪660 of ₪700", true)), tp.months.map { Triple(it.label, it.text, it.under) })
        assertEquals("₪200 saved so far", tp.saved)
        val o = MoneySave.targetProgress(Target(70000, 80000, false, 1, emptyList(), 0))!!
        assertEquals(listOf<Any>(100, true, "Over the target", "bad", "1 day left", ""), listOf<Any>(o.bar, o.over, o.track, o.tone, o.left, o.saved))
        assertEquals("", MoneySave.targetProgress(Target(70000, 1000, null, 29, emptyList(), 0))!!.track)
        assertNull(MoneySave.targetProgress(null))
    }

    @Test
    fun offers() {
        val tel = MoneySave.offerRows(svi(bill).market, day)!!
        assertEquals(listOf("Provider", "Plan", "12-month average", "After the promo", "Conditions", "Source"), tel.columns)
        assertTrue(tel.hasPlan)
        assertEquals(4, tel.rows.size)
        assertEquals("סלולר המרכז" to "גלישה 500", tel.rows[0].name to tel.rows[0].plan)
        assertEquals(listOf(
            Triple("12-month average", "₪39.90 a month", ""), Triple("After the promo", "₪49.90 a month after 6 months", "₪29.90 a month until then"),
            Triple("Conditions", "ללא התחייבות", "")
        ), tel.rows[0].cells.map { Triple(it.label, it.text, it.sub) })
        assertEquals("No promo", tel.rows[1].cells[1].text)
        assertEquals("₪35.00 a month", tel.rows[1].cells[1].sub)
        assertTrue(tel.rows[1].cells[1].muted)
        assertEquals("₪55.00 a month after 12 months", tel.rows[2].cells[1].text)
        assertTrue(tel.rows.all { it.source?.url == "https://example.com/offer" })
        assertEquals(listOf("השוואת מחירים לדוגמה" to "https://example.com/prices"), tel.sources.map { it.title to it.url })
        val fxo = MoneySave.offerRows(svi(fx).market, day)!!
        assertEquals(listOf("Card or wallet", "Kind", "Conversion fee", "Monthly fee", "Source"), fxo.columns)
        assertEquals(listOf(
            listOf("כרטיס הצפון", "Card", "0%", "None"), listOf("ארנק הדרום", "Wallet", "0.5%", "None"), listOf("בנק הגבעה", "Bank account", "1%", "₪9.90 a month")
        ), fxo.rows.map { r -> listOf(r.name) + r.cells.map { it.text } })
        val elo = MoneySave.offerRows(svi("electricity").market, day)!!
        assertEquals(listOf("Supplier", "Plan", "Discount", "Hours", "Meter", "Source"), elo.columns)
        assertEquals(listOf("7% off", "24 שעות · כל השבוע", "Any meter"), elo.rows[0].cells.map { it.text })
        assertEquals(listOf("20% off", "23:00–07:00 · כל השבוע", "Smart meter needed"), elo.rows[1].cells.map { it.text })
        val aro = MoneySave.offerRows(svi(arnona).market, day)!!
        assertEquals(listOf("Result", "Discount", "Deadline", "How", "Source"), aro.columns)
        assertEquals("A discount for paying the year at once", aro.rows.single().name)
        assertEquals(listOf("2% off", "31 Jan 2027", "באתר העירייה"), aro.rows.single().cells.map { it.text })
        assertEquals("example.com", MoneySave.hostOf("https://www.Example.com/a?b"))
        assertEquals("", MoneySave.hostOf("javascript:alert(1)"))
        assertNull(MoneySave.safeSource(buildJsonObject { put("title", "x"); put("url", "javascript:alert(1)") }))
        assertNull(MoneySave.offerRows(null, day))
    }

    @Test
    fun priceChecks() {
        val okm = svi(bill).market!!
        fun mv(m: io.github.ronganam.dana.data.money.Market) = MoneySave.marketView(m, day, zone)!!
        assertEquals("Check prices", mv(okm.copy(state = "none", items = emptyList(), checkedAt = null)).button)
        mv(okm.copy(state = "none", items = emptyList(), needsAnswer = "service")).let { assertNull(it.button); assertEquals("service", it.question) }
        mv(okm.copy(state = "checking")).let { assertNull(it.button); assertTrue(it.text.contains("Checking prices")); assertTrue(it.offers) }
        assertEquals("Check again", mv(okm).button)
        assertEquals("Checked 18 Nov with a web search · confirm on the provider's site", mv(okm).checked)
        assertEquals("", mv(okm).stale)
        assertEquals("prices may have changed", mv(okm.copy(stale = true)).stale)
        mv(okm.copy(state = "failed", error = "the model did not search")).let { assertEquals("Check again", it.button); assertTrue(it.text.contains("the model did not search")) }
        assertEquals("Try again", mv(okm.copy(state = "failed", items = emptyList())).button)
        mv(okm.copy(state = "unavailable", items = emptyList())).let { assertNull(it.button); assertTrue(it.text.contains("aren't available")) }
        assertTrue(mv(svi(arnona).market!!).checked.contains("municipality"))
        assertNull(MoneySave.marketView(null, day))
        assertEquals("checking", MoneySave.checkReply(202, buildJsonObject { put("state", "checking") }).kind)
        assertEquals("checking", MoneySave.checkReply(409, buildJsonObject { put("error", "running") }).kind)
        val q = f.obj("savings")!!.arr("items").objects()
            .first { it.string("key") == bill }.arr("questions").objects()[0]
        MoneySave.checkReply(400, buildJsonObject { put("error", "needs_answer"); put("question", q) }).let { assertEquals("question", it.kind); assertEquals("service", it.question?.id) }
        assertTrue(MoneySave.checkReply(429, buildJsonObject { put("error", "too_soon"); put("retry_after", 290) }).text.endsWith("in 5 min."))
        assertTrue(MoneySave.checkReply(429, buildJsonObject { put("error", "too_soon"); put("retry_after", 21000) }).text.endsWith("in 6 h."))
        assertEquals("wait", MoneySave.checkReply(429, EmptyJson).kind)
        assertTrue(MoneySave.checkReply(503, buildJsonObject { put("error", "unavailable") }).text.contains("aren't available"))
        assertEquals("This idea has no price check.", MoneySave.checkReply(400, buildJsonObject { put("error", "no_check") }).text)
        assertEquals("error", MoneySave.checkReply(0, null).kind)
    }

    @Test
    fun outcomesLinesLinksAndTheSparkline() {
        val c = MoneySave.outcomeText(svi("subscriptions").outcome)!!
        assertEquals(listOf("good", "Saved ₪60 so far", "≈ ₪359 a year", svi("subscriptions").outcome!!.detail), listOf(c.tone, c.title, c.sub, c.detail))
        MoneySave.outcomeText(svi(bill).outcome)!!.let { assertEquals("wait", it.tone); assertEquals("Waiting for the next charge", it.detail) }
        MoneySave.outcomeText(Outcome("no_change", 0, 0, "Still charged"))!!.let { assertEquals("warn" to "Still charged", it.tone to it.detail) }
        assertNull(MoneySave.outcomeText(null))
        val subs = svi("subscriptions").subitems.map { MoneySave.subitemText(it, day) }
        assertEquals(Triple("₪74.20 / month", "₪890 a year", "next 12 Dec"), subs[0].let { Triple(it.amount, it.yearly, it.next) })
        assertEquals(Triple("₪480.00 / year", "₪480 a year", "next 15 Mar 2027"), subs[3].let { Triple(it.amount, it.yearly, it.next) })
        assertEquals("No charge since you cancelled", subs[4].next)
        MoneySave.link(svi(bill).evidence.link)!!.let { assertEquals(listOf("transactions", "סלולר הצפון", "See these charges"), listOf(it.view, it.q, it.label)) }
        assertEquals("subscriptions", MoneySave.link(svi("subscriptions").evidence.link)!!.category)
        assertEquals("investments" to "Open Investments", MoneySave.link(svi(cash).evidence.link)!!.let { it.view to it.label })
        assertEquals("cashflow", MoneySave.link(svi("cushion").evidence.link)!!.view)
        assertEquals("Open Transactions", MoneySave.link(svi(fx).evidence.link)!!.label)
        assertNull(MoneySave.link(EvidenceLink("nowhere", null, null)))
        assertNull(MoneySave.link(null))
        val spk = MoneySave.spark(svi(bill).evidence.series, 180f, 44f, today = day)!!
        assertEquals(14, spk.count)
        assertEquals(3f, spk.points.first().first)
        assertEquals(177f, spk.points.last().first)
        assertEquals("10 Oct 2025 – 10 Nov", spk.range)
        assertTrue(spk.points.last().second < spk.points.first().second)
        assertNull(MoneySave.spark(svi("renewal:ביטוח הדרום חובה:2026-12").evidence.series, today = day))
        assertNull(MoneySave.spark(emptyList(), today = day))
    }
}
