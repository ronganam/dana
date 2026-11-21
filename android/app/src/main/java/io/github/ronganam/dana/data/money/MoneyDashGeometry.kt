package io.github.ronganam.dana.data.money

import java.time.LocalDate
import java.time.YearMonth
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.roundToInt

/**
 * The charts' geometry, a port of the web's `money-core.js` (paceGeometry,
 * monthsGeometry, cashflowGeometry, balanceGeometry, valueGeometry and
 * their helpers): pure numbers in a W × H box (the Canvas's pixels, the
 * paddings given in the same unit), so drawing and touch read the same
 * positions. Tested in `MoneyDashGeometryTest`.
 */
object MoneyDashGeometry {

    /** The plot's frame: size and paddings (left, right, top, bottom). */
    data class Box(val w: Double, val h: Double, val l: Double, val r: Double, val t: Double, val b: Double) {
        val plotW get() = w - l - r
        val plotH get() = h - t - b
        val base get() = t + plotH
    }

    data class Pt(val x: Double, val y: Double)

    data class Tick(val v: Double, val y: Double, val text: String, val zero: Boolean = false)

    data class XLabel(val key: String, val x: Double, val text: String, val show: Boolean = true)

    fun r1(n: Double): Double = floor(n * 10 + 0.5) / 10

    fun niceMax(v: Double): Double {
        if (!(v > 0)) return 100.0
        val p = 10.0.pow(floor(log10(v)))
        val n = v / p
        return (if (n <= 1) 1.0 else if (n <= 2) 2.0 else if (n <= 2.5) 2.5 else if (n <= 5) 5.0 else 10.0) * p
    }

    /** Round ticks covering [lo, hi]: about [count] steps of 1, 2, 2.5 or 5 × a power of ten. */
    fun niceTicks(lo0: Double, hi0: Double, count: Int = 4): List<Double> {
        val lo = lo0
        val hi = if (!(hi0 > lo)) lo + (abs(lo).takeIf { it != 0.0 } ?: 100.0) else hi0
        val raw = (hi - lo) / count
        val p = 10.0.pow(floor(log10(raw)))
        val n = raw / p
        val step = (if (n <= 1) 1.0 else if (n <= 2) 2.0 else if (n <= 2.5) 2.5 else if (n <= 5) 5.0 else 10.0) * p
        val out = mutableListOf<Double>()
        var k = floor(lo / step).toLong()
        val last = ceil(hi / step).toLong()
        while (k <= last) { out += "%.12g".format(java.util.Locale.US, k * step).toDouble(); k++ }
        return out
    }

    private fun dayNum(iso: String): Long = LocalDate.parse(iso.take(10)).toEpochDay()

    // ------------------------------------------------------------ pace

    data class PaceGeo(
        val box: Box,
        val n: Int,
        val top: Double,
        val ticks: List<Tick>,
        val thisLine: List<Pt>,
        val typicalLine: List<Pt>,
        /** The usual range: high forward then low back (a closed band), empty without one. */
        val band: List<Pt>,
        val end: Pt?,
        val todayX: Double?,
        val xLabels: List<XLabel>
    ) {
        fun x(i: Int): Double = r1(box.l + (i.toDouble() / (n - 1)) * box.plotW)
        fun y(v: Long): Double = r1(box.t + box.plotH - (max(0L, v) / top) * box.plotH)
        /** The day index (0-based) under an x position. */
        fun indexAt(px: Double): Int = ((px - box.l) / box.plotW * (n - 1)).roundToInt().coerceIn(0, n - 1)
    }

    /** The everyday pace chart: this month cumulative by day against the typical month and its usual range; one y scale. */
    fun pace(p: MoneyPace, box: Box): PaceGeo {
        val typ = p.typical ?: emptyList()
        val low = p.low ?: emptyList()
        val high = p.high ?: emptyList()
        val n = maxOf(p.days, p.thisMonth.size, 2)
        val values = (p.thisMonth + typ + high).filterNotNull()
        val top = niceMax(maxOf(0L, values.maxOrNull() ?: 0L).toDouble())
        val x = { i: Int -> r1(box.l + (i.toDouble() / (n - 1)) * box.plotW) }
        val y = { v: Long -> r1(box.t + box.plotH - (max(0L, v) / top) * box.plotH) }
        fun known(arr: List<Long?>): List<Pt> {
            val out = mutableListOf<Pt>()
            for (i in arr.indices) { if (i >= n) break; val v = arr[i] ?: break; out += Pt(x(i), y(v)) }
            return out
        }
        val pThis = known(p.thisMonth)
        val pTyp = known(typ)
        val pLow = known(low)
        val pHigh = known(high)
        val band = if (pHigh.size > 1 && pLow.size == pHigh.size) pHigh + pLow.reversed() else emptyList()
        val ticks = listOf(0.0, 0.5, 1.0).map { f -> Tick(top * f, r1(box.t + box.plotH - f * box.plotH), MoneyDashText.compact(top * f)) }
        val labelDays = listOf(1, 5, 10, 15, 20, 25, n).distinct().filter { d -> d <= n && !(d != n && n - d < 3) }
        return PaceGeo(
            box, n, top, ticks, pThis, pTyp, band,
            end = pThis.lastOrNull(),
            todayX = p.today?.let { x(it - 1) },
            xLabels = labelDays.map { d -> XLabel(d.toString(), x(d - 1), d.toString()) }
        )
    }

    data class PaceAt(val day: Int, val thisMonth: Long?, val typical: Long?, val low: Long?, val high: Long?)

    fun paceAt(p: MoneyPace, i: Int): PaceAt {
        fun at(arr: List<Long?>?) = arr?.getOrNull(i)
        return PaceAt(i + 1, at(p.thisMonth), at(p.typical), at(p.low), at(p.high))
    }

    // ------------------------------------------------------------ 12 months / cash flow bars

    /** The series a group is drawn in (its colour follows the group, never its rank). */
    val SERIES = listOf("food", "housing", "transport", "bills", "shopping", "health", "leisure", "family", "people", "other", "uncategorized")
    private val SERIES_LABELS = mapOf("other" to "Other", "uncategorized" to "Uncategorized")

    fun seriesOf(group: String?): String = when {
        group.isNullOrEmpty() -> "uncategorized"
        group in SERIES -> group
        else -> "other"
    }

    /** A bar segment; [top] = rounded top corners (the top of an upward stack), [bottom] = rounded bottom (the end of a downward one). */
    data class Seg(val key: String, val value: Long, val x: Double, val w: Double, val y: Double, val h: Double, val top: Boolean = false, val bottom: Boolean = false)

    data class MonthBar(
        val month: String,
        val label: String,
        val total: Long,
        val x: Double,
        val w: Double,
        val slotX: Double,
        val slotW: Double,
        val cx: Double,
        val segments: List<Seg>
    )

    data class Legend(val key: String, val label: String)

    data class MonthsGeo(val box: Box, val top: Double, val bars: List<MonthBar>, val ticks: List<Tick>, val xLabels: List<XLabel>, val legend: List<Legend>) {
        fun barAt(px: Double): Int? = bars.indexOfFirst { px >= it.slotX && px < it.slotX + it.slotW }.takeIf { it >= 0 }
    }

    /** One bar per month, its groups folded into the series and stacked bottom-up in SERIES order with a [gap] between segments. */
    fun months(payload: MoneyMonths, box: Box, gap: Double = 2.0): MonthsGeo {
        val months = payload.months
        val top = niceMax(maxOf(0L, months.maxOfOrNull { it.total } ?: 0L).toDouble())
        val slot = box.plotW / maxOf(1, months.size)
        val bw = max(4.0, min(34.0, slot * 0.62))
        val base = box.base
        val present = mutableSetOf<String>()
        val bars = months.mapIndexed { i, m ->
            val sums = mutableMapOf<String, Long>()
            m.groups.forEach { (g, v) -> if (v > 0) { val k = seriesOf(if (g == "uncategorized") null else g); sums[k] = (sums[k] ?: 0) + v } }
            val x = r1(box.l + i * slot + (slot - bw) / 2)
            var stackTop = base
            val segs = mutableListOf<Seg>()
            SERIES.forEach { k ->
                val v = sums[k] ?: return@forEach
                if (v == 0L) return@forEach
                present += k
                val h = v / top * box.plotH
                val g = if (segs.isNotEmpty()) gap else 0.0
                val y = stackTop - h
                segs += Seg(k, v, x, r1(bw), r1(y), r1(max(0.0, h - g)))
                stackTop = y
            }
            val lastVisible = segs.indexOfLast { it.h >= 1 }
            if (lastVisible >= 0) segs[lastVisible] = segs[lastVisible].copy(top = true)
            MonthBar(m.month, m.label, m.total, x, r1(bw), r1(box.l + i * slot), r1(slot), r1(x + bw / 2), segs)
        }
        val ticks = listOf(0.0, 0.5, 1.0).map { f -> Tick(top * f, r1(base - f * box.plotH), MoneyDashText.compact(top * f)) }
        val every = if (months.size > 8) 2 else 1
        val xLabels = bars.mapIndexed { i, b -> XLabel(b.month, b.cx, MoneyDashText.monthShort(b.month), (months.size - 1 - i) % every == 0) }
        val legend = SERIES.filter { it in present }.map { k -> Legend(k, SERIES_LABELS[k] ?: payload.groups.firstOrNull { it.key == k }?.label ?: k) }
        return MonthsGeo(box, top, bars, ticks, xLabels, legend)
    }

    /** Trends as a months payload: the groups' 12 monthly totals (one-offs left out), oldest first, the trend's month last. */
    fun trendsAsMonths(t: MoneyTrends): MoneyMonths {
        val labels = t.monthLabels
        return MoneyMonths(
            groups = t.groups.map { GroupLabel(it.key, it.label) },
            months = labels.mapIndexed { i, label ->
                val sums = mutableMapOf<String, Long>()
                t.groups.forEach { g -> val v = g.months.getOrNull(i) ?: 0; if (v > 0) sums[g.key] = v }
                MonthTotal(MoneyDashText.shiftMonth(t.month, i - (labels.size - 1)), label, sums.values.sum(), sums)
            }
        )
    }

    data class SparkBar(val x: Double, val y: Double, val w: Double, val h: Double, val last: Boolean)

    /** A row's tiny bar chart of monthly totals (oldest first) in a W × H box; nothing drawn for a zero. */
    fun sparkBars(values: List<Long>, w: Double, h: Double, gap: Double = 1.5): Pair<Long, List<SparkBar>> {
        val vs = values.map { max(0L, it) }
        val mx = vs.maxOrNull() ?: 0L
        val n = maxOf(1, vs.size)
        val bw = (w - gap * (n - 1)) / n
        return mx to vs.mapIndexed { i, v ->
            val bh = if (mx > 0 && v > 0) max(1.0, v.toDouble() / mx * h) else 0.0
            SparkBar(r1(i * (bw + gap)), r1(h - bh), r1(bw), r1(bh), i == n - 1)
        }
    }

    private val FLOW_SERIES = listOf("fixed", "yearly", "everyday", "one_off")
    val FLOW_LABELS = mapOf("income" to "Income", "fixed" to "Fixed", "yearly" to "Yearly", "everyday" to "Everyday", "one_off" to "One-off", "set_aside" to "Set aside", "net" to "Net")

    data class CashBar(
        val month: String,
        val label: String,
        val complete: Boolean,
        val x: Double,
        val w: Double,
        val cx: Double,
        val slotX: Double,
        val slotW: Double,
        val income: Seg?,
        val segments: List<Seg>,
        val setAside: Seg?,
        val net: Pt,
        val netValue: Long,
        val showNet: Boolean
    )

    data class CashGeo(
        val box: Box,
        val zero: Double,
        val up: Double,
        val down: Double,
        val unit: Double,
        val bars: List<CashBar>,
        val ticks: List<Tick>,
        val xLabels: List<XLabel>,
        val legend: List<Legend>
    ) {
        val netLine: List<Pt> get() = bars.filter { it.showNet }.map { it.net }
        fun barAt(px: Double): Int? = bars.indexOfFirst { px >= it.slotX && px < it.slotX + it.slotW }.takeIf { it >= 0 }
    }

    /** Income up from zero; fixed, yearly, everyday and one-offs stacked down; money set aside a thin bar beside; the net a point. One signed scale. */
    fun cashflow(cf: MoneyCashflow, box: Box, gap: Double = 2.0): CashGeo {
        val months = cf.months
        fun outOf(m: CashflowMonth) = FLOW_SERIES.sumOf { max(0L, m.out(it)) }
        val maxUp = maxOf(0L, months.maxOfOrNull { max(it.income, it.net) } ?: 0L).toDouble()
        val maxDown = maxOf(0L, months.maxOfOrNull { maxOf(outOf(it), it.setAside, -it.net) } ?: 0L).toDouble()
        var up = if (maxUp > 0) niceMax(maxUp) else 0.0
        val down = if (maxDown > 0) niceMax(maxDown) else 0.0
        if (up == 0.0 && down == 0.0) up = 100.0
        val unit = box.plotH / (up + down)
        val zero = r1(box.t + up * unit)
        val slot = box.plotW / maxOf(1, months.size)
        val anyAside = months.any { it.setAside > 0 }
        val bw = max(4.0, min(24.0, slot * (if (anyAside) 0.42 else 0.56)))
        val sw = if (anyAside) max(3.0, (bw * 0.4).roundToInt().toDouble()) else 0.0
        val groupW = bw + (if (sw > 0) gap + sw else 0.0)
        val present = mutableSetOf<String>()
        val first = months.indexOfFirst { it.income != 0L || it.spent != 0L || it.setAside != 0L }
        val bars = months.mapIndexed { i, m ->
            val x = r1(box.l + i * slot + (slot - groupW) / 2)
            val income = if (m.income > 0) {
                present += "income"
                val h = m.income * unit
                Seg("income", m.income, x, r1(bw), r1(zero - h), r1(max(0.0, h - 1)), top = true)
            } else null
            var edge = zero + 1
            val segs = mutableListOf<Seg>()
            FLOW_SERIES.forEach { k ->
                val v = m.out(k)
                if (v <= 0) return@forEach
                present += k
                val h = v * unit
                val g = if (segs.isNotEmpty()) gap else 0.0
                segs += Seg(k, v, x, r1(bw), r1(edge + g), r1(max(0.0, h - g)))
                edge += h
            }
            val lastVisible = segs.indexOfLast { it.h >= 1 }
            if (lastVisible >= 0) segs[lastVisible] = segs[lastVisible].copy(bottom = true)
            val aside = if (m.setAside > 0) {
                present += "set_aside"
                Seg("set_aside", m.setAside, r1(x + bw + gap), sw, r1(zero + 1), r1(max(0.0, m.setAside * unit - 1)), bottom = true)
            } else null
            val cx = r1(x + bw / 2)
            CashBar(m.month, m.label, m.complete, x, r1(bw), cx, r1(box.l + i * slot), r1(slot), income, segs, aside,
                Pt(cx, r1(zero - m.net * unit)), m.net, first >= 0 && i >= first)
        }
        if (first >= 0) present += "net"
        val ticks = mutableListOf<Tick>()
        if (up > 0) {
            ticks += Tick(up, r1(zero - up * unit), MoneyDashText.compact(up))
            if (up * unit > 40) ticks += Tick(up / 2, r1(zero - up / 2 * unit), MoneyDashText.compact(up / 2))
        }
        ticks += Tick(0.0, zero, "₪0", zero = true)
        if (down > 0) {
            if (down * unit > 40) ticks += Tick(-down / 2, r1(zero + down / 2 * unit), MoneyDashText.compact(-down / 2))
            ticks += Tick(-down, r1(zero + down * unit), MoneyDashText.compact(-down))
        }
        val every = if (months.size > 8) 2 else 1
        val xLabels = bars.mapIndexed { i, b -> XLabel(b.month, b.cx, MoneyDashText.monthShort(b.month), (months.size - 1 - i) % every == 0) }
        val legend = (listOf("income") + FLOW_SERIES + listOf("set_aside", "net")).filter { it in present }.map { Legend(it, FLOW_LABELS.getValue(it)) }
        return CashGeo(box, zero, up, down, unit, bars, ticks, xLabels, legend)
    }

    // ------------------------------------------------------------ time lines (balance, portfolio value)

    /** A point a crosshair can land on: its day, value, place, and the forecast's range / a flow of money. */
    data class TimePoint(
        val date: String,
        val value: Long,
        val x: Double,
        val y: Double,
        val forecast: Boolean = false,
        val low: Long? = null,
        val high: Long? = null,
        val flow: Long = 0
    )

    data class LineGeo(
        val box: Box,
        val lo: Double,
        val hi: Double,
        val ticks: List<Tick>,
        val history: List<Pt>,
        val forecast: List<Pt>,
        /** Closed: high forward, low back. */
        val band: List<Pt>,
        val low: TimePoint?,
        val todayX: Double?,
        val zeroY: Double?,
        val points: List<TimePoint>,
        val xLabels: List<XLabel>
    ) {
        /** The point nearest an x position. */
        fun indexAt(px: Double): Int = points.indices.minByOrNull { abs(points[it].x - px) } ?: 0
    }

    /** A time axis's labels: each month start after [start] up to [end] ("Nov"), every nth shown so they fit [spacing] apart. */
    fun monthLabels(start: String, end: String, plotW: Double, x: (String) -> Double, spacing: Double = 56.0): List<XLabel> {
        val months = mutableListOf<String>()
        var ym = YearMonth.parse(start.take(7)).plusMonths(1)
        while (true) {
            val iso = "$ym-01"
            if (iso > end) break
            months += iso
            ym = ym.plusMonths(1)
        }
        val every = maxOf(1, ceil(months.size / max(2.0, floor(plotW / spacing))).toInt())
        return months.mapIndexed { i, iso -> XLabel(iso, x(iso), MoneyDashText.monthShort(iso), (months.size - 1 - i) % every == 0) }
    }

    /**
     * The bank balance: the history solid, the forecast dashed with its range
     * as a band, the low point, a time axis and round y ticks (zero kept in
     * view unless [includeZero] false). [historyDays] keeps that many days of
     * the history. [lowOverride] marks another low (the 45-day one).
     */
    fun balance(
        b: MoneyBalance,
        box: Box,
        historyDays: Int? = null,
        includeZero: Boolean = true,
        lowOverride: DatedBalance? = null,
        useLowOverride: Boolean = false,
        labelSpacing: Double = 56.0
    ): LineGeo? {
        var hist = b.history
        if (historyDays != null && hist.isNotEmpty()) {
            val from = MoneyDashText.parseDay(hist.last().date)!!.minusDays(historyDays.toLong()).toString()
            hist = hist.filter { it.date >= from }
        }
        val series = b.forecast?.series ?: emptyList()
        if (hist.isEmpty() && series.isEmpty()) return null
        val start = (hist.firstOrNull() ?: series.first()).date
        val end = (series.lastOrNull() ?: hist.last()).date
        val d0 = dayNum(start)
        val span = max(1L, dayNum(end) - d0).toDouble()
        val values = hist.map { it.balance } + series.flatMap { listOf(it.balance, it.low ?: it.balance, it.high ?: it.balance) }
        var lo = values.min().toDouble()
        var hi = values.max().toDouble()
        if (includeZero) { lo = min(lo, 0.0); hi = max(hi, 0.0) }
        val ticks = if (!includeZero) listOf(lo, hi) else niceTicks(lo, hi, 4)
        val dLo = ticks.first()
        val dHi = if (ticks.last() > dLo) ticks.last() else dLo + 100
        val x = { iso: String -> r1(box.l + (dayNum(iso) - d0) / span * box.plotW) }
        val y = { v: Double -> r1(box.t + box.plotH - (v - dLo) / (dHi - dLo) * box.plotH) }
        val band = if (series.size > 1 && series.any { it.low != it.high })
            series.map { Pt(x(it.date), y((it.high ?: it.balance).toDouble())) } + series.reversed().map { Pt(x(it.date), y((it.low ?: it.balance).toDouble())) }
        else emptyList()
        val lowSrc = if (useLowOverride) lowOverride else b.forecast?.lowPoint
        val low = lowSrc?.takeIf { it.date in start..end }?.let { TimePoint(it.date, it.balance, x(it.date), y(it.balance.toDouble())) }
        val today = b.forecast?.asOf ?: hist.last().date
        val byDate = LinkedHashMap<String, TimePoint>()
        hist.forEach { byDate[it.date] = TimePoint(it.date, it.balance, 0.0, 0.0) }
        series.forEach { if (it.date !in byDate) byDate[it.date] = TimePoint(it.date, it.balance, 0.0, 0.0, forecast = true, low = it.low, high = it.high) }
        val points = byDate.values.sortedBy { it.date }.map { it.copy(x = x(it.date), y = y(it.value.toDouble())) }
        return LineGeo(
            box, dLo, dHi,
            ticks = if (!includeZero) emptyList() else ticks.map { Tick(it, y(it), MoneyDashText.compact(it), it == 0.0) },
            history = hist.map { Pt(x(it.date), y(it.balance.toDouble())) },
            forecast = series.map { Pt(x(it.date), y(it.balance.toDouble())) },
            band = band,
            low = low,
            todayX = x(today),
            zeroY = if (dLo <= 0 && dHi >= 0) y(0.0) else null,
            points = points,
            xLabels = monthLabels(start, end, box.plotW, x, labelSpacing)
        )
    }

    /** The portfolio value line: [geo] plus the change over the range and the money moved in it ([moved]: put in +, taken out −). */
    data class ValueGeo(val geo: LineGeo, val from: String, val change: Long, val changePct: Double?, val moved: Long) {
        val points get() = geo.points
        val marks get() = geo.points.filter { it.flow != 0L }
    }

    /**
     * The portfolio value per day: ticks around the range (not from zero), the
     * last [days] days, flows of money marked on the next point with a value.
     */
    fun value(history: List<DatedValue>, box: Box, days: Int? = null, flows: List<Flow> = emptyList(), labelSpacing: Double = 56.0): ValueGeo? {
        var pts = history.filter { MoneyDashText.parseDay(it.date) != null }
        if (days != null && pts.isNotEmpty()) {
            val from = MoneyDashText.parseDay(pts.last().date)!!.minusDays(days.toLong()).toString()
            pts = pts.filter { it.date >= from }
        }
        if (pts.isEmpty()) return null
        val start = pts.first().date
        val end = pts.last().date
        val d0 = dayNum(start)
        val span = max(1L, dayNum(end) - d0).toDouble()
        val lo = pts.minOf { it.value }.toDouble()
        val hi = pts.maxOf { it.value }.toDouble()
        val pad = maxOf((hi - lo) * 0.1, abs(hi) * 0.005, 100.0)
        val ticks = niceTicks(max(if (lo >= 0) 0.0 else Double.NEGATIVE_INFINITY, lo - pad), hi + pad, 4)
        val dLo = ticks.first()
        val dHi = ticks.last()
        val x = { iso: String -> r1(box.l + if (pts.size > 1) (dayNum(iso) - d0) / span * box.plotW else box.plotW) }
        val y = { v: Double -> r1(box.t + box.plotH - (v - dLo) / (dHi - dLo) * box.plotH) }
        val points = pts.map { TimePoint(it.date, it.value, x(it.date), y(it.value.toDouble())) }.toMutableList()
        var moved = 0L
        flows.forEach { f ->
            if (f.amount == 0L || !(f.date > start) || f.date > end) return@forEach
            val i = points.indexOfFirst { it.date >= f.date }
            if (i < 0) return@forEach
            points[i] = points[i].copy(flow = points[i].flow + f.amount)
            moved += f.amount
        }
        val first = pts.first().value
        val last = pts.last().value
        val line = points.map { Pt(it.x, it.y) }
        val geo = LineGeo(
            box, dLo, dHi,
            ticks = ticks.map { Tick(it, y(it), MoneyDashText.compact(it), it == 0.0) },
            history = line, forecast = emptyList(), band = emptyList(), low = null, todayX = null, zeroY = null,
            points = points, xLabels = monthLabels(start, end, box.plotW, x, labelSpacing)
        )
        return ValueGeo(geo, start, last - first, if (first > 0) (last - first).toDouble() / first * 100 else null, moved)
    }
}
