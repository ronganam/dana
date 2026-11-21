package io.github.ronganam.dana.ui.components.assistant

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.outlined.CropFree
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.data.screen.ScreenShare
import io.github.ronganam.dana.data.screen.ScreenShare.Availability
import io.github.ronganam.dana.data.screen.ScreenShare.Missing
import io.github.ronganam.dana.data.screen.ScreenShot
import io.github.ronganam.dana.ui.components.InkButton
import io.github.ronganam.dana.ui.components.InkEmphasis
import io.github.ronganam.dana.ui.components.MonoLabel
import io.github.ronganam.dana.ui.screens.common.isolatedParts

/**
 * The sheet's "Screen" control, at the start of the dock (thumb reach, next
 * to the field and the dial). Off: a hairline chip. On: filled vermilion with
 * a check — and [ScreenStatusLine] above the dock shows the thumbnail that
 * goes. Unavailable: muted, a tap says why ([ScreenStatusLine]).
 */
@Composable
internal fun ScreenChip(state: ScreenShare<ScreenShot>, onTap: () -> Unit, modifier: Modifier = Modifier) {
    val availability = state.availability
    val usable = availability == Availability.READY || availability == Availability.PENDING
    val on = state.on && usable
    val accent = MaterialTheme.colorScheme.primary
    val shape = RoundedCornerShape(10.dp)
    val label = when (availability) {
        Availability.OFF_IN_SETTINGS -> "Screen off"
        Availability.BLOCKED -> "No screen"
        else -> "Screen"
    }
    val description = when {
        on -> "Screen included with the next message. Tap to leave it out."
        usable -> "Include the screen with the next message"
        else -> "Screen not available. Tap for why."
    }
    val content = when {
        on -> MaterialTheme.colorScheme.onPrimary
        usable -> MaterialTheme.colorScheme.onSurface
        else -> MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f)
    }
    Row(
        modifier = modifier
            .heightIn(min = 44.dp)
            .clip(shape)
            .then(if (on) Modifier.background(accent) else Modifier)
            .border(1.dp, if (on) accent else MaterialTheme.colorScheme.outline.copy(alpha = if (usable) 0.55f else 0.3f), shape)
            .clickable(onClick = onTap)
            .semantics { contentDescription = description }
            .padding(horizontal = 10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(
            if (on) Icons.Default.Check else Icons.Outlined.CropFree,
            contentDescription = null,
            tint = content,
            modifier = Modifier.size(16.dp)
        )
        Spacer(Modifier.width(6.dp))
        Text(
            label,
            style = MaterialTheme.typography.labelLarge.copy(fontWeight = if (on) FontWeight.SemiBold else FontWeight.Medium),
            color = content,
            maxLines = 1
        )
    }
}

/**
 * The line above the dock: while the screen is included, its thumbnail, where
 * it is from and what is missing, with × to leave it out; after a tap on an
 * unavailable control, why in one line and the fix when there is one.
 * Nothing otherwise.
 */
@Composable
internal fun ScreenStatusLine(
    state: ScreenShare<ScreenShot>,
    onRemove: () -> Unit,
    onOpenSettings: () -> Unit,
    modifier: Modifier = Modifier
) {
    val availability = state.availability
    val usable = availability == Availability.READY || availability == Availability.PENDING
    if (state.on && usable) {
        IncludedLine(state, onRemove, onOpenSettings, modifier)
    } else if (state.explaining && (availability == Availability.OFF_IN_SETTINGS || availability == Availability.BLOCKED)) {
        WhyNotLine(availability, onOpenSettings, modifier)
    }
}

@Composable
private fun IncludedLine(
    state: ScreenShare<ScreenShot>,
    onRemove: () -> Unit,
    onOpenSettings: () -> Unit,
    modifier: Modifier
) {
    val accent = MaterialTheme.colorScheme.primary
    val screen = state.screen
    val where = remember(screen) {
        val host = screen?.url?.let { runCatching { android.net.Uri.parse(it).host }.getOrNull() }?.removePrefix("www.")
        listOfNotNull(screen?.app ?: screen?.packageName, host ?: screen?.title)
            .filter { it.isNotBlank() }.distinct().joinToString(" · ")
    }
    val missing = when (state.missing) {
        Missing.SCREENSHOT_OFF_IN_SETTINGS -> "Text only · screenshots are off in settings"
        Missing.SCREENSHOT_BLOCKED -> "Text only · this app blocks screenshots"
        Missing.TEXT -> "Screenshot only · no text from this app"
        Missing.NOTHING -> null
    }
    Row(
        modifier = modifier
            .fillMaxWidth()
            .height(IntrinsicSize.Min)
            .padding(start = 6.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(Modifier.width(3.dp).fillMaxHeight().background(accent))
        Spacer(Modifier.width(10.dp))
        val shot = state.shot
        if (shot != null) {
            val image = remember(shot) { shot.thumb.asImageBitmap() }
            Image(
                bitmap = image,
                contentDescription = "The screen that goes with the message",
                contentScale = ContentScale.Crop,
                modifier = Modifier
                    .height(56.dp)
                    .widthIn(max = 56.dp * shot.width / shot.height.coerceAtLeast(1))
                    .clip(RoundedCornerShape(6.dp))
                    .border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(6.dp))
            )
            Spacer(Modifier.width(10.dp))
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            MonoLabel(
                if (state.availability == Availability.PENDING) "Screen · capturing…" else "Screen goes with this message",
                color = accent
            )
            if (where.isNotEmpty()) {
                Text(
                    isolatedParts(where),
                    style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content),
                    color = MaterialTheme.colorScheme.onSurface,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
            }
            if (missing != null) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        missing,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false)
                    )
                    if (state.missing == Missing.SCREENSHOT_OFF_IN_SETTINGS) {
                        InkButton("Settings", emphasis = InkEmphasis.BARE, onClick = onOpenSettings)
                    }
                }
            }
        }
        IconButton(onClick = onRemove, modifier = Modifier.size(44.dp)) {
            Icon(
                Icons.Default.Close,
                contentDescription = "Leave the screen out",
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(18.dp)
            )
        }
    }
}

@Composable
private fun WhyNotLine(availability: Availability, onOpenSettings: () -> Unit, modifier: Modifier) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(start = 8.dp, end = 4.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            if (availability == Availability.OFF_IN_SETTINGS) "Screen sharing is off in the assistant settings."
            else "This app doesn't let assistants see its screen.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 2,
            modifier = Modifier.weight(1f)
        )
        if (availability == Availability.OFF_IN_SETTINGS) {
            Spacer(Modifier.width(8.dp))
            InkButton("Turn on", emphasis = InkEmphasis.PRIMARY, onClick = onOpenSettings)
        }
    }
}
