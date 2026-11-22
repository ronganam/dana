package io.github.ronganam.dana.ui.shell

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import io.github.ronganam.dana.ui.components.RollingText
import io.github.ronganam.dana.ui.theme.Motion
import io.github.ronganam.dana.ui.theme.hues
import io.github.ronganam.dana.ui.viewmodels.Place

/**
 * The places as a slim row of mono labels (the current one gets a vermilion
 * rule on top that grows from its middle, and a faint accent wash under the
 * label). Notifications carries the Inbox's needs-you count (rolls when it
 * changes). Bottom of the screen when folded, bottom of the list pane when
 * unfolded; a pane too narrow for one row wraps it onto two.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ShellNav(
    current: Place,
    places: List<Place>,
    notificationsCount: Int,
    onPlace: (Place) -> Unit,
    modifier: Modifier = Modifier
) {
    FlowRow(
        modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerLow)
            .edgeRule(top = true)
            .padding(horizontal = 14.dp),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        places.forEach { place ->
            val selected = place == current
            val count = if (place == Place.NOTIFICATIONS) notificationsCount else 0
            val accent = MaterialTheme.colorScheme.primary
            val label = place.label + when {
                count <= 0 -> ""
                else -> ", $count need you"
            }
            // Read only in the draw lambda below: switching place redraws two labels, nothing recomposes per frame.
            val on = animateFloatAsState(if (selected) 1f else 0f, Motion.base(), label = "nav")
            val wash = hues.tint(accent).copy(alpha = hues.tint(accent).alpha * 0.6f)
            Row(
                Modifier
                    .heightIn(min = 48.dp)
                    .clickable(role = Role.Tab) { onPlace(place) }
                    .semantics { this.selected = selected; contentDescription = label }
                    .drawBehind {
                        val v = on.value
                        if (v > 0f) {
                            val half = size.width / 2f * v
                            drawRect(wash.copy(alpha = wash.alpha * v), size = size)
                            drawLine(accent, Offset(size.width / 2f - half, 1.dp.toPx()), Offset(size.width / 2f + half, 1.dp.toPx()), 2.dp.toPx())
                        }
                    }
                    .padding(horizontal = 4.dp, vertical = 14.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    place.label.uppercase(),
                    style = MaterialTheme.typography.labelMedium,
                    color = if (selected) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.75f),
                    maxLines = 1,
                    softWrap = false
                )
                if (count > 0) RollingText(
                    " " + if (count > 99) "99+" else count.toString(),
                    style = MaterialTheme.typography.labelMedium,
                    color = accent
                )
            }
        }
    }
}
