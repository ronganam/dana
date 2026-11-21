package io.github.ronganam.dana.ui.screens.now

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Call
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material.icons.outlined.CreditCard
import androidx.compose.material.icons.outlined.Event
import androidx.compose.material.icons.outlined.Lightbulb
import androidx.compose.material.icons.outlined.MonitorHeart
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.NotificationsActive
import androidx.compose.material.icons.outlined.Shield
import androidx.compose.material.icons.outlined.WarningAmber
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.notifications.AppIcons
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** The leading tile of a row is this wide; text and buttons indent by it plus [NOW_TILE_GAP]. */
internal val NOW_TILE = 30.dp
internal val NOW_TILE_GAP = 12.dp

private fun NowGlyph.vector(): ImageVector = when (this) {
    NowGlyph.APPROVAL -> Icons.Outlined.Shield
    NowGlyph.ALERT -> Icons.Outlined.WarningAmber
    NowGlyph.CALL -> Icons.Outlined.Call
    NowGlyph.EVENT -> Icons.Outlined.Event
    NowGlyph.REMINDER -> Icons.Outlined.NotificationsActive
    NowGlyph.CHECK -> Icons.Outlined.MonitorHeart
    NowGlyph.REVIEW -> Icons.Outlined.Checklist
    NowGlyph.NOTIFICATION -> Icons.Outlined.Notifications
    NowGlyph.SUGGESTION -> Icons.Outlined.Lightbulb
    NowGlyph.MONEY -> Icons.Outlined.CreditCard
}

/** What the tile settled on once the app lookup (if any) is done. */
private sealed class Shown {
    class Icon(val bitmap: ImageBitmap) : Shown()
    class Letters(val text: String) : Shown()
    class Glyph(val glyph: NowGlyph) : Shown()
}

/**
 * Where a Now item came from, at a glance: the app's real launcher icon for
 * anything from a notification, else a quiet outline glyph for the kind. The
 * same tile as the Notifications place; decorative (the mono source line next
 * to it says the same in words). Approvals and alerts, the things Dana is
 * waiting on, take the accent; everything else stays muted.
 */
@Composable
fun NowTile(visual: NowVisual, size: Dp = NOW_TILE, modifier: Modifier = Modifier) {
    val ctx = LocalContext.current.applicationContext
    val shown: Shown? by produceState<Shown?>(
        // First frame without I/O when the icon is already in memory; otherwise an empty tile (no flicker to a glyph and back).
        initialValue = when (visual) {
            is NowVisual.Glyph -> Shown.Glyph(visual.glyph)
            is NowVisual.App -> visual.pkg?.let { AppIcons.cached(it) }?.let { Shown.Icon(it.asImageBitmap()) }
        },
        visual
    ) {
        if (value == null && visual is NowVisual.App) {
            value = withContext(Dispatchers.IO) {
                val pkg = visual.pkg ?: visual.name.takeIf { it.isNotBlank() }?.let { AppIcons.packageForLabel(ctx, it) }
                val bmp = pkg?.let { AppIcons.load(ctx, it) }
                when {
                    bmp != null -> Shown.Icon(bmp.asImageBitmap())
                    // A known package without an icon reads best as its letters, an unknown app as the kind's glyph.
                    pkg != null && visual.name.isNotBlank() -> Shown.Letters(visual.name.take(2))
                    else -> Shown.Glyph(visual.fallback)
                }
            }
        }
    }
    val accent = visual is NowVisual.Glyph && (visual.glyph == NowGlyph.APPROVAL || visual.glyph == NowGlyph.ALERT)
    Box(
        modifier.size(size).clip(RoundedCornerShape(size * 0.27f)).background(MaterialTheme.colorScheme.surfaceContainerHigh),
        contentAlignment = Alignment.Center
    ) {
        when (val s = shown) {
            is Shown.Icon -> Image(s.bitmap, contentDescription = null, modifier = Modifier.size(size))
            is Shown.Letters -> Text(s.text, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            is Shown.Glyph -> Icon(
                s.glyph.vector(), contentDescription = null,
                tint = if (accent) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(size * 0.6f)
            )
            null -> Unit
        }
    }
}

/** [NowTile] for a queue item, remembered per item so list scrolling doesn't redo the decision. */
@Composable
fun NowTile(item: io.github.ronganam.dana.data.models.NowItem, size: Dp = NOW_TILE, modifier: Modifier = Modifier) {
    NowTile(remember(item) { NowModel.visual(item) }, size, modifier)
}
