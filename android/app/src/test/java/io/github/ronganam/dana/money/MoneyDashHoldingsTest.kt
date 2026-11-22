package io.github.ronganam.dana.money

import io.github.ronganam.dana.data.money.MoneyFormat
import io.github.ronganam.dana.data.money.AllocationRow
import io.github.ronganam.dana.data.money.DatedValue
import io.github.ronganam.dana.data.money.Flow
import io.github.ronganam.dana.data.money.MoneyDashGeometry
import io.github.ronganam.dana.data.money.MoneyDashGeometry.Box
import io.github.ronganam.dana.data.money.MoneyDashText
import io.github.ronganam.dana.data.money.MoneyDashText.MINUS
import io.github.ronganam.dana.data.money.MoneyMeals
import io.github.ronganam.dana.data.money.MoneyPortfolio
import io.github.ronganam.dana.data.money.arr
import io.github.ronganam.dana.data.money.objects
import io.github.ronganam.dana.data.money.string
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime

/** Investments and the meal card: the payloads parsed, tiles, the value chart, allocation, activity, cycles. */
class MoneyDashHoldingsTest {
    private val today = LocalDate.parse("2026-11-18")
    private val now = ZonedDateTime.of(2026, 11, 18, 14, 0, 0, 0, ZoneId.of("Asia/Jerusalem"))
    private val pf = MoneyPortfolio.parse(MoneyDashFixture.portfolio("portfolio"))
    private val box = Box(600.0, 220.0, 56.0, 12.0, 12.0, 24.0)

    @Test fun accountNames() {
        val a = pf.accounts[0]
        assertEquals("Psagot Trade ••456", MoneyDashText.accountName(a.label, a.number, "investment", a.companyName))
        val src = MoneyDashFixture.portfolio("sources").arr("sources").objects()[0].arr("accounts").objects()[0]
        assertEquals("Psagot Trade ••456", MoneyDashText.accountName(null, src.string("number"), "investment", "Psagot Trade"))
        val ov = MoneyDashFixture.web("overview")
        val cards = ov.arr("cards").objects()
        assertEquals(listOf("Shared card ••4580", "Sample Card ••8763"), cards.map { c ->
            MoneyDashText.accountName(c.string("label"), c.string("number"), "card", c.string("company_name"))
        })
        val bank = ov.arr("banks").objects()[0]
        assertEquals("Sample Bank ••901", MoneyDashText.accountName(bank.string("label"), bank.string("number"), "bank", bank.string("company_name")))
    }

    @Test fun portfolioTiles() {
        val t = MoneyDashText.portfolioTiles(pf, now)
        assertEquals(pf.total.value, t.value.value)
        assertTrue(t.value.sub.startsWith("Updated "))
        assertEquals(pf.total.dayChange, t.today.value)
        assertEquals("in", t.today.tone)
        assertEquals("+1.05% since the morning", t.today.sub)
        assertEquals("+6.98% over what you paid", t.profit.sub)
        assertEquals("in", t.profit.tone)
        assertEquals(pf.total.cash, t.cash.value)
        assertEquals("2.5% of the portfolio", t.cash.sub)
        val empty = MoneyDashText.portfolioTiles(pf.copy(total = pf.total.copy(value = 0, cash = null, dayChange = null, profit = null, cost = null, asOf = null), accounts = emptyList()), now)
        assertNull(empty.today.value)
        assertEquals("", empty.today.tone)
        assertTrue(empty.today.sub.contains("No change"))
        assertNull(empty.profit.value)
        assertEquals(0L, empty.cash.value)
        assertEquals("", empty.cash.sub)
        assertEquals("", empty.value.sub)
    }

    @Test fun numbers() {
        assertEquals("+6.98%", MoneyDashText.pctText(6.98))
        assertEquals("${MINUS}3.62%", MoneyDashText.pctText(-3.62))
        assertEquals("0%", MoneyDashText.pctText(0.0))
        assertEquals("0%", MoneyDashText.pctText(0.001))
        assertEquals("+12.3%", MoneyDashText.pctText(12.345))
        assertEquals("2.5%", MoneyDashText.pctText(2.5, signed = false))
        assertEquals("+2%", MoneyDashText.pctText(2.0))
        assertEquals("", MoneyDashText.pctText(null))
        assertEquals("", MoneyDashText.pctText(Double.NaN))
        assertEquals(listOf("120", "15.5", "1,234.25", ""), listOf(120.0, 15.5, 1234.25, null).map { MoneyDashText.quantityText(it) })
        assertEquals("₪250.00", MoneyFormat.unitPrice(250.0, "ILS"))
        assertEquals("$150.00", MoneyFormat.unitPrice(150.0, "USD"))
        assertEquals("₪0.50", MoneyFormat.unitPrice(0.5, "ILS"))
        assertEquals("₪0.1235", MoneyFormat.unitPrice(0.12345, "NIS"))
        assertEquals("CHF12.50", MoneyFormat.unitPrice(12.5, "CHF"))
        assertEquals("", MoneyFormat.unitPrice(null, "ILS"))
    }

    @Test fun valueChart() {
        val g = MoneyDashGeometry.value(pf.history, box)!!
        assertEquals(pf.history.size, g.points.size)
        assertEquals(box.l, g.points.first().x, 0.01)
        assertEquals(box.w - box.r, g.points.last().x, 0.01)
        assertTrue(g.geo.lo > 0 && g.geo.lo <= pf.history.minOf { it.value } && g.geo.hi >= pf.history.maxOf { it.value })
        assertTrue(g.points.all { it.y >= box.t && it.y <= box.h - box.b })
        assertEquals(pf.history.last().value - pf.history.first().value, g.change)
        assertEquals("Up ₪2,000 (+5.33%) since 9 Oct", MoneyDashText.valueChangeText(g, today))

        assertTrue(MoneyDashGeometry.value(pf.history, box, days = 7)!!.points.all { it.date >= "2026-11-11" })
        assertEquals("", MoneyDashText.valueChangeText(MoneyDashGeometry.value(listOf(DatedValue("2026-11-18", 100)), box), today))
        assertNull(MoneyDashGeometry.value(emptyList(), box))
        assertEquals("Down ₪1 (${MINUS}20%) since 1 Nov",
            MoneyDashText.valueChangeText(MoneyDashGeometry.value(listOf(DatedValue("2026-11-01", 500), DatedValue("2026-11-18", 400)), box), today))
    }

    @Test fun valueChartFlows() {
        val fg = MoneyDashGeometry.value(pf.history, box, flows = pf.flows)!!
        assertEquals(1, pf.flows.size)
        assertEquals(1, fg.marks.size)
        assertEquals(pf.flows[0].date, fg.marks[0].date)
        assertEquals(pf.flows[0].amount, fg.marks[0].flow)
        assertEquals(pf.flows[0].amount, fg.moved)
        assertEquals("Down ₪18,000 since 9 Oct, besides ₪20,000 put in", MoneyDashText.valueChangeText(fg, today))

        val h = listOf(DatedValue("2026-11-01", 1000), DatedValue("2026-11-03", 1600), DatedValue("2026-11-04", 1500))
        val g = MoneyDashGeometry.value(h, box, flows = listOf(Flow("2026-11-02", 500), Flow("2026-11-01", 9), Flow("2026-12-01", 9)))!!
        assertEquals(listOf("2026-11-03"), g.marks.map { it.date })
        assertEquals(500L, g.moved)
        assertEquals("No change since 1 Nov, besides ₪5 put in", MoneyDashText.valueChangeText(g, today))
        val out = MoneyDashGeometry.value(h, box, flows = listOf(Flow("2026-11-03", -300)))
        assertEquals("Up ₪8 since 1 Nov, besides ₪3 taken out", MoneyDashText.valueChangeText(out, today))
    }

    @Test fun allocation() {
        val s = MoneyDashText.allocationSlices(pf.allocation)
        assertEquals(listOf("a0", "a1", "cash"), s.map { it.slot })
        assertEquals(pf.allocation[0].name, s[0].name)
        assertEquals("Cash", s[2].name)
        assertEquals(100.0, s.sumOf { it.width }, 0.5)
        assertEquals("2.5%", s[2].text)
        val folded = MoneyDashText.allocationSlices(listOf(AllocationRow("A", 50), AllocationRow("B", 30), AllocationRow("C", 10),
            AllocationRow("Other holdings", 5), AllocationRow("Cash", 5)), 2)
        assertEquals(listOf(Triple("a0", "A", 50L), Triple("a1", "B", 30L), Triple("rest", "Other holdings", 15L), Triple("cash", "Cash", 5L)),
            folded.map { Triple(it.slot, it.name, it.value) })
        assertTrue(MoneyDashText.allocationSlices(emptyList()).isEmpty())
        assertTrue(MoneyDashText.allocationSlices(listOf(AllocationRow("Cash", 0))).isEmpty())
    }

    @Test fun activity() {
        val tt = pf.trades.map { MoneyDashText.tradeText(it, today) }
        val dep = tt[pf.trades.indexOfFirst { it.kind == "deposit" }]
        assertEquals("Money in", dep.title)
        assertEquals("in", dep.tone)
        assertEquals("", dep.name)
        assertTrue(dep.meta.none { it.contains("units") })
        assertEquals(2000000L, dep.amount)
        val buy = tt.first { it.title == "Bought" && "20 units" in it.meta }
        assertEquals("out", buy.tone)
        assertTrue("fee ₪7.50" in buy.meta)
        assertTrue(buy.name.isNotEmpty())
        val div = tt.first { it.title == "Dividend" }
        assertEquals("in", div.tone)
        assertTrue("tax ₪3.33" in div.meta)
        assertTrue("דיבידנד" in div.meta)
        val i = pf.trades.indexOfFirst { it.kind == "other" }
        assertEquals(pf.trades[i].action, tt[i].title)
        assertEquals("", tt[i].tone)
    }

    // ---------------------------------------------------------------- meal card

    private val ml = MoneyMeals.parse(MoneyDashFixture.meals("meals"))
    private val mday = LocalDate.parse(ml.current!!.start)

    @Test fun mealTiles() {
        val c = ml.current!!
        val t = MoneyDashText.mealTiles(ml, mday)!!
        assertEquals(31050L, t.left.value)
        assertEquals("of ₪1,000 · until ${MoneyDashText.shortDate(c.end, mday)}", t.left.sub)
        assertEquals(0L, t.card.value)
        assertEquals("nothing beyond the budget", t.card.sub)
        assertEquals(1L, t.count.value)
        assertEquals("₪47 from the budget", t.count.sub)
        assertTrue(if (c.runsOut != null) t.pace.text.startsWith("Lasts to ") else t.pace.text.endsWith(" a day"))

        val up = MoneyDashText.mealTiles(ml.copy(current = c.copy(left = 0, count = 3, daysLeft = 2, runsOut = c.start)), mday)!!
        assertEquals("Used up", up.pace.text)
        assertEquals("neg", up.pace.tone)
        assertTrue(up.pace.sub.startsWith("2 days until it renews on "))
        val early = MoneyDashText.mealTiles(ml.copy(current = c.copy(left = 5000, used = 95000, runsOut = c.end)), mday)!!
        assertEquals("Lasts to ${MoneyDashText.shortDate(c.end, mday)}", early.pace.text)
        assertNull(MoneyDashText.mealTiles(ml.copy(current = null), mday))
    }

    @Test fun mealCycles() {
        val mb = MoneyDashText.mealCycleBars(ml.cycles, mday)
        assertEquals(3, mb.size)
        assertTrue(mb[0].start < mb[2].start)
        assertEquals(100.0, mb.maxOf { it.budgetPct + it.cardPct }, 0.001)
        assertEquals(11825L, mb[0].card)
        assertNotNull(mb[0].txn)
        assertTrue(mb[0].billed.startsWith("On your card "))
        assertEquals(5000L, mb[1].card)
        assertEquals("Not on your card yet", mb[1].billed)
        assertNull(mb[1].txn)
        assertEquals("", mb[2].billed)
        assertEquals(MoneyDashText.cycleLabel(ml.cycles[0].start, ml.cycles[0].end, mday), mb[2].label)
        assertTrue(mb[2].label.contains(" – "))
        assertTrue(ml.hasAccount)
    }
}
