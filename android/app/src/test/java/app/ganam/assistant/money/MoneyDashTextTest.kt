package app.ganam.assistant.money

import app.ganam.assistant.data.money.MoneyBalance
import app.ganam.assistant.data.money.MoneyCashflow
import app.ganam.assistant.data.money.MoneyDashText
import app.ganam.assistant.data.money.MoneyDashText.MINUS
import app.ganam.assistant.data.money.MoneyDashText.Sign
import app.ganam.assistant.data.money.MoneyForecast
import app.ganam.assistant.data.money.MoneyOverview
import app.ganam.assistant.data.money.MoneyRecurring
import app.ganam.assistant.data.money.MoneyTrends
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

/** The dashboards' words, against the web's expectations (the server's test_web_money.mjs) on the same payloads. */
class MoneyDashTextTest {
    private val today = LocalDate.parse("2026-11-18")
    private val ov = MoneyOverview.parse(MoneyDashFixture.web("overview"))
    private val past = MoneyOverview.parse(MoneyDashFixture.web("overview_past"))
    private val cf = MoneyCashflow.parse(MoneyDashFixture.web("cashflow"))
    private val bal = MoneyBalance.parse(MoneyDashFixture.web("balance"))
    private val trends = MoneyTrends.parse(MoneyDashFixture.web("trends"))
    private val rec = MoneyRecurring.parse(MoneyDashFixture.web("recurring"))

    @Test fun money() {
        assertEquals("$MINUS₪1,500.00", MoneyDashText.money(-150000))
        assertEquals("+₪15,000.00", MoneyDashText.money(1500000, Sign.PLUS))
        assertEquals("₪0.00", MoneyDashText.money(0, Sign.PLUS))
        assertEquals("₪709.00", MoneyDashText.money(-70900, Sign.NONE))
        assertEquals("₪4,777", MoneyDashText.whole(477684))
        assertEquals("₪1,234,567.89", MoneyDashText.money(123456789))
    }

    @Test fun compact() {
        assertEquals(listOf("₪0", "₪850", "₪4.8k", "₪20k", "₪1.5M", "₪5k"),
            listOf(0.0, 85000.0, 480000.0, 2000000.0, 150000000.0, 500000.0).map { MoneyDashText.compact(it) })
        assertEquals("$MINUS₪10k", MoneyDashText.compact(-1000000.0))
    }

    @Test fun dates() {
        assertEquals("10 Dec", MoneyDashText.shortDate("2026-12-10", today))
        assertEquals("1 Dec 2025", MoneyDashText.shortDate("2025-12-01", today))
        assertEquals("Nov 2026", MoneyDashText.monthLabel("2026-11", short = true))
        assertEquals("2026-01", MoneyDashText.shiftMonth("2025-12", 1))
    }

    @Test fun netTile() {
        val t = MoneyDashText.netTile(ov, cf)
        assertEquals(MoneyDashText.NetTile("Net so far", 1022316, "in", "Kept 68% of income", "Earned ₪15,000 · spent ₪4,777", ""), t)
        assertEquals("Net", MoneyDashText.netTile(past, cf).label)
        assertEquals("₪500 more out than in", MoneyDashText.netTile(past.copy(net = -50000, incomeMonth = 100000), null).rate)
        assertEquals("No income yet this month", MoneyDashText.netTile(ov.copy(incomeMonth = 0, net = -10), null).rate)
    }

    @Test fun everydayTile() {
        assertEquals(MoneyDashText.EverydayTile(401794, "On track for ₪4,018", "", ""), MoneyDashText.everydayTile(ov.everyday, true))
        assertEquals("", MoneyDashText.everydayTile(past.everyday, false).track)
        assertEquals(MoneyDashText.EverydayTile(100000, "On track for ₪3,000", "typical ₪2,000", "up"),
            MoneyDashText.everydayTile(ov.everyday.copy(total = 100000, onTrack = 300000, typical = 200000), true))
    }

    @Test fun balanceTile() {
        assertEquals(MoneyDashText.BalanceTile(1234567, "Lowest in the next 45 days: ₪6,819 on 25 Nov", "₪66,346 by 17 May 2027", ""),
            MoneyDashText.balanceTile(ov.forecast, today))
        val above = ov.forecast!!.copy(soonLow = ov.forecast!!.soonLow!!.copy(balance = 1234567, date = "2026-11-18"))
        assertEquals("Stays above ₪12,346 for the next 45 days", MoneyDashText.balanceTile(above, today)!!.low)
        val below = ov.forecast!!.copy(soonLow = ov.forecast!!.soonLow!!.copy(balance = -5000, date = "2026-12-01"))
        assertEquals("bad", MoneyDashText.balanceTile(below, today)!!.tone)
        assertNull(MoneyDashText.balanceTile(past.forecast, today))
    }

    @Test fun fixedTile() {
        assertEquals(MoneyDashText.FixedTile(478623, "32% of this month's income", "incl. ₪236 a month for yearly charges"), MoneyDashText.fixedTile(ov))
        assertEquals("24% of a typical month's income", MoneyDashText.fixedTile(ov.copy(incomeTypical = 2000000)).share)
    }

    @Test fun vsTypical() {
        val group = { k: String -> trends.groups.first { it.key == k } }
        assertEquals(MoneyDashText.Vs("up", "₪2,250 above typical"), MoneyDashText.vsTypical(group("housing"), true))
        assertEquals("new", MoneyDashText.vsTypical(group("food"), true).dir)
        assertEquals("none", MoneyDashText.vsTypical(group("shopping"), true).dir)
        assertEquals("same", MoneyDashText.vsTypical(group("food").copy(total = 10000, typical = 10200, onTrack = null), false).dir)
        assertEquals("₪50 below typical", MoneyDashText.vsTypical(group("food").copy(total = 5000, typical = 10000, onTrack = null), false).text)
    }

    @Test fun isolateHebrew() {
        assertEquals("⁨רמי לוי - יבנה⁩ ₪3", MoneyDashText.isolateHebrew("רמי לוי - יבנה ₪3"))
        assertEquals("NETFLIX.COM up", MoneyDashText.isolateHebrew("NETFLIX.COM up"))
    }

    @Test fun cadence() {
        val netflix = rec.items.first { it.merchant == "NETFLIX.COM" }
        assertEquals(MoneyDashText.CadenceAmount("₪49.90", "/ month", ""), MoneyDashText.cadenceAmount(netflix.amount, netflix.monthlyCost, netflix.cadence))
        assertEquals(MoneyDashText.CadenceAmount("₪117.30", "/ 2 months", "≈ ₪58.65 a month"), MoneyDashText.cadenceAmount(11730, 5865, "bimonthly"))
        assertEquals(MoneyDashText.CadenceAmount("₪50.00", "/ week", "≈ ₪217.00 a month"), MoneyDashText.cadenceAmount(5000, 21700, "weekly"))
        val yearly = rec.items.first { it.cadence == "yearly" }
        assertEquals(MoneyDashText.CadenceAmount("₪60.00", "/ year", "≈ ₪5.00 a month"), MoneyDashText.cadenceAmount(yearly.amount, yearly.monthlyCost, yearly.cadence))
        val y = rec.yearly[0]
        assertEquals(MoneyDashText.CadenceAmount("₪2,836.00", "/ year", "≈ ₪236.33 a month"), MoneyDashText.cadenceAmount(y.amount, y.monthlyCost, "yearly"))
        assertEquals("Every 2 months", MoneyDashText.cadenceLabel("bimonthly"))
        assertEquals("Yearly", MoneyDashText.cadenceLabel("yearly"))
        assertEquals("/ 2 months", MoneyDashText.perText("bimonthly"))
    }

    @Test fun priceChange() {
        val pc = rec.items.first { it.priceChange != null }.priceChange
        assertEquals("₪39.90 → ₪49.90 since Nov", MoneyDashText.priceChangeText(pc, today))
        assertEquals("₪39.90 → ₪49.90 since Nov 2026", MoneyDashText.priceChangeText(pc, LocalDate.parse("2027-02-01")))
    }

    @Test fun plans() {
        val (car, ksp) = bal.forecast!!.plans
        assertEquals(MoneyDashText.PlanText("3 payments left of 4", "₪2,127.00 to go", "next 10 Jan 2027"), MoneyDashText.planText(car, today))
        assertEquals(MoneyDashText.PlanText("1 payment left of 3", "₪400.00 to go", "next 10 Dec"), MoneyDashText.planText(ksp, today))
    }

    @Test fun cashflowAndForecastNotes() {
        assertEquals("12 months: earned ₪60,519, spent ₪21,777, set aside ₪0, net ₪38,742", MoneyDashText.cashflowTotalsText(cf))
        assertEquals("Forecast leaves out one-offs (none in the last 12 months), includes yearly charges and installment plans.",
            MoneyDashText.forecastNote(bal.forecast))
        assertTrue(MoneyDashText.forecastNote(bal.forecast!!.copy(leftOutTotal = -1234500)).startsWith("Forecast leaves out one-offs (₪12,345 in the last 12 months)"))
    }

    @Test fun upcoming() {
        val up = MoneyDashText.upcoming(bal.forecast, 45)
        assertEquals("2027-01-02", up.end)
        assertEquals(8, up.events.size)
        assertTrue(up.events.zipWithNext().all { (a, b) -> a.event.date <= b.event.date })
        assertTrue(up.events.none { it.event.date == "2027-01-10" })
        val cal = up.events.first { it.event.kind == "card_bill" && it.event.estimated }
        assertEquals(listOf("known" to 345081L, "fixed" to 4990L, "yearly" to 6000L, "everyday" to 30530L), cal.parts.map { it.key to it.amount })
        assertEquals("Already on the card", cal.parts[0].label)
        assertEquals(2, cal.event.items.size)
        assertEquals("₪3,561 – ₪4,906", cal.range)
        assertEquals(1755266L, cal.after)
        assertEquals("", up.events[2].range)
        assertEquals(listOf("known"), up.events[2].parts.map { it.key })
        assertEquals("income", up.events[0].event.kind)
        assertTrue(up.events[0].parts.isEmpty())
        assertEquals(801867L, up.events[0].after)
        assertEquals(46, up.series.size)
        assertTrue(MoneyDashText.upcoming(null as MoneyForecast?).events.isEmpty())
    }
}
