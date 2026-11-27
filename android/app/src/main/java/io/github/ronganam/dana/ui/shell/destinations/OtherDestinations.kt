package io.github.ronganam.dana.ui.shell.destinations

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.components.circuit.GroupRow
import io.github.ronganam.dana.ui.components.circuit.GroupedSection
import io.github.ronganam.dana.ui.components.circuit.PlaceHeader
import io.github.ronganam.dana.ui.shell.HeaderAsk
import io.github.ronganam.dana.ui.shell.SearchPane
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.viewmodels.SearchController
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
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.MoreItem
import io.github.ronganam.dana.ui.viewmodels.Place

/**
 * More: Shopping, Money and Projects (each only with its addon: `shopping`,
 * `money`, `knowledge`), Calls, People and About you. Shopping, Money and
 * Projects take over the place with their own list and stage, under a
 * "← More" link; the others open their screen in the stage
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
    // Its addon went away (turned off): back to More's list.
    LaunchedEffect(item, addons) {
        if ((item == MoreItem.SHOPPING && "shopping" !in addons) || (item == MoreItem.MONEY && "money" !in addons) ||
            (item == MoreItem.PROJECTS && "knowledge" !in addons)
        ) toMore()
    }
    return when (item) {
        MoreItem.SHOPPING -> takeOver(rememberShoppingDestination(viewModel), toMore)
        MoreItem.MONEY -> takeOver(rememberMoneyDestination(viewModel), toMore)
        MoreItem.PROJECTS -> takeOver(rememberProjectsDestination(viewModel), toMore)
        else -> Destination(
            place = Place.MORE,
            picked = item != null,
            onBack = { shell.clearPick(Place.MORE) },
            list = { MoreList(viewModel, item) },
            stage = { MoreStage(viewModel, item) },
            // Its header has the buttons (PlaceHeader's trailing slot).
            askSlot = false
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
    wide = inner.wide,
    askSlot = false
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
        Triple(MoreItem.PROJECTS, "plans · decisions · files", setOf(MoreItem.PROJECTS)).takeIf { "knowledge" in addons },
        Triple(MoreItem.CALLS, calls.size.takeIf { it > 0 }?.toString().orEmpty(), setOf(MoreItem.CALLS)),
        Triple(MoreItem.PEOPLE, people.size.takeIf { it > 0 }?.toString().orEmpty(), setOf(MoreItem.PEOPLE)),
        Triple(MoreItem.ABOUT, "what Dana knows · settings", setOf(MoreItem.ABOUT, MoreItem.SETTINGS, MoreItem.SIGNALS, MoreItem.MEMORY))
    )
    val accent = MaterialTheme.colorScheme.primary
    val canvas = MaterialTheme.colorScheme.background
    val search by viewModel.searchController.state.collectAsState()
    val query = shell.searchDraft.text
    Column(Modifier.fillMaxSize()) {
        // The header buttons (folded: the conversation and Ask Dana) on the 16dp margin, as on every place.
        PlaceHeader("More", trailing = if (!wide) ({ HeaderAsk(viewModel, conversation = true) }) else null)
        // Search your things: reminders, calls, people, notes, notifications, shopping (GET /api/search).
        SearchField(
            value = shell.searchDraft,
            onChange = { shell.searchDraft = it },
            modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 8.dp)
        )
        if (SearchController.isSearchable(query)) {
            BackHandler { shell.searchDraft = TextFieldValue("") }
            Box(Modifier.weight(1f).fillMaxWidth()) {
                SearchPane(
                    query = query,
                    state = search,
                    wide = false,
                    onResult = { r ->
                        shell.searchDraft = TextFieldValue("")
                        viewModel.openSearchResult(r)
                    },
                    onAsk = {
                        shell.openThread()
                        if (viewModel.chatController.sendChatMessage(query.trim())) shell.searchDraft = TextFieldValue("")
                    }
                )
            }
        } else MoreRows(rows, selected, wide, accent, canvas) { shell.selectMore(it) }
    }
}

@Composable
private fun ColumnScope.MoreRows(
    rows: List<Triple<MoreItem, String, Set<MoreItem>>>,
    selected: MoreItem?,
    wide: Boolean,
    accent: androidx.compose.ui.graphics.Color,
    canvas: androidx.compose.ui.graphics.Color,
    onSelect: (MoreItem) -> Unit
) {
    val c = circuitColors
    Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()).padding(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 20.dp)) {
        // One tonal group; unfolded the open item's row is lifted (and marked by the accent at its start).
        GroupedSection {
            rows.forEach { (item, meta, matches) ->
                val on = wide && selected in matches
                item(key = item, color = if (on) canvas else null) {
                    // A count sits at the end of the row; a description goes under the name.
                    val isCount = meta.all { it.isDigit() }
                    GroupRow(
                        title = item.label,
                        subtitle = if (!isCount) meta.replaceFirstChar { it.uppercase() } else null,
                        trailing = if (isCount && meta.isNotEmpty()) ({ Text(meta, style = MaterialTheme.typography.bodyMedium, color = c.inkSoft) }) else null,
                        modifier = if (on) Modifier.drawBehind {
                            drawLine(accent, Offset(1.5.dp.toPx(), 0f), Offset(1.5.dp.toPx(), size.height), 3.dp.toPx())
                        } else Modifier,
                        onClick = { onSelect(item) }
                    )
                }
            }
        }
    }
}

/** More's search field: a tonal pill with a search icon and × to clear. */
@Composable
private fun SearchField(value: TextFieldValue, onChange: (TextFieldValue) -> Unit, modifier: Modifier = Modifier) {
    val c = circuitColors
    BasicTextField(
        value = value,
        onValueChange = onChange,
        singleLine = true,
        textStyle = MaterialTheme.typography.bodyLarge.copy(color = c.ink, textDirection = TextDirection.Content),
        cursorBrush = SolidColor(c.accent),
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
        modifier = modifier.fillMaxWidth(),
        decorationBox = { inner ->
            Row(
                Modifier.fillMaxWidth().heightIn(min = 48.dp).clip(RoundedCornerShape(24.dp))
                    .background(MaterialTheme.colorScheme.surfaceContainerHigh).padding(start = 14.dp, end = 4.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(Icons.Outlined.Search, contentDescription = null, tint = c.inkSoft, modifier = Modifier.size(20.dp))
                Box(Modifier.weight(1f).padding(horizontal = 12.dp)) {
                    if (value.text.isEmpty()) Text("Search reminders, calls, people…", style = MaterialTheme.typography.bodyLarge, color = c.mute, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    inner()
                }
                if (value.text.isNotEmpty()) IconButton(onClick = { onChange(TextFieldValue("")) }, modifier = Modifier.size(40.dp)) {
                    Icon(Icons.Filled.Close, contentDescription = "Clear the search", tint = c.inkSoft, modifier = Modifier.size(18.dp))
                }
            }
        }
    )
}

@Composable
private fun MoreStage(viewModel: MainViewModel, item: MoreItem?) {
    val shell = viewModel.shellController
    val stacked = LocalShellLayout.current == ShellLayout.STACKED
    if (item == null) {
        Column(Modifier.padding(horizontal = 26.dp, vertical = 28.dp)) {
            Text("Pick one on the left.", style = MaterialTheme.typography.headlineSmall)
            Text("Calls, people and settings open here.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    } else MoreItemStage(viewModel, item, stacked)
}

@Composable
private fun MoreItemStage(viewModel: MainViewModel, item: MoreItem, stacked: Boolean) {
    val shell = viewModel.shellController
    val back = { shell.clearPick(Place.MORE) }
    Column(Modifier.fillMaxSize()) {
        // Screens with their own back arrow (People, About you, Settings, Signals, Review) get it; the others get "← More" on the cover.
        val ownBack = item == MoreItem.PEOPLE || item == MoreItem.ABOUT || item == MoreItem.SETTINGS || item == MoreItem.SIGNALS ||
            item == MoreItem.REVIEW
        if (item == MoreItem.MEMORY) BackLink("← About you", back)
        else if (stacked && !ownBack) BackLink("← More", back)
        PaneWindow(Modifier.weight(1f)) {
            when (item) {
                MoreItem.CALLS -> CallsScreen(viewModel)
                MoreItem.PEOPLE -> PeopleScreen(viewModel, onBack = if (stacked) back else null)
                // They take over the place (rememberMoreDestination); never shown in this stage.
                MoreItem.SHOPPING, MoreItem.MONEY, MoreItem.PROJECTS -> Unit
                MoreItem.MEMORY -> MemoryScreen(viewModel)
                MoreItem.ABOUT -> AboutScreen(viewModel, onBack = if (stacked) back else null, onOpenSettings = { shell.selectMore(MoreItem.SETTINGS) })
                // Reached from About you, so Back always returns there.
                MoreItem.SETTINGS -> SettingsScreen(viewModel, onBack = back, onOpenSignals = { shell.selectMore(MoreItem.SIGNALS) })
                // Reached from Settings, so Back returns there.
                MoreItem.SIGNALS -> io.github.ronganam.dana.ui.screens.signals.SignalsScreen(viewModel, onBack = back)
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
