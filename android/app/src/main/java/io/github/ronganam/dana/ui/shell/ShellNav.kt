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
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.theme.circuitColors
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

@Composable
private fun PlaceIcon(place: Place, selected: Boolean, count: Int) {
    val c = circuitColors
    if (count > 0) {
        BadgedBox(badge = {
            // Warm: the count is what waits on the owner.
            Badge(containerColor = c.warm, contentColor = c.onWarm) { Text(if (count > 99) "99+" else count.toString()) }
        }) { Icon(place.icon(selected), contentDescription = null) }
    } else Icon(place.icon(selected), contentDescription = null)
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
 * one's icon (Now · Reminders · Watching · Inbox · More). The Inbox carries its
 * needs-you count. Tapping the current place again goes back to its list.
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
    val colors = NavigationBarItemDefaults.colors(
        selectedIconColor = scheme.onSecondaryContainer,
        selectedTextColor = scheme.onSurface,
        indicatorColor = scheme.secondaryContainer,
        unselectedIconColor = scheme.onSurfaceVariant,
        unselectedTextColor = scheme.onSurfaceVariant
    )
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
    val colors = NavigationRailItemDefaults.colors(
        selectedIconColor = scheme.onSecondaryContainer,
        selectedTextColor = scheme.onSurface,
        indicatorColor = scheme.secondaryContainer,
        unselectedIconColor = scheme.onSurfaceVariant,
        unselectedTextColor = scheme.onSurfaceVariant
    )
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
