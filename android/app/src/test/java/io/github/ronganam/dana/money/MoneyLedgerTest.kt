package io.github.ronganam.dana.money

import io.github.ronganam.dana.data.money.MoneyFormat
import io.github.ronganam.dana.data.money.MoneyLedger
import io.github.ronganam.dana.data.money.MoneyLedger.MINUS
import io.github.ronganam.dana.data.money.Txn
import io.github.ronganam.dana.data.money.arr
import io.github.ronganam.dana.data.money.flag
import io.github.ronganam.dana.data.money.intValue
import io.github.ronganam.dana.data.money.jsonObjectOf
import io.github.ronganam.dana.data.money.longValue
import io.github.ronganam.dana.data.money.obj
import io.github.ronganam.dana.data.money.objects
import io.github.ronganam.dana.data.money.str
import io.github.ronganam.dana.data.money.string
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.LocalDate

/**
 * Transactions, the detail and Accounts against the server's own payloads:
 * `server/tests/fixtures/money_web.json` (a synthetic dataset,
 * today 18 Nov 2026), the same assertions as its `test_web_money.mjs`.
 */
class MoneyLedgerTest {
    init { MoneyFormat.use("ILS") }  // the fixtures' user pays in shekels

    private val f = jsonObjectOf(MoneyLedgerTest::class.java.classLoader!!.getResource("money_web.json")!!.readText())
    private fun body(k: String) = f.obj(k)!!.obj("body")!!
    private val today = LocalDate.parse("2026-11-18")
    private val now = Instant.parse("2026-11-18T07:01:00Z")
    private val list = MoneyLedger.merge(Txn.parseList(body("transactions")), Txn.parseList(body("transactions_next")))
    private fun by(m: String) = list.first { it.merchant == m }
    private val taxonomy = MoneyLedger.taxonomy(body("categories"))
    private fun iso(s: String) = "⁨$s⁩"

    @Test
    fun moneyAndDates() {
        assertEquals("$MINUS₪1,500.00", MoneyFormat.amount(-150000))
        assertEquals("+₪15,000.00", MoneyFormat.amount(1500000, sign = MoneyFormat.Sign.PLUS))
        assertEquals("₪0.00", MoneyFormat.amount(0, sign = MoneyFormat.Sign.PLUS))
        assertEquals("₪709.00", MoneyFormat.amount(-70900, sign = MoneyFormat.Sign.NONE))
        assertEquals("₪4,777", MoneyFormat.amount(477684, cents = false))
        assertEquals("₪100", MoneyFormat.amount(10000, cents = null))
        assertEquals("₪15.94", MoneyFormat.amount(1594, cents = null))
        assertEquals("€35.00", MoneyFormat.foreign(-3500, "EUR"))
        assertEquals("CHF5.00", MoneyFormat.foreign(500, "CHF"))
        assertEquals("Today", MoneyLedger.dayLabel("2026-11-18", today))
        assertEquals("Yesterday", MoneyLedger.dayLabel("2026-11-17", today))
        assertEquals("Mon 16 Nov", MoneyLedger.dayLabel("2026-11-16", today))
        assertEquals("Mon 1 Dec 2025", MoneyLedger.dayLabel("2025-12-01", today))
        assertEquals("10 Dec", MoneyLedger.shortDate("2026-12-10", today))
        assertEquals("Tue 17 Nov 2026", MoneyLedger.longDate("2026-11-17"))
        assertEquals("08:10", MoneyLedger.timeOf("2026-11-18T08:10:00+02:00"))
        assertEquals("2 h ago", MoneyLedger.ago("2026-11-18T05:01:00+00:00", now))
        assertEquals("just now", MoneyLedger.ago("2026-11-18T07:00:30+00:00", now))
        assertEquals("in 10 h", MoneyLedger.until("2026-11-18T17:00:00+00:00", now))
        assertEquals("in 19 min", MoneyLedger.until("2026-11-18T07:20:00+00:00", now))
        assertEquals("3:41", MoneyLedger.countdown("2026-11-18T07:04:41Z", now))
        assertEquals("0:00", MoneyLedger.countdown("2026-11-18T06:00:00Z", now))
        assertEquals("45 s", MoneyLedger.waitText(45))
        assertEquals("2 min", MoneyLedger.waitText(61))
    }

    @Test
    fun daysAddUpToTheOverview() {
        assertEquals(21, list.size)
        assertEquals(21, MoneyLedger.merge(list, Txn.parseList(body("transactions_next"))).size)
        val days = MoneyLedger.groupByDay(list)
        assertEquals(list.map { it.purchaseDate }.toSet().size, days.size)
        assertEquals("2026-11-18", days.first().date)
        assertEquals(list.map { it.id }, days.flatMap { d -> d.items.map { it.id } })
        fun day(d: String) = days.first { it.date == d }
        assertEquals(150000L + 20000 + 20000 + 4500, day("2026-11-17").spent)
        assertEquals(-5000L, day("2026-11-14").spent)
        listOf("2026-11-15", "2026-11-02", "2026-11-04", "2026-11-11").forEach { assertEquals(it, 0L, day(it).spent) }
        assertEquals(1500000L, day("2026-11-10").income)
        val ov = body("overview")
        assertEquals(ov.obj("spent")!!.longValue("total"), days.sumOf { it.spent })
        assertEquals(ov.obj("income")!!.longValue("total"), days.sumOf { it.income })
        val uncat = list.filter(MoneyLedger::needsCategory)
        assertEquals(3, uncat.size)
        assertEquals(ov.intValue("uncategorized"), uncat.count { it.spending })
    }

    @Test
    fun tonesBadgesAndChips() {
        assertEquals("out", MoneyLedger.amountTone(by("סופר פארם")))
        assertEquals("in", MoneyLedger.amountTone(by("משכורת נוגה תעשיות")))
        assertEquals("in", MoneyLedger.amountTone(list.first { it.flow == "refund" }))
        listOf("כאל", "העברה לחיסכון", "משיכת מזומן").forEach { assertEquals(it, "muted", MoneyLedger.amountTone(by(it))) }
        assertEquals("muted", MoneyLedger.amountTone(list.first { it.status == "unmatched" }))
        fun b(t: Txn) = MoneyLedger.badges(t, "Cal").map { it.type to it.text }
        assertEquals(listOf("provisional" to "Waiting for Cal"), b(by("קפה קפה")))
        assertEquals(listOf("pending" to "Pending"), b(list.first { it.status == "pending" }))
        assertEquals(listOf("unmatched" to "Not charged?"), b(list.first { it.status == "unmatched" }))
        assertEquals(listOf("installment" to "1/4"), b(by("ביטוח רכב ישיר")))
        assertEquals(listOf("foreign" to "$5.00"), b(list.first { t -> t.badges.any { it.type == "foreign" } }))
        assertEquals(listOf("refund" to "Refund"), b(list.first { it.flow == "refund" }))
        assertEquals("Waiting for the card company", MoneyLedger.badges(by("קפה קפה"), "").first().text)
        assertEquals("Card bill", MoneyLedger.flowChip(by("כאל")))
        assertEquals("Between your accounts", MoneyLedger.flowChip(by("העברה לחיסכון")))
        assertNull(MoneyLedger.flowChip(by("סופר פארם")))
        assertEquals("Yearly", MoneyLedger.kindBadge(by("ביטוח רכב ישיר"))?.text)
        assertEquals("fixed", MoneyLedger.kindBadge(by("NETFLIX.COM"))?.type)
        assertNull(MoneyLedger.kindBadge(by("סופר פארם")))
        assertEquals("set by you", MoneyLedger.categorySourceLabel("user"))
        assertEquals("Dana's guess", MoneyLedger.categorySourceLabel("model"))
    }

    @Test
    fun theDetail() {
        val prov = Txn.parse(body("transaction_provisional"))
        assertTrue(MoneyLedger.statusExplain(prov, "Cal").contains("at 08:10"))
        assertTrue(MoneyLedger.statusExplain(prov, "Cal").contains("when Cal lists it"))
        assertTrue(MoneyLedger.statusExplain(by("כאל"), null).contains("counted twice"))
        assertEquals("", MoneyLedger.statusExplain(Txn.parse(body("transaction_foreign")), "Cal"))
        val choices = MoneyLedger.flowChoices(Txn.parse(body("transaction_foreign")))
        assertEquals(listOf("purchase", "transfer_out", "investment", "own_transfer", "loan", "fee", "cash"), choices.map { it.first })
        assertEquals("Money set aside", choices[2].second)
        assertEquals("My own money (not spending)", choices[3].second)
        assertEquals("money received", MoneyLedger.flowLabel("transfer_in"))
        assertEquals(listOf("yes", "no", "auto"), listOf(true, false, null).map { MoneyLedger.markState(it) })
        assertEquals("Dana counts it as everyday spending.", MoneyLedger.kindText("everyday"))
        assertEquals("${iso("ONLINE COURSE CO")} now counts as money set aside", MoneyLedger.flowText(body("patch_flow")))
        assertEquals("${iso("ONLINE COURSE CO")}: a one-off", MoneyLedger.markText(body("patch_one_off"), "one_off", true))
        assertEquals(
            "${iso("ONLINE COURSE CO")}: not a yearly charge (3 charges)",
            MoneyLedger.markText(JsonObject(body("patch_one_off") + ("changed" to JsonPrimitive(3))), "yearly", false)
        )
        assertEquals("${iso("ONLINE COURSE CO")}: Dana decides if it's a one-off", MoneyLedger.markText(body("patch_one_off"), "one_off", null))
        val pc = body("patch_category")
        assertEquals("${iso("קפה קפה")} → Restaurants & cafés, now and from now on",
            MoneyLedger.undoText(pc, pc.obj("transaction")!!.string("category_label"), true))
        assertTrue(MoneyLedger.undoText(JsonObject(pc + ("changed" to JsonPrimitive(3))), "Food", true).endsWith("(3 charges)"))
        assertEquals("${iso("קפה קפה")} → Groceries", MoneyLedger.undoText(pc, "Groceries", false))
        assertEquals("Note saved", MoneyLedger.undoText(body("patch_note"), null))
        assertEquals("Note removed", MoneyLedger.undoText(buildJsonObject { put("transaction", buildJsonObject { put("note", "") }) }, null))
    }

    @Test
    fun errorsInWords() {
        val inv = body("error_invalid")
        assertEquals(inv.string("message"), MoneyLedger.errorText(400, inv.string("error"), inv.string("message")))
        assertTrue(MoneyLedger.errorText(403, "not_connected").contains("turned off"))
        assertEquals("Synced a moment ago. Try again in 7 min.", MoneyLedger.errorText(429, "too_soon", retryAfter = 420))
        assertEquals("Cal wants a new password", MoneyLedger.errorText(409, "needs_attention", reason = "Cal wants a new password"))
        assertTrue(MoneyLedger.errorText(0, null).startsWith("Can't reach Dana"))
        assertTrue(MoneyLedger.errorText(400, "bad_code").contains("4 to 8 digits"))
        assertEquals("Something went wrong (HTTP 500).", MoneyLedger.errorText(500, null))
    }

    @Test
    fun filtersAndThePicker() {
        val q = MoneyLedger.query(" קפה קפה ", null, null, "", "250", false, "2026-11").filterValues { it != null }
        assertEquals(mapOf("period" to "2026-11", "q" to "קפה קפה", "max" to "250"), q)
        assertEquals(mapOf("q" to "wolt"), MoneyLedger.query("wolt", null, null, "", "", true, "2026-11").filterValues { it != null })
        assertEquals("₪100 – ₪500", MoneyLedger.amountChip("100", "500"))
        assertEquals("₪100 or more", MoneyLedger.amountChip("100", ""))
        assertEquals("up to ₪49.90", MoneyLedger.amountChip("", "49.9"))
        assertNull(MoneyLedger.amountChip("", "abc"))
        assertEquals("Restaurants & cafés", MoneyLedger.categoryLabel(taxonomy, "restaurants"))
        assertEquals("Food", MoneyLedger.categoryLabel(taxonomy, "food"))
        assertEquals("Uncategorized", MoneyLedger.categoryLabel(taxonomy, "uncategorized"))
        assertEquals("food", MoneyLedger.groupOf(taxonomy, "restaurants"))
        assertNull(MoneyLedger.groupOf(taxonomy, "nope"))
        fun pk(s: String, income: Boolean = false) = MoneyLedger.pickerGroups(taxonomy, s, income).map { g -> g.key to g.categories.map { it.key } }
        assertEquals(listOf("food" to listOf("restaurants")), pk("מסעדות"))
        assertEquals(listOf("food" to listOf("groceries", "restaurants", "delivery")), pk("FOOD"))
        assertEquals(listOf("transport" to listOf("fuel")), pk("fuel"))
        assertEquals("food", pk("").first().first)
        assertEquals("income", pk("").last().first)
        assertEquals("income", pk("", income = true).first().first)
        assertEquals(0, pk("zzz").size)
        assertEquals(taxonomy.size, pk("").size)
    }

    @Test
    fun accountsAndSources() {
        val ov = body("overview")
        val cards = ov.arr("cards").objects()
        assertEquals(listOf("Shared card ••4580", "Sample Card ••8763"), cards.map {
            MoneyLedger.accountName("card", it.str("number"), it.str("label"), it.str("company_name"))
        })
        val bank = ov.arr("banks").objects()[0]
        assertEquals("Sample Bank ••901", MoneyLedger.accountName("bank", bank.str("number"), bank.str("label"), bank.str("company_name")))
        val src = ov.arr("sources").objects()
        val ok = src[0]
        val fail = src[1]
        fun st(o: JsonObject) = MoneyLedger.sourceStatus(o.string("state"), o.str("state_reason"), o.str("last_ok_at"), o.flag("can_sync"), now)
        assertEquals(Triple("ok", "Synced 2 h ago", ""), st(ok).let { Triple(it.tone, it.label, it.detail) })
        assertEquals("warn", st(fail).tone)
        assertEquals("Last sync failed", st(fail).label)
        assertEquals("bank worker not reachable · last good sync 14 h ago", st(fail).detail)
        assertEquals("Waiting for the SMS code", MoneyLedger.sourceStatus("needs_code", null, null, false, now).label)
        assertEquals("Login needs fixing", MoneyLedger.sourceStatus("needs_attention", "Cal wants a new password", null, false, now).label)
        assertEquals("Stopped: sign in again yourself", MoneyLedger.sourceStatus("needs_attention", null, null, true, now).label)
        assertEquals("bad", MoneyLedger.sourceStatus("needs_attention", null, null, true, now).tone)
        assertEquals("busy", MoneyLedger.sourceStatus("syncing", null, null, false, now).tone)
        assertEquals("busy", MoneyLedger.sourceStatus("new", null, null, false, now).tone)
    }
}
