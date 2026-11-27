package io.github.ronganam.dana.ui.components.circuit

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Chat
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.theme.circuitColors

/**
 * Where a folded place's header row is, so the shell's floating Ask Dana
 * lines up with it: [top] from the top of the place, [row] tall, its end on
 * the [margin] every place's content keeps.
 */
object HeaderSlot {
    val top = 8.dp
    val row = 48.dp
    val margin = 16.dp
    /** Between the title and the buttons. */
    val gap = 8.dp
}

/**
 * Ask Dana: the tonal mic in the top-right header slot of every folded place
 * (48dp, a rounded square in the pill indicator's tone, the mic in the accent).
 */
@Composable
fun AskDanaButton(onClick: () -> Unit, modifier: Modifier = Modifier, label: String = "Ask Dana") {
    val c = circuitColors
    Box(
        modifier
            .size(48.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(c.indicator)
            .clickable(role = Role.Button, onClickLabel = label, onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Icon(Icons.Outlined.Mic, contentDescription = label, tint = c.accent, modifier = Modifier.size(24.dp))
    }
}

/**
 * The conversation with Dana, from a place whose ask bar is not docked
 * (Now): a plain 48dp icon button beside Ask Dana that opens the thread
 * without listening.
 */
@Composable
fun ConversationButton(onClick: () -> Unit, modifier: Modifier = Modifier) {
    val c = circuitColors
    Box(
        modifier
            .size(48.dp)
            .clip(CircleShape)
            .clickable(role = Role.Button, onClickLabel = "Open the conversation", onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Icon(Icons.AutoMirrored.Outlined.Chat, contentDescription = "Conversation", tint = c.inkSoft, modifier = Modifier.size(24.dp))
    }
}

/**
 * Room a place's header keeps free at its end for the Ask Dana button the
 * shell floats in the top-right slot (0 where there is none: unfolded, a
 * drilled-in page). Read it through [askSlotInset].
 */
val LocalAskSlotInset = compositionLocalOf { 0.dp }

/** Keeps a header line clear of the floating Ask Dana button. */
@Composable
fun Modifier.askSlotInset(): Modifier = padding(end = LocalAskSlotInset.current)

/**
 * A folded place's header: the title (34sp) in the [HeaderSlot] row, so it
 * sits on the same line as the floating Ask Dana, and an optional
 * supporting line under it. Unfolded (no slot) the same, without the room.
 */
@Composable
fun PlaceHeader(title: String, modifier: Modifier = Modifier, supporting: String? = null, trailing: (@Composable () -> Unit)? = null) {
    val c = circuitColors
    Column(modifier.fillMaxWidth().padding(start = HeaderSlot.margin, end = HeaderSlot.margin, top = HeaderSlot.top)) {
        Row(Modifier.fillMaxWidth().heightIn(min = HeaderSlot.row).askSlotInset(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                title,
                style = io.github.ronganam.dana.ui.theme.PlaceTitle.copy(textDirection = TextDirection.Content),
                color = c.ink,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f).semantics { heading() }
            )
            // The place's own header buttons (Now's Ask Dana): they scroll with the header.
            if (trailing != null) Row(Modifier.padding(start = HeaderSlot.gap), verticalAlignment = Alignment.CenterVertically) { trailing() }
        }
        if (supporting != null) Text(
            supporting,
            style = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Content),
            color = c.inkSoft
        )
    }
}
