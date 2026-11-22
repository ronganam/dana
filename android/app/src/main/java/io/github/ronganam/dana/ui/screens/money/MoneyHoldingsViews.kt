package io.github.ronganam.dana.ui.screens.money

import io.github.ronganam.dana.data.money.MoneyFormat
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.money.Holding
import io.github.ronganam.dana.data.money.MoneyDashGeometry
import io.github.ronganam.dana.data.money.MoneyDashText
import io.github.ronganam.dana.data.money.MoneyFormat.Sign
import io.github.ronganam.dana.data.money.MoneyMeals
import io.github.ronganam.dana.data.money.MoneyPortfolio
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.screens.money.charts.ChartLegend
import io.github.ronganam.dana.ui.screens.money.charts.Key
import io.github.ronganam.dana.ui.screens.money.charts.LegendItem
import io.github.ronganam.dana.ui.screens.money.charts.MoneyPalette
import io.github.ronganam.dana.ui.screens.money.charts.ValueChart
import java.time.ZonedDateTime

// Investments (read-only portfolio) and Meal card: not tied to the month switcher.

// ------------------------------------------------------------------ Investments

private val RANGES = listOf(30 to "1 month", 90 to "3 months", 365 to "1 year")

@Composable
internal fun InvestmentsView(d: Dash) {
    val p = d.parsed("portfolio") { MoneyPortfolio.parse(it) }
    when {
        p == null -> {
            SkelTiles(d.twoCols)
            DashSections(d.twoCols, listOf(0 to { DashSection("Portfolio value") { SkelChart() } }, 1 to { DashSection("Allocation") { SkelRows(4) } }))
        }
        p.accounts.isEmpty() -> Empty("No investment account yet. Add your investment account under Accounts (a provider addon brings it): its holdings, their value and the account's activity show up here after the first sync.")
        else -> InvestmentsLoaded(d, p)
    }
}

@Composable
private fun InvestmentsLoaded(d: Dash, p: MoneyPortfolio) {
    val t = MoneyDashText.portfolioTiles(p, ZonedDateTime.now())
    fun tone(s: String): Color? = when (s) { "in" -> MoneyPalette.good; "neg" -> MoneyPalette.bad; else -> null }
    fun signed(v: Long?, cents: Boolean = false) = v?.let { MoneyFormat.amount(it, Sign.PLUS, cents) } ?: "—"
    Tiles(listOf(
        TileData("Portfolio value", MoneyFormat.whole(t.value.value ?: 0), sub = t.value.sub),
        TileData("Today", signed(t.today.value, cents = true), tone = tone(t.today.tone), sub = t.today.sub),
        TileData("Profit", signed(t.profit.value), tone = tone(t.profit.tone), sub = t.profit.sub),
        TileData("Cash", MoneyFormat.whole(t.cash.value ?: 0), sub = t.cash.sub.ifEmpty { "not invested" })
    ), d.twoCols)
    DashSections(d.twoCols, listOf(
        0 to { ValueSection(d, p) },
        1 to { Allocation(p) },
        0 to { Holdings(p) },
        1 to { Activity(d, p) }
    ))
    Note("Read-only: Dana signs in as a view-only device and never places orders.", Modifier.padding(top = 22.dp))
}

@Composable
private fun ValueSection(d: Dash, p: MoneyPortfolio) {
    val days by d.dash.invDays.collectAsState()
    DashSection("Portfolio value", trailing = { DashSwitch(RANGES, days) { d.dash.setInvDays(it) } }) {
        val g = MoneyDashGeometry.value(p.history, MoneyDashGeometry.Box(600.0, 220.0, 56.0, 12.0, 12.0, 24.0), days, p.flows)
        if (g == null) Empty("The value over time shows up after the next sync.") else {
            val change = MoneyDashText.valueChangeText(g, d.today)
            if (change.isNotEmpty()) Text(change, Modifier.padding(top = 10.dp), style = MaterialTheme.typography.bodyMedium.copy(fontFeatureSettings = "tnum"))
            Box(Modifier.padding(top = 10.dp)) { ValueChart(p, days, if (d.narrow) 200.dp else 240.dp, d.narrow) }
            if (g.marks.isNotEmpty()) ChartLegend(listOf(
                LegendItem(MoneyPalette.line, "Value", line = true),
                LegendItem(MoneyPalette.flow("set_aside"), "Money put in or taken out", ring = true)
            ))
            Note("The account's value each day, as the site reports it.")
        }
    }
}

@Composable
private fun Allocation(p: MoneyPortfolio) {
    val slices = MoneyDashText.allocationSlices(p.allocation, 8)
    DashSection("Allocation") {
        if (slices.isEmpty()) Empty("Nothing held yet.") else {
            Row(
                Modifier
                    .padding(top = 12.dp, bottom = 6.dp)
                    .fillMaxWidth()
                    .height(10.dp)
                    .clip(RoundedCornerShape(5.dp))
                    .semantics { contentDescription = "Allocation: " + slices.joinToString(", ") { "${it.name} ${it.text}" } },
                horizontalArrangement = Arrangement.spacedBy(2.dp)
            ) {
                slices.forEach { s -> Box(Modifier.weight(s.share.toFloat().coerceAtLeast(0.005f)).fillMaxHeight().background(MoneyPalette.slot(s.slot))) }
            }
            slices.forEach { s ->
                DashRow {
                    Key(MoneyPalette.slot(s.slot), Modifier.padding(end = 10.dp))
                    Box(Modifier.weight(1f).padding(end = 8.dp)) { Name(s.name) }
                    Text(s.text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(end = 12.dp))
                    Amount(MoneyFormat.whole(s.value), MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold), modifier = Modifier.widthIn(min = 76.dp))
                }
            }
        }
    }
}

@Composable
private fun Holdings(p: MoneyPortfolio) {
    val total = p.total.value
    val count = p.accounts.sumOf { it.holdings.size }
    DashSection("Holdings", trailing = { Hint("$count ${if (count == 1) "security" else "securities"}") }) {
        val many = p.accounts.size > 1
        p.accounts.forEach { a ->
            if (many) Meta(
                MoneyDashText.accountName(a.label, a.number, "investment", a.companyName) + " · " + MoneyFormat.whole(a.value),
                Modifier.padding(top = 14.dp, bottom = 4.dp)
            )
            if (a.holdings.isEmpty()) Empty("No holdings in this account.")
            a.holdings.forEach { HoldingRow(it, total) }
        }
    }
}

@Composable
private fun HoldingRow(h: Holding, total: Long) {
    val meta = listOfNotNull(
        h.quantity?.let { "${MoneyDashText.quantityText(it)} × ${MoneyFormat.unitPrice(h.price, h.currency)}" },
        h.currency?.takeIf { !MoneyFormat.isUsers(it) },
        if (total > 0 && h.value != null && h.value != 0L) "${MoneyDashText.pctText(h.value.toDouble() / total * 100, signed = false, digits = 1)} of the portfolio" else null,
        h.security?.let { "#$it" }
    ).joinToString(" · ")
    val tone = when {
        h.profit == null || h.profit == 0L -> MaterialTheme.colorScheme.onSurfaceVariant
        h.profit < 0 -> MoneyPalette.bad
        else -> MoneyPalette.good
    }
    DashRow {
        Column(Modifier.weight(1f).padding(end = 10.dp)) {
            Name(h.name ?: "Security #${h.security}")
            Meta(meta)
        }
        Column(horizontalAlignment = Alignment.End) {
            Amount(h.value?.let { MoneyFormat.whole(it) } ?: "—", MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold))
            h.profit?.let {
                Amount(MoneyFormat.amount(it, Sign.PLUS, cents = false) + (h.profitPct?.let { pc -> " · ${MoneyDashText.pctText(pc)}" } ?: ""),
                    MaterialTheme.typography.bodySmall, tone)
            }
        }
    }
}

@Composable
private fun Activity(d: Dash, p: MoneyPortfolio) {
    DashSection("Activity", trailing = { Hint("buys, sells, dividends, tax") }) {
        if (p.trades.isEmpty()) Empty("No activity in the last year.")
        p.trades.forEach { t ->
            val x = MoneyDashText.tradeText(t, d.today)
            DashRow {
                Column(Modifier.weight(1f).padding(end = 10.dp)) {
                    Row {
                        Name(x.title, Modifier.weight(1f, fill = false))
                        if (x.name.isNotEmpty()) {
                            Text(" · ", color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Name(x.name, Modifier.weight(1f, fill = false), MaterialTheme.typography.bodyMedium)
                        }
                    }
                    Meta(x.meta.joinToString(" · "))
                }
                if (x.amount != 0L) Amount(
                    MoneyFormat.amount(x.amount, Sign.PLUS), MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold),
                    if (x.tone == "in") MoneyPalette.good else Color.Unspecified
                )
            }
        }
    }
}

// ------------------------------------------------------------------ Meal card

@Composable
internal fun MealsView(d: Dash) {
    val m = d.parsed("meals") { MoneyMeals.parse(it) }
    val tiles = m?.let { MoneyDashText.mealTiles(it, d.today) }
    when {
        m == null -> {
            SkelTiles(d.twoCols)
            DashSections(d.twoCols, listOf(0 to { DashSection("Cycles") { SkelRows(6) } }, 1 to { DashSection("Places") { SkelRows(5) } }))
        }
        tiles == null -> Empty("No meal card yet. Add your meal card under Accounts (a provider addon brings it): its budget, what is left and every meal show up here after the first sync.")
        else -> MealsLoaded(d, m, tiles)
    }
}

@Composable
private fun MealsLoaded(d: Dash, m: MoneyMeals, t: MoneyDashText.MealTiles) {
    Tiles(listOf(
        TileData("Left this cycle", MoneyFormat.whole(t.left.value ?: 0), sub = t.left.sub),
        TileData("Pace", t.pace.text, tone = if (t.pace.tone == "neg") MoneyPalette.bad else null, sub = t.pace.sub),
        TileData("On your card", MoneyFormat.whole(t.card.value ?: 0), sub = t.card.sub),
        TileData("Meals", (t.count.value ?: 0).toString(), sub = t.count.sub)
    ), d.twoCols)
    DashSections(d.twoCols, listOf(
        0 to { Cycles(d, m) },
        1 to { Places(m) },
        0 to { LatestMeals(d, m) }
    ))
    Note("The budget is your employer's money and never counts as your spending; only the part beyond it, on your own card, does.", Modifier.padding(top = 22.dp))
}

@Composable
private fun Cycles(d: Dash, m: MoneyMeals) {
    val bars = MoneyDashText.mealCycleBars(m.cycles, d.today)
    DashSection("Cycles", trailing = { Hint("newest first") }) {
        if (bars.isEmpty()) Empty("No meals yet.") else {
            ChartLegend(listOf(
                LegendItem(MoneyPalette.line, "From the budget"),
                LegendItem(MoneyPalette.cardPart, "On your card")
            ))
            bars.reversed().forEach { b ->
                Column(Modifier.fillMaxWidth().padding(vertical = 10.dp)) {
                    Row(verticalAlignment = Alignment.Bottom) {
                        Text(b.label, style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium), modifier = Modifier.weight(1f))
                        Amount(MoneyFormat.whole(b.budget), MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold))
                        if (b.card != 0L) Amount("  + ${MoneyFormat.amount(b.card, Sign.NONE)}", MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.SemiBold), MoneyPalette.cardPart)
                    }
                    Row(
                        Modifier
                            .padding(vertical = 6.dp)
                            .fillMaxWidth()
                            .height(8.dp)
                            .clip(RoundedCornerShape(4.dp))
                            .background(MaterialTheme.colorScheme.outlineVariant)
                            .semantics {
                                contentDescription = "${b.label}: ${MoneyFormat.whole(b.budget)} from the budget" +
                                    if (b.card != 0L) ", ${MoneyFormat.amount(b.card)} on your card" else ""
                            },
                        horizontalArrangement = Arrangement.spacedBy(2.dp)
                    ) {
                        if (b.budgetPct > 0) Box(Modifier.fillMaxHeight().fillMaxWidth((b.budgetPct / 100).toFloat().coerceIn(0f, 1f)).background(MoneyPalette.line))
                        if (b.card != 0L) Box(Modifier.fillMaxHeight().fillMaxWidth((maxOf(1.0, b.cardPct) / maxOf(1.0, 100 - b.budgetPct)).toFloat().coerceIn(0f, 1f)).background(MoneyPalette.cardPart))
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Meta("${b.count} ${if (b.count == 1) "meal" else "meals"}" + if (b.billed.isNotEmpty()) " · " else "")
                        if (b.billed.isNotEmpty()) {
                            val txn = b.txn
                            if (txn != null) Text(
                                b.billed, style = MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.Medium),
                                color = MaterialTheme.colorScheme.primary,
                                modifier = Modifier.clickable(role = Role.Button) { d.money.openTransaction(txn, "Meal card line") }.padding(vertical = 6.dp)
                            ) else Meta(b.billed)
                        }
                    }
                }
                Rule()
            }
        }
    }
}

@Composable
private fun Places(m: MoneyMeals) {
    DashSection("Places", trailing = { Hint("most visits first") }) {
        if (m.places.isEmpty()) Empty("No meals yet.") else {
            if (m.kinds.size > 1) Meta(m.kinds.joinToString(" · ") { "${it.label}: ${it.count} · ${MoneyFormat.whole(it.total)}" }, Modifier.padding(vertical = 8.dp))
            m.places.forEach { p ->
                DashRow {
                    Column(Modifier.weight(1f).padding(end = 10.dp)) {
                        Name(p.place)
                        Meta("${p.count} ${if (p.count == 1) "visit" else "visits"}")
                    }
                    Amount(MoneyFormat.whole(p.total), MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold))
                }
            }
        }
    }
}

@Composable
private fun LatestMeals(d: Dash, m: MoneyMeals) {
    DashSection("Latest meals") {
        if (m.meals.isEmpty()) Empty("No meals yet.")
        m.meals.forEach { x ->
            val time = MoneyDashText.timeOf(x.occurredAt)
            val meta = listOfNotNull(
                MoneyDashText.shortDate(x.date, d.today) + if (time.isNotEmpty()) ", $time" else "",
                if (x.orderType == "delivery") "Delivery" else null,
                if (x.cardPaid != 0L) "${MoneyFormat.amount(x.cardPaid)} on your card" else null,
                if (!x.active) "Cancelled" else null
            ).joinToString(" · ")
            val muted = MaterialTheme.colorScheme.onSurfaceVariant
            DashRow {
                Column(Modifier.weight(1f).padding(end = 10.dp)) {
                    Name(x.place ?: "—", strike = !x.active, color = if (x.active) Color.Unspecified else muted)
                    Meta(meta)
                }
                Amount(MoneyFormat.amount(x.total, Sign.NONE), MaterialTheme.typography.bodyMedium.copy(
                    fontWeight = FontWeight.SemiBold,
                    textDecoration = if (x.active) null else androidx.compose.ui.text.style.TextDecoration.LineThrough
                ), if (x.active) Color.Unspecified else muted)
            }
        }
    }
}
