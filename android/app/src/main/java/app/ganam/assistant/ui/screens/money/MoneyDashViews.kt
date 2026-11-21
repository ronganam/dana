package app.ganam.assistant.ui.screens.money

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.ganam.assistant.data.money.MoneyDashText
import app.ganam.assistant.ui.components.InkChoice
import app.ganam.assistant.ui.components.MonoLabel
import app.ganam.assistant.ui.components.Rule
import app.ganam.assistant.ui.screens.common.isolatedParts
import app.ganam.assistant.ui.screens.money.charts.MoneyPalette
import app.ganam.assistant.ui.viewmodels.MoneyController
import app.ganam.assistant.ui.viewmodels.MoneyDashState
import app.ganam.assistant.ui.viewmodels.MoneyView
import org.json.JSONObject

// The dashboard views. Each fills the space it gets, scrolls itself, and lays
// out in columns from the width it is given (two from 600dp: the unfolded
// stage; one on the cover and the unfolded portrait stage).

@Composable
fun MoneyOverview(money: MoneyController, wide: Boolean, modifier: Modifier = Modifier) =
    DashView(money, MoneyView.OVERVIEW, wide, modifier) { OverviewView(it) }

@Composable
fun MoneySpending(money: MoneyController, wide: Boolean, modifier: Modifier = Modifier) =
    DashView(money, MoneyView.SPENDING, wide, modifier) { SpendingView(it) }

@Composable
fun MoneyCashflow(money: MoneyController, wide: Boolean, modifier: Modifier = Modifier) =
    DashView(money, MoneyView.CASHFLOW, wide, modifier) { CashflowView(it) }

@Composable
fun MoneyInvestments(money: MoneyController, wide: Boolean, modifier: Modifier = Modifier) =
    DashView(money, MoneyView.INVESTMENTS, wide, modifier) { InvestmentsView(it) }

@Composable
fun MoneyMeals(money: MoneyController, wide: Boolean, modifier: Modifier = Modifier) =
    DashView(money, MoneyView.MEALS, wide, modifier) { MealsView(it) }

/** What a view draws from: the controller, its payloads for the month and the width it has. */
class Dash(
    val money: MoneyController,
    val data: Map<String, MoneyDashState.Loaded>,
    val month: String,
    /** Two columns (≥ 600dp). */
    val twoCols: Boolean,
    /** Under 480dp: tighter chart paddings. */
    val narrow: Boolean
) {
    val dash: MoneyDashState get() = money.dash
    val today = MoneyDashText.today()
    val current: Boolean get() = month == app.ganam.assistant.data.money.MoneyText.thisMonth()

    fun got(name: String): JSONObject? = dash.got(data, name, month)

    /** A payload parsed, null while it loads (or if it can't be read). */
    fun <T> parsed(name: String, parse: (JSONObject) -> T): T? = got(name)?.let { runCatching { parse(it) }.getOrNull() }

    /** "Cal ••8763" for an account id, from the logins. */
    fun accountName(id: String?): String {
        id ?: return ""
        val sources = money.sources.value ?: return ""
        for (s in sources) for (a in s.accounts) if (a.id == id) return MoneyDashText.accountName(a.label, a.number, a.kind, s.companyName)
        return ""
    }
}

/** Loads the view's payloads (on view, month, revision and horizon) and gives it the width it has. */
@Composable
private fun DashView(money: MoneyController, view: MoneyView, wide: Boolean, modifier: Modifier, content: @Composable (Dash) -> Unit) {
    val data by money.dash.data.collectAsState()
    val month by money.month.collectAsState()
    val revision by money.revision.collectAsState()
    val horizon by money.dash.horizon.collectAsState()
    money.sources.collectAsState()
    LaunchedEffect(view, month, revision, horizon) { money.dash.load(view, month) }
    BoxWithConstraints(modifier.fillMaxSize()) {
        val d = Dash(money, data, month, twoCols = wide && maxWidth >= 600.dp, narrow = maxWidth < 480.dp)
        Column(
            Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 18.dp)
                .padding(top = 14.dp, bottom = 40.dp)
        ) { content(d) }
    }
}

// ------------------------------------------------------------------ layout

/**
 * Sections in one column (their order as given) or two ([twoCols]: each
 * section in its column, in order), like the web's `mny-cols` with `o1…o5`.
 */
@Composable
fun DashSections(twoCols: Boolean, sections: List<Pair<Int, @Composable () -> Unit>>) {
    if (twoCols) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(28.dp)) {
            for (col in 0..1) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(30.dp)) {
                    sections.filter { it.first == col }.forEach { it.second() }
                }
            }
        }
    } else {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(30.dp)) { sections.forEach { it.second() } }
    }
}

/** A section: a mono kicker with what sits at its right (a hint, a total, a switch), the content under it. */
@Composable
fun DashSection(
    title: String,
    modifier: Modifier = Modifier,
    trailing: (@Composable RowScope.() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit
) {
    Column(modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().heightIn(min = 30.dp).padding(bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            MonoLabel(title, Modifier.weight(1f).padding(end = 10.dp), color = MaterialTheme.colorScheme.onSurface)
            trailing?.invoke(this)
        }
        Rule()
        content()
    }
}

/** A muted mono hint at a section's right ("next 45 days"). */
@Composable
fun Hint(text: String) = MonoLabel(text)

/** A total at a section's right ("₪4,786 / month"). */
@Composable
fun HeadNum(text: String, per: String? = null) {
    Row(verticalAlignment = Alignment.Bottom) {
        Amount(text, MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.SemiBold))
        per?.let { Text(" $it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
    }
}

/** A choice of a few options (Everyday / Everything, 3 / 6 / 12 months). */
@Composable
fun <T> DashSwitch(options: List<Pair<T, String>>, selected: T, onPick: (T) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        options.forEach { (v, label) -> InkChoice(label, selected = v == selected, onClick = { onPick(v) }) }
    }
}

/** A line of muted text under a chart or section. */
@Composable
fun Note(text: String, modifier: Modifier = Modifier) {
    Text(
        text, modifier.padding(top = 8.dp),
        style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content),
        color = MaterialTheme.colorScheme.onSurfaceVariant
    )
}

/** "Nothing far from usual." — the empty line of a section. */
@Composable
fun Empty(text: String) {
    Text(
        text, Modifier.padding(vertical = 14.dp),
        style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
        color = MaterialTheme.colorScheme.onSurfaceVariant
    )
}

/** Money text, always left to right with tabular figures. */
@Composable
fun Amount(text: String, style: TextStyle = MaterialTheme.typography.bodyMedium, color: Color = Color.Unspecified, modifier: Modifier = Modifier) {
    Text(
        text, modifier,
        style = style.copy(textDirection = TextDirection.Ltr, fontFeatureSettings = "tnum", textAlign = androidx.compose.ui.text.style.TextAlign.End),
        color = if (color == Color.Unspecified) MaterialTheme.colorScheme.onSurface else color,
        maxLines = 1
    )
}

/** A name that may be Hebrew (a merchant, a place): laid out in its own direction. */
@Composable
fun Name(
    text: String,
    modifier: Modifier = Modifier,
    style: TextStyle = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium),
    color: Color = Color.Unspecified,
    maxLines: Int = 1,
    strike: Boolean = false
) {
    Text(
        text, modifier,
        style = style.copy(textDirection = TextDirection.Content, textDecoration = if (strike) TextDecoration.LineThrough else null),
        color = if (color == Color.Unspecified) MaterialTheme.colorScheme.onSurface else color,
        maxLines = maxLines,
        overflow = TextOverflow.Ellipsis
    )
}

/** A muted meta line of " · " parts, each kept in its order whatever its script. */
@Composable
fun Meta(text: String, modifier: Modifier = Modifier, color: Color = Color.Unspecified, maxLines: Int = 2) {
    Text(
        if (MoneyDashText.hasHebrew(text)) isolatedParts(text) else text, modifier,
        style = MaterialTheme.typography.bodySmall,
        color = if (color == Color.Unspecified) MaterialTheme.colorScheme.onSurfaceVariant else color,
        maxLines = maxLines,
        overflow = TextOverflow.Ellipsis
    )
}

/** A server sentence that may hold Hebrew runs (an insight): isolated runs in an English line. */
@Composable
fun Sentence(text: String, style: TextStyle, color: Color = Color.Unspecified, modifier: Modifier = Modifier) {
    Text(
        MoneyDashText.isolateHebrew(text), modifier,
        style = style.copy(textDirection = TextDirection.Ltr),
        color = if (color == Color.Unspecified) MaterialTheme.colorScheme.onSurface else color
    )
}

/** A hairline-ruled row (tappable when [onClick] is given). */
@Composable
fun DashRow(onClick: (() -> Unit)? = null, modifier: Modifier = Modifier, content: @Composable RowScope.() -> Unit) {
    Row(
        modifier
            .fillMaxWidth()
            .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier)
            .heightIn(min = 48.dp)
            .padding(vertical = 9.dp),
        verticalAlignment = Alignment.CenterVertically,
        content = content
    )
    Rule()
}

// ------------------------------------------------------------------ tiles

/** One tile: mono label, the number, a strong line, a muted line. */
data class TileData(
    val label: String,
    val value: String,
    val per: String? = null,
    val tone: Color? = null,
    val strong: String? = null,
    val strongTone: Color? = null,
    val sub: String? = null,
    val onClick: (() -> Unit)? = null
)

/** Four tiles: in a row of four from 600dp, else two by two; hairlines between, no boxes. */
@Composable
fun Tiles(tiles: List<TileData>, fourAcross: Boolean) {
    val perRow = if (fourAcross) 4 else 2
    Column(Modifier.fillMaxWidth().padding(bottom = 26.dp)) {
        Rule()
        tiles.chunked(perRow).forEach { row ->
            Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
                row.forEachIndexed { i, t ->
                    if (i > 0) Box(Modifier.width(1.dp).fillMaxHeight().background(MaterialTheme.colorScheme.outlineVariant))
                    Tile(t, Modifier.weight(1f).fillMaxHeight())
                }
                repeat(perRow - row.size) { Spacer(Modifier.weight(1f)) }
            }
            Rule()
        }
    }
}

@Composable
private fun Tile(t: TileData, modifier: Modifier) {
    Column(
        modifier
            .then(if (t.onClick != null) Modifier.clickable(role = Role.Button, onClick = t.onClick) else Modifier)
            .padding(horizontal = 12.dp, vertical = 12.dp)
    ) {
        MonoLabel(t.label)
        // The amount and "/ month" are whole pieces: a narrow tile moves "/ month" under the amount.
        AmountPer(Modifier.padding(top = 4.dp, bottom = 2.dp)) {
            Amount(t.value, MaterialTheme.typography.titleLarge.copy(fontSize = 23.sp, fontWeight = FontWeight.SemiBold, letterSpacing = (-0.4).sp), t.tone ?: Color.Unspecified)
            t.per?.let {
                Text(
                    " $it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, softWrap = false, modifier = Modifier.padding(bottom = 3.dp)
                )
            }
        }
        t.strong?.takeIf { it.isNotBlank() }?.let {
            Text(it, style = MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.Medium), color = t.strongTone ?: MaterialTheme.colorScheme.onSurface)
        }
        t.sub?.takeIf { it.isNotBlank() }?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/**
 * An amount and its unit ("/ month"): side by side, bottoms aligned, when they fit; else the unit
 * goes under the amount. A plain Layout, so tile rows that ask for intrinsic heights get the real one.
 */
@Composable
private fun AmountPer(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    androidx.compose.ui.layout.Layout(content, modifier) { measurables, c ->
        val loose = c.copy(minWidth = 0, minHeight = 0)
        val amount = measurables[0].measure(loose)
        val per = measurables.getOrNull(1)?.measure(loose)
        val beside = per == null || amount.width + per.width <= c.maxWidth
        val w = if (per == null) amount.width else if (beside) amount.width + per.width else maxOf(amount.width, per.width)
        val h = if (per == null) amount.height else if (beside) maxOf(amount.height, per.height) else amount.height + per.height
        layout(w.coerceIn(c.minWidth, c.maxWidth), h.coerceIn(c.minHeight, c.maxHeight)) {
            amount.place(0, if (beside) h - amount.height else 0)
            per?.place(if (beside) amount.width else 0, if (beside) h - per.height else amount.height)
        }
    }
}

// ------------------------------------------------------------------ skeletons

@Composable
private fun pulse(): Float {
    val t = rememberInfiniteTransition(label = "skel")
    return t.animateFloat(0.45f, 0.9f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "skel").value
}

/** A grey block standing in for what loads. */
@Composable
fun Skel(height: Dp, modifier: Modifier = Modifier, width: Float = 1f) {
    val a = pulse()
    Box(
        modifier
            .fillMaxWidth(width)
            .height(height)
            .alpha(a)
            .background(MaterialTheme.colorScheme.surfaceContainerHigh, RoundedCornerShape(6.dp))
    )
}

@Composable
fun SkelRows(n: Int) {
    Column(Modifier.fillMaxWidth().padding(top = 10.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        repeat(n) { i -> Skel(16.dp, width = if (i % 2 == 0) 0.9f else 0.7f) }
    }
}

@Composable
fun SkelChart(height: Dp = 180.dp) = Skel(height, Modifier.padding(top = 12.dp))

@Composable
fun SkelTiles(fourAcross: Boolean) {
    Column(Modifier.padding(bottom = 26.dp)) {
        (if (fourAcross) listOf(4) else listOf(2, 2)).forEach { n ->
            Row(Modifier.fillMaxWidth().padding(bottom = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                repeat(n) { Skel(92.dp, Modifier.weight(1f)) }
            }
        }
    }
}

// ------------------------------------------------------------------ colours

@Composable
fun goodColor(): Color = if (MaterialTheme.colorScheme.background.luminanceIsDark()) MoneyPalette.good.copy(alpha = 1f) else androidx.compose.ui.graphics.lerp(MoneyPalette.good, Color.Black, 0.25f)

@Composable
fun badColor(): Color = MoneyPalette.bad

private fun Color.luminanceIsDark() = (0.299f * red + 0.587f * green + 0.114f * blue) < 0.5f

/** A small open / closed chevron drawn in the muted ink. */
@Composable
fun Chevron(open: Boolean) {
    val c = MaterialTheme.colorScheme.onSurfaceVariant
    androidx.compose.foundation.Canvas(Modifier.size(12.dp, 8.dp)) {
        val w = size.width; val h = size.height; val sw = 1.6.dp.toPx()
        val (y0, y1) = if (open) h to 0f else 0f to h
        drawLine(c, androidx.compose.ui.geometry.Offset(0f, y0), androidx.compose.ui.geometry.Offset(w / 2, y1), sw, androidx.compose.ui.graphics.StrokeCap.Round)
        drawLine(c, androidx.compose.ui.geometry.Offset(w / 2, y1), androidx.compose.ui.geometry.Offset(w, y0), sw, androidx.compose.ui.graphics.StrokeCap.Round)
    }
}
