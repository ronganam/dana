package io.github.ronganam.dana.wear.shopping

import io.github.ronganam.dana.protocol.ShopItem
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.runtime.remember
import android.view.HapticFeedbackConstants
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.graphicsLayer
import io.github.ronganam.dana.wear.ui.theme.DanaMotion
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Undo
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Mic
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.wear.compose.foundation.ExperimentalWearFoundationApi
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.items
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.material.Icon
import androidx.wear.compose.material.MaterialTheme
import androidx.wear.compose.material.PositionIndicator
import androidx.wear.compose.material.Scaffold
import androidx.wear.compose.material.Text
import io.github.ronganam.dana.wear.ui.components.rotaryScroll
import io.github.ronganam.dana.wear.ui.theme.DanaWear
import io.github.ronganam.dana.wear.R
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource

/**
 * The list to buy in aisle order, one large row per item. A tap ticks it (a
 * haptic, ticked at once, it stays [Shop.LINGER_MS] and leaves; a second tap
 * while it stays puts it back), Undo sits at the top edge for a few seconds.
 * Rows are one column: every name starts next to the ring, each name runs in
 * its own direction.
 */
@OptIn(ExperimentalWearFoundationApi::class)
@Composable
fun ShoppingScreen(
    state: ShopState,
    now: Long,
    onTick: (ShopItem) -> Unit,
    onUntick: (String) -> Unit,
    onAdd: (() -> Unit)?
) {
    val listState = rememberScalingLazyListState()
    val focus = remember { FocusRequester() }
    val view = LocalView.current
    val canEdit = state.list?.canEdit ?: false
    val rows = Shop.rows(state, now)
    val waiting = Shop.waiting(state)

    Scaffold(positionIndicator = { PositionIndicator(scalingLazyListState = listState) }) {
        ScalingLazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize().rotaryScroll(listState, focus),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(5.dp),
            contentPadding = PaddingValues(top = 26.dp, bottom = 40.dp, start = 10.dp, end = 10.dp)
        ) {
            item(key = "title") { Title(Shop.subtitle(state, now)) }
            if (onAdd != null && canEdit) item(key = "add") { AddButton(onAdd) }
            if (state.unreachable && waiting > 0) item(key = "unreachable") {
                Line(stringResource(R.string.phone_unreachable_waiting, waiting), DanaWear.Amber)
            }
            state.notice?.let { n -> item(key = "notice") { Line(n.text, if (n.error) DanaWear.Rose else DanaWear.InkSoft) } }
            when {
                state.list == null -> item(key = "none") { Calm(stringResource(R.string.no_list_yet), stringResource(R.string.open_dana_on_your_phone)) }
                Shop.allDone(state, now) -> item(key = "done") {
                    Calm(stringResource(R.string.all_done), pluralStringResource(R.plurals.ticked_off, state.tickedThisVisit, state.tickedThisVisit))
                }
                rows.isEmpty() -> item(key = "empty") { Calm(stringResource(R.string.nothing_to_buy), if (canEdit) stringResource(R.string.add_by_voice_or_tell) else null) }
            }
            items(rows, key = { it.key }) { row ->
                when (row) {
                    is ShopRow.Aisle -> AisleLabel(row.name)
                    is ShopRow.Item -> ItemRow(row, canEdit) {
                        if (row.ticked) {
                            view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                            onUntick(row.item.id)
                        } else {
                            view.performHapticFeedback(HapticFeedbackConstants.CONFIRM)
                            onTick(row.item)
                        }
                    }
                }
            }
        }
        // Undo sits at the top edge: the bottom is where the next row is tapped while ticking down the list.
        state.undo?.takeIf { it.until > now }?.let { offer ->
            Box(Modifier.fillMaxSize().padding(top = 14.dp), contentAlignment = Alignment.TopCenter) {
                UndoButton(offer.item.name) {
                    view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                    onUntick(offer.itemId)
                }
            }
        }
    }
}

@Composable
private fun Title(subtitle: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(bottom = 2.dp)) {
        Text(stringResource(R.string.label_shopping), fontFamily = DanaWear.Serif, fontSize = 24.sp, color = DanaWear.Ink)
        if (subtitle.isNotEmpty()) Text(subtitle.uppercase(), fontFamily = DanaWear.Mono, fontSize = 11.sp, color = DanaWear.Accent, maxLines = 1)
    }
}

@Composable
private fun AddButton(onAdd: () -> Unit) {
    Row(
        Modifier
            .heightIn(min = 44.dp)
            .clip(RoundedCornerShape(22.dp))
            .border(1.dp, DanaWear.Outline, RoundedCornerShape(22.dp))
            .clickable(onClickLabel = stringResource(R.string.add_by_voice), role = Role.Button, onClick = onAdd)
            .padding(horizontal = 18.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(Icons.Filled.Mic, contentDescription = null, tint = DanaWear.Accent, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(6.dp))
        Text(stringResource(R.string.add), fontSize = 14.sp, color = DanaWear.Ink)
    }
}

@Composable
private fun Line(text: String, color: androidx.compose.ui.graphics.Color) {
    Text(
        text,
        fontSize = 12.sp,
        color = color,
        textAlign = TextAlign.Center,
        maxLines = 3,
        overflow = TextOverflow.Ellipsis,
        style = MaterialTheme.typography.caption1.copy(textDirection = TextDirection.Content),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp)
    )
}

/** An empty or finished list: one italic serif line and a muted one. */
@Composable
private fun Calm(title: String, line: String?) {
    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(top = 10.dp, bottom = 6.dp)) {
        Text(title, fontFamily = DanaWear.Serif, fontStyle = FontStyle.Italic, fontSize = 22.sp, color = DanaWear.Ink)
        if (line != null) Text(line, fontSize = 12.sp, color = DanaWear.Mute, textAlign = TextAlign.Center)
    }
}

@Composable
private fun AisleLabel(name: String) {
    Text(
        name.uppercase(),
        fontFamily = DanaWear.Mono,
        fontSize = 10.sp,
        color = DanaWear.Mute,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = Modifier.padding(top = 8.dp)
    )
}

/** One item: the ring, the name (its own direction, from the ring's side) and, quieter, the quantity. */
@Composable
private fun ItemRow(row: ShopRow.Item, canEdit: Boolean, onTap: () -> Unit) {
    val item = row.item
    val align = if (LocalLayoutDirection.current == LayoutDirection.Ltr) TextAlign.Left else TextAlign.Right
    fun dir(s: String) = if (Shop.mostlyRtl(s)) TextDirection.Rtl else TextDirection.Ltr
    val shape = RoundedCornerShape(26.dp)
    val stateText = stringResource(if (row.ticked) R.string.bought else R.string.to_buy)
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 52.dp)
            .clip(shape)
            .background(if (row.ticked) DanaWear.tint(DanaWear.Sage) else DanaWear.Surface)
            .clickable(enabled = canEdit, role = Role.Checkbox, onClickLabel = stringResource(if (row.ticked) R.string.put_back else R.string.mark_as_bought), onClick = onTap)
            .semantics {
                stateDescription = stateText
                contentDescription = listOf(item.name, item.quantity).filter { it.isNotEmpty() }.joinToString(", ")
            }
            .padding(start = 12.dp, end = 14.dp, top = 7.dp, bottom = 7.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        if (canEdit) {
            Ring(row.ticked)
            Spacer(Modifier.width(10.dp))
        } else Spacer(Modifier.width(6.dp))
        Column(Modifier.weight(1f).alpha(if (row.ticked) 0.7f else 1f)) {
            Text(
                item.name,
                fontSize = 16.sp,
                color = DanaWear.Ink,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                textAlign = align,
                style = MaterialTheme.typography.body1.copy(
                    textDirection = dir(item.name),
                    textDecoration = if (row.ticked) TextDecoration.LineThrough else null
                ),
                modifier = Modifier.fillMaxWidth()
            )
            if (item.quantity.isNotEmpty()) Text(
                item.quantity,
                fontSize = 12.sp,
                color = DanaWear.Mute,
                maxLines = 1,
                textAlign = align,
                style = MaterialTheme.typography.caption2.copy(textDirection = dir(item.quantity)),
                modifier = Modifier.fillMaxWidth()
            )
        }
    }
}

/** Ticked, the ring fills with sage and the check scales in (read in draw / layer only: no recomposition per frame). */
@Composable
private fun Ring(ticked: Boolean) {
    val fill = animateFloatAsState(if (ticked) 1f else 0f, tween(DanaMotion.BASE - 40, easing = DanaMotion.Standard), label = "tick")
    Box(
        Modifier
            .size(24.dp)
            .border(2.dp, if (ticked) DanaWear.Sage else DanaWear.InkSoft, CircleShape)
            .drawBehind { if (fill.value > 0f) drawCircle(DanaWear.Sage, alpha = fill.value) },
        contentAlignment = Alignment.Center
    ) {
        Icon(
            Icons.Filled.Check,
            contentDescription = null,
            tint = DanaWear.OnAccent,
            modifier = Modifier.size(16.dp).graphicsLayer {
                alpha = fill.value
                scaleX = 0.4f + 0.6f * fill.value
                scaleY = 0.4f + 0.6f * fill.value
            }
        )
    }
}

@Composable
private fun UndoButton(name: String, onUndo: () -> Unit) {
    Row(
        Modifier
            .width(116.dp)
            .heightIn(min = 44.dp)
            .clip(RoundedCornerShape(22.dp))
            .background(DanaWear.SurfaceHigh)
            .border(1.dp, DanaWear.Accent, RoundedCornerShape(22.dp))
            .clickable(onClickLabel = stringResource(R.string.undo_item, name), role = Role.Button, onClick = onUndo),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(Icons.AutoMirrored.Filled.Undo, contentDescription = null, tint = DanaWear.Accent, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(6.dp))
        Text(stringResource(R.string.undo), fontSize = 14.sp, color = DanaWear.Accent)
    }
}
