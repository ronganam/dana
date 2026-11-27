package io.github.ronganam.dana.ui.components.circuit

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.theme.circuitColors

/**
 * Ask Dana: the tonal mic in the top-right header slot of every folded place
 * (48dp, the pill indicator's tone, the mic in the accent).
 */
@Composable
fun AskDanaButton(onClick: () -> Unit, modifier: Modifier = Modifier, label: String = "Ask Dana") {
    val c = circuitColors
    Box(
        modifier
            .size(48.dp)
            .clip(CircleShape)
            .background(c.indicator)
            .clickable(role = Role.Button, onClickLabel = label, onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Icon(Icons.Outlined.Mic, contentDescription = label, tint = c.accent, modifier = Modifier.size(24.dp))
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
