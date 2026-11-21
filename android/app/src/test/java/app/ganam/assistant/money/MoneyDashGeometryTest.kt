package app.ganam.assistant.money

import app.ganam.assistant.data.money.CashflowMonth
import app.ganam.assistant.data.money.MoneyBalance
import app.ganam.assistant.data.money.MoneyCashflow
import app.ganam.assistant.data.money.MoneyDashGeometry
import app.ganam.assistant.data.money.MoneyDashGeometry.Box
import app.ganam.assistant.data.money.MoneyDashText
import app.ganam.assistant.data.money.MoneyDashText.MINUS
import app.ganam.assistant.data.money.MoneyInsights
import app.ganam.assistant.data.money.MoneyMerchants
import app.ganam.assistant.data.money.MoneyMonths
import app.ganam.assistant.data.money.MoneyOverview
import app.ganam.assistant.data.money.MoneyPace
import app.ganam.assistant.data.money.MoneyTrends
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

/** The charts' geometry (pace, 12 months, cash flow, balance, ticks, sparklines), against the web's numbers on the same payloads. */
class MoneyDashGeometryTest {
    private val ov = MoneyOverview.parse(MoneyDashFixture.web("overview"))
    private val pace = MoneyPace.parse(MoneyDashFixture.web("pace"))
    private val months = MoneyMonths.parse(MoneyDashFixture.web("months"))
    private val trends = MoneyTrends.parse(MoneyDashFixture.web("trends"))
    private val cf = MoneyCashflow.parse(MoneyDashFixture.web("cashflow"))
    private val bal = MoneyBalance.parse(MoneyDashFixture.web("balance"))

    @Test fun niceTicks() {
        assertEquals(listOf(-4000000.0, -2000000.0, 0.0, 2000000.0, 4000000.0), MoneyDashGeometry.niceTicks(-2666133.0, 3768616.0, 4))
        assertEquals(listOf(5.0, 5.5, 6.0, 6.5, 7.0), MoneyDashGeometry.niceTicks(5.0, 7.0, 4))
        assertEquals(listOf(0.0, 25.0, 50.0, 75.0, 100.0), MoneyDashGeometry.niceTicks(0.0, 0.0))
    }

    @Test fun pace() {
        val g = MoneyDashGeometry.pace(pace, Box(640.0, 200.0, 52.0, 14.0, 14.0, 24.0))
        assertEquals(30, g.n)
        assertEquals(18, g.thisLine.size)
        assertEquals(30, g.typicalLine.size)
        assertEquals(52.0, g.thisLine[0].x, 0.0)
        assertEquals(500000.0, g.top, 0.0)
        assertEquals(listOf("₪0", "₪2.5k", "₪5k"), g.ticks.map { it.text })
        assertEquals(176.0, g.ticks[0].y, 0.0)
        assertEquals(14.0, g.ticks[2].y, 0.0)
        assertEquals(52 + (17.0 / 29) * 574, g.todayX!!, 0.11)
        assertEquals(g.todayX!!, g.end!!.x, 0.0)
        assertEquals(60, g.band.size)
        assertEquals(17, g.indexAt(g.todayX!! + 3))
        assertEquals(0, g.indexAt(-50.0))
        assertEquals(29, g.indexAt(9999.0))
        assertEquals(MoneyDashGeometry.PaceAt(18, 401794, 0, 0, 108000), MoneyDashGeometry.paceAt(pace, 17))
        assertNull(MoneyDashGeometry.paceAt(pace, 20).thisMonth)
        assertEquals(listOf("1", "5", "10", "15", "20", "25", "30"), g.xLabels.map { it.text })
        assertEquals(ov.everyday.total, pace.thisMonth[17])
    }

    @Test fun pastPaceAndNoTypical() {
        val gp = MoneyDashGeometry.pace(pace.copy(today = null, thisMonth = pace.high!!), Box(640.0, 200.0, 52.0, 14.0, 14.0, 24.0))
        assertNull(gp.todayX)
        assertEquals(30, gp.thisLine.size)
        val gz = MoneyDashGeometry.pace(MoneyPace("2026-12", "December 2026", 31, 1, listOf<Long?>(0) + List<Long?>(30) { null }, null, null, null, 0), Box(640.0, 200.0, 52.0, 14.0, 14.0, 24.0))
        assertEquals(100.0, gz.top, 0.0)
        assertTrue(gz.typicalLine.isEmpty() && gz.band.isEmpty())
        assertEquals(31, gz.n)
    }

    @Test fun months() {
        val box = Box(640.0, 180.0, 46.0, 6.0, 8.0, 22.0)
        val mg = MoneyDashGeometry.months(months, box)
        val nov = mg.bars.last()
        assertEquals(12, mg.bars.size)
        assertEquals("2025-12", mg.bars[0].month)
        assertEquals("2026-11", nov.month)
        assertEquals(1000000.0, mg.top, 0.0)
        assertEquals(listOf("food", "transport", "bills", "health", "family", "people", "other", "uncategorized"), nov.segments.map { it.key })
        assertEquals(25000L + 1200L, nov.segments.first { it.key == "people" }.value + nov.segments.first { it.key == "other" }.value)
        assertTrue(mg.bars.all { b -> b.segments.sumOf { it.value } == b.total })
        assertTrue(nov.segments.withIndex().all { (i, s) ->
            if (i == 0) abs(s.y + s.h - box.base) < 0.11 else s.h == 0.0 || abs(s.y + s.h + 2 - nov.segments[i - 1].y) < 0.21
        })
        assertEquals(1, nov.segments.count { it.top })
        assertEquals(nov.segments.last { it.h >= 1 }, nov.segments.first { it.top })
        assertTrue(mg.bars[1].segments.isEmpty())
        assertEquals(listOf("food", "housing", "transport", "bills", "shopping", "health", "family", "people", "other", "uncategorized"), mg.legend.map { it.key })
        assertEquals("Food", mg.legend[0].label)
        assertEquals("Other", mg.legend.first { it.key == "other" }.label)
        assertTrue(mg.xLabels[11].show && mg.xLabels[11].text == "Nov" && !mg.xLabels[10].show)
        assertEquals(6, mg.xLabels.count { it.show })
        assertEquals("other", MoneyDashGeometry.seriesOf("finance"))
        assertEquals("uncategorized", MoneyDashGeometry.seriesOf(null))
    }

    @Test fun trendsAsMonths() {
        val tm = MoneyDashGeometry.trendsAsMonths(trends)
        assertEquals(12, tm.months.size)
        assertEquals("2026-11", tm.months[11].month)
        assertEquals("2025-12", tm.months[0].month)
        assertEquals("Dec 2025", tm.months[0].label)
        assertEquals(trends.groups.map { it.key }, tm.groups.map { it.key })
        assertTrue(tm.months.all { m -> m.groups.values.sum() == m.total })
        assertNull(tm.months[11].groups["housing"])
        assertEquals(450000L, tm.months[10].groups["housing"])
        assertEquals("2026-10", MoneyDashGeometry.trendsAsMonths(MoneyTrends.parse(MoneyDashFixture.web("trends_past"))).months[11].month)
    }

    @Test fun sparkBars() {
        val (max, bars) = MoneyDashGeometry.sparkBars(listOf(0, 10, 20, 0), 40.0, 20.0, 0.0)
        assertEquals(20L, max)
        assertEquals(listOf(0.0 to 0.0, 10.0 to 10.0, 20.0 to 20.0, 30.0 to 0.0), bars.map { it.x to it.h })
        assertTrue(bars[3].last && !bars[2].last)
        assertEquals(1.0, MoneyDashGeometry.sparkBars(listOf(1, 1000), 40.0, 20.0).second[0].h, 0.0)
    }

    @Test fun cashflow() {
        val g = MoneyDashGeometry.cashflow(cf, Box(640.0, 220.0, 52.0, 8.0, 10.0, 22.0))
        val nov = g.bars[11]
        assertEquals(2000000.0, g.up, 0.0)
        assertEquals(1000000.0, g.down, 0.0)
        assertEquals(135.3, g.zero, 0.0)
        assertEquals(listOf("₪20k", "₪10k", "₪0", "$MINUS₪5k", "$MINUS₪10k"), g.ticks.map { it.text })
        assertEquals(g.zero, g.ticks.first { it.zero }.y, 0.0)
        assertEquals(1500000L, nov.income!!.value)
        assertTrue(nov.income!!.top)
        assertEquals(listOf("fixed", "yearly", "everyday"), nov.segments.map { it.key })
        assertEquals(g.zero + 1, nov.segments[0].y, 0.0)
        assertEquals(listOf(false, false, true), nov.segments.map { it.bottom })
        assertEquals(cf.months[11].spent, nov.segments.sumOf { it.value })
        assertEquals(cf.months[11].net, nov.netValue)
        assertEquals(12, g.netLine.size)
        assertEquals(g.zero, g.bars[3].net.y, 0.0)
        assertFalse(nov.complete)
        assertTrue(g.bars[10].complete)
        assertEquals(listOf("income", "fixed", "yearly", "everyday", "net"), g.legend.map { it.key })
        val zeroFirst = MoneyDashGeometry.cashflow(cf.copy(months = listOf(cf.months[0].copy(income = 0, spent = 0, net = 0, fixed = 0, yearly = 0, everyday = 0, oneOff = 0, setAside = 0), cf.months[11])), Box(640.0, 220.0, 52.0, 8.0, 10.0, 22.0))
        assertFalse(zeroFirst.bars[0].showNet)
    }

    @Test fun cashflowSetAside() {
        val m = CashflowMonth("2026-10", "Oct 2026", true, income = 1000000, fixed = 200000, yearly = 0, everyday = 300000, oneOff = 100000, spent = 600000, setAside = 250000, net = 400000, savingsRate = null)
        val g = MoneyDashGeometry.cashflow(cf.copy(months = listOf(m)), Box(640.0, 220.0, 56.0, 12.0, 12.0, 24.0))
        val b = g.bars[0]
        assertEquals(250000L, b.setAside!!.value)
        assertTrue(b.setAside!!.x > b.x + b.w)
        assertEquals(g.zero + 1, b.setAside!!.y, 0.0)
        assertEquals(listOf("fixed", "everyday", "one_off"), b.segments.map { it.key })
        assertTrue(g.legend.any { it.key == "set_aside" })
    }

    @Test fun balance() {
        val box = Box(640.0, 220.0, 56.0, 12.0, 12.0, 24.0)
        val g = MoneyDashGeometry.balance(bal, box)!!
        assertEquals(56.0, g.history[0].x, 0.0)
        assertEquals(bal.history.size, g.history.size)
        assertEquals(bal.forecast!!.series.size, g.forecast.size)
        assertTrue(g.band.isNotEmpty())
        assertEquals(-4000000.0, g.lo, 0.0)
        assertEquals(4000000.0, g.hi, 0.0)
        assertTrue(g.zeroY != null && g.ticks.any { it.zero })
        assertEquals("2026-11-25", g.low!!.date)
        assertEquals(681867L, g.low!!.value)
        assertTrue(g.low!!.x > g.todayX!!)
        assertEquals(640.0 - 12, g.points.last().x, 0.11)
        assertEquals(listOf("Sep", "Oct", "Nov", "Dec", "Jan", "Feb"), g.xLabels.map { it.text })
        assertEquals((bal.history.map { it.date } + bal.forecast!!.series.map { it.date }).toSet().size, g.points.size)
        assertFalse(g.points.first { it.date == "2026-11-18" }.forecast)
        assertEquals(3768616L, g.points.first { it.date == "2027-02-16" }.high)
        assertEquals("2026-11-25", g.points[g.indexAt(g.low!!.x)].date)

        val b30 = MoneyDashGeometry.balance(bal, box, historyDays = 30)!!
        assertEquals("2026-10-19", b30.points[0].date)
        assertEquals(31 + 90, b30.points.size)
        assertNull(MoneyDashGeometry.balance(MoneyBalance(emptyList(), null), box))
    }

    @Test fun comingUpSpark() {
        val series = MoneyDashText.upcoming(bal.forecast, 45).series
        val g = MoneyDashGeometry.balance(MoneyBalance(emptyList(), bal.forecast!!.copy(series = series)), Box(300.0, 60.0, 0.0, 0.0, 0.0, 0.0),
            includeZero = false, lowOverride = bal.forecast!!.soonLow, useLowOverride = true)!!
        assertEquals(46, g.forecast.size)
        assertTrue(g.ticks.isEmpty() && g.history.isEmpty())
        assertEquals(681867.0, g.lo, 0.0)
        assertEquals(bal.forecast!!.soonLow!!.date, g.low!!.date)
    }

    @Test fun otherPayloads() {
        val insights = MoneyInsights.parse(MoneyDashFixture.web("insights"))
        assertTrue(insights.isNotEmpty() && insights.all { it.title.isNotEmpty() })
        val merchants = MoneyMerchants.parse(MoneyDashFixture.web("merchants"))
        assertTrue(merchants.isNotEmpty())
        assertEquals("2026-11", ov.month)
        assertTrue(ov.inProgress)
    }
}
