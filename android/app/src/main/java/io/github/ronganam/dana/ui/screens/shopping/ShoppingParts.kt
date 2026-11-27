package io.github.ronganam.dana.ui.screens.shopping

import android.view.HapticFeedbackConstants
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.shopping.Aisle
import io.github.ronganam.dana.data.shopping.ListPerson
import io.github.ronganam.dana.data.shopping.ShoppingText
import io.github.ronganam.dana.data.shopping.ShoppingView
import io.github.ronganam.dana.data.shopping.Usual
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkChoice
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.components.MetaLabel
import io.github.ronganam.dana.ui.components.Rule
import io.github.ronganam.dana.ui.screens.common.byScript
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/*
 * The pieces of Shopping (under More) (`ShoppingDestination` arranges them per
 * window): header, invitation banner, add bar with suggestions and usuals,
 * aisles of rows, Bought, the editor and the Share sheet. Text the user typed
 * is laid out by its own script; the tick stays at the start of a row and the
 * edit button at its end.
 */

/** An aisle's icon (the server's `categories` keys). */
fun aisleIcon(key: String): ImageVector = when (key) {
    "produce" -> Icons.Outlined.Eco
    "bakery" -> Icons.Outlined.BakeryDining
    "dairy" -> Icons.Outlined.Egg
    "meat" -> Icons.Outlined.SetMeal
    "pantry" -> Icons.Outlined.Kitchen
    "snacks" -> Icons.Outlined.Cookie
    "drinks" -> Icons.Outlined.LocalDrink
    "frozen" -> Icons.Outlined.AcUnit
    "household" -> Icons.Outlined.CleaningServices
    "care" -> Icons.Outlined.MedicalServices
    "baby" -> Icons.Outlined.ChildFriendly
    "pets" -> Icons.Outlined.Pets
    else -> Icons.Outlined.Inventory2
}

// ---------- header ----------

/** Title, the quiet line, people's initials, Share (owner), the menu (only with something in it). */
@Composable
fun ShoppingHeader(
    model: ShoppingView.Model,
    menu: List<ShoppingView.MenuEntry>,
    viewing: Boolean,
    canShare: Boolean,
    syncLine: String?,
    onShare: () -> Unit,
    onMenu: (ShoppingView.MenuEntry) -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(modifier.fillMaxWidth().padding(start = 18.dp, end = 8.dp, top = 14.dp, bottom = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Shopping", style = MaterialTheme.typography.headlineMedium, maxLines = 1)
                Text(
                    model.subtitle,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis
                )
            }
            if (model.people.isNotEmpty()) People(model.people)
            if (canShare) IconButton(onClick = onShare) {
                Icon(Icons.Outlined.PersonAddAlt, contentDescription = "Share your list", tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (menu.isNotEmpty()) ListMenu(menu, onMenu)
        }
        if (viewing) InkButton("← Back to your list", emphasis = InkEmphasis.BARE, onClick = onBack, modifier = Modifier.padding(top = 2.dp))
        if (syncLine != null) MetaLabel(syncLine, modifier = Modifier.padding(top = 6.dp))
    }
}

/** Overlapping initials of the list's people (owner first). */
@Composable
private fun People(people: List<ListPerson>) {
    val shown = people.take(4)
    Box(
        Modifier.padding(end = 2.dp).semantics(mergeDescendants = true) {
            contentDescription = "On the list: " + people.joinToString { it.name }
        }
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy((-8).dp)) {
            shown.forEach { Initials(it.name, size = 30) }
        }
    }
}

/** A round chip with a person's initials. */
@Composable
fun Initials(name: String, size: Int = 24, modifier: Modifier = Modifier) {
    Box(
        modifier
            .size(size.dp)
            .clip(CircleShape)
            .background(MaterialTheme.colorScheme.surfaceContainerHighest)
            .border(1.5.dp, MaterialTheme.colorScheme.background, CircleShape),
        contentAlignment = Alignment.Center
    ) {
        // Ink on the highest surface (about 11:1 dark, 12:1 light): readable at a glance.
        Text(
            ShoppingText.initials(name),
            style = (if (size >= 28) MaterialTheme.typography.labelMedium else MaterialTheme.typography.labelSmall)
                .copy(fontWeight = FontWeight.SemiBold),
            color = MaterialTheme.colorScheme.onSurface,
            maxLines = 1
        )
    }
}

@Composable
private fun ListMenu(menu: List<ShoppingView.MenuEntry>, onMenu: (ShoppingView.MenuEntry) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { open = true }) {
            Icon(Icons.Outlined.MoreVert, contentDescription = "List menu", tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            val lists = menu.filter { it.act != "clear" }
            if (lists.isNotEmpty()) MetaLabel("Lists", modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp))
            menu.forEach { e ->
                if (e.act == "clear" && lists.isNotEmpty()) HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                DropdownMenuItem(
                    text = {
                        Column {
                            Text(e.label, style = MaterialTheme.typography.bodyLarge.byScript(e.label))
                            if (e.note.isNotEmpty()) Text(e.note, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    },
                    leadingIcon = when {
                        e.act == "clear" -> ({ Icon(Icons.Outlined.DeleteSweep, null) })
                        e.current -> ({ Icon(Icons.Outlined.Check, null, tint = MaterialTheme.colorScheme.primary) })
                        else -> ({ Spacer(Modifier.size(24.dp)) })
                    },
                    onClick = { open = false; onMenu(e) }
                )
            }
        }
    }
}

// ---------- invitations ----------

/** "Robin shared their shopping list with you" and its answers, with the inline "Move your N items over?" step. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun InviteBanner(
    invite: ShoppingView.InviteModel,
    ui: String?,
    onUse: () -> Unit,
    onLook: () -> Unit,
    onNotNow: () -> Unit,
    onMove: (Boolean) -> Unit,
    modifier: Modifier = Modifier
) {
    val busy = ui == "busy"
    val shape = RoundedCornerShape(14.dp)
    Column(
        modifier
            .fillMaxWidth()
            .padding(horizontal = 14.dp, vertical = 6.dp)
            .clip(shape)
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .border(1.dp, MaterialTheme.colorScheme.primary.copy(alpha = 0.55f), shape)
            .padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        if (ui == "ask") {
            Text(invite.moveText, style = MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                InkButton("Move them", emphasis = InkEmphasis.SOLID, onClick = { onMove(true) })
                InkButton("Leave them", onClick = { onMove(false) })
            }
        } else {
            Row(verticalAlignment = Alignment.Top) {
                Icon(Icons.Outlined.GroupAdd, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.padding(end = 10.dp, top = 1.dp).size(20.dp))
                Column {
                    Text(invite.text, style = MaterialTheme.typography.titleSmall)
                    if (invite.readOnly) Text("View only", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (invite.canUse) InkButton("Use this list", emphasis = InkEmphasis.SOLID, enabled = !busy, onClick = onUse)
                else InkButton("Look at it", emphasis = InkEmphasis.PRIMARY, enabled = !busy, onClick = onLook)
                InkButton("Not now", enabled = !busy, onClick = onNotNow)
            }
        }
    }
}

// ---------- adding ----------

/** The usuals as a row of "+ name" chips. */
@Composable
fun UsualsRow(usuals: List<Usual>, onAdd: (String) -> Unit, modifier: Modifier = Modifier, label: Boolean = true) {
    if (usuals.isNotEmpty()) Column(modifier.fillMaxWidth()) {
        if (label) MetaLabel("Usuals", modifier = Modifier.padding(start = 18.dp, top = 8.dp, bottom = 6.dp))
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 14.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            usuals.forEach { u -> AddChip(u.name) { onAdd(u.name) } }
        }
    }
}

@Composable
private fun AddChip(name: String, onClick: () -> Unit) {
    val shape = RoundedCornerShape(18.dp)
    Row(
        Modifier
            .heightIn(min = 40.dp)
            .clip(shape)
            .border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape)
            .clickable(onClickLabel = "Add $name", onClick = onClick)
            .padding(start = 10.dp, end = 14.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(Icons.Outlined.Add, null, modifier = Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(6.dp))
        Text(name, style = MaterialTheme.typography.bodyMedium.byScript(name), maxLines = 1)
    }
}

/**
 * The add field: Enter (the keyboard's action key) adds what was typed and
 * keeps the field focused and empty for the next one. No voice button: the
 * ask bar has the voice input.
 */
@Composable
fun AddField(
    value: TextFieldValue,
    onValue: (TextFieldValue) -> Unit,
    onAdd: () -> Unit,
    onFocus: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
    focusRequester: FocusRequester = remember { FocusRequester() }
) {
    val shape = RoundedCornerShape(14.dp)
    val textStyle = MaterialTheme.typography.bodyLarge.byScript(value.text).copy(color = MaterialTheme.colorScheme.onSurface)
    Row(
        modifier
            .fillMaxWidth()
            .heightIn(min = 52.dp)
            .clip(shape)
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape)
            .clickable(onClickLabel = "Add items") { focusRequester.requestFocus() }
            .padding(start = 14.dp, end = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(Icons.Outlined.Add, null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(20.dp))
        Spacer(Modifier.width(10.dp))
        Box(Modifier.weight(1f), contentAlignment = Alignment.CenterStart) {
            if (value.text.isEmpty()) Text("Add items…", style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
            BasicTextField(
                value = value,
                onValueChange = onValue,
                singleLine = true,
                textStyle = textStyle,
                cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { onAdd() }),
                modifier = Modifier
                    .fillMaxWidth()
                    .focusRequester(focusRequester)
                    .onFocusChanged { onFocus(it.isFocused) }
                    .semantics { contentDescription = "Add items" }
            )
        }
        if (value.text.isNotBlank()) IconButton(onClick = onAdd) {
            Icon(Icons.Outlined.ArrowUpward, contentDescription = "Add", tint = MaterialTheme.colorScheme.primary)
        }
    }
}

/** Names from the history matching what is typed; × = "Don't suggest again". */
@Composable
fun SuggestionList(suggestions: List<Usual>, onAdd: (String) -> Unit, onForget: (String) -> Unit, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth()) {
        suggestions.take(6).forEach { s ->
            Row(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = 48.dp)
                    .clickable(onClickLabel = "Add ${s.name}") { onAdd(s.name) }
                    .padding(start = 18.dp, end = 4.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(Icons.Outlined.History, null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(12.dp))
                Text(s.name, style = MaterialTheme.typography.bodyLarge.byScript(s.name), modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                IconButton(onClick = { onForget(s.name) }) {
                    Icon(Icons.Outlined.Close, contentDescription = "Don't suggest ${s.name} again", tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp))
                }
            }
        }
    }
}

// ---------- aisles and rows ----------

@Composable
fun AisleHeader(key: String, name: String, count: Int) {
    Column(Modifier.fillMaxWidth().padding(start = 18.dp, end = 18.dp, top = 16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(bottom = 8.dp)) {
            Icon(aisleIcon(key), null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(10.dp))
            MetaLabel(name, modifier = Modifier.weight(1f))
            MetaLabel(count.toString())
        }
        Rule()
    }
}

/**
 * One item. A tap anywhere ticks it (a light haptic); a long press or the
 * pencil opens the editor; a swipe either way removes it. Read-only lists
 * show the row without any of that.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun ItemRow(
    row: ShoppingView.Row,
    canEdit: Boolean,
    surface: androidx.compose.ui.graphics.Color,
    onTap: () -> Unit,
    onEdit: () -> Unit,
    onRemove: () -> Unit
) {
    val view = LocalView.current
    // The swipe only asks for the removal and never stays dismissed: the row leaves the list
    // (shown at once) and comes back in place after Undo or a refusal. A dismissed state would
    // be restored with the row's key when it came back (the state is saveable) and remove it again.
    var removing by remember { mutableStateOf(false) }
    LaunchedEffect(removing) {
        if (removing) { delay(1_000); removing = false }
    }
    val state = rememberSwipeToDismissBoxState()
    val swipeScope = rememberCoroutineScope()
    SwipeToDismissBox(
        state = state,
        // The row never stays swiped away: the removal shows as the list changes.
        onDismiss = { value ->
            if (value != SwipeToDismissBoxValue.Settled && !removing) {
                removing = true
                onRemove()
            }
            swipeScope.launch { state.reset() }
        },
        enableDismissFromStartToEnd = canEdit,
        enableDismissFromEndToStart = canEdit,
        backgroundContent = {
            val toEnd = state.dismissDirection == SwipeToDismissBoxValue.StartToEnd
            Row(
                Modifier.fillMaxSize().background(MaterialTheme.colorScheme.errorContainer).padding(horizontal = 22.dp),
                horizontalArrangement = if (toEnd) Arrangement.Start else Arrangement.End,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(Icons.Outlined.Delete, null, tint = MaterialTheme.colorScheme.onErrorContainer)
                Spacer(Modifier.width(8.dp))
                Text("Remove", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onErrorContainer)
            }
        }
    ) {
        val done = row.checked
        val tap = Modifier.combinedClickable(
            enabled = canEdit,
            role = Role.Checkbox,
            onClickLabel = if (done) "Put back on the list" else "Mark as bought",
            onLongClickLabel = "Edit",
            onLongClick = {
                view.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
                onEdit()
            },
            onClick = {
                view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                onTap()
            }
        )
        Column(Modifier.fillMaxWidth().background(surface)) {
            Row(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = 56.dp)
                    .then(tap)
                    .semantics { stateDescription = if (done) "Bought" else "To buy" }
                    .padding(start = 18.dp, end = if (canEdit) 4.dp else 18.dp, top = 6.dp, bottom = 6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Tick(done)
                Spacer(Modifier.width(14.dp))
                ItemText(row, Modifier.weight(1f))
                row.by?.let { Initials(it.name, size = 26, modifier = Modifier.padding(start = 8.dp)) }
                if (canEdit) IconButton(onClick = onEdit) {
                    Icon(Icons.Outlined.Edit, contentDescription = "Edit ${row.name}", tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(20.dp))
                }
            }
            Rule(Modifier.padding(start = 18.dp, end = 18.dp))
        }
    }
}

@Composable
private fun Tick(done: Boolean) {
    val accent = MaterialTheme.colorScheme.primary
    Box(
        Modifier
            .size(24.dp)
            .clip(CircleShape)
            .then(if (done) Modifier.background(accent) else Modifier.border(1.5.dp, MaterialTheme.colorScheme.outline, CircleShape)),
        contentAlignment = Alignment.Center
    ) {
        if (done) Icon(Icons.Outlined.Check, null, tint = MaterialTheme.colorScheme.onPrimary, modifier = Modifier.size(16.dp))
    }
}

/**
 * Name + quantity chip, the note under it. The layout never follows the text:
 * every name, chip and note starts next to the check, so a mixed Hebrew /
 * English list stays one column; each text still runs in its own direction.
 */
@Composable
private fun ItemText(row: ShoppingView.Row, modifier: Modifier) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    val align = startOfRow()
    Column(modifier.alpha(if (row.checked) 0.62f else 1f)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                row.name,
                style = MaterialTheme.typography.bodyLarge.byScript(row.name).let {
                    if (row.checked) it.copy(textDecoration = TextDecoration.LineThrough) else it
                },
                textAlign = align,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f, fill = false)
            )
            if (row.quantity.isNotEmpty()) QuantityChip(row.quantity, Modifier.padding(start = 8.dp))
        }
        if (row.note.isNotEmpty()) Text(
            row.note,
            style = MaterialTheme.typography.bodySmall.byScript(row.note),
            textAlign = align,
            color = muted,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis
        )
    }
}

/** The row's start edge as an absolute alignment, so a right-to-left line still starts by the check. */
@Composable
private fun startOfRow(): TextAlign =
    if (LocalLayoutDirection.current == LayoutDirection.Ltr) TextAlign.Left else TextAlign.Right

@Composable
private fun QuantityChip(q: String, modifier: Modifier = Modifier) {
    Text(
        q,
        style = MaterialTheme.typography.labelMedium.byScript(q),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        maxLines = 1,
        modifier = modifier
            .clip(RoundedCornerShape(6.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(horizontal = 7.dp, vertical = 2.dp)
    )
}

/** The Bought fold's header: the count, open/close, Clear. */
@Composable
fun BoughtHeader(count: Int, open: Boolean, canEdit: Boolean, onToggle: () -> Unit, onClear: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(top = 18.dp)) {
        Row(
            Modifier
                .fillMaxWidth()
                .heightIn(min = 48.dp)
                .clickable(onClickLabel = if (open) "Hide bought items" else "Show bought items", onClick = onToggle)
                .padding(start = 18.dp, end = 14.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Icon(
                if (open) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp)
            )
            Spacer(Modifier.width(10.dp))
            MetaLabel("Bought · $count", modifier = Modifier.weight(1f))
            if (canEdit) InkButton("Clear", emphasis = InkEmphasis.BARE, onClick = onClear)
        }
        Rule(Modifier.padding(horizontal = 18.dp))
    }
}

/** Nothing to buy: one serif line and a muted one. */
@Composable
fun ShoppingEmpty(canEdit: Boolean, addBelow: Boolean, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth().padding(horizontal = 26.dp, vertical = 36.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(
            "Nothing to buy",
            style = MaterialTheme.typography.headlineMedium
        )
        Text(
            when {
                !canEdit -> "Nothing on this list right now."
                addBelow -> "Add what you need below, or tell Dana."
                else -> "Add what you need above, or tell Dana."
            },
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

// ---------- the editor ----------

/**
 * Edit item: name, quantity (− / + for a number), note, aisle, Remove.
 * Done (or closing it) saves what changed; a refusal shows here.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun EditorContent(
    draft: ShoppingView.Draft,
    error: String?,
    categories: List<Aisle>,
    onDraft: (ShoppingView.Draft) -> Unit,
    onStep: (Int) -> Unit,
    onDone: () -> Unit,
    onRemove: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(modifier.fillMaxWidth().padding(horizontal = 18.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Edit item", style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
            InkButton("Done", emphasis = InkEmphasis.SOLID, onClick = onDone)
        }
        Field("Name") {
            InkTextField(draft.name, { onDraft(draft.copy(name = it)) }, imeAction = ImeAction.Done, onDone = onDone)
            if (error != null) Text(error, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(top = 4.dp))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Field("Quantity", Modifier.weight(1f)) { Stepper(draft.quantity, { onDraft(draft.copy(quantity = it)) }, onStep) }
            Field("Note", Modifier.weight(1.2f)) {
                InkTextField(draft.note, { onDraft(draft.copy(note = it)) }, placeholder = "Brand, size…", imeAction = ImeAction.Done, onDone = onDone)
            }
        }
        Field("Aisle") {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                categories.forEach { c ->
                    AisleChip(c, selected = c.key == draft.category) { onDraft(draft.copy(category = c.key)) }
                }
            }
        }
        Row(
            Modifier
                .clip(RoundedCornerShape(10.dp))
                .border(1.dp, MaterialTheme.colorScheme.error.copy(alpha = 0.7f), RoundedCornerShape(10.dp))
                .clickable(onClick = onRemove)
                .heightIn(min = 44.dp)
                .padding(horizontal = 14.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Icon(Icons.Outlined.Delete, null, tint = MaterialTheme.colorScheme.error, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text("Remove", style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Medium), color = MaterialTheme.colorScheme.error)
        }
    }
}

@Composable
private fun Field(label: String, modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Column(modifier) {
        Text(label, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(bottom = 6.dp))
        content()
    }
}

@Composable
private fun InkTextField(
    value: String,
    onValue: (String) -> Unit,
    modifier: Modifier = Modifier,
    placeholder: String = "",
    imeAction: ImeAction = ImeAction.Next,
    onDone: () -> Unit = {},
    textStyle: TextStyle = MaterialTheme.typography.bodyLarge
) {
    val shape = RoundedCornerShape(10.dp)
    Box(
        modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .clip(shape)
            .background(MaterialTheme.colorScheme.background)
            .border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape)
            .padding(horizontal = 12.dp),
        contentAlignment = Alignment.CenterStart
    ) {
        if (value.isEmpty() && placeholder.isNotEmpty()) Text(placeholder, style = textStyle, color = MaterialTheme.colorScheme.onSurfaceVariant)
        BasicTextField(
            value = value,
            onValueChange = onValue,
            singleLine = true,
            textStyle = textStyle.byScript(value).copy(color = MaterialTheme.colorScheme.onSurface),
            cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = imeAction),
            keyboardActions = KeyboardActions(onDone = { onDone() }),
            modifier = Modifier.fillMaxWidth()
        )
    }
}

/** Quantity: a field with − / + around it (the buttons count only for a plain number or nothing). */
@Composable
private fun Stepper(value: String, onValue: (String) -> Unit, onStep: (Int) -> Unit) {
    val count = ShoppingView.isCount(value)
    val shape = RoundedCornerShape(10.dp)
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .clip(shape)
            .background(MaterialTheme.colorScheme.background)
            .border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape),
        verticalAlignment = Alignment.CenterVertically
    ) {
        IconButton(onClick = { onStep(-1) }, enabled = count && value.isNotBlank()) { Icon(Icons.Outlined.Remove, contentDescription = "Less") }
        Box(Modifier.weight(1f), contentAlignment = Alignment.Center) {
            if (value.isEmpty()) Text("–", style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
            BasicTextField(
                value = value,
                onValueChange = onValue,
                singleLine = true,
                textStyle = MaterialTheme.typography.bodyLarge.copy(
                    color = MaterialTheme.colorScheme.onSurface,
                    textAlign = androidx.compose.ui.text.style.TextAlign.Center
                ),
                cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                modifier = Modifier.fillMaxWidth().semantics { contentDescription = "Quantity" }
            )
        }
        IconButton(onClick = { onStep(1) }, enabled = count) { Icon(Icons.Outlined.Add, contentDescription = "More") }
    }
}

@Composable
private fun AisleChip(aisle: Aisle, selected: Boolean, onClick: () -> Unit) {
    val shape = RoundedCornerShape(18.dp)
    val accent = MaterialTheme.colorScheme.primary
    Row(
        Modifier
            .heightIn(min = 40.dp)
            .clip(shape)
            .then(if (selected) Modifier.background(accent.copy(alpha = 0.12f)) else Modifier)
            .border(1.dp, if (selected) accent else MaterialTheme.colorScheme.outlineVariant, shape)
            .clickable(role = Role.RadioButton, onClick = onClick)
            .semantics { stateDescription = if (selected) "Chosen" else "" }
            .padding(start = 10.dp, end = 14.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(aisleIcon(aisle.key), null, tint = if (selected) accent else MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(6.dp))
        Text(aisle.name, style = MaterialTheme.typography.bodyMedium, color = if (selected) accent else MaterialTheme.colorScheme.onSurface)
    }
}

// ---------- sharing ----------

/** Share your list: who is on it (Can edit / View only / ×) and who can be added. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ShareContent(
    model: ShoppingView.ShareModel?,
    error: String?,
    busy: Boolean,
    onLevel: (userId: String, level: String) -> Unit,
    onDone: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(modifier.fillMaxWidth().padding(horizontal = 18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Share your list", style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
            InkButton("Done", emphasis = InkEmphasis.SOLID, onClick = onDone)
        }
        if (error != null) Text(error, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        if (model == null) {
            if (error == null) Text("Loading…", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else if (model.people.isEmpty()) Text(
            "Only you can see this list. People you add see changes live and get an invitation.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        ) else {
            MetaLabel("On the list")
            model.people.forEach { p ->
                Column {
                    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.heightIn(min = 52.dp)) {
                        Initials(p.name, size = 30)
                        Spacer(Modifier.width(12.dp))
                        Text(p.name, style = MaterialTheme.typography.bodyLarge.byScript(p.name), modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                        IconButton(onClick = { onLevel(p.id, "none") }, enabled = !busy) {
                            Icon(Icons.Outlined.Close, contentDescription = "Take ${p.name} off the list", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(start = 42.dp, bottom = 6.dp)) {
                        InkChoice("Can edit", selected = p.access == "edit") { if (!busy && p.access != "edit") onLevel(p.id, "edit") }
                        InkChoice("View only", selected = p.access == "read") { if (!busy && p.access != "read") onLevel(p.id, "read") }
                    }
                    Rule()
                }
            }
        }
        if (model != null && model.others.isNotEmpty()) {
            MetaLabel("Add someone", modifier = Modifier.padding(top = 4.dp))
            model.others.forEach { u ->
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.heightIn(min = 52.dp)) {
                    Initials(u.name, size = 30)
                    Spacer(Modifier.width(12.dp))
                    Text(u.name, style = MaterialTheme.typography.bodyLarge.byScript(u.name), modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    InkButton("Add", emphasis = InkEmphasis.PRIMARY, enabled = !busy, onClick = { onLevel(u.id, "edit") })
                }
            }
        }
    }
}
