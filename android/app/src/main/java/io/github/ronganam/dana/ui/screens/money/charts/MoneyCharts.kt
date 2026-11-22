package io.github.ronganam.dana.ui.screens.money.charts

import io.github.ronganam.dana.data.money.MoneyFormat
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.money.MoneyDashGeometry
import io.github.ronganam.dana.data.money.MoneyDashGeometry.Box
import io.github.ronganam.dana.data.money.MoneyDashGeometry.Pt
import io.github.ronganam.dana.ui.theme.Hue
import io.github.ronganam.dana.ui.theme.Neutral
import kotlin.math.max
import kotlin.math.min

// The Money charts on a Compose Canvas: geometry from MoneyDashGeometry (the
// web's, in pixels), a touch crosshair instead of hover (drag sideways to
// scrub, tap to pin or open), sparse axis labels in mono, theme colours.

/**
 * The charts' colours, from Ember's hues (their dark values: mid-tones that
 * read as fills on Ink and Paper alike): series hues follow the entity (a
 * group, a kind of spending), never its rank; money in is sage, money out rose.
 * Text in these colours goes through [goodColor] / [badColor] (theme-aware).
 */
object MoneyPalette {
    private val Info = Hue.SKY.dark
    private val Success = Hue.SAGE.dark
    private val Warning = Hue.AMBER.dark
    private val Danger = Hue.ROSE.dark
    private val teal = Hue.TEAL.dark
    private val violet = Hue.PLUM.dark
    private val pink = lerp(Hue.ROSE.dark, Hue.PLUM.dark, 0.35f)
    private val coral = lerp(Hue.ROSE.dark, Hue.AMBER.dark, 0.45f)
    private val sage = lerp(Success, Info, 0.45f)

    /** A spending group's hue (MoneyDashGeometry.SERIES). */
    fun series(key: String): Color = when (key) {
        "food" -> Info
        "housing" -> coral
        "transport" -> sage
        "bills" -> Warning
        "shopping" -> pink
        "health" -> Success
        "leisure" -> violet
        "family" -> lerp(Danger, Color.White, 0.25f)
        "people" -> teal
        "other" -> Neutral
        else -> lerp(Neutral, Color.Black, 0.25f)
    }

    /** Cash flow's series: income up; fixed, yearly, everyday, one-offs down; set aside apart. */
    fun flow(key: String): Color = when (key) {
        "income" -> Success
        "fixed" -> violet
        "yearly" -> Warning
        "everyday" -> Info
        "one_off" -> coral
        "set_aside" -> sage
        else -> Neutral
    }

    /** An allocation slot: a0… by rank of holding, rest, cash. */
    fun slot(slot: String): Color = when (slot) {
        "rest" -> Neutral
        "cash" -> lerp(Neutral, Color.Black, 0.25f)
        else -> series(MoneyDashGeometry.SERIES.getOrElse(slot.drop(1).toIntOrNull() ?: 0) { "other" })
    }

    val line = Info
    val good = Success
    val bad = Danger
    val cardPart = Warning
}

/** Axis and readout colours from the theme. */
@Immutable
data class ChartInk(val grid: Color, val zero: Color, val axis: Color, val ink: Color, val accent: Color, val canvas: Color, val tipBg: Color)

@Composable
fun chartInk(): ChartInk {
    val c = MaterialTheme.colorScheme
    return ChartInk(c.outlineVariant, c.outline, c.onSurfaceVariant, c.onSurface, c.primary, c.background, c.surfaceContainerHigh)
}

/** Paddings of a chart's plot, in dp. */
data class Pad(val l: Dp, val r: Dp = 8.dp, val t: Dp = 14.dp, val b: Dp = 22.dp)

fun DrawScope.box(pad: Pad): Box = Box(size.width.toDouble(), size.height.toDouble(), pad.l.toPx().toDouble(), pad.r.toPx().toDouble(), pad.t.toPx().toDouble(), pad.b.toPx().toDouble())

private fun Pt.o() = Offset(x.toFloat(), y.toFloat())

private fun path(pts: List<Pt>, close: Boolean = false): Path = Path().apply {
    pts.forEachIndexed { i, p -> if (i == 0) moveTo(p.x.toFloat(), p.y.toFloat()) else lineTo(p.x.toFloat(), p.y.toFloat()) }
    if (close) close()
}

/** A bar segment with its rounded end (top of an upward stack, bottom of a downward one), like the web's barPath. */
fun DrawScope.drawSeg(s: MoneyDashGeometry.Seg, color: Color, radius: Float = 4.dp.toPx()) {
    if (s.h <= 0) return
    val x = s.x.toFloat(); val y = s.y.toFloat(); val w = s.w.toFloat(); val h = s.h.toFloat()
    val r = if (s.top || s.bottom) min(radius, min(w / 2, h)) else 0f
    val p = Path()
    if (r == 0f) { drawRect(color, Offset(x, y), Size(w, h)); return }
    val b = y + h; val xr = x + w
    if (s.bottom) {
        p.moveTo(x, y); p.lineTo(x, b - r); p.quadraticTo(x, b, x + r, b); p.lineTo(xr - r, b); p.quadraticTo(xr, b, xr, b - r); p.lineTo(xr, y); p.close()
    } else {
        p.moveTo(x, b); p.lineTo(x, y + r); p.quadraticTo(x, y, x + r, y); p.lineTo(xr - r, y); p.quadraticTo(xr, y, xr, y + r); p.lineTo(xr, b); p.close()
    }
    drawPath(p, color)
}

private fun DrawScope.label(tm: TextMeasurer, text: String, x: Float, y: Float, style: TextStyle, align: Float = 0.5f, backing: Color? = null) {
    val m = tm.measure(text, style)
    val left = (x - m.size.width * align).coerceIn(0f, max(0f, size.width - m.size.width))
    val top = y - m.size.height / 2f
    if (backing != null) {
        val p = 3.dp.toPx()
        drawRoundRect(backing, Offset(left - p, top), Size(m.size.width + 2 * p, m.size.height.toFloat()), CornerRadius(p))
    }
    drawText(m, topLeft = Offset(left, top))
}

/** Grid lines with their tick labels at the left; a label the compact form repeats (a narrow range) is shown once. */
private fun DrawScope.grid(tm: TextMeasurer, g: Box, ticks: List<MoneyDashGeometry.Tick>, ink: ChartInk, style: TextStyle) {
    ticks.forEachIndexed { i, t ->
        val y = t.y.toFloat()
        drawLine(if (t.zero) ink.zero else ink.grid, Offset(g.l.toFloat(), y), Offset((g.w - g.r).toFloat(), y), 1.dp.toPx())
        if (i > 0 && ticks[i - 1].text == t.text) return@forEachIndexed
        val m = tm.measure(t.text, style)
        drawText(m, topLeft = Offset(g.l.toFloat() - 6.dp.toPx() - m.size.width, y - m.size.height / 2f))
    }
}

private fun DrawScope.xLabels(tm: TextMeasurer, g: Box, labels: List<MoneyDashGeometry.XLabel>, style: TextStyle, selStyle: TextStyle? = null, sel: String? = null) {
    labels.filter { it.show }.forEach { l ->
        label(tm, l.text, l.x.toFloat(), (g.h - 9.dp.toPx()).toFloat(), if (sel != null && l.key == sel && selStyle != null) selStyle else style)
    }
}

@Composable
private fun axisStyle(ink: ChartInk) = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp, color = ink.axis, textDirection = TextDirection.Ltr)

/**
 * A chart with a touch readout: [draw] paints into the canvas and returns
 * where the selection sits (its x in px), [tip] is shown at the top over
 * the selection, clamped inside. Dragging sideways moves [onScrub] (the x
 * in px) and lifting the finger calls [onScrubEnd]; a tap goes to [onTap].
 */
@Composable
fun ChartFrame(
    height: Dp,
    description: String,
    selX: Float?,
    onScrub: (Float) -> Unit,
    onTap: (Float) -> Unit,
    modifier: Modifier = Modifier,
    onScrubEnd: () -> Unit = {},
    tip: (@Composable () -> Unit)? = null,
    draw: DrawScope.() -> Unit
) {
    val scrub by rememberUpdatedState(onScrub)
    val scrubEnd by rememberUpdatedState(onScrubEnd)
    val tap by rememberUpdatedState(onTap)
    Layout(
        content = {
            Canvas(
                Modifier
                    .fillMaxWidth()
                    .height(height)
                    .semantics { contentDescription = description }
                    .pointerInput(Unit) { detectTapGestures(onTap = { tap(it.x) }) }
                    .pointerInput(Unit) {
                        detectHorizontalDragGestures(
                            onDragStart = { scrub(it.x) },
                            onDragEnd = { scrubEnd() },
                            onDragCancel = { scrubEnd() }
                        ) { change, _ -> change.consume(); scrub(change.position.x) }
                    }
            ) { draw() }
            if (tip != null && selX != null) {
                Column(
                    Modifier
                        .background(MaterialTheme.colorScheme.surfaceContainerHigh, RoundedCornerShape(8.dp))
                        .border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(8.dp))
                        .padding(horizontal = 10.dp, vertical = 6.dp)
                ) { tip() }
            }
        },
        modifier = modifier.fillMaxWidth()
    ) { measurables, constraints ->
        val canvas = measurables[0].measure(constraints)
        val tipP = measurables.getOrNull(1)?.measure(Constraints(maxWidth = min(constraints.maxWidth, 240.dp.roundToPx())))
        layout(canvas.width, canvas.height) {
            canvas.place(0, 0)
            if (tipP != null && selX != null) {
                val gap = 12.dp.roundToPx()
                // Beside the finger's line: right of it, or left when there's no room.
                val right = selX.toInt() + gap
                val x = if (right + tipP.width <= canvas.width) right else max(0, selX.toInt() - gap - tipP.width)
                tipP.place(x, 0)
            }
        }
    }
}

/** One readout line: a key swatch, the words, the amount. */
@Composable
fun TipLine(color: Color?, text: String, muted: Boolean = false, dashed: Boolean = false) {
    Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
        if (color != null) Key(color, dashed = dashed, modifier = Modifier.padding(end = 6.dp))
        Text(
            text,
            style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content),
            color = if (muted) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface
        )
    }
}

@Composable
fun TipTitle(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.SemiBold), color = MaterialTheme.colorScheme.onSurface)
}

/** A legend key: a square for a fill, a short line for a line (dashed for a typical / forecast line). */
@Composable
fun Key(color: Color, modifier: Modifier = Modifier, line: Boolean = false, dashed: Boolean = false, ring: Boolean = false) {
    Canvas(modifier.size(if (line || dashed) 14.dp else 9.dp, 9.dp)) {
        when {
            ring -> drawCircle(color, radius = size.height / 2 - 1.dp.toPx(), style = Stroke(2.dp.toPx()))
            dashed -> drawLine(color, Offset(0f, size.height / 2), Offset(size.width, size.height / 2), 2.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(4.dp.toPx(), 3.dp.toPx())))
            line -> drawLine(color, Offset(0f, size.height / 2), Offset(size.width, size.height / 2), 2.dp.toPx())
            else -> drawRoundRect(color, cornerRadius = CornerRadius(2.dp.toPx()))
        }
    }
}

/** A legend entry: its colour, how its key looks, its words. */
data class LegendItem(val color: Color, val label: String, val line: Boolean = false, val dashed: Boolean = false, val ring: Boolean = false)

/** A legend row of keys that wraps; [dimmed] entries are faded (another series is focused). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ChartLegend(items: List<LegendItem>, modifier: Modifier = Modifier, dimmed: Set<Int> = emptySet()) {
    FlowRow(modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        items.forEachIndexed { i, it ->
            Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically, modifier = Modifier.alpha(if (i in dimmed) 0.4f else 1f)) {
                Key(it.color, line = it.line, dashed = it.dashed, ring = it.ring)
                Spacer(Modifier.width(5.dp))
                Text(it.label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

// ------------------------------------------------------------------ spark bars

/** A row's 12-month bar sparkline, the last bar stronger (lighter when the month is in progress). */
@Composable
fun SparkBars(values: List<Long>, color: Color, partial: Boolean = false, modifier: Modifier = Modifier) {
    Canvas(modifier.size(60.dp, 18.dp)) {
        val (mx, bars) = MoneyDashGeometry.sparkBars(values, size.width.toDouble(), size.height.toDouble(), 1.5.dp.toPx().toDouble())
        if (mx == 0L) {
            drawLine(color.copy(alpha = 0.25f), Offset(0f, size.height - 0.5f), Offset(size.width, size.height - 0.5f), 1f)
            return@Canvas
        }
        bars.forEach { b ->
            if (b.h <= 0) return@forEach
            val c = when {
                b.last && partial -> color.copy(alpha = 0.55f)
                b.last -> color
                else -> color.copy(alpha = 0.4f)
            }
            drawRoundRect(c, Offset(b.x.toFloat(), b.y.toFloat()), Size(b.w.toFloat(), b.h.toFloat()), CornerRadius(0.8.dp.toPx()))
        }
    }
}

// ------------------------------------------------------------------ line helpers

private fun DrawScope.crosshair(x: Float, top: Float, bottom: Float, ink: ChartInk) {
    drawLine(ink.ink.copy(alpha = 0.5f), Offset(x, top), Offset(x, bottom), 1.dp.toPx())
}

private fun DrawScope.dot(at: Offset, color: Color, ring: Color, r: Dp = 4.dp) {
    drawCircle(ring, r.toPx() + 2.dp.toPx(), at)
    drawCircle(color, r.toPx(), at)
}

private fun DrawScope.todayMark(tm: TextMeasurer, x: Float, g: Box, ink: ChartInk, style: TextStyle) {
    drawLine(ink.accent.copy(alpha = 0.7f), Offset(x, g.t.toFloat()), Offset(x, g.base.toFloat()), 1.dp.toPx(),
        pathEffect = PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 3.dp.toPx())))
    label(tm, "today", x, (g.t - 6.dp.toPx()).toFloat(), style.copy(color = ink.accent))
}

/** Remembered selection that resets when [key] changes (new data). */
@Composable
fun rememberSel(key: Any?): MutableState<Int?> = remember(key) { mutableStateOf(null) }

// ------------------------------------------------------------------ pace

/**
 * The everyday pace: this month cumulative (accent line, a soft area), the
 * typical month dashed, the usual range as a band, a today marker; drag to
 * read any day.
 */
@Composable
fun PaceChart(
    p: io.github.ronganam.dana.data.money.MoneyPace,
    thisName: String,
    monthName: String,
    height: Dp,
    narrow: Boolean
) {
    val ink = chartInk()
    val tm = rememberTextMeasurer()
    val style = axisStyle(ink)
    val sel = rememberSel(p)
    var geo by remember(p) { mutableStateOf<MoneyDashGeometry.PaceGeo?>(null) }
    val pad = if (narrow) Pad(l = 40.dp, r = 8.dp) else Pad(l = 50.dp, r = 14.dp)
    val g0 = geo
    val i = sel.value
    val v = i?.let { MoneyDashGeometry.paceAt(p, it) }
    ChartFrame(
        height = height,
        description = "Everyday spending by day, cumulative, against a typical month",
        selX = if (g0 != null && i != null) g0.x(i).toFloat() else null,
        onScrub = { x -> geo?.let { sel.value = it.indexAt(x.toDouble()) } },
        onTap = { x -> geo?.let { val n = it.indexAt(x.toDouble()); sel.value = if (sel.value == n) null else n } },
        tip = v?.let {
            {
                TipTitle("${it.day} $monthName")
                it.thisMonth?.let { t -> TipLine(MoneyPalette.line, "$thisName ${money(t)}") }
                it.typical?.let { t -> TipLine(ink.axis, "Typical ${money(t)}", dashed = true) }
                if (it.low != null && it.high != null && it.high > it.low) TipLine(null, "usually ${MoneyFormat.whole(it.low)} – ${MoneyFormat.whole(it.high)}", muted = true)
            }
        }
    ) {
        val g = MoneyDashGeometry.pace(p, box(pad))
        if (geo != g) geo = g
        grid(tm, g.box, g.ticks, ink, style)
        xLabels(tm, g.box, g.xLabels.map { it }, style)
        if (g.band.isNotEmpty()) drawPath(path(g.band, close = true), MoneyPalette.line.copy(alpha = 0.10f))
        if (g.thisLine.size > 1) {
            val area = Path().apply {
                addPath(path(g.thisLine))
                lineTo(g.thisLine.last().x.toFloat(), g.box.base.toFloat())
                lineTo(g.thisLine.first().x.toFloat(), g.box.base.toFloat())
                close()
            }
            drawPath(area, MoneyPalette.line.copy(alpha = 0.12f))
        }
        if (g.typicalLine.size > 1) drawPath(path(g.typicalLine), ink.axis.copy(alpha = 0.85f),
            style = Stroke(1.5.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(4.dp.toPx(), 3.dp.toPx()))))
        if (g.thisLine.size > 1) drawPath(path(g.thisLine), MoneyPalette.line, style = Stroke(2.25.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
        g.todayX?.let { todayMark(tm, it.toFloat(), g.box, ink, style) }
        g.end?.let { dot(it.o(), MoneyPalette.line, ink.canvas) }
        val s = sel.value
        if (s != null) {
            val x = g.x(s).toFloat()
            crosshair(x, g.box.t.toFloat(), g.box.base.toFloat(), ink)
            MoneyDashGeometry.paceAt(p, s).let { at ->
                at.thisMonth?.let { dot(Offset(x, g.y(it).toFloat()), MoneyPalette.line, ink.canvas) }
                at.typical?.let { dot(Offset(x, g.y(it).toFloat()), ink.axis, ink.canvas, 3.5.dp) }
            }
        }
    }
}

private fun money(v: Long) = io.github.ronganam.dana.data.money.MoneyFormat.amount(v, io.github.ronganam.dana.data.money.MoneyFormat.Sign.NONE)
private fun whole(v: Long) = io.github.ronganam.dana.data.money.MoneyFormat.whole(v)

// ------------------------------------------------------------------ 12 months

/**
 * Spending per month stacked by group; the selected month outlined in the
 * accent; [focus] dims the other series. Drag reads a month, tap opens it.
 */
@Composable
fun MonthsChart(
    d: io.github.ronganam.dana.data.money.MoneyMonths,
    selectedMonth: String,
    focus: String?,
    height: Dp,
    narrow: Boolean,
    onOpen: (String) -> Unit
) {
    val ink = chartInk()
    val tm = rememberTextMeasurer()
    val style = axisStyle(ink)
    val selStyle = style.copy(color = ink.accent, fontWeight = FontWeight.Bold)
    val sel = rememberSel(d)
    var geo by remember(d) { mutableStateOf<MoneyDashGeometry.MonthsGeo?>(null) }
    val pad = Pad(l = if (narrow) 40.dp else 46.dp, r = 6.dp, t = 8.dp, b = 22.dp)
    val labels = d.groups.associate { it.key to it.label }
    val i = sel.value
    val bar = i?.let { geo?.bars?.getOrNull(it) }
    ChartFrame(
        height = height,
        description = "Spending per month for 12 months, by group",
        selX = bar?.cx?.toFloat(),
        onScrub = { x -> geo?.barAt(x.toDouble())?.let { sel.value = it } },
        onTap = { x -> geo?.let { g -> g.barAt(x.toDouble())?.let { onOpen(g.bars[it].month) } } },
        onScrubEnd = { sel.value = null },
        tip = bar?.let { b ->
            {
                val m = d.months.getOrNull(i)
                TipTitle("${b.label} · ${money(b.total)}")
                val parts = m?.groups?.filter { it.value > 0 }?.entries?.sortedByDescending { it.value }.orEmpty()
                parts.take(6).forEach { (k, v) ->
                    TipLine(MoneyPalette.series(MoneyDashGeometry.seriesOf(if (k == "uncategorized") null else k)), "${labels[k] ?: k} ${money(v)}")
                }
                if (parts.size > 6) TipLine(null, "+ ${parts.size - 6} more", muted = true)
            }
        }
    ) {
        val g = MoneyDashGeometry.months(d, box(pad), 2.dp.toPx().toDouble())
        if (geo != g) geo = g
        grid(tm, g.box, g.ticks, ink, style)
        g.bars.forEachIndexed { bi, b ->
            b.segments.forEach { s ->
                val c = MoneyPalette.series(s.key)
                drawSeg(s, if (focus != null && focus != s.key) c.copy(alpha = 0.25f) else c)
            }
            if (b.month == selectedMonth) {
                drawRoundRect(ink.accent, Offset((b.slotX + 1).toFloat(), (g.box.t - 4).toFloat()), Size((b.slotW - 2).toFloat(), (g.box.plotH + 8).toFloat()),
                    CornerRadius(4.dp.toPx()), style = Stroke(1.dp.toPx()))
            }
            if (bi == sel.value) drawRect(ink.ink.copy(alpha = 0.06f), Offset(b.slotX.toFloat(), g.box.t.toFloat()), Size(b.slotW.toFloat(), g.box.plotH.toFloat()))
        }
        xLabels(tm, g.box, g.xLabels, style, selStyle, selectedMonth)
    }
}

// ------------------------------------------------------------------ cash flow

/** Money in and out: income up, spending stacked down, set aside beside, the net line. Drag reads, tap opens the month. */
@Composable
fun CashflowChart(
    cf: io.github.ronganam.dana.data.money.MoneyCashflow,
    selectedMonth: String,
    height: Dp,
    narrow: Boolean,
    onOpen: (String) -> Unit
) {
    val ink = chartInk()
    val tm = rememberTextMeasurer()
    val style = axisStyle(ink)
    val selStyle = style.copy(color = ink.accent, fontWeight = FontWeight.Bold)
    val sel = rememberSel(cf)
    var geo by remember(cf) { mutableStateOf<MoneyDashGeometry.CashGeo?>(null) }
    val pad = Pad(l = if (narrow) 44.dp else 52.dp, r = 8.dp, t = 10.dp, b = 22.dp)
    val i = sel.value
    val bar = i?.let { geo?.bars?.getOrNull(it) }
    ChartFrame(
        height = height,
        description = "Money in and out per month for 12 months",
        selX = bar?.cx?.toFloat(),
        onScrub = { x -> geo?.barAt(x.toDouble())?.let { sel.value = it } },
        onTap = { x -> geo?.let { g -> g.barAt(x.toDouble())?.let { onOpen(g.bars[it].month) } } },
        onScrubEnd = { sel.value = null },
        tip = bar?.let {
            {
                val m = cf.months[i]
                TipTitle(m.label + if (m.complete) "" else " · so far")
                listOf("income" to m.income, "fixed" to m.fixed, "yearly" to m.yearly, "everyday" to m.everyday, "one_off" to m.oneOff, "set_aside" to m.setAside)
                    .filter { it.second != 0L }
                    .forEach { (k, v) -> TipLine(MoneyPalette.flow(k), "${MoneyDashGeometry.FLOW_LABELS[k]} ${money(v)}") }
                TipLine(ink.ink, "Net ${io.github.ronganam.dana.data.money.MoneyFormat.amount(m.net, io.github.ronganam.dana.data.money.MoneyFormat.Sign.PLUS)}")
                m.savingsRate?.let { r -> TipLine(null, if (r >= 0) "Kept ${Math.round(r * 100)}% of income" else "More out than in", muted = true) }
            }
        }
    ) {
        val g = MoneyDashGeometry.cashflow(cf, box(pad), 2.dp.toPx().toDouble())
        if (geo != g) geo = g
        grid(tm, g.box, g.ticks, ink, style)
        g.bars.forEachIndexed { bi, b ->
            val a = if (b.complete) 1f else 0.6f
            b.income?.let { drawSeg(it, MoneyPalette.flow("income").copy(alpha = a)) }
            b.segments.forEach { drawSeg(it, MoneyPalette.flow(it.key).copy(alpha = a)) }
            b.setAside?.let { s ->
                drawSeg(s, MoneyPalette.flow("set_aside").copy(alpha = 0.3f * a))
                drawRect(MoneyPalette.flow("set_aside").copy(alpha = a), Offset(s.x.toFloat(), s.y.toFloat()), Size(s.w.toFloat(), s.h.toFloat()), style = Stroke(1.dp.toPx()))
            }
            if (b.month == selectedMonth) {
                drawRoundRect(ink.accent, Offset((b.slotX + 1).toFloat(), (g.box.t - 4).toFloat()), Size((b.slotW - 2).toFloat(), (g.box.plotH + 8).toFloat()),
                    CornerRadius(4.dp.toPx()), style = Stroke(1.dp.toPx()))
            }
            if (bi == sel.value) drawRect(ink.ink.copy(alpha = 0.06f), Offset(b.slotX.toFloat(), g.box.t.toFloat()), Size(b.slotW.toFloat(), g.box.plotH.toFloat()))
        }
        val net = g.netLine
        if (net.size > 1) drawPath(path(net), ink.ink.copy(alpha = 0.8f), style = Stroke(1.5.dp.toPx(), join = StrokeJoin.Round))
        g.bars.filter { it.showNet }.forEach { b ->
            drawCircle(ink.canvas, (if (b.netValue != 0L) 4.dp else 2.5.dp).toPx() + 1.5.dp.toPx(), b.net.o())
            drawCircle(if (b.netValue < 0) MoneyPalette.bad else ink.ink, (if (b.netValue != 0L) 4.dp else 2.5.dp).toPx(), b.net.o())
        }
        xLabels(tm, g.box, g.xLabels, style, selStyle, selectedMonth)
    }
}

// ------------------------------------------------------------------ balance + forecast

/** The bank balance: history solid, forecast dashed with its likely range, today, the low point; drag reads a day. */
@Composable
fun BalanceChart(b: io.github.ronganam.dana.data.money.MoneyBalance, horizon: Int, height: Dp, narrow: Boolean) {
    val ink = chartInk()
    val tm = rememberTextMeasurer()
    val style = axisStyle(ink)
    val sel = rememberSel(b to horizon)
    var geo by remember(b, horizon) { mutableStateOf<MoneyDashGeometry.LineGeo?>(null) }
    val pad = Pad(l = if (narrow) 46.dp else 56.dp, r = 12.dp, t = 14.dp, b = 24.dp)
    val density = LocalDensity.current
    val pt = sel.value?.let { geo?.points?.getOrNull(it) }
    ChartFrame(
        height = height,
        description = "Bank balance: the past and the forecast",
        selX = pt?.x?.toFloat(),
        onScrub = { x -> geo?.let { sel.value = it.indexAt(x.toDouble()) } },
        onTap = { x -> geo?.let { val n = it.indexAt(x.toDouble()); sel.value = if (sel.value == n) null else n } },
        tip = pt?.let { balanceTip(it) }
    ) {
        val g = MoneyDashGeometry.balance(b, box(pad), historyDays = horizon, labelSpacing = with(density) { 56.dp.toPx().toDouble() }) ?: return@ChartFrame
        if (geo != g) geo = g
        grid(tm, g.box, g.ticks, ink, style)
        xLabels(tm, g.box, g.xLabels, style)
        if (g.band.isNotEmpty()) drawPath(path(g.band, close = true), MoneyPalette.line.copy(alpha = 0.13f))
        g.todayX?.let { todayMark(tm, it.toFloat(), g.box, ink, style) }
        if (g.history.size > 1) drawPath(path(g.history), MoneyPalette.line, style = Stroke(2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
        if (g.forecast.size > 1) drawPath(path(g.forecast), MoneyPalette.line,
            style = Stroke(2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round, pathEffect = PathEffect.dashPathEffect(floatArrayOf(5.dp.toPx(), 4.dp.toPx()))))
        g.low?.let { low ->
            val c = if (low.value < 0) MoneyPalette.bad else MoneyPalette.line
            dot(Offset(low.x.toFloat(), low.y.toFloat()), c, ink.canvas, 4.5.dp)
            val align = when {
                low.x > g.box.w - 90.dp.toPx() -> 1f
                low.x < g.box.l + 60.dp.toPx() -> 0f
                else -> 0.5f
            }
            label(tm, "lowest ${io.github.ronganam.dana.data.money.MoneyFormat.compact(low.value.toDouble())}", low.x.toFloat(),
                min(g.box.base - 8.dp.toPx(), low.y + 14.dp.toPx()).toFloat(), style.copy(color = ink.ink), align, backing = ink.canvas)
        }
        sel.value?.let { g.points.getOrNull(it) }?.let { p ->
            crosshair(p.x.toFloat(), g.box.t.toFloat(), g.box.base.toFloat(), ink)
            dot(Offset(p.x.toFloat(), p.y.toFloat()), MoneyPalette.line, ink.canvas)
        }
    }
}

private fun balanceTip(p: MoneyDashGeometry.TimePoint): @Composable () -> Unit = {
    TipTitle(io.github.ronganam.dana.data.money.MoneyDashText.longDate(p.date))
    TipLine(null, (if (p.forecast) "Forecast " else "Balance ") +
        io.github.ronganam.dana.data.money.MoneyFormat.amount(p.value, cents = !p.forecast))
    if (p.forecast && p.low != null && p.high != null && p.low != p.high) TipLine(null, "${MoneyFormat.whole(p.low)} – ${MoneyFormat.whole(p.high)}", muted = true)
}

/** Coming up's sparkline: the next 45 days of the forecast, scaled to itself, the 45-day low marked. */
@Composable
fun UpSpark(fc: io.github.ronganam.dana.data.money.MoneyForecast, series: List<io.github.ronganam.dana.data.money.BalancePoint>) {
    val ink = chartInk()
    val data = remember(fc, series) { io.github.ronganam.dana.data.money.MoneyBalance(emptyList(), fc.copy(series = series)) }
    val sel = rememberSel(data)
    var geo by remember(data) { mutableStateOf<MoneyDashGeometry.LineGeo?>(null) }
    val pad = Pad(l = 4.dp, r = 4.dp, t = 10.dp, b = 8.dp)
    val pt = sel.value?.let { geo?.points?.getOrNull(it) }
    ChartFrame(
        height = 64.dp,
        description = "Projected bank balance for the next 45 days",
        selX = pt?.x?.toFloat(),
        onScrub = { x -> geo?.let { sel.value = it.indexAt(x.toDouble()) } },
        onTap = { x -> geo?.let { val n = it.indexAt(x.toDouble()); sel.value = if (sel.value == n) null else n } },
        tip = pt?.let { balanceTip(it) }
    ) {
        val g = MoneyDashGeometry.balance(data, box(pad), includeZero = false, lowOverride = fc.soonLow, useLowOverride = true) ?: return@ChartFrame
        if (geo != g) geo = g
        if (g.band.isNotEmpty()) drawPath(path(g.band, close = true), MoneyPalette.line.copy(alpha = 0.13f))
        g.zeroY?.let { drawLine(ink.zero, Offset(g.box.l.toFloat(), it.toFloat()), Offset((g.box.w - g.box.r).toFloat(), it.toFloat()), 1.dp.toPx()) }
        drawPath(path(g.forecast), MoneyPalette.line,
            style = Stroke(2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round, pathEffect = PathEffect.dashPathEffect(floatArrayOf(5.dp.toPx(), 4.dp.toPx()))))
        g.low?.let { dot(Offset(it.x.toFloat(), it.y.toFloat()), if (it.value < 0) MoneyPalette.bad else MoneyPalette.line, ink.canvas) }
        sel.value?.let { g.points.getOrNull(it) }?.let { p ->
            crosshair(p.x.toFloat(), g.box.t.toFloat(), g.box.base.toFloat(), ink)
            dot(Offset(p.x.toFloat(), p.y.toFloat()), MoneyPalette.line, ink.canvas)
        }
    }
}

// ------------------------------------------------------------------ portfolio value

/** The portfolio's value per day: a line with a soft area, money put in / taken out ringed on it; drag reads a day. */
@Composable
fun ValueChart(p: io.github.ronganam.dana.data.money.MoneyPortfolio, days: Int, height: Dp, narrow: Boolean) {
    val ink = chartInk()
    val tm = rememberTextMeasurer()
    val style = axisStyle(ink)
    val sel = rememberSel(p to days)
    var geo by remember(p, days) { mutableStateOf<MoneyDashGeometry.ValueGeo?>(null) }
    val pad = Pad(l = if (narrow) 46.dp else 56.dp, r = 12.dp, t = 12.dp, b = 24.dp)
    val density = LocalDensity.current
    val pt = sel.value?.let { geo?.points?.getOrNull(it) }
    ChartFrame(
        height = height,
        description = "Portfolio value over time",
        selX = pt?.x?.toFloat(),
        onScrub = { x -> geo?.let { sel.value = it.geo.indexAt(x.toDouble()) } },
        onTap = { x -> geo?.let { val n = it.geo.indexAt(x.toDouble()); sel.value = if (sel.value == n) null else n } },
        tip = pt?.let { tp ->
            {
                TipTitle(io.github.ronganam.dana.data.money.MoneyDashText.longDate(tp.date))
                TipLine(null, "Value ${MoneyFormat.whole(tp.value)}")
                if (tp.flow != 0L) TipLine(MoneyPalette.flow("set_aside"), "${if (tp.flow > 0) "Put in" else "Taken out"} ${MoneyFormat.whole(kotlin.math.abs(tp.flow))}", muted = true)
            }
        }
    ) {
        val vg = MoneyDashGeometry.value(p.history, box(pad), days, p.flows, with(density) { 56.dp.toPx().toDouble() }) ?: return@ChartFrame
        if (geo != vg) geo = vg
        val g = vg.geo
        grid(tm, g.box, g.ticks, ink, style)
        xLabels(tm, g.box, g.xLabels, style)
        if (g.history.size > 1) {
            val area = Path().apply {
                addPath(path(g.history))
                lineTo(g.history.last().x.toFloat(), g.box.base.toFloat())
                lineTo(g.history.first().x.toFloat(), g.box.base.toFloat())
                close()
            }
            drawPath(area, MoneyPalette.line.copy(alpha = 0.12f))
            drawPath(path(g.history), MoneyPalette.line, style = Stroke(2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
        }
        g.history.lastOrNull()?.let { dot(it.o(), MoneyPalette.line, ink.canvas, 3.5.dp) }
        vg.marks.forEach { m ->
            drawCircle(ink.canvas, 4.5.dp.toPx(), Offset(m.x.toFloat(), m.y.toFloat()))
            drawCircle(if (m.flow < 0) MoneyPalette.bad else MoneyPalette.flow("set_aside"), 4.5.dp.toPx(), Offset(m.x.toFloat(), m.y.toFloat()), style = Stroke(2.dp.toPx()))
        }
        sel.value?.let { g.points.getOrNull(it) }?.let { tp ->
            crosshair(tp.x.toFloat(), g.box.t.toFloat(), g.box.base.toFloat(), ink)
            dot(Offset(tp.x.toFloat(), tp.y.toFloat()), MoneyPalette.line, ink.canvas)
        }
    }
}
