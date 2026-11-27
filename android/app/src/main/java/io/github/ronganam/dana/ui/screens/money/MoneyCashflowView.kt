package io.github.ronganam.dana.ui.screens.money

import io.github.ronganam.dana.data.money.MoneyFormat
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.money.MoneyBalance
import io.github.ronganam.dana.data.money.MoneyCashflow
import io.github.ronganam.dana.data.money.MoneyDashGeometry
import io.github.ronganam.dana.data.money.MoneyDashText
import io.github.ronganam.dana.data.money.MoneyFormat.Sign
import io.github.ronganam.dana.data.money.MoneyOverview
import io.github.ronganam.dana.data.money.MoneyRecurring
import io.github.ronganam.dana.data.money.RecurringItem
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.screens.money.charts.BalanceChart
import io.github.ronganam.dana.ui.screens.money.charts.CashflowChart
import io.github.ronganam.dana.ui.screens.money.charts.ChartLegend
import io.github.ronganam.dana.ui.screens.money.charts.LegendItem
import io.github.ronganam.dana.ui.screens.money.charts.MoneyPalette
import io.github.ronganam.dana.ui.viewmodels.TxnFilter

// Cash flow: 12 months in and out, the bank balance with its forecast, and the
// fixed costs (recurring, yearly, installment plans, regular income).

@Composable
internal fun CashflowView(d: Dash) {
    InAndOut(d)
    Spacer(Modifier.height(30.dp))
    DashSections(d.twoCols, listOf(0 to { BankBalance(d) }, 1 to { FixedCosts(d) }))
}

@Composable
private fun InAndOut(d: Dash) {
    val cf = d.parsed("cashflow") { MoneyCashflow.parse(it) }
    DashSection("Money in and out", trailing = { Hint("12 months to ${MoneyDashText.monthLabel(d.month, short = true)}") }) {
        when {
            cf == null -> SkelChart(220.dp)
            cf.months.none { it.income != 0L || it.spent != 0L || it.setAside != 0L } -> Empty("Nothing in or out in these 12 months yet.")
            else -> {
                Box(Modifier.padding(top = 12.dp)) {
                    CashflowChart(cf, d.month, if (d.narrow) 220.dp else 260.dp, d.narrow) { d.money.setMonth(it) }
                }
                val legend = MoneyDashGeometry.cashflow(cf, MoneyDashGeometry.Box(600.0, 220.0, 50.0, 8.0, 10.0, 22.0)).legend
                val ink = MaterialTheme.colorScheme.onSurface
                ChartLegend(legend.map { l -> if (l.key == "net") LegendItem(ink, l.label, line = true) else LegendItem(MoneyPalette.flow(l.key), l.label) })
                Text(MoneyDashText.cashflowTotalsText(cf), Modifier.padding(top = 10.dp), style = MaterialTheme.typography.bodyMedium.copy(fontFeatureSettings = "tnum"))
                Note("Income above the line; spending below it — fixed, yearly, everyday and one-offs. Money set aside is the thin bar beside: not spending. Drag to read a month, tap to open it.")
            }
        }
    }
}

private val HORIZONS = listOf(90 to "3 months", 180 to "6 months", 365 to "12 months")

@Composable
private fun BankBalance(d: Dash) {
    val horizon by d.dash.horizon.collectAsState()
    val b = d.parsed("balance") { MoneyBalance.parse(it) }
    DashSection("Bank balance", trailing = { DashSwitch(HORIZONS, horizon) { d.dash.setHorizon(it) } }) {
        when {
            b == null -> SkelChart(200.dp)
            b.history.isEmpty() && b.forecast == null -> Empty("Add a bank account to see its balance and where it's heading.")
            else -> {
                val fc = b.forecast
                val now = fc?.balance ?: b.history.last().balance
                val last = fc?.series?.lastOrNull()
                Text(
                    MoneyFormat.amount(now) + " now" + (last?.let { " · about ${MoneyFormat.whole(it.balance)} on ${MoneyDashText.shortDate(it.date, d.today)}" } ?: ""),
                    Modifier.padding(top = 10.dp), style = MaterialTheme.typography.bodyMedium.copy(fontFeatureSettings = "tnum")
                )
                fc?.lowPoint?.let {
                    Text(
                        "lowest ${MoneyFormat.whole(it.balance)} on ${MoneyDashText.shortDate(it.date, d.today)}",
                        style = MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.Medium),
                        color = if (it.balance < 0) MoneyPalette.bad else MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Box(Modifier.padding(top = 10.dp)) { BalanceChart(b, horizon, if (d.narrow) 200.dp else 230.dp, d.narrow) }
                ChartLegend(buildList {
                    add(LegendItem(MoneyPalette.line, "Balance", line = true))
                    if (fc != null) {
                        add(LegendItem(MoneyPalette.line, "Forecast", dashed = true))
                        add(LegendItem(MoneyPalette.line.copy(alpha = 0.25f), "Likely range"))
                    }
                })
                if (fc != null) Note(MoneyDashText.forecastNote(fc))
            }
        }
    }
}

@Composable
private fun FixedCosts(d: Dash) {
    val ovJson by d.money.overview.collectAsState()
    val ov = ovJson?.let { runCatching { MoneyOverview.parse(it) }.getOrNull() }?.takeIf { it.month == d.month }
    val r = d.parsed("recurring") { MoneyRecurring.parse(it) }
    val b = d.parsed("balance") { MoneyBalance.parse(it) }
    DashSection("Fixed costs", trailing = ov?.let { { HeadNum(MoneyFormat.amount(it.fixedMonthly, Sign.NONE), "/ month") } }) {
        if (r == null) SkelRows(5) else {
            val items = r.items.filter { it.direction != "in" }
            val keys = items.map { it.merchantKey }.toSet()
            val yearly = r.yearly.filter { it.merchantKey !in keys }
            val plans = b?.forecast?.plans.orEmpty()
            SubLabel("Recurring")
            if (items.isEmpty()) Empty("No subscriptions or standing orders found yet. They show up after three regular charges.")
            items.forEach { RecurringRow(d, it) }
            if (yearly.isNotEmpty()) {
                SubLabel("Yearly charges")
                yearly.forEach { y ->
                    val ca = MoneyDashText.cadenceAmount(y.amount, y.monthlyCost, "yearly")
                    val meta = listOfNotNull(
                        y.nextDate?.let { "next ${MoneyDashText.shortDate(it, d.today)}" },
                        if (y.payments > 1) "in ${y.payments} payments" else null,
                        y.categoryLabel
                    ).joinToString(" · ")
                    FixedRow(d, y.merchant, meta, null, ca.amount, ca.per, ca.monthly, Color.Unspecified)
                }
            }
            if (plans.isNotEmpty()) {
                SubLabel("Installment plans")
                plans.forEach { p ->
                    val pt = MoneyDashText.planText(p, d.today)
                    FixedRow(
                        d, p.merchant, listOf(pt.left, pt.next, d.accountName(p.accountId)).filter { it.isNotEmpty() }.joinToString(" · "), null,
                        MoneyFormat.amount(p.left, Sign.NONE), "to go", "${MoneyFormat.amount(p.amount, Sign.NONE)} a payment", Color.Unspecified
                    )
                }
            }
            if (r.income.isNotEmpty()) {
                SubLabel("Regular income")
                r.income.forEach { RecurringRow(d, it) }
            }
        }
    }
}

@Composable
private fun SubLabel(text: String) = MetaLabel(text, Modifier.padding(top = 16.dp, bottom = 2.dp))

@Composable
private fun RecurringRow(d: Dash, x: RecurringItem) {
    val ca = MoneyDashText.cadenceAmount(x.amount, x.monthlyCost, x.cadence)
    val meta = listOf(
        MoneyDashText.cadenceLabel(x.cadence),
        x.nextDate?.let { "next ${MoneyDashText.shortDate(it, d.today)}" } ?: "",
        d.accountName(x.accountId).ifEmpty { x.accountLabel.orEmpty() }
    ).filter { it.isNotEmpty() }.joinToString(" · ")
    val flag = x.priceChange?.let { (if (it.to > it.from) "↗ " else "↘ ") + MoneyDashText.priceChangeText(it, d.today) to (it.to > it.from) }
    val inbound = x.direction == "in"
    FixedRow(d, x.merchant, meta, flag, (if (inbound) "+" else "") + ca.amount, ca.per, ca.monthly, if (inbound) MoneyPalette.good else Color.Unspecified)
}

/** A fixed-cost line: the name and its meta (a price change flagged), the amount as its cadence bills it; a tap searches its charges. */
@Composable
private fun FixedRow(d: Dash, merchant: String, meta: String, flag: Pair<String, Boolean>?, amount: String, per: String, monthly: String, tone: Color) {
    DashRow(onClick = { d.money.openTransactions(TxnFilter(q = merchant, allTime = true)) }) {
        Column(Modifier.weight(1f).padding(end = 10.dp)) {
            Name(merchant)
            Meta(meta)
            flag?.let { (text, up) ->
                Text(text, style = MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.Medium), color = if (up) MoneyPalette.bad else MoneyPalette.good)
            }
        }
        Column(horizontalAlignment = Alignment.End) {
            Row(verticalAlignment = Alignment.Bottom) {
                Amount(amount, MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold), tone)
                Text(" $per", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (monthly.isNotEmpty()) Text(monthly, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
