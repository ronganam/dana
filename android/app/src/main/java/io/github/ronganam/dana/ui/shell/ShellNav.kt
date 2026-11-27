package io.github.ronganam.dana.ui.shell

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Inbox
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Inbox
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.NavigationRailItemDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.components.RollingText
import io.github.ronganam.dana.ui.components.popOnRise
import io.github.ronganam.dana.ui.theme.circuitColors
import io.github.ronganam.dana.ui.theme.hues
import io.github.ronganam.dana.ui.viewmodels.Place

/** A place's icon in the nav: outlined, filled when it is the current place. */
private fun Place.icon(selected: Boolean): ImageVector = when (this) {
    Place.NOW -> if (selected) Icons.Filled.Home else Icons.Outlined.Home
    Place.REMINDERS -> if (selected) Icons.Filled.Notifications else Icons.Outlined.Notifications
    Place.WATCHING -> if (selected) Icons.Filled.Visibility else Icons.Outlined.Visibility
    Place.INBOX -> if (selected) Icons.Filled.Inbox else Icons.Outlined.Inbox
    Place.MORE -> Icons.Filled.Menu
}

/** What a screen reader hears for a place: its name, and the Inbox's needs-you count. */
private fun Place.spoken(count: Int): String = if (count > 0) "$label, $count need you" else label

/**
 * A place's own hue, softly on its nav item when it is the current place (the
 * pill's tint and the icon): Now the accent (Dana herself), Reminders amber,
 * Watching sky, Inbox teal (messages); More stays neutral. Never warm.
 */
@Composable
private fun Place.hue(): Color? = when (this) {
    Place.NOW -> circuitColors.accent
    Place.REMINDERS -> hues.amber
    Place.WATCHING -> hues.sky
    Place.INBOX -> hues.teal
    Place.MORE -> null
}

/** The selected item's icon colour and pill: the place's hue (as text, for contrast) on its tint; More the neutral pill. */
@Composable
private fun Place.selectedColors(): Pair<Color, Color> {
    val scheme = MaterialTheme.colorScheme
    val h = hue() ?: return scheme.onSecondaryContainer to scheme.secondaryContainer
    return hues.text(h) to hues.tint(h)
}

@Composable
private fun PlaceIcon(place: Place, selected: Boolean, count: Int) {
    val c = circuitColors
    // Picking a place gives its icon a small springy pop.
    val icon = @Composable { Icon(place.icon(selected), contentDescription = null, modifier = Modifier.popOnRise(if (selected) 1 else 0)) }
    // A badge that shows up while the nav is on screen pops in; one already there when it opened just sits.
    val hadNone = remember { booleanArrayOf(count == 0) }
    if (count == 0) hadNone[0] = true
    if (count > 0) {
        BadgedBox(badge = {
            // Warm: the count is what waits on the owner. It pops when it rises and rolls when it changes.
            Badge(containerColor = c.warm, contentColor = c.onWarm, modifier = Modifier.popOnRise(count, popOnAttach = hadNone[0])) {
                RollingText(if (count > 99) "99+" else count.toString())
            }
        }) { icon() }
    } else icon()
}

@Composable
private fun PlaceLabel(place: Place, selected: Boolean) {
    Text(
        place.label,
        style = MaterialTheme.typography.labelMedium.copy(fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium),
        maxLines = 1,
        softWrap = false
    )
}

/**
 * The folded nav: a bottom bar of the places with a pill under the current
 * one's icon (Now · Reminders · Watching · Inbox · More), tinted in the place's
 * hue; the picked icon pops. The Inbox carries its needs-you count (a warm
 * badge that pops when it rises). Tapping the current place again goes back to its list.
 */
@Composable
fun ShellNav(
    current: Place,
    places: List<Place>,
    inboxCount: Int,
    onPlace: (Place) -> Unit,
    modifier: Modifier = Modifier
) {
    val scheme = MaterialTheme.colorScheme
    NavigationBar(
        modifier = modifier,
        containerColor = scheme.surfaceContainerLow,
        tonalElevation = 0.dp,
        // The shell already pads for the system bars.
        windowInsets = WindowInsets(0, 0, 0, 0)
    ) {
        places.forEach { place ->
            val selected = place == current
            val count = if (place == Place.INBOX) inboxCount else 0
            val (iconColor, pill) = place.selectedColors()
            val colors = NavigationBarItemDefaults.colors(
                selectedIconColor = iconColor,
                selectedTextColor = scheme.onSurface,
                indicatorColor = pill,
                unselectedIconColor = scheme.onSurfaceVariant,
                unselectedTextColor = scheme.onSurfaceVariant
            )
            NavigationBarItem(
                selected = selected,
                onClick = { onPlace(place) },
                icon = { PlaceIcon(place, selected, count) },
                label = { PlaceLabel(place, selected) },
                colors = colors,
                modifier = Modifier.semantics { contentDescription = place.spoken(count) }
            )
        }
    }
}

/**
 * The unfolded nav: a rail with Ask Dana at the top ([header]) and the same
 * places, the current one on a pill.
 */
@Composable
fun ShellRail(
    current: Place,
    places: List<Place>,
    inboxCount: Int,
    onPlace: (Place) -> Unit,
    modifier: Modifier = Modifier,
    header: @Composable () -> Unit = {}
) {
    val scheme = MaterialTheme.colorScheme
    NavigationRail(
        modifier = modifier,
        containerColor = scheme.background,
        header = {
            Spacer(Modifier.height(8.dp))
            header()
            Spacer(Modifier.height(20.dp))
        },
        windowInsets = WindowInsets(0, 0, 0, 0)
    ) {
        places.forEach { place ->
            val selected = place == current
            val count = if (place == Place.INBOX) inboxCount else 0
            val (iconColor, pill) = place.selectedColors()
            val colors = NavigationRailItemDefaults.colors(
                selectedIconColor = iconColor,
                selectedTextColor = scheme.onSurface,
                indicatorColor = pill,
                unselectedIconColor = scheme.onSurfaceVariant,
                unselectedTextColor = scheme.onSurfaceVariant
            )
            NavigationRailItem(
                selected = selected,
                onClick = { onPlace(place) },
                icon = { PlaceIcon(place, selected, count) },
                label = { PlaceLabel(place, selected) },
                colors = colors,
                modifier = Modifier.semantics { contentDescription = place.spoken(count) }
            )
        }
    }
}
