package io.github.ronganam.dana.data.money

import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.YearMonth
import java.time.ZoneId
import java.time.format.TextStyle
import java.time.temporal.ChronoUnit
import java.util.Locale
import kotlin.math.abs
import kotlin.math.floor
import kotlin.math.roundToLong

/**
 * The words and numbers of the Money dashboards: a port of the web's pure
 * `server/static/money-core.js` (₪ formatting, dates, the tiles, the sentences),
 * so the app says what the web page says. Tested in `MoneyDashTextTest`
 * against the server's fixtures. Plain strings: Hebrew runs inside a server
 * sentence are isolated with [isolateHebrew]; amounts are drawn left to right
 * by the screens.
 */
object MoneyDashText {
    const val MINUS = "−"
    private const val FSI = "⁨"
    private const val PDI = "⁩"
    private val MON = listOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

    private fun group3(n: Long): String = "%,d".format(Locale.US, n)

    /** JS Math.round: halves go up (toward +∞). */
    private fun roundHalfUp(v: Double): Long = floor(v + 0.5).toLong()

    // ------------------------------------------------------------ dates

    fun parseDay(iso: String): LocalDate? = runCatching { LocalDate.parse(iso.take(10)) }.getOrNull()

    /** "15 Dec" (+ " 2025" when not in [today]'s year). */
    fun shortDate(iso: String?, today: LocalDate): String {
        val d = iso?.let { parseDay(it) } ?: return ""
        return "${d.dayOfMonth} ${MON[d.monthValue - 1]}" + if (d.year != today.year) " ${d.year}" else ""
    }

    private fun weekday(d: LocalDate) = d.dayOfWeek.getDisplayName(TextStyle.SHORT, Locale.ENGLISH)

    /** "Tue 17 Nov 2026". */
    fun longDate(iso: String?): String {
        val d = iso?.let { parseDay(it) } ?: return ""
        return "${weekday(d)} ${d.dayOfMonth} ${MON[d.monthValue - 1]} ${d.year}"
    }

    /** A day header: "Today", "Yesterday", "Mon 16 Nov" (+ year when not this year). */
    fun dayLabel(iso: String, today: LocalDate): String {
        val d = parseDay(iso) ?: return iso
        if (d == today) return "Today"
        if (d == today.minusDays(1)) return "Yesterday"
        if (d == today.plusDays(1)) return "Tomorrow"
        return "${weekday(d)} ${d.dayOfMonth} ${MON[d.monthValue - 1]}" + if (d.year != today.year) " ${d.year}" else ""
    }

    /** The local time an aware ISO time was written in ("2026-11-18T08:10:00+02:00" → "08:10"). */
    fun timeOf(iso: String?): String = Regex("T(\\d{2}):(\\d{2})").find(iso ?: "")?.let { "${it.groupValues[1]}:${it.groupValues[2]}" } ?: ""

    /** A server instant in the device's clock: "07:01" today, "yesterday 19:00", "17 Nov 19:00". */
    fun whenText(iso: String?, now: java.time.ZonedDateTime): String {
        val t = iso?.let { runCatching { OffsetDateTime.parse(it).atZoneSameInstant(now.zone) }.getOrNull() } ?: return ""
        val hm = "%02d:%02d".format(t.hour, t.minute)
        val day = t.toLocalDate()
        val today = now.toLocalDate()
        return when (day) {
            today -> hm
            today.minusDays(1) -> "yesterday $hm"
            today.plusDays(1) -> "tomorrow $hm"
            else -> "${shortDate(day.toString(), today)} $hm"
        }
    }

    fun shiftMonth(month: String, by: Int): String = YearMonth.parse(month).plusMonths(by.toLong()).toString()

    /** "Nov" for "2026-11". */
    fun monthShort(month: String): String = month.drop(5).take(2).toIntOrNull()?.let { MON[it - 1] } ?: month

    /** "November 2026" / "November" ([year] false). */
    fun monthLabel(month: String, short: Boolean = false, year: Boolean = true): String {
        val ym = runCatching { YearMonth.parse(month) }.getOrNull() ?: return month
        val name = if (short) MON[ym.monthValue - 1] else ym.month.getDisplayName(TextStyle.FULL, Locale.ENGLISH)
        return if (year) "$name ${ym.year}" else name
    }

    // ------------------------------------------------------------ Hebrew

    private val HEB = Regex("[\\u0590-\\u05FF\\uFB1D-\\uFB4F](?:[\\u0590-\\u05FF\\uFB1D-\\uFB4F\\d\\s'\"׳״.,&\\-–]*[\\u0590-\\u05FF\\uFB1D-\\uFB4F])?")

    /** Hebrew runs (a merchant, a name) isolated inside a server-written English sentence, so the amounts beside them keep their place. */
    fun isolateHebrew(text: String?): String = HEB.replace(text ?: "") { FSI + it.value + PDI }

    /** True when the text holds Hebrew (it gets its own direction on screen). */
    fun hasHebrew(text: String?): Boolean = text != null && HEB.containsMatchIn(text)

    private fun pct(part: Long, whole: Long): Long = roundHalfUp(part.toDouble() / whole * 100)

    // ------------------------------------------------------------ Overview tiles

    data class NetTile(val label: String, val value: Long, val tone: String, val rate: String, val sub: String, val typical: String)

    /** The Net tile (overview, and cashflow for the typical month). */
    fun netTile(ov: MoneyOverview, cf: MoneyCashflow?): NetTile {
        val income = ov.incomeMonth
        val net = ov.net
        val rate = when {
            income > 0 -> if (net >= 0) "Kept ${pct(net, income)}% of income" else "${MoneyFormat.whole(-net)} more out than in"
            ov.inProgress -> "No income yet this month"
            else -> "No income that month"
        }
        val typ = if (cf != null && cf.typicalIncome > 0) "typical ${MoneyFormat.whole(cf.typicalNet)}" else ""
        return NetTile(
            label = if (ov.inProgress) "Net so far" else "Net",
            value = net,
            tone = if (net < 0) "out" else "in",
            rate = rate,
            sub = "Earned ${MoneyFormat.whole(income)} · spent ${MoneyFormat.whole(income - net)}",
            typical = typ
        )
    }

    data class EverydayTile(val value: Long, val track: String, val typical: String, val dir: String)

    fun everydayTile(e: Everyday, inProgress: Boolean): EverydayTile {
        val track = if (inProgress && e.onTrack != null) "On track for ${MoneyFormat.whole(e.onTrack)}" else ""
        val typ = e.typical ?: 0
        val typical = if (typ > 0) "typical ${MoneyFormat.whole(typ)}" else ""
        val ref = if (inProgress) e.onTrack else e.total
        val dir = if (typ > 0 && ref != null) when {
            ref > typ * 1.05 -> "up"
            ref < typ * 0.95 -> "down"
            else -> ""
        } else ""
        return EverydayTile(e.total, track, typical, dir)
    }

    data class BalanceTile(val value: Long, val low: String, val end: String, val tone: String)

    fun balanceTile(fc: ForecastTile?, today: LocalDate): BalanceTile? {
        fc ?: return null
        val sl = fc.soonLow
        val days = sl?.days ?: 45
        var low = ""
        var tone = ""
        if (sl != null && sl.balance < fc.balance) {
            low = "Lowest in the next $days days: ${MoneyFormat.whole(sl.balance)} on ${shortDate(sl.date, today)}"
            if (sl.balance < 0) tone = "bad"
        } else if (sl != null) low = "Stays above ${MoneyFormat.whole(sl.balance)} for the next $days days"
        val end = if (fc.endBalance != null && fc.end != null) "${MoneyFormat.whole(fc.endBalance)} by ${shortDate(fc.end, today)}" else ""
        return BalanceTile(fc.balance, low, end, tone)
    }

    data class FixedTile(val value: Long, val share: String, val yearly: String)

    fun fixedTile(ov: MoneyOverview): FixedTile {
        val v = ov.fixedMonthly
        val share = when {
            (ov.incomeTypical ?: 0) > 0 -> "${pct(v, ov.incomeTypical!!)}% of a typical month's income"
            ov.incomeMonth > 0 -> "${pct(v, ov.incomeMonth)}% of this month's income"
            else -> ""
        }
        val yearly = if (ov.yearlyMonthly > 0) "incl. ${MoneyFormat.whole(ov.yearlyMonthly)} a month for yearly charges" else ""
        return FixedTile(v, share, yearly)
    }

    // ------------------------------------------------------------ Spending words

    data class Vs(val dir: String, val text: String)

    /** A trends group / category against its typical month (while the month runs: what it is on track for). */
    fun vsTypical(item: TrendLine, inProgress: Boolean): Vs {
        val value = if (inProgress && item.onTrack != null) item.onTrack else item.total
        val typical = item.typical
        if (value == 0L && typical == 0L) return Vs("none", "")
        if (typical == 0L) return Vs("new", "usually nothing")
        val d = value - typical
        if (abs(d) < maxOf(100.0, typical * 0.05)) return Vs("same", "about typical")
        return Vs(if (d > 0) "up" else "down", "${MoneyFormat.whole(abs(d))} ${if (d > 0) "above" else "below"} typical")
    }

    /** "₪2,250 above" style delta of a mover: "+₪940", "−₪300", "new". */
    fun moverDelta(m: Mover): String = when (m.direction) {
        "new" -> "new"
        "up" -> "+" + MoneyFormat.whole(abs(m.delta), MoneyFormat.Sign.NONE)
        else -> MINUS + MoneyFormat.whole(abs(m.delta), MoneyFormat.Sign.NONE)
    }

    // ------------------------------------------------------------ Coming up

    data class BillPart(val key: String, val label: String, val amount: Long)

    private val BILL_PARTS = listOf(
        "known" to "Already on the card", "installments" to "Installments", "fixed" to "Fixed charges",
        "yearly" to "Yearly charges", "everyday" to "Everyday spending, estimated"
    )

    fun cardBillParts(e: ForecastEvent): List<BillPart> =
        BILL_PARTS.filter { (k, _) -> (e.parts[k] ?: 0) > 0 }.map { (k, l) -> BillPart(k, l, e.parts.getValue(k)) }

    data class Upcoming(val event: ForecastEvent, val parts: List<BillPart>, val range: String, val after: Long?)

    data class UpcomingList(val events: List<Upcoming>, val end: String?, val series: List<BalancePoint>)

    /** The forecast's events in its first [days] days, with each card bill's parts, its range in words and the balance at the end of that day. */
    fun upcoming(fc: MoneyForecast?, days: Int = 45): UpcomingList {
        fc ?: return UpcomingList(emptyList(), null, emptyList())
        val end = parseDay(fc.asOf)?.plusDays(days.toLong())?.toString() ?: fc.end
        val after = fc.series.associate { it.date to it.balance }
        val events = fc.events.filter { it.date <= end }.map { e ->
            val r = e.range?.takeIf { it.first != it.second }?.let { listOf(abs(it.first), abs(it.second)).sorted() }
            Upcoming(e, cardBillParts(e), r?.let { "${MoneyFormat.whole(it[0])} – ${MoneyFormat.whole(it[1])}" } ?: "", after[e.date])
        }
        return UpcomingList(events, end, fc.series.filter { it.date <= end })
    }

    // ------------------------------------------------------------ Cash flow words

    fun cashflowTotalsText(cf: MoneyCashflow): String {
        val t = cf.totals
        return "${cf.months.size} months: earned ${MoneyFormat.whole(t.income)}, spent ${MoneyFormat.whole(t.spent)}, set aside ${MoneyFormat.whole(t.setAside)}, net ${MoneyFormat.whole(t.net)}"
    }

    fun forecastNote(fc: MoneyForecast?): String {
        fc ?: return ""
        val out = if (fc.leftOutTotal != 0L) "${MoneyFormat.whole(abs(fc.leftOutTotal))} in the last 12 months" else "none in the last 12 months"
        return "Forecast leaves out one-offs ($out), includes yearly charges and installment plans."
    }

    private val CADENCES = mapOf("weekly" to "Weekly", "monthly" to "Monthly", "bimonthly" to "Every 2 months", "yearly" to "Yearly")
    private val PER = mapOf("weekly" to "/ week", "monthly" to "/ month", "bimonthly" to "/ 2 months", "yearly" to "/ year")

    fun cadenceLabel(c: String?): String = CADENCES[c] ?: c.orEmpty()
    fun perText(c: String?): String = PER[c].orEmpty()

    data class CadenceAmount(val amount: String, val per: String, val monthly: String)

    /** A recurring charge as its cadence bills it; `monthly` only when one charge is not a month's worth. */
    fun cadenceAmount(amount: Long, monthlyCost: Long?, cadence: String?): CadenceAmount {
        val c = cadence ?: "monthly"
        val monthly = if (c != "monthly" && monthlyCost != null && abs(monthlyCost) != abs(amount))
            "≈ ${MoneyFormat.amount(abs(monthlyCost), MoneyFormat.Sign.NONE)} a month" else ""
        return CadenceAmount(MoneyFormat.amount(abs(amount), MoneyFormat.Sign.NONE), perText(c), monthly)
    }

    data class PlanText(val left: String, val rest: String, val next: String)

    fun planText(p: InstallmentPlan, today: LocalDate): PlanText {
        val n = p.dates.size.takeIf { it > 0 } ?: maxOf(0, p.of - p.number)
        return PlanText(
            left = "$n payment${if (n == 1) "" else "s"} left of ${p.of}",
            rest = "${MoneyFormat.amount(abs(p.left), MoneyFormat.Sign.NONE)} to go",
            next = p.dates.firstOrNull()?.let { "next ${shortDate(it, today)}" } ?: ""
        )
    }

    /** "₪39.90 → ₪49.90 since Nov" (+ the year when not [today]'s). */
    fun priceChangeText(pc: PriceChange?, today: LocalDate): String {
        pc ?: return ""
        val d = parseDay(pc.since) ?: return ""
        return "${MoneyFormat.amount(pc.from)} → ${MoneyFormat.amount(pc.to)} since ${MON[d.monthValue - 1]}" + if (d.year != today.year) " ${d.year}" else ""
    }

    // ------------------------------------------------------------ investments

    /** "+6.98%", "−3.62%", "0%" (two decimals under 10, one above; [digits] for fewer); [signed] false drops the plus. */
    fun pctText(p: Double?, signed: Boolean = true, digits: Int = 2): String {
        if (p == null || p.isNaN() || p.isInfinite()) return ""
        val a = abs(p)
        val d = minOf(digits, if (a >= 10) 1 else 2)
        val fixed = java.math.BigDecimal(a).setScale(d, java.math.RoundingMode.HALF_UP)
        if (fixed.signum() == 0) return "0%"
        val body = fixed.stripTrailingZeros().toPlainString() + "%"
        return (if (p < 0) MINUS else if (!signed) "" else "+") + body
    }

    /** Units: "120", "15.5", "1,234.25". */
    fun quantityText(q: Double?): String {
        if (q == null || q.isNaN()) return ""
        val a = abs(q)
        val w = floor(a + 1e-9).toLong()
        val frac = ((a - w) * 10000).roundToLong()
        val f = if (frac == 0L) "" else "." + frac.toString().padStart(4, '0').trimEnd('0')
        return (if (q < 0) MINUS else "") + group3(w) + f
    }

    data class Tile(val value: Long?, val sub: String, val tone: String = "")

    data class PortfolioTiles(val value: Tile, val today: Tile, val profit: Tile, val cash: Tile)

    fun portfolioTiles(p: MoneyPortfolio, now: java.time.ZonedDateTime): PortfolioTiles {
        val t = p.total
        val value = t.value
        val morning = t.dayChange?.let { value - it }
        val dayPct = if (t.dayChange != null && morning != null && morning > 0) t.dayChange.toDouble() / morning * 100 else null
        val profitPct = if (p.accounts.size == 1 && p.accounts[0].profitPct != null) p.accounts[0].profitPct
        else if (t.profit != null && (t.cost ?: 0) > 0) t.profit.toDouble() / t.cost!! * 100 else null
        fun tone(v: Long?) = if (v == null || v == 0L) "" else if (v < 0) "neg" else "in"
        val cash = t.cash ?: 0
        return PortfolioTiles(
            value = Tile(value, t.asOf?.let { "Updated ${whenText(it, now)}" } ?: ""),
            today = Tile(t.dayChange, when {
                t.dayChange == null -> "No change from the site yet"
                pctText(dayPct).isNotEmpty() -> "${pctText(dayPct)} since the morning"
                else -> "since the morning"
            }, tone(t.dayChange)),
            profit = Tile(t.profit, when {
                t.profit == null -> "No profit figure from the site yet"
                pctText(profitPct).isNotEmpty() -> "${pctText(profitPct)} over what you paid"
                else -> "over what you paid"
            }, tone(t.profit)),
            cash = Tile(cash, if (value > 0) "${pctText(cash.toDouble() / value * 100, signed = false, digits = 1)} of the portfolio" else "")
        )
    }

    /** The value's move over the chart's range in words, money put in or taken out left out of it. */
    fun valueChangeText(g: MoneyDashGeometry.ValueGeo?, today: LocalDate): String {
        if (g == null || g.points.size < 2) return ""
        val since = shortDate(g.from, today)
        if (g.moved != 0L) {
            val net = g.change - g.moved
            val besides = "besides ${MoneyFormat.whole(abs(g.moved), MoneyFormat.Sign.NONE)} ${if (g.moved > 0) "put in" else "taken out"}"
            return if (net != 0L) "${if (net > 0) "Up" else "Down"} ${MoneyFormat.whole(abs(net), MoneyFormat.Sign.NONE)} since $since, $besides" else "No change since $since, $besides"
        }
        if (g.change == 0L) return "No change since $since"
        return "${if (g.change > 0) "Up" else "Down"} ${MoneyFormat.whole(abs(g.change), MoneyFormat.Sign.NONE)}" +
            (g.changePct?.let { " (${pctText(it)})" } ?: "") + " since $since"
    }

    data class Slice(val name: String, val value: Long, val slot: String, val share: Double, val width: Double, val text: String)

    /** The allocation bar: the largest holdings by rank, then "Other holdings", then "Cash". */
    fun allocationSlices(allocation: List<AllocationRow>, max: Int = 7): List<Slice> {
        val rows = allocation.filter { it.value > 0 }
        val cash = rows.filter { it.name == "Cash" }
        val restIn = rows.filter { it.name == "Other holdings" }
        val held = rows.filter { it !in cash && it !in restIn }.sortedByDescending { it.value }
        val total = rows.sumOf { it.value }
        if (total == 0L) return emptyList()
        val top = held.take(max)
        val rest = held.drop(max) + restIn
        val out = top.mapIndexed { i, a -> Triple(a.name, a.value, "a$i") }.toMutableList()
        if (rest.isNotEmpty()) out += Triple("Other holdings", rest.sumOf { it.value }, "rest")
        cash.forEach { out += Triple("Cash", it.value, "cash") }
        return out.map { (n, v, s) ->
            val share = v.toDouble() / total
            Slice(n, v, s, share, Math.round(share * 1000) / 10.0, pctText(share * 100, signed = false, digits = 1))
        }
    }

    private val TRADE_WORDS = mapOf(
        "buy" to "Bought", "sell" to "Sold", "dividend" to "Dividend", "interest" to "Interest", "deposit" to "Money in",
        "withdrawal" to "Money out", "tax" to "Tax", "split" to "Split", "redemption" to "Redemption", "benefit" to "Benefit", "expiry" to "Expired"
    )

    data class TradeText(val title: String, val name: String, val meta: List<String>, val amount: Long, val tone: String)

    fun tradeText(t: Trade, today: LocalDate): TradeText {
        val word = TRADE_WORDS[t.kind]
        val title = word ?: t.action ?: "Activity"
        val units = t.quantity != null && t.quantity != 0.0 && t.security != null && t.kind != "deposit" && t.kind != "withdrawal"
        val meta = listOfNotNull(
            shortDate(t.date, today),
            if (units) "${quantityText(abs(t.quantity!!))} units" else null,
            t.commission?.takeIf { it != 0L }?.let { "fee ${MoneyFormat.amount(abs(it), MoneyFormat.Sign.NONE)}" },
            t.tax?.takeIf { it != 0L && t.kind != "tax" }?.let { "tax ${MoneyFormat.amount(abs(it), MoneyFormat.Sign.NONE)}" },
            if (word != null && t.action != null && t.kind != "buy" && t.kind != "sell") t.action else null
        ).filter { it.isNotEmpty() }
        return TradeText(title, t.name.orEmpty(), meta, t.amount, if (t.amount == 0L) "" else if (t.amount > 0) "in" else "out")
    }

    /** "Psagot Trade ••456": a label of the user's own, else the company, and the last digits (3 for banks and investments, 4 for cards). */
    fun accountName(label: String?, number: String?, kind: String, companyName: String?): String {
        val digits = (number ?: "").filter { it.isDigit() }
        val last = digits.takeLast(if (kind == "bank" || kind == "investment") 3 else 4)
        val company = companyName.orEmpty()
        val custom = if (label != null && !(company.isNotEmpty() && label.startsWith(company))) label else ""
        val name = custom.ifEmpty { (if (kind == "bank") company.removePrefix("Bank ") else company) }.ifEmpty {
            mapOf("bank" to "Bank", "card" to "Card", "investment" to "Investments", "meal" to "Meal card")[kind] ?: "Card"
        }
        return if (last.isNotEmpty()) "$name ••$last" else name
    }

    // ------------------------------------------------------------ meal card

    fun cycleLabel(start: String, end: String, today: LocalDate): String =
        if (parseDay(start) == null || parseDay(end) == null) "" else "${shortDate(start, today)} – ${shortDate(end, today)}"

    data class Pace(val text: String, val tone: String, val sub: String)

    data class MealTiles(val left: Tile, val pace: Pace, val card: Tile, val count: Tile)

    /** The four tiles of the meal card's current cycle; null without a card. */
    fun mealTiles(m: MoneyMeals, today: LocalDate): MealTiles? {
        val c = m.current ?: return null
        val renews = parseDay(c.end)?.plusDays(1)?.toString()
        val days = c.daysLeft
        val pace = when {
            c.left == 0L && c.count > 0 -> Pace("Used up", "neg",
                if (days > 0) "$days ${if (days == 1) "day" else "days"} until it renews on ${shortDate(renews, today)}" else "It renews tomorrow")
            c.runsOut != null -> Pace("Lasts to ${shortDate(c.runsOut, today)}", "neg", "at this cycle's pace; it renews on ${shortDate(renews, today)}")
            c.left != 0L -> Pace("${MoneyFormat.whole(c.perDayLeft ?: 0)} a day", "", if (c.used != 0L) "on track to last the cycle" else "nothing used yet this cycle")
            else -> Pace("—", "", "")
        }
        return MealTiles(
            left = Tile(c.left, if (c.budget != null && c.budget != 0L) "of ${MoneyFormat.whole(c.budget)} · until ${shortDate(c.end, today)}" else "until ${shortDate(c.end, today)}"),
            pace = pace,
            card = Tile(c.cardPaid, if (c.cardPaid != 0L) "beyond the budget, on your own card" else "nothing beyond the budget"),
            count = Tile(c.count.toLong(), if (c.used != 0L) "${MoneyFormat.whole(c.used)} from the budget" else "this cycle")
        )
    }

    data class CycleBar(
        val start: String,
        val label: String,
        val count: Int,
        val total: Long,
        val budget: Long,
        val card: Long,
        val budgetPct: Double,
        val cardPct: Double,
        val billed: String,
        val txn: String?
    )

    /** The cycles oldest first (at most [n]): budget and own-card parts as % of the largest cycle, and what its card line says. */
    fun mealCycleBars(cycles: List<MealCycle>, today: LocalDate, n: Int = 12): List<CycleBar> {
        val list = cycles.take(n).reversed()
        val top = maxOf(1L, list.maxOfOrNull { it.total } ?: 1L).toDouble()
        return list.map { c ->
            CycleBar(
                start = c.start,
                label = cycleLabel(c.start, c.end, today),
                count = c.count,
                total = c.total,
                budget = c.budgetPaid,
                card = c.cardPaid,
                budgetPct = 100 * c.budgetPaid / top,
                cardPct = 100 * c.cardPaid / top,
                billed = if (c.cardPaid != 0L) (c.billed?.let { "On your card ${shortDate(it.first, today)}" } ?: "Not on your card yet") else "",
                txn = c.billed?.second
            )
        }
    }

    /** Days between two ISO dates (for tests and the coming-up window). */
    fun daysBetween(a: String, b: String): Long = ChronoUnit.DAYS.between(parseDay(a), parseDay(b))

    fun today(): LocalDate = LocalDate.now(ZoneId.systemDefault())
}
