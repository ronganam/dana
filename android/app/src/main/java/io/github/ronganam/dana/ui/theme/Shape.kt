package io.github.ronganam.dana.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp

/**
 * Radius scale (Circuit): 8 small controls and a group's inner corners · 12
 * inputs / chips · 16 inner rooms (a draft, a run's steps) · 24 groups · 28
 * sheets and dialogs. Groups have no borders; rows inside them are joined
 * (`ui/components/circuit/GroupedSection`).
 */
val DanaShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(24.dp),
    extraLarge = RoundedCornerShape(28.dp)
)

/** A group's outer and inner corner radius, and the gap between joined rows. */
object Group {
    val outer = 24.dp
    val inner = 8.dp
    val gap = 2.dp
}
