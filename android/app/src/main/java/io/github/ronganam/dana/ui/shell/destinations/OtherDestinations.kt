package io.github.ronganam.dana.ui.shell.destinations

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.screens.CallsScreen
import io.github.ronganam.dana.ui.screens.MemoryScreen
import io.github.ronganam.dana.ui.screens.SettingsScreen
import io.github.ronganam.dana.ui.screens.about.AboutScreen
import io.github.ronganam.dana.ui.screens.people.PeopleScreen
import io.github.ronganam.dana.ui.screens.review.ReviewScreen
import io.github.ronganam.dana.ui.shell.Destination
import io.github.ronganam.dana.ui.shell.LocalShellLayout
import io.github.ronganam.dana.ui.shell.PaneWindow
import io.github.ronganam.dana.ui.shell.ShellLayout
import io.github.ronganam.dana.ui.theme.DanaSerif
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.MoreItem
import io.github.ronganam.dana.ui.viewmodels.Place

/**
 * More: Shopping and Money (each only with its integration), Calls, People and
 * About you. Shopping and Money take over the place with their own list and
 * stage, under a "← More" link; the others open their screen in the stage
 * (full screen with "← More" on the cover). Settings opens from About you, the
 * weekly review from its notification; the old Memory screen only from a
 * "memory" route (previous-layout links), backing out to About you.
 */
@Composable
fun rememberMoreDestination(viewModel: MainViewModel): Destination {
    val shell = viewModel.shellController
    val item by shell.more.collectAsState()
    val addons by viewModel.addons.collectAsState()
    val toMore = { shell.selectMore(null) }
    // Its integration went away (disconnected): back to More's list.
    LaunchedEffect(item, addons) {
        if ((item == MoreItem.SHOPPING && "shopping" !in addons) || (item == MoreItem.MONEY && "money" !in addons)) toMore()
    }
    return when (item) {
        MoreItem.SHOPPING -> takeOver(rememberShoppingDestination(viewModel), toMore)
        MoreItem.MONEY -> takeOver(rememberMoneyDestination(viewModel), toMore)
        else -> Destination(
            place = Place.MORE,
            picked = item != null,
            onBack = { shell.clearPick(Place.MORE) },
            list = { MoreList(viewModel, item) },
            stage = { MoreStage(viewModel, item) }
        )
    }
}

/**
 * A More item with its own list + stage, shown in place of More: its list gets
 * a "← More" link on top, and Back from its list returns to More.
 */
private fun takeOver(inner: Destination, toMore: () -> Unit) = Destination(
    place = Place.MORE,
    picked = inner.picked,
    onBack = { if (inner.backable) inner.onBack() else toMore() },
    list = {
        Column(Modifier.fillMaxSize()) {
            BackLink("← More", toMore)
            Box(Modifier.weight(1f).fillMaxWidth()) { inner.list() }
        }
    },
    stage = inner.stage,
    title = inner.title,
    kicker = inner.kicker,
    backable = true,
    wide = inner.wide
)

@Composable
private fun MoreList(viewModel: MainViewModel, selected: MoreItem?) {
    val shell = viewModel.shellController
    val calls by viewModel.callsController.calls.collectAsState()
    val people by viewModel.peopleController.people.collectAsState()
    val addons by viewModel.addons.collectAsState()
    val shoppingScreen by viewModel.shoppingController.screen.collectAsState()
    val toBuy = shoppingScreen?.takeIf { it.isActive }?.counts?.toBuy ?: 0
    val wide = LocalShellLayout.current != ShellLayout.STACKED
    LaunchedEffect(Unit) { viewModel.peopleController.fetch() }
    val rows = listOfNotNull(
        Triple(MoreItem.SHOPPING, if (toBuy > 0) "$toBuy to buy" else "the list", setOf(MoreItem.SHOPPING)).takeIf { "shopping" in addons },
        Triple(MoreItem.MONEY, "spending · accounts · savings", setOf(MoreItem.MONEY)).takeIf { "money" in addons },
        Triple(MoreItem.CALLS, calls.size.takeIf { it > 0 }?.toString().orEmpty(), setOf(MoreItem.CALLS)),
        Triple(MoreItem.PEOPLE, people.size.takeIf { it > 0 }?.toString().orEmpty(), setOf(MoreItem.PEOPLE)),
        Triple(MoreItem.ABOUT, "what Dana knows · settings", setOf(MoreItem.ABOUT, MoreItem.SETTINGS, MoreItem.MEMORY))
    )
    val accent = MaterialTheme.colorScheme.primary
    val canvas = MaterialTheme.colorScheme.background
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(vertical = 20.dp)) {
        Column(Modifier.padding(horizontal = 18.dp)) {
            MonoLabel("Everything else Dana keeps")
            Text("More", style = MaterialTheme.typography.displayMedium, modifier = Modifier.padding(top = 2.dp, bottom = 12.dp))
            Rule()
        }
        rows.forEach { (item, meta, matches) ->
            val on = wide && selected in matches
            Column(
                Modifier
                    .fillMaxWidth()
                    .then(
                        if (on) Modifier.background(canvas).drawBehind {
                            drawLine(accent, Offset(1.5.dp.toPx(), 0f), Offset(1.5.dp.toPx(), size.height), 3.dp.toPx())
                        } else Modifier
                    )
                    .clickable { shell.selectMore(item) }
            ) {
                // A count sits at the end of the row; a description goes under the name.
                val isCount = meta.all { it.isDigit() }
                Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(item.label, style = MaterialTheme.typography.headlineSmall.copy(fontSize = 25.sp), maxLines = 1)
                        if (!isCount) MonoLabel(meta, modifier = Modifier.padding(top = 2.dp))
                    }
                    if (isCount && meta.isNotEmpty()) MonoLabel(meta)
                }
                Rule(Modifier.padding(horizontal = 18.dp))
            }
        }
    }
}

@Composable
private fun MoreStage(viewModel: MainViewModel, item: MoreItem?) {
    val shell = viewModel.shellController
    val stacked = LocalShellLayout.current == ShellLayout.STACKED
    if (item == null) {
        Column(Modifier.padding(horizontal = 26.dp, vertical = 28.dp)) {
            Text("Pick one on the left.", style = MaterialTheme.typography.headlineSmall.copy(fontFamily = DanaSerif, fontStyle = FontStyle.Italic))
            Text("Calls, people and settings open here.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    } else MoreItemStage(viewModel, item, stacked)
}

@Composable
private fun MoreItemStage(viewModel: MainViewModel, item: MoreItem, stacked: Boolean) {
    val shell = viewModel.shellController
    val back = { shell.clearPick(Place.MORE) }
    Column(Modifier.fillMaxSize()) {
        // Screens with their own back arrow (People, About you, Settings, Review) get it; the others get "← More" on the cover.
        val ownBack = item == MoreItem.PEOPLE || item == MoreItem.ABOUT || item == MoreItem.SETTINGS || item == MoreItem.REVIEW
        if (item == MoreItem.MEMORY) BackLink("← About you", back)
        else if (stacked && !ownBack) BackLink("← More", back)
        PaneWindow(Modifier.weight(1f)) {
            when (item) {
                MoreItem.CALLS -> CallsScreen(viewModel)
                MoreItem.PEOPLE -> PeopleScreen(viewModel, onBack = if (stacked) back else null)
                // They take over the place (rememberMoreDestination); never shown in this stage.
                MoreItem.SHOPPING, MoreItem.MONEY -> Unit
                MoreItem.MEMORY -> MemoryScreen(viewModel)
                MoreItem.ABOUT -> AboutScreen(viewModel, onBack = if (stacked) back else null, onOpenSettings = { shell.selectMore(MoreItem.SETTINGS) })
                // Reached from About you, so Back always returns there.
                MoreItem.SETTINGS -> SettingsScreen(viewModel, onBack = back)
                MoreItem.REVIEW -> ReviewScreen(viewModel, onBack = back)
            }
        }
    }
}

@Composable
private fun BackLink(text: String, onClick: () -> Unit) {
    Text(
        text,
        style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.clickable(onClick = onClick).padding(horizontal = 18.dp, vertical = 12.dp)
    )
}
