package app.ganam.assistant.ui.screens.money

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import app.ganam.assistant.data.money.MoneyDashGeometry
import app.ganam.assistant.data.money.MoneyDashText
import app.ganam.assistant.data.money.MoneyDashText.Sign
import app.ganam.assistant.data.money.MoneyMerchants
import app.ganam.assistant.data.money.MoneyMonths
import app.ganam.assistant.data.money.MoneyTrends
import app.ganam.assistant.data.money.OneOff
import app.ganam.assistant.data.money.TrendLine
import app.ganam.assistant.ui.components.InkButton
import app.ganam.assistant.ui.components.InkEmphasis
import app.ganam.assistant.ui.components.Rule
import app.ganam.assistant.ui.screens.money.charts.ChartLegend
import app.ganam.assistant.ui.screens.money.charts.LegendItem
import app.ganam.assistant.ui.screens.money.charts.MoneyPalette
import app.ganam.assistant.ui.screens.money.charts.MonthsChart
import app.ganam.assistant.ui.screens.money.charts.SparkBars
import app.ganam.assistant.ui.viewmodels.TxnFilter
import org.json.JSONObject

// Spending: where it went (groups → categories), movers, 12 months stacked by
// group, top merchants and the month's big purchases. A tap on a group or a
// category focuses the view on it, the same filter Transactions uses.

/** The category taxonomy of `GET categories` (labels, which group a category is in). */
internal class Taxonomy(json: JSONObject?) {
    private val labels = mutableMapOf<String, String>()
    private val groupOf = mutableMapOf<String, String>()

    init {
        val groups = json?.optJSONObject("taxonomy")?.optJSONArray("groups")
        for (i in 0 until (groups?.length() ?: 0)) {
            val g = groups!!.optJSONObject(i) ?: continue
            val gk = g.optString("key")
            labels[gk] = g.optString("label")
            groupOf[gk] = gk
            val cats = g.optJSONArray("categories")
            for (j in 0 until (cats?.length() ?: 0)) {
                val c = cats!!.optJSONObject(j) ?: continue
                labels.putIfAbsent(c.optString("key"), c.optString("label"))
                groupOf.putIfAbsent(c.optString("key"), gk)
            }
        }
    }

    fun label(key: String?): String = when {
        key.isNullOrEmpty() -> ""
        key == "uncategorized" -> "Uncategorized"
        else -> labels[key] ?: key
    }

    fun group(key: String?): String? = key?.let { groupOf[it] }
}

@Composable
internal fun SpendingView(d: Dash) {
    val money = d.money
    val filter by money.filter.collectAsState()
    val catJson by money.categories.collectAsState()
    val tax = Taxonomy(catJson)
    val t = d.parsed("trends") { MoneyTrends.parse(it) }
    val focus = filter.category
    val focusGroup = when {
        focus == null -> null
        focus == "uncategorized" -> "uncategorized"
        else -> tax.group(focus) ?: focus
    }
    val setFocus: (String?) -> Unit = { k -> money.setFilter(filter.copy(category = k, allTime = false)) }
    if (focus != null) FocusChip(tax.label(focus), onSee = { money.openTransactions(TxnFilter(category = focus)) }, onClear = { setFocus(null) })
    DashSections(d.twoCols, listOf(
        0 to { WhereItWent(d, t, focus, focusGroup, setFocus) },
        1 to { Movers(t, setFocus) },
        0 to { TwelveMonths(d, t, focusGroup) },
        1 to { TopMerchants(d, tax, focus, focusGroup) },
        1 to { BigPurchases(d, t) }
    ))
}

@Composable
private fun FocusChip(label: String, onSee: () -> Unit, onClear: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(bottom = 18.dp)
            .background(MaterialTheme.colorScheme.surfaceContainerLow, RoundedCornerShape(10.dp))
            .padding(start = 12.dp, end = 4.dp, top = 4.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text("Showing ", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Name(label, Modifier.weight(1f, fill = false), MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold))
        Spacer(Modifier.weight(1f))
        InkButton("See its transactions", emphasis = InkEmphasis.BARE, onClick = onSee)
        Text(
            "×", style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.clickable(role = Role.Button, onClickLabel = "Show every category", onClick = onClear).padding(horizontal = 12.dp, vertical = 6.dp)
        )
    }
}

private fun TrendLine.live() = total > 0 || (onTrack ?: 0) > 0 || typical > 0

@Composable
private fun WhereItWent(d: Dash, t: MoneyTrends?, focus: String?, focusGroup: String?, setFocus: (String?) -> Unit) {
    val expanded by d.dash.expanded.collectAsState()
    val groups = t?.groups?.filter { it.live() }.orEmpty()
    val total = groups.sumOf { it.total }
    DashSection("Where it went", trailing = if (t != null) ({ HeadNum(MoneyDashText.money(total, Sign.NONE)) }) else null) {
        if (t == null) SkelRows(6) else {
            val oneOffs = t.oneOffs.sumOf { kotlin.math.abs(it.amount) }
            Meta(
                (if (t.inProgress) "${t.elapsed} of ${t.days} days" else t.label) + " · one-offs left out" +
                    (if (oneOffs > 0) " (${MoneyDashText.whole(oneOffs, Sign.NONE)}, below)" else ""),
                Modifier.padding(vertical = 8.dp)
            )
            Rule()
            if (groups.isEmpty()) Empty("Nothing spent in ${t.label}.")
            groups.forEach { g ->
                val color = MoneyPalette.series(MoneyDashGeometry.seriesOf(if (g.key == "uncategorized") null else g.key))
                val cats = g.categories.filter { it.live() }
                val multi = cats.size > 1 || (cats.firstOrNull()?.let { it.key != g.key } == true)
                val open = g.key in expanded || (focusGroup == g.key && focus != g.key && multi)
                val dim = focusGroup != null && focusGroup != g.key
                Column(Modifier.alpha(if (dim) 0.45f else 1f)) {
                    TrendRow(g, t, color, sub = false, on = focus == g.key, onTap = { setFocus(if (focus == g.key) null else g.key) },
                        expand = if (multi) ({ d.dash.toggleGroup(g.key) }) else null, open = open)
                    if (open) cats.forEach { c ->
                        TrendRow(c, t, color, sub = true, on = focus == c.key, onTap = { setFocus(if (focus == c.key) null else c.key) })
                    }
                }
            }
        }
    }
}

@Composable
private fun TrendRow(x: TrendLine, t: MoneyTrends, color: Color, sub: Boolean, on: Boolean, onTap: () -> Unit, expand: (() -> Unit)? = null, open: Boolean = false) {
    val vs = MoneyDashText.vsTypical(x, t.inProgress)
    val parts = buildList {
        if (t.inProgress && x.onTrack != null && x.onTrack != x.total) add("on track ${MoneyDashText.whole(x.onTrack)}")
        if (x.typical > 0) add("typical ${MoneyDashText.whole(x.typical)}")
    }
    val accent = MaterialTheme.colorScheme.primary
    Row(
        Modifier
            .fillMaxWidth()
            .then(if (on) Modifier.background(MaterialTheme.colorScheme.surfaceContainerLow) else Modifier)
            .semantics { selected = on }
            .clickable(role = Role.Button, onClickLabel = if (on) "Show every category" else "Focus on ${x.label}", onClick = onTap)
            .heightIn(min = 52.dp)
            .padding(start = if (sub) 22.dp else 0.dp, top = 8.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        if (!sub) Box(Modifier.size(10.dp).background(color, RoundedCornerShape(3.dp)))
        Column(Modifier.weight(1f).padding(start = if (sub) 0.dp else 10.dp, end = 8.dp)) {
            Text(
                x.label, maxLines = 1,
                style = if (sub) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium),
                color = if (on) accent else MaterialTheme.colorScheme.onSurface
            )
            if (parts.isNotEmpty()) Meta(parts.joinToString(" · "))
        }
        SparkBars(x.months, color, partial = t.inProgress, modifier = Modifier.padding(end = 10.dp))
        Column(horizontalAlignment = Alignment.End, modifier = Modifier.widthIn(min = 76.dp)) {
            Amount(MoneyDashText.money(x.total, Sign.NONE), MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold))
            if (vs.text.isNotEmpty()) Text(vs.text, style = MaterialTheme.typography.labelSmall, color = deltaColor(vs.dir), maxLines = 1)
        }
        if (expand != null) {
            Box(
                Modifier.width(36.dp).heightIn(min = 44.dp)
                    .clickable(role = Role.Button, onClickLabel = if (open) "Hide ${x.label}'s categories" else "Show ${x.label}'s categories", onClick = expand),
                contentAlignment = Alignment.Center
            ) { Chevron(open) }
        } else Spacer(Modifier.width(36.dp))
    }
    Rule()
}

@Composable
private fun deltaColor(dir: String): Color = when (dir) {
    "up", "new" -> MoneyPalette.bad
    "down" -> MoneyPalette.good
    else -> MaterialTheme.colorScheme.onSurfaceVariant
}

@Composable
private fun Movers(t: MoneyTrends?, setFocus: (String?) -> Unit) {
    DashSection("Movers", trailing = { Hint("against a typical month") }) {
        when {
            t == null -> SkelRows(3)
            t.movers.isEmpty() -> Empty("Nothing far from usual${if (t.inProgress) " so far" else ""}.")
            else -> t.movers.forEach { m ->
                DashRow(onClick = { setFocus(m.key) }) {
                    Text(
                        when (m.direction) { "down" -> "↘"; "new" -> "✦"; else -> "↗" },
                        color = deltaColor(m.direction), style = MaterialTheme.typography.titleMedium, modifier = Modifier.width(26.dp)
                    )
                    Column(Modifier.weight(1f).padding(end = 8.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(Modifier.size(8.dp).background(MoneyPalette.series(MoneyDashGeometry.seriesOf(m.group)), CircleShape))
                            Text(m.label, Modifier.padding(start = 6.dp), style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium), maxLines = 1)
                        }
                        Meta("${if (t.inProgress) "on track for " else ""}${MoneyDashText.whole(m.value)} · typical ${MoneyDashText.whole(m.typical)}")
                    }
                    Amount(MoneyDashText.moverDelta(m), MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold), deltaColor(m.direction))
                }
            }
        }
    }
}

@Composable
private fun TwelveMonths(d: Dash, t: MoneyTrends?, focusGroup: String?) {
    val everything by d.dash.everything.collectAsState()
    val data: MoneyMonths? = if (everything) d.parsed("months") { MoneyMonths.parse(it) } else t?.let { MoneyDashGeometry.trendsAsMonths(it) }
    DashSection("12 months", trailing = {
        DashSwitch(listOf(false to "Everyday", true to "Everything"), everything) { d.dash.setEverything(it) }
    }) {
        when {
            data == null -> SkelChart(160.dp)
            data.months.none { it.total > 0 } -> Empty("No spending in these 12 months yet.")
            else -> {
                val fs = focusGroup?.let { MoneyDashGeometry.seriesOf(if (it == "uncategorized") null else it) }
                Box(Modifier.padding(top = 12.dp)) {
                    MonthsChart(data, d.month, fs, if (d.narrow) 170.dp else 200.dp, d.narrow) { d.money.setMonth(it) }
                }
                val present = MoneyDashGeometry.months(data, MoneyDashGeometry.Box(600.0, 200.0, 40.0, 6.0, 8.0, 22.0)).legend
                ChartLegend(
                    present.map { l -> LegendItem(MoneyPalette.series(l.key), l.label) },
                    dimmed = if (fs == null) emptySet() else present.indices.filter { present[it].key != fs }.toSet()
                )
                Note("${if (everything) "Every charge, one-offs too" else "Without one-offs"} · drag to read a month, tap to open it")
            }
        }
    }
}

@Composable
private fun TopMerchants(d: Dash, tax: Taxonomy, focus: String?, focusGroup: String?) {
    val list = d.parsed("merchants") { MoneyMerchants.parse(it) }
    DashSection("Top merchants", trailing = { Hint(MoneyDashText.monthLabel(d.month, short = true)) }) {
        if (list == null) SkelRows(5) else {
            val shown = list.filter { x ->
                focus == null || x.category == focus || (x.category == null && focus == "uncategorized") ||
                    (focusGroup != null && focusGroup != focus && tax.group(x.category) == focusGroup)
            }
            if (shown.isEmpty()) Empty("${if (focus != null) "No ${tax.label(focus)} merchants" else "No merchants"} in ${MoneyDashText.monthLabel(d.month)}.")
            shown.forEach { x ->
                val color = MoneyPalette.series(MoneyDashGeometry.seriesOf(tax.group(x.category)))
                val sub = listOfNotNull(
                    x.categoryLabel ?: "Uncategorized", "${x.count}×",
                    if (x.count > 1) "avg ${MoneyDashText.whole(x.average, Sign.NONE)}" else null,
                    if (x.oneOff) "one-off" else null
                ).joinToString(" · ")
                DashRow(onClick = { d.money.openTransactions(TxnFilter(q = x.merchant, allTime = true)) }) {
                    Column(Modifier.weight(1f).padding(end = 8.dp)) {
                        Name(x.merchant)
                        Meta(sub, maxLines = 1)
                    }
                    SparkBars(x.months, color, modifier = Modifier.padding(end = 10.dp))
                    Amount(MoneyDashText.money(x.total, Sign.NONE), MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold), modifier = Modifier.widthIn(min = 72.dp))
                }
            }
        }
    }
}

@Composable
private fun BigPurchases(d: Dash, t: MoneyTrends?) {
    val list = t?.oneOffs.orEmpty()
    DashSection("Big purchases", trailing = if (list.isNotEmpty()) ({ HeadNum(MoneyDashText.money(list.sumOf { kotlin.math.abs(it.amount) }, Sign.NONE)) }) else null) {
        when {
            t == null -> SkelRows(2)
            list.isEmpty() -> Empty("No one-offs in ${t.label}.")
            else -> {
                Meta("One-offs: left out of everyday spending and the forecast", Modifier.padding(vertical = 8.dp))
                Rule()
                list.forEach { BigRow(d, it) }
            }
        }
    }
}

@Composable
private fun BigRow(d: Dash, x: OneOff) {
    val name = x.merchant ?: x.description ?: "—"
    DashRow(onClick = { d.money.openTransaction(x.id, name) }) {
        Column(Modifier.weight(1f).padding(end = 8.dp)) {
            Name(name)
            Meta(MoneyDashText.shortDate(x.purchaseDate, d.today) + (x.categoryLabel?.let { " · $it" } ?: ""))
        }
        Amount(MoneyDashText.money(x.amount, Sign.NONE), MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold), modifier = Modifier.padding(end = 10.dp))
        InkButton("Not a one-off") {
            val body = JSONObject().put("one_off", false)
            d.money.write("Couldn't change it", undoText = "⁨$name⁩: not a one-off") { d.money.api.patch("/transactions/${x.id}", body) }
        }
    }
}
