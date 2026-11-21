package io.github.ronganam.dana.ui.shell.destinations

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.data.money.MoneyText
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.screens.money.*
import io.github.ronganam.dana.ui.shell.Destination
import io.github.ronganam.dana.ui.shell.LocalShellLayout
import io.github.ronganam.dana.ui.shell.ShellLayout
import io.github.ronganam.dana.ui.shell.edgeRule
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.MoneyController
import io.github.ronganam.dana.ui.viewmodels.MoneyDetail
import io.github.ronganam.dana.ui.viewmodels.MoneyView
import io.github.ronganam.dana.ui.viewmodels.Place
import io.github.ronganam.dana.ui.viewmodels.ScreenRef

/**
 * Money (More → Money; server docs/money.md → Money tab). It takes over More
 * and is [Destination.wide]: unfolded, the side pane holds the header, the
 * month switcher, "Synced …", the banners and the views as rows; the stage
 * (≈ 800dp in landscape) holds the view, with a transaction or saving item
 * as a pane beside it when there is room, else over it. On the cover one
 * column: the header, the banners, the views as a sideways strip and the
 * view; an open item is full screen and Back closes it.
 */
@Composable
fun rememberMoneyDestination(viewModel: MainViewModel): Destination {
    val money = viewModel.moneyController
    val shell = viewModel.shellController
    val detail by money.detail.collectAsState()
    val stacked = LocalShellLayout.current == ShellLayout.STACKED

    SideEffect { money.ask = { shell.openThread(it) } }
    // Follow the server while Money is on screen.
    LaunchedEffect(money) { money.live() }
    // An open item is what Dana sees with the next message.
    LaunchedEffect(detail) {
        shell.setPlaceRef(
            when (val d = detail) {
                is MoneyDetail.Transaction -> ScreenRef("transaction", d.id, d.title)
                is MoneyDetail.Saving -> ScreenRef("saving", d.key, d.title)
                null -> null
            }
        )
    }
    DisposableEffect(money) { onDispose { shell.setPlaceRef(null) } }

    return Destination(
        place = Place.MORE,
        picked = stacked && detail != null,
        onBack = { money.closeDetail() },
        list = { if (stacked) MoneyCover(money) else MoneySide(money) },
        stage = if (stacked) {
            { detail?.let { MoneyDetailPane(money, it, onClose = { money.closeDetail() }) } }
        } else {
            { MoneyStage(money) }
        },
        title = "Money",
        wide = true
    )
}

/** The cover screen: header, banners, the views strip, the view. */
@Composable
private fun MoneyCover(money: MoneyController) {
    val view by money.view.collectAsState()
    val sources by money.sources.collectAsState()
    Column(Modifier.fillMaxSize()) {
        Column(Modifier.padding(start = 18.dp, end = 18.dp, top = 4.dp)) {
            MoneyHeader(money, compact = true)
        }
        MoneyBanners(money, Modifier.padding(horizontal = 18.dp))
        ViewStrip(money.availableViews(sources), view) { money.select(it) }
        Rule()
        Box(Modifier.weight(1f).fillMaxWidth()) { ViewContent(money, view, wide = false) }
    }
}

/** Unfolded side pane: header, month, banners, the views as rows. */
@Composable
private fun MoneySide(money: MoneyController) {
    val view by money.view.collectAsState()
    val sources by money.sources.collectAsState()
    val accent = MaterialTheme.colorScheme.primary
    val canvas = MaterialTheme.colorScheme.background
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(vertical = 12.dp)) {
        Column(Modifier.padding(horizontal = 18.dp)) { MoneyHeader(money, compact = false) }
        MoneyBanners(money, Modifier.padding(horizontal = 18.dp, vertical = 6.dp))
        Rule(Modifier.padding(top = 8.dp))
        money.availableViews(sources).forEach { v ->
            val on = v == view
            Text(
                v.label,
                style = MaterialTheme.typography.headlineSmall.copy(fontSize = 20.sp),
                color = if (on) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier
                    .fillMaxWidth()
                    .then(
                        if (on) Modifier.background(canvas).drawBehind {
                            drawLine(accent, Offset(1.5.dp.toPx(), 0f), Offset(1.5.dp.toPx(), size.height), 3.dp.toPx())
                        } else Modifier
                    )
                    .clickable(role = Role.Tab) { money.select(v) }
                    .semantics { selected = on }
                    .padding(horizontal = 18.dp, vertical = 8.dp)
            )
        }
    }
}

/** Kicker, title, the month switcher (monthly views) and "Synced 2 h ago". */
@Composable
private fun MoneyHeader(money: MoneyController, compact: Boolean) {
    val view by money.view.collectAsState()
    val month by money.month.collectAsState()
    val sources by money.sources.collectAsState()
    val syncing = sources?.any { it.busy } == true
    val lastOk = sources?.mapNotNull { it.lastOkAt }?.maxOrNull()
    val synced = when {
        sources.isNullOrEmpty() -> null
        syncing -> "Syncing…"
        else -> "Synced " + MoneyText.ago(lastOk)
    }
    if (compact) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Money", style = MaterialTheme.typography.displaySmall, modifier = Modifier.weight(1f))
            if (view.monthly) MonthSwitcher(month, onShift = { money.shiftMonth(it) })
        }
        synced?.let { MonoLabel(it, Modifier.padding(bottom = 6.dp)) }
    } else {
        MonoLabel(synced ?: "Spending · accounts · savings")
        Text("Money", style = MaterialTheme.typography.displayMedium, modifier = Modifier.padding(top = 2.dp, bottom = 8.dp))
        if (view.monthly) MonthSwitcher(month, onShift = { money.shiftMonth(it) })
    }
}

/** "‹ November ›": the next month only up to the current one. */
@Composable
private fun MonthSwitcher(month: String, onShift: (Int) -> Unit) {
    val current = MoneyText.thisMonth()
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            "‹", style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.clickable { onShift(-1) }.padding(horizontal = 10.dp, vertical = 4.dp)
        )
        Text(
            MoneyText.monthLabel(month, short = true),
            style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.Medium)
        )
        val canNext = month < current
        Text(
            "›", style = MaterialTheme.typography.titleLarge,
            color = if (canNext) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.outlineVariant,
            modifier = Modifier.clickable(enabled = canNext) { onShift(1) }.padding(horizontal = 10.dp, vertical = 4.dp)
        )
    }
}

/** The views as a sideways-scrolling row of mono labels (the cover screen). */
@Composable
private fun ViewStrip(views: List<MoneyView>, current: MoneyView, onPick: (MoneyView) -> Unit) {
    val accent = MaterialTheme.colorScheme.primary
    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 10.dp)) {
        views.forEach { v ->
            val on = v == current
            Text(
                v.label.uppercase(),
                style = MaterialTheme.typography.labelMedium,
                color = if (on) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.75f),
                maxLines = 1,
                modifier = Modifier
                    .heightIn(min = 44.dp)
                    .clickable(role = Role.Tab) { onPick(v) }
                    .semantics { selected = on }
                    .drawBehind {
                        if (on) drawLine(accent, Offset(0f, size.height - 1.dp.toPx()), Offset(size.width, size.height - 1.dp.toPx()), 2.dp.toPx())
                    }
                    .padding(horizontal = 8.dp, vertical = 14.dp)
            )
        }
    }
}

/** Unfolded stage: the view, and the open item beside it (≥ 760dp) or over it. */
@Composable
private fun MoneyStage(money: MoneyController) {
    val view by money.view.collectAsState()
    val detail by money.detail.collectAsState()
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val width = maxWidth
        val side = width >= 760.dp
        val d = detail
        when {
            d == null -> ViewContent(money, view, wide = true)
            side -> Row(Modifier.fillMaxSize()) {
                Box(Modifier.weight(1f).fillMaxHeight()) { ViewContent(money, view, wide = width - 380.dp >= 560.dp) }
                Box(
                    Modifier.width(380.dp).fillMaxHeight()
                        .background(MaterialTheme.colorScheme.surfaceContainerLow).edgeRule(start = true)
                ) { MoneyDetailPane(money, d, onClose = { money.closeDetail() }) }
            }
            else -> MoneyDetailPane(money, d, onClose = { money.closeDetail() })
        }
    }
}

@Composable
private fun ViewContent(money: MoneyController, view: MoneyView, wide: Boolean) {
    val error by money.loadError.collectAsState()
    val e = error
    if (e != null && view != MoneyView.ACCOUNTS) {
        Column(Modifier.padding(horizontal = 18.dp, vertical = 24.dp)) {
            Text("Money couldn't load.", style = MaterialTheme.typography.headlineSmall)
            Text(e, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    } else when (view) {
        MoneyView.OVERVIEW -> MoneyOverview(money, wide)
        MoneyView.SPENDING -> MoneySpending(money, wide)
        MoneyView.CASHFLOW -> MoneyCashflow(money, wide)
        MoneyView.SAVE -> MoneySave(money, wide)
        MoneyView.INVESTMENTS -> MoneyInvestments(money, wide)
        MoneyView.MEALS -> MoneyMeals(money, wide)
        MoneyView.TRANSACTIONS -> MoneyTransactions(money, wide)
        MoneyView.ACCOUNTS -> MoneyAccounts(money, wide)
    }
}
