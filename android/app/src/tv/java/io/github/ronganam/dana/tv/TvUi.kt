package io.github.ronganam.dana.tv

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.ronganam.dana.ui.theme.DmMono
import io.github.ronganam.dana.ui.theme.DanaFontFamily
import io.github.ronganam.dana.ui.theme.DanaSerif

/**
 * Ink & Paper at ten feet: the phone's fonts and colour tokens, larger sizes.
 * The screen is 960×540 dp; keep content inside the 48/27 dp safe area.
 */
object TvType {
    val clock = TextStyle(fontFamily = DanaSerif, fontSize = 60.sp, lineHeight = 60.sp)
    val value = TextStyle(fontFamily = DanaSerif, fontSize = 30.sp, lineHeight = 32.sp)
    val title = TextStyle(fontFamily = DanaSerif, fontSize = 34.sp, lineHeight = 38.sp)
    val name = TextStyle(fontFamily = DanaFontFamily, fontSize = 15.sp, lineHeight = 19.sp, fontWeight = FontWeight.Medium)
    val body = TextStyle(fontFamily = DanaFontFamily, fontSize = 15.sp, lineHeight = 21.sp)
    val small = TextStyle(fontFamily = DanaFontFamily, fontSize = 13.sp, lineHeight = 17.sp)
    val mono = TextStyle(fontFamily = DmMono, fontSize = 12.sp, letterSpacing = 1.2.sp)
}

/** Mono uppercase kicker. */
@Composable
fun TvMono(text: String, modifier: Modifier = Modifier, color: Color = MaterialTheme.colorScheme.onSurfaceVariant) {
    Text(text.uppercase(), modifier, color = color, style = TvType.mono, maxLines = 1, overflow = TextOverflow.Ellipsis)
}

/** Section kicker over a hairline rule. */
@Composable
fun TvSection(text: String, modifier: Modifier = Modifier, trailing: String? = null) {
    androidx.compose.foundation.layout.Column(modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            TvMono(text)
            if (trailing != null) {
                Spacer(Modifier.width(12.dp))
                TvMono(trailing, color = MaterialTheme.colorScheme.outline)
            }
        }
        Spacer(Modifier.height(6.dp))
        HorizontalDivider(thickness = 1.dp, color = MaterialTheme.colorScheme.outlineVariant)
    }
}

/**
 * A D-pad focusable tile: hairline at rest; when focused it grows slightly and
 * takes a vermilion outline (no glow, no shadow). OK / Enter clicks.
 */
@Composable
fun FocusTile(
    modifier: Modifier = Modifier,
    shape: Shape = RoundedCornerShape(10.dp),
    focusRequester: FocusRequester? = null,
    background: Color = MaterialTheme.colorScheme.surfaceContainer,
    onFocus: (Boolean) -> Unit = {},
    onClick: () -> Unit,
    content: @Composable BoxScope.(focused: Boolean) -> Unit,
) {
    var focused by remember { mutableStateOf(false) }
    val scale by animateFloatAsState(if (focused) 1.05f else 1f, tween(110), label = "tileScale")
    val accent = MaterialTheme.colorScheme.primary
    val hairline = MaterialTheme.colorScheme.outlineVariant
    Box(
        modifier
            .graphicsLayer { scaleX = scale; scaleY = scale }
            .then(if (focusRequester != null) Modifier.focusRequester(focusRequester) else Modifier)
            .onFocusChanged { focused = it.isFocused; onFocus(it.isFocused) }
            .clip(shape)
            .background(background)
            .border(if (focused) 2.dp else 1.dp, if (focused) accent else hairline, shape)
            .clickable(remember { MutableInteractionSource() }, indication = null, onClick = onClick)
    ) { content(focused) }
}

/** A small focusable text chip (settings actions, options). */
@Composable
fun TvChip(
    text: String,
    modifier: Modifier = Modifier,
    selected: Boolean = false,
    focusRequester: FocusRequester? = null,
    onClick: () -> Unit,
) {
    FocusTile(
        modifier = modifier,
        shape = RoundedCornerShape(18.dp),
        focusRequester = focusRequester,
        background = if (selected) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceContainer,
        onClick = onClick,
    ) { focused ->
        Text(
            text,
            Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            style = TvType.small,
            color = when {
                selected -> MaterialTheme.colorScheme.onPrimaryContainer
                focused -> MaterialTheme.colorScheme.onSurface
                else -> MaterialTheme.colorScheme.onSurfaceVariant
            },
            maxLines = 1,
        )
    }
}

/** Row of chips with even spacing. */
@Composable
fun ChipRow(content: @Composable () -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) { content() }
}
