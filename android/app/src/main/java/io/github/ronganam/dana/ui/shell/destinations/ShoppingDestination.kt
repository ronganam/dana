package io.github.ronganam.dana.ui.shell.destinations

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import io.github.ronganam.dana.data.shopping.ShoppingScreen
import io.github.ronganam.dana.data.shopping.ShoppingView
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.screens.shopping.AddField
import io.github.ronganam.dana.ui.screens.shopping.AisleHeader
import io.github.ronganam.dana.ui.screens.shopping.BoughtHeader
import io.github.ronganam.dana.ui.screens.shopping.EditorContent
import io.github.ronganam.dana.ui.screens.shopping.InviteBanner
import io.github.ronganam.dana.ui.screens.shopping.ItemRow
import io.github.ronganam.dana.ui.screens.shopping.ShareContent
import io.github.ronganam.dana.ui.screens.shopping.ShoppingEmpty
import io.github.ronganam.dana.ui.screens.shopping.ShoppingHeader
import io.github.ronganam.dana.ui.screens.shopping.SuggestionList
import io.github.ronganam.dana.ui.screens.shopping.UsualsRow
import io.github.ronganam.dana.ui.shell.Destination
import io.github.ronganam.dana.ui.shell.LocalShellLayout
import io.github.ronganam.dana.ui.shell.ShellLayout
import io.github.ronganam.dana.ui.shell.edgeRule
import io.github.ronganam.dana.ui.viewmodels.MainViewModel
import io.github.ronganam.dana.ui.viewmodels.Place
import io.github.ronganam.dana.ui.viewmodels.ShoppingController

/**
 * Shopping (docs/shopping.md). Cover screen: one column (header, invitations,
 * usuals, aisles, Bought) with the add field docked at the bottom, in thumb
 * reach above the ask bar; the editor is a bottom sheet. Unfolded: the side
 * pane holds the header, invitations, usuals and Bought (and the editor while
 * one item is edited); the stage holds the aisles with the add field under
 * them. The screen stays on while the place shows; the live stream runs while
 * it is in front.
 */
@Composable
fun rememberShoppingDestination(viewModel: MainViewModel): Destination {
    val sc = viewModel.shoppingController
    val shell = viewModel.shellController
    val wide = LocalShellLayout.current != ShellLayout.STACKED
    val viewing by sc.viewing.collectAsState()

    // Shown on a counter in the kitchen: keep the screen on while the list is here.
    val view = LocalView.current
    DisposableEffect(view) {
        view.keepScreenOn = true
        onDispose { view.keepScreenOn = false }
    }
    // Follow changes live while the place is in front (and read it again on coming back).
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_START -> sc.startLive()
                Lifecycle.Event.ON_STOP -> sc.stopLive()
                else -> Unit
            }
        }
        lifecycle.addObserver(observer)
        if (lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) sc.startLive()
        onDispose {
            lifecycle.removeObserver(observer)
            sc.stopLive()
            sc.closeEditor()
            sc.setAddFocused(false)
            shell.askHidden = false
            shell.snackbarLift = 0.dp
        }
    }
    // Looking at someone's list read-only: Back returns to your own.
    BackHandler(enabled = viewing != null) { sc.backToActive() }

    return Destination(
        place = Place.MORE,
        picked = false,
        onBack = {},
        list = { if (wide) ShoppingSide(viewModel) else ShoppingCover(viewModel) },
        stage = if (wide) ({ ShoppingStage(viewModel) }) else null,
        title = "Shopping"
    )
}

/** What every arrangement draws from. */
private class ShoppingUi(val screen: ShoppingScreen, val model: ShoppingView.Model)

@Composable
private fun rememberShoppingUi(sc: ShoppingController): ShoppingUi? {
    val screen by sc.screen.collectAsState()
    val linger by sc.linger.collectAsState()
    val s = screen
    val model = remember(s, linger) { s?.let { ShoppingView.model(it, sc.me(), linger) } }
    return if (s != null && model != null) ShoppingUi(s, model) else null
}

// ---------- cover screen ----------

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ShoppingCover(viewModel: MainViewModel) {
    val sc = viewModel.shoppingController
    val ui = rememberShoppingUi(sc)
    val focused by sc.addFocused.collectAsState()
    if (ui == null) {
        Loading(sc)
    } else Column(Modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f).fillMaxWidth()) {
            item(key = "header") { Header(sc, ui) }
            invites(sc, ui)
            if (ui.model.canEdit && !focused && ui.model.usuals.isNotEmpty()) item(key = "usuals") {
                UsualsRow(ui.model.usuals, onAdd = sc::addName)
            }
            aisles(sc, ui, Color.Unspecified)
            if (ui.model.empty) item(key = "empty") { ShoppingEmpty(ui.model.canEdit, addBelow = true) }
            bought(sc, ui, Color.Unspecified)
            item(key = "end") { Spacer(Modifier.height(24.dp)) }
        }
        if (ui.model.canEdit) AddDock(viewModel, ui, Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
    }
    EditorSheet(sc, ui)
    ShareSheet(sc)
}

// ---------- unfolded ----------

@Composable
private fun ShoppingSide(viewModel: MainViewModel) {
    val sc = viewModel.shoppingController
    val ui = rememberShoppingUi(sc)
    val editor by sc.editor.collectAsState()
    val boughtOpen by sc.boughtOpen.collectAsState()
    val tint = MaterialTheme.colorScheme.surfaceContainerLow
    val e = editor
    when {
        ui == null -> Loading(sc)
        e != null -> Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(top = 16.dp, bottom = 24.dp)) {
            EditorContent(
                draft = e.draft, error = e.error, categories = ui.screen.categories,
                onDraft = sc::editDraft, onStep = sc::stepQuantity, onDone = sc::saveEditor,
                onRemove = { sc.remove(e.draft.itemId) }
            )
        }
        else -> Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 16.dp)) {
            Header(sc, ui)
            Invites(sc, ui)
            if (ui.model.canEdit) UsualsRow(ui.model.usuals, onAdd = sc::addName)
            if (ui.model.bought.isNotEmpty()) {
                BoughtHeader(ui.model.bought.size, boughtOpen, ui.model.canEdit, sc::toggleBought, sc::clearBought)
                if (boughtOpen) ui.model.bought.forEach { r -> Row(sc, ui, r, tint) }
            }
        }
    }
    ShareSheet(sc)
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ShoppingStage(viewModel: MainViewModel) {
    val sc = viewModel.shoppingController
    val ui = rememberShoppingUi(sc)
    if (ui != null) Column(Modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f).fillMaxWidth().widthIn(max = 760.dp)) {
            item(key = "top") { Spacer(Modifier.height(4.dp)) }
            aisles(sc, ui, Color.Unspecified)
            if (ui.model.empty) item(key = "empty") { ShoppingEmpty(ui.model.canEdit, addBelow = true) }
            item(key = "end") { Spacer(Modifier.height(24.dp)) }
        }
        if (ui.model.canEdit) AddDock(viewModel, ui, Modifier.padding(horizontal = 16.dp, vertical = 10.dp).widthIn(max = 760.dp))
    }
}

// ---------- shared pieces ----------

@Composable
private fun Header(sc: ShoppingController, ui: ShoppingUi) {
    val viewing by sc.viewing.collectAsState()
    val waiting by sc.waiting.collectAsState()
    val failed by sc.failed.collectAsState()
    val shareable by sc.shareable.collectAsState()
    val canShare = viewing == null && ui.model.isOwner && shareable?.let { it.first == ui.model.listId && it.second } == true
    val syncLine = when {
        viewing != null -> null
        waiting > 0 -> if (waiting == 1) "1 change waiting to sync" else "$waiting changes waiting to sync"
        failed -> "Offline · the list as last seen"
        else -> null
    }
    ShoppingHeader(
        model = ui.model,
        menu = remember(ui.screen) { ShoppingView.menuItems(ui.screen) },
        viewing = viewing != null,
        canShare = canShare,
        syncLine = syncLine,
        onShare = sc::openShare,
        onMenu = { e ->
            when (e.act) {
                "switch" -> sc.switchTo(e.listId, "edit")
                "view" -> sc.lookAt(e.listId)
                "clear" -> sc.clearBought()
            }
        },
        onBack = sc::backToActive
    )
}

@Composable
private fun Invites(sc: ShoppingController, ui: ShoppingUi) {
    val inviteUi by sc.inviteUi.collectAsState()
    ui.model.invites.forEach { inv -> Invite(sc, ui, inv.listId, inviteUi[inv.listId]) }
}

private fun LazyListScope.invites(sc: ShoppingController, ui: ShoppingUi) {
    items(ui.model.invites, key = { "invite-" + it.listId }) { inv ->
        val inviteUi by sc.inviteUi.collectAsState()
        Invite(sc, ui, inv.listId, inviteUi[inv.listId])
    }
}

@Composable
private fun Invite(sc: ShoppingController, ui: ShoppingUi, listId: String, state: String?) {
    val invite = ui.model.invites.firstOrNull { it.listId == listId }
    if (invite != null) InviteBanner(
        invite = ShoppingView.invite(invite, ui.screen),
        ui = state,
        onUse = { sc.useInvite(listId) },
        onLook = { sc.lookAt(listId) },
        onNotNow = { sc.answerInvite(listId, "dismiss", false) },
        onMove = { move -> sc.answerInvite(listId, "use", move) }
    )
}

@OptIn(ExperimentalFoundationApi::class)
private fun LazyListScope.aisles(sc: ShoppingController, ui: ShoppingUi, surface: Color) {
    for (g in ui.model.groups) {
        item(key = "aisle-" + g.key) {
            AisleHeader(g.key, g.name, g.rows.count { !it.checked })
        }
        items(g.rows, key = { it.id }) { r ->
            Box(Modifier.animateItemPlacement()) { Row(sc, ui, r, surface) }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
private fun LazyListScope.bought(sc: ShoppingController, ui: ShoppingUi, surface: Color) {
    if (ui.model.bought.isEmpty()) return
    item(key = "bought") {
        val open by sc.boughtOpen.collectAsState()
        BoughtHeader(ui.model.bought.size, open, ui.model.canEdit, sc::toggleBought, sc::clearBought)
    }
    items(ui.model.bought, key = { "b-" + it.id }) { r ->
        val open by sc.boughtOpen.collectAsState()
        if (open) Box(Modifier.animateItemPlacement()) { Row(sc, ui, r, surface) }
    }
}

@Composable
private fun Row(sc: ShoppingController, ui: ShoppingUi, r: ShoppingView.Row, surface: Color) {
    ItemRow(
        row = r,
        canEdit = ui.model.canEdit,
        surface = if (surface == Color.Unspecified) MaterialTheme.colorScheme.background else surface,
        onTap = { sc.tap(r) },
        onEdit = { sc.openEditor(r.id) },
        onRemove = { sc.remove(r.id) }
    )
}

/**
 * The add field at the bottom, with what helps while typing above it: the
 * usuals while it is empty, matching names once something is typed. While it
 * has the keyboard the ask bar steps aside (one field, one voice input).
 */
@Composable
private fun AddDock(viewModel: MainViewModel, ui: ShoppingUi, modifier: Modifier = Modifier) {
    val sc = viewModel.shoppingController
    val shell = viewModel.shellController
    val focused by sc.addFocused.collectAsState()
    val suggestions by sc.suggestions.collectAsState()
    val density = LocalDensity.current
    val focus = LocalFocusManager.current
    val requester = remember { FocusRequester() }
    val ime = WindowInsets.ime.getBottom(density) > 0
    // The keyboard went away (Back): leave the field, so the dock folds and the ask bar returns.
    var hadIme by remember { mutableStateOf(false) }
    LaunchedEffect(ime) {
        if (hadIme && !ime && focused) focus.clearFocus()
        hadIme = ime
    }
    SideEffect { shell.askHidden = focused && ime }
    val text = sc.addText.text
    Column(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.background)
            .edgeRule(top = focused)
            .onSizeChanged { shell.snackbarLift = with(density) { it.height.toDp() } }
    ) {
        if (focused) {
            if (text.isBlank()) UsualsRow(ui.model.usuals, onAdd = sc::addName, modifier = Modifier.padding(top = 8.dp), label = ui.model.usuals.isNotEmpty())
            else if (suggestions.isNotEmpty()) SuggestionList(suggestions, onAdd = sc::addName, onForget = sc::forget)
        }
        AddField(
            value = sc.addText,
            onValue = sc::onAddText,
            onAdd = sc::addTyped,
            onFocus = sc::setAddFocused,
            focusRequester = requester,
            modifier = modifier
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun EditorSheet(sc: ShoppingController, ui: ShoppingUi?) {
    val editor by sc.editor.collectAsState()
    val e = editor
    val s = ui
    if (e != null && s != null) ModalBottomSheet(
        onDismissRequest = { sc.saveEditor(closing = true) },
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surfaceContainerLow
    ) {
        Column(Modifier.verticalScroll(rememberScrollState()).imePadding().padding(bottom = 28.dp)) {
            EditorContent(
                draft = e.draft, error = e.error, categories = s.screen.categories,
                onDraft = sc::editDraft, onStep = sc::stepQuantity, onDone = sc::saveEditor,
                onRemove = { sc.remove(e.draft.itemId) }
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ShareSheet(sc: ShoppingController) {
    val share by sc.share.collectAsState()
    val sh = share
    if (sh != null) ModalBottomSheet(
        onDismissRequest = sc::closeShare,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = MaterialTheme.colorScheme.surfaceContainerLow
    ) {
        Column(Modifier.verticalScroll(rememberScrollState()).padding(bottom = 28.dp)) {
            ShareContent(sh.model, sh.error, sh.busy, onLevel = sc::setShare, onDone = sc::closeShare)
        }
    }
}

@Composable
private fun Loading(sc: ShoppingController) {
    val failed by sc.failed.collectAsState()
    Column(Modifier.fillMaxWidth().padding(horizontal = 26.dp, vertical = 32.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(
            if (failed) "Couldn't load the list" else "Loading…",
            style = MaterialTheme.typography.titleMedium
        )
        if (failed) {
            Text("The server can't be reached right now.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            InkButton("Try again", emphasis = InkEmphasis.PRIMARY, onClick = sc::refresh)
        }
    }
}
