package io.github.ronganam.dana.ui.screens.money

import io.github.ronganam.dana.data.money.MoneyFormat
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.money.Insight
import io.github.ronganam.dana.data.money.MoneyBalance
import io.github.ronganam.dana.data.money.MoneyCashflow
import io.github.ronganam.dana.data.money.MoneyDashText
import io.github.ronganam.dana.data.money.MoneyFormat.Sign
import io.github.ronganam.dana.data.money.MoneyInsights
import io.github.ronganam.dana.data.money.MoneyOverview
import io.github.ronganam.dana.data.money.MoneyPace
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.screens.money.charts.ChartLegend
import io.github.ronganam.dana.ui.screens.money.charts.LegendItem
import io.github.ronganam.dana.ui.screens.money.charts.MoneyPalette
import io.github.ronganam.dana.ui.screens.money.charts.PaceChart
import io.github.ronganam.dana.ui.screens.money.charts.UpSpark
import io.github.ronganam.dana.ui.theme.Warning
import io.github.ronganam.dana.ui.viewmodels.MoneyController
import io.github.ronganam.dana.ui.viewmodels.MoneyDashState
import io.github.ronganam.dana.ui.viewmodels.MoneyView
import io.github.ronganam.dana.ui.viewmodels.TxnFilter

// Overview: the four tiles, the month's insights, coming up (the month in
// progress) and the everyday pace (server docs/money.md → Money tab).

@Composable
internal fun OverviewView(d: Dash) {
    val money = d.money
    val ovJson by money.overview.collectAsState()
    val ov = ovJson?.let { runCatching { MoneyOverview.parse(it) }.getOrNull() }?.takeIf { it.month == d.month }
    if (ov == null) {
        SkelTiles(d.twoCols)
        DashSections(d.twoCols, listOf(
            0 to { DashSection("Insights") { SkelRows(4) } },
            1 to { DashSection("Everyday pace") { SkelChart() } }
        ))
    } else {
        OverviewLoaded(d, ov)
    }
}

@Composable
private fun OverviewLoaded(d: Dash, ov: MoneyOverview) {
    val money = d.money
    val fc = if (ov.inProgress) ov.forecast else null
    val cf = d.parsed("cashflow") { MoneyCashflow.parse(it) }
    Tiles(overviewTiles(d, ov, cf, fc), d.twoCols)
    val insights: @Composable () -> Unit = { InsightsSection(d, ov) }
    val pace: @Composable () -> Unit = { PaceSection(d, ov) }
    if (fc != null) {
        if (d.twoCols) {
            DashSections(true, listOf(0 to insights, 1 to { ComingUp(d) }))
            Spacer(Modifier.height(30.dp))
            pace()
        } else {
            DashSections(false, listOf(0 to insights, 0 to { ComingUp(d) }, 0 to pace))
        }
    } else {
        DashSections(d.twoCols, listOf(0 to insights, 1 to pace))
    }
}

private fun overviewTiles(d: Dash, ov: MoneyOverview, cf: MoneyCashflow?, fc: io.github.ronganam.dana.data.money.ForecastTile?): List<TileData> {
    val money = d.money
    val good = MoneyPalette.good
    val bad = MoneyPalette.bad
    val net = MoneyDashText.netTile(ov, cf)
    val ev = MoneyDashText.everydayTile(ov.everyday, ov.inProgress)
    val bt = MoneyDashText.balanceTile(fc, d.today)
    val fx = MoneyDashText.fixedTile(ov)
    return listOf(
        TileData(
            net.label, MoneyFormat.amount(net.value, Sign.PLUS), tone = if (net.value < 0) bad else good,
            strong = net.rate + if (net.typical.isNotEmpty()) " · ${net.typical}" else "", sub = net.sub,
            onClick = { money.select(MoneyView.CASHFLOW) }
        ),
        TileData(
            "Everyday spending", MoneyFormat.amount(ev.value, Sign.NONE),
            strong = ev.track.takeIf { it.isNotEmpty() }?.let { (if (ev.dir == "up") "↗ " else if (ev.dir == "down") "↘ " else "") + it },
            strongTone = when (ev.dir) { "up" -> bad; "down" -> good; else -> null },
            sub = ev.typical.ifEmpty { if (ov.inProgress) "so far this month" else "in all" },
            onClick = { money.select(MoneyView.SPENDING) }
        ),
        if (bt != null) TileData(
            "Balance ahead", MoneyFormat.amount(bt.value), tone = if (bt.value < 0) bad else null,
            strong = bt.low, strongTone = if (bt.tone == "bad") bad else null, sub = bt.end,
            onClick = { money.select(MoneyView.CASHFLOW) }
        ) else TileData(
            "One-offs", MoneyFormat.amount(ov.oneOff, Sign.NONE),
            sub = if (ov.setAside != 0L) "Set aside ${MoneyFormat.whole(ov.setAside, Sign.NONE)}" else "left out of everyday spending",
            onClick = { money.select(MoneyView.SPENDING) }
        ),
        TileData(
            "Fixed costs", MoneyFormat.amount(fx.value, Sign.NONE), per = "/ month",
            strong = fx.share, sub = fx.yearly.ifEmpty { "recurring and yearly charges" },
            onClick = { money.select(MoneyView.CASHFLOW) }
        )
    )
}

// ------------------------------------------------------------------ insights

/** Where an insight's link goes: a transaction, a category focused on its view, or the view. */
internal fun follow(money: MoneyController, link: io.github.ronganam.dana.data.money.InsightLink, title: String) {
    if (link.id != null) { money.openTransaction(link.id, title); return }
    if (link.category != null) money.setFilter(money.filter.value.copy(category = link.category, allTime = false))
    val view = when (link.view) {
        "spending" -> MoneyView.SPENDING
        "cashflow", "plan" -> MoneyView.CASHFLOW
        "save" -> MoneyView.SAVE
        "investments" -> MoneyView.INVESTMENTS
        "meals" -> MoneyView.MEALS
        "transactions" -> MoneyView.TRANSACTIONS
        "accounts" -> MoneyView.ACCOUNTS
        else -> MoneyView.OVERVIEW
    }
    money.select(view)
}

@Composable
private fun InsightsSection(d: Dash, ov: MoneyOverview) {
    val money = d.money
    val items = d.parsed("insights") { MoneyInsights.parse(it) }
    DashSection("Insights", trailing = if (ov.uncategorized > 0) {
        { InkButton("${ov.uncategorized} to sort", emphasis = InkEmphasis.PRIMARY) { money.openTransactions(TxnFilter(category = "uncategorized")) } }
    } else null) {
        when {
            items == null -> SkelRows(4)
            items.isEmpty() -> Empty("Nothing stands out in ${MoneyDashText.monthLabel(d.month)}.")
            else -> items.forEach { InsightRow(it) { link -> follow(money, link, it.title) } }
        }
    }
}

@Composable
private fun InsightRow(it: Insight, onOpen: (io.github.ronganam.dana.data.money.InsightLink) -> Unit) {
    val tone = when (it.tone) {
        "good" -> MoneyPalette.good
        "bad" -> MoneyPalette.bad
        "warn" -> Warning
        else -> MaterialTheme.colorScheme.outline
    }
    val link = it.link
    Row(
        Modifier
            .fillMaxWidth()
            .height(IntrinsicSize.Min)
            .then(if (link != null) Modifier.clickable(role = Role.Button) { onOpen(link) } else Modifier)
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(Modifier.width(3.dp).fillMaxHeight().background(tone))
        Column(Modifier.weight(1f).padding(start = 12.dp, end = 8.dp)) {
            Sentence(it.title, MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium))
            it.detail?.let { det -> Sentence(det, MaterialTheme.typography.bodySmall, MaterialTheme.colorScheme.onSurfaceVariant, Modifier.padding(top = 2.dp)) }
        }
        if (link != null) Text("›", style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    Rule()
}

// ------------------------------------------------------------------ coming up

private val KIND_WORDS = mapOf("income" to "Income", "fixed" to "Fixed", "yearly" to "Yearly", "card_bill" to "Card bill")
private val ITEM_KINDS = mapOf("fixed" to "Fixed", "yearly" to "Yearly", "installment" to "Installment", "everyday" to "Everyday")

@Composable
private fun ComingUp(d: Dash) {
    val b = d.parsed("balance") { MoneyBalance.parse(it) }
    val open by d.dash.openBills.collectAsState()
    DashSection("Coming up", trailing = { Hint("next 45 days") }) {
        val fc = b?.forecast
        when {
            b == null -> { SkelChart(64.dp); SkelRows(4) }
            fc == null -> Empty("Add a bank account to see what's coming.")
            else -> {
                val up = MoneyDashText.upcoming(fc, 45)
                val last = up.series.lastOrNull()
                val low = fc.soonLow
                Row(Modifier.padding(top = 10.dp), verticalAlignment = Alignment.Bottom) {
                    Text(
                        MoneyFormat.amount(fc.balance) + " now" +
                            (last?.let { " → about ${MoneyFormat.whole(it.balance)} on ${MoneyDashText.shortDate(it.date, d.today)}" } ?: ""),
                        style = MaterialTheme.typography.bodyMedium.copy(fontFeatureSettings = "tnum")
                    )
                }
                if (low != null && low.balance < fc.balance) {
                    Text(
                        "lowest ${MoneyFormat.whole(low.balance)} on ${MoneyDashText.shortDate(low.date, d.today)}",
                        style = MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.Medium),
                        color = if (low.balance < 0) MoneyPalette.bad else MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                if (up.series.size >= 2) Box(Modifier.padding(vertical = 6.dp)) { UpSpark(fc, up.series) }
                if (up.events.isEmpty()) Empty("Nothing expected in the next 45 days.")
                var lastDate: String? = null
                up.events.forEach { u ->
                    val e = u.event
                    if (e.date != lastDate) {
                        lastDate = e.date
                        MonoLabel(MoneyDashText.dayLabel(e.date, d.today), Modifier.padding(top = 12.dp, bottom = 2.dp))
                    }
                    val bill = e.kind == "card_bill"
                    val key = MoneyDashState.billKey(e.date, e.accountId, e.label)
                    val isOpen = bill && key in open
                    val name = if (bill) d.accountName(e.accountId).ifEmpty { e.label } else e.label
                    DashRow(onClick = if (bill) ({ d.dash.toggleBill(key) }) else null) {
                        Column(Modifier.weight(1f).padding(end = 10.dp)) {
                            Name(name)
                            Meta((KIND_WORDS[e.kind] ?: e.kind) + if (bill && u.range.isNotEmpty()) " · ${u.range}" else "")
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            if (e.estimated) MonoLabel("about")
                            Amount(
                                MoneyFormat.amount(e.amount, if (e.amount > 0) Sign.PLUS else Sign.MINUS, cents = !e.estimated),
                                MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
                                if (e.amount > 0) MoneyPalette.good else Color.Unspecified
                            )
                        }
                        if (bill) Box(Modifier.padding(start = 10.dp)) { Chevron(isOpen) }
                    }
                    if (isOpen) BillParts(u)
                }
            }
        }
    }
}

@Composable
private fun BillParts(u: MoneyDashText.Upcoming) {
    Column(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .padding(horizontal = 12.dp, vertical = 8.dp)
    ) {
        u.parts.forEach { p ->
            Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
                Text(p.label, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
                Amount(MoneyFormat.amount(p.amount, Sign.NONE, cents = p.key != "everyday"), MaterialTheme.typography.bodySmall)
            }
        }
        if (u.event.items.isNotEmpty()) {
            MonoLabel("On this bill", Modifier.padding(top = 8.dp, bottom = 2.dp))
            u.event.items.forEach { it ->
                Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.weight(1f).padding(end = 8.dp)) { Name(it.label, style = MaterialTheme.typography.bodySmall) }
                    Text("${ITEM_KINDS[it.kind] ?: ""} · ", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Amount(MoneyFormat.amount(it.amount, Sign.NONE), MaterialTheme.typography.bodySmall)
                }
            }
        }
        if (u.event.estimated) Note("The everyday part is an estimate from your usual card spending.")
    }
    Rule()
}

// ------------------------------------------------------------------ everyday pace

@Composable
private fun PaceSection(d: Dash, ov: MoneyOverview) {
    val p = d.parsed("pace") { MoneyPace.parse(it) }?.takeIf { it.month == d.month }
    DashSection("Everyday pace") {
        if (p == null) SkelChart() else PaceBody(d, ov, p)
    }
}

@Composable
private fun PaceBody(d: Dash, ov: MoneyOverview, p: MoneyPace) {
    run {
        val name = MoneyDashText.monthLabel(p.month, short = true, year = false)
        val thisName = if (d.current) "This month" else name
        val hasThis = p.thisMonth.any { it != null && it > 0 }
        val none = !hasThis && (p.high ?: emptyList()).none { (it ?: 0) > 0 }
        if (none) Empty("No everyday spending in ${MoneyDashText.monthLabel(p.month)} yet.")
        else Box(Modifier.padding(top = 10.dp)) { PaceChart(p, thisName, name, if (d.narrow) 190.dp else 220.dp, d.narrow) }
        val ink = MaterialTheme.colorScheme.onSurfaceVariant
        ChartLegend(
            buildList {
                add(LegendItem(MoneyPalette.line, thisName, line = true))
                if (p.typical != null) {
                    add(LegendItem(ink, "Typical", dashed = true))
                    add(LegendItem(MoneyPalette.line.copy(alpha = 0.25f), "Usual range"))
                }
            }
        )
        val so = p.thisMonth.lastOrNull { it != null } ?: 0L
        val at = p.today?.let { it - 1 } ?: ((p.typical?.size ?: 0) - 1)
        val typ = p.typical?.takeIf { at >= 0 }?.getOrNull(minOf(at, p.typical.size - 1))
        val track = if (ov.month == p.month && ov.everyday.onTrack != null && p.today != null) " · on track for ${MoneyFormat.whole(ov.everyday.onTrack)}" else ""
        val foot = MoneyFormat.amount(so, Sign.NONE) + (if (p.today != null) " so far" else " in all") + track +
            (typ?.let { " · typical ${MoneyFormat.whole(it)} ${if (p.today != null) "by day ${p.today}" else "for the month"}" } ?: "")
        Text(foot, Modifier.padding(top = 10.dp), style = MaterialTheme.typography.bodyMedium.copy(fontFeatureSettings = "tnum"))
        if (p.typical == null) Note("A typical month needs a whole month of history.")
        Note("Everyday spending only: fixed costs, yearly charges and one-offs are left out. Typical = the median of the last ${p.months} whole months.")
    }
}
